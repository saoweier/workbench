"""Isolated video, publishing choices and manual revisit contracts; no real posting."""
from pathlib import Path
import os,sys,shutil,tempfile,json,wave,subprocess,time,threading
from uuid import uuid4
from datetime import timedelta
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-studio-'))
shutil.copytree(ROOT/'examples/demo/storage/artifacts',tmp/'artifacts')
shutil.copy2(ROOT/'examples/demo/storage/workbench.db',tmp/'test.db')
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",CWB_ARTIFACT_DIR=str(tmp/'artifacts'),CWB_TMP_DIR=str(tmp/'tmp'),CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'))
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app.api.videos import service,settings,presenters
from PIL import Image
from io import BytesIO
from app.api.douyin_publishing import service as publishing
from app.models.entities import PlatformRevision,ContentItem,VideoTask,ProviderCallRow,DouyinPublishTask,_now
from app.services.video_speech import SpeechUncertain,ffmpeg,command
from app.services.publishing_preferences import preferences,save_preferences
from app.services.platform_account import write_json
from app.services.douyin_scheduler import dispatch_due
from app.services.network_policy import validate_url

passed=0
def check(label,value):
    global passed
    assert value,label
    passed+=1;print('PASS '+label,flush=True)
client=TestClient(app);headers={'X-CWB-Local-Action':'account-connection','Origin':'http://testserver'}
def post(url,data):return client.post('/api/v1'+url,json=data,headers=headers)
with service.sf() as session:
    pr=session.scalar(select(PlatformRevision).where(PlatformRevision.platform=='douyin'));pr_id=pr.id
    cid=pr.content_revision.content_id
