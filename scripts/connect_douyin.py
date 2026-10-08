"""Visible, isolated persistent browser; the user handles login and challenges."""
from __future__ import annotations
import argparse
import importlib
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.platform_account import DOUYIN_URL, classify_login, read_json, write_json


def run(root: Path, read_only=False) -> None:
    root.mkdir(parents=True, exist_ok=True)
    guard = root / "browser.lock"
    try:
        # No force-killing or deleting another process's browser profile lock.
        lock_file = guard.open("a+b")
        if os.name == "nt":
            import msvcrt
            lock_file.seek(0)
            if lock_file.read(1) == b"":
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # Another connection helper owns this profile.
    data = read_json(root / "status.json")
    data.update(browser_open=True)
    last_command = read_json(root / "command.json").get("id")
    stopped = threading.Event()
    data_lock = threading.Lock()

    def update(state, message):
        with data_lock:
            data.update(state=state, message=message, heartbeat=time.time(), read_only=read_only)
            if state == "connected":
                data["last_verified_at"] = datetime.now(timezone.utc).isoformat()
            try:
                write_json(root / "status.json", data)
            except OSError:
                pass  # A transient status-file failure must not close an editor.

    def keep_alive():
        while not stopped.wait(3):
            with data_lock:
                data['heartbeat'] = time.time()
                try:
                    write_json(root / 'status.json', data)
                except OSError:
                    pass
    heartbeat_thread = threading.Thread(target=keep_alive,daemon=True)
    heartbeat_thread.start()

    context = None
    try:
        from playwright.sync_api import sync_playwright
        from app.services.renderer import PlaywrightRenderer
        with sync_playwright() as runtime:
            executable = PlaywrightRenderer.resolve_executable()
            browser_options = {"executable_path": executable} if executable else {}
            context = runtime.chromium.launch_persistent_context(
                str(root / "browser-profile"), headless=read_only,
                viewport={"width": 1280, "height": 820}, accept_downloads=False, **browser_options)
            page = context.pages[0] if context.pages else context.new_page()
            def creator_page():
                pages = [p for p in context.pages if not p.is_closed()]
                official = [p for p in pages if p.url.startswith("https://creator.douyin.com/")]
                return official[-1] if official else (pages[-1] if pages else None)
            update("opening", "连接窗口已打开，正在加载抖音官方页面。")
            try:
                page.goto(DOUYIN_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                update("needs_attention", "官方页面暂未加载完成，请在连接窗口检查网络；系统不会跳过浏览器安全提示。")
            while context.pages:
                command = read_json(root / "command.json")
                if command.get("id") != last_command:
                    last_command = command.get("id")
                    if command.get("action") == "close":
                        break
                    if not read_only and command.get("action") not in {"check", "close"} and context.pages:
                        from app.services import douyin_browser
                        try:
                            importlib.reload(douyin_browser)
                            douyin_browser.handle_command(creator_page(), root, command)
                            write_json(root / "command_result.json", {"id":last_command,"ok":True})
                        except Exception as command_error:
                            # A failed page inspection must not close an authenticated editor.
                            import re
                            brief = str(command_error).splitlines()[0][:300]
                            brief = re.sub(r"https?://\S+", "[URL]", brief)
                            write_json(root / "command_result.json", {"id":last_command,"ok":False,
                                "action":command.get("action"),"error_type":type(command_error).__name__,"brief":brief})
                    # 'check' observes the current page, without reloading a QR code.
                pages = [p for p in context.pages if not p.is_closed()]
                if not pages:
                    break
                page = creator_page()
                try:
                    from app.services.douyin_flow import poll_tasks
                    poll_tasks(page, root, read_only=read_only)
                    text = page.locator("body").inner_text(timeout=2000)
                    state = classify_login(page.url, text)
                    messages = {
                        "connected": "已检测到抖音创作者后台登录。请核对窗口中的账号；关闭窗口后下次会再次验证。",
                        "waiting_login": "请用抖音 App 扫描连接窗口里的二维码，并在手机上确认登录。",
                        "needs_attention": "抖音需要人工验证，请在连接窗口中处理。",
                        "unverified": "尚未确认登录。扫码后请进入创作者后台首页；如果已登录但仍未识别，请保留窗口告诉我们。",
                    }
                    update(state, messages[state])
                    if read_only:
                        break  # One bounded, read-only revisit; never upload or submit.
                    page.wait_for_timeout(2000)
                except Exception:
                    if not context.pages:
                        break
                    update("needs_attention", "暂时无法检查当前页面，请查看连接窗口。")
                    time.sleep(2)
            context.close()
            context = None
    except Exception as failure:
        # Never put Playwright exception text (potential URLs/tokens) into the API.
        update("error", "连接窗口无法运行。请先关闭其他连接窗口；若仍失败，请检查 Chromium 安装及本机网络。")
        # Private diagnostic only; do not store URLs, selectors, cookies or tokens.
        write_json(root / 'helper-error.json', {'error_type':type(failure).__name__,
            'file':Path(failure.filename).name if isinstance(failure,OSError) and failure.filename else None,
            'at':datetime.now(timezone.utc).isoformat()})
    finally:
        stopped.set()
        heartbeat_thread.join(timeout=4)
        data["browser_open"] = False
        if read_only and data.get('state') in {'waiting_login','needs_attention'}:
            data['message'] = '后台回访需要重新登录或完成平台验证，请到平台账号打开连接窗口。'
        elif data.get("state") != "error":
            data["state"] = "saved" if data.get("last_verified_at") else "disconnected"
            data["message"] = ("本机登录状态已保留，重新打开后会再次验证是否有效。" if data.get("last_verified_at")
                               else "连接窗口已关闭，尚未验证账号登录。")
        data["heartbeat"] = time.time()
        write_json(root / "status.json", data)
        lock_file.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--storage", required=True)
    parser.add_argument("--read-only", action="store_true")
    args = parser.parse_args()
    run(Path(args.storage).resolve(), read_only=args.read_only)
