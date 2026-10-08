export const GUIDE_VERSION='20261006';
const step=(title,body,url,target,action,kind='learn')=>({title,body,url,target,action,kind});
export const LESSONS={
 create:{name:'做出第一篇图文',description:'选题 → 样式 → 制作 → 预览与调整',steps:[
 step('先确认文字模型已就绪','在「文字模型」里保存服务地址、API 和模型名称，并启用。文字、图片与语音分别配置；教程只检查配置，不会替你调用模型。','/views/ApiSettings.html','#t-name','打开模型设置','model'),
 step('从热榜选题，或写自己的题目','热榜来自 HotPush，可按板块和来源浏览。点击题目或手动输入，再补充你想要的效果。例如：做 TOP10，1～2 页，每项一句理由。榜单顺序不等于已核验的全网热度。','/views/Production.html?step=0','#topic-source-choices','去确定选题','topic'),
 step('选视觉风格，锁定数量与页数','选海报样式、信息密度、表达风格和页数。样式不改变题型：手绘也能做榜单，杂志也能做教程。需要真实数字或具体图片时，先提供原文和自己的配图。','/views/Production.html?step=1','#creator-templates','去选择样式','style'),
 step('核对要求，再开始制作','确认题目、页数和两个生成平台后，由你点击制作。真实模式会使用配置的模型。任务区展示调研、规划、生成、审核与出图；失败时先看原因，结果未知时不要重复提交。','/views/Production.html?step=2','#creator-start','去确认制作','produce'),
 step('放大图片，核对真正的内容','任务完成后打开成品，点击每张图放大阅读。检查数量、顺序、核心答案、数值与配图，也分别看抖音和小红书文案。审核未通过时先处理问题，再批准。','/views/ReviewPreview.html','#pages','去查看实际成品','preview'),
 step('不满意，就在这里继续调整','「内容再调整」可以更短、更详细、更活跃，也能输入自己的修改要求、补资料或换样式。会生成新版本；原数量、页数继续保留，除非明确要求修改。每次修改后重新预览和批准。','/views/ReviewPreview.html','#revision-studio','找到内容再调整'),
 step('满意后，批准当前平台版本','核对图片和发布文案，再批准当前平台稿，下载发布包。批准不会发布到平台；新版本不会继承旧版批准。模板规则与外观的修改在「样式工坊」，只影响后续任务。','/views/ReviewPreview.html','#btn-approve','去批准这一版','approve')
 ]},
 manual:{name:'手动发布与数据回访',description:'准备成品 → 本人发布 → 登记链接 → 填真实数据',steps:[
 step('先选择发布与回访方式','在「发布与回访」选择手动发布。你下载成品、在平台发布，再回来登记。回访方式可独立选择；后台自动回访默认关闭。','/views/PublishingHub.html','#choose-manual','打开发布方式'),
 step('选择要发布的成品','在手动发布页选内容和平台，复制标题、正文，下载已批准的图片包。视频可到视频编辑台下载 MP4。准备素材不等于已经发布。','/views/ManualPublishing.html','#manual-content','去准备素材'),
 step('发布完成，再登记实际链接','在自己的创作者后台上传素材并发布。回到这里填真实作品链接或编号，确认它对应当前选择的成品，再保存登记。教程不会替你点击平台的发布按钮。','/views/ManualPublishing.html','#manual-link','去登记已发布作品','publish'),
 step('保存这份作品的真实反馈','选择这份作品，填写实际后台播放、点赞、评论等指标。没拿到的数据留空；实际为零填 0。注明观察时间，勾选真实数据确认后保存，也可以导入平台报表或评论。','/views/ManualPublishing.html','#manual-publication','去保存一次回访','metrics'),
 step('从回访进入复盘','在评论与复盘中查看已有作品的数据与评论，再决定下一篇怎么改。登记、回访和效果评估是不同步骤；数据尚未更新时，等平台更新后补录。','/views/ReviewInsights.html','h1','打开评论与复盘')
 ]},
 assisted:{name:'抖音浏览器辅助',description:'扫码连接 → 准备发布页 → 本人确认与回访',steps:[
 step('连接自己的抖音账号','打开平台账号页，在专用窗口登录官方创作者后台，手机确认扫码。登录失效或出现平台验证时，由你处理后再继续。','/views/PlatformAccounts.html','h1','打开平台账号'),
 step('浏览器准备，你确认提交','在抖音发布页选已批准的成品，让浏览器上传和填写。先核对图片、文案与平台设置，再按页面要求逐条确认。教程不会创建发布任务或提交作品。','/views/DouyinPublishing.html','h1','打开抖音发布页'),
 step('按需回访，也可手动补录','作品发布后查看记录，按需读取单作品数据。平台数据尚未更新或读取受阻时，使用手动回访或报表导入；不要把缺失当成零。后台自动回访只有你开启后才运行。','/views/PublishingHub.html','#background-revisit','查看回访设置')
 ]},
 templates:{name:'维护模板与 Skill',description:'同题比较 → 修改规则与外观 → 诊断生产过程',steps:[
 step('用同一份内容比较视觉风格','样式工坊的「同题对照」让不同模板使用相同内容，比较布局、字体与图案。代表样例展示不同内容形态，不代表模板只能做那一类题。','/views/TemplateLibrary.html','#preview-mode','打开样式工坊'),
 step('一套样式，维护两部分','写作 Skill 规定写什么、怎么组织；配套模板控制颜色、字体和布局。编辑后先预览固定示例，再保存；也能复制搭建新模板。已有成品与进行中的任务保留冻结版本。','/views/TemplateLibrary.html','#template-instructions','查看规则与外观'),
 step('流程与题材规则在这里维护','技能页分别维护调研、规划、生成、审核、修改及不同题材的规则。先选择内容查看实际执行履历，定位哪一步出错，再修改对应 Skill。改 Skill 不会自动重做旧作品。','/views/SkillWorkflow.html','#skills','打开技能管理与诊断')
 ]},
 video:{name:'把图文做成讲解视频',description:'选图文 → 改讲稿与分镜 → 配音与导出',steps:[
 step('从已有图文开始','在视频编辑台选择图文，也可以从预览页点「转成讲解视频」。每页会整理成章节与逐句分镜，先看章节和讲稿是否完整。','/views/VideoStudio.html','#video-source','打开视频编辑台'),
 step('先看画面，再改讲解','逐句检查讲稿、关键词和画面，切换流程、对比或知识预设。可为某一句上传自有图片；图表需要原文数字。播放分镜是无声预览，不等于已生成配音视频。','/views/VideoStudio.html','#sentence-list','查看讲稿与分镜'),
 step('选声音与讲解员，再生成','选择在线配音、本机朗读或自己的语音 API，调整语速与讲解员。生成前核对方式，真实语音 API 可能收费；完成后下载 MP4、音频与字幕。教程不会替你生成视频。','/views/VideoStudio.html','#voice-engine','查看配音与角色设置')
 ]},
 diagnose:{name:'生成失败或内容不对',description:'看错误 → 核对原文 → 调整对应规则',steps:[
 step('先看任务停在哪一步','打开制作任务，查看调研、规划、生成或渲染的具体错误。资料不足先补来源，引用编号或布局错误查看诊断；存在结果未知的调用时先核查，不要反复点击重试。','/views/Production.html?pane=tasks','#runs','去看制作任务'),
 step('核对实际原文与生成记录','预览页「资料与生成诊断」显示原文读取情况及各阶段输入输出。热榜标题、网页链接和搜索摘要不能代替可读原文；引用匹配也不能证明来源本身可靠。','/views/ReviewPreview.html','#diagnostic-details','打开内容诊断'),
 step('补资料，再调整内容或 Skill','单篇问题在预览页补资料、修改要求并生成新版本。反复出现的写法问题，到技能管理修改对应阶段或题材规则；版式问题到样式工坊。原来的错误记录会保留。','/views/SkillWorkflow.html','#trace-content','查看执行履历')
 ]}
};
