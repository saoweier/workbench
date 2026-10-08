> 历史阶段报告。当前交付结果、计数与操作入口以 [TEST_REPORT.md](TEST_REPORT.md)、[DELIVERY.md](DELIVERY.md) 和 [QUICK_START.md](QUICK_START.md) 为准。

# P3 完成报告：容错、恢复与集中处理

> 对应 `docs/04-development-plan.md` P3 阶段（T12–T15）
> 完成日期：2026-10-02
> 测试：**379 通过 / 0 失败**（P0 61 + P1 38 + P2 82 + P3 198）

---

## 1. 双态登记表

每个任务都登记两个状态，互不替代：

- `development_status`：代码与测试是否完成
- `integration_status`：是否已接真实外部系统并验证

| 任务 | 内容 | development_status | integration_status |
|---|---|---|---|
| T12 | 质量规则 + 局部修复 | ✅ 完成 | `integration_pending` |
| T13 | 租约、四类故障恢复、Worker | ✅ 完成 | `integration_pending` |
| T14 | 用量单一真相 + 预算门 | ✅ 完成 | `integration_pending` |
| T15 | 一次集中处理 | ✅ 完成 | `integration_pending` |

**为什么 `integration_status` 仍是 `integration_pending`：**

真实 Provider Key 尚未配置（用户计划在 P3 后配置），
平台上传规格也未经真实界面核对（`platform_upload_verified = false`）。
本阶段全部验证都在 `fixture` / `local_seed` 下完成。
**不得**把 fixture 通过当作"真实链路可用"的证据。

---

## 2. 交付物清单

| 类型 | 文件 | 说明 |
|---|---|---|
| 质量规则 | `backend/app/services/quality_rules.py` | 5 类规则 |
| 局部修复 | `backend/app/services/repair_service.py` | ≤2 轮，规则优先、模型兜底 |
| 恢复 | `backend/app/services/recovery_service.py` | 租约 + `fencing_token` + 四类故障 |
| Worker | `backend/app/worker.py` | 独立进程，`python -m app.worker` |
| 用量 | `backend/app/services/usage_service.py` | 唯一读数入口 |
| 异常聚合 | `backend/app/services/attention_service.py` | 折叠 + 置顶 + 时长记录 |
| API | `backend/app/api/batches.py` | 批次与批量运行（补齐 P2 遗留缺口） |
| API | `backend/app/api/attention.py` | 集中处理 4 个路由 |
| API | `backend/app/api/usage.py` | 改为只读 `provider_call` 表 |
| 前端 | `frontend/src/views/Attention.html` | 集中处理页（新建） |
| 前端 | `frontend/src/views/UsageCosts.html` | 用量与消耗页（新建） |
| 测试 | `backend/tests/test_p3_e2e.py` | 198 项 |
| 脚本 | `scripts/run-all-tests.sh` | 一键跑全量回归（含状态清理） |

---

## 3. 五个质量规则类

`check_quality()` 统一入口，五条规则各自独立、可分别断言：

| 规则 | 触发码 | 级别 | 可修复 |
|---|---|---|---|
| 事实支撑 | `FACT_UNSUPPORTED`（页面级） | error | ✅ 删引用即可 |
| 事实支撑 | `DANGLING_SOURCE_REF` / `CLAIM_WITHOUT_EVIDENCE` / `SNIPPET_ONLY_FACT` / `FABRICATED_EXPERIENCE`（母稿级） | error | ❌ 见下 |
| 数字一致性 | `NUMBER_MISMATCH` | error | ✅ |
| 模板边界 | `BODY_TOO_WIDE` / `BODY_LINE_TOO_LONG` / `TOO_MANY_LINES` / `HEADING_TOO_WIDE` | error | ✅ |
| 模板边界 | `TEMPLATE_WARN`（含密度不足、行长偏长等） | warning | 不阻塞 |
| 产物与字体 | `MISSING_ARTIFACT` / `FONT_UNAVAILABLE` | error | ❌ 环境问题 |
| 重复表达 | `REPEATED_EXPRESSION` | warning | 不阻塞 |

### 关键设计：母稿级问题**不可页内修复**

`DANGLING_SOURCE_REF` 这类问题出在 claims↔sources 之间，重写页面文字**永远修不掉**。
如果标成可修复，模型会一轮轮重写、白烧调用次数和费用，最后还是不合格。
所以这类统一标 `repairable=False`，直接交人工或回到研究阶段补来源。

`UNREPAIRABLE_CODES = {FONT_UNAVAILABLE, MISSING_ARTIFACT}` 采用**白名单式**封闭集合：
新码默认是可修复的，只有明确列的才不可修 —— 这样新规则上线不会"默认卡死"。

