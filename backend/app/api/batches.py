"""批次调度接口（P3/T13）。

**补上 P2 的欠账。** P2 的 `api/production.py` 模块注释里明确写了：

> 关于 `/batches/{id}/runs`：P2 阶段批次表已在 P1 建好，但单条内容的完整
> 生产线是 P2 的主要工作量。这里先用 `/contents/produce` 表达同一语义，
> 待 P3 的 Worker 落地后再把批次级调度接上 `/batches/{id}/runs`。
> **不假装批次调度已经实现。**

现在 Worker 已经落地，这里把批次级调度接上。

## 批次语义

一个批次 = 一组内容条目 + 一份非金额限制（条目数、库存上限、成本模式）。

| 接口 | 用途 |
|---|---|
| `POST /batches` | 建批次（可指定 item_limit / cost_mode / 金额上限） |
| `GET /batches` | 列出批次与其条目 |
| `GET /batches/{id}` | 批次详情：条目、状态、成本、待处置异常 |
| `POST /batches/{id}/runs` | **启动本批次的自动生产**（入队 Job，交给 Worker） |
| `POST /batches/{id}/close` | 关闭批次 |

## 重要：入队 ≠ 立即执行

`POST /batches/{id}/runs` 返回 202 与 run_id，**不阻塞等成品**。
真正的执行由独立 Worker 领取 job 完成。这样 API 重启不会打断任务。

如果当前没有 Worker 在跑，接口会如实说明这一点，而不是假装任务已经在跑。
"""
from __future__ import annotations

from fastapi import APIRouter, Body
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..core.config import get_settings
from ..core.errors import NotFound, StateConflict, ValidationFailed
from ..models.entities import (
    Base,
    Batch,
    ContentItem,
    Event,
    Job,
    ProviderCallRow,
    Run,
    enable_sqlite_fk,
)
from ..services.production_service import ProductionService
from ..services.profile_store import ProfileStore
from ..services.provider_contract import CostMode, ProviderStore, RunMode, SecretStore
from ..services.provider_runtime import ProviderRuntime
from ..services.recovery_service import RecoveryService
from ..services.topic_service import PENDING_STATES

router = APIRouter(tags=["batches"])
_settings = get_settings()

_engine = create_engine(_settings.database_url, future=True)
enable_sqlite_fk(_engine)
Base.metadata.create_all(_engine)
SessionFactory = sessionmaker(bind=_engine, future=True)

_runtime = ProviderRuntime(
    ProviderStore(_settings.storage_root / "provider_configs.json"),
    SecretStore(_settings.secret_store_path),
)
_svc = ProductionService(SessionFactory, runtime=_runtime, profiles=ProfileStore())
_recovery = RecoveryService(SessionFactory)


def _batch_public(s, b: Batch) -> dict:
    items = s.query(ContentItem).filter_by(batch_id=b.id).all()
    return {
        "id": b.id,
        "profile_version_id": b.profile_version_id,
        "item_limit": b.item_limit,
        "cost_mode": b.cost_mode,
        "budget_limit_micro": b.budget_limit_micro,
        "budget_limit_meaning": ("null 表示未设置金额上限（不是 0 额度）"
                                 if b.budget_limit_micro is None else "已设置金额上限"),
        "currency": b.currency,
        "state": b.state,
        "created_at": b.created_at.isoformat(),
        "item_count": len(items),
        "items": [
            {"id": i.id, "display_id": i.display_id, "topic": i.topic,
             "state": i.state, "run_mode": i.run_mode,
             "active_revision_id": i.active_revision_id}
            for i in items
        ],
    }


