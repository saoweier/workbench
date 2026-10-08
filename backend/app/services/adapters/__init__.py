"""适配器注册表（P2/T08）。

新增协议 = 新增一个 adapter 类并在此登记。**不允许**通过改 base_url
把一种协议当另一种用。
"""
from __future__ import annotations

from ..provider_contract import AdapterType
from .anthropic_messages import AnthropicMessagesAdapter
from .base import AdapterResult, BaseAdapter, Transport, TransportResponse
from .fixture import FixtureAdapter
from .openai_compatible import OpenAICompatibleAdapter
from .search_http_json import SearchHttpJsonAdapter
from .searxng import SearXNGAdapter
from .openai_images import OpenAIImagesAdapter

ADAPTER_REGISTRY: dict[AdapterType, type[BaseAdapter]] = {
    AdapterType.OPENAI_COMPATIBLE: OpenAICompatibleAdapter,
    AdapterType.SEARCH_HTTP_JSON: SearchHttpJsonAdapter,
    AdapterType.SEARXNG: SearXNGAdapter,
    AdapterType.ANTHROPIC_MESSAGES: AnthropicMessagesAdapter,
    AdapterType.OPENAI_IMAGES: OpenAIImagesAdapter,
}

#: 第一版**真正实现**的协议。其余只在枚举里占位，调用即明确报错。
IMPLEMENTED_ADAPTERS = frozenset(
    at for at, cls in ADAPTER_REGISTRY.items() if cls.implemented
)

#: fixture 用的确定性适配器（不属于任何真实协议，仅供离线验证）
FIXTURE_ADAPTER = FixtureAdapter

__all__ = [
    "ADAPTER_REGISTRY",
    "IMPLEMENTED_ADAPTERS",
    "FIXTURE_ADAPTER",
    "AdapterResult",
    "BaseAdapter",
    "Transport",
    "TransportResponse",
    "FixtureAdapter",
    "OpenAICompatibleAdapter",
    "SearchHttpJsonAdapter",
    "AnthropicMessagesAdapter",
]
