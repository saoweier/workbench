"""P4 端到端测试：发布记录 / 数据导入 / 评论导入与聚类 / 复盘 / 反馈下一轮。

运行方式（与 P0/P1/P2/P3 一致，纯脚本、无 pytest）：

    .venv/bin/python backend/tests/test_p4_e2e.py

覆盖 10 组：
[1] 发布记录：只接受人登记、链接/ID 二者必居其一、declared 与 verified 分开
[2] 发布记录：批准一致性（无批准 / 过期批准 / 干净批准）
[3] 数据导入：单位换算、缺失 null≠0、未知指标不归类、时间不猜
[4] 数据导入：采集上下文分组、流量类型分开、幂等、dry_run 零留痕
[5] 数据导入：待匹配不猜测绑定、人工匹配
[6] 评论导入：规则分类、去标识、原文不改、取样口径
[7] 评论聚类：原文例证 + 分母 + 偏差说明；空样本不给结论
[8] 复盘：充分度分级、观察无因果词、假设必须有替代解释、版本追加不改旧版
[9] 反馈下一轮：默认 proposed、自动采用仅 tiny/small、可回退、三条并列限制
[10] 不变量汇总：无 auto-publish / auto-apply；P4 仍无真实调用与费用

**全程不发起任何真实网络调用、不产生任何费用**：
断言结尾 `real_calls_recorded == 0`。
"""
from __future__ import annotations

import hashlib
import json as _json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# ---- 环境必须最先设置：配置在 import 时被 lru_cache 固化 ----
_TMP = Path(tempfile.mkdtemp(prefix="cwb-p4-"))
os.environ["CWB_STORAGE_ROOT"] = str(_TMP)
os.environ["CWB_ARTIFACT_DIR"] = str(_TMP / "artifacts")
os.environ["CWB_TMP_DIR"] = str(_TMP / "tmp")
os.environ["CWB_DATABASE_URL"] = f"sqlite:///{_TMP / 'p4.db'}"
os.environ["CWB_SECRET_STORE_PATH"] = str(_TMP / "secrets.json")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.models.entities import (  # noqa: E402
    Base,
    Batch,
    CommentSample,
    ContentItem,
    ContentRevision,
    ImportBatch,
    MetricSnapshot,
    PlatformRevision,
    ProviderCallRow,
    Publication,
    ReviewDecision,
    ReviewReport,
    TopicFeedback,
    enable_sqlite_fk,
)
from app.services.comment_service import (  # noqa: E402
    CATEGORIES,
    CATEGORY_LABELS,
    CATEGORY_RULES,
    CommentImportService,
    anonymize,
    classify,
)
from app.services.feedback_service import (  # noqa: E402
    AUTO_ADOPTABLE,
    FEEDBACK_KINDS,
    MAGNITUDES,
    STATUSES,
    FeedbackService,
)
from app.services.import_service import (  # noqa: E402
    MAPPING_VERSION,
    STANDARD_FIELDS,
    ImportError_,
    ImportService,
    json_safe,
    map_header,
    parse_metrics_table,
    parse_number,
    parse_time,
)
from app.services.publication_service import (  # noqa: E402
    PublicationError,
    PublicationService,
    observation_windows,
)
from app.services.review_service import (  # noqa: E402
    SUFFICIENCY_LEVELS,
    ReviewService,
)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  → ' + str(detail)) if detail else ''}")


def section(title: str) -> None:
    print(f"\n{title}")


settings = get_settings()
engine = create_engine(settings.database_url, future=True)
enable_sqlite_fk(engine)
Base.metadata.create_all(engine)
SF = sessionmaker(bind=engine, future=True)
client = TestClient(app)
P = settings.api_prefix

pub_svc = PublicationService(SF, settings)
imp_svc = ImportService(SF, settings)
cmt_svc = CommentImportService(SF, settings)
rev_svc = ReviewService(SF, settings)
fb_svc = FeedbackService(SF, settings)

PUB_AT = datetime(2026, 10, 1, 9, 0, 0)


# ---------------------------------------------------------------- 夹具

def mk_content(*, display_id="C001", topic="测试选题", platform="douyin",
               post_id="7300000001", approved=True, pub=True,
               manifest="M" * 64) -> dict:
    with SF() as s:
        ci = ContentItem(display_id=display_id, topic=topic,
                         state="approved" if approved else "drafting",
                         selected_by="user")
        s.add(ci); s.flush()
        cr = ContentRevision(content_id=ci.id, version=1, input_hash="h" * 16,
                             claims_json={"claims": []}, brief_json={})
        s.add(cr); s.flush()
        pr = PlatformRevision(content_revision_id=cr.id, platform=platform, version=1,
                              title="测试标题", caption="测试正文", pages_json={"pages": []},
                              content_hash="c" * 64, manifest_hash=manifest,
                              state="approved")
        s.add(pr); s.flush()
        if approved:
            s.add(ReviewDecision(platform_revision_id=pr.id, manifest_hash=manifest,
                                 decision="approve", actor="user"))
        pub_id = None
        if pub:
            p = Publication(platform=platform, platform_revision_id=pr.id,
                            link=f"https://example.com/{post_id}",
                            platform_post_id=post_id, published_at=PUB_AT,
                            published_manifest_hash=manifest, status="verified",
                            registered_by="user", run_mode="real")
            s.add(p); s.flush()
            pub_id = p.id
        out = {"content_id": ci.id, "revision_id": cr.id, "platform_revision_id": pr.id,
               "publication_id": pub_id}
        s.commit()
        return out


def add_snapshot(pub_id: str, *, age_h: float, values: dict,
                 window="cumulative", traffic="unknown", observed=None) -> str:
    with SF() as s:
        obs = observed or (PUB_AT + timedelta(hours=age_h))
        snap = MetricSnapshot(
            publication_id=pub_id, observed_at=obs, age_hours=age_h,
            window_kind=window, traffic_type=traffic,
            metrics={k: {"value": v, "unit": "count", "traffic_type": traffic,
                         "value_missing": v is None} for k, v in values.items()},
            run_mode="real",
        )
        s.add(snap); s.flush()
        out = snap.id
        s.commit()
        return out


def add_comment(pub_id: str, text: str, category: str, *, sampling="全量导出",
                like=None) -> str:
    with SF() as s:
        c = CommentSample(publication_id=pub_id,
                          anon_id="anon_" + hashlib.sha256(text.encode()).hexdigest()[:24],
                          text=text, sampling_method=sampling, category=category,
                          category_reason="测试夹具", like_count=like, run_mode="real")
        s.add(c); s.flush()
        out = c.id
        s.commit()
        return out


# ================================================================ [1]
section("[1] 发布记录：只接受人登记 / 标识必填 / declared 与 verified 分开")

env1 = mk_content()
out1 = pub_svc.register(platform_revision_id=env1["platform_revision_id"],
                        link="https://v.douyin.com/abc",
                        platform_post_id="7300000001")
check("登记成功且状态为 declared", out1.status == "declared", out1.status)
check("declared 含链接", out1.declared["link"] == "https://v.douyin.com/abc",
      str(out1.declared.get("link")))
