"""研究、自动生产与改稿接口（P2/T08–T11）。

对照 docs/03-data-and-api.md §6：

| 接口 | 用途 | 本研究实现状态 |
|---|---|---|
| POST /batches/{id}/runs | 启动自动生产，mode=real/fixture | **已接线，见 `api/batches.py`（P3/T13 补上）** |
| GET /runs/{id} | 状态、产物、异常与成本 | ✅ |
| POST /contents/{id}/research | 只跑研究与证据阶段 | ✅ |
| POST /contents/{id}/change-requests | 用户一句话修改，带 base_revision_id | ✅ |

关于 `/batches/{id}/runs` 的沿革：P2 阶段批次表已在 P1 建好，但单条内容的
完整生产线是 P2 的主要工作量，当时先用 `/contents/produce` 表达同一语义，
并在注释里明确登记"批次级调度尚未接线"。**P3/T13 的 Worker 落地后，
该接口已在 `api/batches.py` 实现**（入队 Job，由独立 Worker 领取执行）。
本模块保留 `/contents/produce` 作为"直接跑一条"的同步入口。

没有任何接口会自动发布；不存在 POST /auto-publish。
"""
from __future__ import annotations

from fastapi import APIRouter, Body
from pathlib import Path
import hashlib
import json
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..core.config import get_settings
from ..core.errors import NotFound, ValidationFailed, StateConflict
from ..models.entities import (
    Base,
    ContentItem,
    ContentRevision,
    Job,
    PlatformRevision,
    ProviderCallRow,
    ProviderExchange,
    Event,
    Run,
    enable_sqlite_fk,
)
from ..services.production_service import ProductionService
from ..services.profile_store import ProfileStore
from ..services.provider_contract import ProviderStore, RunMode, SecretStore
from ..services.provider_runtime import ProviderRuntime
from ..services.content_forms import CreativeBrief

router = APIRouter(tags=["production"])
_settings = get_settings()

_engine = create_engine(_settings.database_url, future=True)
enable_sqlite_fk(_engine)
Base.metadata.create_all(_engine)
SessionFactory = sessionmaker(bind=_engine, future=True)

_store = ProviderStore(_settings.storage_root / "provider_configs.json")
_secrets = SecretStore(_settings.secret_store_path)
_runtime = ProviderRuntime(_store, _secrets)
_svc = ProductionService(SessionFactory, runtime=_runtime, profiles=ProfileStore())


class RebuildInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_revision_id: str
    request_id: UUID
    requirements: str = Field(min_length=3, max_length=8000)
    materials: str = Field(default='', max_length=16000)
    run_mode: RunMode = RunMode.REAL
    media_ids: list[str] = Field(default_factory=list,max_length=6)
    image_policy: str = Field(default='auto',pattern=r'^(auto|diagram)$')
    creative_brief: CreativeBrief | None = None


def _inherited_materials(base) -> list[dict]:
    """改稿时沿用上一版里**用户自己提供**的资料。

    改稿最常见的用法就是只写一句修改要求，不重新粘贴资料。资料基础不该因为
    「这次没再贴一遍」而丢掉——丢了研究阶段就得回退到自动检索，而检索召回
    并不总是能把原题找回来，改稿就会整条失败（用户看到的是「改不了稿」）。
    只用原稿里 `kind=user_provided` 的正文，检索摘要不入库、也不在这里复用。
    """
    out: list[dict] = []
    total = 0
    for src in (base.claims_json or {}).get("sources", []):
        if src.get("kind") != "user_provided":
            continue
        text = (src.get("excerpt") or "").strip()
        if not text or total + len(text) > 16000:
            continue
        out.append({"text": text, "kind": "user_provided"})
        total += len(text)
    return out


