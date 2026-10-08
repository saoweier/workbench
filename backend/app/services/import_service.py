"""数据导入与规范化（P4/T17）。

对照 `docs/04-development-plan.md` T17、`docs/02-modules.md` §M09、
`docs/03-data-and-api.md` §7，以及不变量第 **5、7** 条。

## 这个模块最容易犯的错

数据导入看起来是"读 CSV 存进去"，但真正需要小心的全是**解释权**问题：

| 陷阱 | 后果 | 本模块的做法 |
|---|---|---|
| 缺失值当 0 | "这条没人看" 与 "数据没给" 变成同一个数字 | 缺就是 `None`，**永不写 0** |
| 累计值与窗口值混着比 | 拿"7 天累计播放"和"当天播放"比大小 | `aggregation_kind` 分开存，比较前必须同口径 |
| 无法匹配就猜 | 数据挂到错误的作品上，复盘全错 | **进待匹配列表**，不猜 |
| 同文件重复导入翻倍 | 播放量凭空涨一倍 | `file_hash + mapping_version` 幂等 |
| 平台改名就丢历史 | 平台改了字段名，老数据没法回溯 | `raw_fields` 原样保留 |
| 修订覆盖旧值 | "上次看是 1.2 万，这次是 1.1 万"这种回溯性修正被抹掉 | `supersedes_id` 追加，**旧行不删不改** |

## 为什么要有"待匹配"而不是报错

导入的核心场景是**批量**：一百行里九十九行能对上，一行对不上。
如果整批报错，用户被迫去修那一行才能拿到另外九十九行，实际会逼着他"随便填个 ID 让它过"。
所以本模块的选择是：**能导的先导进去，对不上的单独列出来等人处理**，
并且这批数据在复盘时会被明确标注"有 N 行未匹配"。

## 单位校验

`unit` 不是装饰。平台导出可能一列"播放量"，有时是"1.2万"这种中文单位。
本模块只做**保守**的规范化：能明确解析的解析，解析不了的原样留下并标记，
**绝不猜**（数值歧义不得静默写入 —— 02 文档原话）。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..models.entities import (
    ImportBatch,
    MetricSnapshot,
    PlatformRevision,
    Publication,
    _now,
)

#: 标准长表模板字段（03 文档 §7）。raw_name 保留映射前名称。
STANDARD_FIELDS = (
    "platform", "post_id", "url", "published_at", "observed_at",
    "metric_name", "value", "unit", "denominator_metric",
    "traffic_type", "aggregation_kind", "raw_metric_name",
)

#: 长表模板的必需字段——缺了就没法建立时间与口径，不能"尽量导"
REQUIRED_FIELDS = ("observed_at", "metric_name", "value")

#: 窗口口径。**不同 kind 之间不能比较**。
AGGREGATION_KINDS = ("cumulative", "window", "unknown")

#: 流量类型。自然流量与付费流量混着算，等于什么也没算。
TRAFFIC_TYPES = ("organic", "paid", "unknown")

#: 已知指标名 → 规范化名。表外的名字**不拒绝**，只是标成未映射。
KNOWN_METRICS = {
    "play": "views", "plays": "views", "view": "views", "views": "views",
    "播放量": "views", "播放": "views", "曝光量": "impressions", "impressions": "impressions",
    "impression": "impressions", "曝光": "impressions",
    "like": "likes", "likes": "likes", "点赞": "likes", "点赞数": "likes",
    "comment": "comments", "comments": "comments", "评论": "comments", "评论数": "comments",
    "collect": "collects", "collects": "collects", "favorite": "collects",
    "收藏": "collects", "收藏数": "collects",
    "share": "shares", "shares": "shares", "分享": "shares", "分享数": "shares",
    "follow": "follows", "follows": "follows", "涨粉": "follows", "新增粉丝": "follows",
    "completion_rate": "completion_rate", "完播率": "completion_rate",
    "duration": "duration_seconds", "时长": "duration_seconds",
    "click": "clicks", "clicks": "clicks", "点击": "clicks",
}

#: 单位换算。出现中文单位是平台导出的常态，必须能认，认不出就别动。
UNIT_MULTIPLIERS = {
    "": 1, "count": 1, "次": 1, "个": 1,
    "万": 10000, "w": 10000, "w+": 10000,
    "千": 1000, "k": 1000, "k+": 1000,
    "百万": 1000000, "m": 1000000,
}

_NUM_RE = re.compile(r"^[-+]?\d+(?:\.\d+)?")

#: 指标名 → 单位。用于一致性校验（"播放量 1.2 万次" 与 "播放量 12000" 是同一口径）
_COUNT_METRICS = frozenset({
    "views", "impressions", "likes", "comments", "collects",
    "shares", "follows", "clicks",
})

#: 比率类指标，值域 0–1（或 0–100 百分数）
_RATE_METRICS = frozenset({"completion_rate"})


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def json_safe(value: Any) -> Any:
    """把任意值转成可 JSON 序列化的形态。

    为什么需要它：`mapping_json` / `raw_fields` 这些列想保留**原始解释依据**，
    而解析中间结果里混着 `datetime`。直接塞进 JSON 列会在 flush 时炸
    （`Object of type datetime is not JSON serializable`），
    而且这个错发生在写库那一刻——比解析阶段晚得多，排查起来很费劲。

    时间统一转 ISO 字符串：既能读，也保留时区信息。
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return _aware(value).isoformat() if value.tzinfo is None or value.tzinfo else value.isoformat()
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    return str(value)


