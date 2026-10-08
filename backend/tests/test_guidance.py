"""Tutorial regressions on a disposable HTTP fixture. No business writes/model calls."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import json
import sys
import threading

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.services.renderer import PlaywrightRenderer

passed = []
def check(name, value):
    assert value, name
    passed.append(name)
    print('PASS ' + name, flush=True)

class Fixture(SimpleHTTPRequestHandler):
    writes = []
    gets = []
    ready = True
    run_state = 'queued'
    detail = {'active_revision_id': 'current', 'revisions': []}
    publications = []
    snapshots = 0
    fail = False

    def log_message(self, *_):
        pass

    def reply(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        self.writes.append(self.path)
        self.reply({'error': {'message': 'No writes allowed'}}, 405)
    do_PUT = do_PATCH = do_DELETE = do_POST

    def do_GET(self):
        path = urlparse(self.path).path
        cls = type(self)
        if path.startswith('/api/v1/'):
            cls.gets.append(self.path)
            if cls.fail:
                return self.reply({'error': {'message': '测试连接中断'}}, 503)
            if path.endswith('/studio/options'):
                return self.reply({'text_ready': cls.ready})
            if '/runs/' in path:
                return self.reply({'state': cls.run_state, 'blocked_stage': 'research', 'error': '请补充原文'})
            if '/contents/' in path:
                return self.reply(cls.detail)
            if path.endswith('/metrics'):
                return self.reply({'snapshot_count': cls.snapshots})
            if path.endswith('/publications'):
                return self.reply({'items': cls.publications})
            return self.reply({})
        if path == '/' or (path.startswith('/views/') and not path.endswith('UserGuide.html')):
            target = {
                '/views/ApiSettings.html': '<input id="t-name" aria-label="模型名称">',
                '/views/Production.html': '<div id="topic-source-choices">选题入口</div><textarea id="creator-topic"></textarea><div id="creator-templates">样式</div><button id="creator-start">制作</button><div id="runs">任务</div>',
                '/views/ReviewPreview.html': '<select id="sel-content"><option value="content-1">测试内容</option></select><div id="pages">实际图片</div><section id="revision-studio">内容再调整</section><button id="btn-approve">批准</button><details id="diagnostic-details"><summary>资料与生成诊断</summary></details>',
                '/views/PublishingHub.html': '<a id="choose-manual">手动发布</a><input type="checkbox" id="background-revisit">',
                '/views/ManualPublishing.html': '<select id="manual-content"><option>平台稿</option></select><a id="manual-preview" href="/views/ReviewPreview.html?content=content-1">预览</a><input id="manual-link"><select id="manual-publication"><option value="own">当前作品</option><option value="other">其他作品</option></select>',
                '/views/TemplateLibrary.html': '<select id="preview-mode"><option>同题对照</option></select><textarea id="template-instructions">写作 Skill</textarea>',
                '/views/VideoStudio.html': '<select id="video-source"><option>图文</option></select><div id="sentence-list">逐句分镜</div><select id="voice-engine"><option>本机朗读</option></select>',
                '/views/SkillWorkflow.html': '<div id="skills">规则</div><select id="trace-content"><option>执行履历</option></select>',
            }.get(path, '')
            html = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<link rel="stylesheet" href="/assets/app.css"><link rel="stylesheet" href="/assets/workbench.css"></head>'
                    '<body><nav id="nav" class="nav"></nav><main class="wrap"><h1>教程测试页面</h1><button id="open-tutorial">打开教程目录</button>'
                    + target + '</main><script type="module">import {navBar} from "/assets/app.js?v=20261005";document.getElementById("nav").innerHTML=navBar("index");</script></body></html>')
            raw = html.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        return super().do_GET()

server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Fixture, directory=str(ROOT / 'frontend/src')))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = 'http://127.0.0.1:' + str(server.server_port)
proof = ROOT / 'docs/test-artifacts/tutorial-20261006'
proof.mkdir(parents=True, exist_ok=True)
try:
    with sync_playwright() as automation:
        browser = automation.chromium.launch(headless=True, executable_path=PlaywrightRenderer.resolve_executable())
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base + '/')
        expect(page.locator('.tutorial-entry')).to_be_visible()
        expect(page.locator('#guided-operation')).to_be_hidden()
        expect(page.locator('#guide-hub')).to_be_hidden()
        check('no forced tutorial on first visit', True)
        page.locator('#open-tutorial').click()
        expect(page.locator('#guide-hub')).to_be_visible()
        check('six distinct lessons in accessible dialog', page.locator('[data-guide-lesson]').count() == 6 and page.locator('#guide-hub').get_attribute('aria-labelledby') == 'guide-hub-title')
        page.screenshot(path=str(proof / 'tutorial-directory-desktop.png'), full_page=True)
        page.keyboard.press('Escape')
        expect(page.locator('#guide-hub')).to_be_hidden()
        check('escape closes optional directory and restores focus', page.locator('#open-tutorial').evaluate('el=>el===document.activeElement'))
        page.locator('.tutorial-entry').click()
        page.locator('[data-guide-lesson="create"]').click()
        expect(page.locator('#guide-count')).to_have_text('做出第一篇图文 · 1 / 7')
        expect(page.locator('#guide-next')).to_be_enabled()
        check('model check reads readiness without calling model', page.url.endswith('/views/ApiSettings.html') and '/api/v1/studio/options' in Fixture.gets)
        page.locator('#guide-next').click()
        expect(page.locator('#guide-count')).to_have_text('做出第一篇图文 · 2 / 7')
        expect(page.locator('#guide-next')).to_be_disabled()
        check('missing topic cannot be counted complete', True)
        page.evaluate('localStorage.setItem("cwb.direct.creator.v2",JSON.stringify({topic:"2万token价值多少钱",step:1}));document.dispatchEvent(new Event("creator-step-changed"))')
        expect(page.locator('#guide-next')).to_be_enabled()
        check('topic state update unlocks next step', True)
        page.locator('#guide-next').click()
        expect(page.locator('#guide-count')).to_have_text('做出第一篇图文 · 3 / 7')
        expect(page.locator('#guide-next')).to_be_disabled()
        page.evaluate('localStorage.setItem("cwb.direct.creator.v2",JSON.stringify({topic:"2万token价值多少钱",step:2}));document.dispatchEvent(new Event("creator-draft-changed"))')
        expect(page.locator('#guide-next')).to_be_enabled()
        check('style completion follows saved creator step', True)
        page.locator('#guide-next').click()
        expect(page.locator('#guide-next')).to_be_disabled()
        page.evaluate('localStorage.setItem("cwb.direct.creator.v2",JSON.stringify({topic:"2万token价值多少钱",step:2,task:{run_id:"run-1",content_id:"content-1"}}));document.dispatchEvent(new Event("creator-task-changed"))')
        expect(page.locator('#guide-next')).to_be_enabled()
        check('submitted task is distinct from finished images', '完成后再核对' in page.locator('#guide-status').inner_text())
        page.locator('#guide-next').click()
        expect(page.locator('#guide-next')).to_be_disabled()
        check('preview requires actual page artifact', 'content=content-1' in page.url)
        Fixture.detail = {'active_revision_id': 'current', 'revisions': [{'revision_id': 'current', 'platforms': [{'state': 'ready_for_review', 'artifacts': [{'kind': 'page_image'}]}]}]}
        page.evaluate('document.dispatchEvent(new Event("change"))')
        expect(page.locator('#guide-next')).to_be_enabled()
        page.locator('#guide-next').click()
        expect(page.locator('#guide-count')).to_have_text('做出第一篇图文 · 6 / 7')
        expect(page.locator('#revision-studio')).to_have_class('guide-target')
        expect(page.locator('#guide-next')).to_have_text('我了解了 →')
        check('revision lesson highlights actual editor without charging revision', True)
        before_url = page.url
        page.locator('#guide-go').click()
        check('locate button stays on the page and highlights the real editor', page.url == before_url and page.locator('#revision-studio').get_attribute('class') == 'guide-target')
        page.reload()
        expect(page.locator('#guide-count')).to_have_text('做出第一篇图文 · 6 / 7')
        check('lesson resumes after reload', True)
        page.keyboard.press('Escape')
        expect(page.locator('#guided-operation')).to_have_class('guide-panel collapsed')
        check('escape collapses active guide', True)
        page.locator('#guide-menu').click()
        page.locator('#guide-resume').click()
        expect(page.locator('#guide-next')).to_be_visible()
        page.locator('#guide-next').click()
        Fixture.detail['revisions'].append({'revision_id': 'old', 'platforms': [{'state': 'approved'}]})
        page.evaluate('document.dispatchEvent(new Event("change"))')
        expect(page.locator('#guide-status')).to_contain_text('等待核对')
        expect(page.locator('#guide-next')).to_be_disabled()
        check('old version approval cannot finish current version', True)
        Fixture.detail['revisions'][0]['platforms'][0]['state'] = 'approved'
        page.evaluate('document.dispatchEvent(new Event("change"))')
        expect(page.locator('#guide-next')).to_be_enabled()
        page.locator('#guide-next').click()
        expect(page.locator('#guided-operation')).to_be_hidden()
        expect(page.locator('#guide-hub')).to_be_visible()
        check('finishing lesson returns to directory', True)
        page.locator('[data-guide-lesson="manual"]').click()
        for _ in range(2):
            page.locator('#guide-skip').click()
        expect(page.locator('#guide-count')).to_have_text('手动发布与数据回访 · 3 / 5')
        Fixture.publications = [{'id': 'other', 'content_id': 'different-content', 'run_mode': 'real', 'status': 'verified'}]
        page.evaluate('document.dispatchEvent(new Event("change"))')
        expect(page.locator('#guide-status')).to_contain_text('等待发布')
        expect(page.locator('#guide-next')).to_be_disabled()
        check('unrelated real publication does not satisfy guide', True)
        Fixture.publications = [{'id': 'own', 'content_id': 'content-1', 'run_mode': 'fixture', 'status': 'verified'}]
        page.evaluate('document.dispatchEvent(new Event("change"))')
        expect(page.locator('#guide-status')).to_contain_text('等待发布')
        check('fixture publication does not satisfy real publication guide', page.locator('#guide-next').is_disabled())
        Fixture.publications[0].update(run_mode='real', status='withdrawn')
        page.evaluate('document.dispatchEvent(new Event("change"))')
        expect(page.locator('#guide-status')).to_contain_text('等待发布')
        check('withdrawn publication is not marked completed', page.locator('#guide-next').is_disabled())
        Fixture.publications[0]['status'] = 'declared'
        page.evaluate('document.dispatchEvent(new Event("change"))')
        expect(page.locator('#guide-next')).to_be_enabled()
        page.locator('#guide-next').click()
        page.locator('#manual-publication').select_option('other')
        expect(page.locator('#guide-status')).to_contain_text('不属于当前成品')
        check('selected unrelated revisit is rejected', page.locator('#guide-next').is_disabled())
        page.locator('#manual-publication').select_option('own')
        expect(page.locator('#guide-status')).to_contain_text('保存实际指标')
        check('publication alone is not a metrics snapshot', page.locator('#guide-next').is_disabled())
        Fixture.snapshots = 1
        page.evaluate('document.dispatchEvent(new Event("change"))')
        expect(page.locator('#guide-next')).to_be_enabled()
        check('actual snapshot enables revisit completion', True)
        page.locator('#guide-close').click()
        for lesson, count in [('assisted', 3), ('templates', 3), ('video', 3), ('diagnose', 3)]:
            page.locator('.tutorial-entry').click()
            page.locator(f'[data-guide-lesson="{lesson}"]').click()
            for index in range(count):
                expect(page.locator('#guide-next')).to_be_enabled()
                check(f'{lesson} step {index+1} resolves actual target', page.locator('.guide-target').count() == 1)
                page.locator('#guide-next').click()
            expect(page.locator('#guide-hub')).to_be_visible()
            page.locator('#guide-hub-close').click()
        page.locator('.tutorial-entry').click()
        page.locator('[data-guide-lesson="create"]').click()
        Fixture.ready = False
        page.evaluate('document.dispatchEvent(new Event("change"))')
        expect(page.locator('#guide-status')).to_contain_text('等待保存')
        check('unconfigured model does not appear ready', page.locator('#guide-next').is_disabled())
        Fixture.fail = True
        page.evaluate('document.dispatchEvent(new Event("change"))')
        expect(page.locator('#guide-status')).to_contain_text('测试连接中断')
        check('read failure is explicit and can be skipped', page.locator('#guide-skip').is_enabled() and page.locator('#guide-next').is_disabled())
        Fixture.fail = False
        page.locator('#guide-close').click()
        page.goto(base + '/views/UserGuide.html')
        expect(page.locator('.tutorial-entry')).to_be_visible()
        check('full manual covers six workflow sections', page.locator('.user-guide>section').count() == 6)
        check('manual explains editing current content and Skill plus template', '内容再调整' in page.inner_text('main') and '冻结版本' in page.inner_text('main'))
        page.screenshot(path=str(proof / 'user-guide-desktop.png'), full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.emulate_media(reduced_motion='reduce')
        check('full manual has no mobile horizontal overflow', page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        page.locator('.tutorial-entry').click()
        expect(page.locator('#guide-hub')).to_be_visible()
        check('directory fits mobile width', page.locator('#guide-hub').evaluate('e=>e.getBoundingClientRect().right<=innerWidth && e.getBoundingClientRect().left>=0'))
        check('native dialog traps focus', page.locator('#guide-hub').evaluate('e=>e.contains(document.activeElement)'))
        page.screenshot(path=str(proof / 'tutorial-directory-mobile.png'), full_page=True)
        page.locator('[data-guide-lesson="templates"]').click()
        expect(page.locator('#guide-next')).to_be_enabled()
        check('active guide fits mobile viewport', page.locator('#guided-operation').evaluate('e=>e.getBoundingClientRect().right<=innerWidth && e.getBoundingClientRect().bottom<=innerHeight'))
        check('guide respects reduced motion', page.locator('#guided-operation').evaluate('e=>getComputedStyle(e).animationName==="none"'))
        page.screenshot(path=str(proof / 'tutorial-follow-mobile.png'), full_page=True)
        check('tutorial makes zero mutation requests', not Fixture.writes)
        check('tutorial has zero browser script errors', not errors)
        browser.close()
finally:
    server.shutdown()

print(f'{len(passed)} 通过 / 0 失败')
proof.joinpath('results.json').write_text(json.dumps({'passed': passed, 'writes': Fixture.writes}, ensure_ascii=False, indent=2), encoding='utf-8')