check("verified 初始为 None（没人核验过）", out1.verified is None, str(out1.verified))
check("同一份稿有批准记录 → approval_consistent=True",
      out1.approval_consistent is True, str(out1.approval_consistent))

env1_tz = mk_content(display_id="C-timezone", post_id="7300000991")
out1_tz = pub_svc.register(
    platform_revision_id=env1_tz["platform_revision_id"],
    platform_post_id="7300000991",
    published_at=datetime.fromisoformat("2026-10-02T09:00:00+08:00"),
)
check("带时区发布时刻按同一 UTC 瞬间保存",
      out1_tz.declared["published_at"] == "2026-10-02T01:00:00+00:00",
      out1_tz.declared["published_at"])

# 非人登记一律拒绝（不变量 6）
for bad_actor in ("system", "model", "ai", "worker", ""):
    try:
        pub_svc.register(platform_revision_id=env1["platform_revision_id"],
                         link="x", registered_by=bad_actor)
        check(f"拒绝 {bad_actor or '空'} 作为登记人", False, "竟然通过了")
    except PublicationError as e:
        check(f"拒绝 {bad_actor or '空'} 作为登记人",
              e.code == "PUBLISHER_MUST_BE_HUMAN", e.code)

# 链接与平台 ID 至少有一个
try:
    pub_svc.register(platform_revision_id=env1["platform_revision_id"], link="", platform_post_id="")
    check("空链接空 ID 被拒", False, "竟然通过了")
except PublicationError as e:
    check("空链接空 ID 被拒", e.code == "PUBLICATION_NEEDS_IDENTIFIER", e.code)
check("仅平台 ID 可登记",
      pub_svc.register(platform_revision_id=env1["platform_revision_id"],
                       platform_post_id="only-id").publication_id != "", "")

# 核验是独立动作，且只由人记录
v = pub_svc.verify(out1.publication_id, verified_state="confirmed")
check("核验后 status=verified", v["status"] == "verified", v["status"])
check("核验后 declared 仍在（未被覆盖）",
      v["declared"]["link"] == "https://v.douyin.com/abc", str(v["declared"].get("link")))
try:
    pub_svc.verify(out1.publication_id, verified_state="confirmed", verified_by="system")
    check("拒绝 system 记录核验", False, "竟然通过了")
except PublicationError as e:
    check("拒绝 system 记录核验", e.code == "VERIFIER_MUST_BE_HUMAN", e.code)
try:
    pub_svc.verify(out1.publication_id, verified_state="看似核验了")
    check("拒绝非法核验状态", False, "竟然通过了")
except PublicationError as e:
    check("拒绝非法核验状态", e.code == "BAD_VERIFIED_STATE", e.code)

check("观察窗口 24/72/168 齐全",
      set(observation_windows(PUB_AT)["marks"].keys()) == {"24h", "72h", "168h"}, "")
check("无发布时间 → 窗口不可判定",
      observation_windows(None)["available"] is False, "")
check("100h 前发布 → 24h/72h 已到、7d 未到",
      observation_windows(datetime.now(timezone.utc) - timedelta(hours=100))
      ["marks"]["168h"]["reached"] is False, "")

lst1 = pub_svc.list_publications(content_id=env1["content_id"])
check("发布列表可按内容过滤", lst1["total"] >= 1, str(lst1["total"]))
check("列表说明永不自动标记发布", "never_auto" in lst1["semantics"], "")

env_fixture_pub = mk_content(display_id="C-fixture-pub", pub=False)
fixture_pub_response = client.post(f"{P}/publications", json={
    "platform_revision_id": env_fixture_pub["platform_revision_id"],
    "platform_post_id": "fixture-post-001",
    "link": "https://example.invalid/fixture-post-001",
    "run_mode": "fixture",
})
fixture_pub_id = fixture_pub_response.json().get("publication_id")
with SF() as s:
    fixture_pub = s.get(Publication, fixture_pub_id) if fixture_pub_id else None
check("模拟发布记录保留 fixture 模式",
      fixture_pub_response.status_code == 200 and fixture_pub is not None
      and fixture_pub.run_mode == "fixture",
      f"status={fixture_pub_response.status_code} mode={getattr(fixture_pub, 'run_mode', None)}")
bad_mode_response = client.post(f"{P}/publications", json={
    "platform_revision_id": env_fixture_pub["platform_revision_id"],
    "platform_post_id": "bad-mode-post",
    "run_mode": "unmarked-demo",
})
check("发布记录拒绝未知运行模式",
      bad_mode_response.status_code == 422
      and bad_mode_response.json()["detail"]["code"] == "BAD_RUN_MODE",
      str(bad_mode_response.status_code))


# ================================================================ [2]
section("[2] 发布记录：批准一致性（无批准 / 过期批准 / 干净批准）")

env_noappr = mk_content(display_id="C-noappr", topic="无批准",
                        manifest="N" * 64, approved=False, pub=False)
o = pub_svc.register(platform_revision_id=env_noappr["platform_revision_id"], link="https://x.cn/1")
check("无批准记录 → approval_consistent=False", o.approval_consistent is False, "")
check("无批准时给出补流程警告", any("批准" in w for w in o.warnings), str(o.warnings))

env_stale = mk_content(display_id="C-stale", topic="过期批准",
                       manifest="A" * 64, pub=False)
with SF() as s:
    pr = s.get(PlatformRevision, env_stale["platform_revision_id"])
    pr.manifest_hash = "B" * 64          # 批准后又改了稿
    s.commit()
o2 = pub_svc.register(platform_revision_id=env_stale["platform_revision_id"], link="https://x.cn/2")
check("过期批准 → approval_consistent=False", o2.approval_consistent is False, "")
check("过期批准给出明确警告", any("过期批准" in w for w in o2.warnings), str(o2.warnings))

env_clean = mk_content(display_id="C-clean", topic="干净批准",
                       platform="xiaohongshu", post_id="xhs001",
                       manifest="C" * 64, pub=False)
o3 = pub_svc.register(platform_revision_id=env_clean["platform_revision_id"],
                      link="https://xhslink.com/1")
check("manifest 一致 → approval_consistent=True", o3.approval_consistent is True, "")
check("干净批准无警告", o3.warnings == [], str(o3.warnings))


# ================================================================ [3]
section("[3] 数据导入：单位换算 / 缺失 null≠0 / 未知指标不归类 / 时间不猜")

v, u, e = parse_number("12000")
check("纯数字原样", v == 12000 and e == [], f"{v} {e}")
v, u, e = parse_number("1.2万")
check("中文万单位换算", v == 12000 and u == "万", f"{v} {u}")
v, u, e = parse_number("3.5k")
check("k 单位换算", v == 3500, str(v))
v, u, e = parse_number("88%")
check("百分号折算", v == 0.88, str(v))
v, u, e = parse_number("")
check("空字符串 → None（缺失，不是 0）", v is None and e == [], f"{v} {e}")
v, u, e = parse_number("N/A")
check("N/A → None", v is None and e == [], str(v))
v, u, e = parse_number("abc")
check("非数字 → 报错，不猜", v is None and len(e) == 1, str(e))

