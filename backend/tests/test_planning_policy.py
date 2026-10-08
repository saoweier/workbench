"""Replay C035 planning and prove business limits come only from frozen Skills."""
import json,os,sys,tempfile,types
from pathlib import Path
from copy import deepcopy
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-plan-policy-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'))
from fastapi.testclient import TestClient
from app.main import app
from app.api.production import SessionFactory
from app.models.entities import Event
from app.services.content_skills import ContentSkills,ContentPlan,planning_limits
from app.services.provider_contract import RunMode
from app.services.adapters.base import AdapterResult
from app.services.evidence_gate import require_evidence,needs_grounding
from app.services.content_recipes import detect_direction
from app.services.content_forms import build_brief
from app.api.studio import StudioInput
from app.services.research_service import ResearchService
from app.core.errors import ValidationFailed
TestClient(app)
passed=0
def check(name,value):
 global passed
 assert value,name
 passed+=1;print('PASS '+name,flush=True)
def rejects(fn):
 try:fn()
 except ValidationFailed as e:return e
 raise AssertionError('Expected a truthful rejection')
class Runtime:
 def __init__(self,output):self.output=output;self.calls=[]
 def search_provider(self):return None
 def complete_text(self,**kw):
  self.calls.append(kw)
  return types.SimpleNamespace(id='mock-'+str(len(self.calls))),AdapterResult(ok=True,parsed=deepcopy(self.output))
original=json.loads((ROOT/'backend/tests/fixtures/c035-planning-response.json').read_text(encoding='utf8'))
topic='诺贝尔物理学奖即将公布   预测一下哪些议题可能得奖'
check('stored C035 response reproduces old hidden cap violation',[len(p['points']) for p in original['pages']]==[3,5,3,3])
catalog=ContentSkills(SessionFactory).catalog();snap={v['id']:v for v in catalog}
check('default editable planning Skill has no count or page limit',snap['planning']['planning_limits']=={'max_points_per_page':None,'max_pages':None})
claims=[{'id':'C01','statement':'测试用资料'}]
source={'id':'U01','kind':'user_provided','access_state':'ok','excerpt_basis':'user_provided','excerpt':'这里是用于隔离结构验证的合成正文，证明规划可以承载五个方向；不作为实际诺奖预测的事实依据。'}
def run(rt,key,brief=None,snapshot=None,sources=None):
 return ContentSkills(SessionFactory,rt).plan(topic=topic,requirements='',claims=claims,sources=[source] if sources is None else sources,
    run_mode=RunMode.REAL,context_id=key,brief=brief,snapshot=snapshot or snap)
rt=Runtime(original);result=run(rt,'original-response')
check('original five-point plan passes structure without deleting fifth direction',result.pages[1].points==original['pages'][1]['points'])
check('original completed response is not regenerated or silently repaginated',len(rt.calls)==1 and len(result.pages)==4)
schema=rt.calls[0]['json_schema']
check('schema has no hidden point cap','maxItems' not in schema['$defs']['PlanPage']['properties']['points'])
check('schema has no hidden page cap','maxItems' not in schema['properties']['pages'] and 'maximum' not in schema['$defs']['PlanPage']['properties']['index'])
check('creator API has no hidden eight-page cap','maximum' not in StudioInput.model_json_schema()['properties']['pages']['anyOf'][0])
check('user nine-page brief is not rejected before reaching Skill',build_brief(topic='教程恰好9页').page_max==9)
check('claim references remain constrained to actual available IDs',schema['$defs']['PlanPage']['properties']['claim_ids']['items']['enum']==['C01'])
many=deepcopy(original);many['pages']=[]
for n in range(9):
 p=deepcopy(original['pages'][1]);p['index']=n+1;p['points']=[f'需要保留的具体要点{i+1}' for i in range(20)];many['pages'].append(p)
rt=Runtime(many);result=run(rt,'no-other-cap')
check('planning no longer invents alternate 16-point or eight-page caps',len(result.pages)==9 and len(result.pages[8].points)==20)
for form in ['explainer','tutorial','guide','comparison','meme','ranking','directory','listicle','review']:
 rt=Runtime(original);result=run(rt,'form-'+form,brief={'form':form})
 check(form+' has no code-owned per-topic cap',len(result.pages[1].points)==5)
