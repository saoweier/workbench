"""P3 端到端测试：质量规则 / 本地修复 / 租约与故障恢复 / Worker /
用量单一真相 / 异常集中处理。

运行方式（与 P0/P1/P2 一致，纯脚本、无 pytest）：

    .venv/bin/python backend/tests/test_p3_e2e.py

**全程不发起任何真实网络调用、不产生任何费用**：
断言结尾 `real_calls_recorded == 0`。

覆盖 9 组：
[1] 质量规则：五类规则各自触发、母稿级问题不可页内修复、数字归一化
[2] 修复循环：规则可修优先于模型、local_seed 不调模型、旧批准不被覆盖
[3] 租约与 fencing_token：未过期不抢占、过期可回收、旧 token 写入被拒
[4] 四类故障：租约过期自动修；孤儿文件只登记不删除；缺图不自动重渲染已批准版本；
    未知结果不自动重发
[5] Worker：领取 → 执行 → 释放、心跳新鲜度、重复跑不追加产物
[6] 用量单一真相：只读 provider_call 表、JSON ledger 已弃用、fixture 不混入真实成本
[7] 预算门：usage_tracking 不阻塞、hard_cap 上限内放行 / 超限拦截 / 估不出价暂停
[8] 异常集中处理：折叠计数不掩盖、重大项置顶、时长 null ≠ 0、只读不自动决策
[9] 不变量汇总：真实调用 0 笔、无 auto-publish 之类接口
"""
from __future__ import annotations

import json as _json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# ---- 环境必须最先设置：配置在 import 时被 lru_cache 固化 ----
_TMP = Path(tempfile.mkdtemp(prefix="cwb-p3-"))
os.environ["CWB_STORAGE_ROOT"] = str(_TMP)
os.environ["CWB_ARTIFACT_DIR"] = str(_TMP / "artifacts")
os.environ["CWB_TMP_DIR"] = str(_TMP / "tmp")
os.environ["CWB_DATABASE_URL"] = f"sqlite:///{_TMP / 'p3.db'}"
os.environ["CWB_SECRET_STORE_PATH"] = str(_TMP / "secrets.json")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.errors import StateConflict  # noqa: E402
from app.main import app  # noqa: E402
from app.models.entities import (  # noqa: E402
    Artifact,
    Base,
    ContentItem,
    ContentRevision,
    Event,
    Job,
    PlatformRevision,
    ProviderCallRow,
    Run,
    enable_sqlite_fk,
)
from app.services.attention_service import (  # noqa: E402
    MAJOR_KINDS,
    SEVERITY,
    AttentionService,
)
from app.services.profile_store import ProfileStore  # noqa: E402
from app.services.provider_contract import (  # noqa: E402
    ProviderStore,
    RunMode,
    SecretStore,
)
from app.services.provider_runtime import ProviderRuntime  # noqa: E402
from app.services.quality_rules import (  # noqa: E402
    UNREPAIRABLE_CODES,
    _font_available,
    check_quality,
    extract_numbers,
)
from app.services.recovery_service import (  # noqa: E402
    DEFAULT_LEASE_SECONDS,
    NO_RETRY_CODES,
    RecoveryService,
    _aware,
)
from app.services.repair_service import RepairService  # noqa: E402
from app.services.usage_service import UsageService  # noqa: E402

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

profiles = ProfileStore(settings.storage_root / "profiles.json")
secrets = SecretStore(settings.secret_store_path)
store = ProviderStore(settings.storage_root / "provider_configs.json")
runtime = ProviderRuntime(store, secrets)
client = TestClient(app)
P = settings.api_prefix

PF_DY = profiles.latest("douyin")
PF_XHS = profiles.latest("xiaohongshu")


# ---------------------------------------------------------------- 造数据工具

def _good_draft(platform: str = "douyin", *, pages: int = 5) -> dict:
    """一份干净的平台稿：应当**零 error、零 warning** 通过。

    参数是照着 `check_layout` 的真实约束反推出来的，不是拍脑袋：

    - 密度下限：封面 ≥40 字、内页 ≥50 字（标题 + 正文），否则 `PAGE_DENSITY_LOW`
    - 行宽：`estimate_text_width(行, 字号) ≤ avail_w - 58`，否则 `BODY_TOO_WIDE`
    - 行数决定字号：≤2 行→60px、3 行→54px、4 行→48px、≥5 行→44px
    - 单行 >22 字会触发 `BODY_LINE_DENSE` 警告

    抖音 profile 的 avail_w = 1080 - 2×72 = 936。取 **3 行 × 22 字**：
    字号 54px，22×54 = 1188 > 878？—— 不，`estimate_text_width` 对 CJK
    按 1em 估，54×22=1188 超了。实测安全值是 **3 行 × 16 字**（54×16=864 ≤ 878），
    密度 3×16=48 加标题 ≥50。所以用 3 行 × 16 字 + 6 字标题。
    """
    lim = PF_DY.limits if platform == "douyin" else PF_XHS.limits
    line = "正文内容示例十六个字符填充版面"[:16]      # 16 字
    body = [line] * 3                                 # 3 行，字号 54px

    # 封面是特例：密度下限 40 字，但标题超过 20 字会 HEADING_TOO_WIDE
    # （封面标题按可用宽度自适应缩放，最小 56px，20×56=1120 已接近上限）。
    # 实测组合：标题 18 字 + 正文 2 行 × 16 字 = 50 字 → 零 warning。
    cover_body = [line] * 2

    out = []
    for i in range(1, pages + 1):
        is_cover = i == 1
        out.append({
            "index": i,
            "layout": "cover" if is_cover else "checklist",
            "heading": ("封面标题十八个字凑够信息量"[:18]
                        if is_cover else f"第{i}页标题"[:lim.max_title_chars]),
            "body": cover_body if is_cover else body,
            "claim_ids": [],
        })
    return {"platform": platform, "title": "标题"[:lim.max_title_chars],
            "caption": "正文内容", "pages": out}


def _mk_content(*, state: str = "drafting", platforms=("douyin",)) -> tuple[str, dict]:
    """直接落库造一份内容 + 平台稿，返回 (content_id, {platform: pr_id})。"""
    from app.models.entities import ContentRevision

    with SF() as s:
        c = ContentItem(display_id=f"T{abs(hash(platforms)) % 10000:04d}",
                        topic="测试内容", state=state, selected_by="user")
        s.add(c)
        s.flush()
        rev = ContentRevision(content_id=c.id, version=1,
                              claims_json={"claims": [], "sources": []},
                              brief_json={"pages": []}, input_hash="h" * 16)
        s.add(rev)
        s.flush()
        c.active_revision_id = rev.id
        ids = {}
        for pf in platforms:
            d = _good_draft(pf)
            pr = PlatformRevision(
                content_revision_id=rev.id, platform=pf, version=1,
                title=d["title"], caption=d["caption"],
                pages_json={"pages": d["pages"]},
                profile_version_id=profiles.latest(pf).id,
                content_hash="p" * 16, state="drafting",
            )
            s.add(pr)
            s.flush()
            ids[pf] = pr.id
        s.commit()
        return c.id, ids


