/* 内容工作台 · 共享前端层（无构建，原生 ES 模块）
 *
 * 这个文件只做三件事：
 *   1. 调后端 API 并**如实暴露错误**（不吞异常、不假装成功）
 *   2. 提供几个渲染小工具（null 与 0 视觉上必须不同）
 *   3. 统一的页面外壳（导航 + 状态条）
 *
 * 设计约束：不使用任何框架与打包器。原生 HTML/CSS/JS 直接跑，
 * 这样启动脚本不需要 node/npm，用户拿到就能开。
 */

export const API = "/api/v1";

/* ---------------------------------------------------------------- 请求 */

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details || {};
  }
  toString() {
    return `[${this.status} ${this.code}] ${this.message}`;
  }
}

async function handle(res) {
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { /* 非 JSON 原样返回 */ }
  if (res.ok) return body;

  // 后端的错误有两种包装：{"error":{...}} 与 FastAPI 的 {"detail":...}
  const err = body?.error || (body?.detail && typeof body.detail === "object" ? body.detail : null);
  if (err) {
    throw new ApiError(res.status, err.code || "ERROR", err.message || text, err.details);
  }
  throw new ApiError(res.status, "HTTP_" + res.status,
    typeof body?.detail === "string" ? body.detail : (text || res.statusText));
}

let pendingRequests=0,networkTimer=null;
async function request(path,options){
  pendingRequests++;
  if(pendingRequests===1)networkTimer=setTimeout(()=>document.body.classList.add('network-busy'),180);
  try{return await handle(await fetch(API+path,options));}
  finally{pendingRequests--;if(!pendingRequests){clearTimeout(networkTimer);document.body.classList.remove('network-busy');}}
}
export const api = {
  async get(path) {
    return request(path,{headers:{Accept:"application/json"}});
  },
  async post(path, body) {
    return request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-CWB-Local-Action":"account-connection" },
      body: JSON.stringify(body ?? {}),
    });
  },
  async del(path) {
    return request(path, { method: "DELETE",headers:{"X-CWB-Local-Action":"account-connection"} });
  },
  async patch(path, body) {
    return request(path, { method: "PATCH",
      headers: { "Content-Type": "application/json","X-CWB-Local-Action":"account-connection" }, body: JSON.stringify(body) });
  },
  async put(path, body) {
    return request(path, { method: 'PUT',headers: {'Content-Type':'application/json','X-CWB-Local-Action':'account-connection'},body:JSON.stringify(body) });
  },
};

/* ---------------------------------------------------------------- 渲染工具 */

/** null / undefined 一律显示「未设置」，**绝不渲染成 0**。 */
export function orNone(v, label = "未设置") {
  return (v === null || v === undefined || v === "") ? label : String(v);
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

export function num(v) {
  if (v === null || v === undefined) return '<span class="missing">缺失</span>';
  return Number(v).toLocaleString("zh-CN");
}

export function money(micro) {
  if (micro === null || micro === undefined) return '<span class="missing">未知</span>';
  return "¥" + (micro / 1e6).toFixed(2);
}

export function bytes(n) {
  if (n === null || n === undefined) return "未知";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0, v = Number(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
}

export function time(iso) {
  if (!iso) return '<span class="missing">未记录</span>';
  const d = new Date(iso);
  return isNaN(d) ? esc(iso) : d.toLocaleString("zh-CN", { hour12: false });
}

export const MODE_LABEL = { real: "真实调用", fixture: "契约演练", local_seed: "项目资料" };

export function modeChip(m) {
  return `<span class="mode ${esc(m)}">${MODE_LABEL[m] || esc(m)}</span>`;
}

/** 运行状态 → 中文标签 + 配色 */
const STATE_MAP = {
  drafted: ["", "已起稿"], researching: ["", "调研中"], selected: ["", "已选题"],
  producing: ["", "生产中"], ready_for_review: ["warn", "待预览"], approved: ["ok", "已批准"],
  partially_approved: ["warn", "部分批准"], rejected: ["err", "已退回"],
  paused: ["warn", "已暂缓"], drafting: ["", "起草中"], blocked: ["err", "已阻塞"],
  queued: ["", "排队中"], running: ["acc", "运行中"], succeeded: ["ok", "成功"],
  failed: ["err", "失败"], unknown: ["warn", "结果未知"], checking: ["", "检查中"],
  imported: ["ok", "已导入"], parsed: ["", "已解析"], declared: ["warn", "已登记未核验"],
  verified: ["ok", "已核验"], withdrawn: ["", "已撤下"],
  proposed: ["warn", "待确认"], accepted: ["ok", "已采用"], reverted: ["", "已回退"],
  superseded: ["", "已被替代"], confirmed: ["ok", "已确认"], mismatch: ["warn", "不一致"],
  inaccessible: ["warn", "打不开"], comparable: ["ok", "可比"], baseline_only: ["warn", "仅基线"],
  insufficient: ["warn", "数据不足"], none: ["err", "无数据"],
  exported: ["ok", "已导出"], cancelled: ["", "已取消"],
  changes_requested: ["warn", "需修改"], on_hold: ["warn", "已暂缓"],
  discarded: ["", "已丢弃"],
};

export function stateChip(s) {
  const [cls, label] = STATE_MAP[s] || ["", s || "未知"];
  return `<span class="chip ${cls}">${esc(label)}</span>`;
}

export function toast(msg, kind = "info") {
  let box = document.getElementById("cwb-toast");
  if (!box) {
    box = document.createElement("div");
    box.id = "cwb-toast";
    box.className = "toast-box";
    box.setAttribute("role","status");box.setAttribute("aria-live","polite");
    document.body.appendChild(box);
  }
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), kind === "err" ? 8000 : 4000);
}

