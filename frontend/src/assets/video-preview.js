// The same scene vocabulary, text and 720×1280 composition used by MP4 export.
const tones=['#dbe8fa','#dceee5','#fae8c8','#f4dfe2'];
const images=new Map();
export function loadImage(url){
  if(!url)return Promise.resolve(null);
  if(!images.has(url))images.set(url,new Promise(resolve=>{const image=new Image();image.onload=()=>resolve(image);image.onerror=()=>{images.delete(url);resolve(null);};image.src=url;}));
  return images.get(url);
}
const ease=t=>1-(1-Math.max(0,Math.min(1,t)))**3;
function box(c,x,y,w,h,color,r=12){c.fillStyle=color;c.beginPath();c.roundRect(x,y,w,h,r);c.fill();}
function text(c,value,x,y,size=28,limit=18,color='#21364b',max=4){c.font=`${size}px "Microsoft YaHei",sans-serif`;c.fillStyle=color;c.textBaseline='top';const chars=Array.from(value);for(let i=0;i<Math.min(max,Math.ceil(chars.length/limit));i++){let line=chars.slice(i*limit,(i+1)*limit).join('');if(i===max-1&&chars.length>max*limit)line=line.slice(0,-1)+'…';c.fillText(line,x,y+i*size*1.4);}}
function line(c,points,color='#285f9c',width=4){c.strokeStyle=color;c.lineWidth=width;c.lineCap='round';c.lineJoin='round';c.beginPath();points.forEach(([x,y],i)=>i?c.lineTo(x,y):c.moveTo(x,y));c.stroke();}
function icon(c,name,x,y,s=1,reveal=1){c.save();c.translate(x,y);c.scale(s,s);c.strokeStyle='#285f9c';c.lineWidth=4;c.lineCap='round';c.lineJoin='round';const stroke=ps=>line(c,ps.slice(0,Math.max(2,Math.ceil(ps.length*reveal))));const rect=(x,y,w,h,r=6)=>{c.beginPath();c.roundRect(x,y,w,h,r);c.stroke();};
  if(name==='source'){rect(0,6,56,42);stroke([[28,7],[28,48]]);for(const x of [8,36])for(const y of [17,29])stroke([[x,y],[x+12,y]]);}
  else if(name==='pencil')stroke([[4,42],[38,8],[48,18],[14,52],[2,54],[4,42],[14,52]]);
  else if(name==='check'){rect(0,0,56,56,12);stroke([[12,29],[24,40],[45,16]]);}
  else if(name==='clock'){c.beginPath();c.arc(28,28,28,0,Math.PI*2);c.stroke();stroke([[28,10],[28,29],[42,37]]);}
  else if(name==='chart'){stroke([[2,3],[2,54],[56,54]]);[22,37,49].forEach((h,i)=>rect(12+i*14,54-h*reveal,9,h*reveal,2));}
  else if(name==='page'){rect(7,0,41,56);[15,26,37].forEach(y=>stroke([[17,y],[y===37?32:38,y]]));}
  else{c.beginPath();c.ellipse(28,20,19,20,0,0,Math.PI*2);c.stroke();stroke([[19,39],[19,49],[38,49],[38,39]]);stroke([[22,56],[35,56]]);}
  c.restore();
}
function artwork(c,scene,y,p){[[88,y+48],[288,y],[495,y+75]].forEach(([x,cy],i)=>{cy+=(1-ease(p-i*.1))*45;box(c,x,cy,133,178,tones[i],14);icon(c,i===1?scene.icon:i===0?'page':'check',x+36,cy+32,1.1,ease(p));[76,55,65].forEach((length,j)=>box(c,x+28,cy+112+j*16,length,4,'#99b1c2',2));});const len=185*ease(p);line(c,[[219,y+148],[219+len,y+148]],'#7092b6',4);text(c,'→',199+len,y+129,25,2,'#7092b6',1);}
export function drawPreview(canvas,scene,p,pageImage,character,options={}){
  const c=canvas.getContext('2d');c.clearRect(0,0,720,1280);box(c,0,0,720,1280,'#f4f6f9',0);
  if(!scene){text(c,'选择图文，开始编排讲解',70,530,35,16);return;}
  const entrance=ease(p*5),shift=(1-entrance)*30;
  box(c,32,32,158,38,'#e0eafa',9);text(c,'图文讲解 · 分镜',46,41,18,20,'#315f96',1);text(c,`${scene.page_index+1}/${options.pageCount||1}`,590,42,20,10,'#6c7e90',1);
  text(c,options.pageTitle||'',36,102,36,18,'#21364b',2);text(c,scene.effect_label,38,224,18,20,'#6b7b8f',1);
  if(options.style==='classic'){
    if(pageImage){const scale=Math.min(648/pageImage.width,730/pageImage.height);c.drawImage(pageImage,(720-pageImage.width*scale)/2,250+(730-pageImage.height*scale)/2,pageImage.width*scale,pageImage.height*scale);}
  }else if(['focus','image'].includes(scene.effect)){
    box(c,36,275,648,670,'#e6ecf3',0);
    if(pageImage){let scale=Math.min(648/pageImage.width,670/pageImage.height);if(scene.effect==='focus')scale*=1+.075*p;c.save();c.beginPath();c.rect(36,275,648,670);c.clip();c.drawImage(pageImage,36+(648-pageImage.width*scale)/2,275+(670-pageImage.height*scale)/2+(1-entrance)*45,pageImage.width*scale,pageImage.height*scale);c.restore();}
    box(c,52,820+shift,598,104,'#21364b',16);text(c,scene.title,72,836+shift,28,19,'#f7fafc',2);
  }else if(scene.effect==='scroll'){
    box(c,36,275,648,670,'#e8eef5',0);c.save();c.beginPath();c.rect(36,275,648,670);c.clip();
    scene.items.slice(0,4).forEach((value,i)=>{const y=300+i*210-p*135;box(c,50,y,616,188,tones[i],16);if(pageImage){const scale=Math.min(120/pageImage.width,166/pageImage.height);c.drawImage(pageImage,66,y+12,pageImage.width*scale,pageImage.height*scale);}icon(c,scene.icon,209,y+22,.65);text(c,value,209,y+83,26,16,'#21364b',2);});c.restore();text(c,'图文与讲解要点滚动展示',40,953,19,26,'#6b7b8f',1);
  }else if(scene.effect==='keyword'){
    icon(c,scene.icon,54,288+shift,1.8,entrance);text(c,scene.title,54,424+shift,52,11,'#21364b',3);box(c,54,674,Math.max(1,530*ease(p*3)),9,'#e5ac4e',3);artwork(c,scene,710,p*3);
  }else if(scene.effect==='compare'){
    const sides=scene.items.length>1?scene.items.slice(0,2):[scene.text,options.pageTitle||''];sides.forEach((value,i)=>{const x=36+i*330,y=300+(1-ease(p*4-i*.3))*45;box(c,x,y,318,916-y,tones[i],22);icon(c,i===0?scene.icon:'check',x+26,y+44,1.3,entrance);text(c,`0${i+1}`,x+24,y+155,26,5,'#7593a8',1);text(c,value,x+24,y+226,32,8,'#21364b',6);});box(c,334,539,52,52,'#f4f6f9',26);text(c,'→',345,547,28,2,'#315f96',1);
  }else if(['flow','checklist'].includes(scene.effect)){
    scene.items.slice(0,4).forEach((value,i)=>{const reveal=ease(p*5-i*.45),y=285+i*154+(1-reveal)*38;if(reveal<=0)return;box(c,36,y,648,126,tones[i],16);if(scene.effect==='checklist')icon(c,'check',57,y+35,.8,reveal);else text(c,`${i+1}`.padStart(2,'0'),57,y+38,28,5,'#315f96',1);text(c,value,132,y+25,28,18,'#21364b',2);if(scene.effect==='flow'&&i<scene.items.length-1)text(c,'↓',345,y+122,25,1,'#7593a8',1);});text(c,'按讲稿展开的内容',40,925,19,22,'#6b7b8f',1);
  }else if(scene.effect==='chart'){
    const maximum=Math.max(1,...scene.chart.map(v=>v.value));scene.chart.slice(0,4).forEach((v,i)=>{const y=310+i*155; text(c,v.label,42,y,24,24,'#21364b',1);box(c,42,y+47,Math.max(1,540*v.value/maximum*ease(p*3-i*.2)),52,'#598bcb',8);text(c,`${v.value}${v.unit}`,52,y+55,24,18,540*v.value/maximum*ease(p*3-i*.2)<130?'#21364b':'#fafcfe',1);});text(c,'数据来自当前句子的原文',42,951,19,26,'#6b7b8f',1);
  }
  for(let i=0;i<Math.min(12,options.sceneCount||1);i++)box(c,36+i*23,1002,9,9,i===scene.sentence_index?'#3679cd':'#cfdae7',4);
  const limit=options.presenter==='none'?22:18,chars=Array.from(scene.text),lines=[];for(let i=0;i<chars.length;i+=limit)lines.push(chars.slice(i,i+limit).join(''));const window=Math.min(Math.max(0,lines.length-2),Math.floor(p*Math.max(1,lines.length-1)));lines.slice(window,window+2).forEach((v,i)=>text(c,v,36,1080+i*39,26,26,'#21364b',1));
  if(options.presenter==='custom'&&character){const scale=Math.min(160/character.width,240/character.height);c.drawImage(character,612-character.width*scale/2,1215-character.height*scale+Math.sin(p*Math.PI*2)*3,character.width*scale,character.height*scale);}
  else if(options.presenter==='guide'){box(c,575,1100,86,117,'#245dd9',20);box(c,579,1049,78,87,'#f5c4a0',38);text(c,'·  ·',592,1070,27,8,'#263544',1);}
  text(c,'动态分镜 · 字幕与镜头时间按句长估算',36,1231,16,45,'#748697',1);
}
