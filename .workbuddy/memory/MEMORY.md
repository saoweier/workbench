# content-workbench 项目备忘（长期）

## 架构速览
- 6 段 skill 流水线：discovery → selection → planning → generation → audit → revision（`services/content_skills.py`）。
- 生产编排：`production_service.py`（produce/illustrate）→ `compose_service.py`（母稿 + 双平台改写 + 规则稿 + 校验）→ `visual_content.py` → `renderer.py`（Playwright 出图）。
- 确定性离线 fixture：`services/adapters/fixture.py`（按 schema `minItems` 决定数组长度）。

## 硬约定（勿违反）

**A. 结构化落地，禁止只写提示词**
1. 用户显式要求（页数 / 题材形态 / 榜单条目数 / 导出图片张数 / nutrition 每页上限…）必须落进 schema 或 `services/acceptance.py` 校验。**"提示词写了但程序没拦"＝没写**（踩过：nutrition 每页上限只写在提示词里 → `fruit_quality` 一直红）。
2. 不同题材必须不同模板与主题色，见 `content_forms.py` 的 `FORMS`/`FORM_THEMES`。
3. 结构性页数校验走 `content_forms.build_brief()` 的 `CreativeBrief`，别在 `compose_service` 硬编码页数。改页数下界**别动全局 `minItems`**（会打乱 fixture），用 `compose_service._with_pages(schema, lo, hi)` 带 brief 时动态覆盖。
4. 规则稿**不得编造**：条目数不足直接报错。
5. 榜单契约单一来源是 `acceptance.rank_contract`（`compose_service._valid_rank_layout` 只是薄封装）；条目数随稿冻结在 `PlatformRevision.pages_json.rank_count`。
6. 交付物键名以 `deliverables.DELIVERABLE_KEYS` 为单一来源，`evals.json`、测试、导出包 `manifest.json`/`checklist.md` 共用。
7. 平台差异只在 `services/platform_policy.py` 一处定义（小红书 cover=required / 抖音 optional），生成提示词、`cover_problem`、规则改写、`compile_rows(cover=…)` 四处共用。
8. 技能入口块（7 个 `SKILL.md` 顶部 `【何时使用】/【需要输入】/【交给谁】`）是路由的机器可读来源，由 `content_skills.stage_entry_contract()` 解析；`evals.json` 断言的是**真实路由结果**（form/direction/rank_count/页数预算/template_id/交付物键名），不是提示词文本。改路由要先让用例失败。

**B. 状态机与错误信息**
9. 不要物理删除内容：下线用 `PipelineService.discard()` → 终态 `discarded`，接口 `POST /contents/{id}/discard`。已登记发布的拒绝丢弃。新增状态记得在 `frontend/src/assets/app.js` 的 `STATE_MAP` 补中文标签。
10. 关闭 run **必须写 `run.error`**：`ProductionService._close_run(run, state, *, blocked_stage=None, error=None)`；`_block()` 的失败路径同时落 `run.error` + `blocked_stage`。只塞 `Job.output_refs` 会让前端显示"任务暂停或已取消"这种误导文案。前端 `creation-guide.js` 已做 `run.error → run.jobs[].error` 回退。
11. 内容从未产出过版本就中止 → `content_item.state = blocked`（否则永远显示"排队中"）。已发布/已有版本的内容不要被重跑失败改状态。
12. 限流只拦无人值守路径：待预览库存上限只作用于 `topic_service.select`；引导式创作（`api/creation.py`）不拦。库存口径 = `topic_service.PENDING_STATES = {ready_for_review, checking}`。
13. 未配搜索 Provider 时，需要真实数据的选题会在 planning 被防编造闸门拦下（`plan.blocking_gaps` → `ValidationFailed`，run failed/blocked_stage=planning）。**这是设计行为**，提前提示见 `/creation/options` 的 `search_ready`。
14. `needs_grounding()` 对非纯创作题几乎都 True，真实模式默认要求可读证据。测试要跑通直创就在 `#creator-materials` 填 ≥30 字、不含链接的纯文本（`_absorb_user_materials` 会登记成 `user_provided` + `excerpt_basis=user_provided`）。
15. 导出类错误码（`IMAGE_COUNT_MISMATCH`/`PAGE_SEQUENCE_INCOMPLETE`）属环境问题，已在 `quality_rules.UNREPAIRABLE_CODES`，不要丢进模型修复循环。
16. 来源没有正文一律修数据、不放松闸门：`decide` 批准真实内容要过 `require_evidence`，demo 种子需带 `excerpt` + `access_state=ok` + `excerpt_basis=local_file`（`scripts/seed_demo.py` 重建）。重建后复核 `scripts/check_release.py` 期望：66 图 / 6 发布包 / publication 6 / metric_snapshot 8 / comment_sample 16 / review_report 2 / topic_feedback 2 / integrity ok。
17. 一条规则只准有一个定义点（封面、榜单、交付物、页数皆已如此），复制粘贴出来的第二份一律视为 bug。

