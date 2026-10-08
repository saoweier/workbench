"""Read all HotPush aggregation sources; never connect to originating platforms.
Public SSE feed is cached incrementally, with honest per-source failure states.
"""
from __future__ import annotations
import hashlib,json,re,threading,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlparse
import httpx
from ..core.config import get_settings
from ..core.errors import NotFound,ValidationFailed
from .platform_account import read_json,write_json

DEFAULT_BASE='https://hotpush.dawenzaist.de5.net'
REPOSITORY='https://github.com/JackyST0/hotpush'
# Metadata adapted from HotPush (MIT, Copyright 2025 JackyST0).
SOURCES={k:{'name':n,'category':c} for k,n,c in [
('weibo','微博热搜','热搜榜'),('zhihu','知乎热榜','热搜榜'),('bilibili','B站热搜','视频'),
('v2ex','V2EX 热门','技术'),('hackernews','Hacker News','技术'),('juejin','掘金热榜','技术'),
('ithome','IT之家热榜','科技资讯'),('nodeseek','NodeSeek','技术'),('sspai','少数派','科技资讯'),
('douban_movie','豆瓣热映','影视'),('douban_book','豆瓣新书','阅读'),('zaobao','联合早报','新闻'),('thepaper','澎湃新闻','新闻')]}
CATEGORIES={'hot':'全部热点','life':'生活榜','tech':'科技榜','games':'游戏榜','video':'视频','film':'影视','reading':'阅读','news':'新闻','custom':'其他来源'}
PATTERNS={'life':r'生活|吃|食|水果|旅游|旅行|养生|健康|穿搭|宠物|装修|家居|美食|假期|孩子|家庭',
 'tech':r'科技|手机|芯片|模型|[Aa][Ii]|[Gg]it[Hh]ub|[Ss]kill|[Aa]gent|数码|电脑|机器人|航天|开源',
 'games':r'游戏|王者|原神|[Ll][Oo][Ll]|电竞|[Ss]team|任天堂|[Pp][Ss]5|三角洲|鸣潮|黑神话|十字军'}

def now():return datetime.now(timezone.utc).isoformat()
def classify(title,source_category=''):
    out=[k for k,p in PATTERNS.items() if re.search(p,title)]
    out+= {'技术':['tech'],'科技资讯':['tech'],'视频':['video'],'影视':['film'],'阅读':['reading'],'新闻':['news']}.get(source_category,[])
    return list(dict.fromkeys(out)) or ['hot']
def safe_url(raw):
    p=urlparse(str(raw or ''))
    return str(raw) if p.scheme in {'http','https'} and p.hostname and not p.username and not p.password else None

def normalize_hotlist(data):
    if not isinstance(data,dict):raise ValidationFailed('HotPush榜单应为对象')
    sid=str(data.get('source') or '')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',sid):raise ValidationFailed('HotPush来源编号无效')
    info=SOURCES.get(sid,{'name':str(data.get('source_name') or sid)[:80],'category':'其他来源'})
    values=data.get('items')
    if not isinstance(values,list) or len(values)>500:raise ValidationFailed('HotPush条目列表无效或超出上限')
    rows=[];seen=set()
    for i,v in enumerate(values,1):
        if not isinstance(v,dict):continue
        title=re.sub(r'<[^>]*>','',str(v.get('title') or '')).strip()[:200]
        if not title or title in seen:continue
        seen.add(title)
        rows.append({'id':sid+'-'+hashlib.sha256((str(v.get('id') or i)+title).encode()).hexdigest()[:16],
          'title':title,'rank':i,'rank_label':'源内顺序','platform':sid,'source':info['name'],
          'url':safe_url(v.get('url')),'heat':str(v['hot_score'])[:60] if v.get('hot_score') is not None else None,
          'categories':classify(title,info['category']),'origin':'hotpush',
          'description':re.sub(r'<[^>]*>','',str(v.get('description') or ''))[:1600],
          'published_at':str(v.get('published') or '')[:80] or None})
    return {'platform':sid,'name':str(data.get('source_name') or info['name'])[:80],
      'source_category':info['category'],'items':rows,'updated_at':str(data.get('updated_at') or '')[:80] or None,
      'fetched_at':now(),'state':'ready' if rows else 'empty','origin':'hotpush','stale':False,
      'note':'通过HotPush聚合取得；序号保留来源返回顺序，未提供热度时不推算分值。'}

