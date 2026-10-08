"""Isolated publication contracts; no real account, model or external publish."""
from pathlib import Path
import os
import shutil
import sys
import tempfile
import time
from datetime import timedelta
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-douyin-test-'))
shutil.copytree(ROOT/'examples/demo/storage/artifacts',tmp/'artifacts')
shutil.copy2(ROOT/'examples/demo/storage/workbench.db',tmp/'test.db')
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",
    CWB_ARTIFACT_DIR=str(tmp/'artifacts'),CWB_TMP_DIR=str(tmp/'tmp'),CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'))
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app.api.douyin_publishing import service
from app.models.entities import ContentItem,PlatformRevision,Publication,DouyinPublishTask,ReviewDecision,_now
from app.services.platform_account import write_json
from app.core.errors import StateConflict,ValidationFailed
from app.services.douyin_observer import parse_metrics,POST_LINK
from app.services.douyin_editor import normalize
from app.services.publishing_preferences import save_preferences
save_preferences(service.settings,'assisted',True)

passed=0
def check(label,value):
    global passed
    assert value,label
    passed+=1
    print('PASS '+label)
def blocked(label,fn):
    try: fn()
    except (StateConflict,ValidationFailed,ValueError): check(label,True)
    else: check(label,False)

client=TestClient(app)
headers={'X-CWB-Local-Action':'account-connection','Origin':'http://testserver'}
with service.sf() as s:
    revisions=list(s.scalars(select(PlatformRevision).where(PlatformRevision.platform=='douyin')))
    pr_id=revisions[0].id
    original_publications=s.scalar(select(func.count()).select_from(Publication))
blocked('identity required to plan',lambda:service.plan(pr_id))
write_json(service.root/'identity.json',{'account_id':'test-account','nickname':'Test'})
blocked('demo never uploaded as real content',lambda:service.plan(pr_id))
with service.sf() as s:
    pr=s.get(PlatformRevision,pr_id)
    s.get(ContentItem,pr.content_revision.content_id).run_mode='real'
    s.commit()
task=service.plan(pr_id)
check('plan does not approve or submit',task['state']=='planned' and task['confirmed_at'] is None)
check('plan is idempotent',service.plan(pr_id)['id']==task['id'])
check('public task excludes private storage paths','storage_key' not in str(task))
with service.sf() as s:
    check('planning creates no publication',s.scalar(select(func.count()).select_from(Publication))==original_publications)
blocked('upload requires active login',lambda:service.upload(task['id'],task['payload_hash']))
write_json(service.root/'status.json',{'state':'connected','browser_open':True,'heartbeat':time.time()})
blocked('upload binds exact payload',lambda:service.upload(task['id'],'0'*64))
blocked('cannot confirm before editor preparation',lambda:service.confirm(task['id'],task['payload_hash'],'test-account',True))
check('upload only queues draft',service.upload(task['id'],task['payload_hash'])['state']=='upload_requested')
check('duplicate upload is idempotent',service.upload(task['id'],task['payload_hash'])['state']=='upload_requested')
check('read-only worker cannot claim uploads',service.claim(read_only=True) is None)
check('upload claimed once',service.claim()==('upload',task['id']) and service.claim() is None)
service.finish(task['id'],'awaiting_editor')
check('awaiting editor double upload does not restart',service.upload(task['id'],task['payload_hash'])['state']=='awaiting_editor')
service.finish(task['id'],'awaiting_confirmation')
blocked('explicit true required',lambda:service.confirm(task['id'],task['payload_hash'],'test-account',False))
blocked('account mismatch blocked',lambda:service.confirm(task['id'],task['payload_hash'],'wrong',True))
blocked('changed hash blocked',lambda:service.confirm(task['id'],'0'*64,'test-account',True))
response=client.post(f"/api/v1/douyin/tasks/{task['id']}/confirm",headers=headers,
    json={'expected_payload_hash':task['payload_hash'],'account_id':'test-account','confirmed':'true'})
check('API rejects string boolean',response.status_code==422)
check('foreign origin cannot publish',client.post(f"/api/v1/douyin/tasks/{task['id']}/confirm",
    headers={**headers,'Origin':'https://evil.invalid'},json={}).status_code==403)
