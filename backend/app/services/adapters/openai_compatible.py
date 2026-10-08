"""OpenAI 兼容协议的文本适配器（P2/T08）。

这是第一版**唯一真正实现**的文本协议。协议不同（例如 Anthropic Messages）
必须另写 adapter，不允许仅改 Base URL 就沿用本类。
"""
from __future__ import annotations

import time
import json
from typing import ClassVar
from urllib.parse import urlparse

from ..provider_contract import AdapterType, RunMode
from .base import AdapterResult, BaseAdapter, Transport, TransportResponse


class OpenAICompatibleAdapter(BaseAdapter):
    adapter_type: ClassVar[AdapterType] = AdapterType.OPENAI_COMPATIBLE
    run_mode: ClassVar[RunMode] = RunMode.REAL
    implemented: ClassVar[bool] = True

    def effective_thinking_mode(self):
        if self.cfg.thinking_mode != 'auto':return self.cfg.thinking_mode
        # Official DeepSeek structured drafts have a bounded output budget.
        if urlparse(self.cfg.base_url).hostname=='api.deepseek.com' and self.cfg.model_id in {'deepseek-flash','deepseek-v4-flash','deepseek-v4-pro'}:return 'disabled'
        return None

    def _endpoint(self) -> str:
        return f"{self.cfg.base_url.rstrip('/')}/chat/completions"

    def complete(self, prompt, *, system=None, max_tokens=None,
                 json_schema=None, request_key=None) -> AdapterResult:
        if not self.api_key:
            return self._missing_key()
        if self.transport is None:
            return AdapterResult(
                ok=False, error_code="UNKNOWN",
                error_message="未注入 transport，无法发起调用（测试应注入假传输）",
                run_mode=RunMode.REAL,
            )

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if json_schema is not None:
            prompt += "\n输出必须符合以下 JSON Schema，且仅输出 JSON：\n" + json.dumps(json_schema, ensure_ascii=False)
        messages.append({"role": "user", "content": prompt})

        body: dict = {
            "model": self.cfg.model_id,
            "messages": messages,
        }
        thinking=self.effective_thinking_mode()
        if thinking:
            body['thinking']={'type':'enabled' if thinking=='enabled_low' else thinking}
            if thinking=='enabled_low':body['reasoning_effort']='low'
        if max_tokens or self.cfg.max_output_tokens:
            body["max_tokens"] = max_tokens or self.cfg.max_output_tokens
        if json_schema is not None:
            # 首选结构化输出；部分兼容端不支持时由调用方降级为 prompt 内嵌 schema
            body["response_format"] = {"type": "json_object"}

        started = time.monotonic()
        try:
            resp = self.transport.request(
                "POST", self._endpoint(),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json_body=body,
                timeout=float(self.cfg.timeout_seconds),
            )
        except TimeoutError:
            return AdapterResult(ok=False, error_code="NETWORK_TIMEOUT",
                                 error_message="请求超时", run_mode=RunMode.REAL,
                                 latency_ms=int((time.monotonic() - started) * 1000))
        except Exception as exc:  # 网络类异常统一收敛
            return AdapterResult(ok=False, error_code="NETWORK_TIMEOUT",
                                 error_message=f"网络异常：{exc}", run_mode=RunMode.REAL,
                                 latency_ms=int((time.monotonic() - started) * 1000))

        latency = int((time.monotonic() - started) * 1000)
        return self._interpret(resp, latency)

    def _interpret(self, resp: TransportResponse, latency: int) -> AdapterResult:
        if resp.status_code in (401, 403):
            return AdapterResult(ok=False, error_code="AUTH", http_status=resp.status_code,
                                 error_message="鉴权失败：检查 api_key", run_mode=RunMode.REAL,
                                 latency_ms=latency)
        if resp.status_code == 429:
            return AdapterResult(ok=False, error_code="RATE_LIMIT", http_status=resp.status_code,
                                 error_message="触发限流", run_mode=RunMode.REAL, latency_ms=latency)
        if resp.status_code >= 500:
            return AdapterResult(ok=False, error_code="SERVER_ERROR", http_status=resp.status_code,
                                 error_message="供应商侧错误", run_mode=RunMode.REAL, latency_ms=latency)
        if resp.status_code >= 400:
            return AdapterResult(ok=False, error_code="SERVER_ERROR", http_status=resp.status_code,
                                 error_message=f"请求被拒：HTTP {resp.status_code}",
                                 run_mode=RunMode.REAL, latency_ms=latency)

        body = resp.json_body or {}
        text = None
        try:
            text = body["choices"][0]["message"]["content"]
        except Exception:
            return AdapterResult(ok=False, error_code="BAD_JSON", http_status=resp.status_code,
                                 error_message="响应缺少 choices[0].message.content",
                                 run_mode=RunMode.REAL, latency_ms=latency)

        # usage：缺失就 None，绝不补 0
        usage = body.get("usage") or {}
        in_tok = usage.get("prompt_tokens", usage.get("input_tokens"))
        out_tok = usage.get("completion_tokens", usage.get("output_tokens"))
        cached = None
        details = usage.get("prompt_tokens_details") or {}
        if isinstance(details, dict):
            cached = details.get("cached_tokens")

        parsed, _err = self.safe_json(text) if isinstance(text, str) else (None, None)
        request_id = (
            resp.headers.get("x-request-id")
            or body.get("id")
            or resp.headers.get("request-id")
        )
        finish_reason=body['choices'][0].get('finish_reason')
        empty=not isinstance(text,str) or not text.strip()
        limited=finish_reason=='length'
        return AdapterResult(
            ok=not empty and not limited, text=text, parsed=parsed,
            error_code='OUTPUT_LIMIT' if limited else 'EMPTY' if empty else None,
            error_message='输出额度用尽，未得到完整正文。请在API设置关闭思考模式或增大输出上限；未自动重发。' if limited else '模型返回了空正文；请检查思考模式和模型输出额度，未自动重发。' if empty else None,
            meta={'finish_reason':finish_reason},
            input_tokens=in_tok, output_tokens=out_tok, cached_tokens=cached,
            usage_raw=usage or None,
            remote_request_id=request_id,
            http_status=resp.status_code,
            latency_ms=latency,
            run_mode=RunMode.REAL,
        )