class TrendBoards:
    def __init__(self,root=None):
        self.root=(root or get_settings().storage_root)/'hotpush';self._lock=threading.RLock();self._thread=None
    def config(self):return {'base_url':read_json(self.root/'config.json').get('base_url',DEFAULT_BASE),'repository':REPOSITORY}
    def configure(self,base_url):
        p=urlparse(base_url)
        if p.scheme not in {'https','http'} or not p.hostname or p.username or p.password or p.query or p.fragment:
            raise ValidationFailed('请输入HotPush服务根地址，不含账号、参数或密钥')
        if p.scheme=='http' and p.hostname not in {'localhost','127.0.0.1','::1'}:
            raise ValidationFailed('远程服务请使用HTTPS；本机HotPush可以使用HTTP')
        base=base_url.rstrip('/')
        with self._lock:
            if self._thread and self._thread.is_alive():raise ValidationFailed('正在读取热点，请等本次完成后修改地址')
            write_json(self.root/'config.json',{'base_url':base})
            write_json(self.root/'snapshot.json',{})
        return self.config()
    def snapshot(self):return read_json(self.root/'snapshot.json')
    def _commit(self,fn):
        with self._lock:
            value=self.snapshot();fn(value);write_json(self.root/'snapshot.json',value)
    def ingest(self,data):
        board=normalize_hotlist(data)
        def update(value):value.setdefault('boards',{})[board['platform']]=board
        self._commit(update)
        return board
    def _stream(self):
        base=self.config()['base_url'];deadline=time.monotonic()+100;size=0;event='';lines=[];seen=set()
        try:
            with httpx.Client(timeout=httpx.Timeout(12,read=35),trust_env=False,follow_redirects=False) as client:
                with client.stream('GET',base+'/api/hot/stream',headers={'Accept':'text/event-stream'}) as response:
                    if response.status_code!=200:raise ValueError('HotPush返回HTTP '+str(response.status_code))
                    for line in response.iter_lines():
                        size+=len(line)
                        if size>5_000_000 or time.monotonic()>deadline:raise ValueError('HotPush响应超出读取上限')
                        if line.startswith('event:'):event=line[6:].strip()
                        elif line.startswith('data:'):lines.append(line[5:].strip())
                        elif not line and lines:
                            data=json.loads('\n'.join(lines));lines=[]
                            if event=='hotlist':
                                board=self.ingest(data);seen.add(board['platform'])
                            elif event=='progress':
                                self._commit(lambda v:v.update(progress={k:data.get(k,0) for k in ['completed','total','success']}))
                            elif event=='failed':
                                sid=str(data.get('source_id') or '')
                                if re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',sid):
                                    self._commit(lambda v:v.setdefault('failures',{}).update({sid:'HotPush暂未返回此来源'}))
                            elif event=='done':break
            self._commit(lambda v:v.update(refreshing=False,last_attempt_at=now(),message='本次HotPush读取已完成',last_success_at=now() if seen else v.get('last_success_at')))
        except Exception:
            self._commit(lambda v:v.update(refreshing=False,last_attempt_at=now(),message='HotPush读取暂未完成；已取得的热点保留。可刷新重试或更换自己的HotPush服务地址。'))
    def refresh(self):
        with self._lock:
            if self._thread and self._thread.is_alive():return
            self._commit(lambda v:v.update(refreshing=True,progress={'completed':0,'total':len(SOURCES),'success':0},failures={},last_started_at=now(),message='正在读取HotPush全部来源…'))
            self._thread=threading.Thread(target=self._stream,daemon=True,name='hotpush-read');self._thread.start()
    def find(self,item_id):
        for b in self.snapshot().get('boards',{}).values():
            item=next((i for i in b.get('items',[]) if i['id']==item_id),None)
            if item:return item,b
        raise ValidationFailed('热点条目已更新，请重新选择或直接输入选题')
    def all(self,category='hot',refresh=False):
        if category not in CATEGORIES:raise ValidationFailed('请选择有效板块')
        value=self.snapshot()
        if refresh or not value or (not value.get('refreshing') and time.time()-(self.root/'snapshot.json').stat().st_mtime>900):self.refresh();value=self.snapshot()
        # Recover a previous process's interrupted flag; threads never survive restart.
        if value.get('refreshing') and not (self._thread and self._thread.is_alive()):self.refresh();value=self.snapshot()
        meta={**SOURCES,**{k:{'name':b['name'],'category':b['source_category']} for k,b in value.get('boards',{}).items()}}
        sources=[]
        for sid,info in meta.items():
            b=value.get('boards',{}).get(sid)
            if b:
                b=dict(b);b['stale']=sid in value.get('failures',{}) or bool(value.get('last_started_at') and b['fetched_at']<value['last_started_at'])
                if b['stale']:b.update(state='stale',note='正在更新，先显示上次成功快照。' if value.get('refreshing') and sid not in value.get('failures',{}) else 'HotPush本次未取得此来源，保留原时间的旧快照。')
            else:b={'platform':sid,'name':info['name'],'source_category':info['category'],'items':[],'state':'loading' if value.get('refreshing') else 'unavailable','fetched_at':None,'updated_at':None,'stale':False,'note':'等待HotPush返回此来源' if value.get('refreshing') else 'HotPush暂未返回此来源，无伪造或替代热点。'}
            b['items']=[i for i in b['items'] if category=='hot' or category in i['categories'] or category=='custom' and sid not in SOURCES]
            if category=='hot' or b['items']:sources.append(b)
        return {'category':category,'categories':CATEGORIES,'sources':sources,'refreshing':value.get('refreshing',False),
            'progress':value.get('progress',{}),'message':value.get('message','尚未读取热点'),'last_success_at':value.get('last_success_at'),**self.config()}