/** 页面日志区（右下角），方便看见每一步真的发生了什么。 */
export function log(msg, kind = "") {
  const el = document.getElementById("log");
  if (!el) return;
  const t = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const line = document.createElement("div");
  if (kind) line.className = kind;
  line.textContent = `[${t}] ${msg}`;
  el.prepend(line);
}

/** 把 async 动作包起来：失败时**明确报错**，不让界面静默不动。 */
export function guard(fn) {
  return async (...args) => {
    try {
      return await fn(...args);
    } catch (e) {
      const msg = e instanceof ApiError ? e.toString() : String(e);
      toast(msg, "err");
      log(msg, "err");
      console.error(e);
    }
  };
}

/* ---------------------------------------------------------------- 页面外壳 */

export const NAV = [
  { href: "/", label: "工作台", file: "index" },
  { href: "/views/Production.html", label: "创作室", file: "Production" },
  { href: "/views/ReviewPreview.html", label: "成品预览", file: "ReviewPreview" },
  { href: "/views/VideoStudio.html", label: "图文转视频", file: "VideoStudio" },
  { href: "/views/SkillWorkflow.html", label: "技能管理与诊断", file: "SkillWorkflow" },
  { href: "/views/PublishingHub.html", label: "发布与回访", file: "PublishingHub" },
  { href: "/views/ManualPublishing.html", label: "手动发布与回访", file: "ManualPublishing" },
  { href: "/views/Publications.html", label: "发布记录", file: "Publications" },
  { href: "/views/PlatformAccounts.html", label: "平台账号", file: "PlatformAccounts" },
  { href: "/views/DouyinPublishing.html", label: "浏览器辅助发布", file: "DouyinPublishing" },
  { href: "/views/DataImport.html", label: "数据导入", file: "DataImport" },
  { href: "/views/ReviewInsights.html", label: "评论与复盘", file: "ReviewInsights" },
  { href: "/views/FeedbackLoop.html", label: "反馈下一轮", file: "FeedbackLoop" },
  { href: "/views/Attention.html", label: "集中处理", file: "Attention" },
  { href: "/views/UsageCosts.html", label: "用量与消耗", file: "UsageCosts" },
  { href: "/views/ApiSettings.html", label: "API 设置", file: "ApiSettings" },
  { href: "/views/QueryLab.html", label: "资料查询器", file: "QueryLab" },
];

export function navBar(current) {
  queueMicrotask(() => mountShell(current));
  const group = (label, files) => `${label?`<div class="nav-section-label">${label}</div>`:""}<div class="nav-group">` +
    NAV.filter(n => files.includes(n.file)).map(n =>
      `<a href="${n.href}" class="${n.file === current ? "on" : ""}" ${n.file === current ? 'aria-current="page"' : ''}>
        ${icon(n.file)}<span>${n.label}</span></a>`).join("") + `</div>`;
  return `<div class="brand"><span class="brand-mark">${icon('brand')}</span><span>内容工作台</span></div>
    <button class="nav-close" aria-label="关闭导航">${icon('close')}</button>
    ${group('创作', ['index','Production','ReviewPreview','VideoStudio'])}
    ${group('发布与反馈', ['PublishingHub','ReviewInsights','FeedbackLoop'])}
    <details class="nav-extra" ${['ManualPublishing','Publications','PlatformAccounts','DouyinPublishing','DataImport'].includes(current)?'open':''}><summary>发布工具与记录</summary>${group('', ['ManualPublishing','PlatformAccounts','DouyinPublishing','Publications','DataImport'])}</details>
    ${group('工作区', ['QueryLab','ApiSettings','Attention'])}
    <details class="nav-extra" ${['UsageCosts','SkillWorkflow'].includes(current)?'open':''}><summary>技能与用量</summary>${group('', ['SkillWorkflow','UsageCosts'])}</details>
    <div class="nav-footer"><div class="local-note">本地工作区 · v1.4.0</div>
      <div class="workspace-person"><span class="avatar">创</span><div><strong>我的创作空间</strong><small>让好内容，持续发生</small></div></div>
    </div>`;
}

