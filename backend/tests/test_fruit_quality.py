"""C010 subject regression, verified values, safe local imagery and real layouts."""
import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.errors import ValidationFailed
from app.services.fruit_content import evidence, fruit_index, is_fruit_topic, validate_subject, FRUIT_IDS, atlas_uri
from app.services.visual_content import VisualSpec
from app.services.claim_rules import validate_claims_sources
from app.services.profile_store import engineering_default
from app.services.renderer import build_pages, check_layout, PlaywrightRenderer, verify_images
from app.services.research_service import ResearchService
from app.services.provider_contract import RunMode

passed = 0
def check(ok, message):
    global passed
    assert ok, message
    passed += 1

def rejects(fn, message):
    try:
        fn()
    except (ValueError, ValidationFailed):
        check(True, message)
    else:
        check(False, message)

topic = '秋燥、怕凉、控糖：三类人的秋果选择差异'
sources, claims = evidence()
check(len(sources) == 7 and len(claims) == 7, '缺少营养资料')
check(not validate_claims_sources(claims, sources), '营养证据引用不完整')
check(fruit_index()['apple']['nutrients']['energy_kcal'] == 52, '苹果官方数据')
check(fruit_index()['pear']['nutrients']['fiber_g'] == 3.1, '梨官方数据')
check(fruit_index()['kiwi']['nutrients']['vitamin_c_mg'] == 92.7, '绿肉猕猴桃口径')
check(fruit_index()['grape']['nutrients']['carbohydrate_g'] != fruit_index()['grape']['nutrients']['sugars_g'], '糖与碳水混用')
check(is_fruit_topic(topic) and not is_fruit_topic('苹果手机怎么挑') and not is_fruit_topic('AI创作效率'), '题材串线')
old = {'core_viewpoint':'不排水果榜，核心不是选哪种果，而是怎么吃。','actions':['填写秋果自检卡']}
rejects(lambda: validate_subject(old, topic), 'C010旧稿应拒收')
check(validate_subject(old, 'AI图文创作') is None, '不应影响其他主题')
check(atlas_uri().startswith('data:image/png;base64,'), '素材未本地化')

pages = []
groups = [('cover', ['apple','pear','orange','kiwi']), ('photo',['pear','orange']),
          ('photo',['apple','pear']), ('photo',['apple','kiwi']),
          ('nutrition',['apple','pear','orange']), ('nutrition',['kiwi','grape','persimmon'])]
for n, (kind, ids) in enumerate(groups, 1):
    items = [{'label':fruit_index()[fid]['name'], 'detail':'洗净后切块装盘；按自己的饮食计划分装。', 'icon':'page', 'photo_id':fid} for fid in ids]
    pages.append({'index':n, 'layout':'cover' if n == 1 else 'checklist', 'heading':'三类场景的秋果怎么选' if n==1 else '每100克营养对照' if kind=='nutrition' else '选择水果与具体吃法',
        'kicker':'秋果指南', 'body':['看品种，也看温度与搭配。'], 'footnote':'USDA SR Legacy 2018 · 生鲜可食部参考',
        'claim_ids':['FRUIT_'+fid for fid in ids], 'visual':{'kind':kind,'title':'水果选择与吃法', 'items':items, 'takeaway':'图片为AI示意图，参数有品种差异。'}})
draft = {'platform':'douyin','title':'秋果选择指南','caption':'苹果、梨、橙、猕猴桃、葡萄和柿子洗净切块。USDA SR Legacy 2018每100克生鲜可食部数据有品种差异，配图为AI写实示意图。','pages':pages}
check(validate_subject(draft,topic,platform=True) is None, '具体水果产品未通过')
for mutation in [lambda v:v['items'][0].update(photo_id='https://evil.invalid/x'),
                 lambda v:v['items'][0].update(photo_id='../secret'),
                 lambda v:v['items'][0].update(label='葡萄'),
                 lambda v:v['items'][0].update(url='https://evil.invalid/x'),
                 lambda v:v.update(kind='map')]:
    v=deepcopy(pages[0]['visual']);mutation(v)
    rejects(lambda v=v:VisualSpec.model_validate(v), '素材枚举/题材边界')
v=deepcopy(pages[0]['visual']);v['items'][1]['photo_id']='apple';v['items'][1]['label']='苹果'
rejects(lambda:VisualSpec.model_validate(v), '重复图片')
v=deepcopy(pages[0]['visual']);v['kind']='nutrition'
rejects(lambda:VisualSpec.model_validate(v), '营养表不能挤4种水果')
for mutation in [lambda d:d['pages'].pop(),lambda d:d['pages'][0].update(claim_ids=[]),lambda d:d.update(caption='具体水果切块')]:
    d=deepcopy(draft);mutation(d)
    rejects(lambda d=d:validate_subject(d,topic,platform=True), '缺图/缺来源/缺口径')
rt=SimpleNamespace(search_provider=lambda:None)
research=ResearchService(rt).research(topic=topic,run_mode=RunMode.REAL,user_materials=[{'text':'请推荐秋果','kind':'editorial_plan'}])
check(len(research.claims)==8 and research.claims[0].kind=='opinion', '编辑提纲不得伪装事实')
check(not research.search_executed and any(s.kind=='official_nutrition_dataset' for s in research.sources), '资料库不是实时搜索')
fixture=ResearchService(rt).research(topic=topic,run_mode=RunMode.FIXTURE)
check(not fixture.claims, '模拟结果不能混入真实资料库')
for platform in ['douyin','xiaohongshu']:
    pf=engineering_default(platform);d={**draft,'platform':platform}
    check(not [i for i in check_layout(d,pf) if i.level=='error'],platform+'布局预检')
    docs=build_pages(d,pf)
    check(all('AI写实示意图' in p['html'] for p in docs),platform+'图片披露')
    check('92.7' in docs[-1]['html'] and '每 100 克' in docs[-1]['html'],platform+'营养值未由资料库渲染')
    check(all('图解创作笔记' not in p['html'] for p in docs),platform+'误用创作教程品牌')
    with tempfile.TemporaryDirectory(prefix='cwb-fruit-') as temp:
        result=PlaywrightRenderer(Path(temp)).render_platform(d,pf,display_id='FRUIT-TEST',content_revision_version=1,platform_revision_version=1)
        check(not result.errors,platform+'实际渲染失败：'+str(result.errors))
        check(len(result.images)==6,platform+'成品页数')
print(f'{passed} 通过 / 0 失败')
