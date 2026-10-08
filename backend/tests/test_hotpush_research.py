"""HotPush selected URL -> actual body -> gap-driven search. Isolated data."""
import os,sys,tempfile,json,types
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-origin-first-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",CWB_ARTIFACT_DIR=str(tmp/'artifacts'),CWB_TMP_DIR=str(tmp/'tmp'),CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'))
from fastapi.testclient import TestClient
from app.main import app
from app.api import studio,creation
from app.models.entities import Job
from app.services.research_service import ResearchService,ResearchResult
from app.services.trend_boards import TrendBoards,normalize_hotlist
from app.services.provider_contract import RunMode
from app.services.adapters.base import AdapterResult
from app.services.content_skills import ResearchSearchPlan,ContentSkills,selected_topic_references
from app.services.evidence_gate import factual_sources
count=0
def check(name,value):
 global count
 assert value,name
 count+=1;print('PASS '+name,flush=True)
url='https://www.zhihu.com/question/123456789'
topic='隔离测试：公开问答中的新技术有哪些实际用途'
board={'source':'zhihu','items':[{'id':'origin-test','title':topic,'url':url,'description':'只是热榜摘要，不是文章正文。'}]}
boards=TrendBoards(tmp);hit=boards.ingest(board)['items'][0]
check('normalizer preserves original question URL',hit['url']==url)
client=TestClient(app);headers={'X-CWB-Local-Action':'account-connection'}
with patch.object(studio,'boards',boards):
 q=client.post('/api/v1/studio/produce',headers=headers,json={'request_id':str(uuid4()),'topic':topic,'trend_id':hit['id'],'run_mode':'local_seed'})
check('selected social item queues through actual studio API',q.status_code==202)
with creation.SessionFactory() as s:request=s.get(Job,q.json()['queued_job_id']).output_refs['request']
materials=request['user_materials']
check('queued snapshot retains title and exact URL',any(topic in m['text'] and url in m['text'] for m in materials))
class Runtime:
 def __init__(self):self.calls=[]
 def search_provider(self):return types.SimpleNamespace(name='SearXNG-test',adapter_type='searxng')
 def search(self,query,**kw):
  self.calls.append(query)
  return types.SimpleNamespace(id='isolated-search'),AdapterResult(ok=True,parsed={'results':[{'url':'https://example.org/full-article','title':'公开补充资料','snippet':'仅摘要'}]})
body='这是隔离测试中的问答正文。资料直接介绍新技术的实际用途、操作步骤和适用条件；不代表真实新闻结论。'
rt=Runtime();reader_calls=[]
def readable(u,**kw):
 reader_calls.append((u,kw));return body,'mock-body-hash',u
with patch('app.services.source_reader.read_source_detail',side_effect=readable),patch('app.services.public_research.search_public') as public:
 result=ResearchService(rt).research(topic=topic,user_materials=materials,run_mode=RunMode.REAL,search_planner=lambda *a:(_ for _ in ()).throw(AssertionError('unneeded planning')))
check('selected page is read before any search',reader_calls[0][0]==url and not rt.calls and not public.called)
check('selected social page is not blocked by hot-list domain filter',reader_calls[0][1]['blocked_domains']==())
check('snapshot URL is a lead rather than evidence',next(s for s in result.sources if s.kind=='hotpush_context').url==url and len(factual_sources(result.sources_as_dicts()))==1)
original=next(s for s in result.sources if s.kind=='public_web')
check('actual article body and hash reach later model input',original.url==url and original.excerpt==body and original.sha256=='mock-body-hash' and original.excerpt_basis=='full_text')
check('original reader is traceable without claiming a search',not result.search_executed and result.search_trace[0]['tool']=='read_selected_topic' and result.search_trace[0]['original_url']==url)
check('diagnostic distinguishes origin read from user materials','已读取热榜原链接' in ResearchService.degrade_notes(result)[-1])
plan=ResearchSearchPlan(objective='补充原页未回答的参数',queries=['新技术 具体参数'],required_evidence=['参数'])
with patch('app.services.source_reader.read_source_detail',side_effect=readable),patch('app.services.public_research.search_public') as public:
 ResearchService(rt).discover(result,gaps=['原页没有参数'],planner=lambda gaps,sources:plan)
check('explicit evidence gaps use configured supplemental search',rt.calls and result.search_executed and any(t['tool']=='configured_search' for t in result.search_trace))
check('supplement retains the original reference and body',any(s.url==url and s.excerpt_basis=='full_text' for s in result.sources))
rt=Runtime();reader_calls=[]
def blocked_then_read(u,**kw):
 reader_calls.append((u,kw))
 if u==url:raise ValueError('HTTP 403: selected page rejected public access')
 return body,'supplement-hash',u
with patch('app.services.source_reader.read_source_detail',side_effect=blocked_then_read),patch('app.services.public_research.search_public') as public:
 failed=ResearchService(rt).research(topic=topic,user_materials=materials,run_mode=RunMode.REAL,search_planner=lambda *a:plan)
