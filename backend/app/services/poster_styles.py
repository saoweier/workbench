"""Five independent visual grammars; content form remains a separate contract.

Only validated data is rendered. No model CSS/HTML or invented chart values.
Legacy frozen packages continue using their historical layout.
"""
import html,re
from .poster_templates import scene,HEADER_ART

def e(value):return html.escape(str(value),quote=True)

def style_header(family):
    if family=='handdrawn':return '<div class="hand-doodles" aria-hidden="true">'+HEADER_ART+'</div>'
    if family=='magazine':
        return '<div class="mag-masthead" aria-hidden="true"><span>THE EDIT / 内容特刊</span><i></i><b>READ • THINK • MAKE</b></div>'
    if family=='neon':
        return '''<svg class="tech-orbit" viewBox="0 0 200 150" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="100" cy="75" rx="82" ry="30" transform="rotate(-30 100 75)"/><ellipse cx="100" cy="75" rx="82" ry="30" transform="rotate(30 100 75)"/><circle cx="100" cy="75" r="43" stroke-dasharray="4 7"/><rect x="79" y="54" width="42" height="42"/><path d="M88 68l-7 7 7 7m24-14 7 7-7 7M22 126h36M142 24h36"/><circle cx="172" cy="33" r="5" fill="currentColor"/></g></svg>'''
    if family=='collage':
        return '<div class="collage-stamp" aria-hidden="true"><b>灵感<br>ON!</b></div><span class="collage-star" aria-hidden="true">✳</span>'
    return '<div class="data-mark" aria-hidden="true"><span>●</span><span>■</span><span>▲</span><i></i></div>'

def ordinal(item,n,kind):
    # Numbers on a flow denote steps. Ordinary explanations are never ranked.
    if kind=='rank':return f'<b class="style-rank">{item.rank or n+1:02d}</b>'
    if kind=='flow':return f'<b class="style-step">{n+1:02d}</b>'
    if kind=='compare':return f'<b class="style-choice">{chr(65+n) if n<26 else n+1}</b>'
    return ''

def display_label(item,n,kind):
    if kind!='rank':return item.label
    rank=item.rank or n+1
    return re.sub(r'^0?'+str(rank)+r'(?:[.、）)]\s*|\s+)', '',item.label).strip()

def metadata(item):
    category=f'<small class="style-category">{e(item.category)}</small>' if item.category else ''
    tags='<div class="style-tags">'+''.join(f'<span>{e(t)}</span>' for t in item.tags)+'</div>' if item.tags else ''
    metric=f'<aside class="style-metric" data-source="{e(item.metric_source_id)}"><b>{e(item.metric_text)}</b><small>{e(item.metric_label)}</small></aside>' if item.metric_text else ''
    return category,tags,metric

def render_style(spec,icon,original,preserve=False):
    family=spec.template_style['design_family'];count=len(spec.items)
    if preserve:return '<div class="style-preserved">'+original+'</div>'
    headings=spec.title.split('｜')
    if len(headings)==3 and all(re.fullmatch(r'¥\d+(?:\.\d+)?(?:｜¥\d+(?:\.\d+)?){2}',i.detail) for i in spec.items):
        head='<div class="price-head"><b>模型 / 时段</b>'+''.join(f'<b>{e(h)}</b>' for h in headings)+'</div>'
        rows=''.join('<article class="price-row"><h3>'+e(i.label)+'</h3>'+''.join(f'<b>{e(v)}</b>' for v in i.detail.split('｜'))+'</article>' for i in spec.items)
        return f'<section class="style-price {family}-price">{head}{rows}</section>'
    rows=[];dense=count>6 or (spec.kind in {'rank','catalog'} and count>4)
    for n,item in enumerate(spec.items):
        category,tags,metric=metadata(item);number=ordinal(item,n,spec.kind)
        title=f'<h3>{e(display_label(item,n,spec.kind))}</h3>';detail=f'<p class="style-detail">{e(item.detail)}</p>'
        if family=='handdrawn':
            row=f'<article class="hand-note style-item tone-note-{n%4}"><span class="note-pin" aria-hidden="true"></span>{number}<div class="hand-art">{scene(item.icon,item.label)}</div><div class="hand-copy">{category}{title}{detail}{tags}</div>{metric}</article>'
        elif family=='magazine':
            row=f'<article class="mag-article style-item{" has-metric" if metric else ""}">{number}<div class="mag-illustration">{icon(item.icon,72)}</div><div class="mag-copy">{category}{title}{detail}{tags}</div>{metric}</article>'
        elif family=='neon':
            row=f'<article class="tech-module style-item"><span class="tech-port" aria-hidden="true"></span>{number}<div class="tech-symbol">{icon(item.icon,64)}</div><div class="tech-copy">{category}{title}{detail}{tags}</div>{metric}</article>'
        elif family=='collage':
            copy=f'<div class="cut-name">{title}<div class="cut-meta">{category}{tags}</div></div><div class="cut-description">{detail}</div>' if dense and spec.kind=='rank' else category+title+detail+tags
            row=f'<article class="cut-paper style-item paper-{n%4}"><i class="paper-tape" aria-hidden="true"></i>{number}<div class="cut-illustration">{scene(item.icon,item.label)}</div><div class="cut-copy">{copy}</div>{metric}</article>'
        else:
            row=f'<article class="data-record style-item">{number}<div class="data-symbol">{icon(item.icon,40)}</div><div class="data-key">{category}{title}</div><div class="data-value">{detail}{tags}</div>{metric}</article>'
        rows.append(row)
    container={'handdrawn':'hand-notes','magazine':'mag-columns','neon':'tech-network','collage':'cut-board','data':'data-records'}[family]
    extra=(' style-dense' if dense else '')+(' style-many' if count>12 else '')
    label=f'<h3 class="style-section-title">{e(spec.title)}</h3>'
    if family=='data':label=f'<div class="data-table-head"><b>{e(spec.title)}</b><span>对象 / 类别</span><span>具体说明</span></div>'
    return f'<section class="style-layout family-{family} shape-{spec.kind}{extra}" data-item-count="{count}">{label}<div class="{container}">'+''.join(rows)+'</div></section>'

