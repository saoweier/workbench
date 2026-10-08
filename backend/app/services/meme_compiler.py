"""A two-page meme guide: context/quote/meaning, then formula and examples.

The model writes text; the application owns card counts, pages and citations.
This avoids demanding five cards from a four-card example layout.
"""
from pydantic import BaseModel,ConfigDict,Field
from .catalog_compiler import IconId

def requested_examples(requirements):
    import re
    numbers={'一':1,'两':2,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
    matches=list(re.finditer(r'(\d{1,2}|[一两二三四五六七八九十])\s*[条个]\s*(?:完整)?(?:原创)?(?:例句|示例)',requirements or ''))
    if not matches:return 3
    value=matches[-1].group(1)
    return int(value) if value.isdigit() else numbers[value]

class MemeRow(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    label:str=Field(min_length=1,max_length=14)
    detail:str=Field(min_length=1,max_length=64)
    icon:IconId='page'

class MemeText(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    title:str=Field(min_length=1,max_length=20)
    caption:str=Field(min_length=1,max_length=2200)
    first_heading:str=Field(min_length=1,max_length=24)
    second_heading:str=Field(min_length=1,max_length=24)
    source_note:str=Field(min_length=1,max_length=40)
    first_takeaway:str=Field(min_length=1,max_length=56)
    second_takeaway:str=Field(min_length=1,max_length=56)
    context:MemeRow
    classic_quote:MemeRow
    meaning:MemeRow
    formula:MemeRow
    examples:list[MemeRow]=Field(min_length=3,max_length=3)

def compile_meme(words,claim_ids):
    pages=[]
    for n,(heading,rows,takeaway) in enumerate([
        (words['first_heading'],[words['context'],words['classic_quote'],words['meaning']],words['first_takeaway']),
        (words['second_heading'],[words['formula'],*words['examples']],words['second_takeaway'])],1):
        pages.append({'index':n,'layout':'cover' if n==1 else 'checklist','heading':heading,
            'kicker':'看懂这个梗' if n==1 else '跟用小抄','body':[],
            'footnote':words['source_note'] if n==1 else '', 'claim_ids':list(claim_ids),
            'visual':{'kind':'cover' if n==1 else 'example','title':'剧情、台词、含义' if n==1 else '一句公式，三个例句',
                'items':rows,'takeaway':takeaway}})
    return {'title':words['title'],'caption':words['caption'],'pages':pages}
