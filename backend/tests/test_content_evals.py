"""Skill 路由用例表：用户说法 → 预期阶段 / 题材形态 / 数量约束 / 交付物。"""
import sys,tempfile,os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-evals-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",
                  CWB_ARTIFACT_DIR=str(tmp/'artifacts'),CWB_TMP_DIR=str(tmp/'tmp'),
                  CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'))

from app.services.content_forms import build_brief
from app.services.content_recipes import apply_recipe
from app.services.content_skills import STAGES,routing_cases,stage_entry_contract
from app.services.deliverables import DELIVERABLE_KEYS
from app.services.acceptance import export_contract,rank_contract

passed=0
def check(name,value):
    global passed
    assert value,name
    passed+=1;print('PASS '+name,flush=True)

doc=routing_cases()
cases=doc['cases']
check('用例表有 schema_version 与说明',bool(doc.get('schema_version')) and bool(doc.get('notice')))
check('用例表覆盖至少四个主要入口',len(cases)>=4)
check('用例 id 唯一',len({c['id'] for c in cases})==len(cases))

entry=stage_entry_contract()
by_id={c['id']:c for c in cases}
for case in cases:
    expect=case['expect']
    brief=apply_recipe(build_brief(topic=case['request']))
    label=case['id']
    # 1) 说法的题材形态与题材方向必须与用例表一致
    if 'form' in expect:
        check(f'{label}: 说法落到题材「{expect["form"]}」',brief.form==expect['form'])
    if 'direction' in expect:
        check(f'{label}: 说法落到方向「{expect["direction"]}」',brief.direction==expect['direction'])
    # 2) 用户显式数量必须写进简报，而不是靠提示词运气
    for field in ('rank_count','item_count'):
        if field in expect:
            check(f'{label}: {field} 被解析为 {expect[field]}',getattr(brief,field)==expect[field])
    if 'page_budget' in expect:
        check(f'{label}: 页数预算被解析为 {expect["page_budget"]}',
              [brief.page_min,brief.page_max]==expect['page_budget'])
    if 'template_id' in expect:
        check(f'{label}: 冻结的样式模板为 {expect["template_id"]}',brief.template_id==expect['template_id'])
    # 3) 路由到的阶段必须存在，且该阶段的入口说明完整
    stage=expect['stage_entry']
    check(f'{label}: 路由阶段「{stage}」是真实阶段',stage in STAGES)
    check(f'{label}: 路由阶段的入口说明完整',stage in entry and not entry[stage]['missing'])
    # 4) 交付物键名与程序实际产出的键名同源
    check(f'{label}: 交付物键名与程序一致',
          list(expect['deliverable_keys'])==list(DELIVERABLE_KEYS))
    # 5) 专项验收必须存在，且契约本身可用
    contract=expect['acceptance']
    check(f'{label}: 专项验收「{contract}」可用',
          contract=='rank' and bool(rank_contract([],10)['checks'])
          or contract=='export' and bool(export_contract(0,[])['checks']))

check('页面/榜单/清单三类形态都被用例覆盖',
      {'ranking','listicle','comparison','explainer'} <= {by_id[c]['expect'].get('form') for c in by_id})
check('榜单用例都声明了榜单专项验收',
      all(by_id[c]['expect']['acceptance']=='rank' for c in by_id if by_id[c]['expect'].get('form')=='ranking'))
check('需要真实数据又未配搜索的用例标明会在规划被拦下',
      by_id['ranking-needs-real-data-without-search']['expect']['blocking_stage']=='planning')
check('用例表声明的交付物键名就是清单服务导出的键名',
      list(doc['deliverable_keys'])==list(DELIVERABLE_KEYS))
check('每个用例都有可读说明',all(c['expect'].get('note') for c in cases))

print(f'结果：{passed} 通过 / 0 失败')
