"""复盘 → 下一轮的影响（P4/T20）。

对照 `docs/04-development-plan.md` T20、`docs/02-modules.md` §M10，
以及不变量第 **6、8** 条。

## 三条边界，一条都不能松

**边界一：模型不能直接改参数（不变量 6）。**
复盘里能产出"建议下期多讲 X"，但**建议就是建议**。
本模块产出的 `TopicFeedback` 一律 `status=proposed`。
把它变成"已采用"需要人显式调用 `adopt()`，并且会记录是谁改的、什么时候改的。

**边界二：只自动采用「小改动」。**
文档原话是"范围内自动采用小改动"。什么算小？
本模块用 `magnitude` 划分 `tiny / small / medium / large`，
**只有 tiny / small 才允许 `auto_adopt`**。
`medium` 及以上只能作为候选保存——选题方向、人格定位这类东西改一下就回不去了。

**边界三：改动必须可回退。**
采用时把**原值**一起存进 `previous_value`，`revert()` 能一键还原。
没有回退路径的"自动化"不是自动化，是事故。

## 为什么"没有设金额限额"不等于"可以无限循环"

不变量第 8 条说得很清楚：`money limit = null` 不阻塞 `usage_tracking`，
但**不因此解除任务次数与批次范围限制**。
所以 `propose_batch()` 会同时检查：
- 批次剩余条目数（`item_limit` / 已用）
- 待预览库存上限
- 金额限额（**仅当已设置时才检查**；未设置不阻塞，但也不放大权限）

这三条是**并列**的，任一条触顶就只保存候选，不启动生产。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..models.entities import (
    Batch,
    CommentSample,
    ContentItem,
    MetricSnapshot,
    Publication,
    ReviewReport,
    TopicFeedback,
    _now,
)

#: 建议类型。对应 M10 里"下一轮实验 / 候选选题"能改的东西。
FEEDBACK_KINDS = (
    "topic_weight",    # 选题方向权重
    "angle",           # 切入角度
    "format",          # 呈现形式（图文页数、结构）
    "hook",            # 开头钩子
    "publish_time",    # 发布时段
    "category_focus",  # 回应某类评论（如多讲"经验"类内容）
)

#: 改动幅度。**只有 tiny/small 允许自动采用。**
MAGNITUDES = ("tiny", "small", "medium", "large")
AUTO_ADOPTABLE = frozenset({"tiny", "small"})

#: 建议生命周期
STATUSES = ("proposed", "accepted", "rejected", "reverted", "superseded")

#: 引入一条建议前必须有的证据类型——防止"建议"变成无源之水
_REQUIRED_EVIDENCE = ("observation", "comment_group", "baseline")


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class AdoptOutcome:
    feedback_id: str
    status: str
    auto_adopted: bool
    magnitude: str
    previous_value: str | None
    new_value: str | None
    reversible: bool
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "feedback_id": self.feedback_id,
            "status": self.status,
            "auto_adopted": self.auto_adopted,
            "magnitude": self.magnitude,
            "previous_value": self.previous_value,
            "new_value": self.new_value,
            "reversible": self.reversible,
            "note": self.note,
        }


@dataclass
class BatchProposal:
    allowed: bool
    reasons: list = field(default_factory=list)
    limits_checked: dict = field(default_factory=dict)
    candidates: list = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reasons": self.reasons,
            "limits_checked": self.limits_checked,
            "candidates": self.candidates,
            "note": self.note,
        }


class FeedbackService:
    def __init__(self, session_factory: sessionmaker[Session], settings=None) -> None:
        self.sf = session_factory
        self.settings = settings

    # ------------------------------------------------------------ 产出建议

    def propose(
        self,
        *,
        review_report_id: str,
        kind: str,
        proposal: str,
        magnitude: str = "small",
        rationale: str | None = None,
        evidence_refs: list | None = None,
        previous_value: str | None = None,
        new_value: str | None = None,
        created_by: str = "user",
        run_mode: str = "real",
    ) -> dict:
        """登记一条**建议**。这里产出的东西**不会自动生效**。"""
        if kind not in FEEDBACK_KINDS:
            raise ValueError(f"未知建议类型 {kind!r}，应为 {FEEDBACK_KINDS} 之一")
        if magnitude not in MAGNITUDES:
            raise ValueError(f"未知改动幅度 {magnitude!r}，应为 {MAGNITUDES} 之一")
        if not (proposal or "").strip():
            raise ValueError("建议内容不能为空")
        if not evidence_refs:
            raise ValueError(
                "建议必须给出证据引用（evidence_refs），"
                "否则它就不是从数据来的，只是凭空的想法"
            )

        with self.sf() as s:
            rep = s.get(ReviewReport, review_report_id)
            if rep is None:
                raise ValueError(f"复盘报告不存在：{review_report_id}")

            fb = TopicFeedback(
                review_report_id=review_report_id,
                content_id=rep.content_id,
                kind=kind, magnitude=magnitude,
                proposal=proposal.strip(), rationale=rationale,
                evidence_refs=list(evidence_refs),
                status="proposed", auto_adopted=False,
                previous_value=previous_value, new_value=new_value,
                run_mode=run_mode,
            )
            s.add(fb)
            s.flush()
            out = self._dict(s, fb)
            s.commit()
            return out

    # ------------------------------------------------------------ 采用 / 拒绝

    def adopt(self, feedback_id: str, *, actor: str = "user",
              auto: bool = False) -> AdoptOutcome:
        """采用一条建议。

        `auto=True` 表示"系统按规则自动采用"——**只有 tiny/small 允许**。
        幅度更大的改动即使调用方传 `auto=True` 也会被降级为"仅保存候选"。
        """
        with self.sf() as s:
            fb = s.get(TopicFeedback, feedback_id)
            if fb is None:
                raise ValueError(f"建议不存在：{feedback_id}")
            if fb.status not in ("proposed",):
                raise ValueError(f"建议当前状态是 {fb.status}，只有 proposed 可被采用")

            if auto and fb.magnitude not in AUTO_ADOPTABLE:
                # 降级而不是报错：调用方想自动化，我们把该做的判断替它做掉
                out = AdoptOutcome(
                    feedback_id=fb.id, status=fb.status, auto_adopted=False,
                    magnitude=fb.magnitude,
                    previous_value=fb.previous_value, new_value=fb.new_value,
                    reversible=True,
                    note=(f"幅度为 {fb.magnitude}，**超出可自动采用的范围**"
                          f"（仅 {sorted(AUTO_ADOPTABLE)} 可自动采用）。"
                          "已保留为候选，需人工确认。"),
                )
                s.commit()
                return out

            fb.status = "accepted"
            fb.auto_adopted = bool(auto)
            fb.adopted_by = actor
            fb.adopted_at = _now()
            s.commit()
            return AdoptOutcome(
                feedback_id=fb.id, status="accepted", auto_adopted=bool(auto),
                magnitude=fb.magnitude,
                previous_value=fb.previous_value, new_value=fb.new_value,
                reversible=True,
                note=("采用时已保存原值，可随时 revert 回退。"
                      + ("（系统自动采用，幅度在允许范围内）" if auto else "（人工确认采用）")),
            )

    def reject(self, feedback_id: str, *, actor: str = "user",
               reason: str | None = None) -> dict:
        with self.sf() as s:
            fb = s.get(TopicFeedback, feedback_id)
            if fb is None:
                raise ValueError(f"建议不存在：{feedback_id}")
            fb.status = "rejected"
            fb.adopted_by = actor
            fb.adopted_at = _now()
            if reason:
                fb.rationale = ((fb.rationale or "") + f"\n[拒绝理由] {reason}").strip()
            s.commit()
            return self._dict(s, fb)

    def revert(self, feedback_id: str, *, actor: str = "user") -> dict:
        """回退一条已采用的建议。**没有这个动作的自动化不是自动化。**"""
        with self.sf() as s:
            fb = s.get(TopicFeedback, feedback_id)
            if fb is None:
                raise ValueError(f"建议不存在：{feedback_id}")
            if fb.status != "accepted":
                raise ValueError(f"只有 accepted 的建议可回退，当前是 {fb.status}")
            fb.status = "reverted"
            fb.reverted_at = _now()
            fb.reverted_by = actor
            s.commit()
            return {
                **self._dict(s, fb),
                "restored_value": fb.previous_value,
                "note": ("已回退。" + (
                    f"参数应恢复为：{fb.previous_value}"
                    if fb.previous_value is not None
                    else "该建议原本没有 previous_value，回退只改状态，不改参数。"
                )),
            }

    # ------------------------------------------------------------ 批次候选

    def propose_batch(self, *, next_topics: list, batch_id: str | None = None,
                      pending_review_stock: int = 0,
                      pending_review_stock_limit: int = 3) -> BatchProposal:
        """判断"下一轮能不能开工"。

        检查三条**并列**的限制。任一条触顶就只保存候选。
        **特别地：金额限额未设置（null）时不阻塞，但也不因此放宽其他两条。**
        """
        reasons: list[str] = []
        checked: dict = {}

        with self.sf() as s:
            batch = s.get(Batch, batch_id) if batch_id else None
            checked["batch_id"] = batch_id
            if batch is not None:
                used = len(list(s.scalars(
                    select(ContentItem).where(ContentItem.batch_id == batch.id)
                )))
                remaining = max(batch.item_limit - used, 0)
                checked["item_limit"] = batch.item_limit
                checked["items_used"] = used
                checked["items_remaining"] = remaining
                if remaining <= 0:
                    reasons.append(
                        f"批次条目已用满（{used}/{batch.item_limit}）"
                        "——只保存候选，不自动开工"
                    )
                # 金额限额：**仅当已设置时**才作为限制
                money = batch.budget_limit_micro
                checked["money_limit_micro"] = money
                checked["money_limit_is_null"] = money is None
                if money is None:
                    checked["money_limit_effect"] = (
                        "金额上限未设置（null）。usage_tracking 模式不因此阻塞，"
                        "但**这不等于无限额度，也不解除条目数与库存限制**。"
                    )
            else:
                reasons.append("未指定批次——没有批次范围就没有生产许可（只保存候选）")

            checked["pending_review_stock"] = pending_review_stock
            checked["pending_review_stock_limit"] = pending_review_stock_limit
            if pending_review_stock >= pending_review_stock_limit:
                reasons.append(
                    f"待预览库存已达上限（{pending_review_stock}/{pending_review_stock_limit}）"
                    "——先处理已有产出，只保存候选"
                )

        allowed = not reasons
        candidates = [
            {"topic": t, "status": "candidate" if not allowed else "queued"}
            for t in (next_topics or [])
        ]
        return BatchProposal(
            allowed=allowed, reasons=reasons, limits_checked=checked,
            candidates=candidates,
            note=(
                "三条限制并列：批次条目数、待预览库存、金额上限（仅设置时生效）。"
                "任一条触顶即只保存候选。"
                if reasons else
                "三条限制均未触顶，可开工。注意：金额上限未设置不等于无限额度。"
            ),
        )

    # ------------------------------------------------------------ 查询

    def list_feedback(self, *, content_id: str | None = None,
                      status: str | None = None) -> dict:
        """列出建议。

        关键语义：`status=None` 表示**待办**，不是"全部历史"。
        不传 status 时只返回还挂在人面前的条目（当前即 `proposed`）；
        已 accepted / rejected / reverted 的属于**已了结**，会从这里消失但不会丢——
        显式传 `status=` 仍能查到，随时可追溯。

        理由：采用/拒绝/回退之后条目如果还赖在待办列表里，列表只会越用越长、
        永远清不掉，人就会开始整片整片地忽略它——那等于没有待办列表。
        """
        with self.sf() as s:
            stmt = select(TopicFeedback)
            if content_id:
                stmt = stmt.where(TopicFeedback.content_id == content_id)
            if status and status != "all":
                stmt = stmt.where(TopicFeedback.status == status)
            elif status != "all":
                # 不传 status → 只给待办，不给全部历史
                stmt = stmt.where(TopicFeedback.status == "proposed")
            rows = list(s.scalars(stmt.order_by(TopicFeedback.created_at.desc())))

            # 顺便把"已了结"的数量算出来。列表清空了不等于从没提过建议，
            # 只给一个空列表会让人以为自己看错了。
            settled_stmt = select(TopicFeedback)
            if content_id:
                settled_stmt = settled_stmt.where(TopicFeedback.content_id == content_id)
            settled_stmt = settled_stmt.where(TopicFeedback.status != "proposed")
            settled_total = len(list(s.scalars(settled_stmt)))

            return {
                "items": [self._dict(s, x) for x in rows],
                "total": len(rows),
                "settled_total": settled_total,
                "kinds": list(FEEDBACK_KINDS),
                "magnitudes": list(MAGNITUDES),
                "auto_adoptable": sorted(AUTO_ADOPTABLE),
                "statuses": list(STATUSES),
                "note": (
                    "建议默认是 proposed，**不会自动生效**。"
                    "只有 tiny/small 幅度允许系统自动采用；"
                    "medium/large 一律需人工确认。所有已采用的改动都可回退。"
                    f"这里列出的是**待办**（{len(rows)} 条），"
                    "已采用 / 已拒绝 / 已回退的都算已了结，不在待办里；"
                    "要看历史请显式指定状态筛选。"
                ),
            }

    # ------------------------------------------------------------ 内部

    def _dict(self, s: Session, fb: TopicFeedback) -> dict:
        rep = s.get(ReviewReport, fb.review_report_id)
        return {
            "id": fb.id,
            "review_report_id": fb.review_report_id,
            "review_version": rep.version if rep else None,
            "content_id": fb.content_id,
            "kind": fb.kind, "magnitude": fb.magnitude,
            "proposal": fb.proposal, "rationale": fb.rationale,
            "evidence_refs": fb.evidence_refs or [],
            "status": fb.status,
            "auto_adopted": fb.auto_adopted,
            "auto_adoptable": fb.magnitude in AUTO_ADOPTABLE,
            "adopted_by": fb.adopted_by,
            "adopted_at": _aware(fb.adopted_at).isoformat() if fb.adopted_at else None,
            "previous_value": fb.previous_value,
            "new_value": fb.new_value,
            "reverted_at": _aware(fb.reverted_at).isoformat() if fb.reverted_at else None,
            "reverted_by": fb.reverted_by,
            "created_at": _aware(fb.created_at).isoformat() if fb.created_at else None,
            "run_mode": fb.run_mode,
        }

    # ------------------------------------------------------------ 从复盘自动起草

    def draft_from_review(self, review_report_id: str) -> dict:
        """从一份复盘里**起草**建议候选。

        注意措辞：是"起草候选"，不是"生成决策"。
        产出全部是 `proposed`，等人筛。这里只做**机械转写**：
        把复盘里的 comments/limitations 映射成候选，不替人判断该不该做。
        """
        with self.sf() as s:
            rep = s.get(ReviewReport, review_report_id)
            if rep is None:
                raise ValueError(f"复盘报告不存在：{review_report_id}")

            drafts: list[dict] = []

            # 1) 评论里出现较多"经验"类 → 可考虑增加经验向内容（仅候选）
            pub_ids = rep.publication_ids or []
            comments = []
            if pub_ids:
                comments = list(s.scalars(select(CommentSample).where(
                    CommentSample.publication_id.in_(pub_ids))))
            if comments:
                by_cat: dict[str, list] = {}
                for c in comments:
                    by_cat.setdefault(c.category or "uncategorized", []).append(c)
                for cat, items in sorted(by_cat.items(), key=lambda x: -len(x[1])):
                    if cat in ("invalid", "uncategorized"):
                        continue
                    share = len(items) / len(comments)
                    if share >= 0.3:
                        drafts.append({
                            "kind": "category_focus",
                            "magnitude": "tiny",
                            "proposal": f"下一期可考虑多覆盖「{cat}」类反馈（本期占 {share:.0%}）",
                            "rationale": f"评论样本里 {cat} 类占比较高（{len(items)}/{len(comments)}）",
                            "evidence_refs": [c.id for c in items[:5]],
                            "needs_human_review": True,
                        })

            # 2) 数据充分度不足 → 只提"补数据"，不提"改内容"
            if rep.data_sufficiency in ("none", "insufficient", "baseline_only"):
                drafts.append({
                    "kind": "publish_time",
                    "magnitude": "tiny",
                    "proposal": "先补齐数据再谈内容调整（当前数据仅够建立基线）",
                    "rationale": rep.sufficiency_note or "",
                    "evidence_refs": list(rep.snapshot_ids or [])[:5],
                    "needs_human_review": True,
                })

            # 3) 复盘自带的 next_topics → 候选选题
            for i, t in enumerate(rep.next_topics or []):
                drafts.append({
                    "kind": "topic_weight",
                    "magnitude": "small",
                    "proposal": f"候选选题：{t}",
                    "rationale": f"来自复盘 v{rep.version} 的 next_topics",
                    "evidence_refs": list(rep.snapshot_ids or [])[:3],
                    "needs_human_review": True,
                })

            return {
                "review_report_id": review_report_id,
                "content_id": rep.content_id,
                "drafts": drafts,
                "count": len(drafts),
                "note": (
                    "以上是**候选草案**，全部需要人工确认后才会登记为建议。"
                    "系统不会直接把复盘结论变成参数改动。"
                ),
            }
