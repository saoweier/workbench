"""Visible-page observations and fixed creator-backend actions.

The observer reads DOM content only. It never reads cookies, localStorage,
network tokens or browser internals. Files remain in private account storage.
"""
from datetime import datetime, timezone
import re

from .platform_account import write_json


def extract_identity(text):
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    for index, line in enumerate(lines):
        match = re.fullmatch(r"抖音号[：:]\s*([A-Za-z0-9_.-]{1,64})", line)
        if match:
            return {"account_id":match.group(1), "nickname":lines[index-1] if index else None,
                    "data_center_enabled":False if "暂未开通数据中心权限" in text else (True if "数据中心" in lines else None)}
    return None


def observe(page, root):
    data = page.evaluate("""() => {
      const visible = el => !!(el.getClientRects().length) && getComputedStyle(el).visibility !== 'hidden';
      return {
        title:document.title,
        text:document.body.innerText.slice(0,24000),
        links:[...document.querySelectorAll('a')].filter(visible).slice(0,150).map(el=>({text:el.innerText,href:el.href})),
        buttons:[...document.querySelectorAll('button,[role=button]')].filter(visible).slice(0,150).map(el=>({text:el.innerText,label:el.getAttribute('aria-label'),disabled:el.disabled===true})),
        fields:[...document.querySelectorAll('input,textarea,[contenteditable=true]')].slice(0,80).map(el=>({tag:el.tagName,type:el.type||null,placeholder:el.getAttribute('placeholder'),label:el.getAttribute('aria-label'),accept:el.getAttribute('accept'),multiple:el.multiple===true,editable:el.getAttribute('contenteditable'),visible:visible(el)}))
      };
    }""")
    data["url"] = page.url
    data["tabs"] = [p.url.split('?')[0].split('#')[0] for p in page.context.pages]
    data["observed_at"] = datetime.now(timezone.utc).isoformat()
    identity = extract_identity(data["text"])
    if identity:
        identity["observed_at"] = data["observed_at"]
        write_json(root / "identity.json", identity)
    write_json(root / "inspection.json", data)


def handle_command(page, root, command):
    if command.get("action") == "inspect":
        observe(page, root)
        page.screenshot(path=str(root/'inspection.png'),full_page=True)
        return True
    if command.get("action") == "inspect_publish":
        if not page.url.startswith("https://creator.douyin.com/"):
            raise ValueError("当前不是抖音官方后台")
        page.get_by_role("button", name=re.compile(r"^发布图文")).click(timeout=5000)
        page.wait_for_timeout(1500)
        observe(page, root)
        return True
    if command.get("action") == "inspect_content":
        if not page.url.startswith("https://creator.douyin.com/"):
            raise ValueError("当前不是抖音官方后台")
        page.get_by_text("内容管理", exact=True).click(timeout=5000)
        page.wait_for_timeout(1500)
        observe(page, root)
        return True
    if command.get("action") == "inspect_home":
        page.get_by_role("button", name="首页", exact=True).click(timeout=5000)
        page.wait_for_timeout(1500)
        observe(page, root)
        return True
    if command.get("action") == "inspect_declaration":
        from .douyin_editor import dismiss_onboarding
        dismiss_onboarding(page)
        observe(page, root)
        page.get_by_text("请选择自主声明", exact=True).click(timeout=5000)
        page.wait_for_timeout(300)
        observe(page, root)
        return True
    if command.get('action') == 'inspect_declaration_controls':
        data = page.get_by_text('内容由AI生成',exact=True).evaluate_all("""nodes => nodes.map(el => {
          const out=[];let n=el;
          for(let i=0;i<4&&n;i++,n=n.parentElement){const r=n.getBoundingClientRect();out.push({tag:n.tagName,role:n.getAttribute('role'),label:n.getAttribute('aria-label'),text:n.innerText,rect:{x:r.x,y:r.y,width:r.width,height:r.height},inputs:[...n.querySelectorAll('input')].map(a=>({type:a.type,role:a.getAttribute('role'),label:a.getAttribute('aria-label'),checked:a.checked,disabled:a.disabled}))});}return out;
        })""")
        write_json(root/'declaration-controls.json',{'controls':data})
        return True
    if command.get('action') == 'inspect_settings':
        result = {}
        for text in ['公开','立即发布']:
            result[text] = page.get_by_text(text,exact=True).evaluate_all("""nodes => nodes.map(el=>{
                const parent=el.closest('label')||el.parentElement;
                return {tag:parent.tagName,role:parent.getAttribute('role'),text:parent.innerText,
                    inputs:[...parent.querySelectorAll('input')].map(n=>({type:n.type,checked:n.checked,disabled:n.disabled}))};
            })""")
        write_json(root/'settings-controls.json',result)
        return True
    if command.get("action") == "fill_editor":
        import importlib
        from . import douyin_editor
        importlib.reload(douyin_editor)
        douyin_editor.fill_current(page, root, command["task_id"])
        return True
    if command.get("action") == "finish_editor":
        import importlib
        from . import douyin_editor
        importlib.reload(douyin_editor)
        douyin_editor.complete_prepare(page, root, command["task_id"])
        return True
    if command.get("action") == "reload_flow":
        import importlib
        from . import douyin_flow, douyin_publishing
        from ..core.config import get_settings
        importlib.reload(douyin_publishing)
        importlib.reload(douyin_flow)
        # Development refresh preserves the existing editor and task state.
        old = douyin_flow._service
        douyin_flow._service = douyin_publishing.DouyinPublishingService(get_settings())
        if old:
            old.engine.dispose()
        return True
    return False
