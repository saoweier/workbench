"""评论导入与聚类接口（P4/T18）。

对应 `docs/03-data-and-api.md` §7 评论模板与 `docs/02-modules.md` §M10。

**这个模块的输出必须能被质疑。**
"30% 的评论是质疑"这种结论如果只给百分比不给原文和分母，人就只能信或不信。
所以 `GET /comments/cluster` 的每个分组都强制携带：
原文例证（`examples`）、样本量（`of_total`）、取样方式（`sampling_methods`）、
系统判断理由（`category_reason`），以及"这个分类可能出错"的明示。

路由一览：
    GET  /comments/categories      分类维度与中文标签
    GET  /comments                 已导入的评论（可过滤）
    POST /comments/imports         上传评论 CSV / JSON
    GET  /comments/cluster         按主题聚类（带原文例证与取样偏差）
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from ..core.config import get_settings
from ..models.entities import Base, CommentSample, Publication, enable_sqlite_fk
from ..services.comment_service import (
    CATEGORIES,
    CATEGORY_LABELS,
    COMMENT_FIELDS,
    CommentImportService,
)
from ..services.import_service import ImportError_

router = APIRouter(tags=["comments"])
_settings = get_settings()

_engine = create_engine(_settings.database_url, future=True)
enable_sqlite_fk(_engine)
Base.metadata.create_all(_engine)
SessionFactory = sessionmaker(bind=_engine, future=True)

_svc = CommentImportService(SessionFactory, _settings)

_STATUS_MAP = {
    "NO_COMMENT_TEXT_COLUMN": 422,
    "BAD_JSON_SHAPE": 422,
}


def _fail(err: ImportError_) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_MAP.get(err.code, 400),
        detail={"code": err.code, "message": err.message, "fields": err.fields},
    )


@router.get("/comments/categories")
async def categories() -> dict:
    """分类维度。规则与触发词一并返回，**便于人核对与提修改意见**。"""
    from ..services.comment_service import CATEGORY_RULES

    return {
        "categories": [
            {"key": k, "label": CATEGORY_LABELS[k]} for k in CATEGORIES
        ],
        "template_fields": list(COMMENT_FIELDS),
        "generation_mode": "rule_based",
        "rules": [
            {"category": cat, "keywords": list(kws), "rationale": why}
            for cat, kws, why in CATEGORY_RULES
        ],
        "note": (
            "分类由可读的关键词规则产生，不是模型推断。"
            "规则可以被检查和修改——如果你发现某类总被判错，"
            "改触发词比换模型更直接。规则命中顺序：无效 → 质疑 → 问题 → 需求 → 经验。"
        ),
    }


@router.get("/comments")
async def list_comments(publication_id: str | None = None,
                        category: str | None = None,
                        limit: int = 100) -> dict:
    """已导入的评论。**返回的是匿名 ID 与原文，不含任何用户名。**"""
    with SessionFactory() as s:
        stmt = select(CommentSample)
        if publication_id:
            stmt = stmt.where(CommentSample.publication_id == publication_id)
        if category:
            stmt = stmt.where(CommentSample.category == category)
        rows = list(s.scalars(stmt.order_by(CommentSample.created_at.desc()).limit(limit)))
        items = []
        for c in rows:
            pub = s.get(Publication, c.publication_id)
            items.append({
                "id": c.id,
                "anon_id": c.anon_id,
                "text": c.text,
                "category": c.category,
                "category_label": CATEGORY_LABELS.get(c.category or "", c.category),
                "category_reason": c.category_reason,
                "like_count": c.like_count,     # None = 缺失，不是 0
                "publication_id": c.publication_id,
                "platform": pub.platform if pub else None,
                "sampling_method": c.sampling_method,
                "observed_at": c.observed_at.isoformat() if c.observed_at else None,
            })
        return {
            "items": items,
            "total": len(items),
            "privacy_note": "仅存匿名 ID 的哈希与评论原文；不存公开用户名（按文档要求）。",
            "missing_note": "like_count 为 null 表示该条导出里没有点赞数，不是 0 赞。",
        }


@router.post("/comments/imports")
async def create_comment_import(payload: dict = Body(...)) -> dict:
    """上传评论。

    `sampling_method` **建议显式填写**。不填不会报错，但系统不会替它假设成"全量"，
    而是记成"未声明（口径不确定）"，并在聚类说明里提示其代表性未知。
    """
    content = payload.get("content")
    if not content or not str(content).strip():
        raise HTTPException(status_code=422, detail={
            "code": "EMPTY_CONTENT", "message": "content 不能为空",
        })
    fmt = payload.get("format", "csv")
    if fmt not in ("csv", "json"):
        raise HTTPException(status_code=422, detail={
            "code": "BAD_FORMAT", "message": "format 只支持 csv / json",
        })
    try:
        out = _svc.import_comments(
            str(content),
            file_name=payload.get("file_name"),
            fmt=fmt,
            sampling_method=payload.get("sampling_method"),
            sample_context=payload.get("sample_context"),
            created_by="user",
            run_mode=payload.get("run_mode", "fixture"),
        )
    except ImportError_ as e:
        raise _fail(e)
    return out.as_dict()


@router.get("/comments/cluster")
async def cluster(publication_id: str | None = None,
                  import_id: str | None = None) -> dict:
    """按主题聚类。

    **输出的是「归纳」不是「原文」**——所以每一组都带原文例证、样本量、
    取样方式和系统判断理由。只给比例的聚类结果无法被质疑，也就无法被信任。
    """
    return _svc.cluster(publication_id=publication_id, import_id=import_id)
