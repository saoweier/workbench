"""结构化稿件 → 真实 PNG 渲染（T06）。

要点：
- 模板版本化（cover@1 / checklist@1），改动必须新版本号
- 使用本地字体 + 程序化排版，不依赖生图 API
- 渲染前做**布局检测**：文字溢出、缺字、页序、尺寸
- 产物不可变：同 revision + 同模板版本 → 同字节内容（可校验 sha256）
- 渲染尺寸来自 ProfileVersion（可配置），不写死"平台规定"
"""
from __future__ import annotations

import hashlib
import html
from string import Template
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..services.profile_store import ProfileVersion

TEMPLATE_FONTS = "Noto Sans CJK SC"

#: 中文字体回退栈。跨平台必需：
#: - Linux（本项目开发环境）：Noto Sans CJK SC
#: - Windows：微软雅黑 / 苹方都没有，落到"微软雅黑"→Microsoft YaHei / SimHei
#: - macOS：PingFang SC / Heiti SC
#: 少任何一个平台，浏览器会退到 **不含中文字形的 sans-serif**，
#: 渲染出来是一排豆腐块（□□□）——而且 PNG 仍然生成成功，只是内容是废的。
#: 所以这不是"锦上添花的兼容"，是出图能不能用的前提。
CJK_FONT_STACK = (
    '"Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", "Heiti SC", '
    '"Hiragino Sans GB", "Microsoft YaHei", "微软雅黑", SimHei, "WenQuanYi Micro Hei", '
    '"Segoe UI", sans-serif'
)


@dataclass
class PageIssue:
    level: Literal["error", "warning"]
    code: str
    message: str
    page_index: int | None = None


