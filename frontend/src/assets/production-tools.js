import {api,esc,guard,toast,log} from '/assets/app.js?v=20261009-lan2';
export async function setupTools({refresh}) {
let BATCH=null, SEED_PATH=null;
const produce = guard(async () => {
  const btn = document.getElementById("btn-seed");
  btn.disabled = true;
  try {
  document.getElementById("progress").innerHTML =
    `<div class="busy"><span class="spin"></span>正在跑：研究 → 选题 → 双平台改写 → 渲染…（约 20–60 秒）</div>`;
  log("开始一键出成品（离线基线 local_seed，零真实调用）…");

  const r = await api.post("/contents/produce-from-seed", {seed_path: SEED_PATH});
  const stages = r.stages || {};
  log("阶段结果：" + Object.entries(stages)
    .map(([k, v]) => `${v?.ok ? "✓" : "✗"} ${k}`).join("  "),
    Object.values(stages).every(v => v?.ok) ? "ok" : "err");

  if (r.blocked_stage) {
    const why = stages[r.blocked_stage]?.reason || "（无说明）";
    log(`阻塞在 ${r.blocked_stage}：${why}`, "err");
    document.getElementById("progress").innerHTML =
      `<div class="banner err"><strong>未走完：卡在「${esc(r.blocked_stage)}」</strong>
        <div class="hint" style="margin:5px 0 0">${esc(why)}</div></div>`;
  } else {
    const rc = r.real_calls_recorded ?? 0;
    document.getElementById("progress").innerHTML = `
      <div class="banner ${r.partial ? "err" : "ok"}">
        <strong>${r.partial ? "未全部完成" : "已完成"}</strong> · run
        <code>${esc(String(r.run_id || "").slice(0, 8))}</code> · 真实调用 <b>${rc}</b> 笔${
          rc === 0 ? "（本次零费用）" : ""}
        ${r.offline_baseline ? ' · <span class="chip acc">离线基线</span>' : ""}
        <div class="hint" style="margin:5px 0 0">${esc(r.notice || "")}
          ${r.content_id
            ? ` <a href="/views/ReviewPreview.html">去看成品并批准</a>` : ""}</div>
      </div>`;
    toast("成品已生成。", "ok");
  }
  await refresh();
  } finally { btn.disabled = false; }
});

document.getElementById("btn-seed").onclick = produce;


document.getElementById("btn-attention").onclick = () => location.href = "/views/Attention.html";

document.getElementById("btn-batch").onclick = guard(async () => {
  const msg = document.getElementById("batch-msg");
  msg.textContent = "";
  const budget = document.getElementById("f-budget").value;
  const r = await api.post("/batches", {
    profile_version_id: null,
    item_limit: 1,
    cost_mode: "usage_tracking",
    budget_limit_micro: budget ? Number(budget) : null,
    currency: "CNY",
  });
  BATCH = r.id || r.batch_id || r.batch?.id;
  msg.innerHTML = `批次已建：<code>${esc(BATCH || "")}</code>
    · 金额上限 ${budget ? budget : '<span class="missing">null（未设置，≠ 0 额度）</span>'}`;
  log(`批次已建立 ${BATCH}。金额上限${budget ? "已设" : "未设（null ≠ 0）"}。`, "ok");
  document.getElementById("btn-enqueue").disabled = false;
  document.getElementById("batch-out").innerHTML =
    `<pre>${esc(JSON.stringify(r, null, 2))}</pre>`;
});

document.getElementById("btn-enqueue").onclick = guard(async () => {
  const msg = document.getElementById("batch-msg");
  if (!BATCH) { msg.innerHTML = '<span class="missing">先建批次。</span>'; return; }
  const topic = document.getElementById("f-topic").value.trim();
  if (!topic) { msg.innerHTML = '<span class="missing">填写选题。</span>'; return; }
  const r = await api.post(`/batches/${BATCH}/runs`, {
    topic,
    run_mode: document.getElementById("f-mode").value,
    seed_path: document.getElementById("f-seed").value.trim() || (document.getElementById("f-materials").value.trim() ? null : SEED_PATH),
    platforms: document.getElementById("f-platforms").value.split(",").map(s => s.trim()).filter(Boolean),
    render: true,
    user_materials: document.getElementById("f-materials").value.trim() ? [{text:document.getElementById("f-materials").value.trim(), kind:"user_provided"}] : null,
  });
  log(`已排队：job ${esc(String(r.queued_job_id || r.job_id || "").slice(0, 8))}，`
    + `Worker ${r.worker_running ? "在运行" : "未运行"}。`, r.worker_running ? "ok" : "err");
  if (r.note) log(r.note, r.worker_running ? "" : "err");
  document.getElementById("batch-out").innerHTML =
    `<pre>${esc(JSON.stringify(r, null, 2))}</pre>`;
  await refresh();
  document.getElementById("btn-enqueue").disabled = true;
  toast(r.worker_running ? "已排队，Worker 会领取。" : "已排队，但 Worker 未运行，不会有人执行。",
    r.worker_running ? "ok" : "err");
});


const seeds=await api.get('/seeds');
const sel=document.getElementById('sel-seed');
sel.innerHTML='<option value="">默认项目素材</option>'+(seeds.seeds||[]).map(s=>`<option value="${esc(s.path)}">${esc(s.display_id)} · ${esc(s.path)}</option>`).join('');
SEED_PATH=seeds.seeds?.[0]?.path||null; sel.onchange=()=>{SEED_PATH=sel.value||null;};
const h=await api.get('/health').catch(()=>null); document.getElementById('mode-note').textContent=h?.worker?.status==='running'?' 后台任务运行中。':' 后台任务未连接，请检查启动状态。';
}
