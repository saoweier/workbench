# content-workbench 项目备忘（长期）

## 架构速览
- 6 段 skill 流水线：discovery → selection → planning → generation → audit → revision（`backend/app/services/content_skills.py`）。
- 生产编排：`production_service.py`（produce / illustrate）→ `compose_service.py`（母稿 + 双平台改写 + 规则稿 + 校验）→ `visual_content.py`（图解版式）→ `renderer.py`（Playwright 出图）。
- 确定性离线 fixture：`backend/app/services/adapters/fixture.py`。**注意**：它按 schema 的 `minItems` 决定生成数组长度。

## 硬约定（勿违反）
1. **用户显式要求必须结构化落地**：页数 / 题材形态 / 榜单条目数等必须写进 schema 与校验，不能只拼进 prompt。
2. **不同题材必须不同模板**：每个题材有独立主题色与框架，禁止一套走天下。见 `content_forms.py` 的 `FORMS` / `FORM_THEMES`。
3. 结构性页数校验用 `content_forms.build_brief()` 产出的 `CreativeBrief`，不要在 `compose_service.py` 里硬编码页数。
4. 改 schema 页数下界时**不要动全局 `minItems`**（会打乱 fixture）；用 `compose_service._with_pages(schema, lo, hi)` 仅在带 brief 时动态覆盖。
5. 规则稿**不得编造**：条目数不足时直接报错。
6. **限流只拦无人值守路径**：待预览库存上限只作用于 `topic_service.select`（自动选题）；人主动的引导式创作（`api/creation.py`）不拦。
7. **待预览库存口径** = `topic_service.PENDING_STATES` = `{ready_for_review, checking}`。`approved`/`exported` 不占名额（否则"先处理已有内容"永不生效）。
8. **不要物理删除内容**：要下线用 `PipelineService.discard()` → 终态 `discarded`（保留记录），接口 `POST /contents/{id}/discard`。已登记发布的内容拒绝丢弃。
9. 新增内容状态时记得在 `frontend/src/assets/app.js` 的 `STATE_MAP` 补中文标签，否则列表里显示原始英文状态。
10. **关闭 run 时必须写 `run.error`**：`ProductionService._close_run(run, state, *, blocked_stage=None, error=None)`。只把失败原因塞进 `Job.output_refs` 会让 `run.error` 永空，前端只能显示"任务暂停或已取消"这种误导文案（用户会以为是被取消了）。前端 `creation-guide.js` 已做 `run.error → run.jobs[].error` 回退。
11. **失败原因只进 `out["error"]` 不够**：`production_service._block()` 的失败路径会同时落 `run.error` + `blocked_stage`。
12. **未配搜索 Provider 时，需要真实数据的选题（榜单 TOP10 / 星标 / 价格）会在 planning 被防编造闸门拦下**（`plan.blocking_gaps` → `ValidationFailed`，run 标 failed/blocked_stage=planning）。这是设计行为，不是崩溃；提前提示见 `/creation/options` 的 `search_ready`。
13. **前端读 `activeRev()` / `find()` 的结果前必须判空**：内容可能一条 revision 都没有（生成中途停下）。`ReviewPreview.html` 用 `requireRev()` 抛可读原因，并用 `syncRevisionControls()` 在无版本时**禁用**调整入口 + 写明原因；不要把"入口可点、点了才报 TypeError"留给用户。
14. **"内容从没产出过版本就中止"时要把 `content_item.state` 置为 `blocked`**（`production_service._block()`），否则它一直显示"排队中"却没有任何版本。已发布/已有版本的内容不要被重跑失败改状态。
15. **平台差异只在 `services/platform_policy.py` 一处定义**：小红书 `cover=required`（必须有封面页且封面给核心信息）、抖音 `optional`（可无封面、首页直接给内容）。四处共用：生成提示词 / 平台稿校验 `cover_problem` / 规则改写 / 榜单编译 `compile_rows(cover=…)`。**禁止**在 Skill 说明或 compose 里另写一份封面规则。
16. **"提示词写了但程序没拦"的规则一律视为没写**：容易数出来的成品要求（榜单项数、名次顺序、指标对应项目、导出图片张数、nutrition 每页上限）必须落进 `services/acceptance.py` 或对应 schema 校验。已踩过的坑：提示词写着"每页 nutrition 最多三种"，`VisualSpec` 却没实现，`fruit_quality` 一直是红的。
17. **榜单契约单一来源是 `acceptance.rank_contract`**，`compose_service._valid_rank_layout` 只是薄封装；别在 compose 里再写一套名次校验。榜单条目数随稿冻结在 `PlatformRevision.pages_json.rank_count`，修复/交付阶段据此复算。
18. **导出类错误码（`IMAGE_COUNT_MISMATCH` / `PAGE_SEQUENCE_INCOMPLETE`）属环境问题**，已在 `quality_rules.UNREPAIRABLE_CODES`，不要丢进模型修复循环。
19. **交付物键名以 `deliverables.DELIVERABLE_KEYS` 为单一来源**：`evals.json` 与测试都引用它；导出包 `manifest.json`/`checklist.md` 必须带同一份清单（导出的是"这一版产出了什么"，不是一堆图）。
20. **前端降级要求**：`content-diagnostics.js` 的交付物清单在接口报错或返回非清单结构时要降级显示原因，不能 `deliverables.acceptance.passed` 直接取属性（桩数据缺路径会炸页面）。
21. **改导航条 `NAV` 必须同步 `test_browser.py` 的 `views` 列表**（断言 `#nav a` 数量 == len(views)），并给新页面在 `ready` 映射里加挂载标志。曾因为只加 QueryLab 没同步测试，整个 browser 阶段在第一条断言就挂。
22. **技能入口块是路由的机器可读来源**：7 个流程 `SKILL.md` 顶部必须有 `【何时使用】/【需要输入】/【交给谁】`；`content_skills.stage_entry_contract()` 解析，`evals.json` + `routing_cases()` 提供"用户说法 → 预期路由与交付物"用例，由 `content_evals` 阶段逐条断言真实 `build_brief/apply_recipe` 结果。
23. **`evals.json` 的断言对象是真实路由**（form/direction/rank_count/页数预算/template_id/交付物键名），不是提示词文本；改路由先让用例失败，而不是悄悄走偏。
24. **"来源没有正文"一律修数据，不放松闸门**：`decide` 批准真实内容要过 `require_evidence`。demo 种子原来只声明 `local_planning_document` 的 `path`/`locator` 而没有 `excerpt`，于是被正确拦下。已改为 `excerpt`（`source-notes.md` 的 `## DEMO-00n` 段）+ `access_state=ok` + `excerpt_basis=local_file`，并由 `scripts/seed_demo.py` 重建整个 demo 包。**重建后必须复核 `scripts/check_release.py` 的期望**：66 图 / 6 发布包 / publication 6 / metric_snapshot 8 / comment_sample 16 / review_report 2 / topic_feedback 2 / `integrity_check=ok` / 外键无违规。
25. **fixture 必须能产出语义合法的内容**：`adapters/fixture.py` 的 `_resolve()` 解析 `$ref` / `anyOf`（pydantic 嵌套模型与可选字段写法），`_value_for` ↔ `_synth_for_schema` 之间要贯穿 `root`（含 `$defs`）。`ResearchAssessment` 有专门分支：`_prompt_sources()` 从提示词"本次输入（仅作为数据）："段回读来源，用真实 `excerpt` 首行当 `quote`（否则 `validate_research` 必挂），无正文时 `can_answer=False` + `blocking_gaps`；梗题材给 `origin`+`meaning`。
26. **断言不要只扫 HTML 文本**：前端是「HTML 外壳 + 独立 JS 模块」，`Production.html` 只挂 `/assets/production.js`。`test_p5_e2e.py` 的"真的发起后端请求"用 `page_sources()` 跟随 `<script src>` 与 `from '/assets/…'` 的 import 图（记得剥 `?v=` 查询串）。
27. **前端模块是动态 import，点击前要等挂载**：`app.js` 用 `import("/assets/guidance.js")` 异步加载，而 `#open-tutorial` 是静态按钮 → 点击可能早于事件绑定。`guidance.js` 的 `setupGuidance()` 里"创建 `#guide-hub`"与"绑定点击"在同一段同步代码内，所以 `expect(#guide-hub).to_be_attached()` 是可靠的就绪信号。**同类"单独跑能过、全量跑挂"的失败优先怀疑这种竞态。**
28. **真实模式默认要求可读证据，隔离测试要自己给资料**：`needs_grounding()` 对非纯创作题几乎都返回 True，没配搜索 Provider 时直创会被拦（报"补充搜索未配置，请在API设置中配置SearXNG"）——**这是设计行为，不是崩溃**。测试要跑通直创就在 `#creator-materials` 填一段 ≥30 字、不含链接的纯文本（`_absorb_user_materials` 会登记成 `user_provided` + `excerpt_basis=user_provided` 的可读来源）。
29. **两条互斥断言 = 有一处是遗留**：退役功能（如 `public_research.search_public` 恒抛 `NotConfigured`）要同步清理调用方测试。`planning_policy` 曾断言"未配 Provider 仍尝试公开搜索 2 次"，与 `evidence_grounding` 的"绝不调用已退役抓取器"直接矛盾。
30. **版本号四处同改**：`README.md` 标题、`backend/app/core/config.py:app_version`、`frontend/src/assets/app.js` 的 `本地工作区 · vX.Y.Z`、`scripts/build_release.py` 的 `OUTPUT` 文件名。发布门禁 = 全量回归 + `scripts/check_release.py <zip>`。
31. **页面模块用顶层 await 时，`load` 事件早于初始化完成就返回**：`ReviewPreview.html` 顺序 await `/studio/options`、statusBar、`/contents`、`/contents/{id}`、`/profiles`、`/publications`，`page.goto` 返回（甚至用户手动点）时 `DETAIL` 可能还是 null。**凡依赖已加载数据的入口都必须在模块求值时就 `disabled=true`，等 `renderAll()` 再启用**（见 `REVISION_ENTRY_IDS` / `revisionEntries()` / `syncRevisionControls()`）。否则点击会被 `requireRev()` 抛错吃掉、请求根本发不出去，而 `guard()` 只弹一个瞬时 toast —— 自动化侧表现为一个毫无信息的超时。验收方式：`test_browser.py` 里用 `page.route("**/api/v1/**", 延迟 0.6s)` 把窗口稳定撑开后再断言。
32. **排查"请求没生效"类前后端问题，先看测试的临时库**：测试用 `tempfile.mkdtemp('cwb-skills-')` 建库且**不清理**，`%TEMP%` 下会留一大堆。直接用 sqlite 只读打开 `test.db`，看 `event` 表有没有对应的 `change_requested` / `*_enqueued` 事件、`platform_revision` 有几个版本，就能立刻区分「请求没到后端」和「后端处理失败」。表名是**单数**（`content_item` / `content_revision` / `platform_revision`），`content_revision` 没有 `state` 列、`event` 的时间列叫 `time`、`content_item` 的状态列叫 `state`。
33. **断言要能说出失败原因**：Playwright 里"点一下按钮然后等某个元素变成期望值"会把"请求没发出去"伪装成纯超时。正确写法是先等**明确的成功信号**（如 `#revision-message` 写明新版本号），失败时把界面报错（`#cwb-toast`）一并抛出来。

