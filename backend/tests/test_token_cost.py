"""Isolated cost evidence, arithmetic, reader coverage and PNG regressions."""
import os,sys,json,tempfile,types
from copy import deepcopy
from pathlib import Path
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-token-cost-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",CWB_ARTIFACT_DIR=str(tmp/'artifacts'),CWB_TMP_DIR=str(tmp/'tmp'),CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'))
from app.services.token_cost import *
from app.services.content_forms import build_brief,planning_pages
from app.services.content_recipes import apply_recipe,options
from app.services.compose_service import ComposeService,_strategy_for,_master_page_range
from app.services.research_service import ResearchResult,SourceModel,ResearchService
from app.services.evidence_gate import needs_grounding,require_evidence
from app.services.renderer import build_pages,PlaywrightRenderer,verify_images
from app.services.visual_content import VISUAL_FIT_JS
from app.services.profile_store import engineering_default
from app.services.meme_editorial import visible_page_text
from app.services.content_skills import ContentSkills
from app.services.provider_contract import RunMode
from playwright.sync_api import sync_playwright
from fastapi.testclient import TestClient
from app.main import app
from app.api.production import SessionFactory

passed=0
def check(name,value):
    global passed
    assert value,name
    passed+=1;print('PASS '+name,flush=True)
def rejects(name,fn):
    try:fn();ok=False
    except (ValidationFailed,ValueError):ok=True
    check(name,ok)
# Explicit test fixture; production contains no tariff constants.
text='''模型
deepseek-flash
(1)
deepseek-v4-pro
BASE URL
百万tokens输入
（缓存命中）
空闲时段
0.02元
0.15元
高峰时段
0.04元
0.30元
百万tokens输入
（缓存未命中）
空闲时段
1元
4.5元
高峰时段
2元
9.0元
百万tokens输出
空闲时段
4元
13.5元
高峰时段
8元
27.0元
北京时间工作日9:00 - 12:00、14:00 - 18:00为高峰，法定节假日除外。
'''
source={'id':'W01','kind':'public_web','url':PRICE_URL,'excerpt':text,'excerpt_basis':'full_text','access_state':'ok','sha256':'fixture-hash','retrieved_at':'2026-10-05T16:32:00Z'}
ledger=parse_tariff(source,20000)
check('cost questions require facts even without latest keyword',needs_grounding('2万token价值多少钱'))
rejects('no usable evidence stops guessing',lambda:require_evidence('2万token价值多少钱',[]))
for topic,num in [('2万token价值多少钱',20000),('20000 tokens多少钱',20000),('1.5万token价格',15000),('3千token费用',3000),('2百万token价格',2000000)]:check('quantity '+topic,quantity(topic)==num)
rejects('fractional token quantity rejected',lambda:quantity('0.5token多少钱'))
rejects('missing quantity rejected',lambda:quantity('token多少钱'))
check('mixed cache arithmetic is exact',calculate(20000,'1','4','.02',output_tokens=10000,cached_tokens=5000)==Decimal('.0551'))
rejects('cache cannot exceed input',lambda:calculate(1,'1','4','.02',cached_tokens=2))
rejects('negative tariff rejected',lambda:calculate(1,'-1','4','.02'))
rejects('infinite tariff rejected',lambda:calculate(1,'NaN','4','.02'))
check('tiny cache fees not rounded to zero',ledger['entries'][0]['amounts_cny']['cached']=='0.0004')
check('same total mixed scenario is 10k plus 10k',ledger['mixed_input']==10000 and ledger['mixed_output']==10000)
expected=[('Flash','空闲',['0.02','0.08','0.05','0.0004']),('Pro','空闲',['0.09','0.27','0.18','0.003']),('Flash','高峰',['0.04','0.16','0.10','0.0008']),('Pro','高峰',['0.18','0.54','0.36','0.006'])]
for actual,(model,band,amounts) in zip(ledger['entries'],expected):check(model+band+' amounts match 4 scenarios',[actual['amounts_cny'][k] for k in ['input','output','mixed','cached']]==amounts)
check('query date uses China calendar not UTC',ledger['retrieved_date_china']=='2026-10-06')
check('ledger binds source hash and ID',ledger['source_sha256']=='fixture-hash' and ledger['source_id']=='W01')
for field,value in [('url','https://example.com/pricing'),('excerpt_basis','search_snippet'),('access_state','timeout'),('excerpt',text.replace('deepseek-flash','retired-model')),('excerpt',text.replace('百万tokens输出','千tokens输出'))]:
    rejects('unverified/changed '+field+str(value)[:24],lambda f=field,v=value:parse_tariff({**source,f:v},20000))
