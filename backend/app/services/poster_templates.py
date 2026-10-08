"""Rich hand-drawn poster motifs. Original fixed vectors, escaped editable text."""
import html
from .friendly_templates import DOODLE

HEADER_ART=DOODLE.replace('class="fg-doodle"','class="fg-doodle poster-robot"')+'''<svg class="poster-sparks" viewBox="0 0 830 180" aria-hidden="true"><g fill="none" stroke-linecap="round" stroke-linejoin="round" stroke-width="3"><path d="M17 23l20 8-11-20 1 24 14-14-24 2" stroke="#edae25"/><path d="M58 14l6 9M72 9l2 13M50 33l12 1" stroke="#3a72b2"/><path d="M782 117q-17-21-24-10-7 15 24 30 24-26 12-30-7-5-12 10" stroke="#347254"/><path d="M619 11l3 9 9 1-8 5 3 9-7-6-8 5 3-8-6-6 9-1z" fill="#f5d569" stroke="#e5b344"/><path d="M10 167q410-18 809 0M47 175q350-14 730-6" stroke="#318bac" stroke-width="4"/></g></svg>'''

SCENES={
 'chart': '<ellipse cx="39" cy="53" rx="19" ry="7" fill="#f4c657"/><path d="M20 35v18q19 15 38 0V35" fill="#f4d36f"/><ellipse cx="39" cy="35" rx="19" ry="7" fill="#ffe79c"/><path d="M20 44q19 12 38 0"/><circle cx="73" cy="53" r="18" fill="#f1cb5b"/><circle cx="73" cy="53" r="12"/><path d="M69 45h9M73 45v16"/>',
 'brain': '<rect x="25" y="23" width="53" height="42" rx="6" fill="#b9dcf2"/><rect x="32" y="29" width="38" height="27" rx="4" fill="#e8f5fb"/><path d="M32 17v6m12-6v6m12-6v6m12-6v6M32 65v7m12-7v7m12-7v7m12-7v7M19 31h6m-6 12h6m-6 12h6m53-24h6m-6 12h6m-6 12h6M39 40l7-5 4 9 9-7"/>',
 'code':'<rect x="15" y="23" width="76" height="48" rx="6" fill="#fcfcf0"/><path d="M15 35h76"/><path d="M24 29h2m7 0h2m7 0h2M39 44l-9 9 9 8M65 44l9 9-9 8M55 43l-7 20"/><circle cx="85" cy="65" r="13" fill="#b6d8d1"/><path d="M85 56v18m-9-9h18"/>',
 'question':'<path d="M22 22q-13 0-13 13v18q0 13 13 13h12l9 10 2-10h28q14 0 14-13V35q0-13-14-13z" fill="#c8dea5"/><circle cx="30" cy="43" r="2" fill="#304e40"/><circle cx="47" cy="43" r="2" fill="#304e40"/><circle cx="64" cy="43" r="2" fill="#304e40"/><path d="M72 61l21-26 7 5-21 28-10 6z" fill="#f2bf54"/>',
 'check':'<g transform="rotate(6 52 47)"><rect x="28" y="16" width="49" height="65" rx="5" fill="#fffef2"/><path d="M38 33l4 4 7-8m-11 21 4 4 7-8m-11 21 4 4 7-8" stroke="#54925e"/><path d="M55 34h14M55 51h14M55 67h14"/><rect x="43" y="12" width="20" height="10" rx="3" fill="#b9d9c6"/></g>',
 'search':'<g transform="rotate(-6 52 48)"><rect x="17" y="17" width="58" height="66" rx="4" fill="#fffef2"/><path d="M27 31h37M27 41h23M27 53h25M27 65h20"/></g><circle cx="62" cy="50" r="19" fill="#c9e7ee" fill-opacity=".7"/><path d="M76 65l18 20" stroke="#de9356" stroke-width="9"/>',
 'idea':'<path d="M29 51q-25-35 11-40 31 1 13 34l-7 11H33z" fill="#f5d267"/><path d="M32 61h18m-16 6h14M40 19v9M13 17l7 6M8 40h8M65 17l-7 6M68 40h-7"/><path d="M72 48l-23 36h47z" fill="#f4ca63"/><path d="M73 61v10m0 6v1"/>',
 'globe':'<circle cx="38" cy="45" r="27" fill="#c6e6ef"/><ellipse cx="38" cy="45" rx="12" ry="27"/><path d="M11 45h54M17 30h42M17 61h42"/><path d="M75 18l-7 16h11l-5 18 17-23H80l9-11z" fill="#f1d264"/>',
 'palette':'<rect x="15" y="19" width="65" height="50" rx="6" fill="#f3f7e9"/><path d="M24 59l16-18 10 12 11-22 13 28z" fill="#a4ceba"/><circle cx="63" cy="32" r="6" fill="#f1cb61"/><path d="M47 70v10m-18 1h37"/><path d="M76 47l17-23 7 5-17 25-12 5z" fill="#f0bc66"/>',
 'fruit':'<path d="M50 36q-34-18-34 13-1 32 24 26l10-4 10 4q27 5 24-28-4-29-34-11z" fill="#dbb061"/><path d="M50 36V20m0 4q2-24 21-13 0 19-21 13" fill="#b3d394"/>',
 'page':'<rect x="20" y="16" width="54" height="64" rx="6" fill="#fffef0"/><path d="M32 30h28M32 43h24M32 56h28M32 69h17"/><path d="M78 32l-7 20h13l-7 28 24-37H86l9-11z" fill="#f5d06a"/>',
 'pencil':'<rect x="15" y="25" width="55" height="54" rx="6" fill="#e4eeda"/><path d="M27 39h30M27 51h22M27 63h27"/><path d="M65 51l20-31 10 7-21 30-15 11z" fill="#f0cd66"/><path d="M80 28l10 7M65 51l9 6"/>',
 'source':'<path d="M12 22q22-9 41 2 19-11 41-2v51q-22-8-41 2-19-10-41-2z" fill="#d6e8c0"/><path d="M53 24v51M23 36h19m-19 12h19m-19 12h16M64 36h19m-19 12h19m-19 12h16"/>',
 'briefcase':'<rect x="15" y="31" width="77" height="47" rx="8" fill="#f3c676"/><path d="M36 31V17h34v14M15 46l39 10 38-10M49 49h10v13H49z"/>',
 'game':'<path d="M31 27h46q15 0 21 33 4 29-12 20L72 62H38L22 80Q4 92 9 60q7-33 22-33z" fill="#d8d0e9"/><path d="M26 41v18m-9-9h18M72 43h1m12 11h1M45 27l-3-12h15" stroke-width="4"/>',
 'news':'<rect x="16" y="18" width="76" height="65" rx="6" fill="#fffdf0"/><path d="M26 30h56M26 39h56M26 70h56M60 49h22m-22 10h22"/><rect x="26" y="49" width="24" height="14" rx="2" fill="#b9d9e7"/>',
 'clock':'<circle cx="44" cy="44" r="30" fill="#d1e5f0"/><circle cx="44" cy="44" r="23" fill="#fffdf0"/><path d="M44 27v19l14 9M44 17v4M44 67v4M17 44h4M67 44h4"/><rect x="70" y="60" width="27" height="27" rx="5" fill="#f2d47a"/><path d="M75 59v7m16-7v7M71 70h25m-20 6h4m5 0h4"/>',
}

