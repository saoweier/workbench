"""Embedded skill team contracts, actual edits, media and browser flows; isolated."""
import base64,io,json,os,socket,subprocess,sys,tempfile,time,types
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-skills-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",CWB_ARTIFACT_DIR=str(tmp/'artifacts'),CWB_TMP_DIR=str(tmp/'tmp'),CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'),CWB_PENDING_REVIEW_STOCK_LIMIT='40')
from fastapi.testclient import TestClient
from app.main import app
from app.api.production import SessionFactory
from app.api.creation import runtime
from app.models.entities import ContentItem,ContentRevision,PlatformRevision,Event,ProviderCallRow
from app.services.content_skills import ContentSkills,ContentPlan
from app.services.content_media import ContentMedia
from app.services.provider_contract import RunMode,ProviderConfig,ProviderKind,AdapterType
from app.services.production_service import ProductionService
from app.services.compose_service import ComposeService
from app.services.adapters.base import AdapterResult,TransportResponse
from app.services.adapters.openai_images import OpenAIImagesAdapter
from app.services.visual_content import VisualSpec
from app.services.renderer import PlaywrightRenderer
from app.core.errors import ValidationFailed,StateConflict,NotFound
from PIL import Image

client=TestClient(app);headers={'X-CWB-Local-Action':'account-connection'};passed=0
def check(name,value):
    global passed
    assert value,name
    passed+=1;print('PASS '+name,flush=True)

# 导航打断在途请求时会冒出瞬时网络错误。那是环境噪声，不是页面脚本缺陷；
# 不过滤会让"无脚本异常"断言偶发失败（实测出现过一次），也会掩盖真正的脚本错误。
PAGE_NOISE=('net::ERR_ABORTED','net::ERR_NETWORK_CHANGED','net::ERR_CONNECTION_RESET',
            'net::ERR_CONNECTION_REFUSED','Failed to fetch','The user aborted a request',
            'Load request cancelled')
def page_errors(errors):
    return [e for e in errors if not any(n in e for n in PAGE_NOISE)]
def rejects(fn,cls):
    try:fn()
    except cls:return True
    return False

catalog=client.get('/api/v1/content-skills').json()
check('seven stage and seven subject skills with version and permissions',len(catalog['items'])==14 and all(s['version'] and s['instructions'] and s['permissions'] for s in catalog['items']))
# 入口说明是路由的单一来源：每个流程技能都要能回答「何时用 / 输入是什么 / 交给谁」。
from app.services.content_skills import stage_entry_contract
entry=stage_entry_contract()
check('seven stage skills declare complete routing entry',set(entry)=={'discovery','research','selection','planning','generation','audit','revision'} and all(not v['missing'] and all(v['entry'][k] for k in ('when','inputs','handoff')) for v in entry.values()))
check('catalog exposes routing entry beside permissions',all(s.get('entry') and not s.get('entry_missing') for s in catalog['items'] if s['group']=='stage') and all('entry' not in s for s in catalog['items'] if s['group']=='direction'))
skills=ContentSkills(SessionFactory);snapshot=skills.snapshot('frozen')
item=next(v for v in catalog['items'] if v['id']=='planning')
updated=client.put('/api/v1/content-skills/planning',headers=headers,json={'instructions':item['instructions']+'\n必须输出可照着操作的具体推荐，不回避对象。','expected_version':item['version']})
check('user can customize skill without calling model',updated.status_code==200 and updated.json()['version']!=item['version'])
check('running snapshot unaffected by rule changes',skills.snapshot('frozen')['planning']['version']==item['version'])
check('concurrent old editor gets conflict',client.put('/api/v1/content-skills/planning',headers=headers,json={'instructions':item['instructions'],'expected_version':item['version']}).status_code==409)
check('cross origin mutation rejected',client.put('/api/v1/content-skills/planning',headers={**headers,'Origin':'https://foreign.invalid'},json={'instructions':item['instructions'],'expected_version':updated.json()['version']}).status_code==403)
body={'request_id':str(uuid4()),'topic':'整理周末家庭清单','requirements':'具体对象与方法','run_mode':'local_seed'}
plan=client.post('/api/v1/creation/plans',headers=headers,json=body)
check('independent blueprint has per-page purpose and visual brief',plan.status_code==200 and len(plan.json()['plan']['pages'])==4)
check('plan request is idempotent',client.post('/api/v1/creation/plans',headers=headers,json=body).json()['reused'])
check('changing same planning request rejected',client.post('/api/v1/creation/plans',headers=headers,json={**body,'topic':'其他主题选择方法'}).status_code==409)
check('plan unknown fields rejected',client.post('/api/v1/creation/plans',headers=headers,json={**body,'approval':True}).status_code==422)

