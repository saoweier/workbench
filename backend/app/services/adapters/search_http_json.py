"""HTTP JSON 搜索适配器（P2/T09）。

约定：POST base_url，请求体 {"query":..., "limit":...}，
期望响应 {"results":[{"url","title","snippet","published_at"?}, ...]}。

**无 url 的条目直接丢弃并记 error_message**——搜索摘要不能失去溯源。

注意：搜索结果的 snippet 属于 `search_snippet` 依据，
按文档 02 §M02「搜索摘要不能当事实依据」，它不能支撑 fact 类主张。
这条规则由 claim_rules 强制，不在这里。
"""
from __future__ import annotations

import time
from typing import ClassVar

from ..provider_contract import AdapterType, RunMode
from .base import AdapterResult, BaseAdapter, TransportResponse


class SearchHttpJsonAdapter(BaseAdapter):
    adapter_type: ClassVar[AdapterType] = AdapterType.SEARCH_HTTP_JSON
    run_mode: ClassVar[RunMode] = RunMode.REAL
    implemented: ClassVar[bool] = True

    def complete(self, prompt, *, system=None, max_tokens=None,
                 json_schema=None, request_key=None) -> AdapterResult:
        return self.search(prompt, limit=5, request_key=request_key)

    def search(self, query: str, *, limit: int = 5,
               request_key: str | None = None) -> AdapterResult:
        if not self.api_key:
            return self._missing_key()
        if self.transport is None:
            return AdapterResult(ok=False, error_code="UNKNOWN",
                                 error_message="未注入 transport", run_mode=RunMode.REAL)

        started = time.monotonic()
        try:
            resp = self.transport.request(
                "POST", self.cfg.base_url.rstrip("/"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json_body={"query": query, "limit": limit},
                timeout=float(self.cfg.timeout_seconds),
            )
        except TimeoutError:
            return AdapterResult(ok=False, error_code="NETWORK_TIMEOUT", error_message="搜索超时",
                                 run_mode=RunMode.REAL,
                                 latency_ms=int((time.monotonic() - started) * 1000))
        except Exception as exc:
            return AdapterResult(ok=False, error_code="NETWORK_TIMEOUT",
                                 error_message=f"网络异常：{exc}", run_mode=RunMode.REAL,
                                 latency_ms=int((time.monotonic() - started) * 1000))

        latency = int((time.monotonic() - started) * 1000)
        return self._interpret(resp, latency)

    def _interpret(self, resp: TransportResponse, latency: int) -> AdapterResult:
        if resp.status_code in (401, 403):
            return AdapterResult(ok=False, error_code="AUTH", http_status=resp.status_code,
                                 error_message="搜索鉴权失败", run_mode=RunMode.REAL, latency_ms=latency)
        if resp.status_code == 429:
            return AdapterResult(ok=False, error_code="RATE_LIMIT", http_status=resp.status_code,
                                 error_message="搜索限流", run_mode=RunMode.REAL, latency_ms=latency)
        if resp.status_code >= 400:
            return AdapterResult(ok=False, error_code="SERVER_ERROR", http_status=resp.status_code,
                                 error_message=f"搜索请求被拒：HTTP {resp.status_code}",
                                 run_mode=RunMode.REAL, latency_ms=latency)

        body = resp.json_body or {}
        raw = body.get("results")
        if not isinstance(raw, list):
            return AdapterResult(ok=False, error_code="BAD_JSON", http_status=resp.status_code,
                                 error_message="响应缺少 results 列表",
                                 run_mode=RunMode.REAL, latency_ms=latency)

        kept, dropped = [], []
        for item in raw:
            if not isinstance(item, dict) or not item.get("url"):
                dropped.append(item)
                continue
            kept.append({
                "url": item["url"],
                "title": item.get("title"),
                "snippet": item.get("snippet"),
                "published_at": item.get("published_at"),
            })

        warn = None
        if dropped:
            warn = f"丢弃 {len(dropped)} 条缺少 url 的搜索结果（无法溯源）"

        return AdapterResult(
            ok=True,
            text=None,
            parsed={"results": kept},
            input_tokens=None, output_tokens=None,
            usage_raw=body.get("usage"),
            remote_request_id=resp.headers.get("x-request-id") or body.get("id"),
            http_status=resp.status_code,
            error_message=warn,
            latency_ms=latency,
            run_mode=RunMode.REAL,
            meta={"dropped_count": len(dropped)},
        )
