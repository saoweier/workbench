from datetime import timedelta
import hashlib
import json
import re
import time
import threading
from pathlib import Path
from uuid import uuid4
import wave
from sqlalchemy import create_engine,select
from sqlalchemy.orm import sessionmaker
from ..models.entities import Base,VideoTask,PlatformRevision,ContentItem,ProviderCallRow,Event,_now,enable_sqlite_fk
from ..core.errors import NotFound,StateConflict,ValidationFailed
from .provider_contract import ProviderStore,ProviderKind
from .video_speech import synthesize,SpeechUncertain
from .video_render import render
from .video_presenters import PresenterStore
from .video_storyboard import plan as story_plan

def digest(payload):
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

class VideoService:
    def __init__(self,settings):
        self.settings=settings
        self.engine=create_engine(settings.database_url,future=True);enable_sqlite_fk(self.engine)
        Base.metadata.create_all(self.engine);self.sf=sessionmaker(bind=self.engine,future=True)
        self.root=settings.artifact_dir/'videos'

    def source(self,s,pr_id):
        pr=s.get(PlatformRevision,pr_id)
        if not pr:raise NotFound('图文版本不存在。')
        content=s.get(ContentItem,pr.content_revision.content_id)
        if content.active_revision_id!=pr.content_revision_id:raise StateConflict('请选择当前图文版本。')
        images=sorted([a for a in pr.artifacts if a.kind=='page_image'],key=lambda a:a.page_index)
        pages=pr.pages_json.get('pages',[])
        if not pr.manifest_hash or not images or len(images)!=len(pages):raise StateConflict('图文尚未完成出图，请先生成成品。')
        output=[]
        for art,page in zip(images,pages):
            body=page.get('body') or []
            if isinstance(body,str):body=[body]
            visual=page.get('visual') or {}
            parts=[str(page.get('heading','')),*map(str,body),str(visual.get('takeaway',''))]
            text='。'.join(part.strip().strip('。') for part in parts if part.strip().strip('。'))
            details=[str(page.get('heading','')),*map(str,body)]
            for item in visual.get('items',[]):
                if isinstance(item,dict):details.append('，'.join(str(item.get(k,'')) for k in ['label','detail'] if item.get(k)))
            details.append(str(visual.get('takeaway','')))
            full='。'.join(part.strip('。') for part in details if part.strip('。'))
            output.append({'index':art.page_index,'image_url':f'/api/v1/artifacts/{art.id}/raw',
                'storage_key':art.storage_key,'sha256':art.sha256,'heading':page.get('heading',''),'script':text,'full_script':full,
                'visual':visual})
        return {'platform_revision_id':pr.id,'manifest_hash':pr.manifest_hash,'title':pr.title,
            'display_id':content.display_id,'content_id':content.id,'run_mode':content.run_mode,'pages':output}

    def preview(self,pr_id):
        with self.sf() as s:
            data=self.source(s,pr_id)
            for page in data['pages']:page.pop('storage_key');page.pop('sha256')
            return data

    def plan(self,pages,choices=None,director_preset='knowledge'):
        result=story_plan(pages,choices,director_preset)
        asset_store=PresenterStore(self.settings,'video-materials',False)
        for scene in result['scenes']:
            if scene.get('material_id'):
                asset=asset_store.get(scene['material_id']);scene['material_url']=asset['image_url']
        return result

    def storyboard(self,data):
        with self.sf() as s:
            source=self.source(s,data['platform_revision_id'])
        scripts=data['scripts']
        if len(scripts)!=len(source['pages']) or sum(map(len,scripts))>6000:
            raise ValidationFailed('讲稿须逐页对应，合计不超过 6000 字。')
        for page,script in zip(source['pages'],scripts):
            if not script.strip() or len(script)>1200:raise ValidationFailed('每页讲稿须为 1–1200 字。')
            page['script']=script.strip()
        return self.plan(source['pages'],data.get('scene_choices'),data.get('director_preset','knowledge'))

    def create(self,data):
        engine=data['engine'];voice=data['voice']
        if engine!='local' and data.get('cloud_consent') is not True:raise ValidationFailed('请确认将讲稿发送到所选配音服务。')
        if engine=='edge' and voice not in {'xiaoxiao','yunxi'}:raise ValidationFailed('请选择提供的中文配音。')
        with self.sf() as s:
            s.connection().exec_driver_sql('BEGIN IMMEDIATE')
            payload=self.source(s,data['platform_revision_id'])
            if payload['manifest_hash']!=data['expected_manifest_hash']:raise StateConflict('图文版本已变化，请重新读取讲稿。')
            scripts=data['scripts']
            if len(scripts)!=len(payload['pages']) or sum(map(len,scripts))>6000:raise ValidationFailed('讲稿须逐页对应，合计不超过 6000 字。')
            for page,script in zip(payload['pages'],scripts):
                if not script.strip() or len(script)>1200:raise ValidationFailed('每页讲稿须为 1–1200 字。')
                page['script']=script.strip()
                self.image_path(page)  # Validate immutable bytes before any provider call.
            payload.update(engine=engine,voice=voice,rate=data['rate'],presenter=data['presenter'],config_id=data.get('config_id'))
            if data.get('animation_style')=='presentation':
                payload['animation_style']='presentation'
                payload['storyboard']=self.plan(payload['pages'],data.get('scene_choices'),data.get('director_preset','knowledge'))
            if data['presenter']=='custom':
                if not data.get('presenter_image_id'):raise ValidationFailed('请先上传或选择一张讲解员图片。')
                asset=PresenterStore(self.settings).get(data['presenter_image_id'])
                payload['presenter_image_id']=asset['id']
            if engine=='api':
                cfg=ProviderStore(self.settings.storage_root/'provider_configs.json').get(data.get('config_id'))
                if not cfg or cfg.kind!=ProviderKind.SPEECH or not cfg.enabled or not cfg.secret_ref:raise ValidationFailed('语音 API 未完成配置。')
                payload['config_version']=cfg.updated_at.isoformat()
            key=data['request_key'];hashed=digest(payload)
            existing=s.scalar(select(VideoTask).where(VideoTask.request_key==key))
            if existing:
                if existing.payload_hash!=hashed:raise StateConflict('同一提交编号不能更换稿件；请重新确认生成。')
                return self.public(existing)
            task=VideoTask(id=str(uuid4()),platform_revision_id=payload['platform_revision_id'],request_key=key,payload_hash=hashed,payload_json=payload)
            s.add(task);s.flush()
            s.add(Event(entity_type='video_task',entity_id=task.id,type='video_requested',actor='user',run_mode=payload['run_mode'],payload={'engine':engine,'presenter':data['presenter']}))
            s.commit();return self.public(task)

    def image_path(self,page):
        root=self.settings.artifact_dir.resolve();path=(root/page['storage_key']).resolve()
        if not path.is_relative_to(root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=page['sha256']:
            raise StateConflict('页图缺失或内容发生变化，视频任务已停止。')
        return path

    def public(self,t):
        return {'id':t.id,'state':t.state,'progress':t.progress,'error':t.error,'title':t.payload_json['title'],
            'display_id':t.payload_json['display_id'],'content_id':t.payload_json['content_id'],'engine':t.payload_json['engine'],
            'presenter':t.payload_json['presenter'],'page_count':len(t.payload_json['pages']),'result':t.result_json or {},
            'presenter_image_id':t.payload_json.get('presenter_image_id'),
            'animation_style':t.payload_json.get('animation_style','classic'),
            'created_at':t.created_at.isoformat(),'video_url':f'/api/v1/videos/{t.id}/media/video.mp4' if t.state=='succeeded' else None,
            'audio_url':f'/api/v1/videos/{t.id}/media/narration.wav' if t.state=='succeeded' else None,
            'subtitles_url':f'/api/v1/videos/{t.id}/media/subtitles.srt' if t.state=='succeeded' else None}

    def list(self):
        with self.sf() as s:return {'items':[self.public(t) for t in s.scalars(select(VideoTask).order_by(VideoTask.created_at.desc()))]}

    def get(self,tid):
        with self.sf() as s:
            t=s.get(VideoTask,tid)
            if not t:raise NotFound('视频任务不存在。')
            return self.public(t)

    def cancel(self,tid):
        with self.sf() as s:
            s.connection().exec_driver_sql('BEGIN IMMEDIATE');t=s.get(VideoTask,tid)
            if not t:raise NotFound('视频任务不存在。')
            if t.state not in {'queued','running'}:raise StateConflict('当前视频任务已结束。')
            t.state='cancelled';t.updated_at=_now();s.commit();return self.public(t)

    def process_next(self,heartbeat=lambda:None,speech=synthesize,renderer=render):
        with self.sf() as s:
            s.connection().exec_driver_sql('BEGIN IMMEDIATE')
            # Abandoned execution is not auto-replayed (speech may already be billed).
            for abandoned in s.scalars(select(VideoTask).where(VideoTask.state=='running',VideoTask.updated_at<_now()-timedelta(minutes=10))):
                abandoned.state='interrupted';abandoned.error='任务中断，配音调用可能已执行。请核对后手动重新生成。'
            if s.scalar(select(VideoTask.id).where(VideoTask.state=='running')):
                s.commit();return False
            t=s.scalar(select(VideoTask).where(VideoTask.state=='queued').order_by(VideoTask.created_at))
            if not t:s.commit();return False
            tid,payload=t.id,t.payload_json;t.state='running';t.updated_at=_now();s.commit()
        directory=self.root/tid;directory.mkdir(parents=True,exist_ok=True)
        def update(number):
            heartbeat()
            with self.sf() as s:
                t=s.get(VideoTask,tid)
                if t.state=='cancelled':raise InterruptedError('已取消视频任务，已经发生的配音调用不会撤销。')
                t.progress=number;t.updated_at=_now();s.commit()
        stopped=threading.Event()
        def keep_alive():
            while not stopped.wait(10):
                try:
                    heartbeat()
                    with self.sf() as session:
                        active=session.get(VideoTask,tid)
                        if active.state=='running':active.updated_at=_now();session.commit()
                except Exception:pass
        keeper=threading.Thread(target=keep_alive,daemon=True);keeper.start()
        try:
            images=[self.image_path(page) for page in payload['pages']]
            render_payload=payload
            if payload['presenter']=='custom':
                presenter_path=PresenterStore(self.settings).path(payload.get('presenter_image_id'))
                render_payload={**payload,'presenter_image_path':str(presenter_path)}
            if payload.get('animation_style')=='presentation':
                material_store=PresenterStore(self.settings,'video-materials',False)
                material_paths={scene['material_id']:str(material_store.path(scene['material_id']))
                    for scene in payload['storyboard']['scenes'] if scene.get('material_id')}
                render_payload={**render_payload,'material_paths':material_paths}
            if payload['engine']=='api':
                cfg=ProviderStore(self.settings.storage_root/'provider_configs.json').get(payload['config_id'])
                if not cfg or cfg.updated_at.isoformat()!=payload['config_version']:raise ValueError('排队期间语音配置已修改，请重新确认生成。')
            audio=[]
            for i,page in enumerate(payload['pages']):
                update(int(i/len(images)*30));clip=directory/f'page-{i+1:02}.wav';call_id=None
                if payload['engine']!='local':
                    with self.sf() as s:
                        row=ProviderCallRow(id=str(uuid4()),request_key=f'video:{tid}:page:{i+1}',content_id=payload['content_id'],
                            platform_revision_id=payload['platform_revision_id'],provider_config_id=payload.get('config_id'),
                            provider_name='Microsoft Edge TTS' if payload['engine']=='edge' else 'Speech API',model_id=payload['voice'] if payload['engine']=='edge' else cfg.model_id,
                            state='unknown',run_mode='real',usage_raw={'characters':len(page['script']),'purpose':'video_narration','voice':payload['voice']})
                        s.add(row);s.commit();call_id=row.id
                try:
                    speech(page['script'],clip,payload['engine'],payload['voice'],payload['rate'],self.settings,payload.get('config_id'))
                except Exception as failure:
                    if call_id:
                        with self.sf() as s:
                            row=s.get(ProviderCallRow,call_id);row.state='unknown' if isinstance(failure,SpeechUncertain) else 'failed';row.finished_at=_now();s.commit()
                    raise
                if call_id:
                    with self.sf() as s:row=s.get(ProviderCallRow,call_id);row.state='succeeded';row.finished_at=_now();s.commit()
                audio.append(clip)
            update(35);result=renderer(render_payload,images,audio,directory,update);update(100)
            result['sha256']=hashlib.sha256((directory/'video.mp4').read_bytes()).hexdigest()
            (directory/'manifest.json').write_text(json.dumps({'source_manifest':payload['manifest_hash'],'payload_hash':digest(payload),'result':result},ensure_ascii=False,indent=2),encoding='utf-8')
            with self.sf() as s:
                t=s.get(VideoTask,tid)
                if t.state=='cancelled':return True
                t.state='succeeded';t.result_json=result;t.updated_at=_now();s.commit()
        except Exception as failure:
            with self.sf() as s:
                t=s.get(VideoTask,tid)
                if t.state!='cancelled':
                    t.state='unknown' if isinstance(failure,SpeechUncertain) else 'failed'
                    t.error=str(failure) if isinstance(failure,(ValueError,StateConflict,SpeechUncertain,InterruptedError)) else '视频处理未完成，请检查配音服务、文件和运行环境。'
                    t.updated_at=_now();s.commit()
        finally:
            stopped.set();keeper.join(timeout=2)
        return True
