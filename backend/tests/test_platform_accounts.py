"""Account connection contracts, browser lifecycle and local-only boundaries.

No real login, model call, publishing or cookie export occurs in this test.
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
check("DNS rebinding host blocked", client.get("/api/v1/accounts/douyin", headers={"Host":"evil.invalid"}).status_code == 403)
check("remote client blocked", TestClient(app, client=("192.0.2.1", 123)).get("/api/v1/accounts/douyin").status_code == 403)
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
