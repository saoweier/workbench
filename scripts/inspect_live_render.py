"""Read-only diagnostics of completed public-source test replies."""
import json
import sqlite3
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.services.renderer import check_layout
from app.services.profile_store import ProfileStore
state=json.loads((ROOT/'docs/test-artifacts/live-integration-20261003/request.json').read_text(encoding='utf-8'))
with sqlite3.connect(ROOT/'storage/cwb.db') as db:
    for response,call in db.execute('select response,call from provider_exchange where content_id=?',(state['content_id'],)):
        if not response or not call: continue
        res=json.loads(response); c=json.loads(call)
        if 'variant' not in c.get('prompt_version',''): continue
        data=res.get('parsed') or {}
        platform='xiaohongshu' if 'xiaohongshu' in c['prompt_version'] else 'douyin'
        print(c['prompt_version'],data.get('title'))
        print([(p.get('index'),p.get('layout'),[len(s) for s in p.get('body',[])]) for p in data.get('pages',[])])
        print([i.message for i in check_layout(data,ProfileStore().latest(platform)) if i.level=='error'])
