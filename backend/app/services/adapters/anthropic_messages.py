"""Anthropic Messages 协议适配器 —— **第一版不实现，仅占位**。

为什么不实现（对照 06-execution-clarifications.md 第 2 节）：

> 第一版完成一种明确的文本协议适配器；其余端点如协议不同，需要新增 adapter，
> 不靠更换 Base URL 假装已经适配。

Anthropic 的 /v1/messages 与 OpenAI 的 /v1/chat/completions 在请求体、
响应结构、鉴权头上都不同。如果只把 base_url 换成本类就声称"已适配"，
会在真实调用时静默产出错误结果——这比明确报错更糟。

因此本类：
- `implemented = False`
- 任何调用都返回 `ADAPTER_NOT_IMPLEMENTED`，并给出明确指引
- 在 Enum 中保留位置，使设置页可选，但选择后立即得到可理解的反馈
"""
from __future__ import annotations

from typing import ClassVar

from ..provider_contract import AdapterType, RunMode
from .base import AdapterResult, BaseAdapter

_GUIDE = (
    "Anthropic Messages 协议尚未适配（P2 只实现 OpenAI 兼容协议）。"
    "请勿通过改 base_url 复用其他适配器——协议不同，需要新增 adapter 实现后再启用。"
)


class AnthropicMessagesAdapter(BaseAdapter):
    adapter_type: ClassVar[AdapterType] = AdapterType.ANTHROPIC_MESSAGES
    run_mode: ClassVar[RunMode] = RunMode.REAL
    implemented: ClassVar[bool] = False

    def complete(self, prompt, *, system=None, max_tokens=None,
                 json_schema=None, request_key=None) -> AdapterResult:
        return AdapterResult(
            ok=False,
            error_code="ADAPTER_NOT_IMPLEMENTED",
            error_message=_GUIDE,
            run_mode=RunMode.REAL,
        )