from app.api import creation
from app.api import studio
studio.boards.ingest({'source':'hackernews','source_name':'Hacker News 协议模拟','updated_at':'2026-10-04T00:00:00Z','items':[{'title':'公开科技题材','id':'fixture','url':'https://news.ycombinator.com/item?id=1'}]})
studio.boards.refresh=lambda:None
discovery=client.post('/api/v1/creation/discovery',headers=headers,json={'request_id':str(uuid4()),'run_mode':'local_seed'})
check('discovery exclusively uses HotPush aggregation',discovery.status_code==200 and 'HotPush' in discovery.json()['note'] and not discovery.json()['search_executed'])

png=io.BytesIO();Image.new('RGB',(480,640),'#ee9955').save(png,format='PNG');raw=png.getvalue()
media=client.post('/api/v1/content-media',headers=headers,files={'image':('sample.png',raw,'image/png')},data={'description':'自有产品素材'})
check('upload creates immutable local media',media.status_code==200 and media.json()['origin']=='provided')
mid=media.json()['id'];store=ContentMedia()
check('same upload does not duplicate media',store.store(raw,description='自有产品素材',origin='provided')['id']==mid)
check('uploaded image can be viewed',client.get('/api/v1/content-media/'+mid+'/image').status_code==200)
check('invalid bytes rejected',client.post('/api/v1/content-media',headers=headers,files={'image':('bad.png',b'no image','image/png')}).status_code==422)
check('upload missing local action rejected',client.post('/api/v1/content-media',files={'image':('sample.png',raw,'image/png')}).status_code==403)
check('oversized media rejected',rejects(lambda:store.store(b'x'*20_000_001,description='大图',origin='provided'),ValidationFailed))
check('path traversal rejected',rejects(lambda:store.get('../secrets.json'),NotFound))
visual={'kind':'photo','title':'产品实物图','items':[{'label':'自有素材','detail':'主体明确的图片用于说明内容。','icon':'page','media_id':mid}],'takeaway':'图片与选题相符。'}
check('generic photo layout accepts local media instead of fruit-only ids',VisualSpec.model_validate(visual).items[0].media_id==mid)
bad=deepcopy(visual);bad['items'][0]['media_id']='https://untrusted.invalid/img.png'
check('model cannot supply arbitrary remote image URL',rejects(lambda:VisualSpec.model_validate(bad),ValueError))
image_plan=ContentPlan.model_validate(plan.json()['plan']);image_plan.pages[0].visual_type='generated_image'
check('missing image API gives actionable pause',rejects(lambda:store.create_for_plan(image_plan,content_id='test',context_id='missing'),ValidationFailed))
assets=store.create_for_plan(image_plan,content_id='test',context_id='provided',provided_ids=[mid])
check('provided image fulfills image blueprint without API',len(assets)==1 and assets[0]['id']==mid)
with_image=types.SimpleNamespace(image_provider=lambda:True,generate_image=lambda **kw:(types.SimpleNamespace(request_key='explicit-image'),AdapterResult(ok=True,parsed={'b64_json':base64.b64encode(raw).decode()})))
generated=ContentMedia(with_image).create_for_plan(image_plan,content_id='generated-test',context_id='image')
check('runtime image output saved with AI disclosure',generated[0]['origin']=='generated' and 'AI' in generated[0]['disclosure'])
check('foreign content media cannot be reused',rejects(lambda:store.create_for_plan(image_plan,content_id='other',context_id='foreign',provided_ids=[generated[0]['id']]),ValidationFailed))
cfg=ProviderConfig(name='image mock',kind=ProviderKind.IMAGE,adapter_type=AdapterType.OPENAI_IMAGES,base_url='https://image.invalid/v1',model_id='image-test')
sent=[]
class Transport:
    def request(self,*args,**kw):
        sent.append((args,kw));return TransportResponse(200,{'data':[{'b64_json':base64.b64encode(raw).decode()}]})