research=ResearchResult(topic='2万token多少钱',sources=[SourceModel.model_validate(source)],run_mode=RunMode.REAL)
attach_ledger(research)
check('derived calculation retains raw evidence',len(research.sources)==2 and research.sources[0].excerpt==text)
check('calculated claim refers to original source',research.claims[0].source_ids==[LEDGER_ID,'W01'])
rejects('different requested provider is never silently replaced',lambda:attach_ledger(ResearchResult(topic='2万GPT token多少钱',sources=[SourceModel.model_validate(source)])))
rs=ResearchService(None);links=ResearchResult(topic='链接');rs._absorb_user_materials([{'text':PRICE_URL}],links)
check('URL alone is a lead not a factual claim',links.claims[0].kind=='opinion' and links.sources[0].excerpt_basis=='url_only')
brief=apply_recipe(build_brief(topic='2万token价值多少钱'))
check('automatic route selects cream guide and tech',brief.template_id=='friendly_guide' and brief.direction=='tech')
check('20k quantity is never ranking count',brief.rank_count is None and brief.form!='ranking')
check('default planning stays compact',planning_pages(brief)==2 and _master_page_range(brief)==(1,2) and _strategy_for({'min_pages':5,'max_pages':7},brief)['max_pages']==2)
check('template is exposed by options',any(t['id']=='friendly_guide' for t in options()['templates']))
first=compile_price_page({'index':1,'layout':'cover'},ledger)
second={'index':2,'layout':'checklist','heading':'这笔钱是怎么算的','kicker':'四行讲明白','body':[], 'footnote':'按DeepSeek官方人民币单价计算', 'claim_ids':[LEDGER_ID],
 'visual':{'kind':'checklist','title':'用量、缓存、时间与计量','presentation':'friendly_guide','presentation_version':1,
 'items':[{'label':'怎么算','detail':'2万÷100万=0.02，再乘每百万单价。输入与输出各算一笔，最后相加。','icon':'code'},
 {'label':'缓存更便宜','detail':'Flash空闲时，2万缓存命中输入只需0.0004元；未命中输入为0.02元。','icon':'chart'},
 {'label':'高峰什么时候','detail':'北京时间工作日9–12点、14–18点为高峰；中国法定节假日除外。','icon':'check'},
 {'label':'token不是字数','detail':'token是分词计量单位，不等于字数；实际以接口usage返回用量计费。','icon':'brain'}],
 'takeaway':'先看模型、用量类型与时段，再算费用；API不是会员费。'}}
draft={'platform':'douyin','title':'2万token多少钱','caption':'测试配文。','form':'guide','pages':[first,second]}
check('image text includes all core explanations',not image_quality_issues(draft['pages']))
clock=deepcopy(second);clock['visual']['items'][2]['detail']='北京时间工作日9:00–12:00、14:00–18:00为高峰；中国法定节假日除外。'
check('whole-hour colon notation is not rejected as missing time',not image_quality_issues([first,clock]))
clock['visual']['items'][2]['detail']='北京时间工作日9：00至12：00、14：00至18：00为高峰；中国法定节假日除外。'
check('Chinese colon hour notation is also accepted',not image_quality_issues([first,clock]))
original='API费用按实际usage计量。收藏一下，下次查价不用翻文档。'
check('optional final save reminder can be removed without losing facts',ComposeService._tidy_caption(original,1,20)=='API费用按实际usage计量。')
bad=deepcopy(second);bad['visual']['items']=bad['visual']['items'][:2];bad['footnote']='token不是字数，实际以usage计量；高峰9–12点、14–18点。'
check('caption and footnote cannot repair missing core image rows',len(image_quality_issues([first,bad]))>=3)
check('money explanation is part of actual image projection',first['visual']['items'][0]['detail'] in visible_page_text(first))
docs=build_pages(draft,engineering_default('douyin'))
check('dedicated guide template version recorded',all(d['template_version'].startswith('friendly-guide-') for d in docs))
check('all source-bound table cells rendered',all(i['detail'].split('｜')[0] in docs[0]['html'] for i in first['visual']['items']))
escaped=deepcopy(second);escaped['visual']['items'][0]['detail']='<img src=x onerror=alert(1)>'
check('model HTML remains escaped','<img src=x onerror' not in build_pages({**draft,'pages':[first,escaped]},engineering_default('douyin'))[1]['html'])
check('actual explanations visible on cover','class="studio-art"' not in docs[0]['html'] and 'fg-table' in docs[0]['html'])
client=TestClient(app)
headers={'X-CWB-Local-Action':'account-connection'}
check('template preview is available without model calls',client.get('/api/v1/studio/template-preview/friendly_guide',headers=headers).status_code==200)
svc=ContentSkills(SessionFactory,None)
audit=svc.audit(topic='2万token多少钱',requirements='',plan={'form':'guide'},variants=[{**draft,'pages':[first,bad]}],claims=[],sources=research.sources_as_dicts(),run_mode=RunMode.REAL,context_id='test',content_id=None)
check('program audit stops incomplete price guide without paid call',not audit.passed)
service=ComposeService(SessionFactory);service._active_sources=research.sources_as_dicts()
explicit=apply_recipe(build_brief(topic='2万token价值多少钱',requirements='恰好2页'))
fake=deepcopy(draft);fake.pop('form');fake['pages'][0]['visual']['items'][0]['detail']='¥999｜¥999｜¥999'
compiled=service._validate_variant(fake,known={LEDGER_ID},profile=engineering_default('douyin'),platform='douyin',brief=explicit)
check('composer replaces model-made price cells from frozen math',compiled.pages[0]['visual']['items'][0]['detail']=='¥0.02｜¥0.08｜¥0.05')
check('composer owns the million-token formula as well as price cells','20000÷100万×1＝¥0.02' in compiled.pages[1]['visual']['items'][0]['detail'])
for template_id in ['friendly_guide','editorial','illustrated','rank_cards','category_table']:
    selected=apply_recipe(build_brief(topic='2万token价值多少钱',requirements='恰好2页'),template_id=template_id)
    output=service._validate_variant(deepcopy(fake),known={LEDGER_ID},profile=engineering_default('douyin'),platform='douyin',brief=selected)
    check(template_id+' still uses program-owned price arithmetic',output.pages[0]['visual']['items'][0]['detail']=='¥0.02｜¥0.08｜¥0.05' and '20000÷100万×1＝¥0.02' in output.pages[1]['visual']['items'][0]['detail'])
    check(template_id+' keeps selected style after arithmetic compilation',all(p['visual']['presentation']==template_id and p['visual']['template_style']['design_family']==selected.template_package['style']['design_family'] for p in output.pages))
