"""Models provide editorial rows; local code owns pages, icons and rank numbers."""
from pydantic import BaseModel,ConfigDict,Field
from typing import Literal
from .content_recipes import validate_metrics
from ..core.errors import ValidationFailed

IconId=Literal['question','idea','source','page','check','pencil','search','brain','code','globe','chart','briefcase','game','fruit','news','palette']

class EditorialRow(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    label:str=Field(min_length=1,max_length=80)
    detail:str=Field(min_length=1,max_length=48)
    icon:IconId='page'
    category:str=Field(default='',max_length=12)
    tags:list[str]=Field(default_factory=list,max_length=3)
    metric_text:str|None=Field(default=None,max_length=18)
    metric_label:str|None=Field(default=None,max_length=16)
    metric_source_id:str|None=Field(default=None,max_length=80)

def compile_rows(words,*,strategy,brief,claim_ids,sources,ranking,verify_objects=False,cover=True):
    from copy import deepcopy
    import re
    words=deepcopy(words)
    rows=words['items'];wanted=brief.get('rank_count') if ranking else brief.get('item_count')
    if ranking:
        for n,row in enumerate(rows,1):
            # Remove only a redundant generated ordinal matching this row's
            # program-owned rank, never digits inside the actual title.
            row['label']=re.sub(r'^0?'+str(n)+r'(?:[.、）)]\s*|\s+)', '',row['label']).strip()
    if wanted and len(rows)!=wanted:raise ValidationFailed(f'需要完整{wanted}项，不能增减对象')
    if not 1<=len(rows)<=(12 if ranking else 16):raise ValidationFailed('条目数超出版式容量')
    if len({r['label'].strip().casefold() for r in rows})!=len(rows):raise ValidationFailed('存在重复对象')
    for row in rows:
        if any(not t.strip() or len(t)>10 for t in row.get('tags',[])):raise ValidationFailed('用途标签最多10字，不能为空')
        if not ranking and not row.get('category','').strip():raise ValidationFailed('分类速查表每项必须有类别')
    validate_metrics(rows,sources)
    if verify_objects:
        from .evidence_gate import factual_sources
        import unicodedata,json
        squash=lambda text:''.join(c for c in text if not c.isspace() and unicodedata.category(c)!='Cf').casefold()
        usable=factual_sources(sources)
        evidence=[squash(s.get('excerpt') or '') for s in usable]
        missing=[r['label'] for r in rows if not any(squash(r['label']) in text for text in evidence)]
        if missing:raise ValidationFailed('资料未包含这些具体对象：'+ '、'.join(missing)+'；请补充资料，不能用模型猜测补齐')
        if ranking:
            for source in usable:
                text=source.get('excerpt') or '';start=text.find('{')
                if start<0:continue
                try:board,_=json.JSONDecoder().raw_decode(text[start:])
                except (ValueError,TypeError):continue
                if not isinstance(board,dict) or board.get('order_basis')!='HotPush聚合返回顺序':continue
                listed=board.get('items')
                if not isinstance(listed,list) or len(listed)<len(rows) or not all(isinstance(i,dict) and isinstance(i.get('title'),str) for i in listed):continue
                if [squash(r['label']) for r in rows]!=[squash(i['title']) for i in listed[:len(rows)]]:
                    raise ValidationFailed('HotPush快照榜单的对象或顺序发生变化，不能擅自替换、重排指定榜单')
    if not ranking:
        # Grouping is deterministic. The model cannot split one category into
        # separate blocks; within each category, supplied item order is retained.
        groups={}
        for row in rows:groups.setdefault(row['category'],[]).append(row)
        rows=[row for group in groups.values() for row in group]

    # A multi-page overview uses every page for rows, never adds a redundant cover.
    # `cover=False`（抖音）不设封面版式：名次/分类版面整页铺开，第一页也是内容页。
    count=max(strategy['min_pages'],min(2,strategy['max_pages']))
    if count>len(rows):raise ValidationFailed('页面比条目更多，不能以空页凑篇幅')
    pages=[]
    for group in range(count):
        start=len(rows)*group//count;end=len(rows)*(group+1)//count
        items=[{**row,**({'rank':start+i+1} if ranking else {})} for i,row in enumerate(rows[start:end])]
        pages.append({'index':group+1,'layout':'cover' if (cover and group==0) else 'checklist',
            'heading':words['title'],'kicker':'完整名次' if ranking else '分类速查',
            'body':[words['order_note']] if group==0 else [],
            'footnote':words['source_note'] if group==0 else '', 'claim_ids':list(claim_ids),
            'visual':{'kind':'rank' if ranking else 'catalog','title':words['title'],
                      'items':items,'takeaway':words['takeaway'],'presentation':brief.get('template_id','illustrated'),
                      'presentation_version':brief.get('template_version',1)}})
    return pages