source=client.get('/api/v1/videos/source/'+pr_id).json()
check('rendered pages have editable default narration',len(source['pages'])>1 and all(p['script'] for p in source['pages']))
check('complete narration includes diagram details',all(p['full_script'] and p['full_script'].startswith(p['heading']) for p in source['pages']))
check('source response excludes internal storage paths','storage_key' not in json.dumps(source))
payload=dict(platform_revision_id=pr_id,expected_manifest_hash=source['manifest_hash'],scripts=['讲解测试。']*len(source['pages']),engine='local',voice='xiaoxiao',rate=0,presenter='guide',request_key=str(uuid4()),cloud_consent=False)
check('cross-origin mutation blocked',client.post('/api/v1/videos',json=payload,headers={**headers,'Origin':'https://untrusted.example'}).status_code==403)
check('missing action header blocked',client.post('/api/v1/videos',json=payload).status_code==403)
check('online narration needs explicit consent',post('/videos',{**payload,'engine':'edge'}).status_code==422)
check('string consent is not approval',post('/videos',{**payload,'cloud_consent':'true'}).status_code==422)
check('stale manifest blocked',post('/videos',{**payload,'expected_manifest_hash':'0'*64}).status_code==409)
check('script/page mismatch blocked',post('/videos',{**payload,'scripts':['一页']}).status_code==422)
check('empty narration blocked',post('/videos',{**payload,'scripts':['']*len(source['pages'])}).status_code==422)
task=post('/videos',payload).json();tid=task['id']
check('creating task does not call speech',task['state']=='queued')
check('double click is idempotent',post('/videos',payload).json()['id']==tid)
check('same key cannot change speech',post('/videos',{**payload,'rate':15}).status_code==409)
check('unfinished video not downloadable',client.get(f'/api/v1/videos/{tid}/media/video.mp4').status_code==404)
check('queued task can cancel',post('/videos/'+tid+'/cancel',{}).json()['state']=='cancelled')
check('cancelled task never runs',service.process_next() is False)
task=post('/videos',{**payload,'request_key':str(uuid4())}).json();tid=task['id']
def tone(text,path,*args):
    with wave.open(str(path),'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(24000);output.writeframes(b'\0\0'*12000)
check('actual encoder processes a task',service.process_next(speech=tone))
result=service.get(tid)
check('MP4 render succeeds',result['state']=='succeeded')
check('vertical video includes original presenter',result['result']['width']==720 and result['result']['height']==1280 and result['result']['presenter_kind']=='animated_original')
check('caption timing is honestly marked estimated',result['result']['caption_timing']=='estimated_by_paragraph')
for name in ['video.mp4','narration.wav','subtitles.srt','manifest.json']:
    response=client.get(f'/api/v1/videos/{tid}/media/{name}?download=true')
    check('download '+name,response.status_code==200 and len(response.content)>20 and 'attachment' in response.headers['content-disposition'])
check('private encoder log not exposed',client.get(f'/api/v1/videos/{tid}/media/encoder.log').status_code==404)
check('path traversal not exposed',client.get(f'/api/v1/videos/{tid}/media/%2e%2e%2fsecrets.json').status_code==404)
movie=service.root/tid/'video.mp4'
decoded=command([ffmpeg(),'-i',str(movie),'-f','null','-'])
check('MP4 video and audio decode without errors',decoded.returncode==0 and b'Video: h264' in decoded.stderr and b'Audio: aac' in decoded.stderr)
with wave.open(str(service.root/tid/'narration.wav'),'rb') as clip:
    check('audio duration matches video',abs(clip.getnframes()/clip.getframerate()-result['result']['duration_seconds'])<.02)
without=post('/videos',{**payload,'presenter':'none','request_key':str(uuid4())}).json()
service.process_next(speech=tone)
check('presenter can be omitted from actual render',service.get(without['id'])['result']['presenter_kind']=='none')

# Images must survive upload, task snapshotting and real MP4 encoding.
def upload_image(data,name='角色.png',extra_headers=None):
    return client.post('/api/v1/video-presenters',params={'name':name},content=data,headers=extra_headers or headers)
check('project example appears in local presenter library',any(p['name']=='img_anon_1.png' for p in client.get('/api/v1/video-presenters').json()['items']))
test_image=Image.new('RGBA',(120,240),(0,0,0,0))
from PIL import ImageDraw
ImageDraw.Draw(test_image).rectangle((20,10,99,229),fill=(231,39,155,255))
image_bytes=BytesIO();test_image.save(image_bytes,format='PNG');image_bytes=image_bytes.getvalue()
uploaded=upload_image(image_bytes).json();image_id=uploaded['id']
check('transparent image upload strips empty margin',uploaded['width']==80 and uploaded['height']==220)
check('same image reupload reuses immutable asset',upload_image(image_bytes,'另一个名字.png').json()['id']==image_id)
check('presenter image download preserves alpha',Image.open(BytesIO(client.get(uploaded['image_url']).content)).mode=='RGBA')
check('invalid image rejected',upload_image(b'not an image').status_code==422)
empty_image=BytesIO();Image.new('RGBA',(64,64)).save(empty_image,format='PNG')
check('fully transparent image rejected',upload_image(empty_image.getvalue()).status_code==422)
wide_image=BytesIO();Image.new('RGB',(4097,32)).save(wide_image,format='PNG')
check('oversized image dimensions rejected',upload_image(wide_image.getvalue()).status_code==422)
jpeg_image=BytesIO();Image.new('RGB',(64,64),'navy').save(jpeg_image,format='JPEG')
check('JPEG supported as presenter upload',upload_image(jpeg_image.getvalue(),'肖像.jpg').status_code==200)
check('oversized upload rejected',upload_image(b'x'*(10*1024*1024+1)).status_code==413)
check('cross-origin image upload rejected',upload_image(image_bytes,extra_headers={**headers,'Origin':'https://untrusted.example'}).status_code==403)
check('custom presenter requires an image',post('/videos',{**payload,'presenter':'custom','request_key':str(uuid4())}).status_code==422)
check('missing presenter asset rejected',post('/videos',{**payload,'presenter':'custom','presenter_image_id':'f'*64,'request_key':str(uuid4())}).status_code==404)
custom_payload={**payload,'presenter':'custom','presenter_image_id':image_id,'request_key':str(uuid4())}
custom_task=post('/videos',custom_payload).json()
check('selected asset persisted in task',custom_task['presenter_image_id']==image_id)
check('custom task repeated submission is idempotent',post('/videos',custom_payload).json()['id']==custom_task['id'])
other_id=client.get('/api/v1/video-presenters').json()['items'][0]['id']
check('same submission cannot change presenter image',post('/videos',{**custom_payload,'presenter_image_id':other_id}).status_code==409)
service.process_next(speech=tone)
custom_result=service.get(custom_task['id'])
check('real custom presenter video succeeds',custom_result['state']=='succeeded' and custom_result['result']['presenter_kind']=='custom_image')
custom_movie=service.root/custom_task['id']/'video.mp4'
frame=command([ffmpeg(),'-i',str(custom_movie),'-frames:v','1','-f','image2pipe','-vcodec','png','-']).stdout
with Image.open(BytesIO(frame)) as actual_frame:
    color=actual_frame.getpixel((612,1100))
    check('uploaded character pixels appear in exported MP4',color[0]>190 and color[1]<80 and color[2]>110)
blocked=post('/videos',{**custom_payload,'request_key':str(uuid4())}).json()
asset_path=presenters.path(image_id);saved_bytes=asset_path.read_bytes();asset_path.write_bytes(b'changed')
speech_calls=[]
def no_speech(*args):speech_calls.append(1);raise AssertionError('Changed assets must fail before narration')
service.process_next(speech=no_speech)
check('changed presenter stops before speech',service.get(blocked['id'])['state']=='failed' and not speech_calls)
asset_path.write_bytes(saved_bytes)
check('task response hides presenter storage paths','presenter_image_path' not in json.dumps(custom_result))
cancelled=post('/videos',{**payload,'request_key':str(uuid4())}).json()
def cancelling(text,path,*args):
    service.cancel(cancelled['id']);tone(text,path)
service.process_next(speech=cancelling)
check('running task cancellation is retained',service.get(cancelled['id'])['state']=='cancelled')
check('cancelled output cannot download',client.get('/api/v1/videos/'+cancelled['id']+'/media/video.mp4').status_code==404)
unknown=post('/videos',{**payload,'engine':'edge','cloud_consent':True,'request_key':str(uuid4())}).json()
calls=[]
def uncertain(*args):calls.append(1);raise SpeechUncertain('测试不确定响应')
service.process_next(speech=uncertain)
check('uncertain speech remains uncertain',service.get(unknown['id'])['state']=='unknown')
check('uncertain speech never automatically retries',service.process_next(speech=uncertain) is False and len(calls)==1)
with service.sf() as session:
    ledger=session.scalar(select(ProviderCallRow).where(ProviderCallRow.request_key==f"video:{unknown['id']}:page:1"))
    check('uncertain external call is recorded without invented cost',ledger.state=='unknown' and ledger.reported_micro is None and ledger.estimated_micro is None)

# Exercise a real HTTP speech request against an isolated compatible mock.
requests=[]
mp3=tmp/'voice.mp3';command([ffmpeg(),'-y','-i',str(service.root/tid/'narration.wav'),'-c:a','libmp3lame',str(mp3)])
class SpeechHandler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        requests.append((self.path,data,self.headers.get('Authorization')))
        self.send_response(200);self.send_header('Content-Type','audio/mpeg');self.end_headers();self.wfile.write(mp3.read_bytes())
server=ThreadingHTTPServer(('127.0.0.1',0),SpeechHandler);threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}/v1'
cfg=post('/video-speech/config',dict(base_url=base,model_id='test-speech',api_key='test-secret-only',allow_localhost=True)).json()['config']
check('saving voice config makes no HTTP calls',not requests)
check('voice config never returns secret','test-secret-only' not in json.dumps(cfg) and cfg['secret_configured'])
check('local endpoint must be explicitly allowed',post('/video-speech/config',dict(base_url=base,model_id='test',api_key='x')).status_code==422)
api_task=post('/videos',{**payload,'engine':'api','voice':'alloy','cloud_consent':True,'config_id':cfg['id'],'request_key':str(uuid4())}).json()
def minimal_render(payload,images,audio,directory,progress):
    shutil.copy2(movie,directory/'video.mp4');return {'duration_seconds':3,'width':720,'height':1280,'presenter_kind':'animated_original'}
