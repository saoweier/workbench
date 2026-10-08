from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import FileResponse
from pydantic import BaseModel,ConfigDict,Field
from .accounts import local_request
from ..core.config import get_settings
from ..services.video_service import VideoService
from ..services.video_presenters import PresenterStore,MAX_BYTES
from ..services.video_director import catalog
from ..services.provider_contract import ProviderConfig,ProviderStore,ProviderKind,AdapterType,SecretStore,ProviderStatus
from ..services.network_policy import validate_url
from ..services.publishing_preferences import preferences,save_preferences

router=APIRouter(tags=['video-studio'],dependencies=[Depends(local_request)])
settings=get_settings();service=VideoService(settings)
presenters=PresenterStore(settings)
materials=PresenterStore(settings,'video-materials',False)

@router.get('/video-presenters')
def presenter_list():return presenters.list()

@router.post('/video-presenters')
async def presenter_upload(request:Request,name:str='自定义讲解员'):
    content=bytearray()
    async for part in request.stream():
        content.extend(part)
        if len(content)>MAX_BYTES:raise HTTPException(413,'图片不能超过 10 MB。')
    return presenters.save(bytes(content),name)

@router.get('/video-materials')
def material_list():return materials.list()

@router.post('/video-materials')
async def material_upload(request:Request,name:str='讲解素材'):
    content=bytearray()
    async for part in request.stream():
        content.extend(part)
        if len(content)>MAX_BYTES:raise HTTPException(413,'图片不能超过 10 MB。')
    return materials.save(bytes(content),name)

@router.get('/video-materials/{asset_id}/image')
def material_image(asset_id:str):return FileResponse(materials.path(asset_id),media_type='image/png')

@router.get('/video-presenters/{asset_id}/image')
def presenter_image(asset_id:str):
    return FileResponse(presenters.path(asset_id),media_type='image/png')

class SceneChoice(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scene_id:str=Field(pattern='^[a-f0-9]{24}$')
    effect:Literal['focus','keyword','flow','compare','checklist','chart','image','scroll']|None=None
    material_id:str|None=Field(default=None,pattern='^[a-f0-9]{64}$')

class StoryboardRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    platform_revision_id:str=Field(max_length=36)
    scripts:list[str]=Field(min_length=1,max_length=35)
    scene_choices:list[SceneChoice]=Field(default_factory=list,max_length=400)
    director_preset:Literal['knowledge','process','comparison']='knowledge'

@router.get('/videos/director-presets')
def director_presets():return catalog()

@router.post('/videos/storyboard')
def storyboard(payload:StoryboardRequest):return service.storyboard(payload.model_dump())

class VideoRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    platform_revision_id:str=Field(max_length=36)
    expected_manifest_hash:str=Field(pattern='^[a-f0-9]{64}$')
    scripts:list[str]=Field(min_length=1,max_length=35)
    engine:Literal['edge','local','api']='edge'
    voice:str=Field(default='xiaoxiao',min_length=1,max_length=64,pattern='^[A-Za-z0-9_-]+$')
    rate:int=Field(default=0,ge=-20,le=30)
    presenter:Literal['none','guide','custom']='guide'
    presenter_image_id:str|None=Field(default=None,pattern='^[a-f0-9]{64}$')
    animation_style:Literal['classic','presentation']='classic'
    scene_choices:list[SceneChoice]=Field(default_factory=list,max_length=400)
    director_preset:Literal['knowledge','process','comparison']='knowledge'
    request_key:str=Field(min_length=16,max_length=64)
    cloud_consent:bool=Field(default=False,strict=True)
    config_id:str|None=Field(default=None,max_length=64)

@router.get('/videos/source/{pr_id}')
def source(pr_id:str):return service.preview(pr_id)
@router.get('/videos')
def videos():return service.list()
@router.get('/videos/{tid}')
def task(tid:str):return service.get(tid)
@router.post('/videos')
def create(payload:VideoRequest):return service.create(payload.model_dump())
@router.post('/videos/{tid}/cancel')
def cancel(tid:str):return service.cancel(tid)
@router.get('/videos/{tid}/media/{name}')
def media(tid:str,name:str,download:bool=False):
    task=service.get(tid)
    if task['state']!='succeeded' or name not in {'video.mp4','narration.wav','subtitles.srt','manifest.json','storyboard.json'}:raise HTTPException(404,'视频产物尚未完成或不存在。')
    path=service.root/tid/name
    if not path.is_file():raise HTTPException(404,'产物文件缺失。')
    types={'video.mp4':'video/mp4','narration.wav':'audio/wav','subtitles.srt':'text/plain; charset=utf-8','manifest.json':'application/json','storyboard.json':'application/json'}
    return FileResponse(path,media_type=types[name],filename=f"{task['display_id']}-{name}" if download else None)

class SpeechSettings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(default='我的语音 API',max_length=80)
    base_url:str=Field(max_length=500)
    model_id:str=Field(min_length=1,max_length=100)
    api_key:str|None=Field(default=None,max_length=2048)
    allow_localhost:bool=False

@router.get('/video-speech/config')
def speech_settings():
    cfg=ProviderStore(settings.storage_root/'provider_configs.json').default_for(ProviderKind.SPEECH)
    return {'config':cfg.public_view() if cfg else None}
@router.post('/video-speech/config')
def save_speech(payload:SpeechSettings):
    try:validate_url(payload.base_url,payload.allow_localhost)
    except (OSError,ValueError):raise HTTPException(422,'语音服务地址不可用；远程服务需 HTTPS，本机服务须明确允许。') from None
    store=ProviderStore(settings.storage_root/'provider_configs.json');old=store.default_for(ProviderKind.SPEECH)
    cfg=old or ProviderConfig(name=payload.name,kind=ProviderKind.SPEECH,adapter_type=AdapterType.OPENAI_SPEECH,base_url=payload.base_url)
    cfg.name=payload.name;cfg.base_url=payload.base_url.rstrip('/');cfg.model_id=payload.model_id;cfg.allow_localhost=payload.allow_localhost;cfg.enabled=True
    if payload.api_key:cfg.secret_ref=SecretStore(settings.secret_store_path).put(payload.api_key)
    if not cfg.secret_ref:raise HTTPException(422,'首次配置需要填写语音 API 密钥。')
    cfg.last_test_status=ProviderStatus.CONFIGURED_UNTESTED
    store.upsert(cfg);return {'config':cfg.public_view(),'message':'配置已保存，尚未调用语音服务。'}

class PublishingSettings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mode:Literal['manual','assisted']
    background_revisit:bool=Field(default=False,strict=True)
@router.get('/publishing/preferences')
def publishing_settings():return preferences(settings)
@router.post('/publishing/preferences')
def publishing_save(payload:PublishingSettings):return save_preferences(settings,payload.mode,payload.background_revisit)
