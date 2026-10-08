"""Clear any content state using an isolated DB; never touches live content."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
temp = Path(tempfile.mkdtemp(prefix='cwb-discard-'))
os.environ.update(CWB_STORAGE_ROOT=str(temp), CWB_DATABASE_URL=f"sqlite:///{temp/'test.db'}",
                 CWB_ARTIFACT_DIR=str(temp/'artifacts'), CWB_TMP_DIR=str(temp/'tmp'),
                 CWB_SECRET_STORE_PATH=str(temp/'secrets.json'))
from fastapi.testclient import TestClient
from app.main import app
from app.api.contents import SessionFactory as SF, _svc
from app.models.entities import ContentItem, ContentRevision, PlatformRevision, Publication, Run, Job, Event, ProviderCallRow
from app.core.config import get_settings
from app.core.errors import TaskStopped
from app.worker import Worker

client = TestClient(app)
passed = []
def check(name, condition):
    assert condition, name
    passed.append(name)
    print('PASS ' + name, flush=True)

with SF() as s:
    item = ContentItem(display_id='DEMO-PUBLISHED', topic='Published fixture', state='approved', run_mode='fixture')
    s.add(item); s.flush()
    revision = ContentRevision(content_id=item.id, input_hash='published-fixture')
    s.add(revision); s.flush()
    platform = PlatformRevision(content_revision_id=revision.id, platform='douyin', title='Fixture',
                                caption='Fixture', pages_json={'pages': []}, content_hash='fixture', state='approved')
    s.add(platform); s.flush()
    item.active_revision_id = revision.id
    publication = Publication(platform_revision_id=platform.id, platform='douyin', link='https://example.invalid/post', run_mode='fixture')
    s.add(publication); s.commit()
    published_id, publication_id = item.id, publication.id

response = client.post(f'/api/v1/contents/{published_id}/discard', json={})
check('published content can be cleared with unchanged response shape', response.status_code == 200 and response.json()['state'] == 'discarded' and response.json()['released'])
check('cleared published content disappears from default list', published_id not in {c['id'] for c in client.get('/api/v1/contents').json()['items']})
check('publication remains readable with its original content reference', publication_id in {p['id'] for p in client.get('/api/v1/publications', params={'content_id': published_id}).json()['items']})
check('cleared history remains explicitly queryable', published_id in {c['id'] for c in client.get('/api/v1/contents?include_discarded=1').json()['items']})
repeat = client.post(f'/api/v1/contents/{published_id}/discard', json={})
check('repeated clear stays idempotent', repeat.status_code == 200 and repeat.json()['already'] and not repeat.json()['released'])
with SF() as s:
    check('published clear preserves revisions and a single audit event', s.query(ContentRevision).filter_by(content_id=published_id).count() == 1 and s.query(Event).filter_by(entity_id=published_id, type='content_discarded').count() == 1)

for state in ['selected', 'queued', 'running', 'paused', 'blocked', 'failed', 'ready_for_review', 'partially_approved', 'exported', 'published']:
    with SF() as s:
        item = ContentItem(display_id='STATE-' + state, topic='State fixture', state=state)
        s.add(item); s.commit(); cid = item.id
    response = client.post(f'/api/v1/contents/{cid}/discard', json={})
    check('clear is available in state ' + state, response.status_code == 200 and response.json()['state'] == 'discarded')

with SF() as s:
    active = ContentItem(display_id='ACTIVE', topic='Running fixture', state='queued')
    s.add(active); s.flush()
    run = Run(content_id=active.id, stage='produce', mode='fixture', state='running')
    s.add(run); s.flush()
    request = {'content_id': active.id, 'topic': active.topic, 'run_mode': 'fixture', 'platforms': ['douyin']}
    job = Job(run_id=run.id, stage='batch_dispatch', state='running', input_hash='active', fencing_token=1, output_refs={'request': request})
    s.add(job); s.commit()
    active_id, run_id, job_id = active.id, run.id, job.id
worker = Worker(SF, get_settings(), worker_id='discard-test')
def clear_during_production(**_):
    result = client.post(f'/api/v1/contents/{active_id}/discard', json={})
    assert result.status_code == 200
    raise TaskStopped('cancelled')
with patch.object(worker.production, 'produce', side_effect=clear_during_production):
    result = worker._run_job({'job_id': job_id, 'stage': 'batch_dispatch', 'fencing_token': 1})
with SF() as s:
    check('clear cancels running jobs and runs', s.get(Run, run_id).state == 'cancelled' and s.get(Job, job_id).state == 'cancelled')
    check('worker cancellation never restores cleared content', result['stopped'] == 'cancelled' and s.get(ContentItem, active_id).state == 'discarded')

with SF() as s:
    _svc._recompute_content_state(s, s.get(ContentItem, published_id)); s.commit()
    check('late platform approval does not restore cleared content', s.get(ContentItem, published_id).state == 'discarded')
with SF() as s:
    old_run = s.get(Run, run_id); s.expunge(old_run)
worker.production._block({}, old_run, active_id, 'compose', ValueError('Late failure'))
with SF() as s:
    check('late stage failure does not restore cleared content', s.get(ContentItem, active_id).state == 'discarded')

from app.services.content_lifecycle import update_content_state
with SF() as s:
    other = ContentItem(display_id='STALE', topic='Stale state fixture', state='selected')
    s.add(other); s.commit(); stale_id = other.id
with SF() as stale:
    cached = stale.get(ContentItem, stale_id)
    client.post(f'/api/v1/contents/{stale_id}/discard', json={})
    changed = update_content_state(stale, cached, 'drafting'); stale.commit()
    check('stale session state update cannot undo clear', not changed)
with SF() as s:
    check('cleared state survives stale session commit', s.get(ContentItem, stale_id).state == 'discarded')
    other = ContentItem(display_id='NORMAL', topic='Normal state fixture', state='selected')
    s.add(other); s.commit()
    check('normal content state transitions still work', update_content_state(s, other, 'ready_for_review'))
    s.commit()
    check('normal state transition synchronizes loaded object', other.state == 'ready_for_review')
check('missing content still returns 404', client.post('/api/v1/contents/missing/discard', json={}).status_code == 404)
check('untrusted actor still rejected', client.post(f'/api/v1/contents/{published_id}/discard', json={'actor': 'system'}).status_code == 422)
with SF() as s:
    check('no provider calls were made', s.query(ProviderCallRow).count() == 0)
print(f'{len(passed)} passed / 0 failed')
