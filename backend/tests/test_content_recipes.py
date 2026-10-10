"""Subject/template pipeline and real browser rendering against a local model stub."""
import json,os,sys,tempfile,subprocess,socket,time,urllib.request
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-recipes-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",CWB_ARTIFACT_DIR=str(tmp/'artifacts'),CWB_TMP_DIR=str(tmp/'tmp'),CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'),CWB_PENDING_REVIEW_STOCK_LIMIT='40')
from fastapi.testclient import TestClient
from app.main import app
from app.api import creation,studio,production
from app.models.entities import ContentRevision,ProviderCallRow,Event
from app.services.content_forms import build_brief
from app.services.content_recipes import apply_recipe,validate_metrics
from app.services.content_skills import ContentSkills,ContentAudit,normalize_audit_response
from app.services.catalog_compiler import compile_rows
from app.services.template_examples import example_page
from app.services.visual_content import VisualSpec,illustrated_page_html
from app.services.profile_store import engineering_default
from app.services.renderer import PlaywrightRenderer
from app.services.compose_service import _looks_like_dangerous
from app.core.errors import StateConflict
from app.worker import Worker
from app.core.config import get_settings
from mock_provider import start_mock
from playwright.sync_api import sync_playwright
client=TestClient(app);headers={'X-CWB-Local-Action':'account-connection'};passed=0
def check(name,value):
 global passed
 assert value,name
 passed+=1;print('PASS '+name,flush=True)
def rejects(fn):
 try:fn()
 except ValueError:return True
 except Exception as e:return type(e).__name__=='ValidationFailed'
 return False

audit={'passed':True,'summary':'核对通过','requirements_coverage':['核对来源'],'issues':[{'severity':'warning','page':1,'problem':'注意字号','suggestion':'人工查看','page_note':None}]}
clean=normalize_audit_response(audit)
check('empty audit annotation normalized without altering supplier reply','page_note' not in clean['issues'][0] and 'page_note' in audit['issues'][0])
check('normalized audit remains strict contract',ContentAudit.model_validate(clean).passed)
check('non-empty unknown audit field still rejected',rejects(lambda:ContentAudit.model_validate(normalize_audit_response({**audit,'issues':[{**audit['issues'][0],'page_note':'改变批准状态'}]}))))
check('null privilege field still rejected',rejects(lambda:ContentAudit.model_validate(normalize_audit_response({**audit,'approval':None}))))
opts=client.get('/api/v1/studio/options').json()
check('five paired templates plus automatic matching are available without model calls',{p['id'] for p in opts['templates']}=={'auto','friendly_guide','rank_cards','category_table','illustrated','editorial'} and len(opts['directions'])==8)
b=apply_recipe(build_brief(topic='15个AI工具分类速查表，1页'))
check('15-row directory keeps subject separate from form',b.form=='directory' and b.item_count==15 and b.direction=='tech' and b.template_id=='category_table')
check('fruit ranking stays ranking with life skills',apply_recipe(build_brief(topic='水果TOP10排行榜1页')).form=='ranking' and apply_recipe(build_brief(topic='水果TOP10排行榜1页')).direction=='life')
check('generic source-list requirement cannot erase explicit TOP10',build_brief(topic='Skill TOP10排行榜',requirements='页脚写来源清单').form=='ranking')
check('source-list reference cannot erase directory topic',build_brief(topic='13个来源分类速查表',requirements='页脚注明来源清单').form=='directory')
check('explicit subject wins over inference',apply_recipe(build_brief(topic='AI学习技巧'),direction='learning').direction=='learning')
check('visual style selection preserves explicit TOP10',apply_recipe(build_brief(topic='Skill TOP10'),template_id='category_table').form=='ranking' and apply_recipe(build_brief(topic='Skill TOP10'),template_id='category_table').rank_count==10)
check('public source URL accepted as prose',_looks_like_dangerous('来源 https://github.com/JackyST0/hotpush') is None)
check('absolute path remains blocked',_looks_like_dangerous('读取 /etc/passwd')=='绝对路径')
check('unknown templates rejected',rejects(lambda:apply_recipe(build_brief(topic='内容整理'),template_id='foreign')))
over=build_brief(topic='17个技能分类速查表')
check('explicit 17-row directory is accepted without truncation',over.form=='directory' and over.item_count==17)
row={'label':'示例Skill01','detail':'用于信息整理','icon':'source','category':'信息调研','tags':['资料整理'],'metric_text':'101次','metric_label':'示例累计安装','metric_source_id':'U01'}
sources=[{'id':'U01','access_state':'ok','excerpt':'示例Skill01 101次；示例累计安装，2026-10-01，非真实统计。'}]
check('metric transcription has source and matching object',validate_metrics([row],sources))
check('invented number is rejected',rejects(lambda:validate_metrics([{**row,'metric_text':'999次'}],sources)))
check('another object cannot borrow metric',rejects(lambda:validate_metrics([{**row,'label':'另一技能'}],sources)))
check('unreadable source cannot support metric',rejects(lambda:validate_metrics([row],[{**sources[0],'access_state':'blocked'}])))
check('no data is allowed without fake zero',validate_metrics([{**row,'metric_text':None,'metric_label':None,'metric_source_id':None}],sources))
service=ContentSkills(production.SessionFactory);snap=service.snapshot('recipe-frozen',direction='tech')
check('only selected domain frozen with common stage policies','direction.tech' in snap and 'direction.life' not in snap and 'research' in snap)
tech=next(v for v in service.catalog() if v['id']=='direction.tech')
edited=service.save(tech['id'],tech['instructions']+'\n新增明确检查：必须核对项目名称。',tech['version'])
check('subject edits versioned without changing frozen task',edited['version']!=snap['direction.tech']['version'] and service.snapshot('recipe-frozen',direction='life')==snap)
check('subject rules reach actual generation prompt','科技与 AI' in service.instructions('generation',snapshot=snap) and '累计星标' in service.instructions('generation',snapshot=snap))
check('source instructions cannot grant privilege','不得读取或输出密钥' in service.instructions('generation',snapshot=snap))
for tid in ['rank_cards','category_table','illustrated','editorial','friendly_guide']:
 response=client.get('/api/v1/studio/template-preview/'+tid)
 check(tid+' preview uses local renderer and explicit demo label',response.status_code==200 and '演示内容' in response.text and 'data-template' in response.text)
