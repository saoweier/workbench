import {api, navBar, esc} from '/assets/app.js?v=20261009-lan2';
const $ = id => document.getElementById(id);
$('nav').innerHTML = navBar('DouyinPublishing');
const states = {planned:'图文准备已保存', upload_requested:'等待上传', uploading:'正在上传图文', awaiting_editor:'正在核对编辑页',
 awaiting_confirmation:'图文已填好，等待你确认发布', publish_requested:'等待执行发布', submitting:'正在提交发布', verifying:'正在核实作品',
 succeeded:'已核实平台作品', awaiting_review:'平台审核中', unknown:'提交结果待核实，未自动重发', upload_failed:'上传未完成',
 observation_failed:'回访暂未完成', cancelled:'已取消本轮准备', stale:'图文已变化，请重新准备', rejected:'平台审核未通过'};
let preferences = {mode:'manual'}, account = null, timer = null, busy = false, paused = false, actionError = '';
const choices = new Map(), tasks = new Map(), signatures = new Map();
async function post(path, body={}) {
 const res = await fetch('/api/v1'+path,{method:'POST',headers:{'Content-Type':'application/json','X-CWB-Local-Action':'account-connection'},body:JSON.stringify(body)});
 const data = await res.json(); if(!res.ok) throw new Error(data.error?.message || (typeof data.detail==='string' ? data.detail : '操作未完成，请刷新状态后重试。')); return data;
}
function accountState(data) {
 account = data;
 $('status').textContent = data.verified_now ? `当前连接：${data.account?.nickname || '抖音账号'} · 请核对官方窗口中的账号` : '使用前请到「平台账号」打开连接窗口并核对登录。';
 $('prepare-publish').disabled = busy || !data.account?.account_id || !choices.has($('publish-content').value);
}
function render(task) {
 tasks.set(task.id,task);
 const signature = JSON.stringify(task); if(signatures.get(task.id)===signature) return;
 signatures.set(task.id,signature);
 let row = document.getElementById('task-'+task.id);
 if(!row) {row=document.createElement('section');row.id='task-'+task.id;row.className='publish-task';$('publish-tasks').append(row);}
 const canConfirm=preferences.mode==='assisted'&&task.state==='awaiting_confirmation', canUpload=preferences.mode==='assisted'&&['planned','upload_failed'].includes(task.state);
 const canObserve=['succeeded','unknown','awaiting_review','observation_failed'].includes(task.state);
 const canCancel=['planned','upload_requested','upload_failed','awaiting_confirmation','publish_requested'].includes(task.state);
 const metrics=task.result.metrics || {};
 const metricNames={views:'播放 / 阅读',likes:'点赞',comments:'评论',collects:'收藏',shares:'分享'};
 row.innerHTML=`<h2>${esc(task.title)}</h2><div class="publish-meta"><span class="chip acc">${esc(states[task.state]||task.state)}</span><span>${esc(task.display_id)} · ${task.images.length} 张图</span><span>抖音号 ${esc(task.account_id)}</span></div>
 <div class="publish-images">${task.images.map(i=>`<a href="${esc(i.url)}" target="_blank" rel="noopener"><img src="${esc(i.url)}" alt="第 ${i.page_index} 页" loading="lazy" /></a>`).join('')}</div>
 <details><summary>查看完整发布文案</summary><div class="publish-caption">${esc(task.caption)}</div></details>
 <p class="publish-message">${esc(task.result.message || '')}</p>${task.error?`<p class="publish-error">${esc(task.error)}</p>`:''}
 ${task.result.data_center_notice?`<p class="publish-message">${esc(task.result.data_center_notice)}</p>`:''}
 ${task.result.post_id?`<p class="publish-message">作品编号：${esc(task.result.post_id)} ${/^https:\/\/www\.douyin\.com\/(video|note)\/\d+$/.test(task.result.link||'')?`<a href="${esc(task.result.link)}" target="_blank" rel="noopener">查看实际作品 ↗</a>`:''}</p>`:''}
 ${task.result.observed_at?`<p class="publish-message">最近回访：${esc(new Date(task.result.observed_at).toLocaleString('zh-CN'))} · ${esc(task.result.metrics_status || '待核实')}</p>`:''}
 ${Object.keys(metrics).length?`<div class="publish-metrics">${Object.entries(metrics).map(([name,v])=>`<span>${esc(metricNames[name]||name)}：${v.value==null?'待更新':esc(v.value)}</span>`).join('')}</div>`:''}
 ${canConfirm?`<label class="publish-confirm"><input type="checkbox" data-consent="${task.id}" /><span>我已核对以上图文及官方窗口里的账号，确认将这一版发布到抖音号 ${esc(task.account_id)}。发布后平台可能仍需审核。</span></label>`:''}
 <div class="publish-actions">${canUpload?`<button class="btn primary" data-upload="${task.id}">上传到抖音，准备发布</button>`:''}${canConfirm?`<button class="btn primary" data-confirm="${task.id}" disabled>批准这版图文并发布</button>`:''}${canObserve?`<button class="btn" data-observe="${task.id}">核实作品并回访数据</button>`:''}${canCancel?`<button class="btn" data-cancel="${task.id}">取消本轮准备</button>`:''}<a class="btn" href="/views/ReviewPreview.html?content=${esc(task.content_id)}">查看原始预览</a>${task.publication_id?'<a class="btn" href="/views/Publications.html">查看发布记录</a><a class="btn" href="/views/ReviewInsights.html">进入复盘</a>':''}</div>`;
 row.querySelector('[data-consent]')?.addEventListener('change',e=>{row.querySelector('[data-confirm]').disabled=!e.target.checked;});
 for(const action of ['upload','confirm','observe','cancel']) row.querySelector(`[data-${action}]`)?.addEventListener('click',()=>perform(async()=>{
  const payload={expected_payload_hash:task.payload_hash}; if(action==='confirm') Object.assign(payload,{account_id:task.account_id,confirmed:true});
  render(await post(`/douyin/tasks/${task.id}/${action}`,payload));
 }));
}
async function perform(fn) { if(busy)return;busy=true;clearTimeout(timer);actionError='';$('publish-error').textContent='';document.querySelectorAll('.publish-task button').forEach(b=>b.disabled=true);$('prepare-publish').disabled=true;
 try {await fn();} catch(e) {actionError=e.message;$('publish-error').textContent=e.message;} finally {busy=false;signatures.clear();await refresh();} }
