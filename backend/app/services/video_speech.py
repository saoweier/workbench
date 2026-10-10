"""Explicit speech providers. No implicit fallback or automatic paid retries."""
import asyncio
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import httpx
from .provider_contract import ProviderStore,ProviderKind,SecretStore

VOICES={'xiaoxiao':'zh-CN-XiaoxiaoNeural','yunxi':'zh-CN-YunxiNeural'}

def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()

def command(args,timeout=180):
    p=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if p.returncode:
        raise ValueError('音视频处理未完成，请检查文件格式与运行环境。')
    return p

class SpeechUncertain(Exception):
    pass

def synthesize(text,path,engine,voice,rate,settings,config_id=None):
    path=Path(path)
    if engine=='local':
        if os.name!='nt':
            raise ValueError('本机朗读目前仅支持 Windows；可选择在线配音。')
        source=path.with_suffix('.txt');source.write_text(text,encoding='utf-8')
        # Paths and narration are passed as environment data, never shell code.
        env={**os.environ,'CWB_NARRATION_FILE':str(source),'CWB_NARRATION_WAV':str(path.with_suffix('.source.wav')),'CWB_NARRATION_RATE':str(round(rate/10))}
        script="""Add-Type -AssemblyName System.Speech
$s=New-Object System.Speech.Synthesis.SpeechSynthesizer
try { $v=$s.GetInstalledVoices() | Where-Object {$_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'zh-CN'} | Select-Object -First 1
if(-not $v){throw 'Chinese voice missing'}
$s.SelectVoice($v.VoiceInfo.Name)
$s.Rate=[int]$env:CWB_NARRATION_RATE
$s.SetOutputToWaveFile($env:CWB_NARRATION_WAV)
$s.Speak([System.IO.File]::ReadAllText($env:CWB_NARRATION_FILE,[System.Text.Encoding]::UTF8))
} finally {$s.Dispose()}
"""
        p=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],env=env,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180,creationflags=subprocess.CREATE_NO_WINDOW)
        if p.returncode:raise ValueError('本机中文朗读不可用，请安装中文系统语音或选择在线配音。')
        raw=path.with_suffix('.source.wav')
    elif engine=='edge':
        import edge_tts
        raw=path.with_suffix('.mp3')
        async def go():
            await asyncio.wait_for(edge_tts.Communicate(text,VOICES[voice],rate=f'{rate:+d}%').save(str(raw)),timeout=100)
        try:asyncio.run(go())
        except Exception:raise SpeechUncertain('在线配音响应未完成；没有自动重试，也没有替换为本机声音。') from None
    elif engine=='api':
        cfg=ProviderStore(settings.storage_root/'provider_configs.json').get(config_id)
        if not cfg or cfg.kind!=ProviderKind.SPEECH or not cfg.enabled:
            raise ValueError('请先配置并启用语音 API。')
        key=SecretStore(settings.secret_store_path).get(cfg.secret_ref)
        if not key:raise ValueError('语音 API 尚未设置密钥。')
        from .network_policy import validate_url,proxies_apply
        validate_url(cfg.base_url,allow_localhost=cfg.allow_localhost)
        raw=path.with_suffix('.mp3')
        try:
            # 语音服务常跑在本机/内网：这类地址直连，不走系统代理。
            with httpx.Client(timeout=cfg.timeout_seconds,follow_redirects=False,trust_env=proxies_apply(cfg.base_url)) as client:
                with client.stream('POST',cfg.base_url.rstrip('/')+'/audio/speech',headers={'Authorization':'Bearer '+key},
                    json={'model':cfg.model_id,'voice':voice,'input':text,'response_format':'mp3','speed':1+rate/100}) as response:
                    if not 200<=response.status_code<300:raise ValueError(f'语音 API 返回 HTTP {response.status_code}，请检查语音模型和声音名称。')
                    total=0
                    with raw.open('wb') as output:
                        for chunk in response.iter_bytes():
                            total+=len(chunk)
                            if total>30*1024*1024:raise ValueError('语音响应超过大小限制。')
                            output.write(chunk)
        except httpx.HTTPError:raise SpeechUncertain('语音 API 结果不确定，系统不会自动再次调用。') from None
    else:raise ValueError('不支持的配音方式。')
    command([ffmpeg(),'-y','-i',str(raw),'-ac','1','-ar','24000','-c:a','pcm_s16le',str(path)])
