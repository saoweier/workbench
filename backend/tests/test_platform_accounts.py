"""Account connection contracts, browser lifecycle and access boundaries.

No real login, model call, publishing or cookie export occurs in this test.
Access boundary: same-origin + anti-CSRF are always enforced; Host checking
(anti-DNS-rebinding) and "loopback client only" are both configurable and off
by default since 2026-10-09 / 2026-10-10.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
workspace = Path(tempfile.mkdtemp(prefix="cwb-accounts-"))
os.environ.update(CWB_STORAGE_ROOT=str(workspace), CWB_DATABASE_URL=f"sqlite:///{workspace/'test.db'}",
                  CWB_ARTIFACT_DIR=str(workspace/'artifacts'), CWB_TMP_DIR=str(workspace/'tmp'),
                  CWB_SECRET_STORE_PATH=str(workspace/'secrets.json'))
from fastapi.testclient import TestClient
from app.main import app
from app.api.accounts import service
from app.core.config import get_settings
from app.services.platform_account import DOUYIN_URL, classify_login, read_json, write_json

passed = 0
def check(label, value):
    global passed
    assert value, label
    passed += 1
    print("PASS " + label)


client = TestClient(app)
headers = {"X-CWB-Local-Action": "account-connection", "Origin": "http://testserver"}
initial = client.get("/api/v1/accounts/douyin")
check("empty account is not connected", initial.json()["state"] == "not_connected")
check("empty account has no verified timestamp", initial.json()["last_verified_at"] is None)
check("login does not enable automatic publishing", not initial.json()["auto_publish"])
check("login does not fabricate automatic metrics", not initial.json()["auto_metrics"])
check("page is served", client.get("/views/PlatformAccounts.html").status_code == 200)
check("page names consent and account scope", "我同意将抖音登录状态保存在这台电脑" in client.get("/views/PlatformAccounts.html").text)
check("same-site origin supported", client.get("/api/v1/accounts/douyin", headers={"Origin":"http://testserver"}).status_code == 200)
check("foreign origin blocked on GET", client.get("/api/v1/accounts/douyin", headers={"Origin":"https://evil.invalid"}).status_code == 403)
check("foreign origin blocked on POST", client.post("/api/v1/accounts/douyin/connect", headers={**headers, "Origin":"https://evil.invalid"}).status_code == 403)
check("different port blocked", client.post("/api/v1/accounts/douyin/connect", headers={**headers, "Origin":"http://testserver:1"}).status_code == 403)
check("missing action header blocked", client.post("/api/v1/accounts/douyin/connect").status_code == 403)
check("cross-site browser blocked", client.post("/api/v1/accounts/douyin/connect", headers={**headers,"Sec-Fetch-Site":"cross-site"}).status_code == 403)
# 2026-10-10 起：CWB_ALLOWED_HOSTS 默认 `*`，不做 Host 校验（用公网 IP / 自有域名访问不想被 403）。
# 代价是防 DNS rebinding 的这道 Host 校验默认失效；同源 Origin、跨站拒绝、写操作自定义头仍在。
check("default wildcard lets any host through",
      client.get("/api/v1/accounts/douyin", headers={"Host":"evil.invalid"}).status_code == 200)
_strict_hosts = get_settings().allowed_hosts
try:
    get_settings().allowed_hosts = ""
    check("DNS rebinding host blocked once wildcard removed",
          client.get("/api/v1/accounts/douyin", headers={"Host":"evil.invalid"}).status_code == 403)
finally:
    get_settings().allowed_hosts = _strict_hosts
# 2026-10-09 起：非回环客户端默认放行，否则局域网/内网里其它设备打开工作台会到处 403
# （用户实际遇到的是 /studio/options 被拦，界面显示"创作设置未读取：账号连接只允许本机访问"）。
# 依据：只绑 127.0.0.1 时远程根本连不上，能收到非回环请求本身就说明运维方主动暴露了服务。
lan = TestClient(app, client=("10.0.0.7", 45231), base_url="http://10.0.0.5:8000")
lan_origin = {"Origin": "http://10.0.0.5:8000"}
check("LAN client allowed by default", lan.get("/api/v1/accounts/douyin", headers=lan_origin).status_code == 200)
check("LAN client can read creator settings",
      lan.get("/api/v1/studio/options").status_code == 200)
check("LAN client can reach content skills and video studio",
      lan.get("/api/v1/content-skills").status_code == 200
      and lan.get("/api/v1/video-presenters").status_code == 200)
check("LAN client still needs the workbench action header for writes",
      lan.post("/api/v1/accounts/douyin/connect", headers=lan_origin).status_code == 403)
check("LAN client still blocked on foreign origin",
      lan.get("/api/v1/accounts/douyin", headers={"Origin":"https://evil.invalid"}).status_code == 403)
check("LAN client still blocked as cross-site",
      lan.post("/api/v1/accounts/douyin/connect", headers={**lan_origin,"Sec-Fetch-Site":"cross-site"}).status_code == 403)
check("LAN client with a foreign host follows the same host policy as any client",
      lan.get("/api/v1/accounts/douyin", headers={"Host":"evil.invalid"}).status_code == 200)
_previous = get_settings().allow_remote_access
get_settings().allow_remote_access = False
try:
    check("CWB_ALLOW_REMOTE_ACCESS=false restores the local-only boundary",
          lan.get("/api/v1/accounts/douyin", headers=lan_origin).status_code == 403
          and client.get("/api/v1/accounts/douyin").status_code == 200)
finally:
    get_settings().allow_remote_access = _previous
# 2026-10-09 用户实测反馈：用 http://<机器名>:8000 打开时报"创作设置未读取：请从本机工作台打开账号连接"。
# 根因是 Host 校验只认 IP 字面量和 localhost，机器名被当成"外来域名"拒了。机器名由本机/局域网解析
# 决定，不是外部 DNS 能指过来的，放行它不会给 DNS rebinding 开口子。
import socket
machine = socket.gethostname().lower()
machine_short = machine.split(".")[0]
by_name = TestClient(app, client=("10.0.0.7", 45231), base_url=f"http://{machine}:8000")
allowed_hosts = get_settings().allowed_hosts
try:
    check("本机机器名不被当成外来域名",
          by_name.get("/api/v1/studio/options").status_code == 200
          and by_name.get("/api/v1/accounts/douyin",
                          headers={"Origin": f"http://{machine}:8000"}).status_code == 200)
    check("机器名短名与 .local 同样可用",
          TestClient(app, base_url=f"http://{machine_short}.local:8000").get(
              "/api/v1/studio/options").status_code == 200)
    check("explainer page reachable by machine name",
          by_name.get("/views/PlatformAccounts.html").status_code == 200)
    # 默认 `CWB_ALLOWED_HOSTS=*`：任何 Host 都放行，公网 IP / 自有域名不再被拦。
    check("默认全开时外来域名不再被 Host 校验拦下",
          by_name.get("/api/v1/studio/options", headers={"Host": "cwb.example.com"}).status_code == 200)
    check("默认全开时公网 IP 也能访问",
          TestClient(app, base_url="http://47.96.185.149:8000").get(
              "/api/v1/studio/options").status_code == 200)
    # 显式收紧（去掉 `*`）后恢复严格模式，并且仍把当前 Host 写进文案，用户照着加即可。
    get_settings().allowed_hosts = ""
    rejected = by_name.get("/api/v1/studio/options", headers={"Host": "cwb.example.com"})
    check("收紧后外来域名被拦下并报出当前地址",
          rejected.status_code == 403
          and "cwb.example.com" in rejected.json()["detail"]
          and "CWB_ALLOWED_HOSTS" in rejected.json()["detail"])
    get_settings().allowed_hosts = "cwb.example.com"
    check("CWB_ALLOWED_HOSTS 能放行自己的域名",
          by_name.get("/api/v1/studio/options", headers={"Host": "cwb.example.com"}).status_code == 200)
finally:
    get_settings().allowed_hosts = allowed_hosts
# 反向代理终止 TLS 时浏览器看到 https、应用看到 http，那不是跨站，不该拦。
check("同主机不同方案不算跨站",
      by_name.get("/api/v1/studio/options",
                  headers={"Origin": f"https://{machine}:8000"}).status_code == 200)
check("同主机不同端口仍然是跨站",
      by_name.get("/api/v1/studio/options",
                  headers={"Origin": f"http://{machine}:9999"}).status_code == 403)
dashboard = "首页\n内容管理\n数据中心\n账号设置"
check("landing page never sufficient", classify_login(DOUYIN_URL, dashboard) == "unverified")
check("login page remains waiting", classify_login(DOUYIN_URL, "扫码登录\n首页\n内容管理\n数据中心") == "waiting_login")
check("login route remains unverified", classify_login(DOUYIN_URL + "login", dashboard) == "unverified")
check("navigation and authenticated route required", classify_login(DOUYIN_URL + "fixture-dashboard", dashboard) == "connected")
check("missing dashboard evidence remains unverified", classify_login(DOUYIN_URL + "fixture-dashboard", "加载中") == "unverified")
check("different host cannot prove login", classify_login("https://creator.douyin.com.evil.invalid/", dashboard) == "unverified")
check("HTTP cannot prove login", classify_login("http://creator.douyin.com/dashboard", dashboard) == "unverified")
check("verification is handed to user", classify_login(DOUYIN_URL + "fixture-dashboard", dashboard + "\n安全验证") == "needs_attention")
check("expiry overrides dashboard", classify_login(DOUYIN_URL + "fixture-dashboard", dashboard + "\n请重新登录") == "waiting_login")

with patch("app.services.platform_account.subprocess.Popen") as launch:
    first = client.post("/api/v1/accounts/douyin/connect", headers=headers).json()
    check("connect launches one helper", launch.call_count == 1)
    check("opening isn't authenticated", first["state"] == "opening" and not first["verified_now"])
    check("helper uses isolated local root", str(service.root) in launch.call_args.args[0])
    client.post("/api/v1/accounts/douyin/connect", headers=headers)
    check("double click does not open another profile", launch.call_count == 1)
    check("repeated connect checks existing page", read_json(service.root / "command.json")["action"] == "check")
    client.post("/api/v1/accounts/douyin/close", headers=headers)
    check("close uses local command only", read_json(service.root / "command.json")["action"] == "close")

verified = datetime.now(timezone.utc).isoformat()
write_json(service.root / "status.json", {"state":"connected", "heartbeat":time.time(), "browser_open":True,
     "last_verified_at":verified, "message":"connected", "cookies":"private-test", "access_token":"private-test"})
data = client.get("/api/v1/accounts/douyin").json()
check("fresh observation can be connected", data["verified_now"])
check("response strips private fields", "cookies" not in data and "access_token" not in data)
write_json(service.root / "status.json", {"state":"connected", "heartbeat":time.time()-100, "browser_open":True,
     "last_verified_at":verified})
data = service.status()
check("stale heartbeat isn't currently connected", data["state"] == "saved" and not data["verified_now"])
check("historical verification retained", data["last_verified_at"] == verified)
write_json(service.root / "status.json", {"state":"saved", "browser_open":False, "last_verified_at":verified})
check("closed profile isn't currently connected", not service.status()["verified_now"])
with patch("app.services.platform_account.subprocess.Popen", side_effect=OSError("private-test")):
    data = service.open()
    check("launch failure exposed without private exception", data["state"] == "error" and "private-test" not in json.dumps(data))
(service.root / "status.json").write_text("broken-json", encoding="utf-8")
check("corrupt metadata doesn't crash page", service.status()["state"] == "not_connected")
sys.path.insert(0, str(ROOT / "scripts"))
import backup
check("account credentials not included in backups", all("platform_accounts" not in x for x in backup.INCLUDE))
check("helper does not export storage state", "storage_state(" not in (ROOT / "scripts/connect_douyin.py").read_text(encoding="utf-8"))
actual_replace=os.replace
with patch('app.services.platform_account.os.replace',side_effect=[PermissionError('temporary reader'),None]) as replace:
    write_json(service.root/'windows-retry.json',{'safe':'status'})
    check('Windows reader collision is retried instead of killing browser',replace.call_count==2)
check('temporary write files cleaned',not list(service.root.glob('windows-retry.json.*.tmp')))
print(f"结果：{passed} 通过 / 0 失败")
