"""Manage only this checkout's services; detached processes write local logs."""
from __future__ import annotations
import argparse
import ctypes
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
import webbrowser
import shutil
import sqlite3
from datetime import datetime,timezone

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "storage" / "services.json"

# The workbench is a single-user tool, so the API listens on every interface by
# default: a workbench only reachable from the machine it runs on is useless for
# a phone or a second computer on the same network. Pass --host 127.0.0.1 (or set
# CWB_HOST) to keep it loopback-only again.
DEFAULT_HOST = os.environ.get("CWB_HOST", "0.0.0.0")


def api_command(port, host=None):
    """The single definition of how the app API is launched."""
    return ["-m", "uvicorn", "app.main:app",
            "--host", host or DEFAULT_HOST, "--port", str(port)]


def _virtual_address(address):
    """TUN/proxy/WSL ranges that other devices cannot reach."""
    if not isinstance(address, str):
        return True
    return (address.startswith(("198.18.", "198.19.", "169.254.", "172.26."))
            or address.startswith(("127.", "0.")))


def lan_addresses():
    """Local IPv4 addresses a phone or second computer can actually open.

    Discovery is best-effort: a machine with no usable adapter must still start.
    """
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            try:
                probe.connect(("8.8.8.8", 80))
                candidate = probe.getsockname()[0]
            except Exception:
                candidate = None
    except Exception:
        candidate = None
    for address in ([candidate] if candidate else []) + _resolved_addresses():
        if address and not _virtual_address(address) and address not in found:
            found.append(address)
    return found


def _resolved_addresses():
    try:
        return [info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except Exception:
        return []


def open_urls(port, host):
    """Everything worth telling the user to open, best-effort."""
    urls = [f"http://127.0.0.1:{port}/"]
    if host in ("0.0.0.0", "::"):
        urls += [f"http://{address}:{port}/" for address in lan_addresses()]
    elif host not in ("127.0.0.1", "localhost", "::1"):
        urls.append(f"http://{host}:{port}/")
    return urls


def announce(urls):
    print("工作台已就绪：" + urls[0])
    for url in urls[1:]:
        print("同一局域网的设备也可以打开：" + url)


def identity(pid):
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            fields = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *[ctypes.byref(v) for v in fields]):
                return None
            return (fields[0].dwHighDateTime << 32) | fields[0].dwLowDateTime
        finally:
            kernel.CloseHandle(handle)
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except OSError:
        return None

def health(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=2) as r:
        return json.load(r)

def ensure_query_service(data,logs):
    """The separately tested query service keeps its own files and settings."""
    query_root=ROOT.parent/'content-query'
    config_path=query_root/'config.json'
    if not config_path.exists():return
    config=json.loads(config_path.read_text(encoding='utf-8'))
    if not config.get('workflow_connected'):return
    try:
        with urllib.request.urlopen('http://127.0.0.1:8091/health',timeout=2) as response:
            if json.load(response).get('application')=='content-query':return
    except Exception:pass
    try:
        with socket.socket() as probe:probe.bind(('127.0.0.1',8091))
    except OSError:
        print('查询器端口8091已被占用，未启动其他进程；可以先进入工作台查看搜索配置。')
        return
    with (logs/'query.log').open('ab') as output:
        options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
        process=subprocess.Popen([sys.executable,'-m','uvicorn','query_service.app:app','--host','127.0.0.1','--port','8091'],
            cwd=query_root,env={**os.environ,'PYTHONUTF8':'1','PYTHONPATH':str(query_root)},
            stdin=subprocess.DEVNULL,stdout=output,stderr=output,**options)
    data['processes'].append({'name':'query','pid':process.pid,'identity':identity(process.pid)})
    STATE.write_text(json.dumps(data,indent=2),encoding='utf-8')

def stop(data=None):
    data = data or (json.loads(STATE.read_text()) if STATE.exists() else {})
    if data.get("workspace") != str(ROOT):
        print("没有本项目的运行记录。")
        return
    # Flush the saved browser profile before terminating this checkout's processes.
    sys.path.insert(0,str(ROOT/'backend'))
    from app.core.config import get_settings
    from app.services.platform_account import PlatformAccountService
    account = PlatformAccountService(get_settings().storage_root)
    if account.status()['browser_open']:
        account.command('close')
        deadline = time.monotonic()+8
        while account.status()['browser_open'] and time.monotonic()<deadline:
            time.sleep(0.2)
        if account.status()['browser_open']:
            raise RuntimeError('抖音窗口仍在执行，请先完成当前操作或关闭连接窗口，再停止服务。')
    for item in reversed(data.get("processes", [])):
        if item["identity"] is None or identity(item["pid"]) != item["identity"]:
            continue
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(item["pid"]), "/T", "/F"],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(item["pid"], signal.SIGTERM)
    STATE.unlink(missing_ok=True)
    print("本项目服务已停止。")

