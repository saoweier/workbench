"""发布记录接口（P4/T16）。

对应 `docs/03-data-and-api.md` §5 的 `POST /publications` 与不变量第 2、6 条。

**为什么没有 `POST /auto-publish`：**

文档写得很直白："不存在 POST /auto-publish。" 这并不是"还没做"，
而是一条设计决定——**发布这个动作必须由人完成，系统只负责记录人做过的事**。

所以本模块的接口在设计上就做了两件事：
1. `POST /publications` 要求 `registered_by` 来自可信会话（接口层固定填 `user`，
   不接受客户端传 `system`）；
2. 没有任何接口能"把状态改成已发布"。"已发布"是查询时根据 publication 表**算出来的**，
   不是一个可以被写过去的字段。

路由一览：
    GET  /publications                       发布记录列表（含两平台覆盖情况）
    GET  /publications/summary               某条内容的发布覆盖摘要
    POST /publications                       人工登记一次发布
    POST /publications/{id}/verify           记录一次人工核验结果
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..core.config import get_settings
from ..models.entities import Base, enable_sqlite_fk
from ..services.publication_service import (
    PublicationError,
    PublicationService,
    observation_windows,
)

router = APIRouter(tags=["publications"])
_settings = get_settings()

_engine = create_engine(_settings.database_url, future=True)
enable_sqlite_fk(_engine)
Base.metadata.create_all(_engine)
SessionFactory = sessionmaker(bind=_engine, future=True)

_svc = PublicationService(SessionFactory, _settings)

#: 错误码 → HTTP 状态码。业务校验失败多半是 400/404/409，不是 500。
_STATUS_MAP = {
    "PLATFORM_REVISION_NOT_FOUND": 404,
    "PUBLICATION_NOT_FOUND": 404,
    "PUBLISHER_MUST_BE_HUMAN": 403,
    "VERIFIER_MUST_BE_HUMAN": 403,
    "PUBLICATION_NEEDS_IDENTIFIER": 400,
    "BAD_DECLARATION_SOURCE": 400,
    "BAD_RUN_MODE": 422,
    "BAD_VERIFIED_STATE": 400,
}


def _fail(err: PublicationError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_MAP.get(err.code, 400),
        detail={"code": err.code, "message": err.message, "fields": err.fields},
    )


@router.get("/publications")
async def list_publications(content_id: str | None = None,
                            platform: str | None = None) -> dict:
    """发布记录。`declared` 与 `verified` 分开返回，**不合并成一个"已发布"**。"""
    data = _svc.list_publications(content_id=content_id, platform=platform)
    data["capabilities"] = {
        "auto_publish": False,
        "note": (
            "不存在 auto-publish。系统不会通过下载、定时器或模型推断标记已发布；"
            "「已发布」由 publication 表现算，不是一个可被写入的字段。"
        ),
    }
    return data


@router.get("/publications/summary")
async def publication_summary(content_id: str) -> dict:
    """某条内容的发布覆盖摘要（供 `awaiting_data` 判定使用）。"""
    return _svc.publication_summary(content_id)


@router.post("/publications")
async def register_publication(payload: dict = Body(...)) -> dict:
    """人工登记发布记录。

    `registered_by` 由服务端固定为 `user`——**接口不接收客户端传入的登记人**，
    这是不变量第 6 条在接口层的落点（模型/客户端无法伪装成人工操作）。
    """
    pr_id = payload.get("platform_revision_id")
    if not pr_id:
        raise HTTPException(status_code=422, detail={
            "code": "MISSING_PLATFORM_REVISION_ID",
            "message": "必须指定 platform_revision_id（发布的是哪一个平台版本）",
        })

    published_at = payload.get("published_at")
    parsed_at: datetime | None = None
    if published_at:
        try:
            parsed_at = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=422, detail={
                "code": "BAD_PUBLISHED_AT",
                "message": "published_at 需要 ISO 8601 格式，例如 2026-10-02T09:30:00+08:00",
            })

    try:
        out = _svc.register(
            platform_revision_id=str(pr_id),
            link=payload.get("link"),
            platform_post_id=payload.get("platform_post_id"),
            published_at=parsed_at,
            registered_by="user",          # ← 固定，不读客户端
            account_id=payload.get("account_id"),
            declaration_source=payload.get("declaration_source", "manual"),
            note=payload.get("note"),
            run_mode=payload.get("run_mode", "real"),
        )
    except PublicationError as e:
        raise _fail(e)

    data = out.as_dict()

    # 顺带把观察窗口一并返回：24h/72h/7d 是标准口径，没到点就是没到点
    with SessionFactory() as s:
        from ..models.entities import Publication
        pub = s.get(Publication, out.publication_id)
        data["observation_windows"] = observation_windows(pub.published_at if pub else None)

    return data


@router.post("/publications/{publication_id}/verify")
async def verify_publication(publication_id: str, payload: dict = Body(default={})) -> dict:
    """记录一次人工核验结果。

    **只记录，不去抓平台。** 系统不会替人去打开链接确认——
    那需要真实网络+登录态，也正是 P4 业务验收需要人工补齐的部分。
    """
    state = payload.get("verified_state")
    if not state:
        raise HTTPException(status_code=422, detail={
            "code": "MISSING_VERIFIED_STATE",
            "message": "必须给出 verified_state（confirmed / mismatch / inaccessible）",
        })
    try:
        return _svc.verify(
            publication_id,
            verified_state=str(state),
            verified_by="user",
            note=payload.get("note"),
        )
    except PublicationError as e:
        raise _fail(e)
