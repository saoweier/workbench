"""Direct creation and HotPush contracts; isolated HTTP protocol test, no paid calls."""
import os,sys,tempfile,types,json,time
from pathlib import Path
from uuid import uuid4
from copy import deepcopy
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-direct-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",CWB_ARTIFACT_DIR=str(tmp/'artifacts'),CWB_TMP_DIR=str(tmp/'tmp'),CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'),CWB_PENDING_REVIEW_STOCK_LIMIT='1')
from fastapi.testclient import TestClient
from app.main import app
from app.api import studio,creation,production
from app.models.entities import ContentItem,ContentRevision,PlatformRevision,Job,Event
from app.services.content_forms import build_brief,parse_page_budget,parse_caption_budget
from app.services.content_skills import ContentSkills,ContentPlan
from app.services.trend_boards import TrendBoards,normalize_hotlist,SOURCES
from app.services.compose_service import ComposeService,_valid_rank_layout,_form_prompt_block,_dedupe_source_footnotes
from app.services.profile_store import engineering_default
from app.services.provider_contract import RunMode
from app.services.adapters.base import AdapterResult
from app.services.visual_content import VisualSpec,_diagram
from app.worker import Worker
from app.core.config import get_settings
from app.core.errors import ValidationFailed
from mock_provider import start_mock
client=TestClient(app);headers={'X-CWB-Local-Action':'account-connection'};passed=0
def check(name,value):
 global passed
 assert value,name
 passed+=1;print('PASS '+name,flush=True)
def rejects(fn):
 try:fn()
 except (ValidationFailed,ValueError):return True
 return False

from app.services.research_service import ResearchService
material='来源正文。'*90+'第10个仓库与最后的热度指标'
research=ResearchService(creation.runtime()).research(topic='榜单资料',user_materials=[{'text':material}],run_mode=RunMode.LOCAL_SEED)
check('user reference beyond400 chars survives into actual planning evidence',research.sources[0].excerpt.endswith('最后的热度指标'))
topic='上个月GitHub最热门Skill TOP10，精致排行榜1～2页'
b=build_brief(topic=topic)
check('TOP10 and pages parsed directly from user topic',b.form=='ranking' and b.rank_count==10 and b.strategy_pages()==(1,2))
check('natural month frozen independently from rolling30days',b.time_range and '自然月' in b.time_range)
check('exact two pages is exact',parse_page_budget('全稿恰好2页')==(2,2))
check('fruit topic no longer overrides ranking',build_brief(topic='水果TOP10排行榜1页').form=='ranking')
check('per-page count cannot overwrite totalTOP10',build_brief(topic='TOP10排行榜',requirements='每页3个对象').rank_count==10)
check('TOP2 is not silently expanded',build_brief(topic='最热门TOP2榜单').rank_count==2)
check('unsupported quantity gives explicit error',rejects(lambda:build_brief(topic='TOP20排行榜')))
check('explicit page budget is not capped or truncated by topic code',build_brief(topic='教程恰好9页').page_max==9)
check('negative tutorial wording never selects tutorial',build_brief(topic='HotPush TOP10排行榜',requirements='不是平台热度排名。不要改成分类对照，不写安装教程。').form=='ranking')
check('simple styling does not turn all topics into ranking',build_brief(topic='如何制作会议清单',requirements=studio.requirements(studio.StudioInput(request_id=uuid4(),topic='如何制作会议清单',style='professional'))).form!='ranking')
check('explicit caption interval is parsed independently of page count',parse_caption_budget('两版发布文案各控制在200～260字；全稿2页')==(200,260))
check('caption upper bound is parsed',parse_caption_budget('文案最多300字')==(None,300))
check('caption count is never inferred from nutritional parameters',parse_caption_budget('水果营养每100克提供45千卡') is None)
check('old recommendation endpoint retired',client.post('/api/v1/creation/recommendations',json={'request_id':str(uuid4())}).status_code==410)
check('HotPush includes all13 upstream builtin sources',len(SOURCES)==13)
raw={'source':'zhihu','source_name':'知乎热榜','updated_at':'2026-10-04T10:00:00','items':[{'id':'a','title':'原神游戏新内容','url':'https://www.zhihu.com/question/1','hot_score':None},{'id':'b','title':'GitHub新工具','url':'javascript:alert(1)','hot_score':0}]}
norm=normalize_hotlist(raw)
check('preserve upstream timestamp and original order',norm['updated_at']==raw['updated_at'] and [i['rank'] for i in norm['items']]==[1,2])
check('zero heat distinct from missing',norm['items'][0]['heat'] is None and norm['items'][1]['heat']=='0')
check('unsafe upstream links not rendered',norm['items'][1]['url'] is None)
check('game/tech filters apply without re-ranking',norm['items'][0]['categories']==['games'] and 'tech' in norm['items'][1]['categories'])
check('unknown future upstream sources accepted',normalize_hotlist({**raw,'source':'new_source','source_name':'新源'})['platform']=='new_source')
check('source identifier cannot traverse path',rejects(lambda:normalize_hotlist({**raw,'source':'../../keys'})))
studio.boards.ingest(raw);studio.boards.refresh=lambda:None
hot=client.get('/api/v1/studio/trends').json()
check('all sources shown including unavailable ones',len(hot['sources'])==13 and any(s['state']=='unavailable' for s in hot['sources']))
check('category filters retain source sequence',client.get('/api/v1/studio/trends?category=tech').json()['sources'][0]['items'][0]['rank']==2)
check('invalid category rejected',client.get('/api/v1/studio/trends?category=invalid').status_code==422)
check('no platform login connection endpoint',client.post('/api/v1/studio/sources/zhihu/connect',headers=headers,json={}).status_code==404)
check('HotPush settings protected by local action',client.put('/api/v1/studio/hotpush',json={'base_url':'http://127.0.0.1:3001'}).status_code==403)
check('remote HTTP config rejected',client.put('/api/v1/studio/hotpush',headers=headers,json={'base_url':'http://outside.invalid'}).status_code==422)
check('credentials in feed URL rejected',client.put('/api/v1/studio/hotpush',headers=headers,json={'base_url':'https://key@outside.invalid'}).status_code==422)