service.process_next(renderer=minimal_render)
check('configured API performs per-page speech calls',len(requests)==len(source['pages']) and all(r[0]=='/v1/audio/speech' for r in requests))
check('compatible voice request has expected contract',requests[0][1]['model']=='test-speech' and requests[0][1]['voice']=='alloy' and requests[0][2]=='Bearer test-secret-only')
check('successful voice API task completes',service.get(api_task['id'])['state']=='succeeded')
check('API task never exposes credentials','test-secret-only' not in json.dumps(service.get(api_task['id'])))
changed=post('/videos',{**payload,'engine':'api','voice':'alloy','cloud_consent':True,'config_id':cfg['id'],'request_key':str(uuid4())}).json()
post('/video-speech/config',dict(base_url=base,model_id='changed-speech',allow_localhost=True))
call_count=len(requests);service.process_next(renderer=minimal_render)
check('queued voice configuration changes stop before sending',service.get(changed['id'])['state']=='failed' and len(requests)==call_count)
with patch('app.services.network_policy.socket.getaddrinfo',return_value=[(2,1,6,'',('93.184.216.34',80))]):
    try:validate_url('http://public.example/v1',allow_localhost=True)
    except ValueError:blocked_http=True
    else:blocked_http=False
check('localhost permission does not allow plain HTTP remote secrets',blocked_http)
server.shutdown();server.server_close()

