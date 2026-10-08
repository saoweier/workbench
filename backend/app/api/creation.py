"""Explicit topic creation with durable queueing and legacy route retirement."""
from __future__ import annotations

from uuid import UUID
import hashlib
import json

from fastapi import APIRouter,HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..core.errors import IdempotencyConflict, ValidationFailed
from ..models.entities import Batch, ContentItem, Event, Job, Run, ProviderExchange
from ..services.trend_boards import CATEGORIES
from ..services.provider_contract import ProviderStore, SecretStore, RunMode
from ..services.provider_runtime import ProviderRuntime
from ..services.topic_service import PENDING_STATES
from ..services.content_forms import CreativeBrief
from .batches import SessionFactory, _settings, _worker_running

router = APIRouter(tags=["creation"])


def runtime():
    rt = ProviderRuntime(ProviderStore(_settings.storage_root / "provider_configs.json"),
                         SecretStore(_settings.secret_store_path))
    rt.session_factory = SessionFactory
    return rt


class CreationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    creation_key: UUID
    topic: str = Field(min_length=3, max_length=300)
    audience: str = Field(default="", max_length=160)
    angle: str = Field(default="", max_length=400)
    outline: list[str] = Field(default_factory=list, max_length=6)
    requirements: str = Field(default="", max_length=4000)
    materials: str = Field(default="", max_length=16000)
    platforms: list[str] = Field(default_factory=lambda: ["douyin", "xiaohongshu"], min_length=1, max_length=2)
    run_mode: RunMode = RunMode.LOCAL_SEED
    plan_id: UUID | None = None
    media_ids: list[str] = Field(default_factory=list,max_length=6)
    image_policy: str = Field(default='auto',pattern=r'^(auto|diagram)$')
    creative_brief: CreativeBrief | None = None


class ContinueBlockedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    materials: str = Field(default="", max_length=16000)
    requirements: str = Field(default="", max_length=4000)
    run_mode: RunMode = RunMode.REAL


@router.get("/creation/options")
def options():
    rt = runtime()
    cfg = rt.text_provider()
    ready = bool(cfg and cfg.model_id and cfg.secret_ref and rt.secrets.get(cfg.secret_ref))
    return {"categories": CATEGORIES, "text_ready": ready, "model": cfg.model_id if ready else None,
            # 前端要在"确认生成"这步提醒：没配搜索时，榜单/排名这类需要真实数据的选题
            # 会在内容规划阶段被防编造闸门拦下，不如提前说清楚。
            "search_ready": bool(rt.search_provider()),"public_research_available":False,
            "default_mode": "real" if ready else "local_seed",
            "notice": "直接输入选题，或从HotPush聚合热点中选择。打开页面不会调用文字模型。"}


@router.get("/creation/trends")
def trends():
    from .studio import boards
    return boards.all()


@router.post("/creation/recommendations")
def recommendations():
    raise HTTPException(410,'旧选题推荐已移除。请从分类热榜选题或直接输入主题，不再自动改换角度。')


@router.post("/creation/produce", status_code=202)
def produce(payload: CreationInput):
    return enqueue(payload)


