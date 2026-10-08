import {api,navBar} from './app.js?v=20261005';
document.getElementById('nav').innerHTML=navBar('PublishingHub');
const $=id=>document.getElementById(id);let prefs={mode:'manual',background_revisit:false},busy=false;
async function save(mode,go){if(busy)return;busy=true;$('preference-error').textContent='';try{const r=await fetch('/api/v1/publishing/preferences',{method:'POST',headers:{'Content-Type':'application/json','X-CWB-Local-Action':'account-connection'},body:JSON.stringify({mode,background_revisit:$('background-revisit').checked})});const data=await r.json();if(!r.ok)throw Error(data.detail||data.error?.message||'保存未完成');prefs=data;show();if(go)location.href=go;}catch(e){$('preference-error').textContent=e.message;}finally{busy=false;}}
function show(){$('background-revisit').checked=prefs.background_revisit;$('status').textContent=`当前方式：${prefs.mode==='manual'?'手动发布':'浏览器辅助，逐条确认'} · 后台回访${prefs.background_revisit?'已开启':'已关闭'}`;}
$('choose-manual').onclick=()=>save('manual','/views/ManualPublishing.html');$('choose-assisted').onclick=()=>save('assisted','/views/DouyinPublishing.html');$('save-revisit').onclick=()=>save(prefs.mode);
try{prefs=await api.get('/publishing/preferences');show();}catch(e){$('preference-error').textContent=e.message;}
