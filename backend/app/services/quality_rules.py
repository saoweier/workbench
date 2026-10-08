"""质量规则（P3/T12）。

对照 docs/04-development-plan.md T12 原文的五类规则：

| 规则 | code | 级别 |
|---|---|---|
| 事实引用 | `FACT_UNSUPPORTED` | error |
| 数字一致性 | `NUMBER_MISMATCH` | error |
| 模板边界 | `TEMPLATE_BOUNDARY` | error |
| 缺图与字体 | `MISSING_ARTIFACT` / `FONT_UNAVAILABLE` | error |
| 重复表达 | `REPEATED_EXPRESSION` | warning |

设计原则（重要，P2 已踩过坑）：

1. **不重新发明检测算法**。模板边界直接调 `renderer.check_layout`，
   缺图直接调 `renderer.verify_images`。P2 曾经因为"生成用的宽度算法"与
   "校验用的宽度算法"是两套，导致生成器产出自己校验器不接受的东西。
   一套规则只能有一个实现。
2. **规则可判定、可解释**。数字一致性用集合比对而不是语义相似度，
   宁可漏报也不要把"看起来像"当"对不上"。
3. **warning 不阻断**。重复表达只提示，不拦渲染——它是风格问题不是正确性问题。
4. **不可修复的问题单独标出**。字体缺失、图片缺失是**环境问题**，
   让模型"再改一版文案"解决不了，标 `UNREPAIRABLE_CODES` 交由人工。

本模块**只检测、不修改**。修复在 `repair_service`。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .claim_rules import EVIDENCE_REQUIRED_KINDS, validate_claims_sources
from .profile_store import ProfileVersion
from .renderer import check_layout, verify_images

#: 环境/文件类问题：模型改不动，不进修复循环
UNREPAIRABLE_CODES: frozenset[str] = frozenset({
    "FONT_UNAVAILABLE",
    "MISSING_ARTIFACT",
    # 产物数量/页序缺失属于渲染环境问题：让模型再写一版文案解决不了
    "IMAGE_COUNT_MISMATCH",
    "PAGE_SEQUENCE_INCOMPLETE",
})

#: 数字提取：阿拉伯数字（含小数、千分位）与百分数
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")

#: 中文数词 → 数字。只处理稿件里真实会出现的量级。
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


@dataclass
class QualityIssue:
    """一条质量问题。level/code 与 renderer.PageIssue 保持同形，便于合并展示。"""

    level: str            # error / warning / info
    code: str
    message: str
    location: str | None = None    # 如 "page[2].body[1]" / "claims[C01]"
    platform: str | None = None
    repairable: bool = True

    def as_dict(self) -> dict:
        return {
            "level": self.level, "code": self.code, "message": self.message,
            "location": self.location, "platform": self.platform,
            "repairable": self.repairable,
        }


@dataclass
class QualityReport:
    platform: str
    issues: list[QualityIssue] = field(default_factory=list)
    checked_pages: int = 0

    @property
    def errors(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def repairable(self) -> list[QualityIssue]:
        return [i for i in self.errors if i.repairable]

    @property
    def unrepairable(self) -> list[QualityIssue]:
        return [i for i in self.errors if not i.repairable]

    @property
    def ok(self) -> bool:
        """只有 error 阻断；warning 不阻断。"""
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "platform": self.platform,
            "ok": self.ok,
            "checked_pages": self.checked_pages,
            "errors": [i.as_dict() for i in self.errors],
            "warnings": [i.as_dict() for i in self.warnings],
            "repairable_codes": sorted({i.code for i in self.repairable}),
            "unrepairable_codes": sorted({i.code for i in self.unrepairable}),
            "needs_manual": bool(self.unrepairable),
        }


# ---------------------------------------------------------------- 提取工具


def extract_numbers(text: str) -> set[str]:
    """抽出文本里的数字（归一化：去千分位、去尾随 .0、百分号统一）。

    归一化是为了让 "1,000" 与 "1000"、"50%" 与 "50 %" 视为同一个数字。
    """
    out: set[str] = set()
    for raw in _NUMBER_RE.findall(text or ""):
        out.add(_norm_number(raw))
    return out


def _norm_number(raw: str) -> str:
    s = (raw or "").strip()
    pct = s.endswith("%")
    if pct:
        s = s[:-1]
    s = s.replace(",", "")
    try:
        f = float(s)
        s = str(int(f)) if f == int(f) else str(f)
    except ValueError:
        pass
    return s + ("%" if pct else "")


def _cn_number_to_int(s: str) -> int | None:
    """极简中文数词解析：一/二/三/十/十二/二十/二十三。够用即止。"""
    s = s.strip()
    if not s:
        return None
    if s == "十":
        return 10
    if "十" in s:
        left, _, right = s.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(s) == 1:
        return _CN_DIGITS.get(s)
    # 二百 / 三百
    if len(s) == 2 and s[0] in {"二", "两", "三", "四", "五"} and s[1] == "百":
        return _CN_DIGITS[s[0]] * 100
    return None


# ---------------------------------------------------------------- 各条规则


def _rule_fact_support(draft: dict, claims: list[dict],
                       sources: list[dict]) -> list[QualityIssue]:
    """事实引用：页面声称的 claim 必须存在，且事实类 claim 有依据。

    这里区分两类问题，处置方式完全不同：

    - **页面级**（`FACT_UNSUPPORTED`）：页面引了不存在的 claim。
      这是**文本问题**，删掉那个引用就能修——可修。
    - **母稿级**（`DANGLING_SOURCE_REF` / `CLAIM_WITHOUT_EVIDENCE` /
      `SNIPPET_ONLY_FACT` / `FABRICATED_EXPERIENCE`）：问题出在
      claims↔sources 之间，**改页面文案无法修复**。把它塞进修复循环
      只会让模型反复重写页面却永远修不掉，白烧轮次和调用次数。
      这类标 `repairable=False`，直接交人工或回到研究阶段补来源。
    """
    issues: list[QualityIssue] = []
    known = {c.get("id"): c for c in claims}

    for p in draft.get("pages", []):
        for cid in (p.get("claim_ids") or []):
            if cid not in known:
                issues.append(QualityIssue(
                    level="error", code="FACT_UNSUPPORTED",
                    message=f"页面引用了母稿中不存在的主张 {cid}",
                    location=f"page[{p.get('index')}].claim_ids",
                    repairable=True,
                ))

    # 母稿侧：复用 claim_rules 的判定，不另写一套
    for f in validate_claims_sources(claims, sources):
        if f.level != "error":
            continue
        issues.append(QualityIssue(
            level="error",
            code=f.code if f.code in {"CLAIM_WITHOUT_EVIDENCE", "SNIPPET_ONLY_FACT",
                                      "FABRICATED_EXPERIENCE", "DANGLING_SOURCE_REF"}
            else "FACT_UNSUPPORTED",
            message=f.message, location=f.location,
            # 母稿级问题不是改页面文字能解决的
            repairable=False,
        ))
    return issues


def _rule_number_consistency(draft: dict, claims: list[dict]) -> list[QualityIssue]:
    """数字一致性：页面正文里的数字必须在该页所引 claim 的陈述里出现。

    只在页面确实引用了 claim 时检查。没有引用主张的页面（如纯行动建议页）
    出现的数字属于表达性内容，不强制与主张对齐——否则会把"3 步走"这类
    结构数字误判为事实错误。
    """
    issues: list[QualityIssue] = []
    known = {c.get("id"): c for c in claims}

    for p in draft.get("pages", []):
        cids = [c for c in (p.get("claim_ids") or []) if c in known]
        if not cids:
            continue
        supported: set[str] = set()
        for cid in cids:
            stmt = str(known[cid].get("statement") or "")
            supported |= extract_numbers(stmt)
            supported |= {str(_cn_number_to_int(x)) for x in
                          re.findall(r"[一二两三四五六七八九十百]+", stmt)
                          if _cn_number_to_int(x) is not None}

        for bi, line in enumerate(p.get("body") or []):
            for num in extract_numbers(line):
                if num not in supported:
                    issues.append(QualityIssue(
                        level="error", code="NUMBER_MISMATCH",
                        message=(f"正文数字 {num} 在所引主张 "
                                 f"{cids} 中没有对应依据"),
                        location=f"page[{p.get('index')}].body[{bi}]",
                    ))
    return issues


def _rule_template_boundary(draft: dict, profile: ProfileVersion) -> list[QualityIssue]:
    """模板边界：复用渲染前的布局检测，保证检测与渲染同一套规则。"""
    out: list[QualityIssue] = []
    for i in check_layout(draft, profile):
        page = getattr(i, "page_index", None)
        out.append(QualityIssue(
            level=i.level,
            code=i.code if i.level == "error" else "TEMPLATE_WARN",
            message=i.message,
            location=f"page[{page}]" if page is not None else None,
            repairable=(i.code not in UNREPAIRABLE_CODES),
        ))
    return out


def _rule_artifacts(render_result: Any, artifact_root, profile: ProfileVersion,
                    expected_pages: int | None = None) -> list[QualityIssue]:
    """缺图与字体：渲染产出之后才能判，复用 renderer.verify_images。"""
    if render_result is None:
        return []
    issues: list[QualityIssue] = []

    # 1) 缺图 / 空图 / 尺寸不符 / 导出数量与页数不符（环境与文件问题，模型改不动）
    for i in verify_images(render_result, artifact_root, expected_pages=expected_pages):
        issues.append(QualityIssue(
            level="error",
            code=i.code if i.code in UNREPAIRABLE_CODES else "MISSING_ARTIFACT",
            message=i.message,
            location=f"page[{i.page_index}]" if i.page_index is not None else None,
            repairable=False,
        ))

    # 2) 字体：配置声明的字体族若本机没有，Chromium 会**静默回退**，
    #    页面不报任何错但字形已经变了。所以必须主动核对，不能只看渲染成功。
    fam = profile.render.font_family
    if fam and not _font_available(fam):
        issues.append(QualityIssue(
            level="error", code="FONT_UNAVAILABLE",
            message=(f"配置声明的字体 {fam!r} 在本机不可用，"
                     "渲染会静默回退到其他字形（成品看着正常但字形已变）"),
            repairable=False,
        ))
    return issues


def _rule_rank_contract(draft: dict, rank_count: int | None,
                        sources: list[dict] | None) -> list[QualityIssue]:
    """榜单专项验收：条目数、名次顺序、指标对应项目，复用 acceptance.rank_contract。"""
    if not rank_count:
        return []
    from .acceptance import rank_contract
    out: list[QualityIssue] = []
    for check in rank_contract(draft.get("pages", []), rank_count, sources=sources)["checks"]:
        if check["passed"]:
            continue
        out.append(QualityIssue(
            level="error", code=check["code"], message=check["problem"],
            location=check["label"], repairable=False,
        ))
    return out


def _font_available(family: str) -> bool:
    """用 fc-list 核对字体是否真的安装，而不是相信配置里的声明。"""
    import shutil
    import subprocess

    if not shutil.which("fc-list"):
        return True   # 无法核对时不误报为缺失
    try:
        out = subprocess.run(
            ["fc-list", ":", "family"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        ).stdout
    except Exception:
        return True
    if out is None:
        return True   # 命令未能提供可核验的输出时按"无法核对"处理
    return family.strip().lower() in out.lower()


def _rule_repeated_expression(draft: dict) -> list[QualityIssue]:
    """重复表达：同一平台内页面之间高度相似 → warning，不阻断。"""
    issues: list[QualityIssue] = []
    pages = draft.get("pages", [])
    seen: list[tuple[int, set[str]]] = []

    for p in pages:
        text = (p.get("heading", "") + "".join(p.get("body") or []))
        sh = _shingles(text, 3)
        if not sh:
            continue
        for idx, prev in seen:
            if _jaccard(sh, prev) >= 0.9:
                issues.append(QualityIssue(
                    level="warning", code="REPEATED_EXPRESSION",
                    message=f"第 {p.get('index')} 页与第 {idx} 页表达高度重复",
                    location=f"page[{p.get('index')}]",
                ))
                break
        seen.append((p.get("index"), sh))
    return issues


def _shingles(text: str, n: int) -> set[str]:
    t = re.sub(r"\s+", "", text or "")
    if len(t) < n:
        return set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------- 入口


def check_quality(
    draft: dict,
    profile: ProfileVersion,
    *,
    claims: list[dict] | None = None,
    sources: list[dict] | None = None,
    render_result: Any = None,
    artifact_root=None,
    rank_count: int | None = None,
) -> QualityReport:
    """对单个平台稿做全套质量检查。

    `draft` 形如 {"platform", "title", "caption", "pages": [...]}，与渲染器同形。
    `render_result` 传入时额外检查缺图与字体；不传则只做渲染前检查。
    `rank_count` 传入时额外跑榜单专项验收（条目数 / 名次顺序 / 指标对应项目）。
    """
    platform = draft.get("platform") or getattr(profile, "platform", "")
    claims = claims or []
    sources = sources or []

    rep = QualityReport(platform=platform, checked_pages=len(draft.get("pages", [])))
    rep.issues += _rule_fact_support(draft, claims, sources)
    rep.issues += _rule_number_consistency(draft, claims)
    rep.issues += _rule_template_boundary(draft, profile)
    rep.issues += _rule_rank_contract(draft, rank_count, sources)
    rep.issues += _rule_artifacts(render_result, artifact_root, profile,
                                  expected_pages=len(draft.get("pages", [])) if render_result is not None else None)
    rep.issues += _rule_repeated_expression(draft)
    return rep
