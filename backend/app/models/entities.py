"""最小版本模型（T05）。

设计对齐 03-data-and-api.md 第 2、3 节：
- 主键 UUID；C001 等为显示编号
- revision / artifact / 检查 / 快照**追加保存**，不覆盖历史
- 金额用整数微货币单位；JSON 用于列表类字段，查询用列
- run/job 带 lease + fencing_token，用于任务恢复（T13 完整实现）

P1 只落最小可用子集：profile / content / revision / artifact / run / job / review / event。
P2–P4 的 source、claim、metric、comment、report 表留待对应阶段迁移新增。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- 账号与定位


class AccountProfile(Base):
    """账号定位。批次固定引用某一版本（profile_version）。"""

    __tablename__ = "account_profile"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    versions: Mapped[list["ProfileVersionRow"]] = relationship(back_populates="profile")


class ProfileVersionRow(Base):
    """定位版本。历史不可变——只读，修改一律新建。"""

    __tablename__ = "profile_version"
    __table_args__ = (UniqueConstraint("profile_id", "version", name="uq_profile_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    profile_id: Mapped[str] = mapped_column(ForeignKey("account_profile.id"))
    version: Mapped[int] = mapped_column(Integer)
    audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    pillars: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    voice: Mapped[str | None] = mapped_column(Text, nullable=True)
    platforms: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    limits: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provider_policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    profile: Mapped[AccountProfile] = relationship(back_populates="versions")


# ---------------------------------------------------------------- 批次与内容


class Batch(Base):
    """批次。cost_mode=usage_tracking 时金额字段可为 NULL（未设置，不是 0）。"""

    __tablename__ = "batch"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    profile_version_id: Mapped[str | None] = mapped_column(ForeignKey("profile_version.id"), nullable=True)
    item_limit: Mapped[int] = mapped_column(Integer, default=1)
    cost_mode: Mapped[str] = mapped_column(String(32), default="usage_tracking")
    budget_limit_micro: Mapped[int | None] = mapped_column(Integer, nullable=True)  # NULL = 未设置
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    state: Mapped[str] = mapped_column(String(32), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    items: Mapped[list["ContentItem"]] = relationship(back_populates="batch")


class ContentItem(Base):
    """内容条目。display_id 为显示编号（如 C001），不是唯一键。"""

    __tablename__ = "content_item"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("batch.id"), nullable=True)
    display_id: Mapped[str] = mapped_column(String(32), index=True)
    topic: Mapped[str] = mapped_column(Text)
    selected_by: Mapped[str] = mapped_column(String(16), default="user")  # ai/user
    selection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="selected")
    active_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_mode: Mapped[str] = mapped_column(String(16), default="local_seed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    batch: Mapped[Batch | None] = relationship(back_populates="items")
    revisions: Mapped[list["ContentRevision"]] = relationship(back_populates="content")


class ContentRevision(Base):
    """母稿版本。seed ↔ 平台稿分离；input_hash 用于幂等校验。"""

    __tablename__ = "content_revision"
    __table_args__ = (UniqueConstraint("content_id", "version", name="uq_content_revision_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    content_id: Mapped[str] = mapped_column(ForeignKey("content_item.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    brief_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    claims_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    limitations_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    seed_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    content: Mapped[ContentItem] = relationship(back_populates="revisions")
    platform_revisions: Mapped[list["PlatformRevision"]] = relationship(back_populates="content_revision")


class PlatformRevision(Base):
    """平台变体。已渲染的 revision **不原地修改**；两平台状态互相独立。"""

    __tablename__ = "platform_revision"
    __table_args__ = (
        UniqueConstraint("content_revision_id", "platform", "version", name="uq_platform_revision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    content_revision_id: Mapped[str] = mapped_column(ForeignKey("content_revision.id"))
    platform: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(Text)
    caption: Mapped[str] = mapped_column(Text)
    pages_json: Mapped[dict] = mapped_column(JSON)
    profile_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    platform_profile_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="drafting")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    content_revision: Mapped[ContentRevision] = relationship(back_populates="platform_revisions")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="platform_revision")
    reviews: Mapped[list["ReviewDecision"]] = relationship(back_populates="platform_revision")


class Artifact(Base):
    """产物。路径为相对存储键；不可变；页序唯一。"""

    __tablename__ = "artifact"
    __table_args__ = (
        UniqueConstraint("platform_revision_id", "kind", "page_index", name="uq_artifact_page"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    platform_revision_id: Mapped[str] = mapped_column(ForeignKey("platform_revision.id"))
    kind: Mapped[str] = mapped_column(String(32), default="page_image")
    page_index: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    template_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    platform_revision: Mapped[PlatformRevision] = relationship(back_populates="artifacts")


class ReviewDecision(Base):
    """审核决定。actor 来自可信会话，不接受客户端传 system。"""

    __tablename__ = "review_decision"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    platform_revision_id: Mapped[str] = mapped_column(ForeignKey("platform_revision.id"))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(32))  # approve / request_change / hold
    actor: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    platform_revision: Mapped[PlatformRevision] = relationship(back_populates="reviews")


# ---------------------------------------------------------------- 研究与证据（P2）

class SourceRow(Base):
    """来源。P2 用它承载**访问状态**等需要查询/去重的字段。

    注意：claims 仍以 JSON 内嵌在 ContentRevision.claims_json（随 revision 冻结，
    保证不变量第 7 条"固定输入快照"）；本表是 sources 的可查询侧影，
    不替代 revision 里的快照。
    """

    __tablename__ = "source"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    content_id: Mapped[str | None] = mapped_column(ForeignKey("content_item.id"), nullable=True)
    source_key: Mapped[str] = mapped_column(String(32), index=True)  # 如 S01，内容内唯一
    kind: Mapped[str] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    locator: Mapped[str | None] = mapped_column(Text, nullable=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # full_text / search_snippet / user_provided —— search_snippet 不能支撑 fact
    excerpt_basis: Mapped[str] = mapped_column(String(32), default="user_provided")
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # ok / blocked / timeout / not_found / unconfigured / snippet_only
    access_state: Mapped[str] = mapped_column(String(32), default="ok")
    supports: Mapped[str | None] = mapped_column(Text, nullable=True)
    limitations: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_mode: Mapped[str] = mapped_column(String(16), default="local_seed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ---------------------------------------------------------------- 任务与事件


class Run(Base):
    __tablename__ = "run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    content_id: Mapped[str | None] = mapped_column(ForeignKey("content_item.id"), nullable=True)
    stage: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16), default="local_seed")  # real/fixture/local_seed
    state: Mapped[str] = mapped_column(String(32), default="queued")
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_refs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    not_before: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    blocked_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    jobs: Mapped[list["Job"]] = relationship(back_populates="run")


class Job(Base):
    """阶段步骤。重试不重复追加同一阶段产物（由 unique(run_id, stage, input_hash) 保证）。"""

    __tablename__ = "job"
    __table_args__ = (
        UniqueConstraint("run_id", "stage", "input_hash", name="uq_job_run_stage_input"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id"))
    stage: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="queued")
    input_hash: Mapped[str] = mapped_column(String(64))
    output_refs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[Run] = relationship(back_populates="jobs")


class Event(Base):
    """操作追加记录。同原因异常在此聚合。"""

    __tablename__ = "event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    type: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(64), default="system")
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    run_mode: Mapped[str] = mapped_column(String(16), default="local_seed")
    time: Mapped[datetime] = mapped_column(DateTime, default=_now)
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ProviderExchange(Base):
    """Durable request journal: reserve before send; reuse completed replies."""
    __tablename__ = "provider_exchange"
    request_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    content_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="unknown")
    reserved_micro: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    call: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ProviderCallRow(Base):
    """一次 Provider 调用的记账（P2/T08）。

    为什么落表而不是继续用 JSON ledger：
    - 需要按 content / revision 关联查询
    - JSON 数组整体读写无并发安全，并发会丢账（破坏不变量第 4 条）
    - unique(request_key) 天然保证"重试不重复记账"

    用量与费用分开；金额未知存 NULL，**绝不写 0**。
    """

    __tablename__ = "provider_call"
    __table_args__ = (
        UniqueConstraint("request_key", name="uq_provider_call_request_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    request_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    content_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    platform_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider_config_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    remote_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 用量：拿不到就是 NULL，不等于 0
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 费用：整数微货币单位；三态各自独立，不互相覆盖
    pricing_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    estimated_micro: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reported_micro: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reconciled_micro: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    billing_state: Mapped[str] = mapped_column(String(32), default="unknown")

    state: Mapped[str] = mapped_column(String(16), default="unknown")  # succeeded/failed/unknown
    run_mode: Mapped[str] = mapped_column(String(16), default="real")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_of: Mapped[str | None] = mapped_column(String(36), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------- 反馈闭环（P4）

class Publication(Base):
    """发布记录（P4/T16）。

    不变量第 2 条：**没有实际发布登记，不能通过下载、定时器或 AI 推断自动标记已发布**。
    所以这张表只能由人写入（`registered_by` 是可信会话里的用户标识），
    Provider / 模型 / 下载动作都不能代劳。

    `declared_*` 与 `verified_*` **分开存**：
    - `declared` = 人声称的信息（他说发了，链接他填的）
    - `verified` = 系统真的核验过的状态（比如平台页面打开确认过）

    两者混在一起就会出现"我以为核验过"的假象。人填的链接没核验，
    就是 declared 有值、verified 为 null，这是**如实**的。
    """

    __tablename__ = "publication"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    platform_revision_id: Mapped[str] = mapped_column(ForeignKey("platform_revision.id"))
    #: 至少要有链接或平台作品 ID 之一，否则不算登记（service 层强制）
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform_post_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: 登记时该平台稿的 manifest_hash——用于判定"批准后又改了图/文案"
    published_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: declared / verified 分开；verified 为 null 表示"没人核验过"，不等于"核验失败"
    declaration_source: Mapped[str] = mapped_column(String(32), default="manual")
    verified_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="declared")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    registered_by: Mapped[str] = mapped_column(String(64), default="user")
    run_mode: Mapped[str] = mapped_column(String(16), default="real")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    snapshots: Mapped[list["MetricSnapshot"]] = relationship(back_populates="publication")
    comments: Mapped[list["CommentSample"]] = relationship(back_populates="publication")


class ImportBatch(Base):
    """导入批次（P4/T17、T18）。同文件 + 同映射重复请求幂等。"""

    __tablename__ = "import_batch"
    __table_args__ = (
        UniqueConstraint("kind", "file_hash", "mapping_version", name="uq_import_batch_file_mapping"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(16))  # metrics / comments
    file_hash: Mapped[str] = mapped_column(String(64))
    file_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    format: Mapped[str] = mapped_column(String(16), default="csv")
    mapping_version: Mapped[str] = mapped_column(String(32), default="v1")
    state: Mapped[str] = mapped_column(String(32), default="parsed")
    #: 原始表头 → 规范字段 的映射快照（可追溯"这批是怎么解释的"）
    mapping_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    accepted_rows: Mapped[int] = mapped_column(Integer, default=0)
    error_rows: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0)
    unmatched_rows: Mapped[int] = mapped_column(Integer, default=0)
    errors_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    run_mode: Mapped[str] = mapped_column(String(16), default="fixture")
    created_by: Mapped[str] = mapped_column(String(64), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class MetricSnapshot(Base):
    """指标快照（P4/T17）。同一采集上下文去重；修订**追加**而非覆盖。

    `supersedes_id` 指向被本行取代的旧行——旧行保留，不删除也不原地改。
    这样"上次看是 1.2 万、这次变 1.1 万"这种回溯性修正是可见的。
    """

    __tablename__ = "metric_snapshot"
    __table_args__ = (
        UniqueConstraint("publication_id", "observed_at", "window_kind", "traffic_type",
                         name="uq_metric_snapshot_ctx"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    publication_id: Mapped[str] = mapped_column(ForeignKey("publication.id"))
    import_id: Mapped[str | None] = mapped_column(ForeignKey("import_batch.id"), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime)
    age_hours: Mapped[float | None] = mapped_column(nullable=True)
    #: 累计值 / 窗口值 / 未知——**不能互相比较**
    window_kind: Mapped[str] = mapped_column(String(16), default="cumulative")
    #: 流量类型。**进唯一约束**：自然流量与付费流量在同一时刻是两个不同的观测，
    #: 合并会让"多少是自然涨的"永远算不出来。
    traffic_type: Mapped[str] = mapped_column(String(16), default="unknown")
    #: 规范化后的指标：{metric_name: {"value": int|float|None, "unit": str,
    #   "denominator_metric": str|None, "traffic_type": str|None}}
    metrics: Mapped[dict] = mapped_column(JSON)
    #: 映射前的原始字段，原样保留（平台改名后能回溯）
    raw_fields: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    mapping_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    supersedes_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_mode: Mapped[str] = mapped_column(String(16), default="fixture")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    publication: Mapped[Publication] = relationship(back_populates="snapshots")


class CommentSample(Base):
    """评论样本（P4/T18）。**去掉不需要的身份信息**，保留样本口径。"""

    __tablename__ = "comment_sample"
    __table_args__ = (
        UniqueConstraint("publication_id", "anon_id", name="uq_comment_publication_anon"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    publication_id: Mapped[str] = mapped_column(ForeignKey("publication.id"))
    import_id: Mapped[str | None] = mapped_column(ForeignKey("import_batch.id"), nullable=True)
    #: 平台侧匿名 ID 或原始 ID 的哈希；**不存公开用户名**
    anon_id: Mapped[str] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: 取样方式（全量导出 / 前 N 条 / 时间窗）——**不做口径说明就不能比较**
    sampling_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sample_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 问题 / 质疑 / 经验 / 需求 / 无效 / 未归类
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    category_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    like_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_mode: Mapped[str] = mapped_column(String(16), default="fixture")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    publication: Mapped[Publication] = relationship(back_populates="comments")


class ReviewReport(Base):
    """复盘报告（P4/T19）。按**固定输入快照**生成，后续新增数据不暗中改旧结论。

    `snapshot_ids` / `comment_import_ids` 把"这份结论基于哪批数据"冻住；
    新数据来了是**新建一份**报告（version+1），不是改旧的。
    """

    __tablename__ = "review_report"
    __table_args__ = (
        UniqueConstraint("content_id", "version", name="uq_review_report_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    content_id: Mapped[str] = mapped_column(ForeignKey("content_item.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    #: 生成时的输入快照——**不可变引用**
    snapshot_ids: Mapped[list] = mapped_column(JSON, default=list)
    comment_import_ids: Mapped[list] = mapped_column(JSON, default=list)
    publication_ids: Mapped[list] = mapped_column(JSON, default=list)
    #: 数据充分度：none / insufficient / baseline_only / comparable
    data_sufficiency: Mapped[str] = mapped_column(String(32), default="none")
    sufficiency_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 三栏分开，**不混成一段话**
    observations: Mapped[list] = mapped_column(JSON, default=list)   # 观察事实（程序算的）
    hypotheses: Mapped[list] = mapped_column(JSON, default=list)     # 可能解释 + 替代解释
    experiments: Mapped[list] = mapped_column(JSON, default=list)    # 下一轮建议
    next_topics: Mapped[list] = mapped_column(JSON, default=list)    # 候选选题
    limitations: Mapped[list] = mapped_column(JSON, default=list)
    source_chain: Mapped[list] = mapped_column(JSON, default=list)
    #: 模型参与程度：none（纯程序）/ assisted（模型解释，程序算数）
    generation_mode: Mapped[str] = mapped_column(String(16), default="none")
    run_mode: Mapped[str] = mapped_column(String(16), default="fixture")
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TopicFeedback(Base):
    """复盘 → 下一轮的影响（P4/T20）。

    第 6 条不变量：**模型无法直接更改批准、预算上限、身份、连接权限和发布状态**。
    这里同样——复盘产出的是**建议**（`status=proposed`），
    只有人确认（`accepted`）才会进到"已采用"，
    且**采用的变更必须可回退**（`reverted_at`）。
    """

    __tablename__ = "topic_feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    review_report_id: Mapped[str] = mapped_column(ForeignKey("review_report.id"))
    content_id: Mapped[str] = mapped_column(ForeignKey("content_item.id"))
    #: 建议类型：topic_weight / angle / format / publish_time / hook
    kind: Mapped[str] = mapped_column(String(32))
    #: 只允许"小改动"：幅度分 tiny/small/medium/large，超出范围只能保存为候选
    magnitude: Mapped[str] = mapped_column(String(16), default="small")
    proposal: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 链回原始观察，防止"建议"变成无源之水
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="proposed")
    auto_adopted: Mapped[bool] = mapped_column(Boolean, default=False)
    adopted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: 采用前的旧值，**回退用**
    previous_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reverted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_mode: Mapped[str] = mapped_column(String(16), default="fixture")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class DouyinPublishTask(Base):
    """Durable, user-confirmed browser publishing; never retry an uncertain submit."""
    __tablename__ = "douyin_publish_task"
    __table_args__ = (UniqueConstraint("account_id", "platform_revision_id", "payload_hash", name="uq_douyin_version_account"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    platform_revision_id: Mapped[str] = mapped_column(ForeignKey("platform_revision.id"))
    account_id: Mapped[str] = mapped_column(String(64))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(32), default="planned")
    publication_id: Mapped[str | None] = mapped_column(ForeignKey("publication.id"), nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_observation_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class VideoTask(Base):
    __tablename__ = 'video_task'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    platform_revision_id: Mapped[str] = mapped_column(ForeignKey('platform_revision.id'))
    request_key: Mapped[str] = mapped_column(String(64),unique=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(24),default='queued')
    progress: Mapped[int] = mapped_column(Integer,default=0)
    result_json: Mapped[dict | None] = mapped_column(JSON,nullable=True)
    error: Mapped[str | None] = mapped_column(Text,nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime,default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime,default=_now)

# ---------------------------------------------------------------- SQLite 外键

@event.listens_for(Base.metadata, "before_create")
def _noop(target, connection, **kw):  # pragma: no cover
    return None


def enable_sqlite_fk(engine) -> None:
    """SQLite 每连接启用外键（03 文档第 1 节要求）。"""
    from sqlalchemy import event as sa_event

    @sa_event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()
