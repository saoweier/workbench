"""Source-bound API cost arithmetic. No price constants or model-made tariffs."""
import json,re,hashlib
from decimal import Decimal
from datetime import datetime,timezone,timedelta
from urllib.parse import urlsplit
from ..core.errors import ValidationFailed

PRICE_URL='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
LEDGER_ID='TOKEN_COST_CALC'

def is_token_cost(topic):
    return bool(re.search(r'tokens?',topic,re.I) and re.search(r'多少钱|价值|价格|费用|成本|花费',topic))

def quantity(topic):
    match=re.search(r'(\d+(?:\.\d+)?)\s*(万|千|百万)?\s*tokens?',topic,re.I)
    if not match:raise ValidationFailed('请在token费用选题中给出具体数量，例如2万token')
    value=Decimal(match[1])*{'万':10000,'千':1000,'百万':1000000,None:1}[match[2]]
    if value!=value.to_integral_value() or not 1<=value<=1_000_000_000:raise ValidationFailed('token数量必须是1～10亿的整数')
    return int(value)

def money(value):
    number=Decimal(value)
    # Keep cents aligned; tiny cache fees retain their full precision.
    return format(number,'.2f') if number==number.quantize(Decimal('.01')) else format(number,'f').rstrip('0')

def calculate(tokens,input_rate,output_rate,cached_rate,*,output_tokens=0,cached_tokens=0):
    for n in (tokens,output_tokens,cached_tokens):
        if not isinstance(n,int) or isinstance(n,bool) or n<0:raise ValidationFailed('计费数量必须是非负整数')
    if cached_tokens>tokens:raise ValidationFailed('缓存数量不能超过输入数量')
    rates=[Decimal(str(p)) for p in (input_rate,output_rate,cached_rate)]
    if any(not r.is_finite() or r<0 for r in rates):raise ValidationFailed('单价必须是有限非负数')
    return ((tokens-cached_tokens)*rates[0]+output_tokens*rates[1]+cached_tokens*rates[2])/Decimal(1_000_000)

