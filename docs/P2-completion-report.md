> 历史阶段报告。当前交付结果、计数与操作入口以 [TEST_REPORT.md](TEST_REPORT.md)、[DELIVERY.md](DELIVERY.md) 和 [QUICK_START.md](QUICK_START.md) 为准。

# P2 自动内容生产 · 完成报告

> 阶段：P2（T08–T11） · 日期：2026-10-02 · 设计基线 v1.1
> 目标：素材/题目 → 研究取证 → 自动选题 → 母稿 → 抖音 + 小红书双平台改写 → 渲染候审

## 一句话结论

**P2 工程部分完成。** 从"给一个题"到"拿到两套可审的成品图"全链路打通，含真实 Provider 适配器、
确定性 fixture 演练、来源可追溯、模型越权自动拦截。**82 项 P2 端到端测试全通过**，
连跑 P0/P1 回归后总计 **179 通过 / 0 失败**。

产出的每一页图都**没有发起过任何真实 API 调用**（`real_calls_recorded == 0`），
全部走 `local_seed`，界面与报告均如实标注，不冒充模型产出。

## 1. 双状态登记

| 维度 | 状态 | 说明 |
|---|---|---|
| development_status | ✅ **完成** | T08–T11 全链路可运行且通过 82 项验证 |
| integration_status | ⏸ `integration_pending` | 真实 Provider 端点未接；上传兼容性未核对 |

**为什么 integration 仍是 pending**：本阶段交付了真实适配器代码路径与调用记账，
但**没有配置过任何真实端点**（没有 API Key），因此"真实连通"这件事本身没被验证过。
代码可用 ≠ 端点可用，这两件事分开登记。

## 2. 交付物

| 任务 | 内容 | 路径 |
|---|---|---|
| T08 | Provider 运行时 + 4 个适配器 + 记账 | `services/provider_runtime.py`、`services/adapters/` |
| T09 | 研究取证与证据快照 | `services/research_service.py`、`services/claim_rules.py` |
| T10 | 自动选题（含理由与热度验证口径） | `services/topic_service.py` |
| T11 | 母稿 + 双平台改写（含修复循环） | `services/compose_service.py` |
| — | 编排服务（四阶段串线、幂等作业） | `services/production_service.py` |
| — | 生产 API（6 个端点） | `api/production.py` |
| — | 前端：API 设置页 / 预览页 P2 标注 | `frontend/src/views/ApiSettings.html`、`ReviewPreview.html` |
| 验证 | P2 端到端测试 82 项 | `backend/tests/test_p2_e2e.py` |

### 适配器清单

| 适配器 | 用途 | 类型 |
|---|---|---|
| `openai_compatible` | 文本：OpenAI 兼容 Chat Completions | TEXT |
| `anthropic_messages` | 文本：Anthropic Messages | TEXT |
| `search_http_json` | 搜索：HTTP JSON | SEARCH |
| `fixture` | 离线确定性演练（不出网） | TEXT/SEARCH |

## 3. 实测结果

```
P0 契约冒烟测试：59 通过 / 0 失败
P1 端到端测试：  38 通过 / 0 失败
P2 端到端测试：  82 通过 / 0 失败
                 ─────────────────
                 179 通过 / 0 失败
```

### 全链路实测（走 HTTP API，不是直接调函数）

```
POST /api/contents/produce   → 202，4.5 秒
{
  "ok": true,
  "rendered": {
    "douyin":      {"state": "ready_for_review", "pages": 5},
    "xiaohongshu": {"state": "ready_for_review", "pages": 6}
  },
  "failed": []
}
real_calls_recorded = 0
```

- 抖音 5 页、小红书 6 页，**全部为真实 PNG 文件**（非占位图）
- 尺寸取自 profile 版本（默认 1080×1440），换规格后测试自动跟随
- 每页正文 4–5 条，沿用 P1 的密度守门

## 4. 不变量验证（P2 相关）

| 不变量 | 验证方式 | 结果 |
|---|---|---|
| 模型不能改批准/预算/身份/权限（第 6 条） | 让模型在 JSON 里输出 `approval`/`budget`/`actor` 等字段 | ✅ **确定性拒绝，且不进修复循环** |
| 模型不能借修复绕过越权 | 越权字段故意放在第 2 轮才出现 | ✅ 直接判违规，不重试 |
| 未知结果 ≠ 免费失败（第 4 条） | Provider 返回不可判定状态 | ✅ 记 `unknown`，金额留 NULL |
| 重试不重复记账（第 3 条） | 同一 `request_key` 重复提交 | ✅ 唯一约束拦截，0 条重复 |
| 金额未知不写成 0（第 5/8 条） | 汇总接口含 unknown 记录 | ✅ 真实/演练/未知三类分列，未知单列待核实 |
| 搜索摘要不能当已核实事实 | 仅凭 snippet 下 `fact` 结论 | ✅ 按 `excerpt_basis` 拒绝 |
| 不编造亲测体验 | 稿件出现"我亲测/我用过"类措辞 | ✅ 拦截（否定语"没实测就不说亲测"正确放行） |
| 两平台不能只是换个名字 | 两版本文本相似度 ≥ 0.75 | ✅ 拒绝（"不得仅修改平台名称"） |
| 无批准不得产出发布包（第 1 条） | 生成后立即导出 | ✅ 拒绝，状态停在 `drafting`/`ready_for_review` |
| 稿件失败不留半成品 | 小红书改写失败 | ✅ 整体失败，不写库不出图 |
| `local_seed` 不得发起调用 | 注入 runtime 后跑 local_seed | ✅ 一次都没调 |

