const {JSDOM} = require('jsdom');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.join(__dirname, '../app/static');
const tick = () => new Promise(resolve => setTimeout(resolve, 20));
const wallpaper = {date:'2026-10-11',title:'海岸 <script>bad()</script>',copyright:'摄影师版权',copyright_link:'https://www.bing.com/search?q=test',width:3840,height:2160,thumbnail:'/media/2026-10-11/thumb',preview:'/media/2026-10-11/preview',original:'/media/2026-10-11/original'};
async function gallery() {
  const dom = new JSDOM(fs.readFileSync(path.join(root,'index.html'),'utf8'), {url:'https://wallpaper.boyweb.net',runScripts:'outside-only'});
  const w = dom.window, requests=[];
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  w.fetch=async url=>{requests.push(String(url));return {ok:true,json:async()=>({items:[wallpaper],latest:wallpaper,total:1,archive_count:1})};};
  w.eval(fs.readFileSync(path.join(root,'gallery.js'),'utf8'));await tick();
  assert.equal(w.document.querySelectorAll('.card').length,1);
  assert.equal(w.document.getElementById('hero').hidden,false);
  assert.equal(w.document.querySelector('.card h3').textContent,wallpaper.title);
  assert.equal(w.document.querySelectorAll('.card script').length,0);
  w.document.querySelector('.card').click();
  assert.equal(w.document.getElementById('detail').open,true);
  assert.ok(w.document.getElementById('download').href.endsWith('/original?download=true'));
  w.document.getElementById('close-dialog').click();assert.equal(w.document.getElementById('detail').open,false);
  let copied=null;Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async t=>{copied=t;}},configurable:true});
  const copyButtons=w.document.querySelectorAll('.api-copy');
  assert.equal(copyButtons.length,4);
  assert.equal(copyButtons[1].querySelector('code').textContent,'https://wallpaper.boyweb.net/img/4k');
  copyButtons[1].click();await tick();
  assert.equal(copied,'https://wallpaper.boyweb.net/img/4k');
  assert.equal(copyButtons[1].querySelector('.sr-only').textContent,'已复制');assert.ok(copyButtons[1].classList.contains('copied'));
  w.document.getElementById('search').value='海岸';
  w.document.getElementById('filters').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
  assert.ok(requests.at(-1).includes(encodeURIComponent('海岸')));
  dom.window.close();
}
async function admin() {
  const dom = new JSDOM(fs.readFileSync(path.join(root,'admin.html'),'utf8'),{url:'https://wallpaper.boyweb.net/admin/',runScripts:'outside-only'});
  const w=dom.window;let testBody=null,saveBody=null;
  w.fetch=async(url,opts)=>{
    let data={};
    if(url.endsWith('/settings')) {
      if(opts.method==='PUT')saveBody=JSON.parse(opts.body);
      data={retention_days:30,webdav_enabled:false,webdav_url:'',webdav_directory:'bing-wallpaper',webdav_username:'',password_saved:true};
    } else if(url.endsWith('/status'))data={total:8,local_count:8,remote_count:0,local_bytes:1000,running:false,last_job:null};
    else if(url.endsWith('/webdav/test')){testBody=JSON.parse(opts.body);data={ok:true,steps:['目录访问正常','上传成功','下载与内容校验通过','临时文件已删除']};}
    return {ok:true,status:200,json:async()=>data};
  };
  w.eval(fs.readFileSync(path.join(root,'admin.js'),'utf8'));await tick();
  assert.equal(w.document.getElementById('dashboard').hidden,false);
  w.document.getElementById('webdav_url').value='https://dav.example/dav/';
  w.document.getElementById('webdav_password').value=' secret with spaces ';
  w.document.getElementById('test-webdav').click();await tick();
  assert.equal(testBody.webdav_password,' secret with spaces ');
  assert.equal(testBody.retention_days,30);
  assert.ok(w.document.getElementById('test-result').textContent.includes('测试通过'));
  assert.ok(w.document.getElementById('test-result').textContent.includes('临时文件已删除'));
  assert.equal(saveBody,null,'Test button must not save settings');
  w.document.getElementById('settings-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
  assert.equal(saveBody.webdav_url,'https://dav.example/dav/');
  assert.equal(w.document.getElementById('webdav_password').value,'');
  dom.window.close();
}
(async()=>{await gallery();await admin();console.log('Frontend DOM checks passed: gallery, filters, preview, download, WebDAV test, save, secret clearing.');})().catch(e=>{console.error(e);process.exit(1);});
