"""A blocked, versionless item can resume in place after the user adds source material."""
import os,sys,tempfile
from pathlib import Path
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
tmp=Path(tempfile.mkdtemp(prefix='cwb-recovery-'))
os.environ.update(
    CWB_STORAGE_ROOT=str(tmp),
    CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",
    CWB_ARTIFACT_DIR=str(tmp/'artifacts'),
    CWB_TMP_DIR=str(tmp/'tmp'),
    CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'),
)
sys.path.insert(0,str(ROOT/'backend'))

from fastapi.testclient import TestClient
from app.main import app
from app.api.creation import SessionFactory
from app.models.entities import ContentItem,Run,Job

client=TestClient(app)
with SessionFactory() as s:
    item=ContentItem(display_id='C060',topic='OpenAI数学手稿的结果与边界',state='blocked',run_mode='real')
    s.add(item);s.flush()
    failed=Run(content_id=item.id,stage='produce',state='failed',mode='real',blocked_stage='research',error='缺少原文资料')
    s.add(failed);s.commit();content_id=item.id

request_id=str(uuid4())
response=client.post(f'/api/v1/creation/contents/{content_id}/continue',json={
    'request_id':request_id,
    'run_mode':'local_seed',
    'materials':'官方资料：https://github.com/openai/math/blob/main/README.md',
    'requirements':'保留原题；明确区分手稿数量、研究流程与定理边界。',
})
assert response.status_code==202, response.text
queued=response.json()
assert queued['content_id']==content_id
assert queued['state']=='queued'
with SessionFactory() as s:
    run=s.get(Run,queued['run_id'])
    job=s.query(Job).filter_by(run_id=run.id,stage='batch_dispatch').one()
    request=job.output_refs['request']
    assert request['content_id']==content_id
    assert request['topic']=='OpenAI数学手稿的结果与边界'
    assert request['topic_locked'] is True
    assert 'github.com/openai/math/blob/main/README.md' in request['user_materials'][0]['text']
    assert request['user_requirements'].startswith('保留原题')
print('PASS versionless blocked item resumes in place with source material and locked topic')