check('hot snapshot and user materials overflow gives clear validation error',client.post('/api/v1/studio/produce',headers=headers,json={'request_id':str(uuid4()),'topic':'原神游戏新内容','run_mode':'local_seed','trend_id':norm['items'][0]['id'],'materials':'字'*16000}).status_code==422)
# Local SSE contract covers aggregation only; no request goes to source platforms.
from unittest.mock import patch
stream_boards=TrendBoards(tmp/'sse');stream_boards.configure('http://127.0.0.1:3001')
old_board=stream_boards.ingest({**raw,'source':'weibo'})
future={**raw,'source':'future_source','source_name':'新增来源','items':[{'id':str(i),'title':f'新增热点{i}','url':'https://example.invalid/'} for i in range(160)]}
events=[('start',{'total':14}),('hotlist',raw),('hotlist',future),('failed',{'source_id':'weibo','source_name':'微博热搜'}),('progress',{'completed':14,'total':14,'success':2}),('done',{})]
network_calls=[]
class StreamResponse:
 status_code=200
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def iter_lines(self):
  for name,data in events:
   yield 'event: '+name;yield 'data: '+json.dumps(data,ensure_ascii=False);yield ''
class SSEClient:
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def stream(self,method,url,**kw):network_calls.append((method,url));return StreamResponse()
with patch('app.services.trend_boards.httpx.Client',return_value=SSEClient()):
 stream_boards.refresh();stream_boards._thread.join(timeout=5)
