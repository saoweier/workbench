"""The workflow must consume readable text returned by the query service even
when the URL was already recorded as a blocked HotPush reference.
"""
from types import SimpleNamespace

from app.services.adapters.base import AdapterResult
from app.services.provider_contract import RunMode
from app.services.research_service import ResearchResult, ResearchService


body = '这是一段由独立查询器读取的可读报道正文，包含事件时间、地点与官方通报口径。' * 5
url = 'https://example.org/hotpush-story'


class Runtime:
    def search_provider(self):
        return SimpleNamespace(name='queryer', adapter_type='searxng')

    def search(self, query, **kwargs):
        return SimpleNamespace(id='call-1'), AdapterResult(ok=True, parsed={'results': [{
            'url': url,
            'title': '热榜原文',
            'snippet': '仅摘要',
            'read': {'url': url, 'state': 'readable', 'body': body, 'sha256': 'hash', 'final_url': url},
        }]})


result = ResearchResult(topic='一条热榜事件', content_id='content-1', run_mode=RunMode.REAL, sources=[
    {'id': 'U01', 'kind': 'hotpush_context', 'url': url, 'excerpt': '热榜标题', 'excerpt_basis': 'user_provided'},
    {'id': 'W01', 'kind': 'public_web', 'url': url, 'excerpt': None, 'excerpt_basis': 'search_snippet', 'access_state': 'snippet_only'},
])

ResearchService(Runtime()).discover(
    result,
    planner=lambda gaps, sources: SimpleNamespace(queries=['一条热榜事件'], objective='读取原文', required_evidence=[]),
)

recovered = next(source for source in result.sources if source.id == 'W01')
assert recovered.excerpt_basis == 'full_text', 'a readable cached body for a known URL must upgrade the existing source'
assert recovered.access_state.value == 'ok'
assert recovered.excerpt == body
assert any(claim.source_ids == ['W01'] for claim in result.claims)
assert not any('配置的搜索服务未取得可用正文' in note for note in result.limitations)
print('PASS queryer readable body upgrades existing blocked HotPush URL')
