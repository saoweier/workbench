> 历史阶段报告。当前交付结果、计数与操作入口以 [TEST_REPORT.md](TEST_REPORT.md)、[DELIVERY.md](DELIVERY.md) 和 [QUICK_START.md](QUICK_START.md) 为准。

# P5 v1 发布报告：试运营验收与交付

> 对应 `examples/C001/docs/04-development-plan.md` P5 阶段（T21–T24）
> 完成日期：2026-10-02
> 离线工程回归：**873 通过 / 0 失败**（P0 61 + P1 40 + P2 82 + P3 199 + P4 279 + P5 202 + 运维 10；Windows 实际运行）
> 版本标识：`v0.1.0-p5`（服务自述 `v0.1.0-p0` 起未变，P5 不引入破坏性变更）

---

## 1. 双态登记表

| 任务 | 内容 | development_status | integration_status |
|---|---|---|---|
| T21 | 启动与恢复交付（一键脚本 / 备份恢复） | ✅ 完成 | `integration_verified`（本地）|
| T22 | 端到端与边界测试（按 05 关键场景验收） | ✅ 完成 | `integration_verified`（离线）|
| T23 | 连续试跑 6 个内容包 | ⏸ **未开工（等真实数据）** | `blocked_on_user` |
| T24 | 发布 v1（验收报告 / 已知限制 / 回退方法） | ✅ 完成 | `integration_pending` |

**为什么 T24 的 `integration_status` 不是 `integration_verified`：**

P5 验证的是**工程链路可用**：一键起服务、10 个页面真连后端、
C001 真跑出 11 张图和可下载的发布包、边界场景如实报错。
但**外部集成验收**（真实 Provider 出稿、真实平台上传、真实后台数据复盘）
依赖三件用户侧的事，目前都还没有：

| 外部依赖 | 现状 | 影响 |
|---|---|---|
| 真实 Provider API Key | 未配置 | 真实生成路径未跑过；本地 seed 路径可用 |
| 抖音/小红书上传规格核对 | `profile_upload_verified = false` | 图尺寸/字数按设计规格，未经平台实机核对 |
| 真实后台导出文件 | 未提供 | 导入/复盘只在手造样本上验证 |

**缺失的实测结果不得填成"已达到"** —— 这正是文档 §7 交付清单里
"如缺失，明确完整闭环尚未验收"的要求。

---

## 2. 交付物清单

### 2.1 新增（P5 段）

| 类型 | 文件 | 说明 |
|---|---|---|
| 测试 | `backend/tests/test_p5_e2e.py` | 202 项，11 组（本阶段核心产出）|
| 页面 | `frontend/src/views/Production.html` | 补上缺失的「内容生产」导航目标 |
| 报告 | `docs/P5-completion-report.md` | 本文件 |
| 脚本 | `scripts/run-all-tests.sh` | 扩到 P0–P5 |

### 2.2 重写（P5 段：8 个静态演示页 → 真连后端）

原本这 8 个页面是**手写的静态演示**，字段名与真实 API 有出入，
点按钮只 `console.log`。P5 全部重写为真调后端：

| 页面 | 改动要点 |
|---|---|
| `index.html` | 首页概览真读 `/contents/{id}` 与 `/reviews`；一键出成品真跑 |
| `ReviewPreview.html` | 真批准（带 `expected_manifest_hash`）、真导出 ZIP、页图真加载 |
| `Publications.html` | 真登记发布、真核验（`declared` / `verified` 分列）|
| `DataImport.html` | 真 CSV 上传、真 dry_run、真看明细、待匹配行可人工绑定 |
| `ReviewInsights.html` | 真聚类（带原文例证）、真复盘、真候选草案 |
| `FeedbackLoop.html` | 真采用/拒绝/回退、真三条并列限制面板 |
| `Attention.html` | 真标记已看到/已处理，真结算处理时长（`null` 如实显示）|
| `UsageCosts.html` | 真读币种分字典、真试算表单 |
| `ApiSettings.html` | 真 CRUD、真本地校验 / 真实连通测试 |

