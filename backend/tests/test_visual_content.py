"""Illustrated output contract and real browser layout, isolated and offline."""
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.services.profile_store import engineering_default
from app.services.renderer import build_pages, check_layout, PlaywrightRenderer, verify_images

pf = engineering_default('douyin')
kinds = ['cover', 'map', 'flow', 'compare', 'example', 'checklist']
pages = []
for i, kind in enumerate(kinds, 1):
    items = [{'label': label, 'detail': detail, 'icon': icon} for label, detail, icon in [
        ('受众问题', '示例：个人创作者不知道生成稿该先检查什么。', 'question'),
        ('核心观点', '建议：先把问题、结论、来源与页内容分开审。', 'idea'),
        ('来源依据', '区分公开文档摘要与编辑建议，不冒称原文。', 'source'),
        ('页内内容', '检查每页是否有解释、具体例子与明确行动。', 'page')]]
    if kind == 'compare':
        items = items[:2]
    pages.append({'index': i, 'layout': 'cover' if i == 1 else 'checklist',
        'heading': '把 AI 图文变成可审的作品', 'kicker': '创作者图解指南',
        'body': ['先检查内容结构，再判断是否适合发布。', '示例用于说明方法，不代表真实运营结果。'],
        'footnote': '依据：公开文档摘要与编辑建议', 'claim_ids': ['C01'],
        'visual': {'kind': kind, 'title': '四个检查点', 'items': items,
                   'takeaway': '图解帮助理解，事实仍须逐条核对。'}})
draft = {'platform': 'douyin', 'title': 'AI 图文怎么审', 'caption': '示例与方法说明。', 'pages': pages}
docs = build_pages(draft, pf)
multi_compare=deepcopy(draft);multi_compare['pages']=[deepcopy(pages[3])]
multi_compare['pages'][0]['visual']['items']=[{'label':f'对照项{n}','detail':'完整保留多项对照，不截断成两个对象','icon':'idea'} for n in range(1,5)]
multi_docs=build_pages(multi_compare,pf)
assert all(f'对照项{n}' in multi_docs[0]['html'] for n in range(1,5)), 'C045 超过两项对照应完整渲染，不能元组越界或截断'
assert all('data-visual-kind' in d['html'] for d in docs), '仍然是纯文字列表，没有图解模块'
assert all(d['template_version'].startswith('illustrated-') for d in docs)
assert '<svg' in docs[0]['html'], '封面缺少本地绘制的主题插画'
assert len({d['template_version'] for d in docs}) == 6
assert not [i for i in check_layout(draft, pf) if i.level == 'error']

from app.services.visual_content import VisualSpec
from app.services.visual_content import VISUAL_FIT_JS
from pydantic import ValidationError
for mutate in [lambda v: v.update(kind='remote_photo'),
               lambda v: v.update(url='https://example.invalid/image'),
               lambda v: v['items'][0].update(icon='script')]:
    v = deepcopy(pages[0]['visual']); mutate(v)
    try:
        VisualSpec.model_validate(v)
        raise AssertionError('非法图解结构没有被拒收')
    except ValidationError:
        pass
bad = deepcopy(draft); bad['pages'][0]['visual']['kind'] = 'unsupported'
assert any(i.code == 'VISUAL_INVALID' for i in check_layout(bad, pf))
long=deepcopy(pages[0]['visual']);long['items'][0].update(label='完整问题名称超过十四字符也应该保存',detail='完整的答案说明不应该因为固定六十四个字符就终止制作。'*4)
VisualSpec.model_validate(long)
# Adaptive poster reflows long answers without shrinking them into thumbnails.
from playwright.sync_api import sync_playwright
adaptive=deepcopy(draft);adaptive['pages']=adaptive['pages'][:1]
adaptive['pages'][0]['visual'].update(template_style={'design_family':'neon','heading_font':'mono','body_size':29,'decoration':'rich'},items=[{'label':f'问题{i+1}：上下文窗口与多轮任务的状态信息应当如何管理？','detail':'上下文窗口包含系统提示、历史消息、用户输入和模型输出。多轮任务需要保存状态，对过长记录进行摘要或按任务检索，不能假设模型始终记得所有细节。','icon':'question'} for i in range(5)])
with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path=PlaywrightRenderer.resolve_executable(),args=['--no-sandbox'])
    pg=browser.new_page();pg.set_content(build_pages(adaptive,pf)[0]['html'])
    fit=pg.evaluate(VISUAL_FIT_JS)
    assert fit>=.74,fit
    assert pg.locator('.style-item').count()==5
    assert all(i['detail'] in pg.locator('.v-visual').inner_text() for i in adaptive['pages'][0]['visual']['items'])
    browser.close()
