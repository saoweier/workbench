"""Program-owned evidence boundary; a title, brief, or search hit is not a fact."""
import re
from ..core.errors import ValidationFailed

CONTEXT_KINDS={'editorial_plan','hotpush_context','trend_context'}
QUOTE_PUNCTUATION=str.maketrans({'，':',','：':':','；':';','！':'!','？':'?','（':'(','）':')','“':'"','”':'"','‘':"'",'’':"'"})

def quote_char(char):
    return char.translate(QUOTE_PUNCTUATION)

def factual_sources(sources):
    from .source_reader import unreadable_notice
    return [s for s in sources if s.get('kind') not in CONTEXT_KINDS
        and s.get('access_state')=='ok' and s.get('excerpt_basis') in {'user_provided','full_text','local_file'}
        and len((s.get('excerpt') or '').strip())>=30
        and not unreadable_notice(s.get('excerpt') or '')
        and not (s.get('excerpt') or '').lstrip().startswith(('编辑提纲（非事实依据）','选题来源快照'))]

def is_meme(topic,requirements=''):
    return bool(re.search(r'什么梗|啥梗|热梗|梗指南|梗的(?:来源|意思|出处)|网络梗',topic+' '+requirements))

def needs_grounding(topic,requirements=''):
    # Real creation researches by default. A whitelist of "latest/news" words
    # silently skipped manually entered factual questions such as C044.
    text=topic+' '+requirements
    if re.search(r'基于.{0,12}(?:原文|资料)|资料调研：|真实事件|新闻|什么梗|啥梗|价格|费用|营养|排行榜|(?i:TOP\s*\d+)',text):return True
    pure_creative=bool(re.search(r'虚构(?:故事|小说|童话)|原创(?:诗歌|童话)|祝福语|(?:周末|家务|待办).{0,6}(?:清单|计划)',text))
    return not pure_creative

def require_evidence(topic,sources,requirements=''):
    usable=factual_sources(sources)
    if needs_grounding(topic,requirements) and not usable:
        raise ValidationFailed('核心事实资料不足：尚未取得能回答原题的可读正文。',details={'research_gaps':['尚未取得可读正文；请换可访问的原始资料来源与检索词，标题和摘要不能用于回答事实问题']})
    return usable

def validate_research(report,sources,*,meme=False,strict_quotes=True):
    index={s['id']:s for s in factual_sources(sources)}
    for fact in report.facts:
        source=index.get(fact.source_id)
        quote=fact.quote.strip()
        excerpt=source['excerpt'] if source else ''
        if source and quote not in excerpt:
            # HTML paragraphs become newlines; models may join them with spaces.
            # Match the same contiguous characters, then retain the actual span.
            positions=[i for i,c in enumerate(excerpt) if not c.isspace()]
            # Full/half-width punctuation is typographic variation, not a fact
            # change. Always restore the exact source span after matching.
            needle=quote_char(re.sub(r'\s+','',quote))
            haystack=''.join(quote_char(excerpt[i]) for i in positions)
            offset=haystack.find(needle) if needle else -1
            if offset>=0:
                quote=excerpt[positions[offset]:positions[offset+len(needle)-1]+1]
                fact.quote=quote
        if not source or quote not in excerpt:
            if strict_quotes:
                raise ValidationFailed('调研引用不是可读资料中的原文，已停止生成：'+fact.source_id)
            # Research is an editorial assessment, not an exact transcription
            # task. An unmatched paraphrase must not be displayed as a quote.
            fact.quote=''
            note='来源 '+fact.source_id+' 的摘录未逐字匹配，已作为调研概述保留，原文可在诊断中查看。'
            if note not in report.limitations:report.limitations.append(note)
    if not report.can_answer or report.blocking_gaps:
        gaps=report.blocking_gaps or ['核心事实未得到资料支持']
        raise ValidationFailed('调研无法回答原题：'+'；'.join(gaps),details={'research_gaps':gaps})
    if meme and not {'origin','meaning'} <= {f.role for f in report.facts}:
        raise ValidationFailed('梗指南缺少有原文支持的出处或含义，不能只凭字面解释继续制作')
    if not report.facts:raise ValidationFailed('调研没有可引用的核心事实，已停止生成')
