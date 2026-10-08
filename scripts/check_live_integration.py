"""Validate saved real test, PNGs, and the honest pre-publication state.

No model calls. Creates at most one empty-data review record via the local API.
"""
from pathlib import Path
from io import BytesIO
import csv
import hashlib
import json
import sys
import httpx
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/test-artifacts/live-integration-20261003'
sys.path.insert(0,str(ROOT/'backend'))
from app.worker import build_session_factory
from app.services.pipeline import PipelineService
from app.models.entities import ContentItem

def save(name,data):
    (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def main():
    state=json.loads((OUT/'request.json').read_text(encoding='utf-8'))
    cid=state['content_id']
    # Repair the aggregate state of this already-rendered record after the
    # pipeline state propagation fix. Does not approve, publish, or render.
    settings,sf=build_session_factory()
    pipeline=PipelineService(sf)
    with sf() as s:
        pipeline._recompute_content_state(s,s.get(ContentItem,cid));s.commit()
    checks=[]
    def check(label,condition):
        assert condition,label
        checks.append(label)
    with httpx.Client(base_url='http://127.0.0.1:8000/api/v1',timeout=30) as client:
        def get(path):
            r=client.get(path);r.raise_for_status();return r.json()
        run=get(f"/runs/{state['run_id']}")
        content=get(f'/contents/{cid}')
        calls=get(f'/contents/{cid}/provider-calls')
        save('run.json',run);save('content.json',content)
        save('platforms.json',get(f'/contents/{cid}/platform-revisions'))
        check('真实任务所有阶段成功',run['state']=='succeeded' and all(j['state']=='succeeded' for j in run['jobs']))
        check('内容进入待预览',content['state']=='ready_for_review' and content['run_mode']=='real')
        result=next(j['output_refs'] for j in run['jobs'] if j['stage']=='batch_dispatch')
        topic=result['stages']['topic']
        check('产生三个候选并选择一个',topic['candidate_count']==3 and len(topic['selected'])==1)
        check('入选题目写入真实内容',content['topic']==topic['selected'][0]['topic'])
        check('恢复未重复调用选题和母稿',sum(c['stage_prompt_version']=='topic.propose.v2' for c in calls['items'])==1
            and sum(c['stage_prompt_version']=='compose.master.v1' for c in calls['items'])==1)
        check('所有模型调用均真实且成功',len(calls['items'])==8 and all(c['run_mode']=='real' and c['state']=='succeeded'
            and c['model_id']=='deepseek-flash' for c in calls['items']))
        check('未配置费率时金额保持未知',all(c['reported_micro'] is None for c in calls['items']))
        rev=next(r for r in content['revisions'] if r['revision_id']==content['active_revision_id'])
        prs=rev['platforms']
        check('双平台分别形成待预览稿',{p['platform'] for p in prs}=={'douyin','xiaohongshu'}
            and all(p['state']=='ready_for_review' and p['page_count']==5 for p in prs))
        for pr in prs:
            for artifact in pr['artifacts']:
                response=client.get(f"/platform-revisions/{pr['platform_revision_id']}/pages/{artifact['page_index']}")
                response.raise_for_status()
                raw=response.content
                im=Image.open(BytesIO(raw));im.verify()
                check(f"{pr['platform']} 第 {artifact['page_index']} 页 PNG 可读且指纹匹配",
                    im.format=='PNG' and im.size==(1080,1440) and hashlib.sha256(raw).hexdigest()==artifact['sha256'])
        pubs=get(f'/publications?content_id={cid}')
        summary=get(f'/publications/summary?content_id={cid}')
        save('publication-state.json',summary)
        check('没有虚构发布记录',not pubs['items'])
        reports=get(f'/contents/{cid}/reviews')
        existing=reports.get('items') or reports.get('reports') or []
        if existing:
            review=get(f"/reviews/{existing[-1]['id']}")
        else:
            response=client.post(f'/contents/{cid}/reviews',json={'run_mode':'real',
                'hypotheses':[],'experiments':[],'next_topics':[]})
            response.raise_for_status();review=response.json()
        save('pre-publication-review.json',review)
        check('无数据复盘明确标记 none',review['data_sufficiency']=='none')
        check('无数据时没有效果假设或实验结论',not review['hypotheses'] and not review['experiments'])
        check('无数据时不生成虚假数字指标',not any(o['kind']=='metric' for o in review['observations']))
        fields=get('/imports')['capabilities']['metrics_template']
        with (OUT/'metrics-template.csv').open('w',encoding='utf-8-sig',newline='') as f:
            csv.writer(f).writerow(fields)
        plan={'content_id':cid,'status':'awaiting_publication',
            'windows_hours':[24,72,168],'published_at':None,
            'platforms':[{'platform':p['platform'],'platform_revision_id':p['platform_revision_id'],
                         'title':p['title'],'link':None,'post_id':None,'metrics':None} for p in prs],
            'note':'先人工预览批准、导出并发布，再登记实际链接与时间。24/72/168 小时从实际发布时间算起；未知指标留空。'}
        save('revisit-plan.json',plan)
        save('verification.json',{'passed':len(checks),'failed':0,'checks':checks,
            'content_id':cid,'run_id':run['id'],'topic':content['topic'],
            'real_calls':len(calls['items']),
            'input_tokens':sum(c['input_tokens'] for c in calls['items']),
            'output_tokens':sum(c['output_tokens'] for c in calls['items']),
            'review_report_id':review['id'],'actual_publication_pending':True})
        print(json.dumps({'passed':len(checks),'failed':0,'content_id':cid,
            'topic':content['topic'],'review_report_id':review['id']},ensure_ascii=False))

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    main()
