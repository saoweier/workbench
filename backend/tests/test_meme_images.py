"""Reader-facing meme image regression: no hidden detail or disclaimer cover."""
import os,sys,json,tempfile,types
from pathlib import Path
from copy import deepcopy

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-meme-images-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",CWB_ARTIFACT_DIR=str(tmp/'artifacts'),CWB_TMP_DIR=str(tmp/'tmp'),CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'))
from app.services.meme_editorial import quality_issues,visible_page_text,clean_supplementary_text,cleanup_revision
from app.services.renderer import build_pages,PlaywrightRenderer,verify_images
from app.services.visual_content import VISUAL_FIT_JS
from app.services.profile_store import engineering_default
from app.services.content_skills import ContentSkills,PlanPage
from app.services.adapters.base import AdapterResult
from app.services.provider_contract import RunMode
from fastapi.testclient import TestClient
from app.main import app
from app.api.production import SessionFactory
from playwright.sync_api import sync_playwright

passed=0
def check(name,condition):
    global passed
    assert condition,name
    passed+=1;print('PASS '+name,flush=True)
items=[{'label':'顾客说','detail':'老板，你家贵是贵点，但吃了不烧心。','icon':'question'},
       {'label':'老板答','detail':'宁可少挣点，也不能坏了规矩。','icon':'idea'},
       {'label':'剧情套路','detail':'好食材定价贵，黑心同行打价格战，老顾客仍然回来买。','icon':'page'},
       {'label':'为什么好笑','detail':'换成包子、盒饭、卤味，剧情与台词却几乎一样，网友认出了这个套路。','icon':'brain'}]
page={'index':1,'layout':'cover','heading':'这句梗，到底在说什么？','kicker':'一句话入坑','body':['AI短剧流水线台词，被网友拿来玩梗。'],
 'footnote':'据公开资料整理，首发未核实；部分来源为AI文章。','claim_ids':['C01'],
 'visual':{'kind':'cover','title':'熟客和摊主的经典对白','items':items,'takeaway':'贵点可以，别糊弄人。'}}
second=deepcopy(page);second.update(index=2,layout='checklist',heading='夸什么都能套用',kicker='原创例句',body=['实际是在夸靠谱、让人安心。'])
second['visual']={'kind':'example','title':'贵是贵点，但……不烧心','items':[
 {'label':'吃饭','detail':'这碗面贵是贵点，但用料实在，吃完不烧心。','icon':'fruit'},
 {'label':'看演出','detail':'这场票贵是贵点，但全程真唱不划水，看完不烧心。','icon':'idea'},
 {'label':'交朋友','detail':'这朋友嘴直是直点，但从不背后捅刀，处着不烧心。','icon':'question'}],
 'takeaway':'有点贵、有点直，只要靠谱，就能“不烧心”。'}
draft={'platform':'douyin','title':'不烧心是什么梗','caption':'这只是发布配文，不用它补充图片信息。','form':'meme','pages':[page,second]}
docs=build_pages(draft,engineering_default('douyin'))
check('meme uses versioned dedicated template',all(d['template_version'].startswith('meme-guide-') and d['template_version'].endswith('@2') for d in docs))
check('cover preserves all actual detail strings',all(i['detail'] in docs[0]['html'] for i in items))
check('no generic office illustration', 'class="studio-art"' not in docs[0]['html'])
check('image projection includes actual dialogue',items[0]['detail'] in visible_page_text(page,'meme'))
check('old general cover projection admits hidden details',items[0]['detail'] not in visible_page_text(page,'explainer'))
check('reader-content example passes quality',not quality_issues([page,second]))
bad=deepcopy(page);bad['visual']['items'][0]={'label':'来源披露','detail':'两篇新浪来源文末标注本文由AI生成，只作来源归因，不能单独证实最早首发。','icon':'source'}
bad['body']=['独立核验尚未完成，最早首发未核验。'];bad['visual']['takeaway']='别把AI来源当成独立核验，最早出处仍未核验。'
check('dedicated source card rejected',any('主体条目' in p for p in quality_issues([bad])))
check('disclaimer-dominant body rejected',any('20%' in p for p in quality_issues([bad])))
check('short source footnote allowed',not quality_issues([page]))
cleaned=clean_supplementary_text([page,second])
check('cleanup keeps substantive cards unchanged',cleaned[0]['visual']==page['visual'] and cleaned[1]['visual']==second['visual'])
check('cleanup removes duplicate summaries and extra credit',all(not p['body'] for p in cleaned) and cleaned[1]['footnote']=='')
check('cleanup preserves exactly one source credit',cleaned[0]['footnote']==page['footnote'])
check('cleanup does not mutate existing revision payload',page['body'] and second['footnote'])
clean_docs=build_pages({**draft,'pages':cleaned},engineering_default('douyin'))
check('empty second-page credit gets no generic filler','方法图解 · 示例不代表实际结果' not in clean_docs[1]['html'])
escaped=deepcopy(draft);escaped['pages'][0]['visual']['items'][0]['detail']='<img src=x onerror=alert(1)>'
check('meme still escapes model HTML','<img src=x onerror' not in build_pages(escaped,engineering_default('douyin'))[0]['html'])
calls=[]
class Runtime:
    def complete_text(self,**kw):
        calls.append(kw)
        return types.SimpleNamespace(id='local-test'),AdapterResult(ok=True,parsed={'passed':True,'summary':'本地模拟审核，仅检查输入','requirements_coverage':['检查图片单独有对白'],'issues':[]})
