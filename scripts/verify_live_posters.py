"""Read-only checks of the running workbench and final paid regression output."""
import sys,json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import httpx
from playwright.sync_api import sync_playwright
from app.services.renderer import PlaywrightRenderer
from app.services.token_cost import image_quality_issues,ledger_from,compile_formula_rows
from copy import deepcopy
base='http://127.0.0.1:8000';out=ROOT/'docs/test-artifacts/template-styles-20261006';out.mkdir(parents=True,exist_ok=True)
checks=[]
def check(name,value):
    assert value,name
    checks.append(name);print('PASS '+name)
packages=httpx.get(base+'/api/v1/template-packages').json()['items']
check('live existing five packages all rich',len(packages)==5 and all(p['style']['decoration']=='rich' for p in packages))
check('live packages have five different visual languages',len({p['style']['design_family'] for p in packages})==5)
cid='91454310-7f4d-4b2b-ae51-f54eb0d0be84'
detail=httpx.get(base+'/api/v1/contents/'+cid).json();rev=next(r for r in detail['revisions'] if r['revision_id']==detail['active_revision_id'])
check('latest actual content remains real and awaiting review',detail['run_mode']=='real' and detail['state']=='ready_for_review' and rev['version']==3)
for platform in rev['platforms']:
    check(platform['platform']+' has two pages and complete actual image explanations',len(platform['pages'])==2 and not image_quality_issues(platform['pages']))
    check(platform['platform']+' freezes selected pair',all(p['visual']['template_package_id']=='friendly_guide' and p['visual']['template_style']['decoration']=='rich' for p in platform['pages']))
    for artifact in platform['artifacts']:
        raw=httpx.get(base+artifact['url']).content
        check(platform['platform']+' page '+str(artifact['page_index'])+' API serves correct immutable PNG',raw.startswith(b'\x89PNG') and hashlib.sha256(raw).hexdigest()==artifact['sha256'])
diag=httpx.get(base+'/api/v1/contents/'+cid+'/diagnostics').json()
(ROOT/'docs/test-artifacts/token-cost-20261006/diagnostics.json').write_text(json.dumps(diag,ensure_ascii=False,indent=2),encoding='utf-8')
check('diagnostics exposes selected template rule snapshot',any('template.friendly_guide' in r['rule_snapshot'] for r in diag['runs']))
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path=PlaywrightRenderer.resolve_executable());page=browser.new_page(viewport={'width':1500,'height':1150});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(base+'/views/TemplateLibrary.html');page.wait_for_function("document.querySelectorAll('[data-package]').length===5")
    check('maintenance lists five updated packages',page.locator('[data-package]').count()==5)
    page.wait_for_function("document.querySelector('#template-preview').srcdoc.includes('family-handdrawn')")
    families=set()
    for package in packages:
        page.locator('[data-package="'+package['id']+'"]').click()
        family=package['style']['design_family']
        page.wait_for_function("family => document.querySelector('#template-preview').srcdoc.includes('family-'+family)",arg=family)
        frame=page.frame_locator('#template-preview');frame.locator('.style-item').first.wait_for()
        check('same-content '+package['id']+' has no fake ranking',frame.locator('.style-item').count()==4 and frame.locator('.style-rank').count()==0)
        families.add(family)
    check('library same-content comparison covers five distinct layouts',len(families)==5)
    page.screenshot(path=str(out/'live-template-library.png'),full_page=True)
    page.goto(base+'/views/Production.html');page.get_by_role('button',name='自定义选题',exact=True).click()
    page.locator('#creator-topic').fill('2万token价值多少钱')
    page.get_by_role('button',name='下一步 · 选择样式 →').click();page.wait_for_selector('[data-template=friendly_guide]')
    check('creation shows five actual poster previews',page.locator('.template-poster iframe').count()==5)
    page.wait_for_function("[...document.querySelectorAll('.template-poster')].every(n=>Number(n.style.getPropertyValue('--mini-scale'))>0)")
    check('actual thumbnail scales are set',page.locator('.template-poster').evaluate_all("nodes=>nodes.every(n=>Number(n.style.getPropertyValue('--mini-scale'))>0)"))
    for i in range(5):
        frame_element=page.locator('.template-poster iframe').nth(i)
        frame_element.scroll_into_view_if_needed()
        frame=frame_element.element_handle().content_frame()
        frame.locator('body[data-fit]').wait_for(state='attached')
        check('actual poster thumbnail '+str(i+1)+' is rendered and readable',float(frame.locator('body').get_attribute('data-fit'))>=.74 and frame.locator('.style-item').count()>=2)
    page.screenshot(path=str(out/'live-production-styles.png'),full_page=True)
    page.goto(base+'/views/ReviewPreview.html?content='+cid);page.wait_for_function("document.querySelector('#revision-template option[value=friendly_guide]')")
    check('revision uses dynamic five-package catalog',page.locator('#revision-template option').count()==7)
    for url in ['/views/TemplateLibrary.html','/views/Production.html?step=1','/views/ReviewPreview.html?content='+cid]:
        page.set_viewport_size({'width':390,'height':844});page.goto(base+url);page.wait_for_timeout(500)
        check(url.split('?')[0]+' narrow width',page.evaluate('document.documentElement.scrollWidth<=innerWidth+2'))
    check('live pages have no JavaScript errors',not errors)
    browser.close()
(out/'live-verification.json').write_text(json.dumps({'passed':len(checks),'checks':checks,'revision':rev['version']},ensure_ascii=False,indent=2),encoding='utf-8')
print(f'结果：{len(checks)} 通过 / 0 失败')
