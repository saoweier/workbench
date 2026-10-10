# run_cwb.py - 单实例启动器：全网段 0.0.0.0:9195 只跑 1 个 API + 1 个 Worker
# 背景：底层 harness 每次后台启动会拉起 2 份相同进程，用锁文件(pid)去重。
import os, sys, time, signal, subprocess, ctypes

ROOT   = r"C:\Users\Administrator\Desktop\cloud\view\workbench"
PY     = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PORT   = "9195"

API_ARGS    = ["-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", PORT]
WORKER_ARGS = ["-m", "app.worker", "--interval", "2", "--worker-id", "manual-9195"]

ENV = os.environ.copy()
ENV["PYTHONPATH"]    = "backend"
ENV["PYTHONUTF8"]    = "1"
ENV["CWB_INSTANCE_ID"] = "manual-9195"


import socket

SENTINEL_PORT = 39195  # 仅用于单实例去重的哨兵端口（与 9195 服务端口无关）


def acquire_singleton():
    """用 TCP 哨兵端口做单实例去重（最可靠，无需 ctypes 错误码）。
    首个实例绑定并持住该端口；重复实例 bind 失败即退出。
    监听套接字关闭后端口立即释放（不产生 TIME_WAIT），故无需 SO_REUSEADDR。
    返回 socket 表示本进程成为唯一实例；返回 None 表示重复进程，应退出。"""
    for attempt in range(5):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("0.0.0.0", SENTINEL_PORT))
            s.listen(1)
            print(f"[{time.strftime('%H:%M:%S')}] 获得单实例哨兵端口(:{SENTINEL_PORT})。", flush=True)
            return s
        except OSError as e:
            try:
                s.close()
            except Exception:
                pass
            if attempt < 4:
                time.sleep(0.2)
                continue
            print(f"[{time.strftime('%H:%M:%S')}] 哨兵端口被占用(已有实例)，本重复进程退出: {e}", flush=True)
            return None


# ---- Windows JobObject：父退出即回收子进程 ----
job = None
try:
    kernel32 = ctypes.windll.kernel32
    job = kernel32.CreateJobObjectW(None, None)
    class BASIC(ctypes.Structure):
        _fields_ = [("LimitFlags", ctypes.c_uint32),("a",ctypes.c_void_p),("b",ctypes.c_void_p),
                    ("ActiveProcessLimit", ctypes.c_uint32),("Affinity", ctypes.c_void_p),
                    ("PriorityClass", ctypes.c_uint32),("SchedulingClass", ctypes.c_uint32)]
    class IO(ctypes.Structure):
        _fields_ = [("a",ctypes.c_ulonglong)]*5
    class EXT(ctypes.Structure):
        _fields_ = [("Basic", BASIC),("Io", IO),("c",ctypes.c_void_p),("d",ctypes.c_void_p),
                    ("e",ctypes.c_void_p),("f",ctypes.c_void_p)]
    info = EXT()
    info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
except Exception as e:
    print(f"[{time.strftime('%H:%M:%S')}] JobObject 初始化失败(忽略): {e}", flush=True)
    job = None


def spawn(args):
    p = subprocess.Popen([PY, *args], cwd=ROOT, env=ENV,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if job:
        try:
            kernel32.AssignProcessToJobObject(job, int(p._handle))
        except Exception:
            pass
    return p


def main():
    sentinel = acquire_singleton()
    if sentinel is None:
        sys.exit(0)
    print(f"[{time.strftime('%H:%M:%S')}] 启动工作台 (v1.4.4, 0.0.0.0:{PORT})...", flush=True)
    api = spawn(API_ARGS)
    worker = spawn(WORKER_ARGS)
    print(f"[{time.strftime('%H:%M:%S')}] api pid={api.pid} | worker pid={worker.pid}", flush=True)
    try:
        while True:
            time.sleep(5)
            if api.poll() is not None:
                print(f"[{time.strftime('%H:%M:%S')}] api 退出，重启", flush=True)
                api = spawn(API_ARGS)
            if worker.poll() is not None:
                print(f"[{time.strftime('%H:%M:%S')}] worker 退出，重启", flush=True)
                worker = spawn(WORKER_ARGS)
    except KeyboardInterrupt:
        pass
    finally:
        print(f"[{time.strftime('%H:%M:%S')}] 关闭子进程...", flush=True)
        for p in (api, worker):
            try:
                if p.poll() is None:
                    p.terminate()
            except Exception:
                pass
        try:
            sentinel.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
