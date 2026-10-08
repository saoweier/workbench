"""发布记录（P4/T16）。

对照 `docs/04-development-plan.md` T16、`docs/03-data-and-api.md` §3 publication 行，
以及不变量第 **2、6** 条。

## 为什么这个模块要先于数据导入存在

数据导入（T17）要有东西可以"挂上去"。但比这更根本的是：

> **不变量第 2 条：没有实际发布登记，不能通过下载、定时器或 AI 推断自动标记已发布。**

也就是说，"已发布"这个状态**只能由人写入**。这一条在 P1/P2 是靠"没实现发布状态"来天然满足的；
到了 P4，系统第一次有了"记录发布"的能力，也就第一次有了**破坏这条不变量**的机会。
本模块的存在意义就是把这个机会焊死：

- 登记接口**只接受人填的信息**，`registered_by` 来自可信会话，不接受客户端声明 `system`
- **链接与平台作品 ID 至少有一个**，两个都空就不算登记（"我觉得发了"不是记录）
- 登记**不改变** `platform_revision.state`（那是批准/导出的事）；
  "已发布"是查询时由 publication 表现算出来的，不是某个字段被人一键翻过去
- `declared`（人说的）与 `verified`（核验过的）**分开存**，混淆会出现"以为核验过"

## 批准与发布的关系

登记发布时必须带上当时那版稿的 `manifest_hash`。如果批准之后图/文案又变了，
这个 hash 就对不上——服务层据此把记录标成 `supersedes_approval=False`，
并给出明确提示，而不是默默记成一次干净的发布。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..models.entities import (
    ContentItem,
    PlatformRevision,
    Publication,
    ReviewDecision,
    _now,
)

#: 允许的登记状态。注意没有 "published"——发布与否是**算出来的**，不是人写上去的。
ALLOWED_STATUS = ("declared", "withdrawn")

#: 核验状态。None 表示"没人核验过"，**不等于核验失败**。
VERIFIED_STATES = ("confirmed", "mismatch", "inaccessible")

#: 登记的声明来源：人工 / 平台导出
DECLARATION_SOURCES = ("manual", "platform_export")


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite 的 DateTime 列取出来是 naive 的，直接和 aware 比较会 TypeError。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class PublicationError(ValueError):
    """登记发布时的输入问题。带 code 便于接口层映射状态码。"""

    def __init__(self, code: str, message: str, *, fields: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fields = fields or {}


@dataclass
class RegisterOutcome:
    publication_id: str
    platform: str
    status: str
    declared: dict
    verified: dict | None
    approval_consistent: bool
    run_mode: str
    warnings: list = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "publication_id": self.publication_id,
            "platform": self.platform,
            "status": self.status,
            "declared": self.declared,
            "verified": self.verified,
            "approval_consistent": self.approval_consistent,
            "run_mode": self.run_mode,
            "warnings": self.warnings,
            "note": self.note,
        }


class PublicationService:
    def __init__(self, session_factory: sessionmaker[Session], settings=None) -> None:
        self.sf = session_factory
        self.settings = settings

    # ------------------------------------------------------------ 登记发布

    def register(
        self,
        *,
        platform_revision_id: str,
        link: str | None = None,
        platform_post_id: str | None = None,
        published_at: datetime | None = None,
        registered_by: str = "user",
        account_id: str | None = None,
        declaration_source: str = "manual",
        note: str | None = None,
        run_mode: str = "real",
    ) -> RegisterOutcome:
        """人工登记一条发布记录。

        **不接受** `registered_by="system"`——"已发布"只能由人登记。
        """
        if (registered_by or "").strip().lower() in ("system", "model", "ai", "worker", ""):
            raise PublicationError(
                "PUBLISHER_MUST_BE_HUMAN",
                "发布登记只能由可信用户会话发起；不接受 system/model/worker 作为登记人",
                fields={"registered_by": registered_by},
            )
        if declaration_source not in DECLARATION_SOURCES:
            raise PublicationError(
                "BAD_DECLARATION_SOURCE",
                f"声明来源必须是 {DECLARATION_SOURCES} 之一",
                fields={"declaration_source": declaration_source},
            )
        if run_mode not in {"real", "fixture", "local_seed"}:
            raise PublicationError(
                "BAD_RUN_MODE",
                "run_mode 必须是 real、fixture 或 local_seed",
                fields={"run_mode": run_mode},
            )
        if not (link or "").strip() and not (platform_post_id or "").strip():
            raise PublicationError(
                "PUBLICATION_NEEDS_IDENTIFIER",
                "至少要填写平台链接或平台作品 ID —— 两者都空不算一条发布记录",
                fields={"link": link, "platform_post_id": platform_post_id},
            )

        warnings: list[str] = []
        with self.sf() as s:
            pr = s.get(PlatformRevision, platform_revision_id)
            if pr is None:
                raise PublicationError("PLATFORM_REVISION_NOT_FOUND",
                                       f"平台稿不存在：{platform_revision_id}")

            # --- 批准校验：没批准过就发布，等于绕过了整个审核环节 ---
            approved = self._latest_approval(s, pr)
            approval_consistent = False
            published_manifest_hash = pr.manifest_hash
            if approved is None:
                warnings.append(
                    "该平台稿没有批准记录。已如实登记，但这属于**先发布后补流程**，"
                    "系统不会因此把它当成一次合规发布。"
                )
            elif pr.manifest_hash and approved.manifest_hash != pr.manifest_hash:
                # 批准的是旧快照，现在的稿已经变了
                warnings.append(
                    "当前稿的 manifest 与批准时的 manifest 不一致"
                    f"（批准 {approved.manifest_hash[:8]}… / 现在 "
                    f"{(pr.manifest_hash or '')[:8]}…）。"
                    "这属于「过期批准」，不构成对当前版本的批准。"
                )
            else:
                approval_consistent = True

            pub = Publication(
                account_id=account_id,
                platform=pr.platform,
                platform_revision_id=platform_revision_id,
                link=(link or "").strip() or None,
                platform_post_id=(platform_post_id or "").strip() or None,
                # DateTime columns are stored as naive UTC in SQLite. Convert
                # offset-aware inputs before SQLAlchemy drops their tzinfo.
                published_at=(
                    published_at.astimezone(timezone.utc).replace(tzinfo=None)
                    if published_at is not None and published_at.tzinfo is not None
                    else published_at
                ) or _now(),
                published_manifest_hash=published_manifest_hash,
                published_content_hash=pr.content_hash,
                declaration_source=declaration_source,
                verified_state=None,  # 人填的链接默认**没核验过**
                status="declared",
                note=note,
                registered_by=registered_by,
                run_mode=run_mode,
            )
            s.add(pub)
            s.flush()
            pub_id = pub.id
            platform = pub.platform
            declared = self._declared_dict(pub)
            s.commit()

        return RegisterOutcome(
            publication_id=pub_id,
            platform=platform,
            status="declared",
            declared=declared,
            verified=None,
            approval_consistent=approval_consistent,
            run_mode=run_mode,
            warnings=warnings,
            note=(
                "declared = 人填的信息；verified = 系统核验过的状态。"
                "本记录 verified 为空，表示**没人核验过**，不等于核验失败。"
            ),
        )

    # ------------------------------------------------------------ 核验

    def verify(
        self,
        publication_id: str,
        *,
        verified_state: str,
        verified_by: str = "user",
        note: str | None = None,
    ) -> dict:
        """记录一次人工核验结果。**只是记录，不会自动抓平台。**"""
        if verified_state not in VERIFIED_STATES:
            raise PublicationError("BAD_VERIFIED_STATE",
                                   f"核验状态必须是 {VERIFIED_STATES} 之一",
                                   fields={"verified_state": verified_state})
        if (verified_by or "").strip().lower() in ("system", "model", "ai", ""):
            raise PublicationError("VERIFIER_MUST_BE_HUMAN",
                                   "核验只能由可信用户会话记录")

        with self.sf() as s:
            pub = s.get(Publication, publication_id)
            if pub is None:
                raise PublicationError("PUBLICATION_NOT_FOUND", f"发布记录不存在：{publication_id}")
            pub.verified_state = verified_state
            pub.verified_at = _now()
            pub.verified_by = verified_by
            if note:
                pub.verified_note = note
            if verified_state == "confirmed":
                pub.status = "verified"
            s.commit()
            return self._row_dict(s, pub)

    # ------------------------------------------------------------ 查询

    def list_publications(self, *, content_id: str | None = None,
                          platform: str | None = None) -> dict:
        with self.sf() as s:
            stmt = select(Publication)
            if platform:
                stmt = stmt.where(Publication.platform == platform)
            rows = list(s.scalars(stmt.order_by(Publication.created_at.desc())))

            if content_id:
                keep = []
                for pub in rows:
                    pr = s.get(PlatformRevision, pub.platform_revision_id)
                    if pr is None:
                        continue
                    cr_id = pr.content_revision_id
                    from ..models.entities import ContentRevision
                    cr = s.get(ContentRevision, cr_id)
                    if cr is not None and cr.content_id == content_id:
                        keep.append(pub)
                rows = keep

            items = [self._row_dict(s, p) for p in rows]

            # 按内容聚合发布覆盖情况——两平台**独立**，一个发了不代表另一个发了
            coverage: dict[str, dict] = {}
            for it in items:
                cid = it.get("content_id") or "unknown"
                slot = coverage.setdefault(cid, {"platforms": {}, "published_platforms": []})
                slot["platforms"][it["platform"]] = it["status"]
            for cid, slot in coverage.items():
                slot["published_platforms"] = sorted(
                    p for p, st in slot["platforms"].items() if st in ("declared", "verified")
                )

            return {
                "items": items,
                "total": len(items),
                "coverage": coverage,
                "semantics": {
                    "declared": "人已登记发布信息，但未核验",
                    "verified": "人已核验过发布确实存在",
                    "withdrawn": "人标记为已撤回",
                    "never_auto": "本系统不会通过下载、定时器或模型推断把任何记录标成已发布",
                },
            }

    def publication_summary(self, content_id: str) -> dict:
        """某条内容的发布覆盖情况。用于 `awaiting_data` 状态判定。"""
        data = self.list_publications(content_id=content_id)
        items = data["items"]
        platforms: dict[str, str] = {}
        for it in items:
            platforms[it["platform"]] = it["status"]
        any_published = any(st in ("declared", "verified") for st in platforms.values())
        return {
            "content_id": content_id,
            "platforms": platforms,
            "any_published": any_published,
            "publication_count": len(items),
            "note": (
                "两平台状态互相独立：一个平台已发布不表示另一个也发布了。"
                "`any_published` 只是「至少有一个」的粗略判断。"
            ),
        }

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _latest_approval(s: Session, pr: PlatformRevision) -> ReviewDecision | None:
        return s.scalars(
            select(ReviewDecision)
            .where(ReviewDecision.platform_revision_id == pr.id)
            .where(ReviewDecision.decision == "approve")
            .order_by(ReviewDecision.decided_at.desc())
        ).first()

    @staticmethod
    def _declared_dict(pub: Publication) -> dict:
        return {
            "platform": pub.platform,
            "link": pub.link,
            "platform_post_id": pub.platform_post_id,
            "published_at": _aware(pub.published_at).isoformat() if pub.published_at else None,
            "declaration_source": pub.declaration_source,
            "registered_by": pub.registered_by,
        }

    def _row_dict(self, s: Session, pub: Publication) -> dict:
        pr = s.get(PlatformRevision, pub.platform_revision_id)
        cr_id = None
        content_id = None
        if pr is not None:
            cr_id = pr.content_revision_id
            from ..models.entities import ContentRevision
            cr = s.get(ContentRevision, cr_id)
            content_id = cr.content_id if cr else None

        # 过期检测：登记时记下的 manifest 与当前稿是否还一致
        stale = bool(pr is not None and pub.published_manifest_hash
                     and pr.manifest_hash != pub.published_manifest_hash)

        return {
            "id": pub.id,
            "platform": pub.platform,
            "platform_revision_id": pub.platform_revision_id,
            "content_id": content_id,
            "content_revision_id": cr_id,
            "link": pub.link,
            "platform_post_id": pub.platform_post_id,
            "published_at": _aware(pub.published_at).isoformat() if pub.published_at else None,
            "published_manifest_hash": pub.published_manifest_hash,
            "declared": self._declared_dict(pub),
            "verified": (
                None if pub.verified_state is None else {
                    "state": pub.verified_state,
                    "at": _aware(pub.verified_at).isoformat() if pub.verified_at else None,
                    "by": pub.verified_by,
                    "note": pub.verified_note,
                }
            ),
            "status": pub.status,
            "note": pub.note,
            "run_mode": pub.run_mode,
            "revision_changed_since_publish": stale,
            "created_at": _aware(pub.created_at).isoformat() if pub.created_at else None,
        }

    # ------------------------------------------------------------ 统计（供复盘）

    def publication_age_hours(self, publication_id: str,
                              *, now: datetime | None = None) -> float | None:
        """距发布过去多少小时。**没有发布时间就是 null，不猜。**"""
        with self.sf() as s:
            pub = s.get(Publication, publication_id)
            if pub is None or pub.published_at is None:
                return None
            ref = now or _now()
            delta = ref - _aware(pub.published_at)
            return round(delta.total_seconds() / 3600.0, 2)


def observation_windows(published_at: datetime | None,
                        *, marks_hours=(24, 72, 168)) -> dict:
    """发布后 24h / 72h / 7d 的观察窗口（04 文档要求的标准口径）。

    返回每个窗口的"是否已到点"。**没到点就是没到点**，
    不能用模拟数据填上——这是 04 文档明确禁止的。
    """
    if published_at is None:
        return {"available": False, "reason": "没有发布时间，无法判定观察窗口"}
    ref = _now()
    base = _aware(published_at)
    out: dict[str, dict] = {"available": True, "marks": {}}
    for h in marks_hours:
        due = base + timedelta(hours=h)
        out["marks"][f"{h}h"] = {
            "due_at": due.isoformat(),
            "reached": ref >= due,
            "hours_left": None if ref >= due else round((due - ref).total_seconds() / 3600.0, 2),
        }
    return out
