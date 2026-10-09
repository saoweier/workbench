import {api,navBar,esc,guard,toast,uuid} from '/assets/app.js?v=20261009-lan1';
const $=id=>document.getElementById(id);$('nav').innerHTML=navBar('SkillWorkflow');
let packages=[],current=null,versions=[],previewEpoch=0;
const colors={background:'纸面底色',ink:'正文字色',accent:'标题与重点色',soft:'浅色信息区'};
const sizes={heading_size:['标题字号',48,76],body_size:['正文字号',24,34],row_spacing:['行内间距',10,24],radius:['圆角大小',0,32]};
const families={legacy:'历史版式',handdrawn:'手绘便签',magazine:'杂志分栏',neon:'科技线路',collage:'贴纸拼贴',data:'数据档案'};
const clean=p=>{const out=structuredClone(p);delete out.origin;delete out.package_version;return out;};
function list(){
 $('package-list').innerHTML=packages.map(p=>`<button type="button" data-package="${esc(p.id)}" aria-pressed="${current?.id===p.id}"><span class="package-swatch swatch-${esc(p.style.design_family||'legacy')}" style="background:${esc(p.style.background)};color:${esc(p.style.accent)}">${({handdrawn:'✎',magazine:'Aa',neon:'⌘',collage:'✳',data:'▦'})[p.style.design_family]||'文'}</span><span>${esc(p.name)}<small>${esc(families[p.style.design_family||'legacy'])} · ${p.origin==='built_in'?'内置':'我的'}</small></span></button>`).join('');
}
function fill(p){
 $('package-editor').hidden=false;$('editor-title').textContent=p.name;$('current-version').textContent=p.package_version?'版本 '+p.package_version:'新模板 · 保存后可在创作室选择';
 $('template-name').value=p.name;$('template-description').value=p.description;$('template-instructions').value=p.instructions;
 $('style-colors').innerHTML=Object.entries(colors).map(([k,label])=>`<label>${label}<input type="color" data-style="${k}" value="${esc(p.style[k])}"></label>`).join('');
 $('style-sizes').innerHTML=Object.entries(sizes).map(([k,[label,min,max]])=>`<label>${label} <output>${p.style[k]}</output><input type="range" data-style="${k}" min="${min}" max="${max}" value="${p.style[k]}"></label>`).join('');
 $('style-art').innerHTML=`<label>布局风格<select data-style="design_family">${Object.entries(families).map(([id,label])=>`<option value="${id}">${label}</option>`).join('')}</select></label><label>标题字体<select data-style="heading_font"><option value="handwritten">手写海报字</option><option value="system">清晰印刷字</option><option value="serif">杂志衬线字</option><option value="mono">等宽技术字</option><option value="display">粗体展示字</option></select></label><label>图案与框体<select data-style="decoration"><option value="rich">丰富海报元素</option><option value="simple">简化版式</option></select></label>`;
 document.querySelectorAll('#style-art [data-style]').forEach(el=>el.value=p.style[el.dataset.style]);
 $('export-template').hidden=!p.package_version;$('export-template').href='/api/v1/template-packages/'+p.id+'/export';
 $('edit-notice').textContent='保存用于新任务；已有作品和在途任务继续使用其冻结版本。示例预览只检查排版，不调用模型。';
}
function read(){
 const p=clean(current);p.name=$('template-name').value.trim();p.description=$('template-description').value.trim();p.instructions=$('template-instructions').value;
 document.querySelectorAll('[data-style]').forEach(el=>p.style[el.dataset.style]=el.type==='range'?Number(el.value):el.value);return p;
}
async function history(){
 $('template-history').innerHTML='<option value="">选择版本，载入编辑器</option>';versions=[];
 if(!current.package_version)return;
 const id=current.id,data=await api.get('/template-packages/'+id+'/versions');if(id!==current.id)return;versions=data.items;
 $('template-history').innerHTML+=versions.map((p,i)=>`<option value="${i}">${p.origin==='built_in'?'内置':'已保存'} · ${esc(p.package_version)}</option>`).join('');
}
async function preview(){
 const epoch=++previewEpoch;$('preview-template').disabled=true;
 try{
  const r=await fetch('/api/v1/template-packages/preview?mode='+$('preview-mode').value,{method:'POST',headers:{'Content-Type':'application/json','X-CWB-Local-Action':'account-connection'},body:JSON.stringify(read())});
  if(!r.ok){let message='模板预览失败';try{const error=await r.json();message=error.message||error.detail||message;}catch{}throw new Error(typeof message==='string'?message:JSON.stringify(message));}
  const html=await r.text();if(epoch===previewEpoch)$('template-preview').srcdoc=html;
 }finally{if(epoch===previewEpoch)$('preview-template').disabled=false;}
}
async function select(id){current=structuredClone(packages.find(p=>p.id===id));list();fill(current);await Promise.all([preview(),history()]);}
$('package-list').onclick=guard(async e=>{const b=e.target.closest('[data-package]');if(b)await select(b.dataset.package);});
$('template-form').onsubmit=guard(async e=>{
 e.preventDefault();if(!$('template-form').reportValidity())return;$('save-template').disabled=true;
 try{
  const saved=await api.put('/template-packages/'+current.id,{package:read(),expected_version:current.package_version||null});
  const i=packages.findIndex(p=>p.id===saved.id);if(i<0)packages.push(saved);else packages[i]=saved;current=structuredClone(saved);list();fill(current);await history();$('template-message').textContent='已保存。新任务会使用这一套 Skill 和模板，旧稿保留原版。';toast('样式与规则已保存');
 }finally{$('save-template').disabled=false;}
});
$('duplicate-template').onclick=guard(async()=>{
 current={...read(),id:'tpl_'+uuid().replaceAll('-','').slice(0,12),name:($('template-name').value+' · 副本').slice(0,36)};
 list();fill(current);await history();await preview();$('template-name').focus();$('template-message').textContent='已复制到编辑器。改好名字、规则和外观后保存，即可在创作室使用。';
});
$('preview-template').onclick=guard(preview);
$('preview-mode').onchange=guard(preview);
$('template-history').onchange=guard(async e=>{if(e.target.value==='')return;const p=versions[Number(e.target.value)];fill({...p,package_version:current.package_version});await preview();$('edit-notice').textContent='历史版本已载入编辑器；检查后点保存才生效。当前使用中的规则尚未改变。';});
$('style-sizes').oninput=e=>{if(e.target.matches('input'))e.target.previousElementSibling.value=e.target.value;};
new ResizeObserver(entries=>{const width=entries[0].contentRect.width;const host=document.querySelector('.preview-window');host.style.setProperty('--preview-scale',String(width/1080));host.style.height=(width*4/3)+'px';}).observe(document.querySelector('.preview-window'));
await guard(async()=>{const data=await api.get('/template-packages');packages=data.items;list();if(packages.length)await select(packages.find(p=>p.id==='friendly_guide')?.id||packages[0].id);})();
