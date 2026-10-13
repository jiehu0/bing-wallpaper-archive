const {JSDOM} = require('jsdom');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.join(__dirname, '../app/static');
const tick = () => new Promise(resolve => setTimeout(resolve, 20));
const wallpaper = {date:'2026-10-11',title:'海岸 <script>bad()</script>',copyright:'摄影师版权',copyright_link:'https://www.bing.com/search?q=test',width:3840,height:2160,thumbnail:'/media/2026-10-11/thumb',preview:'/media/2026-10-11/preview',original:'/media/2026-10-11/original'};
async function gallery(query='') {
  const dom = new JSDOM(fs.readFileSync(path.join(root,'index.html'),'utf8'), {url:'https://wallpaper.boyweb.net/'+query,runScripts:'outside-only'});
  const w = dom.window, requests=[];
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  w.fetch=async url=>{requests.push(String(url));return {ok:true,json:async()=>({items:[wallpaper],latest:wallpaper,total:36,archive_count:36,years:['2026','2024']})};};
  w.eval(fs.readFileSync(path.join(root,'gallery.js'),'utf8'));await tick();
  const field=id=>w.document.getElementById(id);
  const params=()=>new URL(requests.at(-1),'https://wallpaper.boyweb.net').searchParams;
  const select=async(id,value)=>{field(id).value=value;field(id).dispatchEvent(new w.Event('change'));await tick();};
  if(query) {
    assert.equal(field('year').value,'2024');
    assert.equal(field('month').value,'02');
    assert.equal(params().get('year'),'2024');
    assert.equal(params().get('month'),'02');
    assert.equal(field('day').value,query.includes('day=')?'29':'');
    dom.window.close();return;
  }
  assert.equal(w.document.querySelectorAll('.card').length,1);
  assert.equal(w.document.getElementById('hero').hidden,false);
  assert.equal(w.document.querySelector('.card h3').textContent,wallpaper.title);
  assert.equal(w.document.querySelectorAll('.card script').length,0);
  w.document.querySelector('.card').click();
  assert.equal(w.document.getElementById('detail').open,true);
  assert.ok(w.document.getElementById('download').href.endsWith('/original?download=true'));
  w.document.getElementById('close-dialog').click();assert.equal(w.document.getElementById('detail').open,false);
  w.document.getElementById('toggle-api').click();assert.equal(w.document.getElementById('api-dialog').open,true);
  let copied=null;Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async t=>{copied=t;}},configurable:true});
  const copyButtons=w.document.querySelectorAll('.api-copy');
  assert.equal(copyButtons.length,6);
  assert.equal(copyButtons[2].querySelector('code').textContent,'https://wallpaper.boyweb.net/img/4k');
  copyButtons[2].click();await tick();
  assert.equal(copied,'https://wallpaper.boyweb.net/img/4k');
  assert.equal(copyButtons[2].querySelector('.sr-only').textContent,'已复制');assert.ok(copyButtons[2].classList.contains('copied'));
  assert.ok(w.document.querySelector('.api-toast').classList.contains('show'));
  copyButtons[5].click();await tick();
  assert.equal(copied,'<img src="https://wallpaper.boyweb.net/img/" />');
  w.document.getElementById('close-api').click();assert.equal(w.document.getElementById('api-dialog').open,false);
  w.document.getElementById('search').value='海岸';
  w.document.getElementById('filters').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
  assert.ok(requests.at(-1).includes(encodeURIComponent('海岸')));
  w.HTMLElement.prototype.scrollIntoView=function(){};
  field('next').click();await tick();assert.equal(params().get('page'),'2');
  await select('year','2024');assert.equal(params().get('page'),'1');
  await select('month','02');
  assert.equal(field('day').options.length,30,'Leap February includes 29 days and a placeholder');
  await select('day','29');
  assert.equal(params().get('year'),'2024');assert.equal(params().get('month'),'02');assert.equal(params().get('day'),'29');
  assert.equal(params().get('q'),'海岸');
  await select('year','2026');
  assert.equal(field('day').options.length,29);assert.equal(params().get('day'),'','Invalid day clears after changing year');
  await select('month','01');await select('day','31');await select('month','04');
  assert.equal(params().get('day'),'');assert.equal(field('day').options.length,31);
  await select('year','');assert.equal(params().get('month'),'04','Month can filter across years');
  await select('month','');await select('day','12');assert.equal(params().get('day'),'12','Day can filter across months');
  field('reset').click();await tick();
  for(const id of ['q','year','month','day'])assert.equal(params().get(id),'');
  assert.equal(field('day').options.length,32);
  dom.window.close();
}
async function admin() {
  const dom = new JSDOM(fs.readFileSync(path.join(root,'admin.html'),'utf8'),{url:'https://wallpaper.boyweb.net/admin/',runScripts:'outside-only'});
  const w=dom.window;let testBody=null,saveBody=null;
  w.fetch=async(url,opts)=>{
    let data={};
    if(url.endsWith('/settings')) {
      if(opts.method==='PUT')saveBody=JSON.parse(opts.body);
      data={storage_limit_gb:40,webdav_enabled:false,webdav_url:'',webdav_directory:'bing-wallpaper',webdav_username:'',password_saved:true};
    } else if(url.endsWith('/status'))data={total:8,local_count:8,remote_count:0,local_bytes:1000,storage_bytes:2000,storage_limit_bytes:40e9,running:false,last_job:null};
    else if(url.endsWith('/webdav/test')){testBody=JSON.parse(opts.body);data={ok:true,steps:['目录访问正常','上传成功','下载与内容校验通过','临时文件已删除']};}
    return {ok:true,status:200,json:async()=>data};
  };
  w.eval(fs.readFileSync(path.join(root,'admin.js'),'utf8'));await tick();
  assert.equal(w.document.getElementById('dashboard').hidden,false);
  w.document.getElementById('webdav_url').value='https://dav.example/dav/';
  w.document.getElementById('webdav_password').value=' secret with spaces ';
  w.document.getElementById('test-webdav').click();await tick();
  assert.equal(testBody.webdav_password,' secret with spaces ');
  assert.equal(testBody.storage_limit_gb,40);
  assert.ok(w.document.getElementById('test-result').textContent.includes('测试通过'));
  assert.ok(w.document.getElementById('test-result').textContent.includes('临时文件已删除'));
  assert.equal(saveBody,null,'Test button must not save settings');
  w.document.getElementById('settings-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
  assert.equal(saveBody.webdav_url,'https://dav.example/dav/');
  assert.equal(w.document.getElementById('webdav_password').value,'');
  dom.window.close();
}
(async()=>{await gallery();await gallery('?q=海岸&month=2024-02');await gallery('?year=2024&month=02&day=29');await admin();console.log('Frontend DOM checks passed: gallery, date filters, legacy links, preview, download, WebDAV test, save, secret clearing.');})().catch(e=>{console.error(e);process.exit(1);});