### 2.3 完整交付物

| 类型 | 文件 |
|---|---|
| 服务 | `backend/app/services/`（22 个模块）|
| 模型 | `backend/app/models/entities.py`（19 张表）|
| API | `backend/app/api/`（14 个路由模块，59 条路径 / 67 个操作）|
| 前端 | `frontend/src/`（10 页面 + `app.js`，**无构建**，原生 HTML/CSS/JS）|
| 模板 | 抖音/小红书 profile + 渲染模板（`cover@1` / `checklist@1`，版本化）|
| 脚本 | `start.sh` / `stop.sh` / `backup.sh` / `restore.sh` / `run-all-tests.sh` |
| 测试 | `backend/tests/test_p0..p5*.py`（6 个阶段）|

---

## 3. T21：启动与恢复

### 3.1 一键启动

```bash
./scripts/start.sh          # 依赖检查 → 建库 → 起 API(含前端) → 起 Worker → 报状态
./scripts/stop.sh           # 按 pidfile 停，兜底 pkill 残留
CWB_PORT=8001 ./scripts/start.sh   # 换端口
```

启动脚本的四条设计取舍：

1. **前端由 API 进程托管**。前端是无构建的静态页，`FastAPI` 直接
   `FileResponse` + `StaticFiles`，不需要 node/npm，**启动脚本因此只拉一个服务**。
2. **等健康检查真通过才宣布成功**。轮询 `/api/v1/health`，最多 30 秒；
   超时就把 `api.log` 末尾 15 行打出来，而不是只说一句"启动失败"。
3. **端口占用先报清楚**，不让 uvicorn 抛一堆栈。
4. **Chromium 缺失单独检查**。缺了不会立刻报错，只会在出图时失败——
   那种延迟失败最难查，所以提前提示。

### 3.2 备份与恢复（A21）

```bash
./scripts/backup.sh                       # → storage/backups/cwb-backup-<时间戳>.tar.gz
./scripts/restore.sh <备份> [--force]
```

**备份包含**：`storage/cwb.db`（含 WAL 的 SQLite 快照）、`storage/artifacts`（图片与发布包）、
`storage/profiles.json`、`storage/provider_configs.json`。

**备份排除**：`storage/secrets.json`。**密钥故意不打包**——
备份常常被随手拷到别处，密钥不该跟着走。恢复时沿用当前那一份并明确提示。

**恢复的三道防线**：

1. 检测到 uvicorn/worker 在跑 → 拒绝恢复；先运行 `stop.bat` / `stop.sh`。`--force` 只跳过覆盖确认；
2. 已有数据时要求输入 `yes` 确认（**恢复错方向比没有备份更糟**）；
3. 覆盖前留存 `pre-restore-<时间戳>.tar.gz` 保底快照。

### 3.3 演练结果

用独立测试目录恢复了数据库与产物：历史版本可读、`manifest_hash` 有效、
任务状态可回收，与 A21 的通过条件一致。

---

## 4. T22：端到端与边界测试

`backend/tests/test_p5_e2e.py`，**202 项 / 0 失败**，11 组：

| 组 | 覆盖 | 项数 |
|---|---|---|
| [1] | 前端托管：`/` + 10 页 + `app.js`；非法页名与路径穿越 404 | 22 |
| [2] | 一键出成品：双平台稿 + PNG 页图 + 页号越界 | 25 |
| [3] | 无材料不硬编：选题硬条件阻塞；seed 路径错误带清单 | 7 |
| [4] | 不变量 1：无批准 409 → 批准出包 → 已导出再出包 409 + ZIP 校验 | 20 |
| [5] | 不变量 6 / A06：非可信 actor 与过期批准（批准错版） | 12 |
| [6] | 批次：`null ≠ 0 额度` / 缺 topic / 已关闭批次 | 15 |
| [7] | 用量与经济：唯一真相 / `null ≠ 0` / 不强迫填预算（A24） | 19 |
| [8] | A23/A25/A26：未配置 API 与搜索 / 保存不触发调用 | 16 |
| [9] | A19：执行命令只当内容 | 16 |
| [10] | 前端契约：页面引用的 API 路径都真实存在 | 33 |
| [11] | 全链路零真实调用、零费用 | 9 |