check('publication mode defaults to manual',preferences(settings)=={'mode':'manual','background_revisit':False})
check('preference string flag rejected',post('/publishing/preferences',{'mode':'manual','background_revisit':'true'}).status_code==422)
write_json(publishing.root/'identity.json',{'account_id':'test-user','nickname':'测试'})
write_json(publishing.root/'status.json',{'state':'connected','browser_open':True,'heartbeat':time.time(),'last_verified_at':_now().isoformat()})
with service.sf() as session:session.get(ContentItem,cid).run_mode='real';session.commit()
draft=publishing.plan(pr_id)
check('manual mode rejects assisted upload',post('/douyin/tasks/'+draft['id']+'/upload',{'expected_payload_hash':draft['payload_hash']}).status_code==409)
with service.sf() as session:
    row=session.get(DouyinPublishTask,draft['id']);row.state='publish_requested';row.next_observation_at=None;session.commit()
check('manual mode pauses queued browser submission',publishing.claim() is None)
check('manual mode rejects assisted confirmation',post('/douyin/tasks/'+draft['id']+'/confirm',{'expected_payload_hash':draft['payload_hash'],'account_id':'test-user','confirmed':True}).status_code==409)
with service.sf() as session:
    row=session.get(DouyinPublishTask,draft['id']);row.state='unknown';row.next_observation_at=_now()-timedelta(seconds=1);session.commit()
check('background revisit is disabled by default',publishing.claim(read_only=True) is None)
write_json(publishing.root/'status.json',{'state':'saved','browser_open':False,'last_verified_at':_now().isoformat()})
with patch('app.services.douyin_scheduler.subprocess.Popen') as launch:
    check('default worker does not start platform browser',not dispatch_due(publishing.sf,settings) and not launch.called)
publishing.request_observation(draft['id'])
with patch('app.services.douyin_scheduler.subprocess.Popen') as launch:
    check('user requested revisit works without recurring permission',dispatch_due(publishing.sf,settings) and '--read-only' in launch.call_args.args[0])
check('explicit one-time revisit may be claimed',publishing.claim(read_only=True)==('observe',draft['id']))
check('one-time revisit permission is consumed',publishing.claim(read_only=True) is None)
save_preferences(settings,'assisted',False)
check('assisted choice does not enable recurring revisit',preferences(settings)=={'mode':'assisted','background_revisit':False})

