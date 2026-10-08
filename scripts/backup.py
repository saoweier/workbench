"""内容工作台 · 备份（跨平台，Windows / macOS / Linux 都能用）。

用法：
    python scripts/backup.py [输出目录]

备份三样东西：数据库、产物（图片/发布包）、配置（账号定位与 Provider 设置）。
密钥文件单独标记并**默认不打包**——备份常常被随手拷到别处，密钥不该跟着走。

为什么用 Python 而不是只给 .sh：
    Windows 上跑不了 .sh。备份是"出事时才想起来要做"的事，
    到那个时候才发现脚本用不了就太晚了。
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORAGE = ROOT / "storage"

#: 明确列出要打包的路径。**不要图省事直接打包整个 storage/**——
#: 那样迟早会把密钥和临时文件一起打进去。
INCLUDE = [
    "storage/cwb.db",
    "storage/artifacts",
    "storage/profiles.json",
    "storage/provider_configs.json",
    "storage/publishing_preferences.json",
    "storage/content-media",
    "storage/hotpush",
]

#: 含密钥，永不打包
NEVER_PACK = "storage/secrets.json"


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


def _sqlite_snapshot(source_path: Path, snapshot_path: Path) -> None:
    """Take a consistent SQLite snapshot, including committed WAL pages."""
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(snapshot_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def main(argv: list[str]) -> int:
    out_dir = Path(argv[1]) if len(argv) > 1 else STORAGE / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive = out_dir / f"cwb-backup-{stamp}.tar.gz"

    print(f"备份到：{archive}")
    print()

    # 逐项报告，避免"备份成功"但其实什么都没进去
    print("包含：")
    present: list[str] = []
    for rel in INCLUDE:
        p = ROOT / rel
        if p.exists():
            print(f"  ✓ {rel:<34} {_size(p)}")
            present.append(rel)
        else:
            print(f"  · {rel:<34} （不存在，跳过）")

    print()
    print("排除（含密钥，需自行处理）：")
    secret = ROOT / NEVER_PACK
    if secret.exists():
        print(f"  ! {NEVER_PACK}  ← API 密钥在这里，未打包")
        print("    需要一起备份请手动拷贝，并确认目的地的权限。")
    else:
        print(f"  · {NEVER_PACK} 不存在")

    if not present:
        print()
        print(" 没有可备份的内容（storage/ 是空的？）")
        return 1

    with tempfile.TemporaryDirectory(prefix="cwb-backup-") as tmp:
        snapshot = Path(tmp) / "cwb.db"
        if "storage/cwb.db" in present:
            _sqlite_snapshot(STORAGE / "cwb.db", snapshot)
        with tarfile.open(archive, "w:gz") as tf:
            for rel in present:
                source = snapshot if rel == "storage/cwb.db" else ROOT / rel
                tf.add(source, arcname=rel)

    print()
    print(f"完成：{archive}  ({_size(archive)})")
    print()
    print(f'恢复方法： python scripts/restore.py "{archive}"')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
