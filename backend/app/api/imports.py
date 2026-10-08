"""数据导入接口（P4/T17）。

对应 `docs/03-data-and-api.md` §5 的 `POST /imports` / `GET /imports/{id}`、
`POST /imports/{id}/mapping`，以及 §7 的模板契约。

设计要点：
- **未知映射不猜**。识别不出的列进 `unmapped_columns`，不擅自归类。
- **缺失 ≠ 0**。空值以 `null` 存储，接口原样返回 `null`。
- **待匹配行不报错**，单独列出等人绑定；整批不会因为一行对不上而失败。
- 同一文件 + 同一映射版本重复导入 **幂等**。

路由一览：
    GET  /imports                     导入批次列表
    POST /imports                     上传数据（CSV / JSON 文本）
    GET  /imports/{id}                批次详情（含解析错误与快照）
    POST /imports/{id}/match          把待匹配行人工绑到发布记录
    GET  /publications/{id}/metrics   某条发布记录的指标序列
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..core.config import get_settings
from ..models.entities import Base, enable_sqlite_fk
from ..services.import_service import (
    MAPPING_VERSION,
    STANDARD_FIELDS,
    ImportError_,
    ImportService,
)

router = APIRouter(tags=["imports"])
_settings = get_settings()

_engine = create_engine(_settings.database_url, future=True)
enable_sqlite_fk(_engine)
Base.metadata.create_all(_engine)
SessionFactory = sessionmaker(bind=_engine, future=True)

_svc = ImportService(SessionFactory, _settings)

_STATUS_MAP = {
    "IMPORT_NOT_FOUND": 404,
    "PUBLICATION_NOT_FOUND": 404,
    "ROW_NOT_UNMATCHED": 409,
    "NO_METRIC_COLUMNS": 422,
    "BAD_JSON_SHAPE": 422,
}


def _fail(err: ImportError_) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_MAP.get(err.code, 400),
        detail={"code": err.code, "message": err.message, "fields": err.fields},
    )


@router.get("/imports")
async def list_imports(kind: str | None = None) -> dict:
    """导入批次列表。附标准字段与映射版本，便于核对"这批是怎么解释的"。"""
    data = _svc.list_imports(kind=kind)
    data["capabilities"] = {
        "accepted_formats": ["csv", "json"],
        "metrics_template": list(STANDARD_FIELDS),
        "mapping_version": MAPPING_VERSION,
        "note": (
            "标准长表字段如上。也可用平台后台导出的宽表"
            "（列名如「播放量 / 点赞数」），系统会折成长表。"
        ),
    }
    return data


@router.post("/imports")
async def create_import(payload: dict = Body(...)) -> dict:
    """上传数据。

    请求体：
    ```json
    {
      "kind": "metrics",
      "content": "<CSV 或 JSON 文本>",
      "file_name": "douyin-export.csv",
      "format": "csv",
      "dry_run": false
    }
    ```

    `content` 直接传文本。真实场景是上传文件，这里把"取文本"的责任放在调用方，
    服务端只负责解析与规范化——便于测试，也避免把文件 IO 混进业务逻辑。
    """
    kind = payload.get("kind", "metrics")
    content = payload.get("content")
    if not content or not str(content).strip():
        raise HTTPException(status_code=422, detail={
            "code": "EMPTY_CONTENT", "message": "content 不能为空",
        })
    if kind != "metrics":
        # 评论导入走 /comments/imports（T18），这里不做两件事混在一个入口
        raise HTTPException(status_code=422, detail={
            "code": "UNSUPPORTED_KIND",
            "message": "kind 目前只支持 metrics；评论请走 /comments/imports",
        })

    fmt = payload.get("format", "csv")
    if fmt not in ("csv", "json"):
        raise HTTPException(status_code=422, detail={
            "code": "BAD_FORMAT", "message": "format 只支持 csv / json",
        })

    try:
        out = _svc.import_metrics(
            str(content),
            file_name=payload.get("file_name"),
            fmt=fmt,
            created_by="user",
            run_mode=payload.get("run_mode", "fixture"),
            dry_run=bool(payload.get("dry_run", False)),
        )
    except ImportError_ as e:
        raise _fail(e)

    return out.as_dict()


@router.get("/imports/{import_id}")
async def get_import(import_id: str) -> dict:
    try:
        return _svc.get_import(import_id)
    except ImportError_ as e:
        raise _fail(e)


@router.post("/imports/{import_id}/match")
async def match_unmatched(import_id: str, payload: dict = Body(...)) -> dict:
    """把待匹配行人工绑定到发布记录。

    **这是人工动作。** 系统不会自动猜绑定——"这行数据大概是哪条作品的"
    是猜测，猜错会污染整个复盘。
    """
    row_no = payload.get("row_no")
    pub_id = payload.get("publication_id")
    if row_no is None or not pub_id:
        raise HTTPException(status_code=422, detail={
            "code": "MISSING_FIELDS",
            "message": "需要 row_no 与 publication_id",
        })
    try:
        return _svc.match_unmatched(import_id, row_no=int(row_no),
                                    publication_id=str(pub_id), matched_by="user")
    except ImportError_ as e:
        raise _fail(e)


@router.get("/publications/{publication_id}/metrics")
async def publication_metrics(publication_id: str) -> dict:
    """某条发布记录的指标序列（按采集时间）。"""
    return _svc.snapshots_for_publication(publication_id)