registration=post('/publications',{'platform_revision_id':pr_id,'platform_post_id':'isolated-manual-1','declaration_source':'manual','run_mode':'real'}).json()
pub_id=registration.get('id') or registration.get('publication_id')
check('manual publication remains a user declaration',bool(pub_id) and registration['declared']['declaration_source']=='manual')
rows=[{'platform':'douyin','post_id':'isolated-manual-1','observed_at':'2026-10-04T05:00:00Z','metric_name':'views','value':0,'unit':'count','aggregation_kind':'cumulative','traffic_type':'unknown'}]
response=post('/imports',{'kind':'metrics','format':'json','content':json.dumps(rows),'run_mode':'real','dry_run':False}).json()
check('manual actual zero metric accepted',response['accepted_rows']==1)
metrics=client.get(f'/api/v1/publications/{pub_id}/metrics').json()
check('saved zero remains zero',metrics['series']['views'][0]['value']==0)
check('unfilled metrics are not synthesized','likes' not in metrics['series'])
replay=post('/imports',{'kind':'metrics','format':'json','content':json.dumps(rows),'run_mode':'real','dry_run':False}).json()
check('duplicate manual revisit does not add snapshot',replay['idempotent_replay'] and client.get(f'/api/v1/publications/{pub_id}/metrics').json()['snapshot_count']==metrics['snapshot_count'])