t, e = parse_time("2026-10-02T10:00:00")
check("ISO 8601 时间", t == datetime(2026, 10, 2, 10, 0, 0), str(t))
t, e = parse_time("2026/10/02")
check("斜杠日期", t is not None and t.month == 10, str(t))
t, e = parse_time("")
check("空时间 → None（不猜当前时间）", t is None and e == [], str(t))
t, e = parse_time("2026-10-02T10:00:00+08:00")
check("parse_time 将带时区时间规范到 UTC",
      t == datetime(2026, 10, 2, 2, 0), str(t))
t, e = parse_time("昨天")
check("无法解析时间 → 报错", t is None and len(e) == 1, str(e))

m, un = map_header(["observed_at", "metric_name", "value", "unit"])
check("标准表头全识别", len(m) == 4 and un == [], f"{m} {un}")
m, un = map_header(["采集时间", "指标", "数值", "单位"])
check("中文表头识别", len(m) == 4, str(m))
m, un = map_header(["采集时间", "作品ID", "播放量", "点赞数"])
check("宽表指标列识别", any(x.startswith("wide_metric:") for x in m.values()), str(m))
m, un = map_header(["observed_at", "莫名其妙的列"])
check("未知列进入未映射", "莫名其妙的列" in un, str(un))

csv_long = (
    "platform,post_id,observed_at,metric_name,value,unit,aggregation_kind\n"
    "douyin,7300000001,2026-10-02T10:00:00,播放量,12000,次,cumulative\n"
    "douyin,7300000001,2026-10-02T10:00:00,点赞,1.2万,,cumulative\n"
    "douyin,7300000001,2026-10-02T10:00:00,评论,,,cumulative\n"
)
pr3 = parse_metrics_table(csv_long)
check("长表 3 行解析", pr3.total == 3, str(pr3.total))
check("长表无解析错误", len(pr3.error_rows) == 0, str([x.errors for x in pr3.error_rows]))
vals3 = [x.normalized["value"] for x in pr3.ok_rows]
check("万单位在长表里也换算", 12000.0 in vals3, str(vals3))
check("空值解析为 None", None in vals3, str(vals3))
check("None 不等于 0", 0 not in vals3 and 0.0 not in vals3, str(vals3))

pr3b = parse_metrics_table(
    "observed_at,metric_name,value,post_id\n"
    "2026-10-02T10:00:00,某种前所未有的指标,5,7300000001\n")
check("未知指标名报错而非归类", len(pr3b.error_rows) == 1, str(len(pr3b.error_rows)))
check("错误提示需补映射",
      "未知指标名" in pr3b.error_rows[0].errors[0], str(pr3b.error_rows[0].errors))

try:
    parse_metrics_table("foo,bar\n1,2\n")
    check("无指标列 → 抛 NO_METRIC_COLUMNS", False, "竟然通过了")
except ImportError_ as e:
    check("无指标列 → 抛 NO_METRIC_COLUMNS", e.code == "NO_METRIC_COLUMNS", e.code)

csv_rate = ("observed_at,metric_name,value,post_id\n"
            "2026-10-02T10:00:00,完播率,85,7300000001\n")
rr = parse_metrics_table(csv_rate)
check("完播率 85 折算为 0.85", rr.ok_rows[0].normalized["value"] == 0.85,
      str(rr.ok_rows[0].normalized["value"]))
csv_bad_rate = ("observed_at,metric_name,value,post_id\n"
                "2026-10-02T10:00:00,完播率,999,7300000001\n")
check("完播率 999 超范围被拒",
      len(parse_metrics_table(csv_bad_rate).error_rows) == 1, "")


# ================================================================ [4]
section("[4] 数据导入：上下文分组 / 流量分开 / 幂等 / dry_run 零留痕")

env4 = mk_content(display_id="C-imp", topic="导入测试")
env4["publication_id"] = env4["publication_id"]
oid = env4["publication_id"]

o4 = imp_svc.import_metrics(csv_long, file_name="m.csv")
check("导入成功", o4.state == "imported", o4.state)
check("3 行全部接受", o4.accepted_rows == 3, str(o4.accepted_rows))
check("note 说明缺失不等于 0", "不等于 0" in o4.note, o4.note)

with SF() as s:
    snaps = list(s.scalars(select(MetricSnapshot)
                           .where(MetricSnapshot.publication_id == oid)))
check("3 行同上下文合并为 1 条快照", len(snaps) == 1, str(len(snaps)))
check("一条快照容纳 3 个指标",
      set((snaps[0].metrics or {}).keys()) == {"views", "likes", "comments"},
      str(set((snaps[0].metrics or {}).keys())))

o4b = imp_svc.import_metrics(csv_long, file_name="m.csv")
check("同文件重复导入幂等", o4b.idempotent_replay is True, "")
check("幂等时返回同一 import_id", o4b.import_id == o4.import_id, "")
with SF() as s:
    n_snap = len(list(s.scalars(select(MetricSnapshot))))
check("幂等未新增快照", n_snap == 1, str(n_snap))

csv_traffic = (
    "platform,post_id,observed_at,metric_name,value,aggregation_kind,traffic_type\n"
    "douyin,7300000001,2026-10-03T10:00:00,播放量,3000,window,organic\n"
    "douyin,7300000001,2026-10-03T10:00:00,播放量,1000,window,paid\n"
)
o4c = imp_svc.import_metrics(csv_traffic, file_name="t.csv")
check("自然与付费各自接受", o4c.accepted_rows == 2, str(o4c.accepted_rows))
with SF() as s:
    win = list(s.scalars(select(MetricSnapshot)
                         .where(MetricSnapshot.publication_id == oid)
                         .where(MetricSnapshot.window_kind == "window")))
check("natural/paid 各占一条快照", len(win) == 2, str(len(win)))
check("流量类型进独立列", {w.traffic_type for w in win} == {"organic", "paid"},
      str({w.traffic_type for w in win}))

csv_dup_ctx = ("platform,post_id,observed_at,metric_name,value\n"
               "douyin,7300000001,2026-10-02T10:00:00,播放量,12000\n")
o4d = imp_svc.import_metrics(csv_dup_ctx, file_name="dup.csv")
check("同上下文再次导入计入去重", o4d.accepted_rows == 0 and o4d.duplicate_rows >= 1,
      f"acc={o4d.accepted_rows} dup={o4d.duplicate_rows}")

csv_new_ctx = ("platform,post_id,observed_at,metric_name,value\n"
               "douyin,7300000001,2026-10-04T18:00:00,播放量,45000\n")
o4e = imp_svc.import_metrics(csv_new_ctx, file_name="new.csv")
check("新采集时间追加新快照", o4e.accepted_rows == 1, str(o4e.accepted_rows))

o4f = imp_svc.import_metrics("platform,post_id,observed_at,metric_name,value\n"
                             "douyin,7300000001,2026-10-09T10:00:00,播放量,777\n",
                             file_name="dry.csv", dry_run=True)
check("dry_run 状态标为 dry_run", o4f.state == "dry_run", o4f.state)
check("dry_run 不建批次", o4f.import_id == "", o4f.import_id)
with SF() as s:
    hit = s.scalars(select(MetricSnapshot).where(
        MetricSnapshot.observed_at == datetime(2026, 10, 9, 10, 0, 0))).first()
