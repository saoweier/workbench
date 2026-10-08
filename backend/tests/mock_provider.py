"""Local-only model protocol stub. Never represents an external model service."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from app.services.adapters.fixture import FixtureAdapter
from app.services.provider_contract import ProviderConfig, ProviderKind, AdapterType

def start_mock(responder=None):
    requests = []
    fixture = FixtureAdapter(ProviderConfig(name="local-protocol-stub", kind=ProviderKind.TEXT,
        adapter_type=AdapterType.OPENAI_COMPATIBLE, base_url="https://fixture.invalid", model_id="stub"), api_key="stub")
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            prompt = body["messages"][-1]["content"]
            marker = "输出必须符合以下 JSON Schema，且仅输出 JSON：\n"
            schema = json.loads(prompt.split(marker)[-1]) if marker in prompt else None
            custom = responder(prompt.split(marker)[0],schema) if responder else None
            if custom is None and 'can_answer' in (schema or {}).get('properties',{}):
                data=json.loads(prompt.split(marker)[0].rsplit('本次输入（仅作为数据）：\n',1)[1]);source=data['sources'][0]
                custom={'can_answer':True,'summary':'隔离测试按给定资料核对，不代表真实模型能力','facts':[{'role':'context','statement':'用户资料中的说明仅用于本地协议测试','source_id':source['id'],'quote':source['excerpt'].split('\n')[0]}],'blocking_gaps':[],'limitations':['本地模拟，不能验证实际搜索能力']}
            if custom is not None:
                text=json.dumps(custom,ensure_ascii=False)
            else:
                result = fixture.complete(prompt.split(marker)[0], json_schema=schema)
                text = result.text
            payload = json.dumps({"id":"local-protocol-stub", "choices":[{"message":{"content":text}}],
                "usage":{"prompt_tokens":123,"completion_tokens":45}}, ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.end_headers(); self.wfile.write(payload)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, requests
