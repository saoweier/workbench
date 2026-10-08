> 历史阶段报告。当前交付结果、计数与操作入口以 [TEST_REPORT.md](TEST_REPORT.md)、[DELIVERY.md](DELIVERY.md) 和 [QUICK_START.md](QUICK_START.md) 为准。

# P0 工程契约 · 完成报告

> 阶段：P0（工程契约） · 日期：2026-10-01 · 对应设计基线 v1.1
> 负责人：Coisini（工程） / Rosso（决策确认）

## 一句话结论

**P0 的工程部分已完成并通过验证，可以进入 P1。** 四项原阻塞（C001 成品、平台登录、Provider 选型、预算数字）**全部解除**——P0 不再需要用户预先交任何东西。

## 1. 双状态登记

| 维度 | 状态 | 说明 |
|---|---|---|
| development_status | ✅ **完成** | seed 校验器、可调规格、API 设置契约、用量模型均已实现并测试通过 |
| integration_status | ⏸ `integration_pending` | 真实平台上传兼容性、真实 Provider 连通、真实账单对账——均待 P1/P2 接入 |

**不做的事**：不把"设计完成"当"业务已验证"；不把 fixture 结果当真实效果；不把金额未知写成 0。

## 2. 交付物清单（`/workspace/content-workbench/`）

| 任务 | 交付物 | 路径 |
|---|---|---|
| T01 | seed 校验器（JSON / 页序 / 引用 / 来源存在性） | `backend/app/services/seed_validator.py` |
| T02 | 可调发布规格 Profile（版本化、不可变历史） | `backend/app/services/profile_store.py` |
| T03 | Provider 配置契约 + 用量追踪 + 预算策略 | `backend/app/services/provider_contract.py` |
| T03 | 密钥存储（0600 权限，只回掩码） | `backend/app/services/provider_contract.py::SecretStore` |
| T04 部分 | FastAPI 应用装配 + 健康检查 | `backend/app/main.py` |
| T02/T03 API | `profiles` / `providers` / `usage` / `seeds` 四组接口 | `backend/app/api/*.py` |
| T02/T03 UI | API 设置页（规格 + Provider + 成本） | `frontend/src/views/ApiSettings.html` |
| 验证 | P0 冒烟测试 57 项 | `backend/tests/test_p0_smoke.py` |
| 资料 | C001 开发输入（含 seed 与来源） | `examples/C001/` |
| 资料 | v1.1 设计文档副本 | `examples/C001/docs/` |

## 3. 验证结果

```
结果：57 通过 / 0 失败
P0 契约冒烟测试全部通过
```

关键验证点：

**T01 内容基线**
- C001 seed 校验**通过**（无阻断项、无警告）
- 抖音 5 页、小红书 6 页，页序连续且从 1 开始
- 4 个 claim、3 个 source 的引用**无悬空**
- 3 个来源相对路径**真实存在**（README 15996 / C001-production-pack 9871 / topics 4456 bytes）
- `render_profile.platform_upload_verified = false`，未被伪造成已验证

**T02 发布规格**
- 默认 1080×1440 PNG，`platform_upload_verified=false`
- 修改尺寸 → 版本号递增到 2，**新版本仍为 verified=false**
- 旧版本内容**未被修改**，仅标记 `superseded_by`
- 非法页数范围（min_pages=5 > max_pages=1）被拒 422
- 每次修改产生新版本，批次固定引用原版本 → 满足不变量「历史版本不可变」

**T03 API 与消耗**
- 保存配置**不触发**任何真实调用（响应显式返回 `called_provider: false`）
- 密钥只回 `secret_configured: true` + 掩码 `••••••`，**`secret_ref` 不外泄**，全文检索无密钥原文
- 缺密钥 → 状态 `unconfigured`，并列出缺失项（不是 500）
- `base_url` 非法协议 → 422
- dry_run 测试**不产生费用**；真实测试明确标 `integration_pending`
- 未配置搜索时，界面提示「基于提供资料，未执行自动搜索」

**成本语义（v1.1 核心新增）**
- 默认 `cost_mode=usage_tracking`，`batch_money_limit=null`
- 金额显示为**"金额未知"**而非 `0`（满足不变量第 8 条）
- token 为 `None` 时注明"不等于 0"
- `usage_tracking` 模式不因金额被阻塞；`hard_cap` 模式缺估价才暂停
- 非金额约束照常生效：每批 1 个内容、待预览库存 3、修复最多 2 轮

**不变量**
- 不存在 `POST /auto-publish` 接口 ✅
- health 响应不含任何凭据 ✅

## 4. 未完成 / 待集成项（如实登记）

| 项 | 当前状态 | 何时补 |
|---|---|---|
| 真实 Provider 端点连通与能力探测 | 未实现（仅契约） | P2 · T08 |
| 真实账单对账 | 未实现 | P2 · T08 |
| 抖音/小红书实际上传兼容性 | 未核对（`verified=false`） | P1 · T07 后 |
| PNG 渲染与模板 | 未实现 | P1 · T06 |
| 预览 / 批准 / ZIP 导出 | 未实现 | P1 · T07 |
| 独立 Worker + 租约 | 未实现（health 显式报 `not_running`） | P1 · T04/T13 |
| 数据库（SQLAlchemy + Alembic） | 未接入（P0 用文件持久化） | P1 · T05 |

> 上述缺口**不阻塞** P1 开工，符合 v1.1「缺外部条件不阻塞开发」原则。

## 5. 复现方式

```bash
cd /workspace/content-workbench
python3 -m venv .venv && .venv/bin/pip install pydantic pydantic-settings fastapi uvicorn httpx
.venv/bin/python backend/tests/test_p0_smoke.py     # 57 项冒烟测试
.venv/bin/uvicorn app.main:app --app-dir backend --reload   # 交互式 API 文档 /docs
```

## 6. 下一步

进入 **P1 图文交付骨架**（T04–T07，净 3–4 工作日），目标：本地页面 → C001 双平台 PNG → 集中预览 → 批准 → ZIP。
P1 完成后同样分双状态登记：工程完成 + 上传界面待集成。
