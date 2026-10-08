> 历史阶段报告。当前交付结果、计数与操作入口以 [TEST_REPORT.md](TEST_REPORT.md)、[DELIVERY.md](DELIVERY.md) 和 [QUICK_START.md](QUICK_START.md) 为准。

# P4 完成报告：数据反馈闭环

> 对应 `docs/04-development-plan.md` P4 阶段（T16–T20）
> 完成日期：2026-10-02
> 测试：**661 通过 / 0 失败**（P0 61 + P1 40 + P2 82 + P3 199 + P4 279）

---

## 1. 双态登记表

| 任务 | 内容 | development_status | integration_status |
|---|---|---|---|
| T16 | 发布记录（declared / verified 分离） | ✅ 完成 | `integration_pending` |
| T17 | 数据导入（多指标快照 + 幂等 + 待匹配） | ✅ 完成 | `integration_pending` |
| T18 | 评论导入与聚类（规则分类 + 原文例证） | ✅ 完成 | `integration_pending` |
| T19 | 复盘（充分度分级 + 固定快照 + 版本追加） | ✅ 完成 | `integration_pending` |
| T20 | 反馈下一轮（幅度门 + 可回退 + 三条并列限制） | ✅ 完成 | `integration_pending` |

**为什么 `integration_status` 仍是 `integration_pending`：**

P4 的输入是**平台导出的真实数据**（抖音/小红书后台导出），
与本阶段全部在手工构造的 CSV/JSON 上验证是两件事。
真实导出文件的列名、时区写法、单位（"万"/"w"）习惯、
是否存在同一时刻多次导出等，都还没见过真实样本。
**不得**把手造 CSV 通过当作"真实导入可用"的证据。

---

## 2. 交付物清单

| 类型 | 文件 | 说明 |
|---|---|---|
| 服务 | `backend/app/services/publication_service.py` | 发布登记与核验 |
| 服务 | `backend/app/services/import_service.py` | 指标导入（~850 行） |
| 服务 | `backend/app/services/comment_service.py` | 评论导入与聚类（~470 行） |
| 服务 | `backend/app/services/review_service.py` | 复盘报告生成（~560 行） |
| 服务 | `backend/app/services/feedback_service.py` | 反馈建议与批次候选（~480 行） |
| 模型 | `backend/app/models/entities.py` | 新增 6 张表（P4 段） |
| API | `backend/app/api/publications.py` | 4 个路由 |
| API | `backend/app/api/imports.py` | 5 个路由 |
| API | `backend/app/api/comments.py` | 4 个路由 |
| API | `backend/app/api/reviews.py` | 3 个路由 |
| API | `backend/app/api/feedback.py` | 5 个路由 |
| 测试 | `backend/tests/test_p4_e2e.py` | 279 项，10 组 |
| 脚本 | `scripts/run-all-tests.sh` | 扩到 P0–P4，输出合计与失败阶段 |

新增 21 个路由操作（`app.openapi()` 共 57 条路径 / 65 个操作，其中 P4 相关 19 条路径）。

---

## 3. 发布记录：declared 与 verified 必须分开

**核心问题**：用户点"已发布"时，系统并不真的知道这件事是否发生。
如果把用户声明直接当成事实，后面所有数据都会被一个不存在的发布记录污染。

所以一条发布记录有**两组字段**，永不互相覆盖：

| 组 | 含义 | 谁写 | 初始值 |
|---|---|---|---|
| `declared_*` | 人声称的（链接、作品 ID、发布时间） | 人登记 | 有值 |
| `verified_*` | 核验后的（状态、时间、人） | 人核验 | **None** |

`verified_state` 三态：`confirmed` / `mismatch` / `inaccessible`。
**`mismatch` 和 `inaccessible` 都是有价值的结论**，不是"还没核验"。
测试断言：登记后 `verified is None`；核验 `mismatch` 后 `declared` 一字未改。

### 非人登记被拒

`registered_by` / `verified_by` 落在 `{"system", "model", "ai", "worker", ""}`
→ 分别抛 `PUBLISHER_MUST_BE_HUMAN` / `VERIFIER_MUST_BE_HUMAN`。

**接口层更进一步**：`POST /publications` 里 `registered_by="user"` 是**硬编码的**，
不从 payload 读。测试里客户端显式发 `"registered_by": "system"`，
落库仍是 `user` —— 不变量 6（模型不能改身份/发布状态）在 API 边界的落点。

