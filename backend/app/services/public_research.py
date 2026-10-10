"""Bounded public-page search for research, independent of HotPush trends."""
from html.parser import HTMLParser
from urllib.parse import urlencode,urlsplit,parse_qs
import base64
import re
import httpx
from .source_reader import validate_public_url

BLOCKED_PLATFORMS=('weibo.com','weibo.cn','douyin.com','xiaohongshu.com','zhihu.com')
ENGINES=()

class SearchLinks(HTMLParser):
    def __init__(self):super().__init__();self.heading=0;self.link=None;self.items=[]
    def handle_starttag(self,tag,attrs):
        if tag in {'h2','h3'}:self.heading+=1
        if tag=='a' and self.heading:self.link={'url':dict(attrs).get('href',''),'title':''}
    def handle_data(self,text):
        if self.link is not None:self.link['title']+=text
    def handle_endtag(self,tag):
        if tag=='a' and self.link is not None:
            item=self.link;self.link=None;item['title']=' '.join(item['title'].split())
            if item['url'].startswith(('https://','http://')) and item['title'].strip():self.items.append(item)
        if tag in {'h2','h3'}:self.heading=max(0,self.heading-1)

def _destination(url):
    host=urlsplit(url).hostname or ''
    if host.endswith('bing.com') and '/ck/a' in url:
        value=parse_qs(urlsplit(url).query).get('u',[''])[0]
        if value.startswith('a1'):
            try:return base64.urlsafe_b64decode(value[2:]+'='*(-len(value[2:])%4)).decode()
            except (ValueError,UnicodeError):return url
    if host=='www.baidu.com' and url.startswith('http://'):return 'https://'+url[7:]
    return url

def _relevance(title,query):
    words=re.findall(r'[a-zA-Z]{3,}|\d{4}|[\u4e00-\u9fff]+',query.lower())
    tokens=set(w for w in words if not re.fullmatch(r'\d{4}',w))
    tokens.update(w[i:i+2] for w in words if re.search(r'[\u4e00-\u9fff]',w) for i in range(len(w)-1))
    text=title.lower()
    # 版本号是强区分信号：搜「Python 3.13」时，讲 3.3 / 3.10 的文章同样命中
    # 「python」和「新特性」，会挤掉真正讲 3.13 的内容。这里给版本对上的加分、
    # 对不上的降权，但**不归零**——一旦候选被全部过滤，整次搜索会被判成
    # 「未返回可用链接」，连可用的结果也一起丢掉。候选随后按分数降序排列，
    # 读取配额自然会先给版本对上的那几篇。
    version_bonus=0
    _versions=re.findall(r'\d+\.\d+',query)
    if _versions:
        version_bonus=8 if any(v in text for v in _versions) else -4
    def matches(token):
        return bool(re.search(r'(?<![a-z])'+re.escape(token)+r'(?![a-z])',text)) if token.isascii() and token.isalpha() else token in text
    matched={t for t in tokens if matches(t)}
    english={w for w in words if w.isascii() and w.isalpha()}
    chinese={t for t in tokens if re.search(r'[\u4e00-\u9fff]',t)}
    # Company names alone are not evidence of a multi-part Chinese news event.
    # English terms use word boundaries: "window" must not match "Windows".
    if english and chinese and sum(len(w) for w in words if re.search(r'[\u4e00-\u9fff]',w))>=4 and not matched&chinese:return 0
    if len(english)>=3 and not chinese and len(matched&english)<(len(english)+1)//2:return 0
    subject=sum(len(t) for t in matched)
    if not subject:return 0
    # 命中多个不同的词才说明对题：只蹭到一个通用词（如「助手」）不算相关。
    # 缺这项时「2026年节日大全一览表-百历助手」会仅凭「助手」+年份压过
    # 真正对题的「11款最佳AI编程写代码助手评测推荐_知乎」。
    subject+=4*max(0,len(matched)-1)
    # 年份只是弱信号。原先给 12 分，导致「2026 年节日大全一览表」这类仅靠标题里
    # 含「2026」命中的页面拿到高分、通过相关性过滤，再挤掉真正对题的候选。
    # 降到 3 分：同类year资料仍排在无年份之前，但年份单独挑不过主题词。
    return max(1,subject+sum(3 for year in re.findall(r'\b\d{4}\b',query) if year in text)+version_bonus)

def search_public(*args, **kwargs):
    """Retired compatibility entry point; no search-engine network requests."""
    from ..core.errors import NotConfigured
    raise NotConfigured('内置搜索网页抓取已移除，请配置搜索服务。')
