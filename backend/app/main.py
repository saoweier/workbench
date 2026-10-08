"""FastAPI 应用装配。

范围：规格配置、Provider 设置、用量查询、seed 校验、健康检查、内容生产、
发布登记、数据导入、复盘与反馈；以及**静态前端托管**。

不包含自动发布；不存在 POST /auto-publish。
"""
from __future__ import annotations

import json
import time
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .core.config import get_settings
from .core.errors import AppError, NotFound

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="内容工作台 API（P0 工程契约阶段）",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STARTED_AT = time.time()


@app.exception_handler(AppError)
async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(ValueError)
async def _value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_FAILED", "message": str(exc), "details": {}}},
    )


@app.get("/api/v1/health", tags=["system"])
async def health() -> dict:
    """API / Worker / 存储可用性。不返回任何凭据。"""
    s = get_settings()
    worker = _worker_health(s)
    return {
        "api": {"status": "ok", "version": s.app_version, "pid": os.getpid(),
                "instance_id": os.environ.get("CWB_INSTANCE_ID"),
                "uptime_seconds": round(time.time() - STARTED_AT, 1)},
        "worker": worker,
        "storage": {
            "status": "ok" if s.storage_root.exists() else "error",
            "artifact_dir_writable": _writable(s.artifact_dir),
        },
        "cost_mode": s.cost_mode_default,
        "money_limit_set": s.batch_money_limit_default is not None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _worker_health(s) -> dict:
    """Worker 状态。心跳过期视为未运行——不把"曾经跑过"当"现在在跑"。"""
    path = s.storage_root / "worker.heartbeat"
    if not path.exists():
        return {"status": "not_running", "worker_id": None, "last_heartbeat": None,
                "note": "未检测到 Worker 心跳；启动 `python -m app.worker` 后任务才会推进"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        at = datetime.fromisoformat(data["at"])
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
    except Exception as exc:
        return {"status": "unknown", "worker_id": None,
                "last_heartbeat": None, "note": f"心跳文件不可解析：{exc}"}
    age = (datetime.now(timezone.utc) - at).total_seconds()
    running = age < 60
    return {
        "status": "running" if running else "stale",
        "worker_id": data.get("worker_id"),
        "instance_id": data.get("instance_id"),
        "last_heartbeat": data["at"],
        "age_seconds": round(age, 1),
        "note": ("Worker 正常" if running else
                 f"心跳已过期 {round(age)} 秒，视为未运行"),
    }


def _writable(p) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


from .api import batches as batches_api  # noqa: E402
from .api import contents as contents_api  # noqa: E402
from .api import production as production_api  # noqa: E402
from .api import profiles as profiles_api  # noqa: E402
from .api import providers as providers_api  # noqa: E402
from .api import seeds as seeds_api  # noqa: E402
from .api import usage as usage_api  # noqa: E402
from .api import attention as attention_api  # noqa: E402
from .api import publications as publications_api  # noqa: E402
from .api import imports as imports_api  # noqa: E402
from .api import comments as comments_api  # noqa: E402
from .api import reviews as reviews_api  # noqa: E402
from .api import feedback as feedback_api  # noqa: E402
from .api import creation as creation_api  # noqa: E402
from .api import accounts as accounts_api  # noqa: E402
from .api import douyin_publishing as douyin_publishing_api  # noqa: E402
from .api import videos as videos_api
from .api import content_skills as content_skills_api
from .api import studio as studio_api

app.include_router(seeds_api.router, prefix=settings.api_prefix)
app.include_router(profiles_api.router, prefix=settings.api_prefix)
app.include_router(providers_api.router, prefix=settings.api_prefix)
app.include_router(usage_api.router, prefix=settings.api_prefix)
app.include_router(contents_api.router, prefix=settings.api_prefix)
app.include_router(studio_api.router, prefix=settings.api_prefix)
app.include_router(production_api.router, prefix=settings.api_prefix)
app.include_router(batches_api.router, prefix=settings.api_prefix)
app.include_router(attention_api.router, prefix=settings.api_prefix)
app.include_router(publications_api.router, prefix=settings.api_prefix)
app.include_router(imports_api.router, prefix=settings.api_prefix)
app.include_router(comments_api.router, prefix=settings.api_prefix)
app.include_router(reviews_api.router, prefix=settings.api_prefix)
app.include_router(feedback_api.router, prefix=settings.api_prefix)
app.include_router(creation_api.router, prefix=settings.api_prefix)
app.include_router(accounts_api.router, prefix=settings.api_prefix)
app.include_router(douyin_publishing_api.router, prefix=settings.api_prefix)
app.include_router(videos_api.router,prefix=settings.api_prefix)
app.include_router(content_skills_api.router,prefix=settings.api_prefix)


# ============================================================ 静态前端托管
#
# 前端是**无构建**的静态页面（原生 HTML/CSS/JS），所以这里直接托管，
# 不需要 node/npm。一个进程同时提供 API 与界面，启动脚本因此只需拉起一个服务。
#
# 挂载顺序有讲究：`/api` 路由先注册，静态文件**最后**挂到 `/`，
# 否则 catch-all 会把 API 请求吞掉。

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


@app.get("/", include_in_schema=False)
async def ui_index() -> FileResponse:
    """工作台首页。"""
    return FileResponse(_FRONTEND / "views" / "index.html")


@app.get("/views/{page}.html", include_in_schema=False)
async def ui_page(page: str) -> FileResponse:
    """各功能页。只允许字母数字与连字符，避免路径穿越。"""
    if not page.replace("-", "").isalnum():
        raise NotFound(f"页面名非法：{page}")
    path = _FRONTEND / "views" / f"{page}.html"
    if not path.exists():
        raise NotFound(f"页面不存在：{page}")
    return FileResponse(path)


if (_FRONTEND / "assets").exists():
    app.mount("/assets", StaticFiles(directory=_FRONTEND / "assets"), name="assets")
