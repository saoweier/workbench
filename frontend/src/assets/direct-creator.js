import {api,esc,guard,toast} from '/assets/app.js?v=20261005';
const $=id=>document.getElementById(id),KEY='cwb.direct.creator.v2';
const blank=()=>({key:crypto.randomUUID(),topic:'',requirements:'',density:'balanced',style:'clean',pages:'2',direction:'auto',templateId:'auto',platforms:['douyin','xiaohongshu'],materials:'',mediaIds:[],trendId:null,trendSource:null,task:null,category:'hot',entrySource:null,boardSource:null,boardPage:0,step:0});
export async function setupDirectCreator({onQueued}){
  let draft=blank(),busy=false,boardBusy=false,boardData=null,mode='local_seed',signature='',configReady=false;
  const boardPageSize=6;
  let step=0;
  // Keep each open creation tab independent; local storage remains the recovery
  // copy for a newly opened tab. Existing drafts migrate without losing input.
  try{const saved=JSON.parse(sessionStorage.getItem(KEY)||localStorage.getItem(KEY));if(saved&&typeof saved.topic==='string'&&Array.isArray(saved.mediaIds))draft={...draft,...saved};}catch{}
  draft.entrySource=['hot','manual'].includes(draft.entrySource)?draft.entrySource:draft.topic?(draft.trendId?'hot':'manual'):null;
  draft.boardSource=typeof draft.boardSource==='string'?draft.boardSource:null;
  draft.boardPage=Number.isInteger(draft.boardPage)&&draft.boardPage>=0?draft.boardPage:0;
  draft.density=['short','balanced','detailed'].includes(draft.density)?draft.density:'balanced';
  draft.style=['clean','lively','professional'].includes(draft.style)?draft.style:'clean';
  draft.platforms=Array.isArray(draft.platforms)?draft.platforms.filter(x=>['douyin','xiaohongshu'].includes(x)):['douyin','xiaohongshu'];
  ['requirements','materials'].forEach(k=>{if(typeof draft[k]!=='string')draft[k]='';});
  if(!draft.task?.run_id||typeof draft.task.run_id!=='string')draft.task=null;
  const save=()=>{try{const data=JSON.stringify(draft);sessionStorage.setItem(KEY,data);localStorage.setItem(KEY,data);$('draft-note').textContent='草稿已保存在本机';}catch{$('creator-error').textContent='暂时无法在浏览器保存草稿，请保持页面打开。';}renderBrief();document.dispatchEvent(new CustomEvent('creator-draft-changed'));};
  function collect(){draft.topic=$('creator-topic').value;draft.requirements=$('creator-requirements').value;draft.materials=$('creator-materials').value;draft.pages=$('creator-pages').value;draft.direction=$('creator-direction').value;draft.density=document.querySelector('[name=creator-density]:checked').value;draft.style=document.querySelector('[name=creator-style]:checked').value;draft.platforms=[...document.querySelectorAll('[name=creator-platform]:checked')].map(x=>x.value);save();contract();}
  // 封面策略与后端 platform_policy 保持一致：小红书必须有封面页，抖音可以不设封面。
  function coverNote(){const xhs=draft.platforms.includes('xiaohongshu'),dy=draft.platforms.includes('douyin');if(xhs&&dy)return'小红书含封面，抖音可不设封面';if(xhs)return'封面计入页数';if(dy)return'抖音不另加封面';return'封面按平台决定';}
  function contract(){const text=draft.topic+' '+draft.requirements;const rank=/top\s*(\d{1,2})/i.exec(text);const page=/(\d)\s*[~～到至-]\s*(\d)\s*页/.exec(text)||/(\d)\s*页/.exec(text);$('creator-contract').textContent=`保持原选题 · ${rank?'完整 TOP'+rank[1]+' 名次 · ':''}${page?'按文字要求 '+page[0]:draft.pages?draft.pages+' 页，'+coverNote():'系统按内容决定篇幅'} · 生成后可持续调整。`;}
  function uploads(){$('creator-upload').innerHTML=draft.mediaIds.map(id=>`<img src="/api/v1/content-media/${esc(id)}/image" alt="自有素材" loading="lazy" decoding="async"><button data-remove="${esc(id)}">移除</button>`).join('');}
  function restore(){paintEntry();$('creator-topic').value=draft.topic;$('creator-requirements').value=draft.requirements;$('creator-materials').value=draft.materials;$('creator-pages').value=draft.pages;$('creator-direction').value=draft.direction;document.querySelectorAll('[name=creator-density]').forEach(x=>x.checked=x.value===draft.density);document.querySelectorAll('[name=creator-style]').forEach(x=>x.checked=x.value===draft.style);document.querySelectorAll('[name=creator-platform]').forEach(x=>x.checked=draft.platforms.includes(x.value));uploads();contract();}
  function updateRuns(runs){if(!draft.task)return;const r=runs.find(x=>x.id===draft.task.run_id);if(!r)return;const sig=JSON.stringify({state:r.state,jobs:r.jobs,error:r.error});if(sig===signature)return;signature=sig;draft.task.state=r.state;$('creator-task').hidden=false;$('creator-compose').hidden=true;document.querySelectorAll('.wizard-steps button').forEach(b=>b.removeAttribute('aria-current'));document.querySelectorAll('#creator-compose input,#creator-compose textarea,#creator-compose select').forEach(x=>x.disabled=true);
    document.querySelector('.wizard-steps').hidden=true;
    const done=r.state==='succeeded',stopped=['failed','paused','cancelled'].includes(r.state);$('creator-task-title').textContent=done?'新内容已生成，打开预览继续调整':stopped?'制作暂停：需要处理下面的问题':'正在制作你的内容';
    const names={research:'调研与核验',topic:'确认选题',planning:'组织内容',media:'准备配图',compose:'生成内容',render:'排版出图',audit:'检查要求'};
    $('creator-task-stages').innerHTML=Object.entries(names).map(([key,name])=>{const j=(r.jobs||[]).find(j=>j.stage===key);return `<span class="creator-stage ${j?.state==='succeeded'?'done':j?.state==='running'?'active':''}">${j?.state==='succeeded'?'✓ ':''}${name}${j?.state==='failed'?' · 失败':j?.state==='paused'?' · 已暂停':j?.state==='running'?' · 进行中':''}</span>`;}).join('');
    const result=(r.jobs||[]).find(j=>j.stage==='batch_dispatch')?.output_refs;
    $('creator-task-note').textContent=stopped?(r.error||(r.jobs||[]).find(j=>j.error)?.error||result?.error?.message||'任务暂停，请查看集中处理页面。'):done?'可以要求更短、更详细或改变表达风格。若审核发现偏题，会在预览里显示调整建议。':'后台在执行，进度会持续更新。你可以切换页面，回来继续查看这一篇。';
    $('creator-preview').hidden=!done;$('creator-preview').href='/views/ReviewPreview.html?content='+encodeURIComponent(draft.task.content_id);save();paintTaskAside();document.dispatchEvent(new CustomEvent('creator-task-changed'));
  }
  function renderBoards(){if(!boardData)return;
    $('board-categories').innerHTML=Object.entries(boardData.categories).map(([k,v])=>`<button data-category="${esc(k)}" aria-pressed="${draft.category===k}">${esc(v)}</button>`).join('');
    $('hotpush-base').value=boardData.base_url;
    const sources=boardData.sources,s=sources.find(x=>x.platform===draft.boardSource)||sources.find(x=>x.items.length)||sources[0];
    if(!s){$('board-columns').innerHTML='<p class="hint">这个板块暂无匹配热点，试试全部热点或自定义选题。</p>';return;}
    const pages=Math.max(1,Math.ceil(s.items.length/boardPageSize)),page=Math.min(draft.boardPage,pages-1);
    if(draft.boardSource!==s.platform||draft.boardPage!==page){draft.boardSource=s.platform;draft.boardPage=page;save();}
    $('board-columns').innerHTML=`<div class="board-toolbar"><div class="board-source-picker"><span class="board-source-label">榜单来源</span><div class="board-source-filters" role="group" aria-label="榜单来源">${sources.map(x=>`<button data-board-source="${esc(x.platform)}" aria-label="${esc(x.name)}" aria-pressed="${x===s}">${esc(x.name)}<span>${x.items.length}</span></button>`).join('')}</div></div><nav class="board-pagination" aria-label="榜单翻页"><button data-board-page="prev" aria-label="上一页榜单" ${page===0?'disabled':''}>←</button><span role="status">${page+1} / ${pages} 页</span><button data-board-page="next" aria-label="下一页榜单" ${page===pages-1?'disabled':''}>→</button></nav></div>
      <section class="board-source" aria-label="${esc(s.name)}排行榜"><header class="board-source-heading"><h3>${esc(s.name)}<span>${esc({ready:'已读取',stale:'旧快照',loading:'读取中',empty:'暂无条目',unavailable:'暂不可用'}[s.state]||s.state)}</span></h3><details class="board-source-info"><summary>来源与更新时间</summary><p class="hint">${esc(s.source_category)} · ${esc(s.note)}</p><small>${s.fetched_at?'工作台读取于 '+esc(new Date(s.fetched_at).toLocaleString('zh-CN')):'尚未取得实际热点'}${s.updated_at?'<br>上游更新：'+esc(s.updated_at):''}</small></details></header>
      ${s.items.slice(page*boardPageSize,(page+1)*boardPageSize).map(i=>`<button class="board-item" data-topic="${esc(i.title)}" data-trend="${esc(i.id)}"><b>${esc(i.rank)}</b><span>${esc(i.title)}<small>${esc(i.heat??'上游未提供热度分值')}</small></span></button>`).join('')||'<p class="hint">这个来源暂时没有可读条目，可以切换榜单或自定义选题。</p>'}</section>`;
  }
  let boardTimer=null,pendingBoard=false;
  async function boards(refresh=false){if(boardBusy){pendingBoard=true;return;}boardBusy=true;clearTimeout(boardTimer);$('board-refresh').disabled=true;$('board-state').textContent='正在读取HotPush聚合热点…';const requested=draft.category;
    try{const data=await api.get(`/studio/trends?category=${requested}&refresh=${refresh}`);if(requested===draft.category){boardData=data;renderBoards();const p=data.progress;$('board-state').textContent=`${data.message} · ${p.success||0}/${p.total||data.sources.length} 个来源已取得。序号为源内返回顺序。`;if(data.refreshing&&!$('creator-board').hidden&&step===0&&!document.hidden)boardTimer=setTimeout(()=>boards(),2200);}}
    catch(e){$('board-state').textContent=e.message;}finally{boardBusy=false;$('board-refresh').disabled=false;if(pendingBoard||requested!==draft.category){pendingBoard=false;boards();}}}
  function paintEntry(){document.querySelectorAll('[data-creator-tab]').forEach(x=>x.setAttribute('aria-pressed',String(x.dataset.creatorTab===draft.entrySource)));$('creator-topic-form').hidden=!draft.entrySource||(!draft.topic&&draft.entrySource==='hot');$('creator-board').hidden=!(draft.entrySource==='hot'&&!draft.topic);document.querySelector('.wizard-footer').hidden=step===0&&$('creator-topic-form').hidden;}
  document.querySelectorAll('[data-creator-tab]').forEach(b=>b.onclick=()=>{const hot=b.dataset.creatorTab==='hot';draft.entrySource=b.dataset.creatorTab;if(!hot){draft.trendId=null;draft.trendSource=null;}save();document.querySelectorAll('[data-creator-tab]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));$('creator-board').hidden=!hot;$('creator-topic-form').hidden=hot;document.querySelector('.wizard-footer').hidden=step===0&&hot;if(hot)boards();else{clearTimeout(boardTimer);$('creator-topic').focus();}});
  $('board-categories').onclick=e=>{const b=e.target.closest('[data-category]');if(!b)return;draft.category=b.dataset.category;draft.boardSource=null;draft.boardPage=0;document.querySelectorAll('[data-category]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));save();boards();};
  $('board-refresh').onclick=()=>boards(true);
  $('hotpush-save').onclick=guard(async()=>{await api.put('/studio/hotpush',{base_url:$('hotpush-base').value.trim()});await boards(true);toast('HotPush来源已保存。');});
  $('board-columns').onclick=guard(async e=>{const b=e.target.closest('button');if(!b)return;
    if(b.dataset.boardSource){draft.boardSource=b.dataset.boardSource;draft.boardPage=0;renderBoards();save();$('board-columns').querySelector('[data-board-source][aria-pressed="true"]').focus({preventScroll:true});return;}
    if(b.dataset.boardPage){const direction=b.dataset.boardPage;draft.boardPage=Math.max(0,draft.boardPage+(direction==='next'?1:-1));renderBoards();save();const next=$('board-columns').querySelector(`[data-board-page="${direction}"]:not(:disabled)`);(next||$('board-columns').querySelector('[data-board-page]:not(:disabled)')||$('board-columns').querySelector('[data-board-source][aria-pressed="true"]')).focus({preventScroll:true});return;}
    if(b.dataset.topic){draft.topic=b.dataset.topic;draft.trendId=b.dataset.trend;const hit=boardData.sources.flatMap(s=>s.items).find(i=>i.id===draft.trendId);draft.trendSource=hit?{title:hit.title,url:hit.url,source:hit.source}:null;draft.entrySource='hot';restore();save();$('creator-board').hidden=true;clearTimeout(boardTimer);paintEntry();$('creator-topic').focus();}
  });
  ['creator-topic','creator-requirements','creator-materials'].forEach(id=>$(id).oninput=()=>collect());document.querySelectorAll('[name=creator-density],[name=creator-style],[name=creator-platform],#creator-pages,#creator-direction').forEach(x=>x.onchange=collect);
  $('creator-upload').onclick=e=>{const id=e.target.dataset.remove;if(id){draft.mediaIds=draft.mediaIds.filter(x=>x!==id);uploads();save();}};
  $('creator-images').onchange=guard(async()=>{const files=[...$('creator-images').files];if(files.length+draft.mediaIds.length>6)throw Error('最多6张配图');for(const file of files){const f=new FormData();f.append('image',file);f.append('description',file.name);const r=await fetch('/api/v1/content-media',{method:'POST',headers:{'X-CWB-Local-Action':'account-connection'},body:f});const x=await r.json();if(!r.ok)throw Error(x.error?.message||x.detail||'上传失败');draft.mediaIds.push(x.id);save();uploads();}$('creator-images').value='';});
  $('creator-start').onclick=async()=>{if(busy||!configReady||draft.task)return;collect();$('creator-error').textContent='';if(draft.topic.trim().length<3){$('creator-error').textContent='输入一个选题，或从热榜点选。';$('creator-topic').focus();return;}if(!draft.platforms.length){$('creator-error').textContent='至少选择一个平台';return;}
    busy=true;$('creator-new').disabled=true;$('creator-start').disabled=true;$('creator-start').innerHTML='<span class="spin"></span> 正在提交制作任务…';
    try{const body={request_id:draft.key,topic:draft.topic.trim(),requirements:draft.requirements,density:draft.density,style:draft.style,pages:draft.pages?Number(draft.pages):null,materials:draft.materials,media_ids:draft.mediaIds,direction:draft.direction,template_id:draft.templateId,platforms:draft.platforms,trend_id:draft.trendId,run_mode:mode};
      // A lost response can happen after enqueueing. One retry uses the exact
      // frozen body, so the server recovers the task rather than creating two.
      let task;try{task=await api.post('/studio/produce',body);}catch(e){if(e instanceof TypeError||[502,503,504].includes(e.status))task=await api.post('/studio/produce',body);else throw e;}
      draft.task=task;save();updateRuns([{id:task.run_id,state:task.state,jobs:[]}]);
      try{await onQueued();}catch{$('creator-task-note').textContent='制作已开始，进度暂时未读取。稍后会自动更新。';}
      toast(task.reused?'已找回这次创作的制作任务。':'已按原选题开始制作。');
    }catch(e){$('creator-error').textContent=e instanceof TypeError?'暂时未收到提交结果，请再点一次开始制作；系统会找回同一次任务。':e.message;}finally{busy=false;$('creator-new').disabled=false;$('creator-start').disabled=Boolean(draft.task)||!configReady;$('creator-start').textContent='确认并开始制作 →';}
  };
  $('creator-templates').onclick=e=>{const b=e.target.closest('[data-template]');if(!b)return;draft.templateId=b.dataset.template;document.querySelectorAll('[data-template]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));save();};
  $('creator-new').onclick=()=>{$('creator-start').disabled=!configReady;draft=blank();signature='';clearTimeout(boardTimer);$('creator-task').hidden=true;$('creator-compose').hidden=false;document.querySelector('.wizard-steps').hidden=false;document.querySelectorAll('#creator-compose input,#creator-compose textarea,#creator-compose select').forEach(x=>x.disabled=false);restore();showStep(0,{focus:false});document.querySelectorAll('[data-template]').forEach(x=>x.setAttribute('aria-pressed',String(x.dataset.template===draft.templateId)));save();document.querySelector('[data-creator-tab]')?.focus();document.dispatchEvent(new CustomEvent('creator-task-changed'));};
  function paintTaskAside(){if(!draft.task)return;const done=draft.task.state==='succeeded',stopped=['failed','paused','cancelled'].includes(draft.task.state);$('aside-step').textContent=done?'已完成 / 预览成品':stopped?'制作已停止':'制作进度';$('aside-title').textContent=done?'接下来，交给你的判断。':stopped?'先看清楚，卡在了哪里。':'后台正在处理这一篇。';$('aside-help').textContent=done?'先预览每页内容。需要修改时直接提出要求，满意后再批准和导出。':stopped?'查看任务错误与技能诊断中的检索词、原文和工具失败记录，修复后重新制作。':'切换页面或刷新都能恢复任务。遇到问题可以到制作任务里暂停或修复后继续。';}
  function controller(){return {updateRuns,hasTask:()=>Boolean(draft.task),hasActiveTask:()=>Boolean(draft.task&&['queued','running'].includes(draft.task.state))};}
  const labels={density:{short:'精简',balanced:'均衡',detailed:'详细'},style:{clean:'简洁精致',lively:'活跃生动',professional:'专业克制'}};
  function renderBrief(){
    if(!$('brief-topic'))return;
    const template=document.querySelector(`[data-template="${draft.templateId}"] strong`)?.textContent||'自动匹配';
    $('brief-topic').textContent=draft.topic.trim()||'一个想法，就是开始。';
    const values=[['版式',template],['表达',labels.style[draft.style]],['篇幅',draft.pages?draft.pages+' 页，'+coverNote():'自动决定'],['平台',draft.platforms.map(x=>x==='douyin'?'抖音':'小红书').join(' / ')||'尚未选择']];
    $('brief-settings').innerHTML=values.map(([k,v])=>`<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join('');
    $('creation-review').innerHTML=`<h3>${esc(draft.topic||'还没填写选题')}</h3><dl>${[['版式',template],['表达',labels.style[draft.style]],['信息密度',labels.density[draft.density]],['页数',draft.pages?draft.pages+' 页（'+coverNote()+'）':'自动决定']].map(([k,v])=>`<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>${draft.requirements?`<p><b>补充要求</b><br>${esc(draft.requirements)}</p>`:''}${draft.materials||draft.mediaIds.length?`<p class="hint">已附 ${draft.materials?'参考资料':''}${draft.materials&&draft.mediaIds.length?'与 ':''}${draft.mediaIds.length?draft.mediaIds.length+' 张配图':''}</p>`:''}`;
    if(draft.trendId){
      const source=draft.trendSource;
      let link='';try{const url=new URL(source?.url);if(['http:','https:'].includes(url.protocol)&&!url.username&&!url.password)link=url.href;}catch{}
      $('creation-review').innerHTML+=`<p class="hint"><b>热榜原题来源</b> · ${esc(source?.source||'HotPush')}<br>${esc(source?.title||'已选榜单议题')}${link?`<br><a href="${esc(link)}" target="_blank" rel="noopener noreferrer">查看议题原链接 ↗</a><br>先读取原页，资料不足时补充联网搜索。`:source?' · 上游未提供链接，将从联网搜索开始。':' · 后台按已选条目读取原链接。'}</p>`;
    }
  }
  function validateTopic(){if(!draft.entrySource){$('creator-error').textContent='请先选择「排行榜选题」或「自定义选题」。';document.querySelector('[data-creator-tab]')?.focus();return false;}const valid=$('creator-topic').value.trim().length>=3;$('creator-topic').setAttribute('aria-invalid',String(!valid));$('creator-error').textContent=valid?'':'请先写一个至少 3 个字的选题，或从热点里选择。';if(!valid)$('creator-topic').focus();return valid;}
  function showStep(next,{focus=true}={}){
    next=Math.max(0,Math.min(2,Number(next)||0));
    if(next>0&&!validateTopic())next=0;
    step=next;draft.step=step;collect();document.querySelector('.wizard-footer').hidden=step===0&&$('creator-topic-form').hidden;
    document.querySelectorAll('[data-wizard-panel]').forEach(x=>x.hidden=Number(x.dataset.wizardPanel)!==step);
    document.querySelectorAll('[data-wizard-step]').forEach(x=>{const n=Number(x.dataset.wizardStep);if(n===step)x.setAttribute('aria-current','step');else x.removeAttribute('aria-current');x.dataset.complete=String(n<step);});
    $('wizard-back').hidden=step===0;$('wizard-next').hidden=step===2;$('creator-start').hidden=step!==2;$('creator-start').disabled=!configReady||Boolean(draft.task);
    $('wizard-next').textContent=step===0?'下一步 · 选择样式 →':'下一步 · 确认制作 →';
    const help=[['01 / 确定选题','越具体，越接近你想要的。','排行榜适合发现方向，自定义适合已有想法。两条路径选定题目后，都进入同一个制作流程。'],['02 / 选择样式','先选内容的形状。','排行榜、分类表和图解适合不同的内容。版式示例可直接查看，不会调用模型。'],['03 / 确认制作','把最后的决定留给你。','确认后才会开始制作。完成后先预览和调整，再由你批准导出。']][step];
    $('aside-step').textContent=help[0];$('aside-title').textContent=help[1];$('aside-help').textContent=help[2];
    if(step!==0)clearTimeout(boardTimer);
    if(focus)document.querySelector(`[data-wizard-panel="${step}"] h2`)?.focus({preventScroll:true});
    document.dispatchEvent(new CustomEvent('creator-step-changed',{detail:{step}}));
  }
  $('wizard-next').onclick=()=>{if(validateTopic())showStep(step+1);};$('wizard-back').onclick=()=>showStep(step-1);
  document.querySelectorAll('[data-wizard-step]').forEach(b=>b.onclick=()=>{if(draft.task){$('creator-compose').hidden=false;}showStep(Number(b.dataset.wizardStep));});
  $('creator-inspect').onclick=()=>{$('creator-compose').hidden=!$('creator-compose').hidden;document.querySelector('.wizard-steps').hidden=$('creator-compose').hidden;if(!$('creator-compose').hidden)showStep(2);else paintTaskAside();};
  document.addEventListener('visibilitychange',()=>{clearTimeout(boardTimer);if(!document.hidden&&!$('creator-board').hidden&&step===0&&boardData?.refreshing)boards();});
  restore();
  let options;try{options=await api.get('/studio/options');}catch(e){$('creator-ready').disabled=false;$('creator-start').disabled=true;$('creator-model').textContent='服务暂不可用';$('creator-error').textContent='创作设置未读取：'+e.message+'。恢复服务后刷新页面，输入会保留。';return controller();}$('creator-direction').innerHTML=(options.directions||[{id:'auto',name:'自动识别'}]).map(d=>`<option value="${esc(d.id)}">${esc(d.name)}</option>`).join('');if(!(options.directions||[]).some(d=>d.id===draft.direction))draft.direction='auto';$('creator-direction').value=draft.direction;
  if(!(options.templates||[]).some(t=>t.id===draft.templateId))draft.templateId='auto';
  $('creator-templates').innerHTML=(options.templates||[{id:'auto',name:'自动匹配',description:'依据内容选择版式'}]).map(t=>`<div class="creator-template"><button data-template="${esc(t.id)}" aria-pressed="${draft.templateId===t.id}">${t.id==='auto'?'<span class="template-mini" aria-hidden="true"><b>按内容自动选海报</b><i></i><i></i><i></i></span>':`<span class="template-mini template-poster" aria-hidden="true"><iframe src="/api/v1/studio/template-preview/${esc(t.id)}?v=${esc(t.package_version)}" loading="lazy" title="${esc(t.name)}固定示例" sandbox="allow-scripts" tabindex="-1"></iframe></span>`}<strong>${esc(t.name)}</strong><small>${esc(t.description)}</small></button>${t.id!=='auto'?`<a href="/api/v1/studio/template-preview/${esc(t.id)}" target="_blank" rel="noopener">放大海报示例 ↗</a>`:''}</div>`).join('');
  const posterObserver=new ResizeObserver(entries=>entries.forEach(({target,contentRect})=>{if(contentRect.width>0)target.style.setProperty('--mini-scale',String(contentRect.width/1080));}));
  document.querySelectorAll('.template-poster').forEach(el=>posterObserver.observe(el));
  configReady=true;mode=options.default_mode||'local_seed';$('creator-model').textContent=options.text_ready?'创作搭档 · '+options.model:'本地演练';$('creator-call-note').textContent=mode==='real'?'生成与修改会调用已配置模型。读取热榜不调用文字模型。':options.text_ready?'当前使用本地演练，不调用模型。':'尚未配置文字模型，当前为本地演练；真实内容请在API设置中配置模型。';
  $('creator-ready').disabled=false;$('creator-start').disabled=Boolean(draft.task);if(draft.task){try{updateRuns([await api.get('/runs/'+draft.task.run_id)]);}catch(e){$('creator-task').hidden=false;$('creator-task-title').textContent='上次创作已恢复，进度暂时未读到';$('creator-task-note').textContent='任务记录未读取：'+e.message+'。可到制作任务刷新检查，也可以写下一篇。';}}
  renderBrief();showStep(Number(new URL(location.href).searchParams.get('step')||draft.step||0),{focus:false});if(draft.task){$('creator-compose').hidden=true;$('creator-task').hidden=false;document.querySelector('.wizard-steps').hidden=true;document.querySelectorAll('#creator-compose input,#creator-compose textarea,#creator-compose select').forEach(x=>x.disabled=true);paintTaskAside();}
  if(!draft.task&&step===0&&!$('creator-board').hidden)boards();
  return controller();
}