check('403 original automatically falls back to configured search',reader_calls[0][0]==url and rt.calls and not public.called)
check('403 remains failed provenance without becoming evidence',failed.search_trace[0]['state']=='snippet_only' and any('403' in f['message'] for f in failed.access_failures))
check('fallback acquires actual body',any(s.sha256=='supplement-hash' for s in failed.sources))
refs=selected_topic_references(failed.sources_as_dicts(),failed.search_trace)
check('failed original is retained as supplied reference',refs[0]['provided'] and refs[0]['url']==url and refs[0]['read_state']=='snippet_only')
from app.services.source_reader import extract_article,unreadable_notice
try:extract_article('<html><title>Sina Visitor System</title></html>');visitor_rejected=False
except ValueError:visitor_rejected=True
check('actual Weibo visitor shell is not article evidence',visitor_rejected and unreadable_notice('Sina Visitor System'))
legacy_shell=[s.model_dump(mode='json') for s in failed.sources]
legacy_read=next(s for s in legacy_shell if s['kind']=='public_web')
legacy_read.update(excerpt='Sina Visitor System',access_state='ok',excerpt_basis='full_text')
legacy_trace=[{**failed.search_trace[0],'state':'ok','body_chars':19}]
check('old checkpoints report visitor failure accurately',selected_topic_references(legacy_shell,legacy_trace)[0]['read_state']=='snippet_only')
captured=[]
skill_service=ContentSkills(creation.SessionFactory,rt)
skill_service._model=lambda stage,inputs,*args,**kwargs:captured.append(inputs) or types.SimpleNamespace()
skill_service.research_review(topic=topic,requirements='',sources=failed.sources_as_dicts(),context_id='origin-context',content_id=None,snapshot=None,search_trace=failed.search_trace)
check('actual review input retains failed reference and usable supplemental body',captured[-1]['selected_topic_references'][0]['url']==url and captured[-1]['selected_topic_references'][0]['provided'] and any(s.get('sha256')=='supplement-hash' for s in captured[-1]['sources']))
check('review input forbids blaming missing user URL','存在url就不能声称用户没有提供链接' in captured[-1]['contract'])
missing=[{'text':'选题来源快照（仅记录HotPush返回顺序及时间，不能证明全网热度或文章观点）：'+json.dumps({'item':{'title':topic,'url':None}},ensure_ascii=False)}]
rt=Runtime()
with patch('app.services.source_reader.read_source_detail',side_effect=readable):
 empty=ResearchService(rt).research(topic=topic,user_materials=missing,run_mode=RunMode.REAL,search_planner=lambda *a:plan)
check('missing original URL starts with search',rt.calls and empty.search_executed)
rt=Runtime()
with patch('app.services.source_reader.read_source_detail',side_effect=readable):
 manual=ResearchService(rt).research(topic=topic,run_mode=RunMode.REAL,search_planner=lambda *a:plan)
check('manual factual topic still starts with search',rt.calls and manual.search_executed)
with patch('app.services.source_reader.read_source_detail') as reader:
 reused=ResearchService(Runtime()).research(topic=topic,existing_result=result,run_mode=RunMode.REAL)
check('revision does not reread frozen original',not reader.called and any('冻结' in v for v in reused.limitations))
private=[{'text':'选题来源快照（仅记录HotPush返回顺序及时间，不能证明全网热度或文章观点）：'+json.dumps({'item':{'title':topic,'url':'http://127.0.0.1:8000/private'}})}]
with patch('app.services.source_reader.httpx.Client') as network,patch('app.services.public_research.search_public',return_value=[]):
 # Search has no candidates; real URL validation must reject the local lead.
 no_search=types.SimpleNamespace(search_provider=lambda:None)
 denied=ResearchService(no_search).research(topic=topic,user_materials=private,run_mode=RunMode.REAL)
check('selected reference still rejects nonpublic networks',denied.search_trace[0]['state']=='snippet_only' and '非公开网络' in denied.search_trace[0]['message'])
check('private lead is never promoted to actual evidence',not factual_sources(denied.sources_as_dicts()))
broken=[{'text':'选题来源快照（仅记录HotPush返回顺序及时间，不能证明全网热度或文章观点）：not-json'}]
with patch('app.services.public_research.search_public',return_value=[]):
 res=ResearchService(no_search).research(topic=topic,user_materials=broken,run_mode=RunMode.REAL)
check('legacy malformed snapshot remains context and cannot crash',not factual_sources(res.sources_as_dicts()))
ui=(ROOT/'frontend/src/assets/direct-creator.js').read_text(encoding='utf-8')
check('confirmation shows source and original link','查看议题原链接' in ui and 'trendSource' in ui)
check('topic wording edits no longer clear selected origin',"if(id==='creator-topic')draft.trendId=null" not in ui)
skill=(ROOT/'backend/app/skills/content-team/research/SKILL.md').read_text(encoding='utf-8')
check('research Skill owns origin-first and supplemental policy','原文足够时直接评估和组织内容' in skill and '自定义选题没有原链接时从联网搜索开始' in skill)
print(f'结果：{count} 通过 / 0 失败')
