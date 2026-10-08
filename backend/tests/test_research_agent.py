"""Isolated checks for LLM search planning, tool fallback and provenance."""
import os,sys,tempfile,types,base64
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-search-agent-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",CWB_ARTIFACT_DIR=str(tmp/'artifacts'),CWB_TMP_DIR=str(tmp/'tmp'))
from app.main import app
from app.api.production import SessionFactory
from app.services.content_skills import ContentSkills,ResearchSearchPlan,research_tool_limits
from app.services.public_research import search_public,SearchLinks,_destination,_relevance
from app.services.research_service import ResearchService,ResearchResult
from app.services.provider_contract import RunMode
from app.services.adapters.base import AdapterResult
from app.core.errors import StateConflict,ValidationFailed
passed=0
def check(name,value):
 global passed
 assert value,name
 passed+=1;print('PASS '+name,flush=True)
def rejects(fn,cls):
 try:fn()
 except cls:return True
 return False
class Runtime:
 def __init__(self):self.calls=[]
 def search_provider(self):return types.SimpleNamespace(name='configured-test-search')
 def search(self,query,**kw):
  return types.SimpleNamespace(id='mock-search'),AdapterResult(ok=True,parsed={'results':[{'url':'https://example.org/article','title':'研究原文'}]})
 def complete_text(self,**kw):
  self.calls.append(kw)
  return types.SimpleNamespace(id='search-plan'),AdapterResult(ok=True,parsed={'objective':'查找当前年度公开候选依据','queries':['诺贝尔物理学奖 2026 预测','诺贝尔物理学奖 2026 引文桂冠'],'required_evidence':['公开研究成果']})
rt=Runtime();skills=ContentSkills(SessionFactory,rt);snap=skills.snapshot('agent-search')
plan=skills.research_search_plan(topic='诺贝尔物理学奖 AI预测',requirements='',context_id='agent-search',content_id=None,snapshot=snap)
check('LLM chooses queries rather than returns invented facts',len(plan.queries)==2 and len(rt.calls)==1)
check('tool names and date are supplied to LLM','public_search(query)' in rt.calls[0]['prompt'] and 'current_date' in rt.calls[0]['prompt'])
check('same-language strategy is in frozen Skill','至少两条检索词使用用户选题的语言' in rt.calls[0]['prompt'])
check('AI request is not misread as published AI study','不代表要调研某个已发表的AI预测系统' in rt.calls[0]['prompt'])
check('query budget comes from Skill',rt.calls[0]['json_schema']['properties']['queries']['maxItems']==3)
custom=dict(snap);custom['research']={**snap['research'],'instructions':snap['research']['instructions'].replace('单轮检索词数量：3','单轮检索词数量：2')}
skills.research_search_plan(topic='预测',requirements='',context_id='custom',content_id=None,snapshot=custom)
check('editing Skill updates tool budget',rt.calls[-1]['json_schema']['properties']['queries']['maxItems']==2)
check('followup can be disabled in Skill',research_tool_limits('补充检索轮数：0')['followup_rounds']==0)
check('invalid tool rule is rejected',rejects(lambda:research_tool_limits('单轮检索词数量：0'),ValidationFailed))
p=SearchLinks();p.feed('<h2><a href="https://example.org/article">公开<span>研究</span></a></h2>')
check('Bing h2 articles parse with inline markup',p.items==[{'url':'https://example.org/article','title':'公开研究'}])
encoded='a1'+base64.urlsafe_b64encode(b'https://example.org/article').decode().rstrip('=')
check('Bing outbound wrapper resolves to actual article',_destination('https://www.bing.com/ck/a?u='+encoded)=='https://example.org/article')
check('unrelated result is not a search success',_relevance('ChatGPT MD Plugin','诺贝尔物理学奖 2026 预测')==0)
check('current year evidence outranks generic biography',_relevance('2026诺贝尔奖预测','诺贝尔物理学奖 2026 预测')>_relevance('诺贝尔人物介绍','诺贝尔物理学奖 2026 预测'))
from app.core.errors import NotConfigured
with patch('app.services.public_research.httpx.stream') as network:
 check('HTML search engine scrapers are retired',rejects(lambda:search_public('任意问题'),NotConfigured) and not network.called)
