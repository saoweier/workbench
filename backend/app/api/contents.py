"""内容与流水线接口（T05/T06/T07）。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body
from fastapi.responses import FileResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..core.config import get_settings
from ..core.errors import NotFound, ValidationFailed
from ..models.entities import Artifact, Batch, ContentItem, ContentRevision, PlatformRevision, ReviewDecision, Run, enable_sqlite_fk
from ..models.entities import Base
from ..services.pipeline import PipelineService

router = APIRouter(tags=["contents"])
_settings = get_settings()

_engine = create_engine(_settings.database_url, future=True)
enable_sqlite_fk(_engine)
Base.metadata.create_all(_engine)
SessionFactory = sessionmaker(bind=_engine, future=True)

_svc = PipelineService(SessionFactory)


def _artifact_public(a: Artifact) -> dict:
    return {
        "id": a.id,
        "page_index": a.page_index,
        "kind": a.kind,
        "url": f"/api/v1/artifacts/{a.id}/raw",
        "sha256": a.sha256,
        "size_bytes": a.size_bytes,
        "width": a.width,
        "height": a.height,
        "template_version": a.template_version,
    }


@router.post("/contents/import-seed", status_code=201)
async def import_seed(payload: dict = Body(...)) -> dict:
    """导入 seed 建立版本。默认用 examples/C001 的 seed。"""
    seed_path = payload.get("seed_path") or str(
        _settings.examples_dir / "C001" / "seeds" / "C001" / "seed.json"
    )
    return _svc.import_seed(seed_path, profile_version_id=payload.get("profile_version_id"))


@router.get("/contents")
async def list_contents(include_discarded: bool = False) -> dict:
    with SessionFactory() as s:
        q = s.query(ContentItem)
        if not include_discarded:
            # 丢弃是终态、默认不再出现；传 include_discarded=1 仍可查看，记录没被删。
            q = q.filter(ContentItem.state != "discarded")
        items = q.all()
        return {
            "items": [
                {
                    "id": c.id,
                    "display_id": c.display_id,
                    "topic": c.topic,
                    "state": c.state,
                    "selected_by": c.selected_by,
                    "run_mode": c.run_mode,
                    "active_revision_id": c.active_revision_id,
                }
                for c in items
            ]
        }


@router.post("/contents/{content_id}/discard")
async def discard_content(content_id: str, payload: dict = Body(default={})) -> dict:
    """丢弃一条内容（终态 discarded），释放待预览库存名额。

    与"删除"的区别：只改状态、保留审核与调用记录，可追溯；名字用 discard
    而不是 delete，就是为了不假装数据消失了。
    """
    return _svc.discard(content_id, actor=payload.get("actor"), note=payload.get("note"))


@router.get("/contents/{content_id}")
async def get_content(content_id: str) -> dict:
    with SessionFactory() as s:
        c = s.get(ContentItem, content_id)
        if c is None:
            raise NotFound(f"内容不存在：{content_id}")
        revs = (
            s.query(ContentRevision)
            .filter_by(content_id=c.id)
            .order_by(ContentRevision.version.desc())
            .all()
        )
        out = []
        for r in revs:
            prs = (
                s.query(PlatformRevision)
                .filter_by(content_revision_id=r.id)
                .order_by(PlatformRevision.platform)
                .all()
            )
            out.append({
                "revision_id": r.id,
                "version": r.version,
                "input_hash": r.input_hash,
                "created_at": r.created_at.isoformat(),
                "limitations": (r.limitations_json or {}).get("limitations", []),
                "brief": r.brief_json or {},
                "platforms": [
                    {
                        "platform": pr.platform,
                        "platform_revision_id": pr.id,
                        "version": pr.version,
                        "state": pr.state,
                        "title": pr.title,
                        "caption": pr.caption,
                        "page_count": len(pr.pages_json["pages"]),
                        "pages": pr.pages_json["pages"],
                        "manifest_hash": pr.manifest_hash,
                        "profile_version_id": pr.profile_version_id,
                        "artifacts": [_artifact_public(a) for a in
                                      sorted(pr.artifacts, key=lambda x: x.page_index)],
                        "reviews": [
                            {
                                "decision": d.decision, "actor": d.actor,
                                "manifest_hash": d.manifest_hash,
                                "decided_at": d.decided_at.isoformat(), "note": d.note,
                            }
                            for d in sorted(pr.reviews, key=lambda x: x.decided_at)
                        ],
                    }
                    for pr in prs
                ],
            })
        return {
            "id": c.id,
            "display_id": c.display_id,
            "topic": c.topic,
            "state": c.state,
            "run_mode": c.run_mode,
            "active_revision_id": c.active_revision_id,
            "revisions": out,
        }


@router.post("/platform-revisions/{pr_id}/render")
async def render(pr_id: str) -> dict:
    return _svc.render(pr_id)


@router.post("/review-decisions")
async def review_decision(payload: dict = Body(...)) -> dict:
    """决策组。任一目标已变更则整组冲突。"""
    decision = payload.get("decision")
    actor = payload.get("actor", "coisini")
    note = payload.get("note")
    targets = payload.get("targets") or []
    if not targets:
        targets = [{"platform_revision_id": payload.get("platform_revision_id"),
                    "expected_manifest_hash": payload.get("expected_manifest_hash")}]
    results, conflicts = [], []
    with SessionFactory() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        for t in targets:
            try:
                results.append(_svc.decide(
                    t["platform_revision_id"], decision, actor,
                    expected_manifest_hash=t.get("expected_manifest_hash"), note=note,
                    session=session,
                ))
            except Exception as exc:
                conflicts.append({"platform_revision_id":t.get("platform_revision_id"),
                    "error":type(exc).__name__,"message":str(exc),"details":getattr(exc,"details",{})})
        if conflicts:
            session.rollback()
            return {"ok":False,"conflicts":conflicts,"note":"存在冲突，整组未生效，请刷新预览后重试","applied":[]}
        session.commit()
        return {"ok":True,"applied":results,"conflicts":[]}


@router.post("/packages", status_code=201)
async def create_package(payload: dict = Body(...)) -> dict:
    """生成发布包。**必须有批准记录在前**（不变量 1）。

    兼容两种入参写法：`platform_revision_ids`（列表，契约主推）与
    `platform_revision_id`（单数，历史写法）。两个都空时返回 422，
    而不是让 KeyError 变成 500 —— 参数写错是调用方的问题，不是服务端故障。
    """
    ids = payload.get("platform_revision_ids")
    if not ids:
        single = payload.get("platform_revision_id")
        ids = [single] if single else []
    if not ids:
        raise ValidationFailed(
            "必须提供 platform_revision_id 或 platform_revision_ids（至少一个）"
        )
    actor = payload.get("actor") or "coisini"
    results = [_svc.build_package(pr_id, actor) for pr_id in ids]
    if len(results) == 1:
        return results[0]
    return {"ok": True, "packages": results, "count": len(results)}


@router.get("/contents/{content_id}/deliverables")
async def content_deliverables(content_id: str, platform: str | None = None,
                               revision_id: str | None = None,
                               platform_revision_id: str | None = None) -> dict:
    """本次任务的交付物清单：来源快照、筛选记录、逐页文案、视觉提示、成品图与专项验收。"""
    from ..services.deliverables import build
    return build(SessionFactory, content_id, platform=platform, revision_id=revision_id,
                 platform_revision_id=platform_revision_id)


@router.get("/packages/{pr_id}/download")
async def download_package(pr_id: str) -> FileResponse:
    with SessionFactory() as s:
        pr = s.get(PlatformRevision, pr_id)
        if pr is None:
            raise NotFound(f"平台版本不存在：{pr_id}")
        c = s.get(ContentItem, pr.content_revision.content_id)
    zip_path = _settings.artifact_dir / "packages" / f"{c.display_id}-{pr.platform}-pr{pr.version}.zip"
    if not zip_path.exists():
        raise NotFound("发布包尚未生成")
    return FileResponse(zip_path, media_type="application/zip", filename=zip_path.name)


@router.get("/artifacts/{artifact_id}/raw")
async def artifact_raw(artifact_id: str) -> FileResponse:
    with SessionFactory() as s:
        a = s.get(Artifact, artifact_id)
        if a is None:
            raise NotFound("产物不存在")
    # storage_key 已是相对 artifact_root 的完整路径
    p = _settings.artifact_dir / a.storage_key
    if not p.exists():
        raise NotFound(f"文件缺失：{a.storage_key}")
    return FileResponse(p, media_type="image/png", filename=p.name)


@router.get("/platform-revisions/{pr_id}/pages/{index}")
async def page_image(pr_id: str, index: int) -> FileResponse:
    """按平台稿 + 页号取图。

    前端预览用这个而不是 artifact_id：页面只知道"第几页"，
    让界面去查 artifact 表是把内部结构漏给了调用方。
    """
    with SessionFactory() as s:
        pr = s.get(PlatformRevision, pr_id)
        if pr is None:
            raise NotFound(f"平台版本不存在：{pr_id}")
        art = s.query(Artifact).filter(
            Artifact.platform_revision_id == pr_id,
            Artifact.page_index == index,
        ).order_by(Artifact.created_at.desc()).first()
        if art is None:
            # 退一步：按 storage_key 约定拼路径，兼容早期未写 page_index 的记录
            c = s.get(ContentItem, pr.content_revision.content_id)
            guess = (f"{c.display_id}/{pr.platform}/cr{pr.content_revision.version}"
                     f"-pr{pr.version}/page-{index:02d}.png")
            p = _settings.artifact_dir / guess
            if p.exists():
                return FileResponse(p, media_type="image/png", filename=p.name)
            raise NotFound(f"第 {index} 页图不存在（该稿可能尚未渲染）")
        p = _settings.artifact_dir / art.storage_key
        if not p.exists():
            raise NotFound(f"文件缺失：{art.storage_key}")
    return FileResponse(p, media_type="image/png", filename=p.name)


@router.get("/runs")
async def list_runs(limit: int = 30) -> dict:
    with SessionFactory() as s:
        runs = s.query(Run).order_by(Run.created_at.desc()).limit(limit).all()
        return {"items": [
            {"id": r.id, "stage": r.stage, "state": r.state, "mode": r.mode,
             "attempt": r.attempt, "fencing_token": r.fencing_token,
             "input_hash": (r.input_hash or "")[:16], "error": r.error,
             "content_id": r.content_id,
             "jobs": [{"stage":j.stage,"state":j.state,"error":j.error} for j in r.jobs],
             "created_at": r.created_at.isoformat()}
            for r in runs
        ]}


@router.get("/review-decisions")
async def list_decisions() -> dict:
    with SessionFactory() as s:
        rows = s.query(ReviewDecision).order_by(ReviewDecision.decided_at.desc()).all()
        return {"items": [
            {"id": d.id, "platform_revision_id": d.platform_revision_id,
             "decision": d.decision, "actor": d.actor,
             "manifest_hash": (d.manifest_hash or "")[:16],
             "decided_at": d.decided_at.isoformat(), "note": d.note}
            for d in rows
        ]}
