"""Original cream-paper guide layout. Fixed local SVG; model text is escaped."""
import html,re

DOODLE='''<svg class="fg-doodle" viewBox="0 0 150 130" aria-hidden="true"><g fill="none" stroke="#356a52" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M26 101Q18 63 34 34M25 83Q4 68 15 60Q32 60 25 83M26 66Q46 44 51 54Q52 67 26 66M30 48Q14 26 27 24Q39 27 30 48" fill="#bccb9a"/><rect x="58" y="41" width="72" height="66" rx="17" fill="#e1ead8"/><path d="M88 41V27"/><circle cx="88" cy="22" r="5" fill="#f4c45a"/><rect x="67" y="52" width="53" height="33" rx="13" fill="#275344"/><path d="M78 65q3-6 7 0M102 65q3-6 7 0M88 73q5 5 10 0" stroke="#fffaec"/><path d="M70 105l-5 13M117 105l6 13M58 63l-10 7M131 62l9 8"/><circle cx="93" cy="95" r="3" fill="#efb955"/><path d="M128 16l3 7 8 1-6 5 2 8-7-5-7 5 2-8-6-5 8-1z" fill="#f4d77c"/></g></svg>'''

def render_guide(spec,icon):
    e=lambda v:html.escape(str(v),quote=True)
    headings=spec.title.split('｜')
    table=len(headings)==3 and all(re.fullmatch(r'¥\d+(?:\.\d+)?(?:｜¥\d+(?:\.\d+)?){2}',i.detail) for i in spec.items)
    header=('<div class="fg-columns"><span>模型 / 时段</span>'+''.join('<b>'+e(h)+'</b>' for h in headings)+'</div>') if table else '<h3 class="fg-panel-title">'+e(spec.title)+'</h3>'
    rows=[]
    for n,item in enumerate(spec.items):
        badge=f'<b class="fg-badge">{item.rank or n+1}</b>'
        metric=f'<aside><strong>{e(item.metric_text)}</strong><small>{e(item.metric_label)}</small></aside>' if item.metric_text else ''
        if table:
            copy='<div class="fg-price-label"><h3>'+e(item.label)+'</h3></div>'+''.join('<strong class="fg-price">'+e(v)+'</strong>' for v in item.detail.split('｜'))
        else:
            copy=f'<div class="fg-copy"><h3>{e(item.label)}</h3><p>{e(item.detail)}</p></div>{metric}'
        rows.append(f'<article class="fg-row">{badge}<span class="fg-icon">{icon(item.icon,44)}</span>{copy}</article>')
    return '<section class="fg-panel '+('fg-table' if table else '')+' fg-count-'+str(len(rows))+'">'+header+''.join(rows)+'</section>'

CSS='''
.visual-sheet{background:#fff8e9;color:#253d30;gap:20px;border:2px solid #d7dec6;border-radius:26px;padding:36px 32px;box-shadow:0 0 0 12px #fffdf5}
.page{background:#e9eddd}.v-brand{display:none}.v-header{padding-right:155px;min-height:205px;position:relative}.v-header h1,.v-header h2{font-size:72px;line-height:1.2;letter-spacing:-1px;color:#317451}
.v-kicker{font-size:22px;padding:6px 14px;background:#f6d575;color:#3c4e35;border-radius:12px;border:1.5px solid #bba665;margin-bottom:13px}
.v-sub{font-size:26px;line-height:1.5;color:#3d5145;margin-top:15px}.v-visual{position:relative}.diagram-content{gap:0;width:864px}
.fg-doodle{position:absolute;right:0;top:20px;width:154px;height:150px}.fg-panel{width:100%;border:2px solid #8fa387;border-radius:29px;padding:10px 22px 16px;background:#fffbee}
.fg-panel-title{font-size:30px;line-height:1.4;text-align:center;color:#395e47;padding:16px 0;border-bottom:2px dashed #d8d7c0}
.fg-row{display:flex;gap:18px;align-items:center;min-height:140px;padding:22px 0;border-bottom:2px dashed #dddac4}.fg-row:last-child{border-bottom:0}
.fg-badge{width:46px;min-width:46px;height:46px;display:flex;align-items:center;justify-content:center;background:#3c7b58;color:#fff9eb;font-size:28px;border-radius:50%;box-shadow:inset 0 0 0 2px #75a781}
.fg-icon{display:flex;color:#487c63;width:48px;min-width:48px}.fg-copy{flex:1;min-width:0}.fg-copy h3{font-size:31px;line-height:1.35;margin:0 0 10px;color:#243e30}.fg-copy p{font-size:29px;line-height:1.52;margin:0;color:#435348;overflow-wrap:anywhere}
.fg-row aside{width:130px;text-align:center;border-left:1px solid #d3d8c4;padding-left:10px}.fg-row aside strong{font-size:28px}.fg-row aside small{display:block;font-size:20px}
.fg-columns{display:grid;grid-template-columns:1.3fr repeat(3,1fr);gap:8px;text-align:center;align-items:center;font-size:23px;line-height:1.4;padding:20px 0;border-bottom:2px dashed #d8d7c0}.fg-columns span{color:#54735c}.fg-columns b{background:#e8edd8;border-radius:10px;padding:12px 1px}
.fg-table .fg-row{display:grid;grid-template-columns:1.3fr repeat(3,1fr);gap:8px;min-height:149px;text-align:center}.fg-table .fg-badge,.fg-table .fg-icon{display:none}.fg-price-label h3{font-size:28px;line-height:1.4;color:#315943}.fg-price{font-size:34px;line-height:1.3;color:#243b2c;font-weight:800}
.v-takeaway{border-radius:20px;background:#e1e8cf;border:1.5px solid #b9c7a6;color:#2f563d;padding:22px;gap:14px}.v-takeaway p{font-size:28px;line-height:1.45}.v-body p{font-size:25px;line-height:1.4;color:#4a5d4e}.v-footer{font-size:18px;line-height:1.3;color:#71816a;padding-top:12px}
.fg-count-8 .fg-row,.fg-count-9 .fg-row,.fg-count-10 .fg-row,.fg-count-11 .fg-row,.fg-count-12 .fg-row,.fg-count-13 .fg-row,.fg-count-14 .fg-row,.fg-count-15 .fg-row,.fg-count-16 .fg-row{min-height:70px;padding:12px 0;gap:14px}.fg-count-10 .fg-copy,.fg-count-9 .fg-copy,.fg-count-8 .fg-copy{display:grid;grid-template-columns:.75fr 1fr;gap:12px;align-items:center}.fg-count-10 .fg-copy h3,.fg-count-9 .fg-copy h3,.fg-count-8 .fg-copy h3{margin:0;font-size:26px}.fg-count-10 .fg-copy p,.fg-count-9 .fg-copy p,.fg-count-8 .fg-copy p{font-size:25px;line-height:1.3}
.compact .fg-row{min-height:100px;padding-top:12px;padding-bottom:12px}.compact .fg-copy p{font-size:27px}.compact .fg-count-10 .fg-row{min-height:62px;padding-top:9px;padding-bottom:9px}
'''
