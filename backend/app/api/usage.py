"""用量与消耗查询接口（T03 建立，P3/T14 改为以数据库表为唯一来源）。

## 改动说明（重要）

P0 阶段这里读的是 `storage/provider_calls.json`（一个 JSON ledger）。
P2/T08 之后**记账已经落到 `provider_call` 表**（`unique(request_key)`
保证重试不重复记账），JSON ledger 就成了没人写、却还在被读的僵尸文件。

后果是：P2 跑完实际有调用记录，但本接口会显示"0 笔、金额未知"，
**看着像没花钱，实际已经花了**——正是不变量第 5 条要防的事。

现在统一走 `UsageService`（只读表）。JSON ledger 不再参与；
若文件仍存在，响应里会如实标注它已弃用，而不是假装没这回事。

原则（06 文档第 3 节 + 不变量 5、8）：
- usage 是实际回传的用量，可能没有金额
- **金额未知不填 0**；不同币种不直接相加
- 部分未知时显示"已知金额 + N 笔待核实"
- fixture 与 local_seed 单独计数，不混入真实成本结论
"""
from __future__ import annotations

from fastapi import APIRouter, Body
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..core.config import get_settings
from ..models.entities import Base, enable_sqlite_fk
from ..services.usage_service import UsageBucket, UsageService

router = APIRouter(tags=["usage"])
_settings = get_settings()

_engine = create_engine(_settings.database_url, future=True)
enable_sqlite_fk(_engine)
Base.metadata.create_all(_engine)
SessionFactory = sessionmaker(bind=_engine, future=True)

_svc = UsageService(SessionFactory)

#: P0 遗留文件。只用于"提示它已弃用"，不再作为数据来源。
_LEGACY_LEDGER = _settings.storage_root / "provider_calls.json"


def _legacy_note() -> str | None:
    if not _LEGACY_LEDGER.exists():
        return None
    return (f"检测到 P0 遗留文件 {_LEGACY_LEDGER.name}，已不再作为数据来源"
            "（P2 起记账落库为唯一真相）；可自行归档删除")


@router.get("/usage")
async def get_usage(content_id: str | None = None) -> dict:
    """用量汇总。**只从 `provider_call` 表读**。"""
    rep = _svc.summarize(content_id=content_id)
    data = rep.as_dict()
    data["limits"] = _svc.limits()
    data["pending_review_stock"] = _svc.pending_review_stock()
    data["source"] = {
        "table": "provider_call",
        "note": "唯一数据来源；P0 的 JSON ledger 已弃用",
        "legacy": _legacy_note(),
    }
    data["real_calls_recorded"] = rep.buckets.get("real", UsageBucket("real")).calls
    return data


@router.get("/usage/budget-policy")
async def get_budget_policy() -> dict:
    lim = _svc.limits()
    return {
        "policy": lim,
        "explanation": {
            "usage_tracking": "默认模式：追踪实际消耗，金额上限可为空，不阻塞任务",
            "hard_cap": "可选模式：需提供金额上限与可估价依据，否则暂停付费调用",
            "never": "null 不等于 0；未设置金额上限不等于无限调用（仍受次数/库存/重试限制）",
        },
    }


@router.post("/usage/budget-policy/check")
async def check_budget(
    candidate_micro: int | None = Body(default=None, embed=True),
    calls_so_far: int = Body(default=0, embed=True),
    content_id: str | None = Body(default=None, embed=True),
) -> dict:
    """判断"再发一次调用"是否被允许。

    **未知估价不等于 0**：hard_cap 模式下估不出价就暂停，
    而不是当成免费放行。
    """
    return _svc.check_call_allowed(
        calls_so_far=calls_so_far,
        estimated_next_micro=candidate_micro,
        content_id=content_id,
    )
