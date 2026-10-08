"""反馈下一轮接口（P4/T20）。

对应 `docs/03-data-and-api.md` §5，以及不变量第 6、8 条。

## 这个模块的接口设计只做一件事：把"自动"关进笼子

不变量第 6 条：**模型无法直接更改批准、预算上限、身份、连接权限和发布状态。**
这里再加一条实际的：**模型也不能直接改选题参数。**

所以本模块：
- `POST /feedback` 产出的永远是 `proposed`（建议），**不是"已生效"**
- `POST /feedback/{id}/adopt` 是**唯一**能让建议生效的动作，且默认是人工的
- `auto=true` 只对 `tiny` / `small` 有效；`medium` / `large` 会被**降级为候选**
- `POST /feedback/{id}/revert` 让任何已采用的改动都能退回去
- `POST /feedback/batch-proposal` 同时检查三条并列限制，
  **金额上限为 null 时不阻塞，但也不因此放开条目数与库存**

路由一览：
    GET  /feedback                    建议列表
    POST /feedback                    登记一条建议（默认 proposed）
    GET  /feedback/drafts             从复盘起草候选（机械转写）
    POST /feedback/{id}/adopt         采用（auto 仅 tiny/small 生效）
    POST /feedback/{id}/reject        拒绝
    POST /feedback/{id}/revert        回退
    POST /feedback/batch-proposal     判断下一轮能否开工
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..core.config import get_settings
from ..models.entities import Base, enable_sqlite_fk
from ..services.feedback_service import (
    AUTO_ADOPTABLE,
    FEEDBACK_KINDS,
    MAGNITUDES,
    FeedbackService,
)

router = APIRouter(tags=["feedback"])
_settings = get_settings()

_engine = create_engine(_settings.database_url, future=True)
enable_sqlite_fk(_engine)
Base.metadata.create_all(_engine)
SessionFactory = sessionmaker(bind=_engine, future=True)

_svc = FeedbackService(SessionFactory, _settings)


def _bad_request(e: ValueError) -> HTTPException:
    msg = str(e)
    if "不存在" in msg:
        return HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": msg})
    return HTTPException(status_code=422, detail={"code": "INVALID_FEEDBACK", "message": msg})


@router.get("/feedback")
async def list_feedback(content_id: str | None = None,
                        status: str | None = None) -> dict:
    """建议列表。**这里看到的绝大多数条目都是 `proposed`——即"还没生效"。**"""
    data = _svc.list_feedback(content_id=content_id, status=status)
    data["capabilities"] = {
        "auto_apply": False,
        "human_approval_required_for": ["medium", "large"],
        "reversible": True,
        "note": (
            "建议默认不生效。系统只对 tiny/small 幅度允许自动采用；"
            "medium/large 一律需人工确认（传 auto=true 也会被降级为候选）。"
            "任何已采用的改动都可 revert 回退。"
        ),
    }
    return data


@router.post("/feedback")
async def create_feedback(payload: dict = Body(...)) -> dict:
    """登记一条建议。注意是「建议」，不是「改动」。

    必须带 `evidence_refs`——没有证据引用的建议不接受，
    否则它就不是从数据来的，只是凭空的想法。
    """
    if not payload.get("review_report_id"):
        raise HTTPException(status_code=422, detail={
            "code": "MISSING_REVIEW_REPORT", "message": "必须指定 review_report_id",
        })
    try:
        return _svc.propose(
            review_report_id=str(payload["review_report_id"]),
            kind=payload.get("kind", ""),
            proposal=payload.get("proposal", ""),
            magnitude=payload.get("magnitude", "small"),
            rationale=payload.get("rationale"),
            evidence_refs=payload.get("evidence_refs"),
            previous_value=payload.get("previous_value"),
            new_value=payload.get("new_value"),
            created_by="user",
            run_mode=payload.get("run_mode", "real"),
        )
    except ValueError as e:
        raise _bad_request(e)


@router.get("/feedback/drafts")
async def feedback_drafts(review_report_id: str) -> dict:
    """从复盘起草建议候选。

    **这是机械转写，不是决策。** 它把复盘里已有的评论分布、数据充分度、
    next_topics 映射成候选草案，全部 `needs_human_review=true`。
    系统不会替你判断"这个建议该不该做"。
    """
    try:
        return _svc.draft_from_review(review_report_id)
    except ValueError as e:
        raise _bad_request(e)


@router.post("/feedback/{feedback_id}/adopt")
async def adopt_feedback(feedback_id: str, payload: dict = Body(default={})) -> dict:
    """采用一条建议。

    `auto=true` 表示"按规则自动采用"。**只对 tiny/small 生效**；
    medium/large 会被降级为"仅保存候选"并返回说明，不会静默生效。
    """
    try:
        out = _svc.adopt(feedback_id, actor="user", auto=bool(payload.get("auto", False)))
    except ValueError as e:
        raise _bad_request(e)
    data = out.as_dict()
    data["scope"] = {
        "auto_adoptable_magnitudes": sorted(AUTO_ADOPTABLE),
        "note": "超出上述幅度的改动不会被自动采用，只会保留为候选。",
    }
    return data


@router.post("/feedback/{feedback_id}/reject")
async def reject_feedback(feedback_id: str, payload: dict = Body(default={})) -> dict:
    try:
        return _svc.reject(feedback_id, actor="user", reason=payload.get("reason"))
    except ValueError as e:
        raise _bad_request(e)


@router.post("/feedback/{feedback_id}/revert")
async def revert_feedback(feedback_id: str, payload: dict = Body(default={})) -> dict:
    """回退一条已采用的建议。**没有回退路径的自动化不是自动化，是事故。**"""
    try:
        return _svc.revert(feedback_id, actor="user")
    except ValueError as e:
        raise _bad_request(e)


@router.post("/feedback/batch-proposal")
async def batch_proposal(payload: dict = Body(...)) -> dict:
    """判断"下一轮能不能开工"。

    三条限制**并列**：批次条目数、待预览库存、金额上限（仅已设置时生效）。
    任一条触顶就只保存候选。

    **金额上限为 `null` 不阻塞 `usage_tracking`，但不等于无限额度，
    也不解除条目数与库存限制**（不变量第 8 条）。
    """
    try:
        prop = _svc.propose_batch(
            next_topics=payload.get("next_topics") or [],
            batch_id=payload.get("batch_id"),
            pending_review_stock=int(payload.get("pending_review_stock", 0)),
            pending_review_stock_limit=int(payload.get("pending_review_stock_limit", 3)),
        )
    except ValueError as e:
        raise _bad_request(e)
    data = prop.as_dict()
    data["kinds"] = list(FEEDBACK_KINDS)
    data["magnitudes"] = list(MAGNITUDES)
    return data