### 4.1 本阶段锁住的回归项

这些都是**真实运行时才暴露**、且都曾真实出过问题的地方，现在钉成断言：

| 曾经的缺陷 | 现在的断言 |
|---|---|
| `/api/v1/reviews` 这个路由**不存在**，首页却去调它 → 404 | [10] 从页面源码抽出 API 路径，逐一比对 `openapi.json` |
| `/contents` 列表项**不带** `platform_revisions` → 首页平台列永远"未出图" | [2] 断言列表项真实字段 + 按需取详情 |
| `/packages` 漏传参数 → 500 | [4] 断言 422 且带可读 message |
| `/feedback/drafts` 是 **GET** 带 query 参数，POST 会 405 | [9] 断言 POST → 405 |
| `/attention/acknowledge` **没有 `note` 参数**（不像 resolve） | [9] 断言只传三个字段即成功 |
| 无 ack 直接 resolve → `handling_seconds` 被凑成 0 | 断言返回 `null`（P3 已覆盖，P5 复验） |
| `produce-from-seed` 传 `run_mode=real` 静默走进真实调用 | [3] 断言 422 并指明改用 `/contents/produce` |

### 4.2 页面是真的连了后端

[10] 组不是"看着像"，而是机械核对：

- 从 10 个页面的源码里正则抽出 `/xxx` 形态的调用路径；
- 每条路径的顶层段必须在真实 `openapi.json` 里找到对应路由；
- 每个页面必须含有 `api.get` / `api.post` / `fetch(`（否则它只是静态演示）；
- **凡发起写操作的页面，必须显式带 `actor`** —— 不变量 6 在界面层的落点。

### 4.3 关键边界：批准错版（A06）

文档要求"用户看旧预览时后台有新稿 → approve 返回版本冲突；旧批准不对应新图"。
实现方式是 `expected_manifest_hash`：

```
POST /review-decisions
{ "decision":"approve", "actor":"rosso",
  "platform_revision_id":"...", "expected_manifest_hash":"<用户看到的哈希>" }
```

哈希不匹配时返回 `conflicts[{expected, actual}]`，**同时给出两个值**，
用户能一眼看出"我看的那版"和"现在的这版"差在哪。测试还断言：
被拒的批准**没有**改动平台稿状态、**没有**污染内容态。

决策组是 **all-or-nothing**：一个冲突 → 整组 `applied: []`，
不给"假装部分成功"的结果。

---

## 5. 验收场景对照（05 §2 必测场景）

| ID | 场景 | 覆盖 | 位置 |
|---|---|---|---|
| A01 | C001 双平台导出 | ✅ | P1 + P5[2][4] |
| A02 | 自动流程无异常 | ✅ | P2 + P5[2] |
| A03 | 引用不存在/关键事实无证据 | ✅ | P3[1] |
| A04 | 文本过长、字体缺失、图片失效 | ✅ | P3[1][4] |
| A05 | 只修改某一页 | ✅ | P2 |
| A06 | 用户看旧预览时后台有新稿 | ✅ | **P5[5]** |
| A07 | 批次重复提交 / HTTP 重试 | ✅ | P2[9] + P3 |
| A08 | Worker 生成后被终止 | ✅ | P3[4][5] |
| A09 | 租约过期后旧 Worker 返回 | ✅ | P3[3] |
| A10 | Provider 超时但可能已收费 | ✅ | P3[4] |
| A11 | 硬预算耗尽、实际超估算 | ✅ | P3[7] |
| A12 | 取消时远端请求仍运行 | ✅ | P3 |
| A13 | 单平台失败 | ✅ | P2 + P3 |
| A14 | 未批准导出/下载后状态 | ✅ | **P5[4][5]** |
| A15 | 重复导入同一文件 | ✅ | P4[4] |
| A16 | 缺失、0、时间区间、不同分母 | ✅ | P4[3] |
| A17 | 无作品匹配、截图数值歧义 | ✅ | P4[5] |
| A18 | 复盘没有足够数据 | ✅ | P4[8] |
| A19 | 网页/评论包含执行命令 | ✅ | **P5[9]** |
| A20 | 超出本地目录的路径或内网 URL | ✅ | P2 + P5[1] |
| A21 | 备份恢复 | ✅ | **P5[3]**（脚本 + 演练）|
| A22 | 自动下一轮达到库存/额度上限 | ✅ | P3[7] + P4[9] |
| A23 | 未配置 API 或搜索 | ✅ | **P5[8]** |
| A24 | usage_tracking，金额上限为空、价格未知 | ✅ | **P5[7]** |
| A25 | Provider 只有 tokens，无金额或 usage 缺失 | ✅ | P3[6] + **P5[7]** |
| A26 | API 配置保存与测试 | ✅ | **P5[8]** |

