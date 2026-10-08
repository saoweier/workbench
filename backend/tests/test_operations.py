"""运维边界回归：SQLite 在线快照、恢复归档边界与 Windows 服务检测。"""
from __future__ import annotations

import contextlib
import io
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import backup, restore  # noqa: E402
from scripts.run_all_tests import STAGES, parse_summary  # noqa: E402

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{(' → ' + detail) if detail else ''}")


with tempfile.TemporaryDirectory(prefix="cwb-operations-") as tmp:
    sandbox = Path(tmp)
    work = sandbox / "project"
    storage = work / "storage"
    storage.mkdir(parents=True)
    db = storage / "cwb.db"
    artifact = storage / "artifacts" / "C001" / "fixture.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("fixture-artifact", encoding="utf-8")
    (storage / "profiles.json").write_text("{}", encoding="utf-8")
    (storage / "secrets.json").write_text('{"api_key":"never-backup"}', encoding="utf-8")

    # 保持写连接打开，确保最新的已提交页仍位于 WAL 中。
    writer = sqlite3.connect(db)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE sample (value TEXT NOT NULL)")
    writer.execute("INSERT INTO sample VALUES ('committed-in-wal')")
    writer.commit()

    backup.ROOT = work
    backup.STORAGE = storage
    archive_dir = sandbox / "backups"
    with contextlib.redirect_stdout(io.StringIO()):
        backup_status = backup.main(["backup.py", str(archive_dir)])
    writer.close()

    archives = list(archive_dir.glob("*.tar.gz"))
    snapshot_ok = False
    secrets_excluded = False
    if archives:
        with tarfile.open(archives[0], "r:gz") as tf:
            secrets_excluded = "storage/secrets.json" not in tf.getnames()
            extracted = sandbox / "snapshot.db"
            extracted.write_bytes(tf.extractfile("storage/cwb.db").read())
        conn = sqlite3.connect(extracted)
        try:
            snapshot_ok = conn.execute("SELECT value FROM sample").fetchone() == (
                "committed-in-wal",
            )
        except sqlite3.DatabaseError:
            snapshot_ok = False
        finally:
            conn.close()
    check("备份包含 WAL 中已提交的数据", backup_status == 0 and snapshot_ok)
    check("备份排除密钥文件", secrets_excluded)

    # 恢复拒绝越界成员，且不能在项目根目录外创建文件。
    restore_root = sandbox / "restore-project"
    restore_storage = restore_root / "storage"
    restore_storage.mkdir(parents=True)
    restore.ROOT = restore_root
    restore.STORAGE = restore_storage
    malicious = sandbox / "malicious.tar.gz"
    with tarfile.open(malicious, "w:gz") as tf:
        payload = b"outside"
        info = tarfile.TarInfo("../escaped.txt")
        info.size = len(payload)
        import io as _io
        tf.addfile(info, _io.BytesIO(payload))
    with patch.object(restore, "_services_running", return_value=False), \
            contextlib.redirect_stdout(io.StringIO()):
        restore_status = restore.main(["restore.py", str(malicious), "--force"])
    check("恢复拒绝路径越界的归档成员",
          restore_status != 0 and not (sandbox / "escaped.txt").exists())

    with patch.object(restore, "_services_running", return_value=False), \
            contextlib.redirect_stdout(io.StringIO()):
        roundtrip_status = restore.main(["restore.py", str(archives[0]), "--force"])
    restored_db = restore_storage / "cwb.db"
    restored_artifact = restore_storage / "artifacts" / "C001" / "fixture.txt"
    roundtrip_ok = False
    if restored_db.exists() and restored_artifact.exists():
        conn = sqlite3.connect(restored_db)
        try:
            roundtrip_ok = (
                conn.execute("SELECT value FROM sample").fetchone() == ("committed-in-wal",)
                and restored_artifact.read_text(encoding="utf-8") == "fixture-artifact"
            )
        except sqlite3.DatabaseError:
            roundtrip_ok = False
        finally:
            conn.close()
    check("备份可恢复数据库与产物", roundtrip_status == 0 and roundtrip_ok)

    # tasklist 不包含命令行；Windows 必须读取进程命令行而非只看 python.exe。
    with patch.object(restore.sys, "platform", "win32"), \
            patch("subprocess.run", return_value=SimpleNamespace(
                stdout="python.exe,1234,Console,1,10,000 K\r\n")) as run:
        restore._services_running()
    cmd = run.call_args.args[0] if run.call_args else []
    check("Windows 服务检测查询进程命令行", any(
        isinstance(part, str) and "Get-CimInstance Win32_Process" in part
        for part in cmd
    ))

runner = (ROOT / "scripts" / "run-all-tests.sh").read_text(encoding="utf-8")
check("跨平台测试入口相对脚本定位项目根目录",
      "BASH_SOURCE[0]" in runner and "/workspace/content-workbench" not in runner)
backup_shell = (ROOT / "scripts" / "backup.sh").read_text(encoding="utf-8")
restore_shell = (ROOT / "scripts" / "restore.sh").read_text(encoding="utf-8")
stop_script = (ROOT / "scripts" / "stop-services.ps1").read_text(encoding="utf-8")
check("Unix 备份入口复用一致性 SQLite 快照实现",
      "scripts/backup.py" in backup_shell and "tar -czf" not in backup_shell)
check("Unix 恢复入口复用安全路径校验实现",
      "scripts/restore.py" in restore_shell and "tar -xzf" not in restore_shell)
check("Windows 停止脚本只匹配本项目虚拟环境进程",
      ".venv\\Scripts\\python.exe" in stop_script
      and "ExecutablePath" in stop_script
      and "uvicorn app.main:app" in stop_script
      and "app.worker" in stop_script
      and "taskkill" not in (ROOT / "stop.bat").read_text(encoding="utf-8"))
check("跨平台回归汇总器识别中文统计行并含所有阶段",
      parse_summary("结果：61 通过 / 0 失败") == (61, 0)
      and parse_summary("279 通过 / 0 失败") == (279, 0)
      and {"p0_smoke", "p1_e2e", "p2_e2e", "p3_e2e", "p4_e2e", "p5_e2e", "operations", "delivery"}.issubset(STAGES))

print(f"\n{len(PASS)} 通过 / {len(FAIL)} 失败")
if FAIL:
    print("失败项：")
    for name in FAIL:
        print(f"  - {name}")
sys.exit(1 if FAIL else 0)
