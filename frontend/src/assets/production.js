import {api,navBar,esc,stateChip,modeChip,toast,guard,icon,time} from '/assets/app.js?v=20261009-lan2';
import {setupDirectCreator} from '/assets/direct-creator.js?v=20261009-lan2';
const $=id=>document.getElementById(id);
$('nav').innerHTML=navBar('Production');
document.querySelectorAll('[data-icon]').forEach(el=>el.innerHTML=icon(el.dataset.icon));
const skeleton=()=>`<div class="loading-list" role="status" aria-label="正在读取"><div><div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line short"></div></div><div><div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line short"></div></div><span class="sr-only">正在读取，请稍候</span></div>`;
let pane='create',guide=null,libraryLoaded=false,toolsReady=false,toolLoad=null,libraryFlight=null,runsFlight=null,items=[],visibleCount=12,runCount=15,runs=[],timer=null;
const rows=new Map(),runRows=new Map(),detailCache=new Map();
const selectedContents=new Set();
let cleaning=false;
let libraryEpoch=0;
const syncHtml=(node,html)=>{if(node._html!==html){node.innerHTML=html;node._html=html;}};
function reconcile(container,map,entries,tag,render){
  const ids=new Set(entries.map(x=>x.id));
  for(const [id,node] of map)if(!ids.has(id)){node.remove();map.delete(id);}
  if(!map.size)container.replaceChildren();
  entries.forEach((entry,index)=>{let node=map.get(entry.id);if(!node){node=document.createElement(tag);map.set(entry.id,node);}render(node,entry);if(container.children[index]!==node)container.insertBefore(node,container.children[index]||null);});
}
async function showPane(name,{focus=false}={}){
  if(!['create','library','tasks','tools'].includes(name))name='create';pane=name;
  document.querySelectorAll('[data-pane]').forEach(b=>{const selected=b.dataset.pane===name;b.setAttribute('aria-selected',String(selected));b.tabIndex=selected?0:-1;});
  document.querySelectorAll('[role=tabpanel]').forEach(p=>p.hidden=p.id!=='pane-'+name);
  const url=new URL(location.href);if(name==='create')url.searchParams.delete('pane');else url.searchParams.set('pane',name);history.replaceState(null,'',url);
  if(focus)$('tab-'+name).focus();
  if(name==='library'&&!libraryLoaded)await loadLibrary();
  if(name==='tasks')await loadRuns();
  if(name==='tools')await loadTools();
  schedule();
}
document.querySelectorAll('[data-pane]').forEach(b=>b.onclick=guard(()=>showPane(b.dataset.pane)));
document.querySelector('.workspace-tabs').addEventListener('keydown',e=>{
  if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();
  const buttons=[...document.querySelectorAll('[data-pane]')];let index=buttons.findIndex(x=>x.dataset.pane===pane);
  index=e.key==='Home'?0:e.key==='End'?buttons.length-1:(index+(e.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;
  guard(()=>showPane(buttons[index].dataset.pane,{focus:true}))();
});
function filtered(){const query=$('library-search').value.trim().toLowerCase(),filter=$('library-filter').value;
  return items.filter(c=>(!query||`${c.topic} ${c.display_id}`.toLowerCase().includes(query))&&(filter==='all'||(filter==='review'?['ready_for_review','partially_approved','changes_requested'].includes(c.state):filter==='approved'?['approved','exported'].includes(c.state):!['ready_for_review','partially_approved','changes_requested','approved','exported'].includes(c.state))));}
function renderLibrary(){
  const matching=filtered(),shown=matching.slice(0,visibleCount);
  if(!shown.length){rows.clear();$('contents').innerHTML=`<tr><td colspan="7"><div class="state-empty"><h3>${items.length?'没有找到匹配内容':'你的第一篇内容，从这里开始'}</h3><p>${items.length?'换个关键词，或选择全部状态。':'先写下一个选题，再把它做成图文。'}</p>${!items.length?'<a class="solid-link" href="/views/Production.html">开始创作 →</a>':''}</div></td></tr>`;}
  else reconcile($('contents'),rows,shown,'tr',(row,c)=>{
    if(!row.cells.length)for(let i=0;i<7;i++)row.insertCell();
    const cells=[`<label class="library-checkbox"><input type="checkbox" data-select-content="${esc(c.id)}" aria-label="选择 ${esc(c.display_id)}"></label>`,`<span class="hint">${esc(c.display_id)}</span>`,esc(c.topic||'未命名内容'),stateChip(c.state),modeChip(c.run_mode),detailCache.get(c.id)||`<button data-detail="${esc(c.id)}">加载平台稿</button>`,`<button data-cid="${esc(c.id)}">打开成品</button><button class="danger" data-discard="${esc(c.id)}">丢弃</button>`];
    cells.forEach((html,index)=>syncHtml(row.cells[index],html));
  });
  $('library-pagination').textContent=`已显示 ${shown.length} / ${matching.length} 条`;$('library-more').hidden=shown.length>=matching.length;
  syncLibrarySelection();
}
function syncLibrarySelection(){
  const matching=filtered(),shown=matching.slice(0,visibleCount),count=shown.filter(c=>selectedContents.has(c.id)).length;
  const all=$('library-select-all');all.checked=shown.length>0&&count===shown.length;all.indeterminate=count>0&&count<shown.length;all.disabled=cleaning||!libraryLoaded||!shown.length;
  for(const [id,row] of rows){row.dataset.selected=String(selectedContents.has(id));const input=row.querySelector('[data-select-content]');if(input){input.checked=selectedContents.has(id);input.disabled=cleaning;}row.querySelector('[data-discard]')?.toggleAttribute('disabled',cleaning);}
  $('library-selection-count').textContent=`已选 ${selectedContents.size} 条`;
  $('library-select-matching').textContent=`选择全部筛选结果（${matching.length} 条）`;
  $('library-select-matching').hidden=matching.length<=shown.length;
  $('library-select-matching').disabled=cleaning||matching.every(c=>selectedContents.has(c.id));
  $('library-clear-selection').disabled=cleaning||!selectedContents.size;
  $('library-bulk-discard').disabled=cleaning||!selectedContents.size;
  if(!cleaning)$('library-bulk-discard').textContent='批量清理';
  ['library-search','library-filter','library-more'].forEach(id=>$(id).disabled=cleaning);
  $('btn-refresh').disabled=cleaning||Boolean(libraryFlight);
  $('contents').setAttribute('aria-busy',String(cleaning));
}
async function loadLibrary(){
  while(libraryFlight)await libraryFlight;
  const btn=$('btn-refresh');btn.disabled=true;$('refresh-note').textContent=libraryLoaded?'正在更新，保留现有列表…':'正在读取内容…';
  if(!libraryLoaded)$('contents').innerHTML=`<tr><td colspan="7">${skeleton()}</td></tr>`;
  libraryFlight=(async()=>{try{const data=await api.get('/contents');items=[...(data.items||[])].reverse();const current=new Set(items.map(c=>c.id));for(const id of selectedContents)if(!current.has(id))selectedContents.delete(id);libraryLoaded=true;libraryEpoch++;detailCache.clear();renderLibrary();$('library-count').textContent=items.length;$('refresh-note').textContent='平台稿按需读取。打开成品可继续改稿和预览。';}
  catch(e){$('refresh-note').textContent='内容未能更新：'+e.message+'。请点刷新重试。';if(!libraryLoaded)$('contents').innerHTML='<tr><td colspan="7"><div class="state-empty">内容暂时无法读取。你的草稿仍保留。</div></td></tr>';}
  finally{libraryFlight=null;syncLibrarySelection();}})();return libraryFlight;
}
$('btn-refresh').onclick=loadLibrary;
['library-search','library-filter'].forEach(id=>$(id).addEventListener(id==='library-search'?'input':'change',()=>{selectedContents.clear();visibleCount=12;renderLibrary();}));
$('library-more').onclick=()=>{visibleCount+=12;renderLibrary();};
$('contents').onchange=e=>{const input=e.target.closest('[data-select-content]');if(!input||cleaning)return;if(input.checked)selectedContents.add(input.dataset.selectContent);else selectedContents.delete(input.dataset.selectContent);syncLibrarySelection();};
$('library-select-all').onchange=e=>{if(cleaning)return;filtered().slice(0,visibleCount).forEach(c=>e.target.checked?selectedContents.add(c.id):selectedContents.delete(c.id));syncLibrarySelection();};
$('library-select-matching').onclick=()=>{if(cleaning)return;filtered().forEach(c=>selectedContents.add(c.id));syncLibrarySelection();};
$('library-clear-selection').onclick=()=>{selectedContents.clear();syncLibrarySelection();};
async function discardContents(ids){
  if(cleaning||!ids.length)return;
  const targets=items.filter(c=>ids.includes(c.id));if(!targets.length)return;
  if(!confirm(`清理选中的 ${targets.length} 条内容？\n${targets.slice(0,5).map(c=>c.display_id).join('、')}${targets.length>5?' 等':''}\n内容将移出列表，未完成的制作任务会停止。审核、调用与发布记录仍保留，平台上的作品保持原状。`))return;
  cleaning=true;syncLibrarySelection();let done=0,processed=0;const failures=[];
  try{
    for(const c of targets){
      $('library-cleanup-note').textContent=`正在清理 ${processed+1} / ${targets.length} 条…`;$('library-bulk-discard').textContent=`清理中 ${processed+1} / ${targets.length}`;
      try{await api.post('/contents/'+encodeURIComponent(c.id)+'/discard',{});done++;selectedContents.delete(c.id);detailCache.delete(c.id);items=items.filter(x=>x.id!==c.id);$('library-count').textContent=items.length;renderLibrary();}
      catch(e){failures.push(`${c.display_id}：${e.message}`);}processed++;
    }
    await loadLibrary();
    $('library-cleanup-note').textContent=`已清理 ${done} 条。${failures.length?`${failures.length} 条未能清理${targets.some(c=>selectedContents.has(c.id))?'，保留勾选，可再次尝试':''}。${failures.slice(0,3).join('；')}${failures.length>3?'；其余失败条目仍在列表中。':''}`:'审核与调用记录仍保留。'}`;
    $('library-cleanup-note').classList.toggle('cleanup-error',failures.length>0);
    toast(failures.length?`已清理 ${done} 条，${failures.length} 条未完成。`:`已清理 ${done} 条内容。`,failures.length?'err':'ok');
  }finally{cleaning=false;syncLibrarySelection();}
}
$('library-bulk-discard').onclick=guard(()=>discardContents([...selectedContents]));
$('contents').onclick=guard(async e=>{
  const b=e.target.closest('button');if(!b||b.disabled)return;
  if(b.dataset.cid){location.href='/views/ReviewPreview.html?content='+encodeURIComponent(b.dataset.cid);return;}
  if(b.dataset.detail){b.disabled=true;b.textContent='读取中…';const id=b.dataset.detail,epoch=libraryEpoch;
    try{const d=await api.get('/contents/'+encodeURIComponent(id));if(epoch!==libraryEpoch)return;const rev=d.revisions?.find(x=>x.revision_id===d.active_revision_id)||d.revisions?.at(-1);
      const html=rev?.platforms?.map(p=>`<div>${esc(p.platform==='douyin'?'抖音':p.platform==='xiaohongshu'?'小红书':p.platform)} ${stateChip(p.state)} <span class="hint">${p.page_count??0} 页</span><button data-render="${esc(p.platform_revision_id)}">重新出图</button></div>`).join('')||'<span class="hint">尚未出图</span>';detailCache.set(id,html);renderLibrary();
    }catch(e){if(b.isConnected){b.disabled=false;b.textContent='重试读取';}throw e;}return;}
  if(b.dataset.discard){await discardContents([b.dataset.discard]);return;}
  if(b.dataset.render){b.disabled=true;try{const r=await api.post('/platform-revisions/'+encodeURIComponent(b.dataset.render)+'/render',{});toast(`已重新生成 ${r.artifacts?.length||0} 张页图。`,'ok');await loadLibrary();}finally{b.disabled=false;}}
});
const stageNames={produce:'图文制作',research:'资料调研',topic:'确认选题',planning:'组织内容',media:'准备配图',compose:'写作',render:'排版出图',audit:'检查要求',batch_dispatch:'安排生产'};
function renderRuns(){const shown=runs.slice(0,runCount);if(!shown.length){runRows.clear();$('runs').innerHTML='<div class="state-empty"><h3>还没有制作任务</h3><p>开始创作后，进度会出现在这里。</p><a class="solid-link" href="/views/Production.html">写第一篇 →</a></div>';}
  else reconcile($('runs'),runRows,shown,'article',(node,r)=>{node.className='run';const actions=['queued','running','paused'].includes(r.state)?`<button data-control="${esc(r.id)}" data-action="${r.state==='paused'?'resume':'pause'}">${r.state==='paused'?'继续':'暂停'}</button><button data-control="${esc(r.id)}" data-action="cancel">取消</button>`:r.state==='failed'?`<button data-control="${esc(r.id)}" data-action="retry">修复后继续</button>`:'';
    // Keep the node and focus stable on unchanged polling results.
    const html=`<span class="stage">${esc(stageNames[r.stage]||(r.stage?.startsWith('render:')?'平台出图':r.stage)||'制作任务')}</span>${stateChip(r.state)}${r.mode?modeChip(r.mode):''}<span class="hint">${time(r.created_at)}</span>${actions}<div class="run-progress">${(r.jobs||[]).map(j=>esc(stageNames[j.stage]||j.stage)+' '+stateChip(j.state)).join(' · ')}</div>${r.error?`<p class="creator-error">${esc(r.error)}</p>`:''}<details><summary>任务诊断信息</summary><p>任务 ${esc(r.id)} · 尝试 ${r.attempt??'未记录'} · 租约 ${r.fencing_token??'未记录'}</p><code>${esc(r.input_hash||'')}</code></details>`;
    if(node._html!==html){const active=node.contains(document.activeElement)?{...document.activeElement.dataset}:null;const open=node.querySelector('details')?.open;syncHtml(node,html);if(open)node.querySelector('details').open=true;if(active)node.querySelector(`[data-action="${active.action}"]`)?.focus({preventScroll:true});}
  });$('tasks-more').hidden=shown.length>=runs.length;
}
function schedule(){clearTimeout(timer);timer=null;const active=runs.some(r=>['queued','running'].includes(r.state));if(!document.hidden&&(active||guide?.hasActiveTask()))timer=setTimeout(()=>loadRuns({background:true}),4500);}
async function loadRuns({background=false}={}){
  while(runsFlight){if(background)return;await runsFlight;}const btn=$('tasks-refresh');btn.disabled=true;if(!runs.length&&pane==='tasks')$('runs').innerHTML=skeleton();
  runsFlight=(async()=>{try{const data=await api.get('/runs');runs=data.items||[];if(pane==='tasks')renderRuns();guide?.updateRuns(runs);const n=runs.filter(r=>['queued','running'].includes(r.state)).length;$('task-count').textContent=n||'';$('tasks-note').textContent=n?`${n} 个任务进行中 · 进度自动更新`:'当前没有进行中的任务，自动更新已停止。';}
    catch(e){$('tasks-note').textContent='进度暂时无法读取：'+e.message+'。请点刷新重试。';if(pane==='tasks'&&!runs.length)$('runs').innerHTML='<div class="state-empty">任务记录读取失败，请刷新重试。</div>';if(!background)toast('任务进度读取失败，已有内容已保留。','err');}
    finally{btn.disabled=false;runsFlight=null;schedule();}})();return runsFlight;
}
$('tasks-refresh').onclick=()=>loadRuns();$('tasks-more').onclick=()=>{runCount+=15;renderRuns();};
$('runs').onclick=guard(async e=>{const b=e.target.closest('[data-control]');if(!b||b.disabled)return;b.disabled=true;try{await api.post('/runs/'+encodeURIComponent(b.dataset.control)+'/control',{action:b.dataset.action});await loadRuns();}finally{b.disabled=false;}});
async function loadTools(){if(toolsReady)return;if(toolLoad)return toolLoad;$('tools-content').innerHTML=skeleton();toolLoad=(async()=>{try{const [response,module]=await Promise.all([fetch('/assets/production-tools.html?v=20261009-lan2'),import('/assets/production-tools.js?v=20261009-lan2')]);if(!response.ok)throw Error('工具页面读取失败');$('tools-content').innerHTML=await response.text();await module.setupTools({refresh:async()=>{await loadRuns();libraryLoaded=false;}});toolsReady=true;}
  catch(e){$('tools-content').innerHTML=`<div class="state-empty"><h3>工具暂时未能载入</h3><p>${esc(e.message)}</p><button id="tools-retry">重新载入</button></div>`;$('tools-retry').onclick=()=>loadTools();}finally{toolLoad=null;}})();return toolLoad;}
document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden&&(pane==='tasks'||guide?.hasTask()))loadRuns({background:true});});
// 「新建创作」是整页的主入口。它一旦在初始化阶段抛错，页面就永远停在
// 「读取创作设置」上，而用户只看到一次瞬时提示（生产环境实际遇到过：
// 用 http://内网IP:8000 打开时 crypto.randomUUID 不存在）。这里改成
// 显示可读原因，并让「我的内容 / 制作任务 / 高级工具」页签照常可用。
try{guide=await setupDirectCreator({onQueued:async()=>{libraryLoaded=false;await loadRuns();}});}
catch(e){guide={hasTask:()=>false,hasActiveTask:()=>false,updateRuns:()=>{}};
  $('creator-model').textContent='创作设置未能载入';
  $('creator-error').textContent='创作设置读取失败：'+(e?.message||e)+'。请刷新重试；若一直失败，改用 http://localhost:8000 打开，或把当前地址配成 HTTPS。';
  toast('创作设置未能载入，原因已显示在「新建创作」里','err');}
const initial=new URL(location.href).searchParams.get('pane')||'create';await showPane(initial);
if(guide.hasTask())await loadRuns();
document.addEventListener('creator-task-changed',()=>{schedule();});