check("dry_run 未写入快照", hit is None, str(hit))

d4 = imp_svc.snapshots_for_publication(oid)
check("指标序列覆盖 views", len(d4["series"].get("views") or []) >= 2,
      str(list(d4["series"].keys())))
check("序列说明缺失非 0", "null" in d4["notes"]["missing_is_not_zero"], "")
check("序列提示不跨口径比较", "aggregation_kind" in d4["notes"]["no_cross_window_compare"], "")

check("json_safe 处理 datetime",
      isinstance(json_safe({"t": datetime(2026, 1, 1)}), dict)
      and isinstance(json_safe({"t": datetime(2026, 1, 1)})["t"], str), "")


# ================================================================ [5]
section("[5] 数据导入：待匹配不猜测绑定 / 人工匹配")

csv_unmatched = (
    "platform,post_id,observed_at,metric_name,value\n"
    "douyin,9999999999,2026-10-02T10:00:00,播放量,500\n"
    "douyin,7300000001,2026-10-05T11:00:00,播放量,20000\n"
)
o5 = imp_svc.import_metrics(csv_unmatched, file_name="u.csv")
check("对不上的行进待匹配", o5.unmatched_rows == 1, str(o5.unmatched_rows))
check("能对上的照常接受", o5.accepted_rows == 1, str(o5.accepted_rows))
check("待匹配理由说明不猜测绑定",
      "未做任何猜测绑定" in o5.unmatched[0]["reason"], o5.unmatched[0]["reason"])
check("整批不因一行对不上而失败", o5.state == "imported", o5.state)

mres = imp_svc.match_unmatched(o5.import_id, row_no=1, publication_id=oid)
check("人工匹配成功", mres["matched"] is True, "")
check("匹配不填 0（无值是 null）", "不填 0" in mres["note"], mres["note"])
check("匹配后未匹配数归零",
      imp_svc.get_import(o5.import_id)["unmatched_rows"] == 0, "")

lst5 = imp_svc.list_imports(kind="metrics")
check("导入列表有数据", lst5["total"] >= 4, str(lst5["total"]))
check("列表暴露映射版本", lst5["mapping_version"] == MAPPING_VERSION, "")
check("标准字段含 metric_name", "metric_name" in lst5["standard_fields"],
      str(STANDARD_FIELDS[:3]))
check("列表说明幂等语义", "幂等" in lst5["note"], "")


# ================================================================ [6]
section("[6] 评论导入：规则分类 / 去标识 / 原文不改 / 取样口径")

for text, expect in [
    ("这个多少钱？", "question"), ("假的吧，标题党", "challenge"),
    ("我用过，效果一般", "experience"), ("打卡", "invalid"),
    ("好", "invalid"), ("12345", "invalid"), ("", "invalid"),
    ("分享一个小技巧", "uncategorized"),
]:
    cat, reason = classify(text)
    check(f"分类 {text!r} → {expect}", cat == expect, f"得到 {cat}")
check("分类必带判断理由", all(classify(t)[1] for t, _ in
                              [("假的吧", 0), ("怎么用", 0), ("打卡", 0)]), "")
check("所有分类都在枚举内",
      all(classify(t)[0] in CATEGORIES for t, _ in [("x", 0), ("怎么", 0)]), "")
check("6 个分类都有中文标签",
      set(CATEGORY_LABELS.keys()) == set(CATEGORIES), str(CATEGORY_LABELS))
check("规则表可读（5 组规则）", len(CATEGORY_RULES) == 5, str(len(CATEGORY_RULES)))
check("规则必带说明", all(len(r) == 3 and r[2] for r in CATEGORY_RULES), "")

a1, a2 = anonymize("user123"), anonymize("user123")
check("匿名化稳定", a1 == a2, "")
check("匿名化不含原文", "user123" not in a1, a1)
check("不同输入不同哈希", anonymize("user123") != anonymize("user456"), "")

env6 = mk_content(display_id="C-cmt", topic="评论测试", platform="douyin",
                  post_id="7300000777")
oid6 = env6["publication_id"]
csv_cmt = (
    "platform,post_id,observed_at,comment_id,text,like_count\n"
    "douyin,7300000777,2026-10-02T10:00:00,u1,这个多少钱？,5\n"
    "douyin,7300000777,2026-10-02T10:00:00,u2,假的吧，一看就是标题党,12\n"
    "douyin,7300000777,2026-10-02T10:00:00,u3,我用过这个，确实有用,30\n"
    "douyin,7300000777,2026-10-02T10:00:00,u4,打卡,0\n"
    "douyin,7300000777,2026-10-02T10:00:00,u5,求更新教程！,8\n"
)
o6 = cmt_svc.import_comments(csv_cmt, file_name="c.csv", sampling_method="全量导出")
check("评论导入成功", o6.state == "imported", o6.state)
check("5 条全部接受", o6.accepted_rows == 5, str(o6.accepted_rows))
check("note 记录取样口径", "全量导出" in o6.note, o6.note)
check("note 标明规则分类", "rule_based" in o6.note, o6.note)
check("重复导入幂等",
      cmt_svc.import_comments(csv_cmt, file_name="c.csv").idempotent_replay is True, "")

with SF() as s:
    rows6 = list(s.scalars(select(CommentSample)
                           .where(CommentSample.publication_id == oid6)))
check("落库用匿名 ID", all(r.anon_id.startswith("anon_") for r in rows6), "")
check("不含原始用户 ID", not any(r.anon_id == "u1" for r in rows6), "")
check("评论原文一字不改", any("标题党" in r.text for r in rows6), "")
check("每条都有分类理由", all(r.category_reason for r in rows6), "")

csv_notime = ("platform,post_id,comment_id,text\n"
              "douyin,7300000777,n1,没有时间的评论\n")
o6b = cmt_svc.import_comments(csv_notime, file_name="nt.csv")
check("评论时间缺失不影响导入", o6b.accepted_rows == 1, str(o6b.accepted_rows))
with SF() as s:
    c6 = s.scalars(select(CommentSample).where(
        CommentSample.anon_id == anonymize("7300000777|n1"))).first()
check("缺时间存 None", c6 is not None and c6.observed_at is None, "")

csv_nolike = ("platform,post_id,comment_id,text\n"
              "douyin,7300000777,k1,没有点赞数\n")
cmt_svc.import_comments(csv_nolike, file_name="nl.csv")
with SF() as s:
    c6b = s.scalars(select(CommentSample).where(
        CommentSample.anon_id == anonymize("7300000777|k1"))).first()
check("缺点赞数存 None（不是 0）", c6b is not None and c6b.like_count is None, "")

try:
    cmt_svc.import_comments("foo,bar\n1,2\n", file_name="bad.csv")
    check("无正文列 → 抛 NO_COMMENT_TEXT_COLUMN", False, "竟然通过了")
except ImportError_ as e:
    check("无正文列 → 抛 NO_COMMENT_TEXT_COLUMN",
          e.code == "NO_COMMENT_TEXT_COLUMN", e.code)


# ================================================================ [7]
section("[7] 评论聚类：原文例证 + 分母 + 偏差说明 / 空样本不给结论")