**C. 前端**
17. 读 `activeRev()`/`find()` 结果前必须判空（可能一条 revision 都没有）。`ReviewPreview.html` 用 `requireRev()` 抛可读原因 + `syncRevisionControls()` 在无版本时**禁用**入口并写明原因。
18. 页面模块用顶层 await 时，`load` 事件早于初始化完成就返回；**凡依赖已加载数据的入口必须在模块求值时就 `disabled=true`**，等 `renderAll()` 再启用。否则点击被 `requireRev()` 抛错吃掉、`guard()` 只弹瞬时 toast → 自动化侧表现为无信息超时。验收：`test_browser.py` 用 `page.route("**/api/v1/**", 延迟 0.6s)` 撑开窗口再断言。
19. 前端模块多为动态 import，点击前要等挂载（如 `expect(#guide-hub).to_be_attached()` 是可靠就绪信号）。"单独跑能过、全量跑挂"优先怀疑这种竞态。
20. 交付物清单等接口调用要能降级：`content-diagnostics.js` 在报错或非清单结构时显示原因，不能直接取 `deliverables.acceptance.passed`（桩数据缺路径会炸页面）。
21. 非安全上下文（`http://内网IP`）下 `crypto.randomUUID`/`navigator.clipboard`/`crypto.subtle` 不存在。前端一律用 `app.js` 的 `uuid()`/`copyText()`；`uuid()` 用 `crypto.getRandomValues` 拼合法 UUID v4（后端 `request_id` 声明为 `UUID`）。
22. **改任何前端文件必须同时换它的 `?v=` 缓存 token**，改 `app.js` 尤其要动（所有模块共用同一 URL 引它）。检查残留：`grep -ro "?v=[0-9a-z-]*" frontend/src | sort | uniq -c`。测试不受影响（服务端忽略查询串，`test_p5_e2e.page_sources()` 会剥 `?v=`）。
23. 改导航条 `NAV` 必须同步 `test_browser.py` 的 `views` 列表与 `ready` 映射。

**D. 部署与访问边界**
24. **访问边界是一个依赖挂 5 个路由**：`app/api/accounts.py` 的 `local_request` 被 `accounts`/`studio`/`content_skills`/`douyin_publishing`/`videos` 共用。2026-10-09 之前要求"客户端回环 + Host 本机名"，导致局域网访问连 `/studio/options` 都 403，前端只显示「创作设置未读取：账号连接只允许本机访问」（**看着像账号坏了，其实是整个应用**）。现行规则：`Settings.allow_remote_access`（`CWB_ALLOW_REMOTE_ACCESS`，默认 **True**）控制非回环客户端；Host 校验保留（本机名 ∪ `CWB_ALLOWED_HOSTS` ∪ 私有/回环/链路本地 **IP 字面量**，只放行 IP 字面量因 DNS rebinding 必须借攻击者域名）；同源 + 拒 `Sec-Fetch-Site: cross-site` + 写操作要求 `X-CWB-Local-Action` 头恒开。**别再把"必须本机客户端"硬编码回来**，收紧用环境变量。
25. **本机服务默认只绑回环，且后端没有任何登录/鉴权**：`scripts/launcher.py` 写死 `--host 127.0.0.1`（`ensure_query_service` 同）。单用户本地工作台，要对外必须自加反向代理 + 认证。`test_query_startup.py` 只约束 **query 服务**绑回环、`test_operations.py` 只查停止脚本 → 改 app API 绑定地址不破坏测试。离线 wheel 只适用 Windows 64 位 Python 3.13，Linux/macOS 走 `requirements.txt`。
26. **`examples/demo/storage/` 必须留在版本控制里**：`.gitignore` 的 `storage/` 任意层级生效会把它排除，而 `launcher.initialize_demo()` 在默认库为空时依赖它（含 `artifacts/` 66 图 + `profiles.json`），缺了启动即失败。复核办法：发布包 zip 条目与 `git ls-files` 做差集。
27. 真机验证脚本 **`scripts/verify_insecure_origin.py`**：默认起隔离服务（`--host 0.0.0.0` + 临时库）用内网 IP 打开；`--origin http://<内网IP>:8000` 验已跑实例（实例只绑回环时加 `--alias-host` 走 Chromium `--host-resolver-rules`）；自带**反向对照**（换回 `crypto.randomUUID()` 断言能复现报错）。本机内网 IP **10.6.101.1**（探测时跳过 `198.18.0.0/15`、`169.254.0.0/16`、`172.26.0.0/16`）。
28. **验"正在跑的真实实例"的脚本默认必须只读**：带 `POST /studio/produce` 会在用户真实库里排内容（踩过 C083，只能 discard）。`StaticFiles` 每次请求读磁盘 → 前端改动无需重启，但 `/api/v1/health` 的 `version` 是启动时读的，重启才更新。

