"""Known output corrections keep facts/page identity; uncertainty never resends."""
import os,sys,tempfile,types,json
from pathlib import Path
from copy import deepcopy
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-field-repair-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'))
from app.services.compose_service import ComposeService,MasterDraft
from app.services.content_forms import build_brief
from app.services.content_skills import ContentSkills
from app.services.profile_store import engineering_default
from app.services.provider_contract import RunMode
from app.services.adapters.base import AdapterResult
from app.api.production import SessionFactory
from app.core.errors import StateConflict,ValidationFailed
from app.services.poster_styles import display_label
from app.services.visual_content import VisualItem
passed=0
def check(name,value):
 global passed
 assert value,name
 passed+=1;print('PASS '+name,flush=True)
def rejects(fn):
 try:fn()
 except (ValidationFailed,StateConflict):return True
 return False
class Runtime:
 def __init__(self,outputs):self.outputs=outputs;self.calls=[]
 def complete_text(self,**kw):
  self.calls.append(kw);value=self.outputs[len(self.calls)-1]
  if isinstance(value,Exception):raise value
  return types.SimpleNamespace(id='call-'+str(len(self.calls))),value if isinstance(value,AdapterResult) else AdapterResult(ok=True,parsed=deepcopy(value))

good={'title':'赛制入门','caption':'三个阶段，按资料说明规则。','pages':[{'index':1,'layout':'cover','heading':'三个阶段','kicker':'赛制指南','body':[],'footnote':'资料：官方手册','claim_ids':['C01'],
 'visual':{'kind':'cover','title':'阶段示意','items':[{'label':'入围赛','detail':'四队争夺一个瑞士轮名额。','icon':'game'},{'label':'瑞士轮','detail':'十六队参赛，八队晋级。','icon':'chart'}],'takeaway':'按阶段读懂晋级规则'}}]}
bad=deepcopy(good);bad['title']='这是一个明显超过二十个字符的赛制指南标题需要精简'
bad['pages'][0]['visual']['kind']='unsupported'
original=deepcopy(bad);rt=Runtime([good]);service=ComposeService(SessionFactory,rt);generated=[]
service._generate_variant=lambda *args,**kw:generated.append(kw) or deepcopy(bad)
master=MasterDraft(audience_problem='赛制是什么',core_viewpoint='三个阶段',claim_ids=['C01'])
brief=build_brief(topic='赛制说明',requirements='做一个赛制指南，恰好1页')
draft,rounds=service.compose_platform(master,'xiaohongshu',engineering_default('xiaohongshu'),run_mode=RunMode.REAL,creative_brief=brief)
check('known invalid draft gets field repair rather than regeneration',len(generated)==1 and len(rt.calls)==1 and rounds==1)
check('actual completed draft and measured error reach repair',json.dumps(original,ensure_ascii=False) in rt.calls[0]['prompt'] and 'title_chars' in rt.calls[0]['prompt'])
check('legal page content and citations retained',draft.pages==good['pages'] or draft.pages[0]['visual']['items']==good['pages'][0]['visual']['items'])
check('validation does not mutate received response',bad==original)
schema=rt.calls[0]['json_schema']
check('schema declares actual title cap',schema['properties']['title']['maxLength']==20)
check('schema declares exact requested page budget',schema['properties']['pages']['minItems']==schema['properties']['pages']['maxItems']==1)
check('schema does not impose per-type object caps','allOf' not in schema['properties']['pages']['items']['properties']['visual'] and 'maxItems' not in schema['properties']['pages']['items']['properties']['visual']['properties']['items'])
for name,value in [('page_identity',{**deepcopy(good),'pages':[]}),('citation_identity',deepcopy(good))]:
 if name=='citation_identity':value['pages'][0]['claim_ids']=['C99']
 rt=Runtime([value]);service=ComposeService(SessionFactory,rt)
 check('field repair rejects '+name,rejects(lambda:service._repair_variant_fields(bad,'douyin','超限',{'min_pages':1,'max_pages':1},engineering_default('douyin'),brief,None,RunMode.REAL,1)))