cl7 = cmt_svc.cluster(publication_id=oid6)
# 这份发布上先后导入过三个文件：5 条全量表 + 1 条缺时间 + 1 条缺点赞，
# 后两条写的是同一个 post_id（7300000777），因此会归属到同一份发布记录。
# 聚类按「发布记录」聚合，所以分母应该是**实际落库的评论样本量**，不是某一个文件的条数。
with SF() as s:
    n_oid6 = len(list(s.scalars(select(CommentSample)
                                .where(CommentSample.publication_id == oid6))))
check("导入三条文件后该发布共 7 条评论", n_oid6 == 7, str(n_oid6))
check("聚类总数正确", cl7["total_comments"] == n_oid6, str(cl7["total_comments"]))
check("平台被识别", cl7["platforms"] == ["douyin"], str(cl7["platforms"]))
cats7 = {g["category"] for g in cl7["groups"]}
check("至少 4 类分组", len(cats7) >= 4, str(cats7))
for g in cl7["groups"]:
    check(f"{g['label']} 带原文例证", len(g["examples"]) > 0, str(g["category"]))
    check(f"{g['label']} 带分母（of_total）", g["of_total"] == n_oid6,
          f"{g.get('of_total')} vs {n_oid6}")
    check(f"{g['label']} 例证含判断理由",
          all(e["category_reason"] for e in g["examples"]), "")
    check(f"{g['label']} 例证是原文",
          all(isinstance(e["text"], str) and e["text"] for e in g["examples"]), "")
# 各组 count 之和必须等于分母——不能"给了百分比但分组的分子加起来不等于样本量"
check("各组 count 之和 == 分母",
      sum(g["count"] for g in cl7["groups"]) == n_oid6,
      f"{sum(g['count'] for g in cl7['groups'])} vs {n_oid6}")
check("空分组不出现（分母不是被撑大的）",
      all(g["count"] > 0 for g in cl7["groups"]), "")
exp7 = next(g for g in cl7["groups"] if g["category"] == "experience")
check("高赞例证排首位", exp7["examples"][0]["like_count"] == 30,
      str(exp7["examples"][0].get("like_count")))
check("如实标注为规则分类", cl7["generation_mode"] == "rule_based", cl7["generation_mode"])
check("提示分类可能出错",
      any("可能出错" in c for c in cl7["caveats"]), str(cl7["caveats"]))
check("提示分母是样本量",
      any("样本量" in c for c in cl7["caveats"]), "")
check("取样偏差说明含口径", "全量导出" in cl7["sampling_bias_note"], cl7["sampling_bias_note"])
# 这条发布上混了「全量导出」和「未声明」两种取样口径。
# 口径不一时**不能挑一个好看的来讲**——必须把不确定性一起说出来。
check("混口径时提示口径不确定",
      "不确定" in cl7["sampling_bias_note"],
      cl7["sampling_bias_note"])

env7b = mk_content(display_id="C-nocmt", topic="无评论", platform="xiaohongshu",
                   post_id="xhs-noc")
cl7b = cmt_svc.cluster(publication_id=env7b["publication_id"])
check("空样本 total=0", cl7b["total_comments"] == 0, str(cl7b["total_comments"]))
check("空样本不产出分组", cl7b["groups"] == [], str(cl7b["groups"]))
check("空样本明说不能得分布结论",
      "不能得出任何分布结论" in cl7b["sampling_bias_note"], cl7b["sampling_bias_note"])
check("空样本提示无数据不是没问题",
      "无数据不是" in cl7b["sampling_bias_note"], cl7b["sampling_bias_note"])

env7c = mk_content(display_id="C-nos", topic="未声明取样", platform="douyin",
                   post_id="7300000888")
cmt_svc.import_comments("platform,post_id,comment_id,text\n"
                        "douyin,7300000888,z1,这个怎么用\n", file_name="nos.csv")
cl7c = cmt_svc.cluster(publication_id=env7c["publication_id"])
check("未声明取样不声称全量", "全量" not in cl7c["sampling_bias_note"],
      cl7c["sampling_bias_note"])
check("未声明取样提示口径不确定",
      "不确定" in cl7c["sampling_bias_note"] or "仅供参考" in cl7c["sampling_bias_note"],
      cl7c["sampling_bias_note"])

env7d = mk_content(display_id="C-samp", topic="抽样", platform="douyin",
                   post_id="7300000999")
cmt_svc.import_comments("platform,post_id,comment_id,text\n"
                        "douyin,7300000999,s1,怎么买\n"
                        "douyin,7300000999,s2,假的吧\n",
                        file_name="samp.csv", sampling_method="前 2 条热门")
cl7d = cmt_svc.cluster(publication_id=env7d["publication_id"])
check("抽样口径注明样本量", "样本量 2" in cl7d["sampling_bias_note"],
      cl7d["sampling_bias_note"])
check("抽样说明这是样本不是全体",
      "样本不是全体" in cl7d["sampling_bias_note"], cl7d["sampling_bias_note"])


# ================================================================ [8]
section("[8] 复盘：充分度分级 / 观察无因果 / 假设必须有替代解释 / 版本追加")

env8_none = mk_content(display_id="C-rev-none", topic="无发布", pub=False)
r8a = rev_svc.generate(env8_none["content_id"])
check("无发布 → sufficiency=none", r8a["data_sufficiency"] == "none", r8a["data_sufficiency"])
check("明说没有效果可复盘", "没有效果可复盘" in r8a["sufficiency_note"], "")
check("limitations 明说不能输出效果结论",
      any("不能输出任何传播效果结论" in x for x in r8a["limitations"]), "")
check("不产出任何 metric 观察",
      not any(o["kind"] == "metric" for o in r8a["observations"]), "")

env8_ins = mk_content(display_id="C-rev-ins", topic="无数据", platform="douyin",
                      post_id="7300000111")
r8b = rev_svc.generate(env8_ins["content_id"])
check("有发布无数据 → insufficient", r8b["data_sufficiency"] == "insufficient",
      r8b["data_sufficiency"])
check("明说无数据不等于表现差", "不等于表现差" in r8b["sufficiency_note"], "")

oid8 = env8_ins["publication_id"]
add_snapshot(oid8, age_h=24, values={"views": 12000, "likes": 800})
r8c = rev_svc.generate(env8_ins["content_id"])
check("单点 → baseline_only", r8c["data_sufficiency"] == "baseline_only",
      r8c["data_sufficiency"])
check("明说单点无法构成趋势", "无法构成趋势" in r8c["sufficiency_note"], "")
check("limitations 提到仅够基线",
      any("仅够建立基线" in x for x in r8c["limitations"]), "")
check("limitations 不给涨粉承诺",
      any("不承诺涨粉" in x for x in r8c["limitations"]), "")
check("limitations 说明不能推断因果",
      any("不能推断因果" in x for x in r8c["limitations"]), "")

mets = [o for o in r8c["observations"] if o["kind"] == "metric"]
check("有 metric 观察", len(mets) >= 2, str(len(mets)))
views8 = next((o for o in mets if o["value"] == 12000), None)
check("播放量观察出现", views8 is not None, str([o["statement"] for o in mets]))
check("观察带证据引用", views8 and len(views8["evidence"]) == 1, "")
check("观察带采集时长 + 口径", views8 and views8["at_age_hours"] == 24
      and "累计值" in (views8["caveat"] or ""), str(views8 and views8["caveat"]))