stream_result=stream_boards.all()
check('SSE requests only configured HotPush aggregation endpoint',network_calls==[('GET','http://127.0.0.1:3001/api/hot/stream')])
check('SSE admits every source including custom source',len(stream_result['sources'])==14 and stream_result['progress']['total']==14)
check('SSE keeps all160 returned items without sample truncation',len(next(s for s in stream_result['sources'] if s['platform']=='future_source')['items'])==160)
stale=next(s for s in stream_result['sources'] if s['platform']=='weibo')
check('failed source keeps old snapshot with original time',stale['state']=='stale' and stale['fetched_at']==old_board['fetched_at'] and stale['items']==old_board['items'])
check('SSE completion is finite and records progress',not stream_result['refreshing'] and stream_result['progress']['success']==2)

body={'request_id':str(uuid4()),'topic':topic,'requirements':'不要改成使用教程','density':'short','style':'lively','pages':6,'run_mode':'local_seed'}
q=client.post('/api/v1/studio/produce',headers=headers,json=body)
check('manual topic queues without recommendation',q.status_code==202)
queued=q.json();cid=queued['content_id']
check('topic text pages override conflicting dropdown',queued['creative_brief']['page_max']==2)
check('topic unchanged and TOP10 retained',queued['creative_brief']['rank_count']==10)
check('same request reuses single task',client.post('/api/v1/studio/produce',headers=headers,json=body).json()['reused'])
edited=client.post('/api/v1/studio/produce',headers=headers,json={**body,'topic':'换个主题'})
check('edited studio topic starts a separate task without overwriting',edited.status_code==202 and edited.json()['run_id']!=queued['run_id'])
with creation.SessionFactory() as s:
 job=s.get(Job,queued['queued_job_id']);req=job.output_refs['request']
 check('no automatic topic replacement in queued request',req['topic']==topic and req['topic_locked'])
 check('density and style actually reach queued work','信息密度' in req['user_requirements'] and '活跃' in req['user_requirements'])
 # Remove only this isolated test queue before the protocol worker test.
 for isolated in (queued,edited.json()):
  s.get(Job,isolated['queued_job_id']).state='cancelled';s.get(production.Run,isolated['run_id']).state='cancelled'
 s.commit()

spec={'kind':'rank','title':'完整TOP10','items':[{'label':f'owner{i}/skill-repository','rank':i,'detail':f'示例指标{i}，本地测试对象。','icon':'page'} for i in range(1,11)],'takeaway':'本地协议模拟，不代表真实热门仓库。'}
check('rank supports full repository names',VisualSpec.model_validate(spec).items[0].label=='owner1/skill-repository')
pages=[{'visual':spec}]
check('complete10 objects accepted',_valid_rank_layout(pages,b) is None)
bad=deepcopy(pages);bad[0]['visual']['items'].pop();check('missing entry rejected',bool(_valid_rank_layout(bad,b)))
bad=deepcopy(pages);bad[0]['visual']['items'][1]['label']=bad[0]['visual']['items'][0]['label'];check('duplicate objects rejected',bool(_valid_rank_layout(bad,b)))
bad=deepcopy(pages);bad[0]['visual']['items'][1]['rank']=1;check('duplicate rank rejected',bool(_valid_rank_layout(bad,b)))
split=deepcopy(spec);split['items']=split['items'][5:]
check('rank number continues across pages','rank-badge">6<' in _diagram(VisualSpec.model_validate(split)))
check('one-page leaderboard does not require separate cover','只有1页' in _form_prompt_block(b,{'min_pages':1,'max_pages':1}))