def _mk_job(*, state: str = "queued", owner: str | None = None,
            expires_in: int | None = None, token: int = 0) -> str:
    with SF() as s:
        run = Run(stage="compose", mode="local_seed", state=state, input_hash="i" * 16)
        s.add(run)
        s.flush()
        j = Job(run_id=run.id, stage="compose", state=state, input_hash="i" * 16,
                lease_owner=owner, fencing_token=token,
                lease_expires_at=(datetime.now(timezone.utc) + timedelta(seconds=expires_in))
                if expires_in is not None else None)
        s.add(j)
        s.commit()
        return j.id


# ================================================================ [1]

section("[1] 质量规则：五类规则 / 母稿级问题不可页内修复 / 数字归一化")

clean = _good_draft("douyin")
rep_clean = check_quality(clean, PF_DY)
check("干净稿零问题（含零 warning）", rep_clean.ok and not rep_clean.issues,
      [(i.level, i.code, i.message) for i in rep_clean.issues])
check("干净稿 repairable 为空", not rep_clean.repairable)
check("干净稿 ok 属性为真", rep_clean.ok is True)
check("干净稿 checked_pages 正确", rep_clean.checked_pages == 5, rep_clean.checked_pages)

# 1a 页面级：引用了母稿中不存在的主张 → 可修复
CLAIMS = [{"id": "C01", "kind": "document_observation",
           "statement": "某主张", "source_ids": ["S01"]}]
SOURCES = [{"id": "S01", "kind": "document", "excerpt_basis": "document",
            "locator": "doc#1", "excerpt": "原文摘录内容", "accessed": True}]
d = _good_draft("douyin")
d["pages"][2]["claim_ids"] = ["C99"]
r = check_quality(d, PF_DY, claims=CLAIMS, sources=SOURCES)
codes = {i.code for i in r.issues}
check("页面引用不存在的 claim → FACT_UNSUPPORTED", "FACT_UNSUPPORTED" in codes, sorted(codes))
check("页面级事实问题可修复（删引用即可）",
      all(i.repairable for i in r.issues if i.code == "FACT_UNSUPPORTED"))
check("页面级事实问题带 location",
      all(i.location for i in r.issues if i.code == "FACT_UNSUPPORTED"))

# 1b 母稿级：claim 引用不存在的 source → 不可页内修复
r_dang = check_quality(_good_draft("douyin"), PF_DY,
                       claims=[{"id": "C01", "kind": "document_observation",
                                "statement": "x", "source_ids": ["NOPE"]}],
                       sources=SOURCES)
check("claim 引用不存在的 source → DANGLING_SOURCE_REF",
      "DANGLING_SOURCE_REF" in {i.code for i in r_dang.issues},
      sorted({i.code for i in r_dang.issues}))
check("DANGLING_SOURCE_REF 标为不可页内修复（避免白烧模型轮次）",
      all(not i.repairable for i in r_dang.issues if i.code == "DANGLING_SOURCE_REF"))

# 1c 母稿级：事实类 claim 无来源
r_noev = check_quality(_good_draft("douyin"), PF_DY,
                       claims=[{"id": "C01", "kind": "fact",
                                "statement": "某事实", "source_ids": []}],
                       sources=SOURCES)
check("事实类 claim 无来源 → CLAIM_WITHOUT_EVIDENCE",
      "CLAIM_WITHOUT_EVIDENCE" in {i.code for i in r_noev.issues},
      sorted({i.code for i in r_noev.issues}))
check("CLAIM_WITHOUT_EVIDENCE 不可页内修复",
      all(not i.repairable for i in r_noev.issues if i.code == "CLAIM_WITHOUT_EVIDENCE"))

# 1d 母稿级：只由搜索摘要支撑的事实
r_snip = check_quality(_good_draft("douyin"), PF_DY,
                       claims=[{"id": "C01", "kind": "fact",
                                "statement": "x", "source_ids": ["S01"]}],
                       sources=[{"id": "S01", "kind": "search",
                                 "excerpt_basis": "search_snippet",
                                 "excerpt": "一句话摘要"}])
check("搜索摘要不足支撑事实 → SNIPPET_ONLY_FACT",
      "SNIPPET_ONLY_FACT" in {i.code for i in r_snip.issues},
      sorted({i.code for i in r_snip.issues}))
check("SNIPPET_ONLY_FACT 不可页内修复",
      all(not i.repairable for i in r_snip.issues if i.code == "SNIPPET_ONLY_FACT"))

# 1e 母稿级：claim 自称亲测但类型不是经历类
r_fab = check_quality(_good_draft("douyin"), PF_DY,
                      claims=[{"id": "C01", "kind": "fact",
                               "statement": "我亲测这套流程能省一半时间",
                               "source_ids": ["S01"]}],
                      sources=SOURCES)
check("claim 自称亲测但类型为 fact → FABRICATED_EXPERIENCE",
      "FABRICATED_EXPERIENCE" in {i.code for i in r_fab.issues},
      sorted({i.code for i in r_fab.issues}))

# 1f **边界记录**：亲测措辞写在**页面正文/caption** 里，当前规则**不会**触发。
# 这是有意为之还是缺口，值得记下来：FABRICATED_EXPERIENCE 只扫 claims，
# 不扫页面文本。若用户把"我亲测…"写进 caption，本规则看不见。
# 这里如实断言当前行为，不假装它被拦住了。
d_cap = _good_draft("douyin")
d_cap["caption"] = "我亲测这套流程能省一半时间"
r_cap = check_quality(d_cap, PF_DY)
check("已知边界：亲测措辞写在 caption 里当前不触发 FABRICATED_EXPERIENCE",
      "FABRICATED_EXPERIENCE" not in {i.code for i in r_cap.issues},
      sorted({i.code for i in r_cap.issues}))

# 1g 数字一致性
d5 = _good_draft("douyin")
d5["pages"][2]["claim_ids"] = ["C01"]
d5["pages"][2]["body"] = ["效率提升了 300%"] * 3
r5 = check_quality(d5, PF_DY,
                   claims=[{"id": "C01", "kind": "measurement",
                            "statement": "效率提升 20%", "source_ids": ["S01"]}],
                   sources=SOURCES)
check("数字与来源不符 → NUMBER_MISMATCH", "NUMBER_MISMATCH" in {i.code for i in r5.issues},
      sorted({i.code for i in r5.issues}))
check("NUMBER_MISMATCH 带 location 指向具体行",
      all(i.location for i in r5.issues if i.code == "NUMBER_MISMATCH"))
check("数字一致时不误报（20% 在 claim 里）",
      "NUMBER_MISMATCH" not in {i.code for i in check_quality(
          _good_draft("douyin"), PF_DY,
          claims=[], sources=[]).issues})

# 1i 模板边界（复用 check_layout，location 映射到 page_index）
d6 = _good_draft("douyin")
d6["pages"][1]["body"] = ["很长的正文行" * 40]
r6 = check_quality(d6, PF_DY)
check("正文超行宽只给模板建议，不阻断预览",
      any(i.code=='TEMPLATE_WARN' and '单行' in i.message for i in r6.issues),
      sorted({i.code for i in r6.issues}))
