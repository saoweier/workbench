"""Isolated delivery checks: actual queue execution and local HTTP protocol."""
from pathlib import Path
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
tmp = Path(tempfile.mkdtemp(prefix="cwb-delivery-"))
os.environ.update(CWB_STORAGE_ROOT=str(tmp), CWB_DATABASE_URL=f"sqlite:///{tmp / 'test.db'}",
    CWB_ARTIFACT_DIR=str(tmp / "artifacts"), CWB_TMP_DIR=str(tmp / "tmp"),
    CWB_SECRET_STORE_PATH=str(tmp / "secrets.json"), CWB_PENDING_REVIEW_STOCK_LIMIT="20")
from fastapi.testclient import TestClient
from app.main import app
from app.worker import Worker, build_session_factory
from app.models.entities import Artifact, ContentRevision, ProviderCallRow, ProviderExchange, Job, Run
from app.core.errors import StateConflict
from datetime import datetime, timedelta, timezone
from app.services.adapters.fixture import FixtureAdapter
from app.services.provider_contract import ProviderConfig, ProviderKind, AdapterType, RunMode, ProviderStore, SecretStore
from app.services.provider_runtime import ProviderRuntime

passed = 0
def check(name, condition):
    global passed
    assert condition, name
    passed += 1
    print("PASS " + name)

client = TestClient(app)
settings, sf = build_session_factory()
worker = Worker(sf, settings)
seed = str(ROOT / "examples/C001/seeds/C001/seed.json")
bid = client.post("/api/v1/batches", json={}).json()["id"]
queued = client.post(f"/api/v1/batches/{bid}/runs", json={"topic":"队列执行验收", "seed_path":seed}).json()
with sf() as s:
    check("入队没有提前生成图片", s.query(Artifact).count() == 0)
    check("入队没有提前生成稿件", s.query(ContentRevision).count() == 0)
check("后台真实执行生产", worker.tick()["processed"] == 1)
detail = client.get(f"/api/v1/contents/{queued['content_id']}").json()
check("双平台渲染完成后主条目进入待预览", detail['state']=='ready_for_review')
prs = detail["revisions"][0]["platforms"]
check("后台产出两平台稿件", len(prs) == 2)
check("后台产出十一张可读页图", sum(len(p["artifacts"]) for p in prs) == 11)
for pr in prs:
    response = client.get(f"/api/v1/platform-revisions/{pr['platform_revision_id']}/pages/1")
    check("页图是真实PNG", response.content.startswith(b"\x89PNG"))
check("再次扫描不重复执行", worker.tick()["processed"] == 0)
decision=client.post("/api/v1/review-decisions",json={"decision":"approve","actor":"coisini","targets":[
    {"platform_revision_id":prs[0]["platform_revision_id"],"expected_manifest_hash":prs[0]["manifest_hash"]},
    {"platform_revision_id":prs[1]["platform_revision_id"],"expected_manifest_hash":"wrong-version"}]}).json()
updated=client.get(f"/api/v1/contents/{queued['content_id']}").json()["revisions"][0]["platforms"]
check("批量审批错版时整组回滚",not decision["ok"] and all(p["state"]=="ready_for_review" for p in updated))
with sf() as s:
    j=s.get(Job,queued["queued_job_id"])
    j.state="running";j.lease_owner="interrupted-worker";j.lease_expires_at=datetime.now(timezone.utc)-timedelta(seconds=10)
    # Simulate termination after rendering, before dispatcher release.
    s.get(Run,j.run_id).state="running";s.commit()
check("中断后租约过期任务恢复执行",worker.tick()["processed"]==1)
with sf() as s:
    check("中断恢复复用阶段，不重复稿件与图片",s.query(ContentRevision).count()==1 and s.query(Artifact).count()==11)
    check("复用后的阶段全部完成",all(j.state=="succeeded" for j in s.query(Job).all()))
bid2=client.post("/api/v1/batches",json={}).json()["id"]
control=client.post(f"/api/v1/batches/{bid2}/runs",json={"topic":"控制操作","seed_path":seed,"render":False}).json()
check("排队任务可暂停",client.post(f"/api/v1/runs/{control['run_id']}/control",json={"action":"pause"}).json()["state"]=="paused")
check("暂停任务不会被执行",worker.tick()["processed"]==0)
check("暂停任务可继续",client.post(f"/api/v1/runs/{control['run_id']}/control",json={"action":"resume"}).json()["state"]=="queued")
check("继续后真实执行",worker.tick()["processed"]==1)
bid3=client.post("/api/v1/batches",json={}).json()["id"]
cancel=client.post(f"/api/v1/batches/{bid3}/runs",json={"topic":"取消操作","seed_path":seed}).json()
check("排队任务可取消",client.post(f"/api/v1/runs/{cancel['run_id']}/control",json={"action":"cancel"}).json()["state"]=="cancelled")
check("取消后不执行",worker.tick()["processed"]==0)
check("未知运行模式被拒绝", client.post(f"/api/v1/batches/{bid}/runs", json={"topic":"a","run_mode":"typo"}).status_code == 422)