标识必填：`link` 与 `platform_post_id` 至少一个，否则
`PUBLICATION_NEEDS_IDENTIFIER`（HTTP 400）—— **两者都空不算一条发布记录**。

---

## 4. 数据导入

### 4.1 长表 → 多指标快照（真实设计缺陷）

标准后台导出是**长表**：同一时刻的播放量、点赞、完播率各占一行。

初版按"一行一个快照"写库，造成两个问题：

1. **把一次采集拆成了互不相关的记录**，复盘时无法回答"这一时刻整体表现如何"
2. **直接撞上上下文唯一约束**（同 `publication_id + observed_at + window_kind`）

修复：按 `(publication_id, observed_at, aggregation_kind, traffic_type)` **分组**，
一组写入**一个** `MetricSnapshot`，`metrics` JSON 里放多个指标格。
测试断言：3 行同刻导出 → 1 个快照、3 个指标。

### 4.2 `traffic_type` 必须独立成列（真实设计缺陷）

自然流量与付费流量在**同一时刻**都有一条记录。
初版把 `traffic_type` 只塞在 `metrics` JSON 里，
去重键看不到它 → 两条被判成重复，**"自然流量到底多少"永远算不出来**。

修复：把 `traffic_type` 提升为**显式列**，同时进入唯一约束与全部去重/失效查询：

```python
UniqueConstraint("publication_id", "observed_at", "window_kind", "traffic_type",
                 name="uq_metric_snapshot_ctx")
```

### 4.3 dry_run 不得污染幂等表（真实缺陷）

`dry_run=True` 时跳过幂等早退，然后仍去 INSERT 一条 `ImportBatch`，
撞上 `uq_import_batch_file_mapping` 直接报错；
**更糟的是它让紧随其后的真实导入也失败** —— 一个"试算"动作把正式流程废掉了。

修复：新增 `_dry_run_metrics(parsed)` 提前返回，**不建批次行、不写快照**，
状态如实报 `"dry_run"`、`import_id == ""`。测试断言试算之后真导入仍成功。

### 4.4 幂等与待匹配

幂等键 `(kind, file_hash, mapping_version)`。同一文件重放 → `idempotent_replay=True`，
**不重复写入**（`duplicate_rows` 单独计数，不算错误）。

匹配不上的行**不猜**：放入 `unmatched` 并说明
"找不到对应的发布记录，已放入待匹配，未做任何猜测绑定"。
`POST /imports/{id}/match` 由人指定绑定目标。

### 4.5 单位与缺失

`UNIT_MULTIPLIERS` 覆盖 `万/w/千/k/百万/m`；`parse_number` 返回
`(值, 单位, 警告)`，**"1.2万" → 12000 且留下单位警告**。

**缺失 ≠ 0**：解析不出数字 → `None` + `value_missing=True`，
不写 0（写 0 会让"没采到"看起来像"确实是 0 播放"）。

`parse_time` 的 `ValueError` 被捕获并映射为行级错误 / 422，
不会漏成 500。

---

## 5. 评论：规则分类 + 原文例证

### 5.1 分类是 rule_based，不是模型推断

5 组有序规则（先判 `invalid`，再 `challenge` → `question` → `request` → `experience`），
每条返回 `(category, reason)`，**理由必填**。
`GET /comments/categories` 把规则表整个暴露出来供人核对。

输出里显式声明 `generation_mode = "rule_based"`，并带 caveat：
"分类是系统的判断，可能出错；请用原文例证核对。"

**分类可能出错，所以每组都带原文例证**（≤5 条，按点赞降序，
保留 `created_at` 顺序信息；超过 5 条标 `examples_truncated`）。

### 5.2 分母必须一起给

`share` 永远伴随 `of_total`。**只给百分比不给样本量是耍流氓。**
测试额外断言"各组 count 之和 == 分母"，防止出现"分子加起来不等于样本量"。

### 5.3 取样口径与偏差

取样方式不同（全量导出 / 前 N 条 / 时间窗）之间**不可直接比较**。三种路径：

| 情况 | note |
|---|---|
| 未声明 | "未声明取样方式。分布数字仅供参考，不确定其代表性。" |
| 声明全量 | "若是全量导出，代表性最好；仍受平台展示逻辑影响。" |
| 声明抽样 | "取样方式：前 2 条热门，样本量 2。**这是样本不是全体**…" |
| 空样本 | "**空样本不能得出任何分布结论**——无数据不是「没有问题」。" |

`_guess_sampling()` **不假装知道是全量**，默认记"未声明（导入 N 行，口径不确定）"。