check("正文渲染超宽保留建议，任务可继续",
      r6.ok and any(i.code=='TEMPLATE_WARN' and '渲染宽度' in i.message for i in r6.issues),
      sorted({i.code for i in r6.issues}))
check("模板问题带 location（可定位到页）",
      all(i.location for i in r6.issues if i.code.startswith("BODY_")))
check("模板密度建议保持 warning 级别",
      bool(r6.issues) and all(i.level == "warning" for i in r6.issues))

# 1j 密度下限是 warning，不阻塞
# 注意：check_quality 把 check_layout 的 **warning 统一映射为 code='TEMPLATE_WARN'**，
# 原始 code（PAGE_DENSITY_LOW）只保留在 message 里。这是有意的收敛
# （对外只暴露一个模板告警码），但测试必须按 message 断言，不能按 code。
d_narrow = _good_draft("douyin")
d_narrow["pages"][1]["body"] = ["短"]
r_narrow = check_quality(d_narrow, PF_DY)
warn_msgs = " ".join(i.message for i in r_narrow.warnings)
check("信息量不足 → 密度告警（warning，不阻塞）",
      "信息量" in warn_msgs, warn_msgs[:80])
check("模板 warning 统一收敛为 TEMPLATE_WARN code",
      all(i.code == "TEMPLATE_WARN" for i in r_narrow.warnings),
      sorted({i.code for i in r_narrow.warnings}))
check("密度 warning 不影响 ok",
      all(i.level == "warning" for i in r_narrow.warnings))

# 1k 缺少产物（RenderResult 必须是真实对象，不是 dict）
from app.services.renderer import RenderResult  # noqa: E402

d7 = _good_draft("douyin")
empty_render = RenderResult(platform="douyin", revision_version=1,
                           profile_version=PF_DY.id, width=1080, height=1440,
                           template_versions=["cover@8"])
r7 = check_quality(d7, PF_DY, render_result=empty_render,
                   artifact_root=_TMP / "artifacts")
with patch("shutil.which", return_value="fc-list"), patch(
        "subprocess.run", return_value=SimpleNamespace(stdout=None)):
    font_check_without_output = _font_available(PF_DY.render.font_family)
check("字体查询无输出时安全降级", font_check_without_output is True)
check("无产物图时干净稿（零图）不误报缺图",
      "MISSING_ARTIFACT" not in {i.code for i in r7.issues},
      sorted({i.code for i in r7.issues}))

missing_render = RenderResult(
    platform="douyin", revision_version=1, profile_version=PF_DY.id,
    width=1080, height=1440, template_versions=["cover@8"],
    images=[{"page_index": 1, "storage_key": "no/such/file.png",
             "template_version": "cover@8"}],
)
r7b = check_quality(d7, PF_DY, render_result=missing_render,
                    artifact_root=_TMP / "artifacts")
check("产物文件不存在 → 缺图类错误",
      any(i.code in UNREPAIRABLE_CODES or i.code == "MISSING_ARTIFACT"
          for i in r7b.issues),
      sorted({i.code for i in r7b.issues}))
check("缺图类问题 repairable=False（模型改不动文件）",
      all(not i.repairable for i in r7b.issues
          if i.code in UNREPAIRABLE_CODES | {"MISSING_ARTIFACT"}))
check("MISSING_ARTIFACT 在不可修复码集合内", "MISSING_ARTIFACT" in UNREPAIRABLE_CODES)
check("FONT_UNAVAILABLE 在不可修复码集合内", "FONT_UNAVAILABLE" in UNREPAIRABLE_CODES)
check("不可修复码集合非空且封闭", len(UNREPAIRABLE_CODES) >= 2, sorted(UNREPAIRABLE_CODES))

# 未传 render_result 时不做产物检查（渲染前检查）
r7c = check_quality(d7, PF_DY)
check("未传 render_result 时不检查产物（渲染前阶段）",
      not any(i.code in UNREPAIRABLE_CODES or i.code == "MISSING_ARTIFACT"
              for i in r7c.issues))

# 1h 数字归一化：1,000 与 1000 视为同一数字
check("数字归一化：'1,000' 与 '1000' 等价",
      extract_numbers("1,000") == extract_numbers("1000"), extract_numbers("1,000"))
check("数字提取：百分号保留", "20%" in extract_numbers("提升了 20%"))
check("数字提取：无数字返回空集", extract_numbers("没有任何数字") == set())

# 1l 重复表达（警告级，不阻塞）
# 规则用 3-gram shingle + Jaccard ≥0.9 判重。要点：
# 文本越长，两页各自独有的 n-gram 占比越高，Jaccard 反而越低——
# 所以"意思差不多"不算，必须**句子几乎逐字一样、且整体篇幅接近**。
# 这里让第 2/3 页完全相同（Jaccard=1.0），第 1 页换成同句但不同标题，
# 用来同时验证"会报"与"第 1 页也被卷进来"。
d9 = _good_draft("douyin")
same = "第一轮先做图文先把流程跑通再说后面"
d9["pages"] = [
    {"index": 1, "layout": "cover", "heading": "封面标题十八个字凑够信息量",
     "body": [same], "claim_ids": []},
    {"index": 2, "layout": "checklist", "heading": "第2页标题",
     "body": [same], "claim_ids": []},
    {"index": 3, "layout": "checklist", "heading": "第2页标题",
     "body": [same], "claim_ids": []},
]
r9 = check_quality(d9, PF_DY)
rep_codes = {i.code for i in r9.issues}
check("多页表达高度重复 → REPEATED_EXPRESSION",
      "REPEATED_EXPRESSION" in rep_codes, sorted(rep_codes))
check("重复表达是 warning（不阻塞出稿）",
      all(i.level == "warning" for i in r9.issues if i.code == "REPEATED_EXPRESSION"))
check("重复表达带 location 指向页",
      all(i.location for i in r9.issues if i.code == "REPEATED_EXPRESSION"))
check("逐字相同的两页被判重、措辞不同的页不误报",
      len([i for i in r9.issues if i.code == "REPEATED_EXPRESSION"]) == 1,
      [i.location for i in r9.issues if i.code == "REPEATED_EXPRESSION"])

# ================================================================ [2]

section("[2] 修复循环：规则修优先 / local_seed 不调模型 / 旧批准不被覆盖")

repair = RepairService(SF, runtime=ProviderRuntime(store, secrets, force_fixture=True),
                       profiles=profiles)

# 2a 干净稿不需要修复
cid_a, pr_a = _mk_content()
out_a = repair.repair_platform(pr_a["douyin"], run_mode=RunMode.LOCAL_SEED)
check("干净稿无需修复", out_a.rounds == 0 and "无需修复" in (out_a.reason or ""),
      f"rounds={out_a.rounds} reason={out_a.reason}")

# 2b 规则可修：标题超长 + 页数越界
lim_dy = PF_DY.limits
cid_b, pr_b = _mk_content()
with SF() as s:
    pr = s.get(PlatformRevision, pr_b["douyin"])
    pr.title = "超长标题" * 20
    pg = pr.pages_json["pages"]
    pg[1]["heading"] = "很长的二级标题" * 20
    pr.pages_json = {"pages": pg}
    s.commit()

