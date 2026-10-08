"""Smoke check with an empty, temporary profile. Never log in or publish."""
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.platform_account import read_json, write_json


def main():
    # Keep temporary browser data separate from the user's saved connection.
    root = Path(tempfile.mkdtemp(prefix="douyin-empty-smoke-", dir=ROOT / "storage/tmp"))
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    process = subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "scripts/connect_douyin.py"),
                                "--storage", str(root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               cwd=ROOT, **options)
    ready = False
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            data = read_json(root / "status.json")
            if data.get("state") == "waiting_login":
                ready = True
                print("PASS 官方扫码页显示为等待登录，未误报已连接", flush=True)
                break
            if data.get("state") == "error" or process.poll() is not None:
                break
            time.sleep(0.5)
    finally:
        write_json(root / "command.json", {"action":"close", "id":uuid4().hex})
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            print("FAIL 连接窗口未按指令结束；未强杀其他浏览器", flush=True)
            return 1
    closed = read_json(root / "status.json")
    if not ready:
        print("FAIL 本机扫码窗口未达到可验证的等待登录状态", flush=True)
        return 1
    if closed.get("state") != "disconnected" or closed.get("browser_open"):
        print("FAIL 关闭后的状态不正确", flush=True)
        return 1
    print("PASS 关闭连接窗口后保持未登录，未产生真实发布", flush=True)
    print("结果：2 通过 / 0 失败", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