rt=Runtime([AdapterResult(ok=False,error_code='NETWORK_TIMEOUT',error_message='unknown')]);service=ComposeService(SessionFactory,rt)
check('unknown repair result stops after one call',rejects(lambda:service._repair_variant_fields(bad,'douyin','超限',{'min_pages':1,'max_pages':1},engineering_default('douyin'),brief,None,RunMode.REAL,1)) and len(rt.calls)==1)
for label,rank,expected in [('1 生化危机',1,'生化危机'),('06、活色生香',6,'活色生香'),('1917',1,'1917'),('1号玩家',1,'1号玩家'),('2Do',2,'2Do')]:
 check('rank display preserves full name '+label,display_label(VisualItem(label=label,rank=rank,detail='示例',icon='page'),0,'rank')==expected)

text='官方手册原文：四支队伍进行入围赛，其中一支进入瑞士轮。瑞士轮共有十六支队伍，八支晋级下一阶段。'
source={'id':'W01','kind':'public_web','access_state':'ok','excerpt_basis':'full_text','excerpt':text}
report={'can_answer':True,'summary':'原文说明三个阶段的参赛和晋级数量','facts':[{'role':'context','statement':'四队争夺一个晋级名额','source_id':'W01','quote':'四支队伍进行入围赛，其中一支进入瑞士轮'}],'blocking_gaps':[],'limitations':[]}
invalid=deepcopy(report);invalid['facts'][0]['quote']='四支队伍进行入围赛……一支进入瑞士轮'
for bad_report in [invalid,{**deepcopy(report),'facts_note':'模型声称已核验'}]:
 rt=Runtime([bad_report,report]);skills=ContentSkills(SessionFactory,rt)
 snap=skills.snapshot('snapshot-'+str(passed))
 snap['research']['instructions']=snap['research']['instructions'].replace('引用一致性处理：提示','引用一致性处理：严格')
 result=skills.research_review(topic='赛制说明',requirements='',sources=[source],context_id='research-'+str(passed),content_id=None,snapshot=snap)
 check('completed citation/schema error receives one correction',len(rt.calls)==2 and result.can_answer)
 check('citation repair requires contiguous original quote','禁止省略号拼接' in rt.calls[1]['prompt'])
rt=Runtime([{**report,'can_answer':False,'blocking_gaps':['核心事实缺失']}]);skills=ContentSkills(SessionFactory,rt)
check('genuinely missing facts never trigger format correction',rejects(lambda:skills.research_review(topic='赛制说明',requirements='',sources=[source],context_id='missing',content_id=None,snapshot=skills.snapshot('missing'))) and len(rt.calls)==1)
rt=Runtime([AdapterResult(ok=False,error_code='NETWORK_TIMEOUT',error_message='unknown')]);skills=ContentSkills(SessionFactory,rt)
check('unknown research response never resends',rejects(lambda:skills.research_review(topic='赛制说明',requirements='',sources=[source],context_id='unknown',content_id=None,snapshot=skills.snapshot('unknown'))) and len(rt.calls)==1)
from app.services.meme_compiler import MemeText,compile_meme,requested_examples
from app.services.visual_content import VisualSpec
row=lambda label,detail:{'label':label,'detail':detail,'icon':'page'}
words={'title':'不烧心是什么梗','caption':'规矩体AI短剧里的一句台词，跟用时夸东西贵但靠谱。','first_heading':'先看懂原来的剧情','second_heading':'一句公式，三个例句','source_note':'剧情台词据公开记述','first_takeaway':'贵是贵点，但靠谱。','second_takeaway':'用具体场景接梗，不按辣菜解释。',
 'context':row('剧情套路','老板坚持好油好料，同行打价格战，老客回头买贵的。'),
 'classic_quote':row('经典台词','老板，你家贵是贵点，但吃了不烧心。'),
 'meaning':row('实际含义','贵一点但用料靠谱、令人放心；重复的流水线台词成为笑点。'),
 'formula':row('套用公式','你家X贵是贵点，但Y不烧心。'),
 'examples':[row('修车铺','你家修车贵是贵点，但明码实价，修完不烧心。'),row('演唱会','这票贵是贵点，但全程真唱，听完不烧心。'),row('广告博主','接广告贵是贵点，但只推真用过的，刷到不烧心。')]}