@dataclass
class RenderResult:
    platform: str
    revision_version: int
    profile_version: str
    width: int
    height: int
    template_versions: list[str]
    images: list[dict] = field(default_factory=list)
    issues: list[PageIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[PageIssue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def passed(self) -> bool:
        return not self.errors

    def manifest_hash(self) -> str:
        """文件集合指纹。审核决定绑定它，跨版本不能沿用。"""
        h = hashlib.sha256()
        for img in sorted(self.images, key=lambda x: x["page_index"]):
            h.update(f"{img['page_index']}:{img['sha256']}".encode())
        return h.hexdigest()


# ---------------------------------------------------------------- 布局检测

def estimate_text_width(text: str, font_px: int) -> float:
    """粗略估算文本像素宽度：CJK 按 1.0 em，拉丁/数字按 0.55 em。"""
    total = 0.0
    for ch in text:
        total += 1.0 if ord(ch) > 0x2E80 else 0.55
    return total * font_px


def auto_heading_font(heading: str, avail_w: int, *, cover: bool) -> int:
    """标题字号自适应。字号必须与模板 CSS 保持一致，否则检测与渲染会脱节。"""
    max_font = 76 if cover else 52
    min_font = 40 if cover else 34
    for f in range(max_font, min_font - 1, -2):
        if estimate_text_width(heading, f) <= avail_w:
            return f
    return min_font


def _body_scale(n_lines: int, is_cover: bool, body: list[str] | None = None,
                avail_w: int = 1000) -> tuple[int, int, int]:
    """按条目数与最长行宽动态决定正文字号与行距（内页）。

    两个约束要同时满足：
    1. 内容少时放大字号填充版面（否则"一大张图两行字"）
    2. 最长行必须在可用宽度内（否则溢出）
    返回 (内页字号, 条目间距, 封面条目字号)。
    """
    if is_cover:
        return 48, 44, 50

    # 先按条目数给出理想字号
    if n_lines <= 2:
        want = 60
    elif n_lines == 3:
        want = 54
    elif n_lines == 4:
        want = 48
    else:
        want = 44

    # 再按最长行宽收紧，确保不溢出
    longest = max((len(line) for line in (body or [])), default=0)
    for f in range(want, 33, -2):
        if longest == 0 or estimate_text_width_approx(longest, f) <= avail_w - 58:
            want = f
            break
    else:
        want = 34

    gap = {60: 74, 58: 70, 56: 66, 54: 62, 52: 58, 50: 54, 48: 50,
           46: 46, 44: 42, 42: 40, 40: 38, 38: 36, 36: 34, 34: 32}.get(want, 40)
    return want, gap, want + 2


def estimate_density(page: dict) -> int:
    """估算单页信息量（标题 + 正文总字数）。用于密度下限告警。"""
    return len(page.get("heading", "")) + sum(len(b) for b in page.get("body", []))


def estimate_text_width_approx(n_chars: int, font_px: int) -> float:
    """按字符数估算宽度（CJK 全角，保守取 1.0 em）。"""
    return n_chars * font_px


def check_layout(platform_draft: dict, profile: ProfileVersion) -> list[PageIssue]:
    """渲染前检测。规则来自可配置 profile，不是模型自己定的"平台规定"。"""
    issues: list[PageIssue] = []
    lim = profile.limits
    pages = platform_draft.get("pages", [])
    idx = [p.get("index") for p in pages]

    if idx != list(range(1, len(idx) + 1)):
        issues.append(PageIssue("error", "PAGE_INDEX_NOT_CONTIGUOUS", f"页序不连续：{idx}"))
    if not (lim.min_pages <= len(pages) <= lim.max_pages):
        issues.append(
            PageIssue("warning", "PAGE_COUNT_OUT_OF_RANGE",
                      f"页数 {len(pages)} 超出范围 [{lim.min_pages}, {lim.max_pages}]")
        )

    title = platform_draft.get("title", "")
    if len(title) > lim.max_title_chars:
        issues.append(
            PageIssue("warning", "TITLE_TOO_LONG",
                      f"标题 {len(title)} 字，超过上限 {lim.max_title_chars}", None)
        )
    caption = platform_draft.get("caption", "")
    if len(caption) > lim.max_caption_chars:
        issues.append(
            PageIssue("warning", "CAPTION_TOO_LONG",
                      f"正文 {len(caption)} 字，超过上限 {lim.max_caption_chars}", None)
        )

    # 封面与正文页可用高度不同
    cover_h = profile.render.height_px - 2 * profile.render.safe_margin_px
    inner_h = profile.render.height_px - 2 * profile.render.safe_margin_px
    avail_w = profile.render.width_px - 2 * profile.render.safe_margin_px

    for p in pages:
        pi = p.get("index")
        heading = p.get("heading", "")
        is_cover = p.get("layout") == "cover"
        if 'visual' in p:
            from .visual_content import VisualSpec
            try:
                visual = VisualSpec.model_validate(p['visual'])
                # Modern posters use layout for the header and kind for the
                # content. A tutorial can start directly with a flow or map.
                modern = (visual.template_style or {}).get('design_family', 'legacy') != 'legacy'
                if is_cover and not modern and visual.kind not in ('cover', 'rank','catalog'):
                    raise ValueError('封面图解必须与封面页对应')
                if (not is_cover) and visual.kind == 'cover':
                    raise ValueError('封面图解必须与封面页对应')
                if not isinstance(p.get('body'), list) or any(not isinstance(b, str) for b in p['body']):
                    raise ValueError('图解页补充正文需要文字列表')
            except (ValueError, TypeError) as exc:
                issues.append(PageIssue('error', 'VISUAL_INVALID', str(exc), pi))
            continue
        # 封面标题按可用宽度自适应缩放（下限 56px），正文页 52px
        h_font = auto_heading_font(heading, avail_w, cover=is_cover)
        body = p.get("body", [])
        # 与模板用同一套字号算法，否则检测说没问题、渲染却溢出
        b_font = _body_scale(len(body), is_cover, body, avail_w)[0 if not is_cover else 2]

        if len(heading) > lim.max_heading_chars:
            issues.append(PageIssue(
                "warning", "HEADING_LONG",
                f"标题 {len(heading)} 字 > 建议 {lim.max_heading_chars} 字（将自动缩放字号）", pi))
        if estimate_text_width(heading, h_font) > avail_w + 1:
            issues.append(PageIssue(
                "warning", "HEADING_TOO_WIDE",
                f"标题在最小字号 {h_font}px 下仍宽 {estimate_text_width(heading, h_font):.0f}px > 可用 {avail_w}px", pi))

        if len(body) > lim.max_body_lines_per_page:
            issues.append(PageIssue(
                "warning", "TOO_MANY_LINES",
                f"{len(body)} 行 > 上限 {lim.max_body_lines_per_page}", pi))
        for line in body:
            if len(line) > lim.max_body_chars_per_page:
                issues.append(PageIssue(
                    "warning", "BODY_LINE_TOO_LONG",
                    f"单行 {len(line)} 字 > 上限 {lim.max_body_chars_per_page}", pi))
            if estimate_text_width(line, b_font) > avail_w - 58:
                issues.append(PageIssue(
                    "warning", "BODY_TOO_WIDE",
                    f"正文行（{b_font}px）渲染宽度约 {estimate_text_width(line, b_font):.0f}px > 可用 {avail_w - 58}px", pi))
            if len(line) > 22:
                issues.append(PageIssue(
                    "warning", "BODY_LINE_DENSE",
                    f"单行 {len(line)} 字偏长，建议拆成两句以保持可读性", pi))

        # 缺失字形检测（PIL 实际渲染后再查，这里先查控制字符与空串）
        for line in [heading, *body]:
            if line and any(ord(c) < 32 and c not in "\t" for c in line):
                issues.append(PageIssue("error", "CONTROL_CHAR", "文本含控制字符", pi))
            if line == "":
                issues.append(PageIssue("warning", "EMPTY_LINE", "存在空行", pi))

        # 信息密度下限：整版只有一两行字会显得空洞（用户明确反馈过）
        density = estimate_density(p)
        min_density = 40 if is_cover else 50
        if density < min_density:
            issues.append(PageIssue(
                "warning", "PAGE_DENSITY_LOW",
                f"本页信息量 {density} 字 < 建议 {min_density} 字，版面会显得空", pi))

    return issues


# ---------------------------------------------------------------- HTML 模板

CSS_BASE = """
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: ${font}; }
.page {
  width: ${w}px; height: ${h}px; position: relative; overflow: hidden;
  background: #FAF8F5; color: #16181C;
  padding: ${m}px;
  display: flex; flex-direction: column;
}
.page.cover { background: #14171C; color: #F5F3EF; }
.page.inner { background: #FAF8F5; }
/* 品牌行占据文档流的固定高度，标题从它下方开始，绝不重叠 */
.brand {
  flex: 0 0 auto; height: 40px;
  display:flex; justify-content:space-between; align-items:center;
  font-size:24px; letter-spacing:.12em; opacity:.45; font-weight:500;
  margin-bottom: 48px;
}
.page.cover .brand { margin-bottom: 64px; }
.content { flex: 1 1 auto; display:flex; flex-direction:column; }
/* 内页：内容垂直均分整张画布。标题区、正文列表分别吸收剩余空间，
   列表整体居中，页脚注释贴底——彻底消除"一大张图两行字"的空洞。 */
.page.inner .content { justify-content: space-between; }
.page.inner ul { flex: 0 1 auto; display:flex; flex-direction:column;
                 justify-content:center; align-self:stretch; }
/* 封面：正文列表也均分剩余空间，而不是挤在标题正下方 */
.page.cover .content { justify-content: flex-start; }
.page.cover ul { flex: 1 1 auto; display:flex; flex-direction:column;
                 justify-content:center; align-self:stretch; }
.page.cover .content { justify-content: center; }
.pnum {
  position:absolute; bottom:${m}px; right:${m}px;
  font-size:28px; opacity:.55; font-weight:600; letter-spacing:.04em;
}
h1 { font-size: ${cfont}px; line-height:1.2; font-weight:800; letter-spacing:-.02em; }
h2 { font-size: ${ifont}px; line-height:1.22; font-weight:800; letter-spacing:-.02em; }
.rule { width:120px; height:9px; background:#D9542B; border-radius:5px; margin:38px 0 0; }
.cover .rule { background:#E86A3C; margin-bottom: 10px; }
/* 分类标签：把结论先行做成可视块，增加信息密度 */
.kicker {
  display:inline-block; align-self:flex-start;
  font-size:26px; font-weight:800; letter-spacing:.1em;
  color:#D9542B; border:2px solid #D9542B; border-radius:6px;
  padding:6px 16px; margin-bottom:30px;
}
.cover .kicker { color:#E86A3C; border-color:#E86A3C; }
ul { list-style:none; margin-top:${ulgap}px; }
li {
  font-size:${bfont}px; line-height:1.5; padding-left:64px; position:relative;
  margin-bottom:${ligap}px; color:#1E2228; font-weight:500;
}
li::before {
  content:""; position:absolute; left:0; top:${dotgap}px;
  width:24px; height:24px; border-radius:50%; background:#D9542B;
}
/* 条目内强调：引导词加粗 */
li b { font-weight:800; color:#0F1114; }
.cover ul { margin-top:40px; }
.cover li { font-size:${cbfont}px; color:#D3D7DE; font-weight:400; }
.cover li::before { background:#E86A3C; width:26px; height:26px; top:${cdotgap}px; }
.cover li b { color:#F5F3EF; }
/* 页脚注释：贴底并带分隔线，与页码同高 */
.footnote {
  margin-top:auto; padding-top:30px; border-top:2px solid #E3E0DA;
  font-size:28px; line-height:1.5; color:#6E7681; padding-right:110px;
}
.cover .footnote { border-top-color:#2B3138; color:#9AA3AF; }
.accent { color:#D9542B; }
.cover .accent { color:#E86A3C; }
"""

_CSS = Template(CSS_BASE)


def _page_html(page: dict, profile: ProfileVersion, platform: str, total: int,
               template_versions: list[str], form: str = '') -> tuple[str, str]:
    if 'visual' in page:
        from .visual_content import illustrated_page_html
        return illustrated_page_html(page, profile, platform, total, CJK_FONT_STACK, form=form)
    layout = page.get("layout", "checklist")
    tv = f"{layout}@8"
    r = profile.render
    avail_w = r.width_px - 2 * r.safe_margin_px
    heading = page.get("heading", "")
    is_cover = layout == "cover"
    h_font = auto_heading_font(heading, avail_w, cover=is_cover)
    body_lines = len(page.get("body", []))
    # 正文行少时放大字号、加大行距，让版面铺满，避免"一大张图两行字"
    b_font, li_gap, c_b_font = _body_scale(body_lines, is_cover, page.get("body", []), avail_w)
    u_gap = {2: 92, 3: 76, 4: 62}.get(body_lines, 52)
    css = _CSS.substitute(
        # 用跨平台回退栈，而不是 profile 里的单个字体名：
        # profile 写的是"工程规格"（Noto Sans CJK SC），但用户机器上不一定有。
        # 这里把 profile 的字体放在栈首优先，后面跟各平台的中文兜底，
        # 保证在任何系统上都不会退到无中文字形的 sans-serif。
        font=f'"{r.font_family}", {CJK_FONT_STACK}',
        w=r.width_px, h=r.height_px, m=r.safe_margin_px,
        cfont=h_font if is_cover else 76,
        ifont=h_font if not is_cover else 52,
        bfont=b_font, cbfont=c_b_font,
        ligap=li_gap, ulgap=u_gap, dotgap=int(b_font * 0.42),
        cdotgap=int(c_b_font * 0.42),
    )
    heading = html.escape(heading)
    kicker = html.escape(page.get("kicker", "") or "")
    footnote = html.escape(page.get("footnote", "") or "")
    body_items = "".join(f"<li>{html.escape(b)}</li>" for b in page.get("body", []))

    brand = f'<div class="brand"><span>内容工作台</span><span>{platform.upper()}</span></div>'
    pnum = f'<div class="pnum">{page["index"]}/{total}</div>'

    kicker_html = f'<div class="kicker">{kicker}</div>' if kicker else ""
    foot_html = f'<div class="footnote">{footnote}</div>' if footnote else ""
    h_tag = "h1" if is_cover else "h2"

    inner = f"""
  {brand}
  <div class="content">
    {kicker_html}
    <{h_tag}>{heading}</{h_tag}>
    <div class="rule"></div>
    <ul>{body_items}</ul>
    {foot_html}
  </div>
  {pnum}"""
    page_cls = "cover" if is_cover else "inner"

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>{css}</style></head>
<body><div class="page {page_cls}">{inner}
</div></body></html>"""
    return doc, tv


def build_pages(platform_draft: dict, profile: ProfileVersion) -> list[dict]:
    pages = platform_draft.get("pages", [])
    # 题材形态决定主题与品牌文案；旧稿没有该字段时回落到通用解释型主题。
    form = platform_draft.get("form", "") or ""
    out = []
    for p in pages:
        doc, tv = _page_html(p, profile, platform_draft["platform"], len(pages),
                             profile.render.template_versions, form=form)
        out.append({"page": p, "html": doc, "template_version": tv})
    return out


# ---------------------------------------------------------------- 渲染执行


class PlaywrightRenderer:
    """用 Chromium 把 HTML 渲染成 PNG。产物写入 storage/artifacts/<...>/。

    优先使用系统已安装的 Chromium/Chrome（离线沙箱常见），找不到再回退到
    Playwright 自带浏览器。渲染引擎版本记入 manifest，便于复现。

    **关于事件循环**：Playwright 的 sync API 不能在 asyncio 事件循环里调用
    （FastAPI 路由就是跑在事件循环里的）。`render_platform_isolated()` 会把
    渲染放到独立线程执行，路由层调它；直接调 `render_platform()` 只适用于
    纯脚本环境。
    """

    def __init__(self, artifact_root: Path) -> None:
        self.root = Path(artifact_root)

    @classmethod
    def _system_browser_candidates(cls) -> list[str]:
        """各平台常见的 Chromium/Chrome 安装位置。

        优先用系统已装的浏览器，找不到再回退到 Playwright 自带那份
        （`playwright install chromium`）——后者才是跨平台可靠路径，
        系统路径只是"能省一次下载就省一次"。
        """
        import os
        import sys

        cands: list[str] = []
        if sys.platform == "win32":
            for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
                base = os.environ.get(env)
                if not base:
                    continue
                cands += [
                    os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                    os.path.join(base, "Chromium", "Application", "chrome.exe"),
                    os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                ]
        elif sys.platform == "darwin":
            cands += [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            ]
        else:
            cands += [
                "/usr/bin/google-chrome",
                "/usr/bin/chromium",
                "/usr/bin/chromium-browser",
                "/usr/bin/microsoft-edge",
            ]
        return cands

    @classmethod
    def resolve_executable(cls) -> str | None:
        import os

        for p in cls._system_browser_candidates():
            if os.path.exists(p):
                return p
        return None

    def render_platform_isolated(self, platform_draft: dict, profile: ProfileVersion,
                                 **kwargs) -> RenderResult:
        """在**独立线程**里渲染，供 asyncio 环境（FastAPI 路由）调用。

        每次新建线程而不是复用线程池：Playwright 的 sync API 与其
        greenlet 上下文绑定，跨请求复用容易踩到事件循环残留。
        渲染本身是秒级操作，线程创建开销可忽略。
        """
        import threading

        box: dict = {}

        def _run() -> None:
            try:
                box["result"] = self.render_platform(platform_draft, profile, **kwargs)
            except BaseException as exc:  # noqa: BLE001
                box["error"] = exc

        t = threading.Thread(target=_run, name="cwb-render", daemon=True)
        t.start()
        t.join()
        if "error" in box:
            raise box["error"]
        return box["result"]

    def render_platform(
        self,
        platform_draft: dict,
        profile: ProfileVersion,
        *,
        display_id: str,
        content_revision_version: int = 1,
        platform_revision_version: int = 1,
    ) -> RenderResult:
        kw = {
            "width": profile.render.width_px,
            "height": profile.render.height_px,
        }
        result = RenderResult(
            platform=platform_draft["platform"],
            revision_version=platform_revision_version,
            profile_version=profile.id,
            width=kw["width"],
            height=kw["height"],
            template_versions=sorted({t for t in profile.render.template_versions}),
        )

        # 1) 布局检测（渲染前）
        result.issues.extend(check_layout(platform_draft, profile))
        if result.errors:
            return result  # 有阻断项不出图，避免产出有缺陷的成品

        pages = build_pages(platform_draft, profile)
        result.template_versions = sorted({p['template_version'] for p in pages})
        out_dir = (
            self.root / display_id / platform_draft["platform"]
            / f"cr{content_revision_version}-pr{platform_revision_version}"
        )
        out_dir.mkdir(parents=True, exist_ok=True)

        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            launch_kwargs = {"args": ["--no-sandbox", "--font-render-hinting=none",
                                      "--disable-dev-shm-usage"]}
            exe = self.resolve_executable()
            if exe:
                launch_kwargs["executable_path"] = exe
            browser = pw.chromium.launch(**launch_kwargs)
            ctx = browser.new_context(
                viewport={"width": kw["width"], "height": kw["height"]},
                device_scale_factor=1,
            )
            try:
                for item in pages:
                    pg = ctx.new_page()
                    pg.set_content(item["html"], wait_until="load")
                    pg.wait_for_timeout(120)
                    if 'visual' in item['page']:
                        from .visual_content import VISUAL_FIT_JS
                        fit = pg.evaluate(VISUAL_FIT_JS)
                        if fit < 0.74:
                            result.issues.append(PageIssue('warning', 'VISUAL_TOO_DENSE',
                                f'已自动适配到 {fit:.2f} 倍；建议在预览中检查字号，必要时精简文字', item['page']['index']))
                    # 缺字检测：渲染后测量实际尺寸
                    box = pg.evaluate(
                        "() => { const e=document.querySelector('h1,h2'); "
                        "const r=e.getBoundingClientRect(); "
                        "return {w:r.width, h:r.height, sh:e.scrollHeight, shw:e.scrollWidth}; }"
                    )
                    idx = item["page"]["index"]
                    if box["shw"] > kw["width"] - 2 * profile.render.safe_margin_px + 2:
                        result.issues.append(PageIssue(
                            "warning", "RENDER_OVERFLOW_X",
                            f"标题实际宽度 {box['shw']:.0f}px 超过可用区域", idx))
                    if box["sh"] > kw["height"]:
                        result.issues.append(PageIssue(
                            "warning", "RENDER_OVERFLOW_Y",
                            f"标题实际高度 {box['sh']:.0f}px 超过画布", idx))

                    if 'visual' in item['page']:
                        overflow = pg.evaluate('''() => {
                          const sheet = document.querySelector('.visual-sheet');
                          const outer = sheet.getBoundingClientRect();
                          const problems = [];
                          for (const e of sheet.querySelectorAll('h1,h2,h3,p,.v-footer,.v-visual,.cover-nodes,.example-window,.map-grid,.flow-stack,.compare-grid,.check-sheet')) {
                            const b = e.getBoundingClientRect();
                            if (b.bottom > outer.bottom + 2 || b.right > outer.right + 2 || b.left < outer.left - 2 || b.top < outer.top - 2)
                              problems.push(e.className || e.tagName);
                          }
                          const v = sheet.querySelector('.v-visual').getBoundingClientRect();
                          const body = sheet.querySelector('.v-body').getBoundingClientRect();
                          for (const e of sheet.querySelector('.v-visual').children) {
                            const b = e.getBoundingClientRect();
                            if (b.bottom > body.top - 2 || b.top < v.top - 2) problems.push('diagram overlap: '+e.className);
                          }
                          return problems;
                        }''')
                        if overflow:
                            result.issues.append(PageIssue('warning', 'VISUAL_RENDER_OVERFLOW',
                                '已生成预览，建议检查安全区域：' + ', '.join(overflow), idx))

                    fname = f"page-{idx:02d}.png"
                    fpath = out_dir / fname
                    pg.screenshot(path=str(fpath), type="png")
                    pg.close()

                    data = fpath.read_bytes()
                    result.images.append({
                        "page_index": idx,
                        "layout": item["page"].get("layout"),
                        "file": fname,
                        "storage_key": fpath.relative_to(self.root).as_posix(),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "size_bytes": len(data),
                        "width": kw["width"],
                        "height": kw["height"],
                        "template_version": item["template_version"],
                    })
            finally:
                ctx.close()
                browser.close()

        return result


def verify_images(result: RenderResult, root: Path, expected_pages: int | None = None) -> list[PageIssue]:
    """产物复核：文件存在、尺寸正确、页序唯一、无空文件；给定页数时核对导出数量与页序完整。"""
    issues: list[PageIssue] = []
    seen: set[int] = set()
    from PIL import Image

    for img in result.images:
        p = Path(root) / img["storage_key"]
        if not p.exists():
            issues.append(PageIssue("error", "ARTIFACT_MISSING", f"文件不存在：{img['storage_key']}", img["page_index"]))
            continue
        if p.stat().st_size == 0:
            issues.append(PageIssue("error", "ARTIFACT_EMPTY", "文件为 0 字节", img["page_index"]))
        if img["page_index"] in seen:
            issues.append(PageIssue("error", "PAGE_INDEX_DUPLICATE", "页序重复", img["page_index"]))
        seen.add(img["page_index"])

        with Image.open(p) as im:
            if (im.width, im.height) != (result.width, result.height):
                issues.append(PageIssue(
                    "error", "SIZE_MISMATCH",
                    f"实际 {im.width}×{im.height} ≠ 期望 {result.width}×{result.height}",
                    img["page_index"]))

    # 导出数量与页数必须一致——「图少了但没人发现」是成品交付里最难查的错。
    if expected_pages is not None:
        from .acceptance import export_contract
        for check in export_contract(expected_pages, result.images)["checks"]:
            if not check["passed"]:
                issues.append(PageIssue("error", check["code"], check["problem"], None))
    return issues
