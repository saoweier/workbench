"""局部修复服务（P3/T12）。

质量规则（`quality_rules`）负责**发现问题**，本模块负责**在限定范围内修好它**。

三条硬约束：

1. **局部修复，不是重写**。只改出问题的那个字段/那一页，其余内容原样保留。
   重新生成整份稿会把已经人工确认过的内容也改掉，那叫重做不叫修复。
2. **修复不越批准**（不变量第 1 条）。修复一定新建 revision，
   旧版本与其批准记录**原地不动**；新版本从 `drafting` 开始，需重新预览与批准。
3. **越权与不可修的问题不消耗修复轮次**：
   - 模型输出里出现 approval/budget/actor 等字段 → `_PrivilegeViolation`，直接拒（复用 P2 语义）
   - 字体缺失、图片缺失 → `UNREPAIRABLE_CODES`，是环境问题，让模型再写一版文案解决不了

修复上限来自 `settings.max_repair_rounds`（默认 2），**模型不能自行提高**。
两轮之后仍不合格 → 返回 `unrepairable=True` + 问题清单，**不产出半成品**。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..core.config import get_settings
from ..core.errors import NotFound, ValidationFailed
from ..models.entities import ContentItem, ContentRevision, Event, PlatformRevision
from .compose_service import ComposeService, _PrivilegeViolation, _as_json
from .profile_store import ProfileStore, ProfileVersion
from .provider_contract import RunMode
from .quality_rules import QualityIssue, QualityReport, check_quality
from .renderer import estimate_text_width


@dataclass
class RepairOutcome:
    """一次修复尝试的完整交代。"""

    content_id: str
    platform: str
    base_revision_id: str
    new_revision_id: str | None = None
    new_revision_version: int | None = None
    platform_revision_id: str | None = None
    rounds: int = 0
    fixed: list[str] = field(default_factory=list)
    remaining: list[dict] = field(default_factory=list)
    unrepairable: bool = False
    reason: str | None = None
    run_mode: str = RunMode.LOCAL_SEED.value

    def as_dict(self) -> dict:
        return {
            "content_id": self.content_id,
            "platform": self.platform,
            "base_revision_id": self.base_revision_id,
            "new_revision_id": self.new_revision_id,
            "new_revision_version": self.new_revision_version,
            "platform_revision_id": self.platform_revision_id,
            "rounds": self.rounds,
            "fixed": self.fixed,
            "remaining": self.remaining,
            "unrepairable": self.unrepairable,
            "reason": self.reason,
            "run_mode": self.run_mode,
            "note": ("已创建新版本并完成局部修复；"
                     "旧版本与旧批准保持不变，新版本需重新预览与批准"
                     if self.new_revision_id else
                     "未创建新版本（无可修复项或需人工处理）"),
        }


#: 修复提示词模板。要求模型只回一个 JSON 对象，且**只能动指定字段**。
REPAIR_PROMPT = """\
你是内容编辑。下面这份平台稿有具体的质量问题，请**只修复列出的问题**。

平台：{platform}
标题：{title}
正文：{caption}
页面：
{pages}

需要修复的问题：
{issues}

