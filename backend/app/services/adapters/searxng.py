"""SearXNG's native GET /search?format=json protocol (no API key required)."""
from __future__ import annotations

import time
import re
from urllib.parse import urlencode, urlsplit, urlunsplit

from ..provider_contract import AdapterType, RunMode
from .base import AdapterResult, BaseAdapter


class SearXNGAdapter(BaseAdapter):
    adapter_type = AdapterType.SEARXNG
    supports_source_url = True

    def complete(self, prompt, *, system=None, max_tokens=None, json_schema=None, request_key=None):
        return self.search(prompt, request_key=request_key)

    def test_connection(self):
        return self.search('SearXNG', limit=1, request_key='__connection_test__')

    def search(self, query, *, limit=5, request_key=None, source_url=None):
        started = time.monotonic()
        def fail(code, message, status=None, meta=None):
            return AdapterResult(ok=False,error_code=code,error_message=message,http_status=status,
                                 latency_ms=int((time.monotonic()-started)*1000),
                                 run_mode=RunMode.REAL,meta=meta or {})
        if not query.strip():
            return fail('EMPTY','请输入检索问题')
        if self.transport is None:
            return fail('UNKNOWN','未注入搜索传输')
        try:
            from ..source_reader import validate_public_url
            base = urlsplit(self.cfg.base_url)
            if base.query or base.fragment:
                return fail('BAD_URL','SearXNG 地址请填写实例根地址或 /search，不含查询参数')
            validate_public_url(self.cfg.base_url, allow_localhost=self.cfg.allow_localhost)
            path = base.path.rstrip('/')
            if not path.endswith('/search'):
                path += '/search'
            params = {'q':query.strip(),'format':'json','language':self.cfg.search_language,
                      'categories':'general','pageno':1}
            if self.cfg.search_engines:
                params['engines'] = ','.join(self.cfg.search_engines)
            if source_url:
                params['source_url'] = source_url
            url = urlunsplit((base.scheme,base.netloc,path,urlencode(params),''))
            headers = {'Accept':'application/json'}
            if self.api_key:
                headers['Authorization'] = f'Bearer {self.api_key}'
            response = self.transport.request('GET',url,headers=headers,json_body=None,
                                              timeout=float(self.cfg.timeout_seconds))
        except ValueError as exc:
            return fail('BAD_URL',str(exc))
        except TimeoutError:
            return fail('NETWORK_TIMEOUT','SearXNG 搜索超时；请检查实例及上游引擎')
        except ConnectionRefusedError:
            return fail('NETWORK_TIMEOUT',
                f'连不上 SearXNG（{self.cfg.base_url}）：该地址端口没有服务在监听。'
                '请确认 SearXNG 实例已启动，且地址与端口和实例实际监听的端口一致。')
        except Exception as exc:
            return fail('NETWORK_TIMEOUT',f'无法连接 SearXNG（{self.cfg.base_url}）：{exc}')
        status = response.status_code
        if status == 403:
            return fail('AUTH','SearXNG 拒绝 JSON 搜索，请在 settings.yml 的 search.formats 中启用 json；也可能需要实例鉴权',status)
        if status == 429:
            return fail('RATE_LIMIT','SearXNG 实例限流，请稍后重试',status)
        if status in {502,503,504}:
            # 这两种成因完全相反，必须分开说：本机地址出现的 502 通常来自系统代理/网关，
            # 而不是 SearXNG。历史上这条信息把「实例没启动」说成「SearXNG 返回 502」，
            # 让人跑去检查地址填错——排查方向直接被带偏。
            local = urlsplit(self.cfg.base_url).hostname in {'127.0.0.1','::1','localhost'}
            hint = ('本机地址出现 502/503 一般不是 SearXNG 自己返回的，而是系统代理或网关挡在前面；'
                    '请确认实例真的在该端口监听、且本机地址没有被代理接管。' if local else
                    '请确认 SearXNG 实例本身及其上游搜索引擎是否可用。')
            return fail('SERVER_ERROR',f'访问 SearXNG 得到 HTTP {status}（未跟随跳转）。{hint}',status)
        if status >= 400 or 300 <= status < 400:
            return fail('SERVER_ERROR',f'SearXNG 返回 HTTP {status}；未跟随跳转',status)
        body = response.json_body
        if not isinstance(body,dict) or not isinstance(body.get('results'),list):
            return fail('BAD_JSON','实例未返回 SearXNG JSON 结果；请检查地址及 JSON 格式配置',status)
        query_report=body.get('query_report')
        if isinstance(query_report,dict) and query_report.get('query')!=query.strip():
            return fail('BAD_JSON','独立查询器返回的议题与请求不一致，未使用该资料',status)
        kept, seen, dropped = [], set(), 0
        for hit in body['results']:
            if not isinstance(hit,dict):
                dropped += 1
                continue
            url = hit.get('url')
            try:
                address = urlsplit(url) if isinstance(url,str) else None
                valid = address is not None and address.scheme in {'http','https'} and address.hostname and not address.username
            except ValueError:
                valid = False
            if not valid:
                dropped += 1
                continue
            if url in seen:
                continue
            seen.add(url)
            record={'url':url,'title':str(hit.get('title') or ''),
                         'snippet':str(hit.get('content') or ''),
                         'published_at':hit.get('publishedDate'),
                         'engines':hit.get('engines') or ([hit['engine']] if hit.get('engine') else []),
                         'score':hit.get('score')}
            # This extension is provided by the independently tested query
            # service. Standard SearXNG responses continue to work unchanged.
            if isinstance(query_report,dict) and isinstance(hit.get('read'),dict):
                record['read']=hit['read']
            kept.append(record)
        from ..public_research import _relevance
        candidates = [{**hit,'relevance':_relevance(hit['title']+' '+hit['snippet'],query)} for hit in kept]
        has_subject = bool(re.search(r'[a-zA-Z]{3,}|[\u4e00-\u9fff]{2,}',query))
        if has_subject and not isinstance(query_report,dict):
            # Filter before applying the result limit: unrelated high-ranked
            # engine responses must not hide a relevant result from another.
            # The independent query service already ranks and filters with
            # its own query-aware relevance model. Reapplying this simpler
            # title-only lexical filter loses valid results for long English
            # queries (for example, "mathematical manuscripts" vs "math").
            kept = [hit for hit in kept if _relevance(hit['title']+' '+hit['snippet'],query)>0]
            # 按相关性降序。读取配额有限（每轮只读少数几条就停），必须让最相关的
            # 候选排在前面；否则刚过阈值的页面会先被读走并触发提前停止，
            # 真正对题的候选连排队的机会都没有。
            kept.sort(key=lambda hit:_relevance(hit['title']+' '+hit['snippet'],query),reverse=True)
        meta = {'unresponsive_engines':body.get('unresponsive_engines') or [],
                'dropped_count':dropped,'result_count':len(kept),'search_backend':'searxng',
                'candidates':candidates[:30],'irrelevant_count':len(candidates)-len(kept)}
        if isinstance(query_report,dict):
            meta.update(search_backend='query_lab',query_report=query_report)
        if not kept:
            return fail('EMPTY','SearXNG 未返回可用链接；查看诊断中的上游引擎状态',status,meta)
        return AdapterResult(ok=True,parsed={'results':kept[:limit]},http_status=status,
                             latency_ms=int((time.monotonic()-started)*1000),
                             run_mode=RunMode.REAL,meta=meta)
