"""Bounded real illustration upgrade of C009's already-generated public content.

Uses only the local application API. No secrets, internal source files or private
project documents are included. A saved request prevents duplicate paid queues.
"""
import json
import sys
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/test-artifacts/illustrated-content-20261003'
CID = '8faa1ce5-5253-40eb-b530-0947275c3689'

def save(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def main():
    action = sys.argv[1] if len(sys.argv)>1 else 'status'
    with httpx.Client(base_url='http://127.0.0.1:8000/api/v1', timeout=30) as c:
        def get(path):
            r = c.get(path); r.raise_for_status(); return r.json()
        if action == 'queue':
            if (OUT/'request.json').exists():
                raise SystemExit('Request exists; use status to avoid duplicate paid calls.')
            detail = get(f'/contents/{CID}')
            assert detail['display_id'] == 'C009' and detail['run_mode'] == 'real'
            save('baseline.json', detail)
            r = c.post(f'/contents/{CID}/illustrate', json={
                'base_revision_id':detail['active_revision_id'], 'run_mode':'real'})
            r.raise_for_status(); save('request.json', r.json()); print(r.text)
        elif action == 'retry':
            request = json.loads((OUT/'request.json').read_text(encoding='utf-8'))
            run = get(f"/runs/{request['run_id']}")
            assert run['state']=='failed', 'Only retry a released failed run.'
            r = c.post(f"/runs/{request['run_id']}/control", json={'action':'retry'})
            r.raise_for_status(); save('retry.json',r.json()); print(r.text)
        elif action == 'finalize':
            # Local layout-only revision: reuse AI text, preserve all old PNGs.
            sys.path.insert(0,str(ROOT/'backend'))
            from app.api.production import SessionFactory, _svc
            from app.models.entities import ContentItem, ContentRevision, PlatformRevision, Job, Event
            from app.services.compose_service import MasterDraft, MasterPage, PlatformDraft, ComposeOutcome
            from app.services.provider_contract import RunMode
            from datetime import datetime, timezone
            detail = get(f'/contents/{CID}')
            if (OUT/'finalized.json').exists():
                raise SystemExit('Already finalized; use status.')
            with SessionFactory() as s:
                item=s.get(ContentItem,CID); base=s.get(ContentRevision,item.active_revision_id)
                assert base.version==2
                rows=s.query(PlatformRevision).filter_by(content_revision_id=base.id).all()
                assert all(r.state=='ready_for_review' and r.manifest_hash for r in rows)
                variants={r.platform:PlatformDraft(platform=r.platform,title=r.title,caption=r.caption,
                    pages=r.pages_json['pages']) for r in rows}
                brief=base.brief_json; claims=base.claims_json['claims']; sources=base.claims_json['sources']
                master=MasterDraft(audience_problem=brief['audience_problem'],core_viewpoint=brief['core_viewpoint'],
                    actions=brief.get('actions',[]),claim_ids=[c['id'] for c in claims],run_mode=RunMode.REAL,
                    limitations=[t.replace('原文摘录','公开文档摘要（非逐字原文）') for t in base.limitations_json['limitations']],
                    pages=[MasterPage(index=p['index'],heading=p['heading'],
                        points=[i['detail'] for i in p['visual']['items']],claim_ids=p.get('claim_ids',[])) for p in rows[0].pages_json['pages']])
                profiles={r.platform:_svc.profiles.get(r.profile_version_id) for r in rows}
                old_id=base.id
            _svc.compose._persist(CID,ComposeOutcome(master=master,variants=variants,claims=claims,sources=sources,
                run_mode=RunMode.REAL,model_used=True),profiles=profiles,run_mode=RunMode.REAL)
            new_detail=get(f'/contents/{CID}')
            active=next(r for r in new_detail['revisions'] if r['revision_id']==new_detail['active_revision_id'])
            assert active['version']==3
            for pr in active['platforms']:
                r=c.post(f"/platform-revisions/{pr['platform_revision_id']}/render",json={})
                r.raise_for_status(); assert r.json()['ok'],r.text
                save(f"render-{pr['platform']}.json",r.json())
            request=json.loads((OUT/'request.json').read_text(encoding='utf-8'))
            with SessionFactory() as s:
                # The saved composition checkpoint already completed. A retry had
                # left its status running; reconcile that status without altering
                # the old revision, supplier response or historic manifest.
                job=s.query(Job).filter_by(run_id=request['run_id'],stage='compose').one()
                assert job.output_refs['revision_id']==old_id
                if job.state=='running':
                    job.state='succeeded';job.finished_at=datetime.now(timezone.utc)
                s.add(Event(entity_type='content_item',entity_id=CID,type='illustration_layout_upgraded',
                    actor='coisini',run_mode='real',payload={'base_revision_id':old_id,'revision_id':active['revision_id'],
                        'template_version':3,'new_model_calls':0,'note':'复用已完成的 AI 图文，修复语义断行与长示例排版；资料摘要标注更正'}))
                s.commit()
            save('finalized.json',{'revision_id':active['revision_id'],'version':3,'new_model_calls':0})
            save('content.json',get(f'/contents/{CID}'))
            print('Final illustrated revision v3 rendered. No new model calls.')
        elif action == 'status':
            request = json.loads((OUT/'request.json').read_text(encoding='utf-8'))
            run = get(f"/runs/{request['run_id']}")
            save('run.json', run)
            print(json.dumps({'state':run['state'],'blocked_stage':run.get('blocked_stage'),
                'error':run.get('error'), 'jobs':[{'stage':j['stage'],'state':j['state'],'error':j.get('error'),
                    'output_refs':j.get('output_refs') if j['state']=='failed' else None} for j in run['jobs']],
                'calls':[{'stage':p.get('stage_prompt_version'),'state':p['state']} for p in run['provider_calls']]},ensure_ascii=False))
            save('content.json',get(f'/contents/{CID}'))
        else:
            raise SystemExit('Use queue or status')

if __name__ == '__main__':
    main()
