# SearXNG 联网资料搜索

SearXNG 已作为原生搜索后端接入工作台。它聚合网页搜索引擎并返回标题、链接和摘要，工作台负责继续读取文章正文，文字模型负责规划检索与整理内容。热点榜单仍来自 HotPush。

## 本机运行

1. 启动 Docker Desktop。
2. 双击项目根目录 `start-search.bat`。也可以运行 `.venv/Scripts/python.exe -X utf8 scripts/searxng.py start`。
3. 打开工作台「API 设置 → 搜索 / 资料检索」，点「填入本机 SearXNG 配置」，然后保存。
4. 在「实际搜索与正文读取测试」输入完整原题，点「搜索并检查正文」。这一操作搜索并读取前三条链接，不调用文字模型。
5. 正常进入创作室开始制作。热榜选题先读取 HotPush 条目携带的原链接，资料不足时再使用启用的搜索配置补充；自定义事实选题没有原链接时从搜索开始。

本机地址为 `http://127.0.0.1:8088`，无需 API Key，需要勾选「允许连接自己部署的本机搜索服务」。配置名称、地址、语言、引擎列表均可在页面维护。填写外部自建实例时无需勾选本机选项；如果实例要求 Bearer 鉴权，可以填写其 Key。

## 维护

- 查询状态：`.venv/Scripts/python.exe scripts/searxng.py status`
- 查看日志：`.venv/Scripts/python.exe scripts/searxng.py logs`
- 停止本项目搜索容器：`.venv/Scripts/python.exe scripts/searxng.py stop`
- 引擎配置：`storage/searxng/config/settings.yml`，修改后可执行 `docker compose -f deploy/searxng/compose.yml restart`。
- 首次启动根据 `deploy/searxng/settings.yml.example` 创建配置并生成实例密钥，后续启动保留配置。
- 官方镜像按本轮实际拉取的 digest 固定，修改镜像版本时需重新检查协议及真实搜索结果。
- 端口仅绑定本机，容器设为 `unless-stopped`，不影响已有数据库及其他项目容器。

## 诊断怎么看

“有链接”表示搜索接口返回结果。“可读正文”表示工作台确实读取了原文。摘要不等于原文，同一报道的转载也不等于多个独立证据。

SearXNG 上游可能返回验证码、限流、网络或解析失败。设置页和内容技能诊断保留实际引擎状态。2026-10-06 的本机实测中，百度、DuckDuckGo 出现验证码，Google 访问失败；其他引擎仍返回了相关报道。不要因此显示“全部引擎正常”。

SearXNG 无结果或其候选文章均不可读时，生产链路尝试原有公开网页入口；失败记录保留，不会把摘要改标为正文。改稿可使用已冻结的可读来源，会标注本次未重新检索。图片质量、文案质量和人工批准仍需另行核对。

JSON API 如果返回 403，先检查实例 `settings.yml` 的 `search.formats` 是否包含 `json`。公共实例可能关闭 JSON 或限流，因此工作台附带本机部署。

官方资料：[搜索 API](https://docs.searxng.org/dev/search_api.html)、[容器安装](https://docs.searxng.org/admin/installation-docker.html)。
