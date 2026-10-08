"""Animated presentation frames: supplied photos, diagrams and local vector artwork."""
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

COLORS = ['#dbe8fa', '#dceee5', '#fae8c8', '#f4dfe2']


@lru_cache(maxsize=48)
def font(size):
    for path in ['C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simhei.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
        if Path(path).is_file(): return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def wrap(text, limit):
    return [text[i:i+limit] for i in range(0, len(text), limit)] or ['']


def text_lines(d, text, xy, size, limit, color='#21364b', max_lines=4):
    lines = wrap(text, limit)
    for i, line in enumerate(lines[:max_lines]):
        if i == max_lines-1 and len(lines) > max_lines: line = line[:-1] + '…'
        d.text((xy[0], xy[1]+i*int(size*1.4)), line, font=font(size), fill=color)


def ease(value): return 1-(1-min(1,max(0,value)))**3


def icon(d, name, x, y, scale=1, color='#285f9c', reveal=1):
    def pts(points): return [(x+a*scale,y+b*scale) for a,b in points]
    width=max(2,round(4*scale))
    def line(points):
        points=pts(points);d.line(points[:max(2,math.ceil(len(points)*reveal))],fill=color,width=width,joint='curve')
    def box(rect,r=6):d.rounded_rectangle((x+rect[0]*scale,y+rect[1]*scale,x+rect[2]*scale,y+rect[3]*scale),radius=r*scale,outline=color,width=width)
    if name=='source':
        box((0,6,56,48));line([(28,7),(28,48)]);line([(8,17),(20,17)]);line([(36,17),(48,17)]);line([(8,29),(20,29)]);line([(36,29),(48,29)])
    elif name=='pencil':
        line([(4,42),(38,8),(48,18),(14,52),(2,54),(4,42),(14,52)])
    elif name=='check':
        box((0,0,56,56),12);line([(12,29),(24,40),(45,16)])
    elif name=='clock':
        d.ellipse((x,y,x+56*scale,y+56*scale),outline=color,width=width);line([(28,10),(28,29),(42,37)])
    elif name=='chart':
        line([(2,3),(2,54),(56,54)])
        for i,h in enumerate([22,37,49]):box((12+i*14,54-h*reveal,21+i*14,54),2)
    elif name=='page':
        box((7,0,48,56));line([(17,15),(38,15)]);line([(17,26),(38,26)]);line([(17,37),(32,37)])
    else:
        d.ellipse((x+10*scale,y,x+47*scale,y+39*scale),outline=color,width=width);line([(19,39),(19,49),(38,49),(38,39)]);line([(22,56),(35,56)])


def art(d, scene, y, phase):
    # Editorial composition of relevant drawings rather than random decorations.
    cards=[(88,y+48,-12),(288,y,0),(495,y+75,12)]
    for i,(x,cy,offset) in enumerate(cards):
        cy += int((1-ease(phase-i*.1))*45)
        d.rounded_rectangle((x,cy,x+133,cy+178),radius=14,fill=COLORS[i])
        icon(d, scene['icon'] if i==1 else ('page' if i==0 else 'check'), x+36,cy+32,1.1,reveal=ease(phase))
        for j,length in enumerate([76,55,65]):d.rounded_rectangle((x+28,cy+112+j*16,x+28+length,cy+116+j*16),radius=2,fill='#99b1c2')
    length=int(185*ease(phase))
    d.line((219,y+148,219+length,y+148),fill='#7092b6',width=4)
    d.polygon([(219+length,y+148),(209+length,y+142),(209+length,y+154)],fill='#7092b6')


def frame(scene, source, progress, custom=None, show_presenter='none', page_title='', page_count=1):
    p=min(1,max(0,progress));entrance=ease(p*5)
    canvas=Image.new('RGB',(720,1280),'#f4f6f9');d=ImageDraw.Draw(canvas)
    d.rounded_rectangle((32,32,190,70),radius=9,fill='#e0eafa')
    d.text((46,41),'图文讲解 · 分镜',font=font(18),fill='#315f96')
    d.text((590,42),f"{scene['page_index']+1}/{page_count}",font=font(20),fill='#6c7e90')
    text_lines(d,page_title,(36,102),36,18,max_lines=2)
    kind=scene['effect'];items=scene['items'];shift=int((1-entrance)*30)
    d.text((38,224),scene['effect_label'],font=font(18),fill='#6b7b8f')
    if kind in {'focus','image'}:
        viewport=(36,275,684,945);w,h=648,670
        image=ImageOps.contain(source.convert('RGB'),(w,h),Image.Resampling.LANCZOS)
        if kind=='focus':
            zoom=1+.075*p;image=image.resize((round(image.width*zoom),round(image.height*zoom)),Image.Resampling.BICUBIC)
        layer=Image.new('RGB',(w,h),'#e6ecf3');layer.paste(image,((w-image.width)//2,int((h-image.height)/2+(1-entrance)*45)))
        canvas.paste(layer,(36,275));d=ImageDraw.Draw(canvas)
        d.rounded_rectangle((52,820+shift,650,924+shift),radius=16,fill='#21364b')
        text_lines(d,scene['title'],(72,836+shift),28,19,color='#f7fafc',max_lines=2)
    elif kind=='scroll':
        lane=Image.new('RGB',(648,670),'#e8eef5');ld=ImageDraw.Draw(lane)
        image=ImageOps.contain(source.convert('RGB'),(225,305),Image.Resampling.LANCZOS)
        offset=int(p*135)
        for i,value in enumerate(items[:4]):
            cy=25+i*210-offset
            ld.rounded_rectangle((14,cy,630,cy+188),radius=16,fill=COLORS[i])
            thumb=ImageOps.contain(image,(120,166),Image.Resampling.LANCZOS)
            lane.paste(thumb,(30,cy+12))
            icon(ld,scene['icon'],173,cy+22,.65)
            text_lines(ld,value,(173,cy+83),26,16,max_lines=2)
        canvas.paste(lane,(36,275));d=ImageDraw.Draw(canvas)
        d.text((40,953),'图文与讲解要点滚动展示',font=font(19),fill='#6b7b8f')
    elif kind=='keyword':
        icon(d,scene['icon'],54,288+shift,1.8,reveal=entrance)
        text_lines(d,scene['title'],(54,424+shift),52,11,max_lines=3)
        line_width=int(530*ease(p*3));d.rounded_rectangle((54,674,54+max(1,line_width),683),radius=3,fill='#e5ac4e')
        art(d,scene,710,p*3)
    elif kind=='compare':
        sides=items[:2] if len(items)>1 else [scene['text'],page_title]
        for i,text in enumerate(sides):
            x=36+i*330;cy=300+int((1-ease(p*4-i*.3))*45)
            d.rounded_rectangle((x,cy,x+318,916),radius=22,fill=COLORS[i])
            icon(d,scene['icon'] if i==0 else 'check',x+26,cy+44,1.3,reveal=entrance)
            d.text((x+24,cy+155),f'0{i+1}',font=font(26),fill='#7593a8')
            text_lines(d,text,(x+24,cy+226),32,8,max_lines=6)
        d.ellipse((334,539,386,591),fill='#f4f6f9');d.text((345,547),'→',font=font(28),fill='#315f96')
    elif kind in {'flow','checklist'}:
        for i,text in enumerate(items[:4]):
            reveal=ease(p*5-i*.45);cy=285+i*154+int((1-reveal)*38)
            if reveal<=0:continue
            d.rounded_rectangle((36,cy,684,cy+126),radius=16,fill=COLORS[i])
            if kind=='checklist':icon(d,'check',57,cy+35,.8,reveal=reveal)
            else:d.text((57,cy+38),f'{i+1:02}',font=font(28),fill='#315f96')
            text_lines(d,text,(132,cy+25),28,18,max_lines=2)
            if kind=='flow' and i<len(items)-1:d.text((345,cy+122),'↓',font=font(25),fill='#7593a8')
        d.text((40,925),'按讲稿展开的内容',font=font(19),fill='#6b7b8f')
    elif kind=='chart':
        values=scene['chart'];maximum=max(v['value'] for v in values) or 1
        for i,v in enumerate(values[:4]):
            cy=310+i*155;text_lines(d,v['label'],(42,cy),24,24,max_lines=1)
            extent=max(1,int(540*v['value']/maximum*ease(p*3-i*.2)))
            d.rounded_rectangle((42,cy+47,42+extent,cy+99),radius=8,fill='#598bcb')
            d.text((52,cy+55),f"{v['value']:g}{v['unit']}",font=font(24),fill='#21364b' if extent<130 else '#fafcfe')
        d.text((42,951),'数据来自当前句子的原文',font=font(19),fill='#6b7b8f')
    # Progress dots describe the current page's sentence beats, never quantitative data.
    for i in range(min(12,scene.get('page_scene_count',1))):
        x=36+i*23;d.ellipse((x,1002,x+9,1011),fill='#3679cd' if i==scene['sentence_index'] else '#cfdae7')
    caption=wrap(scene['text'],18 if show_presenter!='none' else 22)
    window=min(max(0,len(caption)-2),int(p*max(1,len(caption)-1)))
    for i,line in enumerate(caption[window:window+2]):d.text((36,1080+i*39),line,font=font(26),fill='#21364b')
    if custom is not None:
        character=ImageOps.contain(custom,(160,240),Image.Resampling.LANCZOS)
        cy=1215-character.height+int(math.sin(p*math.pi*2)*3)
        canvas.paste(character,(612-character.width//2,cy),character)
    d.text((36,1231),'动态分镜 · 字幕与镜头时间按句长估算',font=font(16),fill='#748697')
    return canvas
