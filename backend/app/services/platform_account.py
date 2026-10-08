"""Local browser account connection. No passwords, cookie export or publishing API."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from uuid import uuid4
from urllib.parse import urlparse

DOUYIN_URL = "https://creator.douyin.com/"
_lock = threading.Lock()


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        for attempt in range(25):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                # Windows can briefly deny replacement while a reader holds
                # the destination. A status poll must not kill the browser.
                if attempt==24:
                    raise
                time.sleep(0.04)
    finally:
        temp.unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def classify_login(url: str, visible_text: str) -> str:
    """Require official creator origin + dashboard navigation; cookies aren't proof."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "creator.douyin.com":
        return "unverified"
    if any(x in visible_text for x in ("扫码登录", "验证码登录", "密码登录", "请重新登录")):
        return "waiting_login"
    if any(x in visible_text for x in ("安全验证", "拖动滑块", "完成验证", "账号异常")):
        return "needs_attention"
    if parsed.path in ("", "/") or "login" in parsed.path.lower():
        return "unverified"
    lines = {x.strip() for x in visible_text.splitlines() if x.strip()}
    navigation = ("首页", "内容管理", "作品发布", "作品管理", "数据中心", "作品数据", "互动管理", "创作者学院")
    return "connected" if sum(x in lines for x in navigation) >= 3 else "unverified"


class PlatformAccountService:
    def __init__(self, storage: Path):
        self.root = storage / "platform_accounts" / "douyin"

    def status(self) -> dict:
        data = read_json(self.root / "status.json")
        identity = read_json(self.root / "identity.json")
        state = data.get("state", "not_connected")
        active = bool(data.get("browser_open")) and time.time() - data.get("heartbeat", 0) < 30
        if data.get("browser_open") and not active:
            state = "saved" if data.get("last_verified_at") else "disconnected"
        return {"platform": "douyin", "state": state, "browser_open": active,
                "verified_now": active and state == "connected",
                "last_verified_at": data.get("last_verified_at"),
                "message": data.get("message") if active or not data.get("browser_open") else "连接窗口已停止，请重新打开检查登录状态。",
                "official_url": DOUYIN_URL, "auto_publish": False, "auto_metrics": False,
                "account": {k:identity.get(k) for k in ("account_id", "nickname", "observed_at", "data_center_enabled")}}

    def open(self) -> dict:
        with _lock:
            if self.status()["browser_open"]:
                self.command("check")
                return self.status()
            self.root.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                self.root.chmod(0o700)
            previous = read_json(self.root / "status.json")
            write_json(self.root / "status.json", {
                "state": "opening", "browser_open": True, "heartbeat": time.time(),
                "last_verified_at": previous.get("last_verified_at"),
                "message": "正在打开专用浏览器，请在抖音官方页面扫码登录。"})
            root = Path(__file__).resolve().parents[3]
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
            try:
                # Discard raw browser logs: they may contain private navigation details.
                subprocess.Popen([sys.executable, "-X", "utf8", str(root / "scripts/connect_douyin.py"),
                                  "--storage", str(self.root)], cwd=root,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, **options)
            except OSError:
                write_json(self.root / "status.json", {
                    "state": "error", "browser_open": False, "message": "无法启动连接窗口，请检查本机运行环境。"})
            return self.status()

    def command(self, action: str) -> dict:
        if action not in {"check", "close"}:
            raise ValueError("不支持的账号操作")
        if not self.status()["browser_open"]:
            return self.status()
        write_json(self.root / "command.json", {"action": action, "id": uuid4().hex})
        return self.status()