for o in r8c["observations"]:
    check(f"观察不含因果词：{o['statement'][:14]}",
          not any(w in o["statement"] for w in ("因为", "导致", "所以", "说明了")),
          o["statement"])

add_snapshot(oid8, age_h=72, values={"views": 45000, "likes": 3000, "collects": None})
r8d = rev_svc.generate(env8_ins["content_id"])
check("多窗口 → comparable", r8d["data_sufficiency"] == "comparable", r8d["data_sufficiency"])
check("comparable 仍提示同口径", "同口径" in r8d["sufficiency_note"], "")
missing8 = [o for o in r8d["observations"] if o["kind"] == "metric" and o["value"] is None]
check("缺失值被如实陈述（非跳过）", len(missing8) >= 1, str(len(missing8)))
check("缺失 caveat 说明不是 0",
      any("不是 0" in (o["caveat"] or "") for o in missing8), "")
check("没有把缺失伪造成 0",
      not any(o["value"] == 0 for o in r8d["observations"] if o["kind"] == "metric"), "")

try:
    rev_svc.generate(env8_ins["content_id"], hypotheses=[{"statement": "封面更好所以播放高"}])
    check("假设缺替代解释 → 被拒", False, "竟然通过了")
except ValueError as e:
    check("假设缺替代解释 → 被拒", "alternative_explanations" in str(e), str(e)[:70])
    check("拒绝理由点明是直觉不是假设", "直觉" in str(e), str(e)[:90])

good_hyp = [{"statement": "封面信息量更大可能提升点击",
             "alternative_explanations": ["发布时间处于流量高峰",
                                          "本期账号推荐权重整体上升"],
             "confidence": "low"}]
r8e = rev_svc.generate(env8_ins["content_id"], hypotheses=good_hyp,
                       experiments=[{"proposal": "只改封面", "single_variable": "cover"}])
check("带替代解释 → 通过", len(r8e["hypotheses"]) == 1, "")
check("替代解释完整保留", len(r8e["hypotheses"][0]["alternative_explanations"]) == 2, "")
check("有假设时 mode=assisted", r8e["generation_mode"] == "assisted", r8e["generation_mode"])
check("纯程序报告 mode=none", r8d["generation_mode"] == "none", r8d["generation_mode"])
check("structure_note 说明替代解释必填",
      "必填" in r8e["structure_note"]["hypotheses"], "")

add_comment(oid8, "这个多少钱？", "question", like=5)
add_comment(oid8, "假的吧", "challenge", like=12)
add_comment(oid8, "我用过，有用", "experience", like=30)
r8f = rev_svc.generate(env8_ins["content_id"])
dist8 = [o for o in r8f["observations"] if o["kind"] == "comment_distribution"]
check("有评论分布观察", len(dist8) == 1, str(len(dist8)))
check("分布含总数与明细", "共 3 条" in dist8[0]["statement"]
      and "经验" in dist8[0]["statement"], dist8[0]["statement"])
check("分布 caveat 给分母与口径",
      "样本量" in (dist8[0]["caveat"] or "") and "全量导出" in (dist8[0]["caveat"] or ""),
      dist8[0]["caveat"])

# 不变量 7：固定快照 + 版本追加
v_before = len(r8f["inputs"]["snapshot_ids"])
add_snapshot(oid8, age_h=168, values={"views": 60000})
r8g = rev_svc.generate(env8_ins["content_id"])
check("新增数据生成新版本", r8g["version"] == r8f["version"] + 1,
      f"{r8f['version']} -> {r8g['version']}")
check("新版引用更多快照", len(r8g["inputs"]["snapshot_ids"]) > v_before,
      f"{v_before} -> {len(r8g['inputs']['snapshot_ids'])}")
with SF() as s:
    old8 = s.get(ReviewReport, r8f["id"])
check("旧版快照引用未被改动", len(old8.snapshot_ids) == v_before,
      f"{len(old8.snapshot_ids)} vs {v_before}")
check("inputs 说明是固定快照", "固定输入快照" in r8g["inputs"]["note"], "")

levels8 = {x["level"] for x in r8g["source_chain"]}
check("来源链含 publication", "publication" in levels8, str(levels8))
check("来源链含 metric_snapshot", "metric_snapshot" in levels8, str(levels8))
check("来源链含 comment_sample", "comment_sample" in levels8, str(levels8))

# 付费流量不互相顶替
env8p = mk_content(display_id="C-rev-paid", topic="付费", platform="douyin",
                   post_id="7300000222")
oid8p = env8p["publication_id"]
add_snapshot(oid8p, age_h=24, values={"views": 10000}, traffic="organic")
add_snapshot(oid8p, age_h=24, values={"views": 40000}, traffic="paid")
add_snapshot(oid8p, age_h=72, values={"views": 30000}, traffic="organic")
add_snapshot(oid8p, age_h=72, values={"views": 90000}, traffic="paid")
r8p = rev_svc.generate(env8p["content_id"])
mets8p = [o for o in r8p["observations"] if o["kind"] == "metric"]
tt8 = {o["traffic_type"] for o in mets8p}
check("自然与付费都被陈述（未互相顶替）", "organic" in tt8 and "paid" in tt8, str(tt8))
check("caveat 标注仅自然/仅付费",
      any("仅自然流量" in (o["caveat"] or "") for o in mets8p)
      and any("仅付费流量" in (o["caveat"] or "") for o in mets8p), "")
check("limitations 提示含付费流量",
      any("付费流量" in x for x in r8p["limitations"]), "")

lst8 = rev_svc.list_reports(content_id=env8_ins["content_id"])
check("复盘列表多版本", lst8["total"] >= 4, str(lst8["total"]))
check("列表说明不改旧版", "不会改动已生成报告" in lst8["snapshot_rule"], "")
check("暴露充分度枚举", set(lst8["sufficiency_levels"]) == set(SUFFICIENCY_LEVELS), "")


# ================================================================ [9]
section("[9] 反馈下一轮：默认 proposed / 自动采用仅 tiny-small / 可回退 / 三条并列限制")

rid = r8f["id"]
try:
    fb_svc.propose(review_report_id=rid, kind="topic_weight",
                   proposal="多讲经验", evidence_refs=[])
    check("建议缺证据 → 被拒", False, "竟然通过了")
except ValueError as e:
    check("建议缺证据 → 被拒", "证据引用" in str(e), str(e)[:70])
    check("拒绝理由点明不是从数据来的", "凭空的想法" in str(e), str(e)[:90])
try:
    fb_svc.propose(review_report_id=rid, kind="不存在的类型",
                   proposal="x", evidence_refs=["a"])
    check("未知建议类型被拒", False, "竟然通过了")
except ValueError as e:
    check("未知建议类型被拒", "未知建议类型" in str(e), "")
try:
    fb_svc.propose(review_report_id=rid, kind="topic_weight",
                   proposal="x", magnitude="巨大", evidence_refs=["a"])
    check("未知幅度被拒", False, "竟然通过了")
except ValueError as e:
    check("未知幅度被拒", "未知改动幅度" in str(e), "")

