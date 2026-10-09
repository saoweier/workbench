"""Real browser operations against isolated seeded API and Worker processes."""
from pathlib import Path
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from playwright.sync_api import sync_playwright, expect
from app.services.renderer import PlaywrightRenderer
from mock_provider import start_mock

passed=0
def check(name, value):
    global passed
    assert value, name
    passed+=1
    print("PASS " + name, flush=True)

# 导航打断在途请求时会冒出瞬时网络错误。那是环境噪声，不是页面脚本缺陷；
# 不过滤会让"无脚本异常"断言偶发失败，也会掩盖真正的脚本错误。
PAGE_NOISE=('net::ERR_ABORTED','net::ERR_NETWORK_CHANGED','net::ERR_CONNECTION_RESET',
            'net::ERR_CONNECTION_REFUSED','Failed to fetch','The user aborted a request',
            'Load request cancelled')
def page_errors(errors):
    return [e for e in errors if not any(n in e for n in PAGE_NOISE)]

tmp = Path(tempfile.mkdtemp(prefix="cwb-browser-"))
for name in ["artifacts", "tmp"]:
    shutil.copytree(ROOT / "examples/demo/storage" / name, tmp / name)
shutil.copy2(ROOT / "examples/demo/storage/workbench.db", tmp / "cwb.db")
shutil.copy2(ROOT / "examples/demo/storage/profiles.json", tmp / "profiles.json")
with socket.socket() as probe:
    probe.bind(("127.0.0.1",0));port=probe.getsockname()[1]
env={**os.environ,"PYTHONUTF8":"1","PYTHONPATH":str(ROOT / "backend"),
     "CWB_STORAGE_ROOT":str(tmp),"CWB_ARTIFACT_DIR":str(tmp/"artifacts"),"CWB_TMP_DIR":str(tmp/"tmp"),
     "CWB_DATABASE_URL":f"sqlite:///{tmp/'cwb.db'}","CWB_SECRET_STORE_PATH":str(tmp/"secrets.json"),
     "CWB_PENDING_REVIEW_STOCK_LIMIT":"20"}
