"""Template pair editing, snapshots, output and maintenance UI; isolated."""
import os,sys,tempfile,json,io,zipfile,subprocess,socket,time
from pathlib import Path
from copy import deepcopy
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-templates-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",CWB_ARTIFACT_DIR=str(tmp/'artifacts'),CWB_TMP_DIR=str(tmp/'tmp'),CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'))
from fastapi.testclient import TestClient
from app.main import app
from app.api.production import SessionFactory
from app.services.template_packages import TemplateStore,TemplatePackage,TemplateStyle,fingerprint
from app.services.content_skills import ContentSkills,HARD_RULES
from app.services.content_recipes import apply_recipe,options
from app.services.content_forms import build_brief
from app.services.template_examples import example_page,example_form
from app.services.renderer import build_pages,PlaywrightRenderer,verify_images
from app.services.visual_content import VISUAL_FIT_JS
from app.models.entities import Event,ProviderCallRow
from app.services.profile_store import engineering_default
from app.services.compose_service import ComposeService
from app.services.meme_editorial import visible_page_text
from playwright.sync_api import sync_playwright
from pydantic import ValidationError
from app.core.errors import StateConflict
passed=0
def check(name,condition):
 global passed
 assert condition,name
 passed+=1;print('PASS '+name,flush=True)
client=TestClient(app);headers={'X-CWB-Local-Action':'account-connection'}
store=TemplateStore(SessionFactory);packages=store.catalog()
check('five complete builtin skill/layout pairs',len(packages)==5 and all(p['instructions'] and p['renderer'] and p['style'] for p in packages))
check('every builtin is validated',all(TemplatePackage.model_validate({k:v for k,v in p.items() if k not in {'origin','package_version'}}) for p in packages))
check('five independent visual families with rich poster elements',len({p['style']['design_family'] for p in packages})==5 and all(p['style']['decoration']=='rich' and not p['forms'] for p in packages))
check('typography differs across the five visual families',len({p['style']['heading_font'] for p in packages})==5)
original=store.get('friendly_guide');definition={k:v for k,v in original.items() if k not in {'origin','package_version'}}
check('list API reports paired versions',len(client.get('/api/v1/template-packages',headers=headers).json()['items'])==5)
request={'package':definition,'expected_version':original['package_version']}
check('cross-site writes are blocked',client.put('/api/v1/template-packages/friendly_guide',json=request,headers={**headers,'Origin':'https://untrusted.example'}).status_code==403)
check('writes require explicit local action header',client.put('/api/v1/template-packages/friendly_guide',json=request).status_code==403)
for change in [dict(id='../escape'),dict(html='<script>alert(1)</script>'),dict(renderer='remote_exec'),dict(style={**definition['style'],'accent':'url(http://evil)'}),dict(style={**definition['style'],'heading_size':120}),dict(style={**definition['style'],'ink':'#fff8e9'})]:
 r=client.put('/api/v1/template-packages/friendly_guide',headers=headers,json={'package':{**definition,**change},'expected_version':original['package_version']})
 check('invalid template settings rejected '+str(list(change)),r.status_code==422)
snapshot_id=str(uuid4());skills=ContentSkills(SessionFactory)
frozen=skills.snapshot(snapshot_id,direction='tech',template_package=original)
check('template skill snapshot binds exact package',frozen['template.friendly_guide']['package']['style']==definition['style'])
check('planning sees selected template rules',definition['instructions'] in skills.instructions('planning',snapshot=frozen))
check('generation sees selected template rules',definition['instructions'] in skills.instructions('generation',snapshot=frozen))
check('audit sees selected template rules',definition['instructions'] in skills.instructions('audit',snapshot=frozen))
check('discovery is not biased by visual template',definition['instructions'] not in skills.instructions('discovery',snapshot=frozen))
check('fixed permission rules remain present',HARD_RULES in skills.instructions('generation',snapshot=frozen))
updated=deepcopy(definition);updated['instructions']+='\n每行先给一个具体例子，再写解释。';updated['style']['body_size']=30
saved=store.save(updated,original['package_version'])
check('rules and appearance change in one atomic version',saved['package_version']!=original['package_version'] and saved['style']['body_size']==30)
check('stale writer cannot overwrite',client.put('/api/v1/template-packages/friendly_guide',headers=headers,json=request).status_code==409)
check('existing task keeps frozen template',skills.snapshot(snapshot_id,template_package=saved)==frozen)
fresh=skills.snapshot(str(uuid4()),template_package=saved)
check('new task sees new paired version',fresh['template.friendly_guide']['version']==saved['package_version'])
history=store.versions('friendly_guide')
check('history preserves both builtin and saved versions',len(history)==2 and history[0]['package_version']==original['package_version'])
clone={**updated,'id':'mint_demo','name':'薄荷讲解手册','style':{**updated['style'],'accent':'#236b79'}}
new=store.save(clone)
check('new template created without new Python renderer',new['renderer']=='friendly_guide' and new['id']=='mint_demo')
check('custom template appears in creation options',any(p['id']=='mint_demo' for p in options()['templates']))
brief=apply_recipe(build_brief(topic='概念讲解',requirements='恰好1页'),template_id='mint_demo')
check('selected brief freezes full pair',brief.template_package['package_version']==new['package_version'])
demo=client.get('/api/v1/studio/template-preview/mint_demo',headers=headers)
check('custom template shares same production preview engine',demo.status_code==200 and '#236b79' in demo.text)
preview=client.post('/api/v1/template-packages/preview',headers=headers,json=clone)
check('unsaved style preview makes no permanent edit',preview.status_code==200 and len(store.versions('mint_demo'))==1)
archive=client.get('/api/v1/template-packages/mint_demo/export',headers=headers)
with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
 check('export contains SKILL and template definition',set(z.namelist())=={'mint_demo/SKILL.md','mint_demo/template.json'})
 check('export is reusable validated package',TemplatePackage.model_validate({**json.loads(z.read('mint_demo/template.json')),'instructions':z.read('mint_demo/SKILL.md').decode()}).id=='mint_demo')
