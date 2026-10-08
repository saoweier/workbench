"""Read-only inspection of illustrated responses; never sends requests."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.api.production import SessionFactory, _svc
from app.models.entities import ProviderExchange, ContentRevision
with SessionFactory() as s:
    cid = '8faa1ce5-5253-40eb-b530-0947275c3689'
    revision = s.query(ContentRevision).filter_by(content_id=cid).order_by(ContentRevision.version).first()
    known = {c['id'] for c in revision.claims_json['claims']}
    for row in s.query(ProviderExchange).filter_by(content_id=cid).all():
        version = (row.call or {}).get('prompt_version','')
        if not version.endswith('v3-illustrated') or not row.response: continue
        print(version)
        raw = row.response.get('parsed')
        if not raw:
            print(row.response.get('error_code'),row.response.get('error_message'))
            print(str(row.response.get('text') or '')[:500])
            continue
        platform = 'douyin' if '.douyin.' in version else 'xiaohongshu'
        try:
            _svc.compose._validate_variant(raw,known=known,profile=_svc.profiles.latest(platform),platform=platform)
            print('VALID', len(raw.get('caption','')), [(p.get('visual',{}).get('kind'),p.get('heading')) for p in raw.get('pages',[])])
        except Exception as exc:
            print(type(exc).__name__,str(exc))
            print('keys:',list(raw), 'type:',raw.get('type'))
            print('page:',str(raw.get('pages',[])[:1])[:500])
