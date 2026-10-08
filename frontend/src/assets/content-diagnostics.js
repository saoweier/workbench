import {api,esc,time} from '/assets/app.js?v=20261005';
const acquisition=r=>r?.search_executed?'已执行资料搜索':r?.search_trace?.some(t=>t.tool==='read_selected_topic'&&t.state==='ok')?'已读取热榜原链接，未执行补充搜索':'未执行资料搜索';
const json=value=>`<pre style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:420px;overflow:auto">${esc(JSON.stringify(value,null,2))}</pre>`;
export async function renderDiagnostics(el,id){
 el.innerHTML='<p>正在读取资料和执行记录…</p>';
 try{
  const data=await api.get(`/contents/${id}/diagnostics`);
  // 交付物清单可能因为还没有版本而不可用；诊断页本身仍要能打开。
  const dv=await api.get(`/contents/${id}/deliverables`).catch(e=>({error:e.message}));
  const deliverables=Array.isArray(dv?.deliverables)&&dv.acceptance?dv:null;
  const deliverableBlock=deliverables
    ?`<h3>本次任务的交付物清单（程序核对）</h3>${deliverables.acceptance.passed?'':'<p class="banner"><b>存在未通过项，先解决再发布。</b></p>'}<div class="panel" style="padding:14px"><table style="width:100%;border-collapse:collapse"><thead><tr><th align="left">交付物</th><th align="left">应有</th><th align="left">实际</th><th align="left">结论</th></tr></thead><tbody>${deliverables.deliverables.map(r=>`<tr><td>${esc(r.label)}</td><td>${esc(String(r.expected))}</td><td>${esc(String(r.actual))}</td><td>${r.passed?'通过':'<b>未通过</b>'}</td></tr>`).join('')}</tbody></table><p class="hint">${esc(deliverables.notice)}</p></div>`
    :`<h3>本次任务的交付物清单</h3><p class="banner">交付物清单暂不可用：${esc(dv?.error||'该内容还没有可交付的版本')}</p>`;
  const issues=data.runs.flatMap(r=>r.issues);const last=data.runs.at(-1);
  el.innerHTML=`<div class="banner" style="margin-bottom:16px"><b>当前稿 ${data.active_revision_version?'V'+data.active_revision_version:'尚未生成'} · 最近任务${last?.state==='succeeded'?'已完成':last?.state==='failed'?'已停止':'处理中'}</b><p>${issues.length?'以下问题包含保留的历史版本，请结合任务时间和版本对照。':'资料与生成记录'}</p>${issues.map(s=>`<p>${esc(s)}</p>`).join('')}<p>最近任务：${acquisition(last)} · 可用正文资料 ${last?.factual_source_count||0} 份。文字模型已配置不代表已经联网搜索。</p></div>
  <div class="actions"><a class="btn" href="/views/SkillWorkflow.html?content=${encodeURIComponent(id)}">维护技能 / 查看实际规则 →</a></div>
  ${deliverableBlock}
  <h3>1. 读到了哪些资料</h3>${data.runs.map(r=>`<details ${r===last?'open':''}><summary>任务 ${esc(r.run_id.slice(0,8))} · ${acquisition(r)} · ${esc(r.search_provider||'已有资料')} · 原文 ${r.factual_source_count} 份</summary>${r.sources.map(s=>`<article class="panel" style="padding:14px;margin:10px 0"><b>${esc(s.id)} · ${esc(s.kind)} · ${esc(s.excerpt_basis)} · ${esc(s.access_state)}</b>${s.url&&/^https?:\/\//.test(s.url)?`<p><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">打开来源原文 ↗</a></p>`:''}<p>${esc(s.limitations||'')}</p><details><summary>查看当时保存的摘录</summary>${json(s.excerpt||'未取得正文')}</details></article>`).join('')||'<p>没有保存的资料。</p>'}${r.search_trace?.length?`<h4>模型检索计划与工具执行记录</h4>${json(r.search_trace)}`:''}${r.access_failures.length?`<h4>读取失败</h4>${json(r.access_failures)}`:''}${r.assessment?`<h4>调研提取的核心事实与逐字引用</h4>${json(r.assessment)}`:''}</details>`).join('')}
  <h3>2. 哪一步开始产生这个说法</h3><p class="hint">按执行时间排序。展开输入与输出，对照规划是否已经写入错误解释。</p>${data.steps.map(s=>`<details class="skill-trace"><summary>${esc(s.skill_name)} · ${esc(s.state)} · ${time(s.created_at)} · ${esc(s.version)}</summary><h4>收到的输入</h4>${json(s.inputs)}<h4>产生的输出</h4>${json(s.output)}</details>`).join('')}
  <h3>3. 当时实际使用的技能规则</h3>${data.runs.map(r=>`<details><summary>任务 ${esc(r.run_id.slice(0,8))} 的冻结规则</summary>${Object.values(r.rule_snapshot).map(s=>`<details><summary>${esc(s.name)} · ${esc(s.version)}</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(s.instructions)}</pre></details>`).join('')||'<p>没有保存完整冻结规则。</p>'}</details>`).join('')}
  <h3>4. 模型请求与原始返回</h3>${data.calls.map(c=>`<details class="skill-trace"><summary>${esc(c.prompt_version||'模型调用')} · ${esc(c.model||'')} · ${esc(c.state)}</summary>${c.input?`<h4>请求内容（执行结果见状态）</h4>${json(c.input)}`:`<p class="hint">${esc(c.input_notice)}</p>`}<h4>模型原始返回</h4>${json(c.response)}</details>`).join('')||'<p>没有模型调用记录。</p>'}<p class="hint">${esc(data.notice)}</p>`;
 }catch(e){el.innerHTML=`<p class="banner">诊断记录读取失败：${esc(e.message)}</p>`;throw e;}
}
