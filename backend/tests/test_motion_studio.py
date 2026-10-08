"""Sentence direction, local assets and actual animated MP4; no external calls."""
from pathlib import Path
import os,sys,tempfile,shutil,wave,json
from io import BytesIO
from uuid import uuid4
from PIL import Image,ImageChops

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-motion-'))
shutil.copytree(ROOT/'examples/demo/storage/artifacts',tmp/'artifacts')
shutil.copy2(ROOT/'examples/demo/storage/workbench.db',tmp/'test.db')
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",CWB_ARTIFACT_DIR=str(tmp/'artifacts'),CWB_TMP_DIR=str(tmp/'tmp'),CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'))
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app.api.videos import service,materials
from app.models.entities import PlatformRevision,VideoTask,ProviderCallRow
from app.services.video_storyboard import sentences,plan,timed_scenes,EFFECTS
from app.services.video_motion import frame
from app.services.video_speech import ffmpeg,command
client=TestClient(app);headers={'X-CWB-Local-Action':'account-connection','Origin':'http://testserver'};passed=0
def check(label,condition):
    global passed
    assert condition,label
    passed+=1;print('PASS '+label,flush=True)
def post(path,data):return client.post('/api/v1'+path,json=data,headers=headers)
with service.sf() as s:pr_id=s.scalar(select(PlatformRevision).where(PlatformRevision.platform=='douyin')).id
source=service.preview(pr_id)
script='选一个清楚的观点。先研究，再写稿，然后检查。不是全部自动，而是人工确认。审核标题，核对来源，记录结果。把已有素材逐页展开。把核心意思讲清楚。方案一20%，方案二30%。'
scripts=[script]+['这一章，做一个总结。']*(len(source['pages'])-1)
base={'platform_revision_id':pr_id,'scripts':scripts}
story=post('/videos/storyboard',base).json();first_page=[s for s in story['scenes'] if s['page_index']==0];target=first_page[0]
check('one sentence receives one beat',len(first_page)==7)
check('all supplied narration survives segmentation',''.join(s['text'] for s in first_page)==script)
check('semantic scenes cover flow comparison checklist scroll and chart',{'flow','compare','checklist','scroll','chart'}<=set(s['effect'] for s in first_page))
with service.sf() as s:check('preview makes no tasks or provider calls',s.scalar(select(func.count()).select_from(VideoTask))==0 and s.scalar(select(func.count()).select_from(ProviderCallRow))==0)
check('timing honestly marked estimated',story['timing']=='estimated_by_sentence_length')
check('sentence IDs stable',post('/videos/storyboard',base).json()['scenes'][0]['id']==target['id'])
check('long sentences split without loss',''.join(sentences('测试'*120))=='测试'*120 and max(map(len,sentences('测试'*120)))<=80)
check('repeated punctuation never creates empty scenes',sentences('先选素材。。再讲解！!')==['先选素材。。','再讲解！!'])
check('punctuation-only narration rejected',post('/videos/storyboard',{**base,'scripts':['。。']+scripts[1:]}).status_code==422)
check('three embedded presets available',len(client.get('/api/v1/videos/director-presets').json()['items'])==3)
neutral=[{**source['pages'][0],'visual':{},'script':'把观点表达清楚。给读者一个解释。用简单的话说完整。'}]
check('presets change actual decisions',plan(neutral,director_preset='knowledge')['scenes'][1]['effect']!=plan(neutral,director_preset='process')['scenes'][1]['effect'])
check('unknown preset rejected',post('/videos/storyboard',{**base,'director_preset':'invented'}).status_code==422)
check('explicit effect overrides preset',post('/videos/storyboard',{**base,'scene_choices':[{'scene_id':target['id'],'effect':'keyword'}]}).json()['scenes'][0]['effect']=='keyword')
check('chart cannot fabricate numbers',post('/videos/storyboard',{**base,'scene_choices':[{'scene_id':target['id'],'effect':'chart'}]}).status_code==422)
chart=next(s for s in first_page if s['effect']=='chart')
check('chart uses original numbers',[v['value'] for v in chart['chart']]==[20,30])
check('missing material rejected',post('/videos/storyboard',{**base,'scene_choices':[{'scene_id':target['id'],'material_id':'0'*64}]}).status_code==404)
check('cross-origin storyboard blocked',client.post('/api/v1/videos/storyboard',json=base,headers={**headers,'Origin':'https://untrusted.example'}).status_code==403)
check('script count validated',post('/videos/storyboard',{**base,'scripts':['一页']}).status_code==422)
green=Image.new('RGB',(120,160),(37,172,105));buf=BytesIO();green.save(buf,format='PNG')
asset=client.post('/api/v1/video-materials',params={'name':'本机素材.png'},content=buf.getvalue(),headers=headers).json()
check('sentence material stored separately',asset['image_url'].startswith('/api/v1/video-materials/') and client.get(asset['image_url']).status_code==200)
choice={'scene_id':target['id'],'material_id':asset['id']}
check('material enters actual plan',post('/videos/storyboard',{**base,'scene_choices':[choice]}).json()['scenes'][0]['material_url']==asset['image_url'])
timeline=timed_scenes(first_page,8)
check('timing covers actual audio',timeline[0]['start']==0 and abs(timeline[-1]['end']-8)<1e-9 and all(abs(a['end']-b['start'])<1e-9 for a,b in zip(timeline,timeline[1:])))
check('timing uses sentence length',len({round(s['end']-s['start'],4) for s in timeline})>1)
proof=ROOT/'docs/test-artifacts';proof.mkdir(parents=True,exist_ok=True)
montage=Image.new('RGB',(960,852),'#eef3f8')
for i,effect in enumerate(EFFECTS):
    beat={**target,'effect':effect,'effect_label':EFFECTS[effect],'chart':chart['chart'],'items':['研究资料','写成讲稿','审核内容','记录结果'],'page_scene_count':7}
    first=frame(beat,green,.02,page_title='逐句动态讲解');later=frame(beat,green,.8,page_title='逐句动态讲解')
    check('export motion visible for '+effect,ImageChops.difference(first,later).getbbox() is not None)
    montage.paste(later.resize((240,426)),((i%4)*240,(i//4)*426))
montage.save(proof/'motion-scene-gallery.png')
payload={**base,'expected_manifest_hash':source['manifest_hash'],'engine':'local','voice':'xiaoxiao','rate':0,'presenter':'none','request_key':str(uuid4()),'cloud_consent':False,'animation_style':'presentation','director_preset':'process','scene_choices':[choice]}
task=post('/videos',payload).json()
check('animated task queues without speech',task['state']=='queued')
check('animated task is idempotent',post('/videos',payload).json()['id']==task['id'])
check('same submission cannot swap director',post('/videos',{**payload,'director_preset':'comparison'}).status_code==409)
with service.sf() as s:
    snapshot=s.get(VideoTask,task['id']).payload_json
    check('director and material version saved',snapshot['storyboard']['director']['id']=='process' and snapshot['storyboard']['director']['version']==1 and snapshot['storyboard']['scenes'][0]['material_id']==asset['id'])
def tone(text,path,*args):
    with wave.open(str(path),'wb') as clip:
        clip.setnchannels(1);clip.setsampwidth(2);clip.setframerate(24000);clip.writeframes(b'\0\0'*round(24000*(8 if text==script else 1.5)))
service.process_next(speech=tone);result=service.get(task['id']);movie=service.root/task['id']/'video.mp4'
check('actual animated MP4 has all scenes at 24 fps',result['state']=='succeeded' and result['result']['fps']==24 and result['result']['scene_count']==len(story['scenes']))
check('actual captions use sentence timing',result['result']['caption_timing']=='estimated_by_sentence_length')
downloaded=client.get(f"/api/v1/videos/{task['id']}/media/storyboard.json?download=true")
check('versioned direction plan is downloadable',downloaded.status_code==200 and downloaded.json()['director']['id']=='process' and 'attachment' in downloaded.headers['content-disposition'])
check('downloaded scene plan exposes no filesystem path','presenter_image_path' not in downloaded.text and 'storage_key' not in downloaded.text)
check('downloaded scene timestamps span final audio',abs(downloaded.json()['scenes'][-1]['end']-result['result']['duration_seconds'])<.02)
check('actual video and audio decode',command([ffmpeg(),'-i',str(movie),'-f','null','-']).returncode==0)
png=command([ffmpeg(),'-ss','0.3','-i',str(movie),'-frames:v','1','-f','image2pipe','-vcodec','png','-']).stdout
with Image.open(BytesIO(png)) as decoded:
    pixel=decoded.getpixel((200,450));check('uploaded material appears in MP4 pixels',pixel[1]>140 and pixel[0]<80)
check('subtitle text contains every supplied sentence',all(s['text'][:10] in (service.root/task['id']/'subtitles.srt').read_text(encoding='utf-8') for s in story['scenes']))
with service.sf() as s:check('export test calls no provider',s.scalar(select(func.count()).select_from(ProviderCallRow))==0)
shutil.copy2(movie,proof/'motion-smoke.mp4');service.engine.dispose()
print(f'结果：{passed} 通过 / 0 失败')