**A01–A26 全部有自动化断言。** 加粗为本 P5 段新增覆盖。

### 5.1 T23 连续试跑：明确未开工

文档要求"建议 6 个内容包，真实记录产物、人工分钟、异常、费用、发布情况"。
**用户已明确选择"等我的真实数据"**，因此本阶段不跑，
也不预填任何实测数字。`integration_status = blocked_on_user`。

---

## 6. 不变量落点（1–9）

| # | 不变量 | P5 断言 |
|---|---|---|
| 1 | 无批准不出包；过期批准不能覆盖新内容 | P5[4] 409 → 批准 → 201 → 再出包 409；P5[5] 过期哈希被拒 |
| 2 | 无真实发布登记不能自动标已发布 | P5[9] `auto_publish: false`；登记后仍 `declared` |
| 3 | 重试不重复产物；失败不抹掉前序成功阶段 | P5[11] 二次出成品 `before==after`；P5[3] 阻塞不抹掉研究结果 |
| 4 | 未知 provider 结果 ≠ 免费失败 | P3[6] + P5[7] `unpriced_calls` 单列 |
| 5 | 真实/fixture/估算区分；**缺失 ≠ 0** | P5[7] `null` 原样 + "不是 0 额度"；P5[9] `like_count` null |
| 6 | **模型不能改批准/预算/身份/连接权限/发布状态** | P5[5] 5 种非人 actor 全拒；P5[9] 7 个"自动"接口不存在；P5[10] 页面写操作必带 actor |
| 7 | 复盘用固定输入快照 | P4[8] |
| 8 | 金额 null 不阻塞 usage_tracking；null ≠ 0 | P5[7] 试算 `allowed=true`；`null ≠ 无限调用` 明示 |
| 9 | 保存 API 设置不自动调用；未配置搜索不冒称已执行 | P5[8] `called_provider=false`；`search_executed=false` + 来源标注 local_seed |

---

## 7. 已知限制

### 7.1 外部集成未验收（需用户侧动作）

| 限制 | 现状 | 解除条件 |
|---|---|---|
| 真实 Provider 生成路径 | 未跑过（无 Key） | 在「API 设置」填 Key 并启用，跑一次 `/contents/produce` 带 `run_mode=real` |
| 平台上传规格 | `profile_upload_verified = false` | 用真实账号核对抖音/小红书的图尺寸与字数，在「API 设置」页点「标记已核对上传兼容性」 |
| 真实后台数据导入 | 只验证过手造 CSV/JSON | 提供一份真实导出文件；列名/时区/"万"单位习惯可能需加映射 |
| 复盘结论质量 | 只在 4 条评论样本上验证 | T23 连续试跑；样本量太小时系统已如实标注 `data_sufficiency` |

### 7.2 设计上就不做的事（不是缺口）

- **没有 `POST /auto-publish`**：发布由人完成，系统只记录。
  「已发布」是查询时算出来的，不是可写字段。
- **没有自动批准**：批准只来自可信会话（`TRUSTED_ACTORS`），模型/客户端无法伪装。
- **没有自动匹配/自动补值**：导入的行匹配不上就进待办，不猜绑定（猜错会污染整个复盘）。
- **没有定时备份**：首版手动一键备份，不创建系统定时任务（文档明确要求）。
- **没有视频生成 / 平台自动连接**：这两个尚未接入，属 v1 之后。

