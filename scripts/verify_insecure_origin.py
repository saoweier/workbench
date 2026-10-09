"""在**真实非安全上下文来源**上验证工作台可用（内网 IP / 局域网主机名访问场景）。

背景：只有 https:// 与 localhost/127.0.0.1 才算安全上下文。用 `http://内网IP:8000`
打开时 `crypto.randomUUID` / `navigator.clipboard` 根本不存在，旧版会让「新建创作」
卡在"读取创作设置"。这个脚本不做任何模拟——浏览器就是普通浏览器，API 是真的缺失。

用法（Windows 下用项目自带 venv）：

  # 1) 自检：起一套隔离服务（绑 0.0.0.0、独立临时库），用本机内网地址打开
  .venv\\Scripts\\python.exe scripts/verify_insecure_origin.py
  .venv\\Scripts\\python.exe scripts/verify_insecure_origin.py --lan-ip 10.6.101.1

  # 2) 验已经在跑的实例（例如在生产机上）。若该实例只绑 127.0.0.1，
  #    用 --alias-host 让 Chromium 把一个非 localhost 主机名解析到 127.0.0.1：
  .venv\\Scripts\\python.exe scripts/verify_insecure_origin.py --origin http://10.6.101.1:8000
  .venv\\Scripts\\python.exe scripts/verify_insecure_origin.py --origin http://cwb-intranet:8000 --alias-host cwb-intranet

退出码非 0 表示验证未通过。
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from playwright.sync_api import sync_playwright
from app.services.renderer import PlaywrightRenderer
import httpx

passed = 0


def check(name: str, value) -> None:
    global passed
    assert value, name
    passed += 1
    print("PASS " + name, flush=True)


def local_ipv4() -> str | None:
    """取本机对外可达的 IPv4（不实际发包）。

    先按默认路由探一次；若拿到的是 TUN/代理常用的保留段（198.18.0.0/15、169.254.0.0/16）
    或 WSL 虚拟网段，再按主机名解析一次，尽量挑一个真实网卡的地址。
    """
    def virtual(address: str) -> bool:
        return (address.startswith(("198.18.", "198.19.", "169.254."))
                or address.startswith("172.26.") or address.startswith(("127.", "0.")))

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("8.8.8.8", 80))
            candidate = probe.getsockname()[0]
        except OSError:
            candidate = None
    if candidate and not virtual(candidate):
        return candidate
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if not virtual(address):
                return address
    except OSError:
        pass
    return candidate


def accept_isolated_service(lan_ip: str) -> tuple[str, str, list, list, Path]:
    """起一套绑 0.0.0.0 的隔离服务，返回（内网地址, 回环地址, 进程, 日志句柄, 临时目录）。"""
    tmp = Path(tempfile.mkdtemp(prefix="cwb-insecure-origin-"))
    for name in ["artifacts", "tmp"]:
        shutil.copytree(ROOT / "examples/demo/storage" / name, tmp / name)
    shutil.copy2(ROOT / "examples/demo/storage/workbench.db", tmp / "cwb.db")
    shutil.copy2(ROOT / "examples/demo/storage/profiles.json", tmp / "profiles.json")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT / "backend"),
           "CWB_STORAGE_ROOT": str(tmp), "CWB_ARTIFACT_DIR": str(tmp / "artifacts"),
           "CWB_TMP_DIR": str(tmp / "tmp"), "CWB_DATABASE_URL": f"sqlite:///{tmp/'cwb.db'}",
           "CWB_SECRET_STORE_PATH": str(tmp / "secrets.json"), "CWB_PENDING_REVIEW_STOCK_LIMIT": "20"}
    procs, logs = [], []
    for name, args in [("api", ["-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(port)]),
                       ("worker", ["-m", "app.worker", "--interval", "0.5"])]:
        handle = (tmp / f"{name}.log").open("wb")
        logs.append(handle)
        procs.append(subprocess.Popen([sys.executable, *args], cwd=str(ROOT), env=env,
                     stdout=handle, stderr=handle,
                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
    loopback = f"http://127.0.0.1:{port}"
    client = httpx.Client(trust_env=False, timeout=5)
    for _ in range(160):
        try:
            if client.get(loopback + "/api/v1/health").json()["worker"]["status"] == "running":
                break
        except Exception:
            pass
        time.sleep(0.5)
    return f"http://{lan_ip}:{port}", loopback, procs, logs, tmp


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lan-ip", help="本机内网 IPv4；默认自动探测")
    parser.add_argument("--origin", help="直接验证这个来源（例如 http://10.6.101.1:8000）")
    parser.add_argument("--alias-host", help="把这个主机名解析到 127.0.0.1（用于只绑回环的实例）")
    parser.add_argument("--api", help="后端 API 基址，用于提交校验；默认与 --origin 同主机")
    parser.add_argument("--submit", action="store_true",
                        help="验 --origin 时也做提交校验（会往目标库排一条 local_seed 任务，默认关闭）")
    args = parser.parse_args()

    procs: list = []
    logs: list = []
    tmp: Path | None = None
    client = httpx.Client(trust_env=False, timeout=10)
    browser_args = [f"--host-resolver-rules=MAP {args.alias_host} 127.0.0.1"] if args.alias_host else []
    try:
        if args.origin:
            origin = args.origin.rstrip("/")
            api_base = (args.api or origin).rstrip("/")
            if not browser_args:
                # 只绑回环的实例没法用内网 IP 直连；提醒一下而不是静默失败。
                host = origin.split("//", 1)[-1].split(":", 1)[0]
                if host in {"127.0.0.1", "localhost"}:
                    print("注意：来源是 localhost/127.0.0.1，那是安全上下文，验不出这个场景。"
                          "请改用内网 IP，或用 --alias-host 指定一个非 localhost 主机名。")
        else:
            lan_ip = args.lan_ip or local_ipv4()
            if not lan_ip:
                print("未能探测到内网 IPv4，请用 --lan-ip 指定。")
                return 2
            origin, api_base, procs, logs, tmp = accept_isolated_service(lan_ip)
            print(f"隔离服务已起：{origin}")
        print(f"--- 访问来源：{origin}")

        errors: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True,
                                        executable_path=PlaywrightRenderer.resolve_executable(),
                                        args=browser_args)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(20000)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(origin + "/views/Production.html")

            # 先证明环境真的是非安全上下文，否则后面的断言会"因为环境太安全"而假过。
            check("该来源被浏览器判定为非安全上下文（isSecureContext=false）",
                  page.evaluate("()=>globalThis.isSecureContext") is False)
            check("该来源下 crypto.randomUUID 确实不存在（不是被脚本抹掉的）",
                  page.evaluate("()=>typeof globalThis.crypto.randomUUID") == "undefined")

            page.get_by_role("button", name="自定义选题", exact=True).click()
            page.locator("#creator-topic").wait_for(state="visible")
            page.wait_for_timeout(600)
            status = page.locator("#creator-model").inner_text()
            check(f"创作设置读出来了，不再卡在「读取创作设置」（状态条：{status}）",
                  "读取创作设置" not in status)
            check("打开创作页无脚本异常" + ("：" + " | ".join(e[:200] for e in errors) if errors else ""),
                  not errors)

            # 草稿 key 由 uuid() 生成；后端把 request_id 声明为 UUID，必须是合法 UUID v4。
            page.locator("#creator-topic").fill("内网来源可用性验证")
            key = page.evaluate("()=>JSON.parse(sessionStorage.getItem('cwb.direct.creator.v2')"
                                "||localStorage.getItem('cwb.direct.creator.v2')||'{}').key")
            try:
                version = UUID(str(key)).version
            except Exception:
                version = None
            check(f"草稿 request_key 是合法 UUID v4（{key}）", version == 4)

            # 端到端：页面产出的 key 交给后端应当被接受，非法 UUID 应被拒（422）。
            # 这会真的往目标库排一条 local_seed 任务，所以验"已经在跑的实例"时默认不做，
            # 免得污染真实工作区；自检模式用的是临时库，默认就做。
            if args.origin and not args.submit:
                print("跳过提交校验（验运行中实例时默认不写库；要写请显式加 --submit）")
            else:
                headers = {"X-CWB-Local-Action": "account-connection"}
                body = {"request_id": key, "topic": "内网来源可用性验证", "run_mode": "local_seed", "pages": 1}
                try:
                    good = client.post(api_base + "/api/v1/studio/produce", headers=headers, json=body)
                    bad = client.post(api_base + "/api/v1/studio/produce", headers=headers,
                                      json={**body, "request_id": "not-a-uuid"})
                    check(f"页面产出的 key 被后端接受（HTTP {good.status_code}），非法 UUID 被拒（HTTP {bad.status_code}）",
                          good.status_code != 422 and bad.status_code == 422)
                except Exception as error:
                    print(f"跳过（后端不可达：{error}）")

            page.goto(origin + "/views/ReviewPreview.html")
            page.wait_for_function("()=>document.querySelectorAll('img').length>0"
                                   " && [...document.querySelectorAll('img')].some(i=>i.naturalWidth>0)")
            check("预览页在该来源下也能加载并给出可点入口",
                  not page.locator("#revision-submit").is_disabled())
            check("整条路径无脚本异常" + ("：" + " | ".join(e[:200] for e in errors) if errors else ""),
                  not errors)
            browser.close()
    finally:
        for process in reversed(procs):
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
        for handle in logs:
            handle.close()

    print(f"{passed} 通过 / 0 失败")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
