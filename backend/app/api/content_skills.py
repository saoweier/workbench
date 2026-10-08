"""Skills configuration and explicit planning/media operations."""
from uuid import UUID, uuid4
from fastapi import APIRouter, UploadFile, File, Form, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel,ConfigDict,Field

from .production import SessionFactory,_runtime
from ..core.errors import NotFound,StateConflict,ValidationFailed
from ..models.entities import ContentItem,ContentRevision,PlatformRevision,Event
from ..services.content_skills import ContentSkills,ContentPlan
from ..services.content_media import ContentMedia
from ..services.provider_contract import RunMode
from ..services.research_service import ResearchService
from .accounts import local_request
from .creation import runtime

router=APIRouter(tags=['content-skills'],dependencies=[Depends(local_request)])


class SkillEdit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    instructions:str=Field(min_length=20,max_length=8000)
    expected_version:str

from ..services.template_packages import TemplatePackage,TemplateStore
class TemplateEdit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    package:TemplatePackage
    expected_version:str|None=None

@router.get('/template-packages')
def template_packages():return {'items':TemplateStore(SessionFactory).catalog(),'notice':'每套样式包含写作Skill与本地排版设置。新任务冻结整个包；编辑不会修改旧稿。'}

@router.put('/template-packages/{template_id}')
def save_template(template_id:str,payload:TemplateEdit):
    if template_id!=payload.package.id:raise ValidationFailed('模板标识与地址不一致')
    return TemplateStore(SessionFactory).save(payload.package,payload.expected_version)

@router.get('/template-packages/{template_id}/versions')
def template_versions(template_id:str):return {'items':TemplateStore(SessionFactory).versions(template_id)}

@router.post('/template-packages/preview')
def preview_template(payload:TemplatePackage,mode:str='representative'):
    from fastapi.responses import HTMLResponse
    from ..services.template_examples import example_page,example_form
    from ..services.visual_content import illustrated_page_html,VISUAL_FIT_JS
    from ..services.profile_store import engineering_default
    if mode not in {'representative','common'}:raise ValidationFailed('未知预览方式')
    page=example_page(payload.id,package=payload.model_dump(mode='json'),mode=mode)
    doc,_=illustrated_page_html(page,engineering_default('douyin'),'douyin',1,'sans-serif',form=example_form(page))
    doc=doc.replace('</body>',"<script>document.fonts.ready.then(()=>requestAnimationFrame(()=>{const fit=("+VISUAL_FIT_JS+")();document.body.dataset.fit=fit;}));</script></body>")
    return HTMLResponse(doc)