### 7.3 技术债（已登记）

| 项 | 说明 | 影响 |
|---|---|---|
| 集合无分页 | `/contents`、`/runs`、`/imports` 是 `limit` 截断，无游标 | 数据量大时列表性能下降；当前个人规模无感 |
| 前端无构建 | 原生 JS，无 TS 类型检查、无打包压缩 | 改动直观、零依赖；代价是无静态校验 |
| SQLite 单机 | WAL + 单写者 | 单人多机需改 Postgres（ORM 层已隔离）|
| 提示词版本硬编码 | 提示词版本号写在服务里 | 改提示词需改代码；已在 `provider_call.prompt_version` 留痕可追溯 |

### 7.4 已修复的重大缺陷：测试脚本删生产数据

**这个必须单独记，因为它是 P5 交付当天真实发生过的事故。**

`scripts/run-all-tests.sh` 在每跑一个阶段前执行：

```bash
rm -rf storage/profiles.json storage/provider_configs.json storage/secrets.json \
       storage/cwb.db storage/test_p1.db storage/worker.heartbeat storage/artifacts/*
```

这行是 P0/P1 还没有存储隔离时留下的"清场"动作。等 P0 也隔离之后，
它已经**纯粹是副作用**，但注释里只更新了"P0 已加隔离"，清理没删。
结果是：跑一次全量回归 = 删掉生产库 + 22 张成品图 + 发布包。

**为什么难查**：`storage/cwb.db` 被删后，已在运行的 uvicorn 仍持有文件句柄，
API 照常返回数据 —— 看起来一切正常。只有页图 404、页面显示"图未生成"，
而数据库里 `artifact_count` 还写着 5。这种"库说有一致、文件说没有"的
分裂状态，从现象到根因要跨三层。

**修复（两处，缺一不可）：**

1. `backend/tests/test_p1_e2e.py` 补上 `CWB_*` 环境变量隔离
   （原先它直接写 `storage/test_p1.db`，还 `rmtree(artifact_dir/"C001")`）；
2. `scripts/run-all-tests.sh` 删掉整段清理，改成只打一行提示。
   现在 P0–P5 **六个阶段全部**自带 `mkdtemp` 隔离，脚本不再需要动生产目录。

**验证方式**：跑测试前后对 `storage/cwb.db` 取 md5 比对，一致才算通过。
数据从 `storage/backups/cwb-backup-20261002-101133.tar.gz` 恢复
（22 张 PNG + 数据库 + profile 规格），恢复后页面图重新可访问。

**顺带确认了一件事**：`backup.sh` 的排除策略是对的 —— 备份里没有
`secrets.json`，恢复时明确提示"密钥未随备份覆盖，需要重新填"。
真出事的时候，这条设计反而省了一次密钥泄漏的风险。

### 7.5 Windows 全量回归发现的跨平台问题

首次在 Windows 上实际执行 P0–P5 时，P1 的 ZIP manifest 将反斜杠写入图片路径，
导致清单里的图片无法按 ZIP 标准路径读取；P3 的字体探测使用系统默认 GBK
解码 `fc-list` 的 UTF-8 输出，导致 stdout 为空并令质量检查崩溃。

修复后，渲染器把新产物的 `storage_key` 统一写成正斜杠，ZIP manifest 对旧路径
也按两种分隔符提取文件名；字体探测明确使用 UTF-8，且无可用输出时安全降级。
回归新增 P1 两项、P3 一项断言。Windows 全量结果为 **859 通过 / 0 失败**；
运行前后数据库 SHA-256 一致，产物文件数量保持 23。

### 7.6 备份恢复与测试入口加固

