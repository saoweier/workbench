"""交付物清单：来源快照 / 筛选记录 / 逐页文案 / 视觉提示 / 成品图 / 专项验收，程序可核对。"""
import json,os,sqlite3,sys,tempfile,zipfile
from copy import deepcopy
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-deliver-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",
                  CWB_ARTIFACT_DIR=str(tmp/'artifacts'),CWB_TMP_DIR=str(tmp/'tmp'),
                  CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'),CWB_PENDING_REVIEW_STOCK_LIMIT='40')

from fastapi.testclient import TestClient
from app.main import app
from app.api.production import SessionFactory
from app.api.creation import runtime
from app.models.entities import Artifact,ContentItem,PlatformRevision
from app.services.production_service import ProductionService
from app.services.deliverables import DELIVERABLE_KEYS,build

client=TestClient(app);headers={'X-CWB-Local-Action':'account-connection'}
passed=0
def check(name,value):
    global passed
    assert value,name
    passed+=1;print('PASS '+name,flush=True)

produced=ProductionService(SessionFactory,runtime=runtime()).produce_from_seed(render=True)
cid=produced['content_id']
with SessionFactory() as s:
    rev_id=s.get(ContentItem,cid).active_revision_id

body=client.get('/api/v1/contents/'+cid+'/deliverables').json()
check('清单覆盖内容/版本/简报/来源/主张/平台/交付物/验收',
      {'content','revision','brief','sources','claims','platforms','deliverables','acceptance'}.issubset(body))
check('清单覆盖两个平台',{p['platform'] for p in body['platforms']}=={'douyin','xiaohongshu'})
rows={r['key']:r for r in body['deliverables']}
check('每个平台都有六类交付物 + 专项验收',
      {k.split('.',1)[1] for k in rows}==set(DELIVERABLE_KEYS))
check('来源快照逐条列出（含访问状态）',
      bool(body['sources']) and all({'id','kind','access_state','locator'} <= set(x) for x in body['sources']))
check('筛选记录写明选题理由与主张编号',
      bool(rows['douyin.screening']['actual']) and '选题理由' in rows['douyin.screening']['note'])
check('逐页文案逐条可读（每页有标题且有条目或正文）',
      all(p['pages'] and all((x['heading'] or '').strip() and (x['body'] or x['visual']['items'])
                             for x in p['pages']) for p in body['platforms']))
check('视觉提示：带图解的页必须有类型与标题',
      all(x['visual']['kind'] and x['visual']['title']
          for p in body['platforms'] for x in p['pages'] if x['visual']['kind']))
check('成品图数量与页数一致（导出契约）',
      all(p['images']['actual']==p['images']['expected']==len(p['pages']) for p in body['platforms'])
      and all(rows[k]['passed'] for k in rows if k.endswith('.images')))
check('发布文案行给出实际字数',
      all('字' in rows[f'{p}.copy_text']['actual'] for p in ('douyin','xiaohongshu')))
check('清单整体通过且没有问题项',body['acceptance']['passed'] and not body['acceptance']['problems'])
check('非榜单题材不硬套榜单契约',all(p['acceptance']['rank'] is None for p in body['platforms']))

# 榜单题材才挂榜单专项验收（条目数 / 名次顺序 / 指标对应项目 / 名次版面）
rank_page={'index':1,'layout':'cover','heading':'榜单','body':[],'claim_ids':[],
           'visual':{'kind':'rank','title':'榜单','takeaway':'',
                     'items':[{'label':f'对象{n}','rank':n,'detail':'入选理由','icon':'page'} for n in range(1,11)]}}
with SessionFactory() as s:
    pr=s.query(PlatformRevision).filter_by(content_revision_id=rev_id,platform='douyin').one()
    original=deepcopy(pr.pages_json)
    pr.pages_json={**original,'form':'ranking','rank_count':10,'pages':[deepcopy(rank_page)]}
    s.commit()
