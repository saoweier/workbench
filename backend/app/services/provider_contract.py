"""Provider 配置契约与用量追踪（T03）。

设计要点（对照 06-execution-clarifications.md）：
- 提供方与模型**不写死**，全部来自设置页；密钥只存引用 ID，不回显全值。
- 状态机：unconfigured / configured_untested / available / error。
  fixture 与 local_seed 是**运行模式**，不是 Provider 可用状态。
- 保存配置**不自动调用**；只有「测试连接」与「开始生成」会发起有限调用。
- 成本：默认 usage_tracking，金额上限可为 None（未设置）。
  金额未知**不填 0**；估算结果单独标注，不冒充实际扣费。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# ------------------------------------------------------------------ 枚举


class ProviderKind(str, Enum):
    TEXT = "text"          # 文字模型
    SEARCH = "search"      # 搜索/检索
    SPEECH = 'speech'
    IMAGE = 'image'


class AdapterType(str, Enum):
    """第一版只实现一种明确的文本协议；其余协议需要新增 adapter，
    不允许仅换 Base URL 就假装已适配。"""

    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    SEARCH_HTTP_JSON = "search_http_json"
    SEARXNG = "searxng"
    OPENAI_SPEECH = 'openai_speech'
    OPENAI_IMAGES = 'openai_images'


class ProviderStatus(str, Enum):
    UNCONFIGURED = "unconfigured"
    CONFIGURED_UNTESTED = "configured_untested"
    AVAILABLE = "available"
    ERROR = "error"


class BillingState(str, Enum):
    """计费状态。unknown 表示拿不到金额，绝不当成 0。"""

    UNKNOWN = "unknown"
    ESTIMATED = "estimated"
    PROVIDER_REPORTED = "provider_reported"
    RECONCILED = "reconciled"


class RunMode(str, Enum):
    REAL = "real"
    FIXTURE = "fixture"
    LOCAL_SEED = "local_seed"


class CostMode(str, Enum):
    USAGE_TRACKING = "usage_tracking"
    HARD_CAP = "hard_cap"


# ------------------------------------------------------------------ 配置模型


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProviderConfig(BaseModel):
    """设置页保存的一条 Provider 配置。密钥本体不入此模型。"""

    id: str = Field(default_factory=lambda: f"pc-{uuid.uuid4().hex[:12]}")
    name: str
    kind: ProviderKind
    adapter_type: AdapterType
    base_url: str
    secret_ref: str | None = None      # 指向密钥存储的键，不是密钥
    model_id: str | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=1800)
    max_output_tokens: int | None = Field(default=None, ge=1)
    thinking_mode: Literal["auto", "disabled", "enabled"] = "auto"
    enabled: bool = False
    allow_localhost: bool = False       # 允许用户自己的本机服务
    search_language: str = "auto"
    search_engines: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    last_test_status: ProviderStatus = ProviderStatus.UNCONFIGURED
    last_test_at: datetime | None = None
    last_test_message: str | None = None
    # 费率表可选。没有费率不影响连接与调用，只影响能否给出估算金额。
    pricing_version: str | None = None
    pricing: dict | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("base_url")
    @classmethod
    def _url_shape(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        return v.rstrip("/")

    def requires_api_key(self) -> bool:
        return self.adapter_type != AdapterType.SEARXNG

    def status(self) -> ProviderStatus:
        """派生状态：缺必要项即 unconfigured，与历史测试结果无关。"""
        if not self.base_url or (self.requires_api_key() and not self.secret_ref) or not self.enabled:
            return ProviderStatus.UNCONFIGURED
        if self.kind in {ProviderKind.TEXT,ProviderKind.SPEECH,ProviderKind.IMAGE} and not self.model_id:
            return ProviderStatus.UNCONFIGURED
        return self.last_test_status

    def public_view(self) -> dict:
        """对外响应：只有掩码与是否已设置，绝不回显密钥。"""
        d = self.model_dump(mode="json")
        d.pop("secret_ref", None)
        d["secret_configured"] = bool(self.secret_ref)
        d["requires_api_key"] = self.requires_api_key()
        d["secret_mask"] = "••••••" if self.secret_ref else None
        d["status"] = self.status().value
        d["kind"] = self.kind.value
        d["adapter_type"] = self.adapter_type.value
        return d


class ProviderConfigIn(BaseModel):
    name: str
    kind: ProviderKind
    adapter_type: AdapterType
    base_url: str
    model_id: str | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=1800)
    max_output_tokens: int | None = Field(default=None, ge=1)
    thinking_mode: Literal["auto", "disabled", "enabled"] = "auto"
    enabled: bool = False
    allow_localhost: bool = False
    search_language: str = "auto"
    search_engines: list[str] = Field(default_factory=list)
    api_key: str | None = None          # 仅写入，永不回读
    pricing_version: str | None = None
    pricing: dict | None = None


# ------------------------------------------------------------------ 用量追踪


class ProviderCall(BaseModel):
    """一次 Provider 调用的记账记录。用量与费用分开。"""

    id: str = Field(default_factory=lambda: f"call-{uuid.uuid4().hex[:12]}")
    job_id: str | None = None
    request_key: str | None = None
    remote_request_id: str | None = None
    provider_name: str | None = None
    model_id: str | None = None
    prompt_version: str | None = None

    # 用量：拿不到就不填，None ≠ 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    usage_raw: dict | None = None

    # 费用：整数微货币单位，禁止浮点累计
    pricing_version: str | None = None
    estimated_micro: int | None = None
    reported_micro: int | None = None
    reconciled_micro: int | None = None
    currency: str | None = None
    billing_state: BillingState = BillingState.UNKNOWN

    state: Literal["succeeded", "failed", "unknown"] = "unknown"
    run_mode: RunMode = RunMode.REAL
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None
    retry_of: str | None = None

    def effective_micro(self) -> tuple[int | None, BillingState]:
        """最高可信度的金额及其来源。缺口不补 0。"""
        if self.reconciled_micro is not None:
            return self.reconciled_micro, BillingState.RECONCILED
        if self.reported_micro is not None:
            return self.reported_micro, BillingState.PROVIDER_REPORTED
        if self.estimated_micro is not None:
            return self.estimated_micro, BillingState.ESTIMATED
        return None, BillingState.UNKNOWN


class UsageSummary(BaseModel):
    """汇总。不同币种不直接相加；部分未知时如实标注笔数。"""

    call_count: int = 0
    succeeded: int = 0
    failed: int = 0
    unknown_result: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_by_currency: dict[str, int] = Field(default_factory=dict)
    cost_state_by_currency: dict[str, BillingState] = Field(default_factory=dict)
    unpriced_call_count: int = 0
    fixture_call_count: int = 0

    def display_cost(self, currency: str = "CNY") -> str:
        amt = self.cost_by_currency.get(currency)
        st = self.cost_state_by_currency.get(currency)
        if amt is None:
            return f"金额未知（{self.unpriced_call_count} 笔待核实）"
        label = {
            BillingState.ESTIMATED: "估算",
            BillingState.PROVIDER_REPORTED: "供应商回传",
            BillingState.RECONCILED: "已对账",
        }.get(st, "未知来源")
        tail = f"，另有 {self.unpriced_call_count} 笔待核实" if self.unpriced_call_count else ""
        return f"{amt / 1_000_000:.4f} {currency}（{label}{tail}）"


def summarize_calls(calls: list[ProviderCall], *, include_fixture: bool = False) -> UsageSummary:
    s = UsageSummary()
    for c in calls:
        if c.run_mode != RunMode.REAL and not include_fixture:
            s.fixture_call_count += 1
            continue
        s.call_count += 1
        if c.state == "succeeded":
            s.succeeded += 1
        elif c.state == "failed":
            s.failed += 1
        else:
            s.unknown_result += 1
        if c.input_tokens is not None:
            s.input_tokens = (s.input_tokens or 0) + c.input_tokens
        if c.output_tokens is not None:
            s.output_tokens = (s.output_tokens or 0) + c.output_tokens

        micro, state = c.effective_micro()
        cur = c.currency or "CNY"
        if micro is None:
            s.unpriced_call_count += 1
        else:
            s.cost_by_currency[cur] = s.cost_by_currency.get(cur, 0) + micro
            prev = s.cost_state_by_currency.get(cur)
            # 取最低可信度作为整体标注，避免把估算冒充实际
            rank = {
                BillingState.ESTIMATED: 0,
                BillingState.PROVIDER_REPORTED: 1,
                BillingState.RECONCILED: 2,
            }
            if prev is None or rank.get(state, 0) < rank.get(prev, 0):
                s.cost_state_by_currency[cur] = state
    return s


class BudgetPolicy(BaseModel):
    """批次成本策略。金额上限可为 None，表示未设置，不是 0 额度。"""

    cost_mode: CostMode = CostMode.USAGE_TRACKING
    batch_money_limit_micro: int | None = None
    daily_money_limit_micro: int | None = None
    currency: str = "CNY"
    # 非金额约束始终生效，与是否设置金额上限无关
    item_limit: int = 1
    pending_review_stock_limit: int = 3
    max_repair_rounds: int = 2
    max_calls_per_run: int = 40

    @field_validator("batch_money_limit_micro", "daily_money_limit_micro")
    @classmethod
    def _non_negative(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("金额上限不能为负数；未设置请传 null")
        return v

    def can_call(self, summary: UsageSummary, *, estimated_next_micro: int | None) -> tuple[bool, str]:
        if self.cost_mode == CostMode.USAGE_TRACKING:
            return True, "usage_tracking 模式：仅记录实际消耗，不因金额数字阻塞"
        # hard_cap 模式才校验金额
        if self.batch_money_limit_micro is None:
            return False, "hard_cap 模式必须提供 batch_money_limit；未设置金额上限不能启用硬上限"
        if estimated_next_micro is None:
            return False, "hard_cap 模式下无法估价，暂停付费调用（不影响 usage_tracking 模式）"
        used = summary.cost_by_currency.get(self.currency, 0)
        if used + estimated_next_micro > self.batch_money_limit_micro:
            return False, f"超出批次上限：已用 {used} + 预计 {estimated_next_micro} > {self.batch_money_limit_micro}"
        return True, "在上限内"


# ------------------------------------------------------------------ 存储


class SecretStore:
    """密钥存储。P0 用本地文件 + 0600 权限；P1 换系统钥匙串或加密存储。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: dict[str, str] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def put(self, value: str) -> str:
        self._reload()
        ref = f"secret-{uuid.uuid4().hex[:12]}"
        self._data[ref] = value
        self._flush()
        return ref

    def get(self, ref: str | None) -> str | None:
        self._reload()
        return self._data.get(ref) if ref else None

    def delete(self, ref: str | None) -> None:
        self._reload()
        if ref and ref in self._data:
            del self._data[ref]
            self._flush()

    def _reload(self) -> None:
        self._data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self._data), encoding="utf-8")
        temp.replace(self.path)
        self.path.chmod(0o600)


class ProviderStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._items: dict[str, ProviderConfig] = {}
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for item in raw:
                pc = ProviderConfig.model_validate(item)
                self._items[pc.id] = pc

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps([c.model_dump(mode="json") for c in self._items.values()], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(self.path)

    def _reload(self) -> None:
        raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
        self._items = {c.id: c for c in map(ProviderConfig.model_validate, raw)}

    def list(self, kind: ProviderKind | None = None) -> list[ProviderConfig]:
        self._reload()
        vals = list(self._items.values())
        if kind:
            vals = [c for c in vals if c.kind == kind]
        return sorted(vals, key=lambda c: c.created_at)

    def get(self, cid: str) -> ProviderConfig | None:
        self._reload()
        return self._items.get(cid)

    def upsert(self, cfg: ProviderConfig) -> ProviderConfig:
        self._reload()
        cfg.updated_at = _now()
        self._items[cfg.id] = cfg
        self._flush()
        return cfg

    def delete(self, cid: str) -> bool:
        self._reload()
        if cid in self._items:
            del self._items[cid]
            self._flush()
            return True
        return False

    def default_for(self, kind: ProviderKind) -> ProviderConfig | None:
        """返回该类别下第一个可用/未测但完整配置的条目。"""
        cands = [c for c in self.list(kind) if c.enabled and c.status() != ProviderStatus.UNCONFIGURED]
        return cands[0] if cands else None
