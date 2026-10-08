"""Versioned, local direction rules; no model or network call during preview."""
import json
from pathlib import Path

from ..core.errors import ValidationFailed

ROOT = Path(__file__).resolve().parent.parent / 'skills' / 'video-director'


def catalog():
    data=json.loads((ROOT/'presets.json').read_text(encoding='utf-8'))
    return {'skill':'video-director','version':data['version'],'items':data['presets']}


def preset(preset_id):
    data=catalog()
    value=next((p for p in data['items'] if p['id']==preset_id),None)
    if not value:raise ValidationFailed('请选择提供的导演预设。')
    return {**value,'skill':'video-director','version':data['version']}
