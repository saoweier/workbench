"""在 Windows 上原生启动 SearXNG（仅本地回环，供工作台的搜索通道使用）。

官方不支持 Windows，直接跑会缺 Unix 专有模块；这里用 shim 补上 `pwd` / `grp`，
并按 Unix 方式注入 POSIX 环境。仅本文件生效，不改动 SearXNG 源码语义。

用法：
  SEARXNG_SRC=<源码目录> PYTHONUTF8=1 python run_searxng.py

环境变量（未给则用默认值）：
  SEARXNG_SRC             SearXNG 源码树目录（必填）
  SEARXNG_SETTINGS_PATH   默认 <工作台>/storage/searxng/config/settings.yml
  SEARXNG_BIND_ADDRESS    默认 127.0.0.1
  SEARXNG_PORT            默认 8088

**必须带 PYTHONUTF8=1**：否则进程内默认 GBK，URL 里的中文查询参数按 GBK 解码
会变成乱码，表现为「引擎在返回结果，但全都不对题」。
"""
import os
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
# deploy/searxng/windows/run_searxng.py -> 工作台根目录
ROOT = HERE.parents[2]

SRC = os.environ.get("SEARXNG_SRC")
if not SRC:
    raise SystemExit("请设置 SEARXNG_SRC 指向 SearXNG 源码目录，例如：\n"
                     "  SEARXNG_SRC=.workbuddy-tmp/searxng-src PYTHONUTF8=1 python run_searxng.py")


class _PwEnt:
    """`pwd.struct_passwd` 的最小替身，SearXNG 只用到这几个属性。"""

    pw_name = "user"
    pw_uid = 0
    pw_gid = 0
    pw_dir = str(ROOT)
    pw_shell = ""


def _install_posix_shims():
    """补上仅 Unix 才有的标准模块，使 Windows 上也能跑起来。"""
    if "pwd" not in sys.modules:
        module = types.ModuleType("pwd")
        module.getpwuid = lambda uid: _PwEnt()          # type: ignore[attr-defined]
        module.getpwnam = lambda name: _PwEnt()         # type: ignore[attr-defined]
        module.struct_passwd = _PwEnt
        sys.modules["pwd"] = module
    if "grp" not in sys.modules:
        module = types.ModuleType("grp")
        module.getgrgid = lambda gid: type("G", (), {"gr_name": "users", "gr_gid": gid})()
        module.getgrnam = lambda name: type("G", (), {"gr_name": name, "gr_gid": 0})()
        sys.modules["grp"] = module


os.environ.setdefault("SEARXNG_SETTINGS_PATH", str(ROOT / "storage/searxng/config/settings.yml"))
os.environ.setdefault("SEARXNG_BIND_ADDRESS", "127.0.0.1")
os.environ.setdefault("SEARXNG_PORT", "8088")
os.environ.setdefault("SEARXNG_DEBUG", "0")
os.environ.setdefault("SEARXNG_LIMITER", "false")
os.environ.setdefault("SEARXNG_PUBLIC_INSTANCE", "false")

_install_posix_shims()
sys.path.insert(0, str(Path(SRC).resolve()))

if __name__ == "__main__":
    if sys.version_info < (3, 11):
        raise SystemExit("SearXNG 需要 Python 3.11+（本机为 %s）" % sys.version.split()[0])
    print("settings =", os.environ["SEARXNG_SETTINGS_PATH"], flush=True)
    print("bind     =", os.environ["SEARXNG_BIND_ADDRESS"], os.environ["SEARXNG_PORT"], flush=True)
    from searx.webapp import run

    run()