from app.services.renderer import PlaywrightRenderer,verify_images
one={'platform':'xiaohongshu','form':'ranking','title':'热点来源TOP10','caption':'十个来源与用途，仅为排版测试。','pages':[{'index':1,'layout':'cover','heading':'10个热点来源名次与用途','kicker':'选题素材清单','body':['名次为本次编辑顺序，非平台热度排名。','每个来源配一句用途，便于按方向找选题。'],'claim_ids':[],'footnote':'顺序为编辑顺序；未核验各平台实时可用性。','visual':{'kind':'rank','title':'10个热点来源与选题方向','items':[{'label':name,'rank':i,'detail':'热门问题聚合，适合问答式选题与观点讨论类内容。','icon':'page'} for i,name in enumerate(['知乎热榜','微博热搜','B站热搜','掘金热榜','IT之家热榜','少数派','NodeSeek','豆瓣热映','豆瓣新书','澎湃新闻'],1)],'takeaway':'10个来源覆盖问答、社会、视频、技术、数码、工具、社区、影视、阅读与新闻方向。'}}]}
rendered=PlaywrightRenderer(tmp/'dense-ranking').render_platform(one,engineering_default('xiaohongshu'),display_id='SINGLE-TOP10')
check('one-page TOP10 fits with full explanations and footer',rendered.passed and len(rendered.images)==1 and not verify_images(rendered,tmp/'dense-ranking'))
check('render keeps all ten names and numbers',len(one['pages'][0]['visual']['items'])==10 and all(i['rank']==n for n,i in enumerate(one['pages'][0]['visual']['items'],1)))
cap_brief=build_brief(topic='TOP10排行榜1～2页',requirements='两版发布文案各控制在200～260字')
cap_raw={k:v for k,v in one.items() if k!='form'};cap_raw['caption']='字'*261
cap_svc=ComposeService(creation.SessionFactory,profiles=object())
source_note_case={'caption':'来源：IT之家转述央视新闻；未公布信息以官方发布为准。','pages':[{'footnote':'来源：IT之家转述央视新闻。'},{'footnote':'尚未公布：以官方发布为准。'}]}
_dedupe_source_footnotes(source_note_case)
check('caption attribution removes duplicate source notes from all poster pages',all(not p['footnote'] for p in source_note_case['pages']))
repeat_note_case={'caption':'本稿整理了四项合作方向。','pages':[{'footnote':'依据：公开报道'},{'footnote':'依据：公开报道'}]}
_dedupe_source_footnotes(repeat_note_case)
check('without caption attribution only one source footnote survives',sum(bool(p['footnote']) for p in repeat_note_case['pages'])==1)
check('default caption budget is advisory and retains complete text',len(cap_svc._validate_variant(cap_raw,known=set(),profile=engineering_default('xiaohongshu'),platform='xiaohongshu',brief=cap_brief).caption)==261)
cap_svc.skills.instructions=lambda *a,**kw:'发布文案长度处理：严格'
check('overlong caption is blocked before rendering despite model approval',rejects(lambda:cap_svc._validate_variant(cap_raw,known=set(),profile=engineering_default('xiaohongshu'),platform='xiaohongshu',brief=cap_brief)))
cap_raw['caption']='字'*199
check('explicit caption minimum is enforced',rejects(lambda:cap_svc._validate_variant(cap_raw,known=set(),profile=engineering_default('xiaohongshu'),platform='xiaohongshu',brief=cap_brief)))
cap_raw['caption']='字'*259+'\n'
check('all spaces and newlines count toward caption limit',len(cap_svc._validate_variant(cap_raw,known=set(),profile=engineering_default('xiaohongshu'),platform='xiaohongshu',brief=cap_brief).caption)==260)
# Oversized caption repair uses one small model operation; complete pages survive untouched.
from app.services.compose_service import MasterDraft
capt_calls=[]
class CaptionRuntime:
 def complete_text(self,**kw):capt_calls.append(kw);return types.SimpleNamespace(id='caption-test'),AdapterResult(ok=True,parsed={'caption':'字'*230})
