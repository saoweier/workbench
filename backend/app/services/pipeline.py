"""P1 流水线：seed → revision → 渲染 → 审核 → 发布包（T05/T06/T07）。

不变量落实：
- 无批准记录不得产出 ready-to-publish 包（第 1 条）
- 过期批准不能覆盖新图/新文案 → 绑定 manifest_hash，不匹配即 409
- 重试不重复追加同一阶段产物（第 3 条，unique(run_id, stage, input_hash)）
- 模型无法直接更改批准状态（第 6 条，actor 只来自可信会话）
"""
from __future__ import annotations

import hashlib
from contextlib import nullcontext
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from ..core.config import get_settings
from ..core.errors import NotFound, StateConflict, ValidationFailed
from ..models.entities import (
    Artifact,
    Batch,
    ContentItem,
    ContentRevision,
    Event,
    Job,
    PlatformRevision,
    Publication,
    ReviewDecision,
    Run,
)
from ..services.profile_store import ProfileStore
from ..services.renderer import PlaywrightRenderer, verify_images
from ..services.seed_validator import validate_seed
from .content_lifecycle import update_content_state

TRUSTED_ACTORS = {"rosso", "coisini"}


def _portable_basename(storage_key: str) -> str:
    """Return the final path component for keys written on any OS."""
    return storage_key.replace("\\", "/").rsplit("/", 1)[-1]