words=MemeText.model_validate(words).model_dump(mode='json');compiled=compile_meme(words,['C01'])
check('two-page meme card budget is three then four',[len(p['visual']['items']) for p in compiled['pages']]==[3,4])
check('meaning is on page one instead of displacing examples',compiled['pages'][0]['visual']['items'][-1]==words['meaning'])
check('each complete original example gets its own card',compiled['pages'][1]['visual']['items'][1:]==words['examples'])
check('meme compiler preserves the entire classic quote',compiled['pages'][0]['visual']['items'][1]['detail']==words['classic_quote']['detail'])
check('exactly one short source note survives',sum(bool(p['footnote']) for p in compiled['pages'])==1)
check('meme pages have no duplicate body summaries',all(p['body']==[] for p in compiled['pages']))
check('each compiled page satisfies unchanged visual capacity',all(VisualSpec.model_validate(p['visual']) for p in compiled['pages']))
check('meme compiler never assigns rank numbers',not any('rank' in i for p in compiled['pages'] for i in p['visual']['items']))
check('explicit example count cannot be forced back to three',requested_examples('五条例句')==5 and requested_examples('2个原创例句')==2)
check('latest example count instruction wins',requested_examples('三个例句；改成两条例句')==2)
from app.services.catalog_compiler import compile_rows
cat={'title':'演练榜单','caption':'隔离测试数据','order_note':'按聚合返回顺序','source_note':'隔离快照','takeaway':'保留名称与顺序','items':[
 {'label':'1 完整书名甲','detail':'仅测试对象保留。','icon':'page'}, {'label':'2 完整书名乙','detail':'仅测试顺序保留。','icon':'page'}]}
cat_source={'id':'U01','kind':'user_provided','access_state':'ok','excerpt_basis':'user_provided','excerpt':json.dumps({'order_basis':'HotPush聚合返回顺序','items':[{'rank':1,'title':'完整书名甲'},{'rank':2,'title':'完整书名乙'}]},ensure_ascii=False)}
def compile_cat(value=cat,sources=None):
 return compile_rows(value,strategy={'min_pages':1,'max_pages':1},brief={'rank_count':2},claim_ids=['C01'],sources=sources or [cat_source],ranking=True,verify_objects=True)
result=compile_cat()
check('real ranking verifies names and removes only generated ordinals',[i['label'] for i in result[0]['visual']['items']]==['完整书名甲','完整书名乙'])
check('ranking compilation does not mutate supplier rows',cat['items'][0]['label']=='1 完整书名甲')
swapped=deepcopy(cat);swapped['items']=list(reversed(swapped['items']));swapped['items'][0]['label']='完整书名乙';swapped['items'][1]['label']='完整书名甲'
check('wrong HotPush object order is rejected',rejects(lambda:compile_cat(swapped)))
unknown=deepcopy(cat);unknown['items'][0]['label']='不存在的书'
check('invented ranked object is rejected',rejects(lambda:compile_cat(unknown)))
check('snapshot context is never evidence for ranked objects',rejects(lambda:compile_cat(sources=[{**cat_source,'kind':'hotpush_context'}])))
limited=build_brief(topic='赛制说明',requirements='恰好1页，每项详情不超过12字')
check('explicit card detail budget becomes a contract',limited.detail_max==12)
service=ComposeService(SessionFactory,Runtime([]))
check('schema contains actual requested detail budget',service._variant_output_schema({'min_pages':1,'max_pages':1},engineering_default('douyin').limits,limited)['$defs']['VisualItem']['properties']['detail']['maxLength']==12)
too_long=deepcopy(good);too_long['pages'][0]['visual']['items'][0]['detail']='这一条详情明显超过用户指定的十二个字符限制'
check('program rejects user-specific detail overflow',rejects(lambda:service._validate_variant(too_long,known={'C01'},profile=engineering_default('douyin'),platform='douyin',brief=limited)))
from app.api import studio
from app.models.entities import ContentItem,ContentRevision
from unittest.mock import patch
from uuid import uuid4
old=build_brief(topic='演练TOP2',requirements='两页，编写剧情');captured=[]
with SessionFactory() as s:
 item=ContentItem(display_id='TEST01',topic='演练TOP2',state='drafting',run_mode='real');s.add(item);s.flush()
 rev=ContentRevision(content_id=item.id,version=1,input_hash='isolated-revision-test',brief_json={'creative_brief':old.model_dump(mode='json')});s.add(rev);s.flush();item.active_revision_id=rev.id;cid=item.id;rid=rev.id;s.commit()
