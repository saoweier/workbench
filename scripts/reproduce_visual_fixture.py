"""Local deterministic reproduction; no API provider or business DB."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.adapters.fixture import FixtureAdapter
from app.services.provider_contract import ProviderConfig,ProviderKind,AdapterType
from app.services.compose_service import ComposeService,VARIANT_SCHEMA,PlatformDraft
cfg=ProviderConfig(name='local',kind=ProviderKind.TEXT,adapter_type=AdapterType.OPENAI_COMPATIBLE,
    base_url='https://fixture.invalid',model_id='fixture')
adapter=FixtureAdapter(cfg,api_key='fixture')
svc=ComposeService(None,profiles=object())
failures=[]
for n in range(100):
    variants={p:PlatformDraft(platform=p,**adapter.complete(f'平台：{p}\n可用主张 C01',
        json_schema=VARIANT_SCHEMA,request_key=f'case-{n}-{p}').parsed) for p in ['douyin','xiaohongshu']}
    try: svc._assert_variants_distinct(variants)
    except Exception as exc: failures.append((n,str(exc)))
print('Deterministic failures:',failures[:10], 'total:',len(failures))
assert not failures, failures
