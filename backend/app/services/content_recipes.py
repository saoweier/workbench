"""Subject policies and owned template choices, independent of content form."""
import re
from ..core.errors import ValidationFailed

DIRECTIONS = {
    'general': {'name':'通用内容','description':'按原题整理，明确事实、观点与建议'},
    'tech': {'name':'科技与 AI','description':'工具、技能与数码，核对名称、版本和数据口径'},
    'life': {'name':'生活与食养','description':'具体推荐、吃法与配图，营养数值注明单位和来源'},
    'games': {'name':'游戏','description':'按游戏版本、玩法和玩家场景组织攻略'},
    'news': {'name':'新闻与热点','description':'时间线、来源与事实核验，避免把热搜当成结论'},
    'work': {'name':'职场与办公','description':'按工作任务、适用对象和可执行步骤整理'},
    'learning': {'name':'学习与知识','description':'概念、例子、方法与适用边界'},
}
TEMPLATES = {
    'auto': {'name':'自动匹配','description':'依据内容形态选择版式','forms':[]},
    'rank_cards': {'name':'彩色排行卡片','description':'名次、图标、用途标签；有来源才展示数据','forms':['ranking'],'version':1},
    'category_table': {'name':'分类速查表','description':'分类色块＋名称＋主要功能，一页集中整理','forms':['directory'],'version':1},
    'illustrated': {'name':'图解手册','description':'关系图、流程、对比、配图与具体示例','forms':[],'version':4},
    'friendly_guide': {'name':'奶油绿速查手册','description':'奶油纸底、绿色大标题、小插画和紧凑信息行；第一张也给出完整答案','forms':[],'version':1},
    'editorial': {'name':'黑白编辑版','description':'大标题、清晰层次和少量重点色','forms':[],'version':1},
}
from .template_packages import builtins,TemplateStore
# Template metadata and rules share the package directory as their source.
TEMPLATES={k:v for k,v in TEMPLATES.items() if k=='auto'} | {k:{'name':p.name,'description':p.description,'forms':p.forms,'version':p.version} for k,p in builtins().items()}

def detect_direction(topic):
    text=topic.lower()
    if any(w in text for w in ('什么梗','啥梗','热梗','梗指南','网络梗')):return 'news'
    for key,words in [('tech',('github','skill','技能','ai','人工智能','数码','手机','mcp','软件','token')),
                      ('games',('游戏','原神','玩家','关卡','装备','攻略')),
                      ('life',('水果','营养','吃法','食谱','生活','旅行','家居')),
                      ('work',('办公','职场','求职','简历','会议','工作')),
                      ('learning',('学习','知识','读书','课程','考试')),
                      ('news',('新闻','热点','事件','热搜','诺贝尔','诺奖','获奖预测','颁奖'))]:
        if any(w in text for w in words):return key
    return 'general'

def apply_recipe(brief, *, direction='auto', template_id='auto'):
    if direction not in {'auto',*DIRECTIONS}:raise ValidationFailed('未知题材方向')
    available={p['id']:p for p in TemplateStore().catalog()}
    if template_id!='auto' and template_id not in available:raise ValidationFailed('未知内容模板')
    from .content_forms import FORMS,parse_rank_count
    if template_id!='auto' and available[template_id]['style'].get('design_family','legacy')=='legacy' and available[template_id]['renderer']=='category_table' and brief.form not in {'ranking','tutorial','comparison','review'}:
        form=FORMS['directory']
        brief.form=form.id;brief.form_name=form.name;brief.theme=form.theme
        brief.framework=list(form.structure);brief.allowed_kinds=list(form.allowed_kinds);brief.required_kinds=list(form.required_kinds)
        brief.rank_count=None
        brief.item_count=parse_rank_count(brief.original_requirements) or parse_rank_count(brief.original_topic)
    from .token_cost import is_token_cost
    price=is_token_cost(brief.original_topic)
    if template_id=='auto':template_id='friendly_guide' if price else {'ranking':'rank_cards','directory':'category_table'}.get(brief.form,'illustrated')
    if price:
        brief.framework=['第一页直接给出具体模型与情形的金额，不能只做装饰封面','按冻结单价计算输入、输出、缓存与混合用量；每个金额明确币种和时段','解释token计量、费用公式与会员/中转计费的区别；不能硬凑榜单']
        if not brief.explicit_pages:brief.page_min=1;brief.page_max=2
    package=available[template_id]
    allowed=package['forms']
    if allowed and brief.form not in allowed:
        raise ValidationFailed(f"{package['name']}不适合当前{brief.form_name}，请选择自动匹配或图解手册；不会改变你的题型。")
    if brief.form=='directory':brief.rank_count=None
    if brief.item_count and brief.form=='directory' and brief.item_count>16:
        raise ValidationFailed('分类速查表当前支持最多16项，请明确缩小数量；不会裁掉条目。')
    brief.direction=detect_direction(brief.original_topic) if direction=='auto' else direction
    brief.direction_name=DIRECTIONS[brief.direction]['name']
    brief.detected_from=brief.detected_from+'；模板 '+package['name']
    brief.template_id=template_id;brief.template_version=package['version'];brief.template_package=package
    return brief

def options():
    return {'directions':[{'id':'auto','name':'自动识别','description':'按选题采用对应题材技能'},*[{'id':k,**v} for k,v in DIRECTIONS.items()]],
            'templates':[{'id':'auto',**TEMPLATES['auto']},*[{k:p[k] for k in ['id','name','description','forms','version','renderer','package_version','style']} for p in TemplateStore().catalog()]]}

def validate_metrics(items,sources):
    """A displayed metric must occur alongside its object in a source record.

    This proves transcription, not source truth. Dates and scope remain visible
    and semantic review checks them; no model claim grants verified status.
    """
    by_id={s['id']:s for s in sources}
    for row in items:
        metric=row.get('metric_text');sid=row.get('metric_source_id')
        if not metric:
            if sid or row.get('metric_label'):raise ValidationFailed('无数据值时不能附数据标签或来源')
            continue
        if not sid or not row.get('metric_label') or sid not in by_id:
            raise ValidationFailed('展示数据必须有统计口径和可用来源；没有依据请不显示数值')
        source=by_id[sid]
        if source.get('access_state') not in {'ok','OK'}:raise ValidationFailed('数据来源不可读，不能展示指标')
        text=source.get('excerpt') or ''
        squash=lambda s:re.sub(r'\s+','',s).casefold()
        label=squash(row['label']);value=squash(metric)
        if not any(label in squash(line) and value in squash(line) for line in text.splitlines()):
            raise ValidationFailed(f"{row['label']}的数据未在同一条来源记录中找到；请补充原始数据或去掉数值")
    return True
