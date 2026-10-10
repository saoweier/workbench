"""Creator UX regression against a disposable HTTP fixture, never the business API.

Run directly with the project Python. No model, platform, or business DB access.
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import json
import re
import sys
import threading
import time

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.renderer import PlaywrightRenderer

sys.stdout.reconfigure(encoding="utf-8")
passed = []


def check(name, condition):
    assert condition, name
    passed.append(name)
    print("PASS " + name, flush=True)


class Fixture(SimpleHTTPRequestHandler):
    counts = {}
    bodies = []
    failure = None
    state = "queued"
    options_delay = 0
    discard_requests = []
    failed_discards = set()
    drop_next_submission = False
    discard_delay = .15
    contents = [{"id": f"content-{i}", "display_id": f"T{i:03}",
                 "topic": f"内容选题 {i}", "state": "ready_for_review", "run_mode": "fixture"}
                for i in range(1, 31)]

    def log_message(self, *_):
        pass

    def reply(self, body, status=200):
        encoded = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    @classmethod
    def run(cls):
        return {"id": "run-1", "state": cls.state, "stage": "produce", "mode": "fixture",
                "created_at": "2026-10-05T08:00:00+08:00", "error": "请补充来源" if cls.state == "failed" else None,
                "jobs": [{"stage": stage, "state": "succeeded" if cls.state == "succeeded" else
                          "running" if stage == "research" else "queued"}
                         for stage in ["audit", "compose", "render", "research", "topic", "planning", "media"]]}

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/test-cover.svg":
            image = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="520"><rect width="400" height="520" fill="#f7f3eb"/><rect x="30" y="35" width="340" height="40" fill="#d7e4f8"/><rect x="30" y="105" width="260" height="18" fill="#48668b"/><rect x="30" y="165" width="340" height="125" fill="#e4eadc"/><rect x="30" y="320" width="340" height="125" fill="#eadfcb"/></svg>'.encode()
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(image)))
            self.end_headers()
            self.wfile.write(image)
            return
        if path == "/":
            self.path = "/views/index.html"
            return super().do_GET()
        if not path.startswith("/api/v1/"):
            type(self).counts[path] = type(self).counts.get(path, 0) + 1
            return super().do_GET()
        path = path.removeprefix("/api/v1")
        cls = type(self)
        cls.counts[path] = cls.counts.get(path, 0) + 1
        if path == cls.failure:
            return self.reply({"error": {"message": "测试连接中断", "code": "UNAVAILABLE"}}, 503)
        if path == "/studio/options":
            time.sleep(cls.options_delay)
            data = {"text_ready": True, "model": "本地测试模型", "default_mode": "fixture",
                    "directions": [{"id": "auto", "name": "自动识别"}, {"id": "tech", "name": "科技与 AI"}],
                    "templates": [{"id": "auto", "name": "自动匹配", "description": "依据内容选择"},
                                  {"id": "rank_cards", "name": "彩色排行卡片", "description": "名次清晰"},
                                  {"id": "illustrated", "name": "图解手册", "description": "关系与流程"}]}
        elif path == "/health":
            data = {"api": {"version": "test"}, "worker": {"status": "running"}, "cost_mode": "usage_tracking"}
        elif path == "/studio/trends":
            data = {"categories": {"hot": "全部热点", "tech": "科技榜"}, "base_url": "https://example.test",
                    "message": "本地榜单测试", "refreshing": False, "progress": {"success": 1, "total": 1},
                    "sources": [{"name": "测试榜单", "platform": "fixture", "source_category": "科技",
                                 "state": "ready", "note": "隔离测试数据", "items": [
                                     {"id": f"trend-{i}", "rank": i, "title": f"榜单中的选题 {i}"}
                                     for i in range(1, 31)]},
                                {"name": "另一来源", "platform": "fixture-b", "source_category": "科技",
                                 "state": "ready", "note": "隔离测试数据", "items": [
                                     {"id": f"source-b-{i}", "rank": i, "title": f"另一来源的题目 {i}"}
                                     for i in range(1, 8)]},
                                {"name": "暂不可用来源", "platform": "fixture-empty", "source_category": "科技",
                                 "state": "unavailable", "note": "暂无快照", "items": []}]}
        elif path == "/content-skills":
            data = {"items": [], "capabilities": {"text_ready": True, "image_ready": False,
                    "search_ready": False, "notice": "隔离测试"}}
        elif path == "/contents":
            data = {"items": cls.contents}
        elif path.startswith("/contents/"):
            cid = path.rsplit("/", 1)[-1]
            data = {"id": cid, "active_revision_id": "rev-1", "revisions": [{"revision_id": "rev-1",
                    "platforms": [{"platform": "douyin", "platform_revision_id": "pr-" + cid,
                                   "state": "ready_for_review", "page_count": 2,
                                   "artifacts": [{"kind": "page_image", "url": "/test-cover.svg"}]}]}]}
        elif path == "/runs":
            data = {"items": [cls.run()] if cls.bodies else []}
        elif path == "/runs/run-1":
            data = cls.run()
        elif path == "/seeds":
            data = {"seeds": [{"path": "test.json", "display_id": "T001"}]}
        elif path == "/publications":
            data = {"items": [], "total": 0}
        else:
            data = {"items": []}
        self.reply(data)

    def do_POST(self):
        path = urlparse(self.path).path.removeprefix("/api/v1")
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if path == "/studio/produce":
            type(self).bodies.append(body)
            if type(self).drop_next_submission:
                type(self).drop_next_submission = False
                # Simulate an accepted job whose HTTP response was lost.
                self.close_connection = True
                self.connection.close()
                return
            time.sleep(.3)
            self.reply({"run_id": "run-1", "content_id": "content-1", "state": "queued"})
        elif path == "/runs/run-1/control":
            type(self).state = {"pause": "paused", "resume": "running", "retry": "succeeded", "cancel": "cancelled"}[body["action"]]
            self.reply({"ok": True})
        elif path.startswith("/contents/") and path.endswith("/discard"):
            cls = type(self)
            cid = path.split("/")[2]
            cls.discard_requests.append(cid)
            time.sleep(cls.discard_delay)
            if cid in cls.failed_discards:
                self.reply({"error": {"message": "测试连接中断，请稍后重试", "code": "UNAVAILABLE"}}, 503)
            else:
                cls.contents = [c for c in cls.contents if c["id"] != cid]
                self.reply({"id": cid, "state": "discarded"})
        else:
            self.reply({"ok": True})


server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Fixture, directory=str(ROOT / "frontend/src")))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}"
screens = ROOT / "docs/frontend-verification"
screens.mkdir(exist_ok=True)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=PlaywrightRenderer.resolve_executable())
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = ctx.new_page()
        page.set_default_timeout(10000)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        Fixture.options_delay = .6
        page.goto(base + "/views/Production.html", wait_until="domcontentloaded")
        expect(page.get_by_text("读取创作设置", exact=False)).to_be_visible()
        expect(page.get_by_role("button", name="自定义选题", exact=True)).to_be_enabled()
        Fixture.options_delay = 0
        check("创作首屏不读取内容清单、任务记录、高级素材或榜单", all(Fixture.counts.get(x, 0) == 0 for x in ["/contents", "/runs", "/seeds", "/studio/trends"]))
        check("选题从排行榜和自定义两个入口开始", page.get_by_role("button", name="排行榜选题", exact=True).is_visible() and page.get_by_role("button", name="自定义选题", exact=True).is_visible() and not page.get_by_role("textbox", name="你的选题").is_visible())
        page.get_by_role("button", name="排行榜选题", exact=True).click()
        expect(page.locator(".board-item")).to_have_count(6)
        check("排行榜按需读取且只显示一个来源的六条", Fixture.counts.get("/studio/trends") == 1 and page.locator(".board-source").count() == 1)
        check("来源直接显示筛选按钮且只有一个选中", page.locator("#creator-board select").count() == 0 and page.locator('[data-board-source][aria-pressed="true"]').count() == 1 and page.get_by_role("group", name="榜单来源", exact=True).get_by_role("button").count() == 3)
        check("热点服务设置保持隐藏", page.get_by_text("热点服务设置", exact=True).is_hidden())
        page.reload()
        expect(page.locator(".board-item")).to_have_count(6)
        check("尚未点选时刷新回到排行榜", page.locator("#creator-board").is_visible() and page.locator("#creator-topic").is_hidden())
        page.get_by_role("button", name="下一页榜单").click()
        expect(page.locator(".board-item").first).to_contain_text("榜单中的选题 7")
        check("翻页替换当前六条而不追加长列表", page.locator(".board-item").count() == 6 and "榜单中的选题 12" in page.locator(".board-source").inner_text())
        page.reload()
        expect(page.get_by_role("navigation", name="榜单翻页")).to_contain_text("2 / 5 页")
        check("刷新恢复正在浏览的榜单页码", "榜单中的选题 7" in page.locator(".board-item").first.inner_text())
        board_reads = Fixture.counts.get("/studio/trends")
        page.get_by_role("group", name="榜单来源", exact=True).get_by_role("button", name="另一来源", exact=True).click()
        expect(page.locator(".board-item")).to_have_count(6)
        check("切换来源只呈现所选榜单且不重新请求", Fixture.counts.get("/studio/trends") == board_reads and "另一来源的题目 1" in page.locator(".board-source").inner_text())
        page.get_by_role("button", name="下一页榜单").click()
        expect(page.locator(".board-item")).to_have_count(1)
        check("最后一页保留余下条目并禁止越界", page.get_by_role("button", name="下一页榜单").is_disabled() and "另一来源的题目 7" in page.locator(".board-item").inner_text())
        page.reload()
        expect(page.locator(".board-item")).to_have_count(1)
        check("刷新恢复所选来源的高亮和页码", page.get_by_role("group", name="榜单来源", exact=True).get_by_role("button", name="另一来源", exact=True).get_attribute("aria-pressed") == "true" and "另一来源的题目 7" in page.locator(".board-item").inner_text())
        page.get_by_role("button", name="上一页榜单").click()
        expect(page.locator(".board-item")).to_have_count(6)
        page.get_by_role("group", name="榜单来源", exact=True).get_by_role("button", name="暂不可用来源", exact=True).click()
        expect(page.locator(".board-source")).to_contain_text("这个来源暂时没有可读条目")
        check("空来源仍可切换且不产生空白长列表", page.locator(".board-item").count() == 0 and page.get_by_role("button", name="下一页榜单").is_disabled())
        page.get_by_role("group", name="榜单来源", exact=True).get_by_role("button", name="测试榜单", exact=True).click()
        check("榜单取消内外两层滚动", page.locator("#board-columns,.board-source").evaluate_all("els=>els.every(el=>getComputedStyle(el).overflowY==='visible')"))
        page.screenshot(path=str(screens / "rankings-desktop.png"), full_page=True, animations="disabled")
        page.set_viewport_size({"width": 390, "height": 844})
        check("手机榜单没有横向溢出或嵌套滚动", page.evaluate("document.documentElement.scrollWidth <= innerWidth") and page.locator("#board-columns,.board-source").evaluate_all("els=>els.every(el=>getComputedStyle(el).overflowY==='visible')"))
        page.screenshot(path=str(screens / "rankings-mobile.png"), full_page=True, animations="disabled")
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.get_by_role("button", name="下一页榜单").click()
        page.get_by_role("button", name=re.compile("榜单中的选题 10")).click()
        expect(page.get_by_role("textbox", name="你的选题")).to_have_value("榜单中的选题 10")
        check("榜单点选进入题目确认并保留来源", page.get_by_role("button", name="排行榜选题", exact=True).get_attribute("aria-pressed") == "true" and not page.locator("#creator-board").is_visible())
        page.reload()
        expect(page.get_by_role("textbox", name="你的选题")).to_have_value("榜单中的选题 10")
        check("刷新恢复排行榜选题", page.get_by_role("button", name="排行榜选题", exact=True).get_attribute("aria-pressed") == "true")
        page.get_by_role("button", name="自定义选题", exact=True).click()
        page.get_by_role("textbox", name="你的选题").fill("")
        page.get_by_role("button", name="下一步 · 选择样式 →").click()
        expect(page.get_by_role("alert")).to_contain_text("至少 3 个字")
        check("空题目不能进入下一步", page.get_by_role("textbox", name="你的选题").is_visible())
        page.get_by_role("textbox", name="你的选题").fill("上个月技能 TOP10，恰好 2 页")
        page.get_by_text("补充要求、参考资料与配图", exact=False).click()
        page.get_by_label("特别想强调什么？").fill("保留全部十个名称")
        page.get_by_label("参考资料与来源").fill("来源：测试资料，共十项")
        page.get_by_role("button", name="下一步 · 选择样式 →").click()
        expect(page.get_by_role("heading", name="让内容，有你的风格。")).to_be_visible()
        check("步骤切换只呈现当前步骤", not page.get_by_role("textbox", name="你的选题").is_visible())
        page.get_by_role("button", name=re.compile("彩色排行卡片")).click()
        page.get_by_role("radio", name="详细", exact=True).check()
        page.get_by_role("radio", name="活跃生动", exact=True).check()
        page.reload()
        expect(page.get_by_role("heading", name="让内容，有你的风格。")).to_be_visible()
        check("刷新恢复当前步骤和版式", page.get_by_role("button", name=re.compile("彩色排行卡片")).get_attribute("aria-pressed") == "true")
        check("刷新恢复密度与风格", page.get_by_role("radio", name="详细", exact=True).is_checked() and page.get_by_role("radio", name="活跃生动", exact=True).is_checked())
        page.get_by_role("button", name="下一步 · 确认制作 →").click()
        expect(page.get_by_role("heading", name="准备好，把想法做出来。")).to_be_visible()
        expect(page.locator("#creation-review")).to_contain_text("保留全部十个名称")
        page.get_by_role("checkbox", name="抖音", exact=True).uncheck()
        page.get_by_role("checkbox", name="小红书", exact=True).uncheck()
        page.get_by_role("button", name="确认并开始制作 →").click()
        expect(page.get_by_role("alert")).to_contain_text("至少选择一个平台")
        check("未选平台不提交制作", len(Fixture.bodies) == 0)
        page.get_by_role("checkbox", name="抖音", exact=True).check()
        page.get_by_role("button", name="确认并开始制作 →").click()
        expect(page.get_by_role("heading", name="正在制作你的内容")).to_be_visible()
        body = Fixture.bodies[0]
        check("题目、要求、来源和样式完整进入实际请求", body["topic"] == "上个月技能 TOP10，恰好 2 页" and body["requirements"] == "保留全部十个名称" and body["materials"] == "来源：测试资料，共十项" and body["template_id"] == "rank_cards" and body["density"] == "detailed" and body["platforms"] == ["douyin"])
        check("创作阶段不再由界面指定页码", "pages" not in body)
        page.reload()
        expect(page.get_by_role("heading", name="正在制作你的内容")).to_be_visible()
        check("制作中刷新恢复任务且不重复提交", len(Fixture.bodies) == 1)
        page.get_by_role("tab", name="制作任务", exact=False).click()
        expect(page.get_by_role("button", name="暂停", exact=True)).to_be_visible()
        page.get_by_role("button", name="暂停", exact=True).click()
        expect(page.get_by_role("button", name="继续", exact=True)).to_be_visible()
        check("任务控制连接原有后台接口", Fixture.state == "paused")
        Fixture.state = "failed"
        page.get_by_role("button", name="刷新任务").click()
        expect(page.get_by_role("tabpanel", name="制作任务", exact=False).get_by_text("请补充来源", exact=False)).to_be_visible()
        page.get_by_role("button", name="修复后继续").click()
        expect(page.get_by_role("button", name="修复后继续")).to_have_count(0)
        page.get_by_role("tab", name="新建创作").click()
        expect(page.get_by_role("link", name="打开成品，预览与调整 →")).to_be_visible()
        check("任务完成自动回到可预览状态", Fixture.state == "succeeded")
        page.get_by_role("tab", name="我的内容", exact=False).click()
        expect(page.get_by_role("button", name="加载平台稿")).to_have_count(12)
        check("内容库只显示首批条目且不批量读取详情", not any(path.startswith("/contents/") for path in Fixture.counts))
        page.get_by_role("button", name="加载平台稿").first.click()
        expect(page.get_by_role("button", name="重新出图")).to_have_count(1)
        check("平台稿只读取点开的那一条", sum(n for path, n in Fixture.counts.items() if path.startswith("/contents/")) == 1)
        page.get_by_role("searchbox", name="搜索内容").fill("内容选题 1")
        expect(page.get_by_role("button", name="打开成品")).to_have_count(11)
        check("搜索可以找到尚未展示的内容", "内容选题 10" in page.get_by_role("table").inner_text())
        page.get_by_role("searchbox", name="搜索内容").fill("")
        before = page.get_by_role("table").inner_text()
        Fixture.failure = "/contents"
        page.get_by_role("button", name="刷新内容").click()
        expect(page.locator("#refresh-note")).to_contain_text("测试连接中断")
        check("刷新失败保留现有内容", page.get_by_role("table").inner_text() == before)
        Fixture.failure = None
        page.get_by_role("tab", name="高级工具").click()
        expect(page.get_by_role("button", name="一键出成品（local_seed）")).to_be_visible()
        check("高级工具只在进入时载入", Fixture.counts.get("/seeds", 0) == 1 and Fixture.counts.get("/assets/production-tools.html", 0) == 1)
        page.get_by_role("tab", name="新建创作").click()
        page.get_by_role("button", name="写下一篇").click()
        expect(page.get_by_role("button", name="自定义选题", exact=True)).to_be_enabled()
        check("写下一篇回到两个选题入口并保留旧任务记录", page.locator("#creator-topic").is_hidden() and page.locator("#creator-topic").input_value() == "" and len(Fixture.bodies) == 1)
        page.screenshot(path=str(screens / "production-desktop.png"), full_page=True, animations="disabled")
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.get_by_role("button", name="展开导航")).to_be_visible()
        check("手机创作页面没有横向溢出", page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        page.get_by_role("button", name="展开导航").click()
        expect(page.get_by_role("link", name="成品预览", exact=True)).to_be_visible()
        page.get_by_role("button", name="关闭导航").click()
        page.screenshot(path=str(screens / "production-mobile.png"), full_page=True, animations="disabled")
        page.emulate_media(reduced_motion="reduce")
        check("减少动态效果偏好关闭页面位移动画", page.locator(".page-intro").evaluate("el=>getComputedStyle(el).animationName") == "none")
        check("创作及任务流程没有未处理脚本异常", not errors)
        ctx.close()

        # Submission recovery and independent drafts across open tabs.
        ctx = browser.new_context(viewport={"width": 1440, "height": 850})
        a = ctx.new_page()
        a.goto(base + "/views/Production.html")
        a.get_by_role("button", name="自定义选题", exact=True).click()
        a.get_by_role("textbox", name="你的选题").fill("标签页甲的独立选题")
        b = ctx.new_page()
        b.goto(base + "/views/Production.html")
        b.get_by_role("textbox", name="你的选题").fill("标签页乙的独立选题")
        a.reload()
        expect(a.get_by_role("textbox", name="你的选题")).to_have_value("标签页甲的独立选题")
        check("其他标签页保存不覆盖当前标签页草稿", a.locator('#creator-topic').input_value() != b.locator('#creator-topic').input_value())
        a.get_by_role("button", name="下一步 · 选择样式 →").click()
        a.get_by_role("button", name="下一步 · 确认制作 →").click()
        before = len(Fixture.bodies)
        Fixture.drop_next_submission = True
        a.get_by_role("button", name="确认并开始制作 →").click()
        expect(a.locator('#creator-task')).to_be_visible()
        check("提交回包丢失后自动找回任务", len(Fixture.bodies) == before+2)
        check("恢复请求使用完全相同的内容和提交编号", Fixture.bodies[-1] == Fixture.bodies[-2])
        check("提交恢复不留下阻塞错误", a.locator('#creator-error').inner_text() == '')
        a.reload()
        expect(a.locator('#creator-task')).to_be_visible()
        check("刷新已提交任务不再次生成", len(Fixture.bodies) == before+2)
        b.reload()
        expect(b.get_by_role("textbox", name="你的选题")).to_have_value("标签页乙的独立选题")
        check("另一标签页保持可编辑而不继承提交状态", b.locator('#creator-task').is_hidden())
        ctx.close()

        # A new origin-isolated context has no old drafts or guide state.
        ctx = browser.new_context(viewport={"width": 1440, "height": 850})
        page = ctx.new_page()
        page.set_default_timeout(10000)
        page.on("pageerror", lambda e: errors.append(str(e)))
        Fixture.counts.clear()
        page.goto(base + "/")
        expect(page.get_by_text("本地工作区已连接")).to_be_visible()
        expect(page.get_by_text("30 组内容 · 0 组实际创作", exact=False)).to_be_visible()
        check("首页不查询逐条复盘报告", not any("/reviews" in path for path in Fixture.counts))
        check("演示内容不冒充实际进度", "30 篇实际创作" not in page.locator("#home-journey").inner_text())
        check("首页引导不强制弹窗", page.get_by_role("complementary", name="实际操作引导").is_hidden())
        page.get_by_role("heading", name="最近的创作").scroll_into_view_if_needed()
        expect(page.locator("#content-gallery .content-card img").first).to_be_visible()
        check("首页封面详情按视野载入，未遍历全部内容", 0 < sum(n for path, n in Fixture.counts.items() if path.startswith("/contents/")) <= 6)
        page.locator('#open-tutorial').click()
        page.locator('[data-guide-lesson="create"]').click()
        guide = page.get_by_role("complementary", name="实际操作引导")
        expect(guide).to_contain_text("文字模型已就绪")
        guide.get_by_role("button", name="下一步 →").click()
        expect(page.get_by_role("heading", name="先选一个起点。")).to_be_visible()
        expect(page.get_by_role("complementary", name="实际操作引导")).to_contain_text("从热榜选题，或写自己的题目")
        expect(page.locator("#topic-source-choices")).to_have_class(re.compile("guide-target"))
        check("引导跨页面保留步骤并先指向两个选题入口", "guide-target" in page.locator("#topic-source-choices").get_attribute("class"))
        check("未完成操作时不能伪造下一步完成", page.get_by_role("complementary", name="实际操作引导").get_by_role("button", name="下一步 →").is_disabled())
        page.get_by_role("button", name="自定义选题", exact=True).click()
        page.get_by_role("textbox", name="你的选题").fill("真实引导的新题目")
        page.get_by_role("button", name="下一步 · 选择样式 →").click()
        expect(page.get_by_role("complementary", name="实际操作引导")).to_contain_text("选题已保存")
        check("实际操作后引导状态动态更新", page.get_by_role("complementary", name="实际操作引导").get_by_role("button", name="下一步 →").is_enabled())
        page.get_by_role("button", name="退出指引").click()
        check("退出引导清除高亮", page.locator(".guide-target").count() == 0)
        page.goto(base + "/")
        expect(page.get_by_text("30 组内容 · 0 组实际创作", exact=False)).to_be_visible()
        page.screenshot(path=str(screens / "home-desktop.png"), full_page=True, animations="disabled")
        page.set_viewport_size({"width": 390, "height": 844})
        check("手机首页没有横向溢出", page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        page.screenshot(path=str(screens / "home-mobile.png"), full_page=True, animations="disabled")
        check("首页与引导没有未处理脚本异常", not errors)
        Fixture.failure = "/contents/content-1/diagnostics"
        page.goto(base + "/views/SkillWorkflow.html")
        expect(page.locator("#history")).to_contain_text("诊断记录读取失败：测试连接中断")
        check("诊断读取失败显示原因且不产生未处理异常", not errors)
        Fixture.failure = None
        ctx.close()

        # Batch cleanup uses only disposable fixture content, never the user's data.
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = ctx.new_page()
        page.set_default_timeout(10000)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/views/Production.html?pane=library")
        expect(page.get_by_role("checkbox", name="选择 T030", exact=True)).to_be_visible()
        bulk = page.get_by_role("button", name="批量清理", exact=True)
        check("未选择内容时不能批量清理", bulk.is_disabled())
        page.get_by_role("checkbox", name="选择 T030", exact=True).check()
        expect(page.locator("#library-selection-count")).to_contain_text("已选 1 条")
        check("单条勾选更新数量和表头半选状态", page.get_by_role("checkbox", name="全选当前显示的内容").evaluate("el=>el.indeterminate"))
        page.get_by_role("checkbox", name="全选当前显示的内容").check()
        expect(page.locator("#library-selection-count")).to_contain_text("已选 12 条")
        check("表头全选只选当前显示条目", page.get_by_role("checkbox", name=re.compile("^选择 T")).count() == 12)
        page.get_by_role("button", name="加载更多内容").click()
        expect(page.get_by_role("checkbox", name=re.compile("^选择 T"))).to_have_count(24)
        check("加载更多保留原选择且不自动勾选新条目", page.get_by_role("checkbox", name="选择 T030", exact=True).is_checked() and not page.get_by_role("checkbox", name="选择 T007", exact=True).is_checked())
        page.get_by_role("button", name="选择全部筛选结果（30 条）", exact=True).click()
        expect(page.locator("#library-selection-count")).to_contain_text("已选 30 条")
        check("可以选中尚未显示的全部筛选结果", page.get_by_role("checkbox", name="全选当前显示的内容").is_checked())
        page.get_by_role("searchbox", name="搜索内容").fill("内容选题 1")
        expect(page.locator("#library-selection-count")).to_contain_text("已选 0 条")
        check("变更筛选清空选择防止误清理隐藏条目", bulk.is_disabled())
        page.get_by_role("searchbox", name="搜索内容").fill("")
        page.get_by_role("checkbox", name="选择 T030", exact=True).check()
        page.get_by_role("checkbox", name="选择 T029", exact=True).check()
        page.once("dialog", lambda dialog: dialog.dismiss())
        bulk.click()
        check("取消确认不发送清理请求且保留选择", not Fixture.discard_requests and page.get_by_role("checkbox", name="选择 T029", exact=True).is_checked())
        page.screenshot(path=str(screens / "library-bulk-desktop.png"), full_page=True, animations="disabled")
        page.set_viewport_size({"width": 390, "height": 844})
        check("手机批量工具栏没有横向溢出", page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        page.screenshot(path=str(screens / "library-bulk-mobile.png"), full_page=True, animations="disabled")
        page.set_viewport_size({"width": 1440, "height": 1000})
        Fixture.failed_discards = {"content-29"}
        confirmations = []
        def approve_cleanup(dialog):
            confirmations.append(dialog.message)
            dialog.accept()
        page.once("dialog", approve_cleanup)
        bulk.click()
        expect(page.get_by_role("searchbox", name="搜索内容")).to_be_disabled()
        expect(page.locator("#library-cleanup-note")).to_contain_text("1 条未能清理")
        expect(page.get_by_role("checkbox", name="选择 T030", exact=True)).to_have_count(0)
        check("清理确认显示实际数量并只处理选中条目", "选中的 2 条" in confirmations[0] and Fixture.discard_requests == ["content-30", "content-29"])
        check("部分失败保留失败条目与勾选并报告原因", page.get_by_role("checkbox", name="选择 T029", exact=True).is_checked() and "测试连接中断，请稍后重试" in page.locator("#library-cleanup-note").inner_text())
        Fixture.failed_discards.clear()
        page.once("dialog", approve_cleanup)
        bulk.click()
        expect(page.get_by_role("checkbox", name="选择 T029", exact=True)).to_have_count(0)
        expect(bulk).to_be_disabled()
        check("重试只处理剩余失败条目", Fixture.discard_requests == ["content-30", "content-29", "content-29"])
        page.once("dialog", approve_cleanup)
        page.locator('[data-discard="content-28"]').click()
        expect(page.get_by_role("checkbox", name="选择 T028", exact=True)).to_have_count(0)
        check("单条丢弃继续可用", Fixture.discard_requests[-1] == "content-28")
        page.get_by_role("button", name="选择全部筛选结果（27 条）", exact=True).click()
        Fixture.discard_delay = .01
        page.once("dialog", approve_cleanup)
        bulk.click()
        expect(page.get_by_role("heading", name="你的第一篇内容，从这里开始")).to_be_visible()
        expect(page.locator("#library-cleanup-note")).to_contain_text("已清理 27 条")
        check("批量清理覆盖未展示条目并正确呈现空列表", not Fixture.contents and page.get_by_role("checkbox", name="全选当前显示的内容").is_disabled() and bulk.is_disabled())
        check("多选与批量清理没有未处理脚本异常", not errors)
        ctx.close()
        browser.close()
finally:
    server.shutdown()
    server.server_close()

(screens / "results.json").write_text(json.dumps({"passed": len(passed), "checks": passed}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"{len(passed)} 通过 / 0 失败")