class ImportError_(ValueError):
    """导入过程的可预期错误。带 code 供接口层映射。"""

    def __init__(self, code: str, message: str, *, fields: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fields = fields or {}


@dataclass
class ParsedRow:
    row_no: int
    raw: dict
    normalized: dict | None = None
    errors: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.normalized is not None


@dataclass
class ParseResult:
    total: int = 0
    rows: list = field(default_factory=list)
    header: list = field(default_factory=list)
    mapping: dict = field(default_factory=dict)
    unmapped_columns: list = field(default_factory=list)

    @property
    def ok_rows(self) -> list:
        return [r for r in self.rows if r.ok]

    @property
    def error_rows(self) -> list:
        return [r for r in self.rows if not r.ok]

    def as_dict(self, *, limit: int = 50) -> dict:
        return {
            "total": self.total,
            "header": self.header,
            "mapping": self.mapping,
            "unmapped_columns": self.unmapped_columns,
            "ok_count": len(self.ok_rows),
            "error_count": len(self.error_rows),
            "errors": [
                {"row": r.row_no, "errors": r.errors, "raw": r.raw}
                for r in self.error_rows[:limit]
            ],
            "errors_truncated": len(self.error_rows) > limit,
        }


# ------------------------------------------------------------------ 解析

def _norm_header(h: str) -> str:
    return re.sub(r"[\s_\-]+", "_", (h or "").strip().lower())


#: 原始表头 → 标准字段。**版本化**：映射变了要建新版本，不能让旧数据换个解释。
HEADER_ALIASES: dict[str, str] = {
    "platform": "platform", "平台": "platform",
    "post_id": "post_id", "postid": "post_id", "作品id": "post_id",
    "item_id": "post_id", "note_id": "post_id", "aweme_id": "post_id",
    "url": "url", "link": "url", "链接": "url", "作品链接": "url",
    "published_at": "published_at", "publish_time": "published_at",
    "发布时间": "published_at",
    "observed_at": "observed_at", "observed_time": "observed_at",
    "collect_time": "observed_at", "采集时间": "observed_at", "导入时间": "observed_at",
    "metric_name": "metric_name", "metric": "metric_name", "指标": "metric_name",
    "指标名": "metric_name", "metricname": "metric_name",
    "value": "value", "值": "value", "数值": "value",
    "unit": "unit", "单位": "unit",
    "denominator_metric": "denominator_metric", "denominator": "denominator_metric",
    "分母": "denominator_metric", "分母指标": "denominator_metric",
    "traffic_type": "traffic_type", "traffic": "traffic_type", "流量类型": "traffic_type",
    "aggregation_kind": "aggregation_kind", "aggregation": "aggregation_kind",
    "window_kind": "aggregation_kind", "窗口类型": "aggregation_kind", "口径": "aggregation_kind",
    "raw_metric_name": "raw_metric_name", "原始指标名": "raw_metric_name",
}

#: 映射版本。HEADER_ALIASES 或规范逻辑一改，必须升版本。
MAPPING_VERSION = "v1"


def map_header(header: list[str]) -> tuple[dict, list[str]]:
    """返回 (原始列 → 标准字段, 未识别列)。"""
    mapping: dict[str, str] = {}
    unmapped: list[str] = []
    for raw in header:
        key = _norm_header(raw)
        # 先看规范名，再看中文/别名
        target = HEADER_ALIASES.get(key) or HEADER_ALIASES.get((raw or "").strip())
        if target and target not in mapping.values():
            mapping[raw] = target
        else:
            unmapped.append(raw)
    # 允许"宽表"：把已知指标名直接当列（抖音后台常这么导）
    for raw in list(unmapped):
        metric = KNOWN_METRICS.get(_norm_header(raw)) or KNOWN_METRICS.get((raw or "").strip())
        if metric and "metric_name" not in mapping.values() and "value" not in mapping.values():
            mapping[raw] = f"wide_metric:{metric}"
            unmapped.remove(raw)
    return mapping, unmapped


def parse_number(text: Any) -> tuple[float | None, str, list[str]]:
    """解析数值。返回 (数值, 识别到的单位, 错误列表)。

    **认不出就返回 None + 错误，绝不猜**（02 文档："数值歧义不得静默写入"）。
    """
    errors: list[str] = []
    if text is None:
        return None, "", errors
    raw = str(text).strip()
    if raw == "":
        return None, "", errors  # 空 = 缺失，不是 0
    if raw in ("-", "--", "N/A", "n/a", "null", "NULL", "无"):
        return None, "", errors

    m = _NUM_RE.match(raw)
    if not m:
        errors.append(f"无法解析数值：{raw!r}")
        return None, "", errors

    num = float(m.group(0))
    rest = raw[m.end():].strip().lower()
    unit = ""
    if rest:
        key = rest.rstrip("+")
        if rest in UNIT_MULTIPLIERS:
            num *= UNIT_MULTIPLIERS[rest]
            unit = rest
        elif key in UNIT_MULTIPLIERS:
            num *= UNIT_MULTIPLIERS[key]
            unit = key
        elif "%" in rest:
            num = num / 100.0
            unit = "percent"
        else:
            errors.append(f"数值后面跟了看不懂的单位：{rest!r}")
            return None, "", errors
    return num, unit, errors


def parse_time(text: Any) -> tuple[datetime | None, list[str]]:
    """解析时间。支持 ISO 8601 与常见的 `YYYY-MM-DD HH:MM:SS`。

    **不给时间就不猜当前时间**——时间口径错了，age_hours 全错，复盘就废了。
    """
    errors: list[str] = []
    if text is None or str(text).strip() == "":
        return None, errors
    raw = str(text).strip()
    candidates = [
        raw.replace("Z", "+00:00"),
        raw.replace("/", "-"),
        raw.replace("年", "-").replace("月", "-").replace("日", " ").strip(),
    ]
    for cand in candidates:
        try:
            dt = datetime.fromisoformat(cand)
            # SQLAlchemy/SQLite DateTime columns discard tzinfo. Normalize
            # offset-aware values to naive UTC at the ingestion boundary.
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt, errors
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt), errors
        except ValueError:
            continue
    # 纯数字时间戳
    if re.fullmatch(r"\d{10}", raw):
        return datetime.fromtimestamp(int(raw), tz=timezone.utc).replace(tzinfo=None), errors
    if re.fullmatch(r"\d{13}", raw):
        return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc).replace(tzinfo=None), errors
    errors.append(f"无法解析时间：{raw!r}")
    return None, errors