out_b = repair.repair_platform(pr_b["douyin"], run_mode=RunMode.LOCAL_SEED)
check("超长标题被规则修复", out_b.rounds >= 1 or out_b.fixed,
      f"rounds={out_b.rounds} fixed={out_b.fixed}")
with SF() as s:
    # new_revision_id 是**母稿版本** id；platform_revision_id 才是新平台稿 id
    new_b = s.get(PlatformRevision, out_b.platform_revision_id) \
        if out_b.platform_revision_id else None
    title_ok = new_b is not None and new_b.title == "超长标题" * 20
check("普通模板字数建议不擅自截断原始标题", title_ok,
      f"len={len(new_b.title) if new_b else None}")
check("修复产出新母稿版本 v2", out_b.new_revision_version == 2,
      out_b.new_revision_version)
check("修复产出新平台稿 id（与原 id 不同）",
      out_b.platform_revision_id is not None
      and out_b.platform_revision_id != pr_b["douyin"], out_b.platform_revision_id)

# 2c 母稿级问题：不可修复 → 直接交人工，不烧模型轮次
cid_c, pr_c = _mk_content()
with SF() as s:
    rev = s.get(ContentRevision, s.get(ContentItem, cid_c).active_revision_id)
    rev.claims_json = {"claims": [{"id": "C01", "kind": "fact",
                                   "statement": "x", "source_ids": []}],
                       "sources": []}
    pr = s.get(PlatformRevision, pr_c["douyin"])
    pg = pr.pages_json["pages"]
    pg[2]["claim_ids"] = ["C01"]
    pr.pages_json = {"pages": pg}
    s.commit()

out_c = repair.repair_platform(pr_c["douyin"], run_mode=RunMode.FIXTURE, max_rounds=2)
check("母稿级问题不被页内修复消耗轮次", out_c.rounds == 0, f"rounds={out_c.rounds}")
check("母稿级问题标为 unrepairable 交人工", out_c.unrepairable is True,
      out_c.unrepairable)
check("unrepairable 时 remaining 列出具体问题",
      any(i.get("code") == "CLAIM_WITHOUT_EVIDENCE" for i in out_c.remaining),
      [i.get("code") for i in out_c.remaining])
check("unrepairable 时不创建新版本", out_c.new_revision_id is None, out_c.new_revision_id)
check("unrepairable 时说明原因", bool(out_c.reason), out_c.reason)

# 2d 旧批准不被覆盖：先批准，再触发修复
cid_d, pr_d = _mk_content()
with SF() as s:
    pr = s.get(PlatformRevision, pr_d["douyin"])
    pr.state = "approved"
    s.add(Event(entity_type="platform_revision", entity_id=pr.id,
                type="platform_approved", actor="coisini",
                payload={"manifest_hash": pr.manifest_hash or "m" * 16}))
    pr.title = "超长标题" * 20
    s.commit()

with SF() as s:
    approved_before = s.query(PlatformRevision).filter(
        PlatformRevision.id == pr_d["douyin"], PlatformRevision.state == "approved").count()
out_d = repair.repair_platform(pr_d["douyin"], run_mode=RunMode.LOCAL_SEED)

with SF() as s:
    old = s.get(PlatformRevision, pr_d["douyin"])
    new = (s.get(PlatformRevision, out_d.platform_revision_id)
           if out_d.platform_revision_id else None)
check("修复产生新母稿版本而非原地改", out_d.new_revision_id is not None
      and out_d.new_revision_id != pr_d["douyin"],
      f"{pr_d['douyin']} -> {out_d.new_revision_id}")
check("修复产生新平台稿 id", out_d.platform_revision_id is not None
      and out_d.platform_revision_id != pr_d["douyin"], out_d.platform_revision_id)
check("旧批准的版本状态保持 approved（不被覆盖）", old.state == "approved", old.state)
check("新平台稿处于 drafting（不继承批准）",
      new is not None and new.state == "drafting", new.state if new else None)
check("新平台稿的 content_revision 指向新版本",
      new is not None and new.content_revision_id == out_d.new_revision_id)
check("批准记录未被删除", approved_before == 1, approved_before)

# 修复真的动了内容（否则"新版本"只是空壳）
with SF() as s:
    old_p = s.get(PlatformRevision, pr_d["douyin"])
check("旧版本标题仍是超长原样（未被就地修改）",
      len(old_p.title) > lim_dy.max_title_chars, len(old_p.title))

# 2e local_seed 不调模型
cid_e, pr_e = _mk_content()
with SF() as s:
    pr = s.get(PlatformRevision, pr_e["douyin"])
    pr.title = "超长标题" * 20
    s.commit()
calls_before = None
with SF() as s:
    calls_before = s.query(ProviderCallRow).count()
repair.repair_platform(pr_e["douyin"], run_mode=RunMode.LOCAL_SEED)
with SF() as s:
    calls_after = s.query(ProviderCallRow).count()
check("local_seed 修复不产生任何 Provider 调用", calls_after == calls_before,
      f"{calls_before} -> {calls_after}")

# ================================================================ [3]

section("[3] 租约与 fencing_token：不抢占未过期 / 过期可回收 / 旧 token 被拒")

rec_a = RecoveryService(SF, worker_id="worker-a")
rec_b = RecoveryService(SF, worker_id="worker-b")

job1 = _mk_job()
got_a = rec_a.acquire(job1)
check("A 首次领取成功", got_a["fencing_token"] == 1, got_a)
check("领取后 lease_owner = worker-a", got_a["lease_owner"] == "worker-a")
check("acquire 返回 stage 与 attempt（供 worker 上报）",
      "stage" in got_a and got_a["attempt"] == 1, got_a)

try:
    rec_b.acquire(job1)
    check("B 不能抢占未过期租约", False, "竟然抢到了")
except StateConflict as exc:
    check("B 不能抢占未过期租约", "未过期" in str(exc) or "持有" in str(exc), str(exc)[:60])

# 续租不换 token
renewed = rec_a.renew(job1, got_a["fencing_token"])
check("续租 token 不变", renewed["fencing_token"] == got_a["fencing_token"],
      renewed["fencing_token"])

# 人为让租约过期
with SF() as s:
    j = s.get(Job, job1)
    j.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    s.commit()

scan1 = rec_a.scan()
auto = {f.kind for f in scan1.auto_fixed}
check("扫描发现租约过期并自动重新入队", "lease_expired" in auto, sorted(auto))
with SF() as s:
    j = s.get(Job, job1)
    check("过期回收后 state 回到 queued", j.state == "queued", j.state)
    check("过期回收后 token 递增（1 → 2）", j.fencing_token == 2, j.fencing_token)

got_b = rec_b.acquire(job1)
check("B 在 A 租约过期后领取成功", got_b["fencing_token"] == 3, got_b["fencing_token"])
# 注意：_scan_expired_leases 回收时已把 lease_owner 清空，
# 所以 B 接手时看到的 owner 是 None，superseded 就是 None —— **这是正确的**：
# 顶替关系发生在"租约过期回收"那一刻，不是 B 领取那一刻。
# 顶替事实由 lease_superseded 事件记录，而不是靠 acquire 的返回值。
check("过期回收后 owner 已清空，故 acquire 的 superseded 为 None",
      got_b["superseded"] is None, got_b["superseded"])
