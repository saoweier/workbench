"""专项验收契约：榜单项数、名次顺序、指标对应项目、导出图片数量；全部离线确定性判定。"""
import os,sys,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-accept-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",
                  CWB_ARTIFACT_DIR=str(tmp/'artifacts'),CWB_TMP_DIR=str(tmp/'tmp'),
                  CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'))

from app.services.acceptance import rank_contract,rank_problem,export_contract
from app.services.compose_service import ComposeService,_valid_rank_layout,MasterDraft
from app.services.content_forms import build_brief
from app.services.profile_store import engineering_default
from app.services.renderer import RenderResult,verify_images
from app.services.quality_rules import check_quality,UNREPAIRABLE_CODES

passed=0
def check(name,value):
    global passed
    assert value,name
    passed+=1;print('PASS '+name,flush=True)


def rank_page(items,total=10):
    return {'index':1,'layout':'cover','heading':'榜单','body':[],'claim_ids':[],
            'visual':{'kind':'rank','title':'榜单','items':items,'takeaway':''}}

def good_items(total=10,metric=False):
    items=[{'label':f'对象{n}','rank':n,'detail':'入选理由'} for n in range(1,total+1)]
    if metric:
        for item in items:
            item.update(metric_text='热度 100',metric_label='热度指数',metric_source_id='U01')
    return items

# ---------------------------------------------------------------- 榜单契约
ok=rank_contract([rank_page(good_items())],10)
check('完整榜单通过专项验收',ok['passed'] and ok['applicable'] and all(c['passed'] for c in ok['checks']))
check('榜单契约覆盖四项必查（版面/条目/顺序/指标）',
      {'rank_board','rank_count','rank_order','rank_metric'}.issubset({c['key'] for c in ok['checks']}))

missing=rank_contract([{'index':1,'layout':'cover','visual':{'kind':'cover','items':[]}}],10)
check('缺少名次版面被拦下',not missing['passed'] and 'RANK_BOARD_MISSING' in {c['code'] for c in missing['checks'] if not c['passed']})

short=rank_contract([rank_page(good_items(5))],10)
check('榜单项数不足报出确切差数',not short['passed'] and '实际只有 5 条' in short['problems'][0])

dupe=good_items();dupe[3]['label']=dupe[0]['label']
dup=rank_contract([rank_page(dupe)],10)
check('重复对象被拦下',not dup['passed'] and 'RANK_DUPLICATE_OBJECT' in {c['code'] for c in dup['checks'] if not c['passed']})

bad_order=good_items();bad_order[2]['rank']=9
order=rank_contract([rank_page(bad_order)],10)
check('名次不连续被拦下',not order['passed'] and 'RANK_ORDER_BROKEN' in {c['code'] for c in order['checks'] if not c['passed']})

no_source=good_items(metric=True);no_source[0]['metric_source_id']=None
metric=rank_contract([rank_page(no_source)],10)
check('指标缺少来源被拦下',not metric['passed'] and 'RANK_METRIC_MISMATCH' in {c['code'] for c in metric['checks'] if not c['passed']})

check('rank_problem 无问题时返回 None',rank_problem([rank_page(good_items())],10) is None)
check('rank_problem 有问题时返回可读文案',isinstance(rank_problem([rank_page(good_items(3))],10),str))

# 指标必须与对象同源（借 content_recipes.validate_metrics 判定，不凭语义相似）
metric_items=good_items(metric=True)
source={'id':'U01','kind':'public_article','access_state':'ok',
        'excerpt':'\n'.join(f"{i['label']} {i['metric_text']}" for i in metric_items)}
swapped=[{**i} for i in metric_items]
for item in swapped:
    item['metric_text']='热度 999'
check('指标与对象不同源时被拦下',
      not rank_contract([rank_page(swapped)],10,sources=[source])['passed'])
check('指标确实同源时通过',
      rank_contract([rank_page(metric_items)],10,sources=[source])['passed'])
check('没有可读来源时只查字段完整性、不误报同源失败',
      rank_contract([rank_page(metric_items)],10,sources=[])['passed'])

