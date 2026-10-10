"""Isolated protocol/configuration/research tests; no live search or paid calls."""
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-searxng-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'),
                  CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}")
from app.services.provider_contract import ProviderConfig,ProviderKind,AdapterType,ProviderStatus,ProviderStore,SecretStore,RunMode
from app.services.provider_runtime import ProviderRuntime
from app.services.adapters.searxng import SearXNGAdapter
from app.services.adapters.base import TransportResponse
from app.services.research_service import ResearchResult,ResearchService
from app.services.content_skills import ResearchSearchPlan

passed=failed=0
def check(name,ok):
    global passed,failed
    passed+=int(bool(ok));failed+=int(not ok)
    print(('PASS ' if ok else 'FAIL ')+name)

class Transport:
    def __init__(self,body=None,status=200,error=None):
        self.calls=[];self.body=body;self.status=status;self.error=error
    def request(self,method,url,**kwargs):
        self.calls.append((method,url,kwargs))
        if self.error:raise self.error
        return TransportResponse(self.status,self.body)

def cfg(**kw):
    return ProviderConfig(name='Test SearXNG',kind=ProviderKind.SEARCH,adapter_type=AdapterType.SEARXNG,
                          base_url=kw.pop('base_url','http://127.0.0.1:8088'),enabled=True,
                          last_test_status=ProviderStatus.CONFIGURED_UNTESTED,**kw)

response={'results':[{'url':'https://example.org/story','title':'原始问题：Anthropic请神学家讨论Claude灵魂','content':'这里只是检索摘要',
                      'publishedDate':'2026-10-06','engines':['bing','google'],'score':2.5}],
          'unresponsive_engines':[['duckduckgo','timeout']]}
t=Transport(response);config=cfg(allow_localhost=True,search_language='zh-CN',search_engines=['bing','google'])
with patch('app.services.source_reader.validate_public_url'):
    result=SearXNGAdapter(config,transport=t).search('原题 + 灵魂 & Claude',limit=1)
method,url,params=t.calls[0];query=parse_qs(urlsplit(url).query)
check('SearXNG does not require a fake API key',result.ok and not config.requires_api_key())
check('native GET protocol is used',method=='GET' and params['json_body'] is None)
check('native endpoint is /search',urlsplit(url).path=='/search')
check('original query is preserved including punctuation',query['q']==['原题 + 灵魂 & Claude'])
check('JSON format and language are explicit',query['format']==['json'] and query['language']==['zh-CN'])
check('engine selection reaches the actual request',query['engines']==['bing,google'])
check('no authorization header when no key is set','Authorization' not in params['headers'])
check('search snippet is mapped without promoting it to body',result.parsed['results'][0]['snippet']=='这里只是检索摘要' and 'excerpt_basis' not in result.parsed['results'][0])
check('publication date and engine attribution survive',result.parsed['results'][0]['published_at']=='2026-10-06' and result.parsed['results'][0]['engines']==['bing','google'])
check('partial engine failure is preserved',result.meta['unresponsive_engines']==[['duckduckgo','timeout']])
origin='https://m.weibo.cn/search?containerid=example'
read={'url':'https://example.org/story','state':'readable','body':'Anthropic请神学家讨论Claude，这是一段隔离测试原文。'*20,
      'sha256':'frozen-body-hash','final_url':'https://example.org/story'}
lab_response={'query':'Anthropic Claude','results':[{**response['results'][0],'read':read}],
              'query_report':{'id':'0123456789abcdef','query':'Anthropic Claude','original':{'url':origin,'provided':True},'model_calls':0}}
with patch('app.services.source_reader.validate_public_url'):
    lab_transport=Transport(lab_response)
    handoff=SearXNGAdapter(config,transport=lab_transport).search('Anthropic Claude',source_url=origin)
check('independent query receives HotPush original URL',parse_qs(urlsplit(lab_transport.calls[0][1]).query)['source_url']==[origin])
check('independent body survives adapter without becoming a snippet',handoff.ok and handoff.parsed['results'][0]['read']==read)
check('query report identifier survives diagnostics',handoff.meta['search_backend']=='query_lab' and handoff.meta['query_report']['id']=='0123456789abcdef')
english_query='OpenAI 722 mathematical manuscripts near-Riemann hypothesis announcement'
english_lab={'query':english_query,'results':[{'url':'https://www.unite.ai/math-722','title':'OpenAI Releases 722 Math Manuscripts From an Unreleased AI Model',
    'content':'OpenAI published 722 math manuscripts organized into 372 families.',
    'read':{'url':'https://www.unite.ai/math-722','state':'readable','body':'OpenAI published 722 math manuscripts organized into 372 families. '*20,
            'sha256':'math-hash','final_url':'https://www.unite.ai/math-722'}}],
    'query_report':{'id':'math-722-report','query':english_query,'readable_found':True}}
