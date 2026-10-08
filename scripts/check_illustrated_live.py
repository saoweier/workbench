"""Read-only checks of actual C009 images and preserved historical revision."""
import hashlib
import json
from pathlib import Path
import httpx
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/test-artifacts/illustrated-content-20261003'
CID='8faa1ce5-5253-40eb-b530-0947275c3689'
checks=[]
def check(name, condition):
    checks.append({'name':name,'passed':bool(condition)})
    assert condition,name

with httpx.Client(base_url='http://127.0.0.1:8000/api/v1',timeout=30) as c:
    detail=c.get(f'/contents/{CID}').json()
    baseline=json.loads((OUT/'baseline.json').read_text(encoding='utf-8'))
    active=next(r for r in detail['revisions'] if r['revision_id']==detail['active_revision_id'])
    check('C009 最新图解版本为 v3',active['version']==3)
    check('内容已完成渲染并等待人工预览',detail['state']=='ready_for_review')
    old=next(r for r in detail['revisions'] if r['revision_id']==baseline['active_revision_id'])
    original=next(r for r in baseline['revisions'] if r['revision_id']==baseline['active_revision_id'])
    check('旧母稿、旧平台稿与旧产物记录未覆盖',old==original)
    for platform in ['douyin','xiaohongshu']:
        pr=next(p for p in active['platforms'] if p['platform']==platform)
        check(f'{platform} 六张实际图',len(pr['pages'])==len(pr['artifacts'])==6)
        kinds={p['visual']['kind'] for p in pr['pages']}
        check(f'{platform} 插画、示例与多种图解',{'cover','example'}.issubset(kinds) and len(kinds)>=4)
        check(f'{platform} 文案达到内容产品密度',500<=len(pr['caption'])<=1000)
        check(f'{platform} 文案无内部主张编号或摘要冒充原文','C01' not in pr['caption'] and '原文摘录' not in pr['caption'])
        check(f'{platform} 未代替用户审批或发布',pr['state']=='ready_for_review')
        files=ROOT/'storage/artifacts/C009'/platform/'cr3-pr1'
        for a in pr['artifacts']:
            path=files/f"page-{a['page_index']:02d}.png"
            data=path.read_bytes()
            with Image.open(path) as im:
                check(f"{platform} P{a['page_index']} 尺寸与实际文件哈希",im.size==(1080,1440) and hashlib.sha256(data).hexdigest()==a['sha256'])
            check(f"{platform} P{a['page_index']} 使用新图解模板",a['template_version'].startswith('illustrated-') and a['template_version'].endswith('@3'))
    check('双平台表达及图解安排不同',active['platforms'][0]['pages']!=active['platforms'][1]['pages'])
    pubs=c.get('/publications',params={'content_id':CID}).json()
    check('没有伪造真实发布记录',not pubs['items'])
    request=json.loads((OUT/'request.json').read_text(encoding='utf-8'))
    run=c.get(f"/runs/{request['run_id']}").json()
    check('恢复后整条任务和各阶段均成功',run['state']=='succeeded' and all(j['state']=='succeeded' for j in run['jobs']))
    new_calls=[p for p in run['provider_calls'] if p.get('stage_prompt_version','').endswith('v3-illustrated')]
    check('真实文字模型调用有日志，未冒充生图调用',len(new_calls)==4 and all(p['state']=='succeeded' for p in new_calls))
    (OUT/'content.json').write_text(json.dumps(detail,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'verification.json').write_text(json.dumps({'checks':checks,'passed':len(checks),'failed':0,
        'new_text_model_calls':4,'new_image_model_calls':0,'new_real_usage':new_calls},ensure_ascii=False,indent=2),encoding='utf-8')
print(f'{len(checks)} 通过 / 0 失败')
