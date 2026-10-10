# 任务连续失败 —— 诊断与修复报告

日期：2026-10-10
触发：用户指出「测试最近五次任务全部失败」，质疑上一轮"跑通了"的结论

---

## 一、先承认：用户是对的，我上一轮的结论是错的

查库确认，`run` 表最近 5 条任务**全部失败**，`content_item` 对应 5 条 `state=blocked`：

| 编号 | 选题 | 时间 | 状态 | 报错 |
|---|---|---|---|---|
| C014 | 巴拿马8.0级地震 | 10-10 00:49 | blocked | 热榜原链接已携带，但原站未返回可用正文 |
| C013 | Most notable open source AI coding assistants on GitHub | 10-09 14:32 | blocked | 补充搜索尚未取得能回答本题的资料 |
| C012 | 同上 | 10-09 14:28 | blocked | 同上 |
| C011 | 2026 年最值得关注的 10 个开源 AI 编程助手 | 10-09 14:25 | blocked | 同上 |
| C010 | 同上 | 10-09 13:17 | blocked | 同上 |
| C009 | GitHub 上最值得关注的 10 个开源 AI 编程助手 | 10-09 10:36 | changes_requested | **成功** |

**我为什么判断错了**：C009 之所以跑通，是因为我当时**手工喂了 10 条资料**，绕开了自动检索环节。
而用户的实际操作路径是「只给选题，让系统自己去找资料」。
**我用一条被资料兜住的路径，证明了整条链路可用——这个验证本身是无效的。**

---

## 二、查出的 3 个真实缺陷（均已修复）

### 1. 搜索通道配置错误 —— 每次搜索必然 404

```
d2 的配置：adapter_type = searxng
           base_url     = https://api.deepseek.com   ← 指向大模型地址
           secret_ref   = deepseek 的 key
           最近自检      = "SearXNG 返回 HTTP 404"
```

搜索适配器是 SearXNG，地址却是 DeepSeek 的 API——每一次补充搜索都打在不存在的端点上。
更糟的是：因为 `d2` 处于 `enabled=true`，系统认定"搜索已配置"，于是既不提示"未配置"，也拿不到结果。

**修复**：已将 `base_url` 改为本机 SearXNG 地址 `http://127.0.0.1:8088`。

### 2. 正文读取时限被硬编码为 8 秒 —— 热榜原链接永远读不到

`backend/app/services/source_reader.py`：

```python
with httpx.Client(timeout=8, follow_redirects=False) as client:   # 旧
```

真实文章页常见 200KB~2MB，走代理时首字节就要好几秒。8 秒会把「慢」误判成「读不到」，
于是热榜原链接永远拿不到正文（C014 的直接原因），整条链路被判「无资料」而中止。

**修复**：默认改为 **30 秒**，可用环境变量 `CWB_SOURCE_READ_TIMEOUT` 覆盖。

### 3. 失败文案把三种病因压成同一句话 —— 把排查方向带偏

旧逻辑只有两种输出：「未配置搜索」和「补充搜索尚未取得能回答本题的资料」。
于是「搜到了但内容不对题」被讲成「没搜到」，用户（和我）都以为是搜索坏了。

**修复**：`ProductionService._search_failure_note()` 现在分三档输出：

| 实际情况 | 现在的提示 |
|---|---|
| 没配搜索 | 补充搜索未配置，请在 API 设置中配置 SearXNG |
| 通道整体失败 | 补充搜索通道「d2」(searxng @ …) 7 次检索全部失败，上游报错：SearXNG 返回 HTTP 404 |
| **检索通了但不对题** | 检索本身是通的（4 次成功），已读到 6 篇正文（baibaidu.com、chinacalendar.app、gov.cn、github.com），但没有一篇能回答原题：检索结果与原题不符。**这不是搜索故障** |

第三种才是 C010~C014 的真实情况。

---

## 三、把本机 SearXNG 真正跑起来了

之前一直说「本地没装 SearXNG」，根因是：
官方 `deploy/searxng/compose.yml` 是 Docker **Linux 容器**方案，
而本机 Docker 是 **Windows 容器模式**，拉不了 Linux 镜像 → 官方部署路径在这台机器上不可用。

我让它在 Windows 上原生跑通了（监听 8088），关键四处：