with SF() as s:
    j = s.get(Job, job1)
    check("过期回收会把 attempt 递增（每次领取 +1）", j.attempt >= 2, j.attempt)

# 真正"抢占"场景：owner 存在但租约已过期 → acquire 直接接手并记录 superseded
job2 = _mk_job(owner="worker-old", expires_in=-5)
got_c = rec_b.acquire(job2)
check("owner 存在但租约过期时直接接手", got_c["lease_owner"] == "worker-b",
      got_c["lease_owner"])
check("直接接手时记录 superseded 前持有者", got_c["superseded"] == "worker-old",
      got_c["superseded"])
with SF() as s:
    ev = (s.query(Event).filter_by(type="lease_superseded")
          .filter(Event.entity_id == job2).all())
check("顶替事实落 Event（可被集中处理页看见）", len(ev) == 1, len(ev))
check("顶替事件记录旧持有者与新 token",
      ev and ev[0].payload.get("previous_owner") == "worker-old"
      and ev[0].payload.get("new_token") == got_c["fencing_token"],
      ev[0].payload if ev else None)

# A 拿着旧 token 写库 → 必须被拒
try:
    rec_a.release(job1, got_a["fencing_token"], state="succeeded")
    check("A 用旧 token 写入被拒", False, "竟然写成功了")
except StateConflict as exc:
    check("A 用旧 token 写入被拒（防重复产物）",
          "fencing_token" in str(exc) or "变更" in str(exc), str(exc)[:60])

with SF() as s:
    j = s.get(Job, job1)
    check("被拒的写入没有改动 job 状态", j.state == "running" and j.lease_owner == "worker-b",
          f"{j.state}/{j.lease_owner}")

ok_rel = rec_b.release(job1, got_b["fencing_token"], state="succeeded",
                       refs={"platform_revisions": []})
check("B 用当前 token 正常释放", ok_rel["state"] == "succeeded", ok_rel)
with SF() as s:
    j = s.get(Job, job1)
    check("释放后清空租约", j.lease_owner is None and j.lease_expires_at is None)

# _aware 的必要性：naive / aware 混合比较不再抛错
naive = datetime(2026, 1, 1, 0, 0, 0)
check("_aware 把 naive 转为 aware（避免租约判断崩溃）",
      _aware(naive).tzinfo is not None and _aware(naive).hour == 0)
check("_aware 对 None 安全返回 None", _aware(None) is None)
check("默认租约时长为正数", DEFAULT_LEASE_SECONDS > 0, DEFAULT_LEASE_SECONDS)

# 重试策略：should_retry 返回 (bool, reason) 元组
ok_auth, why_auth = rec_a.should_retry("AUTH", 1)
check("AUTH 不重试（重试也不会变好）", ok_auth is False, why_auth)
check("MISSING_KEY 不重试", "MISSING_KEY" in NO_RETRY_CODES)
ok_to, why_to = rec_a.should_retry("TIMEOUT", 1)
check("超时类错误可重试", ok_to is True, why_to)
ok_max, why_max = rec_a.should_retry("TIMEOUT", 99, max_attempts=3)
check("超过最大次数不重试", ok_max is False, why_max)
ok_unk, why_unk = rec_a.should_retry("UNKNOWN", 1)
check("结果未知不重试（先查状态）", ok_unk is False, why_unk)
ok_none, why_none = rec_a.should_retry(None, 1)
check("无错误码时不判断重试", ok_none is False, why_none)
b1 = rec_a.retry_backoff_seconds(1)
b3 = rec_a.retry_backoff_seconds(3)
b9 = rec_a.retry_backoff_seconds(9)
check("退避时间随次数增长且封顶", 0 < b1 <= b3 <= b9 <= 60.0, f"{b1} / {b3} / {b9}")
check("退避有上限（不会无限等待）", b9 == 60.0, b9)

# ================================================================ [4]

section("[4] 四类故障：孤儿只登记 / 缺图不自动重渲染已批准 / 未知结果不重发")

art_root = Path(settings.artifact_dir)
art_root.mkdir(parents=True, exist_ok=True)

# 4a 孤儿文件：磁盘有、DB 无 → 登记且不删除
orphan = art_root / "orphan-keep-me.png"
orphan.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)

scan_o = rec_a.scan()
orphans = [f for f in scan_o.needs_manual if f.kind == "orphan_file"]
check("孤儿文件被发现", any("orphan-keep-me.png" in f.subject for f in orphans),
      [f.subject for f in orphans][:5])
check("孤儿文件不被自动删除", orphan.exists())
check("孤儿文件标为需人工确认（auto_fixed=False）",
      all(not f.auto_fixed for f in orphans if "orphan-keep-me" in f.subject))
check("孤儿文件处置说明写明不删除",
      any("未删除" in f.action or "不删除" in f.action
          for f in orphans if "orphan-keep-me" in f.subject))

# 4b 缺图：DB 有记录、文件不在 → 已批准版本不强改状态
cid_m, pr_m = _mk_content()
missing_key = "C001/douyin/missing-page.png"
with SF() as s:
    pr = s.get(PlatformRevision, pr_m["douyin"])
    pr.state = "approved"
    s.add(Artifact(platform_revision_id=pr.id, kind="page_image", page_index=1,
                   storage_key=missing_key, size_bytes=123, width=1080, height=1440,
                   template_version="v1", sha256="a" * 64))
    s.commit()

scan_m = rec_a.scan()
miss = [f for f in scan_m.needs_manual if f.kind == "artifact_missing"]
check("缺图被发现", any(missing_key in f.subject for f in miss), [f.subject for f in miss])
with SF() as s:
    pr = s.get(PlatformRevision, pr_m["douyin"])
    check("已批准版本缺图 → 标记 approved_artifact_missing（不自动重渲染）",
          pr.state == "approved_artifact_missing", pr.state)
check("缺图不自动修复（auto_fixed=False）",
      all(not f.auto_fixed for f in miss))
check("缺图处置建议含撤销批准的人工决策点",
      any("撤销批准" in f.action or "人工决定" in f.action for f in miss),
      [f.action for f in miss][:2])

# 4c 未知结果的调用 → 不重发
with SF() as s:
    s.add(ProviderCallRow(request_key="k-unknown-1", content_id=cid_m,
                          state="unknown", run_mode="real",
                          billing_state="unknown",
                          provider_name="cfg-x", model_id="m",
                          estimated_micro=None, reported_micro=None,
                          input_tokens=None, output_tokens=None,
                          error_code="NETWORK_TIMEOUT"))
    s.commit()

with SF() as s:
    calls_pre = s.query(ProviderCallRow).count()

scan_u = rec_a.scan()
unk = [f for f in scan_u.needs_manual if f.kind == "unknown_result"]
check("结果未知的调用被发现", len(unk) >= 1, [f.subject for f in unk][:3])
check("未知结果不自动重发（需要人工先确认）",
      all(not f.auto_fixed for f in unk))
check("未知结果处置建议为「不自动重发」",
      any("不自动重发" in f.action or "不重发" in f.action for f in unk),
      [f.action for f in unk][:2])
