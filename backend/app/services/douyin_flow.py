"""Browser execution of persistent tasks. Every publish requires user confirmation."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import re

from ..core.config import get_settings
from .douyin_publishing import DouyinPublishingService
from .douyin_browser import extract_identity, observe
from .platform_account import read_json,write_json

_service = globals().get("_service")


def get_service(root, read_only=False):
    global _service
    settings = get_settings()
    if root.resolve() != (settings.storage_root / "platform_accounts/douyin").resolve():
        return None  # An empty-profile smoke check cannot access live publishing tasks.
    if _service is None:
        _service = DouyinPublishingService(settings)
        if not read_only:
            _service.recover()
    return _service


def verify_account(page, root, expected):
    page.goto("https://creator.douyin.com/creator-micro/home", wait_until="domcontentloaded", timeout=30000)
    page.get_by_text(re.compile(r"^抖音号[：:]")).wait_for(timeout=15000)
    identity = extract_identity(page.locator("body").inner_text())
    if not identity or identity["account_id"] != expected:
        raise ValueError("当前抖音账号与发布任务不一致")
    observe(page, root)
    return identity


def upload(page, root, service, task):
    verify_account(page, root, task["account_id"])
    page.goto("https://creator.douyin.com/creator-micro/content/upload?default-tab=3",
              wait_until="domcontentloaded", timeout=30000)
    field = page.locator('input[type="file"][accept*="image"]')
    field.wait_for(timeout=15000)
    files = []
    total = 0
    artifact_root = service.settings.artifact_dir.resolve()
    for item in task["images"]:
        path = (artifact_root / item["storage_key"]).resolve()
        if not path.is_relative_to(artifact_root):
            raise ValueError("图片路径不安全")
        content = path.read_bytes()
        total += len(content)
        if total > 100 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError("图片已变化或文件总量过大")
        files.append({"name":f"page-{item['page_index']:02d}.png", "mimeType":"image/png", "buffer":content})
    field.set_input_files(files, timeout=30000)
    # Observe actual editor controls before filling; no guessed publish click.
    page.wait_for_function("count => new RegExp('已添加\\\\s*'+count+'\\\\s*张图片').test(document.body.innerText)",arg=len(files),timeout=90000)
    observe(page, root)
    evidence = root / "tasks" / task["task_id"]
    evidence.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(evidence / "upload.png"), full_page=True)
    service.finish(task["task_id"], "awaiting_editor", result={
        "uploaded_pages":len(files), "uploaded_at":datetime.now(timezone.utc).isoformat(),
        "message":"图片已上传，需要确认实际编辑控件并完成文案填写。"})
    from .douyin_editor import fill_current, complete_prepare
    fill_current(page,root,task["task_id"])
    complete_prepare(page,root,task["task_id"])


def poll_tasks(page, root, read_only=False):
    service = get_service(root,read_only=read_only)
    if service is None:
        return
    claimed = service.claim(read_only=read_only)
    if not claimed:
        return False
    action, task_id = claimed
    try:
        task = service.execution(task_id, historical=action=="observe")
        if action == "upload":
            upload(page, root, service, task)
        elif action == "publish":
            from .douyin_submit import publish
            publish(page,root,service,task)
        else:
            from .douyin_observer import observe_task
            # A revisit must never navigate away from an unfinished editor.
            observer = page.context.new_page()
            try:
                observe_task(observer,root,service,task)
            finally:
                observer.close()
    except Exception as failure:
        import re
        write_json(root/'tasks'/task_id/'operation-error.json',{'error_type':type(failure).__name__,
            'brief':re.sub(r'https?://\S+','[URL]',str(failure).splitlines()[0][:200]),'action':action})
        service.finish(task_id, "unknown" if action == "publish" else ("observation_failed" if action=="observe" else "upload_failed"),
            error="发布结果需要核实，未自动重发。" if action == "publish" else "操作未完成，请检查连接窗口；账号不符、网络异常或平台验证时会停止。",
            next_seconds=300 if action=="publish" else None)
    return True
