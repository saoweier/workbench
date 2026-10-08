"""研究与证据服务（P2/T09）。

职责：把「一个主题」变成**结构化、可溯源**的 sources + claims。

设计约束（对照 docs/02-modules.md §M02、§M04）：
- 搜索**未配置**时不是失败，是明确的降级状态：只用已有资料，
  且 `search_executed=False`，界面必须标注"未执行自动搜索"。
- 访问失败要**逐条记录** access_state，不静默丢弃。
- 搜索摘要（snippet）不能当事实依据——依据强度由 excerpt_basis 承载，
  判定规则在 claim_rules。
- 定位不到原文时 excerpt 为 None 且 access_state=snippet_only，不假装有摘录。
- 「资料不足仍继续」必须把范围限制写进 limitations 并透传到成品。
"""
from __future__ import annotations

import hashlib
import re
import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..core.errors import NotConfigured,ValidationFailed,StateConflict
from ..services.claim_rules import validate_claims_sources
from .provider_contract import ProviderCall, RunMode
from .provider_runtime import ProviderRuntime


class AccessState(str, Enum):
    OK = "ok"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    NOT_FOUND = "not_found"
    UNCONFIGURED = "unconfigured"
    SNIPPET_ONLY = "snippet_only"


class SourceModel(BaseModel):
    id: str
    kind: str
    url: str | None = None
    path: str | None = None
    retrieved_at: datetime | None = None
    locator: str | None = None
    excerpt: str | None = None
    #: full_text / user_provided / local_file / search_snippet
    excerpt_basis: str = "user_provided"
    sha256: str | None = None
    access_state: AccessState = AccessState.OK
    supports: str | None = None
    limitations: str | None = None


class ClaimModel(BaseModel):
    id: str
    kind: str
    statement: str
    source_ids: list[str] = Field(default_factory=list)


class ResearchResult(BaseModel):
    content_id: str | None = None
    topic: str
    sources: list[SourceModel] = Field(default_factory=list)
    claims: list[ClaimModel] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    search_executed: bool = False
    search_provider: str | None = None
    access_failures: list[dict] = Field(default_factory=list)
    run_mode: RunMode = RunMode.LOCAL_SEED
    calls: list[ProviderCall] = Field(default_factory=list)
    findings: list[dict] = Field(default_factory=list)
    assessment: dict | None = None
    search_trace: list[dict] = Field(default_factory=list)

    def claims_as_dicts(self) -> list[dict]:
        return [c.model_dump() for c in self.claims]

    def sources_as_dicts(self) -> list[dict]:
        return [s.model_dump(mode="json") for s in self.sources]