check('missing local action cannot publish',client.post(f"/api/v1/douyin/tasks/{task['id']}/confirm",json={}).status_code==403)
check('confirmed task queues publication',service.confirm(task['id'],task['payload_hash'],'test-account',True)['state']=='publish_requested')
check('read-only worker cannot submit',service.claim(read_only=True) is None)
check('submit claimed exactly once',service.claim()==('publish',task['id']) and service.claim() is None)
service.require_publish_approval(task['id'])
check('approval bound to manifest',True)
with service.sf() as s:
    latest=s.scalar(select(ReviewDecision).where(ReviewDecision.platform_revision_id==pr_id).order_by(ReviewDecision.decided_at.desc()))
    latest.decision='reject';s.commit()
blocked('revoked review blocks click',lambda:service.require_publish_approval(task['id']))
with service.sf() as s:
    latest=s.scalar(select(ReviewDecision).where(ReviewDecision.platform_revision_id==pr_id).order_by(ReviewDecision.decided_at.desc()))
    latest.decision='approve';s.commit()
service.finish(task['id'],'unknown',next_seconds=1)
check('uncertain confirm never resubmits',service.confirm(task['id'],task['payload_hash'],'test-account',True)['state']=='unknown')
blocked('uncertain state cannot click publish',lambda:service.require_publish_approval(task['id']))
blocked('uncertain state cannot upload again',lambda:service.upload(task['id'],task['payload_hash']))
with service.sf() as s:
    s.get(DouyinPublishTask,task['id']).next_observation_at=_now()-timedelta(seconds=1);s.commit()
check('read-only worker claims revisit only',service.claim(read_only=True)==('observe',task['id']))
check('revisit retains crash recovery deadline',service.get(task['id'])['next_observation_at'] is not None)
evidence={'title':task['title'],'account_id':'test-account','post_id':'7123456789012345678',
    'link':'https://www.douyin.com/note/7123456789012345678','audit_state':'审核中'}
blocked('missing platform identifier cannot register',lambda:service.record_observed_publication(task['id'],{**evidence,'post_id':''}))
blocked('title mismatch cannot register',lambda:service.record_observed_publication(task['id'],{**evidence,'title':'different'}))
blocked('account mismatch cannot register',lambda:service.record_observed_publication(task['id'],{**evidence,'account_id':'wrong'}))
pub_id=service.record_observed_publication(task['id'],evidence)
check('same observed publication idempotent',service.record_observed_publication(task['id'],evidence)==pub_id)
blocked('different post cannot overwrite registration',lambda:service.record_observed_publication(task['id'],{**evidence,'post_id':'7123456789012345679'}))
with service.sf() as s:
    pub=s.get(Publication,pub_id)
    check('actual observation registration has provenance',pub.declaration_source=='browser_observation' and pub.verified_by=='browser')
    check('click time never invented as publish time',pub.published_at is None)
service.finish(task['id'],'submitting')
service.recover()
check('crash after submit becomes unknown',service.get(task['id'])['state']=='unknown')
check('crash never queues another publish',service.claim() is None)
check('missing counts remain absent',parse_metrics('待更新')=={})
missing=parse_metrics('播放量 --\n点赞数 暂无数据')
check('platform placeholders never become zero',missing['views']['value'] is None and missing['likes']['value'] is None)
actual=parse_metrics('播放量 1.2万\n点赞数 0\n评论 23')
check('actual zero preserved distinctly',actual['likes']['value']==0 and actual['views']['value']==12000 and actual['comments']['value']==23)
check('unlabeled aggregate never guessed',parse_metrics('12500\n热门\n1.2万')=={})
check('ambiguous duplicate metric rejected','views' not in parse_metrics('播放量 12\n播放量 34'))
check('public links require exact Douyin host',POST_LINK.fullmatch(evidence['link']) and not POST_LINK.fullmatch('https://www.douyin.com.evil.invalid/note/7123456789012345678'))
check('editor normalization retains all text',normalize('一\n\n二\u200b')==normalize('一\n二') and normalize('一二')!=normalize('一\n二'))
# Byte changes must fail before a queued upload can be claimed.
with service.sf() as s:
    row=s.get(DouyinPublishTask,task['id']);row.state='upload_requested'
    image=row.payload_json['images'][0];s.commit()
