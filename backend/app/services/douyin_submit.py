"""Exactly one click for an explicitly confirmed, unchanged editor."""
from datetime import datetime, timezone

from .douyin_editor import validate_editor
from .douyin_flow import verify_account
from .douyin_browser import observe

def validate_settings(page):
    for label in ['公开','立即发布']:
        control=page.get_by_label(label,exact=True)
        if control.count()!=1 or not control.is_checked():
            raise ValueError('当前可见范围或发布时间与本轮公开立即发布确认不一致')


def publish(page, root, service, task):
    service.require_publish_approval(task["task_id"])
    text=validate_editor(page,task)
    validate_settings(page)
    if "内容由AI生成" not in text or "请选择自主声明" in text or "对作品内容添加声明" in text:
        raise ValueError("AI 内容声明尚未完成")
    # Check the actual account in a separate tab, preserving this exact draft.
    check=page.context.new_page()
    try:
        verify_account(check,root,task["account_id"])
    finally:
        check.close()
    validate_editor(page,task)
    validate_settings(page)
    evidence=root/"tasks"/task["task_id"]
    page.screenshot(path=str(evidence/"before-submit.png"),full_page=True)
    service.finish(task["task_id"],"submitting",result={"submission_attempted_at":datetime.now(timezone.utc).isoformat()})
    # This is the only place in the codebase that clicks the platform publish button.
    page.get_by_role("button",name="发布",exact=True).click(timeout=8000)
    page.wait_for_timeout(5000)
    observe(page,root)
    page.screenshot(path=str(evidence/"after-submit.png"),full_page=True)
    service.finish(task["task_id"],"verifying",result={"message":"已点击发布，正在依据平台作品列表核实；尚未凭点击结果登记成功。"},next_seconds=10)
