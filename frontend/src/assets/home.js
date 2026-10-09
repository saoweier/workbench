import {api,navBar,esc,icon,stateChip,modeChip,guard,toast} from '/assets/app.js?v=20261009-lan1';
const $=id=>document.getElementById(id);$('nav').innerHTML=navBar('index');document.querySelectorAll('[data-icon]').forEach(el=>el.innerHTML=icon(el.dataset.icon));
let contents=[],options=null,health=null,publications=null,count=6,flight=null,epoch=0,observer=null,overviewReady=false;
const details=new Map();
const demo=c=>c.run_mode==='fixture'||String(c.display_id).startsWith('DEMO')||/^【演示】/.test(c.topic||'');
const own=c=>!demo(c)&&c.run_mode==='real';
const skeleton=()=>'<div class="home-gallery-skeleton"><div class="skeleton"></div><div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line short"></div></div>';
$('content-gallery').innerHTML=Array.from({length:6},skeleton).join('');
function paintJourney(){const real=contents.filter(own),review=real.find(c=>['ready_for_review','partially_approved','changes_requested'].includes(c.state)),approved=real.find(c=>['approved','partially_approved','exported'].includes(c.state));
  const registered=(publications?.items||[]).filter(p=>p.run_mode==='real'&&!String(p.declared?.link||p.link||'').includes('example.invalid')&&real.some(c=>c.id===p.content_id));
  const stages=[{title:'准备创作搭档',note:options?.text_ready?'文字模型已就绪':'连接自己的文字模型',url:'/views/ApiSettings.html',done:Boolean(options?.text_ready)},{title:'做出一篇内容',note:real.length?`${real.length} 篇实际创作`:'选题 → 样式 → 制作',url:'/views/Production.html',done:real.some(c=>['ready_for_review','approved','partially_approved','exported'].includes(c.state))},{title:'预览与发布',note:approved?'已有平台稿获批准':'先检查每一页，再导出',url:'/views/ReviewPreview.html',done:Boolean(registered.length)},{title:'回访与改进',note:'带回真实指标和评论',url:'/views/ReviewInsights.html',done:false}];
  const next=stages.findIndex(s=>!s.done);$('home-journey').innerHTML=stages.map((s,i)=>`<a class="journey-step ${i===next?'is-next':''}" href="${s.url}"><span>${String(i+1).padStart(2,'0')} ${s.done?'✓ 已完成':i===next?'· 接着做':''}</span><h3>${s.title} ↗</h3><p>${s.note}</p></a>`).join('');
  let action;
  if(!options)action={tag:'配置暂时未读取',title:'创作设置暂时无法读取',note:'可刷新重试，或进入设置检查连接。',url:'/views/ApiSettings.html',label:'检查设置'};
  else if(!options.text_ready)action={tag:'先准备一下',title:'连接你的创作搭档',note:'保存文字模型配置后，就能制作自己的内容。也可以先浏览演示作品。',url:'/views/ApiSettings.html',label:'配置文字模型'};
  else if(review)action={tag:'有内容等你过目',title:review.topic,note:'成品已经出图。检查每一页，也可以继续提出修改要求。',url:'/views/ReviewPreview.html?content='+encodeURIComponent(review.id),label:'继续预览这篇'};
  else if(approved&&!registered.length)action={tag:'下一步 / 发布',title:'把准备好的内容分享出去',note:'进入发布工作区整理素材，选择你的发布方式。',url:'/views/PublishingHub.html',label:'准备发布'};
  else if(registered.length)action={tag:'下一步 / 回访',title:'看看内容带来了什么反馈',note:'登记平台实际指标，再用数据和评论帮助下一次创作。',url:'/views/ManualPublishing.html',label:'带回发布数据'};
  else action={tag:'创作搭档已就绪',title:'现在就做你的下一篇',note:'从一个具体的选题开始。制作过程中随时查看真实进度。',url:'/views/Production.html',label:'开始创作'};
  $('home-next').innerHTML=`<p class="eyebrow">接着做 / YOUR NEXT STEP</p><span class="next-badge">${esc(action.tag)}</span><h3>${esc(action.title)}</h3><p>${esc(action.note)}</p><a class="text-link" href="${esc(action.url)}">${action.label} →</a>`;
  $('journey-caption').textContent='进度根据模型配置、实际创作与发布登记计算，演示作品不计入。';
}
function visual(c,d,index){const rev=d?.revisions?.find(r=>r.revision_id===d.active_revision_id)||d?.revisions?.at(-1);const artifacts=rev?.platforms?.flatMap(p=>p.artifacts||[])||[];const image=artifacts.find(a=>a.kind==='page_image');const title=(c.topic||c.display_id||'未命名内容').replace(/^【演示】\s*/,'');
  const cover=image?`<img src="${esc(image.url)}" alt="${esc(title)}的封面" loading="lazy" decoding="async">`:`<div class="cover-placeholder">${icon('ReviewPreview')}${d?'尚未生成页图':'封面将在进入视野时载入'}</div>`;
  return `<div class="content-card-image">${cover}<span class="mode ${demo(c)?'fixture':esc(c.run_mode)}">${demo(c)?'演示':esc({real:'实际创作',local_seed:'项目资料'}[c.run_mode]||'内容')}</span>${d?`<span class="card-count">${artifacts.filter(a=>a.kind==='page_image').length} 张页图</span>`:''}</div><div class="content-card-title">${esc(title)}</div><div class="content-card-bottom"><small>${esc(c.display_id)}</small>${stateChip(c.state)}</div>`;
}
function watchImages(node){node.querySelectorAll('img').forEach(img=>{const done=()=>img.classList.add('is-loaded');img.addEventListener('load',done,{once:true});img.addEventListener('error',()=>{const placeholder=document.createElement('div');placeholder.className='cover-placeholder';placeholder.textContent='封面暂未读到 · 打开成品重试';img.replaceWith(placeholder);},{once:true});if(img.complete&&img.naturalWidth)done();});}
function paintGallery(){observer?.disconnect();const gallery=$('content-gallery');gallery.replaceChildren();
  if(!contents.length){gallery.innerHTML='<div class="state-empty"><h3>第一篇内容，还在你的脑海里。</h3><p>给它写一个题目，开始就有方向。</p><a class="solid-link" href="/views/Production.html">写下第一个想法 →</a></div>';$('home-more').hidden=true;return;}
  const requestedEpoch=epoch;
  observer=new IntersectionObserver(entries=>{for(const entry of entries){if(!entry.isIntersecting)continue;observer.unobserve(entry.target);loadCover(entry.target,requestedEpoch);}},{rootMargin:'80px'});
  contents.slice(0,count).forEach((c,i)=>{const card=document.createElement('a');card.className='content-card';card.href='/views/ReviewPreview.html?content='+encodeURIComponent(c.id);card.dataset.content=c.id;card.style.setProperty('--cover-bg',['#f0e8dd','#e3edf6','#e4ede6','#ede8f2'][i%4]);card.innerHTML=visual(c,details.get(c.id),i);gallery.append(card);watchImages(card);if(!details.has(c.id))observer.observe(card);});
  $('home-more').hidden=count>=contents.length;
}
const coverQueue=[];let coverActive=0;
function loadCover(card,requestedEpoch){coverQueue.push({card,requestedEpoch});drainCovers();}
function drainCovers(){while(coverActive<3&&coverQueue.length){const {card,requestedEpoch}=coverQueue.shift();if(requestedEpoch!==epoch||!card.isConnected)continue;coverActive++;
  api.get('/contents/'+encodeURIComponent(card.dataset.content)).then(d=>{if(requestedEpoch!==epoch)return;details.set(card.dataset.content,d);const c=contents.find(c=>c.id===card.dataset.content);if(c&&card.isConnected){card.innerHTML=visual(c,d);watchImages(card);}}).catch(()=>{if(card.isConnected){const target=card.querySelector('.cover-placeholder');if(target)target.textContent='封面暂未读取 · 打开成品查看';}}).finally(()=>{coverActive--;drainCovers();});}}
