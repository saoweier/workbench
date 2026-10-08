"""One explicit real rebuild following the user's image-content correction."""
import json,uuid
from pathlib import Path
import httpx
out=Path('docs/test-artifacts/meme-image-quality-20261006');out.mkdir(exist_ok=True)
cid='bd923729-6646-4c8e-b554-7e3ec75fab83'
detail=httpx.get('http://127.0.0.1:8000/api/v1/contents/'+cid).json()
request={'request_id':str(uuid.uuid4()),'base_revision_id':detail['active_revision_id'],
 'instruction':'根据读者反馈重做图片：全稿恰好2页（封面计入），活泼口语的梗指南。图片必须独立讲清这句梗，不能靠发布caption补足。第一页直接讲具体短剧剧情，图中放完整经典对话和笑点；第二页用一句话讲真正含义，给可套用句式以及3条具体原创例句，食品和非食品场景都要有。items/detail就是给读者看的实质内容，不要只放原始语境、经典句式、同类台词、来源披露的标签，不要通用办公插画。删除主体中所有重复的审稿提醒、来源披露、独立核验、首发未知之类说明。来源可信度保留在诊断记录，图片只留一行简短脚注（最多48字）准确交代；不要反复出现。takeaway必须总结梗的实际意思或用法，不能再提醒查来源。配色明快，字大且信息紧凑，读者看图就学会这个梗。沿用原文事实，不再按红油重口味猜测。最新图片要求优先于旧版本的长篇来源说明。',
 'direction':'news','template_id':'illustrated','materials':'https://k.sina.com.cn/article_7879996051_1d5af32930680199pe.html\nhttps://k.sina.com.cn/article_7879848900_1d5acf3c406803btr2.html?from=ent\nhttps://tidenews.com.cn/news.html?id=3576984','run_mode':'real'}
request['image_policy']='diagram'
response=httpx.post('http://127.0.0.1:8000/api/v1/studio/contents/'+cid+'/revise',json=request,headers={'X-CWB-Local-Action':'account-connection'},timeout=30)
response.raise_for_status()
(out/'request.json').write_text(json.dumps({'request':request,'response':response.json()},ensure_ascii=False,indent=2),encoding='utf-8')
print(response.status_code,response.text)
