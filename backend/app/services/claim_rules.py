"""Claim / Source 引用完整性规则（P2/T09）。

从 seed_validator 抽出，供三处共用：
- seed 导入校验（保持原 finding code 不变，P0 测试不回归）
- 研究服务产出校验
- 改写服务引用链校验

规则（对照 docs/02-modules.md §M02、docs/03-data-and-api.md §3）：

| code | 含义 |
|---|---|
| CLAIM_ID_DUPLICATE | claims 内 id 重复 |
| SOURCE_ID_DUPLICATE | sources 内 id 重复 |
| DANGLING_SOURCE_REF | claim 引用了不存在的 source |
| CLAIM_WITHOUT_EVIDENCE | 事实类主张没有任何来源 |
| SNIPPET_ONLY_FACT | fact 只靠搜索摘要支撑——搜索摘要不能当事实依据 |
| FABRICATED_EXPERIENCE | 文本自称"亲测"但 claim 类型不是用户经历 |
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# ---------------------------------------------------------------- 常量

#: 必须可溯源的主张类型（观点类不强制）
EVIDENCE_REQUIRED_KINDS = {"fact", "project_plan", "project_goal", "document_observation",
                           "planned_measurement", "measurement"}

#: 可以承载"亲测"语义的类型
EXPERIENCE_KINDS = {"user_experience", "ai_experiment"}

#: 依据类型：只有全文才能支撑事实
FULL_BASIS = {"full_text", "user_provided", "local_file", "document"}

#: 自称亲测的措辞（确定性文本规则，不依赖模型判断）
EXPERIENCE_PHRASES = ("亲测", "我试了", "我试过", "我实测", "我用过", "实测过", "亲身试")

#: 允许的 claim 类型
ALLOWED_CLAIM_KINDS = {
    "fact", "opinion", "user_experience", "ai_experiment",
    "lead", "hypothesis", "project_plan", "project_goal",
    "document_observation", "planned_measurement", "measurement",
}


class RuleFinding(BaseModel):
    level: str  # error / warning / info
    code: str
    message: str
    location: str | None = None


def _source_basis(s: dict[str, Any]) -> str:
    """推断 source 的依据类型。没写 excerpt_basis 时按是否有 url 粗判。"""
    basis = s.get("excerpt_basis")
    if basis:
        return basis
    # 老 seed 没有该字段：本地文件视为全文，仅 url 视为摘要
    if s.get("path") or s.get("file_key"):
        return "user_provided"
    return "search_snippet"


def validate_claims_sources(
    claims: list[dict[str, Any]] | None,
    sources: list[dict[str, Any]] | None,
    *,
    check_experience: bool = True,
) -> list[RuleFinding]:
    """校验 claim/source 的引用完整性与依据强度。"""
    findings: list[RuleFinding] = []
    claims = claims or []
    sources = sources or []

    claim_ids = [c.get("id") for c in claims]
    source_ids = [s.get("id") for s in sources]

    if len(set(claim_ids)) != len(claim_ids):
        findings.append(RuleFinding(level="error", code="CLAIM_ID_DUPLICATE",
                                    message="claims 中存在重复 id"))
    if len(set(source_ids)) != len(source_ids):
        findings.append(RuleFinding(level="error", code="SOURCE_ID_DUPLICATE",
                                    message="sources 中存在重复 id"))

    src_index = {s.get("id"): s for s in sources}
    source_id_set = set(source_ids)

    for c in claims:
        cid = c.get("id")
        kind = c.get("kind")
        refs = c.get("source_ids") or []

        missing = [sid for sid in refs if sid not in source_id_set]
        if missing:
            findings.append(RuleFinding(
                level="error", code="DANGLING_SOURCE_REF",
                message=f"claim {cid} 引用了不存在的 source: {missing}",
                location=f"claims[{cid}].source_ids",
            ))

        if kind not in ALLOWED_CLAIM_KINDS:
            findings.append(RuleFinding(
                level="warning", code="CLAIM_KIND_UNKNOWN",
                message=f"claim {cid} 的类型 {kind!r} 不在枚举内",
                location=f"claims[{cid}].kind",
            ))

        # 事实类必须有来源
        if not refs and (kind in EVIDENCE_REQUIRED_KINDS):
            findings.append(RuleFinding(
                level="error", code="CLAIM_WITHOUT_EVIDENCE",
                message=f"claim {cid} 没有任何 source，事实类主张必须可溯源",
                location=f"claims[{cid}]",
            ))

        # 搜索摘要不能当事实依据
        if kind in EVIDENCE_REQUIRED_KINDS and refs:
            bases = []
            for sid in refs:
                s = src_index.get(sid)
                if s is None:
                    continue
                bases.append(_source_basis(s))
            if bases and all(b not in FULL_BASIS for b in bases):
                findings.append(RuleFinding(
                    level="error", code="SNIPPET_ONLY_FACT",
                    message=(f"claim {cid}（{kind}）只由搜索摘要支撑；"
                             "搜索摘要不能作为事实依据，需换取全文来源或降级为线索"),
                    location=f"claims[{cid}]",
                ))

        # 禁止捏造亲测
        if check_experience:
            stmt = str(c.get("statement") or "")
            if any(p in stmt for p in EXPERIENCE_PHRASES) and kind not in EXPERIENCE_KINDS:
                findings.append(RuleFinding(
                    level="error", code="FABRICATED_EXPERIENCE",
                    message=(f"claim {cid} 的表述含亲测/实测措辞，但类型是 {kind!r}；"
                             "未经用户确认的经历不得写成亲测（需改为 user_experience 或删去措辞）"),
                    location=f"claims[{cid}]",
                ))

    return findings
