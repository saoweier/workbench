"""Isolated regression: factual grounding, diagnostic records and skill editing."""
import json,os,sys,tempfile,types
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-grounding-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",CWB_ARTIFACT_DIR=str(tmp/'artifacts'),CWB_TMP_DIR=str(tmp/'tmp'),CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'))
from fastapi.testclient import TestClient
from app.main import app
from app.api.production import SessionFactory
from app.services.evidence_gate import factual_sources,require_evidence,validate_research
from app.services.content_skills import ContentSkills,ResearchAssessment,PlanPage
from app.services.content_forms import build_brief
from app.services.content_recipes import detect_direction
from app.services.research_service import ResearchService,ResearchResult
from app.services.provider_contract import RunMode
from app.services.adapters.base import AdapterResult
from app.services.content_diagnostics import diagnose,redact
from app.services.source_reader import validate_public_url,read_source_detail,TextParser
from app.services.public_research import SearchLinks
from app.services.pipeline import PipelineService
from app.models.entities import ContentItem,ContentRevision,PlatformRevision,Artifact,Event,Run,ReviewDecision
from app.core.errors import ValidationFailed

passed=0
def check(name,value):
    global passed
    assert value,name
    passed+=1;print('PASS '+name,flush=True)
def rejects(fn,cls=ValidationFailed):
    try:fn()
    except cls:return True
    return False

topic='这菜吃了能不烧心吗是什么梗'
text='公开资料介绍：这个句式出现在规矩体AI短剧中。顾客用它表达价格贵一些但用料可靠、令人放心。并非按重口味红油菜字面解释的玩笑。'
source={'id':'U01','kind':'user_provided','access_state':'ok','excerpt_basis':'user_provided','excerpt':text}
for kind in ['editorial_plan','hotpush_context','trend_context']:
    check(kind+' cannot become facts',not factual_sources([{**source,'kind':kind}]))
for basis in ['search_snippet','url_only']:
    check(basis+' cannot become facts',not factual_sources([{**source,'excerpt_basis':basis}]))
check('read search result is usable',bool(factual_sources([{**source,'kind':'search_result','excerpt_basis':'full_text'}])))
check('failed body cannot become facts',not factual_sources([{**source,'access_state':'snippet_only'}]))
check('missing evidence stops critical topic',rejects(lambda:require_evidence(topic,[])))
check('ordinary creative task needs no factual web claim',require_evidence('整理周末清单',[])==[])
for factual_topic,req in [('黄金睡眠时长出炉','基于AASM资料'),('Vue3 UIKit 实战','基于实际原文'),('新版2Do详解','只写资料实际支持的功能'),('英雄联盟全球总决赛赛制说明','按官方2026资料')]:
    check(factual_topic+' cannot continue from a context snapshot',rejects(lambda:require_evidence(factual_topic,[{**source,'kind':'hotpush_context'}],req)))
check('guide remains guide when discussing individual differences',build_brief(topic='黄金睡眠时长出炉',requirements='做一个活泼的生活知识指南，直接解释建议和个人差异').form=='guide')
check('onboarding remains tutorial when mentioning differences',build_brief(topic='新版2Do详解',requirements='做成任务管理上手指南，说明与普通清单的区别').form=='tutorial')
brief=build_brief(topic=topic,requirements='活泼一点的梗指南，恰好2页')
check('meme gets dedicated form and two-page constraint',brief.form=='meme' and brief.page_max==2)
check('meme avoids food shopping direction',detect_direction(topic)=='news')
diagram={'index':1,'purpose':'展示含义对照','heading':'含义对照','points':['可靠品质'],'visual_type':'compare','visual_brief':'含义对照示意'}
check('diagram layout normalizes to rendering route',PlanPage.model_validate(diagram).visual_type=='diagram')
check('unknown media route rejected',rejects(lambda:PlanPage.model_validate({**diagram,'visual_type':'random'}),ValueError))

