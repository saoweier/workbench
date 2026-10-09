import {renderDiagnostics} from '/assets/content-diagnostics.js?v=deliverables-20261007';
import {api,navBar,statusBar,esc,guard,toast,time} from '/assets/app.js?v=20261009-lan1';
document.getElementById('nav').innerHTML=navBar('SkillWorkflow');
document.getElementById('status').innerHTML=await statusBar();
const skills=await api.get('/content-skills');
const planningRuleHint=s=>s.id==='planning'?`<p class="hint" data-planning-limits>当前规则：每页要点 ${s.planning_limits?.max_points_per_page??'不限'} · 总页数 ${s.planning_limits?.max_pages??'不限'}。在下方编辑「每页规划要点上限：不限」「规划总页数上限：不限」，也可填正整数。用户指定页数优先。</p>`:'';
// 入口块用固定三问说明「何时用 / 要什么 / 交给谁」，来源是技能自身的 SKILL.md。
const ENTRY_LABELS=[['when','何时使用'],['inputs','需要输入'],['handoff','交给谁']];
const entryBlock=s=>{
 if(!s.entry)return'';
 const rows=ENTRY_LABELS.filter(([k])=>s.entry[k]).map(([k,label])=>`<p class="hint skill-entry-row"><b>${label}</b>：${esc(s.entry[k])}</p>`).join('');
 const missing=(s.entry_missing||[]).length?`<p class="hint skill-entry-warning">入口说明缺 ${esc((s.entry_missing||[]).join('、'))}，请按【何时使用】/【需要输入】/【交给谁】补齐（不补齐则界面不显示该行）。</p>`:'';
 return `<div class="skill-entry" data-entry="${esc(s.id)}">${rows}${missing}</div>`;
};
document.getElementById('capabilities').textContent=`文字模型：${skills.capabilities.text_ready?'已配置':'待配置'} · 图片模型：${skills.capabilities.image_ready?'已配置':'待配置（也可上传配图）'} · 资料搜索：${skills.capabilities.search_ready?'已配置搜索接口':'尚未配置补充搜索服务'}。${skills.capabilities.notice}`;
const cards=items=>items.map((s,i)=>`<section class="skill-card" data-skill="${esc(s.id)}"><span class="skill-number">0${i+1} · ${s.origin==='built_in'?'内置技能':'你的工作要求'}</span><h2>${esc(s.name)}</h2>${entryBlock(s)}${planningRuleHint(s)}<label class="hint" for="skill-${esc(s.id)}">工作要求</label><textarea id="skill-${esc(s.id)}" maxlength="8000">${esc(s.instructions)}</textarea><div class="actions"><small data-current-version="${esc(s.id)}">版本 ${esc(s.version)}</small><button data-save="${esc(s.id)}">保存要求</button><button data-versions="${esc(s.id)}">历史 / 恢复内置</button></div><div data-version-list="${esc(s.id)}"></div><p class="hint" data-message="${esc(s.id)}">保存不会调用模型，不会改变已完成稿件。</p></section>`).join('');
document.getElementById('skills').innerHTML=cards(skills.items.filter(s=>s.group==='stage'));document.getElementById('direction-skills').innerHTML=cards(skills.items.filter(s=>s.group==='direction'));
const saveSkill=guard(async e=>{const b=e.target.closest('[data-save]');if(!b)return;const s=skills.items.find(v=>v.id===b.dataset.save);b.disabled=true;try{const updated=await api.put(`/content-skills/${s.id}`,{instructions:document.getElementById('skill-'+s.id).value,expected_version:s.version});Object.assign(s,updated);if(s.id==='planning')document.querySelector('[data-planning-limits]').outerHTML=planningRuleHint(s);const entryEl=document.querySelector(`[data-entry="${s.id}"]`);if(entryEl)entryEl.outerHTML=entryBlock(s);document.querySelector(`[data-current-version="${s.id}"]`).textContent='版本 '+s.version;document.querySelector(`[data-message="${s.id}"]`).textContent='已保存。新任务会采用版本 '+s.version;toast('技能要求已更新');}finally{b.disabled=false;}});
document.getElementById('skills').onclick=saveSkill;document.getElementById('direction-skills').onclick=saveSkill;
const contents=await api.get('/contents');const select=document.getElementById('trace-content');select.innerHTML=contents.items.map(c=>`<option value="${esc(c.id)}">${esc(c.display_id)} · ${esc(c.topic)}</option>`).join('');
const requested=new URLSearchParams(location.search).get('content');if(requested&&[...select.options].some(o=>o.value===requested))select.value=requested;
async function history(){if(select.value)await renderDiagnostics(document.getElementById('history'),select.value);}
document.getElementById('trace-load').onclick=guard(history);select.onchange=guard(history);await guard(history)();

const versions={};
const versionHandler=guard(async e=>{
 const button=e.target.closest('[data-versions]');if(!button)return;
 const id=button.dataset.versions;const data=await api.get(`/content-skills/${id}/versions`);versions[id]=data.items;
 document.querySelector(`[data-version-list="${id}"]`).innerHTML=`<label>载入版本（保存后才生效）<select data-load-version="${id}"><option value="">选择历史规则</option>${data.items.map((v,i)=>`<option value="${i}">${v.origin==='built_in'?'内置规则':'已保存规则'} · ${esc(v.version)}</option>`).join('')}</select></label><p class="hint">${esc(data.notice)}</p>`;
});
for(const id of ['skills','direction-skills']){
 document.getElementById(id).addEventListener('click',versionHandler);
 document.getElementById(id).addEventListener('change',e=>{const v=e.target;if(!v.matches('[data-load-version]')||v.value==='')return;const id=v.dataset.loadVersion;document.getElementById('skill-'+id).value=versions[id][Number(v.value)].instructions;document.querySelector(`[data-message="${id}"]`).textContent='已载入编辑器，检查后点保存。当前线上规则尚未改变。';});
}