async function load(){if(flight)return flight;$('btn-refresh').disabled=true;flight=(async()=>{try{const data=await api.get('/contents');contents=[...(data.items||[])].reverse();epoch++;details.clear();paintGallery();paintJourney();$('gallery-caption').textContent=`${contents.length} 组内容 · ${contents.filter(own).length} 组实际创作 · 封面按需载入`;$('content-gallery').setAttribute('aria-busy','false');}
  catch(e){$('gallery-caption').textContent='内容读取失败：'+e.message+'，可点刷新重试。';if(!contents.length)$('content-gallery').innerHTML='<div class="state-empty">内容暂时无法读取。请检查工作区连接并重试。</div>';$('content-gallery').setAttribute('aria-busy','false');}
  finally{$('btn-refresh').disabled=false;flight=null;}})();return flight;}
$('home-more').onclick=()=>{count+=6;paintGallery();};$('btn-refresh').onclick=load;
$('workspace-details').addEventListener('toggle',async()=>{if(!$('workspace-details').open||overviewReady)return;$('overview').innerHTML='<div class="inline-loading"><span class="spin"></span>读取工作区状态…</div>';try{const [h,p]=await Promise.all([api.get('/health'),api.get('/publications')]);health=h;publications=p;$('overview').innerHTML=[['内容条目',contents.length],['发布登记',p.total??p.items?.length??0],['后台任务',h.worker?.status==='running'?'运行中':'需检查'],['金额上限',h.money_limit_set?'已设置':'未设置']].map(([label,value])=>`<div class="card"><div class="n">${esc(value)}</div><div class="l">${label}</div></div>`).join('');overviewReady=true;paintJourney();}catch(e){$('overview').innerHTML='<p>工作区状态未能读取。收起后重新展开可重试。</p>';}});
const startup=await Promise.allSettled([api.get('/studio/options'),api.get('/health'),api.get('/publications')]);
options=startup[0].status==='fulfilled'?startup[0].value:null;health=startup[1].status==='fulfilled'?startup[1].value:null;publications=startup[2].status==='fulfilled'?startup[2].value:null;
$('workspace-health').innerHTML=health?`<span class="chip ${health.worker?.status==='running'?'ok':'warn'}">${health.worker?.status==='running'?'本地工作区已连接':'后台任务需检查'}</span>`:'<span class="chip err">工作区未连接</span>';
await load();paintJourney();
