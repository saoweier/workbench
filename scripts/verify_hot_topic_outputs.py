"""Verify saved real API outputs and create the visual regression report.

No model calls, posting, approval, or changes to live content.
"""
import sys,json,hashlib,html,unicodedata
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.services.visual_content import illustrated_page_html,VISUAL_FIT_JS,VisualSpec
from app.services.renderer import PlaywrightRenderer,check_layout
from app.services.profile_store import engineering_default
from app.services.poster_styles import display_label
from playwright.sync_api import sync_playwright
OUT=ROOT/'docs/test-artifacts/hot-topic-real-20261006'
CASES=[('book_top10','新书 TOP10'),('movie_top10_release','热映作品 TOP10'),
 ('game_format_final','游戏赛制'),('life_sleep_fixed','生活睡眠'),
 ('tech_diplay','科技解读'),
 ('meme_regression_verified','历史梗回归')]
results=[];checks=0
def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def check(condition,message):
 global checks
 if not condition:raise AssertionError(message)
 checks+=1
def clean(value):return ''.join(c for c in value if not c.isspace() and unicodedata.category(c)!='Cf')
with sync_playwright() as pw:
 browser=pw.chromium.launch(headless=True,executable_path=PlaywrightRenderer.resolve_executable())
 page=browser.new_page(viewport={'width':1080,'height':1440})
 for key,label in CASES:
  record=read(key+'-request.json');run=read(key+'-run.json');content=read(key+'-content.json')
  check(run['state']=='succeeded',key+' run incomplete')
  check(all(c['run_mode']=='real' and c['model_id']=='deepseek-flash' for c in run['provider_calls']),key+' must use real configured model')
  rid=next(j['output_refs']['public']['revision_id'] for j in run['jobs'] if j['stage']=='compose')
  revision=next(r for r in content['revisions'] if r['revision_id']==rid)
  audit=next(j['output_refs']['report'] for j in run['jobs'] if j['stage']=='audit')
  check(audit['passed'] and not any(i['severity']=='error' for i in audit['issues']),key+' audit failed')
  expected=record['case']['pages'];platform_results=[]
  check({p['platform'] for p in revision['platforms']}=={'douyin','xiaohongshu'},key+' platform coverage')
  for variant in revision['platforms']:
   platform=variant['platform'];pages=variant['pages'];profile=engineering_default(platform)
   check(len(pages)==expected and variant['page_count']==expected,key+' page count')
   check(len(variant['artifacts'])==expected,key+' actual images missing')
   check(not any(i.level=='error' for i in check_layout(variant,profile)),key+' structural layout')
   items=[i for pg in pages for i in pg['visual']['items']]
   if key.startswith(('book_','movie_')):
    check([clean(i['label']) for i in items]==[clean(n) for n in record['case']['expected_names']],key+' full names/order')
    check([i['rank'] for i in items]==list(range(1,11)),key+' complete TOP10')
    check([len(p['visual']['items']) for p in pages]==([10] if expected==1 else [5,5]),key+' row distribution')
   if key.startswith('movie_'):
    for i,minutes in zip(items,[94,140,132,106,102,106,88,276,123,100]):
     check(str(minutes)+'分钟' in i['detail'],key+' runtime '+i['label'])
     check(len(i['detail'])<=40,key+' explicit detail cap')
    check(sum(bool(p['footnote']) for p in pages)==1,key+' source repeats')
    check(not any(i.get('tags') for i in items),key+' unnecessary tags')
   if key=='life_sleep_fixed':
    check('7' in ''.join(str(p) for p in pages),'sleep core numeric answer absent')
    check('18' in json.dumps(variant,ensure_ascii=False) and '60' in json.dumps(variant,ensure_ascii=False),'sleep adult scope absent')
   if key=='meme_regression_verified':
    check([len(p['visual']['items']) for p in pages]==[3,4],'meme complete examples')
    check('AI短剧' in ''.join(i['detail']+i['label'] for i in pages[0]['visual']['items']),'meme origin must be in main image')
   if key=='work_tutorial_release':check(all(len(i['detail'])<=45 for i in items),'tutorial explicit card limit')
   fits=[]
   for pg,artifact in zip(pages,sorted(variant['artifacts'],key=lambda a:a['page_index'])):
    path=OUT/key/platform/f"page-{pg['index']:02d}.png"
    check(hashlib.sha256(path.read_bytes()).hexdigest()==artifact['sha256'],key+' PNG hash')
    check(Image.open(path).size==(artifact['width'],artifact['height']),key+' PNG dimensions')
    doc,_=illustrated_page_html(pg,profile,platform,pg['index'],'sans-serif',form=revision['brief']['creative_brief']['form'])
    page.set_content(doc);page.evaluate('document.fonts.ready');fit=page.evaluate(VISUAL_FIT_JS);fits.append(fit)
    check(fit>=.74,key+' readability '+str(fit))
    text=page.locator('.diagram-content').inner_text();spec=VisualSpec.model_validate(pg['visual'])
    check(all(display_label(i,n,spec.kind) in text and i.detail in text for n,i in enumerate(spec.items)),key+' actual DOM retains full card facts')
    check(page.evaluate("() => {const d=document.querySelector('.diagram-content').getBoundingClientRect(),h=document.querySelector('.v-header').getBoundingClientRect(),t=document.querySelector('.v-takeaway').getBoundingClientRect();return d.top>=h.bottom-1 && d.bottom<=t.top+1;}"),key+' overlap')
   platform_results.append({'platform':platform,'pages':len(pages),'readability_min':round(min(fits),4),'images':[str((OUT/key/platform/f"page-{p['index']:02d}.png").relative_to(OUT)).replace('\\','/') for p in pages]})
  results.append({'key':key,'label':label,'display_id':content['display_id'],'content_id':content['id'],'revision_id':rid,'revision_version':revision['version'],'topic':content['topic'],'audit':audit,'platforms':platform_results})
 browser.close()
