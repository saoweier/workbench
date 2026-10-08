"""内容工作台 · 恢复（跨平台，Windows / macOS / Linux 都能用）。

用法：
    python scripts/restore.py <备份文件.tar.gz> [--force]

默认**不覆盖**已有数据：先停下来问清楚。
恢复错方向比没有备份更糟，所以这里刻意多问了一句。
"""
from __future__ import annotations

import sys
import sqlite3
import tarfile
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
import tempfile

ROOT = Path(__file__).resolve().parent.parent
STORAGE = ROOT / "storage"

CHECK = [
    "storage/cwb.db",
    "storage/artifacts",
    "storage/profiles.json",
    "storage/provider_configs.json",
    "storage/publishing_preferences.json",
    "storage/content-media",
    "storage/hotpush",
]


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return f"{n:.1f}GB"


def _size(p: Path) -> str:
    if p.is_file():
        return _human(p.stat().st_size)
    if p.is_dir():
        return _human(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()))
    return "-"


def _services_running() -> bool:
    """判断本机是否有内容工作台 API 或 Worker 进程。"""
    import subprocess

    try:
        if sys.platform == "win32":
            out = subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    "$p = Get-CimInstance Win32_Process | Where-Object { "
                    "$_.Name -match '^python(w)?\\.exe$' -and "
                    "$_.CommandLine -match '(?i)(uvicorn\\s+app\\.main:app|app\\.worker)' "
                    "}; if ($p) { 'running' }",
                ],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=10,
            ).stdout
            return bool(out and out.strip())
        out = subprocess.run(
            ["ps", "-eo", "args"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        ).stdout
        return "uvicorn app.main:app" in out or "app.worker" in out
    except Exception:
        return True  # 判断失败时宁可阻止覆盖，也不冒险写坏运行中的数据


def _validate_archive(tf: tarfile.TarFile) -> None:
    """Accept only regular project data files under the documented storage paths."""
    seen: set[str] = set()
    for member in tf.getmembers():
        name = member.name
        posix = PurePosixPath(name)
        windows = PureWindowsPath(name)
        parts = posix.parts
        if (not name or "\\" in name or posix.is_absolute() or windows.is_absolute()
                or windows.drive or ".." in parts or not parts or parts[0] != "storage"):
            raise ValueError(f"归档路径不安全：{name!r}")
        if name in seen:
            raise ValueError(f"归档包含重复路径：{name!r}")
        seen.add(name)
        is_dir = member.isdir()
        if not (is_dir or member.isfile()):
            raise ValueError(f"归档包含不支持的文件类型：{name!r}")
        rel = "/".join(parts)
        allowed_file = rel in {
            "storage/cwb.db", "storage/profiles.json",
            "storage/provider_configs.json", "storage/publishing_preferences.json",
        }
        allowed_artifact = len(parts) >= 2 and parts[:2] in {("storage", "artifacts"),("storage","content-media"),("storage","hotpush")}
        allowed_dir = is_dir and rel in {"storage", "storage/artifacts", "storage/content-media", "storage/hotpush"}
        if not (allowed_file or allowed_artifact or allowed_dir):
            raise ValueError(f"归档路径不在允许恢复的文件范围内：{name!r}")


def _safe_extract(tf: tarfile.TarFile, root: Path) -> None:
    """Extract a validated archive without following links or leaving root."""
    root_resolved = root.resolve()
    _validate_archive(tf)
    members = tf.getmembers()
    destinations: list[tuple[tarfile.TarInfo, Path]] = []
    for member in members:
        dest = root.joinpath(*PurePosixPath(member.name).parts)
        resolved = dest.resolve()
        try:
            resolved.relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError(f"恢复目标越出项目目录：{member.name!r}") from exc
        destinations.append((member, dest))
    for member, dest in destinations:
        if member.isdir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        source = tf.extractfile(member)
        if source is None:
            raise ValueError(f"无法读取归档成员：{member.name!r}")
        with source, dest.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)