requests = []
fixture = FixtureAdapter(ProviderConfig(name="protocol-stub",kind=ProviderKind.TEXT,
    adapter_type=AdapterType.OPENAI_COMPATIBLE,base_url="https://fixture.invalid",model_id="stub"), api_key="stub")
class Server(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        requests.append({"path":self.path,"auth":self.headers.get("Authorization"),"body":body})
        messages = body["messages"]
        prompt = messages[-1]["content"]
        marker = "输出必须符合以下 JSON Schema，且仅输出 JSON：\n"
        schema = json.loads(prompt.split(marker)[-1]) if marker in prompt else None
        res = fixture.complete(prompt.split(marker)[0], system=messages[0]["content"] if len(messages)>1 else None,
                               json_schema=schema)
        content = json.dumps({"id":"local-http-stub", "choices":[{"message":{"content":res.text}}],
                              "usage":{"prompt_tokens":123,"completion_tokens":45}},ensure_ascii=False).encode()
        self.send_response(200);self.send_header("Content-Type","application/json");self.end_headers();self.wfile.write(content)

server = ThreadingHTTPServer(("127.0.0.1",0), Server)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    saved = client.post("/api/v1/provider-configs", json={"name":"local-protocol-test","kind":"text",
        "adapter_type":"openai_compatible","base_url":f"http://127.0.0.1:{server.server_port}/v1",
        "model_id":"stub","enabled":True,"api_key":"test-secret-only","allow_localhost":True}).json()
    check("保存API配置不发送请求", len(requests)==0)
    bid = client.post("/api/v1/batches",json={}).json()["id"]
    queued = client.post(f"/api/v1/batches/{bid}/runs",json={"topic":"本地协议模拟全链路","seed_path":seed,"run_mode":"real"}).json()
    result = worker.tick()
    run = client.get(f"/api/v1/runs/{queued['run_id']}").json()
    print("HTTP-run",result,run) if not requests else None
    check("启动后新增API配置立即生效", len(requests)>0)
    if run['state'] != 'succeeded':
        print('DELIVERY_RUN_FAILURE',json.dumps(run,ensure_ascii=False))
    check("实际HTTP模型链路完整执行",run["state"]=="succeeded")
    check("实际发送Bearer认证",all(r["auth"]=="Bearer test-secret-only" for r in requests))
    check("结构要求实际传递给模型",all("输出必须符合" in r["body"]["messages"][-1]["content"] for r in requests))
    with sf() as s:
        rows=s.query(ProviderCallRow).filter_by(run_mode="real").all()
        check("每次请求都有用量账目",len(rows)==len(requests) and all(r.input_tokens==123 for r in rows))
        check("未知价格没有写成零",all(r.estimated_micro is None for r in rows))
    runtime=worker.production.runtime
    count=len(requests)
    runtime.complete_text(prompt="journal-cache", content_id=queued["content_id"],prompt_version="cache.v1",run_mode=RunMode.REAL)
    runtime.complete_text(prompt="journal-cache", content_id=queued["content_id"],prompt_version="cache.v1",run_mode=RunMode.REAL)
    check("重放复用持久结果，不重复网络调用",len(requests)==count+1)
    with sf() as s:
        exchange=s.query(ProviderExchange).filter_by(content_id=queued["content_id"]).first()
        exchange.state="unknown";s.commit()
    cfg=runtime.text_provider()
    cfg.model_id="updated-model";runtime.store.upsert(cfg)
    call,res=runtime.complete_text(prompt="changed-config",content_id=queued["content_id"],run_mode=RunMode.REAL)
    check("更新配置后使用新模型",requests[-1]["body"]["model"]=="updated-model")
    check("磁盘账目持久化",call.input_tokens==123)
    runtime.complete_text(prompt="unknown-replay",content_id=queued["content_id"],run_mode=RunMode.REAL)
    with sf() as s:
        exchange=s.query(ProviderExchange).filter_by(request_key=runtime.complete_text(
            prompt="unknown-replay",content_id=queued["content_id"],run_mode=RunMode.REAL)[0].request_key).one()
        exchange.state="unknown";s.commit()
    count=len(requests)
    try:
        runtime.complete_text(prompt="unknown-replay",content_id=queued["content_id"],run_mode=RunMode.REAL)
        blocked=False
    except StateConflict:
        blocked=True
    check("结果未知不会自动再次出网",blocked and len(requests)==count)
    bid=client.post("/api/v1/batches",json={"cost_mode":"hard_cap","budget_limit_micro":100}).json()["id"]
    limited=client.post(f"/api/v1/batches/{bid}/runs",json={"topic":"硬限额测试","seed_path":seed,"run_mode":"real"}).json()
    worker.tick()
    check("缺费率时硬限额在出网前停止",len(requests)==count and client.get(f"/api/v1/runs/{limited['run_id']}").json()["state"]=="failed")
finally:
    server.shutdown();server.server_close()
print(f"{passed} 通过 / 0 失败")