negative=read('tech_explainer_fixed-run.json')
check(negative['state']=='failed' and negative['blocked_stage']=='research','unreadable/mismatched evidence must stop')
tutorial=read('work_tutorial_release-run.json')
check(tutorial['state']=='failed' and tutorial['blocked_stage']=='planning','tutorial planning failure must be disclosed')
report={'checks':checks,'real_cases':len(results),'platform_outputs':sum(len(r['platforms']) for r in results),'png_pages':sum(p['pages'] for r in results for p in r['platforms']),'cases':results,'failed_cases':[{'key':'work_tutorial_release','state':tutorial['state'],'stage':tutorial['blocked_stage'],'error':tutorial['error']}],'negative_case':{'key':'tech_explainer_fixed','state':negative['state'],'error':negative['error']},'limits':['按HotPush快照验证，不代表独立核验聚合源所有事实。','未测试外部账号发布。','原始失败与修订均保留；并非全部首次生成通过。']}
(OUT/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',23)
thumbs=[]
for r in results:
 for source in r['platforms'][0]['images']:
  image=Image.open(OUT/source).convert('RGB');image.thumbnail((420,560))
  thumbs.append((r,image,source))
sheet=Image.new('RGB',(1800,((len(thumbs)+3)//4)*640),'#e9edf2');draw=ImageDraw.Draw(sheet)
for n,(r,im,source) in enumerate(thumbs):
 x=(n%4)*450+15;y=(n//4)*640+10
 draw.text((x,y),r['display_id']+' '+r['label'],font=font,fill='#17213d');sheet.paste(im,(x,y+40))
sheet.save(OUT/'contact-sheet.jpg',quality=94)
cards=''
for r in results:
 images=''.join(f'<a href="{html.escape(src)}"><img src="{html.escape(src)}"></a>' for src in r['platforms'][0]['images'])
 issues='；'.join(i['problem'] for i in r['audit']['issues']) or '无审核问题'
 cards+=f'<section><h2>{r["display_id"]} · {html.escape(r["label"])}</h2><p>{html.escape(r["topic"])}</p><p>版本{r["revision_version"]} · 两平台各{r["platforms"][0]["pages"]}页 · <a href="http://127.0.0.1:8000/views/ReviewPreview.html?content={r["content_id"]}">打开工作台预览</a></p><div class="images">{images}</div><details><summary>审核与测试记录</summary><p>{html.escape(r["audit"]["summary"])}</p><p>{html.escape(issues)}</p><a href="{r["key"]}-run.json">原始运行记录</a></details></section>'
doc=f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>多题材真实生成回归</title><style>body{{margin:0;background:#edf0f4;color:#18213b;font:16px/1.7 "Microsoft YaHei",sans-serif}}main{{max-width:1180px;margin:auto;padding:32px}}section{{background:white;padding:24px;margin:24px 0;border-radius:16px}}h1{{font-size:34px}}.images{{display:flex;gap:18px;flex-wrap:wrap}}img{{width:min(480px,100%);height:auto;border:1px solid #ddd}}a{{color:#215fe3}}details{{margin-top:16px}}@media(max-width:700px){{main{{padding:12px}}section{{padding:14px}}img{{width:100%}}}}</style><main><h1>多题材真实生成回归</h1><p>2026-10-06 · deepseek-flash · 测试6个当日HotPush选题/榜单 + 1个历史梗回归。最终6个案例通过，生成12个平台版本、20张实际PNG，{checks}项输出检查通过。2Do教程未通过；另测Vue3 UIKit资料不足负例，已拦截。</p><p>榜单按HotPush聚合返回顺序整理，不能宣称评分/销量/全网热度排行。所有修订与原始失败保留；此次没有外部发布。</p>{cards}<section><h2>没有通过的记录</h2><p>2Do教程已修复首页图解限制、长步骤排版、修改时证据丢失；最终重做仍因规划引用不存在的主张编号C18而停止，不能标成通过。旧版已出图但同步属性描述过宽，未将它充作最终通过案例。</p><p>Vue3 UIKit 原网页仅返回占位文本，检索引文未与原文匹配，阻止生成。旧2Do完整介绍把高级筛选表达式和解释挤进一张卡，超限后停止；原始失败记录保留。</p><p>引用匹配只证明原文一致，不证明来源独立或可靠。此次仅检查生成、调整、审核、实际出图，不含外部发布。</p><a href="verification.json">完整检查结果</a></section></main></html>'
(OUT/'gallery.html').write_text(doc,encoding='utf-8')
print(json.dumps({k:report[k] for k in ['checks','real_cases','platform_outputs','png_pages']},ensure_ascii=False))
