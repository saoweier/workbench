"""用量与运行限制（P3/T14）。

对照 docs/04-development-plan.md T14 与不变量第 5、8 条。

## 为什么这个模块必须存在

P2 把记账落到了 `provider_call` 表（`unique(request_key)` 保证重试不重复记账），
但 `api/usage.py` 还在读 P0 遗留的 `provider_calls.json`。**同一件事有两个真相来源**，
两边数字迟早对不上——而且大概率是"页面显示 0 消耗、实际已经花了钱"这种最坏的对不上。

本模块作为**唯一**的用量聚合入口，只从表里读。

## 四条口径（写死在实现里，不靠调用方自觉）

1. **真数据 / fixture / 估算显式区分**。fixture 调用不计入真实成本结论。
2. **金额未知不写 0**。缺金额的调用计入"待核实笔数"，不参与求和。
3. **不同币种不直接相加**。
4. **null ≠ 0**：金额上限未设置 ≠ 零额度，也 ≠ 无限调用
   （次数、库存、重试等非金额限制始终生效）。

## 限额分两类

| 类型 | 默认 | 是否阻塞 |
|---|---|---|
| 金额上限 | 未设置（null） | `usage_tracking` 模式**不阻塞**；`hard_cap` 模式才校验 |
| 每批条目数 | 1 | 阻塞 |
| 待预览库存 | 3 | 暂停（不是失败，P2 已实现） |
| 修复轮次 | 2 | 标 unrepairable |
| 单次运行调用数 | 40 | 停止后续调用 |
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.config import get_settings
from ..models.entities import ProviderCallRow

#: 调用成功/失败/未知
OK_STATES = ("succeeded", "failed", "unknown")


@dataclass
class UsageBucket:
    """一个来源（real/fixture/local_seed）的用量。"""

    run_mode: str
    calls: int = 0
    succeeded: int = 0
    failed: int = 0
    unknown_result: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: 币种 → 微货币单位（估算）
    estimated_micro: dict[str, int] = field(default_factory=dict)
    reported_micro: dict[str, int] = field(default_factory=dict)
    reconciled_micro: dict[str, int] = field(default_factory=dict)
    unpriced_calls: int = 0

    def as_dict(self) -> dict:
        return {
            "run_mode": self.run_mode,
            "calls": self.calls,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "unknown_result": self.unknown_result,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_micro_by_currency": self.estimated_micro,
            "reported_micro_by_currency": self.reported_micro,
            "reconciled_micro_by_currency": self.reconciled_micro,
            "unpriced_calls": self.unpriced_calls,
            "tokens_note": "null 表示供应商未回传，不等于 0",
            "money_note": "空字典表示没有任何一笔可定价；待核实笔数另计，不写 0",
        }


@dataclass
class UsageReport:
    buckets: dict[str, UsageBucket] = field(default_factory=dict)
    total_calls: int = 0

    def real(self) -> UsageBucket:
        return self.buckets.setdefault("real", UsageBucket("real"))

    def as_dict(self) -> dict:
        real = self.buckets.get("real", UsageBucket("real"))
        fixture = self.buckets.get("fixture", UsageBucket("fixture"))
        local = self.buckets.get("local_seed", UsageBucket("local_seed"))
        return {
            "total_calls": self.total_calls,
            "by_run_mode": {k: v.as_dict() for k, v in self.buckets.items()},
            "real": real.as_dict(),
            "fixture": fixture.as_dict(),
            "local_seed": local.as_dict(),
            "display": {
                "real_cost": _display(real),
                "fixture_cost": "不产生真实消耗（演练数据，未计入真实成本）",
                "note": ("真实 / 演练 / 本地 三类分开列示；"
                         "金额未知不等于 0，未知笔数单列待核实"),
            },
        }


def _display(b: UsageBucket) -> str:
    if b.calls == 0:
        return "无真实调用"
    if not b.reconciled_micro and not b.reported_micro and not b.estimated_micro:
        return f"金额未知（{b.unpriced_calls} 笔待核实）"
    parts = []
    for label, table in (("已对账", b.reconciled_micro),
                         ("供应商回传", b.reported_micro),
                         ("估算", b.estimated_micro)):
        for cur, micro in sorted(table.items()):
            parts.append(f"{micro / 1_000_000:.4f} {cur}（{label}）")
    tail = f"，另有 {b.unpriced_calls} 笔待核实" if b.unpriced_calls else ""
    return " + ".join(parts) + tail


class UsageService:
    """用量聚合与限额判定。**唯一**从 `provider_call` 表读数的入口。"""

    def __init__(self, session_factory) -> None:
        self.sf = session_factory
        self.settings = get_settings()

    # ------------------------------------------------------------ 聚合

    def summarize(self, *, content_id: str | None = None,
                  platform_revision_id: str | None = None,
                  batch_content_ids: list[str] | None = None) -> UsageReport:
        with self.sf() as s:
            q = s.query(ProviderCallRow)
            if content_id:
                q = q.filter(ProviderCallRow.content_id == content_id)
            if platform_revision_id:
                q = q.filter(ProviderCallRow.platform_revision_id == platform_revision_id)
            if batch_content_ids is not None:
                q = q.filter(ProviderCallRow.content_id.in_(batch_content_ids))
            rows = q.all()

        rep = UsageReport()
        for c in rows:
            rep.total_calls += 1
            b = rep.buckets.setdefault(c.run_mode or "unknown",
                                      UsageBucket(c.run_mode or "unknown"))
            b.calls += 1
            if c.state == "succeeded":
                b.succeeded += 1
            elif c.state == "failed":
                b.failed += 1
            else:
                b.unknown_result += 1
            if c.input_tokens is not None:
                b.input_tokens = (b.input_tokens or 0) + c.input_tokens
            if c.output_tokens is not None:
                b.output_tokens = (b.output_tokens or 0) + c.output_tokens

            # 三态各自独立累计，互不覆盖
            cur = c.currency or "UNKNOWN"
            any_price = False
            for table, val in ((b.estimated_micro, c.estimated_micro),
                               (b.reported_micro, c.reported_micro),
                               (b.reconciled_micro, c.reconciled_micro)):
                if val is not None:
                    table[cur] = table.get(cur, 0) + val
                    any_price = True
            if not any_price:
                b.unpriced_calls += 1
        return rep

    # ------------------------------------------------------------ 限额

    def limits(self) -> dict:
        """当前生效的全部限制。金额上限可为 null。"""
        s = self.settings
        return {
            "money": {
                "cost_mode": s.cost_mode_default,
                "batch_money_limit_micro": s.batch_money_limit_default,
                "daily_money_limit_micro": s.daily_money_limit_default,
                "currency": s.currency_default,
                "meaning": "null 表示未设置金额上限（不是 0 额度）",
                "enforced_in": ("usage_tracking 模式不因金额阻塞" 
                                if s.cost_mode_default == "usage_tracking"
                                else "hard_cap 模式会校验金额"),
            },
            "non_money": {
                "item_limit": s.default_batch_item_limit,
                "pending_review_stock_limit": s.pending_review_stock_limit,
                "max_repair_rounds": s.max_repair_rounds,
                "max_calls_per_run": 40,
                "meaning": "这些限制与是否设置金额上限无关，始终生效",
            },
        }

    def check_call_allowed(self, *, calls_so_far: int,
                           estimated_next_micro: int | None = None,
                           content_id: str | None = None) -> dict:
        """能不能再发一次调用。**未知估价不等于 0**。"""
        rep = self.summarize(content_id=content_id)
        real = rep.buckets.get("real", UsageBucket("real"))
        limits = self.limits()
        max_calls = limits["non_money"]["max_calls_per_run"]

        # 1) 调用次数上限：与金额无关，始终生效
        if calls_so_far >= max_calls:
            return {"allowed": False, "reason": "call_limit_reached",
                    "detail": f"本次运行已调用 {calls_so_far} 次，达到上限 {max_calls}",
                    "blocking_kind": "non_money"}

        cost_mode = self.settings.cost_mode_default
        if cost_mode == "usage_tracking":
            return {"allowed": True,
                    "reason": "usage_tracking 模式：记录实际消耗，不因金额阻塞",
                    "detail": {"recorded_calls": real.calls,
                               "unpriced_calls": real.unpriced_calls},
                    "blocking_kind": None}

        # 2) hard_cap 模式才校验金额
        limit = self.settings.batch_money_limit_default
        if limit is None:
            return {"allowed": False, "reason": "hard_cap_requires_limit",
                    "detail": "hard_cap 模式必须提供金额上限；未设置上限不能启用硬上限",
                    "blocking_kind": "config"}
        if estimated_next_micro is None:
            return {"allowed": False, "reason": "cannot_estimate",
                    "detail": "hard_cap 模式下无法估价，暂停付费调用",
                    "blocking_kind": "unknown_price"}
        cur = self.settings.currency_default
        used = real.reconciled_micro.get(cur) or real.reported_micro.get(cur) \
            or real.estimated_micro.get(cur) or 0
        if used + estimated_next_micro > limit:
            return {"allowed": False, "reason": "budget_exceeded",
                    "detail": f"已用 {used} + 预计 {estimated_next_micro} > 上限 {limit}",
                    "blocking_kind": "money"}
        return {"allowed": True, "reason": "在上限内", "blocking_kind": None}

    def pending_review_stock(self) -> dict:
        """待预览库存。这是"暂停"信号不是"失败"。"""
        from ..models.entities import ContentItem
        from .topic_service import PENDING_STATES

        with self.sf() as s:
            rows = s.query(ContentItem).filter(ContentItem.state.in_(
                sorted(PENDING_STATES)), ~ContentItem.display_id.like("DEMO-%")).all()
        limit = self.settings.pending_review_stock_limit
        return {
            "pending": len(rows),
            "limit": limit,
            "reached": len(rows) >= limit,
            "meaning": "达到上限时暂停新增制作；这是明确的暂停状态，不是失败",
            "content_ids": [c.id for c in rows],
        }
