"""Persistent publishing plan and explicit user confirmation, bound to exact files."""
from __future__ import annotations
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from ..core.errors import NotFound, StateConflict, ValidationFailed
from ..models.entities import (Base, ContentItem, DouyinPublishTask, Event, PlatformRevision,
                               Publication, ReviewDecision, _now, enable_sqlite_fk)
from .platform_account import PlatformAccountService, read_json
from .pipeline import PipelineService


def digest(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class DouyinPublishingService:
    def __init__(self, settings):
        self.settings = settings
        self.engine = create_engine(settings.database_url, future=True)
        enable_sqlite_fk(self.engine)
        Base.metadata.create_all(self.engine)
        self.sf = sessionmaker(bind=self.engine, future=True)
        self.accounts = PlatformAccountService(settings.storage_root)
        self.root = self.accounts.root

    def _current_payload(self, s, pr_id):
        pr = s.get(PlatformRevision, pr_id)
        if not pr or pr.platform != "douyin":
            raise NotFound("请选择抖音图文版本")
        content = s.get(ContentItem, pr.content_revision.content_id)
        if content.active_revision_id != pr.content_revision_id:
            raise StateConflict("这不是当前内容版本，请重新选择")
        if content.run_mode != "real":
            raise StateConflict("真实发布仅接受真实生成的内容，演示样本不会上传")
        images = sorted([a for a in pr.artifacts if a.kind == "page_image"], key=lambda a:a.page_index)
        if not pr.manifest_hash or not 1 <= len(images) <= 35:
            raise StateConflict("需要完整渲染的图文，且图片数量为 1–35 张")
        payload = {"content_id":content.id, "display_id":content.display_id,
                   "title":pr.title, "caption":pr.caption,
                   "images":[{"artifact_id":a.id, "page_index":a.page_index, "sha256":a.sha256,
                               "storage_key":a.storage_key} for a in images]}
        return pr, content, payload

    def _checked(self, s, task):
        pr, content, current = self._current_payload(s, task.platform_revision_id)
        if digest(current) != task.payload_hash or pr.manifest_hash != task.manifest_hash:
            raise StateConflict("图文已变化，这份发布准备已失效，请重新准备")
        root = self.settings.artifact_dir.resolve()
        for image in current["images"]:
            path = (root / image["storage_key"]).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise StateConflict("图片缺失或路径不安全，禁止上传")
            if path.stat().st_size > 50 * 1024 * 1024 or hashlib.sha256(path.read_bytes()).hexdigest() != image["sha256"]:
                raise StateConflict("图片内容与审核版本不一致，禁止上传")
        return pr, content, current

    def plan(self, pr_id):
        account = read_json(self.root / "identity.json")
        account_id = account.get("account_id")
        if not account_id:
            raise StateConflict("尚未识别账号身份，请打开抖音后台首页检查连接")
        with self.sf() as s:
            s.connection().exec_driver_sql("BEGIN IMMEDIATE")
            pr, content, payload = self._current_payload(s, pr_id)
            hashed = digest(payload)
            existing = s.scalar(select(DouyinPublishTask).where(DouyinPublishTask.account_id == account_id,
                DouyinPublishTask.platform_revision_id == pr.id, DouyinPublishTask.payload_hash == hashed))
            if existing:
                if existing.state == 'cancelled':
                    existing.state, existing.error, existing.result_json = 'planned', None, {}
                    existing.confirmed_at, existing.confirmed_by = None, None
                    existing.updated_at = _now()
                    s.commit()
                return self._public(existing)
            task = DouyinPublishTask(id=str(uuid4()), platform_revision_id=pr.id, account_id=account_id,
                manifest_hash=pr.manifest_hash, payload_hash=hashed, payload_json=payload)
            s.add(task)
            s.flush()
            self._checked(s, task)
            s.add(Event(entity_type="douyin_publish_task", entity_id=task.id, type="publish_plan_created",
                        actor="user", run_mode="real", payload={"account_id":account_id,"manifest_hash":pr.manifest_hash}))
            s.commit()
            return self._public(task)

    def _task(self, s, task_id):
        task = s.get(DouyinPublishTask, task_id)
        if not task:
            raise NotFound("发布任务不存在")
        return task

    def list(self):
        with self.sf() as s:
            return {"items":[self._public(t) for t in s.scalars(select(DouyinPublishTask).order_by(DouyinPublishTask.created_at.desc()))]}

    def get(self, task_id):
        with self.sf() as s:
            return self._public(self._task(s, task_id))

    def upload(self, task_id, expected_hash):
        from .publishing_preferences import preferences
        if preferences(self.settings)['mode']!='assisted':
            raise StateConflict('当前选择手动发布。请先到发布与回访入口选择浏览器辅助。')
        if not self.accounts.status()["verified_now"]:
            raise StateConflict("请先连接抖音账号并进入后台首页")
        with self.sf() as s:
            s.connection().exec_driver_sql("BEGIN IMMEDIATE")
            task = self._task(s, task_id)
            if task.payload_hash != expected_hash:
                raise StateConflict("发布准备已经变化，请刷新后重试")
            if task.state in {"upload_requested", "uploading", "awaiting_editor", "awaiting_confirmation", "publish_requested", "submitting", "verifying", "succeeded"}:
                return self._public(task)
            if task.state not in {"planned", "upload_failed"}:
                raise StateConflict("当前任务不能再次上传，请先核实状态")
            self._checked(s, task)
            if s.scalar(select(DouyinPublishTask).where(DouyinPublishTask.id != task.id,
                DouyinPublishTask.state.in_(["upload_requested", "uploading", "awaiting_editor", "awaiting_confirmation", "publish_requested", "submitting", "verifying"]))):
                raise StateConflict("请先完成或取消当前发布准备，一次只准备一条内容")
            task.state, task.error, task.updated_at = "upload_requested", None, _now()
            s.commit()
            return self._public(task)

    def confirm(self, task_id, expected_hash, account_id, confirmed):
        from .publishing_preferences import preferences
        if preferences(self.settings)['mode']!='assisted':
            raise StateConflict('当前选择手动发布，浏览器不会代为提交。')
        if confirmed is not True:
            raise ValidationFailed("请明确确认将这一版图文发布到所选账号")
        with self.sf() as s:
            s.connection().exec_driver_sql("BEGIN IMMEDIATE")
            task = self._task(s, task_id)
            if expected_hash != task.payload_hash or account_id != task.account_id:
                raise StateConflict("账号或图文版本不一致，请重新检查")
            if task.state in {"publish_requested", "submitting", "verifying", "succeeded", "unknown"}:
                return self._public(task)  # An uncertain submission is NEVER resubmitted.
            if task.state != "awaiting_confirmation":
                raise StateConflict("先完成图文上传和发布前检查")
            pr, content, _ = self._checked(s, task)
            PipelineService(self.sf).decide(pr.id, "approve", "coisini", task.manifest_hash,
                "用户在发布任务中确认这一版图文及账号", session=s)
            task.state, task.confirmed_at, task.confirmed_by = "publish_requested", _now(), "coisini"
            task.updated_at = _now()
            s.add(Event(entity_type="douyin_publish_task", entity_id=task.id, type="publish_confirmed",
                        actor="user", run_mode="real", payload={"account_id":account_id,"payload_hash":expected_hash}))
            s.commit()
            return self._public(task)

    def request_observation(self, task_id):
        with self.sf() as s:
            task = self._task(s, task_id)
            if task.state not in {"succeeded", "unknown", "awaiting_review", "observation_failed"}:
                raise StateConflict("尚未实际提交发布，不能获取作品回访")
            task.next_observation_at, task.updated_at = _now(), _now()
            task.result_json={**(task.result_json or {}),'manual_revisit_requested':True}
            s.commit()
            return self._public(task)

    def cancel(self, task_id):
        with self.sf() as s:
            s.connection().exec_driver_sql("BEGIN IMMEDIATE")
            task = self._task(s, task_id)
            if task.state not in {"planned", "upload_failed", "awaiting_confirmation", "upload_requested", "publish_requested"}:
                raise StateConflict("已经提交或正在执行，不能取消；请核实实际平台状态")
            task.state, task.updated_at = "cancelled", _now()
            task.next_observation_at = None
            s.commit()
            return self._public(task)

    def claim(self, read_only=False):
        from .publishing_preferences import preferences
        prefs=preferences(self.settings)
        with self.sf() as s:
            s.connection().exec_driver_sql("BEGIN IMMEDIATE")
            task = None if read_only or prefs['mode']!='assisted' else s.scalar(select(DouyinPublishTask).where(DouyinPublishTask.state.in_(["upload_requested", "publish_requested"]))
                            .order_by(DouyinPublishTask.created_at))
            if task:
                action = "upload" if task.state == "upload_requested" else "publish"
                try:
                    self._checked(s, task)
                except (StateConflict, NotFound) as exc:
                    task.state, task.error, task.updated_at = "stale", str(exc), _now()
                    s.commit()
                    return None
                task.state = "uploading" if action == "upload" else "submitting"
                task.updated_at = _now()
                s.commit()
                return action, task.id
            task = next((t for t in s.scalars(select(DouyinPublishTask).where(DouyinPublishTask.next_observation_at<=_now()).order_by(DouyinPublishTask.next_observation_at))
                if prefs['background_revisit'] or (t.result_json or {}).get('manual_revisit_requested')),None)
            if task:
                task.next_observation_at = _now()+timedelta(seconds=300)
                task.result_json = {**(task.result_json or {}), "observation_started_at":_now().isoformat()}
                task.result_json['manual_revisit_requested']=False
                task.updated_at = _now()
                s.commit()
                return "observe", task.id
        return None

    def finish(self, task_id, state, result=None, error=None, next_seconds=None):
        with self.sf() as s:
            task = self._task(s, task_id)
            task.state, task.error, task.updated_at = state, error, _now()
            if result is not None:
                task.result_json = {**(task.result_json or {}), **result}
            if next_seconds:
                task.next_observation_at = _now() + timedelta(seconds=next_seconds)
            s.commit()

    def execution(self, task_id, historical=False):
        with self.sf() as s:
            task = self._task(s, task_id)
            if not historical:
                self._checked(s, task)
            payload = dict(task.payload_json)
            payload.update(task_id=task.id, account_id=task.account_id, payload_hash=task.payload_hash,
                           manifest_hash=task.manifest_hash, state=task.state, confirmed_by=task.confirmed_by,
                           result=task.result_json or {})
            return payload

    def require_publish_approval(self, task_id):
        from .publishing_preferences import preferences
        if preferences(self.settings)['mode']!='assisted':
            raise StateConflict('已切换为手动发布，本次浏览器提交停止。')
        with self.sf() as s:
            task = self._task(s, task_id)
            pr, _, _ = self._checked(s, task)
            if task.state != "submitting":
                raise StateConflict("当前任务不处于待提交状态，禁止再次发布")
            approval = s.scalar(select(ReviewDecision).where(ReviewDecision.platform_revision_id==pr.id)
                                .order_by(ReviewDecision.decided_at.desc()))
            if not task.confirmed_at or task.confirmed_by != "coisini" or not approval or approval.decision!="approve" or approval.manifest_hash!=task.manifest_hash:
                raise StateConflict("缺少当前版本的明确人工发布确认")

    def record_observed_publication(self, task_id, evidence):
        """Private worker path: platform identifier + exact title are mandatory."""
        import re
        post_id = str(evidence.get("post_id", ""))
        if not re.fullmatch(r"\d{15,25}", post_id):
            raise ValidationFailed("平台未返回可核验的作品编号")
        with self.sf() as s:
            s.connection().exec_driver_sql("BEGIN IMMEDIATE")
            task = self._task(s, task_id)
            if not task.confirmed_at or evidence.get("title")!=task.payload_json["title"] or evidence.get("account_id")!=task.account_id:
                raise StateConflict("作品证据与已确认任务不一致")
            if task.publication_id:
                pub = s.get(Publication,task.publication_id)
                if pub.platform_post_id!=post_id:
                    raise StateConflict("平台作品编号与已有登记不一致")
                return pub.id
            existing = s.scalar(select(Publication).where(Publication.platform=="douyin", Publication.account_id==task.account_id,
                                                          Publication.platform_post_id==post_id))
            if existing:
                if existing.platform_revision_id!=task.platform_revision_id:
                    raise StateConflict("同一作品编号已关联其他图文版本")
                task.publication_id=existing.id
                s.commit()
                return existing.id
            pub = Publication(id=str(uuid4()),account_id=task.account_id,platform="douyin",
                platform_revision_id=task.platform_revision_id, platform_post_id=post_id, link=evidence.get("link"),
                published_manifest_hash=task.manifest_hash,published_content_hash=task.payload_hash,
                declaration_source="browser_observation", registered_by="coisini",run_mode="real",status="declared",
                verified_state="confirmed",verified_at=_now(),verified_by="browser",
                verified_note="已从当前账号官方作品列表读取编号并核对标题；审核状态单独记录。",
                note="用户确认具体图文后由工作台浏览器执行，依据实际平台页面登记。")
            s.add(pub)
            s.flush()
            task.publication_id=pub.id
            task.updated_at=_now()
            s.add(Event(entity_type="publication",entity_id=pub.id,type="browser_publication_observed",actor="browser",
                run_mode="real",payload={"task_id":task.id,"post_id":post_id,"source":"creator.douyin.com","audit_state":evidence.get("audit_state")}))
            s.commit()
            return pub.id

    def recover(self):
        with self.sf() as s:
            for task in s.scalars(select(DouyinPublishTask).where(DouyinPublishTask.state.in_(["uploading", "awaiting_editor", "awaiting_confirmation", "submitting", "verifying"]))):
                task.state = "unknown" if task.confirmed_at else "upload_failed"
                if task.confirmed_at:
                    task.next_observation_at = _now()+timedelta(seconds=10)
                task.error = "连接中断。提交结果需先核实，系统不会自动再次发布。" if task.confirmed_at else "上传中断，可以重新准备。"
                task.updated_at = _now()
            s.commit()

    @staticmethod
    def _public(task):
        p = task.payload_json
        return {"id":task.id, "platform_revision_id":task.platform_revision_id, "account_id":task.account_id,
                "payload_hash":task.payload_hash, "manifest_hash":task.manifest_hash,
                "display_id":p["display_id"], "content_id":p["content_id"], "title":p["title"], "caption":p["caption"],
                "images":[{"page_index":i["page_index"],"url":f"/api/v1/artifacts/{i['artifact_id']}/raw"} for i in p["images"]],
                "state":task.state, "error":task.error, "result":task.result_json or {}, "publication_id":task.publication_id,
                "confirmed_at":task.confirmed_at.isoformat() if task.confirmed_at else None,
                "next_observation_at":task.next_observation_at.isoformat() if task.next_observation_at else None}
