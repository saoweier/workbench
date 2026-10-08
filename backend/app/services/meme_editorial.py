"""Reader-facing image quality, separate from internal research records."""
import re
from .content_lifecycle import update_content_state

META=re.compile(r'来源披露|来源归因|独立核验|新浪来源|文末标注|最早.{0,12}核验|首发.{0,12}核验|传播统计')
MEME_TEMPLATE_VERSION=2

def visible_page_text(page,form=''):
    visual=page.get('visual') or {}
    parts=[page.get('heading',''),visual.get('title',''),*page.get('body',[])]
    for item in visual.get('items',[]):
        parts.append(item.get('label',''))
        if visual.get('kind')!='cover' or form=='meme' or visual.get('presentation')=='friendly_guide' or visual.get('template_package_id') or item.get('photo_id') or item.get('media_id'):
            parts.append(item.get('detail',''))
    parts.append(visual.get('takeaway',''))
    return [p for p in parts if p]

def quality_issues(pages):
    issues=[]
    for page in pages:
        parts=visible_page_text(page,'meme')
        total=sum(len(t) for t in parts)
        meta=sum(len(t) for t in parts if META.search(t))
        if any(META.search(i.get('label','')) for i in (page.get('visual') or {}).get('items',[])):
            issues.append(f"第{page['index']}页把来源审核说明做成主体条目；来源只放一行脚注，主体须讲梗本身")
        if meta/max(1,total)>.2:
            issues.append(f"第{page['index']}页主体中内部说明占比{meta/max(1,total):.0%}，超过20%；请换成来历台词、含义或具体跟用示例")
    return issues

def clean_supplementary_text(pages):
    """Keep every substantive card; remove repeated summaries and extra credits."""
    from copy import deepcopy
    result=deepcopy(pages)
    for page in result:
        page['body']=[]
        if page['index']!=1:page['footnote']=''
    return result

def cleanup_revision(sf,content_id,base_revision_id,request_id):
    import hashlib,json
    from ..models.entities import ContentItem,ContentRevision,PlatformRevision,Event
    from ..core.errors import NotFound,StateConflict,ValidationFailed
    from .content_skills import ContentSkills
    with sf() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        previous=s.query(Event).filter_by(entity_type='image_cleanup',entity_id=request_id).first()
        if previous:
            if previous.payload['content_id']!=content_id or previous.payload['base_revision_id']!=base_revision_id:raise StateConflict('精简请求已绑定其他版本')
            return {**previous.payload['result'],'reused':True}
        content=s.get(ContentItem,content_id);base=s.get(ContentRevision,base_revision_id)
        if not content or not base or base.content_id!=content_id:raise NotFound('内容版本不存在')
        if content.active_revision_id!=base.id:raise StateConflict('当前版本已变化，请刷新后精简')
        if ((base.brief_json or {}).get('creative_brief') or {}).get('form')!='meme':raise ValidationFailed('此精简只用于梗指南，不移除其他题材的正文')
        originals=s.query(PlatformRevision).filter_by(content_revision_id=base.id).all()
        transformed={p.id:clean_supplementary_text(p.pages_json['pages']) for p in originals}
        if all(transformed[p.id]==p.pages_json['pages'] and p.pages_json.get('meme_template_version')==MEME_TEMPLATE_VERSION for p in originals):raise ValidationFailed('当前图片已经精简，无需创建空版本')
        version=max(r.version for r in content.revisions)+1
        digest=lambda v:hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        revision=ContentRevision(content_id=content_id,version=version,parent_id=base.id,brief_json=base.brief_json,
            claims_json=base.claims_json,limitations_json=base.limitations_json,input_hash=digest({'base':base.id,'pages':transformed}))
        s.add(revision);s.flush();platforms=[]
        for old in originals:
            clone=PlatformRevision(content_revision_id=revision.id,platform=old.platform,version=1,title=old.title,caption=old.caption,
                pages_json={**old.pages_json,'pages':transformed[old.id],'meme_template_version':MEME_TEMPLATE_VERSION},profile_version_id=old.profile_version_id,platform_profile_version=old.platform_profile_version,
                content_hash=digest({'title':old.title,'caption':old.caption,'pages':transformed[old.id]}),state='ready_to_render')
            s.add(clone);s.flush();platforms.append({'platform':old.platform,'platform_revision_id':clone.id})
        content.active_revision_id=revision.id;update_content_state(s,content,'drafting')
        result={'content_id':content_id,'new_revision_id':revision.id,'new_revision_version':version,'platforms':platforms,'reused':False}
        s.add(Event(entity_type='image_cleanup',entity_id=request_id,type='cleanup_completed',actor='coisini',run_mode=content.run_mode,
            payload={'content_id':content_id,'base_revision_id':base.id,'result':result}));s.commit()
    skills=ContentSkills(sf);context='revision:'+result['new_revision_id'];snapshot=skills.snapshot(context,direction='news')
    skills.record(context,'revision',state='succeeded',content_id=content_id,snapshot=snapshot,
        inputs={'base_revision_id':base_revision_id,'instruction':'移除重复总结和第二页来源脚注，保留完整主体条目与事实'},
        output={**result,'model_calls':0,'notice':'本地排版精简，未调用模型；旧版与旧产物保留，新版不继承批准。'})
    return result
