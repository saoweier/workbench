"""复盘（P4/T19）。

对照 `docs/04-development-plan.md` T19、`docs/02-modules.md` §M10，
以及不变量第 **5、7** 条。

## 这个模块的全部难度在"克制"

复盘最容易滑向两个坑：

**坑一：编因果。**
"这条 3 万播放，那条 8 千，因为这条封面更好"——这不是观察，是**故事**。
系统没有任何数据能支撑这个因果（两条作品在选题、时间、账号权重上全都不同）。
本模块强制三栏分离：

| 栏位 | 内容 | 谁产生 | 允许出现的句式 |
|---|---|---|---|
| `observations` | 观察事实 | **程序计算** | "播放量 12000（采集于发布后 24.0h）" |
| `hypotheses` | 可能解释 + **替代解释** | 人/模型 | "可能……；另一种可能是……" |
| `experiments` | 下一轮建议 | 人/模型 | "建议只改封面一个变量" |

**关键在"替代解释"是必填项**：提一个解释就必须同时给至少一个竞争解释。
这条约束会让"我觉得是封面"这种话自己暴露出来——因为它给不出替代解释。

**坑二：数据不足硬撑。**
只有 1 次采集、或者数据没到 24h 窗口，就该说"基线未建立"，
而不是拿一个点画一条趋势线。`data_sufficiency` 就是干这个的：
`none / insufficient / baseline_only / comparable`。

## 不变量第 7 条

复盘按**固定输入快照**生成。`snapshot_ids` / `comment_import_ids` 冻住"这份结论基于哪批数据"。
新数据来了是**新建一份报告**（version+1），不是改旧的——
否则上周看到的结论这周悄悄变了，人会以为是自己记错。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..models.entities import (
    CommentSample,
    ContentItem,
    ContentRevision,
    MetricSnapshot,
    PlatformRevision,
    Publication,
    ReviewReport,
    _now,
)
from .comment_service import CATEGORY_LABELS

#: 数据充分度。**没有可比数据时不允许输出效果结论。**
SUFFICIENCY_LEVELS = ("none", "insufficient", "baseline_only", "comparable")

#: 标准观察窗口（04 文档）。没到点就是没到点。
STANDARD_WINDOWS = (24, 72, 168)

#: 单个窗口至少要有这么多条采集才算建立了基线
MIN_SNAPSHOTS_FOR_BASELINE = 2

#: 可比性要求：发布后时长差在这个比例内，才算"近似发布后时长"
_SIMILAR_AGE_TOLERANCE = 0.35

#: 观察类指标（用于生成 observations 的呈现顺序）
_ORDERED_METRICS = ("views", "impressions", "likes", "comments", "collects", "shares", "follows")

_METRIC_LABELS = {
    "views": "播放量", "impressions": "曝光量", "likes": "点赞数",
    "comments": "评论数", "collects": "收藏数", "shares": "分享数",
    "follows": "涨粉数", "clicks": "点击数", "completion_rate": "完播率",
}


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _label(metric: str) -> str:
    return _METRIC_LABELS.get(metric, metric)


@dataclass
class Observation:
    """一条观察事实。**只陈述数字，不加解释。**"""
    kind: str                     # metric / window / comment_distribution / meta
    statement: str
    value: object = None
    unit: str | None = None
    at_age_hours: float | None = None
    window_kind: str | None = None
    traffic_type: str | None = None
    evidence: list = field(default_factory=list)   # snapshot id / comment id
    caveat: str | None = None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind, "statement": self.statement, "value": self.value,
            "unit": self.unit, "at_age_hours": self.at_age_hours,
            "window_kind": self.window_kind, "traffic_type": self.traffic_type,
            "evidence": self.evidence, "caveat": self.caveat,
        }


class ReviewService:
    def __init__(self, session_factory: sessionmaker[Session], settings=None) -> None:
        self.sf = session_factory
        self.settings = settings

    # ------------------------------------------------------------ 生成

    def generate(
        self,
        content_id: str,
        *,
        created_by: str = "system",
        run_mode: str = "real",
        hypotheses: list | None = None,
        experiments: list | None = None,
        next_topics: list | None = None,
    ) -> dict:
        """为一组**固定快照**生成复盘。

        `hypotheses` / `experiments` 由调用方（人，或经人工确认的模型输出）传入。
        **如果传了 hypothesis 却没给替代解释，会被拒绝**——见 `_check_hypotheses`。
        """
        hyps = list(hypotheses or [])
        exps = list(experiments or [])
        self._check_hypotheses(hyps)

        with self.sf() as s:
            content = s.get(ContentItem, content_id)
            if content is None:
                raise ValueError(f"内容不存在：{content_id}")

            pubs = self._publications_of(s, content_id)
            snapshots = self._snapshots_of(s, [p.id for p in pubs])
            comments = self._comments_of(s, [p.id for p in pubs])

            snapshot_ids = [x.id for x in snapshots]
            import_ids = sorted({x.import_id for x in comments if x.import_id})
            publication_ids = [p.id for p in pubs]

            sufficiency, suff_note = self._assess(snapshots, pubs, comments)
            observations = self._observations(s, pubs, snapshots, comments)
            limitations = self._limitations(snapshots, pubs, comments, sufficiency)

            # 版本：同一内容每次生成 +1，**不覆盖旧版**
            version = (max((r.version for r in s.scalars(
                select(ReviewReport).where(ReviewReport.content_id == content_id)
            )), default=0) + 1)

            report = ReviewReport(
                content_id=content_id, version=version,
                snapshot_ids=snapshot_ids, comment_import_ids=import_ids,
                publication_ids=publication_ids,
                data_sufficiency=sufficiency, sufficiency_note=suff_note,
                observations=[o.as_dict() for o in observations],
                hypotheses=hyps, experiments=exps,
                next_topics=list(next_topics or []),
                limitations=limitations,
                source_chain=self._source_chain(s, pubs, snapshots, comments),
                # 命题是模型/人给的，数字是程序算的——如实标注
                generation_mode="assisted" if (hyps or exps) else "none",
                run_mode=run_mode, created_by=created_by,
            )
            s.add(report)
            s.flush()
            rid = report.id
            s.commit()

            return self._report_dict(s, s.get(ReviewReport, rid))

    # ------------------------------------------------------------ 充分度

    def _assess(self, snapshots: list, pubs: list,
                comments: list) -> tuple[str, str]:
        if not pubs:
            return "none", (
                "没有发布记录。**没有发布就没有效果可复盘**——"
                "此状态下只能做流程复盘（见 limitations），不能输出任何传播效果结论。"
            )
        if not snapshots:
            return "insufficient", (
                f"有 {len(pubs)} 条发布记录，但**没有任何指标数据**。"
                "可能原因：还没到采集时间，或数据尚未导入。"
                "无数据不等于表现差。"
            )

        # 按 observed_at 去重后的采集点数
        ctxs = {(x.publication_id, x.observed_at) for x in snapshots}
        windows_covered = self._windows_covered(snapshots)

        if len(ctxs) < MIN_SNAPSHOTS_FOR_BASELINE:
            return "baseline_only", (
                f"只有 {len(ctxs)} 次采集。**单个数据点无法构成趋势**——"
                "最多作为基线记录，不能比较涨跌。"
            )
        if len(windows_covered) < 2:
            reached = ", ".join(f"{h}h" for h in STANDARD_WINDOWS if h in windows_covered) or "无"
            return "baseline_only", (
                f"采集点够，但只覆盖 {reached} 这一档发布后时长。"
                "**同一发布后时长的数据才可以比较**；跨窗口比较会得出错误结论。"
            )
        return "comparable", (
            "采集点覆盖 ≥2 档发布后时长，可做**同口径**比较。"
            "注意：相近发布后时长仍不等于严格 A/B，其他变量可能在同时变化。"
        )

    def _windows_covered(self, snapshots: list) -> set:
        out = set()
        for snap in snapshots:
            age = snap.age_hours
            if age is None:
                continue
            for h in STANDARD_WINDOWS:
                if abs(age - h) <= h * 0.25:
                    out.add(h)
        return out

    # ------------------------------------------------------------ 观察

    def _observations(self, s: Session, pubs: list, snapshots: list,
                      comments: list) -> list[Observation]:
        obs: list[Observation] = []

        # --- 元信息：这份复盘基于什么 ---
        obs.append(Observation(
            kind="meta",
            statement=(f"本复盘基于 {len(pubs)} 条发布记录、{len(snapshots)} 条指标快照、"
                       f"{len(comments)} 条评论样本。"),
            value=len(snapshots),
            evidence=[p.id for p in pubs],
            caveat="输入是**生成时刻的快照**；后续新增数据会生成新版本报告，不会改动本版结论。",
        ))

        # --- 每条发布记录的指标：逐条陈述，绝不横向比大小 ---
        for pub in pubs:
            rows = [x for x in snapshots if x.publication_id == pub.id]
            if not rows:
                obs.append(Observation(
                    kind="meta",
                    statement=f"{pub.platform} 这条作品暂无指标数据。",
                    evidence=[pub.id],
                    caveat="无数据不等于零播放。",
                ))
                continue
            # 按"代表性快照"输出。**每个 (口径, 流量类型) 组合都要出现**——
            # 只取一条会让另一种流量类型的数据凭空消失，
            # 而"自然流量多少"恰恰是最该看见的那个数。
            for latest in self._representative_snapshots(rows):
                for metric in _ORDERED_METRICS:
                    cell = (latest.metrics or {}).get(metric)
                    if not cell:
                        continue
                    val = cell.get("value")
                    scope = self._scope_prefix(latest)
                    if val is None:
                        # 缺失也如实陈述——**不能因为缺就跳过，那等于把缺失藏起来**
                        obs.append(Observation(
                            kind="metric",
                            statement=f"{pub.platform} {scope}{_label(metric)}：本次采集缺失",
                            value=None,
                            unit=cell.get("unit"),
                            at_age_hours=latest.age_hours,
                            window_kind=latest.window_kind,
                            traffic_type=latest.traffic_type,
                            evidence=[latest.id],
                            caveat="缺失值不是 0。不要把 null 当作「没有播放」。",
                        ))
                        continue
                    obs.append(Observation(
                        kind="metric",
                        statement=(f"{pub.platform} {scope}{_label(metric)} "
                                   f"{val:g} {cell.get('unit') or ''}".strip()),
                        value=val, unit=cell.get("unit"),
                        at_age_hours=latest.age_hours,
                        window_kind=latest.window_kind,
                        traffic_type=latest.traffic_type,
                        evidence=[latest.id],
                        caveat=self._metric_caveat(latest),
                    ))

        # --- 评论分布 ---
        if comments:
            total = len(comments)
            by_cat: dict[str, int] = {}
            for c in comments:
                by_cat[c.category or "uncategorized"] = by_cat.get(c.category or "uncategorized", 0) + 1
            parts = "、".join(
                f"{CATEGORY_LABELS.get(k, k)} {v} 条（{v/total:.0%}）"
                for k, v in sorted(by_cat.items(), key=lambda x: -x[1])
            )
            samplings = sorted({c.sampling_method for c in comments if c.sampling_method})
            obs.append(Observation(
                kind="comment_distribution",
                statement=f"评论样本共 {total} 条：{parts}。",
                value=total,
                evidence=[c.id for c in comments[:20]],
                caveat=(f"取样方式：{'、'.join(samplings) if samplings else '未声明'}。"
                        "分母是**本次导入的样本量**，不是全部评论数。"),
            ))

        return obs

    def _representative_snapshots(self, rows: list) -> list[MetricSnapshot]:
        """按 (口径, 流量类型) 分组，每组取一个代表快照。

        为什么不是"取一条最新的"：同一时刻的**自然流量和付费流量是两次不同的观测**。
        只取一条会让另一种凭空消失，而"自然流量多少"恰恰是最该看见的数。
        同理，累计值与窗口值也不能互相顶替。
        """
        buckets: dict[tuple, list[MetricSnapshot]] = {}
        for x in rows:
            buckets.setdefault((x.window_kind, x.traffic_type), []).append(x)
        out = []
        for (_w, _t), group in buckets.items():
            # 累计口径优先（累计值信息量最大），同类里取最新
            cum = [x for x in group if x.window_kind == "cumulative"]
            pool = cum or group
            out.append(max(pool, key=lambda x: (_aware(x.observed_at) or _now())))
        # 稳定的输出顺序：累计在前，自然流量在前
        out.sort(key=lambda x: (x.window_kind != "cumulative", x.traffic_type))
        return out

    @staticmethod
    def _scope_prefix(snap: MetricSnapshot) -> str:
        """在陈述里标出这条数字的范围，避免读者把自然流量当成全部。"""
        if snap.traffic_type == "organic":
            return "自然流量："
        if snap.traffic_type == "paid":
            return "付费流量："
        return ""

    def _metric_caveat(self, snap: MetricSnapshot) -> str:
        bits = []
        if snap.window_kind == "window":
            bits.append("这是**窗口值**，不能与累计值直接比较")
        elif snap.window_kind == "cumulative":
            bits.append("这是**累计值**")
        else:
            bits.append("口径未知，无法与其他口径比较")
        if snap.traffic_type == "organic":
            bits.append("仅自然流量")
        elif snap.traffic_type == "paid":
            bits.append("仅付费流量")
        if snap.age_hours is not None:
            bits.append(f"采集于发布后 {snap.age_hours:.1f}h")
        return "；".join(bits) + "。"

    # ------------------------------------------------------------ 局限

    def _limitations(self, snapshots: list, pubs: list, comments: list,
                     sufficiency: str) -> list[str]:
        out: list[str] = []
        if sufficiency == "none":
            out.append("**没有发布记录**：不能输出任何传播效果结论，本报告只能作为流程复盘。")
        if sufficiency == "insufficient":
            out.append("**没有指标数据**：不能得出效果结论；无数据不等于表现差。")
        if sufficiency == "baseline_only":
            out.append("**数据仅够建立基线**：不能比较涨跌，不能推断限流，不能推断算法偏好。")
        if not comments:
            out.append("**没有评论样本**：无法了解受众反应；这不表示「没有负面反馈」。")
        if comments and not any(c.sampling_method and "未声明" not in c.sampling_method
                                for c in comments):
            out.append("**评论取样口径未明确声明**：分布数字的代表性未知。")
        out.append("**不能推断因果**：不同作品的选题、发布时间、账号权重都不同，"
                   "数据差异不能归因于单一改动。")
        out.append("**不承诺涨粉**：本报告不预测、不承诺任何传播效果。")
        if any(s.traffic_type == "paid" for s in snapshots):
            out.append("数据中包含**付费流量**：自然表现需与付费部分分开看。")
        return out

    # ------------------------------------------------------------ 校验

    @staticmethod
    def _check_hypotheses(hyps: list) -> None:
        """提一个解释，就必须给至少一个**竞争解释**。

        这是本模块最重要的一条约束。它会逼着"我觉得是封面"这种话自己暴露出来——
        因为说这话的人给不出替代解释。写不出替代解释，说明手里的是直觉不是假设。
        """
        for i, h in enumerate(hyps, start=1):
            if not isinstance(h, dict):
                raise ValueError(f"第 {i} 条假设格式不对，需要是对象")
            if not h.get("statement"):
                raise ValueError(f"第 {i} 条假设缺少 statement")
            alts = h.get("alternative_explanations") or []
            if not alts:
                raise ValueError(
                    f"第 {i} 条假设缺少 alternative_explanations。"
                    "提一个解释必须同时给出至少一个竞争解释——"
                    "给不出竞争解释，说明手里是直觉而不是假设。"
                )

    # ------------------------------------------------------------ 数据获取

    def _publications_of(self, s: Session, content_id: str) -> list[Publication]:
        crs = list(s.scalars(select(ContentRevision).where(
            ContentRevision.content_id == content_id)))
        cr_ids = [c.id for c in crs]
        if not cr_ids:
            return []
        prs = list(s.scalars(select(PlatformRevision).where(
            PlatformRevision.content_revision_id.in_(cr_ids))))
        pr_ids = [p.id for p in prs]
        if not pr_ids:
            return []
        return list(s.scalars(select(Publication).where(
            Publication.platform_revision_id.in_(pr_ids))))

    def _snapshots_of(self, s: Session, pub_ids: list) -> list[MetricSnapshot]:
        if not pub_ids:
            return []
        return list(s.scalars(select(MetricSnapshot).where(
            MetricSnapshot.publication_id.in_(pub_ids)
        ).order_by(MetricSnapshot.observed_at.asc())))

    def _comments_of(self, s: Session, pub_ids: list) -> list[CommentSample]:
        if not pub_ids:
            return []
        return list(s.scalars(select(CommentSample).where(
            CommentSample.publication_id.in_(pub_ids)
        ).order_by(CommentSample.created_at.asc())))

    def _source_chain(self, s: Session, pubs: list, snapshots: list,
                      comments: list) -> list[dict]:
        """来源链：从结论能一路回溯到原始记录。"""
        chain = []
        for pub in pubs:
            chain.append({
                "level": "publication", "id": pub.id, "platform": pub.platform,
                "link": pub.link, "platform_post_id": pub.platform_post_id,
                "status": pub.status,
                "verified": pub.verified_state,
            })
        for snap in snapshots:
            chain.append({
                "level": "metric_snapshot", "id": snap.id,
                "publication_id": snap.publication_id,
                "observed_at": _aware(snap.observed_at).isoformat() if snap.observed_at else None,
                "age_hours": snap.age_hours, "window_kind": snap.window_kind,
                "traffic_type": snap.traffic_type, "import_id": snap.import_id,
            })
        for c in comments[:50]:
            chain.append({
                "level": "comment_sample", "id": c.id,
                "publication_id": c.publication_id, "anon_id": c.anon_id,
                "category": c.category, "import_id": c.import_id,
            })
        return chain

    # ------------------------------------------------------------ 读取

    def list_reports(self, *, content_id: str | None = None) -> dict:
        with self.sf() as s:
            stmt = select(ReviewReport)
            if content_id:
                stmt = stmt.where(ReviewReport.content_id == content_id)
            rows = list(s.scalars(stmt.order_by(ReviewReport.created_at.desc())))
            return {
                "items": [self._report_dict(s, r, brief=True) for r in rows],
                "total": len(rows),
                "snapshot_rule": (
                    "每份报告绑定生成时刻的输入快照；新增数据会生成新版本，"
                    "不会改动已生成报告的结论（不变量第 7 条）。"
                ),
                "sufficiency_levels": list(SUFFICIENCY_LEVELS),
            }

    def get_report(self, report_id: str) -> dict:
        with self.sf() as s:
            r = s.get(ReviewReport, report_id)
            if r is None:
                raise ValueError(f"复盘报告不存在：{report_id}")
            return self._report_dict(s, r)

    def _report_dict(self, s: Session, r: ReviewReport, *,
                     brief: bool = False) -> dict:
        content = s.get(ContentItem, r.content_id)
        base = {
            "id": r.id, "content_id": r.content_id,
            "display_id": content.display_id if content else None,
            "topic": content.topic if content else None,
            "version": r.version,
            "data_sufficiency": r.data_sufficiency,
            "sufficiency_note": r.sufficiency_note,
            "generation_mode": r.generation_mode,
            "run_mode": r.run_mode,
            "created_at": _aware(r.created_at).isoformat() if r.created_at else None,
            "snapshot_count": len(r.snapshot_ids or []),
            "comment_count": len(r.source_chain or []) if brief else 0,
        }
        if brief:
            base["inputs"] = {
                "snapshot_ids": r.snapshot_ids or [],
                "comment_import_ids": r.comment_import_ids or [],
                "publication_ids": r.publication_ids or [],
            }
            base["observation_count"] = len(r.observations or [])
            base["hypothesis_count"] = len(r.hypotheses or [])
            return base

        base.update({
            "inputs": {
                "snapshot_ids": r.snapshot_ids or [],
                "comment_import_ids": r.comment_import_ids or [],
                "publication_ids": r.publication_ids or [],
                "note": "以上为**固定输入快照**；本报告结论只对这些数据负责。",
            },
            "observations": r.observations or [],
            "hypotheses": r.hypotheses or [],
            "experiments": r.experiments or [],
            "next_topics": r.next_topics or [],
            "limitations": r.limitations or [],
            "source_chain": r.source_chain or [],
            "structure_note": {
                "observations": "程序计算的**观察事实**，不带解释。",
                "hypotheses": "可能解释 + **替代解释**（必填）。只有解释没有替代解释会被拒绝入库。",
                "experiments": "下一轮建议。只改一个主要变量，并记录其他变化。",
                "limitations": "本报告的边界。**没有数据时这里会明说不能得效果结论。**",
            },
        })
        return base