ranked=build(SessionFactory,cid,platform='douyin',revision_id=rev_id)
ranked_platform=ranked['platforms'][0]
check('榜单题材的清单带榜单专项验收',bool(ranked_platform['acceptance']['rank']))
check('榜单验收覆盖四项（版面/条目/顺序/指标）',
      {'rank_board','rank_count','rank_order','rank_metric'} <=
      {c['key'] for c in ranked_platform['acceptance']['rank']['checks']})
check('榜单条目数与名次顺序都对得上',ranked_platform['acceptance']['rank']['passed'])
check('榜单条目被计入逐页文案清单',
      sum(len(x['visual']['items']) for x in ranked_platform['pages'])==10)
with SessionFactory() as s:
    pr=s.query(PlatformRevision).filter_by(content_revision_id=rev_id,platform='douyin').one()
    pr.pages_json=original;s.commit()

# 导出包必须带同一份清单：导出的是"这一版到底产出了什么"
with SessionFactory() as s:
    prs=s.query(PlatformRevision).filter_by(content_revision_id=rev_id).all()
    targets=[{'platform_revision_id':p.id,'expected_manifest_hash':p.manifest_hash} for p in prs]
    douyin_pr=next(p.id for p in prs if p.platform=='douyin')
approval=client.post('/api/v1/review-decisions',
                      json={'decision':'approve','actor':'coisini','targets':targets}).json()
check('批准记录成功',approval.get('ok') is True)
pack=client.post('/api/v1/packages',json={'platform_revision_ids':[douyin_pr]}).json()
check('发布包 manifest 带交付物清单与验收结论',
      bool(pack['manifest'].get('deliverables')) and 'acceptance' in pack['manifest']
      and bool(pack['manifest'].get('source_snapshot')))
check('manifest 的交付物行可直接核对',
      all(r.get('expected')!='' and 'passed' in r for r in pack['manifest']['deliverables']))
with zipfile.ZipFile(Path(pack['zip_path'])) as z:
    names=z.namelist()
    checklist=z.read('checklist.md').decode('utf-8')
    inzip=json.loads(z.read('manifest.json').decode('utf-8'))
check('发布包含图片/文案/清单/manifest',
      any(n.endswith('.png') for n in names) and {'caption.txt','checklist.md','manifest.json'} <= set(names))
check('checklist.md 列出逐项交付物表格', '交付物清单（程序核对）' in checklist and '| 交付物 |' in checklist)
check('checklist.md 保留人工核对项', '手动上传后回填发布记录' in checklist)
check('包内 manifest 与接口清单同源', inzip['deliverables']==pack['manifest']['deliverables'])

# 产物缺失必须体现在清单里：交付物数量对不上就不能算通过
with SessionFactory() as s:
    victim=s.query(Artifact).filter_by(platform_revision_id=douyin_pr).order_by(Artifact.page_index).first()
    s.delete(victim);s.commit()
broken=build(SessionFactory,cid,platform_revision_id=douyin_pr)
img_row=next(r for r in broken['deliverables'] if r['key'].endswith('.images'))
check('缺一张成品图 → 成品图行不通过',not img_row['passed'])
check('缺图使整体验收不通过',
      not broken['acceptance']['passed'] and any('张' in p for p in broken['acceptance']['problems']))
check('清单写明缺哪一页','缺页' in img_row['note'])

# 没有版本的内容给可读错误，而不是 500
db=sqlite3.connect(str(tmp/'test.db'))
db.execute("insert into content_item (id,display_id,topic,selected_by,state,run_mode,created_at)"
           " values (?,?,?,?,?,?,?)",
           ('22222222-2222-2222-2222-222222222222','C900','没有版本','user','blocked','real','2026-10-04 08:00:00'))
db.commit();db.close()
missing=client.get('/api/v1/contents/22222222-2222-2222-2222-222222222222/deliverables')
detail=missing.json().get('error',{}).get('message','')
check('没有版本的内容返回 404 而不是 500',missing.status_code==404 and '版本' in detail)
check('不存在的内容也返回 404',client.get('/api/v1/contents/not-a-content/deliverables').status_code==404)

print(f'结果：{passed} 通过 / 0 失败')