@router.post("/batches", status_code=201)
async def create_batch(payload: dict = Body(default={})) -> dict:
    """建批次。金额上限可留空——**留空不等于 0 额度**。"""
    cost_mode = payload.get("cost_mode") or _settings.cost_mode_default
    item_limit = payload.get("item_limit", _settings.default_batch_item_limit)
    if not isinstance(item_limit, int) or isinstance(item_limit, bool) or not 1 <= item_limit <= 100:
        raise ValidationFailed("批次条目数必须为 1–100 的整数")
    if cost_mode not in {m.value for m in CostMode}:
        raise ValidationFailed(f"未知 cost_mode：{cost_mode}")
    limit = payload.get("budget_limit_micro", _settings.batch_money_limit_default)
    if limit is not None and limit < 0:
        raise ValidationFailed("金额上限不能为负数；未设置请传 null")

    with SessionFactory() as s:
        b = Batch(
            profile_version_id=payload.get("profile_version_id"),
            item_limit=item_limit,
            cost_mode=cost_mode,
            budget_limit_micro=limit,
            currency=payload.get("currency", _settings.currency_default),
            state="open",
        )
        s.add(b)
        s.commit()
        return _batch_public(s, b)


@router.get("/batches")
async def list_batches(limit: int = 20) -> dict:
    with SessionFactory() as s:
        rows = s.query(Batch).order_by(Batch.created_at.desc()).limit(limit).all()
        return {"items": [_batch_public(s, b) for b in rows],
                "total": s.query(Batch).count()}


@router.get("/batches/{batch_id}")
async def get_batch(batch_id: str) -> dict:
    with SessionFactory() as s:
        b = s.get(Batch, batch_id)
        if b is None:
            raise NotFound(f"批次不存在：{batch_id}")
        data = _batch_public(s, b)
        item_ids = [i["id"] for i in data["items"]]
        runs = (
            s.query(Run).filter(Run.content_id.in_(item_ids)).all()
            if item_ids else []
        )
        calls = (
            s.query(ProviderCallRow).filter(ProviderCallRow.content_id.in_(item_ids)).all()
            if item_ids else []
        )
        pending = 0
        for i in data["items"]:
            if i["state"] in PENDING_STATES:
                pending += 1
        data["runs"] = [
            {"id": r.id, "stage": r.stage, "state": r.state, "mode": r.mode,
             "blocked_stage": r.blocked_stage, "attempt": r.attempt,
             "created_at": r.created_at.isoformat()}
            for r in runs
        ]
        data["pending_review_count"] = pending
        data["pending_review_limit"] = _settings.pending_review_stock_limit
        data["cost"] = _cost(calls)
        data["non_money_limits"] = {
            "item_limit": b.item_limit,
            "pending_review_stock_limit": _settings.pending_review_stock_limit,
            "max_repair_rounds": _settings.max_repair_rounds,
            "max_calls_per_run": 40,
        }
        return data


