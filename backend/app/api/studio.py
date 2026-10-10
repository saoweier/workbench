"""Direct creator: choose a real hot-list item or type a topic, then make/edit."""
from uuid import UUID, uuid5
import json
from fastapi import APIRouter,Depends
from pydantic import BaseModel,ConfigDict,Field
from .accounts import local_request
from . import creation,production
from ..core.errors import ValidationFailed,NotFound
from ..models.entities import ContentItem,ContentRevision,Event,Run,PlatformRevision
from ..services.trend_boards import TrendBoards,CATEGORIES,SOURCES
from ..services.provider_contract import RunMode
from ..services.content_forms import (build_brief,parse_page_budget,parse_rank_count,parse_caption_budget,CreativeBrief,detect_form,
    adjust_page_budget,page_budget_note,parse_cover_request,parse_page_direction,strip_page_budget_notes,
    DENSITY_CAPTION,parse_density_direction,density_note,strip_density_notes)

router=APIRouter(tags=['studio'],dependencies=[Depends(local_request)])
boards=TrendBoards()
class StudioInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:UUID
    topic:str=Field(min_length=3,max_length=300)
    requirements:str=Field(default='',max_length=3000)
    density:str=Field(default='balanced',pattern=r'^(short|balanced|detailed)$')
    style:str=Field(default='clean',pattern=r'^(clean|lively|professional)$')
    direction:str=Field(default='auto',pattern=r'^(auto|general|tech|life|games|news|work|learning)$')
    template_id:str=Field(default='auto',pattern=r'^[a-z][a-z0-9_]{1,31}$')
    materials:str=Field(default='',max_length=16000)
    media_ids:list[str]=Field(default_factory=list,max_length=6)
    image_policy:str=Field(default='auto',pattern=r'^(auto|diagram)$')
    platforms:list[str]=Field(default_factory=lambda:['douyin','xiaohongshu'],min_length=1,max_length=2)
    run_mode:RunMode=RunMode.REAL
    trend_id:str|None=None

def requirements(p):
    """把创作设置拼成创作要求。

    **创作阶段不写死页码**：篇幅由题型形态与信息密度决定（精简／均衡／详细）。
    用户自己在选题或要求里写了页数就按用户的来，这里不再另外追加页码。
    """
    return '\n'.join(filter(None,[p.requirements.strip(),
        '信息密度：'+{'short':'精简，只保留核心结论；发布文案150～300字','balanced':'均衡，足够具体；发布文案300～500字','detailed':'详细，解释依据和实用细节，避免凑内容；发布文案500～850字'}[p.density],
        '表达风格：'+{'clean':'简洁清楚、精致排版','lively':'活跃、生动、明快配色','professional':'专业、克制、清晰对照'}[p.style],
        '严格保留用户原选题、时间范围及内容形态；资料不足时说明缺口并暂停，禁止改换选题。']))

@router.get('/studio/options')
def options():
    from ..services.content_recipes import options as recipe_options
    return {**creation.options(),**recipe_options(),'categories':CATEGORIES,'sources':SOURCES,'workflow':'选题→简单外观设置→生成→持续调整'}

@router.get('/studio/template-preview/{template_id}')
def template_preview(template_id:str,mode:str='representative'):
    from fastapi.responses import HTMLResponse
    from ..services.template_examples import example_page,example_form
    from ..services.visual_content import illustrated_page_html,VISUAL_FIT_JS
    from ..services.profile_store import engineering_default
    if mode not in {'representative','common'}:raise ValidationFailed('未知预览方式')
    page=example_page(template_id,mode=mode)
    doc,_=illustrated_page_html(page,engineering_default('douyin'),'douyin',1,'sans-serif',form=example_form(page))
    # Same local production renderer; responsive view affects this demo page only.
    doc=doc.replace('</head>','<meta name="viewport" content="width=1080"></head>')
    doc=doc.replace('</body>',"<script>document.fonts.ready.then(()=>requestAnimationFrame(()=>{const fit=("+VISUAL_FIT_JS+")();document.body.dataset.fit=fit;if(fit<0.74){document.querySelector('.diagram-content').textContent='示例过密，请选择更简短的内容。';}}));</script></body>")
    return HTMLResponse(doc)

@router.get('/studio/trends')
def trends(category:str='hot',refresh:bool=False):return boards.all(category,refresh)

class HotPushConfig(BaseModel):
    model_config=ConfigDict(extra='forbid')
    base_url:str=Field(min_length=10,max_length=240)

@router.get('/studio/hotpush')
def hotpush_config():return boards.config()

@router.put('/studio/hotpush')
def save_hotpush(payload:HotPushConfig):return boards.configure(payload.base_url)