@router.get('/template-packages/{template_id}/export')
def export_template(template_id:str):
    import io,json,zipfile
    from fastapi.responses import Response
    p=TemplateStore(SessionFactory).get(template_id);rules=p.pop('instructions')
    for key in ['origin','package_version']:p.pop(key,None)
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr(template_id+'/SKILL.md',rules)
        z.writestr(template_id+'/template.json',json.dumps(p,ensure_ascii=False,indent=2))
    return Response(buffer.getvalue(),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="template-{template_id}.zip"'})


class PlanInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:UUID
    topic:str=Field(min_length=3,max_length=160)
    requirements:str=Field(default='',max_length=4000)
    materials:str=Field(default='',max_length=16000)
    run_mode:RunMode=RunMode.REAL


class DiscoveryInput(BaseModel):
    request_id:UUID
    direction:str=Field(default='',max_length=300)
    run_mode:RunMode=RunMode.LOCAL_SEED


@router.post('/creation/discovery')
def discover(payload:DiscoveryInput):
    from ..services.content_skills import _hash
    rt=runtime();service=ContentSkills(SessionFactory,rt);cid=str(payload.request_id)
    signature=_hash(payload.model_dump(mode='json'))
    with SessionFactory() as s:
        old=s.query(Event).filter_by(entity_type='discovery',entity_id=cid,type='discovery_ready').first()
        if old:
            if old.payload['signature']!=signature:raise StateConflict('检索要求已变化，请开始新一次发现')
            return old.payload['result']
    snapshot=service.snapshot(cid)
    from .studio import boards
    data=boards.all()
    result={'items':[i for b in data['sources'] for i in b['items']],
        'fetched_at':data.get('last_success_at'),'note':'热点仅通过HotPush聚合服务读取；保留来源返回顺序，不推算全网热度。',
        'search_executed':False,'refreshing':data['refreshing']}
    service.record(cid,'discovery',state='succeeded',inputs=payload.model_dump(mode='json'),output=result,snapshot=snapshot)
    with SessionFactory() as s:
        s.add(Event(entity_type='discovery',entity_id=cid,type='discovery_ready',actor='user',run_mode=payload.run_mode.value,payload={'signature':signature,'result':result}));s.commit()
    return result


@router.get('/content-skills')
def skills():
    return {'items':ContentSkills(SessionFactory).catalog(),'capabilities':{
        'text_ready':bool(_runtime.text_provider()),'image_ready':bool(_runtime.image_provider()),
        'search_ready':bool(_runtime.search_provider()),'public_research_available':False,'notice':'七个流程技能与七个题材技能；热梗先核验出处与含义。公开搜索失败或缺少正文会停止，不用模型猜测。打开页面与保存规则不会调用模型。'}}


@router.put('/content-skills/{skill_id}')
def edit_skill(skill_id:str,payload:SkillEdit):
    return ContentSkills(SessionFactory).save(skill_id,**payload.model_dump())


@router.get('/contents/{content_id}/skill-history')
def skill_history(content_id:str):
    with SessionFactory() as s:
        if not s.get(ContentItem,content_id):raise NotFound('内容不存在')
    return {'items':ContentSkills(SessionFactory).history(content_id)}

@router.get('/contents/{content_id}/diagnostics')
def content_diagnostics(content_id:str):
    from ..services.content_diagnostics import diagnose
    return diagnose(SessionFactory,content_id)

@router.get('/content-skills/{skill_id}/versions')
def skill_versions(skill_id:str):
    from ..services.content_skills import SKILL_ROOT,SKILL_IDS,_hash
    if skill_id not in SKILL_IDS:raise NotFound('技能不存在')
    text=(SKILL_ROOT/skill_id/'SKILL.md').read_text(encoding='utf-8')
    versions=[{'version':_hash({'id':skill_id,'instructions':text})[:16],'instructions':text,'origin':'built_in','created_at':None}]
    with SessionFactory() as s:
        for e in s.query(Event).filter_by(entity_type='skill_definition',entity_id=skill_id,type='skill_saved').order_by(Event.time.desc()):
            instructions=e.payload['instructions'];versions.append({'version':_hash({'id':skill_id,'instructions':instructions})[:16],'instructions':instructions,'origin':'workspace_override','created_at':e.time.isoformat()})
    return {'items':versions,'notice':'载入历史规则只填入编辑器，保存后才影响新任务；已有任务的实际规则仍使用冻结版本。'}


@router.post('/creation/plans')
def plan(payload:PlanInput):
    from ..services.content_skills import _hash
    digest=_hash(payload.model_dump(mode='json'))
    with SessionFactory() as s:
        previous=s.query(Event).filter_by(entity_type='content_plan',entity_id=str(payload.request_id),type='plan_ready').first()
        if previous:
            if previous.payload['input_hash']!=digest:raise StateConflict('规划输入已变化，请生成新的规划')
            return {**previous.payload,'plan_id':str(payload.request_id),'reused':True}
    rt=runtime()
    service=ContentSkills(SessionFactory,rt);snapshot=service.snapshot(str(payload.request_id))
    research=ResearchService(rt).research(topic=payload.topic,requirements=payload.requirements,
        user_materials=[{'text':payload.materials}] if payload.materials.strip() else [],run_mode=payload.run_mode)
    from ..services.evidence_gate import is_meme
    if payload.run_mode==RunMode.REAL and is_meme(payload.topic,payload.requirements):
        assessment=service.research_review(topic=payload.topic,requirements=payload.requirements,
            sources=research.sources_as_dicts(),context_id=str(payload.request_id),content_id=None,snapshot=snapshot)
        from ..services.research_service import ClaimModel
        for fact in assessment.facts:
            research.claims.append(ClaimModel(id=f'C{len(research.claims)+1:02d}',kind='document_observation',
                statement=f'{fact.role}：{fact.statement}；原文引用：{fact.quote}',source_ids=[fact.source_id]))
        research.assessment=assessment.model_dump(mode='json')
        research.limitations.extend(v for v in assessment.limitations if v not in research.limitations)
    from ..services.content_forms import build_brief
    brief=build_brief(topic=payload.topic,requirements=payload.requirements)
    result=service.plan(topic=payload.topic,requirements=payload.requirements,claims=research.claims_as_dicts(),
        sources=research.sources_as_dicts(),run_mode=payload.run_mode,context_id=str(payload.request_id),
        snapshot=snapshot,brief=brief)
    value={'input_hash':digest,'topic':payload.topic,'requirements':payload.requirements,'materials':payload.materials,'plan':result.model_dump(mode='json'),
           'claims':research.claims_as_dicts(),'sources':research.sources_as_dicts(),'snapshot':snapshot,'run_mode':payload.run_mode.value}
    with SessionFactory() as s:
        s.add(Event(entity_type='content_plan',entity_id=str(payload.request_id),type='plan_ready',actor='user',run_mode=payload.run_mode.value,payload=value));s.commit()
    return {**value,'plan_id':str(payload.request_id),'reused':False}


@router.post('/content-media')
async def upload_media(image:UploadFile=File(...),content_id:str|None=Form(default=None),description:str=Form(default='用户配图')):
    if content_id:
        with SessionFactory() as s:
            if not s.get(ContentItem,content_id):raise NotFound('内容不存在')
    raw=await image.read(20_000_001)
    return ContentMedia().store(raw,content_id=content_id,description=description,origin='provided')


@router.get('/content-media/{media_id}/image')
def media_image(media_id:str):
    return FileResponse(ContentMedia().path(media_id),media_type='image/png')


class AuditInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:UUID
    base_revision_id:str
    requirements:str=Field(default='',max_length=4000)
    run_mode:RunMode=RunMode.REAL

class ImageCleanupInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:UUID
    base_revision_id:str

@router.post('/contents/{content_id}/image-cleanup')
def image_cleanup(content_id:str,payload:ImageCleanupInput):
    from ..services.meme_editorial import cleanup_revision
    from ..services.pipeline import PipelineService
    result=cleanup_revision(SessionFactory,content_id,payload.base_revision_id,str(payload.request_id))
    pipeline=PipelineService(SessionFactory)
    for platform in result['platforms']:pipeline.render(platform['platform_revision_id'])
    return result


@router.post('/contents/{content_id}/skill-audit')
def audit_content(content_id:str,payload:AuditInput):
    with SessionFactory() as s:
        item=s.get(ContentItem,content_id);rev=s.get(ContentRevision,payload.base_revision_id)
        if not item or not rev or rev.content_id!=content_id:raise NotFound('内容版本不存在')
        if item.active_revision_id!=rev.id:raise StateConflict('当前版本已更新，请刷新后审核')
        topic=item.topic;brief=rev.brief_json or {};claims=(rev.claims_json or {}).get('claims',[])
        drafts=[{'platform':p.platform,'title':p.title,'caption':p.caption,'pages':p.pages_json['pages']} for p in s.query(PlatformRevision).filter_by(content_revision_id=rev.id)]
    rt=runtime();service=ContentSkills(SessionFactory,rt);context=str(payload.request_id)
    report=service.audit(topic=topic,requirements=payload.requirements or brief.get('user_requirements',''),
        plan=brief.get('content_plan'),variants=drafts,claims=claims,run_mode=payload.run_mode,
        context_id=context,content_id=content_id,snapshot=service.snapshot(context,direction=(brief.get('creative_brief') or {}).get('direction','general')),sources=(rev.claims_json or {}).get('sources',[]))
    return {'revision_id':payload.base_revision_id,'report':report.model_dump(mode='json'),'notice':'模型审核为建议，不代替人工批准；文字模型不具备图片像素审核能力。'}
