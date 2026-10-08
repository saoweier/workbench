"""集中异常处理（P3/T15）。

对照 docs/04-development-plan.md T15 与 docs/02-modules.md §M08。

## 这个模块解决什么

前面三个阶段各自会产出"需要人看一眼"的东西：

- P2：模型越权被拒、候选被淘汰、库存到顶暂停
- P3/T12：修复两轮仍不合格、字体缺失
- P3/T13：租约被顶替、孤儿文件、缺图、**结果未知待人工确认**
- P3/T14：调用次数到顶、金额超限

如果这些散落在各处，人会漏看；全量展示，人会被淹没。
所以要做两件事：**聚合**（同类折叠）+ **排序**（重要的浮上来）。

## 三条设计原则

1. **聚合不掩盖数量**。3 条同类异常折叠成 1 条，但必须显示"×3"、
   首次与末次时间。折叠是为了少看几行，不是为了让人以为只有一条。
2. **重大变更置顶**。影响已批准版本的改动（缺图、批准失效）
   不埋在日常流水里——这类事不处理会直接导致发错。
3. **不做自动决策**。本模块**只读 + 只记录**。
   不自动批准、不自动发布、不自动重试、不自动删文件。
   它的职责是"把要人管的东西集中起来"，不是"减少人管的东西"。

## 人工打断时长

记录人从"看到异常"到"处理完"的时长（`acknowledge` → `resolve`），
用于 P5 估算真实人工负担。**不猜、不默认填 0**——没记录就是 null。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..core.errors import NotFound, ValidationFailed
from ..models.entities import Event
from .recovery_service import RecoveryService, _aware
from .topic_service import PENDING_STATES

#: 异常类型 → 严重级与是否属于"重大变更"
SEVERITY: dict[str, str] = {
    "model_privilege_violation": "critical",
    "repair_exhausted": "high",
    "repair_needs_manual": "high",
    "artifact_missing": "critical",
    "orphan_file": "low",
    "lease_superseded": "medium",
    "unknown_result": "high",
    "compose_failed": "high",
    "call_limit_reached": "medium",
    "budget_exceeded": "medium",
}

#: 影响已批准版本的改动 —— 这类必须置顶，不能埋在日常流水里
MAJOR_KINDS = frozenset({
    "artifact_missing",
    "model_privilege_violation",
    "unknown_result",
    "repair_exhausted",
})

#: 需要人工决策（不是看一眼就过去的）
NEEDS_ACTION = frozenset({
    "artifact_missing", "orphan_file", "unknown_result",
    "repair_exhausted", "repair_needs_manual", "model_privilege_violation",
})

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass
class AttentionItem:
    """一条待处理项。可能是 N 条同类异常的聚合。"""

    kind: str
    title: str
    severity: str
    count: int = 1
    first_seen: str | None = None
    last_seen: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    detail: dict = field(default_factory=dict)
    suggested_action: str = ""
    needs_action: bool = True
    is_major: bool = False
    acknowledged_by: str | None = None
    acknowledged_at: str | None = None
    resolved_by: str | None = None
    resolved_at: str | None = None
    #: 人工处理耗时（秒）。**没记录就是 null，不填 0**
    handling_seconds: float | None = None

    @property
    def key(self) -> str:
        raw = f"{self.kind}|{self.entity_type}|{self.entity_id}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "kind": self.kind,
            "title": self.title,
            "severity": self.severity,
            "count": self.count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "detail": self.detail,
            "suggested_action": self.suggested_action,
            "needs_action": self.needs_action,
            "is_major": self.is_major,
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at,
            "resolved_by": self.resolved_by,
            "resolved_at": self.resolved_at,
            "handling_seconds": self.handling_seconds,
            "handling_note": ("null 表示尚未记录处理时长，不等于 0 秒"
                              if self.handling_seconds is None else None),
        }


@dataclass
class AttentionReport:
    major: list[AttentionItem] = field(default_factory=list)
    items: list[AttentionItem] = field(default_factory=list)
    folded_logs: list[dict] = field(default_factory=list)
    scanned_events: int = 0

    def as_dict(self) -> dict:
        return {
            "major": [i.as_dict() for i in self.major],
            "items": [i.as_dict() for i in self.items],
            "folded_logs": self.folded_logs,
            "summary": {
                "major_count": len(self.major),
                "item_count": len(self.items),
                "needs_action": sum(1 for i in self.items if i.needs_action),
                "total_occurrences": sum(i.count for i in self.items),
                "scanned_events": self.scanned_events,
            },
            "note": ("同类异常已折叠但保留计数与时间范围；"
                     "标 major 的会影响已批准版本，必须优先处理"),
        }


class AttentionService:
    """异常聚合与集中处理。**只读 + 只记录，不做自动决策。**"""

    def __init__(self, session_factory, *, actor: str = "coisini") -> None:
        self.sf = session_factory
        self.actor = actor
        self.recovery = RecoveryService(session_factory, actor=actor)

    # ------------------------------------------------------------ 聚合

    def collect(self, *, content_id: str | None = None,
                include_recovery_scan: bool = True) -> AttentionReport:
        """把散落各处的异常收拢成一份清单。"""
        rep = AttentionReport()
        grouped: dict[str, AttentionItem] = {}

        # 1) Event 表里的异常类事件
        with self.sf() as s:
            q = s.query(Event)
            if content_id:
                q = q.filter(Event.entity_id == content_id)
            events = q.order_by(Event.time).all()
            rep.scanned_events = len(events)
            for e in events:
                sev = SEVERITY.get(e.type)
                if sev is None:
                    continue
                grp = f"{e.type}|{e.entity_type}|{e.entity_id}"
                item = grouped.get(grp)
                ts = _aware(e.time).isoformat()
                if item is None:
                    item = AttentionItem(
                        kind=e.type,
                        title=_title_for(e.type),
                        severity=sev,
                        first_seen=ts, last_seen=ts,
                        entity_type=e.entity_type, entity_id=e.entity_id,
                        detail={"message": (e.payload or {}).get("message")},
                        suggested_action=_action_for(e.type),
                        needs_action=e.type in NEEDS_ACTION,
                        is_major=e.type in MAJOR_KINDS,
                        acknowledged_at=ts if e.resolved_by else None,
                        resolved_by=e.resolved_by,
                        resolved_at=ts if e.resolved_by else None,
                    )
                    if e.payload:
                        item.detail.setdefault("payload", e.payload)
                    grouped[grp] = item
                else:
                    # 折叠：计数 +1，保留时间范围
                    item.count += 1
                    item.last_seen = ts
                    if e.resolved_by and not item.resolved_by:
                        item.resolved_by = e.resolved_by
                        item.resolved_at = ts
                    item.detail = item.detail or {}

        # 2) 恢复扫描的发现（租约/孤儿/缺图/未知结果）
        if include_recovery_scan:
            rec = self.recovery.scan()
            for f in rec.needs_manual:
                grp = f"{f.kind}|recovery|{f.subject}"
                if grp in grouped:
                    continue
                grouped[grp] = AttentionItem(
                    kind=f.kind, title=_title_for(f.kind),
                    severity=SEVERITY.get(f.kind, "medium"),
                    entity_type="recovery", entity_id=f.subject,
                    detail=f.detail, suggested_action=f.action,
                    needs_action=f.kind in NEEDS_ACTION,
                    is_major=f.kind in MAJOR_KINDS,
                )

        # 3) 待预览库存到顶（不是失败，但需要人去看）
        with self.sf() as s:
            from ..models.entities import ContentItem
            pend = (s.query(ContentItem)
                    .filter(ContentItem.state.in_(sorted(PENDING_STATES)), ~ContentItem.display_id.like("DEMO-%")).all())
        from ..core.config import get_settings
        limit = get_settings().pending_review_stock_limit
        if len(pend) >= limit:
            grouped["stock_limit|content_item|"] = AttentionItem(
                kind="stock_limit", title="待预览库存已达上限",
                severity="low",
                detail={"pending": len(pend), "limit": limit,
                        "content_ids": [c.id for c in pend]},
                suggested_action="先处理已有预览（批准/修改/暂缓）再新增制作；"
                                 "这是明确的暂停状态，不是失败",
                needs_action=True, is_major=False,
            )

        # 4) 汇总用量限制（调用次数/金额）
        from .usage_service import UsageService
        usage = UsageService(self.sf)
        allow = usage.check_call_allowed(calls_so_far=10**6, content_id=content_id)
        if not allow["allowed"] and allow["blocking_kind"] == "money":
            grouped["budget_exceeded|usage|"] = AttentionItem(
                kind="budget_exceeded", title="已达金额上限",
                severity="medium", detail=allow,
                suggested_action="调整批次金额上限，或改用 usage_tracking 模式",
                needs_action=True, is_major=False,
            )

        items = sorted(grouped.values(),
                       key=lambda i: (_SEVERITY_ORDER.get(i.severity, 9),
                                      -(i.count or 0)))
        rep.items = items
        rep.major = [i for i in items if i.is_major]
        rep.folded_logs = self._fold_logs(events) if rep.scanned_events else []
        return rep

    # ------------------------------------------------------------ 日志折叠

    @staticmethod
    def _fold_logs(events: list[Event]) -> list[dict]:
        """连续相同消息只留一条 + 计数。避免一屏重复把重要信息冲走。"""
        out: list[dict] = []
        for e in events:
            msg = (e.payload or {}).get("message") or e.type
            if out and out[-1]["message"] == msg:
                out[-1]["count"] += 1
                out[-1]["last_seen"] = _aware(e.time).isoformat()
                continue
            out.append({
                "type": e.type, "message": msg, "count": 1,
                "first_seen": _aware(e.time).isoformat(),
                "last_seen": _aware(e.time).isoformat(),
            })
        return out

    # ------------------------------------------------------------ 人工处理记录

    def acknowledge(self, kind: str, entity_id: str, *, actor: str | None = None) -> dict:
        """标记"人已经看到了"。开始计处理时长。"""
        who = actor or self.actor
        with self.sf() as s:
            s.add(Event(entity_type="attention", entity_id=entity_id,
                        type="acknowledged", actor=who,
                        payload={"kind": kind, "at": _now_iso()}))
            s.commit()
        return {"kind": kind, "entity_id": entity_id, "acknowledged_by": who,
                "at": _now_iso()}

    def resolve(self, kind: str, entity_id: str, *, actor: str | None = None,
                note: str = "") -> dict:
        """标记已处理，并算出人工耗时。

        **耗时时长基于真实记录**：找不到 acknowledge 记录时为 null，
        不拿"当前时间"硬凑一个数出来。
        """
        who = actor or self.actor
        now = datetime.now(timezone.utc)
        acked_at: datetime | None = None

        with self.sf() as s:
            q = (s.query(Event)
                 .filter(Event.type == "acknowledged")
                 .filter(Event.entity_id == entity_id)
                 .order_by(Event.time.desc()))
            for e in q.all():
                if (e.payload or {}).get("kind") == kind:
                    acked_at = _aware(e.time)
                    break
            s.add(Event(entity_type="attention", entity_id=entity_id,
                        type="resolved", actor=who,
                        payload={"kind": kind, "note": note,
                                 "at": now.isoformat()}))
            s.commit()

        handling = round((now - acked_at).total_seconds(), 1) if acked_at else None
        return {
            "kind": kind, "entity_id": entity_id, "resolved_by": who,
            "resolved_at": now.isoformat(),
            "handling_seconds": handling,
            "note": note,
            "handling_note": ("未找到 acknowledge 记录，处理时长记为 null（不填 0）"
                              if handling is None else None),
        }

    def handling_stats(self) -> dict:
        """人工负担统计，供 P5 估算。**只统计有记录的，缺失单列**。"""
        resolved, missing = [], 0
        with self.sf() as s:
            events = s.query(Event).order_by(Event.time).all()
            acks: dict[tuple, datetime] = {}
            for e in events:
                if e.type == "acknowledged":
                    acks[((e.payload or {}).get("kind"), e.entity_id)] = _aware(e.time)
                elif e.type == "resolved":
                    k = ((e.payload or {}).get("kind"), e.entity_id)
                    start = acks.get(k)
                    if start is None:
                        missing += 1
                        continue
                    resolved.append(round((_aware(e.time) - start).total_seconds(), 1))
        return {
            "resolved_count": len(resolved),
            "handling_seconds_total": round(sum(resolved), 1) if resolved else None,
            "handling_seconds_avg": (round(sum(resolved) / len(resolved), 1)
                                     if resolved else None),
            "handling_seconds_max": max(resolved) if resolved else None,
            "records_missing_ack": missing,
            "note": ("null 表示没有可用记录，不等于 0；"
                     "缺 acknowledge 记录的处理单列计数，不并进平均"),
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _title_for(kind: str) -> str:
    return {
        "model_privilege_violation": "模型试图改写无权字段（已拒绝）",
        "repair_exhausted": "修复两轮仍不合格",
        "repair_needs_manual": "存在需人工处理的问题（字体/文件）",
        "artifact_missing": "产物图片缺失",
        "orphan_file": "发现无记录的孤儿文件",
        "lease_superseded": "任务租约被其他 Worker 顶替",
        "unknown_result": "Provider 结果未知，待人工确认",
        "compose_failed": "稿件生成失败",
        "call_limit_reached": "本次运行调用次数已达上限",
        "budget_exceeded": "已达金额上限",
        "stock_limit": "待预览库存已达上限",
    }.get(kind, kind)


def _action_for(kind: str) -> str:
    return {
        "model_privilege_violation": "无需处理：越权输出已被确定性拒绝，模型无法改状态",
        "repair_exhausted": "人工查看剩余问题，决定手动修改或补充来源",
        "repair_needs_manual": "先处理环境（字体/文件），再重跑修复",
        "artifact_missing": "重渲染该平台；若版本已批准，需人工决定是否撤销批准",
        "orphan_file": "确认是否为有用产物；**不要盲目删除**",
        "lease_superseded": "确认任务是否已由新 Worker 完成，避免重复执行",
        "unknown_result": "去服务商后台查这次调用的真实状态；**不要直接重发**",
        "compose_failed": "查看失败原因，补齐材料后重跑该阶段",
        "stock_limit": "先处理已有预览再新增制作（暂停不是失败）",
    }.get(kind, "人工确认")