STYLE_CSS=r'''
/* Shared fit contract only. Each family owns typography, grid and artwork. */
.visual-sheet:not(.design-legacy){padding:34px 30px;gap:22px;color:var(--ink);border-radius:0;box-shadow:none;border:0}
.visual-sheet:not(.design-legacy) .v-brand{display:none}
.visual-sheet:not(.design-legacy) .v-header{position:relative;padding:0;min-height:0}
.visual-sheet:not(.design-legacy) .v-header h1,.visual-sheet:not(.design-legacy) .v-header h2{font-family:var(--title-font);font-size:var(--title-size);line-height:1.16;color:var(--ink);letter-spacing:-1px;max-width:none}
.visual-sheet:not(.design-legacy) .v-kicker{font-size:21px;margin-bottom:15px;letter-spacing:2px}
.visual-sheet:not(.design-legacy) .v-sub{font-size:27px;line-height:1.4;color:var(--ink);margin-top:17px}
.visual-sheet:not(.design-legacy) .diagram-content{width:876px;gap:0}
.visual-sheet:not(.design-legacy) .v-body p{color:var(--ink);font-size:25px;line-height:1.4;border-color:var(--accent)}
.visual-sheet:not(.design-legacy) .v-takeaway{color:var(--ink);background:var(--soft);padding:20px 22px;gap:14px;border-radius:var(--corner)}
.visual-sheet:not(.design-legacy) .v-takeaway p{font-size:27px;line-height:1.4}
.visual-sheet:not(.design-legacy) .v-takeaway svg{color:var(--accent)}
.visual-sheet:not(.design-legacy) .v-footer{color:var(--ink);font-size:18px;padding-top:14px;border-color:var(--accent)}
.visual-sheet:not(.design-legacy) .v-footer b{font-size:21px;color:var(--accent)}
.style-section-title{font-size:26px;line-height:1.4;margin-bottom:23px;color:var(--accent)}
.style-item{position:relative;min-width:0;overflow-wrap:anywhere}
.style-item h3{font-size:34px;line-height:1.28;margin:0 0 14px}
.style-detail{font-size:var(--poster-body);line-height:1.5;margin:0;color:var(--ink)}
.style-category{display:block;font-size:19px;line-height:1.3;margin-bottom:6px;color:var(--accent)}
.style-tags{display:flex;flex-wrap:wrap;gap:7px;margin-top:9px;font-size:17px;line-height:1.3}
.style-tags span{padding:3px 7px;background:var(--soft);color:var(--ink)}
.style-metric{font-size:26px;line-height:1.25;flex:none}.style-metric small{display:block;font-size:18px;margin-top:4px}
.style-rank,.style-step,.style-choice{display:block;font-size:32px;line-height:1.2;color:var(--accent)}
.style-dense .style-item h3{font-size:26px;line-height:1.25;margin:0 0 5px}
.style-dense .style-detail{font-size:25px;line-height:1.35}
.style-dense .style-category{font-size:17px;margin-bottom:3px}
.style-dense .style-tags{margin-top:5px;font-size:16px}
.style-dense .style-metric{font-size:24px}
.style-dense .style-metric small{font-size:16px}
.style-dense .style-rank{font-size:27px}
/* Cream paper, pencil dividers, doodles and pinned notebook pages. */
.visual-sheet.design-handdrawn{border:2px solid #b2c7a0;border-radius:28px;background-image:repeating-linear-gradient(0deg,transparent 0 32px,#39714908 32px 33px)}
.design-handdrawn .v-header{padding-right:166px!important;min-height:205px!important}
.visual-sheet.design-handdrawn .v-header h1,.visual-sheet.design-handdrawn .v-header h2{color:var(--accent);text-shadow:2px 3px #bdd4b158}
.design-handdrawn .hand-doodles{position:absolute;right:0;top:0;width:158px;height:188px}
.design-handdrawn .poster-robot{width:158px;height:150px}.design-handdrawn .poster-sparks{position:absolute;left:0;bottom:0;width:158px;height:35px}
.design-handdrawn .v-header:after{content:'';display:block;height:6px;width:95%;margin-top:14px;background:var(--accent);border-radius:50%;transform:rotate(-1deg)}
.design-handdrawn .v-kicker{border:1px dashed var(--accent);border-radius:20px;transform:rotate(-1deg)}
.design-handdrawn .hand-notes{display:grid;grid-template-columns:1fr 1fr;gap:calc(var(--poster-gap) + 7px);padding:12px 5px}
.hand-note{padding:26px 26px 27px;border:2px solid #93a989;border-radius:var(--corner);background:#fffdf3;box-shadow:5px 7px 0 #386b4210;min-height:284px}
.hand-note:nth-child(even){transform:rotate(.6deg)}.hand-note:nth-child(odd){transform:rotate(-.5deg)}
.hand-art{float:right;width:98px;height:100px;margin:0 0 10px 12px}.hand-art svg{width:100%;height:100%}
.hand-copy h3{font-family:var(--title-font);border-bottom:2px dashed #bfcdb2;padding-bottom:12px;color:var(--accent)}
.note-pin{position:absolute;top:-7px;left:26px;width:44px;height:15px;background:#e9bc4880;transform:rotate(-6deg)}
.hand-note .style-rank,.hand-note .style-step{display:inline-block;padding:5px 12px;border:2px solid var(--accent);border-radius:50%;margin-bottom:10px}
.design-handdrawn .style-section-title{text-align:center;font-family:var(--title-font);border:2px dashed #9caf8e;border-radius:45%;padding:8px}
.design-handdrawn .shape-flow .hand-notes{grid-template-columns:1fr}
.design-handdrawn .shape-flow .hand-note{min-height:138px;display:flex;align-items:center;gap:22px;padding:18px 25px}
.design-handdrawn .shape-flow .hand-note:not(:last-child):after{content:'↓';position:absolute;bottom:-29px;left:50%;font-size:30px;color:var(--accent)}
.design-handdrawn .shape-flow .hand-copy{flex:1}.design-handdrawn .shape-flow .hand-art{order:3;margin:0;height:84px;flex:none}
.design-handdrawn .style-dense .hand-notes{display:block;padding:6px 20px;border:2px solid #93a989;border-radius:22px;background:#fffdf3}
.design-handdrawn .style-dense .hand-note{display:flex;align-items:center;gap:16px;min-height:0;padding:13px 0;border:0;border-bottom:2px dashed #ccd5bb;box-shadow:none;transform:none;background:none;border-radius:0}
.design-handdrawn .style-dense .note-pin{display:none}.design-handdrawn .style-dense .hand-art{order:3;width:55px;height:52px;margin:0;flex:none}
.design-handdrawn .style-dense .hand-copy{flex:1;display:grid;grid-template-columns:210px 1fr;align-items:center;gap:12px}
.design-handdrawn .style-dense .hand-copy h3{border:0;padding:0;margin:0}.design-handdrawn .style-dense .style-category,.design-handdrawn .style-dense .style-tags{grid-column:1/-1}
.design-handdrawn .style-dense .style-rank{margin:0;padding:4px 8px}.design-handdrawn .style-dense .hand-note:last-child{border:0}
.design-handdrawn .v-takeaway{border:2px dashed #91a982}
/* Editorial magazine: asymmetric headline, open columns, rules, drop caps. */
.visual-sheet.design-magazine{padding:32px 34px;background-image:linear-gradient(90deg,#ba3b2910 0 14px,transparent 14px)}
.design-magazine .mag-masthead{display:flex;justify-content:space-between;gap:16px;align-items:center;font:17px Georgia,serif;letter-spacing:1px;padding:0 0 14px;border-bottom:5px solid var(--ink);margin-bottom:22px;order:-1}
.design-magazine .mag-masthead i{height:14px;flex:1;background:repeating-linear-gradient(90deg,var(--ink) 0 2px,transparent 2px 6px)}
.design-magazine .v-header{display:flex;flex-direction:column;border-bottom:1px solid var(--ink);padding-bottom:23px!important}
.visual-sheet.design-magazine .v-kicker{align-self:flex-start;border-radius:0;padding:0;background:none;color:var(--accent);font-size:20px;letter-spacing:4px}
.visual-sheet.design-magazine .v-header h1,.visual-sheet.design-magazine .v-header h2{font-size:calc(var(--title-size) + 6px);line-height:1.12;font-weight:900;max-width:780px}
.design-magazine .v-sub{max-width:650px;font-family:Georgia,"SimSun",serif}
.design-magazine .style-section-title{font:23px Georgia,"SimSun",serif;letter-spacing:2px;padding-bottom:10px;border-bottom:1px solid var(--ink)}
.mag-columns{display:grid;grid-template-columns:1.15fr 1fr;gap:calc(var(--poster-gap) + 14px) calc(var(--poster-gap) + 22px)}
.mag-article{padding:22px 0;border-top:3px solid var(--ink);min-height:240px}
.mag-article:first-child:not(:last-child){grid-column:1/-1;display:flex;align-items:flex-start;gap:30px;min-height:194px;border-top:0}
.mag-illustration{color:var(--accent);float:right;margin:8px 12px 20px}.mag-article:first-child .mag-illustration{margin:4px 0;width:150px;flex:none;order:3}
.mag-article:first-child .mag-illustration svg{width:130px;height:130px;stroke-width:1.4}
.mag-copy{flex:1}.mag-copy h3{font-family:var(--title-font);font-size:35px;font-weight:900}
.mag-article:first-child .mag-copy h3{font-size:47px}.mag-copy .style-detail{line-height:1.65}
.mag-article .style-rank{font:50px Georgia,serif;margin-bottom:12px}
.mag-article .style-step{border-top:7px solid var(--accent);padding-top:8px;margin-bottom:14px}
.design-magazine .shape-flow .mag-columns{grid-template-columns:1fr;gap:14px}
.design-magazine .shape-flow .mag-article,.design-magazine .shape-flow .mag-article:first-child{grid-column:auto;display:flex;align-items:center;gap:22px;min-height:112px;padding:14px 0;border-top:2px solid var(--ink)}
.design-magazine .shape-flow .style-step{flex:none;font-size:26px;margin:0;padding-top:5px;border-top-width:4px}
.design-magazine .shape-flow .mag-illustration,.design-magazine .shape-flow .mag-article:first-child .mag-illustration{order:0;float:none;flex:none;width:54px;margin:0}
.design-magazine .shape-flow .mag-illustration svg,.design-magazine .shape-flow .mag-article:first-child .mag-illustration svg{width:54px;height:54px}
.design-magazine .shape-flow .mag-copy{display:grid;grid-template-columns:182px 1fr;gap:20px;align-items:center}
.design-magazine .shape-flow .mag-copy h3,.design-magazine .shape-flow .mag-article:first-child .mag-copy h3{font-size:29px;margin:0}
.design-magazine .shape-flow .style-detail{font-size:27px;line-height:1.4}
.design-magazine .shape-compare .mag-article:first-child{grid-column:auto;display:block;border-top:3px solid var(--ink);min-height:535px}
.design-magazine .shape-compare .mag-article{min-height:535px}
.design-magazine .shape-compare .mag-illustration{float:none;margin:22px 0 42px}.design-magazine .shape-compare .mag-article:first-child .mag-illustration svg{width:96px;height:96px}
.design-magazine .style-dense .mag-columns{gap:12px 26px;grid-template-columns:1fr 1fr}
.design-magazine .style-dense .mag-article,.design-magazine .style-dense .mag-article:first-child{display:block;grid-column:auto;min-height:0;padding:12px 0;border-top:2px solid var(--ink)}
.design-magazine .style-dense .mag-illustration{display:none}.design-magazine .style-dense .mag-copy h3{font-size:27px}
.design-magazine .style-dense .style-rank{float:left;font-size:32px;margin:0 12px 8px 0}
.design-magazine .style-dense .style-detail{font-size:25px;line-height:1.35}
.design-magazine .style-dense .mag-article.has-metric .style-metric{position:absolute;right:0;top:13px;text-align:right}
.design-magazine .style-dense .mag-article.has-metric .mag-copy h3,.design-magazine .style-dense .mag-article.has-metric .style-category{padding-right:90px}
.visual-sheet.design-magazine .v-takeaway{border-radius:0;border-top:5px solid var(--ink);border-bottom:1px solid var(--ink);background:none!important;padding:20px 0}
.design-magazine .v-takeaway p{font-family:Georgia,"SimSun",serif;font-style:italic}
/* Dark technical schematic: square modules, routing rails, circuit orbit. */
.visual-sheet.design-neon{border:1px solid var(--accent);background-image:linear-gradient(#90d9bd0b 1px,transparent 1px),linear-gradient(90deg,#90d9bd0b 1px,transparent 1px);background-size:28px 28px}
.design-neon .v-header{padding-right:170px!important;border-bottom:2px solid var(--accent);padding-bottom:24px!important;min-height:205px!important}
.design-neon .tech-orbit{position:absolute;right:-8px;top:15px;width:180px;height:155px;color:var(--accent)}
.visual-sheet.design-neon .v-header h1,.visual-sheet.design-neon .v-header h2{color:var(--accent);font-weight:800;letter-spacing:0}
.visual-sheet.design-neon .v-kicker{border:1px solid var(--accent);border-radius:0;background:none;color:var(--accent);padding:6px 12px}
.design-neon .style-section-title{font:22px Consolas,"Microsoft YaHei",monospace;letter-spacing:2px;border-left:12px solid var(--accent);padding-left:15px}
.tech-network{display:grid;grid-template-columns:1fr 1fr;gap:calc(var(--poster-gap) + 4px)}
.tech-module{border:1px solid #6bbaa187;clip-path:polygon(0 0,calc(100% - 16px) 0,100% 16px,100% 100%,0 100%);background:var(--soft);padding:30px 28px;min-height:282px}
.tech-port{position:absolute;top:0;left:30px;width:64px;height:4px;background:var(--accent)}
.tech-symbol{margin-bottom:22px;color:var(--accent)}
.tech-module h3{font-family:var(--title-font);font-size:35px;color:var(--accent)}
.tech-module .style-rank,.tech-module .style-step{position:absolute;right:26px;top:24px;font:28px Consolas,monospace}
.design-neon .shape-flow .tech-network{display:flex;flex-direction:column;gap:28px;border-left:2px solid var(--accent);padding-left:25px}
.design-neon .shape-flow .tech-module{display:flex;align-items:center;gap:25px;min-height:145px;padding:24px 28px;clip-path:none}
.design-neon .shape-flow .tech-module:before{content:'';position:absolute;left:-33px;top:50%;width:12px;height:12px;border:2px solid var(--accent);background:var(--paper)}
.design-neon .shape-flow .tech-symbol{margin:0;flex:none}.design-neon .shape-flow .tech-copy{flex:1;padding-right:36px}
.design-neon .shape-flow .style-step{right:16px;top:12px;font-size:22px}
.design-neon .style-dense .tech-network{display:flex;flex-direction:column;gap:9px}
.design-neon .style-dense .tech-module{display:flex;align-items:center;gap:15px;padding:13px 18px;min-height:0;clip-path:none}
.design-neon .style-dense .tech-symbol{margin:0;flex:none}.design-neon .style-dense .tech-symbol svg{width:42px;height:42px}
.design-neon .style-dense .tech-copy{flex:1;display:grid;grid-template-columns:225px 1fr;gap:6px 15px;align-items:center}
.design-neon .style-dense .style-category,.design-neon .style-dense .style-tags{grid-column:1/-1}
.design-neon .style-dense .style-rank{position:static;font-size:26px;flex:none}
.visual-sheet.design-neon .v-takeaway{border:1px solid var(--accent);border-radius:0;box-shadow:7px 7px 0 #6de4b525}
.design-neon .style-tags span{border:1px solid #6bbaa187}
/* Playful collage: yellow canvas, torn sheets, tape, stamps, punchy lettering. */
.visual-sheet.design-collage{border:3px solid var(--ink);background-image:radial-gradient(#17213d1c 1px,transparent 1px);background-size:12px 12px}
.design-collage .v-header{padding-right:155px!important;padding-bottom:20px!important}
.visual-sheet.design-collage .v-header h1,.visual-sheet.design-collage .v-header h2{font-weight:900;font-size:calc(var(--title-size) + 6px);letter-spacing:-2px;transform:rotate(-1deg);text-shadow:3px 4px #fff9ef}
.design-collage .v-kicker{transform:rotate(-3deg);box-shadow:4px 4px 0 var(--ink);border:2px solid var(--ink);border-radius:0!important}
.collage-stamp{position:absolute;right:0;top:4px;display:grid;place-items:center;width:122px;height:122px;border:4px solid var(--ink);border-radius:50%;transform:rotate(12deg);background:#e2eeff;box-shadow:6px 6px 0 var(--ink);font:32px "SimHei",sans-serif;text-align:center;line-height:1.05;color:#183cb1}
.collage-star{position:absolute;right:135px;top:83px;font-size:49px;color:#b83a29}
.design-collage .style-section-title{display:inline-block;background:var(--ink);color:var(--paper);padding:7px 15px;transform:rotate(-1deg);font-size:25px}
.cut-board{display:grid;grid-template-columns:1fr 1fr;gap:calc(var(--poster-gap) + 10px) calc(var(--poster-gap) + 8px);padding:15px 8px}
.cut-paper{padding:28px 24px 23px;border:2px solid var(--ink);box-shadow:7px 7px 0 var(--ink);min-height:295px;transform:rotate(-1deg);background:#fffdf3;color:#18213b}
.cut-paper:nth-child(even){transform:rotate(1deg)}.cut-paper.paper-1{background:#dbe9ff}.cut-paper.paper-2{background:#ffd7db}.cut-paper.paper-3{background:#d7edc3}
.paper-tape{position:absolute;top:-12px;left:calc(50% - 40px);width:80px;height:27px;background:#f7f6eaa8;border-left:1px dashed #18213b66;border-right:1px dashed #18213b66;transform:rotate(4deg)}
.cut-illustration{float:right;width:100px;height:100px;margin:0 0 10px 12px}.cut-illustration svg{width:100%;height:100%}
.cut-copy h3{font-family:var(--title-font);font-weight:900;font-size:36px;text-decoration:underline;text-decoration-color:#192da95c;text-decoration-thickness:9px;text-underline-offset:-3px}
.cut-copy .style-detail{color:#18213b}.cut-paper .style-category{color:#183cb1}
.cut-paper .style-rank,.cut-paper .style-step{display:inline-block;background:#fffdf3;border:2px solid #18213b;box-shadow:3px 3px 0 #18213b;padding:3px 10px;color:#183cb1;margin-bottom:10px}
.design-collage .style-dense .cut-board{gap:21px;padding:12px 8px}
.design-collage .style-dense .cut-paper{padding:16px 18px;min-height:0;box-shadow:4px 5px 0 var(--ink)}
.design-collage .style-dense .cut-illustration{width:45px;height:45px;margin:0 0 6px 8px}
.design-collage .style-dense .style-rank{font-size:24px;float:left;margin:0 12px 8px 0}
.design-collage .style-dense .cut-copy h3{font-size:26px}.design-collage .style-dense .style-detail{font-size:25px;line-height:1.35}
.design-collage .shape-rank.style-dense .cut-board{grid-template-columns:1fr;gap:11px;padding:12px 8px}
.design-collage .shape-rank.style-dense .cut-paper{display:flex;align-items:center;gap:14px;padding:12px 16px;transform:rotate(-.35deg)}
.design-collage .shape-rank.style-dense .cut-paper:nth-child(even){transform:rotate(.35deg)}
.design-collage .shape-rank.style-dense .paper-tape{left:25px;width:45px;height:18px;top:-5px}
.design-collage .shape-rank.style-dense .style-rank{float:none;flex:none;font-size:24px;margin:0;padding:4px 7px}
.design-collage .shape-rank.style-dense .cut-copy{display:grid;grid-template-columns:255px 1fr;gap:15px;align-items:center;flex:1;min-width:0}
.design-collage .shape-rank.style-dense .cut-name h3{font-size:25px;line-height:1.25;text-decoration:none;margin:0 0 5px}
.design-collage .shape-rank.style-dense .cut-meta{display:flex;gap:4px;flex-wrap:wrap;align-items:center}
.design-collage .shape-rank.style-dense .cut-meta .style-category{font-size:14px;line-height:1.2;margin:0;border-right:1px solid #18213b55;padding-right:5px}
.design-collage .shape-rank.style-dense .style-tags{font-size:16px;margin:0;gap:4px}
.design-collage .shape-rank.style-dense .style-tags span{padding:2px 4px}
.design-collage .shape-rank.style-dense .cut-illustration{order:3;float:none;flex:none;width:40px;height:40px;margin:0}
.design-collage .shape-rank.style-dense .style-detail{font-size:25px;line-height:1.3}
.visual-sheet.design-collage.v-ranking-dense{gap:16px}
.visual-sheet.design-collage.v-ranking-dense .v-header h1,.visual-sheet.design-collage.v-ranking-dense .v-header h2{font-size:48px;letter-spacing:-1px}
.design-collage.v-ranking-dense .v-header{padding-right:116px!important;padding-bottom:12px!important}
.design-collage.v-ranking-dense .collage-stamp{width:90px;height:90px;font-size:25px}
.design-collage.v-ranking-dense .collage-star{right:105px;top:60px;font-size:35px}
.design-collage.v-ranking-dense .v-kicker{font-size:18px;margin-bottom:12px}
.design-collage.v-ranking-dense .shape-rank .cut-paper{padding-top:9px;padding-bottom:9px}
.visual-sheet.design-collage .v-takeaway{border:2px solid var(--ink);border-radius:0;box-shadow:5px 5px 0 var(--ink);transform:rotate(-.5deg)}
/* Data dossier: explicit columns, category rails, diagram legend, calm precision. */
.visual-sheet.design-data{padding-left:38px;border-left:12px solid var(--accent);background-image:linear-gradient(0deg,transparent 0 97%,#15447308 97%);background-size:100% 36px}
.design-data .v-header{border-bottom:3px solid var(--accent);padding-right:125px!important;padding-bottom:26px!important}
.data-mark{position:absolute;right:0;top:12px;width:105px;display:flex;flex-wrap:wrap;gap:9px;color:var(--accent);font-size:26px}
.data-mark i{display:block;height:8px;width:100%;background:var(--accent)}
.visual-sheet.design-data .v-kicker{border-radius:0;background:var(--soft);color:var(--accent);letter-spacing:3px;font-size:19px}
.visual-sheet.design-data .v-header h1,.visual-sheet.design-data .v-header h2{font-weight:850;letter-spacing:0}
.data-table-head{display:grid;grid-template-columns:42% 1fr;gap:8px 18px;padding:17px 22px;background:var(--accent);color:var(--paper);font-size:23px;line-height:1.3}
.data-table-head>b{grid-column:1/-1;font-size:28px;padding-bottom:8px}
.data-records{border:1px solid #aebfd1;border-top:0}
.data-record{display:flex;align-items:center;gap:var(--poster-gap);padding:28px 22px;border-bottom:1px solid #b8c7d6;min-height:156px;background:var(--paper)}
.data-record:nth-child(even){background:var(--soft)}.data-record:last-child{border-bottom:0}
.data-symbol{width:44px;flex:none;color:var(--accent)}.data-key{width:245px;flex:none}.data-value{flex:1;min-width:0}
.data-key h3{font-size:30px;margin:0}.data-key .style-category{border-left:4px solid var(--accent);padding-left:9px}
.data-record .style-rank,.data-record .style-step{font-family:Consolas,monospace;font-size:28px}
.design-data .style-dense .data-record{min-height:0;padding:8px 15px;gap:12px}
.design-data .style-dense .data-key{width:220px;display:grid;grid-template-columns:62px 1fr;gap:8px;align-items:center}.design-data .style-dense .data-key h3{font-size:25px;margin:0}
.design-data .style-dense .data-key:not(:has(.style-category)){display:block}
.design-data .style-dense .style-category{font-size:14px;line-height:1.1;margin-bottom:3px}
.design-data .style-dense .data-key .style-category{margin:0;padding:0;border:0}
.design-data .style-dense .style-detail{font-size:24px;line-height:1.3}
.design-data .style-dense .data-symbol{width:32px}.design-data .style-dense .data-symbol svg{width:32px;height:32px}
.design-data .style-dense .data-table-head{font-size:19px;padding:13px 18px}
.design-data .style-dense .data-table-head>b{font-size:23px}
.visual-sheet.design-data .v-takeaway{border-radius:0;border-top:3px solid var(--accent);background:var(--soft)}
/* Large directories get editorial columns, never dropped objects or tiny type. */
.style-many .style-detail{font-size:24px;line-height:1.3}
.design-magazine .style-many .mag-columns{grid-template-columns:repeat(3,1fr);gap:14px 24px}
.design-magazine .style-many .mag-copy h3{font-size:25px}
.design-magazine .style-many .style-detail{font-size:24px;line-height:1.3}
.design-handdrawn .style-many .hand-notes{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;padding:8px;border:0;background:none}
.design-handdrawn .style-many .hand-note{display:block;padding:15px;border:1px solid #93a989;border-radius:12px;background:#fffdf3}
.design-handdrawn .style-many .hand-art{float:right;width:38px;height:38px;margin:0 0 6px 8px}
.design-handdrawn .style-many .hand-copy{display:block}
.design-handdrawn .style-many .hand-copy h3{font-size:25px;margin-bottom:8px}
.design-handdrawn .style-many .style-category{font-size:16px;margin-bottom:6px}
.design-handdrawn .style-many .style-detail{font-size:24px;line-height:1.3}
.design-neon .style-many .tech-network{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.design-neon .style-many .tech-module{display:block;padding:17px 18px;min-height:0}
.design-neon .style-many .tech-copy{display:block}
.design-neon .style-many .tech-symbol{float:right;margin:0 0 7px 9px}
.design-neon .style-many .tech-symbol svg{width:32px;height:32px}
.design-neon .style-many .style-category{font-size:16px}.design-neon .style-many .style-detail{font-size:24px;line-height:1.3}
.design-collage .style-many .cut-board{grid-template-columns:repeat(3,1fr);gap:20px 18px}
.design-collage .style-many .cut-paper{padding:15px 16px}
.design-collage .style-many .cut-illustration{display:none}
.design-collage .style-many .style-category{font-size:16px}
.design-collage .style-many .style-detail{font-size:24px;line-height:1.3}
/* Numerical tables and real photographs share semantic structure, not facts. */
.style-price{border:2px solid var(--accent);background:var(--paper)}
.price-head,.price-row{display:grid;grid-template-columns:1.35fr repeat(3,1fr);gap:8px;text-align:center;align-items:center;padding:23px 16px;border-bottom:1px solid var(--accent)}
.price-head{background:var(--soft);font-size:23px;line-height:1.4}.price-row{min-height:143px}.price-row:last-child{border-bottom:0}
.price-row h3{font-size:28px;color:var(--ink)}.price-row>b{font-size:34px;color:var(--ink)}
.magazine-price{border:0;border-top:5px solid var(--ink);border-bottom:3px solid var(--ink)}
.neon-price .price-row>b{font-family:Consolas,monospace;color:var(--accent)}
.collage-price{box-shadow:7px 8px 0 var(--ink);transform:rotate(-.4deg)}
.handdrawn-price{border-radius:24px;overflow:hidden}.handdrawn-price .price-row{border-bottom-style:dashed}
.style-preserved .nutrition-sheet,.style-preserved .media-card,.style-preserved .fruit-card{background:var(--soft);border-color:var(--accent)}
.style-preserved .nutrition-sheet h3,.style-preserved .nutrition-values b,.style-preserved .nutrition-values span,.style-preserved .nutrition-unit,.style-preserved .nutrition-scope,.style-preserved .fruit-card h3,.style-preserved .fruit-card p,.style-preserved .fruit-badge{color:var(--ink)}
.design-neon .style-preserved .media-card h3,.design-neon .style-preserved .media-card p{color:var(--ink)}
/* Overflow reflows into wide readable rows without deleting model content. */
.visual-sheet.adaptive-poster{gap:14px}
.visual-sheet.adaptive-poster .v-header{min-height:0!important;padding-bottom:14px!important}
.visual-sheet.adaptive-poster .v-header h1,.visual-sheet.adaptive-poster .v-header h2{font-size:48px}
.visual-sheet.adaptive-poster .v-kicker{font-size:18px;margin-bottom:9px}
.visual-sheet.adaptive-poster .v-sub{font-size:23px;margin-top:9px}
.visual-sheet.adaptive-poster .v-takeaway{padding:13px 17px}
.visual-sheet.adaptive-poster .v-takeaway p{font-size:23px}
.style-adaptive .style-section-title{font-size:23px;margin-bottom:12px}
.style-adaptive .hand-notes,.style-adaptive .mag-columns,.style-adaptive .tech-network,.style-adaptive .cut-board{display:flex!important;flex-direction:column;gap:9px!important}
.style-adaptive .style-item,.style-adaptive .style-item:first-child{min-height:0!important;padding:10px 15px!important;display:flex!important;align-items:center;gap:13px;transform:none!important}
.style-adaptive .hand-art,.style-adaptive .mag-illustration,.style-adaptive .tech-symbol,.style-adaptive .cut-illustration{display:block!important;float:none!important;order:0!important;width:40px!important;height:40px!important;flex:none;margin:0!important}
.style-adaptive .style-item svg{width:40px!important;height:40px!important}
.style-adaptive .hand-copy,.style-adaptive .mag-copy,.style-adaptive .tech-copy,.style-adaptive .cut-copy{display:grid!important;grid-template-columns:minmax(0,36%) minmax(0,1fr)!important;flex:1;min-width:0;gap:5px 16px!important;align-items:center}
.style-adaptive .style-item h3,.style-adaptive .style-detail{font-size:var(--adaptive-font,26px)!important;line-height:1.35!important;margin:0!important}
.style-adaptive .style-category,.style-adaptive .style-tags{grid-column:1/-1}
.style-adaptive .style-rank,.style-adaptive .style-step{position:static!important;float:none!important;margin:0!important;flex:none;font-size:25px}
.style-adaptive .data-key{width:32%!important}
.style-adaptive .style-item .cut-name,.style-adaptive .style-item .cut-description{min-width:0}
'''
