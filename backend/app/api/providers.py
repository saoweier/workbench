"""Provider 设置接口（T03 配置契约 + T08 真实连接测试）。

关键约束：
- 密钥写入后只返回掩码，任何接口都不回显全值。
- 保存配置**不触发**任何真实调用。
- 「测试连接」默认 dry_run：只做本地校验，不发网络。
  `dry_run=false` 才发起一次有明确范围的探测，并在响应里提示可能产生实际消耗。
- 没有配置时返回明确 unconfigured，不报 500。
"""
from __future__ import annotations

from fastapi import APIRouter, Body
from pydantic import BaseModel, Field

from ..core.config import get_settings
from ..core.errors import NotFound,ValidationFailed
from ..services.provider_contract import (
    ProviderConfig,
    ProviderConfigIn,
    ProviderKind,
    ProviderStatus,
    ProviderStore,
    RunMode,
    SecretStore,
)
from ..services.provider_runtime import ProviderRuntime

router = APIRouter(tags=["providers"])
_settings = get_settings()
_store = ProviderStore(_settings.storage_root / "provider_configs.json")
_secrets = SecretStore(_settings.secret_store_path)
_runtime = ProviderRuntime(_store, _secrets)


def _persist(cfg: ProviderConfig) -> ProviderConfig:
    return _store.upsert(cfg)


@router.get("/provider-configs")
async def list_provider_configs(kind: ProviderKind | None = None) -> dict:
    items = _store.list(kind)
    text_default = _store.default_for(ProviderKind.TEXT)
    search_default = _store.default_for(ProviderKind.SEARCH)
    image_default = _runtime.image_provider()
    return {
        "items": [c.public_view() for c in items],
        "defaults": {
            "text": text_default.public_view() if text_default else None,
            "search": search_default.public_view() if search_default else None,
            "image": image_default.public_view() if image_default else None,
        },
        "capabilities": {
            "image": {"has_config":image_default is not None,"note":"图片模型单独配置，也可上传自有图片；普通文字接口不能生成照片"},
            "text": {"has_config": text_default is not None,
                     "note": "无配置时可用 local_seed 与本仓库 fixture 继续开发"},
            "search": {"has_config": search_default is not None,
                       "note": "热榜优先读取原链接；补充调研使用配置的搜索服务，推荐SearXNG。未配置时不执行补充搜索，界面须标注未执行自动搜索"},
        },
        "cost_mode": _settings.cost_mode_default,
        "money_limit_set": _settings.batch_money_limit_default is not None,
    }


@router.post("/provider-configs", status_code=201)
async def create_provider_config(payload: ProviderConfigIn) -> dict:
    secret_ref = _secrets.put(payload.api_key) if payload.api_key else None
    cfg = ProviderConfig(
        name=payload.name,
        kind=payload.kind,
        adapter_type=payload.adapter_type,
        base_url=payload.base_url,
        secret_ref=secret_ref,
        model_id=payload.model_id,
        timeout_seconds=payload.timeout_seconds,
        max_output_tokens=payload.max_output_tokens,
        thinking_mode=payload.thinking_mode,
        enabled=payload.enabled,
        allow_localhost=payload.allow_localhost,
        search_language=payload.search_language,
        search_engines=payload.search_engines,
        pricing_version=payload.pricing_version,
        pricing=payload.pricing,
        last_test_status=ProviderStatus.CONFIGURED_UNTESTED if secret_ref or payload.adapter_type.value == 'searxng' else ProviderStatus.UNCONFIGURED,
    )
    cfg = _persist(cfg)
    return {
        "item": cfg.public_view(),
        "called_provider": False,
        "note": "配置已保存，未发起任何真实调用",
    }