instruction='只做元数据速查，详情最多30字，取消剧情要求'
with patch('app.api.studio.production.rebuild',side_effect=lambda cid,payload:captured.append(payload) or {'state':'queued'}):
 studio.revise(cid,studio.StudioRevision(request_id=uuid4(),base_revision_id=rid,instruction=instruction))
check('revision stores latest request instead of stale prose',captured[0].creative_brief.original_requirements==instruction)
check('revision keeps existing hard pages while updating detail cap',captured[0].creative_brief.page_max==2 and captured[0].creative_brief.detail_max==30)
check('revision explicitly resolves contradictory historical requests','修改优先级：' in captured[0].requirements)

# 「增加页数 / 增加封面」这类**不带数字**的改稿要求过去被解析器整个忽略：
# parse_page_budget 解析不到就沿用旧预算，于是用户反复改稿、页数一直不变
# （C031 就卡在首轮选的 1 页，小红书那一页还被封面吃掉，没有正文）。
from app.services.content_forms import adjust_page_budget,strip_page_budget_notes
grow='增加页数  增加封面'
with patch('app.api.studio.production.rebuild',side_effect=lambda cid,payload:captured.append(payload) or {'state':'queued'}):
 studio.revise(cid,studio.StudioRevision(request_id=uuid4(),base_revision_id=rid,instruction=grow))
grown=captured[-1].creative_brief
check('qualitative increase-pages request really grows the budget',
      grown.explicit_pages and grown.page_max>2 and grown.page_min>=2)
check('qualitative cover request is recorded for every platform',grown.want_cover is True)
check('rebuilt requirement states the current page budget','全稿2～4页，封面计入页数。' in captured[-1].requirements)
check('stale page sentences are dropped before the new instruction',
      strip_page_budget_notes('\n全稿恰好1页，封面计入页数。\n保留原文要点\n')=='保留原文要点')
check('a shrinking request never goes below cover+content',
      adjust_page_budget('explainer',6,6,'down')==(2,4) and adjust_page_budget('explainer',1,1,'down') is None)
check('a plain rewrite keeps the page budget untouched',
      build_brief(topic='赛制说明',requirements='更短更精简').explicit_pages is False)

# 小红书缺封面时由 compose 补页后照样通过校验，不再两轮修复后整份中止。
coverless=deepcopy(good)
coverless['pages'][0]['layout']='checklist'
coverless['pages'][0]['visual']['kind']='map'
def _compose_coverless(brief):
    rt=Runtime([coverless]);service=ComposeService(SessionFactory,rt)
    service._generate_variant=lambda *a,**kw:deepcopy(coverless)
    return (rt,*service.compose_platform(master,'xiaohongshu',engineering_default('xiaohongshu'),
        run_mode=RunMode.REAL,creative_brief=brief))
rt,draft,_=_compose_coverless(build_brief(topic='赛制说明'))
check('missing xiaohongshu cover is added when the page budget allows',
      [p['layout'] for p in draft.pages]==['cover','checklist'] and not rt.calls)
rt,draft,_=_compose_coverless(build_brief(topic='赛制说明',requirements='做一个赛制指南，恰好1页'))
check('a locked one-page budget converts the first page into the cover rather than adding one',
      [p['layout'] for p in draft.pages]==['cover'] and not rt.calls)
