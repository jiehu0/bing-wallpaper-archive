const $ = id => document.getElementById(id);
let page = 1, latest = null, generation = 0;
function dateOptions(id, values, unit) {
  const selected = $(id).value;
  $(id).replaceChildren(new Option(`请选择${unit}`, ''), ...values.map(value => new Option(`${value}${unit}`, value)));
  $(id).value = values.includes(selected) ? selected : '';
}
function updateDays() {
  const year = Number($('year').value) || 2000; // 未选年时保留 2 月 29 日
  const month = Number($('month').value);
  const days = month ? new Date(year, month, 0).getDate() : 31;
  dateOptions('day', Array.from({length: days}, (_, i) => String(i + 1).padStart(2, '0')), '日');
}
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
  const params = new URLSearchParams({page, q: $('search').value.trim(), year: $('year').value, month: $('month').value, day: $('day').value});
  try {
    const response = await fetch('/api/wallpapers?' + params); if (!response.ok) throw new Error();
    const data = await response.json(); if (current !== generation) return;
    const years = [...new Set([...(data.years || []), $('year').value].filter(Boolean))].sort().reverse();
    dateOptions('year', years, '年');
    $('archive-count').textContent = String(data.archive_count).padStart(3, '0');
    $('api-count').textContent = data.archive_count;
    $('result-count').textContent = ` / ${data.total}`;
    latest = data.latest;
    if (latest) {
      $('hero').hidden = false; $('hero-image').src = latest.preview; $('hero-image').alt = latest.title;
      $('page-bg').style.backgroundImage = `url("${encodeURI(latest.preview)}")`;
      $('hero-title').textContent = latest.title; $('hero-date').textContent = latest.date;
      $('hero-copyright').textContent = latest.copyright;
    }
    $('gallery').replaceChildren(...data.items.map(card));
    $('message').textContent = data.items.length ? '' : (data.archive_count ? '没有找到匹配的风景，试试其他关键词或日期。' : '第一批壁纸正在路上，稍后回来看看。');
    const pages = Math.max(1, Math.ceil(data.total / 18));
    $('page-info').textContent = `${page} / ${pages}`; $('previous').disabled = page <= 1; $('next').disabled = page >= pages;
  } catch { if (current === generation) $('message').textContent = '暂时无法加载，请刷新页面重试。'; }
}
$('filters').addEventListener('submit', event => {event.preventDefault(); page = 1; load();});
$('reset').addEventListener('click', () => {
  ['search', 'year', 'month', 'day'].forEach(id => {$(id).value = '';});
  updateDays(); page = 1; load();
});
['year', 'month', 'day'].forEach(id => $(id).addEventListener('change', () => {updateDays(); page = 1; load();}));
$('previous').addEventListener('click', () => {page--; load(); $('archive').scrollIntoView();});
$('next').addEventListener('click', () => {page++; load(); $('archive').scrollIntoView();});
$('toggle-search').addEventListener('click', () => {$('filters').scrollIntoView({block: 'center'}); $('search').focus({preventScroll: true});});
$('hero-open').addEventListener('click', () => latest && openDetail(latest));
$('close-dialog').addEventListener('click', () => $('detail').close());
$('detail').addEventListener('click', e => {if (e.target === $('detail')) $('detail').close();});
$('toggle-api').addEventListener('click', () => $('api-dialog').showModal());
$('close-api').addEventListener('click', () => $('api-dialog').close());
// 点击遮罩关闭 API 弹窗
$('api-dialog').addEventListener('click', e => {if (e.target === $('api-dialog')) $('api-dialog').close();});
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch {}
  // 非安全上下文或旧浏览器的回退方案
  const area = document.createElement('textarea'); area.value = text; area.setAttribute('readonly', '');
  area.style.position = 'fixed'; area.style.opacity = '0'; document.body.append(area); area.select();
  let ok = false; try { ok = document.execCommand('copy'); } catch {} area.remove(); return ok;
}
// API 弹窗：每条链接都是按钮，点击即复制；“调用方式”一行复制完整的 <img> 代码
const copyIcon = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
const checkIcon = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
const toast = document.createElement('div'); toast.className = 'api-toast'; toast.setAttribute('role', 'status'); toast.setAttribute('aria-live', 'polite');
$('api-dialog').append(toast);
let toastTimer;
function showToast(text, ok) {
  toast.textContent = text; toast.classList.toggle('error', !ok); toast.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.classList.remove('show'), 1600);
}
document.querySelectorAll('.api-copy').forEach(button => {
  const url = location.origin + button.dataset.path;
  const text = button.dataset.snippet === 'img' ? `<img src="${url}" />` : url;
  const code = document.createElement('code'); code.textContent = text;
  const icon = document.createElement('span'); icon.className = 'api-icon'; icon.setAttribute('aria-hidden', 'true'); icon.innerHTML = copyIcon;
  const status = document.createElement('span'); status.className = 'sr-only';
  button.replaceChildren(code, icon, status); button.title = '点击复制';
  let timer;
  button.addEventListener('click', async () => {
    const ok = await copyText(text);
    status.textContent = ok ? '已复制' : '复制失败，请手动选择';
    showToast(ok ? '已复制到剪贴板' : '复制失败，请手动选择', ok);
    if (ok) icon.innerHTML = checkIcon;
    button.classList.toggle('copied', ok);
    clearTimeout(timer); timer = setTimeout(() => {
      icon.innerHTML = copyIcon; status.textContent = ''; button.classList.remove('copied');
    }, 1600);
  });
});
// 兼容随机壁纸页的 month=YYYY-MM，同时支持独立的年、月、日参数。
const initial = new URLSearchParams(location.search);
$('search').value = initial.get('q') || '';
const initialMonth = initial.get('month') || '';
const legacyMonth = /^\d{4}-(0[1-9]|1[0-2])$/.test(initialMonth);
const initialYear = initial.get('year') || (legacyMonth ? initialMonth.slice(0, 4) : '');
if (/^[1-9]\d{3}$/.test(initialYear)) {
  dateOptions('year', [initialYear], '年'); $('year').value = initialYear;
}
dateOptions('month', Array.from({length: 12}, (_, i) => String(i + 1).padStart(2, '0')), '月');
$('month').value = legacyMonth ? initialMonth.slice(5) : initialMonth;
if (!$('month').value) $('month').value = '';
updateDays();
const initialDay = initial.get('day') || '';
if ([...$('day').options].some(option => option.value === initialDay)) $('day').value = initialDay;
load();
