"""Cross content forms with every style; verify facts and browser geometry."""
import os,sys,tempfile,json
from pathlib import Path
from copy import deepcopy
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-style-families-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'))
from app.services.template_packages import builtins,fingerprint,TemplateStyle
from app.services.template_examples import example_page,example_form
from app.services.content_recipes import apply_recipe
from app.services.content_forms import build_brief
from app.services.visual_content import illustrated_page_html,VISUAL_FIT_JS,VisualSpec
from app.services.profile_store import engineering_default
from app.services.renderer import PlaywrightRenderer
from app.services.fruit_content import fruit_index
from app.services.poster_styles import display_label
from playwright.sync_api import sync_playwright
passed=0
def check(name,condition):
 global passed
 assert condition,name
 passed+=1;print('PASS '+name,flush=True)
packages={id:{**p.model_dump(mode='json'),'package_version':fingerprint(p)} for id,p in builtins().items()}
samples={'common':example_page('friendly_guide',packages['friendly_guide'],mode='common'),
         'comparison':example_page('editorial',packages['editorial']),
         'flow':example_page('illustrated',packages['illustrated']),
         'ranking':example_page('rank_cards',packages['rank_cards']),
         'directory':example_page('category_table',packages['category_table'])}
samples['ranking']['visual']['items'][0].update(metric_text='101次',metric_label='演示统计',metric_source_id='DEMO',tags=['演示标签'])
fruits=fruit_index()
photo=deepcopy(samples['common']);photo['heading']='两种水果的吃法';photo['visual'].update(kind='photo',title='具体品种与吃法',items=[{'label':fruits[id]['name'],'detail':'洗净后切块食用，保留具体品种与图片。','icon':'fruit','photo_id':id} for id in ['apple','orange']])
samples['photo']=photo
nutrition=deepcopy(photo);nutrition['heading']='水果营养参数';nutrition['visual'].update(kind='nutrition',title='每100克生鲜可食部');samples['nutrition']=nutrition
price=deepcopy(samples['common']);price['heading']='2万Token费用 · 演示';price['visual'].update(kind='cover',title='输入｜输出｜缓存',items=[{'label':f'演示模型{n}','detail':'¥0.02｜¥0.08｜¥0.0004','icon':'chart'} for n in range(1,5)])
price['layout']='cover';samples['price']=price
for p in packages.values():
 for topic,form,count in [('Skill TOP10，1页','ranking',10),('15个工具分类速查，1页','directory',15),('AI是什么，1页','explainer',None)]:
  b=apply_recipe(build_brief(topic=topic),template_id=p['id'])
  check(p['id']+' preserves requested form '+form,b.form==form and b.page_max==1 and (count is None or (b.rank_count or b.item_count)==count))
