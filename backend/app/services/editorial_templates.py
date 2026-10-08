"""Locally drawn editorial layouts. All prose is escaped and icons are fixed."""
import html

def render_rows(spec,icon):
    e=lambda value:html.escape(str(value),quote=True)
    ranked=spec.kind=='rank'
    categories=list(dict.fromkeys(i.category or '内容整理' for i in spec.items))
    rows=[]
    # Group only adjacent rows, retaining user/source order even if categories repeat.
    start=0
    while start<len(spec.items):
        category=spec.items[start].category or '内容整理';end=start+1
        if not ranked:
            while end<len(spec.items) and (spec.items[end].category or '内容整理')==category:end+=1
        color=categories.index(category)%5
        entries=[]
        for n in range(start,end):
            i=spec.items[n]
            tags=''.join(f'<span>{e(t)}</span>' for t in i.tags)
            metric=f'<aside class="ed-metric"><strong>{e(i.metric_text)}</strong><small>{e(i.metric_label)}</small></aside>' if i.metric_text else ''
            badge=f'<b class="ed-number">{(i.rank or n+1):02d}</b>' if ranked else ''
            entries.append(f'<article class="ed-row ed-tone-{color}">{badge}<span class="ed-icon">{icon(i.icon,38)}</span><div class="ed-copy"><h3>{e(i.label)}</h3><p>{e(i.detail)}</p><div class="ed-tags">{tags}</div></div>{metric}</article>')
        group_head='' if ranked else f'<div class="ed-category ed-tone-{color}"><span>{icon(spec.items[start].icon,40)}</span><b>{e(category)}</b></div>'
        rows.append(f'<section class="ed-group">{group_head}<div class="ed-group-rows">{"".join(entries)}</div></section>')
        start=end
    head='' if ranked else '<div class="ed-table-head"><span>分类</span><span>名称与主要功能</span></div>'
    return f'<div class="ed-overview ed-{ "ranking" if ranked else "catalog" } ed-count-{len(spec.items)}">{head}{"".join(rows)}</div>'

CSS='''
.v-editorial-sheet{gap:18px}.v-editorial-sheet .v-header h1,.v-editorial-sheet .v-header h2{font-size:58px;line-height:1.18;letter-spacing:-1.3px}
.v-editorial-sheet .v-kicker{padding:5px 13px;font-size:22px;margin-bottom:12px;background:#fff4b9;color:#202730}
.v-editorial-sheet .v-sub{font-size:25px;line-height:1.3;margin-top:10px}.v-editorial-sheet .v-brand{font-size:20px;letter-spacing:0}
.v-editorial-sheet .v-body{gap:4px}.v-editorial-sheet .v-body p{font-size:24px;line-height:1.25}.v-editorial-sheet .v-takeaway{padding:14px 20px;background:#202b31;color:#fff;gap:12px;border-radius:15px}
.v-editorial-sheet .v-takeaway p{font-size:25px;line-height:1.35}.v-editorial-sheet .v-footer{font-size:19px;padding-top:10px}
.v-editorial-sheet .v-visual{justify-content:center}.v-editorial-sheet .diagram-content{gap:8px}
.ed-overview{display:flex;flex-direction:column;gap:7px;color:#15252d;width:100%}
.ed-table-head{display:grid;grid-template-columns:138px 1fr;background:#cce8f5;border-radius:12px 12px 4px 4px;text-align:center;padding:12px;font-size:25px;font-weight:750}
.ed-group{display:flex;gap:6px;min-width:0}.ed-group-rows{display:flex;flex-direction:column;gap:6px;flex:1;min-width:0}
.ed-category{width:138px;flex:none;border-radius:12px;display:flex;flex-direction:column;justify-content:center;align-items:center;gap:13px;text-align:center;padding:12px;font-size:26px;line-height:1.2;overflow-wrap:anywhere}
.ed-category span{display:flex}.ed-row{display:flex;align-items:center;gap:16px;padding:10px 14px;border:1px solid #dce6e0;border-radius:12px;background:#fff;min-width:0}
.ed-ranking .ed-row{background:#fff;border-radius:18px;padding:12px 16px;box-shadow:0 2px 3px #15252d08}
.ed-ranking .ed-copy{flex:1}.ed-number{width:48px;min-width:48px;align-self:stretch;display:flex;align-items:center;justify-content:center;background:#fff1a2;color:#142029;border-radius:12px;font-size:31px;font-weight:800}
.ed-icon{display:flex;justify-content:center;align-items:center;width:53px;min-width:53px;height:53px;border-radius:13px;background:#e8f4f3;color:#228895}
.ed-copy{flex:1;min-width:0;overflow-wrap:anywhere}.ed-copy h3{font-size:27px;line-height:1.13;font-weight:800;letter-spacing:-.5px;margin:0 0 4px}
.ed-copy p{font-size:24px;line-height:1.2;margin:0;color:#3b525a}.ed-tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:5px}
.ed-tags:empty{display:none}.ed-tags span{font-size:20px;line-height:1.15;padding:3px 8px;background:#eff6e6;border-radius:7px;color:#38533e}
.ed-metric{width:144px;min-width:144px;text-align:center;border-left:1px solid #dce6e0;padding-left:10px}.ed-metric strong{display:block;font-size:30px;color:#15252d;line-height:1.15}.ed-metric small{font-size:19px;line-height:1.2;display:block;color:#4e616a;margin-top:5px}
.ed-tone-0 .ed-icon,.ed-category.ed-tone-0{background:#cef0fd;color:#0075b1}.ed-tone-1 .ed-icon,.ed-category.ed-tone-1{background:#dbf6dc;color:#127b4a}
.ed-tone-2 .ed-icon,.ed-category.ed-tone-2{background:#ffe3c7;color:#b76817}.ed-tone-3 .ed-icon,.ed-category.ed-tone-3{background:#eee1ff;color:#7941ba}
.ed-tone-4 .ed-icon,.ed-category.ed-tone-4{background:#ffdcef;color:#b62d85}
.ed-catalog .ed-row{padding:9px 12px;background:#fafefc}.ed-catalog .ed-copy{display:grid;grid-template-columns:minmax(160px, .8fr) minmax(220px,1fr);column-gap:16px;align-items:center}
.ed-catalog .ed-copy h3{font-size:26px;margin:0}.ed-catalog .ed-copy p{font-size:24px}.ed-catalog .ed-tags{grid-column:1/-1}
.ed-catalog .ed-icon{width:40px;min-width:40px;height:40px}.ed-catalog .ed-icon svg{width:30px;height:30px}
.ed-catalog .ed-metric{width:125px;min-width:125px}.ed-catalog .ed-metric strong{font-size:26px}
.ed-catalog .ed-tags span{font-size:19px}.ed-catalog .ed-tags{margin-top:2px}
.v-editorial-sheet .compact .ed-row{padding-top:5px;padding-bottom:5px}.v-editorial-sheet .compact .ed-category{padding-top:8px;padding-bottom:8px}
.v-palette-editorial{color:#172128}.v-palette-editorial .v-kicker{background:#ffe66b;color:#172128}.v-palette-editorial .v-brand,.v-palette-editorial .v-sub{color:#475b66}
.v-palette-editorial .tone-0,.v-palette-editorial .tone-1,.v-palette-editorial .tone-2,.v-palette-editorial .tone-3{background:#f6f7f3;color:#172128;border:1px solid #cdd6d7}
.v-palette-editorial .v-takeaway{background:#172128;color:#fff}.v-palette-editorial .brand-mark i{background:#edc63a}
'''