### 数字归一化

`extract_numbers("1,000") == extract_numbers("1000")`。
中文数字（一二三…）也参与比对，避免"提升三成"与"提升30%"被判成冲突。

`NUMBER_MISMATCH` **只在页面确实引用了 claim 时**才检查 ——
否则"3 步走"这类结构数字会被误判为事实错误。

---

## 4. 修复循环

```
检测 → 分类（可修 / 不可修）
        ├─ 不可修 → 直接 unrepairable，0 轮模型调用
        └─ 可修 → 先按规则改（确定性，免费）
                    └─ 仍有语义问题 → 才问模型（≤2 轮）
复检 → 仍不合格 → unrepairable，不产出半成品
```

**三条硬约束：**

1. **`local_seed` 永不调模型。** 已在测试中断言调用表计数不变。
2. **旧批准不被覆盖。** 修复产**新** revision + **新**平台稿，
   旧版本与旧批准原封不动。测试断言：旧稿仍 `approved`、新稿 `drafting`、
   批准记录数不变、旧稿标题仍是超长原样（未被就地改）。
3. **不产出半成品。** 两轮仍不合格 → `unrepairable=True`，**不创建新版本**。

修复后仍被如实交代的剩余项放在 `remaining`；`unrepairable` 是布尔值，
两者分工明确（前者是"还有什么"，后者是"能不能修"）。

---

## 5. 租约与 fencing_token

**为什么不能只靠时间判断过期：** 两个 Worker 可能同时认为自己在跑同一个 job。
A 卡住没死透、B 判定租约过期后接手，A 醒过来继续写库 → 产物重复追加、状态互相踩踏。

```
A 领取 → token=1
A 卡住，租约过期
B 接手 → token=2      (A 写入会带 token=1 → 被拒)
```

token **只增不减**，旧持有者永远追不上。测试覆盖：
未过期不可抢占 / 过期可回收 / token 递增 / 续租 token 不变 /
**旧 token 写入被拒且 job 状态未被改动**。

### 四类故障与处置

| 故障 | 检测 | 处置 | 自动？ |
|---|---|---|---|
| 租约过期 | `lease_expires_at < now` 且非终态 | 重新入队，token+1，attempt+1 | ✅ 自动 |
| 孤儿文件 | 磁盘有、DB 无 | 标 `orphan_file`，**只登记不删除** | ❌ 人工确认 |
| 产物缺失 | DB 有、文件无 | 已批准版 → `approved_artifact_missing`；其他 → `checking` | ❌ 人工决定 |
| **结果未知** | `ProviderCall.state == 'unknown'` | **先查状态，不重发**；费用保持 NULL | ❌ 人工确认 |

**"未知 ≠ 免费失败"** 是这一块的核心。未知可能意味着调用其实成功了 ——
盲目重发会双倍计费，且可能产生两份有效产出。测试断言扫描后调用记录数不变。

**已批准版本缺图不自动重渲染**：自动重渲染会让批准失效，
而"这个版本已经被批准过"这件事只有人能撤回。所以状态改为
`approved_artifact_missing` 并置顶到集中处理页。

### 本阶段发现并修复的真实缺陷

**`_scan_artifacts` 改了状态却没 commit。**
`with self.sf() as s:` 块退出时会回滚未提交的改动，
所以"已标记为 `approved_artifact_missing`"实际上从未落库 ——
平台稿仍停在 `approved`，人下次看到的还是"一切正常"，故障被静默吞掉。
这正是不变量第 3 条（失败不抹掉已有成功、但也不能假装没发生）要防的事。
修复方式：加 `dirty` 标记 + 在有变更时 `s.commit()`。

---

## 6. Worker

独立进程，与 API 解耦：

```bash
python -m app.worker --once          # 跑一轮就退出（适合验证）
python -m app.worker --interval 5    # 常驻，每 5 秒一轮
python -m app.worker --ticks 3       # 跑 3 轮
```

每轮：`scan()` → `_claim_next()` → `_run_job()` → `release()`。

- 单个 job 失败**不会拖垮 Worker**（捕获 `Exception` 后标 `failed` 继续）
- 无待办时是空操作，并明确回报 `"无待办 job"`（不是静默）
- 写心跳文件 `worker.heartbeat`；`GET /api/v1/health` 读它判断状态

**心跳新鲜度**：≥60 秒视为 `stale`。
**不把"曾经跑过"当成"现在在跑"** —— 这是很实际的区别，
不然页面显示"运行中"，人以为任务在推进，其实已经停了半小时。

状态有三态：`running` / `stale` / `not_running`（无心跳）。

---

