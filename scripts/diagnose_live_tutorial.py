"""Read-only geometry reproduction from the exact live revision."""
import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.services.visual_content import illustrated_page_html,VISUAL_FIT_JS
from app.services.renderer import PlaywrightRenderer
from app.services.profile_store import engineering_default
from playwright.sync_api import sync_playwright
path=ROOT/'docs/test-artifacts/hot-topic-real-20261006/work_tutorial_beginner-content.json'
content=json.loads(path.read_text(encoding='utf-8'))
pages=content['revisions'][-1]['platforms'][0]['pages']
(ROOT/'backend/tests/fixtures/work_tutorial_real.json').write_text(json.dumps(pages,ensure_ascii=False,indent=2),encoding='utf-8')
with sync_playwright() as pw:
 browser=pw.chromium.launch(headless=True,executable_path=PlaywrightRenderer.resolve_executable())
 page=browser.new_page(viewport={'width':1080,'height':1440})
 for pg in pages:
  doc,_=illustrated_page_html(pg,engineering_default('douyin'),'douyin',pg['index'],'sans-serif',form='guide')
  page.set_content(doc);page.evaluate('document.fonts.ready')
  print(pg['index'],page.evaluate(VISUAL_FIT_JS),page.evaluate("() => [...document.querySelectorAll('.v-header,.diagram-content,.v-takeaway')].map(e=>({cls:e.className,h:e.getBoundingClientRect().height}))"))
 browser.close()