sample=example_page('mint_demo');draft={'platform':'douyin','form':example_form(sample),'title':'模板回归示例','caption':'固定排版示例，非真实排行榜。','pages':[sample]}
docs=build_pages(draft,engineering_default('douyin'))
check('artifact version includes exact paired fingerprint',new['package_version'] in docs[0]['template_version'])
result=PlaywrightRenderer(tmp/'artifacts').render_platform(draft,engineering_default('douyin'),display_id='PAIR')
check('custom pair produces verified PNG',result.passed and len(result.images)==1 and not verify_images(result,tmp/'artifacts'))
# All five built-in packages render the same engine used by production.
for p in packages:
 pg=example_page(p['id']);d={**draft,'form':example_form(pg),'pages':[pg]}
 r=PlaywrightRenderer(tmp/'artifacts').render_platform(d,engineering_default('douyin'),display_id=p['id'])
 check(p['name']+' has stable readable PNG',r.passed and len(r.images)==1)
 html=build_pages(d,engineering_default('douyin'))[0]['html']
 check(p['name']+' uses its own structural layout','family-'+p['style']['design_family'] in html and html.count('style-item')>=len(pg['visual']['items']) and all(i['detail'] in html for i in pg['visual']['items']))
 check(p['name']+' preview form matches its content','data-form="'+example_form(pg)+'"' in html)
with SessionFactory() as s:check('editing and preview never call provider',s.query(ProviderCallRow).count()==0)
# Actual maintenance page interactions against a temporary local server.
sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
log=(tmp/'server.log').open('w',encoding='utf-8');env={**os.environ,'PYTHONPATH':str(ROOT/'backend')}
server=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log,creationflags=0x08000000 if os.name=='nt' else 0)
try:
 import httpx
 for _ in range(100):
  try:
   if httpx.get(f'http://127.0.0.1:{port}/api/v1/template-packages',timeout=1).status_code==200:break
  except Exception:pass
  time.sleep(.1)
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True,executable_path=PlaywrightRenderer.resolve_executable());page=browser.new_page(viewport={'width':1440,'height':1100});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(f'http://127.0.0.1:{port}/views/TemplateLibrary.html');page.locator('#template-instructions').wait_for()
  check('maintenance page lists custom pair',page.locator('[data-package="mint_demo"]').count()==1)
  page.locator('[data-package="mint_demo"]').click();page.wait_for_function("document.querySelector('#editor-title').textContent==='薄荷讲解手册'")
  page.locator('#duplicate-template').click();page.wait_for_function("document.querySelector('#current-version').textContent.includes('新模板')")
  page.locator('#template-name').fill('浏览器新模板');page.locator('#template-instructions').fill('每一行先给一个具体例子，再解释为什么。至少包含完整答案，不增加空封面。')
  page.locator('[data-style="heading_font"]').select_option('system')
  page.locator('#save-template').click();page.wait_for_function("document.querySelector('#template-message').textContent.includes('已保存')")
  check('UI copies and saves skill plus paired layout',page.locator('#package-list').inner_text().count('浏览器新模板')==1)
  check('art typography is editable in paired settings',page.locator('[data-style="heading_font"]').input_value()=='system' and page.locator('[data-style="decoration"]').input_value()=='rich')
  page.locator('#template-history').select_option('0');page.locator('#preview-template').click()
  check('preview iframe remains available',page.locator('#template-preview').is_visible())
  page.locator('#preview-mode').select_option('common');page.locator('#preview-template').click()
  frame=page.frame_locator('#template-preview');frame.locator('.style-item').first.wait_for()
  check('same-content comparison contains four concepts, no invented ranks',frame.locator('.style-item').count()==4 and frame.locator('.style-rank').count()==0)
  page.locator('#preview-mode').select_option('representative');page.locator('#preview-template').click()
  frame.locator('.style-item').first.wait_for()
  check('paired layout family is editable',page.locator('[data-style="design_family"]').input_value()=='handdrawn')
  page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(100)
  check('maintenance page fits mobile width',page.evaluate('document.documentElement.scrollWidth<=innerWidth+2'))
  check('maintenance UI has no JavaScript errors',not errors)
  browser.close()
finally:
 server.terminate();server.wait(timeout=10);log.close()
print(f'结果：{passed} 通过 / 0 失败')