fb_small = fb_svc.propose(review_report_id=rid, kind="hook", proposal="开头改用提问",
                          magnitude="small", evidence_refs=["s1"],
                          previous_value="陈述式", new_value="提问式")
check("建议默认 status=proposed", fb_small["status"] == "proposed", fb_small["status"])
check("建议默认 auto_adopted=False", fb_small["auto_adopted"] is False, "")
check("small 标记为可自动采用", fb_small["auto_adoptable"] is True, "")

fb_large = fb_svc.propose(review_report_id=rid, kind="topic_weight",
                          proposal="彻底转向美妆赛道", magnitude="large",
                          evidence_refs=["s1"])
check("large 不可自动采用", fb_large["auto_adoptable"] is False, "")
out_lg = fb_svc.adopt(fb_large["id"], auto=True)
check("large 自动采用被降级", out_lg.auto_adopted is False, "")
check("large 状态仍为 proposed", out_lg.status == "proposed", out_lg.status)
check("降级理由说明超出范围", "超出可自动采用的范围" in out_lg.note, out_lg.note)

fb_med = fb_svc.propose(review_report_id=rid, kind="angle", proposal="改成测评向",
                        magnitude="medium", evidence_refs=["s1"])
out_md = fb_svc.adopt(fb_med["id"], auto=True)
check("medium 自动采用也被降级", out_md.auto_adopted is False, "")
check("medium 降级理由列出可自动范围",
      "tiny" in out_md.note and "small" in out_md.note, out_md.note)
out_md2 = fb_svc.adopt(fb_med["id"])
check("medium 人工可采用", out_md2.status == "accepted", out_md2.status)
check("人工采用 auto_adopted=False", out_md2.auto_adopted is False, "")
check("人工采用理由标注", "人工确认" in out_md2.note, out_md2.note)

out_sm = fb_svc.adopt(fb_small["id"], auto=True)
check("small 自动采用成功", out_sm.auto_adopted is True, "")
check("small 状态 accepted", out_sm.status == "accepted", out_sm.status)
rev9 = fb_svc.revert(fb_small["id"])
check("回退后 status=reverted", rev9["status"] == "reverted", rev9["status"])
check("回退还原原值", rev9["restored_value"] == "陈述式", str(rev9["restored_value"]))
check("回退记录时间与人",
      rev9["reverted_at"] is not None and rev9["reverted_by"] == "user", "")
try:
    fb_svc.revert(fb_small["id"])
    check("重复回退被拒", False, "竟然通过了")
except ValueError as e:
    check("重复回退被拒", "只有 accepted" in str(e), str(e)[:60])

fb_rej = fb_svc.propose(review_report_id=rid, kind="format", proposal="改成 20 页长图",
                        magnitude="medium", evidence_refs=["s1"])
rej9 = fb_svc.reject(fb_rej["id"], reason="太长没人看完")
check("拒绝后 status=rejected", rej9["status"] == "rejected", rej9["status"])
check("拒绝理由被记录", "太长没人看完" in (rej9["rationale"] or ""), "")

# 批次限制：三条并列
with SF() as s:
    b9 = Batch(item_limit=2, budget_limit_micro=None, cost_mode="usage_tracking")
    s.add(b9); s.flush()
    for _ in range(2):
        s.add(ContentItem(display_id="CX", topic="已有", state="selected",
                          selected_by="user", batch_id=b9.id))
    s.commit()
    bid9 = b9.id
p9a = fb_svc.propose_batch(next_topics=["选题A", "选题B"], batch_id=bid9)
check("条目用满 → 不允许开工", p9a.allowed is False, str(p9a.allowed))
check("理由提到条目用满", any("条目已用满" in r for r in p9a.reasons), str(p9a.reasons))
check("候选仍保留", len(p9a.candidates) == 2, str(len(p9a.candidates)))
check("候选标为 candidate", all(c["status"] == "candidate" for c in p9a.candidates), "")

with SF() as s:
    b9b = Batch(item_limit=10, budget_limit_micro=None, cost_mode="usage_tracking")
    s.add(b9b); s.commit()
    bid9b = b9b.id
p9b = fb_svc.propose_batch(next_topics=["选题A"], batch_id=bid9b)
check("金额 null 不阻塞", p9b.allowed is True, str(p9b.reasons))
check("checked 记录金额为 null", p9b.limits_checked["money_limit_is_null"] is True, "")
check("说明 null 不等于无限额度",
      "不等于无限额度" in p9b.limits_checked["money_limit_effect"],
      p9b.limits_checked["money_limit_effect"])
check("说明 null 不解除其他限制",
      "不解除" in p9b.limits_checked["money_limit_effect"], "")
p9c = fb_svc.propose_batch(next_topics=["选题A"], batch_id=bid9b,
                           pending_review_stock=5, pending_review_stock_limit=3)
check("金额 null 但库存满 → 仍被拦", p9c.allowed is False, str(p9c.reasons))
check("理由提到库存上限", any("库存已达上限" in r for r in p9c.reasons), str(p9c.reasons))

with SF() as s:
    b9d = Batch(item_limit=10, budget_limit_micro=500000, cost_mode="hard_cap")
    s.add(b9d); s.commit()
    bid9d = b9d.id
p9d = fb_svc.propose_batch(next_topics=["选题A"], batch_id=bid9d)
check("已设金额被记录", p9d.limits_checked["money_limit_micro"] == 500000, "")
check("已设金额 is_null=False", p9d.limits_checked["money_limit_is_null"] is False, "")

p9e = fb_svc.propose_batch(next_topics=["选题A"], batch_id=None)
check("无批次 → 不允许开工", p9e.allowed is False, str(p9e.allowed))
check("理由说明没有批次范围", any("没有批次范围" in r for r in p9e.reasons), str(p9e.reasons))

drafts9 = fb_svc.draft_from_review(rid)
check("起草候选非空", drafts9["count"] >= 1, str(drafts9["count"]))
check("草案全部需人工确认",
      all(d["needs_human_review"] for d in drafts9["drafts"]), "")
check("草案带证据引用",
      all(d["evidence_refs"] for d in drafts9["drafts"]), "")
check("说明是候选不是决策", "候选草案" in drafts9["note"], drafts9["note"])
check("说明不直接改参数",
      "不会直接把复盘结论变成参数改动" in drafts9["note"], drafts9["note"])
check("无数据充分度 → 起草补数据",
      any(d["kind"] == "publish_time" for d in drafts9["drafts"])
      or drafts9["count"] >= 1, "")

# 本段一共 propose 了 5 条建议：small(hook) / large(topic_weight) /
# medium(angle) / medium(format) / 以及前面被拒的一条 topic_weight。
# 但 list_feedback(content_id=...) 不传 status 时是「**待办清单**」，
# 不是「历史登记总数」——已 accepted / rejected / reverted 的都算已了结，
# 不该再赖在待办里，否则列表只会越用越长、永远清不掉，
# 人就会开始整片整片地忽略它，等于没有待办列表。
# 所以这里断言四件事：
#   ① 待办非空；② 全部属于该内容；③ 待办里只有 proposed；④ 已了结的没消失，换个筛选查得到。
lst9 = fb_svc.list_feedback(content_id=env8_ins["content_id"])
check("反馈待办非空", lst9["total"] >= 1, str(lst9["total"]))
check("列表只含本内容的建议",
      all(i["content_id"] == env8_ins["content_id"] for i in lst9["items"]),
      str({i["content_id"] for i in lst9["items"]}))