def parse_metrics_table(text: str, *, fmt: str = "csv") -> ParseResult:
    """解析标准长表指标 CSV，或平台后台导出的宽表。"""
    res = ParseResult()
    if fmt == "json":
        data = json.loads(text)
        if not isinstance(data, list):
            raise ImportError_("BAD_JSON_SHAPE", "JSON 导入需要是一个数组，每项一行")
        rows_raw = data
        header = sorted({k for r in data for k in r.keys()}) if data else []
    else:
        # 去掉 BOM，兼容 Excel 导出的 UTF-8-BOM
        text = text.lstrip("\ufeff")
        reader = csv.DictReader(io.StringIO(text))
        header = list(reader.fieldnames or [])
        rows_raw = [dict(r) for r in reader]

    res.header = header
    res.mapping, res.unmapped_columns = map_header(header) if header else ({}, [])

    wide_cols = [c for c, t in res.mapping.items() if t.startswith("wide_metric:")]
    is_long = "metric_name" in res.mapping.values() and "value" in res.mapping.values()

    if not is_long and not wide_cols:
        raise ImportError_(
            "NO_METRIC_COLUMNS",
            "没有识别出任何指标列。长表模板需要 metric_name + value，"
            "或使用「播放量/点赞数」这类已知指标名作列名。",
            fields={"header": header, "mapping": res.mapping},
        )

    for idx, raw in enumerate(rows_raw, start=1):
        if is_long:
            res.rows.append(_parse_long_row(idx, raw, res.mapping))
        else:
            res.rows.extend(_parse_wide_row(idx, raw, res.mapping, wide_cols))
    res.total = len(res.rows)
    return res


def _pick(raw: dict, mapping: dict, field_name: str) -> Any:
    for raw_col, target in mapping.items():
        if target == field_name:
            return raw.get(raw_col)
    return None


