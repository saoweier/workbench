"""A template is a versioned pair: content SKILL + safe local layout settings."""
import hashlib,json,re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
from sqlalchemy import create_engine,inspect
from sqlalchemy.orm import sessionmaker
from ..core.config import get_settings
from ..core.errors import NotFound,StateConflict,ValidationFailed
from ..models.entities import Event

PACKAGE_ROOT=Path(__file__).resolve().parents[1]/'skills'/'templates'
RendererId=Literal['illustrated','rank_cards','category_table','editorial','friendly_guide']

class TemplateStyle(BaseModel):
    model_config=ConfigDict(extra='forbid')
    background:str=Field(default='#fff8e9',pattern=r'^#[0-9a-fA-F]{6}$')
    ink:str=Field(default='#253d30',pattern=r'^#[0-9a-fA-F]{6}$')
    accent:str=Field(default='#317451',pattern=r'^#[0-9a-fA-F]{6}$')
    soft:str=Field(default='#e1e8cf',pattern=r'^#[0-9a-fA-F]{6}$')
    heading_size:int=Field(default=68,ge=48,le=76)
    body_size:int=Field(default=29,ge=24,le=34)
    row_spacing:int=Field(default=18,ge=10,le=24)
    radius:int=Field(default=24,ge=0,le=32)
    heading_font:Literal['system','handwritten','serif','mono','display']='system'
    decoration:Literal['simple','rich']='simple'
    # Missing on historical snapshots: retain their original renderer exactly.
    design_family:Literal['legacy','handdrawn','magazine','neon','collage','data']='legacy'

    @model_validator(mode='after')
    def readable_colours(self):
        def luminance(hex):
            values=[int(hex[i:i+2],16)/255 for i in (1,3,5)]
            linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in values]
            return sum(v*w for v,w in zip(linear,[.2126,.7152,.0722]))
        for fg,bg in [(self.ink,self.background),(self.accent,self.background),(self.ink,self.soft)]:
            a,b=sorted([luminance(fg),luminance(bg)])
            if (b+.05)/(a+.05)<4.5:raise ValueError('字色与底色过于接近，请选更深的字色或更浅的底色')
        return self

class TemplatePackage(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:str=Field(pattern=r'^[a-z][a-z0-9_]{1,31}$')
    name:str=Field(min_length=2,max_length=36)
    description:str=Field(min_length=4,max_length=200)
    renderer:RendererId
    version:int=Field(default=1,ge=1,le=99)
    forms:list[Literal['ranking','directory','listicle','tutorial','comparison','review','guide','meme','explainer']]=Field(default_factory=list,max_length=9)
    instructions:str=Field(min_length=20,max_length=8000)
    style:TemplateStyle=Field(default_factory=TemplateStyle)

    @model_validator(mode='after')
    def engine_contract(self):
        if self.id=='auto':raise ValueError('auto是自动选择，不是可编辑模板')
        if self.style.design_family=='legacy':
            if self.renderer=='rank_cards' and self.forms!=['ranking']:raise ValueError('旧排行渲染器只适用于ranking')
            if self.renderer=='category_table' and self.forms!=['directory']:raise ValueError('旧分类表渲染器只适用于directory')
        return self

def fingerprint(package):
    data=package.model_dump(mode='json') if hasattr(package,'model_dump') else package
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:16]

def builtins():
    result={}
    for folder in sorted(PACKAGE_ROOT.iterdir()):
        if not folder.is_dir() or not (folder/'template.json').exists():continue
        data=json.loads((folder/'template.json').read_text(encoding='utf-8'))
        if data['id']!=folder.name:raise ValidationFailed('模板目录与id不一致：'+folder.name)
        data['instructions']=(folder/'SKILL.md').read_text(encoding='utf-8')
        result[data['id']]=TemplatePackage.model_validate(data)
    return result

@lru_cache(maxsize=4)
def factory(url):
    return sessionmaker(bind=create_engine(url,future=True),future=True)