def _write_safety_backup(destination: Path) -> None:
    """Save a consistent pre-restore snapshot of database and user artifacts."""
    with tempfile.TemporaryDirectory(prefix="cwb-pre-restore-") as tmp:
        snapshot = Path(tmp) / "cwb.db"
        if (STORAGE / "cwb.db").exists():
            source = sqlite3.connect(STORAGE / "cwb.db")
            target = sqlite3.connect(snapshot)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
        with tarfile.open(destination, "w:gz") as tf:
            for rel in CHECK:
                path = ROOT / rel
                if not path.exists():
                    continue
                tf.add(snapshot if rel == "storage/cwb.db" else path, arcname=rel)


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a != "--force"]
    force = "--force" in argv

    if not args:
        print("用法： python scripts/restore.py <备份文件.tar.gz> [--force]")
        print()
        print("现有备份：")
        backups = sorted((STORAGE / "backups").glob("*.tar.gz"))
        if backups:
            for b in backups:
                print(f"  {b}  ({_size(b)})")
        else:
            print("  （storage/backups/ 下没有备份）")
        return 1

    archive = Path(args[0])
    if not archive.is_file():
        print(f"X 备份文件不存在：{archive}")
        return 1

    print("=" * 62)
    print(" 从备份恢复")
    print("=" * 62)
    print(f" 备份文件：{archive}  ({_size(archive)})")
    print()

    with tarfile.open(archive, "r:gz") as tf:
        names = tf.getnames()
        try:
            _validate_archive(tf)
        except ValueError as exc:
            print(f"X 备份文件未通过路径与类型校验：{exc}")
            return 1
    print(" 备份内容：")
    for n in names[:20]:
        print(f"   {n}")
    print(f"   … 共 {len(names)} 项")
    print()

    # 服务在跑的时候恢复数据库会写坏文件，所以必须先停
    if _services_running():
        if force:
            print(" --force 已指定，但请**自行确认**服务已停止。")
            print(" 建议先运行： stop.bat   （Windows）/ bash scripts/stop.sh")
            print(" 要继续请重新执行并先停掉服务。")
            return 1
        print(" 检测到服务可能在运行。")
        print(" 恢复前必须停止服务，否则数据库可能写坏。")
        print()
        print(" 先执行： stop.bat   （Windows）/ bash scripts/stop.sh")
        return 1

    # 确认要覆盖
    if (STORAGE / "cwb.db").exists() and not force:
        print()
        print(" 当前已有数据（storage/cwb.db）。恢复会覆盖它。")
        try:
            ans = input(" 确认覆盖？输入 yes 继续： ").strip().lower()
        except EOFError:
            ans = ""
        if ans != "yes":
            print(" 已取消，未做任何改动。")
            return 1

    # 就地保底：万一恢复的内容不对，还能退回来
    backups_dir = STORAGE / "backups"
    if any((ROOT / rel).exists() for rel in CHECK):
        backups_dir.mkdir(parents=True, exist_ok=True)
        safety = backups_dir / f"pre-restore-{datetime.now():%Y%m%d-%H%M%S-%f}.tar.gz"
        _write_safety_backup(safety)
        print(f" 已留存恢复前快照：{safety}")

    print()
    print(" 正在恢复…")
    try:
        with tarfile.open(archive, "r:gz") as tf:
            _safe_extract(tf, ROOT)
    except (OSError, tarfile.TarError, ValueError) as exc:
        print(f"X 恢复未完成：{exc}")
        return 1

    print()
    print(" 恢复完成。恢复的内容：")
    for rel in CHECK:
        p = ROOT / rel
        if p.exists():
            print(f"   ✓ {rel:<34} {_size(p)}")

    print()
    if (STORAGE / "secrets.json").exists():
        print(" 提示：secrets.json 存在，密钥未被备份覆盖，沿用当前这一份。")
    else:
        print(" 提示：未找到 secrets.json。若之前配过 Provider，需要重新填一次密钥")
        print("       （备份里不含密钥是有意的）。")
    print()
    print(" 重新启动： start.bat   （Windows）/ bash scripts/start.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