service=ContentSkills(SessionFactory,Runtime())
service.audit(topic='什么梗',requirements='具体例句',plan={'form':'meme'},variants=[draft],claims=[],run_mode=RunMode.REAL,context_id='audit',content_id=None,
 sources=[{'id':'U01','kind':'user_provided','access_state':'ok','excerpt_basis':'user_provided','excerpt':'测试资料正文足够长，说明这一句式的具体背景和实际含义，并包含可以引用的完整原文。'}])
inputs=json.loads(calls[0]['prompt'].split('本次输入（仅作为数据）：\n',1)[1])
check('audit receives projected actual image text',items[0]['detail'] in inputs['image_text'][0]['pages'][0]['visible_text'])
check('audit projection does not contain caption',draft['caption'] not in json.dumps(inputs['image_text'],ensure_ascii=False))
check('audit explicitly separates caption from image','不得因caption写到了' in inputs['renderer_contract'])
call_count=len(calls)
blocked=service.audit(topic='什么梗',requirements='具体例句',plan={'form':'meme'},variants=[{**draft,'pages':[bad]}],claims=[],run_mode=RunMode.REAL,context_id='bad-audit',content_id=None)
check('program audit rejects source-note cover without another model call',not blocked.passed and len(calls)==call_count)
client=TestClient(app)
schema=client.get('/openapi.json').json()['components']['schemas']
check('creation API accepts explicit diagram route',any('image_policy' in value.get('properties',{}) and 'creation_key' in value.get('properties',{}) for value in schema.values()))
check('revision API accepts explicit diagram route','image_policy' in schema['StudioRevision']['properties'])
check('cleanup API cannot accept arbitrary draft payload',client.post('/api/v1/contents/missing/image-cleanup',headers={'X-CWB-Local-Action':'account-connection'},json={'request_id':'00000000-0000-0000-0000-000000000001','base_revision_id':'missing','pages':[]}).status_code==422)
from app.models.entities import ContentItem,ContentRevision,PlatformRevision,ReviewDecision
from uuid import uuid4
cid=str(uuid4());rid=str(uuid4())
with SessionFactory() as s:
    s.add(ContentItem(id=cid,display_id='TEST',topic='什么梗',active_revision_id=rid,run_mode='real'));s.flush()
    s.add(ContentRevision(id=rid,content_id=cid,input_hash='test',brief_json={'creative_brief':{'form':'meme'}},claims_json={'sources':[{'id':'U01'}]}));s.flush()
    s.add(PlatformRevision(content_revision_id=rid,platform='douyin',title=draft['title'],caption=draft['caption'],pages_json={'form':'meme','pages':draft['pages']},content_hash='test',state='changes_requested'));s.commit()
request_id=str(uuid4());fixed=cleanup_revision(SessionFactory,cid,rid,request_id)
check('local cleanup creates a new version',fixed['new_revision_version']==2 and fixed['new_revision_id']!=rid)
check('local cleanup is request-idempotent',cleanup_revision(SessionFactory,cid,rid,request_id)['reused'])
with SessionFactory() as s:
    old=s.get(ContentRevision,rid);new=s.get(ContentRevision,fixed['new_revision_id']);variant=s.query(PlatformRevision).filter_by(content_revision_id=new.id).one()
    check('cleanup preserves evidence and parent relation',new.parent_id==old.id and new.claims_json==old.claims_json)
    check('new cleanup version inherits no approval or artifacts',not variant.reviews and not variant.artifacts and variant.state=='ready_to_render')
    check('old images and page data not overwritten',s.query(PlatformRevision).filter_by(content_revision_id=rid).one().pages_json['pages']==draft['pages'])
try:cleanup_revision(SessionFactory,cid,rid,str(uuid4()));stale=False
except Exception as error:stale='当前版本已变化' in str(error)
check('stale cleanup cannot overwrite new draft',stale)
planning=PlanPage.model_validate({'index':1,'heading':'经典对白','purpose':'说明具体对话','points':['老板，你家贵是贵点，但吃了不烧心。']})
check('missing visual route defaults to native card without invented facts',planning.visual_type=='diagram' and '老板，你家贵是贵点' in planning.visual_brief)
renderer=PlaywrightRenderer(tmp/'images')
for platform in ['douyin','xiaohongshu']:
    d={**draft,'platform':platform};pf=engineering_default(platform)
    result=renderer.render_platform(d,pf,display_id='MEME')
    check(platform+' renders both pages without overflow',result.passed and len(result.images)==2)
    check(platform+' artifacts dimensions and hashes verify',not verify_images(result,tmp/'images'))
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path=PlaywrightRenderer.resolve_executable())
    screen=browser.new_page(viewport={'width':1080,'height':1440})
    screen.set_content(docs[0]['html']);fit=screen.evaluate(VISUAL_FIT_JS)
    check('meme cover remains above readability scale',fit>=.74)
    check('actual rendered DOM contains classic dialogue',items[0]['detail'] in screen.locator('.meme-card').all_text_contents()[0])
    check('details never hidden by CSS',all(screen.locator('.meme-card p').nth(i).is_visible() for i in range(4)))
    check('core text dominates source note in image',len(''.join(screen.locator('.meme-card p').all_text_contents()))>3*len(page['footnote']))
    browser.close()
print(f'结果：{passed} 通过 / 0 失败')