**多口径混在一份发布上时不挑好看的讲** —— 只要有"未声明"就提示口径不确定。

### 5.4 去标识

只存 `anon_id = "anon_" + sha256("cwb::" + seed)[:24]`，
**不存公开用户名**。测试断言落库字段里没有原始用户 ID。
唯一约束 `(publication_id, anon_id)` 保证重复导入不产生重复样本。

---

## 6. 复盘：先分级，再说话

### 6.1 充分度四级

| 级别 | 条件 | 能不能出效果结论 |
|---|---|---|
| `none` | 无发布记录 | ❌ 明说"没有效果可复盘" |
| `insufficient` | 有发布、无数据 | ❌ 明说"无数据不等于表现差" |
| `baseline_only` | 单点数据 | ❌ 明说"单点无法构成趋势" |
| `comparable` | ≥2 个窗口 | ⚠️ 可比较，但仍提示"同口径" |

**"没有数据"和"数据不好"是两件事。** 前者不能推出后者。
`none` 时 limitations 明写"不能输出任何传播效果结论"，且**不产出任何 metric 观察**。

### 6.2 观察不带因果

所有 `observations` 由程序生成，测试逐条断言
**不含"因为/导致/所以/说明了"**。可以陈述"播放量 12000"，
不能陈述"播放量高是因为封面好"。

### 6.3 假设必须有替代解释

```python
rev_svc.generate(content_id, hypotheses=[{"statement": "封面更好所以播放高"}])
```

→ `ValueError`：**"给不出竞争解释，说明手里是直觉而不是假设"**。

合法假设必须带 `alternative_explanations`（≥1 条）。
HTTP 层缺该字段 → 422 `HYPOTHESIS_NEEDS_ALTERNATIVE`。
有假设时 `generation_mode = "assisted"`，纯程序报告是 `"none"`。

### 6.4 固定快照 + 版本追加（不变量 7）

报告落库时把当时的 `snapshot_ids` / `comment_import_ids` / `publication_ids`
**固化成 JSON 列表**。之后新增数据只会产生**新版本**，
旧版引用**一字不改**。测试断言：新增快照后旧报告 `snapshot_ids` 长度不变。

`(content_id, version)` 唯一约束保证版本不重号。

### 6.5 付费流量不互相顶替（真实设计缺口）

初版 `_latest_snapshot` 每份发布只取**一个**快照。
自然与付费是同一时刻的两条观测，于是**互相顶替** ——
只能看到其中一个，"这波投流带来多少"永远答不出来。

修复：`_representative_snapshots()` 按 `(window_kind, traffic_type)` 分组，
全部保留；`_scope_prefix()` 给陈述加限定：

> 自然流量：播放量 10000
> 付费流量：播放量 40000

caveat 相应标注"仅自然流量"/"仅付费流量"，limitations 提示存在付费流量。
测试断言 `{"organic", "paid"}` 两类都被陈述。

### 6.6 来源链

`source_chain` 逐层列出实际用到的
`publication` / `metric_snapshot` / `comment_sample` / `comment_import`。
测试对**有评论的内容**断言含 `comment_sample`；
对**无评论的内容**则断言其**不在**链里（如实，不美化）。

---

## 7. 反馈下一轮

### 7.1 建议默认不生效

`propose()` 产出 `status="proposed"`、`auto_adopted=False`。
接口声明 `capabilities.auto_apply = False`。

**没有证据引用的建议被拒**：

> 建议必须给出证据引用（evidence_refs），否则它就不是从数据来的，只是凭空的想法

### 7.2 自动采用仅 tiny / small

| 幅度 | `auto=True` | `auto=False` |
|---|---|---|
| `tiny` / `small` | ✅ 自动采用 | ✅ 人工采用 |
| `medium` / `large` | ⚠️ **降级为仅候选** + 说明超出范围 | ✅ 人工采用 |

`large` 自动采用**不报错而是降级** —— 调用方想自动化，系统替它把该做的判断做掉。

### 7.3 可回退

`adopt()` 时保存 `previous_value`。`revert()` 只对 `accepted` 生效，
恢复 `status="reverted"` 并回报 `restored_value`。
重复回退被拒："只有 accepted 的建议可回退"。

**没有回退动作的自动化不是自动化。**

### 7.4 待办列表 ≠ 历史列表（测试暴露的真实缺口）

接口 docstring 写着"这里看到的绝大多数条目都是 `proposed`"，
但初版实现不传 `status` 时**返回全部**。后果：采用/拒绝/回退之后
条目仍赖在列表里，列表只会越用越长、永远清不掉，
人就会开始整片整片地忽略它 —— **那等于没有待办列表**。

