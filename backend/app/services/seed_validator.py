"""C001 seed 校验（T01）。

校验项（对照 seeds/C001/source-notes.md 第 14、18 行）：
- JSON 可解析、schema_version 匹配
- 页序连续、从 1 开始、无重复
- claim_ids / source_ids 引用全部可解析，不允许悬空
- layout 来自枚举
- render_profile 存在且 platform_upload_verified 明确标注
- 来源文件相对路径真实存在（不存在则标 missing_local_source，不报硬错误）

原则：文件存在只证明文档存在，不证明设计已实现。校验通过 ≠ 成品质量通过 ≠ 可发布。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .claim_rules import validate_claims_sources

ALLOWED_LAYOUTS = {"cover", "checklist", "quote", "compare", "steps"}


class CheckFinding(BaseModel):
    level: Literal["error", "warning", "info"]
    code: str
    message: str
    location: str | None = None


class SeedValidationReport(BaseModel):
    display_id: str
    seed_path: str
    seed_sha256: str
    validated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: str
    run_mode: str
    dataset_kind: str
    platforms: dict[str, dict[str, Any]] = Field(default_factory=dict)
    sources: dict[str, Any] = Field(default_factory=dict)
    findings: list[CheckFinding] = Field(default_factory=list)

    @property
    def errors(self) -> list[CheckFinding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[CheckFinding]:
        return [f for f in self.findings if f.level == "warning"]

    @property
    def passed(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if self.passed:
            tail = f"，{len(self.warnings)} 条提示" if self.warnings else ""
            return f"通过（无阻断项{tail}）"
        return f"未通过，{len(self.errors)} 条阻断项"


EXPECTED_SCHEMA_PREFIX = "seed-c001"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_seed(seed_path: str | Path) -> SeedValidationReport:
    seed_path = Path(seed_path).resolve()
    findings: list[CheckFinding] = []

    if not seed_path.exists():
        raise FileNotFoundError(f"seed 文件不存在: {seed_path}")

    try:
        data = json.loads(seed_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"seed.json 不是合法 JSON: {exc}") from exc

    report = SeedValidationReport(
        display_id=data.get("display_id", "UNKNOWN"),
        seed_path=str(seed_path),
        seed_sha256=sha256_file(seed_path),
        schema_version=str(data.get("schema_version", "")),
        run_mode=str(data.get("run_mode", "")),
        dataset_kind=str(data.get("dataset_kind", "")),
    )

    # 1) schema_version
    if not report.schema_version.startswith(EXPECTED_SCHEMA_PREFIX):
        findings.append(
            CheckFinding(
                level="error",
                code="SCHEMA_VERSION_MISMATCH",
                message=f"schema_version 期望以 {EXPECTED_SCHEMA_PREFIX} 开头，实际 {report.schema_version!r}",
                location="$schema_version",
            )
        )

    # 2) run_mode 必须显式，不能伪装成真实 API 运行
    if report.run_mode not in {"local_seed", "fixture", "real"}:
        findings.append(
            CheckFinding(
                level="error",
                code="RUN_MODE_INVALID",
                message=f"run_mode 必须是 local_seed/fixture/real 之一，实际 {report.run_mode!r}",
                location="$run_mode",
            )
        )
    elif report.run_mode == "local_seed" and data.get("api_generated_in_app") is True:
        findings.append(
            CheckFinding(
                level="error",
                code="RUN_MODE_CONTAMINATED",
                message="run_mode=local_seed 却声明 api_generated_in_app=true，本地草稿不能伪装为应用自动生成",
                location="$api_generated_in_app",
            )
        )

    # 3) claims / sources 引用完整性
    #    规则已抽到 claim_rules，与研究/改写服务共用同一套判据。
    #    pydantic 的 min_length 等约束用不到，这里只做结构校验。
    claims = data.get("claims", [])
    sources = data.get("sources", [])
    claim_ids = {c.get("id") for c in claims}
    for rf in validate_claims_sources(claims, sources):
        findings.append(
            CheckFinding(level=rf.level, code=rf.code, message=rf.message, location=rf.location)
        )

    # 4) 来源文件真实存在性（缺失只警告，标 missing_local_source）
    src_dir = seed_path.parent
    source_check: dict[str, Any] = {"ok": [], "missing": []}
    for s in data.get("sources", []):
        p = s.get("path")
        if not p:
            continue
        target = (src_dir / p).resolve()
        if target.exists():
            source_check["ok"].append({"id": s.get("id"), "path": p, "bytes": target.stat().st_size})
        else:
            source_check["missing"].append({"id": s.get("id"), "path": p})
            findings.append(
                CheckFinding(
                    level="warning",
                    code="MISSING_LOCAL_SOURCE",
                    message=f"source {s.get('id')} 指向的本地文件不存在：{p}（可完成本地渲染验证，来源完整性需补）",
                    location=f"sources[{s.get('id')}].path",
                )
            )
    report.sources = source_check

    # 5) 平台草稿：页序、layout、引用
    for pd in data.get("platform_drafts", []):
        platform = pd.get("platform", "unknown")
        pages = pd.get("pages", [])
        info: dict[str, Any] = {"page_count": len(pages), "layouts": [], "problems": []}

        idx = [p.get("index") for p in pages]
        if idx != list(range(1, len(idx) + 1)):
            findings.append(
                CheckFinding(
                    level="error",
                    code="PAGE_INDEX_NOT_CONTIGUOUS",
                    message=f"{platform} 页序不连续或未从 1 开始：{idx}",
                    location=f"platform_drafts[{platform}].pages",
                )
            )
            info["problems"].append("page_index")

        first_layout = pages[0].get("layout") if pages else None
        if first_layout != "cover":
            findings.append(
                CheckFinding(
                    level="warning",
                    code="FIRST_PAGE_NOT_COVER",
                    message=f"{platform} 首页 layout={first_layout!r}，常规图文首页应为 cover",
                    location=f"platform_drafts[{platform}].pages[0]",
                )
            )

        for p in pages:
            lay = p.get("layout")
            info["layouts"].append(lay)
            if lay not in ALLOWED_LAYOUTS:
                findings.append(
                    CheckFinding(
                        level="error",
                        code="LAYOUT_NOT_ALLOWED",
                        message=f"{platform} 第 {p.get('index')} 页 layout={lay!r} 不在枚举内",
                        location=f"platform_drafts[{platform}].pages[{p.get('index')}]",
                    )
                )
            bad = [cid for cid in p.get("claim_ids", []) if cid not in claim_ids]
            if bad:
                findings.append(
                    CheckFinding(
                        level="error",
                        code="DANGLING_CLAIM_REF",
                        message=f"{platform} 第 {p.get('index')} 页引用不存在的 claim: {bad}",
                        location=f"platform_drafts[{platform}].pages[{p.get('index')}].claim_ids",
                    )
                )
            if not p.get("body"):
                findings.append(
                    CheckFinding(
                        level="warning",
                        code="EMPTY_BODY",
                        message=f"{platform} 第 {p.get('index')} 页 body 为空",
                        location=f"platform_drafts[{platform}].pages[{p.get('index')}]",
                    )
                )

        if not pd.get("title"):
            findings.append(
                CheckFinding(
                    level="error",
                    code="MISSING_TITLE",
                    message=f"{platform} 缺少 title",
                    location=f"platform_drafts[{platform}].title",
                )
            )
        if not pd.get("caption"):
            findings.append(
                CheckFinding(
                    level="warning",
                    code="MISSING_CAPTION",
                    message=f"{platform} 缺少 caption，导出包将不含正文文本",
                    location=f"platform_drafts[{platform}].caption",
                )
            )

        info["layouts"] = sorted(set(info["layouts"]))
        report.platforms[platform] = info

    # 6) render_profile 必须显式声明未验证
    rp = data.get("render_profile")
    if not rp:
        findings.append(
            CheckFinding(
                level="error",
                code="MISSING_RENDER_PROFILE",
                message="缺少 render_profile，无法确定渲染尺寸",
                location="$render_profile",
            )
        )
    else:
        if rp.get("platform_upload_verified") is True:
            findings.append(
                CheckFinding(
                    level="warning",
                    code="UNVERIFIED_FLAG_REQUESTED",
                    message="seed 声称 platform_upload_verified=true，但仓库内不存在上传兼容性验证证据",
                    location="$render_profile.platform_upload_verified",
                )
            )
        report.platforms.setdefault("_render_profile", {}).update(
            {
                "width_px": rp.get("width_px"),
                "height_px": rp.get("height_px"),
                "format": rp.get("format"),
                "platform_upload_verified": rp.get("platform_upload_verified"),
            }
        )

    # 7) 证据边界：limitations 必须存在
    if not data.get("limitations"):
        findings.append(
            CheckFinding(
                level="warning",
                code="MISSING_LIMITATIONS",
                message="缺少 limitations，无法说明证据边界",
                location="$limitations",
            )
        )

    report.findings = findings
    return report