with patch('app.services.source_reader.validate_public_url'):
    english_handoff=SearXNGAdapter(config,transport=Transport(english_lab)).search(english_query)
check('queryer relevance is not discarded by the stricter generic English title filter',
      english_handoff.ok and english_handoff.parsed['results'][0]['read']['state']=='readable')
with patch('app.services.source_reader.validate_public_url'):
    mismatch=SearXNGAdapter(config,transport=Transport({**lab_response,'query_report':{'query':'另一议题'}})).search('Anthropic Claude')
check('mismatched independent query report is rejected',not mismatch.ok and mismatch.error_code=='BAD_JSON')
class LabRuntime:
    def search_provider(self):return config
    def search(self,query,**kwargs):
        self.last_source=kwargs.get('source_url')
        return type('Call',(),{'id':'isolated-query'})(),handoff
lab_runtime=LabRuntime();lab_service=ResearchService(lab_runtime)
from app.services.research_service import SourceModel
lab_research=ResearchResult(topic='Anthropic Claude',run_mode=RunMode.REAL,
    sources=[SourceModel(id='U01',kind='hotpush_context',url=origin,excerpt='HotPush快照',excerpt_basis='user_provided')])
with patch('app.services.source_reader.read_source_detail',side_effect=AssertionError('正文应由独立查询器提供，不应再次读取')) as no_reread:
    lab_service.discover(lab_research,max_sources=8)
check('workflow passes selected link into independent search',lab_runtime.last_source==origin)
check('workflow consumes frozen acquired body without re-fetching',not no_reread.called and lab_research.sources[-1].excerpt==read['body'])
check('workflow retains full-text provenance and hash',lab_research.sources[-1].excerpt_basis=='full_text' and lab_research.sources[-1].sha256=='frozen-body-hash')
check('workflow retains independent diagnostic report',any(t.get('query_report',{}).get('id')=='0123456789abcdef' for t in lab_research.search_trace))
check('missing usage is unknown rather than zero',result.input_tokens is None and result.usage_raw is None)
with patch('app.services.source_reader.validate_public_url'):
    t2=Transport(response);SearXNGAdapter(cfg(base_url='https://example.org/tools/search'),api_key='test-key',transport=t2).search('x')
check('explicit /search endpoint is not doubled',urlsplit(t2.calls[0][1]).path=='/tools/search')
check('optional protected instance key is supported',t2.calls[0][2]['headers']['Authorization']=='Bearer test-key')
with patch('app.services.source_reader.validate_public_url'):
    clean=SearXNGAdapter(config,transport=Transport({'results':[None,{}, {'url':'file:///secret'},response['results'][0],response['results'][0]]})).search('x')
check('malformed and non-HTTP links are excluded',clean.ok and clean.meta['dropped_count']==3)
check('duplicate result links are deduplicated',len(clean.parsed['results'])==1)
with patch('app.services.source_reader.validate_public_url'):
    noisy=[{'url':'https://example.org/noise/'+str(i),'title':'Gmail login help','content':'How to access your account','engines':['bing']} for i in range(10)]
    ranked=SearXNGAdapter(config,transport=Transport({'results':noisy+response['results']})).search('Anthropic 神学家 Claude 灵魂',limit=1)
    unrelated=SearXNGAdapter(config,transport=Transport({'results':noisy})).search('Anthropic 神学家 Claude 灵魂')
check('C047 unrelated results cannot hide later relevant news',ranked.ok and ranked.parsed['results'][0]['url']=='https://example.org/story' and ranked.meta['irrelevant_count']==10)
check('C047 irrelevant-only response triggers fallback instead of success',not unrelated.ok and unrelated.error_code=='EMPTY')
with patch('app.services.source_reader.validate_public_url'):
    malformed=SearXNGAdapter(config,transport=Transport({'results':[{'url':'http://['},{'url':'https://key@example.org/a'},response['results'][0]]})).search('x')
