"""适配器基类（P2/T08）。

设计边界（对照 06-execution-clarifications.md 第 2 节）：
- 第一版**只真实现一种明确的文本协议**；协议不同就要新增 adapter，
  不允许"只换 Base URL 就声称已适配"。
- adapter 只负责"发一次请求、拿回结果"，**不做重试**（重试在 P3/T13 实现退避）。
- adapter 不做业务 schema 校验，只尝试 JSON 解析；业务校验在 compose/research 层。
- 拿不到的用量字段一律 None，**绝不填 0**。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

from ..provider_contract import AdapterType, ProviderConfig, RunMode


class Transport(Protocol):
    """最小传输抽象。测试可注入假实现，避免打真实网络。"""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict | None,
        timeout: float,
    ) -> "TransportResponse": ...


@dataclass
class TransportResponse:
    status_code: int
    json_body: dict | None
    headers: dict[str, str] = field(default_factory=dict)
    text: str | None = None


@dataclass
class AdapterResult:
    """一次调用的原始结果。不含任何业务字段解释。"""

    ok: bool
    text: str | None = None
    parsed: dict | None = None

    # 用量：None 表示供应商未回传，不等于 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    usage_raw: dict | None = None

    remote_request_id: str | None = None
    http_status: int | None = None

    # MISSING_KEY / NETWORK_TIMEOUT / RATE_LIMIT / AUTH / SERVER_ERROR /
    # BAD_JSON / EMPTY / ADAPTER_NOT_IMPLEMENTED / UNKNOWN
    error_code: str | None = None
    error_message: str | None = None
    latency_ms: int | None = None

    run_mode: RunMode = RunMode.REAL
    meta: dict = field(default_factory=dict)

    def as_state(self) -> str:
        return "succeeded" if self.ok else "failed"


class BaseAdapter(ABC):
    """所有适配器的基类。"""

    adapter_type: ClassVar[AdapterType]
    run_mode: ClassVar[RunMode] = RunMode.REAL
    #: 是否已在第一版真正实现。False 的适配器调用即明确报错，不假装可用。
    implemented: ClassVar[bool] = True

    def __init__(self, cfg: ProviderConfig, *, api_key: str | None = None,
                 transport: Transport | None = None) -> None:
        self.cfg = cfg
        self.api_key = api_key
        self.transport = transport

    # ------------------------------------------------------------ 生命周期

    def _missing_key(self, what: str = "api_key") -> AdapterResult:
        return AdapterResult(
            ok=False,
            error_code="MISSING_KEY",
            error_message=f"缺少 {what}，未发起任何网络调用",
            run_mode=self.run_mode,
        )

    @abstractmethod
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        json_schema: dict | None = None,
        request_key: str | None = None,
    ) -> AdapterResult:
        """发一次调用并返回原始结果。**不抛异常**，失败通过 AdapterResult 表达。"""

    def test_connection(self) -> AdapterResult:
        """极短探测。默认实现即最小 complete；子类可覆盖为更轻的探针。"""
        return self.complete(
            "ping",
            system="Reply with the single word: pong",
            max_tokens=8,
            request_key="__connection_test__",
        )

    # ------------------------------------------------------------ 工具

    @staticmethod
    def safe_json(text: str | None) -> tuple[dict | None, str | None]:
        """尝试解析 JSON。返回 (parsed, error_code)。"""
        if not text:
            return None, "EMPTY"
        import json

        try:
            obj: Any = json.loads(text)
        except Exception:
            # Some JSON-mode providers occasionally append a quote immediately after
            # a completed array/object (for example: {"items": ["a"]", "next": ...}).
            # Drop only that impossible quote, and only when outside a JSON string;
            # leave all other malformed output visible to the caller as BAD_JSON.
            repaired = _drop_stray_container_quotes(text)
            if repaired == text:
                repaired = text
            else:
                try:
                    obj = json.loads(repaired)
                    return (obj if isinstance(obj, dict) else None), (None if isinstance(obj, dict) else "BAD_JSON")
                except Exception:
                    pass

            # Some JSON-mode models return a Python-literal representation for
            # nested arrays/objects (single-quoted strings) while keeping JSON
            # booleans/null. Accept only literal syntax: no names, calls, or
            # executable expressions. Business schemas still validate the
            # resulting dict at the call site.
            try:
                import ast

                candidate = _json_literals_to_python(repaired)
                obj = ast.literal_eval(candidate)
            except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
                return None, "BAD_JSON"
        return (obj if isinstance(obj, dict) else None), (None if isinstance(obj, dict) else "BAD_JSON")


def _drop_stray_container_quotes(text: str) -> str:
    """Remove an extra quote after a closed JSON container, without touching strings."""
    chars: list[str] = []
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            chars.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            previous = next((c for c in reversed(chars) if not c.isspace()), "")
            following = next((c for c in text[index + 1:] if not c.isspace()), "")
            if previous in "]}" and following in ",}]":
                continue
            in_string = True
        chars.append(char)
    return "".join(chars)


def _json_literals_to_python(text: str) -> str:
    """Translate JSON literals outside strings before safe literal parsing."""
    output: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    replacements = {"true": "True", "false": "False", "null": "None"}
    while index < len(text):
        char = text[index]
        if quote:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            output.append(char)
            index += 1
            continue
        replacement = next((value for token, value in replacements.items()
                            if text.startswith(token, index)
                            and (index == 0 or not (text[index - 1].isalnum() or text[index - 1] == "_"))
                            and (index + len(token) == len(text) or not (text[index + len(token)].isalnum() or text[index + len(token)] == "_"))), None)
        if replacement:
            token = next(token for token, value in replacements.items() if value == replacement)
            output.append(replacement)
            index += len(token)
            continue
        output.append(char)
        index += 1
    return "".join(output)