| 配置 | 原因 |
|---|---|
| `engines[bing].base_url: https://cn.bing.com` | 默认 `www.bing.com` 会 302 跳转，叠加下一项会导致**静默零结果**（既不报错也没结果） |
| `enable_http3: false` | 本机 HTTP/3 不通 |
| **启动加 `PYTHONUTF8=1`** | 最阴的一个坑：进程内默认 GBK，URL 查询参数按 GBK 解码，**中文查询全部变乱码** |
| 引擎只留 `bing` / `baidu` | 其余国外引擎全不通；`baidu` 稳定 `Suspended: CAPTCHA`，实际只有 bing 出力 |

中文乱码的具体表现（这个是关键证据）：

```
搜 "特斯拉"                                    → 查询词变成 %CC%D8... 乱码
搜 "2026 年最值得关注的 10 个开源 AI 编程助手"  → 只剩 "2026" 被识别
                                              → 返回「2026 年节假日安排」
```

**启动命令**：

```bash
PYTHONUTF8=1 .workbuddy-tmp/searxng-venv/Scripts/python.exe .workbuddy-tmp/run_searxng.py
```

**修复后验证**：
- `特斯拉` → 10 条正确结果（特斯拉中国、百度百科、tesla.com）
- `开源 AI 编程助手 排行` → 10 条合理结果（GitHubDaily、开源中国、开源社）

---

## 四、修完仍然失败的，不是 bug —— 是搜索召回精度

搜索通道打通后重跑，结果如下：

### 案例 A：「2026 年最值得关注的 10 个开源 AI 编程助手」

- 超长中文查询被 Bing 分词成「2026」→ 返回**节假日安排**（9/10 条被判与原题无关）
- 短查询「开源 AI 编程助手 排行 2026」能返回 8 条相关结果，但都是泛泛的开源社区介绍，**不是榜单**
- 最终读到 6 篇正文（github.com、gitee.com、gov.cn…），但没有一篇能回答"哪 10 个助手"

### 案例 B：「Python 3.13 有哪些新特性」

- 搜索通了，读到 4 篇完整正文：CSDN 5888 字、python.org 6629 字、廖雪峰 2963 字、python.org/downloads 20000 字
- 但精确定位官方 What's New 的 3 条英文查询（`Python 3.13 release notes what's new` 等）**全部 0 结果**
- 最终判定「资料无法回答原题」

**结论**：Bing 通用搜索对「TOP N 榜单 / 最新事件」这类选题**召回精度不够**。
证据闸门拦下是**正确**的（不能凭空编造），但这意味着这类选题靠自动检索很难跑通。

---

## 五、需要你拍板的三条路

| 方案 | 说明 | 代价 |
|---|---|---|
| **A. 换商业搜索 API** | 接国内可直连的搜索服务（博查 / 智谱 Web Search 等），召回质量明显优于 Bing 抓取。工作台已有 `search_http_json` 适配器，可能需小改请求体格式 | 需要申请 key，通常付费 |
| **B. 继续调 SearXNG** | 加更多引擎、做查询改写、加站点限定（如 `site:github.com`） | 我这边可以继续做，但天花板受 Bing 本身限制 |
| **C. 榜单/新闻类走「引导式创作」** | 你自己粘资料，像 C009 那样 | 这条路本来就能跑通，但要人工准备资料 |

---

## 六、代码仓库状态

- 本地已提交两个 commit：
  - `d4b6da3` 修掉本轮暴露的两个真实缺陷（正文超时 + 失败文案）
  - `21ee396` 删除数量类硬限制 + API 连通性校验 + Host 校验默认全开
- **推送失败**：本机到 github.com 的网络当前完全不通，12 次重试均报
  `CONNECT tunnel failed, response 502` / `Failed to connect to github.com:443`
- 可在网络恢复后于 `workbench/` 目录执行：

```bash
git push https://github.com/saoweier/workbench.git main
```

---

## 七、回归测试

352 项通过 / 0 失败：

| 测试 | 结果 |
|---|---|
| evidence_grounding | 73 / 0 |
| hotpush_research | 28 / 0 |
| research_agent | 48 / 0 |
| p2_e2e | 90 / 0 |
| platform_accounts | 57 / 0 |
| searxng | 56 / 0 |