check('malformed URLs and credential links never crash or enter output',malformed.ok and malformed.meta['dropped_count']==2 and len(malformed.parsed['results'])==1)
for status,code in [(403,'AUTH'),(429,'RATE_LIMIT'),(500,'SERVER_ERROR'),(302,'SERVER_ERROR')]:
    with patch('app.services.source_reader.validate_public_url'):
        err=SearXNGAdapter(config,transport=Transport({},status)).search('x')
    check(f'HTTP {status} is not reported as available',not err.ok and err.error_code==code)
    if status==403:check('403 explains JSON format configuration','search.formats' in err.error_message)
for body,code in [(None,'BAD_JSON'),([], 'BAD_JSON'),({'results':[],'unresponsive_engines':[['bing','blocked']]},'EMPTY')]:
    with patch('app.services.source_reader.validate_public_url'):
        err=SearXNGAdapter(config,transport=Transport(body)).search('x')
    check(f'invalid or empty response: {code}',not err.ok and err.error_code==code)
with patch('app.services.source_reader.validate_public_url'):
    err=SearXNGAdapter(config,transport=Transport(error=TimeoutError())).search('x')
check('timeout is visible and not retried',not err.ok and err.error_code=='NETWORK_TIMEOUT')
blocked=Transport(response)
with patch('app.services.source_reader.socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',8088))]):
    err=SearXNGAdapter(cfg(),transport=blocked).search('x')
check('localhost requires explicit opt-in',err.error_code=='BAD_URL' and not blocked.calls)
bad=Transport(response)
err=SearXNGAdapter(cfg(base_url='https://example.org/?password=x'),transport=bad).search('x')
check('query-bearing instance URL is rejected before a call',err.error_code=='BAD_URL' and not bad.calls)
store=ProviderStore(tmp/'providers.json');store.upsert(config)
runtime=ProviderRuntime(store,SecretStore(tmp/'keys.json'),transport_factory=lambda cfg:t)
check('keyless search can be selected by runtime',runtime.search_provider().id==config.id)
check('dry-run requires no network or fake secret',runtime.test_connection(config,dry_run=True)['status']=='configured_untested')
check('public configuration honestly says no key is needed',config.public_view()['requires_api_key'] is False and config.public_view()['secret_configured'] is False)
generic=ProviderConfig(name='Old HTTP',kind=ProviderKind.SEARCH,adapter_type=AdapterType.SEARCH_HTTP_JSON,base_url='https://example.org',enabled=True)
check('legacy key-requiring adapters remain unchanged',generic.status()==ProviderStatus.UNCONFIGURED and runtime._missing_fields(generic)==['api_key'])

from fastapi.testclient import TestClient
from app.main import app
from app.api import providers
client=TestClient(app)
created=client.post('/api/v1/provider-configs',json={'name':'Local search','kind':'search','adapter_type':'searxng','base_url':'http://127.0.0.1:8088','enabled':True,'allow_localhost':True,'search_engines':['bing'],'search_language':'zh-CN'})
item=created.json()['item'];cid=item['id']
check('API accepts a keyless SearXNG configuration',created.status_code==201 and item['status']=='configured_untested')
check('save never makes a network request',created.json()['called_provider'] is False)
check('language and engine settings round trip',item['search_language']=='zh-CN' and item['search_engines']==['bing'])
with patch.object(providers._runtime,'_build_adapter',return_value=SearXNGAdapter(config,transport=t)),patch('app.services.source_reader.validate_public_url'),patch('app.services.source_reader.read_source_detail',return_value=('隔离测试正文'*30,'test-hash','https://example.org/story')):
    probe=client.post(f'/api/v1/provider-configs/{cid}/search-probe',json={'query':'原始问题'})
    no_read=client.post(f'/api/v1/provider-configs/{cid}/search-probe',json={'query':'原始问题','read_articles':False})
check('diagnostic probe reports actual body acquisition',probe.status_code==200 and probe.json()['readable_count']==1 and probe.json()['results'][0]['body_chars']==180)
check('search probe does not invoke an LLM',probe.json()['called_text_model'] is False)
check('real search probe updates connection availability',providers._store.get(cid).last_test_status==ProviderStatus.AVAILABLE)
check('search-only probe does not pretend to read articles',no_read.json()['readable_count']==0 and no_read.json()['results'][0]['body_state']=='not_read')
with patch.object(providers._runtime,'_build_adapter',return_value=SearXNGAdapter(config,transport=t)),patch('app.services.source_reader.validate_public_url'),patch('app.services.source_reader.read_source_detail',side_effect=ValueError('文章访问被拒')):
    probe=client.post(f'/api/v1/provider-configs/{cid}/search-probe',json={'query':'原始问题'})
