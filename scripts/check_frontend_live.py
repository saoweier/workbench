"""Read-only visual check of the locally running UI; never submits production.

Uses a fresh browser context so the creator's saved drafts are not changed.
"""
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.renderer import PlaywrightRenderer

proof = ROOT / "docs/frontend-verification"
proof.mkdir(exist_ok=True)
base = "http://127.0.0.1:8000"
with sync_playwright() as runtime:
    browser = runtime.chromium.launch(headless=True, executable_path=PlaywrightRenderer.resolve_executable())
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/views/Production.html")
    expect(page.get_by_role("button", name="自定义选题", exact=True)).to_be_enabled()
    expect(page.get_by_role("button", name="排行榜选题", exact=True)).to_be_visible()
    expect(page.get_by_role("textbox", name="你的选题")).to_be_hidden()
    page.screenshot(path=str(proof / "live-production-desktop.png"), full_page=True, animations="disabled")
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(proof / "live-production-mobile.png"), full_page=True, animations="disabled")
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.get_by_role("button", name="排行榜选题", exact=True).click()
    source_filters = page.get_by_role("group", name="榜单来源", exact=True)
    expect(source_filters).to_be_visible()
    expect(page.locator("#creator-board select")).to_have_count(0)
    expect(page.locator('[data-board-source][aria-pressed="true"]')).to_have_count(1)
    if source_filters.get_by_role("button").count() > 1:
        source_name = source_filters.get_by_role("button").nth(1).get_attribute("aria-label")
        source_filters.get_by_role("button").nth(1).click()
        expect(source_filters.get_by_role("button", name=source_name, exact=True)).to_have_attribute("aria-pressed", "true")
        expect(page.get_by_role("region", name=source_name + "排行榜", exact=True)).to_be_visible()
    expect(page.locator(".board-source")).to_have_count(1)
    assert page.locator(".board-item").count() <= 6
    assert page.locator("#board-columns,.board-source").evaluate_all("els=>els.every(el=>getComputedStyle(el).overflowY==='visible')")
    page.screenshot(path=str(proof / "live-rankings-desktop.png"), full_page=True, animations="disabled")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(proof / "live-rankings-mobile.png"), full_page=True, animations="disabled")
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.get_by_role("tab", name="我的内容", exact=False).click()
    expect(page.locator("#refresh-note")).to_contain_text("平台稿按需读取")
    boxes = page.locator('[data-select-content]')
    chosen = min(2, boxes.count())
    for index in range(chosen):
        boxes.nth(index).check()
    expect(page.locator("#library-selection-count")).to_have_text(f"已选 {chosen} 条")
    page.locator("h1").scroll_into_view_if_needed()
    page.screenshot(path=str(proof / "live-library-bulk-desktop.png"), full_page=True, animations="disabled")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(proof / "live-library-bulk-mobile.png"), full_page=True, animations="disabled")
    if chosen:
        page.get_by_role("button", name="清空选择", exact=True).click()
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(base + "/")
    expect(page.get_by_text("本地工作区已连接", exact=True)).to_be_visible()
    expect(page.locator("#content-gallery")).to_have_attribute("aria-busy", "false")
    if page.locator("#content-gallery .content-card").count():
        expect(page.locator("#content-gallery .content-card").first).to_be_visible()
    else:
        expect(page.get_by_role("heading", name="第一篇内容，还在你的脑海里。", exact=True)).to_be_visible()
    for card in page.locator(".content-card").all():
        card.scroll_into_view_if_needed()
        expect(card).not_to_contain_text("封面将在进入视野时载入")
    page.locator("h1").scroll_into_view_if_needed()
    page.screenshot(path=str(proof / "live-home-desktop.png"), full_page=True, animations="disabled")
    assert not errors, errors
    browser.close()
print("Live UI verified; no model calls or business writes.")