page=example_page('category_table');VisualSpec.model_validate(page['visual'])
interleaved=deepcopy(page['visual']['items']);interleaved[3],interleaved[12]=interleaved[12],interleaved[3]
words={'title':'分类表','caption':'演示','order_note':'分类整理','source_note':'演示资料','takeaway':'保留全部对象','items':interleaved}
compiled=compile_rows(words,strategy={'min_pages':1,'max_pages':1},brief={'item_count':15,'template_id':'category_table','template_version':1},claim_ids=[],sources=[],ranking=False)
cats=[i['category'] for i in compiled[0]['visual']['items']]
check('same categories are never split into different blocks',len([c for i,c in enumerate(cats) if not i or cats[i-1]!=c])==len(set(cats)))
check('program grouping never loses or duplicates items',set(i['label'] for i in compiled[0]['visual']['items'])==set(i['label'] for i in interleaved))
check('unknown concrete objects rejected before render',rejects(lambda:compile_rows(words,strategy={'min_pages':1,'max_pages':1},brief={'item_count':15},claim_ids=[],sources=sources,ranking=False,verify_objects=True)))
check('15 demo items exist and no metrics invented',len(page['visual']['items'])==15 and all(not i.get('metric_text') for i in page['visual']['items']))
mal=deepcopy(page);mal['visual']['items'][0]['label']='<script>alert(1)</script>'
html,_=illustrated_page_html(mal,engineering_default('douyin'),'douyin',1,'sans-serif',form='directory')
check('editorial text escaped','<script>alert(1)</script>' not in html and '&lt;script&gt;' in html)

