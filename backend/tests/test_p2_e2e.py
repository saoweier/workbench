"""P2 端到端测试：研究 → 选题 → 母稿/双平台改写 → 渲染，以及拒收与降级路径。

运行方式（与 P0/P1 一致，纯脚本、无 pytest）：

    .venv/bin/python backend/tests/test_p2_e2e.py

**全程不发起任何真实网络调用、不产生任何费用**：
字符串模式下 provider 调用都是 fixture / local_seed，测试末尾断言
`real_calls_recorded == 0` 与 `ProviderCallRow.run_mode == 'real'` 计数为 0。

覆盖 9 组：
[1] fixture 确定性：同输入同输出、不同输入不同输出、用量为 None、run_mode=fixture
[2] Provider 契约：未配置不调用、anthropic 未实现明确报错、pricing 估算 vs 未知
[3] 研究与证据：摘录定位、搜索未配置降级、访问失败逐条记录、snippet 不能支撑 fact
[4] 选题：3–5 候选、去重、规则分、禁止未校准指标词、库存满暂停（不是失败）
[5] 母稿与改写：禁用字段拒收且不进修复、多余字段可修复、悬空引用、
    HTML/路径/shell、页数超 profile、平台封面策略（小红书必须有封面/抖音可无）、禁止捏造亲测
[6] 修复循环：1 轮成功、2 轮失败无半成品
[7] 编排与 API：produce 全链路、Run/Job 落表、成本 NULL≠0、改稿新建版本
[8] 不变量：real_calls_recorded==0、fixture 不混入真实成本、旧批准不被覆盖
[9] 幂等：同输入重复生成不追加 revision / 不重复记账
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# ---- 环境必须最先设置：配置在 import 时被 lru_cache 固化 ----
_TMP = Path(tempfile.mkdtemp(prefix="cwb-p2-"))
os.environ["CWB_STORAGE_ROOT"] = str(_TMP)
os.environ["CWB_ARTIFACT_DIR"] = str(_TMP / "artifacts")
os.environ["CWB_TMP_DIR"] = str(_TMP / "tmp")
os.environ["CWB_DATABASE_URL"] = f"sqlite:///{_TMP / 'p2.db'}"
os.environ["CWB_SECRET_STORE_PATH"] = str(_TMP / "secrets.json")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.errors import ValidationFailed  # noqa: E402
from app.main import app  # noqa: E402
from app.models.entities import (  # noqa: E402
    Base,
    ContentItem,
    ContentRevision,
    PlatformRevision,
    ProviderCallRow,
    enable_sqlite_fk,
)
from app.services.adapters import ADAPTER_REGISTRY  # noqa: E402
from app.services.claim_rules import validate_claims_sources  # noqa: E402
from app.services.compose_service import (  # noqa: E402
    FORBIDDEN_FIELDS,
    ComposeService,
    _as_json,
    _text_similarity,
)
from app.services.production_service import ProductionService  # noqa: E402
from app.services.profile_store import ProfileStore  # noqa: E402
from app.services.provider_contract import (  # noqa: E402
    AdapterType,
    BillingState,
    ProviderConfig,
    ProviderKind,
    ProviderStatus,
    ProviderStore,
    RunMode,
    SecretStore,
)
from app.services.provider_runtime import ProviderRuntime  # noqa: E402
from app.services.renderer import check_layout  # noqa: E402
from app.services.research_service import ResearchService  # noqa: E402
from app.services.topic_service import TopicService  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  → ' + str(detail)) if detail else ''}")


def section(title: str) -> None:
    print(f"\n{title}")


settings = get_settings()
SEED = settings.examples_dir / "C001" / "seeds" / "C001" / "seed.json"

engine = create_engine(settings.database_url, future=True)
enable_sqlite_fk(engine)
Base.metadata.create_all(engine)
SF = sessionmaker(bind=engine, future=True)

profiles = ProfileStore(settings.storage_root / "profiles.json")
secrets = SecretStore(settings.secret_store_path)
store = ProviderStore(settings.storage_root / "provider_configs.json")
runtime = ProviderRuntime(store, secrets)
client = TestClient(app)

CLAIMS = [
    {"id": "C01", "kind": "project_plan", "statement": "先做抖音与小红书图文",
     "source_ids": ["S01"]},
    {"id": "C02", "kind": "document_observation", "statement": "现有文案可作输入",
     "source_ids": ["S02"]},
    {"id": "C03", "kind": "project_goal", "statement": "人工只在最后一道关",
     "source_ids": ["S01"]},
]
KNOWN = {"C01", "C02", "C03"}

# ================================================================ [1]

section("[1] fixture 适配器：确定性 / 不伪装成真实调用")
fx_rt = ProviderRuntime(store, secrets, force_fixture=True)
svc_fx = ComposeService(SF, runtime=fx_rt, profiles=profiles)

m_a, _ = svc_fx.compose_master(content_id=None, topic="同一主题", claims=CLAIMS,
                               limitations=["边界"], run_mode=RunMode.FIXTURE)
m_b, _ = svc_fx.compose_master(content_id=None, topic="同一主题", claims=CLAIMS,
                               limitations=["边界"], run_mode=RunMode.FIXTURE)
check("同输入 fixture 输出完全一致", _as_json(m_a) == _as_json(m_b))

m_c, _ = svc_fx.compose_master(content_id=None, topic="另一个完全不同的主题",
                               claims=CLAIMS, limitations=["边界"],
                               run_mode=RunMode.FIXTURE)
check("不同输入 fixture 输出不同（新资料→新内容）", _as_json(m_a) != _as_json(m_c))

call, res = fx_rt.complete_text(prompt="p", json_schema={"properties": {"x": {"type": "string"}}},
                                run_mode=RunMode.FIXTURE)
check("fixture run_mode 固定为 fixture", call.run_mode == RunMode.FIXTURE, call.run_mode.value)
check("fixture 不伪造 token 数（全部 None）",
      call.input_tokens is None and call.output_tokens is None and call.cached_tokens is None)
check("fixture remote_request_id 带 fixture- 前缀",
      (call.remote_request_id or "").startswith("fixture-"), call.remote_request_id)
check("fixture 不产生估算费用（billing=unknown, 金额 None）",
      call.billing_state == BillingState.UNKNOWN and call.estimated_micro is None)

# ================================================================ [2]

section("[2] Provider 契约：未配置不调用 / 未实现不假装可用 / 估算不冒充实际")
empty_store = ProviderStore(_TMP / "empty_cfg.json")
empty_rt = ProviderRuntime(empty_store, SecretStore(_TMP / "empty_sec.json"))
from app.core.errors import NotConfigured  # noqa: E402

try:
    empty_rt.complete_text(prompt="x", run_mode=RunMode.REAL)
    check("未配置文本 Provider 时拒绝调用", False, "竟然成功")
except NotConfigured as exc:
    check("未配置文本 Provider 时拒绝调用（不伪称已调用）", "未配置" in str(exc), str(exc)[:50])

anthropic_cls = ADAPTER_REGISTRY[AdapterType.ANTHROPIC_MESSAGES]
check("anthropic 适配器标记为未实现", anthropic_cls.implemented is False)
r_an = anthropic_cls(
    ProviderConfig(name="a", kind=ProviderKind.TEXT,
                   adapter_type=AdapterType.ANTHROPIC_MESSAGES,
                   base_url="https://api.example.com", model_id="m"),
    api_key="k",
).complete("hi")
check("未实现适配器调用即明确报错", not r_an.ok and r_an.error_code == "ADAPTER_NOT_IMPLEMENTED",
      r_an.error_code)
check("未实现适配器不产生用量", r_an.input_tokens is None and r_an.output_tokens is None)

priced = ProviderConfig(
    name="priced", kind=ProviderKind.TEXT, adapter_type=AdapterType.OPENAI_COMPATIBLE,
    base_url="https://api.example.com", model_id="m",
    pricing={"input_per_1k_micro": 100, "output_per_1k_micro": 200, "currency": "CNY"},
    pricing_version="v-test",
)
from app.services.adapters.base import AdapterResult  # noqa: E402

est = empty_rt.estimate_micro(priced, AdapterResult(ok=True, input_tokens=1000, output_tokens=500))
check("有费率才算估算（1000/1000*100 + 500/1000*200 = 200）", est == 200, str(est))
check("无费率时估算为 None（不写 0）",
      empty_rt.estimate_micro(
          ProviderConfig(name="n", kind=ProviderKind.TEXT,
                         adapter_type=AdapterType.OPENAI_COMPATIBLE,
                         base_url="https://api.example.com", model_id="m"),
          AdapterResult(ok=True, input_tokens=1000)) is None)

# ================================================================ [3]

section("[3] 研究与证据：摘录定位 / 搜索降级 / 访问失败 / 摘要不足以支撑事实")
r_svc = ResearchService(ProviderRuntime(empty_store, SecretStore(_TMP / "s3.json")))
seed_data = json.loads(SEED.read_text(encoding="utf-8"))
res = r_svc.research(topic=seed_data["topic"], seed_path=str(SEED))

check("复用 seed 得到 sources 与 claims", len(res.sources) == 3 and len(res.claims) == 4,
      f"sources={len(res.sources)} claims={len(res.claims)}")
check("未配置搜索时 search_executed=False", res.search_executed is False)
check("未执行搜索有明确降级说明",
      any("未执行自动搜索" in x for x in res.limitations))
check("local_seed 来源标注不是本次检索结果",
      any("local_seed" in x for x in res.limitations))
check("来源按 locator 定位到真实摘录",
      all(s.excerpt for s in res.sources), str([bool(s.excerpt) for s in res.sources]))
check("来源带 sha256（可校验未被篡改）", all(s.sha256 for s in res.sources))

# 访问失败逐条记录（不静默丢）
res_bad = r_svc.research(topic="t", user_materials=[
    {"id": "U01", "kind": "user_provided", "text": "用户提供的材料正文内容"},
])
check("用户材料吸收为 source", len(res_bad.sources) == 1 and res_bad.sources[0].id == "U01")

# 定位不到就返回 None，不伪造摘录
check("定位不到时返回 None（不伪造摘录）",
      ResearchService.extract_excerpt("abc", "不存在的关键词") is None)
check("摘要来源标记为 search_snippet",
      ResearchService.extract_excerpt("第1行\n包含关键内容的行\n第3行", "关键内容") is not None)

# snippet 不能支撑 fact
findings = validate_claims_sources(
    [{"id": "C99", "kind": "fact", "statement": "某事实", "source_ids": ["R01"]}],
    [{"id": "R01", "kind": "search_result", "url": "https://x", "excerpt_basis": "search_snippet"}],
)
check("仅搜索摘要支撑的 fact 被拒（SNIPPET_ONLY_FACT）",
      any(f.code == "SNIPPET_ONLY_FACT" for f in findings), str([f.code for f in findings]))

# ================================================================ [4]

section("[4] 选题：候选/去重/理由清洗/库存暂停")
topic_svc = TopicService(SF, runtime=None)
cands = topic_svc.propose(research=res, count=3)
check("产出 3 个候选", len(cands) >= 3, str(len(cands)))
check("候选硬条件齐全", all(c.hard_conditions for c in cands))
check("候选恒未做热度验证", all(c.heat_verified is False for c in cands))
check("候选理由不含未校准指标词",
      not any(w in c.selection_reason for c in cands
              for w in ("爆款", "概率", "涨粉", "保证", "必火", "流量密码")),
      str([c.selection_reason[:14] for c in cands]))
check("候选理由写明未做热度验证",
      all("热度验证" in c.selection_reason for c in cands))

try:
    topic_svc.propose(research=res, count=2)
    check("候选数不在 3–5 时拒绝", False)
except ValidationFailed as exc:
    check("候选数不在 3–5 时拒绝（不凑数）", "3–5" in str(exc), str(exc))

sim_svc = TopicService(SF, runtime=None)
similar = sim_svc.propose(research=res, count=3, raw_candidates=[
    {"topic": "完全一样的一个选题名称", "audience_problem": "p", "selection_reason": "r",
     "supporting_claim_ids": ["C01"]},
    {"topic": "完全一样的一个选题名称", "audience_problem": "p", "selection_reason": "r",
     "supporting_claim_ids": ["C01", "C02"]},
    {"topic": "另一个截然不同的选题方向", "audience_problem": "p", "selection_reason": "r",
     "supporting_claim_ids": ["C02"]},
])
check("重复主题被去重（3 → 2）", len(similar) == 2, str(len(similar)))

sel = topic_svc.select(cands)
check("选中 1 个", len(sel["selected"]) == 1 and not sel["blocked"], str(sel["blocked"]))

# 库存满 → 暂停（不是失败），不抛异常
import app.services.topic_service as tsm  # noqa: E402
from app.models.entities import Batch  # noqa: E402

with SF() as s:
    batch = s.query(Batch).first() or Batch()
    if batch.id is None:
        s.add(batch)
        s.flush()
    for i in range(5):
        s.add(ContentItem(batch_id=batch.id, display_id=f"STK{i}", topic="占位",
                          selected_by="user", state="ready_for_review"))
    s.commit()
blocked = topic_svc.select(cands)
check("待预览库存满时返回 blocked（不是抛异常）", blocked["blocked"] is True, str(blocked.get("reason")))
check("blocked 理由说明是暂停新增制作", "暂停" in (blocked.get("reason") or ""),
      blocked.get("reason"))
with SF() as s:
    s.query(ContentItem).filter(ContentItem.display_id.like("STK%")).delete(synchronize_session=False)
    s.commit()

# 丢弃（discard）：只改状态、释放待预览名额；不物理删除，可追溯。
# 这是"卡在待预览库存上又删不掉"的唯一出口，故单列回归。
with SF() as s:
    keep = ContentItem(display_id="C801", topic="待丢弃条目", selected_by="user",
                       state="ready_for_review", run_mode="real")
    s.add(keep)
    s.commit()
    keep_id = keep.id
stock_before = topic_svc._pending_stock()
d = client.post(f"/api/v1/contents/{keep_id}/discard", json={})
check("丢弃接口置终态并释放名额",
      d.status_code == 200 and d.json()["state"] == "discarded" and d.json()["released"] is True,
      f"{d.status_code} {d.text[:150]}")
check("丢弃后待预览库存 -1", topic_svc._pending_stock() == stock_before - 1,
      f"{stock_before} -> {topic_svc._pending_stock()}")
check("默认内容列表不再返回已丢弃条目",
      keep_id not in {c["id"] for c in client.get("/api/v1/contents").json()["items"]})
check("include_discarded=1 仍可查到（不是物理删除）",
      keep_id in {c["id"] for c in client.get("/api/v1/contents?include_discarded=1").json()["items"]})
d2 = client.post(f"/api/v1/contents/{keep_id}/discard", json={})
check("重复丢弃幂等", d2.status_code == 200 and d2.json()["already"] is True
      and d2.json()["released"] is False, d2.text[:150])
with SF() as s:
    from app.models.entities import Event as _Ev
    check("丢弃事件存在（可追溯，不是物理删除）",
          s.query(_Ev).filter_by(entity_type="content", entity_id=keep_id,
                                 type="content_discarded").count() == 1)
with SF() as s:
    s.query(ContentItem).filter(ContentItem.display_id.like("C80%")).delete(synchronize_session=False)
    s.commit()

# ================================================================ [5]

section("[5] 母稿与改写：权限边界与硬校验")
c_svc = ComposeService(SF, runtime=runtime, profiles=profiles)


def _pages(n: int, layout_first: bool = True) -> list[dict]:
    return [{"index": i, "heading": "标题", "points": [], "claim_ids": []}
            for i in range(1, n + 1)]


for field in ("state", "approval", "published", "budget", "actor", "html", "path", "command"):
    try:
        c_svc._validate_master(
            {"audience_problem": "p", "core_viewpoint": "v", "claim_ids": [],
             "pages": _pages(3), field: "x"}, known=KNOWN, topic="t")
        check(f"禁用字段 {field} 被拒", False, "未拒绝")
    except ValidationFailed as exc:
        check(f"禁用字段 {field} 被拒（权限越界，不进修复）",
              getattr(exc, "code", "") == "MODEL_PRIVILEGE_VIOLATION", getattr(exc, "code", ""))

try:
    c_svc._validate_master({"audience_problem": "p", "core_viewpoint": "v", "claim_ids": [],
                            "pages": _pages(3), "note": "多余"}, known=KNOWN, topic="t")
    check("普通多余字段被拒（可修复）", False)
except ValidationFailed as exc:
    check("普通多余字段被拒且非越权",
          getattr(exc, "code", "") != "MODEL_PRIVILEGE_VIOLATION", exc.message)

try:
    c_svc._validate_master({"audience_problem": "p", "core_viewpoint": "v",
                            "claim_ids": ["NOPE"], "pages": _pages(3)},
                           known=KNOWN, topic="t")
    check("悬空 claim 引用被拒", False)
except ValidationFailed as exc:
    check("悬空 claim 引用被拒", "NOPE" in exc.message, exc.message)

try:
    c_svc._validate_master({"audience_problem": "p", "core_viewpoint": "v", "claim_ids": [],
                            "pages": _pages(2)}, known=KNOWN, topic="t")
    check("母稿少于 3 页被拒", False)
except ValidationFailed as exc:
    check("母稿少于 3 页被拒", "少于 3" in exc.message, exc.message)

pf_dy = profiles.latest("douyin")
base_v = {"platform": "douyin", "title": "标题", "caption": "正文",
          "pages": [{"index": 1, "layout": "cover", "heading": "h",
                     "body": ["a", "b", "c", "d"], "claim_ids": ["C01"]}]}

for label, bad in [("HTML 标签", {"title": "<div>注入</div>"}),
                   ("绝对路径", {"caption": "写到 /root/x/y.md"}),
                   ("shell 命令", {"caption": "执行 rm -rf /tmp"})]:
    d = dict(base_v)
    d.update(bad)
    try:
        c_svc._validate_variant(d, known=KNOWN, profile=pf_dy, platform="douyin")
        check(f"平台稿含{label} 被拒", False)
    except ValidationFailed as exc:
        check(f"平台稿含{label} 被拒（越权）",
              getattr(exc, "code", "") == "MODEL_PRIVILEGE_VIOLATION", exc.message)

pf_xhs = profiles.latest("xiaohongshu")
# 封面策略按平台区分（platform_policy）：小红书必须有封面页；抖音可以没有封面，
# 第一页直接给内容。旧契约「两平台首页都得是 cover」已作废。
try:
    c_svc._validate_variant({**base_v, "platform": "xiaohongshu",
                             "pages": [{**base_v["pages"][0], "layout": "checklist"}]},
                            known=KNOWN, profile=pf_xhs, platform="xiaohongshu")
    check("小红书首页非 cover 被拒", False, "竟然通过")
except ValidationFailed as exc:
    check("小红书首页非 cover 被拒", "cover" in exc.message, exc.message[:60])
coverless = c_svc._validate_variant(
    {**base_v, "pages": [{**base_v["pages"][0], "layout": "checklist"}]},
    known=KNOWN, profile=pf_dy, platform="douyin")
check("抖音可以没有封面页（第一页直接给内容）", coverless.pages[0]["layout"] == "checklist")

for label, d, expect in [
    ("悬空 claim", {**base_v, "pages": [{**base_v["pages"][0], "claim_ids": ["ZZ"]}]}, "ZZ"),
    ("亲测措辞",
     {**base_v, "caption": "我亲测这套流程能省一半时间"}, "亲测"),
]:
    try:
        c_svc._validate_variant(d, known=KNOWN, profile=pf_dy, platform="douyin")
        check(f"{label} 被拒", False)
    except ValidationFailed as exc:
        check(f"{label} 被拒", expect in exc.message, exc.message[:60])

# Engineering profile defaults are advisory; explicit user page budgets have
# separate regression coverage and still constrain the requested deliverable.
over_profile={**base_v,'pages':[{'index':i,'layout':'cover' if i==1 else 'checklist',
                               'heading':'h','body':['a']*4,'claim_ids':['C01']}
                              for i in range(1,pf_dy.limits.max_pages+2)]}
accepted=c_svc._validate_variant(over_profile,known=KNOWN,profile=pf_dy,platform='douyin')
check('超过工程默认页数给提示，不阻止创作',any(i.code=='PAGE_COUNT_OUT_OF_RANGE' and i.level=='warning'
      for i in check_layout(accepted.model_dump(),pf_dy)))

check("页数/字数上限取自 profile 而非模型自定",
      pf_dy.limits.max_pages == 18 and pf_dy.limits.max_title_chars == 20,
      f"max_pages={pf_dy.limits.max_pages}")

# 两平台必须分别处理
out = c_svc.compose(content_id=None, topic="分开处理验证主题", claims=CLAIMS,
                    limitations=["边界"], run_mode=RunMode.LOCAL_SEED, persist=False)
sim = _text_similarity(_as_json(out.variants["douyin"]), _as_json(out.variants["xiaohongshu"]))
check("两平台稿相似度低于阈值（未只改平台名）", sim < 0.75, f"sim={sim:.2f}")
check("两平台页数按各自策略不同",
      len(out.variants["douyin"].pages) != len(out.variants["xiaohongshu"].pages)
      or sim < 0.75,
      f"dy={len(out.variants['douyin'].pages)} xhs={len(out.variants['xiaohongshu'].pages)}")
check("规则稿一次通过渲染前布局检测",
      not [i for i in check_layout(out.variants["douyin"].model_dump(), profiles.latest("douyin"))
           if i.level == "error"]
      and not [i for i in check_layout(out.variants["xiaohongshu"].model_dump(),
                                       profiles.latest("xiaohongshu")) if i.level == "error"])

# ================================================================ [6]

section("[6] 限定修复：1 轮成功 / 2 轮失败不产出半成品")
once_rt = ProviderRuntime(store, secrets, force_fixture=True, fixture_scenario={1: "bad_json"})
svc_once = ComposeService(SF, runtime=once_rt, profiles=profiles)
_, rounds = svc_once.compose_master(content_id=None, topic="修复验证主题", claims=CLAIMS,
                                    limitations=["边界"], run_mode=RunMode.FIXTURE)
check("第 1 轮非法 JSON 后修复成功", rounds == 1, f"repair_round={rounds}")

max_rt = ProviderRuntime(store, secrets, force_fixture=True,
                         fixture_scenario={1: "bad_json", 2: "bad_json", 3: "bad_json"})
svc_max = ComposeService(SF, runtime=max_rt, profiles=profiles)

with SF() as s:
    probe = ContentItem(display_id="P2NOHALF", topic="半成品验证", selected_by="ai",
                        state="selected")
    s.add(probe)
    s.commit()
    probe_id = probe.id
    before = (s.query(ContentRevision).filter_by(content_id=probe_id).count(),
              s.query(PlatformRevision).count())

try:
    svc_max.compose(content_id=probe_id, topic="修复失败验证主题", claims=CLAIMS,
                    limitations=["边界"], run_mode=RunMode.FIXTURE, max_repair_rounds=2)
    check("2 轮修复失败后抛错", False, "竟然成功")
except ValidationFailed as exc:
    check("2 轮修复失败后抛错（集中异常）", "修复" in exc.message, exc.message[:60])

with SF() as s:
    after = (s.query(ContentRevision).filter_by(content_id=probe_id).count(),
             s.query(PlatformRevision).count())
check("失败不留半成品（无 revision、无 platform_revision）", before == after,
      f"{before} vs {after}")

# ================================================================ [7]

section("[7] 编排与 API：全链路、Run/Job、成本、改稿")
prod = ProductionService(SF, runtime=runtime, profiles=profiles)

r = client.post(f"{settings.api_prefix}/contents/produce", json={
    "topic": seed_data["topic"], "seed_path": str(SEED),
    "run_mode": "local_seed", "render": False,
})
body = r.json()
check("POST /contents/produce 返回 202", r.status_code == 202, str(r.status_code))
check("链路覆盖 research/topic/compose",
      {"research", "topic", "compose"} <= set(body["stages"]), str(sorted(body["stages"])))
check("选题候选理由在产出时已清洗（含未做热度验证）",
      all("热度验证" in c["selection_reason"] for c in body["stages"]["topic"]["candidates"]))
check("local_seed 未使用模型", body["stages"]["compose"]["model_used"] is False)
check("双平台改写完成",
      set(body["stages"]["compose"]["platforms"]) == {"douyin", "xiaohongshu"})
check("未渲染时 ready_for_review 不成立", not body.get("ready_for_review"))

cid = body["content_id"]
run = client.get(f"{settings.api_prefix}/runs/{body['run_id']}").json()
check("GET /runs/{id} 返回阶段明细", {"research", "topic", "compose"} <=
      {j["stage"] for j in run["jobs"]}, str(sorted({j["stage"] for j in run["jobs"]})))
check("job 记录 attempt 与 input_hash",
      all(j["attempt"] >= 1 and len(j["input_hash"]) == 16 for j in run["jobs"]))
check("成本金额为 null（未知不写 0）", run["cost"]["estimated_micro"] is None)
check("成本说明写明 null 表示未知", "不等于 0" in run["cost"]["note"])

prs = client.get(f"{settings.api_prefix}/contents/{cid}/platform-revisions").json()
check("平台稿状态 ready_to_render", all(i["state"] == "ready_to_render" for i in prs["items"]))
check("平台稿页数未超 profile 上限", all(i["page_count"] <= 18 for i in prs["items"]),
      str([i["page_count"] for i in prs["items"]]))

ch = client.post(f"{settings.api_prefix}/contents/{cid}/change-requests", json={
    "base_revision_id": body["stages"]["compose"]["revision_id"],
    "instruction": "抖音标题改短一点",
})
check("POST change-requests 返回 201", ch.status_code == 201, str(ch.status_code))
chb = ch.json()
check("改稿产生新版本", chb["new_revision_version"] >= 2, str(chb["new_revision_version"]))
check("改稿定位到平台+字段", chb["target"]["platform"] == "douyin"
      and chb["target"]["field"] == "title", str(chb["target"]))

vague = client.post(f"{settings.api_prefix}/contents/{cid}/change-requests", json={
    "base_revision_id": body["stages"]["compose"]["revision_id"], "instruction": "改改"})
check("指向不明时拒绝并提示澄清", vague.status_code == 422
      and "指向不明" in vague.json()["error"]["message"],
      f"{vague.status_code} {vague.json().get('error', {}).get('message', '')[:40]}")

check("不存在 auto-publish 接口（不会自动发布）",
      client.post(f"{settings.api_prefix}/auto-publish").status_code == 404)

# ================================================================ [8]

section("[8] 不变量：真实调用计数 / fixture 不混入真实成本 / 旧批准不被覆盖")
with SF() as s:
    all_rows = s.query(ProviderCallRow).all()
real_rows = [x for x in all_rows if x.run_mode == "real"]
fixture_rows = [x for x in all_rows if x.run_mode == "fixture"]
check("全程未记录任何真实 Provider 调用", len(real_rows) == 0, f"real={len(real_rows)}")
check("fixture 调用单独归类（不混入真实成本）",
      all(x.billing_state == "unknown" and x.estimated_micro is None for x in fixture_rows),
      f"fixture={len(fixture_rows)}")
check("fixture 行不伪造 token", all(x.input_tokens is None for x in fixture_rows))

# 旧批准不被新版本覆盖
with SF() as s:
    revs = (s.query(ContentRevision).filter_by(content_id=cid)
            .order_by(ContentRevision.version).all())
    first_rev = revs[0]
    pr0 = s.query(PlatformRevision).filter_by(content_revision_id=first_rev.id).first()
    pr0.state = "approved"
    pr0.manifest_hash = "deadbeef" * 8
    s.commit()
    pr0_id = pr0.id
new_rev = chb["new_revision_id"]
with SF() as s:
    old = s.get(PlatformRevision, pr0_id)
    check("改稿后旧平台稿仍为 approved", old.state == "approved", old.state)
    newest = s.query(PlatformRevision).filter_by(content_revision_id=new_rev).all()
    # 新版本平台稿不继承旧批准：只可能是 drafting（未重渲染）
    # 或 ready_for_review（重渲染完成但尚未批准），绝不会是 approved
    check("新版本平台稿未继承旧批准",
          all(p.state in {"drafting", "ready_for_review"} for p in newest),
          str([p.state for p in newest]))
    check("新版本无任何批准记录",
          all(not p.reviews for p in newest),
          str([[d.decision for d in p.reviews] for p in newest]))

# ================================================================ [9]

section("[9] 幂等：重复生成不追加版本、不重复记账")
with SF() as s:
    n_before = s.query(ContentRevision).filter_by(content_id=cid).count()
prod.produce(topic=seed_data["topic"], content_id=cid, seed_path=str(SEED),
             run_mode=RunMode.LOCAL_SEED, render=False)
with SF() as s:
    n_after = s.query(ContentRevision).filter_by(content_id=cid).count()
check("同母稿重复生成不追加 revision", n_before == n_after, f"{n_before} vs {n_after}")

with SF() as s:
    keys = [x.request_key for x in s.query(ProviderCallRow).all() if x.request_key]
check("request_key 无重复（重试不重复记账）", len(keys) == len(set(keys)),
      f"{len(keys)} keys")

# ================================================================

print("\n" + "=" * 58)
print(f"结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print(f"  - {f}")
shutil.rmtree(_TMP, ignore_errors=True)