# ---------------------------------------------------------------- 导出契约
check('导出图片数量与页数一致时通过',export_contract(3,[{'page_index':1},{'page_index':2},{'page_index':3}])['passed'])
check('导出图片数量与页数一致时通过（零图零页）',export_contract(0,[])['passed'])
short_export=export_contract(3,[{'page_index':1}])
check('导出图片数量与页数不一致被拦下',
      not short_export['passed'] and 'IMAGE_COUNT_MISMATCH' in {c['code'] for c in short_export['checks'] if not c['passed']})
gap=export_contract(3,[{'page_index':1},{'page_index':3},{'page_index':3}])
check('导出页序不完整被拦下',
      not gap['passed'] and 'PAGE_SEQUENCE_INCOMPLETE' in {c['code'] for c in gap['checks'] if not c['passed']})

empty=RenderResult(platform='douyin',revision_version=1,profile_version='p',width=1080,height=1440,template_versions=['cover@8'])
check('verify_images 未给页数时不做数量检查',not verify_images(empty,tmp/'artifacts'))
check('verify_images 给了页数就能发现导出缺失',
      'IMAGE_COUNT_MISMATCH' in {i.code for i in verify_images(empty,tmp/'artifacts',expected_pages=2)})

# ---------------------------------------------------------------- 契约接入
pf=engineering_default('xiaohongshu')
bad_draft={'platform':'xiaohongshu','title':'榜单','caption':'编辑整理榜单','pages':[rank_page(good_items(9))]}
rep=check_quality(bad_draft,pf,rank_count=10)
check('check_quality 带 rank_count 时跑榜单专项验收',
      'RANK_COUNT_MISMATCH' in {i.code for i in rep.issues})
check('榜单专项问题标为不可自动修复',
      all(not i.repairable for i in rep.issues if i.code=='RANK_COUNT_MISMATCH'))
check('导出类新错误码纳入不可修复集合',
      {'IMAGE_COUNT_MISMATCH','PAGE_SEQUENCE_INCOMPLETE'}.issubset(UNREPAIRABLE_CODES))
check('不带 rank_count 时不做榜单专项验收',
      'RANK_COUNT_MISMATCH' not in {i.code for i in check_quality(bad_draft,pf).issues})

# ---------------------------------------------------------------- 规则改写与契约同源
brief=build_brief(topic='热点来源TOP10排行榜1～2页')
svc=ComposeService(object(),profiles=object())
master=MasterDraft(audience_problem='需要完整榜单',core_viewpoint='热点来源TOP10',claim_ids=[],limitations=[],
                   actions=[],pages=[{'index':1,'heading':'榜单','points':[f'来源对象{n}' for n in range(1,11)],'claim_ids':[]}])
for platform in ('douyin','xiaohongshu'):
    lim=engineering_default(platform).limits
    draft=svc._ranking_by_rules(master,platform,{'min_pages':1,'max_pages':2},lim,brief)
    pages=draft['pages']
    check(f'{platform} 规则榜单稿通过专项验收',_valid_rank_layout(pages,brief) is None)
    items=[i for p in pages for i in (p.get('visual') or {}).get('items',[]) if (p.get('visual') or {}).get('kind')=='rank']
    check(f'{platform} 规则榜单稿名次从1连续到10',[i['rank'] for i in items]==list(range(1,11)))
check('规则榜单稿平台封面差异保持：小红书有封面页、抖音没有',
      any(p['layout']=='cover' for p in svc._ranking_by_rules(master,'xiaohongshu',{'min_pages':1,'max_pages':2},engineering_default('xiaohongshu').limits,brief)['pages'])
      and not any(p['layout']=='cover' for p in svc._ranking_by_rules(master,'douyin',{'min_pages':1,'max_pages':2},engineering_default('douyin').limits,brief)['pages']))

# 非榜单题材不误判
check('非榜单题材不触发榜单契约',_valid_rank_layout([rank_page(good_items(3))],build_brief(topic='三个提高睡眠质量的方法')) is None)

print(f'结果：{passed} 通过 / 0 失败')
