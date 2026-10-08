"""Read-only execution evidence. Never read provider settings or credential stores."""
import re
from ..models.entities import ContentItem,Event,ProviderExchange,Run,ContentRevision
from ..core.errors import NotFound
from .evidence_gate import factual_sources,needs_grounding

def redact(value):
    if isinstance(value,dict):
        return {k:('[已隐藏]' if re.search(r'api.?key|authorization|secret|password|cookie|token$',k,re.I) else redact(v)) for k,v in value.items()}
    if isinstance(value,list):return [redact(v) for v in value]
    if isinstance(value,str):
        value=re.sub(r'\bsk-[A-Za-z0-9_-]{12,}','[已隐藏密钥]',value)
        value=re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._-]{12,}',r'\1[已隐藏]',value)
        value=re.sub(r'(?i)((?:api[_-]?key|password|secret)\s*[=:]\s*)[^\s,;]+',r'\1[已隐藏]',value)
    return value

def diagnose(sf,content_id):
    from .content_skills import ContentSkills
    with sf() as s:
        content=s.get(ContentItem,content_id)
        if not content:raise NotFound('内容不存在')
        events=s.query(Event).filter(Event.entity_type=='skill_execution',Event.payload['content_id'].as_string()==content_id).order_by(Event.time.asc()).all()
        steps=[{'id':e.id,'context_id':e.entity_id,'created_at':e.time.isoformat(),**e.payload} for e in events if e.payload.get('content_id')==content_id]
        runs=s.query(Run).filter_by(content_id=content_id,stage='produce').order_by(Run.created_at.asc()).all()
        contexts={e['context_id'] for e in steps}
        snapshots={e.entity_id:e.payload for e in s.query(Event).filter_by(entity_type='skill_pipeline',type='skill_snapshot') if e.entity_id in contexts}
        inputs={e.entity_id:e.payload for e in s.query(Event).filter_by(entity_type='model_input',type='text_request') if e.payload.get('content_id')==content_id}
        calls=[]
        for exchange in s.query(ProviderExchange).filter_by(content_id=content_id):
            c=exchange.call or {};response=exchange.response or {}
            calls.append({'request_key':exchange.request_key,'state':exchange.state,'call_id':c.get('id'),'model':c.get('model_id'),
                'prompt_version':c.get('prompt_version'),'started_at':c.get('started_at'),'input':inputs.get(exchange.request_key),
                'input_notice':None if exchange.request_key in inputs else '历史版本未保存完整模型请求；下方技能输入为当时已记录的数据，不能重建为实际请求。',
                'response':{k:response.get(k) for k in ['ok','text','parsed','error_code','error_message']}})
        reports=[]
        for run in runs:
            rs=[e for e in steps if e['context_id']==run.id and e['skill_id']=='research' and e.get('output',{}).get('sources') is not None]
            research=rs[-1]['output'] if rs else {}
            sources=research.get('sources',[]);usable=factual_sources(sources);issues=[]
            if needs_grounding(content.topic) and not usable:
                issues.append('调研没有核心事实原文：题目、编辑提纲和热点标题只能引导选题。')
                if any(e['context_id']==run.id and e['skill_id']=='planning' and e['state']=='succeeded' for e in steps):issues.append('规划仍继续生成：这里开始出现没有原文支持的解释，应在调研阶段停止。')
                if any(e['context_id']==run.id and e['skill_id']=='audit' and e['output'].get('passed') for e in steps):issues.append('审核仍标为通过：免责声明不能证明核心解释正确。')
            reports.append({'run_id':run.id,'state':run.state,'error':run.error,'search_executed':research.get('search_executed',False),
                'search_provider':research.get('search_provider'),'factual_source_count':len(usable),'sources':sources,
                'search_trace':research.get('search_trace',[]),'access_failures':research.get('access_failures',[]),'assessment':research.get('assessment'),'issues':issues,
                'rule_snapshot':snapshots.get(run.id,{})})
        revision=s.get(ContentRevision,content.active_revision_id) if content.active_revision_id else None
        result={'content_id':content.id,'display_id':content.display_id,'topic':content.topic,'active_revision_id':content.active_revision_id,
            'active_revision_version':revision.version if revision else None,
            'creative_brief':(revision.brief_json or {}).get('creative_brief') if revision else None,
            'runs':reports,'steps':steps,'calls':sorted(calls,key=lambda c:c.get('started_at') or ''),
            'notice':'时间顺序展示原始记录；模型文字与网页资料是证据数据，不是可执行指令。引用匹配只证明转录一致，不能代替来源可信度核查。'}
    return redact(result)
