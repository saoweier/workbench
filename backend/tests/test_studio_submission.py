"""Submission regression: isolated SQLite, no worker or paid model calls."""
import os, sys, tempfile, json
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
tmp = Path(tempfile.mkdtemp(prefix='cwb-submission-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp), CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}", CWB_ARTIFACT_DIR=str(tmp/'artifacts'), CWB_TMP_DIR=str(tmp/'tmp'), CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'))
from fastapi.testclient import TestClient
from app.main import app
from app.api import studio, creation
from app.models.entities import Event, ContentItem
client=TestClient(app)
headers={'X-CWB-Local-Action':'account-connection'}
passed=0
def check(name, ok):
 global passed
 assert ok, name
 passed+=1
 print('PASS '+name, flush=True)
def send(body):return client.post('/api/v1/studio/produce', headers=headers, json=body)
body={'request_id':str(uuid4()), 'topic':'如何做一个读书笔记', 'run_mode':'local_seed'}
first=send(body)
check('initial submission queues', first.status_code==202)
second=send(body)
check('repeat returns same job', second.status_code==202 and second.json()['run_id']==first.json()['run_id'] and second.json()['reused'])
changed=send({**body,'topic':'如何做一个旅行清单'})
check('changed topic starts new creation without blocking', changed.status_code==202 and changed.json()['run_id']!=first.json()['run_id'])
check('changed topic repeat does not duplicate', send({**body,'topic':'如何做一个旅行清单'}).json()['run_id']==changed.json()['run_id'])
trend={**body,'request_id':str(uuid4()),'trend_id':'source-1','topic':'热榜新闻怎么理解'}
hit={'id':'source-1','title':trend['topic'],'url':'https://example.org/news','source':'新闻'}
with patch.object(studio.boards,'find',return_value=(hit,{'fetched_at':'2026-10-06T01:00:00','stale':False})):
 hot=send(trend)
check('hot topic queues',hot.status_code==202)
with patch.object(studio.boards,'find',side_effect=RuntimeError('source now unavailable')):
 retry=send(trend)
check('retry needs no upstream lookup',retry.status_code==202 and retry.json()['run_id']==hot.json()['run_id'])
with patch.object(studio.boards,'find',return_value=({**hit,'rank':99},{'fetched_at':'2026-10-07T01:00:00','stale':True})):
 retry=send(trend)
check('upstream timestamp and rank drift cannot duplicate job',retry.status_code==202 and retry.json()['run_id']==hot.json()['run_id'])
race={**trend,'request_id':str(uuid4())}
barrier=Barrier(2)
def concurrent_snapshot(*args):
 barrier.wait(timeout=10)
 return hit,{'fetched_at':str(uuid4()),'stale':False}
with patch.object(studio.boards,'find',side_effect=concurrent_snapshot), ThreadPoolExecutor(max_workers=2) as pool:
 responses=list(pool.map(send,[race,race]))
check('concurrent retries with different live snapshots enqueue exactly once',all(r.status_code==202 for r in responses) and len({r.json()['run_id'] for r in responses})==1)
changed_style=send({**body,'style':'lively'})
check('changed style is new intent',changed_style.status_code==202 and changed_style.json()['run_id']!=first.json()['run_id'])
same_fields=send({**body,'density':'balanced','style':'clean','materials':'','media_ids':[]})
check('omitted defaults and explicit defaults are identical',same_fields.json()['run_id']==first.json()['run_id'])
direct={'creation_key':str(uuid4()),'topic':'直接接口测试','run_mode':'local_seed'}
old=client.post('/api/v1/creation/produce',json=direct)
check('direct creation retains overwrite protection',client.post('/api/v1/creation/produce',json={**direct,'topic':'另一个直接选题'}).status_code==409)
legacy={'request_id':str(uuid4()),'topic':'旧版本草稿恢复','run_mode':'local_seed'}
p=studio.StudioInput(**legacy)
from app.services.content_forms import build_brief
from app.services.content_recipes import apply_recipe
raw=p.requirements+'\n全稿恰好1页，封面计入页数。'
brief=apply_recipe(build_brief(topic=p.topic,requirements=raw),direction=p.direction,template_id=p.template_id)
brief.caption_max=500
old=creation.produce(creation.CreationInput(creation_key=p.request_id,topic=p.topic,requirements=studio.requirements(p),outline=[p.topic],creative_brief=brief,run_mode=p.run_mode))
recovered=send(legacy)
check('legacy identical submission recovers original task',recovered.status_code==202 and recovered.json()['run_id']==old['run_id'])
check('legacy changed topic is not blocked',send({**legacy,'topic':'旧草稿换成新的选题'}).status_code==202)
with creation.SessionFactory() as s:
 check('original intent is never overwritten',s.get(ContentItem,first.json()['content_id']).topic==body['topic'])
print(f'结果：{passed} 通过 / 0 失败')