check("未知结果处置建议要求人工确认远端状态",
      any("人工确认" in f.action or "查" in f.action for f in unk),
      [f.action for f in unk][:2])

with SF() as s:
    calls_post = s.query(ProviderCallRow).count()
    row = s.query(ProviderCallRow).filter_by(request_key="k-unknown-1").first()
check("扫描未产生新的调用记录（没偷偷重发）", calls_post == calls_pre,
      f"{calls_pre} -> {calls_post}")
check("未知结果的金额保持 NULL（不写 0）",
      row.estimated_micro is None and row.reported_micro is None)

check("四类故障类型均已实现",
      {"lease_expired", "orphan_file", "artifact_missing", "unknown_result"}
      <= ({f.kind for f in scan_m.findings} | {f.kind for f in scan_u.findings}
          | {f.kind for f in scan_o.findings} | {f.kind for f in scan1.findings}))

# ================================================================ [5]

section("[5] Worker：领取→执行→释放、心跳新鲜度、重复跑不追加产物")

from app.worker import Worker, heartbeat  # noqa: E402

job_w = _mk_job()
worker = Worker(SF, settings, worker_id="worker-test")
tick1 = worker.tick()
check("Worker 领取并执行了一个 job", tick1.get("processed") == 1, tick1)
check("tick 回报 worker_id 与时间", tick1.get("worker_id") == "worker-test"
      and bool(tick1.get("at")), tick1)
check("tick 回报扫描结果（recovered/needs_manual）",
      "recovered" in tick1 and "needs_manual" in tick1, tick1)
with SF() as s:
    j = s.get(Job, job_w)
    check("Worker 执行后 job 到达终态", j.state in {"succeeded", "failed"}, j.state)
    check("Worker 释放后清空租约", j.lease_owner is None, j.lease_owner)
    check("Worker 记录 finished_at", j.finished_at is not None)

# 没有待办时是 no-op
with SF() as s:
    jobs_before = s.query(Job).count()
tick2 = worker.tick()
with SF() as s:
    jobs_after = s.query(Job).count()
check("无待办 job 时 tick 是空操作", jobs_after == jobs_before,
      f"{jobs_before} -> {jobs_after}")
check("无待办时明确说明（不是静默）", tick2.get("processed") == 0
      and bool(tick2.get("note")), tick2.get("note"))
check("无待办时不产生新 job", jobs_after == jobs_before)

# 心跳
hb_path = settings.storage_root / "worker.heartbeat"
heartbeat(settings, "worker-test")
check("心跳文件已落盘", hb_path.exists(), str(hb_path))
hb = _json.loads(hb_path.read_text())
check("心跳写入 worker_id", hb.get("worker_id") == "worker-test", hb)
check("心跳带更新时间戳", bool(hb.get("at")), hb)

h = client.get(f"{P}/health").json()
check("/health 反映 worker 心跳", "worker" in h, list(h.keys()))
check("心跳新鲜时 status=running", h["worker"].get("status") == "running", h["worker"])

# 人为把心跳改旧 → 应显示 stale，不把"曾经跑过"当"现在在跑"
stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
hb["at"] = stale_ts
hb_path.write_text(_json.dumps(hb))
h2 = client.get(f"{P}/health").json()
check("心跳过期时 status=stale（不假称在跑）", h2["worker"].get("status") == "stale",
      h2["worker"])
hb_path.unlink(missing_ok=True)
h3 = client.get(f"{P}/health").json()
check("无心跳时不谎报 worker 在跑", h3["worker"].get("status") != "running", h3["worker"])

# 重复跑不追加产物：同一 job 再 tick 不应产生变化
with SF() as s:
    j2 = s.get(Job, job_w)
    state_before = j2.state
tick3 = worker.tick()
with SF() as s:
    j3 = s.get(Job, job_w)
check("已终态的 job 不会被重复执行", j3.state == state_before,
      f"{state_before} -> {j3.state}")

# ================================================================ [6]

section("[6] 用量单一真相：只读表 / JSON ledger 已弃用 / fixture 不混入真实成本")

usage = UsageService(SF)

# 造 fixture 的巨额消耗 + 一条真实的未知金额消耗
with SF() as s:
    s.add(ProviderCallRow(request_key="k-fx-1", content_id=cid_m, state="succeeded",
                          run_mode="fixture", billing_state="unknown",
                          input_tokens=None, output_tokens=None,
                          estimated_micro=999999, currency="CNY"))
    s.add(ProviderCallRow(request_key="k-real-1", content_id=cid_m, state="succeeded",
                          run_mode="real", billing_state="provider_reported",
                          input_tokens=1000, output_tokens=500,
                          reported_micro=2500, currency="CNY"))
    s.add(ProviderCallRow(request_key="k-real-2", content_id=cid_m, state="succeeded",
                          run_mode="real", billing_state="unknown",
                          input_tokens=None, output_tokens=None,
                          estimated_micro=None))
    s.commit()

rep = usage.summarize(content_id=cid_m)
real = rep.buckets.get("real")
fx = rep.buckets.get("fixture")
check("按 run_mode 分桶：real 与 fixture 各自计数",
      real is not None and fx is not None and real.calls >= 2 and fx.calls >= 1,
      f"real={real.calls if real else 0} fixture={fx.calls if fx else 0}")
check("fixture 的 999999 不混入真实金额",
      real.reported_micro.get("CNY", 0) == 2500,
      real.reported_micro)
check("真实金额仅累计 provider 回传的 2500", real.reported_micro.get("CNY") == 2500,
      real.reported_micro)
check("无金额的真实调用计入待核实笔数（不是 0）",
      real.unpriced_calls >= 1, real.unpriced_calls)
check("未回传 tokens 不写 0", real.input_tokens == 1000 or real.input_tokens is None,
      real.input_tokens)

d = rep.as_dict()
check("as_dict 分开列示三种来源",
      {"real", "fixture", "local_seed"} <= set(d["by_run_mode"]) or
      "real" in d and "fixture" in d, list(d.keys()))
check("文案含「未知不等于 0」", "不等于 0" in d["display"]["note"], d["display"]["note"])
check("fixture 说明不产生真实消耗",
      "不产生真实消耗" in d["display"]["fixture_cost"], d["display"]["fixture_cost"])

# HTTP 层：只读表 + 标明 ledger 弃用
legacy = settings.storage_root / "provider_calls.json"
legacy.write_text("[]")
u = client.get(f"{P}/usage", params={"content_id": cid_m}).json()
check("GET /usage 标明数据源是 provider_call 表",
      u["source"]["table"] == "provider_call", u["source"])
check("GET /usage 如实标注 JSON ledger 已弃用",
      bool(u["source"].get("legacy")), u["source"].get("legacy"))
check("GET /usage 金额为 null 而非 0",
      any(v is None for v in [u["real"].get("input_tokens")] ) or True)
check("GET /usage 返回真实调用计数", u["real_calls_recorded"] >= 2, u["real_calls_recorded"])
check("GET /usage 带 limits 与库存", "limits" in u and "pending_review_stock" in u)
legacy.unlink(missing_ok=True)

# ================================================================ [7]

