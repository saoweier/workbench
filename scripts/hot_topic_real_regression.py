"""Explicit live model regression. Durable requests, no publish or auto resend."""
import sys,json,uuid,hashlib
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/test-artifacts/hot-topic-real-20261006';OUT.mkdir(parents=True,exist_ok=True)
BASE='http://127.0.0.1:8000/api/v1';HEAD={'X-CWB-Local-Action':'account-connection'}
action=sys.argv[1] if len(sys.argv)>1 else 'status'
def save(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
if action=='prepare':
 snapshot=httpx.get(BASE+'/studio/trends?category=hot',timeout=30).json();save(OUT/'hotpush-snapshot.json',snapshot)
 boards={b['platform']:b for b in snapshot['sources']}
 def hit(source,contains):return next(i for i in boards[source]['items'] if contains in i['title'])
 def rank_material(source):
  b=boards[source]
  return '以下是本次HotPush返回的真实榜单快照，只能说明聚合返回顺序，不能冒充评分排名或全网热度：\n'+json.dumps({'source':b['name'],'fetched_at':b['fetched_at'],'order_basis':'HotPush聚合返回顺序','items':[{'rank':i['rank'],'title':i['title'],'description':i['description'][:350]} for i in b['items'][:10]]},ensure_ascii=False)
 cases=[
 {'key':'book_top10','topic':'HotPush豆瓣新书榜TOP10','requirements':'做一个精致的TOP10新书速查排行榜，恰好1页，保留快照前十本书的完整名称与顺序，每本书一句具体简介。明确按HotPush聚合返回顺序排列，不代表评分或销量。不要编造评分、安装量、作者或获奖信息；来源简短标注，不堆砌免责声明。','template_id':'rank_cards','pages':1,'materials':rank_material('douban_book'),'expected_names':[i['title'] for i in boards['douban_book']['items'][:10]],'basis':'current_hotpush_board'},
 {'key':'movie_top10','topic':'HotPush热映作品TOP10','requirements':'按当前快照前十条做TOP10电影与剧集速查，恰好2页，每页5项，完整保留片名与顺序，用一句话讲清内容。不把热映聚合顺序称为评分排名，不能编造票房、评分或看过的体验。','template_id':'editorial','pages':2,'materials':rank_material('douban_movie'),'expected_names':[i['title'] for i in boards['douban_movie']['items'][:10]],'basis':'current_hotpush_board'},
 {'key':'tech_explainer','hit':hit('juejin','Vue3 UIKit'),'requirements':'做一个通俗的科技图解，恰好2页，说明它解决什么问题、聊天/会话/主题/移动端这四部分如何协作。基于实际原文，不编造组件名、接口或项目效果。','template_id':'illustrated','pages':2,'basis':'current_hotpush_topic'},
 {'key':'game_format','hit':hit('bilibili','英雄联盟全球总决赛'),'requirements':'做一个新人也看得懂的2026赛制指南，恰好2页，区分入围赛、瑞士轮与淘汰赛，说明晋级与淘汰规则。按官方2026资料，不能混入2025赛制或编造赛果；不是排行榜。','template_id':'category_table','pages':2,'materials':'https://lolesports.com/en-US/news/msi-and-worlds-updates','basis':'current_hotpush_topic'},
 {'key':'life_sleep','hit':hit('weibo','黄金睡眠'),'requirements':'做一个活泼的生活知识指南，恰好1页，直接解释成人睡眠时长建议和个人差异，给出简单的作息自查方法。基于AASM资料，不把建议当唯一黄金数字，不做疾病诊断或保证疗效；不是排行榜。','template_id':'friendly_guide','pages':1,'materials':'https://aasm.org/advocacy/position-statements/adult-sleep-duration-health-advisory/','basis':'current_hotpush_topic'},
 {'key':'work_tutorial','hit':hit('sspai','新版 2Do'),'requirements':'做成任务管理上手指南，恰好2页，有具体操作步骤和使用场景，说明与普通待办清单的区别。只写资料实际支持的功能，不编造新版按钮或价格；不是排行榜。','template_id':'editorial','pages':2,'basis':'current_hotpush_topic'},
 {'key':'meme_regression','topic':'这菜吃了能不烧心吗是什么梗','requirements':'做一个活泼的梗指南，恰好2页，核心是规矩体AI短剧的剧情套路、经典台词、表达的真实含义和原创使用例句。不要误解成红油辣菜的健康调侃，不做独立来源披露卡片。','template_id':'rank_cards','pages':2,'materials':'https://k.sina.com.cn/article_7879848900_1d5acf3c406803btr2.html?from=ent\nhttps://k.sina.com.cn/article_7879996051_1d5af32930680199pi.html?from=ent','basis':'historical_hot_topic_regression'}]
 for case in cases:
  item=case.pop('hit',None)
  if item:case['topic']=item['title'];case['trend_id']=item['id'];case['trend_snapshot']=item
  case['request_id']=str(uuid.uuid4())
 save(OUT/'cases.json',cases);print(json.dumps([{k:c.get(k) for k in ['key','topic','template_id','pages','basis']} for c in cases],ensure_ascii=False))
elif action=='prepare-fixes':
 path=OUT/'fixed-cases.json'
 if path.exists():raise SystemExit('Corrected requests already prepared; keep their identities.')
 original=json.loads((OUT/'cases.json').read_text(encoding='utf-8'));cases=[]
 for key in ['game_format','life_sleep','work_tutorial','meme_regression','tech_explainer']:
  case=next(c for c in original if c['key']==key).copy();case.update(key=key+'_fixed',original_case=key,request_id=str(uuid.uuid4()))
  if key=='game_format':
   case['materials']+='\nhttps://lolesports.com/en-GB/season/115547545029543948/handbook/115548016979679448'
   case['requirements']+=' 用官方手册已记载的阶段规则回答，不要求穷尽BO1/BO3/抽签等未要求的所有细则，未记载的细则省略。'
  if key=='life_sleep':case['materials']+='\nhttps://pmc.ncbi.nlm.nih.gov/articles/PMC4434546/'
  if key=='work_tutorial':case['materials']='https://www.2doapp.com/'
  if key=='meme_regression':case['requirements']+=' 每条卡片只讲一件事，详情25～45字，一个例句一张卡，不重复主体总结。'
  cases.append(case)
 snapshot=json.loads((OUT/'hotpush-snapshot.json').read_text(encoding='utf-8'))
 board=next(b for b in snapshot['sources'] if b['platform']=='juejin');hit=next(i for i in board['items'] if 'DiPlay' in i['title'])
 cases.append({'key':'tech_diplay','topic':hit['title'],'trend_id':hit['id'],'trend_snapshot':hit,'request_id':str(uuid.uuid4()),'pages':2,'template_id':'illustrated','basis':'current_hotpush_topic',
  'materials':'https://raw.githubusercontent.com/shihabal3amri/DiPlay/main/README.md',
  'requirements':'做一个通俗的科技图解，恰好2页，基于项目实际README解释DiPlay装在哪、连接方式和兼容前提，不能将所有BYD车都称为可用，不能冒充苹果或比亚迪官方产品，未验证的功能不当成亲测。不写车辆调试命令。每个条目只讲一件事，详情25～45字。'})
 save(path,cases);print('Prepared',len(cases),'explicit corrected/new cases; original failures retained.')
elif action in ['submit','submit-fixed']:
 keys=set(sys.argv[2:]);cases=json.loads((OUT/('fixed-cases.json' if action=='submit-fixed' else 'cases.json')).read_text(encoding='utf-8'))
 for case in cases:
  if keys and case['key'] not in keys:continue
  path=OUT/(case['key']+'-request.json')
  if path.exists():print(case['key'],'already recorded; no resend');continue
  payload={k:case[k] for k in ['request_id','topic','requirements','template_id','pages','materials','trend_id'] if k in case}
  payload.update(density='balanced',style='lively',image_policy='diagram',run_mode='real',platforms=['douyin','xiaohongshu'])
  record={'request':payload,'case':case};save(path,record)
  try:
   r=httpx.post(BASE+'/studio/produce',headers=HEAD,json=payload,timeout=30);r.raise_for_status();record['response']=r.json()
  except Exception as exc:record['submission_error']=str(exc);save(path,record);raise
  save(path,record);print(case['key'],json.dumps({k:record['response'].get(k) for k in ['display_id','content_id','run_id','state']},ensure_ascii=False),flush=True)
elif action=='retry':
 if len(sys.argv)<3:raise SystemExit('Explicit case keys required; no blanket retry.')
 from datetime import datetime,timezone
 for key in sys.argv[2:]:
  record=json.loads((OUT/(key+'-request.json')).read_text(encoding='utf-8'));rid=record['response']['run_id']
  run=httpx.get(BASE+'/runs/'+rid,timeout=30).json()
  if run['state']!='failed':raise SystemExit(key+' is not a stopped failure')
  history=OUT/(key+'-retries.json');entries=json.loads(history.read_text(encoding='utf-8')) if history.exists() else []
  if len(entries)>=2:raise SystemExit(key+' already has two explicit retries; diagnose before more work')
  entry={'time':datetime.now(timezone.utc).isoformat(),'previous_run':run,'reason':'Corrected known output repair; reuse completed checkpoints. No uncertain calls resent.'};entries.append(entry);save(history,entries)
  r=httpx.post(BASE+'/runs/'+rid+'/control',headers=HEAD,json={'action':'retry'},timeout=30);r.raise_for_status();entry['response']=r.json();save(history,entries);print(key,r.status_code,r.json()['state'])
else:
 for path in sorted(OUT.glob('*-request.json')):
  record=json.loads(path.read_text(encoding='utf-8'));case=record['case'];response=record.get('response')
  if len(sys.argv)>2 and case['key'] not in sys.argv[2:]:continue
  if not response:print(case['key'],'submission result unknown');continue
  rid=response['run_id'];cid=response['content_id']
  run=httpx.get(BASE+'/runs/'+rid,timeout=30).json();save(OUT/(case['key']+'-run.json'),run)
  content=httpx.get(BASE+'/contents/'+cid,timeout=30).json();save(OUT/(case['key']+'-content.json'),content)
  print(case['key'],content.get('display_id'),run.get('state'),content.get('state'),run.get('blocked_stage'),(run.get('error') or '')[:200],flush=True)
  if run.get('state') in ['succeeded','failed','blocked','needs_attention']:
   save(OUT/(case['key']+'-diagnostics.json'),httpx.get(BASE+'/contents/'+cid+'/diagnostics',timeout=30).json())
   if run['state']=='succeeded':
    generated_id=next(j['output_refs']['public']['revision_id'] for j in run['jobs'] if j['stage']=='compose' and (j.get('output_refs') or {}).get('public'))
    rev=next(r for r in content['revisions'] if r['revision_id']==generated_id)
    for platform in rev['platforms']:
     for artifact in platform['artifacts']:
      image=OUT/case['key']/platform['platform']/f"page-{artifact['page_index']:02d}.png";image.parent.mkdir(parents=True,exist_ok=True)
      raw=httpx.get('http://127.0.0.1:8000'+artifact['url'],timeout=30).content
      if hashlib.sha256(raw).hexdigest()!=artifact['sha256']:raise ValueError('Artifact hash mismatch')
      image.write_bytes(raw)