escaped = deepcopy(draft); escaped['pages'][0]['visual']['items'][0]['label'] = '<img src=x>'
assert '<img src=x>' not in build_pages(escaped, pf)[0]['html']
legacy = deepcopy(draft)
for page in legacy['pages']: page.pop('visual')
assert all(d['template_version'] in {'cover@8', 'checklist@8'} for d in build_pages(legacy, pf))

with tempfile.TemporaryDirectory(prefix='cwb-visual-') as tmp:
    renderer = PlaywrightRenderer(Path(tmp))
    result = renderer.render_platform(draft, pf, display_id='VISUAL')
    assert result.passed, [(i.code, i.message) for i in result.errors]
    assert len(result.images) == 6
    assert not verify_images(result, Path(tmp))
    assert set(result.template_versions) == {d['template_version'] for d in docs}
    rerender = renderer.render_platform(draft, pf, display_id='VISUAL')
    assert result.manifest_hash() == rerender.manifest_hash()
    dense = deepcopy(draft)
    for page in dense['pages']:
        page['heading'] = '四个关键审稿字段分别怎么检查与填写'
        for item in page['visual']['items']:
            item['detail'] = '先写清读者遇到的具体问题，再注明这条来源支持哪一句观点；这是教学示例，不代表实际运营结果。'
    dense_result = renderer.render_platform(dense, pf, display_id='DENSE')
    assert dense_result.passed, [(i.page_index,i.code,i.message) for i in dense_result.errors]

# A changed diagram must create a revision, even when the master is unchanged.
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.entities import Base, ContentItem, ContentRevision
from app.services.compose_service import ComposeService, ComposeOutcome, MasterDraft, PlatformDraft
from app.services.provider_contract import RunMode
from app.services.compose_service import _normalize_variant_response
assert _normalize_variant_response({'type':'json_object','title':'示例'}) == {'title':'示例'}
assert _normalize_variant_response({'type':'unexpected','title':'示例'})['type'] == 'unexpected'
assert _normalize_variant_response({'type':'json_object','state':'approved'})['state'] == 'approved'
engine = create_engine('sqlite://')
Base.metadata.create_all(engine)
sf = sessionmaker(bind=engine)
with sf() as s:
    content = ContentItem(display_id='VISUAL-REV', topic='公开方法示例')
    s.add(content); s.commit(); cid = content.id
service = ComposeService(sf, profiles=object())
from app.services.adapters.fixture import FixtureAdapter
from app.services.provider_contract import ProviderConfig, ProviderKind, AdapterType
from app.services.compose_service import VARIANT_SCHEMA
adapter=FixtureAdapter(ProviderConfig(name='local',kind=ProviderKind.TEXT,adapter_type=AdapterType.OPENAI_COMPATIBLE,
    base_url='https://fixture.invalid',model_id='fixture'),api_key='fixture')
for n in range(100):
    pair={p:PlatformDraft(platform=p,**adapter.complete(f'平台：{p}\n可用主张 C01',
        json_schema=VARIANT_SCHEMA,request_key=f'case-{n}-{p}').parsed) for p in ['douyin','xiaohongshu']}
    service._assert_variants_distinct(pair)
identical={p:pair['douyin'].model_copy(update={'platform':p}) for p in ['douyin','xiaohongshu']}
from app.core.errors import ValidationFailed
try:
    service._assert_variants_distinct(identical)
    raise AssertionError('只改平台名的稿件仍必须被拒绝')
except ValidationFailed:
    pass
