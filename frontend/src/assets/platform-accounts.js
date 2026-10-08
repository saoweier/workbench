import {api, navBar} from '/assets/app.js?v=20261005';

document.getElementById('nav').innerHTML = navBar('PlatformAccounts');
const byId = id => document.getElementById(id);
const titles = {not_connected:'还未连接抖音账号', opening:'正在打开连接窗口', waiting_login:'等待你扫码登录',
  connected:'已检测到后台登录', needs_attention:'需要你在窗口中处理', unverified:'正在确认登录状态',
  saved:'登录状态已保留，使用前需要重新检查', disconnected:'连接窗口已关闭', error:'连接窗口未能正常运行'};
let current = null;
let busy = false;
let timer = null;
let stopped = false;
function render(data) {
  current = data;
  byId('connection-title').textContent = titles[data.state] || '暂未确认连接状态';
  byId('connection-message').textContent = data.message || '勾选本机保存说明后，点击按钮打开抖音官方登录窗口。';
  controls();
}
function controls() {
  byId('connect-douyin').disabled = busy || !current || !byId('local-consent').checked || current.browser_open;
  byId('connect-douyin').textContent = current?.browser_open ? '连接窗口已打开' : '打开抖音，扫码连接';
  byId('check-douyin').disabled = busy;
  byId('check-douyin').textContent = current?.browser_open ? '我已登录，检查状态' : '刷新连接状态';
  byId('close-douyin').disabled = busy || !current?.browser_open;
}
function schedule() {
  clearTimeout(timer);
  if (!stopped && current?.browser_open && !document.hidden) timer = setTimeout(refresh, 3000);
}
async function refresh() {
  if (busy || stopped) return;
  busy = true;
  controls();
  try {
    render(await api.get('/accounts/douyin'));
    byId('connection-error').textContent = '';
  } catch (error) {
    byId('connection-error').textContent = `读取连接状态失败：${error.message}。返回此页可重新读取。`;
  } finally { busy = false; controls(); schedule(); }
}
async function action(name) {
  if (busy) return;
  busy = true;
  clearTimeout(timer);
  controls();
  byId('connection-error').textContent = '';
  try {
    const response = await fetch(`/api/v1/accounts/douyin/${name}`, {method:'POST', headers:{
      'Content-Type':'application/json', 'X-CWB-Local-Action':'account-connection'}, body:'{}'});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '操作失败，请重试。');
    render(data);
  } catch(error) {
    byId('connection-error').textContent = error.message;
  } finally {
    busy = false;
    controls();
    schedule();
  }
}
byId('local-consent').addEventListener('change', controls);
byId('connect-douyin').addEventListener('click', () => action('connect'));
byId('check-douyin').addEventListener('click', () => current?.browser_open ? action('check') : refresh());
byId('close-douyin').addEventListener('click', () => action('close'));
document.addEventListener('visibilitychange', () => {
  clearTimeout(timer);
  if (!document.hidden) refresh();
});
window.addEventListener('pagehide', () => { stopped = true; clearTimeout(timer); });
window.addEventListener('pageshow', () => { if(stopped) { stopped = false; refresh(); } });
refresh();