## 尚未修好（与本轮改动无关，改动前先看这里）
- 无。v1.4.0（2026-10-08）全量回归 **37 阶段 2555 通过 / 0 失败**（含 `content_skills` 62、`browser` 108）。再遇到"红"阶段先区分**断言失败**与**异常退出**：`run_all_tests.py` 对二者都记进"失败阶段"，但只有后者没有 `阶段统计` 行。

## 运行环境（Windows）
- Bash 工具在本机不可用（`dirname: command not found`）。用 **PowerShell** 或直接读文件。
- 必须用项目 venv：`C:\Users\18057\Desktop\npu\ar_sop\content-workbench\.venv\Scripts\python.exe`（系统 python 无 pydantic）。
- PowerShell 标准输出常不回显 → 把结果 `Set-Content` 写文件再用 Read 读。
- 跑测试（推荐）：`.venv\Scripts\python.exe -B scripts\run_all_tests.py <stage...>`，stage 用文件名去 `test_`/`.py`（如 `p5_e2e creation_guide`）；不带参数跑全量。测试是**纯脚本**（无 pytest），各自建临时库，输出末行是 `N 通过 / M 失败`。PS 下加 `| Set-Content -Encoding UTF8 file` 再 Read（中文会乱码但 `N 通过` 的阿拉伯数字可读）。
- **前端改动必须跑 `browser` 阶段**（Playwright，会真的起 api+worker，108 条断言）。只跑后端各阶段**测不到前端崩溃**——"内容再调整"的 TypeError 就是这么漏过去的。该阶段能跑通的前提：隔离测试没有搜索 Provider，所以凡是要走真实研究链路的用例都得自己喂"用户提供的参考资料"（见第 28 条）。
- 浏览器测试里"无脚本异常"类断言要过滤瞬时网络错误（`net::ERR_ABORTED` 等，见 `test_browser.page_errors()`），否则偶发失败；断言信息里要拼上实际异常文本，否则失败时无法定位。
- DB：`storage/cwb.db`。**该库是 SQLite WAL 模式**，只拷 `cwb.db` 会得到陈旧快照；备份/复刻必须同时拷 `cwb.db-wal` + `cwb.db-shm`。
- **服务启停只能由用户操作**：Agent 沙箱会在工具调用结束时强制回收所有被 spawn 的子进程（默认 / DETACHED / CREATE_BREAKAWAY_FROM_JOB 实测全被杀），`schtasks.exe` 被程序黑名单拦截。所以 `scripts/launcher.py start` 在 Agent 侧"看起来成功但立刻死"；`stop` 可用。**别人要求重启服务时，直接让用户双击 `start.bat`，不要反复尝试，并如实说明服务当前是否在跑。**
- 项目自己的服务端口：**8000**（uvicorn + app.worker）。机器上还有别的项目在跑（如 qada_skill:8610），别误杀。
- **git**：本项目已在 2026-10-08 首次 `git init`，远端 `git@github.com:saoweier/workbench.git`（`origin`，SSH）。三点必知：
  1. 工程目录 ACL 属主是沙箱账户（`COMPASS/CodexSandboxOffline`），git 会报 `detected dubious ownership`；已加 `safe.directory`。换机器/换目录要重加。
  2. **`run_in_background` 的 Bash 仍走沙箱**，读 `~/.ssh` 会被拒 → 推送必须**前台 + `dangerouslyDisableSandbox:true`**。
  3. SSH 到 github.com 通、到 **gitee.com 是 publickey denied**；没有 `gh` CLI 与 API token，**Agent 不能创建远端仓库**，只能推用户已建好的空仓库。提交范围见 `.gitignore` + `.git/info/exclude`（后者排除了 `docs/test-artifacts/`，故意不改 `.gitignore` 以免影响已发布的 zip/sha256）。

## 已知关注点
- 题材形态共 7 种：ranking / listicle / tutorial / comparison / review / guide / explainer。
- 图解 visual kind：cover / map / flow / compare / example / checklist / rank / photo / nutrition。