/** Small consistent line icons, rendered locally without a dependency or network. */
export function icon(name, extraClass = '') {
  const paths = {
    brand:'<path d="M5 17 18 4l-3 16-5-6-6-3L18 4M10 14l1 6"/>',
    index:'<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    Production:'<path d="m15 4 5 5L9 20l-6 1 1-6L15 4ZM13 6l5 5M14 20h7"/>',
    ReviewPreview:'<rect x="3" y="3" width="18" height="18" rx="3"/><path d="m4 16 5-5 4 4 3-3 5 5"/><circle cx="16" cy="8" r="1.5"/>',
    Publications:'<path d="m21 3-7 18-4-7-7-4L21 3ZM10 14 21 3"/>',
    PublishingHub:'<path d="m21 3-7 18-4-7-7-4L21 3ZM10 14 21 3"/>',
    ManualPublishing:'<path d="M4 20h16M7 16l2-6 8-8 4 4-8 8-6 2ZM15 4l4 4"/>',
    VideoStudio:'<rect x="3" y="4" width="18" height="16" rx="3"/><path d="m10 8 6 4-6 4V8Z"/>',
    PlatformAccounts:'<rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="2"/><path d="M5 17c0-4 8-4 8 0m3-8h3m-3 4h3"/>',
    DouyinPublishing:'<path d="m21 3-7 18-4-7-7-4L21 3ZM10 14 21 3"/><path d="M4 20h4"/>',
    DataImport:'<path d="M12 3v12m-4-4 4 4 4-4M4 15v5h16v-5"/>',
    ReviewInsights:'<path d="M4 20h17M7 16v-5m5 5V5m5 11V8"/>',
    FeedbackLoop:'<path d="M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2"/>',
    Attention:'<rect x="4" y="5" width="16" height="16" rx="3"/><path d="M9 3h6v4H9zM8 13l2 2 5-5M14 17h3"/>',
    UsageCosts:'<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
    ApiSettings:'<path d="M10 3h4l1 3 3 1 3 3-2 3v4l-3 1-2 3h-4l-1-3-3-1-3-3 2-3V7l3-1 2-3Z"/><circle cx="12" cy="12" r="3"/>',
    search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
    QueryLab:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
    plus:'<path d="M12 5v14M5 12h14"/>',
    arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
    menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
    close:'<path d="m6 6 12 12M6 18 18 6"/>',
    sparkle:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3ZM20 2v4m-2-2h4"/>',
    chevron:'<path d="m9 5 7 7-7 7"/>',
  };
  return `<svg class="icon ${extraClass}" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.index}</svg>`;
}

