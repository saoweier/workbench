"""Exact editor preparation/readback. No publish button is clicked here."""
from datetime import datetime, timezone
import re

from ..core.config import get_settings
from .douyin_publishing import DouyinPublishingService
from .douyin_browser import observe
from .platform_account import write_json


def dismiss_onboarding(page):
    if "Hi，我是发文助手" in page.locator("body").inner_text():
        done = page.get_by_role("button", name="完成", exact=True)
        if done.count() == 1 and done.is_visible():
            done.click(timeout=5000)


def normalize(text):
    # The platform renders blank paragraphs with zero-width markers and a
    # single visual newline. Preserve every paragraph and all other characters.
    return re.sub(r"\n+", "\n", text.replace("\u200b", "").replace("\r\n", "\n")).strip()


def fill_current(page, root, task_id):
    service = DouyinPublishingService(get_settings())
    task = service.execution(task_id)
    if task["state"] != "awaiting_editor" or not page.url.startswith("https://creator.douyin.com/creator-micro/content/post/image"):
        raise ValueError("当前不是待填写的图文编辑页")
    dismiss_onboarding(page)
    title = page.get_by_placeholder("添加作品标题", exact=True)
    editor = page.locator('[contenteditable="true"]')
    if editor.count() != 1 or len(task["title"]) > 20 or len(task["caption"]) > 1000:
        raise ValueError("编辑控件或图文长度不符合当前平台要求")
    title.fill(task["title"])
    editor.fill(task["caption"])
    page.wait_for_timeout(500)
    actual_title, actual_caption = title.input_value(), editor.inner_text()
    write_json(root / "tasks" / task_id / "readback.json", {"title":actual_title,"caption":actual_caption,
        "expected_title_length":len(task["title"]),"expected_caption_length":len(task["caption"])})
    observe(page, root)
    if title.input_value() != task["title"] or normalize(editor.inner_text()) != normalize(task["caption"]):
        raise ValueError("文案回读与原稿不一致")
    text = page.locator("body").inner_text()
    if not re.search(rf"已添加\s*{len(task['images'])}\s*张图片", text):
        raise ValueError("平台图片数量与准备稿不一致")
    observe(page, root)
    page.screenshot(path=str(root / "tasks" / task_id / "editor.png"), full_page=True)
    service.finish(task_id, "awaiting_editor", result={"text_filled_at":datetime.now(timezone.utc).isoformat(),
        "message":f"{len(task['images'])} 张图片与文案已填写并回读一致，正在核对 AI 内容声明。"})


def validate_editor(page, task):
    if not page.url.startswith("https://creator.douyin.com/creator-micro/content/post/image"):
        raise ValueError("当前不是图文编辑页")
    title = page.get_by_placeholder("添加作品标题", exact=True)
    editor = page.locator('[contenteditable="true"]')
    if title.count()!=1 or editor.count()!=1 or title.input_value()!=task["title"] or normalize(editor.inner_text())!=normalize(task["caption"]):
        raise ValueError("当前编辑页与已确认图文不一致")
    text = page.locator("body").inner_text()
    if not re.search(rf"已添加\s*{len(task['images'])}\s*张图片", text):
        raise ValueError("当前图片数量不一致")
    return text


def complete_prepare(page, root, task_id):
    service = DouyinPublishingService(get_settings())
    task = service.execution(task_id)
    if task["state"] != "awaiting_editor":
        raise ValueError("当前不是待检查的编辑稿")
    text = validate_editor(page, task)
    def stage(name):
        write_json(root/'tasks'/task_id/'editor-stage.json',{'stage':name})
    if "请选择自主声明" in text:
        if "请选择声明类型（单选）" not in text:
            stage('open_declaration')
            page.get_by_text("请选择自主声明", exact=True).click(timeout=5000)
        stage('choose_ai')
        page.get_by_label("内容由AI生成", exact=True).check(timeout=8000)
        stage('confirm_ai')
        page.get_by_role("button", name="确定", exact=True).click(timeout=5000)
    stage('verify_declaration')
    text = validate_editor(page, task)
    if "内容由AI生成" not in text or "请选择自主声明" in text:
        raise ValueError("尚未确认平台 AI 内容声明")
    observe(page, root)
    page.screenshot(path=str(root / "tasks" / task_id / "editor.png"), full_page=True)
    service.finish(task_id, "awaiting_confirmation", result={
        "ai_declaration":"内容由AI生成", "editor_verified_at":datetime.now(timezone.utc).isoformat(),
        "message":f"{len(task['images'])} 张图片、{len(task['title'])} 字标题、完整正文已核对，已标注内容由 AI 生成。正式发布仍需你确认。"})