caption_service=ComposeService(creation.SessionFactory,profiles=object(),runtime=CaptionRuntime())
caption_service.skills.instructions=lambda *a,**kw:'发布文案长度处理：严格'
caption_input=deepcopy(cap_raw);caption_input['caption']='字'*301
caption_service._generate_variant=lambda *a,**kw:deepcopy(caption_input)
master=MasterDraft(audience_problem='需要完整排行榜',core_viewpoint='热点来源TOP10',claim_ids=[],limitations=[],actions=['查看榜单'],pages=[{'index':i,'heading':'榜单','points':['完整对象'],'claim_ids':[]} for i in (1,2)])
cap_draft,rounds=caption_service.compose_platform(master,'xiaohongshu',engineering_default('xiaohongshu'),content_id='caption-isolated',run_mode=RunMode.REAL,creative_brief=cap_brief)
expected_caption_pages=deepcopy(caption_input['pages'])
for page in expected_caption_pages:
 page['visual'].update(presentation=cap_brief.template_id,presentation_version=cap_brief.template_version)
check('caption repair preserves every generated page and object with frozen presentation metadata',cap_draft.pages==expected_caption_pages and len(cap_draft.caption)==230)
check('caption repair is one explicit bounded field request',len(capt_calls)==1 and capt_calls[0]['json_schema']['properties']['caption']['maxLength']==260 and capt_calls[0]['max_tokens']==2048 and rounds==0)

plan={'audience':'测试读者','objective':'完整回答原题','required_elements':['十个具体对象'],'acceptance_checks':['完整TOP10且不超页'],'pages':[{'index':i,'heading':'完整榜单','purpose':'列出具体对象','points':['必须回答用户原题'],'visual_type':'diagram','visual_brief':'完整名次版面','claim_ids':[]} for i in range(1,6)],'blocking_gaps':[]}
calls=[]
class BadRuntime:
 def complete_text(self,**kw):calls.append(kw);return types.SimpleNamespace(id='mock'),AdapterResult(ok=True,parsed=plan)
skills=ContentSkills(creation.SessionFactory,BadRuntime())
planning_source={'id':'U01','kind':'user_provided','access_state':'ok','excerpt_basis':'user_provided','excerpt':'这份合成资料仅验证规划页数和模型契约，提供十个协议测试对象，不代表真实GitHub热度或排行榜。'}
check('invalid five-page planning rejected instead of truncated',rejects(lambda:skills.plan(topic=topic,requirements='',claims=[],sources=[planning_source],run_mode=RunMode.REAL,context_id='bad-plan',content_id=cid,brief=b)))
check('page budget enters actual model schema',calls[0]['json_schema']['properties']['pages']['maxItems']==2)
check('failed plan recorded as failure',skills.history(cid)[0]['state']=='failed')