class TemplateStore:
    def __init__(self,sf=None):self.sf=sf or factory(get_settings().database_url)

    def catalog(self):
        values={k:{**v.model_dump(mode='json'),'origin':'built_in'} for k,v in builtins().items()}
        with self.sf() as s:
            if inspect(s.get_bind()).has_table('event'):
                for event in s.query(Event).filter_by(entity_type='template_package',type='template_saved').order_by(Event.time.asc()):
                    package=TemplatePackage.model_validate(event.payload['package'])
                    values[package.id]={**package.model_dump(mode='json'),'origin':'workspace_override'}
        return [{**v,'package_version':fingerprint({k:val for k,val in v.items() if k!='origin'})} for v in values.values()]

    def get(self,id):
        found=next((p for p in self.catalog() if p['id']==id),None)
        if not found:raise NotFound('模板不存在')
        return found

    def save(self,package,expected_version=None):
        package=TemplatePackage.model_validate(package)
        with self.sf() as s:
            s.connection().exec_driver_sql('BEGIN IMMEDIATE')
            old=s.query(Event).filter_by(entity_type='template_package',entity_id=package.id,type='template_saved').order_by(Event.time.desc()).first()
            original=old.payload['package'] if old else builtins().get(package.id)
            if original is None:
                if expected_version is not None:raise StateConflict('模板不存在，不能修改旧版本')
            elif expected_version!=fingerprint(original):raise StateConflict('模板已被修改，请刷新后再保存；新模板请使用新的标识')
            s.add(Event(entity_type='template_package',entity_id=package.id,type='template_saved',actor='user',run_mode='local_seed',payload={'package':package.model_dump(mode='json')}));s.commit()
        return self.get(package.id)

    def versions(self,id):
        result=[];original=builtins().get(id)
        if original:result.append({**original.model_dump(mode='json'),'package_version':fingerprint(original),'origin':'built_in'})
        with self.sf() as s:
            for event in s.query(Event).filter_by(entity_type='template_package',entity_id=id,type='template_saved').order_by(Event.time.desc()):
                p=event.payload['package'];result.append({**p,'package_version':fingerprint(p),'origin':'workspace_override'})
        if not result:raise NotFound('模板不存在')
        return result

def style_css(style):
    """Only typed hex colours and bounded numbers reach the renderer."""
    s=TemplateStyle.model_validate(style)
    font={'handwritten':'"STKaiti","KaiTi","Comic Sans MS",serif','serif':'Georgia,"SimSun",serif','mono':'Consolas,"Microsoft YaHei",monospace','display':'"SimHei","Microsoft YaHei",sans-serif'}.get(s.heading_font,'inherit')
    return f'''.visual-sheet{{background-color:{s.background};color:{s.ink};--paper:{s.background};--ink:{s.ink};--accent:{s.accent};--soft:{s.soft};--title-font:{font};--title-size:{s.heading_size}px;--corner:{s.radius}px;--poster-body:{s.body_size}px;--poster-gap:{s.row_spacing}px}}
    .v-header h1,.v-header h2{{font-size:{s.heading_size}px;color:{s.accent}}}
    .v-header h1,.v-header h2,.poster-label,.poster-panel-title{{font-family:{font}}}
    .fg-copy p,.ed-copy p{{font-size:{s.body_size}px;color:{s.ink}}}
    .poster-detail{{font-size:var(--poster-body);color:{s.ink}}}.poster-panel[class*="poster-count-1"] .poster-detail{{font-size:calc(var(--poster-body) - 4px)}}
    .poster-row{{column-gap:var(--poster-gap)}}.fg-row{{gap:{s.row_spacing}px}}.fg-panel,.meme-card,.ed-row,.poster-panel{{border-radius:{s.radius}px}}
    .fg-badge,.v-kicker{{background:{s.accent};color:{s.background}}}
    .fg-columns b,.visual-sheet .v-takeaway{{background:{s.soft};color:{s.ink}}}'''
