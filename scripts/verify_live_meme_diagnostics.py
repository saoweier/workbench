"""Read-only verification of the specific real case, never generates or publishes."""
import hashlib,json,sys
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.services.renderer import PlaywrightRenderer
base='http://127.0.0.1:8000'
cid='bd923729-6646-4c8e-b554-7e3ec75fab83'
runid='f46944b7-c4be-423c-bfd3-6002e69a0a13'
out=ROOT/'docs/test-artifacts/meme-diagnosis-20261005';out.mkdir(exist_ok=True)
checks=[]
def check(name,condition):
    assert condition,name
    checks.append(name);print('PASS '+name,flush=True)
def get(path):
    response=httpx.get(base+'/api/v1'+path,timeout=20);response.raise_for_status();return response.json()
d=get('/contents/'+cid);diag=get('/contents/'+cid+'/diagnostics');run=get('/runs/'+runid)
r=next(v for v in d['revisions'] if v['revision_id']==d['active_revision_id'])
check('real DeepSeek production completed all stages',run['state']=='succeeded' and all(j['state']=='succeeded' for j in run['jobs']))
check('new V2 preserves original V1',r['version']==2 and len(d['revisions'])==2)
check('no automatic approval or publishing',d['state']=='ready_for_review' and all(v['state']=='ready_for_review' for v in r['platforms']))
check('research read three public original pages',diag['runs'][-1]['factual_source_count']==3)
check('origin and meaning source-backed assessment exists',{'origin','meaning'}<={f['role'] for f in diag['runs'][-1]['assessment']['facts']})
check('historical incorrect planning and audit are visible',any(len(v['issues'])==3 for v in diag['runs']))
check('new requests and raw replies are preserved',len([c for c in diag['calls'] if c['input']])==7)
for variant in r['platforms']:
    platform=variant['platform'];caption=variant['caption']
    check(platform+' exactly two pages including cover',len(variant['pages'])==2)
    check(platform+' explains AI short drama and figurative reliability','AI短剧' in caption and '靠谱' in caption and '红油重口味' not in caption)
    check(platform+' discloses AI sources and unverified earliest post','本文由AI生成' in caption and any(v in caption for v in ['未核验','没核验']))
    check(platform+' labels invented usage examples','创作示例' in caption)
    for artifact in variant['artifacts']:
        raw=httpx.get(base+artifact['url']).content
        check(platform+f" rendered image {artifact['page_index']} has expected hash",hashlib.sha256(raw).hexdigest()==artifact['sha256'] and artifact['width']==1080 and artifact['height']==1440)
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path=PlaywrightRenderer.resolve_executable())
    page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(base+'/views/ReviewPreview.html?content='+cid)
    expect(page.locator('#caption')).to_contain_text('AI短剧')
    expect(page.locator('#pages img')).to_have_count(2)
    check('live preview displays corrected two-page draft',True)
    expect(page.locator('#diagnostic-link')).to_have_attribute('href','/views/SkillWorkflow.html?content='+cid)
    page.locator('#diagnostic-details > summary').click()
    expect(page.locator('#content-diagnostic')).to_contain_text('审核仍标为通过')
    page.screenshot(path=str(out/'preview-v2-diagnostics.png'),full_page=True)
    check('live preview diagnostic exposes original failure',True)
    page.goto(base+'/views/SkillWorkflow.html?content='+cid)
    expect(page.locator('[data-save]')).to_have_count(14)
    expect(page.locator('#history')).to_contain_text('原始返回')
    page.locator('#history').screenshot(path=str(out/'skill-diagnostics.png'))
    check('live skill page editable and linked to actual calls',True)
    check('live diagnostic pages have no script errors',not errors)
    browser.close()
result={'content_id':cid,'active_revision':r['version'],'run_id':runid,'total_recorded_calls':len(diag['calls']),'new_calls_with_prompt':sum(bool(c['input']) for c in diag['calls']),
        'passed':len(checks),'failed':0,'checks':checks,'notice':'仅只读验证：没有新增模型请求，没有批准或发布。资料归因不等同于独立证实最早出处。'}
(out/'live-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(f'结果：{len(checks)} 通过 / 0 失败')
