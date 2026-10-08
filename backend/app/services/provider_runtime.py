"""Provider 运行时（P2/T08）。

职责边界（重要）：
- 只做「解析配置 → 选 adapter → 发一次调用 → 产出 (ProviderCall, AdapterResult)」
- **不自己 commit**，不持有 session。调用方（research/compose/production）把
  ProviderCall 纳入自己的事务，于是 `unique(request_key)` 能统一兜住重复记账。
- 依赖注入 ProviderStore / SecretStore，测试可用临时目录实例，不碰生产 storage。

不变量落实：
- 未配置 provider 时**不发任何调用**（第 5/9 条：不伪称已调用）
- 用量与费用：拿不到就 None，**不写 0**；估算不冒充实际扣费
- fixture / local_seed 与 real 严格分离，由 ProviderCall.run_mode 承载
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
import math
from sqlalchemy import func
from typing import Callable

from ..core.errors import NotConfigured, StateConflict, BudgetLimitError
from ..models.entities import ProviderCallRow, ProviderExchange, ContentItem, Batch, Event
from .adapters import ADAPTER_REGISTRY, FIXTURE_ADAPTER, Transport
from .adapters.base import AdapterResult, BaseAdapter
from .adapters.http_transport import HttpTransport
from .provider_contract import (
    AdapterType,
    BillingState,
    ProviderCall,
    ProviderConfig,
    ProviderKind,
    ProviderStatus,
    ProviderStore,
    RunMode,
    SecretStore,
)


def make_request_key(*parts: str | None) -> str:
    """请求编号。同 (内容, 阶段, prompt 版本) → 同 key，保证重试不重复记账。"""
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode())
        h.update(b"\x1f")
    return h.hexdigest()[:32]


class ProviderRuntime:
    def __init__(
        self,
        store: ProviderStore,
        secrets: SecretStore,
        *,
        transport_factory: Callable[[ProviderConfig], Transport | None] | None = None,
        force_fixture: bool = False,
        fixture_scenario: dict[int, str] | None = None,
    ) -> None:
        self.store = store
        self.secrets = secrets
        self.transport_factory = transport_factory
        self.force_fixture = force_fixture
        self.fixture_scenario = fixture_scenario or {}
        #: 复用 adapter 实例，让 fixture 的"按调用序号注入场景"跨轮次有效。
        #: 每轮新建 adapter 会让 _calls 归零，注入场景永远命中第 1 轮，
        #: 于是"1 轮修好"这类路径无法离线验证。
        self._adapter_cache: dict[tuple[str, bool], BaseAdapter] = {}
        #: 合成 fixture 配置也按 kind 缓存（同上原因）
        self._fixture_cfg_cache: dict[str, ProviderConfig] = {}
        self.session_factory = None
        self.context_content_id = None
        self.before_call = None

    # ------------------------------------------------------------ 选配置

    def text_provider(self) -> ProviderConfig | None:
        return self._pick(ProviderKind.TEXT)

    def search_provider(self) -> ProviderConfig | None:
        return self._pick(ProviderKind.SEARCH)

    def image_provider(self) -> ProviderConfig | None:
        return self._pick(ProviderKind.IMAGE)

    def generate_image(self,*,prompt,content_id,request_key,prompt_version):
        cfg=self.image_provider()
        if cfg is None:raise NotConfigured('图片模型未配置，请在API设置中配置图片接口或上传自己的配图')
        adapter=self._build_adapter(cfg,fixture=False)
        if not hasattr(adapter,'generate'):raise NotConfigured('请选择Images兼容接口，文字模型不能自动当作图片模型')
        rkey=make_request_key(content_id,'image',prompt_version,request_key,prompt)
        return self._execute(cfg,rkey,content_id,prompt_version,False,lambda:adapter.generate(prompt,request_key=rkey),input_bytes=len(prompt.encode()),max_output=cfg.max_output_tokens)

    def _pick(self, kind: ProviderKind) -> ProviderConfig | None:
        """选一个可用于真实调用的配置。

        比 ProviderStore.default_for 稍宽：允许"配置完整但尚未测试"的条目。
        理由是用户填完密钥后不该被强制先点一次测试才能生成；连接是否真的通，
        由实际调用结果（ProviderCall.state）回答，不由先验状态冒充。
        """
        preferred = self.store.default_for(kind)
        if preferred is not None:
            return preferred
        for c in self.store.list(kind):
            if not c.enabled:
                continue
            if not c.base_url or (c.requires_api_key() and not c.secret_ref):
                continue
            if kind in {ProviderKind.TEXT,ProviderKind.IMAGE} and not c.model_id:
                continue
            return c
        return None

    # ------------------------------------------------------------ 选 adapter

    def _build_adapter(self, cfg: ProviderConfig, *, fixture: bool) -> BaseAdapter:
        # 同 (配置, 是否 fixture) 复用同一实例：fixture 的调用序号才有意义
        api_key = self.secrets.get(cfg.secret_ref) if cfg.secret_ref else None
        fingerprint = hashlib.sha256((cfg.model_dump_json() + (api_key or "")).encode()).hexdigest()
        key = (cfg.id if fixture else fingerprint, fixture)
        cached = self._adapter_cache.get(key)
        if cached is not None:
            return cached
        if fixture:
            adapter: BaseAdapter = FIXTURE_ADAPTER(
                cfg, api_key=api_key or "fixture-key",
                scenario=self.fixture_scenario,
            )
        else:
            cls = ADAPTER_REGISTRY.get(cfg.adapter_type)
            if cls is None:
                raise NotConfigured(f"未知适配器类型：{cfg.adapter_type}")
            transport = self.transport_factory(cfg) if self.transport_factory else HttpTransport()
            adapter = cls(cfg, api_key=api_key, transport=transport)
        self._adapter_cache[key] = adapter
        return adapter

    # ------------------------------------------------------------ 文本调用

    def complete_text(
        self,
        *,
        prompt: str,
        system: str | None = None,
        max_tokens: int | None = None,
        json_schema: dict | None = None,
        job_id: str | None = None,
        content_id: str | None = None,
        revision_id: str | None = None,
        prompt_version: str | None = None,
        request_key: str | None = None,
        run_mode: RunMode | None = None,
        cfg: ProviderConfig | None = None,
    ) -> tuple[ProviderCall, AdapterResult]:
        cfg = cfg or self.text_provider()
        fixture = self.force_fixture or run_mode == RunMode.FIXTURE
        if cfg is None and not fixture:
            raise NotConfigured(
                "文本 Provider 未配置：可在设置页填写，或使用 run_mode=fixture 做离线验证"
            )
        if cfg is None:
            cfg = self._synthetic_fixture_config("text")

        adapter = self._build_adapter(cfg, fixture=fixture)
        content_id = content_id or self.context_content_id
        thinking=adapter.effective_thinking_mode() if hasattr(adapter,'effective_thinking_mode') else None
        rkey = make_request_key(content_id, "text", prompt_version, request_key, prompt, system, *([f'thinking:{thinking}'] if thinking else []))
        if self.session_factory and content_id:
            from .content_diagnostics import redact
            with self.session_factory() as s:
                old=s.query(Event).filter_by(entity_type='model_input',entity_id=rkey,type='text_request').first()
                if old is None:
                    s.add(Event(entity_type='model_input',entity_id=rkey,type='text_request',actor='system',run_mode=(RunMode.FIXTURE if fixture else (run_mode or RunMode.REAL)).value,
                        payload={'content_id':content_id,'prompt_version':prompt_version,'model':cfg.model_id,
                            'system':redact(system or ''),'prompt':redact(prompt),'json_schema':json_schema,'max_tokens':max_tokens or cfg.max_output_tokens}));s.commit()
        return self._execute(cfg, rkey, content_id, prompt_version, fixture,
            lambda: adapter.complete(prompt, system=system, max_tokens=max_tokens,
                                     json_schema=json_schema, request_key=rkey),
            input_bytes=len((prompt + (system or "") + str(json_schema or "")).encode()),
            max_output=max_tokens or cfg.max_output_tokens)

    def _execute(self, cfg, rkey, content_id, prompt_version, fixture, send, *, input_bytes=0, max_output=None):
        if self.before_call:
            self.before_call()
        sf = self.session_factory
        reserved = None
        if sf and not fixture:
            with sf() as s:
                s.connection().exec_driver_sql("BEGIN IMMEDIATE")
                previous = s.get(ProviderExchange, rkey)
                if previous:
                    if previous.response is not None and previous.state != "unknown":
                        return ProviderCall.model_validate(previous.call), AdapterResult(**previous.response)
                    raise StateConflict("上次模型请求结果未知，已阻止重复发送；请核查服务商请求记录")
                if content_id and s.query(ProviderExchange).filter_by(content_id=content_id).count() >= 40:
                    raise StateConflict("本次内容已达到 40 次调用上限")
                item = s.get(ContentItem, content_id) if content_id else None
                batch = s.get(Batch, item.batch_id) if item and item.batch_id else None
                if batch and batch.cost_mode == "hard_cap":
                    pricing = cfg.pricing or {}
                    rates = [pricing.get("input_per_1k_micro"), pricing.get("output_per_1k_micro")]
                    if batch.budget_limit_micro is None or max_output is None or any(r is None or r < 0 for r in rates):
                        raise BudgetLimitError("硬金额上限需要预算、完整费率与输出上限；尚未发起付费调用")
                    reserved = math.ceil((input_bytes * rates[0] + max_output * rates[1]) / 1000)
                    currency = pricing.get("currency", "CNY")
                    if currency != batch.currency:
                        raise BudgetLimitError("费率与预算币种不一致")
                    used = s.query(func.coalesce(func.sum(ProviderExchange.reserved_micro), 0)).filter_by(batch_id=batch.id).scalar()
                    if used + reserved > batch.budget_limit_micro:
                        raise BudgetLimitError("已用金额与本次预留超过批次上限；尚未发起调用")
                s.add(ProviderExchange(request_key=rkey, content_id=content_id,
                    batch_id=batch.id if batch else None, reserved_micro=reserved,
                    currency=batch.currency if batch else None))
                s.add(ProviderCallRow(request_key=rkey, content_id=content_id,
                    provider_config_id=cfg.id, provider_name=cfg.name, model_id=cfg.model_id,
                    prompt_version=prompt_version, state="unknown", run_mode="real"))
                s.commit()
        res = send()
        call = self._to_call(res, cfg=cfg, request_key=rkey, content_id=content_id,
                             prompt_version=prompt_version)
        if res.error_code in {"NETWORK_TIMEOUT", "UNKNOWN"}:
            call.state = "unknown"
        if sf and not fixture:
            with sf() as s:
                exchange = s.get(ProviderExchange, rkey)
                exchange.state = call.state
                exchange.response = {**asdict(res), "run_mode": res.run_mode.value}
                exchange.call = call.model_dump(mode="json")
                if reserved is not None and call.estimated_micro is not None:
                    exchange.reserved_micro = call.estimated_micro
                row = s.query(ProviderCallRow).filter_by(request_key=rkey).one()
                updated = self.to_row(call, content_id=content_id, provider_config_id=cfg.id,
                                     error_code=res.error_code, error_message=res.error_message,
                                     latency_ms=res.latency_ms)
                for column in ProviderCallRow.__table__.columns:
                    if column.name not in {"id", "started_at"}:
                        setattr(row, column.name, getattr(updated, column.name))
                s.commit()
        return call, res

    # ------------------------------------------------------------ 搜索调用

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        job_id: str | None = None,
        content_id: str | None = None,
        prompt_version: str | None = None,
        request_key: str | None = None,
        run_mode: RunMode | None = None,
        source_url: str | None = None,
    ) -> tuple[ProviderCall, AdapterResult]:
        cfg = self.search_provider()
        fixture = self.force_fixture or run_mode == RunMode.FIXTURE
        if cfg is None and not fixture:
            # 不是错误：搜索未配置是明确的"降级可继续"状态，由 research 层处理
            raise NotConfigured("搜索 Provider 未配置：将只基于已有资料生成，且不声称已自动搜索")
        if cfg is None:
            cfg = self._synthetic_fixture_config("search")

        adapter = self._build_adapter(cfg, fixture=fixture)
        content_id = content_id or self.context_content_id
        rkey = make_request_key(content_id, "search", prompt_version, request_key, query,
                                cfg.id,cfg.adapter_type.value,cfg.base_url,cfg.search_language,
                                ','.join(cfg.search_engines),str(limit))
        if source_url:
            rkey=make_request_key(rkey,source_url)
        def send():
            if hasattr(adapter, "search") and not fixture:
                extra={'source_url':source_url} if source_url and getattr(adapter,'supports_source_url',False) else {}
                return adapter.search(query, limit=limit, request_key=rkey,**extra)
            return adapter.complete(query, system="返回与查询相关的公开资料列表", request_key=rkey,
                json_schema={"properties": {"results": {"type": "array", "items": {
                    "type": "object", "properties": {"url": {"type": "string"},
                    "title": {"type": "string"}, "snippet": {"type": "string"}}}}}})
        return self._execute(cfg, rkey, content_id, prompt_version, fixture, send)

    # ------------------------------------------------------------ 连接测试

    def test_connection(self, cfg: ProviderConfig, *, dry_run: bool = True) -> dict:
        """dry_run=True 只做本地校验，**不发任何网络调用**、不产生 ProviderCall。"""
        missing = self._missing_fields(cfg)
        if missing:
            cfg.last_test_status = ProviderStatus.UNCONFIGURED
            cfg.last_test_message = f"缺少必要配置: {', '.join(missing)}"
            self.store.upsert(cfg)
            return {
                "status": ProviderStatus.UNCONFIGURED.value,
                "missing": missing,
                "called_provider": False,
                "run_mode": RunMode.LOCAL_SEED.value,
                "message": cfg.last_test_message,
            }

        if dry_run:
            cfg.last_test_status = ProviderStatus.CONFIGURED_UNTESTED
            cfg.last_test_message = "本地校验通过（dry_run，未发起网络调用）"
            self.store.upsert(cfg)
            return {
                "status": ProviderStatus.CONFIGURED_UNTESTED.value,
                "called_provider": False,
                "run_mode": RunMode.LOCAL_SEED.value,
                "message": cfg.last_test_message,
            }

        # 真实探测：发一条极短请求。**可能产生实际消耗**。
        adapter = self._build_adapter(cfg, fixture=False)
        res = adapter.test_connection()
        call = self._to_call(res, cfg=cfg, request_key=make_request_key(cfg.id, "conn_test"),
                             prompt_version="__connection_test__")
        cfg.last_test_status = ProviderStatus.AVAILABLE if res.ok else ProviderStatus.ERROR
        if res.ok:
            cfg.last_test_message = "真实连接成功"
        else:
            cls = ADAPTER_REGISTRY.get(cfg.adapter_type)
            if cls is not None and not cls.implemented:
                cfg.last_test_status = ProviderStatus.ERROR
            cfg.last_test_message = f"{res.error_code}: {res.error_message}"
        self.store.upsert(cfg)

        return {
            "status": cfg.last_test_status.value,
            "called_provider": True,
            "run_mode": res.run_mode.value,
            "usage": {
                "input_tokens": res.input_tokens,
                "output_tokens": res.output_tokens,
                "note": "None 表示供应商未回传，不等于 0",
            },
            "remote_request_id": res.remote_request_id,
            "error_code": res.error_code,
            "message": cfg.last_test_message,
            "notice": "本次已发起真实网络调用，可能产生实际消耗",
            "call": call.model_dump(mode="json"),
        }

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _missing_fields(cfg: ProviderConfig) -> list[str]:
        missing = []
        if not cfg.base_url:
            missing.append("base_url")
        if cfg.requires_api_key() and not cfg.secret_ref:
            missing.append("api_key")
        if cfg.kind in {ProviderKind.TEXT,ProviderKind.IMAGE} and not cfg.model_id:
            missing.append("model_id")
        return missing

    def _synthetic_fixture_config(self, kind: str) -> ProviderConfig:
        """fixture 模式下没有真实配置时，构造一个占位配置让链路能跑。

        base_url 用不可路由的保留域名，确保即便误配也不会真的打出去。
        **按 kind 缓存**：每轮新建会得到新的 cfg.id，让 adapter 缓存失效，
        于是"按调用序号注入场景"的离线验证路径永远走不到第二轮。
        """
        cached = self._fixture_cfg_cache.get(kind)
        if cached is not None:
            return cached
        cfg = ProviderConfig(
            name=f"fixture-{kind}",
            kind=ProviderKind.TEXT if kind == "text" else ProviderKind.SEARCH,
            adapter_type=AdapterType.OPENAI_COMPATIBLE,
            base_url="https://fixture.invalid/offline",
            model_id="fixture-deterministic-v1",
            enabled=True,
            last_test_status=ProviderStatus.AVAILABLE,
        )
        self._fixture_cfg_cache[kind] = cfg
        return cfg

    def estimate_micro(self, cfg: ProviderConfig | None, res: AdapterResult) -> int | None:
        """有费率表才算估算；没有就 None（不写 0）。"""
        if not cfg or not cfg.pricing:
            return None
        try:
            in_rate = cfg.pricing.get("input_per_1k_micro")
            out_rate = cfg.pricing.get("output_per_1k_micro")
            if in_rate is None and out_rate is None:
                return None
            if (in_rate is not None and res.input_tokens is None) or (out_rate is not None and res.output_tokens is None):
                return None
            total = 0
            if res.input_tokens is not None and in_rate is not None:
                total += int(round(res.input_tokens / 1000 * in_rate))
            if res.output_tokens is not None and out_rate is not None:
                total += int(round(res.output_tokens / 1000 * out_rate))
            return total
        except Exception:
            return None

    def _to_call(
        self, res: AdapterResult, *, cfg: ProviderConfig, request_key: str,
        job_id=None, content_id=None, revision_id=None, prompt_version=None,
    ) -> ProviderCall:
        est = self.estimate_micro(cfg, res)
        if est is not None:
            billing = BillingState.ESTIMATED
        else:
            billing = BillingState.UNKNOWN
        return ProviderCall(
            job_id=job_id,
            request_key=request_key,
            remote_request_id=res.remote_request_id,
            provider_name=cfg.name,
            model_id=cfg.model_id,
            prompt_version=prompt_version,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            cached_tokens=res.cached_tokens,
            usage_raw=res.usage_raw,
            pricing_version=cfg.pricing_version,
            estimated_micro=est,
            currency=(cfg.pricing or {}).get("currency"),
            billing_state=billing,
            state=res.as_state(),
            run_mode=res.run_mode,
        )

    # ------------------------------------------------------------ 落库

    @staticmethod
    def to_row(call: ProviderCall, *, content_id=None, revision_id=None,
               platform_revision_id=None, provider_config_id=None,
               error_code=None, error_message=None, latency_ms=None) -> ProviderCallRow:
        return ProviderCallRow(
            id=call.id,
            request_key=call.request_key,
            job_id=call.job_id,
            content_id=content_id,
            revision_id=revision_id,
            platform_revision_id=platform_revision_id,
            provider_config_id=provider_config_id,
            remote_request_id=call.remote_request_id,
            provider_name=call.provider_name,
            model_id=call.model_id,
            prompt_version=call.prompt_version,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            cached_tokens=call.cached_tokens,
            usage_raw=call.usage_raw,
            pricing_version=call.pricing_version,
            estimated_micro=call.estimated_micro,
            reported_micro=call.reported_micro,
            reconciled_micro=call.reconciled_micro,
            currency=call.currency,
            billing_state=call.billing_state.value,
            state=call.state,
            run_mode=call.run_mode.value,
            error_code=error_code,
            error_message=error_message,
            latency_ms=latency_ms,
            finished_at=call.finished_at,
        )