## 7. 用量单一真相

**问题**：P0 用 `storage/provider_calls.json` 记账，P2 改成落 `provider_call` 表，
但 `/usage` 还在读那个 JSON。**同一件事两个真相来源**，
两边迟早对不上 —— 而且大概率是"页面显示 0 消耗、实际已经花钱"。

**修复**：`UsageService` 成为唯一读数入口，只读表。
JSON ledger 若仍存在，响应里**如实标注它已弃用**，而不是假装没这回事。

### 四条口径

1. **real / fixture / local_seed 显式分开**。fixture 的消耗不计入真实成本结论。
   测试用了一条 `999999` 的 fixture 记录，断言它**没有**渗进真实金额。
2. **金额未知不写 0**。缺金额的调用计入"待核实笔数"，不参与求和。
3. **不同币种不直接相加**（按币种分桶）。
4. **null ≠ 0**：未设金额上限 ≠ 零额度，也 ≠ 无限调用。

三态金额（`estimated` / `reported` / `reconciled`）各自独立累计，互不覆盖。

---

## 8. 预算门

| 模式 | 行为 |
|---|---|
| `usage_tracking`（默认） | 只记录，**不因金额阻塞** |
| `hard_cap`（可选） | 需上限 + 可估价依据，否则拒绝启用 |

`hard_cap` 的三种判定都已在测试中验证：

- 上限内 → 放行
- 超限 → 拦截（`blocking_kind = "money"`）
- **估不出价 → 暂停**（`blocking_kind = "unknown_price"`）

最后一条是重点：**"估不出价"不等于"不要钱"**。
如果未知估价当成 0 放行，硬上限就成了摆设。

非金额限制（条目数 / 库存 / 修复轮次 / 单次调用数）**与是否设上限无关，始终生效**。
已断言 `item_limit` 满时批次拒绝新任务（409）。

---

## 9. 集中异常处理

### 三条设计原则

1. **聚合不掩盖数量**。3 条同类同实体异常折叠成 1 条，但必须显示 `×3`、
   首次与末次时间。折叠是为了少看几行，不是让人以为只有一条。
   聚合键含 `entity_id`，所以不同实体的同类问题**分别列出**（这是有意的）。
2. **重大变更置顶**。`MAJOR_KINDS = {artifact_missing, model_privilege_violation,
   unknown_result, repair_exhausted}` —— 影响已批准版本的改动不埋在日常流水里。
3. **不做自动决策**。**只读 + 只记录**。不自动批准、发布、重试、删文件。
   职责是"把要人管的东西集中起来"，不是"减少人管的东西"。

### 写操作只有两个

`acknowledge`（已看到，开始计时）与 `resolve`（已处理完，结算时长）。
两个都只是**记录事实**，不改变任何业务状态。
接口响应里显式声明 `capabilities.read_only = true`、`auto_actions = []`。

### 人工打断时长：null ≠ 0

`handling_seconds` 找不到 `acknowledge` 记录时返回 **null**，
并附说明文案；**不拿当前时间硬凑一个数**。
统计时缺 ack 的处理单单列计数，**不并进平均**。
这套数据是 P5 估算真实人工负担的依据 —— 掺水就等于白记。

### 日志折叠

连续相同消息只留首条 + 计数，避免一屏重复把重要信息冲走。

---

## 10. 前端

| 页面 | 说明 |
|---|---|
| `Attention.html` | 集中处理：重大项置顶、折叠计数、时长（null 显示为 null）、已看到/已处理两个动作 |
| `UsageCosts.html` | 用量与消耗：按 run_mode 分列、已知/待核实分开、`hard_cap` 暂停语义 |

三个既有页面（`ApiSettings` / `ReviewPreview`）的标注同步更新到 P3。
四个页面均通过标签闭合校验。

前端在无后端时展示**契约演示数据**，数据结构与 `as_dict()` 严格对齐，便于直接替换。

---

## 11. 不变量对照

| # | 不变量 | P3 验证 |
|---|---|---|
| 1 | 无批准不出可发布包；旧批准不被新内容覆盖 | 修复产新版本，旧批准完好 |
| 2 | 无真实发布登记不自动标已发布 | 无 auto-publish 接口（404） |
| 3 | 重试不重复追加产物；失败不抹掉成功阶段 | fencing_token 拒旧持有者写入 |
| 4 | 未知结果 ≠ 免费失败；过期租约 ≠ 盲目重计费 | 未知结果不重发、费用 NULL |
| 5 | real/fixture/估算显式区分；缺失 ≠ 0 | fixture `999999` 未渗入真实成本 |
| 6 | 模型不能改批准/预算/身份/连接/发布状态 | 越权字段 `MODEL_PRIVILEGE_VIOLATION` |
| 7 | 评审用固定输入快照 | 沿用 P1/P2 快照机制，无退化 |
| 8 | 金额上限 null 不阻塞 usage_tracking；null ≠ 0 | 三种 `hard_cap` 判定 + `usage_tracking` 放行 |
| 9 | 保存设置不自动调用；未配置搜索不谎称已执行 | 未配置 Provider 不谎报 available |

