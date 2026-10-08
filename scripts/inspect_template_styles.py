"""Read-only browser geometry and actual rendered style proof, no model calls."""
import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.services.template_packages import builtins,fingerprint
from app.services.template_examples import example_page,example_form
from app.services.visual_content import illustrated_page_html,VISUAL_FIT_JS
from app.services.profile_store import engineering_default
from app.services.renderer import PlaywrightRenderer
from playwright.sync_api import sync_playwright
out=ROOT/'docs/test-artifacts/template-styles-20261006';out.mkdir(parents=True,exist_ok=True)
report=[]
with sync_playwright() as p:
 b=p.chromium.launch(headless=True,executable_path=PlaywrightRenderer.resolve_executable())
 page=b.new_page(viewport={'width':1080,'height':1440})
 for id,package in builtins().items():
  for mode in ['representative','common']:
   pg=example_page(id,package.model_dump(mode='json'),mode=mode)
   doc,_=illustrated_page_html(pg,engineering_default('douyin'),'douyin',1,'sans-serif',form=example_form(pg))
   page.set_content(doc);page.evaluate('document.fonts.ready');fit=page.evaluate(VISUAL_FIT_JS)
   geometry=page.evaluate('''() => {const s=sel=>document.querySelector(sel)?.getBoundingClientRect().toJSON();return {area:s('.v-visual'),header:s('.v-header'),diagram:s('.diagram-content'),takeaway:s('.v-takeaway'),rows:[...document.querySelectorAll('.style-item')].map(el=>({height:el.offsetHeight,detail:getComputedStyle(el.querySelector('.style-detail')).fontSize,text:el.textContent}))}}''')
   page.screenshot(path=str(out/f'{id}-{mode}.png'))
   report.append({'id':id,'mode':mode,'fit':fit,'geometry':geometry})
   print(id,mode,round(fit,3),'area',round(geometry['area']['height']),'rows',[(r['height'],r['detail']) for r in geometry['rows'][:3]],flush=True)
 b.close()
(out/'geometry.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
from PIL import Image,ImageDraw,ImageFont
ids=['friendly_guide','editorial','illustrated','rank_cards','category_table']
sheet=Image.new('RGB',(1570,495),'#edf0f3');draw=ImageDraw.Draw(sheet)
font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',19)
for n,id in enumerate(ids):
 x=20+n*310
 draw.text((x+8,13),builtins()[id].name,font=font,fill='#203040')
 with Image.open(out/f'{id}-common.png') as im:
  thumb=im.convert('RGB');thumb.thumbnail((290,420));sheet.paste(thumb,(x,49))
sheet.save(out/'styles-comparison.png')