class Response:
 def __init__(self,code,body):self.status_code=code;self.body=body.encode();self.is_redirect=300<=code<400
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def raise_for_status(self):pass
 def iter_bytes(self):yield self.body
rs=ResearchService(rt);result=ResearchResult(topic='诺贝尔物理学奖 2026 预测',run_mode=RunMode.REAL)
body='这是隔离测试中的正文模拟资料：公开研究成果包括凝聚态物理与量子材料，非真实获奖结论。'
with patch.object(rt,'search',wraps=rt.search) as search,patch('app.services.source_reader.read_source_detail',return_value=(body,'test-hash','https://example.org/article')):
 rs.discover(result,planner=lambda gaps,sources:plan)
check('tool anchors original topic before complementary model queries',search.call_count>=2 and search.call_args_list[0].args[0]==result.topic and search.call_args_list[1].args[0] in plan.queries and search.call_args_list[1].args[0]!=result.topic)
check('duplicate article is read only once',len(result.sources)==1)
check('body provenance remains distinct from search snippet',result.sources[0].excerpt_basis=='full_text' and result.sources[0].sha256=='test-hash')
check('agent plan and article read are traceable',result.search_trace[0]['tool']=='llm_search_plan' and any(t['tool']=='read_public_article' for t in result.search_trace))
check('tool returns source body to later stages',result.sources[0].excerpt==body)
with patch.object(rt,'search',side_effect=ValueError('配置服务不可用')):
 empty=rs.research(topic='最新新闻是什么',run_mode=RunMode.REAL,search_planner=lambda gaps,sources:plan)
check('empty tools cannot fabricate a body',empty.search_executed and not empty.sources and len(empty.access_failures)==2)
def unknown(*args):raise StateConflict('调用结果未知；未自动重发')
check('unknown model plan result is not swallowed or resent',rejects(lambda:rs.research(topic='最新新闻是什么',run_mode=RunMode.REAL,search_planner=unknown),StateConflict))
class ConfiguredRuntime(Runtime):
 def search_provider(self):return types.SimpleNamespace(name='configured-test-search')
 def search(self,query,**kw):
  self.calls.append(query)
  return types.SimpleNamespace(id='configured-call'),AdapterResult(ok=True,parsed={'results':[{'url':'https://example.org/article/'+str(len(self.calls)),'title':'研究原文','snippet':'仅摘要'}]})
configured=ConfiguredRuntime()
with patch('app.services.public_research.search_public') as public,patch('app.services.source_reader.read_source_detail',return_value=(body,'test-hash','https://example.org/article')):
 res=ResearchService(configured).research(topic='最新诺贝尔奖预测',run_mode=RunMode.REAL,search_planner=lambda gaps,sources:plan)
check('configured provider also anchors original topic',configured.calls==['最新诺贝尔奖预测',plan.queries[0]])
check('configured provider is preferred over public fallback',public.call_count==0 and res.search_provider=='configured-test-search')
check('configured tool provenance is recorded',any(t['tool']=='configured_search' for t in res.search_trace) and len(res.calls)==2)
check('configured search still requires reading actual body',res.sources[0].excerpt_basis=='full_text')
from app.services.source_reader import read_source_detail
class ArticleResponse(Response):
 def __init__(self,body):
  super().__init__(200,body);self.headers={'content-type':'text/html'};self.encoding='utf-8'