check('article failure is distinct from search failure',probe.json()['ok'] and probe.json()['readable_count']==0 and probe.json()['results'][0]['body_state']=='failed')
check('empty diagnostic query is rejected',client.post(f'/api/v1/provider-configs/{cid}/search-probe',json={'query':''}).status_code==422)

frozen=ResearchResult(topic='新闻题',run_mode=RunMode.REAL,sources=[{'id':'W01','kind':'public_web','url':'https://example.org/story','access_state':'ok','excerpt_basis':'full_text','excerpt':'隔离测试：旧版本冻结文章正文，记录报道的原始内容而不是标题或摘要。'*3}])
with patch.object(runtime,'search') as search,patch('app.services.public_research.search_public') as fallback:
    reused=ResearchService(runtime).research(topic='新闻题',run_mode=RunMode.REAL,existing_result=frozen)
check('configured search revisions reuse frozen readable evidence',not search.called and not fallback.called and not reused.search_executed)
check('reuse is disclosed rather than claimed as fresh search',any('未重新检索' in note for note in reused.limitations))

# ---- 本机地址绝不能被系统代理接管 ----------------------------------------
# httpx 默认 trust_env=True：环境里只要有 HTTP_PROXY，连 http://127.0.0.1:8088
# 也会被送去代理。代理连不上目标时返回 502，于是「实例根本没启动」被说成
# 「SearXNG 返回 HTTP 502；未跟随跳转」，用户会跑去核对地址是不是填错了。
from app.services.network_policy import proxies_apply
from app.services.adapters.http_transport import HttpTransport
check('本机回环地址不使用系统代理',proxies_apply('http://127.0.0.1:8088') is False)
check('localhost 不使用系统代理',proxies_apply('http://localhost:8088') is False)
check('IPv6 回环不使用系统代理',proxies_apply('http://[::1]:8088') is False)
check('内网地址不使用系统代理',proxies_apply('http://192.168.1.9:3001') is False)
check('公网地址仍交给系统代理',proxies_apply('https://cn.bing.com/search') is True)

client_kwargs=[]
class FakeResponse:
    status_code=200
    text='{"ok":true}'
    headers={}
    def json(self):return {'ok':True}
class FakeClient:
    def __init__(self,**kw):client_kwargs.append(kw)
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def request(self,*a,**kw):return FakeResponse()
with patch('app.services.adapters.http_transport.httpx.Client',FakeClient):
    HttpTransport().request('GET','http://127.0.0.1:8088/search',headers={})
    HttpTransport().request('GET','https://api.example.com/v1/chat/completions',headers={})
check('传输层对本机地址关闭环境代理',client_kwargs[0]['trust_env'] is False)
check('传输层对公网地址保留环境代理',client_kwargs[1]['trust_env'] is True)

with patch('app.services.source_reader.validate_public_url'):
    local=cfg(allow_localhost=True,search_language='zh-CN')
    proxy_hit=SearXNGAdapter(local,transport=Transport(status=502)).search('特斯拉')
    remote_hit=SearXNGAdapter(cfg(base_url='https://searx.example.com',allow_localhost=False),transport=Transport(status=502)).search('特斯拉')
    refused=SearXNGAdapter(local,transport=Transport(error=ConnectionRefusedError('refused'))).search('特斯拉')
check('本机地址上的 502 指向代理/网关而不是 SearXNG 自己',
      not proxy_hit.ok and '代理' in proxy_hit.error_message and 'SearXNG 返回 HTTP 502' not in proxy_hit.error_message)
check('公网实例的 502 才指向实例与上游引擎','上游' in remote_hit.error_message)
check('连不上时点名地址与端口，而不是含糊的“无法连接”',
      not refused.ok and '127.0.0.1:8088' in refused.error_message and '没有服务在监听' in refused.error_message)

frontend=(ROOT/'frontend/src/views/ApiSettings.html').read_text(encoding='utf-8')
check('UI exposes native SearXNG and real search diagnostics','value="searxng"' in frontend and 'search-probe' in frontend)
check('deployment only listens on the host loopback','127.0.0.1:8088:8080' in (ROOT/'deploy/searxng/compose.yml').read_text())
check('deployment explicitly enables JSON format','    - json' in (ROOT/'deploy/searxng/settings.yml.example').read_text())
print(f'结果：{passed} 通过 / {failed} 失败')
raise SystemExit(1 if failed else 0)
