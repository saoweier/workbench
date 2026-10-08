"""Install into a local environment; use bundled Windows 3.13 wheels offline."""
from pathlib import Path
import argparse
import os
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]

def main():
    os.environ["PYTHONUTF8"] = "1"
    parser = argparse.ArgumentParser()
    parser.add_argument("--venv", type=Path, default=ROOT / ".venv")
    args = parser.parse_args()
    if not (3, 11) <= sys.version_info[:2] <= (3, 13):
        raise SystemExit("请使用 Python 3.11–3.13；离线依赖包适用于 Windows 64 位 Python 3.13。")
    target = args.venv.resolve()
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        print("正在创建项目运行环境…", flush=True)
        venv.EnvBuilder(with_pip=True).create(target)
    wheels = ROOT / "vendor/wheels"
    offline = os.name == "nt" and sys.version_info[:2] == (3, 13) and bool(list(wheels.glob("*.whl")))
    command = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
    if offline:
        command += ["--no-index", "--find-links", str(wheels), "-r", str(ROOT / "requirements-lock.txt")]
    else:
        command += ["--timeout", "20", "--retries", "1", "-r", str(ROOT / "requirements.txt")]
    print("正在安装依赖（本地离线包）…" if offline else "正在联网安装依赖…", flush=True)
    subprocess.run(command, check=True, cwd=ROOT, timeout=300)
    subprocess.run([str(python), str(ROOT / "scripts/doctor.py")], cwd=ROOT, check=True)
    print("安装完成。双击 start.bat 打开工作台。")

if __name__ == "__main__": main()
