"""Verified fruit catalogue, local image assets and topic acceptance criteria.

The model chooses catalogue IDs and prose; numbers and image bytes belong to
local code. The fixed SR Legacy extract is not a live search or a GI database.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from ..core.errors import ValidationFailed

ROOT = Path(__file__).resolve().parents[1] / 'resources' / 'fruit'
FRUIT_IDS = ('apple', 'pear', 'orange', 'kiwi', 'grape', 'persimmon')


@lru_cache(maxsize=1)
def catalogue():
    return json.loads((ROOT / 'nutrition.json').read_text(encoding='utf-8'))


def fruit_index():
    return {f['id']: f for f in catalogue()['fruits']}


def is_fruit_topic(topic: str) -> bool:
    # Match the subject, not quoted references, code, or general creator topics.
    return bool(re.search(r'水果|秋果|鲜果|果汁|吃果|猕猴桃|柿子', topic)) or bool(
        re.search(r'苹果|葡萄|梨|橙', topic) and re.search(r'吃|营养|膳食|控糖|食用', topic))


def required_fruits(topic: str) -> set[str]:
    if re.search(r'水果|秋果|鲜果', topic):
        return set(FRUIT_IDS)
    return {k for k, f in fruit_index().items() if f['name'].replace('绿肉', '') in topic}


def guidance(topic: str) -> str:
    if not is_fruit_topic(topic):
        return ''
    return (
        '\n题材验收要求：这是水果选购与吃法内容，必须直接推荐具体水果和具体吃法；'
        '不要写成创作者审稿教程、填表教程或只说少量适量。'
        '按原选题的生活场景组织；怕凉指食用温度偏好，不诊断寒性体质。'
        '提供品种对照、可执行搭配或处理步骤，说明其为编辑建议。'
        '营养数值只能使用提供的 USDA 每100克生鲜可食部资料，热量/碳水/糖/纤维/维C分开；'
        '数据有品种与成熟度差异，不能当熟果、果汁或中国某品种的实测。'
        '没有GI依据就不填GI、不宣称低GI；不能承诺治秋燥、止咳、降血糖、暖胃。'
        '碳水对照不能代替糖尿病个体营养方案，不按水果计数推算重量。'
        '禁止无来源的疾病功效和编造用户留言、实测经历。'
    )


def evidence():
    """Small immutable subset extracted from the official USDA archive."""
    pack = catalogue()
    sources, claims = [], []
    for f in pack['fruits']:
        sid, cid = 'FDC_' + f['id'], 'FRUIT_' + f['id']
        excerpt = json.dumps({'description': f['description'], 'fdc_id': f['fdc_id'],
                              'per_100g_edible_raw': f['nutrients'], 'scope': pack['scope']}, ensure_ascii=False)
        sources.append({'id': sid, 'kind': 'official_nutrition_dataset',
            'url': f"https://fdc.nal.usda.gov/food-details/{f['fdc_id']}/nutrients",
            'retrieved_at': '2026-10-04T00:00:00+00:00',
            'locator': f"SR Legacy 2018, fdc_id={f['fdc_id']}; food_nutrient.csv",
            'excerpt': excerpt, 'excerpt_basis': 'full_text',
            'sha256': hashlib.sha256(excerpt.encode()).hexdigest(),
            'access_state': 'ok', 'supports': '生鲜每100克营养参数',
            'limitations': pack['scope'] + ' 官方原始压缩包SHA256：' + pack['dataset_sha256']})
        n = f['nutrients']
        claims.append({'id': cid, 'kind': 'fact', 'source_ids': [sid],
            'statement': f"{f['name']}（{f['description']}），每100克生鲜可食部："
                f"热量{n['energy_kcal']:g}kcal，碳水{n['carbohydrate_g']:g}g，"
                f"总糖{n['sugars_g']:g}g，纤维{n['fiber_g']:g}g，"
                f"维C{n['vitamin_c_mg']:g}mg，水分{n['water_g']:g}g。不是GI数据。"})
    guide = json.loads((ROOT / 'guideline.json').read_text(encoding='utf-8'))
    sources.append(guide['source'])
    claims.append(guide['claim'])
    return sources, claims


def validate_subject(raw: dict, topic: str, *, platform: bool = False):
    """Reject the observed C010 failure before saving a reviewable draft."""
    if not is_fruit_topic(topic):
        return
    text = json.dumps(raw, ensure_ascii=False)
    names = [f['name'].replace('绿肉', '') for f in catalogue()['fruits']]
    count = sum(name in text for name in names)
    minimum = min(4, max(1, len(required_fruits(topic))))
    errors = []
    if count < minimum:
        errors.append(f'须直接介绍至少{minimum}种具体水果，不能用“水分足的鲜果”等泛称替代')
    if not any(w in text for w in ('切片', '切块', '切瓣', '蒸', '搭配', '分装', '洗净', '去核')):
        errors.append('缺少具体吃法或处理步骤')
    if any(w in text for w in ('审稿判断', '秋果自检卡', '填写示例', '不排水果榜', '没有给具体品种')):
        errors.append('水果产品被改写成填表/审稿教程或主动回避具体品种')
    if platform:
        pages = raw.get('pages') or []
        refs = {i.get('photo_id') for p in pages for i in (p.get('visual') or {}).get('items', []) if i.get('photo_id')}
        needed = required_fruits(topic)
        if not needed.issubset(refs):
            errors.append('缺少选题所需的水果示意图：' + ','.join(sorted(needed - refs)))
        if not any((p.get('visual') or {}).get('kind') == 'nutrition' for p in pages):
            errors.append('缺少由资料库生成的每100克营养对照表')
        for p in pages:
            visual = p.get('visual') or {}
            for item in visual.get('items') or []:
                fid = item.get('photo_id')
                if fid and ('FRUIT_' + fid) not in p.get('claim_ids', []):
                    errors.append(f"第{p.get('index')}页水果{fid}未引用其营养资料主张")
        caption = raw.get('caption', '')
        if 'USDA' not in caption or '100' not in caption:
            errors.append('发布文案须交代USDA及每100克生鲜可食部口径')
        if not any(w in caption for w in ('差异', '参考', '样本')):
            errors.append('须交代品种/样本差异')
        if 'AI' not in caption or '示意' not in caption:
            errors.append('发布文案须标注水果配图为AI示意图')
    if errors:
        raise ValidationFailed('选题内容验收未通过：' + '；'.join(dict.fromkeys(errors)))


@lru_cache(maxsize=1)
def atlas_uri():
    metadata = json.loads((ROOT / 'imagery.json').read_text(encoding='utf-8'))
    raw = (ROOT / 'fruit-atlas-v1.png').read_bytes()
    if hashlib.sha256(raw).hexdigest() != metadata['sha256']:
        raise ValueError('水果图片与已冻结的素材校验值不符')
    return 'data:image/png;base64,' + base64.b64encode(raw).decode()


def photo_svg(fid: str) -> str:
    if fid not in FRUIT_IDS:
        raise ValueError('水果图片编号不在本地素材库')
    n = FRUIT_IDS.index(fid)
    # The original bitmap is preserved. SVG is a bounded viewport over the atlas.
    return (f'<svg class="fruit-photo" viewBox="{n%3*418} {n//3*627} 418 627" '
            f'role="img" aria-label="{fruit_index()[fid]["name"]}，AI写实示意图">'
            f'<image href="{atlas_uri()}" width="1254" height="1254"/></svg>')
