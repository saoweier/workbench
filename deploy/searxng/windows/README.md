# 在 Windows 上原生运行 SearXNG（无需 Docker）

`deploy/searxng/compose.yml` 是官方 Docker 方案。但如果本机 Docker 是
**Windows 容器模式**（拉不了 Linux 镜像），或根本没装 Docker，可以用这里的
原生方案：直接跑 SearXNG 源码。

## 为什么不能照抄官方 README

SearXNG 官方不支持 Windows，直接跑会缺 Unix 专有模块。实测需要三件事：

1. **补 `pwd` / `grp` 两个模块**（`run_searxng.py` 里已用 shim 注入）。
2. **必须设 `PYTHONUTF8=1`**。否则进程内默认 GBK，URL 里的中文查询参数按 GBK
   解码后全是乱码：搜「特斯拉」→乱码；搜「2026 年最值得关注的 10 个开源 AI
   编程助手」→只剩「2026」被识别→返回一堆**节假日安排**。这是整条链路最阴的坑，
   现象是「引擎明明在返回结果，但全都不对题」。
3. **打 `patches/sogou-antispider.patch`**。当前 SearXNG 的 `sogou` 引擎检查
   `resp.next_request`，而 curl_cffi 版的 `SXNG_Response` 没有这个属性，
   于是抛 `AttributeError` 被记成 `unexpected crash`——把真实的「被反爬拦截」
   掩盖成引擎崩溃，排查时会完全走错方向。

## 步骤

```bash
# 1. 取源码（放到任意目录，例如 .workbuddy-tmp/searxng-src）
git clone --depth 1 https://github.com/searxng/searxng.git <SEARXNG_SRC>

# 2. 建独立 venv 并装依赖
python -m venv <SEARXNG_VENV>
<SEARXNG_VENV>/Scripts/pip install -r <SEARXNG_SRC>/requirements.txt

# 3. 打补丁
git -C <SEARXNG_SRC> apply deploy/searxng/windows/patches/sogou-antispider.patch

# 4. 放配置
cp deploy/searxng/windows/settings.yml.example storage/searxng/config/settings.yml
#    并把 server.secret_key 换成自己生成的随机串

# 5. 启动（注意 PYTHONUTF8=1）
SEARXNG_SRC=<SEARXNG_SRC> PYTHONUTF8=1 \
  <SEARXNG_VENV>/Scripts/python deploy/searxng/windows/run_searxng.py
```

启动后监听 `127.0.0.1:8088`，工作台的搜索通道填 `http://127.0.0.1:8088` 即可。

自检：

```bash
python - <<'PY'
import json, urllib.parse, urllib.request
u = "http://127.0.0.1:8088/search?" + urllib.parse.urlencode(
    {"q": "Python 3.13 新特性", "format": "json"})
d = json.loads(urllib.request.urlopen(u, timeout=60).read().decode("utf-8"))
print(len(d["results"]), d.get("unresponsive_engines"))
PY
```

**注意用 Python 发中文查询，不要用 Git Bash 的 curl**：curl 会把中文按 GBK
percent-encode，得到的是乱码请求，会让人误判成「SearXNG 坏了」。

## 国内引擎配置的实测结论

`settings.yml.example` 里只保留 `bing / baidu / sogou / 360search / quark`：

| 引擎 | 实测 |
|---|---|
| `bing` | 正常。**必须 `base_url: https://cn.bing.com` + `enable_http3: false`**——用默认 `www.bing.com` 会 302，叠加 http3 后**静默零结果**（不报错也没结果）。 |
| `baidu` | `www.baidu.com` / `m.baidu.com` 对本机 IP 稳定弹验证码。走 `baidu_category: 'it'`（开发者搜索 kaifa.baidu.com）才有结果，但**常返回错版号**（搜 3.13 给 3.3/3.10）。 |
| `sogou` | 少数几次可用，随后即 CAPTCHA / antispider 限流。需要上面的补丁才不会崩。 |
| `360search` | 基本返回空壳。 |
| `quark` | 模块默认 `categories` 为空，**必须显式写 `categories: [general]`** 才参与 general 查询；即便如此实测也常为空。 |

## 引擎会「静默退化」——这是最需要注意的行为

Bing 对某些查询形态会**退化成对头词的宽泛匹配**，并且不报错：结果条数照常有
10~20 条，只是没有一条答得了原题。实测（同一时刻）：

| 查询 | 含 `3.13` 的结果 |
|---|---|
| `Python 3.13 新特性` | 10/10（首条即官方 What's New 页） |
| `Python 3.13` | 10/10 |
| `Python 3.13 有哪些新特性` | **0** |
| `Python 3.13 新特性 介绍` | **0** |
| `Python 3.13 release notes` | **0** |
| `What's New In Python 3.13` | **0** |

**多一个修饰词就退化。** 所以检索词要短（2~6 个词），并且工作台侧做了兜底：
`backend/app/services/research_service.py` 的 `looks_degraded()` 会发现
「结果里一个版本号都没有」，然后用 `shortened_query()` 截到版本号为止重搜一次
（`Python 3.13 有哪些新特性` → `Python 3.13`，落回引擎能正常服务的形态）。