report={'can_answer':True,'summary':'原文支持短剧语境与可靠品质的含义','facts':[
 {'role':'origin','statement':'资料介绍规矩体AI短剧语境','source_id':'U01','quote':'这个句式出现在规矩体AI短剧中'},
 {'role':'meaning','statement':'表示贵一些但用料可靠令人放心','source_id':'U01','quote':'价格贵一些但用料可靠、令人放心'}],
 'blocking_gaps':[],'limitations':['未考证唯一首发视频，不声称独立证实']}
validate_research(ResearchAssessment.model_validate(report),[source],meme=True)
check('literal source quotes validate',True)
wrapped=deepcopy(report);wrapped['facts'][0]['quote']='这个句式出现在\n规矩体AI短剧中'
wrapped=ResearchAssessment.model_validate(wrapped)
validate_research(wrapped,[source],meme=True)
check('only whitespace variation accepts and stores original span',wrapped.facts[0].quote=='这个句式出现在规矩体AI短剧中')
wrong=deepcopy(report);wrong['facts'][0]['quote']='看到红油菜就担心烧心的调侃'
check('invented literal quote is rejected',rejects(lambda:validate_research(ResearchAssessment.model_validate(wrong),[source],meme=True)))
wrong=deepcopy(report);wrong['facts'][0]['source_id']='U99'
check('unavailable source id is rejected',rejects(lambda:validate_research(ResearchAssessment.model_validate(wrong),[source],meme=True)))
wrong=deepcopy(report);wrong['facts']=wrong['facts'][:1]
check('missing meaning blocks meme guide',rejects(lambda:validate_research(ResearchAssessment.model_validate(wrong),[source],meme=True)))
wrong=deepcopy(report);wrong['can_answer']=False
check('model uncertainty is not silently overridden',rejects(lambda:validate_research(ResearchAssessment.model_validate(wrong),[source],meme=True)))

calls=[]
class Runtime:
    def search_provider(self):return None
    def complete_text(self,**kw):
        calls.append(kw)
        if 'queries' in kw.get('json_schema',{}).get('properties',{}):
            return types.SimpleNamespace(id='search-plan'),AdapterResult(ok=True,parsed={'objective':'搜索原始语境正文','queries':['规矩体 AI 短剧 不烧心 含义'],'required_evidence':['原始语境与含义']})
        return types.SimpleNamespace(id='test-call'),AdapterResult(ok=True,parsed=deepcopy(report))
rt=Runtime();skills=ContentSkills(SessionFactory,rt);snapshot=skills.snapshot('frozen')
result=skills.research_review(topic=topic,requirements='活泼指南',sources=[source],context_id='frozen',content_id=None,snapshot=snapshot)
check('research executes real schema stage',len(calls)==1 and result.can_answer)
check('raw source is passed to model',text in calls[-1]['prompt'])
check('research contract distinguishes first ever origin', '不要求找到历史上的唯一首发视频' in calls[-1]['prompt'])
before=len(calls)
check('planning cannot spend before missing evidence gate',rejects(lambda:skills.plan(topic=topic,requirements='',claims=[],sources=[],run_mode=RunMode.REAL,context_id='blocked')) and len(calls)==before)
check('audit cannot approve missing core evidence',rejects(lambda:skills.audit(topic=topic,requirements='',plan={},variants=[],claims=[],sources=[],run_mode=RunMode.REAL,context_id='blocked',content_id='missing')) and len(calls)==before)
with patch('app.services.public_research.search_public',side_effect=ValueError('搜索要求验证')), patch('app.services.source_reader.read_source_detail',side_effect=ValueError('隔离测试不联网')):
    rs=ResearchService(rt).research(topic=topic,user_materials=[{'text':'https://example.com/x\n选题来源快照（仅记录HotPush返回顺序及时间，不能证明全网热度或文章观点）：\nhttps://douyin.com/blocked'}],run_mode=RunMode.REAL)