---

## 12. 测试

```
$ ./scripts/run-all-tests.sh

===== p0_smoke =====   61 通过 / 0 失败
===== p1_e2e   =====   38 通过 / 0 失败
===== p2_e2e   =====   82 通过 / 0 失败
===== p3_e2e   =====  198 通过 / 0 失败
```

**P3 测试 9 组：** 质量规则 / 修复循环 / 租约与 token / 四类故障 /
Worker 与心跳 / 用量单一真相 / 预算门 / 异常集中处理 / 不变量汇总。

**全程零真实调用：** 手工造的 3 条 `real` 记录断言无 `remote_request_id`，
证明它们不是链路产生的。P3 链路本身只跑 fixture / local_seed。

### 测试写完后发现的两个真实缺陷

除上面提到的 `_scan_artifacts` 未提交外，还有：

**`FABRICATED_EXPERIENCE` 只扫 claims，不扫页面文本。**
"我亲测…"写在 caption 或正文里时，当前规则**看不见**。
测试把这条**如实断言为已知边界**（而不是假装拦住了）。
是否要扩展到页面文本，留待后续决定 —— 扩展会带来误报风险
（引用他人经历、转述用户原话等）。

### 测试卫生问题（已处理）

`storage/` 是仓库内共享目录，P0/P1 测试不隔离，
跨测试运行的残留（`profiles.json` 版本号累加、`test_p1.db`）
会造成假失败。已在 `scripts/run-all-tests.sh` 里统一做前置清理。
**长期建议**：让 P0/P1 也像 P2/P3 一样用临时目录隔离。

---

## 13. 已知限制

| 限制 | 影响 | 现状 |
|---|---|---|
| 真实 Provider 未配置 | 无法验证真实链路质量 | 用户计划 P3 后配置 |
| `platform_upload_verified = false` | 尺寸为工程排版值，非平台官方规格 | 需真机核对后翻转 |
| 平台上传规格未核对 | 发布包可能与平台要求不符 | P5 待办 |
| `FABRICATED_EXPERIENCE` 不扫页面文本 | caption 里的"亲测"漏检 | 已如实记录 |
| P0/P1 测试不隔离存储 | 需靠脚本前置清理 | 建议后续改成临时目录 |
| Worker 单进程 | 无并发压测验证 | 租约机制已设计支持多 Worker |
| 模板 warning 统一收敛为 `TEMPLATE_WARN` | 原始 code 只在 message 里 | 有意设计，测试按 message 断言 |

---

## 14. 复现方式

```bash
cd /workspace/content-workbench

# 全量回归（含状态清理）
./scripts/run-all-tests.sh

# 单独跑 P3
.venv/bin/python -B backend/tests/test_p3_e2e.py

# 起后端
.venv/bin/uvicorn app.main:app --app-dir backend --reload

# 起 Worker（另开一个终端）
.venv/bin/python -m app.worker --interval 5

# 健康检查（看 Worker 是否在跑）
curl -s localhost:8000/api/v1/health | python3 -m json.tool

# 集中处理清单
curl -s localhost:8000/api/v1/attention | python3 -m json.tool

# 用量（只读 provider_call 表）
curl -s localhost:8000/api/v1/usage | python3 -m json.tool
```

---

## 15. 退出标准核对

| 标准 | 状态 |
|---|---|
| 全部测试通过（P3 ≥ 70） | ✅ 198 |
| P0/P1/P2 回归无退化 | ✅ 61 / 38 / 82 |
| 四个任务均有 API 与前端入口 | ✅ |
| `/batches/{id}/runs` 真正可用 | ✅ 已实现并测试 |
| `integration_status` 仍为 `integration_pending` | ✅ 如实登记 |
| 完成报告入 `docs/P3-completion-report.md` | ✅ 本文件 |

---

## 16. 下一步

**用户侧（并行）：** 配置真实 Provider Key，翻转 `platform_upload_verified`。

**P4：** 数据反馈闭环 —— 发布记录、数据导入、评论导入、复盘、反馈下一轮。
**P5：** 试运营与交付 —— 安装启动、端到端测试、连续试跑、发布 v1。

P4 范围不变，见 `00-master-plan.md`。
