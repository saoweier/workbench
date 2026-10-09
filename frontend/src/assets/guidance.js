import {api,esc} from '/assets/app.js?v=20261008-insecure1';
import {LESSONS,GUIDE_VERSION} from '/assets/guidance-lessons.js?v=20261006';
const KEY='cwb.guidance.v4',DRAFT='cwb.direct.creator.v2';
export function setupGuidance(){
 if(document.getElementById('guided-operation'))return;
 const css=document.createElement('link');css.rel='stylesheet';css.href='/assets/guidance.css?v='+GUIDE_VERSION;document.head.append(css);
 let session=null,poll=null,timer=null,inflight=false,sequence=0,lastDone=false,highlight=null,pointFrame=null,returnFocus=null;
 try{session=JSON.parse(sessionStorage.getItem(KEY));}catch{}
 if(!session||!LESSONS[session.lesson]||!Number.isInteger(session.index)||session.index<0||session.index>=LESSONS[session.lesson].steps.length)session={active:false,lesson:'create',index:0,completed:[],skipped:[],content_id:null};
 session.completed=Array.isArray(session.completed)?session.completed:[];session.skipped=Array.isArray(session.skipped)?session.skipped:[];
 const panel=document.createElement('aside');panel.id='guided-operation';panel.className='guide-panel';panel.hidden=true;panel.setAttribute('aria-label','实际操作引导');
 panel.innerHTML='<header><span id="guide-count"></span><button id="guide-menu" aria-label="选择其他教程">目录</button><button id="guide-minimize" aria-label="收起指引">−</button><button id="guide-close" aria-label="退出指引">×</button></header><progress id="guide-progress" value="0" aria-label="引导进度"></progress><h2 id="guide-title"></h2><p id="guide-body"></p><p id="guide-status" class="guide-status" role="status"></p><div class="guide-actions"><a id="guide-go" class="solid-link"></a><button id="guide-next" class="primary" disabled>下一步 →</button><button id="guide-back" class="guide-skip">上一步</button><button id="guide-skip" class="guide-skip">暂时跳过</button></div><a class="guide-reference" href="/views/UserGuide.html" target="_blank" rel="noopener">查看完整使用教程 ↗</a>';
 document.body.append(panel);
 const hub=document.createElement('dialog');hub.id='guide-hub';hub.className='guide-hub';hub.setAttribute('aria-labelledby','guide-hub-title');
 hub.innerHTML=`<header><div><span class="guide-eyebrow">使用教程 · 2026.10.06</span><h2 id="guide-hub-title">这次，想学哪一段？</h2><p>从真实页面跟着做。教程会标出操作位置，由你确认每一步。</p></div><button id="guide-hub-close" aria-label="关闭教程目录">×</button></header><button id="guide-resume" class="guide-resume" hidden>继续上次教程 →</button><div class="guide-lessons">${Object.entries(LESSONS).map(([id,l],n)=>`<button data-guide-lesson="${id}"><span class="guide-lesson-number">${String(n+1).padStart(2,'0')}</span><span><strong>${esc(l.name)}</strong><small>${esc(l.description)}</small><em>${l.steps.length} 步${id==='create'?' · 建议从这里开始':''}</em></span><span aria-hidden="true">↗</span></button>`).join('')}</div><footer><span>可随时收起、跳过、重开。阅读教程不会生成或发布内容。</span><a href="/views/UserGuide.html">完整使用教程 ↗</a></footer>`;
 document.body.append(hub);const $=id=>document.getElementById(id);
 const save=()=>{try{sessionStorage.setItem(KEY,JSON.stringify(session));}catch{}};
 const draft=()=>{try{return JSON.parse(localStorage.getItem(DRAFT))||{};}catch{return {};}};
 const steps=()=>LESSONS[session.lesson].steps,current=()=>steps()[session.index];
 const onPage=()=>location.pathname===new URL(current().url,location.origin).pathname;
 function contentId(){
  const selected=onPage()?$('#sel-content')?.value:null;
  const manual=location.pathname.endsWith('/ManualPublishing.html')?$('#manual-preview')?.getAttribute('href'):null;
  const query=['ReviewPreview.html','VideoStudio.html','ManualPublishing.html','SkillWorkflow.html'].some(p=>location.pathname.endsWith(p))?new URLSearchParams(location.search).get('content'):null;
  const id=selected||(manual?new URL(manual,location.origin).searchParams.get('content'):null)||query||session.content_id||draft().task?.content_id;
  if(id&&id!==session.content_id){session.content_id=id;save();}return id;
 }
 function clearTarget(){highlight?.classList.remove('guide-target');highlight=null;panel.classList.remove('guide-panel--top');}
 function point(){
  clearTarget();if(!session.active||panel.classList.contains('collapsed')||!onPage())return;
  let selector=current().target;const d=draft();
  if(current().kind==='topic')selector=$('creator-topic')?.getClientRects().length?'#creator-topic':$('creator-board')?.getClientRects().length?'#creator-board':'#topic-source-choices';
  if(['style','produce'].includes(current().kind)&&!document.querySelector(selector)?.getClientRects().length)selector=$('creator-topic')?.getClientRects().length?'#creator-topic':'#topic-source-choices';
  if(d.task&&['topic','style','produce'].includes(current().kind))selector='#creator-task';
  const node=document.querySelector(selector);if(!node||!node.getClientRects().length)return;
  highlight=node;node.classList.add('guide-target');const r=node.getBoundingClientRect(),g=panel.getBoundingClientRect();
  if(r.right>g.left&&r.left<g.right&&r.bottom>g.top&&r.top<g.bottom)panel.classList.add('guide-panel--top');
 }
 function path(){const url=new URL(current().url,location.origin),id=contentId();if(id&&['ReviewPreview.html','VideoStudio.html','ManualPublishing.html','SkillWorkflow.html'].some(p=>url.pathname.endsWith(p)))url.searchParams.set('content',id);return url.pathname+url.search;}
 function render(){
  sequence++;lastDone=false;clearTimeout(poll);panel.hidden=!session.active;if(!session.active)return;
  $('guide-count').textContent=`${LESSONS[session.lesson].name} · ${session.index+1} / ${steps().length}`;
  $('guide-progress').max=steps().length;$('guide-progress').value=new Set([...session.completed,...session.skipped]).size;
  $('guide-title').textContent=current().title;$('guide-body').textContent=current().body;
  $('guide-go').textContent=onPage()?'定位操作位置 ↓':current().action+' ↗';$('guide-go').href=path();$('guide-go').hidden=false;
  $('guide-next').textContent=session.index===steps().length-1?'结束这段教程':current().kind==='learn'?'我了解了 →':'下一步 →';
  $('guide-next').disabled=true;$('guide-back').hidden=session.index===0;$('guide-status').classList.remove('done');
  $('guide-status').textContent=onPage()?'正在读取当前状态…':'打开对应页面，指引会标出操作位置。';point();verify();
 }
 async function verify(){
  if(!session.active||inflight||document.hidden||panel.classList.contains('collapsed'))return;
  const token=sequence,s=current(),d=draft();inflight=true;let done=false,status='';
  try{
   if(s.kind==='model'){const o=await api.get('/studio/options');done=Boolean(o.text_ready);status=done?'文字模型已就绪。':'等待保存并启用文字模型配置。';}
   else if(s.kind==='topic'){done=typeof d.topic==='string'&&d.topic.trim().length>=3&&(d.step>=1||Boolean(d.task));status=done?'选题已保存，可以设置样式。':'选定题目后，点击「下一步 · 选择样式」。';}
   else if(s.kind==='style'){done=Boolean(d.topic)&&(d.step>=2||Boolean(d.task));status=done?'样式与页数已保存，可以确认制作。':!d.topic?'请先完成选题，再进入「选择样式」。':'核对样式后，进入「确认制作」。';}
   else if(s.kind==='produce'){if(d.task?.run_id){const run=await api.get('/runs/'+encodeURIComponent(d.task.run_id));done=['queued','running','succeeded'].includes(run.state);status=done?'任务已提交；完成后再核对实际图片。':'任务停在'+(run.blocked_stage||'制作阶段')+'：'+(run.error||'请查看任务提示。');}else status=!d.topic?'请先完成选题与样式，再确认制作。':'等待你提交制作任务。';}
   else if(s.kind==='preview'||s.kind==='approve'){
    const id=contentId();if(id){const detail=await api.get('/contents/'+encodeURIComponent(id)),rev=detail.revisions?.find(r=>r.revision_id===detail.active_revision_id);
     done=s.kind==='preview'?Boolean(rev?.platforms?.some(p=>p.artifacts?.some(a=>a.kind==='page_image'))):Boolean(rev?.platforms?.some(p=>['approved','exported'].includes(p.state)));
     status=done?(s.kind==='preview'?'已有实际图片，请逐页放大核对。':'当前版本已有平台稿获批准。'):s.kind==='preview'?'还没有实际图片；先到制作任务查看是否完成或被阻断。':'等待核对后批准当前平台版本。';
     if(s.kind==='preview'&&detail.state==='changes_requested')status+=' 审核有待修改问题，请查看诊断并调整。';
    }else status='先在页面选择一份内容。';
   }else if(s.kind==='publish'||s.kind==='metrics'){
    const id=contentId();if(!id)status='先在手动发布页选择自己的真实成品。';
    else{const pubs=await api.get('/publications?content_id='+encodeURIComponent(id)),own=(pubs.items||[]).filter(p=>p.content_id===id&&p.run_mode==='real'&&['declared','verified'].includes(p.status)&&!(p.declared?.link||'').includes('example.invalid'));
     if(s.kind==='publish'){done=own.length>0;status=done?'当前内容已有真实作品的发布登记。':'等待发布完成后，保存当前内容的真实链接或编号。';}
     else{const selected=$('manual-publication')?.value,publication=selected?own.find(p=>p.id===selected):null;if(publication){const m=await api.get('/publications/'+encodeURIComponent(publication.id)+'/metrics');done=(m.snapshot_count??0)>0;}status=done?'当前选择的作品已有回访指标。':selected&&!publication?'所选回访作品不属于当前成品，请核对作品与内容。':'选择当前内容的发布作品，保存实际指标；暂未更新时可跳过。';}
    }
   }else{done=onPage()&&Boolean(document.querySelector(s.target)?.getClientRects().length);status=done?'已打开操作位置。阅读后继续；不会替你保存或提交。':'打开对应页面，等待操作位置载入。';}
   if(token!==sequence||!session.active)return;
   lastDone=Boolean(done);$('guide-status').textContent=status;$('guide-status').classList.toggle('done',lastDone);$('guide-next').disabled=!lastDone;point();
  }catch(e){if(token===sequence){lastDone=false;$('guide-next').disabled=true;$('guide-status').textContent='暂时无法读取状态：'+e.message+'。可以查看页面或稍后继续。';}}
  finally{inflight=false;clearTimeout(poll);if(session.active&&!document.hidden&&!panel.classList.contains('collapsed'))poll=setTimeout(verify,lastDone?12000:4500);}
 }
 function navigate(){const url=path();if(location.pathname+location.search!==url)location.href=url;}
 function openHub(){returnFocus=document.activeElement;$('guide-resume').hidden=!session.active;$('guide-resume').textContent=`继续「${LESSONS[session.lesson].name}」 · 第${session.index+1}步 →`;if(!hub.open)hub.showModal();}
 function advance(skipped=false){
  if(!skipped&&!lastDone)return;const list=skipped?session.skipped:session.completed;if(!list.includes(session.index))list.push(session.index);
  if(session.index===steps().length-1){session.active=false;sequence++;save();panel.hidden=true;clearTarget();clearTimeout(poll);openHub();$('guide-resume').hidden=true;return;}
  session.index++;save();render();navigate();
 }
 function start(lesson){const id=contentId();hub.close();session={active:true,lesson,index:0,completed:[],skipped:[],content_id:id||null};panel.classList.remove('collapsed');$('guide-minimize').textContent='−';$('guide-minimize').setAttribute('aria-label','收起指引');save();render();navigate();}
 document.querySelectorAll('[data-start-guide],#open-tutorial').forEach(b=>b.addEventListener('click',openHub));
 hub.querySelectorAll('[data-guide-lesson]').forEach(b=>b.onclick=()=>start(b.dataset.guideLesson));
 $('guide-hub-close').onclick=()=>hub.close();hub.addEventListener('close',()=>{if(!session.active)returnFocus?.focus({preventScroll:true});});
 $('guide-resume').onclick=()=>{hub.close();panel.classList.remove('collapsed');$('guide-minimize').textContent='−';$('guide-minimize').setAttribute('aria-label','收起指引');render();navigate();};
 $('guide-menu').onclick=openHub;$('guide-next').onclick=()=>advance();$('guide-skip').onclick=()=>advance(true);
 $('guide-go').onclick=e=>{if(onPage()){e.preventDefault();point();highlight?.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth'});}};
 $('guide-back').onclick=()=>{session.index=Math.max(0,session.index-1);save();render();navigate();};
 $('guide-close').onclick=()=>{session.active=false;sequence++;save();panel.hidden=true;clearTarget();clearTimeout(poll);};
 function collapse(){panel.classList.toggle('collapsed');const collapsed=panel.classList.contains('collapsed');$('guide-minimize').textContent=collapsed?'+':'−';$('guide-minimize').setAttribute('aria-label',collapsed?'展开指引':'收起指引');clearTimeout(poll);point();if(!collapsed)verify();}
 $('guide-minimize').onclick=collapse;
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&session.active&&!hub.open&&!panel.classList.contains('collapsed'))collapse();});
 function changed(){if(!session.active)return;clearTimeout(timer);timer=setTimeout(verify,300);}
 ['creator-draft-changed','creator-step-changed','creator-task-changed','change'].forEach(name=>document.addEventListener(name,changed));
 document.addEventListener('click',e=>{if(e.target.closest('#t-save,#btn-approve,#manual-register,#manual-save-metrics,#revision-submit,#trace-load,#preview-template')){clearTimeout(timer);timer=setTimeout(verify,1000);}});
 function schedulePoint(){if(pointFrame)return;pointFrame=requestAnimationFrame(()=>{pointFrame=null;point();});}
 window.addEventListener('scroll',schedulePoint,{passive:true});window.addEventListener('resize',schedulePoint);
 document.addEventListener('visibilitychange',()=>{clearTimeout(poll);if(!document.hidden)verify();});
 if(session.active)render();
}