# Real browser coverage of first-use guidance and manual forms on isolated storage.
import socket,urllib.request
from playwright.sync_api import sync_playwright,expect
from app.services.renderer import PlaywrightRenderer
with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
url=f'http://127.0.0.1:{port}'
env={**os.environ,'PYTHONUTF8':'1','PYTHONPATH':str(ROOT/'backend')}
proof=ROOT/'docs/test-artifacts';proof.mkdir(parents=True,exist_ok=True)
save_preferences(settings,'manual',False)
with (tmp/'api.log').open('wb') as log:
    process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        for _ in range(60):
            try:
                with urllib.request.urlopen(url+'/api/v1/health',timeout=1) as response:
                    if response.status==200:break
            except Exception:time.sleep(.2)
        with sync_playwright() as automation:
            browser=automation.chromium.launch(headless=True,executable_path=PlaywrightRenderer.resolve_executable())
            page=browser.new_page(viewport={'width':1440,'height':1000},accept_downloads=True)
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(url+'/');expect(page.locator('#open-tutorial')).to_be_visible()
            check('first-use guidance does not force a popup',not page.locator('#guided-operation').is_visible())
            # app.js 用动态 import 载入 guidance.js，静态按钮先可见、事件后绑定。
            # 教程目录由 guidance 同步创建并随即绑定点击，等它出现再点，避免点到空处。
            expect(page.locator('#guide-hub')).to_be_attached()
            page.locator('#open-tutorial').click();expect(page.locator('#guide-hub')).to_be_visible()
            check('tutorial offers six separate lessons',page.locator('[data-guide-lesson]').count()==6)
            page.locator('[data-guide-lesson="create"]').click();expect(page.locator('#guided-operation')).to_be_visible()
            check('real guidance opens accessibly',page.locator('#guided-operation').get_attribute('aria-label')=='实际操作引导')
            page.screenshot(path=str(proof/'studio-tutorial.png'),full_page=True,animations='disabled')
            for index in range(6):
                page.locator('#guide-skip').click();expect(page.locator('#guide-count')).to_have_text(f'做出第一篇图文 · {index+2} / 7')
            check('guidance reaches approval of the current revision',page.locator('#guide-title').inner_text()=='满意后，批准当前平台版本')
            page.locator('#guide-skip').click();expect(page.locator('#guided-operation')).not_to_be_visible()
            page.goto(url+'/');expect(page.locator('#open-tutorial')).to_be_visible()
            check('ended guidance stays closed',not page.locator('#guided-operation').is_visible())
            page.locator('#open-tutorial').click();page.locator('[data-guide-lesson="create"]').click();expect(page.locator('#guided-operation')).to_be_visible();page.keyboard.press('Escape')
            check('guidance can replay and collapse with escape',page.locator('#guided-operation').evaluate('el=>el.classList.contains("collapsed")'))
            page.locator('#guide-close').click()
            page.goto(url+'/views/PublishingHub.html');expect(page.locator('#status')).to_contain_text('手动')
            check('manual default and separate background choice visible',not page.locator('#background-revisit').is_checked())
            page.screenshot(path=str(proof/'studio-publishing-modes.png'),full_page=True)
            page.locator('#choose-manual').click();page.wait_for_url('**/ManualPublishing.html')
            expect(page.locator('#manual-images img')).to_have_count(len(source['pages']))
            check('manual helper only offers real generated source',page.locator('#manual-content option').count()==2)
            check('manual publication waits for actual completion checkbox',page.locator('#manual-register').is_disabled())
            page.locator('#manual-post-id').fill('isolated-browser-manual-2');page.locator('#manual-register-consent').check();page.locator('#manual-register').click()
            expect(page.locator('#manual-register-message')).to_contain_text('已保存')
            expect(page.locator('#manual-publication option')).to_have_count(2)
            check('manual UI creates actual declaration',page.locator('#manual-publication option').count()==2)
            page.locator('[data-metric=views]').fill('0');page.locator('[data-metric=likes]').fill('12')
            page.locator('#manual-metrics-consent').check();page.locator('#manual-save-metrics').click()
            expect(page.locator('#manual-metrics-message')).to_contain_text('已接收 2')
            expect(page.locator('#manual-history')).to_contain_text('点赞：')
            check('manual UI renders saved metrics instead of false zero records','1 条' in page.locator('#manual-history').inner_text())
            check('manual UI blank comments are unchanged',page.locator('[data-metric=comments]').input_value()=='')
            page.screenshot(path=str(proof/'studio-manual-revisit.png'),full_page=True)
            page.goto(url+'/views/ApiSettings.html');expect(page.locator('#configs')).to_contain_text('管理语音配置')
            check('speech settings coexist with text provider page',page.locator('[data-edit="'+cfg['id']+'"]').count()==0)
            page.goto(url+'/views/VideoStudio.html?content='+cid);expect(page.locator('[data-script]')).to_have_count(len(source['pages']))
            expect(page.locator('#sentence-list [data-scene]').first).to_be_visible()
            check('editor exposes only current chapter script',page.locator('[data-script]:visible').count()==1)
            page.locator('[data-page="1"]').click();expect(page.locator('#current-page-title')).to_have_text(source['pages'][1]['heading'])
            check('chapter navigation switches narration',page.locator('[data-script]:visible').input_value()==source['pages'][1]['script'])
            page.locator('[data-page="0"]').click()
            page.locator('#director-preset').select_option('process');expect(page.locator('#director-description')).to_contain_text('逐步')
            expect(page.locator('#storyboard-state')).to_have_text('已自动编排')
            page.locator('.director-guide summary').click()
            check('built-in director controls actual preview plan',page.locator('#director-version').inner_text().startswith('video-director'))
            page.locator('.director-guide summary').click()
            page.locator('#scene-effect').select_option('keyword');expect(page.locator('#sentence-list [data-scene]').first).to_contain_text('关键词强调')
            check('sentence effect can be customized',page.locator('#scene-effect').input_value()=='keyword')
            before_frame=page.locator('#motion-preview').evaluate('(c)=>c.toDataURL()')
            page.locator('#preview-play').click();page.wait_for_timeout(650)
            check('animated preview changes actual canvas pixels',before_frame!=page.locator('#motion-preview').evaluate('(c)=>c.toDataURL()'))
            page.locator('#preview-play').click()
            page.locator('#material-upload').set_input_files(str(ROOT/'images/img_anon_1.png'))
            expect(page.locator('#material-message')).to_contain_text('自定义图片')
            check('sentence material can be restored',page.locator('#material-clear').is_visible())
            page.locator('#material-clear').click();expect(page.locator('#material-clear')).not_to_be_visible()
            page.locator('#scene-effect').select_option('keyword');expect(page.locator('#storyboard-state')).to_have_text('已自动编排')
            page.locator('#presenter-settings summary').click();page.locator('#presenter-image').select_option(label='img_anon_1.png');page.locator('#presenter-settings summary').click()
            page.locator('.editing-desk').screenshot(path=str(proof/'motion-editor-desktop.png'))
            page.screenshot(path=str(proof/'motion-page-desktop.png'),full_page=True)
            page.locator('#presenter-settings summary').click()
            expect(page.locator('#presenter-preview-image')).to_be_visible()
            check('custom presenter is the initial visible choice',page.locator('#presenter').input_value()=='custom')
            page.locator('#presenter-upload').set_input_files(str(ROOT/'images/img_anon_1.png'))
            expect(page.locator('#presenter-message')).to_contain_text('图片已保存并选中')
            selected_image=page.locator('#presenter-image').input_value()
            check('UI upload selects the saved presenter',page.locator('#presenter-preview-image').get_attribute('src')==f'/api/v1/video-presenters/{selected_image}/image')
            page.reload();expect(page.locator('#presenter-image')).to_have_value(selected_image)
            page.locator('#presenter-settings summary').click()
            check('selected presenter survives page reload',page.locator('#presenter').input_value()=='custom')
            page.locator('#presenter').select_option('none');expect(page.locator('#custom-presenter')).not_to_be_visible()
            page.locator('#presenter').select_option('custom');expect(page.locator('#custom-presenter')).to_be_visible()
            check('presenter modes can be switched',page.locator('#presenter-note').is_hidden())
            page.locator('#fill-full-script').click();expect(page.locator('[data-script]').first).to_have_value(source['pages'][0]['full_script'])
            check('full diagram narration can be filled without model calls',page.locator('#script-message').inner_text().startswith(('讲稿已更新','修改讲稿后')))
            page.locator('#fill-short-script').click()
            check('video UI requires consent for online narration',page.locator('#make-video').is_disabled())
            page.locator('#voice-engine').select_option('local');expect(page.locator('#make-video')).to_be_enabled()
            check('offline system voice is clearly distinct',page.locator('#cloud-line').is_hidden())
            page.locator('#voice-engine').select_option('edge');page.locator('#cloud-consent').check()
            for index,field in enumerate(page.locator('[data-script]').all()):
                page.locator(f'[data-page="{index}"]').click();field.fill('测试逐页讲稿。')
            expect(page.locator('#storyboard-state')).to_have_text('已自动编排')
            expect(page.locator('#make-video')).to_be_enabled()
            before=len(service.list()['items']);page.locator('#make-video').click()
            expect(page.locator('#video-message')).to_contain_text('视频任务已保存')
            pending=service.list()['items'][0]
            check('video UI saves one explicit queued task',len(service.list()['items'])==before+1 and pending['state']=='queued' and pending['presenter_image_id']==selected_image)
            service.process_next(speech=tone)
            expect(page.locator('#video-'+pending['id']+' video')).to_be_visible(timeout=10000)
            page.locator('#video-'+pending['id']+' video').evaluate('(v)=>v.play()')
            page.wait_for_timeout(500)
            check('actual MP4 plays in browser',page.locator('#video-'+pending['id']+' video').evaluate('(v)=>!v.paused && v.videoWidth===720'))
            page.locator('#video-'+pending['id']).screenshot(path=str(proof/'studio-video-player.png'))
            if page.locator('#presenter-settings').get_attribute('open') is None:page.locator('#presenter-settings summary').click()
            page.locator('#custom-presenter').screenshot(path=str(proof/'studio-custom-presenter.png'))
            with page.expect_download() as downloaded:page.locator('#video-'+pending['id']).get_by_text('下载 MP4',exact=True).click()
            check('video UI downloads an MP4',downloaded.value.suggested_filename.endswith('.mp4'))
            page.set_viewport_size({'width':390,'height':844});page.emulate_media(reduced_motion='reduce')
            check('video page fits narrow screen',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
            page.locator('.editing-desk').screenshot(path=str(proof/'motion-editor-mobile.png'))
            page.goto(url+'/');expect(page.locator('#guide-hub')).to_be_attached()
            page.locator('#open-tutorial').click()
            check('tutorial fits narrow screen',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
            page.locator('#guide-hub-close').click()
            check('new UI pages have no script errors',not errors)
            browser.close()
    finally:
        process.terminate();process.wait(timeout=10)

service.engine.dispose();publishing.engine.dispose()
print(f'结果：{passed} 通过 / 0 失败')