## 5. 过程中发现并修复的真实缺陷

全部由测试跑出来，不是推演：

1. **规则生成器写出自己校验器不接受的稿** —— 修复循环空转到上限后报
   `TITLE_TOO_LONG` / `BODY_TOO_WIDE`。根因：`_variant_by_rules` 不知道 profile 限额，
   用魔法数字估行宽。已改为**直接读 profile 限额**，行宽用与校验器**同一个**
   `estimate_text_width` 函数计算，从源头对齐。
2. **fixture 产出页码不连续** —— 合成器用随机数生成 `index`，导致 "页序不连续：[3,4,3]"。
   已改为按真实页序返回，并让 `layout` 首页固定为 `cover`。
3. **fixture 生成悬空 claim 引用** —— 编造的 `C01` 在母稿里不存在。已改为
   **从提示词里提取已存在的 claim id**，只复用不新造。
4. **越权校验误伤自我约束句** —— 稿件写"没实测就不说亲测"，被"亲测"关键词拦下。
   已加**否定词窗口判断**，并在 claim 类型精确时交由更细的规则判定。
5. **`local_seed` 仍去调模型** —— 只要注入了 runtime 就会调用，与运行模式语义矛盾。
   已改为 `local_seed` **无条件跳过模型**。
6. **选题理由清洗不彻底** —— 只清洗了被选中的候选，未入选的仍带"未做热度验证"字样，
   会漏到溯源面板。已改为**全部候选统一清洗**。
7. **Playwright 同步 API 不能跑在 asyncio 事件循环里** —— API 里渲染直接崩。
   已改为**隔离线程渲染**，并在流水线里自动判断当前是否在事件循环中。
8. **fixture 适配器被反复重建导致注入场景失效** —— 修复循环第 2 轮拿不到预期响应，
   测试无法验证"1 轮修复后成功"。已加**适配器缓存与合成配置缓存**。
9. **P1 测试把尺寸写死成 1242×1660** —— 与 profile 版本脱节。已改为
   **从 profile 版本读取**，不再是魔法常量。

> 第 1 条最能说明问题：**生成器与校验器用了两套宽度算法**，
> 表面上"生成失败"，实际是同一件事被算了两次且结果不同。合并成一处后消失。

## 6. 已知限制（如实登记，不掩饰）

| 限制 | 现状 | 何时补 |
|---|---|---|
| 真实 Provider 从未连通 | 4 个适配器代码路径已就绪，但无端点无密钥，**未验证过** | Rosso 配一次即可 |
| 上传兼容性未核对 | `platform_upload_verified = false` | 需 Rosso 看一次发布界面 |
| 批次调度未实现 | `POST /batches/{id}/runs` 尚未接线 | P3 · T13（不假装已实现，已在模块注释说明） |
| 独立 Worker 进程 | 表结构已备，进程未起 | P3 · T13 |
| 自动修复策略 | 稿件级修复已有（上限 2 轮），图文级自动修复未做 | P3 · T12 |
| 用量追踪与限额闭环 | 记账已落库，聚合告警与限额阻塞策略未做 | P3 · T14/T15 |
| Alembic 迁移 | 仍用 `create_all` 建表 | P3 |

> **fixture 不是真实效果。** 演练数据只证明"链路能走通、字段能对上"，
> 不代表真实模型的产出质量。报告里二者始终分列。

## 7. 复现方式

```bash
cd /workspace/content-workbench
rm -f storage/profiles.json storage/provider_configs.json storage/secrets.json
.venv/bin/python backend/tests/test_p0_smoke.py   # 59 项
.venv/bin/python backend/tests/test_p1_e2e.py     # 38 项
.venv/bin/python backend/tests/test_p2_e2e.py     # 82 项
```

> 上述三个测试各自使用独立临时目录（`tempfile.mkdtemp`），互不污染；
> 清理 `storage/` 下三个状态文件是为了避免上一轮持久化状态干扰。

## 8. 下一步

进入 **P3 低干预与恢复**（T12–T15）：图文级自动修复、异常聚合、
用量追踪与限额闭环、断点恢复。届时 `integration_pending` 项仍需 Rosso 配合核对一次真实端点与发布界面。