@router.post('/contents/{content_id}/rebuild', status_code=202)
def rebuild(content_id: str, payload: RebuildInput):
    """Research and rewrite a new revision; retain prior reviews and artifacts."""
    digest = hashlib.sha256(json.dumps({'content_id':content_id, **payload.model_dump(mode='json')}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    with SessionFactory() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        event = s.query(Event).filter_by(entity_type='rebuild', entity_id=str(payload.request_id), type='rebuild_enqueued').first()
        if event:
            if event.payload['input_hash'] != digest:
                raise StateConflict('这次重做已提交，不能覆盖其要求')
            return {**event.payload['result'], 'reused':True}
        item = s.get(ContentItem, content_id)
        if not item:
            raise NotFound('内容不存在')
        if item.active_revision_id != payload.base_revision_id:
            raise StateConflict('基线版本已变化，请刷新预览后重做')
        if s.query(Run).filter(Run.content_id==content_id,Run.state.in_(['queued','running'])).first():
            raise StateConflict('这条内容已有任务执行中，请等待完成')
        if s.query(ProviderExchange).filter(ProviderExchange.content_id==content_id,ProviderExchange.state.in_(['unknown','in_flight'])).first():
            raise StateConflict('该内容存在结果未确认的模型调用，请先处理异常，避免重复扣费')
        if payload.run_mode==RunMode.REAL:
            cfg=_runtime.text_provider()
            if not cfg or not cfg.model_id or not cfg.secret_ref or not _runtime.secrets.get(cfg.secret_ref):
                raise ValidationFailed('请先配置可用的文字模型')
        run=Run(content_id=content_id,stage='produce',state='queued',mode=payload.run_mode.value,attempt=0)
        s.add(run);s.flush()
        base = s.get(ContentRevision, payload.base_revision_id)
        materials=([{'text':payload.materials,'kind':'user_provided'}] if payload.materials.strip()
                   else (_inherited_materials(base) if base else []))
        request={'content_id':content_id,'topic':item.topic,'topic_locked':True,
            'run_mode':payload.run_mode.value,'platforms':['douyin','xiaohongshu'],'render':True,
            'user_materials':materials,'user_requirements':payload.requirements}
        request.update(base_revision_id=payload.base_revision_id,media_ids=payload.media_ids,
            image_policy=payload.image_policy)
        from ..services.content_forms import build_brief
        request['creative_brief']=(payload.creative_brief or build_brief(topic=item.topic,requirements=payload.requirements)).model_dump(mode='json')
        from ..services.content_skills import ContentSkills
        skills=ContentSkills(SessionFactory,_runtime)
        # Attach the revision policy to the actual queued rewrite, not a no-op clone.
        request['user_requirements']+=skills.instructions('revision')
        s.add(Job(run_id=run.id,stage='batch_dispatch',state='queued',input_hash=digest,
                  output_refs={'request':request},attempt=0))
        result={'run_id':run.id,'content_id':content_id,'state':'queued','reused':False}
        s.add(Event(entity_type='rebuild',entity_id=str(payload.request_id),type='rebuild_enqueued',actor='coisini',run_mode=payload.run_mode.value,
            payload={'input_hash':digest,'base_revision_id':payload.base_revision_id,'requirements':payload.requirements,'result':result}))
        s.commit()
    skills.record(result['run_id'],'revision',state='queued',content_id=content_id,
        inputs={'requirements':payload.requirements,'base_revision_id':payload.base_revision_id,'run_mode':payload.run_mode.value},output=result)
    return result


@router.post('/contents/{content_id}/illustrate', status_code=202)
async def illustrate(content_id: str, payload: dict = Body(...)) -> dict:
    """Queue an illustrated revision of an existing draft, bound to its baseline."""
    try:
        mode = RunMode(payload.get('run_mode') or 'local_seed')
    except ValueError:
        raise ValidationFailed('未知运行模式')
    if mode == RunMode.REAL and _runtime.text_provider() is None:
        raise ValidationFailed('请先配置文字模型，再制作真实图解稿')
    base = payload.get('base_revision_id')
    platforms = payload.get('platforms') or ['douyin', 'xiaohongshu']
    if not isinstance(platforms, list) or not platforms or len(set(platforms)) != len(platforms) or any(p not in {'douyin','xiaohongshu'} for p in platforms):
        raise ValidationFailed('请选择有效且不重复的平台')
    with SessionFactory() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        item = s.get(ContentItem, content_id)
        if not item:
            raise NotFound('内容不存在')
        if not base or item.active_revision_id != base:
            raise StateConflict('稿件版本已变化，请刷新预览后再制作图解')
        active = s.query(Run).filter(Run.content_id == content_id, Run.state.in_(['queued','running'])).first()
        if active:
            if active.stage == 'illustrate' and (active.output_refs or {}).get('base_revision_id') == base:
                return {'run_id':active.id,'content_id':content_id,'state':active.state,'reused':True}
            raise StateConflict('这条内容已有生产任务，请等待完成')
        if s.query(ProviderExchange).filter_by(content_id=content_id, state='unknown').first():
            raise StateConflict('此前模型调用结果未知，请核查服务商记录后再生成')
        run = Run(content_id=content_id,stage='illustrate',state='queued',mode=mode.value,
                  output_refs={'base_revision_id':base})
        s.add(run); s.flush()
        request = {'operation':'illustrate','content_id':content_id,'base_revision_id':base,
                   'run_mode':mode.value,'platforms':platforms}
        job = Job(run_id=run.id,stage='batch_dispatch',state='queued',input_hash=f'illustrate:{base}',
                  output_refs={'request':request})
        s.add(job); s.flush()
        s.add(Event(entity_type='content_item',entity_id=content_id,type='illustration_enqueued',
                    actor='coisini',run_mode=mode.value,payload={'base_revision_id':base,'run_id':run.id}))
        result = {'run_id':run.id,'content_id':content_id,'state':'queued','queued_job_id':job.id,'run_mode':mode.value}
        s.commit()
    from .batches import _worker_running
    result['worker_running'] = _worker_running()
    return result


def _mode(raw: str | None) -> RunMode:
    try:
        return RunMode(raw or RunMode.LOCAL_SEED.value)
    except ValueError:
        return RunMode.LOCAL_SEED


@router.post("/contents/produce", status_code=202)
async def produce(payload: dict = Body(...)) -> dict:
    """启动自动生产：研究 → 选题 → 母稿/双平台改写 → 渲染。

    - `run_mode=fixture` 或 `local_seed` 时**不产生任何真实调用与费用**。
    - `run_mode=real` 需要已配置 Provider，否则研究阶段降级为已有资料。
    - 库存达上限时返回 `paused=true`，这是明确暂停，不是失败。

    **`seed_path` 是可选但强烈建议的参数。** 不传时研究阶段没有任何来源，
    选题的硬条件 `materials_available` 不会通过，链路会停在选题阶段
    （这是**如实**的阻塞，不是 bug —— 没有材料就不该硬编内容）。
    想用已有 seed 一键出成品，请调 `/contents/produce-from-seed`。

    返回 202 + run_id。失败时返回 `partial=true` 与 `blocked_stage`，
    已完成的阶段保留在响应里。
    """
    run_mode = _mode(payload.get("run_mode"))
    result = _svc.produce(
        topic=payload.get("topic") or "",
        content_id=payload.get("content_id"),
        seed_path=payload.get("seed_path"),
        run_mode=run_mode,
        platforms=tuple(payload.get("platforms") or ("douyin", "xiaohongshu")),
        render=bool(payload.get("render", True)),
        profile_version_ids=payload.get("profile_version_ids"),
        user_materials=payload.get("user_materials"),
        max_repair_rounds=payload.get("max_repair_rounds"),
    )
    result["integration_status"] = "integration_pending"
    if run_mode == RunMode.REAL and result.get("real_calls_recorded", 0) == 0:
        result.setdefault(
            "notice",
            "run_mode=real 但未记录到真实调用：可能未配置 Provider，本次依据为已有资料",
        )
    return result


@router.post("/contents/produce-from-seed", status_code=202)
async def produce_from_seed(payload: dict = Body(default={})) -> dict:
    """用已有 seed 一键出成品（**离线基线，零外部依赖**）。

    这是「先看到效果」最短的路径：不需要配 Provider、不需要配搜索、
    不产生任何真实调用与费用，直接产出双平台图文成品。

    - `seed_path` 不传时用项目自带的 C001。
    - `run_mode` 强制为 `local_seed` —— 这条路径**不接**真实 Provider，
      免得"随手跑一下"变成意外计费。
    - 响应里如实标注 `integration_status=integration_pending`：
      跑通证明的是**工程链路可用**，不是**真实平台效果**。
    """
    if payload.get("run_mode") not in (None, "local_seed"):
        raise ValidationFailed(
            "produce-from-seed 只走 local_seed 离线基线，不接受其它 run_mode；"
            "需要真实调用请用 /contents/produce 并显式传 run_mode=real"
        )
    seed = payload.get("seed_path") or ""
    if seed and not Path(seed).exists():
        # seed 是本地路径，路径不存在时给出可用清单，而不是让调用方猜
        raise NotFound(
            f"seed 不存在：{seed}",
            details={"available": [str(p) for p in sorted(
                get_settings().examples_dir.glob("*/seeds/*/seed.json"))]},
        )
    result = _svc.produce_from_seed(
        seed_path=seed,
        content_id=payload.get("content_id"),
        render=bool(payload.get("render", True)),
    )
    result["integration_status"] = "integration_pending"
    result["offline_baseline"] = True
    result.setdefault(
        "notice",
        "本次为离线基线：零真实调用、零费用。跑通只证明工程链路可用，"
        "不代表真实平台效果；真实效果需发布后采集数据再复盘。",
    )
    return result


@router.post("/contents/{content_id}/research")
async def research(content_id: str, payload: dict = Body(default={})) -> dict:
    """只跑研究阶段，或补录用户直接提供的材料。"""
    return _svc.research_only(
        content_id,
        seed_path=payload.get("seed_path"),
        user_materials=payload.get("user_materials"),
        run_mode=_mode(payload.get("run_mode")),
    )


@router.post("/contents/{content_id}/change-requests", status_code=201)
async def change_request(content_id: str, payload: dict = Body(...)) -> dict:
    """一句话修改：新建 revision 并生成独立平台产物；旧批准不被覆盖。"""
    base = payload.get("base_revision_id")
    if not base:
        raise NotFound("必须提供 base_revision_id（修改必须绑定具体基线版本）")
    return _svc.change_request(
        content_id, base_revision_id=base,
        instruction=payload.get("instruction") or "",
        run_mode=_mode(payload.get("run_mode")),
    )


@router.post("/runs/{run_id}/control")
async def control_run(run_id: str, payload: dict = Body(...)) -> dict:
    action = payload.get("action")
    if action not in {"pause", "resume", "cancel", "retry"}:
        raise ValidationFailed("任务操作只支持 pause/resume/cancel/retry")
    with SessionFactory() as s:
        s.connection().exec_driver_sql("BEGIN IMMEDIATE")
        run = s.get(Run, run_id)
        job = s.query(Job).filter_by(run_id=run_id, stage="batch_dispatch").one_or_none()
        if not run or not job:
            raise NotFound("没有可调度的批次任务")
        if action == "retry":
            if job.state != "failed" or run.state != "failed" or job.lease_owner is not None:
                raise StateConflict("只能恢复已停止的失败任务")
            if not (job.output_refs or {}).get("request"):
                raise StateConflict("任务没有可恢复的原始请求")
            unknown = s.query(ProviderExchange).filter_by(content_id=run.content_id, state="unknown").first()
            if unknown:
                raise StateConflict("存在结果未知的模型请求，禁止重发；请先核查服务商记录")
            previous_error = run.error
            job.state = run.state = "queued"
            job.error = run.error = None
            run.blocked_stage = None
            s.add(Event(entity_type="run", entity_id=run.id, type="run_retry_requested",
                actor="coisini", run_mode=run.mode,
                payload={"previous_error": previous_error,
                         "note": "显式恢复失败任务，复用已持久化的阶段和模型结果"}))
        elif action == "resume":
            if job.state != "paused" or job.lease_owner is not None:
                raise StateConflict("任务尚未停止到可恢复边界，或不是暂停状态")
            job.state = run.state = "queued"
        else:
            if job.state not in {"queued", "running", "paused"}:
                raise StateConflict("任务已结束")
            job.state = run.state = "paused" if action == "pause" else "cancelled"
        s.commit()
        return {"id":run.id,"state":run.state,"note":"已保存操作；进行中的请求在返回后停止，已完成产物保留"}


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    """Run 详情：阶段状态、产物引用、异常与成本。"""
    with SessionFactory() as s:
        run = s.get(Run, run_id)
        if run is None:
            raise NotFound(f"Run 不存在：{run_id}")
        jobs = (
            s.query(Job).filter_by(run_id=run.id).order_by(Job.started_at).all()
        )
        calls = (
            s.query(ProviderCallRow).filter_by(content_id=run.content_id).all()
            if run.content_id else []
        )
        content = s.get(ContentItem, run.content_id) if run.content_id else None
        return {
            "id": run.id,
            "content_id": run.content_id,
            "display_id": content.display_id if content else None,
            "stage": run.stage,
            "mode": run.mode,
            "state": run.state,
            "blocked_stage": run.blocked_stage,
            "attempt": run.attempt,
            "fencing_token": run.fencing_token,
            "error": run.error,
            "created_at": run.created_at.isoformat(),
            "jobs": [
                {"stage": j.stage, "state": j.state, "attempt": j.attempt,
                 "input_hash": j.input_hash[:16], "output_refs": j.output_refs,
                 "error": j.error,
                 "started_at": j.started_at.isoformat() if j.started_at else None,
                 "finished_at": j.finished_at.isoformat() if j.finished_at else None}
                for j in jobs
            ],
            "provider_calls": [_call_public(c) for c in calls],
            "cost": _cost_summary(calls),
        }


@router.get("/contents/{content_id}/provider-calls")
async def content_provider_calls(content_id: str) -> dict:
    with SessionFactory() as s:
        calls = (
            s.query(ProviderCallRow).filter_by(content_id=content_id)
            .order_by(ProviderCallRow.started_at).all()
        )
        if s.get(ContentItem, content_id) is None:
            raise NotFound(f"内容不存在：{content_id}")
        return {
            "content_id": content_id,
            "items": [_call_public(c) for c in calls],
            "summary": _cost_summary(calls),
            "note": "金额为 NULL 表示未知，不等于 0；fixture 调用不计入真实成本",
        }


@router.get("/contents/{content_id}/platform-revisions")
async def list_platform_revisions(content_id: str) -> dict:
    """列出该内容当前 revision 下的平台稿（供预览页绑定版本）。"""
    with SessionFactory() as s:
        content = s.get(ContentItem, content_id)
        if content is None:
            raise NotFound(f"内容不存在：{content_id}")
        rows = (
            s.query(PlatformRevision)
            .filter_by(content_revision_id=content.active_revision_id)
            .order_by(PlatformRevision.platform).all()
        )
        return {
            "content_id": content_id,
            "active_revision_id": content.active_revision_id,
            "content_state": content.state,
            "items": [
                {"platform_revision_id": r.id, "platform": r.platform,
                 "version": r.version, "title": r.title, "caption": r.caption,
                 "page_count": len(r.pages_json.get("pages", [])),
                 "state": r.state, "manifest_hash": r.manifest_hash,
                 "profile_version_id": r.profile_version_id,
                 "artifact_count": len(r.artifacts)}
                for r in rows
            ],
        }


def _call_public(c: ProviderCallRow) -> dict:
    return {
        "id": c.id,
        "request_key": (c.request_key or "")[:16],
        "stage_prompt_version": c.prompt_version,
        "provider_name": c.provider_name,
        "model_id": c.model_id,
        "state": c.state,
        "run_mode": c.run_mode,
        "input_tokens": c.input_tokens,
        "output_tokens": c.output_tokens,
        "usage_raw": c.usage_raw,
        "billing_state": c.billing_state,
        "estimated_micro": c.estimated_micro,
        "reported_micro": c.reported_micro,
        "reconciled_micro": c.reconciled_micro,
        "currency": c.currency,
        "error_code": c.error_code,
        "error_message": c.error_message,
        "started_at": c.started_at.isoformat(),
    }


def _cost_summary(calls: list[ProviderCallRow]) -> dict:
    """成本汇总。未知与估算分开列，fixture 单独计数。"""
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
        "note": ("null 表示未知，不等于 0；"
                 "fixture 调用不产生真实消耗，未计入金额"),
    }
