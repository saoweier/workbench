"""Typed editorial diagrams. Models supply meaning; local code owns drawing.

No remote images, model HTML, SVG, file names or executable styling are accepted.
The illustrated template is versioned independently from the legacy text cards.
"""
from __future__ import annotations

import html
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .catalog_compiler import IconId

ILLUSTRATED_TEMPLATE_VERSION = 4

VISUAL_FIT_JS = """() => {
  const area = document.querySelector('.v-visual');
  const diagram = area.querySelector('.diagram-content');
  if (diagram.offsetHeight > area.clientHeight) diagram.classList.add('compact');
  const layout = diagram.querySelector('.style-layout:not(.style-preserved)');
  if (layout && (area.clientHeight - 8) / diagram.offsetHeight < 0.74) {
    // Reflow at full width before scaling: a long answer must not turn an
    // entire poster into a tiny two-column thumbnail. Preserve every word.
    layout.classList.add('style-dense', 'style-adaptive');
    document.querySelector('.visual-sheet').classList.add('adaptive-poster');
    for (let size = 26; size >= 22; size -= 1) {
      layout.style.setProperty('--adaptive-font', size + 'px');
      if (diagram.offsetHeight <= area.clientHeight - 8) break;
    }
  }
  const scale = Math.min(1, Math.max(0.01, (area.clientHeight - 8) / diagram.offsetHeight));
  diagram.style.setProperty('--diagram-scale', String(scale));
  return scale;
}"""