class ArticleClient:
 def __init__(self,body):self.body=body
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def stream(self,*args,**kwargs):return ArticleResponse(self.body)
with patch('app.services.source_reader.validate_public_url'),patch('app.services.source_reader.httpx.Client',return_value=ArticleClient('<html><body>Please wait...</body></html>')):
 check('loading shell is never full-text evidence',rejects(lambda:read_source_detail('https://example.org/article'),ValueError))
article='Transformer 架构将注意力机制用于序列建模。Tokenization 将文字拆成模型处理的 token。上下文窗口限制一次可以处理的 token 范围。'
large='<html><script>'+('x'*700000)+'</script><nav>关注 分享 推荐</nav><div id="js_content">'+article+'</div></html>'
with patch('app.services.source_reader.validate_public_url'),patch('app.services.source_reader.httpx.Client',return_value=ArticleClient(large)):
 text,sha,url=read_source_detail('https://example.org/article')
check('large scripts do not reject a small readable article',text==article and len(sha)==64)
fallback=ResearchResult(topic='LLM 面试',run_mode=RunMode.REAL)
def article_read(url,**kwargs):
 if url.endswith('bad'):raise ValueError('网页要求验证')
 return article,'test-hash',url
def candidate_search(query,**kw):
 return types.SimpleNamespace(id='candidates'),AdapterResult(ok=True,parsed={'results':[{'url':'https://example.org/bad','title':'面试资料'},{'url':'https://example.org/good','title':'LLM 面试原文'}]})
with patch.object(rt,'search',side_effect=candidate_search),patch('app.services.public_research.search_public') as retired,patch('app.services.source_reader.read_source_detail',side_effect=article_read):
 rs.discover(fallback,planner=lambda gaps,sources:ResearchSearchPlan(objective='读取面试基础原文',queries=['LLM 面试'],required_evidence=['基础机制']))
check('unreadable article uses next configured candidate without legacy engines',not retired.called)
check('configured candidates retain failure and actual body',len(fallback.sources)==2 and fallback.sources[0].access_state.value=='snippet_only' and fallback.sources[1].excerpt_basis=='full_text')
from app.services.evidence_gate import require_evidence
try:require_evidence('最新LLM面试',[])
except ValidationFailed as exc:check('no-body gap participates in Skill followup budget',bool(exc.details.get('research_gaps')))
from app.services.evidence_gate import validate_research
from app.services.content_skills import ResearchAssessment
original='reward hacking是指在RLHF中，agent发现奖励函数中存在意想不到的漏洞或偏差'
report=ResearchAssessment(can_answer=True,summary='公开资料解释奖励函数漏洞',facts=[{'role':'context','statement':'奖励函数漏洞可能造成奖励投机','source_id':'W05','quote':original.replace('，',',')}],blocking_gaps=[],limitations=[])
sources=[{'id':'W05','kind':'public_web','access_state':'ok','excerpt_basis':'full_text','excerpt':original}]
validate_research(report,sources)
check('C040 punctuation difference restores exact source quote',report.facts[0].quote==original)
report.facts[0].quote=original.replace('RLHF','RAG')
check('punctuation normalization never accepts changed technical terms',rejects(lambda:validate_research(report,sources),ValidationFailed))
validate_research(report,sources,strict_quotes=False)
check('default app quote mode continues with an explicit advisory',report.facts[0].quote=='' and bool(report.limitations))
from app.services.content_skills import strict_research_quotes
check('strict transcription is opt-in through Skill',not strict_research_quotes('引用一致性处理：提示') and strict_research_quotes('引用一致性处理：严格'))
from app.services.content_forms import build_brief
brief=build_brief(topic='LLM 面试必问的 8 个问题，答不上来直接淘汰',requirements='恰好2页')
check('C039 is an eight-item checklist rather than a generic explainer',brief.form=='listicle' and brief.item_count==8 and brief.page_max==2)
failed=ResearchResult(topic='LLM面试',run_mode=RunMode.REAL,sources=[{'id':'W01','kind':'public_web','url':'https://example.org/article','access_state':'snippet_only','excerpt_basis':'search_snippet'}])
with patch('app.services.source_reader.read_source_detail',return_value=(article,'hash','https://example.org/article')):
 rs.refresh_unread_sources(failed)