@router.patch("/provider-configs/{config_id}")
async def update_provider_config(config_id: str, payload: dict = Body(...)) -> dict:
    cfg = _store.get(config_id)
    if not cfg:
        raise NotFound(f"Provider 配置不存在: {config_id}")

    allowed = {
        "name", "base_url", "model_id", "timeout_seconds", "max_output_tokens", "thinking_mode",
        "enabled", "allow_localhost", "adapter_type", "pricing_version", "pricing",
        "search_language", "search_engines",
    }
    try:cfg = ProviderConfig.model_validate({**cfg.model_dump(), **{k:v for k,v in payload.items() if k in allowed}})
    except ValueError:raise ValidationFailed('模型配置字段无效，请检查思考模式、输出上限与超时设置。')

    if "api_key" in payload:
        if cfg.secret_ref:
            _secrets.delete(cfg.secret_ref)
        raw = payload.pop("api_key")
        cfg.secret_ref = _secrets.put(raw) if raw else None
        cfg.last_test_status = (
            ProviderStatus.CONFIGURED_UNTESTED if cfg.secret_ref else ProviderStatus.UNCONFIGURED
        )

    allowed = {
        "name", "base_url", "model_id", "timeout_seconds", "max_output_tokens", "thinking_mode",
        "enabled", "allow_localhost", "adapter_type", "pricing_version", "pricing",
        "search_language", "search_engines",
    }
    cfg = _store.upsert(cfg)
    return {
        "item": cfg.public_view(),
        "called_provider": False,
        "note": "配置已更新，未发起任何真实调用",
    }


@router.delete("/provider-configs/{config_id}")
async def delete_provider_config(config_id: str) -> dict:
    cfg = _store.get(config_id)
    if not cfg:
        raise NotFound(f"Provider 配置不存在: {config_id}")
    _secrets.delete(cfg.secret_ref)
    _store.delete(config_id)
    return {"deleted": True, "id": config_id}


@router.post("/provider-configs/{config_id}/test")
async def test_provider_config(config_id: str, dry_run: bool = True) -> dict:
    """连接测试。

    - dry_run=true（默认）：只做本地校验，**不发起任何网络调用**、不产生费用。
    - dry_run=false：真实探测一次，**会产生实际消耗**，响应里明确标注。
    """
    cfg = _store.get(config_id)
    if not cfg:
        raise NotFound(f"Provider 配置不存在: {config_id}")

    result = _runtime.test_connection(cfg, dry_run=dry_run)
    if not result.get("called_provider"):
        result.setdefault("run_mode", RunMode.LOCAL_SEED.value)
    return result


class SearchProbeIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    read_articles: bool = True


@router.post('/provider-configs/{config_id}/search-probe')
def search_probe(config_id: str, payload: SearchProbeIn) -> dict:
    """Exercise the selected search protocol and article reader, without an LLM call."""
    cfg = _store.get(config_id)
    if not cfg or cfg.kind != ProviderKind.SEARCH:
        raise NotFound('没有找到该搜索配置')
    missing = _runtime._missing_fields(cfg)
    if missing:
        raise ValidationFailed('缺少搜索配置：' + ', '.join(missing))
    adapter = _runtime._build_adapter(cfg, fixture=False)
    if not hasattr(adapter, 'search'):
        raise ValidationFailed('该接口不支持资料搜索')
    result = adapter.search(payload.query.strip(), limit=8)
    from datetime import datetime, timezone
    cfg.last_test_status = ProviderStatus.AVAILABLE if result.ok else ProviderStatus.ERROR
    cfg.last_test_at = datetime.now(timezone.utc)
    cfg.last_test_message = '实际搜索成功' if result.ok else result.error_message
    _store.upsert(cfg)
    output = {'ok':result.ok, 'query':payload.query.strip(), 'provider':cfg.name,
              'adapter_type':cfg.adapter_type.value, 'message':result.error_message,
              'meta':result.meta, 'results':[], 'called_text_model':False}
    from ..services.source_reader import read_source_detail
    for index, hit in enumerate((result.parsed or {}).get('results', [])):
        item = {**hit, 'body_state':'not_read', 'body_chars':0}
        if payload.read_articles and index < 3:
            try:
                cached=hit.get('read')
                if cached is not None:
                    if cached.get('state')!='readable' or not str(cached.get('body') or '').strip():raise ValueError(cached.get('message') or '独立查询器未取得此页正文')
                    body,sha,final_url=cached['body'],cached.get('sha256'),cached.get('final_url') or hit['url']
                else:body, sha, final_url = read_source_detail(hit['url'])
                item.update(body_state='readable',body_chars=len(body),body_hash=sha,
                            final_url=final_url,body_preview=body[:700])
            except Exception as exc:
                item.update(body_state='failed',message=str(exc)[:300])
        output['results'].append(item)
    output['readable_count'] = sum(x['body_state']=='readable' for x in output['results'])
    return output