硬性要求：
1. 只输出一个 JSON 对象，形如 {{"title": "...", "caption": "...", "pages": [{{"index":1,"layout":"cover","heading":"...","body":["..."]}}]}}。
2. **不要输出**任何审批、状态、预算、角色、路径、脚本类字段——这些不属于你决定的范围。
3. 未列出的问题不要改动；没有问题的页保持原样。
4. 只能使用母稿中已有的主张 id（{claim_ids}），不得新增事实或数字。
5. 页数保持在 {page_min}–{page_max} 页之间，序号从 1 连续排列。
"""


class RepairService:
    """图文级局部修复。"""

    def __init__(self, session_factory, runtime=None, *, actor: str = "coisini",
                 profiles: ProfileStore | None = None) -> None:
        self.sf = session_factory
        self.actor = actor
        self.settings = get_settings()
        self.profiles = profiles or ProfileStore()
        self.compose = ComposeService(session_factory, runtime, actor=actor,
                                      profiles=self.profiles)

    # ============================================================ 主入口

    def repair_platform(
        self,
        platform_revision_id: str,
        *,
        run_mode: RunMode = RunMode.LOCAL_SEED,
        max_rounds: int | None = None,
        render_result=None,
        artifact_root=None,
    ) -> RepairOutcome:
        """对一个平台稿跑「检测 → 修复 → 复检」，最多 max_rounds 轮。"""
        max_rounds = (max_rounds if max_rounds is not None
                      else self.settings.max_repair_rounds)

        with self.sf() as s:
            pr = s.get(PlatformRevision, platform_revision_id)
            if pr is None:
                raise NotFound(f"平台版本不存在：{platform_revision_id}")
            content_id = pr.content_revision.content_id
            base_revision_id = pr.content_revision_id
            platform = pr.platform
            profile = (self.profiles.get(pr.profile_version_id)
                       or self.profiles.latest(platform))
            draft = {
                "platform": platform, "title": pr.title, "caption": pr.caption,
                "pages": json.loads(json.dumps(pr.pages_json["pages"])),
            }
            claims = list((pr.content_revision.claims_json or {}).get("claims", []))
            sources = list((pr.content_revision.claims_json or {}).get("sources", []))
            # 榜单稿在生成时就冻结了条目数，修复阶段据此复算专项验收契约
            pages_json = pr.pages_json or {}
            rank_count = pages_json.get("rank_count") if pages_json.get("form") == "ranking" else None

        out = RepairOutcome(content_id=content_id, platform=platform,
                            base_revision_id=base_revision_id,
                            run_mode=run_mode.value)

        report = check_quality(draft, profile, claims=claims, sources=sources,
                               render_result=render_result,
                               artifact_root=artifact_root, rank_count=rank_count)

        if report.ok and not report.warnings:
            out.reason = "未发现质量问题，无需修复"
            return out

        # 只有环境/文件类问题 → 修不了，直接交人工，不浪费模型轮次
        if not report.repairable and report.unrepairable:
            out.unrepairable = True
            out.remaining = [i.as_dict() for i in report.unrepairable]
            out.reason = ("存在非文本可修复的问题（字体/文件），"
                          "需人工处理环境后重跑，不进模型修复循环")
            self._event(content_id, "repair_needs_manual",
                        {"platform": platform, "issues": out.remaining}, run_mode)
            return out

        current = draft
        for rnd in range(1, max_rounds + 1):
            out.rounds = rnd
            issues = report.repairable
            if not issues:
                break
            try:
                current = self._repair_once(
                    current, profile=profile, issues=issues,
                    claims=claims, run_mode=run_mode,
                )
            except _PrivilegeViolation as exc:
                # 越权是**权限问题**不是格式问题：再试一百次也不该让它拿到这些字段
                out.unrepairable = True
                out.remaining = [{"level": "error", "code": exc.code,
                                  "message": exc.message, "location": None}]
                out.reason = "模型输出包含越权字段，已直接拒绝（不进入修复循环）"
                self._event(content_id, "repair_privilege_violation",
                            {"platform": platform, "message": exc.message}, run_mode)
                return out
            except ValidationFailed as exc:
                out.reason = f"修复第 {rnd} 轮未产出可用结果：{exc.message}"
                break

            before = {i.code + "|" + str(i.location) for i in report.repairable}
            report = check_quality(current, profile, claims=claims, sources=sources,
                                   render_result=render_result,
                                   artifact_root=artifact_root, rank_count=rank_count)
            after = {i.code + "|" + str(i.location) for i in report.repairable}
            for key in sorted(before - after):
                out.fixed.append(key)
            if not report.repairable:
                break

        if report.repairable:
            out.unrepairable = True
            out.remaining = [i.as_dict() for i in report.repairable]
            out.reason = f"经 {out.rounds} 轮修复仍不合格，不产出半成品"
            self._event(content_id, "repair_exhausted",
                        {"platform": platform, "rounds": out.rounds,
                         "remaining": out.remaining}, run_mode)
            return out

        # 修好了 → 新建 revision（旧版本与旧批准原地不动）
        self._persist(content_id, base_revision_id, current, profile,
                      report=report, rounds=out.rounds, run_mode=run_mode)
        new_rev_id, new_pr_id, new_version = self._latest_new(content_id)
        out.new_revision_id = new_rev_id
        out.new_revision_version = new_version
        out.platform_revision_id = new_pr_id
        out.remaining = [i.as_dict() for i in report.warnings]
        out.reason = f"已在 {out.rounds} 轮内修复（剩余项均为提示级，不阻断）"
        self._event(content_id, "repaired",
                    {"platform": platform, "rounds": out.rounds,
                     "new_revision_version": new_version,
                     "fixed": out.fixed}, run_mode)
        return out

    # ============================================================ 单轮修复

    def _repair_once(self, draft: dict, *, profile: ProfileVersion,
                     issues: list[QualityIssue], claims: list[dict],
                     run_mode: RunMode) -> dict:
        """跑一轮修复。规则先行，模型只补语义问题。"""
        lim = profile.limits

        # 1) **确定性修复**：能靠规则改的绝不问模型。
        #    模板边界类问题（行太宽/标题太长/页数越界）是纯算术问题，
        #    交给模型反而引入不确定性，且多一次可能计费的调用。
        fixed = self._fix_by_rules(draft, profile, issues, claims)

        # 2) 剩下的语义问题（数字对不上、事实引用缺失）才需要模型
        semantic = [i for i in issues
                    if i.code in {"NUMBER_MISMATCH", "FACT_UNSUPPORTED"}]
        if not semantic:
            return fixed

        if run_mode == RunMode.LOCAL_SEED:
            # local_seed 明确不调模型：把问题留着如实上报，不假装已经修过
            return fixed

        prompt = self._repair_prompt(fixed, semantic, claims, lim)
        return self._ask_model(prompt, schema=self._repair_schema(lim),
                               run_mode=run_mode, draft=fixed)

    def _ask_model(self, prompt: str, *, schema: dict, run_mode: RunMode,
                   draft: dict) -> dict:
        """向模型要一版修复稿。越权字段直接抛 `_PrivilegeViolation`。"""
        from .compose_service import _assert_no_dangerous_text, FORBIDDEN_FIELDS

        runtime = self.compose.runtime
        if runtime is None:
            raise ValidationFailed("未注入 Provider 运行时，无法进行模型修复")

        call, res = runtime.complete_text(
            prompt=prompt, system="你只做局部文字修复，输出严格 JSON。",
            json_schema=schema, content_id=None,
            prompt_version="repair@1", run_mode=run_mode,
        )
        if not res.ok or not res.text:
            raise ValidationFailed(
                f"修复调用失败：{res.error_code or 'NO_RESULT'} {res.error_message or ''}".strip()
            )

        parsed = res.parsed
        if parsed is None:
            from .adapters.base import BaseAdapter

            parsed, err = BaseAdapter.safe_json(res.text)
            if parsed is None:
                raise ValidationFailed(f"修复结果不是合法 JSON：{err}")

        # 越权检查：与母稿生成同一套字段黑名单
        raw = _as_json(parsed)
        for key in raw:
            if str(key).strip().lower() in FORBIDDEN_FIELDS:
                raise _PrivilegeViolation(
                    f"修复输出含越权字段 {key!r}；模型不得决定审批/状态/预算/角色",
                    fields=[str(key)],
                )
        _assert_no_dangerous_text(raw, where="修复输出")

        merged = json.loads(json.dumps(draft))
        if isinstance(parsed.get("title"), str):
            merged["title"] = parsed["title"]
        if isinstance(parsed.get("caption"), str):
            merged["caption"] = parsed["caption"]
        if isinstance(parsed.get("pages"), list) and parsed["pages"]:
            merged["pages"] = parsed["pages"]
        return merged

    @staticmethod
    def _repair_schema(lim) -> dict:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string", "maxLength": lim.max_title_chars},
                "caption": {"type": "string", "maxLength": lim.max_caption_chars},
                "pages": {
                    "type": "array",
                    "minItems": lim.min_pages, "maxItems": lim.max_pages,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "index": {"type": "integer"},
                            "layout": {"type": "string", "enum": ["cover", "checklist"]},
                            "heading": {"type": "string",
                                        "maxLength": lim.max_heading_chars},
                            "body": {"type": "array", "items": {"type": "string"}},
                            "claim_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["index", "layout", "heading", "body"],
                    },
                },
            },
            "required": ["title", "pages"],
        }

    def _repair_prompt(self, draft: dict, issues: list[QualityIssue],
                       claims: list[dict], lim) -> str:
        pages_txt = json.dumps(draft.get("pages", []), ensure_ascii=False, indent=1)
        issue_txt = "\n".join(
            f"- [{i.code}] {i.message}（位置：{i.location or '整体'}）" for i in issues
        )
        return REPAIR_PROMPT.format(
            platform=draft.get("platform", ""),
            title=draft.get("title", ""),
            caption=(draft.get("caption", "") or "")[:200],
            pages=pages_txt[:4000],
            issues=issue_txt,
            claim_ids=[c.get("id") for c in claims],
            page_min=lim.min_pages, page_max=lim.max_pages,
        )

    # ============================================================ 规则修复

    def _fix_by_rules(self, draft: dict, profile: ProfileVersion,
                      issues: list[QualityIssue], claims: list[dict]) -> dict:
        """能确定性修的一律就地修掉，不消耗模型轮次。"""
        out = json.loads(json.dumps(draft))
        lim = profile.limits
        avail_w = profile.render.width_px - 2 * profile.render.safe_margin_px
        codes = {i.code for i in issues}

        # 标题超长 → 截断
        if "TITLE_TOO_LONG" in codes and len(out.get("title", "")) > lim.max_title_chars:
            out["title"] = out["title"][:lim.max_title_chars]

        # 正文超长 → 截断
        if "CAPTION_TOO_LONG" in codes and len(out.get("caption", "")) > lim.max_caption_chars:
            out["caption"] = out["caption"][:lim.max_caption_chars]

        pages = out.get("pages", [])

        # 页序不连续 → 重排（保序，不改变内容顺序）
        if "PAGE_INDEX_NOT_CONTIGUOUS" in codes:
            for n, p in enumerate(pages, start=1):
                p["index"] = n

        # 页数越界 → 截断（只保留上限内页数），少于下限则无法靠删减解决
        if "PAGE_COUNT_OUT_OF_RANGE" in codes and len(pages) > lim.max_pages:
            pages = pages[:lim.max_pages]
            out["pages"] = pages

        # 标题/行宽溢出 → 缩短文本而不是缩字号（字号是模板的职责）
        # 注意：标题也会溢出。`check_layout` 在**最小字号**下仍超宽时判定
        # HEADING_TOO_WIDE；此时缩放已经无路可走，只能截断文字。
        from .renderer import auto_heading_font

        for p in pages:
            heading = p.get("heading", "")
            if len(heading) > lim.max_heading_chars:
                p["heading"] = heading[:lim.max_heading_chars]
                heading = p["heading"]
            is_cover = p.get("layout") == "cover"
            # 与 check_layout 同一套字号算法：它就是这么算、这么判的
            h_font = auto_heading_font(heading, avail_w, cover=is_cover)
            if heading and estimate_text_width(heading, h_font) > avail_w:
                p["heading"] = self._fit_line(heading, h_font, avail_w)

            body = p.get("body") or []
            # 与 check_layout 一致：正文页 52px、封面 60px（_body_scale 的下限）
            font_px = 52 if not is_cover else 60
            fixed_body = [self._fit_line(line, font_px, avail_w) for line in body]
            if fixed_body:
                p["body"] = [ln for ln in fixed_body if ln]
            # 行数越界 → 删掉多余行（保留前 N 行，信息密度优先）
            if len(p.get("body") or []) > lim.max_body_lines_per_page:
                p["body"] = p["body"][:lim.max_body_lines_per_page]

        # 悬空 claim 引用 → 摘掉不存在的 id（保留存在的）
        known = {c.get("id") for c in claims}
        for p in pages:
            if p.get("claim_ids"):
                p["claim_ids"] = [c for c in p["claim_ids"] if c in known]
                if not p["claim_ids"]:
                    p.pop("claim_ids")

        out["pages"] = pages
        return out

    @staticmethod
    def _fit_line(line: str, font_px: int, avail_w: float) -> str:
        """按渲染器的宽度算法逐字缩短，直到不再溢出。

        两个必须注意的点：

        1. 用与 `check_layout` **同一个** `estimate_text_width`，
           否则又会出现"修完了校验器还是说超宽"（P2 踩过这个坑）。
        2. **省略号本身占宽度**。截断后追加 "…" 会让宽度重新涨回去，
           所以要先给省略号留出位置再二分——否则会出现
           "截到 942px 仍然 > 936px" 这种差一点点的反复失败。
        """
        if not line or estimate_text_width(line, font_px) <= avail_w:
            return line

        ell = "…"
        ell_w = estimate_text_width(ell, font_px)
        budget = avail_w - ell_w
        if budget <= 0:
            # 连省略号都放不下：只能空掉这一行，交给校验器如实上报
            return ""

        lo, hi = 0, len(line)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if estimate_text_width(line[:mid], font_px) <= budget:
                lo = mid
            else:
                hi = mid - 1
        cut = lo
        if cut <= 0:
            return ell if ell_w <= avail_w else ""
        if cut >= len(line):
            return line[:cut]
        return line[:cut] + ell

    # ============================================================ 落库

    def _persist(self, content_id: str, base_revision_id: str, draft: dict,
                 profile: ProfileVersion, *, report: QualityReport, rounds: int,
                 run_mode: RunMode) -> None:
        """新建 revision + 克隆平台稿并替换被修复的那个平台。"""
        with self.sf() as s:
            content = s.get(ContentItem, content_id)
            if content is None:
                raise NotFound(f"内容不存在：{content_id}")
            base = s.get(ContentRevision, base_revision_id)
            if base is None:
                raise NotFound(f"基线版本不存在：{base_revision_id}")

            # 旧版本的批准必须原封不动 —— 这里刻意不触碰 base 及其平台稿
            last = (
                s.query(ContentRevision).filter_by(content_id=content_id)
                .order_by(ContentRevision.version.desc()).first()
            )
            new_rev = ContentRevision(
                content_id=content_id,
                version=(last.version + 1) if last else 1,
                parent_id=base.id,
                brief_json=dict(base.brief_json or {}),
                claims_json=dict(base.claims_json or {}),
                limitations_json=dict(base.limitations_json or {}),
                input_hash=f"repair:{base.input_hash}:{rounds}:{draft.get('title','')[:32]}",
                seed_sha256=base.seed_sha256,
            )
            s.add(new_rev)
            s.flush()

            for pr in s.query(PlatformRevision).filter_by(content_revision_id=base.id).all():
                pages = (draft.get("pages") if pr.platform == draft.get("platform")
                         else json.loads(json.dumps(pr.pages_json["pages"])))
                s.add(PlatformRevision(
                    content_revision_id=new_rev.id,
                    platform=pr.platform,
                    version=1,
                    title=(draft.get("title") if pr.platform == draft.get("platform")
                           else pr.title),
                    caption=(draft.get("caption") if pr.platform == draft.get("platform")
                             else pr.caption),
                    pages_json={"pages": json.loads(json.dumps(pages))},
                    profile_version_id=pr.profile_version_id,
                    platform_profile_version=pr.platform_profile_version,
                    content_hash=f"repair:{pr.content_hash}",
                    state="drafting",   # 修复版不继承任何批准
                ))

            content.active_revision_id = new_rev.id
            s.commit()

    def _latest_new(self, content_id: str) -> tuple[str | None, str | None, int | None]:
        with self.sf() as s:
            content = s.get(ContentItem, content_id)
            if content is None or not content.active_revision_id:
                return None, None, None
            rev = s.get(ContentRevision, content.active_revision_id)
            pr = (
                s.query(PlatformRevision)
                .filter_by(content_revision_id=content.active_revision_id)
                .order_by(PlatformRevision.platform).first()
            )
            return content.active_revision_id, (pr.id if pr else None), rev.version

    def _event(self, content_id: str, type_: str, payload: dict, run_mode: RunMode) -> None:
        with self.sf() as s:
            s.add(Event(entity_type="content_item", entity_id=content_id, type=type_,
                        actor=self.actor, run_mode=run_mode.value, payload=payload))
            s.commit()
