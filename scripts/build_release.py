"""Build installable source release; exclude user storage and credentials."""
from pathlib import Path
import hashlib
import json
import zipfile
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT.parent / "content-workbench-v1.4.1-20261008.zip"

def main():
    names=["README.md","requirements.txt","requirements-lock.txt",".gitignore","install.bat","start.bat","stop.bat","test.bat"]
    selected=[ROOT/name for name in names]
    for name in ["backend","frontend","scripts","docs","examples","vendor"]:
        selected += [p for p in (ROOT/name).rglob("*") if p.is_file()
            and "test-artifacts" not in p.parts
            and "__pycache__" not in p.parts and p.suffix not in {".pyc",".bak"}
            and p.name not in {"secrets.json","provider_configs.json","worker.heartbeat","services.json"}
            and not p.name.endswith(("-wal","-shm"))]
    assert all(ROOT / "storage" not in p.parents for p in selected)
    with zipfile.ZipFile(OUTPUT,"w",zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for p in sorted(selected):archive.write(p,"content-workbench/"+p.relative_to(ROOT).as_posix())
    checksum=hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    OUTPUT.with_suffix(".sha256").write_text(checksum+"  "+OUTPUT.name+"\n",encoding="utf-8")
    print(json.dumps({"path":str(OUTPUT),"files":len(selected),"bytes":OUTPUT.stat().st_size,"sha256":checksum},indent=2))
if __name__ == "__main__": main()
