"""An autonomous choice must become the content topic and compose input."""
from pathlib import Path
import os
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-autonomy-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",
    CWB_ARTIFACT_DIR=str(tmp/'artifacts'),CWB_TMP_DIR=str(tmp/'tmp'),CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'))
from app.worker import build_session_factory
from app.models.entities import Base,ContentItem,Event
from app.services.production_service import ProductionService
from app.services.provider_contract import RunMode,ProviderStore,SecretStore
from app.services.provider_runtime import ProviderRuntime
from app.services.topic_service import TopicCandidate

settings,sf=build_session_factory()
Base.metadata.create_all(sf.kw['bind'])
svc=ProductionService(sf,runtime=ProviderRuntime(ProviderStore(tmp/'providers.json'),SecretStore(tmp/'secrets.json')))
picked='如何区分成品批准与真实发布'
svc.topics.propose=lambda **kw:[TopicCandidate(id='T01',topic=picked,audience_problem='审批后还要做什么',
    supporting_claim_ids=['C01'],selection_reason='来源有明确边界',hard_conditions={'supported':True},rule_scores={'fit':4})]
captured=[]
def compose(**kw):
    captured.append(kw['topic'])
    return types.SimpleNamespace(variants={},repair_round=0,model_used=False,notes=[])
svc.compose.compose=compose
svc._active_revision_id=lambda cid:'not-rendered'
result=svc.produce(topic='个人创作者的图文工作流',seed_path=str(ROOT/'examples/C001/seeds/C001/seed.json'),run_mode=RunMode.LOCAL_SEED,render=False)
assert captured==[picked],f"母稿仍用了大方向而不是自主选题：{captured}"
with sf() as s:
    item=s.get(ContentItem,result['content_id'])
    assert item.topic==picked and item.selected_by=='ai'
    assert s.query(Event).filter_by(entity_id=item.id,type='topic_selected_ai').count()==1
from app.services.compose_service import MasterDraft, PAGE_STRATEGY
from app.services.profile_store import engineering_default
prompts=[]
def complete_text(**kw):
    prompts.append(kw['prompt'])
    return None,types.SimpleNamespace(ok=True,parsed={'title':'格式检查','caption':'预览后再发布','pages':[]})
svc.compose.runtime=types.SimpleNamespace(complete_text=complete_text)
pf=engineering_default('douyin')
svc.compose._generate_variant(MasterDraft(audience_problem='生成后如何检查',core_viewpoint='检查完整性'),
    'douyin',PAGE_STRATEGY['douyin'],content_id='isolated',run_mode=RunMode.REAL,
    repair_feedback=None,round_no=0,lim=pf.limits,render_profile=pf.render)
assert '封面正文每行最多 17' in prompts[0]
assert '内页正文每行最多 25' in prompts[0]
assert '标题最多 20' in prompts[0] and '正文最多 1000' in prompts[0]
from fastapi.testclient import TestClient
from app.main import app
from app.models.entities import Job,Run,ProviderExchange
client=TestClient(app)
bid=client.post('/api/v1/batches',json={}).json()['id']
queued=client.post(f'/api/v1/batches/{bid}/runs',json={'topic':'恢复检查'}).json()
endpoint=f"/api/v1/runs/{queued['run_id']}/control"
assert client.post(endpoint,json={'action':'retry'}).status_code==409
with sf() as s:
    j=s.get(Job,queued['queued_job_id']);j.state='failed'
    s.get(Run,queued['run_id']).state='failed'
    s.add(ProviderExchange(request_key='test-unknown',content_id=queued['content_id'],state='unknown'))
    s.commit()
assert client.post(endpoint,json={'action':'retry'}).status_code==409
with sf() as s:
    s.get(ProviderExchange,'test-unknown').state='succeeded';s.commit()
response=client.post(endpoint,json={'action':'retry'})
assert response.status_code==200 and response.json()['state']=='queued'
assert client.post(endpoint,json={'action':'retry'}).status_code==409
print('10 通过 / 0 失败')