def scene(icon,label=''):
    import re
    if re.search(r'公式|怎么算|计算|换算',label):icon='code'
    elif re.search(r'高峰|时段|时间|日历',label):icon='clock'
    elif re.search(r'缓存|价格|多少钱|费用',label):icon='chart'
    elif re.search(r'token|分词|上下文',label,re.I):icon='brain'
    art=SCENES.get(icon,SCENES['page'])
    return '<svg class="poster-scene" viewBox="0 0 110 96" aria-hidden="true"><g stroke="#34493e" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" fill="none">'+art+'</g></svg>'

def render_poster(spec,icon):
    e=lambda text:html.escape(str(text),quote=True)
    rows=[]
    for n,item in enumerate(spec.items):
        category='<small>'+e(item.category)+'</small>' if item.category and spec.kind=='catalog' else ''
        metric=('<span class="poster-metric"><b>'+e(item.metric_text)+'</b><small>'+e(item.metric_label)+'</small></span>') if item.metric_text else ''
        tags=('<span class="poster-tags">'+' · '.join(e(t) for t in item.tags)+'</span>') if item.tags else ''
        arrow='<div class="poster-flow-arrow">↓</div>' if spec.kind=='flow' and n<len(spec.items)-1 else ''
        rows.append(f'<article class="poster-row poster-tone-{n%5}"><b class="poster-number">{item.rank or n+1}</b><h3 class="poster-label">{category}{e(item.label)}</h3><div class="poster-copy"><p class="poster-detail">{e(item.detail)}</p>{tags}{metric}</div>{scene(item.icon,item.label)}</article>{arrow}')
    return f'<section class="poster-panel poster-count-{len(spec.items)}"><h3 class="poster-panel-title">{e(spec.title)}</h3>'+''.join(rows)+'</section>'

