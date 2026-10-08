"""评论导入与聚类（P4/T18）。

对照 `docs/04-development-plan.md` T18、`docs/02-modules.md` §M10、
`docs/03-data-and-api.md` §7，以及不变量第 5 条。

## 三条底线

**1. 去标识，但不销毁原文。**
`anon_id` 存的是平台 ID 或原始用户名的**哈希**，不存公开用户名——
文档原话是"公开用户名不为分析目的强制收集"。
但评论**正文一字不改**保留：改过的评论就不是证据了。

**2. 聚类是「归纳」，不是「原文」。**
系统给一条评论打 `category`（问题/质疑/经验/需求/无效），
这是**系统的判断**，可能错。所以每个分类都必须：
- 能回溯到具体评论原文（`comment_ids`）
- 说明判断理由（`category_reason`）
- 给出样本数与取样偏差

如果只输出"30% 是质疑"，人没法质疑这个结论；给出原文例子，人才能说"这条你分错了"。

**3. 分类靠规则，不靠模型瞎猜。**
首版用**可读的关键词规则**，每条规则写清楚触发词。
这么做不是因为规则更准，而是因为**规则能被检查和修改**——
等拿到真实评论样本再考虑上模型，而不是现在先编一个"看起来很像"的分类器。
`generation_mode` 会如实标注是 `rule_based` 还是 `model_assisted`。

## 取样偏差必须显式说明

"我导了 50 条评论"和"我导了全部 1200 条"得出的结论强度完全不同。
`sampling_method` 是必填项，聚类结果里会把取样口径和样本量一起返回，
**不允许只给百分比不给分母**。
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..models.entities import CommentSample, ImportBatch, Publication, _now
from .import_service import (
    ImportError_,
    ImportOutcome,
    ParseResult,
    ParsedRow,
    json_safe,
    parse_time,
)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


#: 评论模板字段（03 文档 §7）
COMMENT_FIELDS = (
    "platform", "post_id", "url", "anonymous_comment_id",
    "text", "observed_at", "sampling_method", "like_count",
)

#: 分类维度（02 文档 M10 明确列出）
CATEGORIES = ("question", "challenge", "experience", "request", "invalid", "uncategorized")

#: 分类的中文说明，用于前端展示
CATEGORY_LABELS = {
    "question": "问题",
    "challenge": "质疑",
    "experience": "经验",
    "request": "需求",
    "invalid": "无效",
    "uncategorized": "未归类",
}

#: 规则式分类器。**每条规则都要能被读懂、被质疑、被修改。**
#: 顺序有意义：先判无效，再判质疑（"假的吧"要先于"是不是"），最后才是问题。
CATEGORY_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("invalid", (
        "纯表情", "打卡", "已阅", "顶", "沙发", "路过", "哈哈哈",
    ), "命中无信息量的社交性短评（打卡/顶/纯情绪），不进入分析"),
    ("challenge", (
        "假的", "骗人", "不靠谱", "胡说", "吹吧", "真的假的", "不信",
        "误导", "标题党", "营销号", "收钱", "恰饭", "翻车",
    ), "命中质疑/反驳类用语"),
    ("question", (
        "怎么", "如何", "为什么", "在哪", "哪儿", "多少钱", "求", "请问",
        "能不能", "可不可以", "有推荐", "什么牌子", "哪款", "? ", "？",
    ), "命中疑问句式或求教用语"),
    ("request", (
        "求更新", "出教程", "希望", "想要", "能不能做一期", "多来点",
        "催更", "求链接", "蹲一个",
    ), "命中明确的内容需求"),
    ("experience", (
        "我用过", "我也", "我试过", "亲测", "我买过", "我做了",
        "我家", "我这边", "分享下", "补充一下", "实际上",
    ), "命中个人经验分享"),
)

#: 短于这个长度且无上述特征的，归为无效（"好"、"赞"、"？"）
_MIN_MEANINGFUL_CHARS = 4


def classify(text: str) -> tuple[str, str]:
    """规则式分类。返回 (category, reason)。

    **不要在这里塞模型调用。** 规则可读、可测、可改；
    等有真实样本再谈模型，别用一个测不了的黑盒替代一个能读懂的规则。
    """
    t = (text or "").strip()
    if not t:
        return "invalid", "空评论"

    for cat, kws, reason in CATEGORY_RULES:
        for kw in kws:
            if kw in t:
                # 纯粹的情绪短评即使命中"哈哈哈"也不该算经验
                return cat, f"{reason}（触发词：{kw!r}）"

    # 去掉标点后过短 → 无信息量
    stripped = re.sub(r"[\s\W_]+", "", t, flags=re.UNICODE)
    if len(stripped) < _MIN_MEANINGFUL_CHARS:
        return "invalid", f"去掉标点后仅 {len(stripped)} 字，无分析价值"
    if stripped.isdigit():
        return "invalid", "纯数字，无分析价值"

    return "uncategorized", "未命中任何已知模式，保持未归类（不强行塞进某一类）"


def anonymize(seed: str) -> str:
    """匿名化。**只保留可稳定去重的哈希，不保留任何身份线索。**"""
    return "anon_" + hashlib.sha256(("cwb::" + seed).encode("utf-8")).hexdigest()[:24]


class CommentImportService:
    def __init__(self, session_factory: sessionmaker[Session], settings=None) -> None:
        self.sf = session_factory
        self.settings = settings

    # ------------------------------------------------------------ 导入

    def import_comments(
        self,
        text: str,
        *,
        file_name: str | None = None,
        fmt: str = "csv",
        sampling_method: str | None = None,
        sample_context: str | None = None,
        created_by: str = "user",
        run_mode: str = "fixture",
    ) -> ImportOutcome:
        """导入评论。**取样方式必填**——没有取样口径的分布数字是没法解释的。"""
        file_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self.sf() as s:
            prev = s.scalars(
                select(ImportBatch)
                .where(ImportBatch.kind == "comments")
                .where(ImportBatch.file_hash == file_hash)
                .where(ImportBatch.mapping_version == "v1")
            ).first()
            if prev is not None:
                return ImportOutcome(
                    import_id=prev.id, kind="comments", state=prev.state,
                    total_rows=prev.total_rows, accepted_rows=prev.accepted_rows,
                    error_rows=prev.error_rows, duplicate_rows=prev.duplicate_rows,
                    unmatched_rows=prev.unmatched_rows, idempotent_replay=True,
                    parse={}, unmatched=[],
                    note=("这个评论文件（sha256 相同）已用同一映射导入过，"
                          "本次未重复写入。"),
                )

        parsed = self._parse(text, fmt=fmt)
        if not sampling_method:
            sampling_method = self._guess_sampling(parsed.total)

        with self.sf() as s:
            batch = ImportBatch(
                kind="comments", file_hash=file_hash, file_name=file_name, format=fmt,
                mapping_version="v1", state="parsed", mapping_json=parsed.mapping,
                total_rows=parsed.total, created_by=created_by, run_mode=run_mode,
            )
            s.add(batch)
            s.flush()

            accepted = 0
            errs = 0
            dup = 0
            unmatched: list[dict] = []

            pubs = list(s.scalars(select(Publication)))
            by_post = {(p.platform, p.platform_post_id): p for p in pubs if p.platform_post_id}
            by_link = {p.link: p for p in pubs if p.link}

            for row in parsed.rows:
                if not row.ok:
                    errs += 1
                    continue
                n = row.normalized
                pub = self._match(n, by_post, by_link)
                if pub is None:
                    unmatched.append({
                        "row": row.row_no, "post_id": n.get("post_id"),
                        "url": n.get("url"), "anon_id": n.get("anon_id"),
                        "reason": "找不到对应的发布记录，已放入待匹配，未做任何猜测绑定",
                    })
                    continue
                if self._exists(s, pub.id, n["anon_id"]):
                    dup += 1
                    continue
                cat, reason = classify(n["text"])
                s.add(CommentSample(
                    publication_id=pub.id, import_id=batch.id,
                    anon_id=n["anon_id"], text=n["text"],
                    observed_at=n.get("observed_at"),
                    sampling_method=sampling_method,
                    sample_context=sample_context,
                    category=cat, category_reason=reason,
                    like_count=n.get("like_count"),
                    run_mode=run_mode,
                ))
                accepted += 1

            batch.accepted_rows = accepted
            batch.error_rows = errs
            batch.duplicate_rows = dup
            batch.unmatched_rows = len(unmatched)
            batch.errors_json = {
                "parse_errors": [
                    {"row": r.row_no, "errors": r.errors} for r in parsed.error_rows[:100]
                ],
                "unmatched": unmatched[:100],
                "sampling_method": sampling_method,
            }
            batch.state = "imported"
            iid = batch.id
            s.commit()

        return ImportOutcome(
            import_id=iid, kind="comments", state="imported",
            total_rows=parsed.total, accepted_rows=accepted, error_rows=errs,
            duplicate_rows=dup, unmatched_rows=len(unmatched),
            parse=parsed.as_dict(), unmatched=unmatched[:100],
            note=(
                f"取样口径：{sampling_method or '未声明'}。"
                "评论按规则分类（rule_based），每条都保留原文与判断理由；"
                "分类可能出错，可用原文核对。去标识只保留哈希，不存公开用户名。"
            ),
        )

    # ------------------------------------------------------------ 聚类

    def cluster(self, *, publication_id: str | None = None,
                import_id: str | None = None) -> dict:
        """按主题聚合评论。**输出的是「归纳」，每类都带原文例证。**"""
        with self.sf() as s:
            stmt = select(CommentSample)
            if publication_id:
                stmt = stmt.where(CommentSample.publication_id == publication_id)
            if import_id:
                stmt = stmt.where(CommentSample.import_id == import_id)
            rows = list(s.scalars(stmt.order_by(CommentSample.created_at.asc())))

            total = len(rows)
            buckets: dict[str, list[CommentSample]] = {}
            for c in rows:
                buckets.setdefault(c.category or "uncategorized", []).append(c)

            sampling = sorted({c.sampling_method for c in rows if c.sampling_method})
            platforms = set()
            for c in rows:
                pub = s.get(Publication, c.publication_id)
                if pub:
                    platforms.add(pub.platform)

            groups = []
            for cat in CATEGORIES:
                items = buckets.get(cat) or []
                if not items:
                    continue
                # 例证优先取点赞高的，但**保留原始顺序信息**（created_at）
                ordered = sorted(items, key=lambda x: (x.like_count is None, -(x.like_count or 0)))
                groups.append({
                    "category": cat,
                    "label": CATEGORY_LABELS[cat],
                    "count": len(items),
                    # 分母必须一起给——只给百分比不给样本量是耍流氓
                    "share": round(len(items) / total, 4) if total else None,
                    "of_total": total,
                    "examples": [
                        {
                            "comment_id": x.id,
                            "anon_id": x.anon_id,
                            "text": x.text,                       # 原文，一字不改
                            "like_count": x.like_count,
                            "observed_at": _aware(x.observed_at).isoformat() if x.observed_at else None,
                            "category_reason": x.category_reason,
                        }
                        for x in ordered[:5]
                    ],
                    "examples_truncated": len(items) > 5,
                })

            return {
                "publication_id": publication_id,
                "import_id": import_id,
                "total_comments": total,
                "platforms": sorted(platforms),
                "sampling_methods": sampling,
                "sampling_bias_note": self._bias_note(sampling, total),
                "groups": groups,
                "generation_mode": "rule_based",
                "generation_note": (
                    "分类由可读的关键词规则产生（rule_based），**不是模型推断**。"
                    "每类都给出原文例证与判断理由，便于人工核对纠正。"
                ),
                "caveats": [
                    "分类是系统的判断，可能出错；请用原文例证核对。",
                    "比例数字的分母是**本次导入的样本量**，不是全部评论数。",
                    "取样方式不同（全量导出 / 前 N 条 / 时间窗）之间不可直接比较。",
                ],
            }

    @staticmethod
    def _bias_note(sampling: list[str], total: int) -> str:
        if total == 0:
            return "没有评论样本。**空样本不能得出任何分布结论**——无数据不是「没有问题」。"
        if not sampling:
            return "未声明取样方式。分布数字仅供参考，不确定其代表性。"
        joined = "、".join(sampling)
        if any(k in joined for k in ("全部", "全量", "all")):
            return f"取样方式：{joined}。若是全量导出，代表性最好；仍受平台展示逻辑影响。"
        return (
            f"取样方式：{joined}，样本量 {total}。"
            "**这是样本不是全体**：靠前评论、热门评论更容易被导出，"
            "分布可能偏向表达欲强的用户。"
        )

    # ------------------------------------------------------------ 内部

    def _parse(self, text: str, *, fmt: str) -> ParseResult:
        res = ParseResult()
        if fmt == "json":
            import json as _json
            data = _json.loads(text)
            if not isinstance(data, list):
                raise ImportError_("BAD_JSON_SHAPE", "评论 JSON 导入需要是数组")
            header = sorted({k for r in data for k in r.keys()}) if data else []
            rows_raw = data
        else:
            text = text.lstrip("\ufeff")
            reader = csv.DictReader(io.StringIO(text))
            header = list(reader.fieldnames or [])
            rows_raw = [dict(r) for r in reader]

        res.header = header
        res.mapping, res.unmapped_columns = self._map_header(header)

        if "text" not in res.mapping.values():
            raise ImportError_(
                "NO_COMMENT_TEXT_COLUMN",
                "没有找到评论正文列。需要 text / 评论内容 / 评论 其中之一。",
                fields={"header": header},
            )

        for idx, raw in enumerate(rows_raw, start=1):
            res.rows.append(self._parse_row(idx, raw, res.mapping))
        res.total = len(res.rows)
        return res

    @staticmethod
    def _map_header(header: list[str]) -> tuple[dict, list[str]]:
        aliases = {
            "platform": "platform", "平台": "platform",
            "post_id": "post_id", "作品id": "post_id", "note_id": "post_id",
            "item_id": "post_id",
            "url": "url", "link": "url", "链接": "url", "作品链接": "url",
            "anonymous_comment_id": "anon_raw", "comment_id": "anon_raw",
            "评论id": "anon_raw", "评论ID": "anon_raw", "用户id": "anon_raw",
            "user_id": "anon_raw", "userid": "anon_raw",
            "text": "text", "comment": "text", "content": "text",
            "评论内容": "text", "评论": "text", "内容": "text",
            "observed_at": "observed_at", "collect_time": "observed_at",
            "采集时间": "observed_at", "评论时间": "observed_at",
            "time": "observed_at",
            "sampling_method": "sampling_method", "取样方式": "sampling_method",
            "like_count": "like_count", "点赞数": "like_count",
            "点赞": "like_count", "likes": "like_count",
        }
        mapping, unmapped = {}, []
        for raw in header:
            key = re.sub(r"[\s_\-]+", "_", (raw or "").strip().lower())
            t = aliases.get(key) or aliases.get((raw or "").strip())
            if t and t not in mapping.values():
                mapping[raw] = t
            else:
                unmapped.append(raw)
        return mapping, unmapped

    @staticmethod
    def _pick(raw: dict, mapping: dict, target: str) -> Any:
        for col, t in mapping.items():
            if t == target:
                return raw.get(col)
        return None

    def _parse_row(self, no: int, raw: dict, mapping: dict) -> ParsedRow:
        row = ParsedRow(row_no=no, raw=raw)
        norm: dict[str, Any] = {}

        text = self._pick(raw, mapping, "text")
        body = (str(text).strip() if text is not None else "")
        if not body:
            row.errors.append("评论正文为空")
        norm["text"] = body

        raw_id = self._pick(raw, mapping, "anon_raw")
        seed = str(raw_id).strip() if raw_id not in (None, "") else body
        # 有平台 ID 就用 ID 去重；没有就用正文（同一作品下重复文案视为同一条）
        post = self._pick(raw, mapping, "post_id") or self._pick(raw, mapping, "url")
        norm["anon_id"] = anonymize(f"{post}|{seed}")

        obs, terr = parse_time(self._pick(raw, mapping, "observed_at"))
        row.errors.extend(f"observed_at: {e}" for e in terr)
        # 评论时间缺失**不报错**——很多导出不带时间，缺就是缺
        norm["observed_at"] = obs

        lk = self._pick(raw, mapping, "like_count")
        like_val = None
        if lk not in (None, ""):
            try:
                like_val = int(float(str(lk).strip()))
            except ValueError:
                row.errors.extend([f"like_count 无法解析：{lk!r}"])
        norm["like_count"] = like_val      # None = 缺失，不是 0

        norm["platform"] = (self._pick(raw, mapping, "platform") or "").strip() or None
        norm["post_id"] = (self._pick(raw, mapping, "post_id") or "").strip() or None
        norm["url"] = (self._pick(raw, mapping, "url") or "").strip() or None
        norm["sampling_method"] = self._pick(raw, mapping, "sampling_method")

        if not norm["post_id"] and not norm["url"]:
            row.errors.append("既没有 post_id 也没有 url——无法绑定到作品")

        row.normalized = norm
        return row

    @staticmethod
    def _match(n: dict, by_post: dict, by_link: dict) -> Publication | None:
        plat, pid, url = n.get("platform"), n.get("post_id"), n.get("url")
        if pid and plat and (plat, pid) in by_post:
            return by_post[(plat, pid)]
        if url and url in by_link:
            return by_link[url]
        if pid and not plat:
            hits = [p for (pl, q), p in by_post.items() if q == pid]
            if len(hits) == 1:
                return hits[0]
        return None

    @staticmethod
    def _exists(s: Session, pub_id: str, anon_id: str) -> bool:
        return s.scalars(
            select(CommentSample)
            .where(CommentSample.publication_id == pub_id)
            .where(CommentSample.anon_id == anon_id)
        ).first() is not None

    @staticmethod
    def _guess_sampling(total: int) -> str:
        """不声明取样方式时的保守默认。

        **不假装知道是"全量"。** 默认按"未知口径的整份导出"记录，
        并把这个不确定性写进 note，而不是默默当成有代表性。
        """
        return f"未声明（导入 {total} 行，口径不确定）"
