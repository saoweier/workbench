"""创作到成品修改的完整体验核验：全流程、双平台差异、成品修改。

三块各自独立可读：

[1] 完整生产流程：选题 → 研究 → 规划 → 母稿 → 双平台改写 → 渲染 → 人工确认 → 发布包。
    全程走真实 HTTP 协议路径，但协议端是**本机协议桩**（`mock_provider`），
    不发起任何真实网络调用、不产生任何费用。
[2] 抖音版与小红书版的差异：两份文案确实不同；小红书必须有封面页，
    抖音可无；模型漏了封面时由程序补上，而不是把整份稿判死。
[3] 成品修改：改稿能真跑完整条链路，并且**页数**与**内容详细程度**都真的会变
    （过去「更详细」只是提示词里的一句好话，改完看不出差别）。
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
tmp = Path(tempfile.mkdtemp(prefix='cwb-flow-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp), CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}?timeout=30",
                  CWB_ARTIFACT_DIR=str(tmp / 'artifacts'), CWB_TMP_DIR=str(tmp / 'tmp'),
                  CWB_SECRET_STORE_PATH=str(tmp / 'secrets.json'), CWB_PENDING_REVIEW_STOCK_LIMIT='20')

from fastapi.testclient import TestClient
from app.main import app
from app.api import creation
from app.models.entities import Job, Run
from app.services.compose_service import PAGE_STRATEGY
from app.services.platform_policy import (cover_instruction, insert_cover, needs_cover,
                                          requires_cover)
from app.services.content_forms import (DENSITY_CAPTION, adjust_page_budget,
                                        parse_density_direction, strip_density_notes)
from app.worker import Worker
from app.core.config import get_settings
from mock_provider import start_mock

client = TestClient(app)
headers = {'X-CWB-Local-Action': 'account-connection'}
passed = 0


def check(name, value):
    global passed
    assert value, name
    passed += 1
    print('PASS ' + name, flush=True)


def run_state(run_id):
    return client.get('/api/v1/runs/' + run_id).json()


def drain(run_id, limit=40):
    """推到终态。一个 job 跑完整条流水线，但仍按次取件，避免假设 stage 数量。"""
    worker = Worker(creation.SessionFactory, get_settings(), worker_id='flow-test')
    for _ in range(limit):
        if run_state(run_id)['state'] in {'succeeded', 'failed', 'paused', 'stopped'}:
            return run_state(run_id)
        worker.tick()
    return run_state(run_id)


def settled(run_id, label):
    run = drain(run_id)
    check('%s：state=%s error=%s' % (label, run['state'], run.get('error')), run['state'] == 'succeeded')
    return run


def queued_brief(run_id):
    with creation.SessionFactory() as s:
        job = s.query(Job).filter_by(run_id=run_id, stage='batch_dispatch').one()
        req = dict(job.output_refs['request'])
    return req, req['creative_brief']


def sent_schemas(bodies):
    """从本机协议桩收到的请求里回读模型实际拿到的 JSON Schema。"""
    marker = '输出必须符合以下 JSON Schema，且仅输出 JSON：\n'
    out = []
    for body in bodies:
        text = body['messages'][-1]['content']
        if marker in text:
            try:
                out.append(json.loads(text.split(marker, 1)[1]))
            except ValueError:
                pass
    return out


def caption_caps(bodies):
    caps = []
    for schema in sent_schemas(bodies):
        spec = (schema.get('properties') or {}).get('caption') or {}
        if 'maxLength' in spec:
            caps.append(spec['maxLength'])
    return caps


# ============================================================ 离线纯函数：改稿的两把尺子
print('\n[0] 改稿的两把尺子（页数与详细程度）', flush=True)
check('增加页数是把下限抬到原上限，而不是只放宽上限',
      adjust_page_budget('explainer', 4, 7, 'up') == (7, 9))
check('减少页数不低于封面加正文', adjust_page_budget('explainer', 6, 6, 'down') == (2, 4))
check('已经只有一页时不再缩', adjust_page_budget('explainer', 1, 1, 'down') is None)
check('更短更精简会被识别为精简', parse_density_direction('更短更精简，删除重复解释，保留全部具体对象和核心事实。') == 'short')
check('更详细会被识别为详细', parse_density_direction('更详细，补充每项的具体理由和实用细节，保留页数、全部对象及统计口径。') == 'detailed')
check('「保留页数不变」不会被误判成篇幅诉求',
      parse_density_direction('保留页数不变，只改标题') is None)
check('详细程度句子可被替换而不是叠加',
      strip_density_notes('内容详细程度：详细，补充依据。\n保留原文要点') == '保留原文要点')
check('精简对应更短的发布文案上限', DENSITY_CAPTION['short'][1] < DENSITY_CAPTION['detailed'][1])

# ============================================================ [2] 平台差异的确定性单元
print('\n[2a] 平台封面策略', flush=True)
check('小红书必须有封面、抖音不强制', requires_cover('xiaohongshu') and not requires_cover('douyin'))
check('用户明确要封面时抖音也要', needs_cover('douyin', True) and needs_cover('xiaohongshu'))
check('两平台拿到的是不同的封面指令', cover_instruction('douyin') != cover_instruction('xiaohongshu'))
coverless = [{'index': 1, 'layout': 'checklist',
              'visual': {'kind': 'map', 'title': '内容', 'items': [{'label': 'a', 'detail': 'b'}]}}]
fixed = insert_cover('xiaohongshu', coverless)
check('模型漏封面时补一页封面，正文一页不少',
      [p['layout'] for p in fixed] == ['cover', 'checklist'] and [p['index'] for p in fixed] == [1, 2])
check('补出来的封面沿用模型自己写的内容，不是空壳',
      fixed[0]['visual']['kind'] == 'cover' and fixed[0]['visual']['title'] == '内容')
check('用户把页数锁死时把首页就地改成封面，不偷偷加页',
      len(insert_cover('xiaohongshu', coverless, max_pages=1)) == 1)
check('抖音没有封面诉求就不强加封面页', insert_cover('douyin', coverless) is coverless)

# ============================================================ 真实协议路径
topic = '新手怎么整理会议行动项'
material = ('会议行动项整理方法（用户提供的参考资料）：会前明确议题与负责人；'
            '会中只记录已定的结论与待办；会后当天把每条行动项写成「谁、做什么、何时完成」；'
            '下一次会议开始前逐条核对完成情况；记录要放在所有人看得到的地方，不要只存在个人笔记里。')
server, network = start_mock()
try:
    client.post('/api/v1/provider-configs', json={
        'name': '本地协议桩', 'kind': 'text', 'adapter_type': 'openai_compatible',
        'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'model_id': 'flow-stub',
        'api_key': 'local-test-key', 'enabled': True, 'allow_localhost': True})

    print('\n[1] 完整生产流程', flush=True)
    body = {'request_id': str(uuid4()), 'topic': topic,
            'requirements': '不要提付费工具，语言轻松一点', 'density': 'balanced',
            'style': 'clean', 'materials': material, 'run_mode': 'real'}
    q = client.post('/api/v1/studio/produce', headers=headers, json=body)
    check('创作任务按预期排队', q.status_code == 202)
    queued = q.json()
    cid = queued['content_id']
    check('创作阶段不再要求填页码', 'pages' not in queued['creative_brief'])
    check('没有指定页数时简报留空，交给平台默认篇幅',
          not queued['creative_brief'].get('explicit_pages'))

    run = settled(queued['run_id'], '整条生产流程跑完')

    detail = client.get('/api/v1/contents/' + cid).json()
    check('用户原题没有被改换', detail['topic'] == topic)
    rev = next(v for v in detail['revisions'] if v['revision_id'] == detail['active_revision_id'])
    check('双平台稿件都产出', {p['platform'] for p in rev['platforms']} == {'douyin', 'xiaohongshu'})
    check('每个平台都渲染出了图并落盘',
          all(p['artifacts'] and p['page_count'] == len(p['pages']) for p in rev['platforms']))
    check('每页预览图都能读回', all(
        client.get('/api/v1/platform-revisions/%s/pages/%d' % (p['platform_revision_id'], n)).status_code == 200
        for p in rev['platforms'] for n in range(1, p['page_count'] + 1)))
    check('成品默认等人确认，不自动发布',
          all(p['state'] == 'ready_for_review' for p in rev['platforms']))

    approved = client.post('/api/v1/review-decisions', json={
        'decision': 'approve', 'actor': 'coisini', 'note': '流程核验',
        'targets': [{'platform_revision_id': p['platform_revision_id'],
                     'expected_manifest_hash': p['manifest_hash']} for p in rev['platforms']]})
    check('本人确认后两平台一起批准', approved.status_code == 200 and approved.json()['ok'])
    package = client.post('/api/v1/packages', json={
        'actor': 'coisini', 'platform_revision_ids': [p['platform_revision_id'] for p in rev['platforms']]})
    check('批准之后才拿得到发布包', package.status_code == 201)

    print('\n[2] 抖音版与小红书版的差异', flush=True)
    dy = next(p for p in rev['platforms'] if p['platform'] == 'douyin')
    xhs = next(p for p in rev['platforms'] if p['platform'] == 'xiaohongshu')
    check('抖音版和小红书版不是同一份文案',
          (dy['title'], dy['caption']) != (xhs['title'], xhs['caption']))
    check('小红书版首屏是封面页', (xhs['pages'][0].get('visual') or {}).get('kind') == 'cover')
    check('两平台的页数各自遵守自己的默认篇幅',
          PAGE_STRATEGY['douyin']['min_pages'] <= dy['page_count'] <= PAGE_STRATEGY['douyin']['max_pages']
          and PAGE_STRATEGY['xiaohongshu']['min_pages'] <= xhs['page_count'] <= PAGE_STRATEGY['xiaohongshu']['max_pages'])
    check('小红书版每个平台页都有正文，不是封面吃掉唯一一页',
          all(len(p.get('body') or []) + len((p.get('visual') or {}).get('items') or []) > 0
              for p in xhs['pages']))

    print('\n[3] 成品修改：页数', flush=True)
    before = min(p['page_count'] for p in rev['platforms'])
    r1 = client.post('/api/v1/studio/contents/%s/revise' % cid, headers=headers, json={
        'request_id': str(uuid4()), 'base_revision_id': detail['active_revision_id'],
        'instruction': '增加页数，多加几页。', 'run_mode': 'real'})
    check('增加页数的改稿可以提交', r1.status_code == 202)
    req1, brief1 = queued_brief(r1.json()['run_id'])
    check('页数预算真的变大，而不是原地不动',
          brief1['explicit_pages'] and brief1['page_min'] >= PAGE_STRATEGY['douyin']['min_pages']
          and brief1['page_max'] > PAGE_STRATEGY['xiaohongshu']['max_pages'])
    check('新页数写进了创作要求',
          '全稿%d～%d页，封面计入页数。' % (brief1['page_min'], brief1['page_max']) in req1['user_requirements'])
    check('改稿要求里保留了原题', req1['topic'] == topic)
    settled(r1.json()['run_id'], '增加页数的改稿能真的跑完整条流水线')
    grown = client.get('/api/v1/contents/' + cid).json()
    rev_grown = next(v for v in grown['revisions'] if v['revision_id'] == grown['active_revision_id'])
    check('成品里的页数确实比上一版多：%d → %d' % (before, min(p['page_count'] for p in rev_grown['platforms'])),
          min(p['page_count'] for p in rev_grown['platforms']) > before)
    check('改稿产生新版本而不是覆盖旧版本', len(grown['revisions']) > len(detail['revisions']))

    print('\n[3] 成品修改：内容详细程度', flush=True)
    base = grown['active_revision_id']
    r2 = client.post('/api/v1/studio/contents/%s/revise' % cid, headers=headers, json={
        'request_id': str(uuid4()), 'base_revision_id': base,
        'instruction': '更详细，补充每项的具体理由和实用细节，保留页数、全部对象及统计口径。',
        'run_mode': 'real'})
    req2, brief2 = queued_brief(r2.json()['run_id'])
    check('更详细把发布文案上限抬到详细档', brief2['caption_max'] == DENSITY_CAPTION['detailed'][1])
    check('更详细写明了详细程度要求', '内容详细程度：详细' in req2['user_requirements'])
    mark = len(network)
    settled(r2.json()['run_id'], '更详细的改稿能跑完整条流水线')
    check('模型真的收到了更长的文案上限', max(caption_caps(network[mark:])) == DENSITY_CAPTION['detailed'][1])

    detailed = client.get('/api/v1/contents/' + cid).json()
    base2 = detailed['active_revision_id']
    r3 = client.post('/api/v1/studio/contents/%s/revise' % cid, headers=headers, json={
        'request_id': str(uuid4()), 'base_revision_id': base2,
        'instruction': '更短更精简，删除重复解释，保留全部具体对象和核心事实。', 'run_mode': 'real'})
    req3, brief3 = queued_brief(r3.json()['run_id'])
    check('更短更精简把发布文案上限压到精简档', brief3['caption_max'] == DENSITY_CAPTION['short'][1])
    check('旧的「详细」指示被替换掉，不和新要求打架',
          req3['user_requirements'].count('内容详细程度：') == 1
          and '内容详细程度：精简' in req3['user_requirements'])
    check('精简不改动已经被用户确认的页数',
          (brief3['page_min'], brief3['page_max']) == (brief2['page_min'], brief2['page_max']))
    mark = len(network)
    settled(r3.json()['run_id'], '更短更精简的改稿能跑完整条流水线')
    check('模型真的收到了更短的文案上限', max(caption_caps(network[mark:])) == DENSITY_CAPTION['short'][1])

    final = client.get('/api/v1/contents/' + cid).json()
    rev_final = next(v for v in final['revisions'] if v['revision_id'] == final['active_revision_id'])
    check('多次改稿后小红书版依然有封面',
          (next(p for p in rev_final['platforms'] if p['platform'] == 'xiaohongshu')['pages'][0]
           .get('visual') or {}).get('kind') == 'cover')
    check('改动全程没有覆盖用户原题', final['topic'] == topic)
finally:
    server.shutdown()
    server.server_close()

print(f'结果：{passed} 通过 / 0 失败')
