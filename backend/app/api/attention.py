"""集中异常处理接口（P3/T15）。

对应 `docs/04-development-plan.md` T15 与 `docs/02-modules.md` §M08。

设计边界（与 `attention_service.py` 一致）：
- **只读 + 只记录**。这里没有"一键修复全部"之类的接口，
  也不提供"自动批准/自动发布/自动重试/自动删除"。
- 写操作只有两个：`acknowledge`（人已看到）与 `resolve`（人已处理完）。
  这两个都是**记录事实**，不改变任何业务状态。
- 时长统计里 **null ≠ 0**：没有记录就是 null，不硬凑。

路由一览：
    GET  /attention                    异常聚合清单（重大置顶 + 折叠计数）
    GET  /attention/handling-stats     人工处理时长统计（供 P5 估算负担）
    POST /attention/acknowledge        标记"已看到"，开始计时
    POST /attention/resolve            标记"已处理"，结算时长
"""
from __future__ import annotations

from fastapi import APIRouter, Body
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..core.config import get_settings
from ..models.entities import Base, enable_sqlite_fk
from ..services.attention_service import AttentionService

router = APIRouter(tags=["attention"])
_settings = get_settings()

_engine = create_engine(_settings.database_url, future=True)
enable_sqlite_fk(_engine)
Base.metadata.create_all(_engine)
SessionFactory = sessionmaker(bind=_engine, future=True)

_svc = AttentionService(SessionFactory)


@router.get("/attention")
async def list_attention(content_id: str | None = None,
                         include_recovery_scan: bool = True) -> dict:
    """聚合后的待处理清单。

    - `major`：影响已批准版本的改动，必须优先处理
    - `items`：同类异常已折叠，但 `count` / `first_seen` / `last_seen` 保留
    - `folded_logs`：连续重复日志折叠后的结果

    `include_recovery_scan=false` 可跳过租约/孤儿文件/缺图扫描
    （扫描较慢，页面轮询时可关掉）。
    """
    rep = _svc.collect(content_id=content_id,
                       include_recovery_scan=include_recovery_scan)
    data = rep.as_dict()
    data["capabilities"] = {
        "read_only": True,
        "auto_actions": [],
        "note": ("本接口不提供自动批准/发布/重试/删除；"
                 "写操作仅有 acknowledge 与 resolve 两个记录动作"),
    }
    return data


@router.get("/attention/handling-stats")
async def handling_stats() -> dict:
    """人工打断时长统计。**缺失记录单列，不并进平均、不当成 0**。"""
    return _svc.handling_stats()


@router.post("/attention/acknowledge")
async def acknowledge(
    kind: str = Body(..., embed=True),
    entity_id: str = Body(..., embed=True),
    actor: str | None = Body(default=None, embed=True),
) -> dict:
    """标记"人已经看到这条了"。此时开始计处理时长。"""
    return _svc.acknowledge(kind, entity_id, actor=actor)


@router.post("/attention/resolve")
async def resolve(
    kind: str = Body(..., embed=True),
    entity_id: str = Body(..., embed=True),
    actor: str | None = Body(default=None, embed=True),
    note: str = Body(default="", embed=True),
) -> dict:
    """标记"已处理完"。

    找不到对应的 acknowledge 记录时，`handling_seconds` 返回 **null**
    并在 `handling_note` 里说明原因——不拿当前时间硬凑一个数。
    """
    return _svc.resolve(kind, entity_id, actor=actor, note=note)