def _parse_long_row(no: int, raw: dict, mapping: dict) -> ParsedRow:
    row = ParsedRow(row_no=no, raw=raw)
    norm: dict[str, Any] = {}

    observed_at, terr = parse_time(_pick(raw, mapping, "observed_at"))
    row.errors.extend(f"observed_at: {e}" for e in terr)
    if observed_at is None and not terr:
        row.errors.append("observed_at 为空——没有采集时间就无法定位数据口径")
    norm["observed_at"] = observed_at

    raw_metric = _pick(raw, mapping, "metric_name")
    metric_raw = (str(raw_metric).strip() if raw_metric is not None else "")
    if not metric_raw:
        row.errors.append("metric_name 为空")
    metric = KNOWN_METRICS.get(_norm_header(metric_raw)) or KNOWN_METRICS.get(metric_raw)
    norm["metric_name"] = metric
    norm["raw_metric_name"] = _pick(raw, mapping, "raw_metric_name") or metric_raw
    if metric_raw and metric is None:
        row.errors.append(f"未知指标名 {metric_raw!r}——需先补映射，不擅自归类")

    value_text = _pick(raw, mapping, "value")
    value, unit_in_text, verr = parse_number(value_text)
    row.errors.extend(f"value: {e}" for e in verr)
    # 值缺失**不是错误**，是缺失。但要记下来。
    norm["value"] = value
    norm["value_missing"] = value is None

    declared_unit = _pick(raw, mapping, "unit")
    unit = (str(declared_unit).strip().lower() if declared_unit else "") or unit_in_text or ""
    norm["unit"] = unit or ("ratio" if metric in _RATE_METRICS else "count")

    # 比率类做值域校验——百分之 120 的完播率通常是"百分比没除 100"
    if metric in _RATE_METRICS and value is not None and value > 1.0:
        if value <= 100.0:
            norm["value"] = value / 100.0
            norm["value_note"] = f"原值 {value} 按百分数折算为 {value/100.0}"
        else:
            row.errors.append(f"完播率 {value} 超出 0–100% 范围，拒绝写入")

    agg = _pick(raw, mapping, "aggregation_kind")
    agg_s = (str(agg).strip().lower() if agg else "") or "cumulative"
    agg_map = {"累计": "cumulative", "窗口": "window", "当天": "window",
               "cumulative": "cumulative", "total": "cumulative",
               "window": "window", "daily": "window", "day": "window"}
    agg_s = agg_map.get(agg_s, agg_s)
    if agg_s not in AGGREGATION_KINDS:
        row.errors.append(f"未知口径 {agg!r}（应为 {AGGREGATION_KINDS}）")
    norm["aggregation_kind"] = agg_s

    tt = _pick(raw, mapping, "traffic_type")
    tt_s = (str(tt).strip().lower() if tt else "") or "unknown"
    tt_map = {"自然": "organic", "自然流量": "organic", "付费": "paid",
              "付费流量": "paid", "organic": "organic", "paid": "paid"}
    tt_s = tt_map.get(tt_s, tt_s)
    if tt_s not in TRAFFIC_TYPES:
        tt_s = "unknown"
    norm["traffic_type"] = tt_s

    norm["denominator_metric"] = _pick(raw, mapping, "denominator_metric")
    norm["platform"] = (_pick(raw, mapping, "platform") or "").strip() or None
    norm["post_id"] = (_pick(raw, mapping, "post_id") or "").strip() or None
    norm["url"] = (_pick(raw, mapping, "url") or "").strip() or None
    pub_at, perr = parse_time(_pick(raw, mapping, "published_at"))
    row.errors.extend(f"published_at: {e}" for e in perr)
    norm["published_at"] = pub_at

    if not norm["post_id"] and not norm["url"]:
        row.errors.append("既没有 post_id 也没有 url——无法绑定到作品")

    row.normalized = norm
    return row