Python 备份现使用 SQLite backup API，在线备份包含 WAL 中已提交的数据；
恢复校验归档成员、拒绝越界路径和链接，并只恢复 storage 下的数据文件。
恢复前的快照覆盖数据库、产物和配置，不包含密钥。Windows 服务检测改查进程命令行；
停止脚本将进程范围限制在本项目虚拟环境，避免结束其他项目的 Python 服务；
测试入口显式启用 UTF-8，兼容 Windows 中文控制台。Unix 的 shell 入口复用同一套 Python 实现。
运维回归现有 9 项，覆盖 WAL、密钥排除、合法往返恢复、路径穿越拒绝和脚本入口。

---

## 8. 回退方法

### 8.1 退回上一版数据（最常用）

```bash
./scripts/stop.sh
./scripts/restore.sh storage/backups/cwb-backup-<时间戳>.tar.gz --force
./scripts/start.sh
```

`restore.sh` 会先留存 `pre-restore-<时间戳>.tar.gz`，
运行服务时即使带 `--force` 也会拒绝恢复，必须先手动停止服务。
**恢复错了可以再恢复回来**。

### 8.2 退回代码版本

仓库无外部依赖服务，回退 = 换代码 + 换数据：

```bash
git log --oneline            # 找到目标提交
git checkout <目标提交>
./scripts/stop.sh
./scripts/restore.sh <该版本时期的备份> --force
./scripts/start.sh
```

**注意**：`Base.metadata.create_all()` 只建表不删列，
从新版本退到旧版本时，多出来的列/表会被忽略（SQLite 行为），
不会立刻报错，但也不会自动清理。**升级前必须备份**（文档要求）。

### 8.3 只回退一个内容包

不需要动数据库。内容包的状态机本身支持重来：

```
要求修改（changes_requested）→ 生成 v2 → 重新预览 → 重新批准 → 重新导出
```

旧版本与旧批准**保持不变**，新版本需要重新批准 —— 这是不变量 1 的设计结果，
也是"退回上一版"在内容层面的等价物。

### 8.4 完全清空重来

```bash
./scripts/stop.sh
rm -rf storage/cwb.db storage/cwb.db-wal storage/cwb.db-shm \
       storage/artifacts storage/provider_configs.json
./scripts/start.sh           # 自动重新建库
```

`storage/secrets.json` 保留则密钥还在；删掉则需重填。
**这一条会丢掉所有数据**，只在确认不需要时使用。

---

## 9. 验收证据

| 证据 | 位置 / 命令 |
|---|---|
| 全量回归 | P0–P5 + 运维边界 → **873 通过 / 0 失败** |
| 运维边界 | `backend/tests/test_operations.py` → **8 通过 / 0 失败** |
| P5 专项 | `.venv/bin/python backend/tests/test_p5_e2e.py` → 202 通过 |
| 10 页可点 | 浏览器点开全部页面：**0 console error / 0 page error / 0 失败请求** |
| 页图真实加载 | 首页预览图 **11/11** 真实加载（HTTP 200 `image/png`）|
| C001 成品 | 一键出成品 → 抖音 5 页 + 小红书 6 页 PNG；`C001-douyin-pr1.zip` ≈ 500 KB |
| 发布包内容 | `images/page-01..05.png`、`caption.txt`、`manifest.json`、`checklist.md` |
| 批准哈希绑定 | `manifest_hash 3bdc37258628…` 写入 `ReviewDecision` |
| 零真实调用 | 全链路 `real_calls_recorded == 0`，无 `remote_request_id` |
| 处理时长 | 集中处理页：`已结算处理单 2 单 累计 2.6s 平均 1.3s` |

### 9.1 一次完整的手动点通记录（真实 UI 文案）

```
首页   一键出成品  → 已完成 · run bbdc4248 · 真实调用 0 笔（本次零费用） · 离线基线
集中预览          → 已记录「通过」（actor = rosso）。批准绑定 manifest_hash 3bdc37258628…
                  → 已生成 C001-douyin-pr1.zip（499.8 KB）
发布记录          → 登记成功（3 行，6 个候选平台版本）
数据导入          → dry_run 后正式：状态 imported · 批次 02893641
评论与复盘        → 取样口径：full_export · 样本量 4 · 平台 douyin · 分类方式 rule_based
                    3 个分类组 / 4 条原文例证
反馈下一轮        → 三条并列限制：批次条目数未指定 / 待预览库存 0/3 / 金额上限 null
集中处理          → 已结算处理单 2 单 累计处理时长 2.6 s 平均 1.3 s
用量              → 0 总调用笔数 · 未知 真实调用·服务商已报金额 · 0 无可估价依据的笔数
API 设置          → 1 条已保存配置 · platform_upload_verified = false
内容生产          → 9 Runs · 3 条内容
```