CSS='''
.visual-sheet{background-image:linear-gradient(#698c6509 1px,transparent 1px),linear-gradient(90deg,#698c6507 1px,transparent 1px);background-size:26px 26px;border:2px solid #bdc8ac;border-radius:25px;padding:32px 26px;box-shadow:0 0 0 9px #fffef2}.v-brand{display:none}.diagram-content{width:880px}.v-header{position:relative}
.v-header{min-height:215px;padding-right:160px}.v-header h1,.v-header h2{letter-spacing:1px;text-shadow:1px 1px 0 currentColor;line-height:1.16}.v-kicker{transform:rotate(-1.2deg);border:2px solid #d2aa36;background:#f8dc6b!important;color:#2c493b!important;box-shadow:4px 4px 0 #e7c97b55}
.v-sub{display:inline-block;background:#f7df818c;padding:6px 13px;border-radius:12px;transform:rotate(-.5deg)}
.poster-sparks{position:absolute;left:-2px;top:-27px;width:100%;height:180px;pointer-events:none}.poster-robot{position:absolute;right:0;width:142px;height:140px;top:25px;transform:rotate(5deg)}
.poster-panel{border:2.5px solid #7a937a;border-radius:19px;padding:12px 18px;background:#fffcf1dd;box-shadow:5px 6px 0 #99af8355}
.poster-panel-title{font-size:34px;font-weight:900;text-align:center;line-height:1.4;padding:12px 0 18px;color:#244d75;border-bottom:2px dashed #c4cbb6}
.poster-row{display:grid;grid-template-columns:42px 165px minmax(0,1fr) 90px;align-items:center;gap:15px;padding:25px 0;border-bottom:2px dashed #cbc9b3;min-height:156px}.poster-row:last-child{border:0}
.poster-number{height:39px;width:39px;border:2px solid #376987;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:26px;color:#315e7b;font-family:'Comic Sans MS','KaiTi',sans-serif;transform:rotate(-4deg)}
.poster-label{background:#b8dcf2;color:#153b5d;border:2px solid #387fa5;border-radius:13px;font-size:32px;line-height:1.15;padding:15px 7px;text-align:center;box-shadow:3px 3px 0 #387fa531;transform:rotate(-1.4deg);position:relative;overflow-wrap:anywhere}
.poster-label:after{content:'';position:absolute;inset:3px;border:1px solid currentColor;opacity:.3;border-radius:9px;transform:rotate(1.5deg)}
.poster-detail{font-size:28px;line-height:1.52;color:#283e31;overflow-wrap:anywhere}.poster-scene{width:90px;height:86px;transform:rotate(3deg)}
.poster-label small{display:block;font:16px sans-serif;margin-bottom:5px;opacity:.75}.poster-metric{display:flex;align-items:baseline;gap:8px;margin-top:7px}.poster-metric b{font-size:26px;color:#705016}.poster-metric small{font-size:19px}.poster-tags{display:block;font-size:18px;color:#577247;margin-top:6px}.poster-flow-arrow{text-align:center;font-size:26px;color:#5b866b;height:22px;line-height:22px}.fg-price-label{position:relative}.fg-price-label:after{content:'◉';position:absolute;left:3px;top:-17px;font-size:19px;color:#bc8e31;transform:rotate(-15deg)}
.poster-tone-1 .poster-label{background:#cde6b2;border-color:#548043;color:#27492e}.poster-tone-2 .poster-label{background:#f8dca0;border-color:#b58229;color:#6c4b19}.poster-tone-3 .poster-label{background:#daccf0;border-color:#7766a0;color:#4f3f72}.poster-tone-4 .poster-label{background:#b8e6df;border-color:#3d8d83;color:#245951}
.poster-tone-1 .poster-number{border-color:#548043;color:#548043}.poster-tone-2 .poster-number{border-color:#a67526;color:#a67526}.poster-tone-3 .poster-number{border-color:#7766a0;color:#7766a0}.poster-tone-4 .poster-number{border-color:#3d8d83;color:#3d8d83}
.poster-count-8 .poster-row,.poster-count-9 .poster-row,.poster-count-10 .poster-row,.poster-count-11 .poster-row,.poster-count-12 .poster-row,.poster-count-13 .poster-row,.poster-count-14 .poster-row,.poster-count-15 .poster-row,.poster-count-16 .poster-row{min-height:66px;padding:10px 0;gap:12px;grid-template-columns:38px 158px minmax(0,1fr) 75px}
.poster-count-10 .poster-label,.poster-count-11 .poster-label,.poster-count-12 .poster-label{font-size:27px;padding:10px 6px}.poster-count-10 .poster-detail,.poster-count-11 .poster-detail,.poster-count-12 .poster-detail{font-size:24px;line-height:1.3}.poster-count-10 .poster-scene,.poster-count-11 .poster-scene,.poster-count-12 .poster-scene{width:75px;height:65px}
.v-takeaway{position:relative;border:2px solid #88a77b;box-shadow:4px 5px 0 #b9c89c6e}.v-takeaway:after{content:'✦';position:absolute;right:12px;top:5px;font-size:25px;color:#b58d23}.v-footer{border-top:2px dashed #bdc9ac}
.compact .poster-row{min-height:113px;padding:14px 0}.compact .poster-detail{font-size:26px}.compact .poster-count-10 .poster-row,.compact .poster-count-11 .poster-row,.compact .poster-count-12 .poster-row{min-height:60px;padding:7px 0}.compact .poster-count-10 .poster-detail,.compact .poster-count-11 .poster-detail,.compact .poster-count-12 .poster-detail{font-size:23px}.compact .poster-panel-title{padding:9px 0 12px}
.poster-panel:is(.poster-count-13,.poster-count-14,.poster-count-15,.poster-count-16) .poster-row{min-height:58px;padding:5px 0;grid-template-columns:32px 164px minmax(0,1fr) 56px;gap:10px}
.poster-panel:is(.poster-count-13,.poster-count-14,.poster-count-15,.poster-count-16) .poster-label{font-size:22px;padding:4px 6px;line-height:1.1}
.poster-panel:is(.poster-count-13,.poster-count-14,.poster-count-15,.poster-count-16) .poster-label small{font:12px/13px sans-serif;margin-bottom:2px;padding:0}
.poster-panel:is(.poster-count-13,.poster-count-14,.poster-count-15,.poster-count-16) .poster-detail{font-size:24px;line-height:1.3}
.poster-panel:is(.poster-count-13,.poster-count-14,.poster-count-15,.poster-count-16) .poster-scene{width:56px;height:53px}
.poster-panel:is(.poster-count-13,.poster-count-14,.poster-count-15,.poster-count-16) .poster-number{width:30px;height:30px;font-size:20px}
.poster-panel:is(.poster-count-13,.poster-count-14,.poster-count-15,.poster-count-16) .poster-tags{font-size:15px;margin-top:2px}
'''
