"""Deterministic sentence storyboard, using only the creator's supplied content."""
import hashlib
import re

from ..core.errors import ValidationFailed
from .video_director import preset

EFFECTS = {'focus': '图片推近', 'keyword': '关键词强调', 'flow': '流程推进',
           'compare': '左右对比', 'checklist': '逐项勾选', 'chart': '数据图表', 'image': '素材切换', 'scroll': '素材滚动'}
ICONS = [('source', '来源|资料|证据|核对|研究'), ('pencil', '编辑|改稿|写|修改'),
         ('check', '审核|检查|确认|批准|完成'), ('chart', '数据|指标|统计|复盘|记录'),
         ('clock', '时间|分钟|秒|省时|效率'), ('page', '图文|图片|视频|素材|内容|发布'),
         ('idea', 'AI|模型|自动|思路|观点')]
VERSION = 1


def sentences(text):
    # Keep punctuation with the spoken sentence; newlines also delimit beats.
    result = []
    prefix = ''
    for part in re.findall(r'[^。！？!?；;\n]+[。！？!?；;]*|[。！？!?；;]+', text):
        part = part.strip()
        if not part:
            continue
        if not part.strip('。！？!?；;'):
            if result: result[-1] += part
            else: prefix += part
            continue
        part = prefix + part
        prefix = ''
        if len(part) > 80:
            clauses = re.findall(r'[^，,]+[，,]?', part)
            group = ''
            for clause in clauses:
                if group and len(group) + len(clause) > 65:
                    result.append(group); group = ''
                while len(clause) > 80:
                    if group: result.append(group); group = ''
                    result.append(clause[:65]); clause = clause[65:]
                group += clause
            if group: result.append(group)
        else:
            result.append(part)
    return result


def items_for(text, page):
    clauses = [s.strip('，,。！？!?；;：: ') for s in re.split(r'[，,：:、]|(?:→)', text)]
    clauses = [s for s in clauses if s]
    # These are text fragments, never invented supporting claims or statistics.
    if len(clauses) < 2:
        clauses = [i.get('label', '') for i in page.get('visual', {}).get('items', []) if i.get('label')]
    if not clauses:
        clauses = [text.strip('。！？!?；;')]
    return clauses[:4]


def chart_data(text):
    values = []
    for clause in re.split(r'[，,；;。]', text):
        match = re.search(r'(\d+(?:\.\d+)?)\s*(%|％|分钟|秒|次|个|元)', clause)
        if match:
            label = clause[:match.start()].strip(' ：:')[-12:] or '原文数值'
            values.append({'label': label, 'value': float(match[1]), 'unit': match[2].replace('％', '%')})
    return values if len(values) >= 2 and len({v['unit'] for v in values}) == 1 else []


def plan(pages, choices=None, director_preset='knowledge'):
    director=preset(director_preset)
    overrides = {c['scene_id']: c for c in (choices or [])}
    scenes = []; cursor = 0
    for page_number, page in enumerate(pages):
        beats = sentences(page['script'])
        if not beats:
            raise ValidationFailed('每章讲稿需要包含文字，不能只有标点。')
        for sentence_number, text in enumerate(beats):
            scene_id = hashlib.sha256(f'{page_number}:{sentence_number}:{text}'.encode()).hexdigest()[:24]
            words = items_for(text, page); chart = chart_data(text)
            kind = page.get('visual', {}).get('kind')
            intents={'chart':bool(chart),
                'compare':bool(re.search('不是|而是|相比|对比|区别|但是|与其',text)) or kind=='compare',
                'flow':bool(re.search('先|然后|再|流程|步骤|第一|第二|第三|→',text)) or kind=='flow',
                'checklist':bool(re.search('检查|核对|审核|确认|清单|记录',text)) or kind=='checklist',
                'scroll':bool(re.search('素材|资料|来源|多张|图文|逐页',text))}
            fallback=director['fallback_sequence']
            effect=next((name for name in director['intent_priority'] if intents[name]),fallback[sentence_number%len(fallback)])
            override = overrides.get(scene_id, {})
            effect = override.get('effect') or effect
            if effect == 'chart' and not chart:
                raise ValidationFailed('这一句没有两个同单位的数值，不能生成数据图表。可选择流程或对比。')
            material_id = override.get('material_id')
            if material_id: effect = 'image'
            icon = next((name for name, pattern in ICONS if re.search(pattern, text, re.I)), 'idea')
            duration = round(max(2.4, min(18, len(text) / 4.3)), 2)
            title = re.split(r'[，,：:]',text.strip('。！？!?；;'))[0][:22]
            scenes.append({'id': scene_id, 'page_index': page_number, 'sentence_index': sentence_number,
                'text': text, 'title': title, 'items': words, 'effect': effect,
                'effect_label': EFFECTS[effect], 'icon': icon, 'chart': chart,
                'material_id': material_id, 'start': round(cursor, 2), 'duration': duration,
                'image_url': page['image_url']})
            cursor += duration
    if len(scenes) > 400:
        raise ValidationFailed('讲解段落超过 400 段，请精简讲稿。')
    return {'version': VERSION, 'director':director, 'scenes': scenes, 'duration_estimate': round(cursor, 2),
            'timing': 'estimated_by_sentence_length', 'effects': EFFECTS}


def timed_scenes(scenes, duration):
    """Weight sentence boundaries inside each actual page audio clip."""
    weights = [max(1, len(s['text'])) for s in scenes]
    total = sum(weights); start = 0; result = []
    for scene, weight in zip(scenes, weights):
        end = start + duration * weight / total
        result.append({**scene, 'start': start, 'end': end}); start = end
    return result