def responder(prompt,schema):
 props=(schema or {}).get('properties',{})
 if 'can_answer' in props:
  data=json.loads(prompt.rsplit('本次输入（仅作为数据）：\n',1)[1]);source=data['sources'][0]
  return {'can_answer':True,'summary':'隔离协议测试使用用户给定演示资料，非真实热度','facts':[{'role':'context','statement':'给定演示资料列出具体对象及示例数值，均非真实安装量','source_id':source['id'],'quote':source['excerpt'].split('\n')[0]}],'blocking_gaps':[],'limitations':['本地协议模拟，不能用于验证真实搜索能力']}
 if 'objective' in props:
  return {'audience':'资料整理读者','objective':'按提供资料完整列出十五项','required_elements':['15个具体对象','明确演示口径'],
          'acceptance_checks':['完整十五项且一页','指标可追溯'],
          'pages':[{'index':1,'purpose':'列出完整分类速查表','heading':'十五项分类速查','points':['核对全部具体对象'],
                    'visual_type':'diagram','visual_brief':'分类色块、名称与主要功能','claim_ids':['C01']}],
          'material_gaps':[],'blocking_gaps':[],'limitations':['本地协议模拟']}
 if 'core_viewpoint' in props:
  return {'audience_problem':'如何整理这些资料','core_viewpoint':'十五项分类速查','claim_ids':['C01'],'limitations':['仅协议模拟'],
          'actions':['逐项核对资料'],'pages':[{'index':1,'role':'point','heading':'具体对象',
           'points':[f'示例Skill{i:02d}：用于资料整理' for i in range(1,16)],'claim_ids':['C01']}]}
 if 'items' in props:
  return {'title':'十五项分类速查','caption':'给定资料的分类整理，示例数值仅用于本地协议与版式检查，不是真实安装量。',
          'lead':'完整列出十五个对象','order_note':'资料顺序，示例非真实统计','source_note':'用户给定演示资料，2026-10-01，非真实统计','takeaway':'按用途核对每一个对象，保留来源口径。',
          'items':[{'label':f'示例Skill{i:02d}','detail':'用于信息整理与输出','icon':'source' if i<6 else 'code','category':'信息调研' if i<6 else '办公任务' if i<11 else '视觉创作',
                    'tags':[], 'metric_text':f'{100+i}次' if i==1 else None,'metric_label':'示例累计安装' if i==1 else None,'metric_source_id':'U01' if i==1 else None} for i in range(1,16)]}
 if 'passed' in props:return {'passed':True,'summary':'协议模拟核对通过，不代表真实模型语义能力','requirements_coverage':['完整对象','示例数据口径'],'issues':[]}
 return None
