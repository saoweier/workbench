"""Read-only checks of a real direct draft; never generate, approve or publish."""
import argparse
import hashlib
import io
import json
import sqlite3
import urllib.request
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--content', required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--min-versions',type=int,default=2)
    parser.add_argument('--base', default='http://127.0.0.1:8000')
    args = parser.parse_args()
    checks = []

    def get(path):
        with urllib.request.urlopen(args.base + '/api/v1/' + path, timeout=15) as response:
            return response.read()

    def data(path):
        return json.loads(get(path))

    def check(name, value):
        checks.append({'name': name, 'passed': bool(value)})
        assert value, name

    health = data('health')
    run = data('runs/' + args.run)
    detail = data('contents/' + args.content)
    version = next(v for v in detail['revisions'] if v['revision_id'] == detail['active_revision_id'])
    brief = version['brief']['creative_brief']
    check('API与后台任务就绪', health['api']['status'] == 'ok' and health['worker']['status'] == 'running')
    check('真实任务全部阶段完成', run['state'] == 'succeeded' and all(j['state'] == 'succeeded' for j in run['jobs']))
    audit = next(j['output_refs']['report'] for j in run['jobs'] if j['stage'] == 'audit')
    check('模型语义审核没有阻断项', audit['passed'] and not any(i['severity'] == 'error' for i in audit['issues']))
    check('原题及TOP10约束保留', detail['topic'] == brief['original_topic'] and brief['form'] == 'ranking' and brief['rank_count'] == 10)
    check('实际调整保存新旧版本', len(detail['revisions']) >= args.min_versions and version['version'] >= args.min_versions)
    check('新版等待人工批准', detail['state'] == 'ready_for_review' and all(p['state'] == 'ready_for_review' and not p['reviews'] for p in version['platforms']))
    expected = ['知乎热榜', '微博热搜', 'B站热搜', '掘金热榜', 'IT之家热榜', '少数派', 'NodeSeek', '豆瓣热映', '豆瓣新书', '澎湃新闻']
    for platform in version['platforms']:
        name = platform['platform']
        items = [i for page in platform['pages'] if page.get('visual', {}).get('kind') == 'rank' for i in page['visual']['items']]
        check(name + '完整TOP10与原名次', [i['rank'] for i in items] == list(range(1, 11)) and [i['label'] for i in items] == expected)
        check(name + '页数遵守原要求', brief['page_min'] <= platform['page_count'] <= brief['page_max'])
        check(name + '文案逐字符遵守字数要求', (brief['caption_min'] or 1) <= len(platform['caption']) <= brief['caption_max'])
        public = json.dumps({k: platform[k] for k in ('title', 'caption', 'pages')}, ensure_ascii=False)
        check(name + '公开稿无内部测试标签', '功能实测' not in public and '功能测试' not in public and 'sources.py' not in public)
        for artifact in platform['artifacts']:
            image_data = get('artifacts/' + artifact['id'] + '/raw')
            image = Image.open(io.BytesIO(image_data))
            check(name + '页图可读取且校验值一致', image.format == 'PNG' and image.size == (artifact['width'], artifact['height']) and hashlib.sha256(image_data).hexdigest() == artifact['sha256'])

    # Verify only this explicitly identified test content; never inspect credentials/accounts.
    database = ROOT / 'storage/cwb.db'
    with sqlite3.connect('file:' + database.as_posix() + '?mode=ro', uri=True) as db:
        published = db.execute('SELECT count(*) FROM publication p JOIN platform_revision pr ON pr.id=p.platform_revision_id JOIN content_revision r ON r.id=pr.content_revision_id WHERE r.content_id=?', (args.content,)).fetchone()[0]
    check('测试内容未正式发布', published == 0)
    boards = data('studio/trends')
    check('全部13个HotPush来源可见', len(boards['sources']) >= 13)
    calls_after = data('contents/' + args.content + '/provider-calls')['items']
    check('只读检查没有新增模型调用', len(calls_after) == len(run['provider_calls']))
    result = {
        'passed': len(checks), 'failed': 0, 'checks': checks,
        'content_id': args.content, 'run_id': args.run, 'revision': version['version'],
        'platforms': [{'platform': p['platform'], 'pages': p['page_count'], 'caption_chars': len(p['caption'])} for p in version['platforms']],
        'model_calls_total': len(calls_after), 'cost': run['cost'], 'audit': audit,
        'hotpush': {'sources': len(boards['sources']), 'ready': sum(s['state'] == 'ready' for s in boards['sources']), 'items': sum(len(s['items']) for s in boards['sources']), 'refreshing': boards['refreshing']},
        'scope': '仅核验本次真实生成与改稿，无人工批准或平台发布。名次为编辑顺序，非真实热度榜。',
    }
    out = ROOT / 'docs/test-artifacts/direct-top10-real-verification.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ['passed', 'failed', 'revision', 'platforms', 'model_calls_total', 'hotpush']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