@router.post("/creation/contents/{content_id}/continue", status_code=202)
def continue_blocked(content_id: str, payload: ContinueBlockedInput):
    """Resume a blocked, versionless topic in place after the user adds material."""
    from ..core.errors import NotFound, StateConflict

    if payload.run_mode == RunMode.REAL and not options()["text_ready"]:
        raise ValidationFailed("请先配置可用的文字模型")
    digest = hashlib.sha256(json.dumps({"content_id":content_id, **payload.model_dump(mode="json")},
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    with SessionFactory() as s:
        s.connection().exec_driver_sql("BEGIN IMMEDIATE")
        event=s.query(Event).filter_by(entity_type="production_recovery",
            entity_id=str(payload.request_id),type="production_recovery_enqueued").first()
        if event:
            if event.payload.get("input_hash") != digest:
                raise IdempotencyConflict("这次补资料已提交，内容已变化；请开始新的修复任务")
            result=dict(event.payload["result"])
            run=s.get(Run,result["run_id"])
            if run:
                result.update(reused=True,state=run.state,worker_running=_worker_running())
            return result
        item=s.get(ContentItem,content_id)
        if not item:
            raise NotFound("内容不存在")
        if item.active_revision_id or item.state not in {"blocked","drafting","failed"}:
            raise StateConflict("只有尚未生成版本的阻塞内容可以补资料续跑；已有稿件请用内容再调整")
        if s.query(Run).filter(Run.content_id==content_id,Run.state.in_(["queued","running"])).first():
            raise StateConflict("这条内容已有任务执行中，请等待完成")
        if s.query(ProviderExchange).filter(ProviderExchange.content_id==content_id,
                ProviderExchange.state.in_(["unknown","in_flight"])).first():
            raise StateConflict("该内容存在结果未确认的模型调用，请先核查服务商记录")
        run=Run(content_id=content_id,stage="produce",state="queued",mode=payload.run_mode.value,attempt=0)
        s.add(run);s.flush()
        materials=[{"text":payload.materials.strip(),"kind":"user_provided"}] if payload.materials.strip() else []
        from ..services.content_forms import build_brief
        brief=build_brief(topic=item.topic,requirements=payload.requirements)
        request={"content_id":content_id,"topic":item.topic,"topic_locked":True,
            "run_mode":payload.run_mode.value,"platforms":["douyin","xiaohongshu"],"render":True,
            "user_materials":materials,"user_requirements":payload.requirements,
            "creative_brief":brief.model_dump(mode="json")}
        job=Job(run_id=run.id,stage="batch_dispatch",state="queued",input_hash=digest,
            output_refs={"request":request},attempt=0)
        s.add(job);s.flush()
        result={"run_id":run.id,"content_id":content_id,"display_id":item.display_id,
            "queued_job_id":job.id,"state":"queued","run_mode":payload.run_mode.value,
            "worker_running":_worker_running(),"reused":False,
            "creative_brief":brief.model_dump(mode="json")}
        s.add(Event(entity_type="production_recovery",entity_id=str(payload.request_id),
            type="production_recovery_enqueued",actor="coisini",run_mode=payload.run_mode.value,
            payload={"input_hash":digest,"result":result,"topic":item.topic}))
        s.commit()
        return result


def enqueue(payload: CreationInput, *, studio_input: dict | None = None):
    topic = payload.topic.strip()
    if len(topic) < 3 or any(len(s) > 260 for s in payload.outline):
        raise ValidationFailed("请填写清楚的选题，并缩短过长的提纲")
    if len(set(payload.platforms)) != len(payload.platforms) or any(p not in {"douyin", "xiaohongshu"} for p in payload.platforms):
        raise ValidationFailed("请选择有效且不重复的平台")
    canonical = payload.model_dump(mode="json", exclude={"creation_key"})
    digest = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    with SessionFactory() as s:
        s.connection().exec_driver_sql("BEGIN IMMEDIATE")
        event = s.query(Event).filter_by(entity_type="creation", entity_id=str(payload.creation_key), type="creation_enqueued").first()
        if event:
            same_input = (event.payload.get("studio_input") == studio_input
                          if studio_input is not None else event.payload["input_hash"] == digest)
            if not same_input:
                raise IdempotencyConflict("这次创作已提交，不能覆盖；请开始新的创作")
            result = dict(event.payload["result"])
            run = s.get(Run, result["run_id"])
            result.update(reused=True, state=run.state, worker_running=_worker_running())
            return result
        if payload.run_mode == RunMode.REAL and not options()["text_ready"]:
            raise ValidationFailed("请先配置文字模型，再生成真实图文")
        if payload.plan_id:
            plan=s.query(Event).filter_by(entity_type='content_plan',entity_id=str(payload.plan_id),type='plan_ready').first()
            if not plan or any(plan.payload.get(k)!=canonical.get(k) for k in ('topic','requirements','materials','run_mode')):
                raise ValidationFailed('选题、要求或资料已改变，请先重新查看内容规划')
        # 待预览库存只作提示，不阻塞「人就在现场」的引导式创作。
        # 真正需要限流的是无人值守的自动选题（topic_service.select），
        # 那里才会因为库存满而暂停；人主动写一篇不该被上一批的积压拦住。
        stock = s.query(ContentItem).filter(ContentItem.state.in_(PENDING_STATES), ~ContentItem.display_id.like("DEMO-%")).count()
        stock_full = stock >= _settings.pending_review_stock_limit
        b = Batch(item_limit=1, cost_mode=_settings.cost_mode_default,
                  budget_limit_micro=_settings.batch_money_limit_default, currency="CNY", state="open")
        s.add(b); s.flush()
        item = ContentItem(batch_id=b.id, display_id=f"C{s.query(ContentItem).count()+1:03d}", topic=topic,
                           selected_by="user", selection_reason="用户直接选择或输入选题", state="queued", run_mode=payload.run_mode.value)
        s.add(item); s.flush()
        run = Run(content_id=item.id, stage="produce", state="queued", mode=payload.run_mode.value, attempt=0)
        s.add(run); s.flush()
        outline = payload.outline or ["读者面临的问题", "具体对象与选择理由", "实际做法或搭配", "资料对照与适用边界"]
        instructions = json.dumps({"audience": payload.audience, "angle": payload.angle,
                                   "outline": outline, "requirements": payload.requirements}, ensure_ascii=False)
        # 把「排行榜 / TOP10 / 1~2 页」这类自由文本要求解析成结构化创作简报，
        # 让页数、题型、条目数成为可校验的约束，而不是一句拼进提示词的祈愿。
        from ..services.content_forms import build_brief
        brief = payload.creative_brief or build_brief(topic=topic, requirements=payload.requirements,
                            outline=outline, audience=payload.audience)
        materials = []
        if payload.materials.strip():
            materials.append({"text": payload.materials.strip(), "kind": "user_provided"})
        # An editorial plan supports a method/opinion draft, not empirical claims.
        materials.append({"text": f"编辑提纲（非事实依据）：{topic}。拟写内容：" + "；".join(outline), "kind": "editorial_plan"})
        request = {"topic": topic, "content_id": item.id, "run_mode": payload.run_mode.value,
                   "seed_path": None, "platforms": payload.platforms, "render": True,
                   "user_materials": materials, "topic_locked": True, "user_requirements": instructions}
        request.update(plan_id=str(payload.plan_id) if payload.plan_id else None,
                       media_ids=payload.media_ids,image_policy=payload.image_policy)
        request['creative_brief'] = brief.model_dump(mode='json')
        job = Job(run_id=run.id, stage="batch_dispatch", state="queued", input_hash=digest,
                  output_refs={"request": request}, attempt=0)
        s.add(job); s.flush()
        result = {"run_id": run.id, "content_id": item.id, "display_id": item.display_id,
                  "queued_job_id": job.id, "state": "queued", "run_mode": payload.run_mode.value,
                  "worker_running": _worker_running(), "reused": False,
                  "pending_review_stock": stock,
                  "pending_review_stock_limit": _settings.pending_review_stock_limit,
                  "stock_warning": (f"当前已有 {stock} 条待预览（参考上限 "
                                    f"{_settings.pending_review_stock_limit}）。不影响继续创作，"
                                    f"你可以在内容列表里处理或丢弃它们。"
                                    if stock_full else None),
                  "creative_brief": brief.model_dump(mode='json')}
        s.add(Event(entity_type="creation", entity_id=str(payload.creation_key), type="creation_enqueued",
                    actor="coisini", run_mode=payload.run_mode.value,
                    payload={"input_hash": digest, "result": result, "brief": canonical,
                             **({"studio_input": studio_input} if studio_input is not None else {})}))
        s.commit()
        return result
