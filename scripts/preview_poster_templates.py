"""Render all five existing template pairs with the production renderer; no API calls."""
import sys,json
from pathlib import Path
from dataclasses import asdict
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.services.template_packages import builtins,fingerprint
from app.services.template_examples import example_page,example_form
from app.services.profile_store import engineering_default
from app.services.renderer import PlaywrightRenderer,verify_images

out=ROOT/'docs/test-artifacts/template-styles-20261006'
out.mkdir(parents=True,exist_ok=True);report=[]
for id,p in builtins().items():
    package={**p.model_dump(mode='json'),'package_version':fingerprint(p)}
    for platform in ['douyin','xiaohongshu']:
        page=example_page(id,package)
        draft={'platform':platform,'form':example_form(page),
            'title':'海报模板示例','caption':'本地固定演示，不是真实榜单。','pages':[page]}
        result=PlaywrightRenderer(out).render_platform(draft,engineering_default(platform),display_id=id)
        errors=verify_images(result,out) if result.passed else []
        report.append({'id':id,'name':p.name,'platform':platform,'passed':result.passed and not errors,
            'version':package['package_version'],'images':result.images,'issues':[asdict(i) for i in result.issues],'verification_errors':errors})
        print(id,platform,'PASS' if report[-1]['passed'] else report[-1]['issues'])
(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
raise SystemExit(0 if all(r['passed'] for r in report) else 1)
