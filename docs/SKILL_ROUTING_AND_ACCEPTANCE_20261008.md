# 技能入口、专项验收与交付物清单

本轮参照开源项目 `xiaohongshu-tech-card-report` 的做法，把三件事落成程序可判定的形式：
**入口说明可读**、**成品要求可核对**、**交付物可逐项对账**；同时修复两平台内容区别不大的问题。

## 1. 平台差异：小红书要封面，抖音可以没有

过去两个平台的改写各自重复硬编码“首页必须是 cover”，差异被抹平，双平台稿看起来一样。

现在版式差异集中在**单一约束来源** `backend/app/services/platform_policy.py`：

| 平台 | 封面策略 | 文案取向 |
|---|---|---|
| `xiaohongshu` | `required`：必须有 `layout=cover` 的封面页，且封面要给核心信息 | 解释与可收藏复用的清单，标题完整、层次清楚 |
| `douyin` | `optional`：可以不做封面，第一页直接给实质内容 | 直观对比与可立刻执行的行动，短句、结论先行 |

同一份约定在四个位置统一生效，避免“Skill 说明与代码互相冲突”：

- 生成提示词（`_form_prompt_block` / `_generate_variant` 的 `cover_instruction`、`copy_angle_instruction`），`prompt_version` 升为 `v5-platform-cover`；
- 平台稿校验 `_validate_variant` 的 `cover_problem`：
  - 小红书缺封面 → `ValidationFailed`；
  - 抖音出现只有标题的封面图解 → `VISUAL_INVALID`（不为了凑版式做空封面）；
- 规则改写 `_variant_by_rules` / `_ranking_by_rules`：小红书预置封面页，抖音把版面全部留给名次；
- 榜单/速查编译 `catalog_compiler.compile_rows(..., cover=requires_cover(platform))`。

`visual_content.add_rule_visuals` 也改成按 `layout` 判断图解类型，否则“抖音首页不是 cover”会与图解类型冲突而被判 `VISUAL_INVALID`。

## 2. 技能入口说明与路由用例表

七个流程技能的 `SKILL.md` 顶部各有一段可解析的入口块：

```
【何时使用】…
【需要输入】…
【交给谁】…
```

- 解析与校验：`content_skills.skill_entry()` / `entry_missing()` / `stage_entry_contract()`；
- 接口暴露：`GET /content-skills` 的每个流程技能带 `entry` 与 `entry_missing`；
- 界面展示：技能卡片上的“何时使用 / 需要输入 / 交给谁”三行，缺块时明示缺哪一项；
- 用例表：`backend/app/skills/content-team/evals.json`，由 `content_skills.routing_cases()` 读取。

用例表断言的是**真实路由**，不是提示词：

```json
{"id": "ranking-tech", "request": "上个月GitHub最热门Skill TOP10，精致排行榜1～2页",
 "expect": {"form": "ranking", "direction": "tech", "rank_count": 10,
            "page_budget": [1, 2], "template_id": "rank_cards",
            "acceptance": "rank", "deliverable_keys": [...]}}
```

回归会逐条跑 `apply_recipe(build_brief(...))` 并比对形态、方向、数量约束、页数预算、样式模板、交付物键名，以及“未配搜索时需真实数据的榜单会在规划被拦下”这一设计行为。

## 3. 专项验收契约

`backend/app/services/acceptance.py` 把容易出错、但一眼能数出来的成品要求写成程序判定：

**榜单专项 `rank_contract(pages, rank_count, sources=…)`**

| 检查 | 代码 |
|---|---|
| 名次版面存在 | `RANK_BOARD_MISSING` |
| 榜单项数等于要求 | `RANK_COUNT_MISMATCH` |
| 对象互不重复 | `RANK_DUPLICATE_OBJECT` |
| 名次跨页连续不重复 | `RANK_ORDER_BROKEN` |
| 指标对应项目（数值↔口径↔来源，且与对象同源） | `RANK_METRIC_MISMATCH` |