---

## 10. 试运营指标（T23 未开工，此处留位）

文档 §4 要求连续记录 6 个内容包并统计：
常规审核轮次、人工打断次数、人工审核分钟、人工操作分钟、
自动修复率、合格成品成本、周转历时。

**本轮不填任何数字。** 依用户明确选择（"等我的真实数据"），
T23 待真实平台数据到位后开展。数量是工程试跑样本，不代表市场结论。

---

## 11. 快捷命令

```bash
./scripts/start.sh                          # 一键启动（API + 前端 + Worker）
./scripts/stop.sh                           # 停止
./scripts/run-all-tests.sh                  # 全量回归 P0–P5
./scripts/run-all-tests.sh p5_e2e           # 只跑 P5
./scripts/backup.sh                         # 备份（含产物，不含密钥）
./scripts/restore.sh <备份> --force         # 恢复

# 最短路径：看效果（离线基线，零费用）
curl -sX POST localhost:8000/api/v1/contents/produce-from-seed \
  -H 'Content-Type: application/json' -d '{}'

# 看某一稿的页图
curl -sI localhost:8000/api/v1/platform-revisions/<pr_id>/pages/1

# 批准 + 导出发布包（actor 必须是可信会话）
curl -sX POST localhost:8000/api/v1/review-decisions -H 'Content-Type: application/json' \
  -d '{"decision":"approve","actor":"rosso","platform_revision_id":"<pr_id>",
       "expected_manifest_hash":"<哈希>"}'
curl -sX POST localhost:8000/api/v1/packages -H 'Content-Type: application/json' \
  -d '{"platform_revision_id":"<pr_id>","actor":"rosso"}'

# 查看用量与限额（null ≠ 0）
curl -s localhost:8000/api/v1/usage | python3 -m json.tool
```

---

## 12. 退出标准核对

| 标准 | 状态 |
|---|---|
| 一键脚本拉起后端 + 前端 | ✅ `./scripts/start.sh` |
| 前端页面真连后端 | ✅ 10 页，0 console error |
| C001 真跑出图与发布包 | ✅ 11 页 PNG + `C001-douyin-pr1.zip` |
| 全部测试通过（P5 ≥ 100） | ✅ 202 |
| P0–P4 回归无退化 | ✅ 61 / 40 / 82 / 199 / 279 |
| A01–A26 均有自动化断言 | ✅ 见 §5 |
| 不变量 1–9 均有落点断言 | ✅ 见 §6 |
| 已知限制入文档 | ✅ 见 §7 |
| 回退方法可执行 | ✅ 见 §8 |
| T23 未开工如实登记 | ✅ `blocked_on_user` |
| 完成报告入 `docs/P5-completion-report.md` | ✅ 本文件 |

---

## 13. 下一步

**用户侧（解除外部集成验收的唯一路径，三件并行）：**

1. 在「API 设置」页填入真实 Provider Key 并启用，跑一次真实生成；
2. 用真实账号核对抖音/小红书的图片尺寸与字数规格，
   在「API 设置」页点「标记已核对上传兼容性」（翻转 `platform_upload_verified`）；
3. 提供最少 2 个内容包的真实后台导出文件（指标 + 评论）。

**数据到位后：** 开展 T23 连续试跑（6 个内容包），
按 §10 统计人工负担与成本，补齐 §10 表格，
再把 `integration_status` 从 `integration_pending` 翻成 `integration_verified`。

**v1 之后（候选）：** 视频生成、平台连接（自动上传）、
提示词外部化（不用改代码就能改提示词）、列表分页。
