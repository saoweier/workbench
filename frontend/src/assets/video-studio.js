import {api,navBar,esc,uuid} from './app.js?v=20261009-lan1';
import {drawPreview,loadImage} from './video-preview.js';
const $=id=>document.getElementById(id);
$('nav').innerHTML=navBar('VideoStudio');
let source=null,config=null,busy=false,sourceSequence=0,planSequence=0,pollTimer=null,editTimer=null,requestKey=null;
let activePage=0,activeSceneId=null,storyboard=null,choices=new Map(),uploading=false,planning=false;
let playing=false,frameHandle=null,lastTime=null,phase=.55,previewSequence=0,previewImage=null,characterImage=null;
const sources=new Map(),presenterImages=new Map(),signatures=new Map(),directors=new Map();
const reducedMotion=matchMedia('(prefers-reduced-motion: reduce)');
const scripts=()=>[...document.querySelectorAll('[data-script]')].map(t=>t.value);
const scene=()=>storyboard?.scenes.find(s=>s.id===activeSceneId);
async function post(path,data){const r=await fetch('/api/v1'+path,{method:'POST',headers:{'Content-Type':'application/json','X-CWB-Local-Action':'account-connection'},body:JSON.stringify(data)});const d=await r.json();if(!r.ok)throw Error(d.error?.message||(typeof d.detail==='string'?d.detail:'操作未完成，请检查输入。'));return d;}
function ready(){
  let hint='';
  if(!source)hint='先选择一份已经完成的图文。';
  else if(uploading)hint='图片正在保存，请稍候。';
  else if(planning)hint='正在更新逐句分镜…';
  else if(!storyboard)hint='请检查讲稿，并等待分镜整理完成。';
  else if($('presenter').value==='custom'&&!presenterImages.has($('presenter-image').value))hint='展开讲解员设置，上传或选择角色图片。';
  else if($('voice-engine').value==='api'&&!config)hint='请先配置自己的语音 API。';
  else if($('voice-engine').value!=='local'&&!$('cloud-consent').checked)hint='确认在线配音后，即可生成视频。';
  $('ready-hint').textContent=hint;$('make-video').disabled=busy||!!hint;
  $('make-video').textContent=busy?'正在提交…':'生成讲解视频 →';
  const count=storyboard?.scenes.length||0;
  $('generation-summary').textContent=source?`${source.pages.length} 个章节 · ${count} 个分镜${storyboard?' · 预计 '+Math.round(storyboard.duration_estimate/(1+Number($('voice-rate').value)/100))+' 秒':''}`:'先选择内容';
}
function stopPreview(){playing=false;cancelAnimationFrame(frameHandle);lastTime=null;$('preview-play').textContent='播放分镜';}
function paint(){
  const current=scene();drawPreview($('motion-preview'),current,reducedMotion.matches ? .75 : phase,previewImage,characterImage,{pageTitle:source?.pages[activePage]?.heading,pageCount:source?.pages.length,sceneCount:storyboard?.scenes.filter(s=>s.page_index===activePage).length,presenter:$('presenter').value,style:$('animation-style').value});
  $('preview-scrub').value=Math.round(phase*1000);
}
async function updatePreview(){
  const current=scene(),sequence=++previewSequence;stopPreview();
  if(!current){previewImage=null;paint();return;}
  const img=await loadImage(current.material_url||current.image_url);if(sequence!==previewSequence)return;
  previewImage=img;paint();
}
function selectScene(id){
  activeSceneId=id;phase=.55;const current=scene();
  document.querySelectorAll('[data-scene]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.scene===id)));
  $('scene-effect').querySelector('[value=chart]').disabled=!current?.chart?.length;
  $('scene-effect').value=choices.get(id)?.effect||'';$('scene-effect').disabled=!current||planning;
  $('material-upload-button').disabled=!current||uploading;
  $('material-clear').hidden=!current?.material_id;
  $('preview-position').textContent=current?`第 ${current.page_index+1} 章 · 第 ${current.sentence_index+1} 句`:'';
  $('preview-play').disabled=!current;$('preview-replay').disabled=!current;
  $('material-message').textContent=current?.material_id?'这一句已使用自定义图片。':'';
  updatePreview();
}
function showPage(index){
  activePage=index;
  document.querySelectorAll('[data-page]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.page)===index)));
  document.querySelectorAll('[data-script-panel]').forEach(p=>p.hidden=Number(p.dataset.scriptPanel)!==index);
  $('current-page-title').textContent=source?.pages[index]?.heading||'选择章节';
  const scenes=storyboard?.scenes.filter(s=>s.page_index===index)||[];
  $('sentence-list').innerHTML=scenes.map(s=>`<button class="sentence-row" data-scene="${s.id}" aria-pressed="false"><span class="sentence-number">${String(s.sentence_index+1).padStart(2,'0')}</span><span class="sentence-text">${esc(s.text)}<small>${esc(s.effect_label)}${s.material_id?' · 自定义图片':''} · 约 ${Math.round(s.duration)} 秒</small></span></button>`).join('');
  $('scene-summary').textContent=scenes.length?scenes.length+' 句讲解':'';
  $('sentence-list').querySelectorAll('[data-scene]').forEach(button=>button.onclick=()=>selectScene(button.dataset.scene));
  selectScene(scenes.some(s=>s.id===activeSceneId)?activeSceneId:scenes[0]?.id);
}
async function refreshStoryboard(){
  if(!source)return;clearTimeout(editTimer);const sequence=++planSequence,currentSource=source;
  planning=true;ready();stopPreview();$('storyboard-state').textContent='更新中…';$('scene-effect').disabled=true;
  try{
    const result=await post('/videos/storyboard',{platform_revision_id:source.platform_revision_id,scripts:scripts(),scene_choices:[...choices.values()],director_preset:$('director-preset').value});
    if(sequence!==planSequence||source!==currentSource)return;
    storyboard=result;const valid=new Set(result.scenes.map(s=>s.id));choices=new Map([...choices].filter(([id])=>valid.has(id)));
    $('preview-loading').hidden=true;$('storyboard-state').textContent='已自动编排';$('script-message').textContent='修改讲稿后，分镜会自动更新。';
    showPage(activePage);
  }catch(e){if(sequence===planSequence){storyboard=null;$('storyboard-state').textContent='请检查讲稿';$('script-message').textContent=e.message;$('sentence-list').innerHTML='';$('preview-loading').hidden=false;$('preview-loading').textContent=e.message;selectScene(null);}}
  finally{if(sequence===planSequence){planning=false;$('scene-effect').disabled=!scene();ready();}}
}
async function loadSource(){
  const sequence=++sourceSequence;source=null;storyboard=null;choices.clear();activePage=0;activeSceneId=null;++planSequence;stopPreview();ready();
  $('fill-full-script').disabled=true;$('fill-short-script').disabled=true;$('video-error').textContent='';$('preview-loading').hidden=false;$('preview-loading').textContent='正在整理分镜…';
  document.querySelector('.editing-desk').setAttribute('aria-busy','true');
  try{
    const data=await api.get('/videos/source/'+$('video-source').value);if(sequence!==sourceSequence)return;
    source=data;requestKey=null;$('page-count').textContent=String(data.pages.length);$('fill-full-script').disabled=false;$('fill-short-script').disabled=false;
    $('page-tabs').innerHTML=data.pages.map((p,i)=>`<button class="chapter-tab" data-page="${i}" aria-label="第 ${i+1} 章：${esc(p.heading)}" aria-pressed="${i===0}"><img src="${esc(p.image_url)}" alt="第 ${i+1} 章图文" loading="lazy" decoding="async"><b>第 ${i+1} 章</b><span>${esc(p.heading)}</span></button>`).join('');
    $('page-tabs').querySelectorAll('[data-page]').forEach(button=>button.onclick=()=>{stopPreview();showPage(Number(button.dataset.page));});
    $('video-scripts').innerHTML=data.pages.map((p,i)=>`<div data-script-panel="${i}" ${i?'hidden':''}><textarea data-script="${p.index}" aria-label="第 ${p.index} 页讲稿" maxlength="1200">${esc(p.script)}</textarea></div>`).join('');
    $('status').textContent=`${data.display_id} · ${data.pages.length} 章 · 竖屏视频`;
    await refreshStoryboard();
  }catch(e){if(sequence===sourceSequence){$('video-error').textContent=e.message;$('status').textContent='内容未读取完成';}}
  finally{document.querySelector('.editing-desk').setAttribute('aria-busy','false');ready();}
}
function edited(){requestKey=null;planning=true;++planSequence;stopPreview();ready();clearTimeout(editTimer);editTimer=setTimeout(refreshStoryboard,450);}
$('video-scripts').addEventListener('input',edited);
$('video-source').onchange=loadSource;
for(const [id,key] of [['fill-full-script','full_script'],['fill-short-script','script']])$(id).onclick=()=>{if(!source)return;document.querySelectorAll('[data-script]').forEach((field,i)=>field.value=source.pages[i][key]);edited();$('script-message').textContent='讲稿已更新，正在重新编排分镜。';};
$('scene-effect').onchange=()=>{const current=scene();if(!current)return;const previous=choices.get(current.id);choices.set(current.id,{scene_id:current.id,effect:$('scene-effect').value||null,material_id:previous?.material_id||null});requestKey=null;refreshStoryboard();};
$('material-upload-button').onclick=()=>$('material-upload').click();
$('material-clear').onclick=()=>{const current=scene();if(!current)return;const choice=choices.get(current.id);choices.set(current.id,{scene_id:current.id,effect:choice?.effect||null,material_id:null});requestKey=null;refreshStoryboard();};
async function upload(path,file){if(file.size>10*1024*1024)throw Error('图片不能超过 10 MB。');const response=await fetch('/api/v1'+path+'?name='+encodeURIComponent(file.name),{method:'POST',headers:{'Content-Type':file.type||'application/octet-stream','X-CWB-Local-Action':'account-connection'},body:file});const asset=await response.json();if(!response.ok)throw Error(asset.error?.message||(typeof asset.detail==='string'?asset.detail:'图片未保存，请检查格式。'));return asset;}
$('material-upload').onchange=async()=>{const file=$('material-upload').files[0],current=scene(),revision=source?.platform_revision_id;if(!file||!current)return;uploading=true;ready();$('material-upload-button').disabled=true;$('material-message').textContent='正在保存这句的图片…';try{const asset=await upload('/video-materials',file);if(source?.platform_revision_id!==revision)return;choices.set(current.id,{scene_id:current.id,effect:'image',material_id:asset.id});requestKey=null;await refreshStoryboard();}catch(e){$('material-message').textContent=e.message;}finally{uploading=false;$('material-upload').value='';$('material-upload-button').disabled=!scene();ready();}};
async function presenterChanged(){
  const kind=$('presenter').value,asset=presenterImages.get($('presenter-image').value);
  $('custom-presenter').hidden=kind!=='custom';$('presenter-note').hidden=kind!=='guide';$('presenter-preview-image').hidden=!asset;
  $('presenter-setting-summary').textContent=kind==='none'?'不显示讲解员':kind==='guide'?'原版动画角色':asset?.name||'请上传角色图片';
  if(asset)$('presenter-preview-image').src=asset.image_url;
  const selected=asset?.id;characterImage=asset?await loadImage(asset.image_url):null;
  if(presenterImages.get($('presenter-image').value)?.id!==selected)return;
  paint();ready();
}
function fillPresenterOptions(selected){$('presenter-image').innerHTML=presenterImages.size?[...presenterImages.values()].map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join(''):'<option value="">先上传角色图片</option>';if(presenterImages.has(selected))$('presenter-image').value=selected;presenterChanged();}
$('presenter').onchange=()=>{requestKey=null;presenterChanged();};
$('presenter-image').onchange=()=>{requestKey=null;try{localStorage.setItem('cwb-presenter-image',$('presenter-image').value);}catch{}presenterChanged();};
$('presenter-upload-button').onclick=()=>$('presenter-upload').click();
$('presenter-upload').onchange=async()=>{const file=$('presenter-upload').files[0];if(!file)return;uploading=true;ready();$('presenter-upload-button').disabled=true;try{const asset=await upload('/video-presenters',file);presenterImages.set(asset.id,asset);$('presenter').value='custom';fillPresenterOptions(asset.id);requestKey=null;try{localStorage.setItem('cwb-presenter-image',asset.id);}catch{}$('presenter-message').textContent='图片已保存并选中，生成视频时会使用这张图片。';}catch(e){$('presenter-message').textContent=e.message;}finally{uploading=false;$('presenter-upload').value='';$('presenter-upload-button').disabled=false;ready();}};
function tick(time){if(!playing)return;const current=scene();if(!current){stopPreview();return;}const delta=lastTime===null?0:(time-lastTime)/1000;lastTime=time;phase+=delta/current.duration;if(phase>=1){const next=storyboard.scenes[storyboard.scenes.findIndex(s=>s.id===current.id)+1];if(!next){phase=1;paint();stopPreview();return;}activeSceneId=next.id;activePage=next.page_index;showPage(activePage);phase=0;playing=true;$('preview-play').textContent='暂停预览';lastTime=time;}paint();frameHandle=requestAnimationFrame(tick);}
$('preview-play').onclick=()=>{if(playing){stopPreview();return;}phase=0;playing=true;lastTime=null;$('preview-play').textContent='暂停预览';frameHandle=requestAnimationFrame(tick);};
$('preview-replay').onclick=()=>{stopPreview();phase=0;paint();if(!reducedMotion.matches){playing=true;$('preview-play').textContent='暂停预览';frameHandle=requestAnimationFrame(tick);}};
$('preview-scrub').oninput=()=>{stopPreview();phase=Number($('preview-scrub').value)/1000;paint();};
reducedMotion.addEventListener('change',()=>{stopPreview();paint();});
function engineChanged(){const engine=$('voice-engine').value;$('voice-name').parentElement.hidden=engine==='local';$('voice-name').hidden=engine!=='edge';$('api-voice').hidden=engine!=='api';$('cloud-line').hidden=engine==='local';$('cloud-consent').checked=false;requestKey=null;ready();}
$('voice-engine').onchange=engineChanged;$('cloud-consent').onchange=ready;for(const id of ['voice-name','voice-rate','api-voice'])$(id).onchange=()=>{requestKey=null;ready();};
$('director-preset').onchange=()=>{requestKey=null;$('director-description').textContent=directors.get($('director-preset').value)?.description||'';refreshStoryboard();};
$('animation-style').onchange=()=>{requestKey=null;paint();ready();};
$('make-video').onclick=async()=>{if(busy||$('make-video').disabled)return;busy=true;ready();$('video-error').textContent='';try{requestKey ||= uuid();const engine=$('voice-engine').value;const task=await post('/videos',{platform_revision_id:source.platform_revision_id,expected_manifest_hash:source.manifest_hash,scripts:scripts(),engine,voice:engine==='api'?$('api-voice').value.trim():$('voice-name').value,rate:Number($('voice-rate').value),presenter:$('presenter').value,presenter_image_id:$('presenter').value==='custom'?$('presenter-image').value:null,animation_style:$('animation-style').value,scene_choices:[...choices.values()],director_preset:$('director-preset').value,request_key:requestKey,cloud_consent:$('cloud-consent').checked,config_id:config?.id||null});$('video-message').textContent=task.state==='succeeded'?'已有视频已复用。':'视频任务已保存，后台会开始配音与渲染。';renderTask(task);poll();}catch(e){$('video-error').textContent=e.message;}finally{busy=false;ready();}};
$('save-speech').onclick=async()=>{try{config=(await post('/video-speech/config',{base_url:$('speech-base').value.trim(),model_id:$('speech-model').value.trim(),api_key:$('speech-key').value||null,allow_localhost:$('speech-local').checked})).config;$('speech-key').value='';$('speech-message').textContent='配置已保存，尚未调用语音服务。';requestKey=null;ready();}catch(e){$('speech-message').textContent=e.message;}};
$('open-motion-demo').onclick=()=>{$('motion-demo').open=true;$('motion-demo').scrollIntoView({behavior:reducedMotion.matches?'auto':'smooth',block:'start'});$('motion-demo-player').play().catch(()=>{});};
const states={queued:'等待生成',running:'正在配音与渲染',succeeded:'视频已完成',failed:'生成未完成',unknown:'配音响应不确定',interrupted:'任务中断',cancelled:'已取消'};
function renderTask(t){const key=JSON.stringify(t);if(signatures.get(t.id)===key)return;signatures.set(t.id,key);let row=$('video-'+t.id);if(!row){row=document.createElement('article');row.id='video-'+t.id;row.className='video-result';$('video-results').prepend(row);}row.innerHTML=`<h3>${esc(t.display_id+' · '+t.title)}</h3><div class="video-state"><span class="chip acc">${esc(states[t.state]||t.state)}</span><progress max="100" value="${t.progress}"></progress><span>${t.progress}%</span></div>${t.error?`<p class="studio-error">${esc(t.error)}</p>`:''}${t.video_url?`<p class="studio-note">${t.animation_style==='presentation'?'动态讲解 · '+t.result.scene_count+' 个分镜 · ':''}${t.result.duration_seconds} 秒 · ${t.result.width} × ${t.result.height} · ${t.engine==='local'?'本机系统朗读':'AI 合成配音'}</p><video controls preload="metadata" src="${esc(t.video_url)}"></video><div class="actions"><a class="btn primary" href="${esc(t.video_url)}?download=true">下载 MP4</a><a class="btn" href="${esc(t.audio_url)}?download=true">下载配音</a><a class="btn" href="${esc(t.subtitles_url)}?download=true">下载字幕</a>${t.animation_style==='presentation'?`<a class="btn" href="/api/v1/videos/${esc(t.id)}/media/storyboard.json?download=true">下载分镜计划</a>`:''}<a class="btn" href="/views/ManualPublishing.html?content=${esc(t.content_id)}">准备手动发布</a></div>`:(['queued','running'].includes(t.state)?`<button class="editor-button" data-cancel-video="${t.id}">取消任务</button>`:'<p class="studio-note">核对原因后修改讲稿或选项，再重新生成。</p>')}`;row.querySelector('[data-cancel-video]')?.addEventListener('click',async()=>{try{renderTask(await post('/videos/'+t.id+'/cancel',{}));}catch(e){$('video-error').textContent=e.message;}});}
let polling=false;async function poll(){clearTimeout(pollTimer);if(document.hidden||polling)return;polling=true;let active=false;try{const data=await api.get('/videos');data.items.forEach(renderTask);active=data.items.some(t=>['queued','running'].includes(t.state));}catch(e){$('video-error').textContent=e.message;}finally{polling=false;if(active&&!document.hidden)pollTimer=setTimeout(poll,3000);}}
document.addEventListener('visibilitychange',()=>{clearTimeout(pollTimer);if(document.hidden)stopPreview();else poll();});window.addEventListener('pagehide',()=>{clearTimeout(pollTimer);stopPreview();});
paint();ready();
try{
  const [list,cfg,assets,direction]=await Promise.all([api.get('/contents'),api.get('/video-speech/config'),api.get('/video-presenters'),api.get('/videos/director-presets')]);
  direction.items.forEach(p=>directors.set(p.id,p));$('director-version').textContent=direction.skill+' · v'+direction.version;config=cfg.config;
  if(config){$('speech-base').value=config.base_url;$('speech-model').value=config.model_id;$('speech-local').checked=config.allow_localhost;}
  assets.items.forEach(p=>presenterImages.set(p.id,p));let saved=null;try{saved=localStorage.getItem('cwb-presenter-image');}catch{}fillPresenterOptions(saved);
  const details=await Promise.all(list.items.map(c=>api.get('/contents/'+c.id)));
  for(const c of details)for(const p of (c.revisions.find(r=>r.revision_id===c.active_revision_id)?.platforms||[]))if(p.artifacts.length)sources.set(p.platform_revision_id,{c,p});
  $('video-source').innerHTML=sources.size?[...sources].map(([id,{c,p}])=>`<option value="${esc(id)}">${esc(c.display_id+' · '+(p.platform==='douyin'?'抖音':'小红书')+' · '+p.title+(c.run_mode==='real'?'':' [演示]'))}</option>`).join(''):'<option value="">还没有完成的图文</option>';
  const cid=new URLSearchParams(location.search).get('content');if(cid){const found=[...sources].find(([,v])=>v.c.id===cid);if(found)$('video-source').value=found[0];}
  if(sources.size)await loadSource();else{$('editor-empty').hidden=false;document.querySelector('.editing-desk').hidden=true;$('status').textContent='先完成一份图文';}
  await poll();
}catch(e){$('video-error').textContent=e.message;$('status').textContent='工作台读取未完成，请刷新重试。';}
if(location.hash==='#speech-settings')$('speech-settings').open=true;