check('old snapshots default to historical layout',TemplateStyle.model_validate({'decoration':'rich','heading_font':'handwritten'}).design_family=='legacy')
layouts=set()
with sync_playwright() as pw:
 browser=pw.chromium.launch(headless=True,executable_path=PlaywrightRenderer.resolve_executable());page=browser.new_page(viewport={'width':1080,'height':1440})
 for id,p in packages.items():
  for name,base in samples.items():
   pg=deepcopy(base);pg['visual'].update(presentation=p['renderer'],presentation_version=p['version'],template_style=p['style'],template_package_id=p['id'],template_package_version=p['package_version'])
   VisualSpec.model_validate(pg['visual'])
   doc,_=illustrated_page_html(pg,engineering_default('douyin'),'douyin',1,'sans-serif',form=example_form(pg))
   page.set_content(doc);page.evaluate('document.fonts.ready');fit=page.evaluate(VISUAL_FIT_JS)
   check(id+' / '+name+' stays readable',fit>=.74)
   check(id+' / '+name+' fits between header and takeaway',page.evaluate("() => {const d=document.querySelector('.diagram-content').getBoundingClientRect(),h=document.querySelector('.v-header').getBoundingClientRect(),t=document.querySelector('.v-takeaway').getBoundingClientRect();return d.top>=h.bottom-1 && d.bottom<=t.top+1;}"))
   check(id+' / '+name+' preserves every label and detail',all(i['label'] in page.locator('.diagram-content').inner_text() and (True if name=='nutrition' else i['detail'] in page.locator('.diagram-content').inner_text() if name!='price' else all(v in page.locator('.diagram-content').inner_text() for v in i['detail'].split('｜'))) for i in pg['visual']['items']))
   if name=='common':
    check(id+' ordinary explanation is never ranked',page.locator('.style-rank').count()==0 and page.locator('.style-item').count()==4)
    layouts.add(page.locator('.style-layout>div').last.get_attribute('class'))
   if name=='ranking':
    check(id+' retains all ten ranks',page.locator('.style-rank').all_text_contents()==[f'{n:02d}' for n in range(1,11)])
    check(id+' preserves sourced metric and tags',page.locator('.style-metric').inner_text()=='101次\n演示统计' and page.locator('.style-metric').get_attribute('data-source')=='DEMO' and page.locator('.style-tags').inner_text()=='演示标签')
   if name=='flow':check(id+' preserves four ordered steps',page.locator('.style-step').all_text_contents()==['01','02','03','04'])
   if name in {'photo','nutrition'}:check(id+' keeps actual local fruit pictures',page.locator('.fruit-photo').count()==2)
   if name=='nutrition':check(id+' preserves actual nutrient table and unit',page.locator('.nutrition-values b').count()==12 and '每 100 克' in page.locator('.diagram-content').inner_text())
   if name=='price':check(id+' retains all twelve cost cells',page.locator('.price-row>b').count()==12)
  pg=deepcopy(samples['common']);pg['visual']['items'][0]['label']='<b>x</b>';pg['visual']['template_style']=p['style']
  doc,_=illustrated_page_html(pg,engineering_default('douyin'),'douyin',1,'sans-serif')
  check(id+' escapes supplied content','&lt;b&gt;x&lt;/b&gt;' in doc and '<b>x</b>' not in doc)
 # Real model output: long book names plus three tags per row. Simple demo
 # labels did not expose this one-page overflow.
 fixtures=ROOT/'backend/tests/fixtures'
 real_pages=[('books',json.loads((fixtures/'ranking_book_top10.json').read_text(encoding='utf-8')))]
 real_pages += [('movies',p) for p in json.loads((fixtures/'ranking_movie_top10.json').read_text(encoding='utf-8'))]
 real_pages += [('movie_metrics',p) for p in json.loads((fixtures/'ranking_movie_metrics.json').read_text(encoding='utf-8'))]
 real_pages += [('tutorial',p) for p in json.loads((fixtures/'work_tutorial_real.json').read_text(encoding='utf-8'))]
 for name,pg in real_pages:
  for platform in ['douyin','xiaohongshu']:
   doc,_=illustrated_page_html(pg,engineering_default(platform),platform,pg['index'],'sans-serif',form='ranking')
   page.set_content(doc);page.evaluate('document.fonts.ready');fit=page.evaluate(VISUAL_FIT_JS)
   check(name+platform+' real long metadata stays readable',fit>=.74)
   text=page.locator('.diagram-content').inner_text()
   spec=VisualSpec.model_validate(pg['visual'])
   check(name+platform+' keeps every full name and description',all(display_label(i,n,spec.kind) in text and i.detail in text for n,i in enumerate(spec.items)))
   check(name+platform+' keeps every original tag',all(t in text for i in pg['visual']['items'] for t in i.get('tags',[])))
   if spec.kind=='rank':
    check(name+platform+' keeps continuous ranks',page.locator('.style-rank').all_text_contents()==[f"{i['rank']:02d}" for i in pg['visual']['items']])
    check(name+platform+' does not repeat generated ordinals',page.locator('.style-item h3').all_text_contents()==[display_label(i,n,'rank') for n,i in enumerate(spec.items)])
   else:
    check(name+platform+' tutorial is not a ranking',page.locator('.style-rank').count()==0)
    check(name+platform+' actual tutorial has no overlap',page.evaluate("() => {const d=document.querySelector('.diagram-content').getBoundingClientRect(),h=document.querySelector('.v-header').getBoundingClientRect(),t=document.querySelector('.v-takeaway').getBoundingClientRect();return d.top>=h.bottom-1 && d.bottom<=t.top+1;}"))
 browser.close()
check('same content has five different structural layouts',len(layouts)==5)
print(f'结果：{passed} 通过 / 0 失败')
