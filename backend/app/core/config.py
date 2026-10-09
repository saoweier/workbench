"""应用级配置。所有可调项走环境变量，不写死厂商与模型。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CWB_", env_file=".env", extra="ignore")

    app_name: str = "内容工作台"
    app_version: str = "1.4.2"
    api_prefix: str = "/api/v1"

    storage_root: Path = Field(default=PROJECT_ROOT / "storage")
    artifact_dir: Path = Field(default=PROJECT_ROOT / "storage" / "artifacts")
    tmp_dir: Path = Field(default=PROJECT_ROOT / "storage" / "tmp")
    examples_dir: Path = Field(default=PROJECT_ROOT / "examples")

    database_url: str = Field(default=f"sqlite:///{PROJECT_ROOT / 'storage' / 'cwb.db'}")

    # 成本模型默认：实际消耗追踪，金额上限为 null（未设置），不是 0。
    cost_mode_default: str = "usage_tracking"
    batch_money_limit_default: int | None = None
    daily_money_limit_default: int | None = None
    currency_default: str = "CNY"

    # 未设置金额上限时的非金额约束
    default_batch_item_limit: int = 1
    pending_review_stock_limit: int = 3
    max_repair_rounds: int = 2

    # 密钥保存位置（不落库明文）
    secret_store_path: Path = Field(default=PROJECT_ROOT / "storage" / "secrets.json")

    # 访问边界（详见 app/api/accounts.py:local_request）
    # 非回环客户端默认放行：服务能收到这种请求，就说明运维方已经把它绑到了可被访问的
    # 地址（只绑 127.0.0.1 时远程根本连不上）。要恢复"只允许本机"，设
    # CWB_ALLOW_REMOTE_ACCESS=false。
    allow_remote_access: bool = True
    # 用反向代理时把代理域名写进来（逗号分隔），否则回环客户端的 Host 校验会拦掉它。
    allowed_hosts: str = ""

    def extra_allowed_hosts(self) -> set[str]:
        return {h.strip().lower() for h in self.allowed_hosts.split(",") if h.strip()}

    def ensure_dirs(self) -> None:
        for d in (self.storage_root, self.artifact_dir, self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