def _in_event_loop() -> bool:
    """当前线程是否正跑在 asyncio 事件循环里（决定 Playwright 能不能直接调）。"""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class PipelineService:
    def __init__(self, session_factory, actor: str = "coisini") -> None:
        self.sf = session_factory
        self.actor = actor
        self.settings = get_settings()
        self.profiles = ProfileStore()
        self.renderer = PlaywrightRenderer(self.settings.artifact_dir)

    # ------------------------------------------------------------ 导入 seed

    def import_seed(self, seed_path: str | Path, *, profile_version_id: str | None = None) -> dict:
        """导入 seed → 建 content + content_revision + 两个 platform_revision。幂等。"""
        report = validate_seed(seed_path)
        if not report.passed:
            raise ValidationFailed(
                f"seed 校验未通过：{report.summary()}",
                details={"findings": [f.model_dump() for f in report.errors]},
            )

        data = json.loads(Path(seed_path).read_text(encoding="utf-8"))
        display_id = data["display_id"]
        seed_hash = report.seed_sha256

        with self.sf() as s:
            content = s.query(ContentItem).filter_by(display_id=display_id).one_or_none()
            if content is None:
                batch = s.query(Batch).first()
                if batch is None:
                    batch = Batch(item_limit=1, cost_mode=self.settings.cost_mode_default)
                    s.add(batch)
                    s.flush()
                content = ContentItem(
                    batch_id=batch.id,
                    display_id=display_id,
                    topic=data["topic"],
                    selected_by="user",
                    selection_reason="用户已确认选题 A（见 topics.md）",
                    state="drafting",
                    run_mode=data.get("run_mode", "local_seed"),
                )
                s.add(content)
                s.flush()

            # 同一 seed 内容重复导入不新建 revision（不重复追加产物）
            existing = (
                s.query(ContentRevision)
                .filter_by(content_id=content.id, input_hash=seed_hash)
                .one_or_none()
            )
            if existing:
                rev = existing
            else:
                last = (
                    s.query(ContentRevision)
                    .filter_by(content_id=content.id)
                    .order_by(ContentRevision.version.desc())
                    .first()
                )
                rev = ContentRevision(
                    content_id=content.id,
                    version=(last.version + 1) if last else 1,
                    parent_id=last.id if last else None,
                    brief_json=data.get("brief"),
                    claims_json={"claims": data.get("claims", []), "sources": data.get("sources", [])},
                    limitations_json={"limitations": data.get("limitations", [])},
                    input_hash=seed_hash,
                    seed_sha256=seed_hash,
                )
                s.add(rev)
                s.flush()

            # 两平台独立 revision
            platform_summary = {}
            for pd in data["platform_drafts"]:
                pf = (
                    self.profiles.get(profile_version_id)
                    if profile_version_id
                    else self.profiles.latest(pd["platform"])
                )
                content_hash = hashlib.sha256(
                    json.dumps(pd, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()
                pr = (
                    s.query(PlatformRevision)
                    .filter_by(content_revision_id=rev.id, platform=pd["platform"], content_hash=content_hash)
                    .one_or_none()
                )
                if pr is None:
                    last_pr = (
                        s.query(PlatformRevision)
                        .filter_by(content_revision_id=rev.id, platform=pd["platform"])
                        .order_by(PlatformRevision.version.desc())
                        .first()
                    )
                    pr = PlatformRevision(
                        content_revision_id=rev.id,
                        platform=pd["platform"],
                        version=(last_pr.version + 1) if last_pr else 1,
                        title=pd["title"],
                        caption=pd["caption"],
                        pages_json={"pages": pd["pages"]},
                        profile_version_id=pf.id,
                        platform_profile_version=pf.name,
                        content_hash=content_hash,
                        state="ready_to_render",
                    )
                    s.add(pr)
                    s.flush()
                platform_summary[pd["platform"]] = {
                    "platform_revision_id": pr.id,
                    "version": pr.version,
                    "page_count": len(pd["pages"]),
                    "profile_version_id": pf.id,
                    "profile_upload_verified": pf.platform_upload_verified,
                    "state": pr.state,
                }

            content.active_revision_id = rev.id
            s.add(Event(
                entity_type="content_item", entity_id=content.id, type="seed_imported",
                actor=self.actor, run_mode=content.run_mode,
                payload={"seed_sha256": seed_hash, "revision_version": rev.version},
            ))
            s.commit()

            return {
                "content_id": content.id,
                "display_id": display_id,
                "content_revision_id": rev.id,
                "revision_version": rev.version,
                "input_hash": seed_hash,
                "run_mode": content.run_mode,
                "platforms": platform_summary,
                "seed_validation": report.summary(),
            }

    # ------------------------------------------------------------ 渲染

    def render(self, platform_revision_id: str, *, isolated: bool | None = None) -> dict:
        """渲染平台版本。

        `isolated`：是否把 Playwright 放到独立线程执行。
        - None（默认）→ 自动判断：当前线程有 asyncio 事件循环时用独立线程，
          纯脚本环境直接渲染。这样路由层不用关心这个细节，CLI 也不多绕一层。
        """
        if isolated is None:
            isolated = _in_event_loop()
        with self.sf() as s:
            pr = s.get(PlatformRevision, platform_revision_id)
            if pr is None:
                raise NotFound(f"平台版本不存在：{platform_revision_id}")
            pf = self.profiles.get(pr.profile_version_id) or self.profiles.latest(pr.platform)
            content = s.get(ContentItem, pr.content_revision.content_id)
            pages = pr.pages_json["pages"]

            run = Run(
                content_id=content.id,
                stage=f"render:{pr.platform}",
                mode=content.run_mode,
                state="running",
                input_hash=pr.content_hash,
                attempt=1,
                lease_owner=self.actor,
                fencing_token=1,
            )
            s.add(run)
            s.flush()

            draft = {"platform": pr.platform, "title": pr.title, "caption": pr.caption, "pages": pages,
                     "form": (pr.pages_json or {}).get("form", "")}
            render_fn = (self.renderer.render_platform_isolated if isolated
                         else self.renderer.render_platform)
            result = render_fn(
                draft, pf,
                display_id=content.display_id,
                content_revision_version=pr.content_revision.version,
                platform_revision_version=pr.version,
            )

            if result.errors:
                run.state = "failed"
                run.error = "; ".join(f"{i.code}: {i.message}" for i in result.errors)
                s.add(Event(
                    entity_type="platform_revision", entity_id=pr.id, type="render_blocked",
                    actor=self.actor, run_mode=content.run_mode,
                    payload={"issues": [i.__dict__ for i in result.issues]},
                ))
                s.commit()
                return {
                    "ok": False,
                    "run_id": run.id,
                    "stage": run.stage,
                    "blocked": True,
                    "issues": [i.__dict__ for i in result.issues],
                    "note": "存在阻断项，未产出图片（避免产出有缺陷的成品）",
                }

            # 导出数量必须与页数一致：程序的专项验收契约，而不是靠肉眼看图。
            extra = verify_images(result, self.settings.artifact_dir, expected_pages=len(pages))

            # 写入 artifact（先清同 revision 旧记录，避免重复追加）
            s.query(Artifact).filter_by(platform_revision_id=pr.id).delete()
            for img in result.images:
                s.add(Artifact(
                    platform_revision_id=pr.id,
                    kind="page_image",
                    page_index=img["page_index"],
                    # storage_key 是相对于 artifact_root 的完整相对路径，
                    # 必须保留 crX-prY 目录段，否则导出时定位不到文件
                    storage_key=img["storage_key"],
                    sha256=img["sha256"],
                    size_bytes=img["size_bytes"],
                    width=img["width"],
                    height=img["height"],
                    template_version=img["template_version"],
                ))

            pr.manifest_hash = result.manifest_hash()
            pr.state = "ready_for_review" if not extra else "checking"
            self._recompute_content_state(s, content)
            run.state = "succeeded"
            run.output_refs = {"artifact_count": len(result.images), "manifest_hash": pr.manifest_hash}
            run.finished_at = datetime.now(timezone.utc)

            s.add(Event(
                entity_type="platform_revision", entity_id=pr.id, type="rendered",
                actor=self.actor, run_mode=content.run_mode,
                payload={"manifest_hash": pr.manifest_hash, "pages": len(result.images),
                         "warnings": [i.__dict__ for i in result.issues]},
            ))
            s.commit()

            return {
                "ok": True,
                "run_id": run.id,
                "stage": run.stage,
                "platform": pr.platform,
                "platform_revision_id": pr.id,
                "manifest_hash": pr.manifest_hash,
                "state": pr.state,
                "images": result.images,
                "warnings": [i.__dict__ for i in result.issues],
                "verify_issues": [i.__dict__ for i in extra],
            }

    # ------------------------------------------------------------ 审核

    def decide(self, platform_revision_id: str, decision: str, actor: str,
               expected_manifest_hash: str | None = None, note: str | None = None, *, session=None) -> dict:
        """通过 / 修改 / 暂缓。通过必须匹配当前 manifest_hash，否则 409。"""
        if actor not in TRUSTED_ACTORS:
            raise ValidationFailed(f"不接受 actor={actor}；人工批准只来自可信会话")
        if decision not in {"approve", "request_change", "hold"}:
            raise ValidationFailed(f"未知决定：{decision}")

        with (nullcontext(session) if session is not None else self.sf()) as s:
            pr = s.get(PlatformRevision, platform_revision_id)
            if pr is None:
                raise NotFound(f"平台版本不存在：{platform_revision_id}")
            if not pr.artifacts:
                raise StateConflict("尚未渲染，无法审核（没有可批准的图）")
            # approved/exported 可再次决定（撤回批准）；但 manifest 必须匹配
            if pr.state in {"changes_requested", "on_hold"} and decision == "approve":
                pass  # 允许重新批准
            elif pr.state not in {"ready_for_review", "checking", "approved", "exported"}:
                raise StateConflict(f"当前状态 {pr.state} 不允许审核")

            if decision == "approve":
                content = s.get(ContentItem, pr.content_revision.content_id)
                if content.run_mode == 'real':
                    from .evidence_gate import require_evidence
                    require_evidence(content.topic, (pr.content_revision.claims_json or {}).get('sources', []))
                if expected_manifest_hash and expected_manifest_hash != pr.manifest_hash:
                    raise StateConflict(
                        "过期批准：manifest_hash 不匹配，图片或文案已变更",
                        details={"expected": expected_manifest_hash, "actual": pr.manifest_hash},
                    )

            rd = ReviewDecision(
                platform_revision_id=pr.id,
                manifest_hash=pr.manifest_hash,
                decision=decision,
                actor=actor,
                note=note,
            )
            s.add(rd)
            if decision == "approve":
                pr.state = "approved"
            elif decision == "request_change":
                pr.state = "changes_requested"
            else:
                pr.state = "on_hold"

            content = s.get(ContentItem, pr.content_revision.content_id)
            self._recompute_content_state(s, content)

            s.add(Event(
                entity_type="platform_revision", entity_id=pr.id, type=f"review_{decision}",
                actor=actor, run_mode=content.run_mode,
                payload={"manifest_hash": pr.manifest_hash, "note": note},
            ))
            if session is None:
                s.commit()
            else:
                s.flush()
            return {
                "platform_revision_id": pr.id,
                "decision": decision,
                "actor": actor,
                "manifest_hash": pr.manifest_hash,
                "platform_state": pr.state,
                "content_state": content.state,
            }

    def _recompute_content_state(self, s, content: ContentItem) -> None:
        """主状态由两个平台变体聚合；单平台失败不能把整体标完成。"""
        states = {pr.platform: pr.state for pr in
                  s.query(PlatformRevision).filter_by(content_revision_id=content.active_revision_id)}
        if states and all(v == "approved" for v in states.values()):
            state = "approved"
        elif any(v == "changes_requested" for v in states.values()):
            state = "changes_requested"
        elif any(v == "approved" for v in states.values()):
            state = "partially_approved"
        elif states and all(v in {"ready_for_review", "checking"} for v in states.values()):
            state = "ready_for_review"
        else:
            state = "drafting"
        update_content_state(s, content, state)
        content.platform_states = states  # type: ignore[attr-defined]

    # ------------------------------------------------------------ 丢弃

    def discard(self, content_id: str, actor: str | None = None,
                note: str | None = None) -> dict:
        """丢弃一条内容：置终态 `discarded`，释放待预览库存名额。

        设计取舍：**只改状态、不删数据**。审核记录、模型调用、事件都留着，
        便于追溯"这条为什么没了"；同时 `discarded` 不在 `PENDING_STATES` 里，
        所以库存名额会被释放——这正是"处理已有内容"该有的效果。

        任何内容状态都可以移出工作区，包括已有发布登记的内容。
        发布登记及其引用保留，清理本地内容不改变平台作品。
        """
        actor = actor or self.actor
        if actor not in TRUSTED_ACTORS:
            raise ValidationFailed(f"不接受 actor={actor}；丢弃只来自可信会话")
        with self.sf() as s:
            c = s.get(ContentItem, content_id)
            if c is None:
                raise NotFound(f"内容不存在：{content_id}")
            if c.state == "discarded":
                return {"content_id": c.id, "display_id": c.display_id,
                        "state": c.state, "released": False, "already": True, "note": "已经是丢弃状态"}

            # 停掉还在排队 / 运行 / 暂停的任务，避免丢弃后任务回来又写状态
            for run in s.query(Run).filter_by(content_id=c.id).all():
                for job in s.query(Job).filter_by(run_id=run.id).all():
                    if job.state in {"queued", "running", "paused"}:
                        job.state = "cancelled"
                if run.state in {"queued", "running", "paused"}:
                    run.state = "cancelled"

            c.state = "discarded"
            s.add(Event(entity_type="content", entity_id=c.id, type="content_discarded",
                        actor=actor, run_mode=c.run_mode,
                        payload={"display_id": c.display_id, "note": note,
                                 "reason": "人工丢弃；仅改状态、保留全部记录"}))
            s.commit()
            return {"content_id": c.id, "display_id": c.display_id,
                    "state": c.state, "released": True, "already": False,
                    "note": "已丢弃；待预览库存名额已释放"}

    # ------------------------------------------------------------ 发布包

    def build_package(self, platform_revision_id: str, actor: str) -> dict:
        """为已批准 revision 生成 ZIP。未批准 → 409（不变量第 1 条）。"""
        if actor not in TRUSTED_ACTORS:
            raise ValidationFailed(f"不接受 actor={actor}")

        with self.sf() as s:
            pr = s.get(PlatformRevision, platform_revision_id)
            if pr is None:
                raise NotFound(f"平台版本不存在：{platform_revision_id}")
            if pr.state != "approved":
                raise StateConflict(
                    f"当前状态 {pr.state}，未批准不得产出发布包（不变量第 1 条）"
                )
            latest = (
                s.query(ReviewDecision)
                .filter_by(platform_revision_id=pr.id, decision="approve")
                .order_by(ReviewDecision.decided_at.desc())
                .first()
            )
            if latest is None or latest.manifest_hash != pr.manifest_hash:
                raise StateConflict("批准记录与当前 manifest 不匹配，批准已过期")

            content = s.get(ContentItem, pr.content_revision.content_id)
            arts = s.query(Artifact).filter_by(platform_revision_id=pr.id).order_by(Artifact.page_index).all()

            pkg_dir = self.settings.artifact_dir / "packages"
            pkg_dir.mkdir(parents=True, exist_ok=True)
            zip_name = f"{content.display_id}-{pr.platform}-pr{pr.version}.zip"
            zip_path = pkg_dir / zip_name

            manifest = {
                "schema_version": "1",
                "content_id": content.id,
                "display_id": content.display_id,
                "platform": pr.platform,
                "content_revision_version": pr.content_revision.version,
                "platform_revision_id": pr.id,
                "platform_revision_version": pr.version,
                "platform_profile_version": pr.platform_profile_version,
                "profile_upload_verified": False,
                "manifest_hash": pr.manifest_hash,
                "approval": {
                    "decision": latest.decision,
                    "actor": latest.actor,
                    "decided_at": latest.decided_at.isoformat(),
                    "note": latest.note,
                },
                "template_versions": sorted({a.template_version for a in arts}),
                "files": [
                    {
                        "name": f"images/{_portable_basename(a.storage_key)}",
                        "page_index": a.page_index,
                        "sha256": a.sha256,
                        "size_bytes": a.size_bytes,
                        "width": a.width,
                        "height": a.height,
                    }
                    for a in arts
                ],
                "run_mode": content.run_mode,
                "notice": "本包为本地工程产物；平台上传兼容性尚未核对，手动发布由用户完成",
            }

            # 交付物清单随包冻结：导出的是"这一版到底产出了什么"，而不是只丢一堆图。
            from .deliverables import build as build_deliverables
            deliverables = build_deliverables(self.sf, content.id, platform_revision_id=pr.id)
            manifest["deliverables"] = deliverables["deliverables"]
            manifest["acceptance"] = deliverables["acceptance"]
            manifest["source_snapshot"] = [
                {"id": x["id"], "kind": x["kind"], "locator": x["locator"],
                 "access_state": x["access_state"], "url": x["url"]}
                for x in deliverables["sources"]
            ]
            manifest["screening"] = {
                "selection_reason": content.selection_reason,
                "claim_ids": [c["id"] for c in deliverables["claims"]],
            }

            base = f"{content.display_id}/{pr.platform}/{pr.content_revision.version}"
            missing_files: list[str] = []
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
                for a in arts:
                    src = self.settings.artifact_dir / a.storage_key
                    if not src.exists():
                        missing_files.append(a.storage_key)
                        continue
                    z.write(src, f"images/{src.name}")
                # 图片缺失属于硬错误：不允许产出"看起来成功"的残缺发布包
                if missing_files:
                    raise StateConflict(
                        f"发布包缺少 {len(missing_files)} 张图片，已中止导出：{missing_files[:3]}"
                    )
                z.writestr("caption.txt", f"{pr.title}\n\n{pr.caption}\n")
                z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                deliverable_lines = "\n".join(
                    "| %s | %s | %s | %s |" % (r["label"], r["expected"], r["actual"],
                                               "通过" if r["passed"] else "**未通过**")
                    for r in deliverables["deliverables"])
                z.writestr(
                    "checklist.md",
                    "# 发布前核对清单\n\n"
                    "## 交付物清单（程序核对）\n\n"
                    "| 交付物 | 应有 | 实际 | 结论 |\n|---|---|---|---|\n"
                    + deliverable_lines + "\n\n"
                    + ("**存在未通过对勾项，先解决再发布。**\n\n" if not deliverables["acceptance"]["passed"] else "")
                    + "## 人工确认\n\n"
                    "1. 图片尺寸/数量与平台要求一致（**平台上传兼容性本次未核对，需人工确认**）\n"
                    "2. 文字未超平台限制（标题/正文长度见 manifest）\n"
                    "3. 内容与批准版本一致（manifest_hash: %s）\n"
                    "4. 手动上传后回填发布记录（本应用不自动发布）\n" % pr.manifest_hash[:16],
                )

            pr.state = "exported"
            s.add(Event(
                entity_type="platform_revision", entity_id=pr.id, type="package_built",
                actor=actor, run_mode=content.run_mode,
                payload={"zip": zip_name, "manifest_hash": pr.manifest_hash},
            ))
            s.commit()

            return {
                "zip_path": str(zip_path),
                "zip_name": zip_name,
                "size_bytes": zip_path.stat().st_size,
                "manifest": manifest,
                "integration_status": "integration_pending",
                "manual_publish_required": True,
            }