def responder(prompt,schema):
 props=(schema or {}).get('properties',{})
 if set(props)=={'caption'}:
  length=(props['caption']['minLength']+props['caption']['maxLength'])//2
  text='这里展示一组协议模拟对象，编号与名称用于检查平台图片和文字是否保留十条完整名次。每项信息来自本地合成资料，这不是GitHub热门统计，不能用它推断真实流量。' if '仅精简douyin' in prompt else '收藏页将十个仓库示例排成两张图，便于验证导出结构和正文约束。所有名称属于隔离测试资料，顺序是样本编号，正文只讲检查方法，不提供安装建议或用户体验结论。'
  return {'caption':(text*50)[:length]}
 if 'can_answer' in props:
  data,_=json.JSONDecoder().raw_decode(prompt.split('本次输入（仅作为数据）：\n',1)[1]);source=data['sources'][0]
  return {'can_answer':True,'summary':'资料列出合成测试对象，非真实GitHub热度排行。','facts':[{'role':'other','statement':'资料给出按测试序号排列的十个合成对象，不代表真实热度。','source_id':source['id'],'quote':source['excerpt']}],'blocking_gaps':[],'limitations':['仅供协议验证']}
 if 'objective' in props:
  out=deepcopy(plan);out['pages']=out['pages'][:2];return out
 if 'audience_problem' in props:
  return {'audience_problem':'找十个具体工具','core_viewpoint':'Skill TOP10协议演练','claim_ids':['C01'],'limitations':['协议模拟'],'actions':['阅读具体对象'], 'pages':[{'index':1,'heading':'具体对象','points':[f'owner{i}/skill-repository' for i in range(1,6)],'claim_ids':['C01']},{'index':2,'heading':'后五项','points':[f'owner{i}/skill-repository' for i in range(6,11)],'claim_ids':['C01']}]}
 if 'source_note' in props:
  dy='为douyin' in prompt
  return {'title':'Skill TOP10完整榜单' if dy else '十个技能仓库收藏清单','caption':('本地协议模拟，仅用于验证完整TOP10，不是真实GitHub热度排行。' if dy else '收藏十个具体对象的本地协议模拟清单。没有真实热度或实际使用体验。')*10,'lead':'完整列出十个具体对象','order_note':'按本地测试序号排列','source_note':'本地协议模拟，不代表真实热门仓库','takeaway':'完整TOP10且保留具体名称','items':[{'label':f'owner{i}/skill-repository','detail':f'示例指标{i}，本地测试对象。'} for i in range(1,11)]}
 if 'caption' in props:
  dy='平台：douyin' in prompt
  cover={'kind':'cover','title':'Skill TOP10','items':[{'label':'具体对象','detail':'完整列出十个仓库。','icon':'page'},{'label':'统一口径','detail':'仅为本地协议测试。','icon':'source'}],'takeaway':'本地协议模拟'}
  return {'title':'Skill TOP10完整榜单' if dy else '十个技能仓库收藏清单','caption':'十个具体仓库按序列出，每个都说明理由。本地协议模拟，不是真实热度榜。' if dy else '收藏这份十项清单，逐个了解工具用途。本地协议模拟示例，没有真实热度结论。','pages':[{'index':1,'layout':'cover','heading':'Skill TOP10','body':['完整列出十个对象'],'claim_ids':['C01'],'visual':cover},{'index':2,'layout':'checklist','heading':'完整十个名次','body':['源内名次连续'],'claim_ids':['C01'],'visual':spec}]}
 return None
server,network=start_mock(responder)
try:
 client.post('/api/v1/provider-configs',json={'name':'local ranking protocol','kind':'text','adapter_type':'openai_compatible','base_url':f'http://127.0.0.1:{server.server_port}/v1','model_id':'ranking-stub','api_key':'local-test-key','enabled':True,'allow_localhost':True})
 request={**body,'request_id':str(uuid4()),'run_mode':'real','materials':'本地协议模拟的10个对象，按测试序号排列；非真实GitHub热度。\n'+'\n'.join(f'第{i}项 owner{i}/skill-repository，示例指标{i}，本地测试对象。' for i in range(1,11))}
 q=client.post('/api/v1/studio/produce',headers=headers,json=request)
 check('configured model uses direct production HTTP path',q.status_code==202)
 queued=q.json();Worker(creation.SessionFactory,get_settings(),worker_id='direct-test').tick()
 result=client.get('/api/v1/runs/'+queued['run_id']).json()
 check('whole TOP10 two-page pipeline completes with full inventory: '+str(result.get('error')),result['state']=='succeeded')
 detail=client.get('/api/v1/contents/'+queued['content_id']).json();base=detail['active_revision_id']
 rev=next(v for v in detail['revisions'] if v['revision_id']==base)
 check('rendered both platforms exactly two pages',len(rev['platforms'])==2 and all(len(v['pages'])==2 for v in rev['platforms']))
 check('every platform lists all10 distinct entries',all(sum(len(p.get('visual',{}).get('items',[])) for p in v['pages'] if p.get('visual',{}).get('kind')=='rank')==10 for v in rev['platforms']))
 check('preview PNG is available',client.get('/api/v1/platform-revisions/'+rev['platforms'][0]['platform_revision_id']+'/pages/2').status_code==200)
 check('new output still needs human approval',all(v['state']=='ready_for_review' for v in rev['platforms']))
 adjust=client.post('/api/v1/studio/contents/'+queued['content_id']+'/revise',headers=headers,json={'request_id':str(uuid4()),'base_revision_id':base,'instruction':'更短更精简','run_mode':'real'})
 check('quick revision queues real rewrite',adjust.status_code==202)
 with creation.SessionFactory() as s:
  job=s.query(Job).filter_by(run_id=adjust.json()['run_id'],stage='batch_dispatch').one();saved=job.output_refs['request']['creative_brief']
  check('shorter revision retains TOP10/page range/time period',saved['rank_count']==10 and saved['page_max']==2 and saved['time_range']==queued['creative_brief']['time_range'])
  check('repeat adjustments retain only latest instruction in brief',saved['original_requirements']=='更短更精简' and job.output_refs['request']['user_requirements'].count('本次调整：')==1)
  check('revision retains density caption limit',saved['caption_max']==300)
  check('revision retains original topic',job.output_refs['request']['topic']==topic)
 check('every protocol call appears in runtime journal',len(result['provider_calls'])==len(network))
 local_edit=ComposeService(creation.SessionFactory).apply_change_request(queued['content_id'],base_revision_id=base,instruction='抖音标题改为：TOP10十个具体仓库',run_mode=RunMode.LOCAL_SEED)
 with creation.SessionFactory() as s:
  edited=s.query(PlatformRevision).filter_by(content_revision_id=local_edit['new_revision_id'],platform='douyin').one()
  check('targeted title edits retain ranking form metadata',edited.pages_json.get('form')=='ranking')