修复：不传 `status` → 语义是**待办**，只返回 `proposed`；
同时新增 `settled_total` 如实报告已了结条数，
并在 note 里说明"要看历史请显式指定状态筛选"。
测试断言：待办里只有 `proposed`；已了结的**没消失**（按状态查得到）；
**待办 + 已了结 == 历史总数**（不丢数据）。

### 7.5 三条并列限制

`propose_batch()` 同时检查三条，**任一触顶即只保存候选**：

| 限制 | 触顶理由 |
|---|---|
| 批次条目数 | "条目已用满" |
| 待预览库存 | "库存已达上限"，先处理已有产出 |
| 金额上限（**仅设置时生效**） | 见下 |

**金额 null 不阻塞，但也不因此放宽其他两条**（不变量 8）。
响应显式回报 `money_limit_is_null` 与
"未设上限不等于无限额度，也不解除其他限制"。
测试用 `pending_review_stock=5 / limit=3` 配 `budget_limit_micro=None`，
断言仍被拦。

### 7.6 从复盘起草

`draft_from_review()` 是**机械转写**，不是决策：
评论某类占比 ≥30% → `category_focus` 候选；
充分度不足 → 只提"补数据"不提"改内容"；
`next_topics` → 候选选题。

全部 `needs_human_review=True`，note 明写
"系统不会直接把复盘结论变成参数改动"。

---

## 8. 不变量对照

| # | 不变量 | P4 验证 |
|---|---|---|
| 1 | 无批准不出可发布包；旧批准不被新内容覆盖 | 登记时回报 `approval_consistent`；复盘固定快照 |
| 2 | 无真实发布登记不自动标已发布 | 无 `POST /auto-publish`；`auto_publish=False`；标识必填 |
| 3 | 重试不重复追加产物；失败不抹掉成功阶段 | 导入幂等；`dry_run` 不污染批次表 |
| 4 | 未知结果 ≠ 免费失败；过期租约 ≠ 盲目重计费 | 沿用 P3；P4 全程 `real_calls_recorded == 0` |
| 5 | real/fixture/估算显式区分；缺失 ≠ 0 | `value_missing`、`like_count=None`、缺时间 `None` |
| 6 | 模型不能改批准/预算/身份/连接/发布状态 | `registered_by` 硬编码 user；伪造 system 无效 |
| 7 | 评审用固定输入快照 | `snapshot_ids` 固化；新版不改旧版 |
| 8 | 金额 null 不阻塞；null ≠ 0 | 三条并列限制 + `money_limit_is_null` |
| 9 | 保存设置不自动调用；未配置搜索不谎称已执行 | 沿用 P3；P4 无真实远程请求 ID |

---

## 9. 测试

```
$ ./scripts/run-all-tests.sh

===== p0_smoke =====    61 通过 / 0 失败
===== p1_e2e   =====    38 通过 / 0 失败
===== p2_e2e   =====    82 通过 / 0 失败
===== p3_e2e   =====   198 通过 / 0 失败
===== p4_e2e   =====   279 通过 / 0 失败
---------------------------------------------------------
全量回归合计：654 通过 / 0 失败
```

**P4 测试 10 组：** 发布记录 / 指标导入 / 单位与缺失 /
评论导入与分类 / 评论聚类 / 复盘充分度 / 复盘假设与版本 /
反馈建议 / 批次限制 / 不变量汇总。

**全程零真实调用**：`real_calls_recorded == 0`、无 `remote_request_id`。

### 本阶段发现并修复的缺陷（3 真实缺陷 + 2 缺口）

| # | 问题 | 性质 | 修复 |
|---|---|---|---|
| 1 | 一行写一个快照，拆散同次采集且撞唯一约束 | 真实缺陷 | 按四元组分组，一快照多指标 |
| 2 | `traffic_type` 只存在 JSON 里，自然/付费被合并 | 真实缺陷 | 提升为显式列并进唯一约束 |
| 3 | `dry_run` 写批次行，撞幂等约束并**废掉后续真导入** | 真实缺陷 | `_dry_run_metrics` 提前返回 |
| 4 | 复盘每份发布只取一个快照，付费流量被隐藏 | 设计缺口 | 按 `(window, traffic)` 分组 + 作用域前缀 |
| 5 | 反馈列表把已了结条目一起返回，待办永不清空 | 设计缺口 | 不传 status 即待办 + `settled_total` |