def parse_tariff(source,tokens):
    address=urlsplit(source.get('url') or '')
    if address.scheme!='https' or address.hostname!='api-docs.deepseek.com' or address.path.rstrip('/')!='/zh-cn/quick_start/pricing':
        raise ValidationFailed('价格来源必须为DeepSeek官方人民币价格页')
    if source.get('access_state')!='ok' or source.get('excerpt_basis')!='full_text':raise ValidationFailed('官方价格页尚未读取原文')
    text=source.get('excerpt') or ''
    # Check model column order; never silently apply a retired model's tariff.
    if not re.search(r'模型\s+deepseek-flash\s+\(1\)\s+deepseek-v4-pro\s+BASE URL',text):raise ValidationFailed('官方价格表模型列发生变化，请更新解析器')
    rows={}
    for key,pattern in [('cached',r'百万tokens输入\s*（缓存命中）'),('input',r'百万tokens输入\s*（缓存未命中）'),('output',r'百万tokens输出')]:
        hit=re.search(pattern+r'\s*空闲时段\s*([\d.]+)元\s*([\d.]+)元\s*高峰时段\s*([\d.]+)元\s*([\d.]+)元',text)
        if not hit:raise ValidationFailed('官方价格表的'+key+'行无法核验；已停止，未使用旧价')
        rows[key]=hit.groups()
    if not all(term in text for term in ['百万','北京时间','9:00 - 12:00','14:00 - 18:00','法定节假日']):raise ValidationFailed('官方价格页的单位或时段规则发生变化')
    date=datetime.fromisoformat(source['retrieved_at'].replace('Z','+00:00')).astimezone(timezone(timedelta(hours=8))).date().isoformat()
    entries=[]
    for index,(model,band) in enumerate([('Flash','空闲'),('Pro','空闲'),('Flash','高峰'),('Pro','高峰')]):
        rates={k:v[index] for k,v in rows.items()}
        amounts={
          'input':money(calculate(tokens, rates['input'],rates['output'],rates['cached'])),
          'output':money(calculate(0,rates['input'],rates['output'],rates['cached'],output_tokens=tokens)),
          'mixed':money(calculate(tokens//2,rates['input'],rates['output'],rates['cached'],output_tokens=tokens-tokens//2)),
          'cached':money(calculate(tokens,rates['input'],rates['output'],rates['cached'],cached_tokens=tokens))}
        entries.append({'model':model,'band':band,'rates_cny_per_million':rates,'amounts_cny':amounts})
    return {'schema_version':1,'source_id':source['id'],'source_url':source['url'],'source_sha256':source['sha256'],
      'retrieved_date_china':date,'tokens':tokens,'currency':'CNY','unit':'每百万tokens',
      'mixed_input':tokens//2,'mixed_output':tokens-tokens//2,'entries':entries,
      'scope':'DeepSeek官方API理论费用；不等于会员费或中转站账单',
      'peak_rule':'北京时间工作日9–12点、14–18点为高峰；中国法定节假日除外'}

def attach_ledger(result):
    """Called by research: freeze both raw evidence and exact derived arithmetic."""
    from .research_service import SourceModel,ClaimModel
    if re.search(r'claude|gemini|gpt|openai|豆包|通义|千问|kimi',result.topic,re.I) and not re.search(r'deepseek',result.topic,re.I):
        raise ValidationFailed('当前自动费用表支持DeepSeek官方人民币示例；原题指定了其他模型，不能用DeepSeek价格替代。请补充该模型官方价格并选择通用模板。')
    source=next((s.model_dump(mode='json') for s in result.sources if (s.url or '').rstrip('/')==PRICE_URL.rstrip('/')),None)
    if not source:raise ValidationFailed('token费用选题缺少官方单价原文，不能凭模型记忆估价')
    ledger=parse_tariff(source,quantity(result.topic))
    excerpt=json.dumps(ledger,ensure_ascii=False,sort_keys=True)
    result.sources.append(SourceModel(id=LEDGER_ID,kind='derived_calculation',excerpt=excerpt,excerpt_basis='local_file',
      retrieved_at=source['retrieved_at'],sha256=hashlib.sha256(excerpt.encode()).hexdigest(),supports=source['id'],
      limitations='Decimal按冻结的官方人民币单价计算，原文与计算过程可在诊断页查看；不是本次生产的实际账单。'))
    result.claims.append(ClaimModel(id=LEDGER_ID,kind='document_observation',statement=excerpt,source_ids=[LEDGER_ID,source['id']]))
    return ledger

def ledger_from(sources):
    for source in sources or []:
        if source.get('id')==LEDGER_ID and source.get('kind')=='derived_calculation':return json.loads(source['excerpt'])
    return None

def compile_price_page(page,ledger):
    """An editable guide gets a program-owned price table, not guessed numbers."""
    from copy import deepcopy
    result=deepcopy(page);n=ledger['tokens']
    count=str(n//10000)+'万' if n%10000==0 else str(n)
    mixed='各'+str(n//20000)+'万' if n%20000==0 else f"{ledger['mixed_input']}+{ledger['mixed_output']}"
    entries=sorted(ledger['entries'],key=lambda e:(e['model']!='Flash',e['band']!='空闲'))
    result.update(heading=count+'token值多少钱',kicker='API费用 · 人民币',body=[f"DeepSeek示例：混合用量＝输入{ledger['mixed_input']}＋输出{ledger['mixed_output']}token"],
      footnote='DeepSeek官方单价 · '+ledger['retrieved_date_china'],claim_ids=[LEDGER_ID])
    result['visual']={'kind':'cover','title':count+'输入｜'+count+'输出｜'+mixed,
      'items':[{'label':e['model']+' · '+e['band'],'detail':'｜'.join('¥'+e['amounts_cny'][k] for k in ['input','output','mixed']),'icon':'chart'} for e in entries],
      'takeaway':'输入按未缓存计算；输出通常更贵。API费用不是会员费。','presentation':'friendly_guide','presentation_version':1}
    return result

def image_quality_issues(pages):
    """Audit what readers actually see, never substitute a complete caption."""
    from .meme_editorial import visible_page_text
    text='\n'.join(t for p in pages for t in visible_page_text(p))
    # Equivalent whole-hour notation is common in actual model replies.
    # Normalize only for this coverage check; preserve the original image text.
    text=re.sub(r'(?<=[0-9])[:：]00(?![0-9])','',text)
    errors=[]
    for pattern,label in [(r'token.{0,12}(?:不等于|≠|不是).{0,5}字数','token与字数的区别'),
                          (r'(?:usage|实际用量|接口.{0,4}用量)','实际计量口径'),
                          (r'(?:9.{0,3}12|9.{0,3}点).{0,30}14.{0,3}18','高峰时间'),
                          (r'缓存.{0,8}(?:命中|输入)','缓存计费'),
                          (r'(?:÷|/|除以).{0,3}(?:1,?000,?000|100万|百万)','百万token计费公式')]:
        if not re.search(pattern,text,re.I):errors.append('图片正文缺少'+label+'，不能只写在caption或脚注中')
    if any(re.search(r'口径边界|来源披露|来源核验',i.get('label','')) for p in pages for i in p.get('visual',{}).get('items',[])):
        errors.append('费用指南不能把内部口径说明做成主体卡片，请换成具体计算或时段示例')
    return errors

def compile_formula_rows(pages,ledger):
    """Keep prose/layout editable while owning the unit-sensitive arithmetic."""
    example=next(e for e in ledger['entries'] if e['model']=='Flash' and e['band']=='空闲')
    rate=example['rates_cny_per_million']['input']
    # The tariff fields use explicit Decimal strings, never model guesses.
    explanation=f"费用＝token数÷100万×单价。如Flash空闲输入：{ledger['tokens']}÷100万×{rate}＝¥{example['amounts_cny']['input']}，输入输出分开相加。"
    for page in pages:
        for item in page.get('visual',{}).get('items',[]):
            if (re.search(r'公式|怎么算|计算|换算|除以',item.get('label','')) or
                (re.search(r'(?:费用|扣费|成本)\s*[=＝]',item.get('detail','')) and '单价' in item.get('detail',''))):
                item['detail']=explanation;item['icon']='code'
                if LEDGER_ID not in page.setdefault('claim_ids',[]):page['claim_ids'].append(LEDGER_ID)
