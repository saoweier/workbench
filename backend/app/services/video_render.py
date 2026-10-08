"""Vertical illustrated video with burned-in captions and an animated presenter."""
from datetime import timedelta
import math
import json
import os
from pathlib import Path
import subprocess
import time
import wave
from functools import lru_cache
from PIL import Image,ImageDraw,ImageFont,ImageOps
from .video_speech import ffmpeg
from .video_storyboard import timed_scenes
from .video_motion import frame as motion_frame

@lru_cache(maxsize=32)
def font(size):
    for path in ['C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/simhei.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
        if Path(path).is_file():return ImageFont.truetype(path,size)
    return ImageFont.load_default(size=size)

def chunks(text,limit=22):
    return [text[i:i+limit] for i in range(0,len(text),limit)] or ['']

def stamp(seconds):
    milliseconds=round(seconds*1000)
    h,rest=divmod(milliseconds,3600000);m,rest=divmod(rest,60000);s,ms=divmod(rest,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'

def presenter(draw,t):
    # Original geometric avatar; no real person's face or cloned voice.
    x,y=625,1100+int(math.sin(t*2)*3)
    draw.rounded_rectangle((x-43,y+29,x+43,y+119),radius=22,fill='#245dd9')
    draw.ellipse((x-39,y-51,x+39,y+36),fill='#f5c4a0')
    draw.pieslice((x-42,y-64,x+41,y+5),180,355,fill='#29374a')
    blink=int(t*12)%53<3
    for ex in [x-14,x+14]:
        if blink:draw.line((ex-4,y-13,ex+4,y-13),fill='#263544',width=3)
        else:draw.ellipse((ex-3,y-17,ex+3,y-10),fill='#263544')
    opened=int(t*9)%3!=0
    draw.ellipse((x-8,y+4,x+8,y+(16 if opened else 8)),fill='#9e433e')
    draw.line((x-37,y+59,x-61,y+30+int(math.sin(t*3)*12)),fill='#245dd9',width=13)
    draw.ellipse((x-67,y+19+int(math.sin(t*3)*12),x-53,y+36+int(math.sin(t*3)*12)),fill='#f5c4a0')

def render(payload,image_paths,audio_paths,root,progress):
    animated=payload.get('animation_style')=='presentation'
    root=Path(root);fps=24 if animated else 12;width,height=720,1280
    material_images={}
    for asset_id,path in payload.get('material_paths',{}).items():
        with Image.open(path) as material:material_images[asset_id]=material.convert('RGB').copy()
    custom=None
    if payload['presenter']=='custom':
        with Image.open(payload['presenter_image_path']) as source:
            custom=ImageOps.contain(source.convert('RGBA'),(160,240),Image.Resampling.LANCZOS)
    durations=[];audio=root/'narration.wav'
    with wave.open(str(audio),'wb') as merged:
        merged.setnchannels(1);merged.setsampwidth(2);merged.setframerate(24000)
        for path in audio_paths:
            with wave.open(str(path),'rb') as clip:
                if (clip.getnchannels(),clip.getsampwidth(),clip.getframerate())!=(1,2,24000):raise ValueError('配音格式不一致。')
                durations.append(clip.getnframes()/24000)
                merged.writeframes(clip.readframes(clip.getnframes()))
    if not 0<sum(durations)<=1200:raise ValueError('视频配音时长须在 20 分钟内。')
    frames=[max(1,math.ceil(d*fps)) for d in durations]
    # Align audio/page boundaries to video frame boundaries with explicit silence.
    with wave.open(str(audio),'wb') as merged:
        merged.setnchannels(1);merged.setsampwidth(2);merged.setframerate(24000)
        for path,count in zip(audio_paths,frames):
            with wave.open(str(path),'rb') as clip:
                data=clip.readframes(clip.getnframes());merged.writeframes(data)
                pad=round(count/fps*24000)-len(data)//2
                if pad>0:merged.writeframes(b'\0\0'*pad)
    srt=[];start=0;number=0
    scene_timelines=[]
    for page_index,(page,count) in enumerate(zip(payload['pages'],frames)):
        if animated:
            scenes=[s for s in payload['storyboard']['scenes'] if s['page_index']==page_index]
            timeline=timed_scenes(scenes,count/fps);scene_timelines.append(timeline)
            for scene in timeline:
                pieces=chunks(scene['text'])
                for i,piece in enumerate(pieces):
                    a=start+scene['start']+(scene['end']-scene['start'])*i/len(pieces)
                    b=start+scene['start']+(scene['end']-scene['start'])*(i+1)/len(pieces)
                    number+=1;srt.append(f'{number}\n{stamp(a)} --> {stamp(b)}\n{piece}\n')
        else:
            pieces=chunks(page['script'])
            for i,piece in enumerate(pieces):
                number+=1;a=start+(count/fps)*i/len(pieces);b=start+(count/fps)*(i+1)/len(pieces)
                srt.append(f'{number}\n{stamp(a)} --> {stamp(b)}\n{piece}\n')
        start+=count/fps
    (root/'subtitles.srt').write_text('\n'.join(srt),encoding='utf-8')
    if animated:
        actual_scenes=[];offset=0
        for timeline,count in zip(scene_timelines,frames):
            actual_scenes.extend({**scene,'start':round(offset+scene['start'],3),'end':round(offset+scene['end'],3)} for scene in timeline)
            offset+=count/fps
        (root/'storyboard.json').write_text(json.dumps({**payload['storyboard'],'scenes':actual_scenes,
            'duration_seconds':round(offset,3),'timing':'estimated_by_sentence_length'},ensure_ascii=False,indent=2),encoding='utf-8')
    output=root/'video.partial.mp4';log=(root/'encoder.log').open('wb')
    p=subprocess.Popen([ffmpeg(),'-y','-f','rawvideo','-vcodec','rawvideo','-pix_fmt','rgb24','-s','720x1280','-r',str(fps),
        '-i','-','-i',str(audio),'-c:v','libx264','-preset','veryfast','-threads','2','-crf','23','-pix_fmt','yuv420p',
        '-c:a','aac','-b:a','128k','-movflags','+faststart',str(output)],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,
        stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        total=sum(frames);done=0;began=time.monotonic()
        for index,(path,page,count) in enumerate(zip(image_paths,payload['pages'],frames)):
            base=Image.new('RGB',(width,height),'#eff4f9');d=ImageDraw.Draw(base)
            d.text((30,32),'内容工作台 · 图文讲解',font=font(23),fill='#20374e')
            d.text((610,37),f'{index+1}/{len(frames)}',font=font(20),fill='#526576')
            with Image.open(path) as source:
                original=source.convert('RGB').copy()
                image_height=840 if custom is not None else 900
                image=ImageOps.contain(source.convert('RGB'),(660,image_height))
                base.paste(image,((width-image.width)//2,105+(image_height-image.height)//2))
            if custom is not None:
                base.paste(custom,(612-custom.width//2,1215-custom.height),custom)
            pieces=chunks(page['script'])
            for n in range(count):
                if animated:
                    timeline=scene_timelines[index];at=n/fps
                    scene=next((s for s in timeline if at<s['end']),timeline[-1])
                    phase=(at-scene['start'])/max(.01,scene['end']-scene['start'])
                    visual=material_images.get(scene.get('material_id'),original)
                    frame=motion_frame({**scene,'page_scene_count':len(timeline)},visual,phase,custom,
                        payload['presenter'],page['heading'],len(frames))
                    if payload['presenter']=='guide':presenter(ImageDraw.Draw(frame),at)
                    p.stdin.write(frame.tobytes());done+=1
                    if done%24==0:
                        progress(35+int(done/total*60))
                        if time.monotonic()-began>1200:raise ValueError('视频渲染超时。')
                    continue
                frame=base.copy();draw=ImageDraw.Draw(frame)
                piece=pieces[min(len(pieces)-1,int(n/count*len(pieces)))]
                for line_num,line in enumerate(chunks(piece,18 if payload['presenter']!='none' else 22)):
                    draw.text((32,1080+line_num*39),line,font=font(26 if custom is not None else 29),fill='#172e45')
                if payload['presenter']=='guide':presenter(draw,n/fps)
                label='本机系统朗读' if payload['engine']=='local' else 'AI 合成配音'
                draw.text((32,1230),label+' · 字幕时间按段落估算',font=font(17),fill='#63778b')
                p.stdin.write(frame.tobytes());done+=1
                if done%24==0:
                    progress(35+int(done/total*60))
                    if time.monotonic()-began>1200:raise ValueError('视频渲染超时。')
        p.stdin.close()
        if p.wait(timeout=120)!=0:raise ValueError('视频编码失败，请检查磁盘空间。')
        output.replace(root/'video.mp4')
    except Exception:
        p.kill();p.wait(timeout=10);raise
    finally:log.close()
    return {'duration_seconds':round(sum(frames)/fps,2),'width':width,'height':height,'fps':fps,
            'caption_timing':'estimated_by_sentence_length' if animated else 'estimated_by_paragraph',
            'animation_style':'presentation' if animated else 'classic',
            'scene_count':len(payload['storyboard']['scenes']) if animated else len(payload['pages']),
            'storyboard_version':payload.get('storyboard',{}).get('version'),
            'director':{key:payload['storyboard']['director'][key] for key in ['id','name','version']} if animated else None,
            'presenter_kind':{'guide':'animated_original','custom':'custom_image','none':'none'}[payload['presenter']],
            'voice_source':payload['engine'],'size_bytes':(root/'video.mp4').stat().st_size}