check('missing provider performs no search while preserving article failure',not rs.search_executed and bool(rs.access_failures))
check('missing search provider is disclosed',any(v.startswith('未执行自动搜索') for v in rs.limitations))
check('malformed legacy snapshot URL is not automatically opened',not any(s.url and 'douyin.com' in s.url for s in rs.sources))
with patch('app.services.public_research.search_public',side_effect=ValueError('要求验证')) as search:
    rs=ResearchService(rt).research(topic='这菜吃了能不烧心吗',requirements='做一个梗指南',run_mode=RunMode.REAL)
    check('unconfigured search never invokes retired scrapers',search.call_count==0 and not rs.search_executed)
rs=ResearchResult(topic=topic,run_mode=RunMode.REAL)
with patch('app.services.source_reader.read_source_detail',return_value=(text+'本文由AI生成','test-hash','https://example.com/final')) as reader:
    ResearchService(rt)._absorb_search_results([{'url':'https://example.com/start','snippet':'摘要不作事实'}],rs)
    check('configured search reads original body and final URL',rs.sources[0].excerpt_basis=='full_text' and rs.sources[0].url=='https://example.com/final')
    check('configured search discloses AI source and blocks platforms','本文由AI生成' in rs.sources[0].limitations and 'douyin.com' in reader.call_args.kwargs['blocked_domains'])

def dns(ip):return [(2,1,6,'',(ip,443))]
with patch('app.services.source_reader.socket.getaddrinfo',return_value=dns('127.0.0.1')):
    check('private URL rejected',rejects(lambda:validate_public_url('http://example.com'),ValueError))
with patch('app.services.source_reader.socket.getaddrinfo',return_value=dns('198.18.0.1')):
    check('literal proxy IP rejected',rejects(lambda:validate_public_url('http://198.18.0.1'),ValueError))
    with patch('app.services.source_reader.httpx.get',return_value=types.SimpleNamespace(json=lambda:{'Status':0,'Answer':[{'type':1,'data':'8.8.8.8'}]})):
        validate_public_url('https://example.com');check('synthetic DNS requires independent public proof',True)
    with patch('app.services.source_reader.httpx.get',return_value=types.SimpleNamespace(json=lambda:{'Status':0,'Answer':[{'type':1,'data':'10.0.0.1'}]})):
        check('independent private DNS still rejected',rejects(lambda:validate_public_url('https://example.com'),ValueError))
with patch('app.services.source_reader.socket.getaddrinfo',return_value=[]):
    check('empty DNS fails closed',rejects(lambda:validate_public_url('https://example.com'),ValueError))
with patch('app.services.source_reader.validate_public_url'):
    check('original social platform reading blocked',rejects(lambda:read_source_detail('https://www.douyin.com/x',blocked_domains=('douyin.com',)),ValueError))
parser=TextParser();parser.feed('<p>来源原文</p><script>stealSecrets()</script><style>hidden</style>')
check('reader removes executable markup',parser.parts==['来源原文'])
parser=SearchLinks();parser.feed('<h3><a href="https://example.com/article">实际文章</a></h3><a href="https://evil.invalid">广告</a>')
check('search only returns titled article links',parser.items==[{'url':'https://example.com/article','title':'实际文章'}])
check('diagnostic redacts keys and arbitrary user text',redact({'api_key':'bad','note':'api_key=private sk-123456789012345 Bearer 123456789012345'})=={'api_key':'[已隐藏]','note':'api_key=[已隐藏] [已隐藏密钥] Bearer [已隐藏]'})