from app.services.renderer import check_layout
from app.services.template_packages import builtins
for kind in ['flow','map']:
 page=deepcopy(good['pages'][0]);page['visual']['kind']=kind
 page['visual']['template_style']=builtins()['editorial'].style.model_dump(mode='json')
 check('modern tutorial starts with '+kind,not any(i.level=='error' for i in check_layout({'title':'教程','caption':'操作步骤','pages':[page]},engineering_default('douyin'))))
 page['visual'].pop('template_style')
 check('frozen legacy cover rule stays strict for '+kind,any(i.code=='VISUAL_INVALID' for i in check_layout({'title':'教程','caption':'操作步骤','pages':[page]},engineering_default('douyin'))))
two=compile_rows(cat,strategy={'min_pages':2,'max_pages':2},brief={'rank_count':2},claim_ids=['C01'],sources=[cat_source],ranking=True,verify_objects=True)
check('multi-page catalog source note appears once',sum(bool(p['footnote']) for p in two)==1)
check('multi-page catalog ordering note appears once',sum(bool(p['body']) for p in two)==1)
check('source-note cleanup preserves every row and citation',sum(len(p['visual']['items']) for p in two)==2 and all(p['claim_ids']==['C01'] for p in two))
rt=Runtime([good]);service=ComposeService(SessionFactory,rt)
service._repair_variant_fields(bad,'douyin','超长',{'min_pages':1,'max_pages':1},engineering_default('douyin'),limited,None,RunMode.REAL,1)
check('repair prose uses user cap rather than contradictory default','detail硬上限为12字符' in rt.calls[0]['prompt'] and 'detail最多64' not in rt.calls[0]['prompt'])
from app.services.content_skills import ContentAudit
diagnosis={'passed':False,'summary':'诊断说明。'*100,'requirements_coverage':['事实需要修改'],'issues':[{'severity':'error','page':2,'problem':'属性概括过宽','suggestion':'限定为部分属性'}]}
check('complete internal audit explanation retains blocking issues',ContentAudit.model_validate(diagnosis).issues[0].severity=='error' and not ContentAudit.model_validate(diagnosis).passed)
from app.services.research_service import ResearchResult,SourceModel,ClaimModel
from app.services.production_service import ProductionService
with SessionFactory() as s:
 rev=s.get(ContentRevision,rid)
 rev.claims_json={'claims':[{'id':'C01','kind':'fact','statement':'原版明确记录的具体操作','source_ids':['W01']}],
  'sources':[{'id':'W01','kind':'public_web','url':'https://example.com/manual','access_state':'blocked','excerpt_basis':'full_text','excerpt':'这是基线版本冻结的原文资料与访问状态，不冒充本次重新访问。'}]}
 s.commit()
merged=ResearchResult(topic='教程',sources=[SourceModel(id='W01',kind='public_web',excerpt='新搜索摘要',excerpt_basis='search_snippet')],claims=[ClaimModel(id='C01',kind='opinion',statement='新调查',source_ids=['W01'])])
production=ProductionService(SessionFactory,runtime=Runtime([]))
production._reuse_revision_evidence(cid,merged,RunMode.REAL,base_revision_id=rid)
check('revision retains old source even when new search exists',len(merged.sources)==2 and merged.sources[-1].url=='https://example.com/manual')
check('frozen source access state is never promoted to readable',merged.sources[-1].access_state.value=='blocked')
check('new and frozen IDs do not collide',len({s.id for s in merged.sources})==len(merged.sources) and len({c.id for c in merged.claims})==len(merged.claims))
check('reused claim points to the namespaced original source',merged.claims[-1].source_ids==[merged.sources[-1].id] and merged.claims[0].source_ids==['W01'])
check('checkpoint recovery never duplicates frozen sources',not production._reuse_revision_evidence(cid,merged,RunMode.REAL,base_revision_id=rid) and len(merged.sources)==2 and len(merged.claims)==2)
print(f'结果：{passed} 通过 / 0 失败')