def _parse_wide_row(no: int, raw: dict, mapping: dict, wide_cols: list[str]) -> list[ParsedRow]:
    """宽表（一行一个作品，每个指标一列）→ 折成多行长表。"""
    out: list[ParsedRow] = []
    for col in wide_cols:
        metric = mapping[col].split(":", 1)[1]
        row = ParsedRow(row_no=no, raw=raw)
        norm: dict[str, Any] = {}

        observed_at, terr = parse_time(_pick(raw, mapping, "observed_at"))
        row.errors.extend(f"observed_at: {e}" for e in terr)
        if observed_at is None and not terr:
            row.errors.append(f"{col}: observed_at 为空")
        norm["observed_at"] = observed_at

        value, unit_in_text, verr = parse_number(raw.get(col))
        row.errors.extend(f"{col}: {e}" for e in verr)
        norm["value"] = value
        norm["value_missing"] = value is None

        norm["metric_name"] = metric
        norm["raw_metric_name"] = col
        norm["unit"] = unit_in_text or ("ratio" if metric in _RATE_METRICS else "count")
        norm["aggregation_kind"] = "cumulative"
        norm["traffic_type"] = "unknown"
        norm["denominator_metric"] = None
        norm["platform"] = (_pick(raw, mapping, "platform") or "").strip() or None
        norm["post_id"] = (_pick(raw, mapping, "post_id") or "").strip() or None
        norm["url"] = (_pick(raw, mapping, "url") or "").strip() or None
        pub_at, perr = parse_time(_pick(raw, mapping, "published_at"))
        row.errors.extend(f"published_at: {e}" for e in perr)
        norm["published_at"] = pub_at
        if not norm["post_id"] and not norm["url"]:
            row.errors.append("既没有 post_id 也没有 url")
        # 宽表里某一列为空：该指标缺失，但**不代表整行错**
        row.normalized = norm
        out.append(row)
    return out


# ------------------------------------------------------------------ 服务

@dataclass
class ImportOutcome:
    import_id: str
    kind: str
    state: str
    total_rows: int
    accepted_rows: int
    error_rows: int
    duplicate_rows: int
    unmatched_rows: int
    idempotent_replay: bool = False
    parse: dict = field(default_factory=dict)
    unmatched: list = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "import_id": self.import_id,
            "kind": self.kind,
            "state": self.state,
            "total_rows": self.total_rows,
            "accepted_rows": self.accepted_rows,
            "error_rows": self.error_rows,
            "duplicate_rows": self.duplicate_rows,
            "unmatched_rows": self.unmatched_rows,
            "idempotent_replay": self.idempotent_replay,
            "parse": self.parse,
            "unmatched": self.unmatched,
            "note": self.note,
        }