adapter=OpenAIImagesAdapter(cfg,api_key='test-only',transport=Transport())
check('Images protocol returns inline image',adapter.generate('水果主体图片').ok)
check('Images uses one explicit generation with configured model',sent[0][0][1].endswith('/images/generations') and sent[0][1]['json_body']['model']=='image-test' and sent[0][1]['json_body']['n']==1)
check('image connection test buys no image',not adapter.test_connection().ok and len(sent)==1)
class URLTransport:
    def request(self,*args,**kw):return TransportResponse(200,{'data':[{'url':'https://remote.invalid/photo.png'}]})
check('image adapter does not fetch remote model URLs',not OpenAIImagesAdapter(cfg,api_key='test',transport=URLTransport()).generate('test').ok)
class TimeoutTransport:
    def request(self,*args,**kw):raise TimeoutError('unknown')
check('image timeout explicitly unknown without automatic retry',OpenAIImagesAdapter(cfg,api_key='test',transport=TimeoutTransport()).generate('test').error_code=='NETWORK_TIMEOUT')

rt=runtime();svc=ProductionService(SessionFactory,runtime=rt)
produced=svc.produce_from_seed(render=False)
cid=produced['content_id'];base=produced['stages']['compose']['revision_id']
with SessionFactory() as s:
    original=s.query(PlatformRevision).filter_by(content_revision_id=base,platform='douyin').one();old_title=original.title;old_caption=original.caption
    original.state='approved';s.commit()
change=svc.change_request(cid,base_revision_id=base,instruction='抖音标题改为：周末清单怎么做')
with SessionFactory() as s:
    newer=s.query(PlatformRevision).filter_by(content_revision_id=change['new_revision_id'],platform='douyin').one()
    check('targeted revision actually changes text',newer.title=='周末清单怎么做' and newer.title!=old_title)
    check('targeted revision preserves untouched caption',newer.caption==old_caption)
    check('old approved version retained',s.query(PlatformRevision).filter_by(content_revision_id=base,platform='douyin').one().state=='approved')
    check('new version does not inherit approval',newer.state!='approved' and not newer.reviews)
check('no-op revision refused',rejects(lambda:svc.change_request(cid,base_revision_id=change['new_revision_id'],instruction='抖音标题改为：周末清单怎么做'),ValidationFailed))
check('stale revision cannot overwrite current',rejects(lambda:svc.change_request(cid,base_revision_id=base,instruction='抖音标题改为：新的标题'),StateConflict))
history=skills.history(cid)
check('revision trace identifies actual changes',any(e['skill_id']=='revision' and e['output']['actual_fields_changed']==1 for e in history))

calls=[]
audit_response={'passed':True,'summary':'内容没有回答选题，需要调整','requirements_coverage':['缺少具体推荐'],'issues':[{'severity':'error','page':2,'problem':'没有推荐对象','suggestion':'补充具体对象与选择理由'}]}
class FakeRuntime:
    def complete_text(self,**kw):
        calls.append(kw);schema=kw['json_schema']
        value=audit_response if 'requirements_coverage' in schema['properties'] else plan.json()['plan']
        return types.SimpleNamespace(id='fake-call'),AdapterResult(ok=True,parsed=value)
