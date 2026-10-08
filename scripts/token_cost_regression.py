"""Explicit live regression; records requests and responses without provider keys."""
import json,sys,uuid,shutil
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/test-artifacts/token-cost-20261006'
OUT.mkdir(parents=True,exist_ok=True)
BASE='http://127.0.0.1:8000/api/v1'
HEAD={'X-CWB-Local-Action':'account-connection'}
action=sys.argv[1] if len(sys.argv)>1 else 'status'
if action in {'baseline','fixed'}:
    payload={'request_id':str(uuid.uuid4()),'topic':'2万token价值多少钱',
      'requirements':'做成普通人看得懂的活泼价格速查指南。全稿恰好2页，第一页就给具体金额，不要空封面。以DeepSeek官方人民币API价格为例，分别讲2万未缓存输入、2万输出、1万未缓存输入加1万输出。区分Flash和Pro、空闲和高峰。第二页解释计费公式、缓存和token不等于字数。不要做排行榜；不要用泛泛的核验流程、来源披露卡片填充。明确价格日期，API费用不是会员价格。',
      'density':'balanced','style':'lively','pages':2,'direction':'tech',
      'template_id':'illustrated' if action=='baseline' else 'friendly_guide',
      'materials':'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/\nhttps://api-docs.deepseek.com/zh-cn/quick_start/token_usage/',
      'image_policy':'diagram','run_mode':'real','platforms':['douyin','xiaohongshu']}
    response=httpx.post(BASE+'/studio/produce',headers=HEAD,json=payload,timeout=30)
    response.raise_for_status()
    record={'request':payload,'response':response.json()}
    (OUT/(action+'-request.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(record['response'],ensure_ascii=False))
elif action=='revised':
    old=OUT/'revised-request.json'
    if old.exists():
        previous=json.loads(old.read_text(encoding='utf-8'))['response']['run_id']
        archive=OUT/('revision-attempt-'+previous[:8]);archive.mkdir(exist_ok=True)
        for name in ['revised-request.json','revised-run.json','revised-content.json','diagnostics.json']:
            f=OUT/name
            if f.exists():shutil.copy2(f,archive/name)
    prior=json.loads((OUT/'fixed-request.json').read_text(encoding='utf-8'))['response']
    cid=prior['content_id'];detail=httpx.get(BASE+'/contents/'+cid,timeout=30).json()
    payload={'request_id':str(uuid.uuid4()),'base_revision_id':detail['active_revision_id'],
      'instruction':'核对图片后调整：恰好2页，第一图仍为价格表；第二图4条紧凑、有用的信息：怎么算（含除以百万和具体换算）、缓存命中后实际多便宜、高峰时段（北京时间9–12点与14–18点及法定节假日例外）、token不是字数且以usage计量。重要解释在items.detail中，不能藏在caption、takeaway或footnote。删除body里重复总结，不单独做口径边界、来源披露或审稿说明卡片。绿色标题、奶油纸、小插画和横向信息行。金额保留正确精度和币种。',
      'template_id':'friendly_guide','image_policy':'diagram','run_mode':'real',
      'materials':'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/\nhttps://api-docs.deepseek.com/zh-cn/quick_start/token_usage/'}
    r=httpx.post(BASE+'/studio/contents/'+cid+'/revise',json=payload,headers=HEAD,timeout=30);r.raise_for_status()
    (OUT/'revised-request.json').write_text(json.dumps({'request':payload,'response':r.json()},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(r.json(),ensure_ascii=False))
elif action=='retry':
    record=json.loads((OUT/'revised-request.json').read_text(encoding='utf-8'))['response']
    r=httpx.post(BASE+'/runs/'+record['run_id']+'/control',headers=HEAD,json={'action':'retry'},timeout=30)
    r.raise_for_status();print(json.dumps(r.json(),ensure_ascii=False))
else:
    for name in ['baseline','fixed','revised']:
        p=OUT/(name+'-request.json')
        if not p.exists():continue
        record=json.loads(p.read_text(encoding='utf-8'))['response']
        cid=record['content_id'];rid=record['run_id']
        r=httpx.get(BASE+'/runs/'+rid,timeout=30)
        (OUT/(name+'-run.json')).write_text(json.dumps(r.json(),ensure_ascii=False,indent=2),encoding='utf-8')
        detail=httpx.get(BASE+'/contents/'+cid,timeout=30).json()
        (OUT/(name+'-content.json')).write_text(json.dumps(detail,ensure_ascii=False,indent=2),encoding='utf-8')
        run=r.json()
        print(name,json.dumps({k:run.get(k) for k in ['id','state','blocked_stage','error']},ensure_ascii=False))
        for job in run.get('jobs',[]):print(job['stage'],job['state'],job.get('error'))