@router.post('/studio/produce',status_code=202)
def produce(payload:StudioInput):
    # The browser's draft ID may outlive edits or another open tab. Distinguish
    # user intent with a deterministic key; retries of one intent still enqueue
    # exactly once. Live HotPush timestamps and template revisions are not input.
    submission=payload.model_dump(mode='json',exclude={'request_id'})
    submission['topic']=payload.topic.strip()
    creation_key=uuid5(payload.request_id,json.dumps(submission,ensure_ascii=False,sort_keys=True))
    with creation.SessionFactory() as s:
        event=s.query(Event).filter_by(entity_type='creation',entity_id=str(creation_key),type='creation_enqueued').first()
        if not event:
            legacy=s.query(Event).filter_by(entity_type='creation',entity_id=str(payload.request_id),type='creation_enqueued').first()
            if legacy and _legacy_submission_matches(legacy.payload.get('brief',{}),payload):event=legacy
        if event and (event.entity_id==str(payload.request_id) or event.payload.get('studio_input')==submission):
            result=dict(event.payload['result'])
            run=s.get(Run,result['run_id'])
            result.update(reused=True,state=run.state,worker_running=creation._worker_running())
            return result
    notes=requirements(payload)
    # 页数不在创作阶段指定：不带页数预算的简报会走题型默认篇幅，
    # 用户想要更多或更少的页，生成后在预览页提「增加页数／少几页」即可。
    from ..services.content_recipes import apply_recipe
    brief=apply_recipe(build_brief(topic=payload.topic,requirements=payload.requirements),direction=payload.direction,template_id=payload.template_id)
    if brief.caption_max is None:brief.caption_max=DENSITY_CAPTION[payload.density][1]
    material=payload.materials
    if payload.trend_id:
        hit,snapshot=boards.find(payload.trend_id)
        # Preserve the selected item's URL in its frozen snapshot, including
        # social question/article pages. Research reads this lead before search.
        # This is one selected page, not a request to scrape platform hot lists.
        notes+='\n资料调研：先读取所选HotPush议题的原链接，原文不足时用联网搜索补充；热榜标题、摘要和顺序只提供线索，不能代替正文。'
        material+='\n选题来源快照（仅记录HotPush返回顺序及时间，不能证明全网热度或文章观点）：'+json.dumps({'item':hit,'fetched_at':snapshot['fetched_at'],'stale':snapshot.get('stale',False)},ensure_ascii=False)
    if len(material)>16000:raise ValidationFailed('参考资料与热点快照合计超过16000字，请缩短选填资料后重试。')
    # No recommendation or automatic topic substitution is involved.
    return creation.enqueue(creation.CreationInput(creation_key=creation_key,topic=payload.topic,
        requirements=notes,outline=[payload.topic],materials=material,media_ids=payload.media_ids,image_policy=payload.image_policy,
        platforms=payload.platforms,run_mode=payload.run_mode,creative_brief=brief),studio_input=submission)

def _legacy_submission_matches(old:dict,p:StudioInput):
    """Recover pre-upgrade submissions using frozen user fields, not live data."""
    if old.get('topic','').strip()!=p.topic.strip():return False
    if any(old.get(k)!=getattr(p,k) for k in ('platforms','media_ids','image_policy')):return False
    if old.get('run_mode')!=p.run_mode.value:return False
    if old.get('requirements','').split('\n资料调研：',1)[0]!=requirements(p):return False
    marker='\n选题来源快照（'
    material=old.get('materials','')
    if material.split(marker,1)[0]!=p.materials:return False
    if bool(marker in material)!=bool(p.trend_id):return False
    if p.trend_id:
        try:
            frozen=json.loads(material.split('：',1)[1] if material.startswith('选题来源快照') else material.split(marker,1)[1].split('：',1)[1])
            if frozen['item']['id']!=p.trend_id:return False
        except (ValueError,KeyError,IndexError,TypeError):return False
    brief=old.get('creative_brief') or {}
    # Explicit user selections must still match. Automatic selection is frozen.
    return (p.template_id=='auto' or brief.get('template_id')==p.template_id) and (p.direction=='auto' or brief.get('direction')==p.direction)

class StudioRevision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:UUID
    base_revision_id:str
    instruction:str=Field(min_length=2,max_length=3000)
    direction:str|None=Field(default=None,pattern=r'^(auto|general|tech|life|games|news|work|learning)$')
    template_id:str|None=Field(default=None,pattern=r'^[a-z][a-z0-9_]{1,31}$')
    run_mode:RunMode=RunMode.REAL
    materials:str=Field(default='',max_length=16000)
    media_ids:list[str]=Field(default_factory=list,max_length=6)
    image_policy:str=Field(default='auto',pattern=r'^(auto|diagram)$')