modelskills=ContentSkills(SessionFactory,FakeRuntime())
model_sources=[{'id':'U01','kind':'user_text','access_state':'ok','excerpt_basis':'user_provided','excerpt':'隔离测试资料：推荐对象为示例水果A、B；本资料仅用于规划与审核契约测试，不代表真实营养推荐。'}]
modelplan=modelskills.plan(topic='具体推荐',requirements='推荐对象',claims=[],sources=model_sources,run_mode=RunMode.REAL,context_id='model-plan',content_id=cid)
check('custom planning skill actually enters model prompt','不回避对象' in calls[-1]['prompt'])
aud=modelskills.audit(topic='水果怎么选',requirements='推荐水果吃法和参数',plan=modelplan.model_dump(),variants=[],claims=[],sources=model_sources,run_mode=RunMode.REAL,context_id='model-audit',content_id=cid)
check('semantic error cannot pass because model says passed',not aud.passed)
check('audit history reflects effective rejection',skills.history(cid)[0]['output']['passed'] is False)
local=client.post('/api/v1/contents/'+cid+'/skill-audit',headers=headers,json={'request_id':str(uuid4()),'base_revision_id':change['new_revision_id'],'run_mode':'local_seed'})
check('explicit audit endpoint preserves human approval boundary',local.status_code==200 and '不代替人工' in local.json()['notice'])

# Queue a genuine whole-draft rewrite through the configured HTTP protocol stub.
from mock_provider import start_mock
from app.worker import Worker
from app.core.config import get_settings
server,network=start_mock()
try:
    client.post('/api/v1/provider-configs',json={'name':'workflow protocol mock','kind':'text','adapter_type':'openai_compatible','base_url':f'http://127.0.0.1:{server.server_port}/v1','model_id':'test-text','api_key':'local-test-key','enabled':True,'allow_localhost':True})
    request={'request_id':str(uuid4()),'base_revision_id':change['new_revision_id'],'requirements':'补充实际推荐与操作办法，围绕主题重新组织各页。','run_mode':'real'}
    rebuilt=client.post('/api/v1/contents/'+cid+'/rebuild',json=request)
    check('whole content revision is queued without bypassing baseline',rebuilt.status_code==202)
    queued=rebuilt.json();check('same revision request does not create second task',client.post('/api/v1/contents/'+cid+'/rebuild',json=request).json()['reused'])
    worker=Worker(SessionFactory,get_settings(),worker_id='skills-protocol-test');worker.tick()
    result=client.get('/api/v1/runs/'+queued['run_id']).json()
    check('whole rewrite executes planning generation rendering and audit',result['state']=='succeeded' and {'planning','media','compose','render','audit'}.issubset({j['stage'] for j in result['jobs']}))
    with SessionFactory() as s:
        latest=s.get(ContentItem,cid).active_revision_id
        check('whole rewrite creates real new content rather than cloning',latest!=change['new_revision_id'] and s.query(PlatformRevision).filter_by(content_revision_id=latest,platform='douyin').one().title!='周末清单怎么做')
        check('whole rewrite retains frozen factual evidence',bool(s.get(ContentRevision,latest).claims_json['sources']))
        check('whole rewrite saves blueprint and skill versions',bool(s.get(ContentRevision,latest).brief_json['content_plan']) and len(s.get(ContentRevision,latest).brief_json['skill_versions'])==8)
        check('all rewritten platforms still require human approval',all(p.state=='ready_for_review' and not p.reviews for p in s.query(PlatformRevision).filter_by(content_revision_id=latest)))
    check('whole revision records all seven skill stages',set(e['skill_id'] for e in skills.history(cid))=={'discovery','research','selection','planning','generation','audit','revision'})
    check('every paid protocol request is journaled',len(result['provider_calls'])==len(network))
finally:
    server.shutdown();server.server_close()
    # Browser flow below remains completely offline.
    for config in client.get('/api/v1/provider-configs').json()['items']:client.delete('/api/v1/provider-configs/'+config['id'])

