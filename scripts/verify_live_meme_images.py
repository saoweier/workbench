"""Read-only final image checks; never calls a model or creates an approval."""
import hashlib,json,sys
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.services.renderer import build_pages,PlaywrightRenderer
from app.services.profile_store import engineering_default
from app.services.meme_editorial import quality_issues
base='http://127.0.0.1:8000';cid='bd923729-6646-4c8e-b554-7e3ec75fab83'
out=ROOT/'docs/test-artifacts/meme-image-quality-20261006';out.mkdir(exist_ok=True)
checks=[]
def check(name,value):
    assert value,name
    checks.append(name);print('PASS '+name,flush=True)
d=httpx.get(base+'/api/v1/contents/'+cid).json();r=next(v for v in d['revisions'] if v['revision_id']==d['active_revision_id'])
diag=httpx.get(base+'/api/v1/contents/'+cid+'/diagnostics').json();before=len(diag['calls'])
check('latest image revision is V5',r['version']==5)
check('all previous drafts retained',len(d['revisions'])==5)
check('no approval or publishing',d['state']=='ready_for_review' and all(v['state']=='ready_for_review' for v in r['platforms']))
for v in r['platforms']:
    platform=v['platform'];pages=v['pages'];text=''.join(i['detail'] for p in pages for i in p['visual']['items'])
    check(platform+' exactly two reader-facing pages',len(pages)==2)
    check(platform+' classic dialogue appears in image payload','你家贵是贵点，但吃了不烧心' in text and '宁可少挣点，也不能坏了规矩' in text)
    check(platform+' explains actual meaning and provides original examples','靠谱' in text and '原创例句' in ''.join(i['label'] for p in pages for i in p['visual']['items']))
    check(platform+' no disclaimer cards or dominance',not quality_issues(pages))
    check(platform+' no repeated summaries',all(not p['body'] for p in pages))
    check(platform+' only one short source credit',bool(pages[0]['footnote']) and not pages[1]['footnote'] and len(pages[0]['footnote'])<=48)
    docs=build_pages({**v,'form':'meme'},engineering_default(platform))
    check(platform+' all substantive detail reaches actual HTML',all(i['detail'] in doc['html'] for p,doc in zip(pages,docs) for i in p['visual']['items']))
    check(platform+' no office decoration or generic source filler',all('class="studio-art"' not in doc['html'] and '方法图解 · 示例不代表实际结果' not in doc['html'] for doc in docs))
    for a in v['artifacts']:
        response=httpx.get(base+a['url']);response.raise_for_status()
        check(platform+f" PNG {a['page_index']} verified",hashlib.sha256(response.content).hexdigest()==a['sha256'] and a['template_version'].endswith('@2'))
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path=PlaywrightRenderer.resolve_executable());page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(base+'/views/ReviewPreview.html?content='+cid);expect(page.locator('#pages img')).to_have_count(2)
    page.locator('#pages').screenshot(path=str(out/'final-preview.png'))
    check('app preview shows latest two images',True)
    check('no preview script errors',not errors)
    browser.close()
after=len(httpx.get(base+'/api/v1/contents/'+cid+'/diagnostics').json()['calls'])
check('read-only verification creates no model calls',before==after)
result={'revision':r['version'],'passed':len(checks),'failed':0,'checks':checks,'model_audit_status':'Latest extra semantic audit returned invalid format; not counted as passed.'}
(out/'final-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
(out/'final-content.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
(out/'final-diagnostics.json').write_text(json.dumps(diag,ensure_ascii=False,indent=2),encoding='utf-8')
print(f'结果：{len(checks)} 通过 / 0 失败')