def _page_baseline(old:dict,platforms:list[str])->tuple:
    """改稿时的页数基准。

    稿件没有明确页数时，实际生效的是**平台默认篇幅**（抖音 4–6、小红书 5–7），
    而不是题型默认篇幅。拿题型默认当基准，抖音上的「增加页数」会原地不动——
    因为抖音默认上限本来就和题型上限重合。
    """
    if old.get('explicit_pages') and old.get('page_max'):
        return old.get('page_min'),old.get('page_max')
    from ..services.compose_service import PAGE_STRATEGY
    spans=[PAGE_STRATEGY[p] for p in platforms if p in PAGE_STRATEGY]
    if not spans:
        return old.get('page_min'),old.get('page_max')
    return min(s['min_pages'] for s in spans),max(s['max_pages'] for s in spans)


@router.post('/studio/contents/{content_id}/revise',status_code=202)
def revise(content_id:str,payload:StudioRevision):
    # Preserve prior hard constraints unless the user's new instruction changes them.
    with creation.SessionFactory() as s:
        item=s.get(ContentItem,content_id);base=s.get(ContentRevision,payload.base_revision_id)
        if not item or not base or base.content_id!=content_id:raise NotFound('内容版本不存在')
        old=(base.brief_json or {}).get('creative_brief') or {}
        platforms=[r.platform for r in s.query(PlatformRevision).filter_by(content_revision_id=base.id).all()]
    notes=payload.instruction
    new=build_brief(topic=item.topic,requirements=notes)
    # 详细程度：用户没写具体字数时，「更短更精简／更详细」必须真的改变篇幅。
    # 只把它当提示词里的一句好话，用户改完稿看不出任何差别。
    density=parse_density_direction(notes) if not parse_caption_budget(notes) else None
    if old:
        keep=dict(old)
        keep['original_requirements']=notes
        if new.detail_max is not None:keep['detail_max']=new.detail_max
        # 页数：给了数字就按数字；只写「增加页数 / 少几页」也要真的变，
        # 否则用户的定性要求会被旧预算（常常是首轮选定的 1 页）一直锁死。
        if parse_page_budget(notes):
            keep.update(page_min=new.page_min,page_max=new.page_max,explicit_pages=True)
        else:
            direction=parse_page_direction(notes)
            baseline=_page_baseline(old,platforms)
            adjusted=adjust_page_budget(old.get('form'),baseline[0],baseline[1],direction) if direction else None
            if adjusted:
                keep.update(page_min=adjusted[0],page_max=adjusted[1],explicit_pages=True)
        # 封面：只有用户明确提到才改；没提就沿用已有偏好。
        cover=parse_cover_request(notes)
        if cover is not None:keep['want_cover']=cover
        if parse_rank_count(notes,explicit_only=True):keep['rank_count']=new.rank_count
        if parse_caption_budget(notes):keep.update(caption_min=new.caption_min,caption_max=new.caption_max)
        elif density:
            # 旧稿里已经生效的字数下限若与新上限不冲突就留着，避免把用户之前
            # 明确写过的「文案至少 200 字」一起清掉。
            low,high=DENSITY_CAPTION[density]
            keep['caption_max']=high
            keep['caption_min']=keep.get('caption_min') if (keep.get('caption_min') or 0)<high else low
        if detect_form(requirements=notes)!='explainer':
            keep.update({k:getattr(new,k) for k in ['form','form_name','theme','framework','allowed_kinds','required_kinds','item_count']})
        new=CreativeBrief.model_validate(keep)
        # 旧稿里的页数句子先删掉再写当前生效的那一句：留着「全稿恰好1页」会把
        # 「增加页数」直接抵消，模型只会看到最具体的那条硬约束。
        notes=strip_density_notes(strip_page_budget_notes(old.get('original_requirements') or ''))+'\n本次调整：'+notes
        notes+='\n修改优先级：本次调整是最新要求，取代与它冲突的旧要求；旧版正文与提纲仅供定位，不得继续强加已取消的内容。'
        if new.explicit_pages and new.page_min and new.page_max:
            notes+='\n'+page_budget_note(new.page_min,new.page_max)
    if density:
        notes+='\n'+density_note(density)
        # Each new draft already contains earlier edits. Keep original constraints
        # frozen and apply only this adjustment, avoiding an ever-growing prompt.
    from ..services.content_recipes import apply_recipe
    # Changing form without an explicit template selects a compatible template.
    changed_form=bool(old and old.get('form')!=new.form)
    new=apply_recipe(new,direction=payload.direction or new.direction,template_id=payload.template_id or ('auto' if changed_form else new.template_id))
    return production.rebuild(content_id,production.RebuildInput(base_revision_id=payload.base_revision_id,request_id=payload.request_id,requirements=notes,
        run_mode=payload.run_mode,materials=payload.materials,media_ids=payload.media_ids,image_policy=payload.image_policy,creative_brief=new))