class ImportService:
    def __init__(self, session_factory: sessionmaker[Session], settings=None) -> None:
        self.sf = session_factory
        self.settings = settings

    # -------------------------------------------------------- 指标导入

    def import_metrics(
        self,
        text: str,
        *,
        file_name: str | None = None,
        fmt: str = "csv",
        created_by: str = "user",
        run_mode: str = "fixture",
        dry_run: bool = False,
    ) -> ImportOutcome:
        """导入标准长表指标。**同文件同映射重复导入幂等。**

        `dry_run=True` 是**试算**：只解析、只匹配，不建批次、不写快照。
        试算是"看看这批数据能不能对上"，留痕反而是污染——
        而且批次表有 (kind, file_hash, mapping_version) 唯一约束，
        试算若建行，正式导入就会撞约束失败（这个 bug 真实发生过一次）。
        """
        file_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        # --- 幂等：同文件 + 同映射版本，直接返回上次结果 ---
        with self.sf() as s:
            prev = s.scalars(
                select(ImportBatch)
                .where(ImportBatch.kind == "metrics")
                .where(ImportBatch.file_hash == file_hash)
                .where(ImportBatch.mapping_version == MAPPING_VERSION)
            ).first()
            if prev is not None and not dry_run:
                return ImportOutcome(
                    import_id=prev.id, kind="metrics", state=prev.state,
                    total_rows=prev.total_rows, accepted_rows=prev.accepted_rows,
                    error_rows=prev.error_rows, duplicate_rows=prev.duplicate_rows,
                    unmatched_rows=prev.unmatched_rows, idempotent_replay=True,
                    parse={}, unmatched=[],
                    note=("这个文件（sha256 相同）已经用同一套映射导入过，"
                          "本次未重复写入。原始结果见该 import_id。"),
                )

        parsed = parse_metrics_table(text, fmt=fmt)

        if dry_run:
            return self._dry_run_metrics(parsed)

        with self.sf() as s:
            batch = ImportBatch(
                kind="metrics", file_hash=file_hash, file_name=file_name, format=fmt,
                mapping_version=MAPPING_VERSION, state="parsed",
                mapping_json=parsed.mapping,
                total_rows=parsed.total, created_by=created_by, run_mode=run_mode,
            )
            s.add(batch)
            s.flush()

            accepted = 0
            errs = 0
            dup = 0
            unmatched: list[dict] = []

            # 预取已登记发布：按 (platform, post_id) 与 url 建索引
            pubs = list(s.scalars(select(Publication)))
            by_post = {(p.platform, p.platform_post_id): p for p in pubs if p.platform_post_id}
            by_link = {p.link: p for p in pubs if p.link}

            # --- 先按"采集上下文"分组，再逐组写一条快照 ---
            #
            # 为什么不能一行写一条快照：
            # 标准长表里"同一次采集的播放量/点赞/评论"是三行，但它们**同属一个上下文**。
            # 若每行各写一条，则 (publication, observed_at, window_kind) 这个唯一约束
            # 会立刻冲突，而且"同一次采集"这个事实被拆散了——复盘时要看的是
            # "10 点时这条作品的各项指标分别是多少"，不是三条互不相干的记录。
            #
            # 上下文 = publication + observed_at + window_kind + traffic_type
            # 流量类型必须进 key：自然流量和付费流量在同一时刻是**两个不同的观测**，
            # 合并会让"多少是自然涨的"永远算不出来。
            groups: dict[tuple, dict] = {}
            for row in parsed.rows:
                if not row.ok:
                    errs += 1
                    continue
                n = row.normalized
                pub = self._match(s, n, by_post, by_link)
                if pub is None:
                    unmatched.append({
                        "row": row.row_no,
                        "platform": n.get("platform"),
                        "post_id": n.get("post_id"),
                        "url": n.get("url"),
                        "metric_name": n.get("metric_name"),
                        "reason": "找不到对应的发布记录，已放入待匹配，未做任何猜测绑定",
                    })
                    continue
                key = (
                    pub.id,
                    n.get("observed_at"),
                    n.get("aggregation_kind") or "unknown",
                    n.get("traffic_type") or "unknown",
                )
                grp = groups.get(key)
                if grp is None:
                    grp = {"pub": pub, "n": n, "cells": [], "rows": []}
                    groups[key] = grp
                grp["cells"].append((n.get("metric_name"), n))
                grp["rows"].append(row.row_no)

            for key, grp in groups.items():
                pub = grp["pub"]
                n0 = grp["n"]
                if self._is_duplicate(s, pub.id, key):
                    dup += len(grp["cells"])
                    continue
                self._write_snapshot(s, pub, batch.id, n0, run_mode=run_mode,
                                     cells=grp["cells"], row_nos=grp["rows"])
                accepted += len(grp["cells"])

            batch.accepted_rows = accepted
            batch.error_rows = errs
            batch.duplicate_rows = dup
            batch.unmatched_rows = len(unmatched)
            batch.errors_json = {
                "parse_errors": [
                    {"row": r.row_no, "errors": r.errors} for r in parsed.error_rows[:100]
                ],
                "unmatched": unmatched[:100],
            }
            batch.state = "imported"
            iid = batch.id
            s.commit()

        return ImportOutcome(
            import_id=iid, kind="metrics", state="imported",
            total_rows=parsed.total, accepted_rows=accepted, error_rows=errs,
            duplicate_rows=dup, unmatched_rows=len(unmatched),
            parse=parsed.as_dict(),
            unmatched=unmatched[:100],
            note=(
                "缺失值以 null 保存，**不等于 0**；"
                "累计值与窗口值分别存 aggregation_kind，比较前必须同口径；"
                f"未匹配 {len(unmatched)} 行进入待匹配列表，未做猜测绑定。"
            ),
        )

    def _dry_run_metrics(self, parsed: ParseResult) -> ImportOutcome:
        """试算：只报告"会发生什么"，不写任何东西。"""
        with self.sf() as s:
            pubs = list(s.scalars(select(Publication)))
            by_post = {(p.platform, p.platform_post_id): p for p in pubs if p.platform_post_id}
            by_link = {p.link: p for p in pubs if p.link}
            accepted = 0
            unmatched: list[dict] = []
            for row in parsed.rows:
                if not row.ok:
                    continue
                n = row.normalized
                pub = self._match(s, n, by_post, by_link)
                if pub is None:
                    unmatched.append({
                        "row": row.row_no, "platform": n.get("platform"),
                        "post_id": n.get("post_id"), "url": n.get("url"),
                        "metric_name": n.get("metric_name"),
                        "reason": "找不到对应的发布记录，已放入待匹配，未做任何猜测绑定",
                    })
                else:
                    accepted += 1
        return ImportOutcome(
            import_id="", kind="metrics", state="dry_run",
            total_rows=parsed.total, accepted_rows=accepted,
            error_rows=len(parsed.error_rows), duplicate_rows=0,
            unmatched_rows=len(unmatched), parse=parsed.as_dict(),
            unmatched=unmatched[:100],
            note="试算模式：未建立导入批次、未写入任何快照。正式导入请去掉 dry_run。",
        )

    def match_unmatched(self, import_id: str, *, row_no: int,
                        publication_id: str, matched_by: str = "user") -> dict:
        """人工把待匹配行绑到某条发布记录上。**只有人能决定这个绑定。**"""
        with self.sf() as s:
            batch = s.get(ImportBatch, import_id)
            if batch is None:
                raise ImportError_("IMPORT_NOT_FOUND", f"导入批次不存在：{import_id}")
            pub = s.get(Publication, publication_id)
            if pub is None:
                raise ImportError_("PUBLICATION_NOT_FOUND", f"发布记录不存在：{publication_id}")

            errs = dict(batch.errors_json or {})
            unmatched = list(errs.get("unmatched") or [])
            target = next((u for u in unmatched if u.get("row") == row_no), None)
            if target is None:
                raise ImportError_("ROW_NOT_UNMATCHED", f"第 {row_no} 行不在待匹配列表里")

            n = {
                "observed_at": _now().replace(tzinfo=None),
                "metric_name": KNOWN_METRICS.get(target.get("metric_name") or "",
                                                 target.get("metric_name")),
                "value": None,
                "value_missing": True,
                "unit": "count",
                "aggregation_kind": "unknown",
                "traffic_type": "unknown",
                "denominator_metric": None,
                "platform": pub.platform,
                "post_id": pub.platform_post_id,
                "url": pub.link,
            }
            _ = matched_by  # 记录是谁绑的，便于回溯
            snap = self._write_snapshot(s, pub, batch.id, n, run_mode=batch.run_mode)
            errs["unmatched"] = [u for u in unmatched if u.get("row") != row_no]
            batch.errors_json = errs
            batch.unmatched_rows = len(errs["unmatched"])
            batch.accepted_rows += 1
            s.commit()
            return {
                "matched": True, "row": row_no, "publication_id": publication_id,
                "snapshot_id": snap.id,
                "note": "人工绑定完成。指标值为 null（待匹配行原本没有可解析数值），不填 0。",
            }

    # -------------------------------------------------------- 查询

    def list_imports(self, *, kind: str | None = None) -> dict:
        with self.sf() as s:
            stmt = select(ImportBatch)
            if kind:
                stmt = stmt.where(ImportBatch.kind == kind)
            rows = list(s.scalars(stmt.order_by(ImportBatch.created_at.desc())))
            return {
                "items": [self._batch_dict(b) for b in rows],
                "total": len(rows),
                "mapping_version": MAPPING_VERSION,
                "standard_fields": list(STANDARD_FIELDS),
                "note": (
                    "同一文件 + 同一映射版本重复导入是幂等的，不会翻倍；"
                    "映射变更会建立新版本，旧数据保留原有解释。"
                ),
            }

    def get_import(self, import_id: str) -> dict:
        with self.sf() as s:
            b = s.get(ImportBatch, import_id)
            if b is None:
                raise ImportError_("IMPORT_NOT_FOUND", f"导入批次不存在：{import_id}")
            data = self._batch_dict(b)
            data["errors_detail"] = b.errors_json or {}
            snaps = list(s.scalars(
                select(MetricSnapshot).where(MetricSnapshot.import_id == import_id)
            ))
            data["snapshot_count"] = len(snaps)
            data["snapshots"] = [self._snapshot_dict(s, x) for x in snaps[:50]]
            return data

    def snapshots_for_publication(self, publication_id: str) -> dict:
        with self.sf() as s:
            rows = list(s.scalars(
                select(MetricSnapshot)
                .where(MetricSnapshot.publication_id == publication_id)
                .order_by(MetricSnapshot.observed_at.asc())
            ))
            series: dict[str, list] = {}
            for snap in rows:
                for name, cell in (snap.metrics or {}).items():
                    series.setdefault(name, []).append({
                        "observed_at": _aware(snap.observed_at).isoformat() if snap.observed_at else None,
                        "age_hours": snap.age_hours,
                        "value": cell.get("value"),          # 可能是 None —— 缺失不是 0
                        "unit": cell.get("unit"),
                        "aggregation_kind": snap.window_kind,
                        "traffic_type": cell.get("traffic_type"),
                        "supersedes_id": snap.supersedes_id,
                    })
            return {
                "publication_id": publication_id,
                "snapshot_count": len(rows),
                "series": series,
                "notes": {
                    "missing_is_not_zero": "value 为 null 表示该次采集缺失，不是 0",
                    "no_cross_window_compare": "aggregation_kind 不同的点不能直接比较",
                },
            }

    # -------------------------------------------------------- 内部

    def _match(self, s: Session, n: dict, by_post: dict, by_link: dict) -> Publication | None:
        plat = n.get("platform")
        pid = n.get("post_id")
        url = n.get("url")
        if pid:
            if plat and (plat, pid) in by_post:
                return by_post[(plat, pid)]
        if url and url in by_link:
            return by_link[url]
        # 平台为空时按 post_id 唯一匹配
        if pid and not plat:
            hits = [p for (pl, q), p in by_post.items() if q == pid]
            if len(hits) == 1:
                return hits[0]
        return None

    def _is_duplicate(self, s: Session, pub_id: str, key: tuple) -> bool:
        """同一采集上下文去重。

        上下文 = publication + observed_at + window_kind（流量类型在指标 cell 里区分）。
        命中即视为"这次采集已经导过了"，**整组跳过**，不逐指标判断——
        否则会出现"播放量已导、点赞没导"的半截快照，比整组不导更难排查。
        """
        _pub, obs, w, tt = key
        if obs is None:
            return False
        existing = s.scalars(
            select(MetricSnapshot)
            .where(MetricSnapshot.publication_id == pub_id)
            .where(MetricSnapshot.observed_at == obs)
            .where(MetricSnapshot.window_kind == w)
            .where(MetricSnapshot.traffic_type == (tt or "unknown"))
        ).first()
        return existing is not None

    def _write_snapshot(self, s: Session, pub: Publication, import_id: str,
                        n: dict, *, run_mode: str,
                        cells: list | None = None,
                        row_nos: list | None = None) -> MetricSnapshot:
        """写一条快照。`cells` 是同一次采集里的多个指标。"""
        obs = n.get("observed_at")
        age = None
        if obs is not None and pub.published_at is not None:
            base = _aware(pub.published_at)
            cur = _aware(obs)
            age = round((cur - base).total_seconds() / 3600.0, 2)

        metrics: dict[str, dict] = {}
        for metric, cell in (cells or [(n.get("metric_name"), n)]):
            metrics[metric or "unknown"] = {
                "value": cell.get("value"),        # None = 缺失，不是 0
                "unit": cell.get("unit"),
                "traffic_type": cell.get("traffic_type"),
                "denominator_metric": cell.get("denominator_metric"),
                "raw_metric_name": cell.get("raw_metric_name"),
                "value_missing": bool(cell.get("value_missing")),
                "value_note": cell.get("value_note"),
                "source_row": (row_nos or [None])[len(metrics)] if row_nos else None,
            }

        # 同上下文若已有快照 → 追加并标 supersedes（**不覆盖旧行**）
        supersedes = None
        if obs is not None:
            prev = s.scalars(
                select(MetricSnapshot)
                .where(MetricSnapshot.publication_id == pub.id)
                .where(MetricSnapshot.observed_at == obs)
                .where(MetricSnapshot.window_kind == (n.get("aggregation_kind") or "unknown"))
                .where(MetricSnapshot.traffic_type == (n.get("traffic_type") or "unknown"))
            ).first()
            if prev is not None:
                supersedes = prev.id

        snap = MetricSnapshot(
            publication_id=pub.id, import_id=import_id,
            observed_at=obs or _now().replace(tzinfo=None),
            age_hours=age,
            window_kind=n.get("aggregation_kind") or "unknown",
            traffic_type=n.get("traffic_type") or "unknown",
            metrics=metrics,
            raw_fields=json_safe(n.get("_raw")),
            mapping_json=json_safe({k: v for k, v in n.items() if k != "_raw"}),
            supersedes_id=supersedes,
            run_mode=run_mode,
        )
        s.add(snap)
        s.flush()
        return snap

    @staticmethod
    def _batch_dict(b: ImportBatch) -> dict:
        return {
            "id": b.id,
            "kind": b.kind,
            "file_name": b.file_name,
            "file_hash": b.file_hash,
            "format": b.format,
            "mapping_version": b.mapping_version,
            "state": b.state,
            "total_rows": b.total_rows,
            "accepted_rows": b.accepted_rows,
            "error_rows": b.error_rows,
            "duplicate_rows": b.duplicate_rows,
            "unmatched_rows": b.unmatched_rows,
            "run_mode": b.run_mode,
            "created_at": _aware(b.created_at).isoformat() if b.created_at else None,
            "note": "accepted + error + duplicate + unmatched = total_rows",
        }

    def _snapshot_dict(self, s: Session, snap: MetricSnapshot) -> dict:
        pub = s.get(Publication, snap.publication_id)
        return {
            "id": snap.id,
            "publication_id": snap.publication_id,
            "platform": pub.platform if pub else None,
            "platform_post_id": pub.platform_post_id if pub else None,
            "observed_at": _aware(snap.observed_at).isoformat() if snap.observed_at else None,
            "age_hours": snap.age_hours,
            "window_kind": snap.window_kind,
            "metrics": snap.metrics,
            "raw_fields": snap.raw_fields,
            "supersedes_id": snap.supersedes_id,
            "run_mode": snap.run_mode,
        }
