"""Read bounded public text pages; remove executable and non-content markup."""
from html.parser import HTMLParser
import hashlib
import ipaddress
import socket
import re
from urllib.parse import urlsplit,urljoin
import httpx

class TextParser(HTMLParser):
    def __init__(self):
        super().__init__();self.hidden=0;self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag in {"script","style","noscript","svg"}:self.hidden+=1
    def handle_endtag(self,tag):
        if tag in {"script","style","noscript","svg"}:self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden and data.strip():self.parts.append(data.strip())

class ArticleParser(TextParser):
    """Prefer explicit article containers over site navigation and sidebars."""
    VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    def __init__(self):
        super().__init__();self.stack=[];self.article_parts=[];self.article_depth=None
    def handle_starttag(self,tag,attrs):
        super().handle_starttag(tag,attrs)
        if tag in self.VOID:return
        self.stack.append(tag)
        values=dict(attrs)
        if self.article_depth is None and (tag=='article' or values.get('id') in {'js_content','article_content','article-content'}):
            self.article_depth=len(self.stack)
    def handle_endtag(self,tag):
        super().handle_endtag(tag)
        if tag in self.stack:
            index=len(self.stack)-1-self.stack[::-1].index(tag)
            if self.article_depth is not None and index<self.article_depth:self.article_depth=None
            del self.stack[index:]
    def handle_data(self,data):
        super().handle_data(data)
        if self.article_depth is not None and not self.hidden and data.strip():self.article_parts.append(data.strip())

def unreadable_notice(body):
    return len(body)<2000 and bool(re.search(r'此账号已自主注销|账号已注销.{0,20}内容无法查看|内容已被发布者删除|该内容已被删除|CF_APP_WAF|var requestInfo\s*=|^\s*Sina Visitor System\s*$',body,re.I))

def extract_article(text):
    parser=ArticleParser();parser.feed(text)
    body='\n'.join(parser.article_parts or parser.parts).strip()
    if unreadable_notice(body):
        raise ValueError('网页返回注销、删除或访客访问验证提示，未取得文章正文')
    # Successful HTTP responses can still contain only a challenge/JS shell.
    if not body or (len(body)<500 and re.search(r'please wait|just a moment|checking your browser|enable javascript|访问验证|安全验证|请输入验证码',body,re.I)):
        raise ValueError('网页只返回加载或验证页面，未取得文章正文')
    return body

def validate_public_url(url, *, allow_localhost=False):
    address=urlsplit(url)
    if address.scheme not in {"http","https"} or not address.hostname or address.username:
        raise ValueError("来源地址不是公开网页")
    if not allow_localhost:
        addresses=[ipaddress.ip_address(e[4][0]) for e in socket.getaddrinfo(address.hostname,address.port or 443)]
        if addresses and all(a.is_global for a in addresses):return
        # Some desktop proxies use a benchmark-range synthetic DNS mapping.
        # Never allow that range for literal IPs or without independent public DNS.
        try:ipaddress.ip_address(address.hostname);literal=True
        except ValueError:literal=False
        fake=ipaddress.ip_network('198.18.0.0/15')
        if literal or not addresses or not all(a.version==4 and a in fake for a in addresses):
            raise ValueError('来源地址指向非公开网络')
        answer=httpx.get('https://dns.google/resolve',params={'name':address.hostname,'type':'A','edns_client_subnet':'0.0.0.0/0'},timeout=5,follow_redirects=False).json()
        public=[ipaddress.ip_address(v['data']) for v in answer.get('Answer',[]) if v.get('type')==1]
        if answer.get('Status')!=0 or not public or not all(a.is_global for a in public):
            raise ValueError('代理DNS未能核验公开来源地址')

def read_source_detail(url, *, allow_localhost=False, blocked_domains=()):
    with httpx.Client(timeout=8,follow_redirects=False) as client:
      for hop in range(4):
        validate_public_url(url,allow_localhost=allow_localhost)
        host=(urlsplit(url).hostname or '').lower()
        if any(host==d or host.endswith('.'+d) for d in blocked_domains):raise ValueError('不直接读取原社交平台')
        with client.stream("GET",url,headers={"User-Agent":"Mozilla/5.0 ContentWorkbench/1.4"}) as response:
            if response.status_code in {301,302,303,307,308}:
                if hop==3:raise ValueError('来源重定向过多')
                url=urljoin(url,response.headers.get('location',''));continue
            response.raise_for_status()
            mime=response.headers.get("content-type","").lower()
            if not any(kind in mime for kind in ["text/plain","text/html"]):
                raise ValueError("来源不是文字网页")
            raw=bytearray()
            for part in response.iter_bytes():
                raw.extend(part)
                # Bound transport bytes separately from extracted article text.
                # Modern article pages often carry megabytes of scripts/styles.
                if len(raw)>8000000:raise ValueError("网页超过安全读取上限（8MB），未取得完整正文")
            text=bytes(raw).decode(response.encoding or "utf-8",errors="replace")
            break
    if "html" in mime:
        text=extract_article(text)
    if not text.strip():raise ValueError("网页没有可用正文")
    return text[:20000],hashlib.sha256(raw).hexdigest(),url

def read_source(url, *, allow_localhost=False):
    text,sha,_=read_source_detail(url,allow_localhost=allow_localhost)
    return text,sha