async function refresh() {if(busy||paused)return; try {const [status,list,prefs]=await Promise.all([api.get('/accounts/douyin'),api.get('/douyin/tasks'),api.get('/publishing/preferences')]);if(preferences.mode!==prefs.mode)signatures.clear();preferences=prefs;$('publishing-mode').textContent=prefs.mode==='manual'?'当前为手动发布：辅助上传和发布按钮已关闭。':'当前为浏览器辅助发布：每一版仍需本人确认。';accountState(status);list.items.forEach(render);$('publish-error').textContent=actionError;}
 catch(e){$('publish-error').textContent=e.message;}finally{clearTimeout(timer);if(!paused&&!document.hidden)timer=setTimeout(refresh,4000);} }
$('prepare-publish').onclick=()=>perform(async()=>{const id=$('publish-content').value;if(!choices.has(id))throw new Error('请先选择内容');render(await post('/douyin/tasks',{platform_revision_id:id}));});
$('publish-content').onchange=()=>account && accountState(account);
document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)refresh();});
window.addEventListener('pagehide',()=>{paused=true;clearTimeout(timer);});window.addEventListener('pageshow',()=>{if(paused){paused=false;refresh();}});
async function initialize(){try{const contents=await api.get('/contents');const real=contents.items.filter(c=>c.run_mode==='real');const details=await Promise.all(real.map(c=>api.get('/contents/'+c.id)));for(const c of details){const r=c.revisions.find(r=>r.revision_id===c.active_revision_id);const p=r?.platforms.find(p=>p.platform==='douyin'&&p.artifacts.length);if(p)choices.set(p.platform_revision_id,{content:c,platform:p});}
 $('publish-content').innerHTML=choices.size?[...choices].map(([id,{content,platform}])=>`<option value="${esc(id)}">${esc(content.display_id+' · '+platform.title)}</option>`).join(''):'<option value="">还没有真实生成的图文，请先去内容生产</option>';$('publish-content').disabled=!choices.size;await refresh();}catch(e){$('publish-error').textContent=e.message;}}
initialize();