function mountShell(current) {
  if (document.querySelector('.app-topbar')) return;
  const wrap = document.querySelector('body > .wrap');
  const nav = document.getElementById('nav');
  if (!wrap || !nav) return;
  nav.setAttribute('aria-label','主导航');
  const title = current === 'UserGuide' ? '使用教程' : NAV.find(n => n.file === current)?.label || '工作台';
  const header = document.createElement('header');
  header.className = 'app-topbar';
  header.innerHTML = `<button class="menu-toggle" aria-label="展开导航" aria-expanded="false" aria-controls="nav">${icon('menu')}</button>
    <div class="breadcrumb">我的空间 ${icon('chevron')} <strong>${esc(title)}</strong></div>
    <div class="app-search">${icon('search')}<input id="workspace-search" aria-label="搜索页面或内容" placeholder="搜索页面或内容…" autocomplete="off" aria-controls="workspace-results" aria-expanded="false"/>
      <kbd>/</kbd><div class="search-results" id="workspace-results" hidden></div></div>
    <a class="topbar-create" href="/views/Production.html">${icon('plus')}<span>开始创作</span></a>`;
  wrap.prepend(header);
  const network=document.createElement("div");network.className="network-indicator";network.setAttribute("aria-hidden","true");document.body.append(network);
  const tutorial=document.createElement('button');tutorial.className='quiet-button tutorial-entry';tutorial.dataset.startGuide='';tutorial.textContent='使用教程';header.append(tutorial);
  import("/assets/guidance.js?v=20261006").then(m=>m.setupGuidance()).catch(e=>console.warn("操作指引暂时不可用",e));
  const scrim = document.createElement('div');
  scrim.className = 'nav-scrim';
  scrim.hidden = true;
  document.body.appendChild(scrim);
  const menu = header.querySelector('.menu-toggle');
  const mobile = matchMedia('(max-width:768px)');
  function toggle(open) {
    const enabled = open && mobile.matches;
    document.body.classList.toggle('nav-open',enabled);
    scrim.hidden = !enabled;
    menu.setAttribute('aria-expanded', String(enabled));
    nav.inert = mobile.matches && !enabled;
    if (enabled) nav.querySelector('a[aria-current]')?.focus();
  }
  toggle(false);
  menu.onclick = () => toggle(!document.body.classList.contains('nav-open'));
  function closeNav() { toggle(false); menu.focus(); }
  nav.querySelector('.nav-close').onclick = closeNav;
  scrim.onclick = closeNav;
  mobile.addEventListener('change', () => toggle(false));
  nav.addEventListener('keydown', event => {
    if (!mobile.matches || !document.body.classList.contains('nav-open') || event.key !== 'Tab') return;
    const links = [...nav.querySelectorAll('a,button,summary')].filter(el=>el.getClientRects().length);
    const first = links[0], last = links[links.length-1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  const input = header.querySelector('input');
  const results = header.querySelector('.search-results');
  let timer, sequence = 0;
  function dismiss() { results.hidden = true; input.setAttribute('aria-expanded','false'); }
  async function search() {
    const query = input.value.trim().toLowerCase();
    const request = ++sequence;
    if (!query) { dismiss(); return; }
    results.hidden = false;
    input.setAttribute('aria-expanded','true');
    results.innerHTML = '<p>正在查找…</p>';
    const pages = NAV.filter(n => n.label.toLowerCase().includes(query));
    let data;
    try { data = await api.get('/contents'); } catch { data = { items:[] }; }
    if (request !== sequence || query !== input.value.trim().toLowerCase()) return;
    const contents = (data.items || []).filter(c => `${c.topic} ${c.display_id}`.toLowerCase().includes(query)).slice(0,6);
    results.innerHTML = pages.map(n => `<a href="${n.href}">${esc(n.label)}<small>工作区页面</small></a>`).join('') +
      contents.map(c => `<a href="/views/ReviewPreview.html?content=${encodeURIComponent(c.id)}">${esc(c.topic || c.display_id)}<small>${esc(c.display_id)} · 成品预览</small></a>`).join('') || '<p>没有找到匹配内容</p>';
  }
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(search,180); });
  input.addEventListener('focus', () => { if (input.value.trim()) search(); });
  input.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown') { event.preventDefault(); results.querySelector('a')?.focus(); }
    if (event.key === 'Enter') results.querySelector('a')?.click();
  });
  document.addEventListener('click', event => { if (!header.querySelector('.app-search').contains(event.target)) { sequence++; dismiss(); } });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') { dismiss(); if (document.body.classList.contains('nav-open')) closeNav(); }
    if (event.key === '/' && !event.ctrlKey && !event.metaKey && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)) {
      event.preventDefault(); input.focus();
    }
  });
}

/** 顶部状态条：API / Worker / 真实调用数。让"没连上"一眼可见。 */
export async function statusBar() {
  try {
    const h = await api.get("/health");
    const w = h.worker || {};
    const wcls = { running: "ok", stale: "warn", not_running: "err" }[w.status] || "";
    const wlabel = { running: "运行中", stale: "心跳过期", not_running: "未运行" }[w.status] || w.status;
    return `<div class="statusbar">
      <span class="chip ok">API 正常 v${esc(h.api.version)}</span>
      <span class="chip ${wcls}">后台任务 ${wlabel}</span>
      <span class="chip ${h.money_limit_set ? "ok" : ""}">${
        h.money_limit_set ? "已设金额上限" : "未设金额上限（非零额度）"}</span>
      <span class="chip">${esc({usage_tracking:'记录用量',hard_cap:'费用限额'}[h.cost_mode] || h.cost_mode)}</span>
    </div>`;
  } catch (e) {
    return `<div class="statusbar">
      <span class="chip err">后端未连接</span>
      <span style="color:var(--muted);font-size:12px">${esc(String(e))}</span>
    </div>`;
  }
}

/** 导航里标记哪些页面还是"契约演示"（未接后端）。 */
export const LIVE_PAGES = new Set([
  "index", "Production", "ReviewPreview", "Publications", "VideoStudio", "PublishingHub", "ManualPublishing",
  "DataImport", "ReviewInsights", "FeedbackLoop", "Attention", "UsageCosts",
]);