server,requests=start_mock(responder)
process=None;browser=None
try:
 saved=client.post('/api/v1/provider-configs',headers=headers,json={'name':'recipe-protocol-stub','kind':'text','adapter_type':'openai_compatible','base_url':f'http://127.0.0.1:{server.server_port}','model_id':'stub','api_key':'stub','enabled':True,'allow_localhost':True})
 check('model stub configured',saved.status_code==201)
 material='示例数据，仅测试排版，非真实安装量。\n'+'\n'.join(f'示例Skill{i:02d} {100+i}次；示例累计安装，2026-10-01。' for i in range(1,16))
 body={'request_id':str(uuid4()),'topic':'15个Skill分类速查表，1页','direction':'tech','template_id':'category_table','density':'short','materials':material,'run_mode':'real','pages':1}
 task=client.post('/api/v1/studio/produce',headers=headers,json=body).json();Worker(creation.SessionFactory,get_settings(),worker_id='recipe-test').tick()
 run=client.get('/api/v1/runs/'+task['run_id']).json()
 check('directory pipeline completes actual PNG render and audit: '+str(run.get('error')),run['state']=='succeeded')
 detail=client.get('/api/v1/contents/'+task['content_id']).json();rev=next(v for v in detail['revisions'] if v['revision_id']==detail['active_revision_id'])
 for platform in rev['platforms']:
  items=platform['pages'][0]['visual']['items']
  check(platform['platform']+' retains all15 objects and template',len(platform['pages'])==1 and [i['label'] for i in items]==[f'示例Skill{i:02d}' for i in range(1,16)] and platform['pages'][0]['visual']['presentation']=='category_table')
 hist=client.get('/api/v1/contents/'+task['content_id']+'/skill-history').json()['items']
 check('research results and subject version visible in history',any(e['skill_id']=='research' and e['output']['sources'] for e in hist) and all(e['direction_skill']['id']=='direction.tech' for e in hist))
 check('subject skills and research contract in actual HTTP prompts',any('资料调研与核验' in r['messages'][-1]['content'] for r in requests) and all('科技与 AI' in r['messages'][-1]['content'] for r in requests))
 revision=client.post('/api/v1/studio/contents/'+task['content_id']+'/revise',headers=headers,json={'request_id':str(uuid4()),'base_revision_id':detail['active_revision_id'],'instruction':'只换黑白编辑版，保留全部15项与1页','template_id':'editorial','run_mode':'real'}).json();Worker(creation.SessionFactory,get_settings(),worker_id='recipe-test').tick()
 revision_run=client.get('/api/v1/runs/'+revision['run_id']).json()
 check('template switch finishes new revision: '+str(revision_run.get('last_error') or revision_run.get('error') or ''),revision_run['state']=='succeeded')
 detail2=client.get('/api/v1/contents/'+task['content_id']).json();new=next(v for v in detail2['revisions'] if v['revision_id']==detail2['active_revision_id'])
 check('old template unchanged; new version freezes chosen template',len(detail2['revisions'])==2 and rev['brief']['creative_brief']['template_id']=='category_table' and new['brief']['creative_brief']['template_id']=='editorial')
 check('revision preserves count page direction and requires approval',new['brief']['creative_brief']['item_count']==15 and new['brief']['creative_brief']['page_max']==1 and new['brief']['creative_brief']['direction']=='tech' and detail2['state']=='ready_for_review')
 original_audit=ContentSkills.audit
 def interrupted_audit(self,**kwargs):
  original_audit(self,**kwargs)
  raise StateConflict('模拟已收到审核结果但保存阶段中断')
 ContentSkills.audit=interrupted_audit
 stopped=client.post('/api/v1/studio/contents/'+task['content_id']+'/revise',headers=headers,json={'request_id':str(uuid4()),'base_revision_id':detail2['active_revision_id'],'instruction':'换回分类速查表，保留15个对象与1页','template_id':'category_table','run_mode':'real'}).json()
 Worker(creation.SessionFactory,get_settings(),worker_id='recipe-test').tick()
 check('audit failure keeps committed draft checkpoint',client.get('/api/v1/runs/'+stopped['run_id']).json()['state']=='failed')
 ContentSkills.audit=original_audit;count_before=len(requests)
 response=client.post('/api/v1/runs/'+stopped['run_id']+'/control',json={'action':'retry'})
 check('explicit retry accepts completed own draft',response.status_code==200)
 Worker(creation.SessionFactory,get_settings(),worker_id='recipe-test').tick()
 check('retry resumes audit after own new revision',client.get('/api/v1/runs/'+stopped['run_id']).json()['state']=='succeeded')
 check('retry reuses settled calls and adds no duplicate revision',len(requests)==count_before and len(client.get('/api/v1/contents/'+task['content_id']).json()['revisions'])==3)

 before_edit=client.get('/api/v1/contents/'+task['content_id']).json()
 before_revision=next(v for v in before_edit['revisions'] if v['revision_id']==before_edit['active_revision_id'])
 adjusted=client.post('/api/v1/contents/'+task['content_id']+'/change-requests',json={'base_revision_id':before_edit['active_revision_id'],'instruction':'抖音标题改为：十五项工具分类速查','run_mode':'local_seed'})
 check('targeted edit returns rendered platform IDs and states',adjusted.status_code==201 and all(p['platform_revision_id'] and p['state']=='ready_for_review' for p in adjusted.json()['platforms']))
 after_edit=client.get('/api/v1/contents/'+task['content_id']).json()
 after_revision=next(v for v in after_edit['revisions'] if v['revision_id']==after_edit['active_revision_id'])
 check('targeted edit has independent images on both platforms and awaits review',after_edit['state']=='ready_for_review' and all(p['artifacts'] and p['state']=='ready_for_review' for p in after_revision['platforms']))
 old_xhs=next(p for p in before_revision['platforms'] if p['platform']=='xiaohongshu');new_xhs=next(p for p in after_revision['platforms'] if p['platform']=='xiaohongshu')
 check('targeted edit leaves other platform text and source version unchanged without extra model calls',new_xhs['title']==old_xhs['title'] and new_xhs['caption']==old_xhs['caption'] and new_xhs['pages']==old_xhs['pages'] and len(requests)==count_before and len(after_edit['revisions'])==4)

 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 base=f'http://127.0.0.1:{port}'
 process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--app-dir','backend','--host','127.0.0.1','--port',str(port)],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 for _ in range(80):
  try:urllib.request.urlopen(base+'/api/v1/health',timeout=1);break
  except Exception:time.sleep(.1)
 with sync_playwright() as pw:
  browser=pw.chromium.launch(executable_path=PlaywrightRenderer.resolve_executable(),headless=True)
  page=browser.new_page(viewport={'width':1440,'height':1050});errors=[];page.on('pageerror',lambda e:errors.append(page.url+' '+str(e)+' '+e.stack))
  page.goto(base+'/views/Production.html');page.get_by_role('button',name='自定义选题',exact=True).click();page.locator('#creator-topic').fill('我的测试分类速查表');page.get_by_role('button',name='下一步 · 选择样式 →').click();page.wait_for_selector('[data-template]')
  check('five selectable poster templates plus automatic matching and eight directions visible',page.locator('[data-template]').count()==6 and page.locator('#creator-direction option').count()==8)
  check('all five choices show actual poster previews',page.locator('.template-poster iframe').count()==5)
  page.locator('[data-template="category_table"]').click();page.locator('#creator-direction').select_option('tech')
  page.reload();page.wait_for_selector('[data-template]')
  check('template and direction survive page reload',page.locator('[data-template="category_table"]').get_attribute('aria-pressed')=='true' and page.locator('#creator-direction').input_value()=='tech')
  page.goto(base+'/views/SkillWorkflow.html');page.wait_for_selector('.skill-card')
  check('separate workflow and subject policy editors visible',page.locator('#skills .skill-card').count()==7 and page.locator('#direction-skills .skill-card').count()==7)
  page.goto(base+'/views/ReviewPreview.html?content='+task['content_id']);page.wait_for_function("document.querySelector('#revision-template option[value=friendly_guide]')")
  check('revision supports keep-current, automatic and five template choices',page.locator('#revision-template option').count()==7 and page.locator('#revision-direction option').count()==9)
  for url in ['/views/Production.html','/views/SkillWorkflow.html','/views/ReviewPreview.html?content='+task['content_id']]:
   page.set_viewport_size({'width':390,'height':844});page.goto(base+url);page.wait_for_timeout(450)
   check(url.split('?')[0]+' fits mobile',not page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
  for tid in ['rank_cards','category_table']:
   page.set_viewport_size({'width':1080,'height':1440});page.goto(base+'/api/v1/studio/template-preview/'+tid);page.wait_for_function('Number(document.body.dataset.fit)>0')
   check(tid+' visual preview stays above readability threshold',float(page.locator('body').get_attribute('data-fit'))>=.74)
   check(tid+' preview does not overlap header or footer',page.evaluate("() => {const d=document.querySelector('.diagram-content').getBoundingClientRect(), h=document.querySelector('.v-header').getBoundingClientRect(), f=document.querySelector('.v-takeaway').getBoundingClientRect();return d.top>=h.bottom && d.bottom<=f.top;}"))
  check('no browser script errors: '+' | '.join(errors),not errors)
  browser.close();browser=None
finally:
 server.shutdown();server.server_close()
 if process:process.terminate();process.wait(timeout=15)
print(f'结果：{passed} 通过 / 0 失败')
