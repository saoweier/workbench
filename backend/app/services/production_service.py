"""自动生产编排（P2/T08–T11 串线）。

一条内容从「主题」到「可预览成品」的全部阶段，按同一套规则串起来：

    研究(research) → 选题(topic) → 母稿与双平台改写(compose) → 渲染(render)

设计边界（对照 docs/02-modules.md §M07）：

- **每个阶段都落 Run/Job 记录**：状态、尝试次数、输入哈希、输出引用。
  不靠内存里的一次调用栈，进程死了也能从表里看出卡在哪一步。
- **重试不重复追加产物**（不变量第 3 条）：`Job` 有
  `unique(run_id, stage, input_hash)`，同一输入重跑直接复用旧 job。
- **Provider 结果未知 ≠ 免费失败**（不变量第 4 条）：调用失败但拿不到
  远端结果时，`ProviderCall.state` 记 `unknown`，费用记 NULL，不写 0。
- **任一阶段失败不抹掉前面成功的阶段**：Run 停在 blocked 状态，
  已产出的 revision / 平台稿仍在，人工可以只补失败的那一段。
- **fixture / real / local_seed 全程显式**：由 Run.mode 与每条
  ProviderCall.run_mode 承载，绝不把 fixture 结果说成真实调用。

本模块**不做**的事：
- 不自动发布（不存在 auto-publish）
- 不产出发布包（那是 P1 的 build_package，必须有人工批准在前）
- 不因"没配置 Provider"报错中断：研究阶段会降级为 local_seed 并记录说明
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from ..core.config import get_settings
from ..core.errors import NotFound, StateConflict, ValidationFailed
from ..models.entities import (
    Batch,
    ContentItem,
    ContentRevision,
    Event,
    Job,
    PlatformRevision,
    ProviderCallRow,
    Run,
    SourceRow,
)
from .compose_service import ComposeService, ComposeOutcome, MasterDraft, MasterPage
from .pipeline import PipelineService
from .content_lifecycle import update_content_state
from .profile_store import ProfileStore
from .provider_contract import ProviderConfig, ProviderStore, RunMode, SecretStore
from .provider_runtime import ProviderRuntime
from .research_service import ResearchResult, ResearchService
from .topic_service import TopicCandidate, TopicService

STAGES = ("research", "topic", "compose", "render")


class ProductionService:
    """把 P2 各服务编排成一条可恢复的生产线。"""

    def __init__(self, session_factory, *, actor: str = "coisini",
                 runtime: ProviderRuntime | None = None,
                 profiles: ProfileStore | None = None) -> None:
        self.sf = session_factory
        self.actor = actor
        self.settings = get_settings()
        self.profiles = profiles or ProfileStore()
        if runtime is None:
            runtime = ProviderRuntime(ProviderStore(), SecretStore())
        self.runtime = runtime
        self.check_control = None
        self.runtime.session_factory = session_factory
        self.research = ResearchService(runtime, actor=actor)
        self.topics = TopicService(session_factory, runtime, actor=actor)
        self.compose = ComposeService(session_factory, runtime, actor=actor,
                                      profiles=self.profiles)
        self.pipeline = PipelineService(session_factory, actor=actor)
        from .content_skills import ContentSkills
        self.skills=ContentSkills(session_factory,runtime)

    # ============================================================ 主流程

    def illustrate(self, *, content_id: str, base_revision_id: str, run_mode: RunMode,
                   platforms: tuple[str, ...], run_id: str) -> dict:
        """Recompose a frozen draft into an illustrated product; preserve its evidence.

        The queue freezes the base revision. Paid responses use the same durable
        request journal as ordinary production; completed composition checkpoints
        are reused if rendering needs to resume.
        """
        self.runtime.context_content_id = content_id
        self.compose.skill_snapshot=self.skills.snapshot(run_id)
        self.compose.content_plan=None
        self.compose.media_assets=[]
        self.compose.user_requirements=''
        self.compose.creative_brief=None
        self.compose.expected_baseline=base_revision_id
        with self.sf() as s:
            run = s.get(Run, run_id)
            if not run or run.state != 'queued' or run.content_id != content_id:
                raise StateConflict('图解任务状态或内容引用无效')
            base = s.get(ContentRevision, base_revision_id)
            if not base or base.content_id != content_id:
                raise NotFound('图解基线版本不存在')
            originals = s.query(PlatformRevision).filter_by(content_revision_id=base.id).all()
            if not originals:
                raise ValidationFailed('基线没有可改写的稿件')
            brief = base.brief_json or {}
            claims = (base.claims_json or {}).get('claims', [])
            sources = (base.claims_json or {}).get('sources', [])
            known = [c['id'] for c in claims if c.get('id')]
            original_pages = (originals[0].pages_json or {}).get('pages', [])
            known = list(dict.fromkeys(known + [c for p in original_pages for c in p.get('claim_ids', [])]))
            pages = [MasterPage(index=n+1, heading=p.get('heading') or '内容要点',
                         points=(p.get('body') or []) + [i.get('detail', '') for i in (p.get('visual') or {}).get('items', [])],
                         claim_ids=p.get('claim_ids') or []) for n, p in enumerate(original_pages)]
            if claims:
                pages.append(MasterPage(index=len(pages)+1, heading='已有资料依据（摘要，不是原文）',
                    points=[f"{c.get('id')}: {c.get('statement', '')}" for c in claims], claim_ids=known))
            master = MasterDraft(audience_problem=brief.get('audience_problem') or '如何理解并应用这条内容',
                core_viewpoint=brief.get('core_viewpoint') or originals[0].title,
                claim_ids=known, actions=brief.get('actions') or [], pages=pages,
                limitations=(base.limitations_json or {}).get('limitations', []), run_mode=run_mode)
            run.state = 'running'; run.attempt += 1
            s.commit(); s.refresh(run); s.expunge(run)
        out = {'run_id':run_id, 'content_id':content_id, 'run_mode':run_mode.value,
               'partial':False, 'stages':{}, 'base_revision_id':base_revision_id}
        job = self._job_for(run, 'compose', {'base_revision_id':base_revision_id, 'design':'illustrated@1'})
        checkpoint = (job.output_refs or {}).get('revision_id')
        if checkpoint:
            revision_id = checkpoint
            self._finish_job(job, 'succeeded', {'revision_id':revision_id,'design':'illustrated@1'})
        else:
            try:
                self.compose._active_claim_kinds = {c['id']:c.get('kind', '') for c in claims if c.get('id')}
                profiles = {p:self.profiles.latest(p) for p in platforms}
                variants = {}
                repairs = 0
                for platform in platforms:
                    variants[platform], count = self.compose.compose_platform(master, platform, profiles[platform],
                        content_id=content_id, run_mode=run_mode, max_repair_rounds=self.settings.max_repair_rounds)
                    repairs = max(repairs, count)
                if len(variants) >= 2:
                    self.compose._assert_variants_distinct(variants)
                outcome = ComposeOutcome(master=master, variants=variants, claims=claims, sources=sources,
                    repair_round=repairs, model_used=run_mode != RunMode.LOCAL_SEED, run_mode=run_mode)
                self.compose._persist(content_id, outcome, profiles=profiles, run_mode=run_mode)
                revision_id = self._active_revision_id(content_id)
                self._finish_job(job, 'succeeded', {'revision_id':revision_id,'design':'illustrated@1'})
            except Exception as exc:
                self._finish_job(job, 'failed', {}, error=str(exc))
                return self._block(out, run, content_id, 'compose', exc)
        out['stages']['compose'] = {'ok':True, 'revision_id':revision_id,'design':'illustrated@1'}
        job = self._job_for(run, 'render', {'revision_id':revision_id})
        rendered = {}; failed = []
        for pr_id, platform in self._platform_revisions(revision_id):
            result = self.pipeline.render(pr_id)
            if result.get('ok'):
                rendered[platform] = {'platform_revision_id':pr_id,'pages':len(result['images']),
                                      'manifest_hash':result['manifest_hash']}
            else:
                failed.append({'platform':platform,'issues':result.get('issues', [])})
        out['stages']['render'] = {'ok':not failed,'rendered':rendered,'failed':failed}
        self._finish_job(job, 'failed' if failed else 'succeeded', out['stages']['render'],
                         error='图解排版存在阻断项' if failed else None)
        if failed:
            out['partial'] = True; out['blocked_stage'] = 'render'
            self._close_run(run, 'failed', blocked_stage='render',
                            error='图解排版存在阻断项，渲染未通过校验')
        else:
            self._close_run(run, 'succeeded')
        return out

    def produce(
        self,
        *,
        topic: str,
        content_id: str | None = None,
        seed_path: str | None = None,
        run_mode: RunMode = RunMode.LOCAL_SEED,
        platforms: tuple[str, ...] = ("douyin", "xiaohongshu"),
        render: bool = True,
        profile_version_ids: dict[str, str] | None = None,
        user_materials: list[dict] | None = None,
        max_repair_rounds: int | None = None,
        run_id: str | None = None,
        topic_locked: bool = False,
        user_requirements: str = "",
        plan_id: str | None = None,
        media_ids: list[str] | None = None,
        image_policy: str = 'auto',
        base_revision_id: str | None = None,
        creative_brief: dict | None = None,
    ) -> dict:
        """跑完整条链路。返回分阶段结果，**不抛异常掩盖中间状态**。

        失败时返回 `partial=True` + `blocked_stage`，并保留已完成阶段。
        调用方（API）据此返回 202/207 与异常项，而不是把失败伪装成空结果。
        """
        if not str(topic or "").strip():
            raise ValidationFailed("主题不能为空")
        max_repair_rounds = (max_repair_rounds
                             if max_repair_rounds is not None
                             else self.settings.max_repair_rounds)

        content_id = content_id or self._ensure_content(topic, run_mode)
        if base_revision_id:
            with self.sf() as s:
                item=s.get(ContentItem,content_id)
                if not item or item.active_revision_id!=base_revision_id:
                    # Audit/render can fail after this run commits its own new
                    # revision. Resume that checkpoint, never another run's work.
                    checkpoint=s.query(Job).filter_by(run_id=run_id,stage='compose').first() if run_id else None
                    own_id=((checkpoint.output_refs or {}).get('public') or {}).get('revision_id') if checkpoint else None
                    own=s.get(ContentRevision,own_id) if own_id else None
                    if not (item and own and item.active_revision_id==own.id and own.content_id==content_id
                            and own.parent_id==base_revision_id and checkpoint.state=='succeeded'
                            and item.state not in {'approved','published','partially_approved'}):
                        raise StateConflict('基线版本已变化，本次重做已停止')
        self.runtime.context_content_id = content_id
        if run_id:
            with self.sf() as s:
                run = s.get(Run, run_id)
                if run is None or run.content_id != content_id or run.state != "queued":
                    raise StateConflict("任务已执行或内容引用无效，禁止重复生产")
                run.state = "running"
                run.attempt += 1
                s.commit()
                s.refresh(run)
                s.expunge(run)
        else:
            run = self._open_run(content_id, run_mode)
        snapshot=self.skills.snapshot(run.id,direction=(creative_brief or {}).get('direction','general'),template_package=(creative_brief or {}).get('template_package'))
        self.compose.skill_snapshot=snapshot
        self.compose.content_plan=None
        self.compose.media_assets=[]
        self.compose.user_requirements=user_requirements
        self.compose.creative_brief=creative_brief
        self.compose.expected_baseline=base_revision_id
        out: dict = {
            "run_id": run.id,
            "content_id": content_id,
            "run_mode": run_mode.value,
            "stages": {},
            "partial": False,
            "blocked_stage": None,
            "real_calls_recorded": 0,
        }

        # ---------------- 1. 研究 ----------------
        job = self._job_for(run, "research", {"topic": topic, "seed": seed_path or ""})
        if (job.output_refs or {}).get("checkpoint"):
            research = ResearchResult.model_validate(job.output_refs["checkpoint"])
            from .evidence_gate import factual_sources
            if run_mode==RunMode.REAL and not factual_sources(research.sources_as_dicts()):
                self.research.refresh_unread_sources(research)
        else:
            existing_research=ResearchResult(content_id=content_id,topic=topic,run_mode=run_mode)
            if base_revision_id:self._reuse_revision_evidence(content_id,existing_research,run_mode,base_revision_id=base_revision_id)
            research = self.research.research(
                topic=topic, requirements=user_requirements, content_id=content_id, seed_path=seed_path,
                user_materials=user_materials, run_mode=run_mode,
                existing_result=existing_research,
                search_planner=lambda gaps,sources:self.skills.research_search_plan(topic=topic,requirements=user_requirements,
                    context_id=run.id,content_id=content_id,snapshot=snapshot,gaps=gaps,sources=sources,search_trace=existing_research.search_trace),
            )
        if base_revision_id and self._reuse_revision_evidence(content_id,research,run_mode,base_revision_id=base_revision_id):
            note='沿用旧版本冻结的证据；本次未重新核验这些来源'
            if note not in research.limitations:research.limitations.append(note)
        from .evidence_gate import require_evidence,needs_grounding
        try:
            if run_mode==RunMode.REAL:
                from .token_cost import is_token_cost,attach_ledger,ledger_from
                if is_token_cost(topic) and not ledger_from(research.sources_as_dicts()):
                    attach_ledger(research)
                if needs_grounding(topic,user_requirements) and not is_token_cost(topic) and not research.assessment:
                    from .content_skills import research_tool_limits
                    policy=research_tool_limits(snapshot['research']['instructions'])
                    for search_round in range(policy['followup_rounds']+1):
                        try:
                            require_evidence(topic,research.sources_as_dicts(),user_requirements)
                            report=self.skills.research_review(topic=topic,requirements=user_requirements,sources=research.sources_as_dicts(),
                                context_id=run.id,content_id=content_id,snapshot=snapshot,search_trace=research.search_trace)
                            break
                        except ValidationFailed as gap_error:
                            gaps=gap_error.details.get('research_gaps')
                            if not gaps or search_round>=policy['followup_rounds']:raise
                            self.research.discover(research,requirements=user_requirements,gaps=gaps,
                                planner=lambda gaps,sources:self.skills.research_search_plan(topic=topic,requirements=user_requirements,
                                    context_id=run.id,content_id=content_id,snapshot=snapshot,gaps=gaps,sources=sources,search_trace=research.search_trace))
                    research.assessment=report.model_dump(mode='json')
                    research.limitations.extend(v for v in report.limitations if v not in research.limitations)
                    from .research_service import ClaimModel
                    for fact in report.facts:
                        research.claims.append(ClaimModel(id=f'C{len(research.claims)+1:02d}',kind='document_observation',
                            statement=f'{fact.role}：{fact.statement}'+(f'；原文引用：{fact.quote}' if fact.quote else '；调研概述，非逐字引文'),source_ids=[fact.source_id]))
                require_evidence(topic,research.sources_as_dicts(),user_requirements)
        except (ValidationFailed,StateConflict,ValueError) as exc:
            if isinstance(exc,ValidationFailed) and exc.details.get('research_gaps'):
                from .content_skills import selected_topic_references
                refs=selected_topic_references(research.sources_as_dicts(),research.search_trace)
                prefix='热榜原链接已携带，但原站未返回可用正文。' if any(r.get('provided') and r.get('read_state')=='snippet_only' for r in refs) else ''
                search_note=self._search_failure_note(research)
                exc=ValidationFailed(prefix+search_note+'详细原因和来源记录见技能诊断。',details={**exc.details,'assessment_message':str(exc),'search_failure':search_note})
            self._save_sources(content_id,research)
            self.skills.record(run.id,'research',state='failed',content_id=content_id,snapshot=snapshot,
                inputs={'topic':topic,'run_mode':run_mode.value},output={'sources':research.sources_as_dicts(),'claims':research.claims_as_dicts(),
                'search_executed':research.search_executed,'search_provider':research.search_provider,'search_trace':research.search_trace,'access_failures':research.access_failures,'message':str(exc)})
            self._finish_job(job,'failed',{'checkpoint':research.model_dump(mode='json')},error=str(exc))
            return self._block(out,run,content_id,'research',ValidationFailed(str(exc)))
        self.skills.record(run.id,'research',state='succeeded',content_id=content_id,snapshot=snapshot,
            inputs={'topic':topic,'requirements':user_requirements,'creative_brief':creative_brief,'run_mode':run_mode.value},
            output={'sources':research.sources_as_dicts(),'claims':research.claims_as_dicts(),'search_executed':research.search_executed,'search_provider':research.search_provider,'search_trace':research.search_trace,'access_failures':research.access_failures,'assessment':research.assessment,'limitations':research.limitations,'findings':research.findings})
        self._save_sources(content_id, research)
        providers_used = self._persist_calls(research.calls, content_id=content_id, run=run)
        self._finish_job(job, "succeeded", {
            "sources": len(research.sources), "claims": len(research.claims),
            "search_executed": research.search_executed,
            "checkpoint": research.model_dump(mode="json"),
        })
        out["stages"]["research"] = {
            "ok": True,
            "topic": research.topic,
            "sources": len(research.sources),
            "claims": len(research.claims),
            "search_executed": research.search_executed,
            "limitations": research.limitations,
            "degrade_notes": self.research.degrade_notes(research),
            "findings": research.findings,
            "provider_calls": providers_used,
        }
        out["real_calls_recorded"] += providers_used
        self.skills.record(run.id,'discovery',state='succeeded',content_id=content_id,snapshot=snapshot,
            inputs={'topic':topic,'run_mode':run_mode.value},output={'sources':research.sources_as_dicts(),
                'search_executed':research.search_executed,'limitations':research.limitations,'notice':'资料发现不等于平台实时热榜'})

        # ---------------- 2. 选题 ----------------
        job = self._job_for(run, "topic", {"topic": topic,
                                           "claims": len(research.claims)})
        try:
            if topic_locked:
                cands = [self.topics._to_candidate({"topic": topic, "audience_problem": "用户已确认的创作目标",
                    "supporting_claim_ids": [c.id for c in research.claims],
                    "selection_reason": "用户在引导式创作中确认，保留此选题"}, 1, research, [])]
            else:
                cands = self.topics.propose(research=research, count=3, run_mode=run_mode)
            with self.sf() as s:
                existing = s.get(ContentItem, content_id)
                rebuilding = bool(topic_locked and existing and existing.active_revision_id)
            sel = self.topics.select(cands, **({'existing_content_id': content_id, 'user_direct':True} if topic_locked else {}))
        except ValidationFailed as exc:
            self._finish_job(job, "failed", {}, error=exc.message)
            return self._block(out, run, content_id, "topic", exc)

        out["stages"]["topic"] = {
            "ok": not sel["blocked"],
            "candidate_count": len(cands),
            "candidates": [self._cand_public(c) for c in cands],
            "blocked": sel["blocked"],
            "reason": sel.get("reason"),
            "pending_stock": sel.get("pending_stock"),
            "selected": [self._cand_public(c) for c in sel.get("selected", [])],
        }
        if sel["blocked"]:
            # 库存满不是失败，是明确的暂停信号：不算 blocked_stage 的硬错误
            self._finish_job(job, "succeeded", {"blocked": True,
                                                "reason": sel.get("reason")})
            out["stages"]["topic"]["note"] = "待预览库存已达上限，暂停新增制作（不是失败）"
            out["paused"] = True
            out["paused_reason"] = sel.get("reason")
            self._close_run(run, "paused", blocked_stage=None)
            return out
        chosen = sel["selected"][0]
        with self.sf() as s:
            item = s.get(ContentItem, content_id)
            item.topic = chosen.topic
            item.selected_by = "user" if topic_locked else "ai"
            item.selection_reason = chosen.selection_reason
            existing = s.query(Event).filter_by(
                entity_id=content_id, type="topic_confirmed_user" if topic_locked else "topic_selected_ai").all()
            if not any((e.payload or {}).get("run_id") == run.id for e in existing):
                s.add(Event(entity_type="content_item", entity_id=content_id,
                    type="topic_confirmed_user" if topic_locked else "topic_selected_ai", actor=self.actor, run_mode=run_mode.value,
                    payload={"run_id": run.id, "topic": chosen.topic,
                        "reason": chosen.selection_reason, "scores": chosen.rule_scores,
                        "heat_verified": chosen.heat_verified,
                        "candidates": out["stages"]["topic"]["candidates"]}))
            s.commit()
        research.topic = chosen.topic
        self._finish_job(job, "succeeded", {"selected": len(sel["selected"]),
                                           "topic": chosen.topic})
        self.skills.record(run.id,'selection',state='succeeded',content_id=content_id,snapshot=snapshot,
            inputs={'topic':chosen.topic,'requirements':user_requirements,'run_mode':run_mode.value},
            output={'confirmed_by_user':topic_locked,'reason':chosen.selection_reason})

        # Independent content planning and image acquisition with durable checkpoints.
        job=self._job_for(run,'planning',{'topic':research.topic,'requirements':user_requirements,'plan_id':plan_id})
        try:
            from .content_skills import ContentPlan
            checkpoint=(job.output_refs or {}).get('plan')
            if checkpoint:
                plan=ContentPlan.model_validate(checkpoint)
            elif plan_id:
                with self.sf() as s:
                    saved=s.query(Event).filter_by(entity_type='content_plan',entity_id=plan_id,type='plan_ready').first()
                    if not saved or saved.payload['topic']!=research.topic or saved.payload['run_mode']!=run_mode.value:
                        raise ValidationFailed('内容规划与本次选题/运行模式不匹配')
                    plan=ContentPlan.model_validate(saved.payload['plan'])
                    # The confirmed blueprint carries the same frozen evidence used to plan it.
                    from .research_service import ClaimModel,SourceModel
                    research.claims=[ClaimModel.model_validate(c) for c in saved.payload['claims']]
                    research.sources=[SourceModel.model_validate(c) for c in saved.payload['sources']]
                self.skills.record(run.id,'planning',state='succeeded',content_id=content_id,snapshot=snapshot,
                    inputs={'topic':research.topic,'run_mode':run_mode.value,'confirmed_plan_id':plan_id},output=plan.model_dump(mode='json'))
            else:
                plan=self.skills.plan(topic=research.topic,requirements=user_requirements,
                    claims=research.claims_as_dicts(),sources=research.sources_as_dicts(),run_mode=run_mode,
                    context_id=run.id,content_id=content_id,snapshot=snapshot,brief=creative_brief)
            self._finish_job(job,'succeeded',{'plan':plan.model_dump(mode='json')})
            self.compose.content_plan=plan.model_dump(mode='json')
            if any(set(p.claim_ids)-{c.id for c in research.claims} for p in plan.pages):
                raise ValidationFailed('规划的资料引用与当前证据不匹配，请重新规划')
            out['stages']['planning']={'ok':True,'plan':plan.model_dump(mode='json')}
            if plan.blocking_gaps:
                raise ValidationFailed('内容规划等待核心资料：'+'；'.join(plan.blocking_gaps))
        except (ValidationFailed,StateConflict,ValueError) as exc:
            self._finish_job(job,'failed',{},error=str(exc))
            return self._block(out,run,content_id,'planning',ValidationFailed(str(exc)))

        job=self._job_for(run,'media',{'plan':plan.model_dump(mode='json'),'media_ids':media_ids or [],'image_policy':image_policy})
        try:
            from .content_media import ContentMedia
            media=(job.output_refs or {}).get('assets')
            if media is None:
                media=ContentMedia(self.runtime).create_for_plan(plan,content_id=content_id,context_id=run.id,
                    provided_ids=media_ids or [],image_policy=image_policy) if run_mode==RunMode.REAL else []
            self.compose.media_assets=media
            self._finish_job(job,'succeeded',{'assets':media})
            out['stages']['media']={'ok':True,'assets':media,'notice':'模型图片为AI示意图；本地图解不冒充实拍'}
        except (ValidationFailed,StateConflict,NotFound) as exc:
            self._finish_job(job,'failed',{},error=str(exc))
            return self._block(out,run,content_id,'media',ValidationFailed(str(exc)))

        # ---------------- 3. 母稿 + 双平台改写 ----------------
        job = self._job_for(run, "compose", {"topic": research.topic,
                                             "claims": len(research.claims)})
        if (job.output_refs or {}).get("public"):
            out["stages"]["compose"] = job.output_refs["public"]
            revision_id = out["stages"]["compose"]["revision_id"]
            self._finish_job(job, "succeeded", {"public": out["stages"]["compose"]})
        else:
            try:
                outcome = self.compose.compose(
                    content_id=content_id, topic=research.topic,
                    platforms=platforms,
                    claims=research.claims_as_dicts(),
                    sources=research.sources_as_dicts(),
                    limitations=research.limitations,
                    audience=self._audience_of(content_id),
                    seed_path=seed_path,
                    profile_version_ids=profile_version_ids,
                    run_mode=run_mode, max_repair_rounds=max_repair_rounds,
                    persist=True, creative_brief=creative_brief,
                    **({"user_requirements": user_requirements} if user_requirements else {}),
                )
            except ValidationFailed as exc:
                self._finish_job(job, "failed", {}, error=exc.message)
                self._event(content_id, "compose_failed", {"message": exc.message,
                                                           "details": exc.details}, run_mode)
                if getattr(exc, "code", "") == "MODEL_PRIVILEGE_VIOLATION":
                    # 权限越界单独记一条事件，便于集中异常里一眼看到
                    self._event(content_id, "model_privilege_violation",
                                {"message": exc.message, "details": exc.details}, run_mode)
                return self._block(out, run, content_id, "compose", exc)

            revision_id = self._active_revision_id(content_id)
            self._finish_job(job, "succeeded", {
                "revision_id": revision_id, "platforms": list(outcome.variants),
                "repair_round": outcome.repair_round,
            })
            out["stages"]["compose"] = {
                "ok": True,
                "revision_id": revision_id,
                "repair_round": outcome.repair_round,
                "model_used": outcome.model_used,
                "platforms": {
                    p: {"title": d.title, "pages": len(d.pages)}
                    for p, d in outcome.variants.items()
                },
                "notes": outcome.notes,
            }

            self._finish_job(job, "succeeded", {"public": out["stages"]["compose"]})
        self.skills.record(run.id,'generation',state='succeeded',content_id=content_id,snapshot=snapshot,
            inputs={'topic':research.topic,'requirements':user_requirements,'run_mode':run_mode.value},
            output={**out['stages']['compose'],'assets':self.compose.media_assets})
        if base_revision_id:
            self.skills.record(run.id,'revision',state='succeeded',content_id=content_id,snapshot=snapshot,
                inputs={'base_revision_id':base_revision_id,'requirements':user_requirements,'run_mode':run_mode.value},
                output={'new_revision_id':revision_id,'changed':revision_id!=base_revision_id})

        # ---------------- 4. 渲染 ----------------
        if not render:
            self._close_run(run, "succeeded")
            return out

        job = self._job_for(run, "render", {"revision_id": revision_id})
        rendered: dict = {}
        failed: list[dict] = []
        for pr_id, platform in self._platform_revisions(revision_id):
            res = self.pipeline.render(pr_id)
            if res.get("ok"):
                rendered[platform] = {
                    "platform_revision_id": pr_id,
                    "manifest_hash": res["manifest_hash"],
                    "state": res["state"],
                    "pages": len(res["images"]),
                    "warnings": res.get("warnings", []),
                }
            else:
                failed.append({
                    "platform": platform,
                    "platform_revision_id": pr_id,
                    "issues": res.get("issues", []),
                    "note": res.get("note"),
                })
        out["stages"]["render"] = {"ok": not failed, "rendered": rendered,
                                   "failed": failed}
        if failed:
            # 一个平台失败不抹掉另一个平台的成功（不变量第 3 条的镜像要求）
            self._finish_job(job, "failed", {"rendered": list(rendered)},
                             error=f"{len(failed)} 个平台渲染被阻断")
            out["partial"] = True
            out["blocked_stage"] = "render"
            self._close_run(run, "failed", blocked_stage="render",
                            error=f"{len(failed)} 个平台渲染被阻断")
            return out

        self._finish_job(job, "succeeded", {"rendered": list(rendered)})
        job=self._job_for(run,'audit',{'revision_id':revision_id,'plan':plan.model_dump(mode='json')})
        try:
            from .content_skills import ContentAudit
            saved=(job.output_refs or {}).get('report')
            if saved:report=ContentAudit.model_validate(saved)
            else:
                with self.sf() as s:
                    drafts=[{'platform':p.platform,'title':p.title,'caption':p.caption,'pages':p.pages_json.get('pages',[])}
                        for p in s.query(PlatformRevision).filter_by(content_revision_id=revision_id)]
                report=self.skills.audit(topic=research.topic,requirements=user_requirements,plan=plan.model_dump(mode='json'),
                    variants=drafts,claims=research.claims_as_dicts(),run_mode=run_mode,context_id=run.id,
                    content_id=content_id,snapshot=snapshot,sources=research.sources_as_dicts())
            self._finish_job(job,'succeeded',{'report':report.model_dump(mode='json')})
            out['stages']['audit']={'ok':report.passed,'report':report.model_dump(mode='json')}
            if not report.passed:
                with self.sf() as s:
                    update_content_state(s,s.get(ContentItem,content_id),'changes_requested')
                    for p in s.query(PlatformRevision).filter_by(content_revision_id=revision_id):p.state='changes_requested'
                    s.commit()
                out.update(needs_revision=True,ready_for_review=True)
                self._close_run(run,'succeeded')
                return out
        except (ValidationFailed,StateConflict,ValueError) as exc:
            self._finish_job(job,'failed',{},error=str(exc))
            return self._block(out,run,content_id,'audit',ValidationFailed(str(exc)))
        self._close_run(run, "succeeded")
        with self.sf() as s:
            out["real_calls_recorded"] = s.query(ProviderCallRow).filter_by(content_id=content_id, run_mode="real").count()
        out["ready_for_review"] = True
        self._event(content_id, "production_ready_for_review",
                    {"revision_id": revision_id, "platforms": list(rendered)}, run_mode)
        return out

    def produce_from_seed(self, *, seed_path: str = "", content_id: str | None = None,
                          run_mode: RunMode = RunMode.LOCAL_SEED,
                          render: bool = True) -> dict:
        """从已有 seed 走"研究 → 校验 → 改写复用 → 渲染"，零 provider 调用。

        这是 P2 的**离线基线**：证明整条链路在没有任何外部依赖时也能产出成品。
        """
        import json as _json

        seed_path = seed_path or str(
            self.settings.examples_dir / "C001" / "seeds" / "C001" / "seed.json"
        )
        data = _json.loads(open(seed_path, encoding="utf-8").read())
        return self.produce(
            topic=data["topic"], content_id=content_id, seed_path=seed_path,
            run_mode=run_mode, render=render,
        )

    # ============================================================ 研究单开

    def research_only(self, content_id: str, *, seed_path: str | None = None,
                      user_materials: list[dict] | None = None,
                      run_mode: RunMode = RunMode.LOCAL_SEED) -> dict:
        """只跑研究阶段。用于"资料不足时先补材料"的入口。

        没给 seed / 材料时**沿用当前 revision 已冻结的 sources/claims**，
        而不是返回空结果——空手而归会被误读成"这条内容没有依据"，
        而事实是"依据早已在版本里，只是本次没有新增"。
        """
        content = self._load_content(content_id)
        run = self._open_run(content_id, run_mode)
        job = self._job_for(run, "research", {"content_id": content_id})
        res = self.research.research(
            topic=content.topic, content_id=content_id, seed_path=seed_path,
            user_materials=user_materials, run_mode=run_mode,
        )
        if not res.sources and not res.claims:
            reused = self._reuse_revision_evidence(content_id, res, run_mode)
            if reused:
                res.limitations.append(
                    "本次未新增来源：沿用当前版本已冻结的来源与主张，未重新检索"
                )
        self._save_sources(content_id, res)
        calls = self._persist_calls(res.calls, content_id=content_id, run=run)
        self._finish_job(job, "succeeded", {"sources": len(res.sources)})
        self._close_run(run, "succeeded")
        self._event(content_id, "research_completed",
                    {"sources": len(res.sources), "search_executed": res.search_executed},
                    run_mode)
        return {
            "run_id": run.id,
            "content_id": content_id,
            "sources": res.sources_as_dicts(),
            "claims": res.claims_as_dicts(),
            "search_executed": res.search_executed,
            "search_provider": res.search_provider,
            "access_failures": res.access_failures,
            "limitations": res.limitations,
            "degrade_notes": self.research.degrade_notes(res),
            "findings": res.findings,
            "real_calls_recorded": calls,
            "run_mode": run_mode.value,
            "notice": ("本次未执行自动搜索：未配置搜索 Provider，内容依据为已有资料"
                       if not res.search_executed else "本次已执行自动搜索"),
        }

    def _search_failure_note(self, research: ResearchResult) -> str:
        """把搜索通道的真实报错带到用户面前。

        旧文案把「搜索地址配错（404）」和「搜到了但没命中」说成同一句话
        「补充搜索尚未取得能回答本题的资料」，用户看着像前者是后者，只能反复重试。
        这里在检索全部失败时直接报出通道名、地址和上游原始报错。
        """
        cfg = self.runtime.search_provider()
        if cfg is None:
            return '补充搜索未配置，请在API设置中配置SearXNG。'
        traces = [t for t in (getattr(research, 'search_trace', None) or []) if t.get('tool') == 'configured_search']
        executed = [t for t in traces if t.get('state') in {'succeeded', 'failed'}]
        failed = [t for t in traces if t.get('state') == 'failed']
        if executed and len(failed) == len(executed):
            reasons = []
            for t in failed:
                msg = str(t.get('message') or '').strip()
                if msg and msg not in reasons:
                    reasons.append(msg)
            detail = '；'.join(reasons[:3])
            return (f'补充搜索通道「{cfg.name}」({cfg.adapter_type.value} @ {cfg.base_url}) '
                    f'{len(failed)} 次检索全部失败' + (f'，上游报错：{detail}' if detail else '')
                    + '。请到 API 设置核对搜索服务地址，并用「连通性校验」复测。')
        # 检索本身跑通了：正文能读到，但读到的内容答不了原题。
        # 旧文案一律说「尚未取得资料」，把"搜到了但不对题"讲成"没搜到"，
        # 用户会一直去修搜索。这里改为说明实际读到了哪些站点。
        from urllib.parse import urlsplit
        from .evidence_gate import CONTEXT_KINDS
        readable = [s for s in (getattr(research, 'sources_as_dicts', lambda: [])() or [])
                    if s.get('kind') not in CONTEXT_KINDS
                    and s.get('access_state') == 'ok'
                    and s.get('excerpt_basis') in {'user_provided', 'full_text', 'local_file'}
                    and len(str(s.get('excerpt') or '').strip()) >= 30]
        if readable:
            hosts = []
            for s in readable:
                host = urlsplit(str(s.get('url') or '')).hostname or str(s.get('id'))
                if host not in hosts:
                    hosts.append(host)
            return (f'检索本身是通的（{len(executed) - len(failed)} 次成功），已读到 {len(readable)} 篇正文'
                    f'（{"、".join(hosts[:4])}），但没有一篇能回答原题：检索结果与原题不符。'
                    '这不是搜索故障——换更具体的检索口径，或改用「引导式创作」自己提供资料。')
        return '补充搜索尚未取得能回答本题的资料。'

    def _reuse_revision_evidence(self, content_id: str, res: ResearchResult,
                                 run_mode: RunMode, *, base_revision_id: str | None = None) -> bool:
        """把当前 revision 里冻结的 claims/sources 装回研究结果。返回是否命中。"""
        from .research_service import ClaimModel, SourceModel

        with self.sf() as s:
            content = s.get(ContentItem, content_id)
            if content is None or not content.active_revision_id:
                return False
            rev = s.get(ContentRevision, base_revision_id or content.active_revision_id)
            if rev is None or rev.content_id != content_id:
                return False
            blob = rev.claims_json or {}
            # Keep frozen access state and provenance. Namespace reused IDs
            # when new research already has its own sources and claims.
            merge=bool(res.sources or res.claims)
            mapping={};added=False
            for src in blob.get('sources',[]):
                original=SourceModel.model_validate(src)
                match=next((v for v in res.sources if v.url==original.url and v.path==original.path and v.excerpt==original.excerpt and v.access_state==original.access_state and v.excerpt_basis==original.excerpt_basis),None)
                if match:mapping[src['id']]=match.id;continue
                id=f'B{len(res.sources)+1:03d}' if merge else src['id']
                while any(v.id==id for v in res.sources):id='B'+id
                mapping[src['id']]=id
                res.sources.append(original.model_copy(update={'id':id}));added=True
            for c in blob.get("claims", []):
                refs=[mapping.get(id,id) for id in c.get('source_ids') or []]
                if any(v.statement==c.get('statement','') and v.source_ids==refs for v in res.claims):continue
                res.claims.append(ClaimModel(
                    id=('B'+str(len(res.claims)+1).zfill(3)) if merge else c.get("id"), kind=c.get("kind", "fact"),
                    statement=c.get("statement", ""),
                    source_ids=refs,
                ))
                added=True
            for x in (rev.limitations_json or {}).get("limitations", []):
                res.limitations.append(x)
        return added

    # ============================================================ 改稿入口

    def change_request(self, content_id: str, *, base_revision_id: str,
                       instruction: str, run_mode: RunMode = RunMode.LOCAL_SEED) -> dict:
        """一句话修改 → 新 revision → 为新版本生成独立的双平台产物。"""
        res = self.compose.apply_change_request(
            content_id, base_revision_id=base_revision_id,
            instruction=instruction, run_mode=run_mode,
        )
        # 平台文字按目标修改；两份克隆均无新版本产物，须分别渲染后才能预览。
        rerendered = []
        for pr_id, platform in self._platform_revisions(res["new_revision_id"]):
            rr = self.pipeline.render(pr_id)
            rerendered.append({"platform": platform,
                               "platform_revision_id": pr_id,
                               "ok": bool(rr.get("ok")),
                               "manifest_hash": rr.get("manifest_hash"),
                               "issues": rr.get("issues", [])})
        res["rerendered"] = rerendered
        with self.sf() as s:
            for item in res["platforms"]:
                item["state"] = s.get(PlatformRevision, item["platform_revision_id"]).state
        res["note"] = ("已创建新版本并重渲染；旧版本与旧批准保持不变，"
                       "新版本需重新预览与批准")
        return res

    # ============================================================ Run/Job

    def _open_run(self, content_id: str, run_mode: RunMode) -> Run:
        with self.sf() as s:
            run = Run(
                content_id=content_id, stage="produce", mode=run_mode.value,
                state="running", input_hash=None, attempt=1,
                lease_owner=self.actor, fencing_token=1,
            )
            s.add(run)
            s.commit()
            s.refresh(run)
            s.expunge(run)
            return run

    def _close_run(self, run: Run, state: str, *, blocked_stage: str | None = None,
                   error: str | None = None) -> None:
        """收尾一个 run。

        `error` 必须传：只把原因塞进 Job.output_refs 会让 `run.error` 永远为空，
        前端就只能显示一句笼统的"任务暂停或已取消"，用户无从知道到底卡在哪。
        """
        with self.sf() as s:
            r = s.get(Run, run.id)
            if r is None or r.state == 'cancelled':
                return
            r.state = state
            r.blocked_stage = blocked_stage
            if error is not None:
                r.error = error
            s.commit()

    def _job_for(self, run: Run, stage: str, payload: dict) -> Job:
        if self.check_control:
            self.check_control()
        """取或建 job。同 (run, stage, input_hash) 复用 —— 重试不重复追加。"""
        input_hash = _hash(json.dumps(payload, sort_keys=True, ensure_ascii=False))
        with self.sf() as s:
            existing = (
                s.query(Job)
                .filter_by(run_id=run.id, stage=stage, input_hash=input_hash)
                .one_or_none()
            )
            if existing is not None:
                existing.attempt += 1
                existing.state = "running"
                existing.lease_owner = self.actor
                existing.started_at = datetime.now(timezone.utc)
                s.commit()
                s.refresh(existing)
                s.expunge(existing)
                return existing
            job = Job(run_id=run.id, stage=stage, state="running",
                      input_hash=input_hash, attempt=1, lease_owner=self.actor,
                      fencing_token=1, started_at=datetime.now(timezone.utc))
            s.add(job)
            s.commit()
            s.refresh(job)
            s.expunge(job)
            return job

    def _finish_job(self, job: Job, state: str, refs: dict,
                    *, error: str | None = None) -> None:
        with self.sf() as s:
            j = s.get(Job, job.id)
            if j is None or j.state == 'cancelled':
                return
            j.state = state
            j.output_refs = refs
            j.error = error
            j.finished_at = datetime.now(timezone.utc)
            s.commit()

    def _block(self, out: dict, run: Run, content_id: str, stage: str,
               exc: Exception) -> dict:
        out["partial"] = True
        out["blocked_stage"] = stage
        out["error"] = {"code": getattr(exc, "code", "ERROR"),
                        "message": str(exc),
                        "details": getattr(exc, "details", {})}
        out["note"] = (f"在 {stage} 阶段中止；已完成的阶段保留，"
                       "补齐原因后可只重跑该阶段（不产出半成品）")
        self._close_run(run, "failed", blocked_stage=stage,
                        error=out["error"]["message"] or f"在 {stage} 阶段中止")
        # 从未产出过版本的内容不能一直挂着"排队中"：那样它看起来还在跑，
        # 却又没有可预览/可调整的版本。已发布过的内容不动（重跑失败不该抹掉既有状态）。
        if content_id:
            with self.sf() as s:
                item = s.get(ContentItem, content_id)
                if item is not None and not item.active_revision_id:
                    update_content_state(s,item,'blocked')
                    s.commit()
        return out

    # ============================================================ ProviderCall

    def _persist_calls(self, calls: list, *, content_id: str, run: Run) -> int:
        """把 ProviderCall 落库。返回**真实调用**条数（fixture 不计入）。

        未知结果（state=unknown）照记不误，费用保持 NULL：
        "结果未知"不等于"没花钱"，也不等于 0。
        """
        real = 0
        with self.sf() as s:
            for c in calls:
                exists = (
                    s.query(ProviderCallRow).filter_by(request_key=c.request_key)
                    .one_or_none()
                )
                if exists is not None:
                    continue   # 重试不重复记账（unique(request_key) 的语义前置）
                row = ProviderRuntime.to_row(c, content_id=content_id)
                s.add(row)
                if c.run_mode == RunMode.REAL:
                    real += 1
            s.commit()
        return real

    # ============================================================ 落库辅助

    def _ensure_content(self, topic: str, run_mode: RunMode) -> str:
        with self.sf() as s:
            batch = s.query(Batch).first()
            if batch is None:
                batch = Batch(item_limit=self.settings.default_batch_item_limit,
                              cost_mode=self.settings.cost_mode_default)
                s.add(batch)
                s.flush()
            n = s.query(ContentItem).count()
            item = ContentItem(
                batch_id=batch.id, display_id=f"C{n + 1:03d}", topic=topic,
                selected_by="ai", selection_reason="自动生产链路自动建条目",
                state="drafting", run_mode=run_mode.value,
            )
            s.add(item)
            s.commit()
            return item.id

    def _load_content(self, content_id: str) -> ContentItem:
        with self.sf() as s:
            c = s.get(ContentItem, content_id)
            if c is None:
                raise NotFound(f"内容不存在：{content_id}")
            s.expunge(c)
            return c

    def _audience_of(self, content_id: str) -> str:
        with self.sf() as s:
            c = s.get(ContentItem, content_id)
            return "" if c is None else f"围绕「{c.topic}」的受众"

    def _active_revision_id(self, content_id: str) -> str | None:
        with self.sf() as s:
            c = s.get(ContentItem, content_id)
            return c.active_revision_id if c else None

    def _platform_revisions(self, revision_id: str) -> list[tuple[str, str]]:
        with self.sf() as s:
            rows = (
                s.query(PlatformRevision).filter_by(content_revision_id=revision_id)
                .order_by(PlatformRevision.platform).all()
            )
            return [(r.id, r.platform) for r in rows]

    def _save_sources(self, content_id: str, res: ResearchResult) -> None:
        """sources 落表（可查询侧影）。claims 仍随 revision 冻结。"""
        with self.sf() as s:
            s.query(SourceRow).filter_by(content_id=content_id).delete()
            for src in res.sources:
                s.add(SourceRow(
                    content_id=content_id, source_key=src.id, kind=src.kind,
                    url=src.url, file_key=src.path, retrieved_at=src.retrieved_at,
                    locator=src.locator, excerpt=src.excerpt,
                    excerpt_basis=src.excerpt_basis, sha256=src.sha256,
                    access_state=src.access_state.value, supports=src.supports,
                    limitations=src.limitations, run_mode=res.run_mode.value,
                ))
            s.commit()

    def _event(self, content_id: str, type_: str, payload: dict, run_mode: RunMode) -> None:
        with self.sf() as s:
            s.add(Event(entity_type="content_item", entity_id=content_id, type=type_,
                        actor=self.actor, run_mode=run_mode.value, payload=payload))
            s.commit()

    # ============================================================ 公开视图

    @staticmethod
    def _cand_public(c: TopicCandidate) -> dict:
        return {
            "id": c.id, "topic": c.topic,
            "audience_problem": c.audience_problem,
            "supporting_claim_ids": c.supporting_claim_ids,
            "selection_reason": c.selection_reason,
            "total_score": c.total_score,
            "rule_scores": c.rule_scores,
            "hard_conditions": c.hard_conditions,
            "heat_verified": c.heat_verified,
            "rejected_reason": c.rejected_reason,
        }


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