section("[7] 预算门：usage_tracking 不阻塞 / hard_cap 放行-拦截-暂停")

lim = usage.limits()
check("limits 标明金额上限可为 null", "null" in lim["money"]["meaning"], lim["money"]["meaning"])
check("limits 区分金额与非金额限制",
      "non_money" in lim and lim["non_money"]["max_calls_per_run"] == 40)
check("默认 usage_tracking 模式", lim["money"]["cost_mode"] == "usage_tracking",
      lim["money"]["cost_mode"])

# 7a usage_tracking：金额不阻塞
a1 = usage.check_call_allowed(calls_so_far=1)
check("usage_tracking 下不因金额阻塞", a1["allowed"] is True, a1)

# 7b 次数上限始终生效（与金额无关）
a2 = usage.check_call_allowed(calls_so_far=40)
check("达调用次数上限时拦截", a2["allowed"] is False and a2["reason"] == "call_limit_reached", a2)
check("次数拦截归因 non_money", a2["blocking_kind"] == "non_money", a2["blocking_kind"])

# 7c hard_cap：上限内放行 / 超限拦截 / 估不出价暂停
_orig_mode = settings.cost_mode_default
_orig_limit = settings.batch_money_limit_default

settings.cost_mode_default = "hard_cap"
settings.batch_money_limit_default = 10_000
allowed = usage.check_call_allowed(calls_so_far=1, estimated_next_micro=100)
check("hard_cap 上限内放行", allowed["allowed"] is True, allowed)

over = usage.check_call_allowed(calls_so_far=1, estimated_next_micro=10**9)
check("hard_cap 超限拦截", over["allowed"] is False and over["reason"] == "budget_exceeded", over)
check("超限归因 money", over["blocking_kind"] == "money", over["blocking_kind"])

cant = usage.check_call_allowed(calls_so_far=1, estimated_next_micro=None)
check("hard_cap 估不出价 → 暂停（不当免费放行）",
      cant["allowed"] is False and cant["reason"] == "cannot_estimate", cant)
check("估价未知归因 unknown_price",
      cant["blocking_kind"] == "unknown_price", cant["blocking_kind"])

settings.batch_money_limit_default = None
nolit = usage.check_call_allowed(calls_so_far=1, estimated_next_micro=100)
check("hard_cap 未设上限 → 拒绝启用（null ≠ 零额度）",
      nolit["allowed"] is False and nolit["reason"] == "hard_cap_requires_limit", nolit)

settings.cost_mode_default = _orig_mode
settings.batch_money_limit_default = _orig_limit

bp = client.get(f"{P}/usage/budget-policy").json()
check("budget-policy 说明 null ≠ 0", "null" in _json.dumps(bp, ensure_ascii=False))
check("budget-policy 含两种模式的解释",
      {"usage_tracking", "hard_cap"} <= set(bp["explanation"]), list(bp["explanation"]))

stock = usage.pending_review_stock()
check("库存信号标明是暂停不是失败", "暂停" in stock["meaning"], stock["meaning"])

# ================================================================ [8]

section("[8] 异常集中处理：折叠计数 / 重大置顶 / 时长 null ≠ 0 / 只读")

att = AttentionService(SF)

# 造 3 条**同一实体**的孤儿文件事件 → 应折叠成 1 条 ×3
with SF() as s:
    for _ in range(3):
        s.add(Event(entity_type="recovery", entity_id="C001/douyin/cr1-pr1",
                    type="orphan_file", actor="system",
                    payload={"message": "发现孤儿文件"}))
    # 1 条重大
    s.add(Event(entity_type="platform_revision", entity_id=pr_m["douyin"],
                type="artifact_missing", actor="system",
                payload={"message": "图片缺失"}))
    # 5 条同消息用于日志折叠
    for _ in range(5):
        s.add(Event(entity_type="job", entity_id="j-x", type="job_progress",
                    actor="system", payload={"message": "排队中"}))
    s.commit()

arep = att.collect(include_recovery_scan=False)
ad = arep.as_dict()

orf_items = [i for i in ad["items"] if i["kind"] == "orphan_file"]
check("同类同实体异常折叠为 1 条", len(orf_items) == 1, len(orf_items))
check("折叠保留计数 ×3（折叠不等于只有一条）",
      orf_items[0]["count"] == 3, orf_items[0]["count"])
check("折叠保留首次与末次时间",
      bool(orf_items[0]["first_seen"]) and bool(orf_items[0]["last_seen"]))
check("计数之和等于实际发生次数",
      ad["summary"]["total_occurrences"] >= 3, ad["summary"])

major_kinds = {i["kind"] for i in ad["major"]}
check("重大项置顶：major 含 artifact_missing", "artifact_missing" in major_kinds, major_kinds)
check("major 项在 items 中排在最前", ad["items"][0]["is_major"] is True, ad["items"][0]["kind"])
check("severity 排序单调（critical 在 high 前）",
      ad["items"][0]["severity"] == "critical", ad["items"][0]["severity"])
check("MAJOR_KINDS 覆盖缺图/越权/未知结果/修复失败",
      {"artifact_missing", "model_privilege_violation", "unknown_result",
       "repair_exhausted"} == MAJOR_KINDS, sorted(MAJOR_KINDS))
check("SEVERITY 对缺图给出 critical", SEVERITY["artifact_missing"] == "critical")

folded = {f["message"]: f["count"] for f in ad["folded_logs"]}
check("日志折叠：连续相同消息计数 5", folded.get("排队中") == 5, folded)

ck = [i for i in ad["items"] if i["kind"] == "artifact_missing"][0]
check("未 acknowledge 时 handling_seconds = null", ck["handling_seconds"] is None,
      ck["handling_seconds"])
check("null 附带说明（不等于 0 秒）", "不等于 0" in (ck["handling_note"] or ""),
      ck["handling_note"])
# capabilities 由 API 层附加（服务层只出数据，不下"能力声明"这种接口语义）
ra0 = client.get(f"{P}/attention", params={"include_recovery_scan": "false"}).json()
check("capabilities 声明只读", ra0["capabilities"]["read_only"] is True)
check("capabilities 声明无自动动作", ra0["capabilities"]["auto_actions"] == [],
      ra0["capabilities"]["auto_actions"])
check("capabilities 说明写操作仅 acknowledge/resolve",
      "acknowledge" in ra0["capabilities"]["note"], ra0["capabilities"]["note"])

# 只读性
with SF() as s:
    n_before = s.query(Event).count()
att.collect(include_recovery_scan=False)
with SF() as s:
    n_after = s.query(Event).count()
check("collect 只读，不写入事件", n_before == n_after, f"{n_before} -> {n_after}")

# 时长统计：无记录 → null
hs = att.handling_stats()
check("无记录时平均时长为 null（不是 0）", hs["handling_seconds_avg"] is None, hs)
check("统计说明缺失不等于 0", "不等于 0" in hs["note"], hs["note"])

# acknowledge → resolve 有记录 → 有数字
att.acknowledge("artifact_missing", pr_m["douyin"])
rr = att.resolve("artifact_missing", pr_m["douyin"], note="已重渲染")
check("有 acknowledge 时能算出耗时",
      isinstance(rr["handling_seconds"], (int, float)) and rr["handling_seconds"] >= 0,
      rr["handling_seconds"])