limited=deepcopy(snap);limited['planning']['instructions']='# 内容规划\n每页规划要点上限：4\n规划总页数上限：不限\n严格保留有依据的内容，按用户明确要求安排。';limited['planning']['version']='rule-four'
rt=Runtime(original);error=rejects(lambda:run(rt,'skill-four',snapshot=limited))
check('limit is enforced only when explicitly configured in Skill',len(rt.calls)==1 and error.details['rule_source']=='skill.planning' and error.details['actual_points']==[3,5,3,3])
check('error exposes actual frozen rule version',error.details['skill_version']=='rule-four')
check('provider schema follows editable Skill',rt.calls[0]['json_schema']['$defs']['PlanPage']['properties']['points']['maxItems']==4)
with SessionFactory() as s:
 rows=[e.payload for e in s.query(Event).filter_by(entity_type='skill_execution',entity_id='skill-four')]
check('failed trace explains rule source and actual counts',any(e['output'].get('validation_details',{}).get('actual_points')==[3,5,3,3] for e in rows))
unlimited=deepcopy(limited);unlimited['planning']['instructions']=unlimited['planning']['instructions'].replace('上限：4','上限：不限');unlimited['planning']['version']='rule-unlimited'
rt=Runtime(original);result=run(rt,'skill-unlimited',snapshot=unlimited)
check('changing only Skill removes business restriction',len(result.pages[1].points)==5 and 'maxItems' not in rt.calls[0]['json_schema']['$defs']['PlanPage']['properties']['points'])
page_cap=deepcopy(unlimited);page_cap['planning']['instructions']=page_cap['planning']['instructions'].replace('规划总页数上限：不限','规划总页数上限：1')
error=rejects(lambda:run(Runtime(original),'skill-page-limit',snapshot=page_cap))
check('page limit can also be maintained in Skill',error.details['actual_pages']==4)
two=deepcopy(original);two['pages']=two['pages'][:2]
rt=Runtime(two);result=run(rt,'user-page-priority',brief={'form':'explainer','explicit_pages':True,'page_min':2,'page_max':2},snapshot=page_cap)
check('explicit user two-page request overrides one-page general Skill rule',len(result.pages)==2 and rt.calls[0]['json_schema']['properties']['pages']['maxItems']==2)
error=rejects(lambda:run(Runtime(original),'user-page-no-trim',brief={'form':'explainer','explicit_pages':True,'page_min':1,'page_max':2}))
check('user page requirement is still enforced rather than silently trimming','4页' in str(error))
for text in ['每页规划要点上限：0','规划总页数上限：很多','每页规划要点上限：4\n每页规划要点上限：6']:
 check('invalid or ambiguous Skill rule is rejected',bool(rejects(lambda:planning_limits(text))))
service=ContentSkills(SessionFactory);old=deepcopy(snap);current=next(v for v in service.catalog() if v['id']=='planning')
saved=service.save('planning',limited['planning']['instructions'],current['version'])
check('Skill save reports machine-readable limits',saved['planning_limits']['max_points_per_page']==4)
rt=Runtime(original);run(rt,'frozen-unlimited',snapshot=old)
check('editing Skill does not change an older frozen task','maxItems' not in rt.calls[0]['json_schema']['$defs']['PlanPage']['properties']['points'])
new=service.snapshot('new-limited-task')
check('new task freezes edited Skill rule',planning_limits(new['planning']['instructions'])['max_points_per_page']==4)
for t in [topic,'诺奖获奖候选方向前瞻','预测哪些议题可能获奖','新产品即将发布，看看有哪些已公布的参数']:
 check('forward-looking factual topic requires readable evidence',needs_grounding(t) and bool(rejects(lambda:require_evidence(t,[]))))
check('Nobel topic uses news direction Skill',detect_direction(topic)=='news')
rt=Runtime(original);error=rejects(lambda:run(rt,'actual-c035-missing-evidence',sources=[{'id':'U01','kind':'editorial_plan','access_state':'ok','excerpt_basis':'user_provided','excerpt':'编辑提纲（非事实依据）：'+topic}]))
check('actual C035 evidence gap stops before further paid planning call',not rt.calls and '核心事实资料不足' in str(error))
rt=Runtime(original)
with patch('app.services.public_research.search_public',return_value=[]) as search:
 research=ResearchService(rt).research(topic=topic,user_materials=[{'text':topic,'kind':'editorial_plan'}],run_mode=RunMode.REAL)
 check('unconfigured provider performs no retired scraping and discloses it',search.call_count==0 and not research.search_executed
    and any('未执行自动搜索' in note for note in research.limitations))
 error=rejects(lambda:ContentSkills(SessionFactory,rt).research_review(topic=topic,requirements='',sources=research.sources_as_dicts(),context_id='empty-public-result',content_id=None,snapshot=old))
check('no search body cannot be marked verified or guessed by model',not rt.calls and '核心事实资料不足' in str(error))
check('ordinary creative topic is not forced into factual research',not needs_grounding('写一个温馨的虚构故事'))
print(f'结果：{passed} 通过 / 0 失败')
