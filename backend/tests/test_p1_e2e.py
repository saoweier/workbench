"""P1 端到端测试：seed → 渲染 → 审核 → 发布包。

重点验证不变量：
- 无批准不得产出发布包（第 1 条）
- 过期批准不能覆盖新图/新文案（第 1 条）
- 单平台失败不能把整体标完成（聚合状态）
- 重试不重复追加同一阶段产物（第 3 条）
- 不接受客户端伪造 actor=system（第 6 条）

**存储隔离**：本测原先直接写 `storage/`，并 `rmtree(artifact_dir/"C001")`
清理现场——那会**删掉生产库里 C001 的图和发布包**（数据库记录还在，
表现成"预览页显示图未生成"，极难排查）。现在与 P0/P2–P5 一致：
先给环境变量指到独立临时目录，再 import app。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ---- 环境必须最先设置：配置在 import 时被 lru_cache 固化 ----
_TMP = Path(tempfile.mkdtemp(prefix="cwb-p1-"))
os.environ["CWB_STORAGE_ROOT"] = str(_TMP)
os.environ["CWB_ARTIFACT_DIR"] = str(_TMP / "artifacts")
os.environ["CWB_TMP_DIR"] = str(_TMP / "tmp")
os.environ["CWB_DATABASE_URL"] = f"sqlite:///{_TMP / 'p1.db'}"
os.environ["CWB_SECRET_STORE_PATH"] = str(_TMP / "secrets.json")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.models.entities import Base, ContentItem, enable_sqlite_fk  # noqa: E402
from app.services.pipeline import PipelineService  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  → ' + str(detail)) if detail else ''}")


s = get_settings()
TEST_DB = s.storage_root / "test_p1.db"
for f in (TEST_DB,):
    if f.exists():
        f.unlink()
# 这里现在是**临时目录内**的 artifacts/C001，删的是本测自己的产物，
# 不再碰生产数据。
shutil.rmtree(s.artifact_dir / "C001", ignore_errors=True)

eng = create_engine(f"sqlite:///{TEST_DB}", future=True)
enable_sqlite_fk(eng)
Base.metadata.create_all(eng)
SF = sessionmaker(bind=eng, future=True)
svc = PipelineService(SF)

print("\n[1] seed 导入与版本建立")
r = svc.import_seed(s.examples_dir / "C001" / "seeds" / "C001" / "seed.json")
check("导入成功", r["display_id"] == "C001")
check("seed 校验通过", "通过" in r["seed_validation"], r["seed_validation"])
check("母稿版本 v1", r["revision_version"] == 1)
check("两平台各自独立 revision", set(r["platforms"]) == {"douyin", "xiaohongshu"})
check("两平台 profile verified 均为 false",
      all(not v["profile_upload_verified"] for v in r["platforms"].values()))

r2 = svc.import_seed(s.examples_dir / "C001" / "seeds" / "C001" / "seed.json")
check("重复导入同一 seed 不新建 revision（幂等）", r2["revision_version"] == 1, f"v{r2['revision_version']}")

print("\n[2] 渲染：真实 PNG 产出")
dy = svc.render(r["platforms"]["douyin"]["platform_revision_id"])
xhs = svc.render(r["platforms"]["xiaohongshu"]["platform_revision_id"])
check("抖音渲染成功", dy["ok"] is True)
check("小红书渲染成功", xhs["ok"] is True)
check("抖音 5 页", len(dy["images"]) == 5, len(dy["images"]))
check("小红书 6 页", len(xhs["images"]) == 6, len(xhs["images"]))
check("抖音状态 ready_for_review", dy["state"] == "ready_for_review")

for plat, res in (("douyin", dy), ("xiaohongshu", xhs)):
    for img in res["images"]:
        p = s.artifact_dir / plat / ".." / img["storage_key"].split("/", 1)[-1]
    root = s.artifact_dir
    ok = all((root / img["storage_key"]).exists() for img in res["images"])
    check(f"{plat} 全部图片文件真实存在", ok)

check("产物 storage_key 使用跨平台正斜杠",
      all("\\" not in img["storage_key"] for img in dy["images"]),
      [img["storage_key"] for img in dy["images"]])

check("抖音尺寸 1080×1440（工程默认排版值）", (dy["images"][0]["width"], dy["images"][0]["height"]) == (1080, 1440))
check("小红书尺寸 1080×1440", (xhs["images"][0]["width"], xhs["images"][0]["height"]) == (1080, 1440))

# 尺寸取自 profile 版本，而不是写死在测试里；换规格后测试仍应跟着走
from app.services.profile_store import ProfileStore  # noqa: E402
_prof = ProfileStore(s.storage_root / "profiles.json").latest("douyin")
check("抖音尺寸与 profile 版本一致",
      (dy["images"][0]["width"], dy["images"][0]["height"])
      == (_prof.render.width_px, _prof.render.height_px),
      f"profile={_prof.render.width_px}×{_prof.render.height_px}")

# 图片尺寸与文件真伪复核
from PIL import Image  # noqa: E402
im = Image.open(s.artifact_dir / dy["images"][0]["storage_key"])
check("PNG 可解码且尺寸与元数据一致", im.size == (dy["images"][0]["width"], dy["images"][0]["height"]), im.size)

check("两次渲染同内容产出同 manifest_hash（可复现）",
      svc.render(r["platforms"]["douyin"]["platform_revision_id"])["manifest_hash"] == dy["manifest_hash"])

print("\n[3] 不变量：未批准不得产出发布包")
try:
    svc.build_package(r["platforms"]["douyin"]["platform_revision_id"], "coisini")
    check("未批准时导出应被拒绝", False, "却成功了")
except Exception as exc:
    check("未批准时导出被拒绝", "未批准" in str(exc) or "StateConflict" in type(exc).__name__, str(exc)[:60])

print("\n[4] 不变量：不接受伪造 actor")
try:
    svc.decide(r["platforms"]["douyin"]["platform_revision_id"], "approve", "system")
    check("actor=system 应被拒绝", False, "却成功了")
except Exception as exc:
    check("actor=system 被拒绝", "可信会话" in str(exc), str(exc)[:50])

print("\n[5] 审核与聚合状态")
d1 = svc.decide(r["platforms"]["douyin"]["platform_revision_id"], "approve", "rosso",
                expected_manifest_hash=dy["manifest_hash"])
check("抖音批准成功", d1["platform_state"] == "approved")
check("仅一平台批准时主状态为 partially_approved", d1["content_state"] == "partially_approved", d1["content_state"])

d2 = svc.decide(r["platforms"]["xiaohongshu"]["platform_revision_id"], "approve", "rosso",
                expected_manifest_hash=xhs["manifest_hash"])
check("两平台均批准后主状态为 approved", d2["content_state"] == "approved", d2["content_state"])

print("\n[6] 不变量：过期批准不能覆盖新内容")
# 用错误的 manifest_hash 模拟"批准时看到的图"与"当前图"不一致
try:
    svc.decide(r["platforms"]["xiaohongshu"]["platform_revision_id"], "approve", "rosso",
               expected_manifest_hash="deadbeef" * 8)
    check("manifest 不匹配应返回冲突", False, "却成功了")
except Exception as exc:
    check("过期批准被拒绝", "过期批准" in str(exc), str(exc)[:60])

# 真实变更场景：内容改动产生新 revision 后，旧批准不能覆盖新版本
from app.models.entities import PlatformRevision  # noqa: E402
with SF() as sess:
    pr = sess.get(PlatformRevision, r["platforms"]["xiaohongshu"]["platform_revision_id"])
    old_hash = pr.manifest_hash
    pr.manifest_hash = "0" * 64   # 模拟图片集合已变更
    pr.state = "ready_for_review"
    sess.commit()
try:
    svc.decide(r["platforms"]["xiaohongshu"]["platform_revision_id"], "approve", "rosso",
               expected_manifest_hash=old_hash)
    check("内容变更后旧批准应失效", False, "却成功了")
except Exception as exc:
    check("内容变更后旧批准失效", "过期批准" in str(exc), str(exc)[:60])
with SF() as sess:
    pr = sess.get(PlatformRevision, r["platforms"]["xiaohongshu"]["platform_revision_id"])
    pr.manifest_hash = old_hash
    pr.state = "approved"
    sess.commit()

print("\n[7] 发布包导出")
pkg = svc.build_package(r["platforms"]["douyin"]["platform_revision_id"], "coisini")
check("ZIP 生成成功", Path(pkg["zip_path"]).exists())
check("manifest 记录批准信息", pkg["manifest"]["approval"]["decision"] == "approve")
check("manifest 标 profile_upload_verified=false", pkg["manifest"]["profile_upload_verified"] is False)
check("标 manual_publish_required", pkg["manual_publish_required"] is True)
check("integration_status=pending", pkg["integration_status"] == "integration_pending")

with zipfile.ZipFile(pkg["zip_path"]) as z:
    names = z.namelist()
    check("含 5 张图片", len([n for n in names if n.startswith("images/")]) == 5, names)
    check("含 caption.txt", "caption.txt" in names)
    check("含 manifest.json", "manifest.json" in names)
    check("含核对清单", "checklist.md" in names)
    check("不含密钥/私密源文档", not any(k in " ".join(names).lower() for k in ("secret", "apikey")))
    mf = json.loads(z.read("manifest.json"))
    check("manifest 文件哈希完整", len(mf["files"]) == 5)
    # 图片必须真实可解码，且与 manifest 记录的哈希一致
    import hashlib as _h
    real = [n for n in names if n.startswith("images/")]
    manifest_names_exist = all(f["name"] in names for f in mf["files"])
    check("manifest 图片路径使用 ZIP 标准分隔符并匹配实际文件",
          manifest_names_exist, [f["name"] for f in mf["files"]])
    ok_hash = manifest_names_exist and all(
        _h.sha256(z.read(f["name"])).hexdigest() == f["sha256"]
        for f in mf["files"]
    )
    check("ZIP 内图片哈希与 manifest 一致（可校验）", ok_hash)

print("\n[8] 不变量：单平台失败不把整体标完成")
r3 = svc.import_seed(s.examples_dir / "C001" / "seeds" / "C001" / "seed.json")
svc.decide(r3["platforms"]["douyin"]["platform_revision_id"], "approve", "rosso")
st = svc.decide(r3["platforms"]["xiaohongshu"]["platform_revision_id"], "hold", "rosso")
check("一平台暂缓后主状态非 approved", st["content_state"] != "approved", st["content_state"])

print(f"\n{'='*54}")
print(f"结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
if FAIL:
    for f in FAIL:
        print("  -", f)
print(f"临时目录：{_TMP}")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
