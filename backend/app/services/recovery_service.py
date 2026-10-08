"""任务恢复（P3/T13）。

对照 docs/04-development-plan.md T13 原文的四类故障，以及
不变量第 3、4 条。

## 为什么需要租约 + fencing_token

两个 Worker 可能同时认为自己在跑同一个 job——比如 A 卡住了没死透，
B 判定租约过期后接手。如果 A 醒过来继续写库，就会覆盖 B 的产物：
**产物重复追加、状态互相踩踏**，正好违反"重试不重复追加同一阶段产物"。

解决办法是 fencing_token：

    A 领取 job → token=1
    A 卡住，租约过期
    B 接手     → token=2
    A 醒来写库 → 带 token=1，与当前 token=2 不符 → **被拒**

token 只增不减，旧持有者永远追不上。这样"过期"不再是靠时间猜测，
而是靠单调计数器硬性判定。

## 四类故障与处置

| 故障 | 检测方式 | 处置 |
|---|---|---|
| 租约过期 | `lease_expires_at < now` 且 state 非终态 | 重新入队，`fencing_token += 1`，`attempt += 1` |
| 文件在磁盘、DB 没记录 | 扫 artifact 目录 vs `Artifact` 表 | 标 `orphan`，**不自动删除**，列入异常待人工确认 |
| DB 有记录、文件不在 | `Artifact` 行指向的路径不存在 | 标 `artifact_missing`，平台转 `checking`，需重渲染 |
| **远端结果未知** | `ProviderCall.state == 'unknown'` | **先查状态，不重发**；查不到标 `needs_manual_confirm`，费用保持 NULL |

最后一类是重点：**未知 ≠ 失败**。盲目重发可能双倍计费，而两次调用可能
都成功、都产生产出——这正是"未知结果不等于免费失败"要防的事。

本模块**不做**的事：
- 不自动删除任何文件（孤儿文件也要人确认后再删）
- 不自动重发结果未知的调用
- 不自动批准或发布
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..core.config import get_settings
from ..core.errors import NotFound, StateConflict
from ..models.entities import (
    Artifact,
    ContentItem,
    Event,
    Job,
    PlatformRevision,
    ProviderCallRow,
    ProviderExchange,
    Run,
)

#: 租约时长。过长则故障恢复慢，过短则正常任务被误判为过期。
DEFAULT_LEASE_SECONDS = 120

#: 非终态：这些状态的 job 才可能"卡住"
OPEN_STATES = {"queued", "running", "leased"}

#: 不重试的错误码——重试也不会变好，只会多花钱
NO_RETRY_CODES = frozenset({
    "AUTH",                 # 密钥不对
    "MISSING_KEY",          # 没配密钥
    "ADAPTER_NOT_IMPLEMENTED",
    "INVALID_REQUEST",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """统一成 aware UTC。

    SQLite 的 DateTime 列取回来是 **naive** 的（不带时区），
    直接和 `datetime.now(timezone.utc)` 比较会抛
    `TypeError: can't compare offset-naive and offset-aware datetimes`。
    租约过期判断正是最容易踩这个坑的地方——比较失败就意味着
    "过期检测要么崩、要么永远判为没过期"，后者会让故障永远恢复不了。
    """
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


@dataclass
class RecoveryFinding:
    """一条待处置的故障。"""

    kind: str            # lease_expired / orphan_file / artifact_missing / unknown_result
    subject: str         # job id / 文件路径 / row id
    detail: dict = field(default_factory=dict)
    action: str = ""     # 已执行或建议的动作
    auto_fixed: bool = False

    def as_dict(self) -> dict:
        return {"kind": self.kind, "subject": self.subject, "detail": self.detail,
                "action": self.action, "auto_fixed": self.auto_fixed}


@dataclass
class RecoveryReport:
    findings: list[RecoveryFinding] = field(default_factory=list)
    scanned_jobs: int = 0
    scanned_artifacts: int = 0
    scanned_calls: int = 0

    @property
    def auto_fixed(self) -> list[RecoveryFinding]:
        return [f for f in self.findings if f.auto_fixed]

    @property
    def needs_manual(self) -> list[RecoveryFinding]:
        return [f for f in self.findings if not f.auto_fixed]

    def as_dict(self) -> dict:
        return {
            "scanned": {"jobs": self.scanned_jobs, "artifacts": self.scanned_artifacts,
                        "provider_calls": self.scanned_calls},
            "auto_fixed": [f.as_dict() for f in self.auto_fixed],
            "needs_manual": [f.as_dict() for f in self.needs_manual],
            "summary": {
                "auto_fixed": len(self.auto_fixed),
                "needs_manual": len(self.needs_manual),
            },
        }


class RecoveryService:
    """租约管理、故障扫描与恢复。"""

    def __init__(self, session_factory, *, actor: str = "coisini",
                 worker_id: str | None = None) -> None:
        self.sf = session_factory
        self.actor = actor
        self.settings = get_settings()
        self.worker_id = worker_id or f"worker-{self.actor}"

    # ============================================================ 租约

    def acquire(self, job_id: str, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> dict:
        """领取一个 job。成功返回新的 fencing_token。

        只有在 job 未被占用、或**占用者租约已过期**时才能领取。
        每次领取 token 递增，让被顶替的旧持有者后续写入失败。
        """
        with self.sf() as s:
            s.connection().exec_driver_sql("BEGIN IMMEDIATE")
            job = s.get(Job, job_id)
            if job is None:
                raise NotFound(f"Job 不存在：{job_id}")

            now = _now()
            expires = _aware(job.lease_expires_at)
            held = (
                job.lease_owner is not None
                and expires is not None
                and expires > now
                and job.lease_owner != self.worker_id
            )
            if held:
                raise StateConflict(
                    f"Job 仍被 {job.lease_owner} 持有，租约至 "
                    f"{expires.isoformat()}；不抢占未过期租约"
                )

            superseded = job.lease_owner if job.lease_owner not in (None, self.worker_id) else None
            job.lease_owner = self.worker_id
            job.lease_expires_at = now + timedelta(seconds=lease_seconds)
            job.fencing_token = (job.fencing_token or 0) + 1
            job.state = "running"
            job.attempt = (job.attempt or 0) + 1
            job.started_at = now
            s.commit()

            # 顶替了旧持有者时记一条事件：这是需要被看见的事，不是日常
            if superseded:
                s.add(Event(
                    entity_type="job", entity_id=job.id, type="lease_superseded",
                    actor=self.actor, payload={"previous_owner": superseded,
                                               "new_token": job.fencing_token},
                ))
                s.commit()

            return {"job_id": job.id, "stage": job.stage,
                    "fencing_token": job.fencing_token,
                    "lease_owner": job.lease_owner,
                    "lease_expires_at": job.lease_expires_at.isoformat(),
                    "superseded": superseded,
                    "attempt": job.attempt}

    def renew(self, job_id: str, token: int, *,
              lease_seconds: int = DEFAULT_LEASE_SECONDS) -> dict:
        """持有者续租。**token 不变**——续租不是重新领取。"""
        with self.sf() as s:
            job = self._assert_token(s, job_id, token)
            job.lease_expires_at = _now() + timedelta(seconds=lease_seconds)
            s.commit()
            return {"job_id": job.id, "fencing_token": job.fencing_token,
                    "lease_expires_at": job.lease_expires_at.isoformat()}

    def release(self, job_id: str, token: int, *, state: str = "succeeded",
                refs: dict | None = None, error: str | None = None) -> dict:
        """完成并释放。写库前校验 token —— 被顶替的旧持有者在此被拦下。"""
        with self.sf() as s:
            job = self._assert_token(s, job_id, token)
            job.state = state
            job.output_refs = refs or {}
            job.error = error
            job.finished_at = _now()
            job.lease_owner = None
            job.lease_expires_at = None
            s.commit()
            return {"job_id": job.id, "state": job.state,
                    "fencing_token": job.fencing_token}

    def _assert_token(self, s, job_id: str, token: int) -> Job:
        job = s.get(Job, job_id)
        if job is None:
            raise NotFound(f"Job 不存在：{job_id}")
        if job.fencing_token != token:
            raise StateConflict(
                f"fencing_token 已变更（你的 {token} ≠ 当前 {job.fencing_token}）："
                "该 job 已被其他 worker 接管，本次写入被拒绝以避免重复产物"
            )
        return job

    # ============================================================ 故障扫描

    def scan(self, *, content_id: str | None = None,
             lease_seconds: int = DEFAULT_LEASE_SECONDS) -> RecoveryReport:
        """扫描全部四类故障。**自动修可自动修的，其余如实上报。**"""
        rep = RecoveryReport()
        rep.findings += self._scan_expired_leases()
        rep.findings += self._scan_artifacts(content_id=content_id)
        rep.findings += self._scan_unknown_results(content_id=content_id)
        return rep

    def _scan_expired_leases(self) -> list[RecoveryFinding]:
        """租约过期的 job → 重新入队（这是可自动恢复的）。"""
        out: list[RecoveryFinding] = []
        now = _now()
        with self.sf() as s:
            rows = (
                s.query(Job).filter(Job.state.in_(sorted(OPEN_STATES)))
                .filter(Job.lease_expires_at.isnot(None))
                .filter(Job.lease_expires_at < now).all()
            )
            for job in rows:
                prev_owner = job.lease_owner
                if job.stage == "batch_dispatch" and s.query(ProviderExchange).filter_by(content_id=s.get(Run, job.run_id).content_id, state="unknown").count():
                    # Interrupted production may have reached a paid remote API.
                    # Do not replay an entire production with unknown stage outputs.
                    job.state = "failed"
                    job.error = "进程中断，执行结果需人工核查；未自动重发模型调用"
                    run = s.get(Run, job.run_id)
                    if run:
                        run.state = "failed"
                        run.blocked_stage = "interrupted"
                        run.error = job.error
                    out.append(RecoveryFinding(kind="interrupted_production", subject=job.id,
                        detail={"stage": job.stage}, action=job.error, auto_fixed=False))
                    job.lease_owner = None
                    job.lease_expires_at = None
                    job.fencing_token = (job.fencing_token or 0) + 1
                    continue
                job.state = "queued"
                if job.stage == "batch_dispatch":
                    s.get(Run, job.run_id).state = "queued"
                job.lease_owner = None
                job.lease_expires_at = None
                # token 递增：旧持有者若醒来，写入会被拒
                job.fencing_token = (job.fencing_token or 0) + 1
                out.append(RecoveryFinding(
                    kind="lease_expired", subject=job.id,
                    detail={"stage": job.stage, "previous_owner": prev_owner,
                            "attempt": job.attempt,
                            "new_fencing_token": job.fencing_token},
                    action="已重新入队；fencing_token 递增，旧持有者写入将被拒绝",
                    auto_fixed=True,
                ))
            if rows:
                s.commit()
        return out

    def _scan_artifacts(self, *, content_id: str | None = None) -> list[RecoveryFinding]:
        """文件与 DB 不一致：

        - DB 有记录、文件没了 → `artifact_missing`，平台需重渲染
        - 文件在、DB 没记录 → `orphan_file`，**只登记不删除**
        """
        out: list[RecoveryFinding] = []
        root = self.settings.artifact_dir
        dirty = False

        with self.sf() as s:
            q = s.query(Artifact)
            if content_id:
                q = (q.join(PlatformRevision, Artifact.platform_revision_id == PlatformRevision.id)
                      .join(ContentItem, PlatformRevision.content_revision_id
                            == ContentItem.active_revision_id)
                      .filter(ContentItem.id == content_id))
            rows = q.all()

            known: set[str] = set()
            for a in rows:
                known.add(a.storage_key.replace("\\", "/"))
                p = root / a.storage_key
                if not p.exists() or p.stat().st_size == 0:
                    pr = s.get(PlatformRevision, a.platform_revision_id)
                    if pr is not None and pr.state == "approved":
                        # 已批准的版本缺图不能自动重渲染——那会让批准失效
                        pr.state = "approved_artifact_missing"
                        dirty = True
                    elif pr is not None and pr.state != "checking":
                        pr.state = "checking"
                        dirty = True
                    out.append(RecoveryFinding(
                        kind="artifact_missing", subject=a.storage_key,
                        detail={"platform_revision_id": a.platform_revision_id,
                                "page_index": a.page_index,
                                "exists": p.exists()},
                        action=("已批准版本缺图：标记为 approved_artifact_missing，"
                                "需人工决定重渲染或撤销批准"
                                if pr is not None and pr.state.startswith("approved")
                                else "已标记平台稿为 checking，需重渲染"),
                        auto_fixed=False,
                    ))

            # 状态变更必须落库。不 commit 的话，with 块退出时会回滚，
            # "已标记" 就成了空话——平台稿仍停在 approved，
            # 人下次看到的还是"一切正常"，故障被静默吞掉。
            if dirty:
                s.commit()

        # 磁盘上多出来的文件
        if root.exists():
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                key = p.relative_to(root).as_posix()
                if key == ".write_probe" or (key.startswith("packages/") and p.suffix == ".zip"):
                    continue
                if key not in known:
                    out.append(RecoveryFinding(
                        kind="orphan_file", subject=key,
                        detail={"size_bytes": p.stat().st_size},
                        action="仅登记，未删除（删除需人工确认，避免误删有用产物）",
                        auto_fixed=False,
                    ))
        return out

    def _scan_unknown_results(self, *, content_id: str | None = None) -> list[RecoveryFinding]:
        """远端结果未知的调用。

        **绝不自动重发**。理由：未知可能意味着调用其实成功了，
        重发会双倍计费并可能产生两份有效产出。正确做法是先查状态。
        """
        out: list[RecoveryFinding] = []
        with self.sf() as s:
            q = s.query(ProviderCallRow).filter(ProviderCallRow.state == "unknown")
            if content_id:
                q = q.filter(ProviderCallRow.content_id == content_id)
            rows = q.all()
            for c in rows:
                out.append(RecoveryFinding(
                    kind="unknown_result", subject=c.id,
                    detail={
                        "request_key": c.request_key,
                        "provider": c.provider_name,
                        "remote_request_id": c.remote_request_id,
                        "billing_state": c.billing_state,
                        "estimated_micro": c.estimated_micro,
                        "reported_micro": c.reported_micro,
                        "currency": c.currency,
                    },
                    action=("需人工确认远端是否已产生结果；"
                            "**不自动重发**（可能双倍计费）。"
                            "费用保持 NULL，不写 0"),
                    auto_fixed=False,
                ))
        return out

    # ============================================================ 重试策略

    def should_retry(self, error_code: str | None, attempt: int, *,
                     max_attempts: int = 3) -> tuple[bool, str]:
        """能不能重试。**未知结果永远不重试**（先查状态）。"""
        if error_code is None:
            return False, "无错误码，不判断"
        if error_code in NO_RETRY_CODES:
            return False, f"{error_code} 属确定性失败，重试不会变好，只会多花钱"
        if error_code == "UNKNOWN":
            return False, "结果未知：先查远端状态再决定，不盲目重发"
        if attempt >= max_attempts:
            return False, f"已达最大重试次数 {max_attempts}"
        return True, f"可重试（第 {attempt + 1} 次）"

    def retry_backoff_seconds(self, attempt: int, *, base: float = 1.0,
                              cap: float = 60.0) -> float:
        """指数退避。有上限，避免无限等待。"""
        return min(cap, base * (2 ** max(0, attempt - 1)))