**渲染产物 `export_contract(page_count, images)`**：导出图片数量（`IMAGE_COUNT_MISMATCH`）与页序完整（`PAGE_SEQUENCE_INCOMPLETE`）。

接入点：

- 平台稿校验：`compose_service._valid_rank_layout` 直接委托 `rank_problem`（单一来源，不再另写一套）；
- 渲染产物：`renderer.verify_images(..., expected_pages=…)`，由 `pipeline.render` 传入实际页数；
- 质量报告：`quality_rules.check_quality(..., rank_count=…)`，`repair_service` 从冻结的 `pages_json.rank_count` 取值；
- 新增的两个导出类错误码纳入 `UNREPAIRABLE_CODES`，属于环境问题，交人工而不是让模型再写一版文案。

榜单条目数随稿冻结（`PlatformRevision.pages_json.rank_count`），所以修复与交付阶段能复算同一套契约。

顺带补上一条既有缺口：生成提示词一直写着“每页 nutrition 最多三种”，但 `VisualSpec` 没有实现。现已在 `VisualSpec.valid_shape` 里真正拦下（四种水果挤一页会被拒收）。

## 4. 单次任务的交付物清单

`backend/app/services/deliverables.py` 的 `build()`（可重复计算、只读）输出：

- `content` / `revision` / `brief`：任务与冻结的简报、技能版本；
- `sources`：来源快照（含访问状态、口径说明）；
- `claims` + 选题理由：筛选记录；
- `platforms[].pages`：逐页标题、正文、footnote、图解类型与逐条 label/detail；
- `platforms[].images`：应有页数、实际张数、页序、缺页；
- `deliverables[]`：七行可读清单（六类交付物 + 专项验收），每行给出应有值、实际值与是否通过；
- `acceptance`：整体结论与全部问题项。

接口与产物：

- `GET /api/v1/contents/{content_id}/deliverables`（可选 `platform` / `revision_id` / `platform_revision_id`）；
- 界面：“内容团队 → 先查这条内容为什么这样写”顶部显示交付物清单表格；
- 发布包：`manifest.json` 增加 `deliverables` / `acceptance` / `source_snapshot` / `screening`，`checklist.md` 增加逐项交付物表格与“存在未通过项，先解决再发布”。

交付物键名集中为 `DELIVERABLE_KEYS`，`evals.json` 引用同一份常量，避免“清单说有、程序没产”。

## 5. 新增回归阶段

| 阶段 | 覆盖 |
|---|---|
| `acceptance` | 榜单/导出契约的通过与拒绝分支、接入点、规则改写同源 |
| `deliverables` | 清单结构、榜单验收、导出包内清单、缺图不通过、404 可读错误 |
| `content_evals` | 路由用例表逐条断言（说法 → 形态/方向/数量/模板/交付物） |

`content_skills` 阶段同时断言七个流程技能的入口块完整、卡片真的显示三行，以及技能卡片数量仍为 14。

## 6. 修正：预览页"点了没反应"

预览页的模块用顶层 `await` 顺序读接口（创作选项、状态条、内容列表、内容详情、平台规格、发布登记），而浏览器的 `load` 事件在模块初始化完成前就返回。这个窗口里「内容再调整 / 重新审核 / 生成图解」原本已经可点，但 `DETAIL` 还是空 → `requireRev()` 抛错 → **请求根本没有发出去**，界面只弹一个瞬时提示，用户看到的是"点了没反应"。

现在这四个入口在模块求值时就保持禁用，等 `renderAll()` 拿到当前版本再统一启用（`REVISION_ENTRY_IDS` / `revisionEntries()` 与无版本时的禁用逻辑共用一份清单）；没有版本时仍然禁用并写明原因。

配套回归：`browser` 阶段用 `page.route("**/api/v1/**", 延迟 0.6s)` 把这个窗口稳定撑开，断言窗口内四个入口先禁用、详情读回后恢复可用；`content_skills` 的实际改稿用例改为先等 `#revision-message` 的成功信号，失败时把界面报错文本一并抛出，避免把"请求没发出去"伪装成一次超时。