outcome = ComposeOutcome(master=MasterDraft(audience_problem='如何审稿', core_viewpoint='按字段检查'),
    variants={'douyin': PlatformDraft.model_validate(draft)})
service._persist(cid, outcome, profiles={'douyin':pf}, run_mode=RunMode.LOCAL_SEED)
outcome.variants['douyin'].pages[1]['visual']['takeaway'] = '修改后的图解也必须单独保存版本。'
service._persist(cid, outcome, profiles={'douyin':pf}, run_mode=RunMode.LOCAL_SEED)
with sf() as s:
    assert s.query(ContentRevision).count() == 2, '修改图解仍被同一母稿哈希吞掉'
service._persist(cid, outcome, profiles={'douyin':pf}, run_mode=RunMode.LOCAL_SEED)
with sf() as s:
    assert s.query(ContentRevision).count() == 2

# Upgrade existing drafts through the normal queue/worker, with revision binding.
import os
storage = Path(tempfile.mkdtemp(prefix='cwb-illustrate-api-'))
os.environ['CWB_STORAGE_ROOT'] = str(storage)
os.environ['CWB_ARTIFACT_DIR'] = str(storage / 'artifacts')
os.environ['CWB_DATABASE_URL'] = f"sqlite:///{storage / 'api.db'}"
os.environ['CWB_SECRET_STORE_PATH'] = str(storage / 'secrets.json')
from app.core.config import get_settings
get_settings.cache_clear()
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
seed = str(ROOT / 'examples/C001/seeds/C001/seed.json')
baseline = client.post('/api/v1/contents/produce-from-seed', json={'seed_path':seed,'render':False}).json()
cid = baseline['content_id']
detail = client.get(f'/api/v1/contents/{cid}').json()
base = detail['active_revision_id']
endpoint = f'/api/v1/contents/{cid}/illustrate'
assert client.post(endpoint, json={'base_revision_id':'stale','run_mode':'fixture'}).status_code == 409
queued = client.post(endpoint, json={'base_revision_id':base,'run_mode':'fixture'})
assert queued.status_code == 202, queued.text
duplicate = client.post(endpoint, json={'base_revision_id':base,'run_mode':'fixture'})
assert duplicate.json()['run_id'] == queued.json()['run_id']
from app.worker import Worker
from app.api.production import SessionFactory
worker = Worker(SessionFactory, get_settings())
checked_variants=[]
original_distinct=worker.production.compose._assert_variants_distinct
def record_distinct(variants):
    checked_variants.append(set(variants))
    original_distinct(variants)
worker.production.compose._assert_variants_distinct=record_distinct
real_render = worker.production.pipeline.render
worker.production.pipeline.render = lambda *_: {'ok':False,'issues':[{'code':'TEST_RENDER_BLOCK'}]}
worker.tick()
run = client.get(f"/api/v1/runs/{queued.json()['run_id']}").json()
assert checked_variants==[{'douyin','xiaohongshu'}], '旧稿图解升级也必须检查双平台差异'
assert run['state'] == 'failed' and run['blocked_stage'] == 'render'
failed_detail = client.get(f'/api/v1/contents/{cid}').json()
new_id = failed_detail['active_revision_id']
assert new_id != base
worker.production.pipeline.render = real_render
retry = client.post(f"/api/v1/runs/{queued.json()['run_id']}/control",json={'action':'retry'})
assert retry.status_code == 200
worker.tick()
run = client.get(f"/api/v1/runs/{queued.json()['run_id']}").json()
assert run['state'] == 'succeeded', run
assert all(j['state']=='succeeded' for j in run['jobs'])
detail = client.get(f'/api/v1/contents/{cid}').json()
assert detail['active_revision_id'] == new_id
assert detail['active_revision_id'] != base
assert len(detail['revisions']) == 2
active = next(r for r in detail['revisions'] if r['revision_id'] == detail['active_revision_id'])
assert all(p.get('visual') for pr in active['platforms'] for p in pr['pages'])
assert all(pr['state'] == 'ready_for_review' and pr['artifacts'] for pr in active['platforms'])
print('41 通过 / 0 失败')