class VisualItem(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    label: str = Field(min_length=1)
    rank: int | None = Field(default=None,ge=1)
    detail: str = Field(min_length=1)
    icon: IconId
    category: str = ''
    tags: list[str] = Field(default_factory=list)
    metric_text: str | None = None
    metric_label: str | None = None
    metric_source_id: str | None = None
    photo_id: Literal['apple', 'pear', 'orange', 'kiwi', 'grape', 'persimmon'] | None = None
    media_id: str | None = Field(default=None,pattern=r'^media-[0-9a-f]{32}$')


class VisualSpec(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['cover', 'map', 'flow', 'compare', 'example', 'checklist', 'rank', 'photo', 'nutrition', 'catalog']
    title: str = Field(min_length=1)
    # Quantity and editorial density belong to the user's brief and Skill.
    items: list[VisualItem] = Field(min_length=1)
    presentation: Literal['illustrated','rank_cards','category_table','editorial','friendly_guide'] = 'illustrated'
    presentation_version: int = Field(default=4,ge=1,le=99)
    template_style: dict | None = None
    template_package_id: str | None = Field(default=None,pattern=r'^[a-z][a-z0-9_]{1,31}$')
    template_package_version: str | None = Field(default=None,pattern=r'^[0-9a-f]{16}$')
    takeaway: str = Field(min_length=1)

    @model_validator(mode='after')
    def valid_shape(self):
        if self.template_style is not None:
            from .template_packages import TemplateStyle
            self.template_style=TemplateStyle.model_validate(self.template_style).model_dump(mode='json')
        if any(i.media_id for i in self.items):
            if self.kind not in {'cover','photo'} or len(self.items)>2 or not all(i.media_id and not i.photo_id for i in self.items):
                raise ValueError('素材配图仅用于封面/photo，一页1～2张，不能混入路径或不同素材类型')
            from .content_media import ContentMedia
            for i in self.items:ContentMedia().get(i.media_id)
        if (self.kind in {'photo', 'nutrition'} and not any(i.media_id for i in self.items)) or any(i.photo_id for i in self.items):
            if self.kind not in {'cover', 'photo', 'nutrition'} or not all(i.photo_id for i in self.items):
                raise ValueError('水果图片只能用于完整配置的封面、photo、nutrition版面')
            if len({i.photo_id for i in self.items}) != len(self.items):
                raise ValueError('一页不能重复同一种水果图片')
            # 提示词声明「每页 nutrition 最多三种」，这里必须由程序真正拦下，
            # 否则四种水果挤一页仍会被放行（要求只写在提示词里等于没写）。
            if self.kind == 'nutrition' and len(self.items) > 3:
                raise ValueError('营养表每页最多三种水果，更多品种请分页，不能挤在一页')
            from .fruit_content import fruit_index
            fruits = fruit_index()
            for i in self.items:
                name = fruits[i.photo_id]['name'].replace('绿肉', '')
                if name not in i.label:
                    raise ValueError('图片品种与文字标签不一致')
        if any(i.metric_text and (not i.metric_label or not i.metric_source_id) for i in self.items):
            raise ValueError('数据必须注明口径和来源')
        for text in [self.title, self.takeaway, *[t for i in self.items for t in (i.label, i.detail,i.category,*i.tags,i.metric_text or '',i.metric_label or '',i.metric_source_id or '')]]:
            if any(ord(c) < 32 for c in text):
                raise ValueError('图解文本不能含控制字符')
        return self


VISUAL_SCHEMA = VisualSpec.model_json_schema()

# A deliberately small local icon vocabulary. These are drawings, not model code.
_ICONS = {
    'question': '<circle cx="24" cy="24" r="18"/><path d="M18 18c0-8 13-8 13 0 0 5-7 5-7 10m0 7v1"/>',
    'idea': '<path d="M16 31c-13-12-4-27 8-27s21 15 8 27l-2 6H18zM19 43h10M24 10v4"/>',
    'source': '<path d="M7 8h13c4 0 4 3 4 3s0-3 4-3h13v31H28c-4 0-4 3-4 3s0-3-4-3H7zM24 11v30M12 17h7m10 0h7M12 24h7m10 0h7"/>',
    'page': '<path d="M12 5h19l7 8v30H12zM31 5v9h7M18 22h14M18 29h14M18 36h8"/>',
    'check': '<rect x="6" y="6" width="36" height="36" rx="10"/><path d="m14 24 7 7 14-15"/>',
    'search':'<circle cx="20" cy="20" r="13"/><path d="m30 30 12 12"/>',
    'brain':'<path d="M24 9c-10-10-22 2-14 12-13 9-2 24 10 17V10m4-1c10-10 22 2 14 12 13 9 2 24-10 17V10M12 24l7 4m17-4-7 4"/>',
    'code':'<path d="m16 10-12 14 12 14m16-28 12 14-12 14M28 6l-8 36"/>',
    'globe':'<circle cx="24" cy="24" r="19"/><ellipse cx="24" cy="24" rx="8" ry="19"/><path d="M5 24h38M8 13h32M8 35h32"/>',
    'chart':'<path d="M7 5v37h37M14 32V22h7v10m5 0V13h7v19m5 0V7h6v25"/>',
    'briefcase':'<rect x="5" y="14" width="38" height="27" rx="5"/><path d="M16 14V7h16v7M5 24l19 5 19-5M24 24v9"/>',
    'game':'<path d="M12 14h24c9 0 16 29 7 28-4 0-7-9-11-9H16c-4 0-7 9-11 9-9 0-2-28 7-28zM12 21v9M8 25h9M32 23h1m6 6h1"/>',
    'fruit':'<path d="M24 16C0 4 1 42 17 43l7-3 7 3C47 42 48 4 24 16zM24 16V7m0 1c7-9 15-6 12 0-3 5-10 5-12 0"/>',
    'news':'<rect x="7" y="7" width="34" height="35" rx="3"/><path d="M13 13h22M13 21h10v10H13zM28 21h7m-7 7h7M13 36h22"/>',
    'palette':'<path d="M23 5C-3 5-1 41 20 43c9 1 11-3 7-9-3-5 4-7 9-5C54 35 49 4 23 5zM12 20h1m8-8h1m12 3h1m5 8h1"/>',
    'pencil': '<path d="m8 32 23-23 8 8-23 23-11 3zM26 14l8 8M8 32l8 8"/>',
}


def icon_svg(name: str, size: int = 48) -> str:
    drawing = _ICONS.get(name, _ICONS['page'])
    return f'<svg width="{size}" height="{size}" viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{drawing}</svg>'


def _e(value: str) -> str:
    return html.escape(value, quote=True)


def _heading(value: str) -> str:
    # Keep natural clauses and short Chinese terms together at line breaks.
    parts = re.split(r'(?<=[：，、])', value)
    return ''.join(f'<span class="heading-unit">{_e(p)}</span>' for p in parts if p)


def _studio_illustration() -> str:
    """Editorial desk: pencil, source book, page drafts, connector and review lens."""
    return '''<svg class="studio-art" viewBox="0 0 900 420" fill="none" aria-label="资料、草稿、画笔与审稿放大镜组成的创作桌面插画" role="img">
      <ellipse cx="458" cy="372" rx="322" ry="22" fill="#d8ded8"/>
      <path d="M111 217c-46-108 111-210 217-139C412-9 609 17 650 113c149-26 245 130 151 215H140z" fill="#dfece6"/>
      <path d="M71 273c65-53 77 22 134 0M712 77c45 20 29 48 72 55" stroke="#e38454" stroke-width="6" stroke-linecap="round"/>
      <g transform="rotate(-12 234 227)"><rect x="126" y="119" width="197" height="239" rx="16" fill="#559a85"/>
      <rect x="112" y="107" width="197" height="239" rx="16" fill="#fffdf4" stroke="#173e35" stroke-width="5"/>
      <rect x="136" y="133" width="52" height="49" rx="9" fill="#f6c86b"/>
      <path d="M151 155h23m-23 9h14M136 213h145M136 237h112M136 261h145M136 285h93" stroke="#559a85" stroke-width="9" stroke-linecap="round"/></g>
      <g transform="rotate(8 470 204)"><rect x="351" y="61" width="252" height="298" rx="19" fill="#c9d0dd"/>
      <rect x="336" y="46" width="252" height="298" rx="19" fill="#fffdf8" stroke="#19382f" stroke-width="5"/>
      <path d="M367 84h143" stroke="#19382f" stroke-width="13" stroke-linecap="round"/>
      <rect x="366" y="117" width="193" height="94" rx="13" fill="#8bb8df"/>
      <path d="m382 193 45-41 31 23 43-40 44 58" fill="#c7e4d7"/>
      <circle cx="527" cy="144" r="12" fill="#f7d078"/>
      <path d="M402 244h142M402 277h113M373 237l9 9 14-17M373 270l9 9 14-17" stroke="#428771" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/></g>
      <g transform="rotate(13 688 282)"><rect x="629" y="186" width="161" height="153" rx="11" fill="#edb956"/>
      <path d="m650 216 10 10 18-20M693 221h66M650 260h109M650 288h81" stroke="#735525" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/></g>
      <path d="m622 70 74 226-18 32-33-18-74-226z" fill="#ee8655" stroke="#713f2c" stroke-width="4"/>
      <path d="m645 310 33 18-28 14z" fill="#f7e3bd"/><path d="m650 342 5-17 12 6z" fill="#19382f"/>
      <circle cx="745" cy="108" r="49" fill="#fffdf8" fill-opacity=".68" stroke="#19382f" stroke-width="12"/>
      <path d="m779 144 51 50" stroke="#19382f" stroke-width="19" stroke-linecap="round"/>
      <path d="m722 106 15 16 30-31" stroke="#428771" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="m103 77 5-19 5 19 19 5-19 5-5 19-5-19-19-5z" fill="#ee8655"/>
      <circle cx="836" cy="278" r="9" fill="#428771"/><circle cx="299" cy="58" r="8" fill="#edb956"/>
    </svg>'''


def _diagram(spec: VisualSpec) -> str:
    items = spec.items
    kind = spec.kind
    if kind=='catalog' or (kind=='rank' and spec.presentation in {'rank_cards','editorial'}):
        from .editorial_templates import render_rows
        return render_rows(spec,icon_svg)
    if any(i.media_id for i in items):
        from .content_media import ContentMedia
        media=ContentMedia()
        cards=''.join(f'<article class="media-card"><img src="{media.uri(i.media_id)}" alt="{_e(i.label)}"><h3>{_e(i.label)}</h3><p>{_e(i.detail)}</p></article>' for i in items)
        return f'<div class="media-grid media-count-{len(items)}">{cards}</div>'
    if kind in {'photo', 'nutrition'} or any(i.photo_id for i in items):
        return _fruit_diagram(spec)
    if kind == 'cover':
        if spec.template_package_id:
            tiles=''.join(f'<article class="pack-cover-row tone-{n%4}"><span>{icon_svg(i.icon,36)}</span><div><h3>{_e(i.label)}</h3><p>{_e(i.detail)}</p></div></article>' for n,i in enumerate(items))
            return f'<div class="diagram-label">{_e(spec.title)}</div><section class="pack-cover">{tiles}</section>'
        tiles = ''.join(f'<div class="cover-node tone-{n}"><span class="mini-icon">{icon_svg(i.icon, 32)}</span><b>{_e(i.label)}</b></div>' for n, i in enumerate(items))
        return f'{_studio_illustration()}<div class="cover-nodes">{tiles}</div><div class="cover-note">{_e(spec.title)}</div>'
    if kind == 'compare':
        from .poster_templates import scene
        cols = ''.join(f'<article class="compare-side tone-{n%4}"><div class="compare-icon">{scene(i.icon) if (spec.template_style or {}).get("decoration")=="rich" else icon_svg(i.icon,74)}</div><span class="diagram-number">{chr(65+n) if n<26 else n+1}</span><h3>{_e(i.label)}</h3><p>{_e(i.detail)}</p></article>' for n, i in enumerate(items))
        return f'<div class="diagram-label">{_e(spec.title)}</div><div class="compare-grid">{cols}<span class="compare-vs">→</span></div>'
    if kind == 'flow':
        steps = ''.join(f'<article class="flow-step tone-{n}"><span class="step-number">0{n+1}</span><span class="item-icon">{icon_svg(i.icon)}</span><div><h3>{_e(i.label)}</h3><p>{_e(i.detail)}</p></div></article>' + ('<div class="flow-link">↓</div>' if n < len(items)-1 else '') for n, i in enumerate(items))
        return f'<div class="diagram-label">{_e(spec.title)}</div><div class="flow-stack">{steps}</div>'
    if kind == 'example':
        rows = ''.join(f'<div class="example-row"><span class="field-label">{icon_svg(i.icon, 30)}{_e(i.label)}</span><p>{_e(i.detail)}</p></div>' for i in items)
        return f'<div class="example-window"><div class="window-bar"><i></i><i></i><i></i><span>{_e(spec.title)}</span></div><div class="example-tag">填写示例 · 非真实记录</div>{rows}<div class="example-tail">把抽象建议，落到一个可以检查的例子里。</div></div>'
    if kind == 'checklist':
        rows = ''.join(f'<article class="check-row"><span class="check-box">{icon_svg("check", 42)}</span><div><h3>{_e(i.label)}</h3><p>{_e(i.detail)}</p></div><span class="check-index">0{n+1}</span></article>' for n, i in enumerate(items))
        return f'<div class="diagram-label">{_e(spec.title)}</div><div class="check-sheet">{rows}</div>'
    if kind == 'rank':
        # 榜单版面：本地绘制名次徽标，模型只提供名称与一句理由。
        # 一页可容纳 3–12 条，正好覆盖 TOP10 这类「一页讲完」的排行榜。
        rows = ''.join(
            f'<article class="rank-row tone-{n % 4}"><span class="rank-badge">{i.rank or n + 1}</span>'
            f'<div class="rank-copy"><h3>{_e(i.label)}</h3><p>{_e(i.detail)}</p></div>'
            f'<span class="rank-icon">{icon_svg(i.icon, 34)}</span></article>'
            for n, i in enumerate(items))
        return f'<div class="diagram-label">{_e(spec.title)}</div><div class="rank-board">{rows}</div>'
    cards = ''.join(f'<article class="map-node tone-{n}"><span class="item-icon">{icon_svg(i.icon, 54)}</span><span class="map-index">0{n+1}</span><h3>{_e(i.label)}</h3><p>{_e(i.detail)}</p></article>' for n, i in enumerate(items))
    return f'<div class="map-heading"><span>STRUCTURE</span><b>{_e(spec.title)}</b></div><div class="map-grid">{cards}</div>'


CSS = '''
*{box-sizing:border-box;margin:0;padding:0}body{background:#fff}
.pack-cover{display:flex;flex-direction:column;gap:18px}.pack-cover-row{display:flex;gap:22px;align-items:center;padding:24px 28px;border-radius:20px}.pack-cover-row>span{flex:none}.pack-cover-row h3{font-size:30px;margin-bottom:10px}.pack-cover-row p{font-size:28px;line-height:1.5;color:#294a3a}.compact .pack-cover-row{padding:17px 24px}.compact .pack-cover-row p{font-size:26px}
.page{position:relative;overflow:hidden;background:#f7f4eb;color:#17382f}
.visual-sheet{position:absolute;width:936px;height:1296px;display:flex;flex-direction:column;gap:26px;transform-origin:top left}
.v-brand{display:flex;align-items:center;justify-content:space-between;font-size:23px;color:#668073;letter-spacing:2px;height:38px;flex:none}
.brand-mark{display:flex;align-items:center;gap:13px}.brand-mark i{width:24px;height:24px;border-radius:7px;background:#3d846d;transform:rotate(-12deg)}
.v-header{flex:none}.v-kicker{display:inline-flex;font-size:24px;color:#3b715f;padding:8px 17px;background:#e4ede4;border-radius:9px;margin-bottom:20px;font-weight:700;letter-spacing:1px}
h1,h2{font-size:64px;line-height:1.28;font-weight:800;letter-spacing:-1.7px;overflow-wrap:anywhere}h1{font-size:76px}
.heading-unit{display:inline-block;max-width:100%;overflow-wrap:anywhere}h1,h2{text-wrap:balance}
.v-cover h1{max-width:920px}.v-sub{font-size:31px;line-height:1.55;color:#5e7367;margin-top:16px;overflow-wrap:anywhere}
.v-visual{flex:1;min-height:0;display:flex;flex-direction:column;justify-content:center;gap:23px;position:relative}
.studio-art{width:100%;height:auto;max-height:420px;flex:none}.cover-nodes{display:flex;flex-wrap:wrap;justify-content:center;gap:15px}.cover-node{display:flex;gap:10px;align-items:center;border:2px solid rgba(23,56,47,.1);border-radius:17px;padding:18px 22px;font-size:27px}.mini-icon{display:flex}.cover-note{text-align:center;color:#587367;font-size:27px;margin-top:8px}
.tone-0{background:#e2ece4;color:#225c49}.tone-1{background:#fbe1d1;color:#8c4c2d}.tone-2{background:#dce8f5;color:#375d7e}.tone-3{background:#f5e8b9;color:#786022}
.diagram-label{font-size:27px;font-weight:700;color:#527061;display:flex;align-items:center;gap:13px}.diagram-label:before{content:'';width:28px;height:5px;background:#e38454;border-radius:3px}
h3{font-size:36px;line-height:1.3;font-weight:800;overflow-wrap:anywhere}p{font-size:29px;line-height:1.6;overflow-wrap:anywhere}
.item-icon{display:flex;align-items:center;justify-content:center;flex:none}.map-heading{display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #ccd9cd;padding-bottom:19px;font-size:29px}.map-heading span{font-size:23px;letter-spacing:3px;color:#658075}.map-grid{display:grid;grid-template-columns:1fr 1fr;gap:23px}.map-node{padding:30px;border-radius:23px;position:relative;min-height:244px}.map-node .item-icon{justify-content:flex-start;margin-bottom:24px}.map-index{position:absolute;right:30px;top:30px;font-size:40px;opacity:.2;font-weight:800}.map-node h3{margin-bottom:15px}.map-node p{font-size:27px}
.flow-stack{display:flex;flex-direction:column;gap:0}.flow-step{display:flex;align-items:center;gap:24px;padding:24px 28px;border-radius:20px;position:relative}.step-number{font-size:25px;font-weight:800;opacity:.55;align-self:flex-start;padding-top:8px}.flow-step .item-icon{width:64px}.flow-step h3{font-size:32px;margin-bottom:8px}.flow-step p{font-size:27px;line-height:1.45}.flow-link{height:34px;font-size:35px;text-align:center;color:#709082;line-height:30px}
.compare-grid{display:grid;grid-template-columns:1fr 1fr;gap:30px;position:relative}.compare-side{min-height:560px;padding:38px;border-radius:25px;position:relative;display:flex;flex-direction:column}.compare-icon{margin-bottom:65px}.diagram-number{position:absolute;right:38px;top:37px;font-size:62px;font-weight:800;opacity:.15}.compare-side h3{font-size:39px;margin-bottom:32px}.compare-side p{font-size:31px;line-height:1.7}.compare-vs{position:absolute;top:46%;left:50%;transform:translate(-50%,-50%);background:#fffaf1;border:3px solid #f7f4eb;border-radius:50%;width:60px;height:60px;text-align:center;font-size:36px;color:#638375}
.example-window{border:3px solid #193e32;background:#fffdf7;border-radius:24px;overflow:hidden;box-shadow:12px 14px 0 #d8e6db}.window-bar{display:flex;align-items:center;gap:10px;background:#e0eae1;padding:22px 26px;border-bottom:2px solid #b8cdbc}.window-bar i{width:13px;height:13px;background:#749889;border-radius:50%}.window-bar span{font-size:26px;font-weight:700;margin-left:auto}.example-tag{font-size:23px;color:#8c5734;background:#fbe6cd;display:inline-block;margin:26px 30px 8px;padding:8px 16px;border-radius:7px}.example-row{margin:0 30px;padding:24px 0;border-bottom:2px dashed #d8ded2;display:grid;grid-template-columns:210px 1fr;gap:24px}.field-label{display:flex;align-items:flex-start;gap:10px;font-size:26px;font-weight:800;padding-top:3px}.field-label svg{flex:none}.example-row p{font-size:28px;line-height:1.5}.example-tail{font-size:24px;padding:26px 30px;color:#709082}
.check-sheet{background:#fffdf7;border:2px solid #d6dfd2;border-radius:24px;padding:10px 30px}.check-row{display:flex;align-items:flex-start;gap:24px;padding:30px 0;border-bottom:2px dashed #d1dccc}.check-row:last-child{border:none}.check-box{display:flex;color:#40876e;padding-top:4px;flex:none}.check-row h3{font-size:33px;margin-bottom:10px}.check-row p{font-size:27px;line-height:1.5}.check-index{margin-left:auto;font-size:30px;opacity:.3;padding-top:4px;flex:none}
.v-body{display:flex;flex-direction:column;gap:10px;flex:none}.v-body p{font-size:29px;color:#587264;line-height:1.5;padding-left:20px;border-left:4px solid #dbab74}
.v-takeaway{flex:none;background:#193e32;color:#fffaf1;padding:23px 28px;border-radius:17px;display:flex;gap:19px;align-items:flex-start}.v-takeaway svg{flex:none;margin-top:3px}.v-takeaway p{font-size:30px;line-height:1.45;font-weight:650}
.v-footer{flex:none;display:flex;align-items:flex-end;justify-content:space-between;gap:25px;border-top:2px solid #ced8cb;padding-top:22px;font-size:22px;line-height:1.5;color:#728478}.v-footer span{max-width:800px}.v-footer b{font-size:25px;white-space:nowrap;color:#456d5b}
.v-visual{display:block}.diagram-content{position:absolute;left:50%;top:50%;width:936px;display:flex;flex-direction:column;gap:23px;transform-origin:center;transform:translate(-50%,-50%) scale(var(--diagram-scale,1))}
.diagram-content.compact{gap:16px}.compact .map-node{padding:24px;min-height:220px}.compact .map-node .item-icon{margin-bottom:15px}.compact .map-node h3{font-size:33px;margin-bottom:10px}.compact .map-node p{font-size:26px;line-height:1.45}.compact .map-grid{gap:18px}.compact .flow-step{padding:17px 24px;gap:18px}.compact .flow-step h3{font-size:30px;margin-bottom:6px}.compact .flow-step p{font-size:26px;line-height:1.45}.compact .flow-link{height:25px;font-size:27px;line-height:22px}.compact .check-row{padding:22px 0;gap:20px}.compact .check-row h3{font-size:31px;margin-bottom:7px}.compact .check-row p{font-size:26px;line-height:1.45}
.compact .example-row{margin:0 24px;padding:16px 0;grid-template-columns:170px 1fr;gap:18px}.compact .field-label{font-size:24px}.compact .example-row p{font-size:26px;line-height:1.45}.compact .window-bar{padding:16px 22px}.compact .window-bar span{font-size:24px}.compact .example-tag{font-size:22px;margin:18px 24px 4px;padding:6px 14px}.compact .example-tail{font-size:22px;padding:18px 24px}
'''
#: 榜单版面的紧凑行式排版。10 条名次要在一页内可读，因此单行密度高于 map/checklist。
RANK_CSS = '''
.rank-board{display:flex;flex-direction:column;gap:12px}
.rank-row{display:flex;align-items:center;gap:18px;padding:13px 20px;border-radius:15px}
.rank-badge{flex:none;width:52px;height:52px;border-radius:14px;background:#fff;opacity:.92;display:flex;align-items:center;justify-content:center;font-size:27px;font-weight:800}
.rank-copy{flex:1;min-width:0}
.rank-copy h3{overflow-wrap:anywhere;font-size:29px;margin-bottom:3px}
.rank-copy p{font-size:23px;line-height:1.35}
.rank-icon{flex:none;opacity:.5;display:flex}
.compact .rank-board{gap:8px}
.compact .rank-row{padding:9px 16px;gap:14px}
.compact .rank-badge{width:44px;height:44px;font-size:24px;border-radius:12px}
.compact .rank-copy h3{overflow-wrap:anywhere;font-size:27px}
.compact .rank-copy p{font-size:22px}
/* Long rankings use an editorial table: the title and explanation share each row. */
.v-ranking-dense{gap:18px}
.v-ranking-dense .v-header h1,.v-ranking-dense .v-header h2{font-size:56px;line-height:1.2}
.v-ranking-dense .v-kicker{font-size:22px;margin-bottom:12px;padding:6px 14px}
.v-ranking-dense .v-sub{font-size:26px;line-height:1.35;margin-top:10px}
.v-ranking-dense .diagram-content{gap:14px}
.v-ranking-dense .diagram-label{font-size:25px}
.v-ranking-dense .rank-board{gap:8px}
.v-ranking-dense .rank-row{padding:12px 16px;gap:16px;min-height:62px}
.v-ranking-dense .rank-badge{width:42px;height:42px;font-size:25px;border-radius:11px}
.v-ranking-dense .rank-copy{display:grid;grid-template-columns:180px minmax(0,1fr);gap:18px;align-items:center}
.v-ranking-dense .rank-copy h3{font-size:27px;line-height:1.25;margin:0}
.v-ranking-dense .rank-copy p{font-size:24px;line-height:1.35}
.v-ranking-dense .rank-icon{display:none}
.v-ranking-dense .v-body{gap:6px}
.v-ranking-dense .v-body p{font-size:25px;line-height:1.35}
.v-ranking-dense .v-takeaway{padding:16px 22px;gap:14px}
.v-ranking-dense .v-takeaway p{font-size:26px;line-height:1.35}
.v-ranking-dense .v-footer{padding-top:14px;font-size:20px}
.v-ranking-dense .compact .rank-row{padding:8px 16px}
'''


def _fruit_diagram(spec: VisualSpec) -> str:
    from .fruit_content import fruit_index, photo_svg
    fruits = fruit_index()
    if spec.kind == 'nutrition':
        rows = ''
        for i in spec.items:
            f = fruits[i.photo_id]; n = f['nutrients']
            rows += ('<div class="nutrition-row"><div class="nutrition-fruit">' + photo_svg(i.photo_id)
                + f'<h3>{_e(f["name"])}</h3></div><div class="nutrition-values">'
                + ''.join(f'<div><span>{label}</span><b>{n[key]:g}<small>{unit}</small></b></div>' for key, label, unit in [
                    ('energy_kcal', '热量', 'kcal'), ('carbohydrate_g', '碳水', 'g'),
                    ('sugars_g', '总糖', 'g'), ('fiber_g', '膳食纤维', 'g'),
                    ('vitamin_c_mg', '维生素C', 'mg'), ('water_g', '水分', 'g')])
                + '</div></div>')
        return f'<div class="nutrition-sheet"><h3>{_e(spec.title)}</h3><p class="nutrition-unit">每 100 克生鲜可食部 · USDA SR Legacy 2018</p>{rows}<p class="nutrition-scope">品种与成熟度会造成差异；图片是AI示意图，非检测样本。<br>碳水、总糖不是GI；熟制、果汁不能直接套用本表。</p></div>'
    cards = ''
    for i in spec.items:
        f = fruits[i.photo_id]; n = f['nutrients']
        cards += (f'<article class="fruit-card">{photo_svg(i.photo_id)}<div><h3>{_e(i.label)}</h3>'
                  f'<p>{_e(i.detail)}</p><span class="fruit-badge">{n["energy_kcal"]:g} kcal · 碳水 {n["carbohydrate_g"]:g} g / 100 g</span></div></article>')
    return f'<div class="fruit-grid fruit-grid-{len(spec.items)}">{cards}</div>'


FRUIT_CSS = '''
.media-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}.media-count-1{grid-template-columns:1fr}.media-card{border:1px solid #deddd5;background:#fffdf8;border-radius:24px;overflow:hidden}.media-card img{width:100%;height:340px;display:block;object-fit:cover}.media-count-1 img{height:480px}.media-card h3{font-size:34px;padding:22px 24px 0}.media-card p{font-size:27px;padding:14px 24px 26px;line-height:1.5}
.fruit-grid{display:grid;grid-template-columns:1fr 1fr;gap:22px}.fruit-card{background:#fffdf8;border-radius:24px;overflow:hidden;border:1px solid #e4dece;display:flex;flex-direction:column}.fruit-photo{display:block;width:100%;height:245px;background:#faf7f0}.fruit-card>div{padding:18px 24px 22px}.fruit-card h3{font-size:34px;margin:0 0 10px;color:#493c28}.fruit-card p{font-size:27px;line-height:1.45;margin:0;color:#655b49}.fruit-badge{display:block;font-size:21px;color:#976324;margin-top:13px}.fruit-grid-2 .fruit-photo{height:310px}.fruit-grid-2 .fruit-card>div{padding:24px}.fruit-grid-3 .fruit-card:last-child{grid-column:1/-1;display:grid;grid-template-columns:38% 1fr;align-items:center}.fruit-grid-3 .fruit-card:last-child .fruit-photo{height:200px}.nutrition-sheet{padding:24px;background:#fffdf8;border:1px solid #e4dece;border-radius:24px}.nutrition-sheet>h3{font-size:34px;margin:0 0 8px;color:#493c28}.nutrition-unit{font-size:24px;color:#886b44;margin:0 0 20px}.nutrition-row{display:grid;grid-template-columns:180px 1fr;gap:20px;padding:17px 0;border-top:1px solid #e8e2d8}.nutrition-fruit .fruit-photo{height:142px}.nutrition-fruit h3{font-size:27px;margin:6px 0;text-align:center}.nutrition-values{display:grid;grid-template-columns:repeat(3,1fr);gap:14px 12px;align-items:center}.nutrition-values span{display:block;font-size:23px;color:#76644e;white-space:nowrap}.nutrition-values b{font-size:32px;color:#493c28}.nutrition-values small{font-size:21px;margin-left:5px;font-weight:400}.nutrition-scope{font-size:22px;color:#89765d;line-height:1.5;margin:18px 0 0}.v-food .v-takeaway{background:#805829}.v-food .brand-mark{color:#805829}.v-food .v-footer{font-size:20px}.v-food .v-header h1,.v-food .v-header h2{color:#4d3827}
'''


def _theme_css(form: str) -> dict:
    """按题材形态返回主题色与品牌文案。不同题材换主题，不是一套配色走天下。"""
    from .content_forms import FORM_THEMES, FORMS

    key = form or 'explainer'
    theme = FORM_THEMES.get(key, FORM_THEMES['explainer'])
    form_name = FORMS[key].name if key in FORMS else '科普说明'
    css = (
        f'.page{{background:{theme["bg"]};color:{theme["ink"]}}}'
        f'.v-brand{{color:{theme["muted"]}}}'
        f'.brand-mark i{{background:{theme["accent"]}}}'
        f'.v-kicker{{background:{theme["soft"]};color:{theme["accent"]}}}'
        f'.v-takeaway{{background:{theme["ink"]};color:{theme["on_ink"]}}}'
        f'.v-takeaway svg{{color:{theme["accent"]}}}'
        f'.v-sub,.v-body p{{color:{theme["muted"]}}}'
        f'.v-body p{{border-left-color:{theme["accent"]}}}'
        f'.v-footer{{color:{theme["muted"]};border-top-color:{theme["soft"]}}}'
        f'.diagram-label{{color:{theme["muted"]}}}'
        f'.diagram-label:before{{background:{theme["accent"]}}}'
    )
    css += ''.join(f'.tone-{i}{{background:{c};color:{theme["ink"]}}}'
                   for i, c in enumerate(theme["tones"]))
    return {'name': form_name, 'css': css}


def illustrated_page_html(page: dict, profile, platform: str, total: int, font_stack: str,
                          form: str = '') -> tuple[str, str]:
    spec = VisualSpec.model_validate(page['visual'])
    food = any(i.photo_id for i in spec.items)
    media_items=[i for i in spec.items if i.media_id]
    r = profile.render
    avail_w = r.width_px - 2 * r.safe_margin_px
    avail_h = r.height_px - 2 * r.safe_margin_px
    scale = min(avail_w / 936, avail_h / 1296)
    left = r.safe_margin_px + (avail_w - 936 * scale) / 2
    top = r.safe_margin_px + (avail_h - 1296 * scale) / 2
    cover = page.get('layout') == 'cover'
    tag = 'h1' if cover else 'h2'
    body = page.get('body') or []
    sub = f'<div class="v-sub">{_e(body[0])}</div>' if cover and body else ''
    notes = body[1:] if cover else body
    body_html = ''.join(f'<p>{_e(b)}</p>' for b in notes)
    theme = _theme_css(form)
    if media_items:
        platform_name='抖音 · 图文' if platform=='douyin' else '小红书 · 图文'
        brand='图文作品'
    elif food:
        platform_name = '生活食养 · 速读版' if platform == 'douyin' else '生活食养 · 收藏版'
        brand = '秋果选购与吃法'
    elif (form or 'explainer') == 'explainer':
        platform_name = '创作者速读' if platform == 'douyin' else '创作者手册'
        brand = '图解创作笔记'
    else:
        platform_name = f'{theme["name"]} · 速看版' if platform == 'douyin' else f'{theme["name"]} · 收藏版'
        brand = f'{theme["name"]}图解'
    default_footer = ('资料与编辑建议' if food else
                      ('榜单为编辑整理 · 不代表平台热度' if (form == 'ranking') else '方法图解 · 示例不代表实际结果'))
    footer = (page.get('footnote') or default_footer) + (' · 配图：AI写实示意图' if food else '')
    if media_items:
        from .content_media import ContentMedia
        metadata=[ContentMedia().get(i.media_id) for i in media_items]
        footer=(page.get('footnote') or '资料与编辑建议')+(' · 配图：AI生成示意图' if any(m['origin']=='generated' for m in metadata) else ' · 配图：用户提供')
    from .editorial_templates import CSS as EDITORIAL_CSS
    family=(spec.template_style or {}).get('design_family','legacy')
    styled=family!='legacy'
    editorial=not styled and (spec.kind=='catalog' or (spec.kind=='rank' and spec.presentation in {'rank_cards','editorial'}))
    friendly=not styled and spec.presentation=='friendly_guide' and not food and not media_items
    meme=not styled and form=='meme' and not food and not media_items and not friendly
    diagram=_diagram(spec)
    meme_css=''
    header_art=''
    if meme:
        # Render actual details even on page one. The old cover rendered only
        # labels plus a generic office illustration and dropped all explanations.
        diagram='<div class="meme-cards">'+''.join(f'<article class="meme-card tone-{n%4}"><div class="meme-card-heading">{icon_svg(i.icon,34)}<h3>{_e(i.label)}</h3></div><p>{_e(i.detail)}</p></article>' for n,i in enumerate(spec.items))+'</div>'
        meme_css='''.v-brand{height:20px;font-size:19px;letter-spacing:0}.v-kicker{font-size:22px;margin-bottom:14px}.v-header h1,.v-header h2{font-size:70px;line-height:1.17}.v-sub{font-size:28px}.meme-cards{display:flex;flex-direction:column;gap:24px}.meme-card{padding:30px 34px;border-radius:24px;border:2px solid #193e32;box-shadow:6px 7px 0 #193e3214}.meme-card-heading{display:flex;gap:15px;align-items:center;margin-bottom:16px}.meme-card-heading svg{flex:none}.meme-card h3{font-size:32px}.meme-card p{font-size:35px;line-height:1.5;color:#172f29}.v-body p{font-size:26px}.v-takeaway{padding:18px 24px}.v-takeaway p{font-size:28px}.v-footer{font-size:19px;padding-top:14px}.compact .meme-cards{gap:18px}.compact .meme-card{padding:23px 29px}.compact .meme-card p{font-size:32px}.compact .meme-card-heading{margin-bottom:10px}'''
        brand='';platform_name=''
        meme_css+=' .v-brand{display:none}'
        footer=page.get('footnote') or ''
    if friendly:
        from .friendly_templates import render_guide,CSS as FRIENDLY_CSS,DOODLE
        diagram=render_guide(spec,icon_svg)
        header_art=DOODLE
        meme_css=FRIENDLY_CSS;brand='';platform_name='';footer=page.get('footnote') or ''
    if not styled and (spec.template_style or {}).get('decoration')=='rich':
        from .poster_templates import render_poster,HEADER_ART,CSS as POSTER_CSS
        header_art=HEADER_ART;meme_css+=POSTER_CSS
        # Preserve real photos, nutrition tables, price tables and true A/B
        # comparisons. All other knowledge layouts share the rich poster rows.
        if not food and not media_items and spec.kind!='compare' and not (friendly and 'fg-table' in diagram):
            diagram=render_poster(spec,icon_svg)
        if spec.presentation=='editorial':meme_css+=' .poster-label{background:#f8f5e9!important;color:#25303a!important;border-color:#34424a!important}.poster-number{color:#34424a!important;border-color:#34424a!important}'
        if spec.presentation=='rank_cards':meme_css+=' .poster-row{grid-template-columns:44px 235px minmax(0,1fr) 78px}.poster-number{background:#f4d36f;border-radius:12px;color:#332c17;border-color:#c2a23e}.poster-label{transform:rotate(.7deg)}'
        if spec.presentation=='category_table':meme_css+=' .poster-label{background:#f4efd9;border-radius:9px;transform:none}.poster-label small{background:#d9e8d1;padding:3px;border-radius:5px}.poster-panel-title{background:#e2ead6;border-radius:12px}'
    if spec.template_style:
        from .template_packages import style_css
        meme_css+=style_css(spec.template_style)
    if styled:
        from .poster_styles import render_style,style_header,STYLE_CSS
        # Structured photos/nutrition stay intact; styles never rewrite facts.
        diagram=render_style(spec,icon_svg,diagram,preserve=bool(food or media_items))
        header_art=style_header(family) if spec.template_style.get('decoration')=='rich' else ''
        meme_css+=STYLE_CSS
        brand='';platform_name=''
    doc = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>
      {CSS}{FRUIT_CSS}{RANK_CSS}{EDITORIAL_CSS}body{{font-family:"{html.escape(r.font_family, quote=True)}",{font_stack}}}
      {theme['css']}
      {meme_css}
      .page{{width:{r.width_px}px;height:{r.height_px}px}}
      .visual-sheet{{left:{left}px;top:{top}px;transform:scale({scale})}}
      </style></head><body><main class="page"><section class="visual-sheet design-{family} {'v-cover' if cover else ''} {'v-food' if food else ''} {'v-editorial-sheet' if editorial else ''} {'v-palette-editorial' if spec.presentation=='editorial' and not styled else ''} {'v-ranking-dense' if spec.kind == 'rank' and len(spec.items) >= 8 else ''}" data-template="{spec.presentation}" data-template-version="{spec.presentation_version}" data-visual-kind="{spec.kind}" data-form="{html.escape(form or 'explainer', quote=True)}">
      <div class="v-brand"><span class="brand-mark"><i></i>{brand}</span><span>{platform_name}</span></div>
      <header class="v-header"><div class="v-kicker">{_e(page.get('kicker') or '实用图解')}</div><{tag}>{_heading(page['heading'])}</{tag}>{sub}{header_art}</header>
      <div class="v-visual"><div class="diagram-content">{diagram}</div></div>
      <div class="v-body">{body_html}</div>
      <aside class="v-takeaway">{icon_svg('idea', 36)}<p>{_e(spec.takeaway)}</p></aside>
      <footer class="v-footer"><span>{_e(footer)}</span><b>{page['index']:02d} / {total:02d}</b></footer>
      </section></main></body></html>'''
    from .meme_editorial import MEME_TEMPLATE_VERSION
    version=f'friendly-guide-{spec.kind}@{spec.presentation_version}' if friendly else f'meme-guide-{spec.kind}@{MEME_TEMPLATE_VERSION}' if meme else f'editorial-{spec.kind}-{spec.presentation}@{spec.presentation_version}' if editorial or spec.presentation=='editorial' else f'illustrated-media-{spec.kind}@1' if media_items else f'illustrated-fruit-{spec.kind}@1' if food else f'illustrated-{spec.kind}@{ILLUSTRATED_TEMPLATE_VERSION}'
    return doc,version+(f"/package-{spec.template_package_id}@{spec.template_package_version}" if spec.template_package_version else '')


def add_rule_visuals(pages: list[dict]) -> None:
    """Offline generated drafts also get meaningful diagrams from their own points.

    Imported legacy seeds remain untouched. No fabricated examples are added.
    """
    for n, page in enumerate(pages):
        points = page.get('body') or []
        if len(points) < 2:
            continue
        # 图解类型跟着页面的实际版式走：抖音可以不设封面页，那第一页就不能再挂
        # cover 图解（check_layout 会以 VISUAL_INVALID 拒收）。
        kind = 'cover' if page.get('layout') == 'cover' else ('flow' if n == len(pages)-1 else 'map')
        icons = ['question', 'idea', 'source', 'check']
        items = [{'label': f'要点 {i+1}', 'detail': text[:64], 'icon': icons[i]}
                 for i, text in enumerate(points[:4])]
        page['visual'] = {'kind': kind, 'title': page['heading'][:22], 'items': items,
                          'takeaway': points[-1][:56]}
        page['body'] = points[:2]