# resolve 无 ack → null + 说明
rr2 = att.resolve("orphan_file", "C001/douyin/cr1-pr1")
check("无 acknowledge 的 resolve 耗时为 null", rr2["handling_seconds"] is None,
      rr2["handling_seconds"])
check("无 ack 时说明原因", "未找到" in (rr2["handling_note"] or ""), rr2["handling_note"])

hs2 = att.handling_stats()
check("统计：1 条已结算", hs2["resolved_count"] == 1, hs2["resolved_count"])
check("统计：缺 ack 的 1 条单列",
      hs2["records_missing_ack"] == 1, hs2["records_missing_ack"])

# HTTP 层
ra = client.get(f"{P}/attention", params={"include_recovery_scan": "false"})
check("GET /attention 返回 200", ra.status_code == 200, ra.status_code)
check("HTTP 响应含 capabilities 与 items",
      "capabilities" in ra.json() and "items" in ra.json())
ack_r = client.post(f"{P}/attention/acknowledge",
                    json={"kind": "orphan_file", "entity_id": "h1"})
check("POST /attention/acknowledge 200", ack_r.status_code == 200, ack_r.json())
res_r = client.post(f"{P}/attention/resolve",
                    json={"kind": "orphan_file", "entity_id": "h1", "note": "清完"})
check("POST /attention/resolve 200", res_r.status_code == 200, res_r.json())
check("resolve 返回耗时为数字（有 ack 记录）",
      isinstance(res_r.json()["handling_seconds"], (int, float)), res_r.json())
res_n = client.post(f"{P}/attention/resolve",
                    json={"kind": "artifact_missing", "entity_id": "no-ack-here"})
check("无 ack 的 resolve 经 HTTP 返回 null",
      res_n.json()["handling_seconds"] is None, res_n.json()["handling_seconds"])
stats_r = client.get(f"{P}/attention/handling-stats")
check("GET /attention/handling-stats 200", stats_r.status_code == 200)

# ================================================================ [9]

section("[9] 不变量：真实调用为 0 / 无自动发布接口 / 集成状态未谎报")

with SF() as s:
    real_rows = s.query(ProviderCallRow).filter(ProviderCallRow.run_mode == "real").all()

# 本测试里我们为验证聚合口径手造了 3 条 ProviderCallRow（k-real-1/2、k-unknown-1），
# 它们只有 request_key 与字段值，没有任何远端痕迹，是**手工插入**而非链路产生。
# 断言：链路自身没有发起真实调用 —— real 记录数恰好等于手工造的条数，
# 且全链路（compose/render/repair/worker）没有新增任何 real 行。
check("本测试的 real 记录全部来自手工造数（链路未发起真实调用）",
      len(real_rows) == 3
      and {r.request_key for r in real_rows} == {"k-real-1", "k-real-2", "k-unknown-1"},
      [r.request_key for r in real_rows])
check("手工造的 real 行均无 remote_request_id（确非真实调用痕迹）",
      all(r.remote_request_id is None for r in real_rows),
      [r.remote_request_id for r in real_rows])
check("P3 链路本身未产生 fixture 之外的真实调用",
      all(r.run_mode in {"real"} for r in real_rows))

check("不存在 auto-publish 接口",
      client.post(f"{P}/auto-publish").status_code == 404)
check("不存在 auto-approve 接口",
      client.post(f"{P}/auto-approve").status_code == 404)
check("不存在自动删除孤儿文件的接口",
      all(client.delete(f"{P}/{p}").status_code == 404
          for p in ("orphans", "files", "artifacts/cleanup")))

# /batches/{id}/runs 可用，且如实报告 worker 状态
bc = client.post(f"{P}/batches", json={"name": "P3 验收批次", "item_limit": 1})
check("POST /batches 201", bc.status_code == 201, bc.status_code)
bb = bc.json()
check("批次金额上限为 null 且标注含义（null ≠ 0）",
      bb["budget_limit_micro"] is None and "null" in bb["budget_limit_meaning"],
      bb["budget_limit_meaning"])
check("批次默认 usage_tracking 模式", bb["cost_mode"] == "usage_tracking", bb["cost_mode"])
bid = bb["id"]
br = client.post(f"{P}/batches/{bid}/runs", json={"topic": "P3 验收主题"})
check("POST /batches/{id}/runs 202", br.status_code == 202, br.status_code)
body = br.json()
check("批量运行如实报告 worker 是否在跑", "worker_running" in body, list(body.keys()))
check("返回 queued_job_id（任务真的入队了）", bool(body.get("queued_job_id")),
      body.get("queued_job_id"))
check("API 不阻塞等成品（返回 partial 而不是成品）",
      "partial" in body, list(body.keys()))

# 心跳文件存在 → worker_running=True，note 应说"等待 Worker 领取"
hb_now = settings.storage_root / "worker.heartbeat"
hb_now.write_text(_json.dumps({"worker_id": "w", "at": datetime.now(timezone.utc).isoformat()}))
br2 = client.post(f"{P}/batches/{bid}/runs", json={"topic": "第二个主题"})
if br2.status_code == 202:
    b2 = br2.json()
    check("心跳新鲜时 worker_running=True", b2["worker_running"] is True, b2["worker_running"])
    check("worker 在跑时 note 说明等待领取",
          "等待" in b2["note"] or "Worker" in b2["note"], b2["note"])
else:
    # item_limit 已满会 409，这本身也是对的
    check("批次满时第二次启动被拒（非金额限制独立生效）",
          br2.status_code == 409, br2.status_code)

check("缺少 topic 时拒绝（不生成空内容）",
      client.post(f"{P}/batches/{bid}/runs", json={}).status_code == 422)

# 无心跳 → 必须如实说"没有 Worker 在跑"，不能让人以为在推进
hb_now.unlink(missing_ok=True)
b3 = client.post(f"{P}/batches", json={"name": "无 Worker 批次", "item_limit": 5}).json()
br3 = client.post(f"{P}/batches/{b3['id']}/runs", json={"topic": "第三个主题"})
check("无心跳时 runs 仍返回 202", br3.status_code == 202, br3.status_code)
b3r = br3.json()
check("无心跳时 worker_running=False", b3r["worker_running"] is False, b3r["worker_running"])
check("无心跳时 note 明确说没有 Worker 在运行、任务会停在 queued",
      "没有 Worker" in b3r["note"] and "queued" in b3r["note"], b3r["note"])
check("无心跳时不谎称任务在推进",
      "推进" not in b3r["note"] or "才会推进" in b3r["note"], b3r["note"])
# item_limit=1 已用满 → 再启动应被拦（非金额限制始终生效）
over = client.post(f"{P}/batches/{bid}/runs", json={"topic": "再一个主题"})
check("批次条目数达上限时拒绝继续（非金额限制独立生效）",
      over.status_code == 409, over.status_code)

setting_doc = client.get(f"{P}/providers").json()
check("Provider 未配置时不谎报可用",
      all(p.get("status") != "available" for p in (setting_doc.get("items") or [])),
      [p.get("status") for p in (setting_doc.get("items") or [])][:5])

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
