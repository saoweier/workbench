"""Install an extracted release and verify its presets and service lifecycle."""
from pathlib import Path
import argparse
import hashlib
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    checks = []
    target = ROOT / "storage/tmp" / ("release-check-" + uuid.uuid4().hex[:8])
    target.mkdir(parents=True)
    with zipfile.ZipFile(args.archive) as archive:
        for name in archive.namelist():
            path = (target / name).resolve()
            if not path.is_relative_to(target.resolve()):
                raise ValueError("Unsafe archive entry")
        archive.extractall(target)
    project = target / "content-workbench"
    env = {k: v for k, v in os.environ.items() if not k.startswith("CWB_")}
    env["PYTHONUTF8"] = "1"
    log = ROOT / "docs/test-artifacts/package-start.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    def run(*command):
        with log.open("ab") as output:
            subprocess.run(command, cwd=project, env=env, stdout=output, stderr=output,
                           check=True, timeout=300)
    run(sys.executable, "scripts/bootstrap.py")
    checks.append("解压目录使用随包依赖独立离线安装成功")
    python = project / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    def get(path):
        with urllib.request.urlopen(base + path, timeout=10) as response:
            return response.read()
    def health():
        return json.loads(get("/api/v1/health"))
    def launch():
        run(str(python), "scripts/launcher.py", "start", "--port", str(port), "--no-browser")
    def stop():
        run(str(python), "scripts/launcher.py", "stop")
    sentinel = None
    try:
        launch()
        current = health()
        assert current["api"]["status"] == "ok" and current["worker"]["status"] == "running"
        checks.append("首次启动 API 与 Worker 就绪")
        assert b"<!DOCTYPE html>" in get("/") or b"<!doctype html>" in get("/")
        checks.append("工作台首页可访问")
        contents = json.loads(get("/api/v1/contents"))["items"]
        assert len(contents) == 6 and all(c["display_id"].startswith("DEMO-") for c in contents)
        checks.append("空库自动载入六组预设内容")
        with sqlite3.connect(project / "storage/cwb.db") as db:
            artifacts = db.execute("SELECT id, sha256 FROM artifact").fetchall()
            assert len(artifacts) == 66
            for aid, digest in artifacts:
                data = get(f"/api/v1/artifacts/{aid}/raw")
                assert data.startswith(b"\x89PNG") and hashlib.sha256(data).hexdigest() == digest
            checks.append("66 张 PNG 通过接口读取及校验和检查")
            revisions = db.execute("SELECT p.id, c.display_id, p.platform, p.version FROM platform_revision p JOIN content_revision r ON r.id=p.content_revision_id JOIN content_item c ON c.id=r.content_id").fetchall()
            packages = 0
            for prid, display, platform, version in revisions:
                name = f"{display}-{platform}-pr{version}.zip"
                if not (project / "storage/artifacts/packages" / name).exists():
                    continue
                data = get(f"/api/v1/packages/{prid}/download")
                with zipfile.ZipFile(io.BytesIO(data)) as package:
                    assert package.testzip() is None
                    assert any(n.endswith(".png") for n in package.namelist())
                packages += 1
            assert packages == 6
            checks.append("六个发布包实际下载及 ZIP 完整性检查通过")
            counts = {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                      for table in ["publication", "metric_snapshot", "comment_sample", "review_report", "topic_feedback"]}
            assert counts == {"publication": 6, "metric_snapshot": 8, "comment_sample": 16, "review_report": 2, "topic_feedback": 2}
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert not db.execute("PRAGMA foreign_key_check").fetchall()
            checks.append("发布、指标、评论、复盘预设齐全且数据库完整")
        launch()
        assert health()["api"]["instance_id"] == current["api"]["instance_id"]
        assert len(json.loads(get("/api/v1/contents"))["items"]) == 6
        checks.append("重复启动复用服务且不重复填充数据")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        sentinel = subprocess.Popen([str(python), "-c", "import time; time.sleep(120)"], **options)
        stop()
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
        assert sentinel.poll() is None
        checks.append("停止本项目不结束无关 Python 进程")
        launch()
        assert health()["api"]["instance_id"] != current["api"]["instance_id"]
        assert len(json.loads(get("/api/v1/contents"))["items"]) == 6
        checks.append("重新启动保留预设与历史数据")
    finally:
        stop()
        if sentinel is not None and sentinel.poll() is None:
            sentinel.terminate()
            sentinel.wait(timeout=10)
    result = {"passed": len(checks), "failed": 0, "checks": checks,
              "archive": args.archive.name, "workspace": str(project),
              "sha256": hashlib.sha256(args.archive.read_bytes()).hexdigest()}
    (ROOT / "docs/test-artifacts/package-smoke.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