def start(port, open_browser, host=None):
    host = host or DEFAULT_HOST
    subprocess.run([sys.executable, str(ROOT / "scripts/doctor.py")], check=True, cwd=ROOT)
    if STATE.exists():
        previous = json.loads(STATE.read_text())
        try:
            h = health(previous["port"])
            if h["api"]["instance_id"] == previous["instance_id"] and h["worker"]["status"] == "running":
                logs=ROOT/'storage'/'logs';logs.mkdir(parents=True,exist_ok=True)
                ensure_query_service(previous,logs)
                urls = open_urls(previous['port'], previous.get('host', DEFAULT_HOST))
                announce(urls)
                if open_browser: webbrowser.open(urls[0])
                return
        except Exception:
            pass
        stop(previous)
    with socket.socket() as probe:
        probe.bind((host, port))
    initialize_demo()
    logs = ROOT / "storage" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    instance = str(uuid.uuid4())
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT / "backend"),
           "CWB_INSTANCE_ID": instance}
    data = {"workspace": str(ROOT), "instance_id": instance, "port": port, "host": host, "processes": []}
    commands = [("api", api_command(port, host)),
                ("worker", ["-m", "app.worker", "--interval", "2", "--worker-id", instance])]
    try:
        ensure_query_service(data,logs)
        for name, command in commands:
            with (logs / f"{name}.log").open("ab") as output:
                options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
                process = subprocess.Popen([sys.executable, *command], cwd=ROOT, env=env,
                                           stdin=subprocess.DEVNULL, stdout=output, stderr=output, **options)
            data["processes"].append({"name": name, "pid": process.pid, "identity": identity(process.pid)})
            STATE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        for _ in range(60):
            try:
                h = health(port)
                if h["api"]["instance_id"] == instance and h["worker"].get("instance_id") == instance and h["worker"]["status"] == "running":
                    urls = open_urls(port, host)
                    print("API、后台任务和工作台已启动。")
                    announce(urls)
                    if open_browser: webbrowser.open(urls[0])
                    return
            except Exception:
                pass
            time.sleep(0.5)
        raise RuntimeError("服务未就绪，请查看 storage/logs/api.log 和 worker.log")
    except Exception:
        stop(data)
        raise

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status", "restart-worker", "restart-api"])
    parser.add_argument("--port", type=int, default=int(os.environ.get("CWB_PORT", "8000")))
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help="API 监听地址，默认 0.0.0.0（同一局域网的设备可访问）；只允许本机时传 127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.action == "stop": stop()
    elif args.action == "start": start(args.port, not args.no_browser, args.host)
    elif args.action == 'restart-worker': restart_worker()
    elif args.action == 'restart-api': restart_api()
    else: print(json.dumps(health(args.port), ensure_ascii=False, indent=2))