client=TestClient(app);headers={'X-CWB-Local-Action':'account-connection'}
for url in ['https://sspai.com/post/example','https://www.douyin.com/video/example']:
    captured=[]
    with patch('app.api.studio.boards.find',return_value=({'id':'test','title':'资料教程','url':url},{'fetched_at':'2026-10-06T00:00:00Z'})), patch('app.api.studio.creation.enqueue',side_effect=lambda payload,**kw:captured.append(payload) or {'state':'queued'}):
        response=client.post('/api/v1/studio/produce',headers=headers,json={'request_id':str(uuid4()),'topic':'资料教程','trend_id':'test','run_mode':'real'})
    frozen=json.loads(captured[0].materials.split('）：',1)[-1])
    check('selected original URL survives in frozen metadata '+url,response.status_code==202 and frozen['item']['url']==url)
    check('trend title cannot replace actual body '+url,'资料调研：先读取所选HotPush议题的原链接' in captured[0].requirements)
catalog=client.get('/api/v1/content-skills').json();current=next(v for v in catalog['items'] if v['id']=='research')
check('fourteen editable stage and direction policies',len(catalog['items'])==14)
update=client.put('/api/v1/content-skills/research',headers=headers,json={'instructions':current['instructions']+'\n测试自定义规则：来源应可查看。','expected_version':current['version']})
check('edit policy requires no model call',update.status_code==200 and len(calls)==before)
versions=client.get('/api/v1/content-skills/research/versions').json()['items']
check('policy history includes built in and saved revision',len(versions)==2 and versions[0]['origin']=='built_in')
check('running task keeps original frozen rule',skills.snapshot('frozen')['research']['version']==current['version'])
check('old editor cannot overwrite newer rules',client.put('/api/v1/content-skills/research',headers=headers,json={'instructions':current['instructions'],'expected_version':current['version']}).status_code==409)
check('unknown policy version returns 404',client.get('/api/v1/content-skills/unknown/versions').status_code==404)
check('unknown content diagnostics returns 404',client.get('/api/v1/contents/missing/diagnostics').status_code==404)

cid=str(uuid4());rid=str(uuid4());prid=str(uuid4());runid=str(uuid4())
with SessionFactory() as s:
    s.add(ContentItem(id=cid,display_id='LEGACY',topic=topic,state='ready_for_review',run_mode='real',active_revision_id=rid));s.flush()
    s.add(ContentRevision(id=rid,content_id=cid,input_hash='test',claims_json={'sources':[{**source,'kind':'editorial_plan'}]},brief_json={}));s.flush()
    s.add(PlatformRevision(id=prid,content_revision_id=rid,platform='douyin',title='旧错误稿',caption='错误解释',pages_json={'pages':[]},content_hash='test',state='ready_for_review',manifest_hash='test'));s.flush()
    s.add(Artifact(platform_revision_id=prid,kind='page_image',page_index=1,storage_key='test.png',sha256='test',size_bytes=10,width=1,height=1,template_version='test'))
    s.add(Run(id=runid,content_id=cid,stage='produce',state='succeeded',mode='real',input_hash='legacy'))
    s.commit()
for stage,output in [('research',{'sources':[{**source,'kind':'editorial_plan'}],'search_executed':False}),('planning',{'explanation':'红油重口味'}),('audit',{'passed':True})]:
    skills.record(runid,stage,state='succeeded',inputs={'topic':topic},output=output,content_id=cid)
diagnostic=diagnose(SessionFactory,cid)
check('diagnostic identifies all three false acceptance steps',len(diagnostic['runs'][0]['issues'])==3)
check('diagnostic has actual chronological recorded output',len(diagnostic['steps'])==3 and diagnostic['steps'][1]['output']['explanation']=='红油重口味')
check('legacy approval blocked by program evidence guard',rejects(lambda:PipelineService(SessionFactory).decide(prid,'approve','coisini',expected_manifest_hash='test')))
with SessionFactory() as s:
    check('blocked approval writes no review',s.query(ReviewDecision).count()==0 and s.get(PlatformRevision,prid).state=='ready_for_review')
check('diagnostics response is read only',client.get('/api/v1/contents/'+cid+'/diagnostics').status_code==200 and len(calls)==before)

