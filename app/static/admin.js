const $ = id => document.getElementById(id);
const fields = ['retention_days','webdav_enabled','webdav_url','webdav_directory','webdav_username','webdav_password'];
async function api(path, method = 'GET', body) {
  const response = await fetch('/api/admin/' + path, {method, headers: {'Content-Type':'application/json'}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) {$('dashboard').hidden = true; $('login-form').hidden = false;}
    throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请检查填写内容');
  }
  return data;
}
function payload() {const data = {}; for (const key of fields) data[key] = key === 'webdav_enabled' ? $(key).checked : key === 'retention_days' ? Number($(key).value) : key === 'webdav_password' ? $(key).value : $(key).value.trim(); return data;}
function bytes(n) {return `${(n / 1048576).toFixed(1)} MB`;}
async function refreshStatus() {
  try {
    const s = await api('status');
    const cards = [['已收录',s.total+' 张'],['本地原图',s.local_count+' 张'],['WebDAV 原图',s.remote_count+' 张'],['本地原图占用',bytes(s.local_bytes)]];
    $('stats').replaceChildren(...cards.map(([label,value]) => {const e=document.createElement('div'); const p=document.createElement('p');p.textContent=label;const b=document.createElement('strong');b.textContent=value;e.append(p,b);return e;}));
    $('sync').disabled = s.running;
    const job = s.last_job;
    $('job-status').textContent = s.running ? '任务进行中，可以继续浏览本站…' : job ? `上次完成：${new Date(job.finished_at).toLocaleString('zh-CN')} · 新增 ${job.added} 张 · 归档 ${job.archived} 张${job.errors.length ? '。待处理：' + job.errors.join('；') : '。运行正常。'}` : '等待首次采集。';
  } catch (e) { $('job-status').textContent = e.message; }
}
async function dashboard() {
  const cfg = await api('settings');
  for (const key of fields) {if (key === 'webdav_password') continue; if (key === 'webdav_enabled') $(key).checked = cfg[key]; else $(key).value = cfg[key];}
  $('webdav_password').value = '';
  $('webdav_password').placeholder = cfg.password_saved ? '已保存，留空保持不变' : '尚未保存密码';
  $('storage-badge').textContent = cfg.webdav_enabled ? '已启用自动归档' : '尚未启用';
  $('login-form').hidden = true; $('dashboard').hidden = false;
  await refreshStatus();
}
$('login-form').addEventListener('submit', async e => {e.preventDefault();$('login-message').textContent='正在登录…';try {await api('login','POST',{password:$('login-password').value});$('login-password').value='';$('login-message').textContent='';await dashboard();} catch(err){$('login-message').textContent=err.message;}});
$('logout').addEventListener('click', async () => {await api('logout','POST');$('dashboard').hidden=true;$('login-form').hidden=false;});
$('settings-form').addEventListener('submit', async e => {e.preventDefault();$('save-settings').disabled=true;try {await api('settings','PUT',payload());$('save-message').textContent='配置已保存，下次任务将使用新设置。';await dashboard();}catch(err){$('save-message').textContent=err.message;}finally{$('save-settings').disabled=false;}});
$('test-webdav').addEventListener('click', async () => {
  $('test-webdav').disabled=true;$('test-webdav').textContent='正在测试…';$('test-result').hidden=false;$('test-result').textContent='正在检查目录、上传、下载与删除权限…';
  try {const r=await api('webdav/test','POST',payload());$('test-result').textContent=(r.ok?'✓ 测试通过':'× 测试失败')+'\n'+r.steps.join('\n')+(r.error?'\n'+r.error:'');$('test-result').classList.toggle('failed',!r.ok);}catch(e){$('test-result').textContent=e.message;}finally{$('test-webdav').disabled=false;$('test-webdav').textContent='测试连接';}
});
$('sync').addEventListener('click', async () => {try {await api('sync','POST');$('job-status').textContent='采集与归档任务已启动…';$('sync').disabled=true;}catch(e){$('job-status').textContent=e.message;}});
setInterval(() => {if (!$('dashboard').hidden) refreshStatus();},5000);
dashboard().catch(() => {});