class ResearchService:
    def __init__(self, runtime: ProviderRuntime, *, actor: str = "coisini") -> None:
        self.runtime = runtime
        self.actor = actor

    # ------------------------------------------------------------ 主流程

    def research(
        self,
        *,
        topic: str,
        requirements: str = '',
        content_id: str | None = None,
        seed_path: str | Path | None = None,
        user_materials: list[dict] | None = None,
        max_sources: int = 8,
        run_mode: RunMode = RunMode.LOCAL_SEED,
        search_planner=None,
        existing_result: ResearchResult | None = None,
    ) -> ResearchResult:
        from .evidence_gate import factual_sources
        reuse_frozen = existing_result is not None and bool(factual_sources(existing_result.sources_as_dicts()))
        result = existing_result or ResearchResult(content_id=content_id, topic=topic, run_mode=run_mode)

        # 1) 已有 seed 的 sources/claims 直接复用（local_seed，零 provider 调用）
        if seed_path:
            self._absorb_seed(Path(seed_path), result)
            if result.sources:
                result.limitations.append(
                    "来源来自项目已有文档（local_seed），不是本次自动检索结果"
                )

        # 2) 用户直接提供的材料
        if user_materials:
            self._absorb_user_materials(user_materials, result)
        selected_readable = False
        if run_mode==RunMode.REAL:
            references=[] if reuse_frozen else [s for s in result.sources if s.kind=='hotpush_context' and s.url]
            for reference in references:
                self._read_public(reference.url,result,title=reference.locator or '',selected_reference=True)
                selected_readable=selected_readable or bool(factual_sources([result.sources[-1].model_dump(mode='json')]))
            urls=list(dict.fromkeys(u.rstrip('.,，。);；]') for m in (user_materials or [])
                if m.get('kind') not in {'editorial_plan','hotpush_context'}
                for u in re.findall(r'https?://[^\s<>\]）]+',(m.get('text') or '').split('选题来源快照（',1)[0])))[:4]
            for url in urls:
                if not any(s.url==url and s.excerpt_basis=='full_text' for s in result.sources):self._read_public(url,result)
            from .token_cost import is_token_cost,PRICE_URL
            if is_token_cost(topic) and not any((s.url or '').rstrip('/')==PRICE_URL.rstrip('/') for s in result.sources):
                self._read_public(PRICE_URL,result)

        # Curated public basics are available even without a search provider.
        # Keep real evidence separate from fixture/example records.
        from .fruit_content import is_fruit_topic, evidence
        if run_mode == RunMode.REAL and is_fruit_topic(topic):
            sources, claims = evidence()
            existing = {s.id for s in result.sources}
            result.sources.extend(SourceModel.model_validate(s) for s in sources if s['id'] not in existing)
            known = {c.id for c in result.claims}
            result.claims.extend(ClaimModel.model_validate(c) for c in claims if c['id'] not in known)
            result.limitations.append('使用已核验的 USDA SR Legacy 2018 水果资料库，非本次实时检索；每100克生鲜可食部，品种和成熟度有差异；没有GI及治疗功效依据。')

        # Revisions reuse actual frozen articles before spending another search.
        from .evidence_gate import factual_sources
        if reuse_frozen:
            result.limitations.append('沿用旧版本冻结的可读证据；本次未重新检索或核验这些来源')
            return self._validated_result(result)
        if selected_readable:
            # The following research Skill assesses the original body. It may
            # call discover with concrete gaps; don't search speculatively here.
            result.search_provider='HotPush 原链接（补充搜索按需执行）'
            return self._validated_result(result)
        # 3) 自动搜索（未配置则降级）
        search_cfg = self.runtime.search_provider()
        if search_cfg is None:
            result.search_executed = False
            result.limitations.append(
                "未执行自动搜索：未配置搜索 Provider，本次仅基于已有资料整理"
            )
        else:
            result.search_provider = search_cfg.name
            try:
                from .evidence_gate import needs_grounding,factual_sources
                if run_mode==RunMode.REAL and search_planner and needs_grounding(topic,requirements) and not factual_sources(result.sources_as_dicts()):
                    self.discover(result,requirements=requirements,planner=search_planner,max_sources=max_sources)
                    return self._validated_result(result)
                call, res = self.runtime.search(
                    topic, limit=max_sources, content_id=content_id,
                    run_mode=run_mode, prompt_version="research.v1",
                )
                result.calls.append(call)
                result.search_executed = True
                if res.ok and res.parsed:
                    self._absorb_search_results(res.parsed.get("results", []), result)
                else:
                    result.access_failures.append({
                        "stage": "search", "error_code": res.error_code,
                        "message": res.error_message,
                    })
                    result.limitations.append(
                        f"自动搜索未取得结果（{res.error_code}），本次仅基于已有资料整理"
                    )
            except NotConfigured:
                result.search_executed = False
                result.limitations.append("自动搜索不可用，本次仅基于已有资料整理")

        return self._validated_result(result)

    def _validated_result(self,result):
        # 校验证据链，把问题写进 findings（不静默）
        findings = validate_claims_sources(result.claims_as_dicts(), result.sources_as_dicts())
        result.findings = [f.model_dump() for f in findings]
        for f in findings:
            if f.code == "SNIPPET_ONLY_FACT":
                result.limitations.append(f"存在仅靠搜索摘要支撑的主张（{f.location}），需补全文来源")

        if not result.sources:
            result.limitations.append("本次没有任何来源，产出内容不得包含事实性主张")
        return result

    def refresh_unread_sources(self,result):
        """Explicit task resume reopens failed article leads after a reader fix."""
        urls=list(dict.fromkeys(s.url for s in result.sources if s.url and s.kind in {'public_web','search_result'}
            and (s.access_state!=AccessState.OK or s.excerpt_basis!='full_text')))
        for url in urls:
            self._read_public(url,result)
            source=result.sources[-1]
            result.search_trace.append({'tool':'read_public_article','reason':'用户修复后继续，重新读取此前失败来源','url':source.url,
                'source_id':source.id,'state':source.access_state.value,'body_chars':len(source.excerpt or ''),'message':source.limitations})

    def discover(self,result,*,requirements='',planner=None,gaps=None,max_sources=8):
        """Execute the LLM's read-only search plan and retain every tool outcome."""
        from .evidence_gate import factual_sources
        cfg=self.runtime.search_provider()
        if cfg is None:
            result.search_trace.append({'tool':'configured_search','state':'not_configured','message':'未配置补充搜索服务；请在API设置中配置SearXNG。'})
            return
        plan=planner(gaps or [],result.sources_as_dicts()) if planner else None
        queries=plan.queries if plan else [result.topic]
        queries=list(dict.fromkeys(q.strip() for q in queries if q.strip()))
        if not queries:raise ValueError('调研技能没有给出有效检索词')
        # Preserve the user's actual question. Rewriting every query can lose
        # the event entirely even when the unchanged question is searchable.
        model_queries=list(queries)
        queries=list(dict.fromkeys([result.topic.strip(),*queries]))[:len(queries)]
        result.search_executed=True
        if plan:result.search_trace.append({'tool':'llm_search_plan','objective':plan.objective,'queries':queries,'model_queries':model_queries,'query_policy':'保留原题检索，再执行模型的互补检索词','required_evidence':plan.required_evidence,'gaps':gaps or []})
        seen={s.url for s in result.sources if s.url};read_count=0
        per_query=max(1,(max_sources+len(queries)-1)//len(queries))
        for query in queries:
            if read_count>=max_sources:break
            try:
                hits=[]
                if cfg:
                    origin=next((s.url for s in result.sources if s.kind=='hotpush_context' and s.url),None)
                    extra={'source_url':origin} if origin and getattr(cfg,'adapter_type',None)=='searxng' else {}
                    call,response=self.runtime.search(query,limit=max_sources,content_id=result.content_id,run_mode=RunMode.REAL,prompt_version='research.agent.v1',**extra)
                    result.calls.append(call)
                    hits=(response.parsed or {}).get('results',[]) if response.ok else []
                    result.search_trace.append({'tool':'configured_search','engine':cfg.name,'query':query,'state':'succeeded' if response.ok else 'failed','result_count':len(hits),'message':response.error_message,'backend':response.meta.get('search_backend',getattr(cfg,'adapter_type',None)),'upstream':response.meta.get('unresponsive_engines',[]),'candidates':[{k:v for k,v in h.items() if k!='read'} for h in response.meta.get('candidates',hits)],'irrelevant_count':response.meta.get('irrelevant_count',0),'query_report':response.meta.get('query_report')})
            except (StateConflict,ValidationFailed):raise
            except Exception as exc:
                result.access_failures.append({'stage':'configured_search','query':query,'message':str(exc)[:240]});continue
            while True:
                # Read all available candidates from the configured service;
                # never switch to unauthenticated search-engine HTML scraping.
                new_hits=[h for h in hits if h.get('url') and h['url'] not in seen][:min(per_query,max_sources-read_count)]
                acquired=False
                # A HotPush URL is already present in ``seen`` because the
                # first direct read may have failed. The query service can
                # later return a readable cached body for that exact URL.
                # Reuse that body to upgrade the existing provenance record;
                # dropping it as a duplicate would discard the only readable
                # evidence while leaving the workflow falsely blocked.
                for hit in hits:
                    if not hit.get('url') or hit['url'] not in seen:
                        continue
                    cached=hit.get('read')
                    if not isinstance(cached,dict) or cached.get('state')!='readable' or not str(cached.get('body') or '').strip():
                        continue
                    existing=next((source for source in result.sources
                        if source.url==hit['url'] and source.kind in {'public_web','search_result'}
                        and (source.excerpt_basis!='full_text' or source.access_state!=AccessState.OK)),None)
                    if existing is None or read_count>=max_sources:
                        continue
                    self._read_public(hit['url'],result,title=hit.get('title',''),cached_read=cached,existing_source=existing)
                    read_count+=1
                    acquired=acquired or bool(factual_sources([existing.model_dump(mode='json')]))
                    result.search_trace.append({'tool':'read_public_article','query':query,'url':existing.url,'source_id':existing.id,
                        'state':existing.access_state.value,'body_chars':len(existing.excerpt or ''),'message':existing.limitations,
                        'reused_queryer_read':True})
                for hit in new_hits:
                    seen.add(hit['url']);read_count+=1
                    self._read_public(hit['url'],result,title=hit.get('title',''),cached_read=hit.get('read'))
                    source=result.sources[-1]
                    acquired=acquired or bool(factual_sources([source.model_dump(mode='json')]))
                    result.search_trace.append({'tool':'read_public_article','query':query,'url':source.url,'source_id':source.id,
                        'state':source.access_state.value,'body_chars':len(source.excerpt or ''),'message':source.limitations})
                if acquired or read_count>=max_sources:break
                # Exhaust remaining candidates from this engine before changing
                # engines. A previous query's generic body cannot stop rescue.
                if any(h.get('url') and h['url'] not in seen for h in hits):continue
                break
        if not factual_sources(result.sources_as_dicts()):result.limitations.append('配置的搜索服务未取得可用正文；具体查询及引擎状态已保存在技能诊断。')

    # ------------------------------------------------------------ 吸收来源

    def _absorb_seed(self, seed_path: Path, result: ResearchResult) -> None:
        data = json.loads(seed_path.read_text(encoding="utf-8"))
        src_dir = seed_path.parent
        for s in data.get("sources", []):
            state = AccessState.OK
            excerpt = None
            basis = "user_provided"
            sha = None
            p = s.get("path")
            if p:
                target = (src_dir / p).resolve()
                if target.exists():
                    raw = target.read_bytes()
                    sha = hashlib.sha256(raw).hexdigest()
                    excerpt = self.extract_excerpt(
                        raw.decode("utf-8", errors="ignore"), s.get("locator")
                    )
                    if excerpt is None:
                        # 文件在，但定位不到片段：不假装有摘录
                        state = AccessState.SNIPPET_ONLY
                else:
                    state = AccessState.NOT_FOUND
                    result.access_failures.append({
                        "stage": "read_source", "source_id": s.get("id"),
                        "path": p, "message": "本地来源文件不存在",
                    })
            result.sources.append(SourceModel(
                id=s["id"], kind=s.get("kind", "unknown"),
                path=p, locator=s.get("locator"),
                excerpt=excerpt, excerpt_basis=basis, sha256=sha,
                access_state=state,
                supports=s.get("supports"), limitations=s.get("limitations"),
                retrieved_at=datetime.now(timezone.utc),
            ))
        for c in data.get("claims", []):
            result.claims.append(ClaimModel(
                id=c["id"], kind=c.get("kind", "fact"),
                statement=c.get("statement", ""),
                source_ids=c.get("source_ids", []),
            ))

    def _absorb_user_materials(self, materials: list[dict], result: ResearchResult) -> None:
        expanded=[]
        marker='选题来源快照（仅记录HotPush返回顺序及时间，不能证明全网热度或文章观点）：'
        for material in materials:
            text=material.get('text') or ''
            if marker in text:
                original,context=text.split(marker,1)
                if original.strip():expanded.append({**material,'text':original.strip()})
                lead={'text':marker+context,'kind':'hotpush_context'}
                try:
                    import json
                    from .trend_boards import safe_url
                    item=json.loads(context).get('item') or {}
                    lead.update(url=safe_url(item.get('url')),locator=str(item.get('title') or '')[:200])
                except (ValueError,TypeError,AttributeError):pass
                expanded.append(lead)
            else:expanded.append(material)
        materials=expanded
        base = len(result.sources)
        for i, m in enumerate(materials, start=1):
            text = m.get("text") or ""
            excerpt = (self.extract_excerpt(text, m.get("locator")) if m.get("locator") else text[:16000]) or None
            result.sources.append(SourceModel(
                id=m.get("id") or f"U{base + i:02d}",
                kind=m.get("kind", "user_provided"),
                path=m.get("path"), url=m.get("url"),
                locator=m.get("locator"), excerpt=excerpt,
                excerpt_basis="url_only" if text.strip().startswith(('http://','https://')) and not re.sub(r'https?://\S+','',text).strip() else "user_provided",
                sha256=hashlib.sha256(text.encode()).hexdigest() if text else None,
                access_state=AccessState.OK if excerpt else AccessState.SNIPPET_ONLY,
                supports=m.get("supports"),
            ))
            if text.strip():
                result.claims.append(ClaimModel(id=f"C{len(result.claims)+1:02d}",
                    kind="opinion" if m.get('kind') in {'editorial_plan','hotpush_context'} or result.sources[-1].excerpt_basis=='url_only' else "document_observation",
                    statement=("编辑方向（非事实依据）：" if m.get('kind') == 'editorial_plan' else "资料原文记载：") + text.strip()[:400],
                    source_ids=[result.sources[-1].id]))

    def _absorb_search_results(self, items: list[dict], result: ResearchResult) -> None:
        existing = len(result.sources)
        for i, it in enumerate(items, start=1):
            snippet = it.get("snippet")
            result.sources.append(SourceModel(
                id=f"R{existing + i:02d}",
                kind="search_result",
                url=it.get("url"),
                locator="snippet",
                excerpt=snippet,
                # 摘要来源，不足以支撑事实
                excerpt_basis="search_snippet",
                access_state=AccessState.OK if snippet else AccessState.SNIPPET_ONLY,
                limitations="仅为搜索摘要，未取得全文",
            ))
            source = result.sources[-1]
            if result.run_mode == RunMode.REAL and source.url:
                from .source_reader import read_source_detail
                from .public_research import BLOCKED_PLATFORMS
                try:
                    cfg = self.runtime.search_provider()
                    cached=it.get('read')
                    if cached is not None:
                        if cached.get('state')!='readable' or not str(cached.get('body') or '').strip():raise ValueError(cached.get('message') or '独立查询器未取得此页正文')
                        text,sha,final_url=cached['body'],cached.get('sha256'),cached.get('final_url') or source.url
                    else:text, sha, final_url = read_source_detail(source.url, allow_localhost=bool(cfg and cfg.allow_localhost),blocked_domains=BLOCKED_PLATFORMS)
                    source.url=final_url
                    source.excerpt = text
                    source.locator = "公开网页正文（去除脚本与样式；读取范围保留在来源记录中）"
                    source.excerpt_basis = "full_text"
                    source.access_state = AccessState.OK
                    source.sha256 = sha
                    source.retrieved_at = datetime.now(timezone.utc)
                    source.limitations = "取得公开网页文字；事实仍需结合来源可信度核查"
                    if '本文由AI生成' in text:source.limitations+='；该来源声明本文由AI生成，不能单独证实最早出处或传播统计'
                    result.claims.append(ClaimModel(id=f"C{len(result.claims)+1:02d}",
                        kind="document_observation", statement="网页原文记载：" + text[:400],
                        source_ids=[source.id]))
                except Exception as exc:
                    result.access_failures.append({"stage":"read_source", "source_id":source.id,
                        "url":source.url, "message":str(exc)[:200]})
                    source.access_state = AccessState.SNIPPET_ONLY

    def _read_public(self,url,result,*,title='',selected_reference=False,cached_read=None,existing_source=None):
        from .source_reader import read_source_detail
        from .public_research import BLOCKED_PLATFORMS
        source=existing_source or SourceModel(id=f'W{len(result.sources)+1:02d}',kind='public_web',url=url,excerpt_basis='search_snippet',access_state=AccessState.SNIPPET_ONLY,locator=title or '用户提供的网页链接')
        if existing_source is None:
            result.sources.append(source)
        try:
            if cached_read is not None:
                if cached_read.get('state')!='readable' or not str(cached_read.get('body') or '').strip():raise ValueError(cached_read.get('message') or '独立查询器未取得此页正文')
                text,sha,final=cached_read['body'],cached_read.get('sha256'),cached_read.get('final_url') or url
            else:text,sha,final=read_source_detail(url,blocked_domains=() if selected_reference else BLOCKED_PLATFORMS)
            source.url=final;source.excerpt=text;source.sha256=sha;source.excerpt_basis='full_text';source.access_state=AccessState.OK;source.retrieved_at=datetime.now(timezone.utc)
            source.limitations='已读取公开网页原文；来源观点不能自动等同于事实，需核对原题与出处。'
            if '本文由AI生成' in text:source.limitations+=' 该来源声明本文由AI生成，只能注明来源归因，不能单独证实最早出处或传播统计。'
            result.claims.append(ClaimModel(id=f'C{len(result.claims)+1:02d}',kind='document_observation',statement='网页原文记载：'+text[:400],source_ids=[source.id]))
        except Exception as exc:
            source.limitations=str(exc)[:240];result.access_failures.append({'stage':'read_source','source_id':source.id,'url':url,'message':str(exc)[:240]})
        if selected_reference:
            result.search_trace.append({'tool':'read_selected_topic','reason':'优先读取用户选择的HotPush议题原链接',
                'original_url':url,'url':source.url,'title':title,'source_id':source.id,
                'state':source.access_state.value,'body_chars':len(source.excerpt or ''),'message':source.limitations})

    # ------------------------------------------------------------ 摘录定位

    @staticmethod
    def extract_excerpt(text: str | None, locator: str | None) -> str | None:
        """按 locator 在文本里定位摘录。

        locator 支持三种写法：
        - 关键词（含中文短语）：找到所在行并截取该行
        - `line:12` / `line:12-18`：按行号
        - 整段引用（前后各约 80 字上下文）

        定位不到返回 None —— **绝不返回伪造摘录**。
        """
        if not text:
            return None
        if not locator:
            return text[:200]

        loc = locator.strip()

        # 行号
        if loc.startswith("line:"):
            try:
                spec = loc[5:]
                if "-" in spec:
                    a, b = spec.split("-", 1)
                    lines = text.splitlines()[int(a) - 1:int(b)]
                else:
                    lines = [text.splitlines()[int(spec) - 1]]
                joined = "\n".join(lines).strip()
                return joined or None
            except Exception:
                return None

        # 关键词 / 引文：取第一个命中的片段（分号后各段均可尝试）
        for token in [t.strip() for t in loc.replace("；", ";").split(";") if t.strip()]:
            idx = text.find(token)
            if idx >= 0:
                start = max(0, idx - 40)
                end = min(len(text), idx + len(token) + 60)
                return text[start:end].strip()
        return None

    # ------------------------------------------------------------ 降级说明

    @staticmethod
    def degrade_notes(result: ResearchResult) -> list[str]:
        """可继续但范围受限时必须展示的说明。"""
        notes = list(result.limitations)
        if not result.search_executed:
            notes.append('已读取热榜原链接，未执行补充搜索' if any(t.get('tool')=='read_selected_topic' and t.get('state')=='ok' for t in result.search_trace)
                         else "本次未执行自动搜索，内容依据为用户提供的材料")
        if result.access_failures:
            notes.append(f"有 {len(result.access_failures)} 处来源访问失败，已在正文边界中标注")
        return notes