path=tmp/'artifacts'/image['storage_key'];original=path.read_bytes();path.write_bytes(b'changed')
check('changed queued bytes are stopped',service.claim() is None and service.get(task['id'])['state']=='stale')
path.write_bytes(original)
check('historical revisit still accessible after content changes',service.execution(task['id'],historical=True)['task_id']==task['id'])
# Actual local Chromium editor interactions, fully intercepted offline.
from playwright.sync_api import sync_playwright
from app.services.renderer import PlaywrightRenderer
from app.services.douyin_editor import complete_prepare
from app.services.douyin_submit import publish
from app.services.douyin_scheduler import dispatch_due
with service.sf() as s:
    row=s.get(DouyinPublishTask,task['id']);row.state='awaiting_editor';row.next_observation_at=None;s.commit()
proof=service.root/'tasks'/task['id'];proof.mkdir(parents=True,exist_ok=True)
html='''<meta charset="utf-8"><body><p>已添加 %d 张图片</p><input placeholder="添加作品标题" />
<div contenteditable="true"></div><button id="declaration" onclick="document.getElementById('modal').hidden=false">请选择自主声明</button>
<div id="modal" hidden><p>请选择声明类型（单选）</p><label><input type="radio" onclick="document.getElementById('ok').disabled=false">内容由AI生成</label>
<button id="ok" disabled onclick="document.getElementById('declaration').textContent='内容由AI生成';document.getElementById('modal').hidden=true">确定</button></div>
<label><input type="checkbox" checked>公开</label><label><input type="checkbox" checked>立即发布</label>
<button onclick="document.getElementById('clicks').textContent=Number(document.getElementById('clicks').textContent)+1">发布</button><output id="clicks">0</output></body>'''%len(task['images'])
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path=PlaywrightRenderer.resolve_executable())
    context=browser.new_context()
    context.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html; charset=utf-8',body=html))
    page=context.new_page();page.goto('https://creator.douyin.com/creator-micro/content/post/image')
    page.get_by_placeholder('添加作品标题').fill(task['title']);page.locator('[contenteditable=true]').fill(task['caption'])
    complete_prepare(page,service.root,task['id'])
    check('browser editor selects truthful AI declaration',page.locator('#declaration').inner_text()=='内容由AI生成')
    check('editor preparation does not click publish',page.locator('#clicks').inner_text()=='0' and service.get(task['id'])['state']=='awaiting_confirmation')
    blocked('browser submit blocked before user confirmation',lambda:publish(page,service.root,service,service.execution(task['id'])))
    service.confirm(task['id'],task['payload_hash'],'test-account',True);service.claim()
    with patch('app.services.douyin_submit.verify_account',return_value={'account_id':'test-account'}):
        publish(page,service.root,service,service.execution(task['id']))
    check('confirmed browser submission clicks exactly once',page.locator('#clicks').inner_text()=='1')
    check('click alone never proves publication success',service.get(task['id'])['state']=='verifying')
    blocked('second browser submit prohibited',lambda:publish(page,service.root,service,service.execution(task['id'])))
    check('duplicate submit never clicks twice',page.locator('#clicks').inner_text()=='1')
    service.finish(task['id'],'submitting')
    page.get_by_label('公开',exact=True).uncheck()
    blocked('changed audience prevents actual submit',lambda:publish(page,service.root,service,service.execution(task['id'])))
    check('audience mismatch never clicks again',page.locator('#clicks').inner_text()=='1')
    browser.close()
service.finish(task['id'],'unknown')
write_json(service.root/'status.json',{'state':'saved','browser_open':False,'last_verified_at':_now().isoformat()})
with service.sf() as s:
    s.get(DouyinPublishTask,task['id']).next_observation_at=_now()-timedelta(seconds=1);s.commit()
with patch('app.services.douyin_scheduler.subprocess.Popen') as launch:
    check('due revisit dispatches saved login worker',dispatch_due(service.sf,service.settings))
    check('background worker is strictly read-only','--read-only' in launch.call_args.args[0])
    check('scheduler prevents rapid retries',not dispatch_due(service.sf,service.settings) and launch.call_count==1)
write_json(service.root/'scheduler.json',{})
with service.sf() as s:
    s.get(DouyinPublishTask,task['id']).state='awaiting_confirmation';s.commit()
with patch('app.services.douyin_scheduler.subprocess.Popen') as launch:
    check('unfinished draft prevents background profile use',not dispatch_due(service.sf,service.settings) and not launch.called)
service.engine.dispose()
print(f'结果：{passed} 通过 / 0 失败')