def restart_api():
    """Reload only API processes; keep Worker and dedicated account browsers alive."""
    data=json.loads(STATE.read_text(encoding='utf-8'))
    if data.get('workspace')!=str(ROOT):raise RuntimeError('运行记录不属于本项目。')
    host=data.get('host',DEFAULT_HOST)
    api=next(p for p in data['processes'] if p['name']=='api')
    try:observed=health(data['port'])
    except Exception:
        if identity(api['pid']) is not None:raise RuntimeError('API仍在运行但无法核验身份，未操作任何进程。')
        with socket.socket() as probe:probe.bind((host,data['port']))
        observed=None
    if observed:
        if observed['api']['instance_id']!=data['instance_id'] or api['identity'] is None or identity(api['pid'])!=api['identity']:
            raise RuntimeError('API身份已变化，未操作任何进程。')
    sys.path.insert(0,str(ROOT/'backend'))
    from app.core.config import get_settings
    from sqlalchemy import create_engine,select
    from app.models.entities import Job,ProviderExchange
    engine=create_engine(get_settings().database_url)
    try:
        with engine.connect() as c:
            if c.execute(select(Job.id).where(Job.state.in_(['running','queued']))).first() or c.execute(select(ProviderExchange.request_key).where(ProviderExchange.state=='in_flight')).first():
                raise RuntimeError('任务或模型调用仍在进行，请等完成后更新API。')
    finally:engine.dispose()
    # Windows venv launcher may have a separate real API child process.
    # Terminate these verified API PIDs only, without descendant termination.
    targets=list(dict.fromkeys([api['pid'],observed['api']['pid']])) if observed else []
    for pid in targets:
        if identity(pid) is None:continue
        if os.name=='nt':
            result=subprocess.run(['taskkill','/PID',str(pid),'/F'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode and identity(pid) is not None:raise RuntimeError('API进程未停止，请检查服务状态。')
        else:os.kill(pid,signal.SIGTERM)
    env={**os.environ,'PYTHONUTF8':'1','PYTHONPATH':str(ROOT/'backend'),'CWB_INSTANCE_ID':data['instance_id']}
    opts={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
    with (ROOT/'storage/logs/api.log').open('ab') as output:
        child=subprocess.Popen([sys.executable,*api_command(data['port'],host)],cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=output,stderr=output,**opts)
    data['processes']=[p for p in data['processes'] if p['name']!='api']
    data['processes'].insert(0,{'name':'api','pid':child.pid,'identity':identity(child.pid)})
    STATE.write_text(json.dumps(data,indent=2),encoding='utf-8')
    for _ in range(40):
        try:
            h=health(data['port'])
            if h['api']['instance_id']==data['instance_id'] and h['api']['pid'] not in targets:
                print('API已更新，Worker和当前账号连接窗口保留。');return
        except Exception:pass
        time.sleep(.25)
    raise RuntimeError('API未就绪，请查看本项目api.log。')


def restart_worker():
    """Load background changes without closing the API-owned editor browser."""
    data=json.loads(STATE.read_text(encoding='utf-8'))
    if data.get('workspace')!=str(ROOT) or health(data['port'])['api']['instance_id']!=data['instance_id']:
        raise RuntimeError('运行中的 API 不属于当前项目，未操作任何进程。')
    sys.path.insert(0,str(ROOT/'backend'))
    from app.core.config import get_settings
    from sqlalchemy import create_engine,select
    from app.models.entities import Job
    engine=create_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            # Queued jobs have not acquired a worker lease and are safe to
            # pick up after a worker restart. Blocking them here can strand
            # the queue precisely when the worker heartbeat is stale.
            if connection.execute(select(Job.id).where(Job.state == 'running')).first():
                raise RuntimeError('内容生产正在执行，请等当前任务到达停止边界后再更新后台。')
    finally:
        engine.dispose()
    workers=[item for item in data['processes'] if item['name']=='worker']
    for item in workers:
        if item['identity'] is not None and identity(item['pid'])==item['identity']:
            if os.name=='nt':
                subprocess.run(['taskkill','/PID',str(item['pid']),'/T','/F'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,check=True)
            else:
                os.killpg(item['pid'],signal.SIGTERM)
    env={**os.environ,'PYTHONUTF8':'1','PYTHONPATH':str(ROOT/'backend'),'CWB_INSTANCE_ID':data['instance_id']}
    options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
    with (ROOT/'storage/logs/worker.log').open('ab') as output:
        launched_at=datetime.now(timezone.utc)
        child=subprocess.Popen([sys.executable,'-m','app.worker','--interval','2','--worker-id',data['instance_id']],
            cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=output,stderr=output,**options)
    data['processes']=[item for item in data['processes'] if item['name']!='worker']
    data['processes'].append({'name':'worker','pid':child.pid,'identity':identity(child.pid)})
    STATE.write_text(json.dumps(data,indent=2),encoding='utf-8')
    for _ in range(20):
        time.sleep(0.5)
        if identity(child.pid) is None:
            raise RuntimeError('后台任务未启动，请查看本项目 worker 日志。')
        worker=health(data['port'])['worker']
        observed=datetime.fromisoformat(worker['last_heartbeat']) if worker.get('last_heartbeat') else None
        if worker.get('instance_id')==data['instance_id'] and worker.get('status')=='running' and observed and observed>=launched_at:
            print('后台任务已更新，API 与当前抖音编辑窗口保留。')
            return
    raise RuntimeError('后台任务心跳未就绪。')

def initialize_demo():
    # Initialize only a genuinely empty default database. Existing user data wins.
    database = ROOT / "storage/cwb.db"
    if os.environ.get("CWB_DATABASE_URL") or os.environ.get("CWB_STORAGE_ROOT"):
        return
    if database.exists():
        with sqlite3.connect(database) as db:
            exists = db.execute("SELECT name FROM sqlite_master WHERE name='content_item'").fetchone()
            if exists and db.execute("SELECT count(*) FROM content_item").fetchone()[0]:
                return
    demo = ROOT / "examples/demo/storage"
    if not (demo / "workbench.db").exists():
        raise RuntimeError("预设数据缺失，请重新解压完整交付包")
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(demo / "workbench.db") as source, sqlite3.connect(database) as dest:
        source.backup(dest)
    shutil.copytree(demo / "artifacts", database.parent / "artifacts", dirs_exist_ok=True)
    (database.parent / "tmp").mkdir(parents=True, exist_ok=True)
    if (demo / "tmp").exists():
        shutil.copytree(demo / "tmp", database.parent / "tmp", dirs_exist_ok=True)
    shutil.copy2(demo / "profiles.json", database.parent / "profiles.json")
    print("已载入六组演示内容、66 张页图和示例复盘。")

if __name__ == "__main__":
    main()