finally:server.shutdown();server.server_close()
# Cancellation settles every child stage, rather than leaving a perpetual running job.
from app.core.errors import TaskStopped
from app.models.entities import Run
with creation.SessionFactory() as s:
 item=ContentItem(topic='取消任务测试',display_id='TEST-CANCEL',state='queued',run_mode='local_seed');s.add(item);s.flush()
 run=Run(content_id=item.id,stage='produce',state='running',mode='local_seed');s.add(run);s.flush()
 job=Job(run_id=run.id,stage='batch_dispatch',state='running',input_hash='cancel-test',output_refs={'request':{'content_id':item.id,'run_mode':'local_seed','platforms':['douyin']}});s.add(job);s.flush()
 child=Job(run_id=run.id,stage='compose',state='running',input_hash='child-test');s.add(child);s.commit();jid,rid=job.id,run.id
worker=Worker(creation.SessionFactory,get_settings(),worker_id='cancel-test')
def stop_produce(**kw):raise TaskStopped('cancelled')
worker.production.produce=stop_produce
check('worker cancellation returns explicit stopped result',worker._run_job({'job_id':jid,'stage':'batch_dispatch','fencing_token':1})['stopped']=='cancelled')
with creation.SessionFactory() as s:
 check('cancelled run has no orphan running child stages',not s.query(Job).filter(Job.run_id==rid,Job.stage!='batch_dispatch',Job.state=='running').first())
fact='原题事实与完整名次。'*23
reminder=fact+'先收藏，再按你的排期逐个翻。'
check('optional reminder removal preserves entire factual text',ComposeService._tidy_caption(reminder,200,len(fact))==fact)
check('caption normalization never crops oversized facts',ComposeService._tidy_caption(fact,100,150)==fact)
from app.services.compose_service import _normalize_variant_response
metadata_raw=deepcopy(one);metadata_raw['pages'][0]['visual'].update(kind_note_placeholder=None,approval=None)
normalized=_normalize_variant_response(metadata_raw)
check('empty decorative metadata is normalized without editing original response','kind_note_placeholder' not in normalized['pages'][0]['visual'] and 'kind_note_placeholder' in metadata_raw['pages'][0]['visual'])
check('null privilege fields are still rejected rather than normalized','approval' in normalized['pages'][0]['visual'])