**E. 断言与排错（方法论）**
29. **断言要写"必须是正确状态"，不要写"不是某个错误状态"**：曾只断言"状态条 ≠『读取创作设置』"，而 403 降级显示的「服务暂不可用」同样满足 → 假通过，把真实故障当成功。现改为：状态条必须 ∈ {创作搭档…, 本地演练} + `#creator-error` 为空 + `#creator-ready` 已启用。
30. 断言要能说出失败原因：Playwright 里"点一下再等元素变化"会把"请求没发出去"伪装成纯超时。先等**明确的成功信号**，失败时把 `#cwb-toast` 一并抛出。模拟类用例第一条要断言"模拟确实生效"。
31. 断言不要只扫 HTML 文本：前端是「HTML 外壳 + 独立 JS 模块」，用 `test_p5_e2e.page_sources()` 跟随 `<script src>` 与 `from '/assets/…'` 的 import 图。
32. 模拟"模块初始化失败"要看 import 类型：`production.js` 对创作模块是**静态** import，`route.abort()` 会让整个模块求值失败（不可捕获）；要模拟"调用时抛错"得 `route.fulfill` 换成"导出同名函数、调用时 throw"的桩，并在**全新 context** 里做。
33. 两条互斥断言 = 有一处是遗留：退役功能要同步清理调用方测试（`planning_policy` 曾与 `evidence_grounding` 直接矛盾）。
34. 排查"请求没生效"先看测试临时库：测试用 `tempfile.mkdtemp('cwb-skills-')` 建库且不清理，sqlite 只读打开 `test.db` 看 `event` 表有无 `change_requested`/`*_enqueued`、`platform_revision` 有几个版本。表名是**单数**（`content_item`/`content_revision`/`platform_revision`），`content_revision` 无 `state` 列、`event` 时间列叫 `time`。
35. **用 `Write` 整体重写已有文件前先完整读一遍**：重写 `accounts.py` 时只读前 45 行，丢了 `/accounts/douyin/close` 端点。要么完整读、要么局部 Edit，改完用 `git show HEAD:<file>` 对比关键结构（`@router` 列表）。
36. 版本号四处同改：`README.md` 标题、`core/config.py:app_version`、`frontend/src/assets/app.js` 的 `本地工作区 · vX.Y.Z`、`scripts/build_release.py` 的 `OUTPUT` 文件名。发布门禁 = 全量回归 + `scripts/check_release.py <zip>`。

## 尚未修好
- 无。**v1.4.2（2026-10-09）全量回归 37 阶段 2570 通过 / 0 失败**（`platform_accounts` 47/0，真机 14/0，反向对照 3/0）。再遇到"红"阶段先区分**断言失败**与**异常退出**（`run_all_tests.py` 对二者都记进失败阶段，但只有后者没有 `阶段统计` 行）。`browser` 里"两页直接创作经完整后台完成"偶发超时（60s 轮询窗口），机器同时在跑用户常驻服务时会这样——断言名已同时打印 `state` 与 `error`，`state` 仍是 queued/running 就是超时。

## 运行环境（Windows）
- Bash 工具在本机不可用（`dirname: command not found`），用 PowerShell 或直接读文件。必须用项目 venv `.venv\Scripts\python.exe`（系统 python 无 pydantic）。PowerShell 标准输出常不回显 → `Set-Content` 写文件再 Read。
- 跑测试：`.venv\Scripts\python.exe -B scripts\run_all_tests.py <stage...>`（stage 用文件名去 `test_`/`.py`，不带参数跑全量）。纯脚本无 pytest，各自建临时库，末行 `N 通过 / M 失败`。全量重定向要 `python -u` 否则看不到进度。
- **前端改动必须跑 `browser` 阶段**（Playwright，真起 api+worker）。前提：隔离测试没有搜索 Provider，走真实研究链路的用例要自己喂参考资料。该阶段"无脚本异常"断言要过滤瞬时网络错误（`net::ERR_ABORTED` 等，见 `test_browser.page_errors()`），断言信息要拼上实际异常文本。
- DB：`storage/cwb.db`，**SQLite WAL 模式**，备份必须同时拷 `cwb.db-wal` + `cwb.db-shm`。
- **服务启停只能由用户操作**：Agent 沙箱在工具调用结束时回收所有子进程，`schtasks.exe` 被黑名单拦截 → `launcher.py start` 在 Agent 侧"看着成功但立刻死"，`stop` 可用。要重启就让用户双击 `start.bat`，别反复尝试，并如实说明当前是否在跑。项目端口 **8000**，机器上还有别的项目（如 qada_skill:8610），别误杀。
- **git**：远端 `git@github.com:saoweier/workbench.git`（SSH，`origin`）。① 目录 ACL 属主是沙箱账户，会报 dubious ownership，已加 `safe.directory`；② `run_in_background` 的 Bash 仍走沙箱、读 `~/.ssh` 被拒 → **推送必须前台 + `dangerouslyDisableSandbox:true`**；③ SSH 到 github 通、gitee publickey denied；无 `gh`/token，**不能创建远端仓库**。

## 已知关注点
- 题材形态 7 种：ranking / listicle / tutorial / comparison / review / guide / explainer。
- 图解 visual kind：cover / map / flow / compare / example / checklist / rank / photo / nutrition。
