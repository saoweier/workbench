"""Read-only saved-session revisit dispatch from the product Worker."""
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4
from sqlalchemy import select
from ..models.entities import DouyinPublishTask,_now
from .platform_account import PlatformAccountService,read_json,write_json

def dispatch_due(sf,settings):
    from .publishing_preferences import preferences
    background=preferences(settings)['background_revisit']
    accounts=PlatformAccountService(settings.storage_root)
    if accounts.status()['browser_open']:
        return False  # The live helper owns the profile and observes due tasks itself.
    status=read_json(accounts.root/'status.json')
    if status.get('state') in {'waiting_login','needs_attention','error'}:
        return False  # User reconnection is required; stop expired-session retries.
    if not status.get('last_verified_at') or not read_json(accounts.root/'identity.json').get('account_id'):
        return False  # No saved user login; never launch a new authentication flow.
    with sf() as s:
        active=s.scalar(select(DouyinPublishTask.id).where(DouyinPublishTask.state.in_(
            ['upload_requested','uploading','awaiting_editor','awaiting_confirmation','publish_requested','submitting'])))
        due=next((task.id for task in s.scalars(select(DouyinPublishTask).where(DouyinPublishTask.next_observation_at<=_now()))
            if background or (task.result_json or {}).get('manual_revisit_requested')),None)
    if active or not due:
        return False
    stamp=read_json(accounts.root/'scheduler.json')
    if time.time()-stamp.get('attempted_at',0)<300:
        return False
    # Profile OS lock in the helper also serializes independently started Workers.
    write_json(accounts.root/'scheduler.json',{'id':uuid4().hex,'attempted_at':time.time()})
    root=Path(__file__).resolve().parents[3]
    options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
    subprocess.Popen([sys.executable,'-X','utf8',str(root/'scripts/connect_douyin.py'),
        '--storage',str(accounts.root),'--read-only'],cwd=root,
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**options)
    return True
