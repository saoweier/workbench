"""Explicit, bounded real generation. Never reads or prints API secrets.

Run `queue` once; subsequent `status` operations are read-only.
"""
from pathlib import Path
import json
import sys
import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/test-artifacts/live-integration-20261003'
OUT.mkdir(parents=True, exist_ok=True)
STATE = OUT / 'request.json'

def save(name, data):
    (OUT/name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def main():
    with httpx.Client(base_url='http://127.0.0.1:8000/api/v1', timeout=30) as client:
        def request(method, path, **kw):
            r = client.request(method, path, **kw)
            r.raise_for_status()
            return r.json()
        action = sys.argv[1] if len(sys.argv)>1 else 'status'
        if action == 'queue':
            if STATE.exists():
                raise SystemExit('Request already saved; use status instead of duplicating paid calls.')
            configs = request('GET','/provider-configs')
            text = configs['defaults']['text']
            assert text and text['model_id']=='deepseek-flash' and text['secret_configured']
            request('PATCH',f"/provider-configs/{text['id']}",json={'timeout_seconds':180,'max_output_tokens':8192})
            for cfg in configs['items']:
                if cfg['kind']=='search' and cfg['enabled'] and cfg['base_url'].rstrip('/')=='https://api.deepseek.com':
                    request('PATCH',f"/provider-configs/{cfg['id']}",json={'enabled':False})
            # Public documentation summaries only. No internal files, business
            # records, code, or private project documents leave this machine.
            materials=[
                {'kind':'public_documentation','url':'https://api-docs.deepseek.com/api/create-chat-completion/',
                 'text':'DeepSeek 官方接口文档：response_format 设置为 json_object 可以启用 JSON 输出；提示中仍须明确要求输出 JSON。未明确要求时可能持续生成空白直到令牌上限。JSON 输出讨论的是响应格式，不是运营效果。'},
                {'kind':'public_documentation','url':'https://api-docs.deepseek.com/api/create-chat-completion/',
                 'text':'DeepSeek 官方接口文档：max_tokens 限制一次响应生成的令牌数。finish_reason 为 length 表示生成超过令牌上限或上下文上限，消息可能被截断。不能把截断稿当作完整稿件。'},
                {'kind':'public_documentation','url':'https://api-docs.deepseek.com/guides/thinking_mode/',
                 'text':'DeepSeek 官方思考模式说明：思考模式默认开启，可以通过 thinking.type 设置 enabled 或 disabled。最终回答在 content 字段中，思考内容在 reasoning_content 字段中，两者用途不同。'},
                {'kind':'public_documentation','url':'https://api-docs.deepseek.com/api/create-chat-completion/',
                 'text':'DeepSeek 官方接口文档：模型名称使用 deepseek-flash 或 deepseek-v4-pro。对话请求用 messages 组织角色与内容；模型名称和响应格式属于不同配置项。'},
                {'kind':'editorial_proposal','text':'编辑建议（不是行业统计或亲测结论）：用 AI 写图文时，可把受众问题、核心观点、来源与页内容组织成结构化字段。审稿检查题目是否具体、来源是否支持主张、正文是否完整以及不同平台的表达是否合适。'},
                {'kind':'editorial_proposal','text':'编辑建议（不是已经取得的运营成效）：先预览、检查、修改，再人工发布。发布后的阅读和互动数据应来自平台真实记录；没有获得的数据留空。避免把生成完成误当成发布成功或传播效果保证。'},
            ]
            save('public-source-materials.json',materials)
            batch=request('POST','/batches',json={'item_limit':1,'cost_mode':'usage_tracking','currency':'CNY'})
            result=request('POST',f"/batches/{batch['id']}/runs",json={
                'topic':'面向个人创作者的 AI 图文写作方法。根据提供的公开文档和明确标为建议的编辑提案，自主选出一个具体、有资料支持的实用角度；不写运营效果、亲测或内部项目信息。',
                'run_mode':'real','user_materials':materials,
                'platforms':['douyin','xiaohongshu'],'render':True})
            save('request.json',result)
            print(json.dumps(result,ensure_ascii=False))
        elif action == 'status':
            state=json.loads(STATE.read_text(encoding='utf-8'))
            run=request('GET',f"/runs/{state['run_id']}")
            save('run.json',run)
            print(json.dumps({'run_id':run['id'],'state':run['state'],'error':run.get('error'),
                'blocked_stage':run.get('blocked_stage'),
                'jobs':[{'stage':j['stage'],'state':j['state'],'error':j.get('error')} for j in run['jobs']],
                'provider_calls':run['provider_calls']},ensure_ascii=False))
            if run['state'] not in {'queued','running'}:
                save('content.json',request('GET',f"/contents/{state['content_id']}"))
                save('platforms.json',request('GET',f"/contents/{state['content_id']}/platform-revisions"))
        else:
            raise SystemExit('Use queue or status')

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    main()