# Actual browser UI: no paid/external model calls.
from playwright.sync_api import sync_playwright,expect
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
env={**os.environ,'PYTHONPATH':str(ROOT/'backend'),'PYTHONUTF8':'1'}
process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
url=f'http://127.0.0.1:{port}'
try:
    import httpx
    for _ in range(100):
        try:
            if httpx.get(url+'/api/v1/health',timeout=1).status_code==200:break
        except Exception:time.sleep(.1)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=PlaywrightRenderer.resolve_executable());page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url+'/views/SkillWorkflow.html');expect(page.locator('[data-save]')).to_have_count(14)
        check('fourteen editable stage and subject skill cards visible',page.locator('textarea').count()==14)
        check('seven stage cards show when-to-use / inputs / handoff',page.locator('.skill-entry').count()==7 and page.locator('.skill-entry .skill-entry-row').count()>=21)
        page.goto(url+'/views/Production.html');page.get_by_role('button',name='自定义选题',exact=True).click();expect(page.locator('#creator-topic')).to_be_visible()
        check('direct creator requires no topic recommendation',page.locator('#guide-recommend').count()==0 and page.locator('#wizard-next').is_visible())
        page.get_by_text('补充要求、参考资料与配图',exact=False).click()
        page.locator('#creator-images').set_input_files({'name':'sample.png','mimeType':'image/png','buffer':raw});expect(page.locator('#creator-upload img')).to_have_count(1)
        check('creation UI supports actual image upload',page.locator('#creator-upload img').get_attribute('src').startswith('/api/v1/content-media/'))
        page.goto(url+'/views/ReviewPreview.html?content='+cid);expect(page.locator('#revision-submit')).to_be_visible()
        check('content revision entry is visible in preview',page.locator('#revision-requirements').is_visible())
        page.locator('#btn-change').click();expect(page.locator('#revision-requirements')).to_be_focused()
        page.locator('#revision-scope').select_option('title');page.locator('#revision-mode').select_option('local_seed');page.locator('#revision-requirements').fill('改为：实际改稿测试')
        page.locator('#revision-submit').click()
        # 成功信号是 #revision-message 写明新版本号。只盯 #caption 会把"请求根本没发出去"
        # 伪装成一次超时（全量回归里真踩过一次，根因被超时文案盖住），所以失败时把界面上
        # 的报错一并抛出来，避免下次又要靠翻库定位。
        try:
            expect(page.locator('#revision-message')).to_contain_text('已实际修改为',timeout=60000)
        except AssertionError:
            toast=page.locator('#cwb-toast').inner_text().strip() if page.locator('#cwb-toast').count() else '（界面没有报错提示）'
            raise AssertionError('改稿请求没有生效：'+toast[:200]) from None
        expect(page.locator('#caption')).to_contain_text('实际改稿测试',timeout=30000)
        check('preview actual edit changes visible result',True)
        page.locator('#compare-version').locator('..').locator('summary').click();expect(page.locator('#compare-output')).to_contain_text('旧版')
        check('old/current revision comparison available',page.locator('#compare-output').is_visible())
        proof=ROOT/'docs/test-artifacts';proof.mkdir(exist_ok=True)
        page.locator('#revision-studio').screenshot(path=str(proof/'skill-revision-studio.png'))
        page.goto(url+'/views/ApiSettings.html');expect(page.locator('#i-save')).to_be_visible()
        page.locator('#i-name').fill('图片接口本地配置测试');page.locator('#i-base').fill('https://image.invalid/v1');page.locator('#i-model').fill('image-test');page.locator('#i-key').fill('test-key-only');page.locator('#i-enabled').check();page.locator('#i-save').click();expect(page.locator('#i-msg')).to_contain_text('已保存')
        check('image provider UI saves without buying images',True)
        page.goto(url+'/views/SkillWorkflow.html');expect(page.locator('[data-save]')).to_have_count(14);page.screenshot(path=str(proof/'skill-team-desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844});check('skills fit narrow screen',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        page.goto(url+'/views/Production.html');check('planning fits narrow screen',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        page.goto(url+'/views/ReviewPreview.html?content='+cid);expect(page.locator('#revision-submit')).to_be_visible();check('revision UI fits narrow screen',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        real=page_errors(errors)
        check('new flows have no browser script errors'+('：'+' | '.join(e[:160] for e in real) if real else ''),not real)
        browser.close()
finally:
    process.terminate();process.wait(timeout=10)
print(f'结果：{passed} 通过 / 0 失败')