第 3 条最值得记：一个标着"只试算"的动作，**把正式流程写坏了**。
测试的价值就在于第二条真实导入会立刻失败，而不是等上线后才发现。

### 测试侧的三个假设错误（不是代码缺陷）

- 断言 `[7]` 聚类总数 == 5，但同一份发布上先后导入了 3 个文件，
  实际落库 7 条。**分母该是实际样本量，不是某一个文件的条数。**
  改为读真实行数并额外断言"各组 count 之和 == 分母"。
- 断言 `dry_run` 状态为 `"imported"`，正确值是 `"dry_run"`。
- 断言 T20 列表 ≥5 条，实际是**待办**语义（见 §7.4）。

---

## 10. 已知限制

| 限制 | 影响 | 现状 |
|---|---|---|
| 未见过真实平台导出文件 | 列名/时区/单位习惯可能与假设不符 | 匹配器已做别名，但需真实样本确认 |
| 评论分类为规则驱动 | 反讽、方言、跨行业黑话会漏判 | 已声明 `rule_based` 并强制带原文例证 |
| 起止时长 `age_hours` 依赖导出时填 | 缺 `observed_at` 时无法定位窗口 | 如实存 `None`，不猜 |
| 聚类不看语义相似度 | 同义不同词的评论可能分不到一组 | 有意保持可解释，避免黑箱 |
| `sample_context` 仅存储不分析 | 不同投放场景的样本混算 | 单列字段待后续使用 |
| 无真实数据 → 无法验证复盘结论质量 | 只能说"程序没有越界" | 需 P5 真实试跑 |
| 前端 P4 页面 | 见 §11 | 待补 |

---

## 11. 前端

P4 契约演示数据已就绪，页面待建：

| 页面 | 关键呈现 |
|---|---|
| 发布记录 | declared / verified 两栏并列，`verified=None` 显示"未核验"而非"正常" |
| 数据导入 | 幂等replay 标记、待匹配行可手动绑定、`value_missing` 显式显示 |
| 评论与复盘 | 每类带原文例证与理由；充分度分级徽标；假设必须显示替代解释 |
| 反馈 | 只列待办 + 已了结计数；`auto_adoptable` 徽标；采用/拒绝/回退三动作 |

---

## 12. 复现方式

```bash
cd /workspace/content-workbench

# 全量回归
./scripts/run-all-tests.sh
./scripts/run-all-tests.sh p4_e2e        # 只跑 P4

# 起后端
.venv/bin/uvicorn app.main:app --app-dir backend --reload

# 登记一条发布（注意 registered_by 不由客户端决定）
curl -sX POST localhost:8000/api/v1/publications \
  -H 'Content-Type: application/json' \
  -d '{"platform_revision_id":"<id>","link":"https://v.douyin.com/abc"}'

# 导入指标
curl -sX POST localhost:8000/api/v1/imports \
  -H 'Content-Type: application/json' \
  -d '{"kind":"metrics","text":"platform,post_id,observed_at,metric_name,value\n..."}'

# 评论规则表（可核对分类口径）
curl -s localhost:8000/api/v1/comments/categories | python3 -m json.tool

# 生成复盘
curl -sX POST localhost:8000/api/v1/contents/<id>/reviews \
  -H 'Content-Type: application/json' -d '{}'

# 反馈待办（已了结不在其中）
curl -s "localhost:8000/api/v1/feedback?content_id=<id>" | python3 -m json.tool
```

---

## 13. 退出标准核对

| 标准 | 状态 |
|---|---|
| 全部测试通过（P4 ≥ 70） | ✅ 279 |
| P0–P3 回归无退化 | ✅ 61 / 38 / 82 / 198 |
| 五个任务均有 API 入口 | ✅ 21 个路由 |
| 不变量 1–9 均有落点断言 | ✅ 见 §8 |
| `integration_status` 仍为 `integration_pending` | ✅ 如实登记 |
| 完成报告入 `docs/P4-completion-report.md` | ✅ 本文件 |

---

## 14. 下一步

**用户侧（并行）：** 配置真实 Provider Key；提供一份真实的
抖音/小红书后台导出文件（指标 + 评论）用于校准列名与口径。

**P5：** 试运营与交付 —— 安装启动、端到端测试、连续试跑、发布 v1。
需真实试跑约 6 个内容包以验证复盘结论质量。

**建议（已记录，未开工）：** 把 P0/P1 测试也改成临时目录隔离，
彻底去除对 `scripts/run-all-tests.sh` 前置清理的依赖。