# A one-page TOP10 plan must not be forced into four-point tutorial constraints.
full_plan=deepcopy(plan);full_plan['pages']=full_plan['pages'][:1];full_plan['pages'][0]['points']=[f'第{i}个具体榜单对象' for i in range(1,11)]+['排序口径与资料边界']
class RankingPlanRuntime:
 def complete_text(self,**kw):
  check('ranking planning schema has no hidden point limit','maxItems' not in kw['json_schema']['$defs']['PlanPage']['properties']['points'])
  return types.SimpleNamespace(id='rank-plan'),AdapterResult(ok=True,parsed=full_plan)
full=ContentSkills(creation.SessionFactory,RankingPlanRuntime()).plan(topic='TOP10榜单恰好1页',requirements='',claims=[],sources=[planning_source],run_mode=RunMode.REAL,context_id='one-rank-plan',content_id=cid,brief=build_brief(topic='TOP10榜单恰好1页'))
check('one-page planning retains complete10 objects plus scope',len(full.pages)==1 and len(full.pages[0].points)==11)

# DeepSeek official bounded structured outputs use direct mode unless explicitly selected.
from app.services.provider_contract import ProviderConfig,ProviderKind,AdapterType
from app.services.adapters.openai_compatible import OpenAICompatibleAdapter
from app.services.adapters.base import TransportResponse
cfg=ProviderConfig(name='deepseek protocol',kind=ProviderKind.TEXT,adapter_type=AdapterType.OPENAI_COMPATIBLE,base_url='https://api.deepseek.com/v1',model_id='deepseek-flash')
sent=[]
class ThinkingTransport:
 def request(self,*args,**kw):
  sent.append(kw['json_body']);return TransportResponse(200,{'choices':[{'message':{'content':'{"value":"ok"}'},'finish_reason':'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':1}})
a=OpenAICompatibleAdapter(cfg,api_key='fake-local-only',transport=ThinkingTransport());a.complete('JSON结果')
check('official DeepSeek automatic mode disables unbounded thinking',sent[-1]['thinking']=={'type':'disabled'})
other=cfg.model_copy(update={'base_url':'https://other.invalid/v1'});OpenAICompatibleAdapter(other,api_key='fake',transport=ThinkingTransport()).complete('JSON结果')
check('other services keep existing automatic behavior','thinking' not in sent[-1])
explicit=other.model_copy(update={'thinking_mode':'enabled'});OpenAICompatibleAdapter(explicit,api_key='fake',transport=ThinkingTransport()).complete('JSON结果')
check('explicit thinking choice is respected',sent[-1]['thinking']=={'type':'enabled'})
limited=a._interpret(TransportResponse(200,{'choices':[{'message':{'content':None},'finish_reason':'length'}],'usage':{'completion_tokens':8192}}),1)
check('output limit never counts as successful response',not limited.ok and limited.error_code=='OUTPUT_LIMIT' and limited.output_tokens==8192)
empty=a._interpret(TransportResponse(200,{'choices':[{'message':{'content':''},'finish_reason':'stop'}]}),1)
check('empty final answer is an explicit settled failure',not empty.ok and empty.error_code=='EMPTY')
config=client.post('/api/v1/provider-configs',json={'name':'thinking save only','kind':'text','adapter_type':'openai_compatible','base_url':'https://other.invalid/v1','model_id':'test','thinking_mode':'disabled'}).json()
check('thinking config can be saved without paid call',config['item']['thinking_mode']=='disabled' and config['called_provider'] is False)
patch_result=client.patch('/api/v1/provider-configs/'+config['item']['id'],json={'thinking_mode':'enabled'}).json()
check('thinking config can be edited without losing fields',patch_result['item']['thinking_mode']=='enabled' and patch_result['item']['model_id']=='test')
check('invalid thinking setting is clear validation error',client.patch('/api/v1/provider-configs/'+config['item']['id'],json={'thinking_mode':'invalid'}).status_code==422)
print(f'结果：{passed} 通过 / 0 失败')