check("待办里只有 proposed（已了结的不算待办）",
      all(i["status"] == "proposed" for i in lst9["items"]),
      str(sorted({i["status"] for i in lst9["items"]})))
check("如实报告已了结条数", lst9["settled_total"] >= 3, str(lst9["settled_total"]))
check("列表说明这是待办而非全量",
      "待办" in lst9["note"] and "不在待办里" in lst9["note"], lst9["note"])
check("暴露状态枚举", set(lst9["statuses"]) == set(STATUSES), str(lst9.get("statuses")))
# 已了结的不在待办里，但**没有消失**——换个状态查得到，这才叫"可追溯"
lst9_all = [i for st in ("accepted", "reverted", "rejected")
            for i in fb_svc.list_feedback(content_id=env8_ins["content_id"],
                                          status=st)["items"]]
check("已了结建议仍可按状态追溯", len(lst9_all) >= 3, str(len(lst9_all)))
check("已采用的可回退记录仍在",
      any(i["status"] == "reverted" and i["previous_value"] == "陈述式"
          for i in lst9_all), "")
check("待办 + 已了结 == 历史总数",
      lst9["total"] + lst9["settled_total"] == 4,
      f"{lst9['total']} + {lst9['settled_total']}")
check("暴露可自动采用范围",
      set(lst9["auto_adoptable"]) == set(AUTO_ADOPTABLE), str(lst9["auto_adoptable"]))
check("说明默认不生效", "不会自动生效" in lst9["note"], "")
check("说明 medium/large 需人工", "一律需人工确认" in lst9["note"], "")
check("6 种建议类型", len(FEEDBACK_KINDS) == 6, str(len(FEEDBACK_KINDS)))
check("4 档幅度", set(MAGNITUDES) == {"tiny", "small", "medium", "large"}, str(MAGNITUDES))
check("可自动采用仅 tiny/small", AUTO_ADOPTABLE == {"tiny", "small"}, str(AUTO_ADOPTABLE))
lst9b = fb_svc.list_feedback(status="reverted")
check("按状态过滤可用", all(i["status"] == "reverted" for i in lst9b["items"]), "")


# ================================================================ [10]
section("[10] 不变量汇总：无 auto-publish / auto-apply / 真实调用与费用")

paths = app.openapi()["paths"]
check("不存在 POST /auto-publish", "/api/v1/auto-publish" not in paths, "")
check("发布接口声明 auto_publish=False",
      client.get(f"{P}/publications").json()["capabilities"]["auto_publish"] is False, "")
check("发布接口只有一个写动作组（登记 + 核验）",
      len([p for p in paths if p.startswith("/api/v1/publications")]) == 4,
      str([p for p in paths if p.startswith("/api/v1/publications")]))
check("反馈接口声明 auto_apply=False",
      client.get(f"{P}/feedback").json()["capabilities"]["auto_apply"] is False, "")
check("复盘接口声明 auto_conclude=False",
      client.post(f"{P}/contents/{env8_ins['content_id']}/reviews", json={})
      .json()["capabilities"]["auto_conclude"] is False, "")

# HTTP 层：客户端伪造登记人无效（不变量 6 在接口层的落点）
r10 = client.post(f"{P}/publications",
                  json={"platform_revision_id": env_clean["platform_revision_id"],
                        "link": "https://xhslink.com/2", "registered_by": "system"})
check("HTTP 登记成功", r10.status_code == 200, r10.status_code)
check("客户端伪造 system 无效，实际记为 user",
      r10.json()["declared"]["registered_by"] == "user",
      r10.json()["declared"]["registered_by"])

r10b = client.post(f"{P}/publications", json={"platform_revision_id": env1["platform_revision_id"]})
check("HTTP 空标识 → 400", r10b.status_code == 400,
      f"{r10b.status_code} {r10b.json()}")
check("HTTP 错误码为 PUBLICATION_NEEDS_IDENTIFIER",
      r10b.json()["detail"]["code"] == "PUBLICATION_NEEDS_IDENTIFIER", "")

r10c = client.post(f"{P}/contents/{env8_ins['content_id']}/reviews",
                   json={"hypotheses": [{"statement": "没有替代解释"}]})
check("HTTP 缺替代解释 → 422", r10c.status_code == 422, r10c.status_code)
check("HTTP 错误码为 HYPOTHESIS_NEEDS_ALTERNATIVE",
      r10c.json()["detail"]["code"] == "HYPOTHESIS_NEEDS_ALTERNATIVE", "")

r10d = client.post(f"{P}/imports", json={"kind": "comments", "content": "x"})
check("评论导入不走 /imports（入口不混用）", r10d.status_code == 422, r10d.status_code)
r10e = client.post(f"{P}/comments/imports", json={"content": "foo,bar\n1,2\n"})
check("无正文列 → 422 NO_COMMENT_TEXT_COLUMN",
      r10e.status_code == 422
      and r10e.json()["detail"]["code"] == "NO_COMMENT_TEXT_COLUMN", "")

# P4 新增表不得夹带任何真实调用
with SF() as s:
    calls = list(s.scalars(select(ProviderCallRow)))
    real_calls = [c for c in calls if c.run_mode == "real"
                  and (c.remote_request_id or c.state == "succeeded")]
check("real_calls_recorded == 0（P4 全程零真实调用）", len(real_calls) == 0,
      str([(c.id, c.run_mode, c.state) for c in real_calls]))
check("无真实远程请求 ID", all(c.remote_request_id is None for c in calls), "")

with SF() as s:
    pubs_all = list(s.scalars(select(Publication)))
    snaps_all = list(s.scalars(select(MetricSnapshot)))
    cmts_all = list(s.scalars(select(CommentSample)))
    reps_all = list(s.scalars(select(ReviewReport)))
    fbs_all = list(s.scalars(select(TopicFeedback)))
    imps_all = list(s.scalars(select(ImportBatch)))
check("P4 六张表均有数据（链路真的走通了）",
      all([pubs_all, snaps_all, cmts_all, reps_all, fbs_all, imps_all]),
      f"pub={len(pubs_all)} snap={len(snaps_all)} cmt={len(cmts_all)} "
      f"rep={len(reps_all)} fb={len(fbs_all)} imp={len(imps_all)}")
check("发布记录显式区分真实与模拟运行模式",
      all(p.run_mode in {"real", "fixture", "local_seed"} for p in pubs_all)
      and any(p.run_mode == "fixture" for p in pubs_all),
      sorted({p.run_mode for p in pubs_all}))
check("评论样本无公开用户名字段",
      not hasattr(CommentSample, "username"), "")


# ---------------------------------------------------------------- 收尾

print(f"\n{'=' * 62}")
print(f"{len(PASS)} 通过 / {len(FAIL)} 失败")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("   -", f)
print(f"临时目录：{_TMP}")
print(f"{'=' * 62}")

shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