wrong_formula=deepcopy(second);wrong_formula['visual']['items'][0].update(label='怎么算',detail='20000×1=¥0.02。')
compile_formula_rows([wrong_formula],ledger)
check('incorrect model equation is replaced without changing other rows','20000÷100万×1＝¥0.02' in wrong_formula['visual']['items'][0]['detail'] and wrong_formula['visual']['items'][1:]==second['visual']['items'][1:])
check('computed equation records its evidence claim',LEDGER_ID in wrong_formula['claim_ids'])
alternate=deepcopy(second);alternate['visual']['items'][0].update(label='先除以一百万',detail='扣费=token量×每百万单价。20000×1=0.02元。')
compile_formula_rows([alternate],ledger)
check('alternative formula headings cannot bypass unit-safe calculation','20000÷100万×1＝¥0.02' in alternate['visual']['items'][0]['detail'])
rejects('missing calculated claim cannot slip into master',lambda:service._validate_variant(deepcopy(fake),known=set(),profile=engineering_default('douyin'),platform='douyin',brief=explicit))
for platform in ['douyin','xiaohongshu']:
    result=PlaywrightRenderer(tmp/'images').render_platform({**draft,'platform':platform},engineering_default(platform),display_id='TOKEN')
    check(platform+' both PNGs render within safe area',result.passed and len(result.images)==2)
    check(platform+' dimensions and hashes verify',not verify_images(result,tmp/'images'))
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path=PlaywrightRenderer.resolve_executable())
    page=browser.new_page(viewport={'width':1080,'height':1440})
    for n,doc in enumerate(docs):
        page.set_content(doc['html']);page.evaluate('document.fonts.ready');fit=page.evaluate(VISUAL_FIT_JS)
        check('page '+str(n+1)+' readability scale',fit>=.74)
        check('page '+str(n+1)+' actual detail rows are visible',all(page.locator('.fg-row').nth(i).is_visible() for i in range(4)))
        check('page '+str(n+1)+' illustration exists',page.locator('.fg-doodle').count()==1 and page.locator('.fg-doodle').is_visible())
    demo=client.get('/api/v1/studio/template-preview/friendly_guide',headers=headers).text
    page.set_content(demo);page.evaluate('document.fonts.ready');fit=page.evaluate(VISUAL_FIT_JS)
    check('nonranking handdrawn demo remains readable',fit>=.74 and page.locator('.style-item').count()==4 and page.locator('.style-rank').count()==0)
    browser.close()
print(f'结果：{passed} 通过 / 0 失败')
