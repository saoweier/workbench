"""Representative samples and identical-content comparison; never live rankings."""
def example_form(page):
    return {'rank':'ranking','catalog':'directory','flow':'tutorial','compare':'comparison'}.get(page['visual']['kind'],'explainer')

def example_page(template_id,package=None,mode='representative'):
    from .template_packages import TemplateStore
    package=package or TemplateStore().get(template_id)
    from .template_packages import fingerprint
    if 'package_version' not in package:package={**package,'package_version':fingerprint(package)}
    family=package['style'].get('design_family','legacy')
    common=[('Token','模型处理文字的计量单位；输入与输出通常分别计费。','brain'),
            ('Prompt','给AI的指令。说清目标、材料和限制，比只说“帮我写”更有效。','pencil'),
            ('Agent','能拆任务、调用工具并推进步骤的AI；关键动作仍要设定权限。','code'),
            ('RAG','先检索资料，再结合资料回答；保留出处，方便核对。','source')]
    rows=common;kind='map';title='4个AI概念，一次搞懂';section='名称 · 大白话 · 用处'
    takeaway='先把需求说清，再核对答案；学会一个概念就试一个小任务。'
    if mode=='representative' and family=='magazine':
        title='AI写作，两种做法';section='直接起草 vs 带资料改写';kind='compare'
        rows=[('直接起草','适合头脑风暴和粗稿。先说对象与目的，得到结构后再补事实。','idea'),('带资料改写','适合有事实依据的成稿。提供原文，要求保留数字、名称与出处。','source')]
        takeaway='想灵感就先起草，想准确就给资料；最后都要亲自核对。'
    elif mode=='representative' and family=='neon':
        title='把一个想法做成作品';section='从输入到交付的四步';kind='flow'
        rows=[('明确主题','一句话写清：给谁看，解决什么问题，读完能带走什么。','question'),('调研取材','查找具体资料，留下链接和日期，区分事实与观点。','search'),('编排内容','先给答案，再放解释与例子；按信息量决定图表或分镜。','palette'),('检查交付','核对事实、文字和图片，预览手机效果后再准备发布。','check')]
        takeaway='每一步留下一份可检查的结果，出问题就回到对应环节。'
    names=[('信息调研','资料检索','汇集来源，保留引用','search'),('信息调研','热点观察','区分当日热点与统计趋势','chart'),
           ('信息调研','关系图解','把对象关系整理成图','brain'),('知识整理','笔记归纳','按主题整理已有资料','source'),
           ('知识整理','学习卡片','概念配一个简短例子','idea'),('办公任务','文档阅读','提取结构与待办事项','page'),
           ('办公任务','会议整理','区分决议与待确认事项','briefcase'),('办公任务','演示提纲','按照目标组织讲解顺序','pencil'),
           ('办公任务','表格检查','核对字段、单位和空值','check'),('视觉创作','图像构思','先写用途，再定画面','palette'),
           ('视觉创作','视频讲解','把要点拆成讲解镜头','globe'),('视觉创作','排版预览','检查层次、留白与可读性','code'),
           ('发布复盘','发布准备','整理文案、页图与来源','news'),('发布复盘','数据记录','保留平台原始统计口径','chart'),
           ('发布复盘','下一轮调整','依据问题提出修改要求','pencil')]
    if mode=='representative' and family in {'collage','data','legacy'}:
        catalog=family=='data' or package['renderer']=='category_table';count=15 if catalog else 10
        kind='catalog' if catalog else 'rank';rows=names[:count]
        title='15个创作环节速查' if catalog else '创作方法TOP10 · 示例'
        section='按任务分类查找' if catalog else '演示顺序，不代表热度排名'
        takeaway='找到对应任务，再选择方法；先整理事实，再决定怎么表达。'
    items=[]
    for n,row in enumerate(rows):
        if len(row)==3:label,detail,icon=row;category=''
        else:category,label,detail,icon=row
        item={'label':label,'detail':detail,'icon':icon}
        if kind=='catalog':item['category']=category
        if kind=='rank':item['rank']=n+1
        items.append(item)
    page={'index':1,'layout':'cover' if kind in {'rank','catalog'} else 'checklist','heading':title,'kicker':'版式示例 · 演示内容',
          'body':['固定示例，只展示排版与视觉风格。'],
          'footnote':'模板演示 · 非实时排行或平台数据','claim_ids':[],
          'visual':{'kind':kind,'title':section,'items':items,'takeaway':takeaway,
                    'presentation':package['renderer'],'presentation_version':package['version'],
                    'template_style':package['style'],'template_package_id':package['id'],'template_package_version':package['package_version']}}
    return page