from app.services.production_service import ProductionService
with patch('app.services.public_research.search_public',side_effect=ValueError('公开搜索不可用')):
    blocked=ProductionService(SessionFactory,runtime=rt).produce(topic=topic,run_mode=RunMode.REAL,
        user_materials=[{'kind':'editorial_plan','text':'这是系统生成的编辑提纲，不是任何原文事实。'}],render=False)
check('whole production retries search plan then stops instead of guessing',blocked['blocked_stage']=='research' and len(calls)==before)
with SessionFactory() as s:
    check('failed research creates no revision',s.query(ContentRevision).filter_by(content_id=blocked['content_id']).count()==0)

from app.services.provider_runtime import ProviderRuntime
from app.services.provider_contract import ProviderStore,SecretStore
logged=ProviderRuntime(ProviderStore(tmp/'fixture-config.json'),SecretStore(tmp/'fixture-secrets.json'),force_fixture=True)
logged.session_factory=SessionFactory
call,response=logged.complete_text(prompt='仅输出普通测试文本 api_key=never-expose',system='文本模式',content_id=cid,prompt_version='diagnostic-test',run_mode=RunMode.FIXTURE)
with SessionFactory() as s:
    events=s.query(Event).filter_by(entity_type='model_input',type='text_request').all()
    check('actual prompt saved with correct fixture mode',len(events)==1 and events[0].run_mode=='fixture')
    check('prompt saved without submitted sensitive text','never-expose' not in json.dumps(events[0].payload))
logged.complete_text(prompt='仅输出普通测试文本 api_key=never-expose',system='文本模式',content_id=cid,prompt_version='diagnostic-test',run_mode=RunMode.FIXTURE)
with SessionFactory() as s:
    check('same request prompt journal is idempotent',s.query(Event).filter_by(entity_type='model_input',type='text_request').count()==1)

# Verify our two pages independently of the other chat's Production wizard.
import socket,subprocess,time
import httpx
from playwright.sync_api import sync_playwright,expect
from app.services.renderer import PlaywrightRenderer
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
env={**os.environ,'PYTHONPATH':str(ROOT/'backend'),'PYTHONUTF8':'1'}
process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
url=f'http://127.0.0.1:{port}'
try:
    for _ in range(100):
        try:
            if httpx.get(url+'/api/v1/health',timeout=1).status_code==200:break
        except Exception:time.sleep(.1)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=PlaywrightRenderer.resolve_executable())
        page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url+'/views/SkillWorkflow.html?content='+cid)
        expect(page.locator('[data-save]')).to_have_count(14)
        expect(page.locator('#history')).to_contain_text('审核仍标为通过')
        check('skill manager shows actual failure chain',True)
        page.locator('[data-versions="research"]').click()
        expect(page.locator('[data-load-version="research"]')).to_be_visible()
        check('historical skill editor can load built-in instructions',True)
        page.locator('[data-load-version="research"]').select_option('0')
        expect(page.locator('[data-message="research"]')).to_contain_text('当前线上规则尚未改变')
        check('loading old rules does not silently save',True)
        page.set_viewport_size({'width':390,'height':844})
        check('diagnostics and editors fit mobile width',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        page.set_viewport_size({'width':1440,'height':1000})
        page.goto(url+'/views/ReviewPreview.html?content='+cid)
        expect(page.locator('#diagnostic-link')).to_have_attribute('href','/views/SkillWorkflow.html?content='+cid)
        expect(page.locator('#diagnostic-details')).to_be_visible()
        page.locator('#diagnostic-details > summary').click()
        expect(page.locator('#content-diagnostic')).to_contain_text('规划仍继续生成')
        check('preview exposes original planning error',True)
        check('preview links to selected content skill manager',cid in page.locator('#diagnostic-link').get_attribute('href'))
        check('diagnostic UI has no script errors',not errors)
        browser.close()
finally:
    process.terminate();process.wait(timeout=10)
print(f'结果：{passed} 通过 / 0 失败')