url=f"http://127.0.0.1:{port}"
server, requests=start_mock()
outputs=[];processes=[]
proof=ROOT / "docs/test-artifacts"
proof.mkdir(parents=True,exist_ok=True)
try:
    for name, args in [("api",["-m","uvicorn","app.main:app","--host","127.0.0.1","--port",str(port)]),
                       ("worker",["-m","app.worker","--interval","0.5"])]:
        log=(tmp/f"{name}.log").open("wb");outputs.append(log)
        processes.append(subprocess.Popen([sys.executable,*args],cwd=ROOT,env=env,stdout=log,stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0))
    for _ in range(60):
        try:
            with urllib.request.urlopen(url+"/api/v1/health",timeout=1) as response:
                if json.load(response)["worker"]["status"]=="running":break
        except Exception:pass
        time.sleep(0.5)
    def get(path):
        with urllib.request.urlopen(url+"/api/v1"+path) as r:return json.load(r)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=PlaywrightRenderer.resolve_executable())
        page=browser.new_page(viewport={"width":1440,"height":1000},accept_downloads=True)
        page.set_default_timeout(15000)
        errors=[]
        page.on("pageerror",lambda e:errors.append(str(e)))
        views=["", "Production", "ReviewPreview", "Publications", "DataImport", "ReviewInsights",
               "FeedbackLoop", "Attention", "UsageCosts", "ApiSettings", "VideoStudio", "PublishingHub", "ManualPublishing", "DouyinPublishing", "SkillWorkflow", "QueryLab", "PlatformAccounts"]
        # 每个视图等待"已挂载"的标志。导航条必须与 views 一一对应，
        # 否则新增页面（如 QueryLab）会让这条总检查静默失效。
        ready={"": "#workspace-health", "Production": "#creator-model",
               "PlatformAccounts": "#connection-title", "QueryLab": ".query-frame"}
        for view in views:
            page.goto(url+(f"/views/{view}.html" if view else "/"))
            page.locator(ready.get(view, "#status")).wait_for(state="visible")
            page.wait_for_timeout(500)
            check("页面实际执行脚本："+(view or "首页")+("："+" | ".join(e[:160] for e in page_errors(errors)) if page_errors(errors) else ""),
                  not page_errors(errors) and page.locator("#nav a").count()==len(views))
        check("账号连接默认需要本人确认本机保存", page.locator('#connect-douyin').is_disabled())
        page.locator('#local-consent').check()
        check("确认本机保存后可打开账号连接", page.locator('#connect-douyin').is_enabled())
        page.locator('#local-consent').uncheck()
        check("取消本机保存后不再允许打开", page.locator('#connect-douyin').is_disabled())
        page.locator('#check-douyin').click()
        page.wait_for_function("!document.querySelector('#check-douyin').disabled")
        check("未登录时刷新不误报成功", page.locator('#connection-title').inner_text() == '还未连接抖音账号')
        page.goto(url+"/");page.wait_for_timeout(1000)
        check("首页显示六组预设内容",page.locator(".content-card").count()==6)
        page.locator('.content-card').first.wait_for()
        check("图卡绑定六条真实内容",page.locator('.content-card').count()==6)
        for card in page.locator(".content-card").all():card.scroll_into_view_if_needed()
        page.wait_for_function("document.querySelectorAll('.content-card img').length===6 && [...document.querySelectorAll('.content-card img')].every(i=>i.complete && i.naturalWidth>0)")
        check("图卡使用可读取的真实封面",page.locator('.content-card img').count()==6)
        page.screenshot(path=str(proof/"workbench.png"),full_page=True)
        contents=get("/contents")["items"]
        demo=next(c for c in contents if c["display_id"]=="DEMO-001")
        # 页面模块用顶层 await 逐个读接口，浏览器 load 事件早于初始化完成就返回，
        # 用户/自动化完全可能在详情读回来之前点「按要求生成新版本」。那时 DETAIL 还是
        # null，requireRev() 直接抛错、请求根本没发出去，界面只弹一个瞬时提示 ——
        # 看起来就是"点了没反应"（全量回归里 content_skills 就这么偶发挂过一次，
        # 一个 30s 超时把真根因盖住了）。这里把所有后端响应延迟 0.6s 把窗口撑开，
        # 断言窗口内四个入口先禁用、详情读回后恢复可用。
        page.route("**/api/v1/**", lambda r: (time.sleep(0.6), r.continue_()))
        page.goto(url+f"/views/ReviewPreview.html?content={demo['id']}")
        early=[i for i in ("revision-submit","revision-record","skill-audit","btn-illustrate")
               if not page.locator("#"+i).is_disabled()]
        check("详情未读回时调整入口先禁用，不会点了没反应"+("："+"、".join(early) if early else ""),not early)
        page.unroute("**/api/v1/**")
        page.wait_for_function("document.querySelectorAll('img').length > 0 && [...document.querySelectorAll('img')].some(i=>i.naturalWidth>0)")
        check("详情读回后调整入口恢复可用",not page.locator('#revision-submit').is_disabled())
        check("预览显示真实页图",page.locator("img").first.evaluate("i=>i.naturalWidth")>0)
        check('预览提供图文新版本入口', page.locator('#btn-illustrate').is_visible())
        page.locator('.page-open').first.click()
        check('图片可放大阅读', page.locator('#page-zoom').evaluate('d=>d.open'))
        page.locator('#zoom-next').click()
        check('放大阅读可翻到下一页', '2 /' in page.locator('#zoom-title').inner_text())
        page.locator('#zoom-close').click()
        check('关闭放大阅读返回预览', not page.locator('#page-zoom').evaluate('d=>d.open'))
        with page.expect_response(lambda r:"/review-decisions" in r.url and r.request.method=="POST") as approval:
            page.locator("#btn-approve").click()
        check("界面审批绑定实际版本",approval.value.json()["ok"])
        with page.expect_response(lambda r:r.url.endswith("/packages") and r.request.method=="POST") as package:
            page.locator("#btn-zip").click()
        check("界面导出通过审批的发布包",package.value.status==201)
        with page.expect_download() as download:
            page.locator('a[href*="/download"]').click()
        downloaded=tmp/"browser-package.zip";download.value.save_as(str(downloaded))
        with zipfile.ZipFile(downloaded) as z:
            check("实际下载ZIP包含PNG与清单",any(n.endswith(".png") for n in z.namelist()) and any("manifest" in n for n in z.namelist()))
        page.screenshot(path=str(proof/"preview.png"),full_page=True)

        # 生成中途停下的内容没有任何 revision。这类内容一旦进「内容再调整」，
        # 旧的 activeRev().revision_id 会抛 TypeError（用户实际遇到：
        # Cannot read properties of undefined (reading 'revision_id')）。
        noversion_id = "11111111-1111-1111-1111-111111111111"
        db = sqlite3.connect(str(tmp / "cwb.db"))
        db.execute("insert into content_item (id,display_id,topic,selected_by,state,run_mode,created_at)"
                   " values (?,?,?,?,?,?,?)",
                   (noversion_id, "C900", "没有版本的内容：取 revision_id 不能崩", "user", "blocked",
                    "real", "2026-10-04 08:00:00"))
        db.commit(); db.close()
        before_errors = len(page_errors(errors))
        page.goto(url+f"/views/ReviewPreview.html?content={noversion_id}")
        page.locator("#revision-studio").wait_for()
        page.wait_for_timeout(400)
        check("无版本内容能打开预览页而不是白屏", page.locator("#sel-content").input_value() == noversion_id)
        check("无版本时调整入口被禁用", page.locator("#revision-submit").is_disabled()
              and page.locator("#skill-audit").is_disabled())
        check("无版本时说明为什么不能调整", "没有可调整的版本" in page.locator("#revision-message").inner_text())
        page.locator("#revision-requirements").fill("改为：补充具体数据与来源")
        page.evaluate("document.getElementById('revision-submit').disabled=false")
        page.locator("#revision-submit").click()
        page.wait_for_selector("#cwb-toast .toast.err")
        forced = page.locator("#cwb-toast .toast.err").first.inner_text()
        check("强行点击不抛原始 TypeError", "revision_id" not in forced and "undefined" not in forced)
        check("强行点击给出可读原因", "还没有可调整的版本" in forced)
        check("无版本内容的调整不产生未捕获异常"
              + ("：" + " | ".join(e[:160] for e in page_errors(errors)) if page_errors(errors) else ""),
              len(page_errors(errors)) == before_errors)

        page.goto(url+"/views/ApiSettings.html")
        for field,value in [("name","本地协议模拟"),("base",f"http://127.0.0.1:{server.server_port}/v1"),
                            ("model","local-stub"),("key","browser-test-key")]:page.locator("#t-"+field).fill(value)
        page.locator("#t-enabled").check();page.locator("#t-local").check()
        with page.expect_response(lambda r:r.url.endswith("/provider-configs") and r.request.method=="POST") as config:
            page.locator("#t-save").click()
        check("界面保存API成功且无自动调用",config.value.status==201 and len(requests)==0)
        page.locator("[data-edit]").click();page.locator("#t-model").fill("updated-local-stub")
        with page.expect_response(lambda r:"/provider-configs/" in r.url and r.request.method=="PATCH") as edited:
            page.locator("#t-save").click()
        check("界面可修改已保存API",edited.value.status==200)
        page.goto(url+"/views/Production.html")
        page.get_by_role('button',name='自定义选题',exact=True).click()
        expect(page.locator('#creator-topic')).to_be_visible()
        check("直接创作入口不再推荐和改换角度",page.locator('#guide-recommend').count()==0)
        check("工程表单默认折叠",page.locator('#manual-production').count()==0)
        check("打开创作页不自动消耗模型",len(requests)==0)
        picked='新手怎么整理会议行动项'
        # 隔离测试没有配置搜索 Provider，而真实模式默认要求可读资料（防编造）。
        # 走产品设计里的正常路径：用户粘贴参考资料，再基于它创作。
        material=('会议行动项整理方法（用户提供的参考资料）：会前明确议题与负责人；'
                  '会中只记录已定的结论与待办；会后当天把每条行动项写成「谁、做什么、何时完成」；'
                  '下一次会议开始前逐条核对完成情况。')
        page.locator('#creator-topic').fill(picked)
        page.get_by_text('补充要求、参考资料与配图',exact=False).click()
        page.locator('#creator-requirements').fill('不要提付费工具，语言轻松一点，每页配图解')
        page.locator('#creator-materials').fill(material)
        page.reload();expect(page.locator('#creator-topic')).to_have_value(picked)
        check("刷新恢复原题、要求和参考资料",
              page.locator('#creator-requirements').input_value().startswith('不要提付费工具')
              and page.locator('#creator-materials').input_value()==material)
        page.get_by_role('button',name='下一步 · 选择样式 →').click()
        page.get_by_role('button',name='下一步 · 确认制作 →').click()
        with page.expect_response(lambda r:r.url.endswith('/studio/produce')) as direct:
            page.locator('#creator-start').click()
        guide_task=direct.value.json()
        check("直接生成自动排队无需建批次",direct.value.status==202 and guide_task['state']=='queued')
        for _ in range(120):
            guide_run=get('/runs/'+guide_task['run_id'])
            if guide_run['state'] in {'succeeded','failed','paused'}:break
            page.wait_for_timeout(500)
        # 注意"没到终态"和"失败"是两回事：state 还是 queued/running 说明 60s 预算不够
        # （机器同时在跑别的服务时会这样），而 failed 一定带 error。把两者都写进断言名里。
        check("两页直接创作经完整后台完成：state=%s error=%s" % (guide_run['state'], guide_run.get('error')),
              guide_run['state']=='succeeded')
        detail=get('/contents/'+guide_task['content_id'])
        check("用户原题未被换成推荐角度",detail['topic']==picked)
        rev=next(r for r in detail['revisions'] if r['revision_id']==detail['active_revision_id'])
        check("每个平台都遵守两页要求",all(len(p['pages'])==2 for p in rev['platforms']))
        master_calls=[r for r in requests if '用户确认的创作要求' in r['messages'][-1]['content']]
        check("真实协议请求保留用户补充要求",len(master_calls)>=3 and all('不要提付费工具' in r['messages'][-1]['content'] and '语言轻松一点' in r['messages'][-1]['content'] for r in master_calls))
        expect(page.locator('#creator-preview')).to_be_visible(timeout=15000)
        page.reload();expect(page.locator('#creator-preview')).to_be_visible()
        check("恢复任务和成品入口",'已生成' in page.locator('#creator-task-title').inner_text())
        page.locator('#creator-preview').click();expect(page.locator('#revision-shortcuts')).to_be_visible()
        page.get_by_role('button',name='更短更精简',exact=True).click()
        check("预览可一键填入精简要求",page.locator('#revision-requirements').input_value().startswith('更短更精简'))
        page.goto(url+'/views/Production.html');page.locator('#creator-new').click()
        check("完成后可另开一篇并重新选择起点",page.locator('#creator-topic').input_value()=='' and page.locator('#creator-topic').is_hidden() and page.get_by_role('button',name='自定义选题',exact=True).is_enabled())
        payload={'category':'hot','categories':{'hot':'全部热点','tech':'科技榜'},'refreshing':False,'message':'HotPush协议模拟','progress':{'total':13,'success':1},'base_url':'https://hotpush.dawenzaist.de5.net','sources':[{'name':'Hacker News 协议模拟','platform':'hackernews','source_category':'技术','state':'ready','note':'HotPush本地协议模拟，不是真实热点','fetched_at':'2026-10-04T01:01:00Z','updated_at':'2026-10-04T01:00:00Z','items':[{'id':'mock-hotpush','rank':1,'title':'HotPush topic fixture','heat':None}]}]}
        page.route('**/api/v1/studio/trends?**',lambda route:route.fulfill(json=payload))
        page.get_by_role('button',name='排行榜选题',exact=True).click();expect(page.locator('.board-item')).to_have_count(1)
        check("HotPush来源时间及不伪造热度可见",'HotPush' in page.locator('#board-state').inner_text() and '上游未提供热度分值' in page.locator('.board-item').inner_text())
        page.locator('.board-item').click();check("热点直接填入题目无推荐调用",page.locator('#creator-topic').input_value()=='HotPush topic fixture')
        page.screenshot(path=str(proof/'direct-creator-desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844});check("390px直接创作无横向溢出",page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        page.screenshot(path=str(proof/'direct-creator-mobile.png'),full_page=True)
        page.set_viewport_size({'width':1440,'height':1000})
        page.goto(url+"/views/Production.html")
        page.get_by_role("tab",name="高级工具",exact=True).click()
        page.locator("#f-topic").fill("测试自己的资料生产")
        page.locator("#f-materials").fill("工作流先登记资料来源，再把内容整理成双平台稿件。每张图片要在人工批准后才能导出。")
        page.locator("#f-mode").select_option("real")
        page.locator("#btn-batch").click();page.locator("#btn-enqueue").wait_for(state="visible")
        with page.expect_response(lambda r:"/batches/" in r.url and r.url.endswith("/runs")) as queued:
            page.locator("#btn-enqueue").click()
        task=queued.value.json()
        check("界面提交真实后台任务",queued.value.status==202 and task["state"]=="queued")
        for _ in range(120):
            run=get("/runs/"+task["run_id"])
            if run["state"] in {"succeeded","failed"}:break
            page.wait_for_timeout(500)
        check("新资料经HTTP模拟模型生成成品",run["state"]=="succeeded" and len(requests)>=4)
        check("编辑模型配置立即作用于后台",all(r["model"]=="updated-local-stub" for r in requests))
        check("用户资料真正传给模型",any("工作流先登记资料来源" in r["messages"][-1]["content"] for r in requests))
        page.goto(url+f"/views/ReviewPreview.html?content={task['content_id']}")
        page.wait_for_function("document.querySelectorAll('img').length>0 && [...document.querySelectorAll('img')].some(i=>i.naturalWidth>0)")
        check("新内容在预览页可见",page.locator("#sel-content").input_value()==task["content_id"])
        check("首个平台显示真实排版规格",'1080 × 1440' in page.locator('#meta').inner_text())
        page.locator('.tab').filter(has_text='小红书').click()
        check("切换小红书保留真实排版规格",'1080 × 1440' in page.locator('#meta').inner_text())
        page.goto(url+"/views/DataImport.html")
        page.locator("#csv").fill("platform,post_id,observed_at,metric_name,value,unit\ndouyin,DEMO-005-douyin,2026-10-03T11:00:00+08:00,views,19999,count")
        page.locator("#dry").uncheck()
        with page.expect_response(lambda r:r.url.endswith("/imports") and r.request.method=="POST") as imported:
            page.locator("#btn-import").click()
        check("界面导入指标成功",imported.value.status==200 and imported.value.json()["accepted_rows"]==1)
        page.locator("#import-kind").select_option("comments")
        page.locator("#csv").fill("platform,post_id,anonymous_comment_id,text,observed_at,like_count\ndouyin,DEMO-005-douyin,browser-new-comment,有没有完整的教程？,2026-10-03T11:00:00+08:00,3")
        page.locator("#sampling").fill("浏览器验收的虚构评论样本")
        with page.expect_response(lambda r:r.url.endswith("/comments/imports") and r.request.method=="POST") as comments:
            page.locator("#btn-import").click()
        check("界面导入匿名评论成功",comments.value.status==200 and comments.value.json()["accepted_rows"]==1)
        page.goto(url+"/views/Publications.html")
        demo2=next(c for c in contents if c["display_id"]=="DEMO-002")
        platform2=get("/contents/"+demo2["id"])["revisions"][0]["platforms"][0]
        page.locator("#f-pr").select_option(platform2["platform_revision_id"])
        page.locator("#f-post").fill("browser-publication-stub")
        page.locator("#f-at").fill("2026-10-01T09:00")
        with page.expect_response(lambda r:r.url.endswith("/publications") and r.request.method=="POST") as published:
            page.locator("#btn-reg").click()
        check("界面登记人工发布记录",published.value.status==200)
        page.goto(url+"/views/ReviewInsights.html")
        demo5=next(c for c in contents if c["display_id"]=="DEMO-005")
        page.locator("#sel-content").select_option(demo5["id"])
        with page.expect_response(lambda r:r.url.endswith("/reviews") and r.request.method=="POST") as reviewed:
            page.locator("#btn-gen").click()
        check("界面生成新版复盘",reviewed.value.status==200 and reviewed.value.json()["version"]>=2)
        page.wait_for_timeout(300)
        check("复盘页面显示真实快照观察明细",'19999' in page.locator('#reports').inner_text())
        check("复盘结构说明可读",'[object Object]' not in page.locator('#reports').inner_text())
        page.goto(url+f"/views/ReviewInsights.html?content={task['content_id']}")
        page.wait_for_timeout(300)
        check("新内容复盘链接定位正确",page.locator('#sel-content').input_value()==task['content_id'])
        check("未发布内容不混入演示评论",page.locator('#sel-pub option').count()==1
            and '虚构演练样本' not in page.locator('#cats').inner_text())
        page.goto(url+"/views/FeedbackLoop.html")
        page.locator("[data-adopt]").first.click();page.wait_for_timeout(500)
        page.locator("#sel-status").select_option("accepted");page.wait_for_timeout(500)
        check("界面采用后可回退",page.locator("[data-revert]").count()>0)
        page.locator("[data-revert]").first.click();page.wait_for_timeout(500)
        check("全流程无页面脚本异常"+("："+" | ".join(e[:160] for e in page_errors(errors)) if page_errors(errors) else ""),
              not page_errors(errors))
        page.goto(url+"/views/ReviewInsights.html");page.wait_for_timeout(500)
        page.screenshot(path=str(proof/"insights.png"),full_page=True)
        page.goto(url+'/');page.locator('.content-card').first.wait_for()
        page.locator('h1').click();page.keyboard.press('/')
        check("斜杠快捷键聚焦搜索",page.locator('#workspace-search').evaluate('el=>el===document.activeElement'))
        page.locator('#workspace-search').fill('API')
        page.locator('#workspace-results a').first.wait_for()
        check("顶部搜索找到真实业务页面",page.locator('#workspace-results').get_by_role('link',name='API 设置',exact=False).count()==1)
        page.locator('#workspace-search').fill('DEMO-003')
        target=next(c for c in contents if c['display_id']=='DEMO-003')
        result=page.locator(f'#workspace-results a[href*="{target["id"]}"]')
        result.wait_for();result.click()
        page.wait_for_function("id=>document.querySelector('#sel-content')?.value===id",arg=target['id'])
        check("搜索成品进入对应内容预览",page.locator('#sel-content').input_value()==target['id'])
        for width in [1440,1280,1024,768,390]:
            page.set_viewport_size({'width':width,'height':900 if width>768 else 844})
            page.goto(url+'/');page.locator('.content-card').first.wait_for()
            check(f"{width}px 首页无横向溢出",page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
            check(f"{width}px 导航模式正确",page.locator('#nav').evaluate('(n)=>n.inert === (innerWidth<=768)'))
            page.screenshot(path=str(proof/f'ui-{width}.png'),full_page=width<=768)
        for view in views[1:]:
            page.goto(url+f'/views/{view}.html');page.wait_for_timeout(350)
            check('390px 业务页无横向溢出：'+view,page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        page.goto(url+'/');page.locator('.content-card').first.wait_for()
        page.get_by_role('button',name='展开导航').click()
        check('手机导航可展开',page.evaluate("document.body.classList.contains('nav-open') && !document.querySelector('#nav').inert"))
        page.get_by_role('link',name='API 设置',exact=True).click()
        page.wait_for_url('**/views/ApiSettings.html')
        check('手机导航可进入设置',page.locator('#t-save').is_visible())
        page.get_by_role('button',name='展开导航').click();page.keyboard.press('Escape')
        check('Escape 关闭手机导航',page.locator('#nav').evaluate('n=>n.inert'))
        page.emulate_media(reduced_motion='reduce')
        check('尊重减少动态效果设置',page.locator('button').first.evaluate("e=>parseFloat(getComputedStyle(e).transitionDuration)<0.01"))
        check('改版全部交互无脚本异常'+("："+" | ".join(e[:160] for e in page_errors(errors)) if page_errors(errors) else ""),
              not page_errors(errors))
        page.goto(url+'/');page.locator('.content-card').first.wait_for()
        page.screenshot(path=str(proof/"mobile.png"),full_page=True)

        # 生产环境常被用 http://内网IP:8000 打开 —— 那不是安全上下文
        # （isSecureContext=false），crypto.randomUUID / navigator.clipboard
        # 在这些地址上根本不存在。direct-creator.js 曾在模块求值时就调
        # crypto.randomUUID，于是整页抛出 TypeError，用户看到的是
        # 「读取创作设置的时候卡住了」（页面里只留下 crypto.randomUUID is not a function）。
        # 这里用一个新 context 把这两个 API 拿掉，模拟真实的生产访问地址。
        insecure=browser.new_context(viewport={"width":1440,"height":1000})
        insecure.add_init_script(
            "Object.defineProperty(globalThis,'isSecureContext',{value:false,configurable:true});"
            "try{delete Crypto.prototype.randomUUID;}catch(e){}"
            "try{Object.defineProperty(navigator,'clipboard',{value:undefined,configurable:true});}catch(e){}")
        insecure_errors=[]
        insecure_page=insecure.new_page()
        insecure_page.set_default_timeout(15000)
        insecure_page.on("pageerror",lambda e:insecure_errors.append(str(e)))
        insecure_page.goto(url+"/views/Production.html")
        # 这条要放在最前面：万一模拟没生效，后面的断言全会"因为环境是安全的"而假过。
        check("模拟生效：该上下文确实没有 crypto.randomUUID",
              insecure_page.evaluate("()=>typeof globalThis.crypto.randomUUID")=="undefined")
        insecure_page.get_by_role('button',name='自定义选题',exact=True).click()
        expect(insecure_page.locator('#creator-topic')).to_be_visible()
        check("非安全上下文下创作设置能读出来、不卡住"
              +("："+" | ".join(e[:160] for e in page_errors(insecure_errors)) if page_errors(insecure_errors) else ""),
              not page_errors(insecure_errors))
        check("非安全上下文下仍生成合法 UUID（后端把 request_id 声明为 UUID）",
              insecure_page.evaluate("async()=>{const m=await import('/assets/app.js?v=20261009-lan1');"
                  "return /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(m.uuid());}"))
        check("剪贴板不可用时复制走回退且不抛错",
              insecure_page.evaluate("async()=>{const m=await import('/assets/app.js?v=20261009-lan1');"
                  "if(m.canWriteClipboard())return false;return typeof await m.copyText('复制回退测试')==='boolean';}"))
        insecure_page.goto(url+f"/views/ReviewPreview.html?content={demo['id']}")
        insecure_page.wait_for_function("document.querySelectorAll('img').length > 0 && [...document.querySelectorAll('img')].some(i=>i.naturalWidth>0)")
        check("非安全上下文下预览页也能加载并恢复调整入口"
              +("："+" | ".join(e[:160] for e in page_errors(insecure_errors)) if page_errors(insecure_errors) else ""),
              not page_errors(insecure_errors) and not insecure_page.locator('#revision-submit').is_disabled())
        # 上面修的是"初始化不再抛错"。万一将来又冒出别的初始化异常，页面也不能永远
        # 停在「读取创作设置」——必须给出可读原因，并让其他页签照常可用。
        # 注意 production.js 是**静态** import 创作模块的，模块本身加载失败连
        # production.js 都评估不了，那是不可捕获的；用户实际遇到的栈是
        # blank → setupDirectCreator → production.js:128，属于"模块已加载、调用时抛错"。
        # 所以这里换成一个全新 context（缓存干净），把创作模块替换成"调用即抛错"的桩。
        degraded=browser.new_context(viewport={"width":1440,"height":1000})
        degraded.route("**/assets/direct-creator.js*", lambda r: r.fulfill(
            status=200, content_type="application/javascript",
            body="export async function setupDirectCreator(){throw new Error('模拟创作设置初始化失败');}"))
        degraded_page=degraded.new_page()
        degraded_page.set_default_timeout(15000)
        degraded_page.goto(url+"/views/Production.html")
        recovered=False
        try:
            degraded_page.wait_for_function(
                "()=>document.querySelector('#creator-error')?.textContent.includes('创作设置读取失败')",
                timeout=8000)
            recovered=True
        except Exception:pass
        reason=(degraded_page.locator('#creator-error').text_content() or "").strip()
        check("创作设置初始化失败时给出可读原因而不是一直转圈"
              +(("：%s" % reason[:70]) if recovered else "（实际提示：%s）" % (reason or "（空，一直停在「读取创作设置」）")),
              recovered and '模拟创作设置初始化失败' in reason)
        check("创作设置初始化失败时状态条写明未能载入",
              '创作设置未能载入' in degraded_page.locator('#creator-model').inner_text())
        degraded_page.get_by_role('tab',name='制作任务',exact=False).click()
        check("创作设置初始化失败不影响其他页签", degraded_page.locator('#tasks-refresh').is_visible())
        degraded.close()
        insecure.close()
        browser.close()
finally:
    for process in reversed(processes):
        process.terminate()
        try:process.wait(timeout=8)
        except subprocess.TimeoutExpired:process.kill()
    for output in outputs:output.close()
    for name in ["api","worker"]:
        shutil.copy2(tmp/f"{name}.log",proof/f"browser-{name}.log")
    server.shutdown();server.server_close()
print(f"{passed} 通过 / 0 失败")