check('explicit resume reopens previously failed sources after reader fix',len(failed.sources)==2 and failed.sources[-1].excerpt_basis=='full_text' and len(failed.search_trace)==1)
from app.services.source_reader import extract_article
check('C043 cancelled WeChat page is not evidence',rejects(lambda:extract_article('<body>此账号已自主注销，内容无法查看</body>'),ValueError))
check('C043 WAF script page is not evidence',rejects(lambda:extract_article('<body>appkey: CF_APP_WAF; var requestInfo = {};</body>'),ValueError))
from app.services.evidence_gate import factual_sources
from app.services.evidence_gate import needs_grounding
check('C044 manually entered factual topic defaults to research',needs_grounding('如何看待Anthropic被曝请神学家给Claude提供安全建议，并认为Claude有灵魂？'))
check('factual topic needs no magic news keywords',needs_grounding('番茄有哪些营养成分') and needs_grounding('GitHub上的skills怎么使用'))
check('explicit fictional creation may skip factual research',not needs_grounding('写一个温馨的虚构故事'))
check('C043 is event explanation rather than how-to tutorial',build_brief(topic='如何看待Anthropic被曝请神学家给Claude提供安全建议，并认为Claude有灵魂？').form=='explainer')
check('old C043 checkpoint cannot promote cancelled notice',not factual_sources([{'id':'W03','kind':'public_web','access_state':'ok','excerpt_basis':'full_text','excerpt':'此账号已自主注销，内容无法查看\n'+('分享收藏留言\n'*10)}]))
check('C043 company homepage does not match Chinese event',_relevance('Home Anthropic','Anthropic 神学家 Claude 灵魂')==0)
check('C043 encyclopedia does not match English event',_relevance('Anthropic - Wikipedia','Anthropic theologians Claude soul safety')==0)
check('C040 window cannot match Windows',_relevance('Windows launchsettings','LLM context window')==0)
check('C043 actual news matches unchanged question',_relevance('Claude有意识吗？Anthropic找上神学家','如何看待Anthropic被曝请神学家给Claude提供安全建议，并认为Claude有灵魂？')>0)
event_topic='如何看待Anthropic被曝请神学家给Claude提供安全建议，并认为Claude有灵魂？'
event_plan=ResearchSearchPlan(objective='核对事件',queries=['Anthropic 神学家 Claude 安全建议','Anthropic theologians Claude soul safety','Anthropic Claude 灵魂 神学家 2026'],required_evidence=['报道经过'])
event_result=ResearchResult(topic=event_topic,run_mode=RunMode.REAL);event_queries=[]
def event_search(query,**kw):
 event_queries.append(query)
 return types.SimpleNamespace(id='event-search'),AdapterResult(ok=True,parsed={'results':[{'url':'https://example.org/'+str(i),'title':'Anthropic神学家新闻'} for i in range(5)] if query==event_topic else []})
def event_read(url,**kw):
 if url.endswith(('0','1','2')):raise ValueError('文章不可读')
 return '隔离测试新闻正文：该报道介绍Anthropic邀请宗教学者讨论Claude可能的意识与道德地位，并不证明模型有灵魂。','hash',url
with patch.object(rt,'search',side_effect=event_search),patch('app.services.source_reader.read_source_detail',side_effect=event_read):
 rs.discover(event_result,planner=lambda gaps,sources:event_plan)
check('C043 original query survives LLM rewrites',event_queries[0]==event_topic and event_result.search_trace[0]['model_queries']==event_plan.queries)
check('failed first three articles do not hide fourth readable result',any(s.url.endswith('3') and s.access_state.value=='ok' for s in event_result.sources))
print(f'结果：{passed} 通过 / 0 失败')