@router.post("/batches/{batch_id}/runs", status_code=202)
async def start_batch_run(batch_id: str, payload: dict = Body(default={})) -> dict:
    """启动本批次的自动生产。

    入队 Job 后立即返回（202），**不阻塞等成品**。执行由独立 Worker 完成。

    当没有 Worker 在跑时会如实说明（`worker_running=false`），
    而不是让调用方以为任务已经在推进。
    """
    topic = (payload.get("topic") or "").strip()
    if not topic:
        raise ValidationFailed("必须提供 topic（本批次要生产的内容主题）")

    run_mode = _mode(payload.get("run_mode"))
    with SessionFactory() as s:
        b = s.get(Batch, batch_id)
        if b is None:
            raise NotFound(f"批次不存在：{batch_id}")
        if b.state != "open":
            raise StateConflict(f"批次状态为 {b.state}，不接受新任务")
        n_items = s.query(ContentItem).filter_by(batch_id=b.id).count()
        if n_items >= b.item_limit:
            raise StateConflict(
                f"批次条目数已达上限 {b.item_limit}（当前 {n_items}）；"
                "非金额限制与是否设置金额上限无关，始终生效"
            )

    # Persist the request before returning; no model or render call happens here.
    with SessionFactory() as s:
        s.connection().exec_driver_sql("BEGIN IMMEDIATE")
        b = s.get(Batch, batch_id)
        if b.state != "open" or s.query(ContentItem).filter_by(batch_id=batch_id).count() >= b.item_limit:
            raise StateConflict("批次已关闭或条目数已达上限")
        platforms = payload.get("platforms") or ["douyin", "xiaohongshu"]
        if not isinstance(platforms, list) or not platforms or any(p not in {"douyin", "xiaohongshu"} for p in platforms):
            raise ValidationFailed("请选择有效的平台")
        item = ContentItem(batch_id=batch_id, display_id=f"C{s.query(ContentItem).count()+1:03d}",
                           topic=topic, state="queued", run_mode=run_mode.value)
        s.add(item)
        s.flush()
        run = Run(content_id=item.id, stage="produce", state="queued", mode=run_mode.value, attempt=0)
        s.add(run)
        s.flush()
        request = {"topic": topic, "content_id": item.id, "run_mode": run_mode.value,
                   "seed_path": payload.get("seed_path"), "platforms": platforms,
                   "render": bool(payload.get("render", True)),
                   "profile_version_ids": payload.get("profile_version_ids"),
                   "user_materials": payload.get("user_materials")}
        job = Job(run_id=run.id, stage="batch_dispatch", state="queued",
                  input_hash=f"batch:{batch_id}:{item.id}", output_refs={"request": request}, attempt=0)
        s.add(job)
        s.flush()
        s.add(Event(entity_type="batch", entity_id=batch_id, type="batch_run_enqueued",
                    actor="coisini", run_mode=run_mode.value,
                    payload={"content_id": item.id, "job_id": job.id}))
        queued_job_id = job.id
        result = {"run_id": run.id, "content_id": item.id, "run_mode": run_mode.value,
                  "state": "queued", "stages": {}, "partial": False}
        s.commit()

    result["batch_id"] = batch_id
    result["queued_job_id"] = queued_job_id
    result["worker_running"] = _worker_running()
    result["note"] = (
        "已入队，等待 Worker 领取执行（API 不阻塞等成品）"
        if result["worker_running"] else
        "已入队，但当前**没有 Worker 在运行**：任务会一直停在 queued，"
        "需启动 `python -m app.worker` 才会推进"
    )
    return result


@router.post("/batches/{batch_id}/close")
async def close_batch(batch_id: str) -> dict:
    with SessionFactory() as s:
        b = s.get(Batch, batch_id)
        if b is None:
            raise NotFound(f"批次不存在：{batch_id}")
        b.state = "closed"
        s.commit()
        return {"id": b.id, "state": b.state,
                "note": "批次已关闭；已产出的内容不受影响，仍可预览与批准"}


def _worker_running() -> bool:
    """Worker 是否在跑：读心跳文件且判断它是否新鲜。

    过期心跳视为没在跑——**不把"曾经跑过"当成"现在在跑"**。
    """
    import json
    from datetime import datetime, timedelta, timezone

    path = _settings.storage_root / "worker.heartbeat"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        at = datetime.fromisoformat(data["at"])
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
    except Exception:
        return False
    return datetime.now(timezone.utc) - at < timedelta(seconds=60)


def _mode(raw: str | None) -> RunMode:
    try:
        return RunMode(raw or RunMode.LOCAL_SEED.value)
    except ValueError:
        raise ValidationFailed("未知运行模式")


def _cost(calls: list[ProviderCallRow]) -> dict:
    real = [c for c in calls if c.run_mode == "real"]
    fixture = [c for c in calls if c.run_mode == "fixture"]
    unknown = [c for c in real if c.billing_state == "unknown"]
    return {
        "total_calls": len(calls),
        "real_calls": len(real),
        "fixture_calls": len(fixture),
        "unknown_billing_calls": len(unknown),
        "estimated_micro": sum(c.estimated_micro or 0 for c in real) or None,
        "reported_micro": sum(c.reported_micro or 0 for c in real) or None,
        "reconciled_micro": sum(c.reconciled_micro or 0 for c in real) or None,
        "currency": next((c.currency for c in real if c.currency), None),
        "note": "null 表示未知，不等于 0；fixture 调用不产生真实消耗，未计入金额",
    }
