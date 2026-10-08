from .platform_account import read_json,write_json

def preferences(settings):
    data=read_json(settings.storage_root/'publishing_preferences.json')
    return {'mode':'assisted' if data.get('mode')=='assisted' else 'manual',
            'background_revisit':data.get('background_revisit') is True}

def save_preferences(settings,mode,background_revisit):
    data={'mode':mode,'background_revisit':background_revisit}
    write_json(settings.storage_root/'publishing_preferences.json',data)
    return data
