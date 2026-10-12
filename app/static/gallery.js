const $ = id => document.getElementById(id);
let page = 1, latest = null, generation = 0;
function openDetail(item) {
  $('detail-image').src = item.preview;
  $('detail-image').alt = item.title;
  $('detail-date').textContent = `${item.date} · ${item.width} × ${item.height}`;
  $('detail-title').textContent = item.title;
  $('detail-copyright').textContent = item.copyright;
  $('download').href = `${item.original}?download=true`;
  $('source').href = /^https:\/\/(www\.)?bing\.com\//.test(item.copyright_link) ? item.copyright_link : 'https://www.bing.com/';
  $('detail').showModal();
}
function card(item) {
  const button = document.createElement('button'); button.className = 'card';
  const frame = document.createElement('div'); frame.className = 'card-frame';
  const img = document.createElement('img'); img.src = item.thumbnail; img.alt = item.title; img.loading = 'lazy'; img.width = 640; img.height = 360;
  const arrow = document.createElement('span'); arrow.className = 'card-arrow'; arrow.textContent = '↗';
  frame.append(img, arrow);
  const date = document.createElement('p'); date.className = 'card-date'; date.textContent = item.date.replaceAll('-', ' / ');
  const title = document.createElement('h3'); title.textContent = item.title;
  const credit = document.createElement('p'); credit.className = 'card-credit'; credit.textContent = item.copyright;
  button.append(frame, date, title, credit); button.addEventListener('click', () => openDetail(item)); return button;
}
async function load() {
  const current = ++generation;
  $('message').textContent = '正在打开风景档案…';
  const params = new URLSearchParams({page, q: $('search').value.trim(), month: $('month').value});
  try {
    const response = await fetch('/api/wallpapers?' + params); if (!response.ok) throw new Error();
    const data = await response.json(); if (current !== generation) return;
    $('archive-count').textContent = String(data.archive_count).padStart(3, '0');
    $('result-count').textContent = ` / ${data.total}`;
    latest = data.latest;
    if (latest) {
      $('hero').hidden = false; $('hero-image').src = latest.preview; $('hero-image').alt = latest.title;
      $('page-bg').style.backgroundImage = `url("${encodeURI(latest.preview)}")`;
      $('hero-title').textContent = latest.title; $('hero-date').textContent = latest.date;
      $('hero-copyright').textContent = latest.copyright;
    }
    $('gallery').replaceChildren(...data.items.map(card));
    $('message').textContent = data.items.length ? '' : (data.archive_count ? '没有找到匹配的风景，试试其他关键词或月份。' : '第一批壁纸正在路上，稍后回来看看。');
    const pages = Math.max(1, Math.ceil(data.total / 18));
    $('page-info').textContent = `${page} / ${pages}`; $('previous').disabled = page <= 1; $('next').disabled = page >= pages;
  } catch { if (current === generation) $('message').textContent = '暂时无法加载，请刷新页面重试。'; }
}
$('filters').addEventListener('submit', event => {event.preventDefault(); page = 1; load();});
$('reset').addEventListener('click', () => {$('search').value = ''; $('month').value = ''; page = 1; load();});
$('previous').addEventListener('click', () => {page--; load(); $('archive').scrollIntoView();});
$('next').addEventListener('click', () => {page++; load(); $('archive').scrollIntoView();});
$('toggle-search').addEventListener('click', () => {$('filters').scrollIntoView({block: 'center'}); $('search').focus({preventScroll: true});});
$('hero-open').addEventListener('click', () => latest && openDetail(latest));
$('close-dialog').addEventListener('click', () => $('detail').close());
$('detail').addEventListener('click', e => {if (e.target === $('detail')) $('detail').close();});
load();
