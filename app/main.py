import hashlib
import hmac
import html
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from urllib.parse import urlencode
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .storage import Store, WebDAV, TZ, digest, safe_error

STATIC = Path(__file__).parent / 'static'
store = Store(os.getenv('DATA_DIR', './data'))
sessions = {}
attempts = {}
auth_lock = threading.Lock()
remote_slots = threading.BoundedSemaphore(4)


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600000).hex()


def scheduler(stop):
    # Start immediately, then poll hourly. This catches missed days and retries upstream outages.
    while not stop.is_set():
        store.run_job()
        stop.wait(3600)


@asynccontextmanager
async def lifespan(app):
    stop = threading.Event()
    worker = None
    if os.getenv('SCHEDULER_ENABLED', '1') == '1':
        worker = threading.Thread(target=scheduler, args=(stop,), daemon=True)
        worker.start()
    yield
    stop.set()
    if worker:
        worker.join(timeout=5)


app = FastAPI(title='日序 · 必应壁纸档案', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware('http')
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith('/api/admin') or request.url.path.startswith('/admin'):
        response.headers['Cache-Control'] = 'no-store'
    return response


def same_origin(request):
    expected = os.getenv('PUBLIC_URL', 'http://127.0.0.1:8080').rstrip('/')
    origin = request.headers.get('origin')
    if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and origin.rstrip('/') != expected):
        raise HTTPException(403, '请求来源不允许')


def admin(request: Request):
    same_origin(request)
    token = request.cookies.get('wallpaper_session', '')
    key = digest(token.encode())
    with auth_lock:
        expiry = sessions.get(key, 0)
    if expiry < time.time():
        raise HTTPException(401, '请先登录')


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@app.post('/api/admin/login')
def login(body: Login, request: Request, response: Response):
    same_origin(request)
    ip = request.client.host
    with auth_lock:
        recent = [t for t in attempts.get(ip, []) if t > time.time() - 900]
        attempts[ip] = recent
        if len(recent) >= 8:
            raise HTTPException(429, '尝试次数过多，请 15 分钟后重试')
        recent.append(time.time())
    expected = os.getenv('ADMIN_PASSWORD_HASH', '')
    salt = os.getenv('ADMIN_PASSWORD_SALT', '')
    if not expected or not salt or not hmac.compare_digest(password_hash(body.password, salt), expected):
        raise HTTPException(401, '密码不正确')
    token = secrets.token_urlsafe(32)
    with auth_lock:
        sessions.update({digest(token.encode()): time.time() + 43200})
        attempts.pop(ip, None)
        for k in list(sessions):
            if sessions[k] < time.time():
                sessions.pop(k)
    response.set_cookie('wallpaper_session', token, httponly=True, samesite='strict',
                        secure=os.getenv('COOKIE_SECURE', '1') == '1', max_age=43200, path='/api/admin')
    return {'ok': True}


@app.post('/api/admin/logout', dependencies=[Depends(admin)])
def logout(request: Request, response: Response):
    with auth_lock:
        sessions.pop(digest(request.cookies.get('wallpaper_session', '').encode()), None)
    response.delete_cookie('wallpaper_session', path='/api/admin')
    return {'ok': True}


def public_settings(cfg):
    return {k: v for k, v in cfg.items() if k != 'webdav_password'} | {'password_saved': bool(cfg['webdav_password'])}


@app.get('/api/admin/settings', dependencies=[Depends(admin)])
def settings():
    return public_settings(store.config())


@app.put('/api/admin/settings', dependencies=[Depends(admin)])
def update_settings(body: dict):
    if store.lock.locked():
        raise HTTPException(409, '采集任务正在运行，请完成后保存')
    try:
        return public_settings(store.save_config(body))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post('/api/admin/webdav/test', dependencies=[Depends(admin)])
def test_webdav(body: dict):
    dav = None
    try:
        cfg = store.candidate(body)
        dav = WebDAV(cfg)
        return dav.test()
    except Exception as exc:
        return {'ok': False, 'steps': [], 'error': safe_error(exc)}
    finally:
        if dav:
            dav.close()


@app.get('/api/admin/status', dependencies=[Depends(admin)])
def status():
    return store.status()


@app.post('/api/admin/sync', dependencies=[Depends(admin)], status_code=202)
def sync():
    if store.lock.locked():
        raise HTTPException(409, '采集任务正在运行')
    threading.Thread(target=store.run_job, daemon=True).start()
    return {'ok': True, 'message': '采集与归档任务已启动'}


def public_row(row):
    row = dict(row)
    return {k: row[k] for k in ('date', 'title', 'copyright', 'copyright_link', 'width', 'height', 'size')} | {
        'thumbnail': '/media/' + row['date'] + '/thumb', 'preview': '/media/' + row['date'] + '/preview',
        'original': '/media/' + row['date'] + '/original'}


@app.get('/api/wallpapers')
def wallpapers(q: str = Query('', max_length=100), month: str = Query('', pattern=r'^(\d{4}-\d{2})?$'),
               page: int = Query(1, ge=1), per_page: int = Query(18, ge=1, le=60)):
    where, args = [], []
    if q:
        where.append('(title LIKE ? OR copyright LIKE ?)')
        args.extend(['%' + q + '%'] * 2)
    if month:
        where.append('date LIKE ?')
        args.append(month + '%')
    sql = ' WHERE ' + ' AND '.join(where) if where else ''
    with store.db() as c:
        total = c.execute('SELECT COUNT(*) FROM wallpapers' + sql, args).fetchone()[0]
        rows = c.execute('SELECT * FROM wallpapers' + sql + ' ORDER BY date DESC LIMIT ? OFFSET ?',
                         args + [per_page, (page - 1) * per_page]).fetchall()
        latest = c.execute('SELECT * FROM wallpapers ORDER BY date DESC LIMIT 1').fetchone()
        count = c.execute('SELECT COUNT(*) FROM wallpapers').fetchone()[0]
    return {'items': [public_row(r) for r in rows], 'total': total, 'page': page,
            'archive_count': count, 'latest': public_row(latest) if latest else None}


def find_wallpaper(day):
    try:
        datetime.strptime(day, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(400, '日期格式应为 YYYY-MM-DD')
    with store.db() as c:
        row = c.execute('SELECT * FROM wallpapers WHERE date=?', (day,)).fetchone()
    if not row:
        raise HTTPException(404, '该日期尚未收录')
    return dict(row)


@app.get('/media/{day}/{variant}')
def media(day: str, variant: str, download: bool = False):
    row = find_wallpaper(day)
    if variant not in ('thumb', 'preview', 'original'):
        raise HTTPException(404)
    return serve_image(row, variant, download)


def serve_image(row, variant, download=False, cache='public, max-age=86400'):
    day = row['date']
    folder = {'thumb': 'thumbs', 'preview': 'previews', 'original': 'originals'}[variant]
    suffix = '.jpg' if variant == 'original' else '.webp'
    path = store.root / folder / (day + suffix)
    headers = {'Cache-Control': cache, 'ETag': '"' + row['sha256'] + '-' + variant + '"'}
    if download:
        headers['Content-Disposition'] = 'attachment; filename="bing-' + day + suffix + '"'
    if path.exists():
        return FileResponse(path, headers=headers, media_type='image/jpeg' if suffix == '.jpg' else 'image/webp')
    if variant != 'original' or not row['remote_name']:
        raise HTTPException(404, '图片文件不存在')
    if not remote_slots.acquire(blocking=False):
        raise HTTPException(503, '下载繁忙，请稍后重试', headers={'Retry-After': '5'})
    dav = None
    try:
        dav = WebDAV(store.config())
        data = dav.read(row['remote_name'])
        if digest(data) != row['sha256']:
            raise ValueError('远端文件校验失败')
        return Response(data, media_type='image/jpeg', headers=headers)
    except Exception:
        raise HTTPException(502, '历史原图暂时不可用，请稍后重试')
    finally:
        if dav:
            dav.close()
        remote_slots.release()


@app.get('/img/')
@app.get('/img/{mode}')
def image_api(request: Request, mode: str = 'today',
              date: str = Query('', pattern=r'^(\d{8}|\d{4}-\d{2}-\d{2})?$'), type: str = 'image'):
    if mode not in ('today', '4k', '4K', 'rand') or type not in ('image', 'json'):
        raise HTTPException(400, '参数不正确')
    with store.db() as c:
        if date:
            try:
                day = datetime.strptime(date.replace('-', ''), '%Y%m%d').date().isoformat()
            except ValueError:
                raise HTTPException(400, '日期不正确')
            row = c.execute('SELECT * FROM wallpapers WHERE date=?', (day,)).fetchone()
        else:
            row = c.execute('SELECT * FROM wallpapers ORDER BY ' + ('RANDOM()' if mode == 'rand' else 'date DESC') + ' LIMIT 1').fetchone()
    if not row:
        raise HTTPException(404, '该日期尚未收录')
    if type == 'json':
        return public_row(row)
    # 浏览器直接打开 /img/rand 时返回带壁纸信息的页面；<img src> 引用或 curl 等仍返回图片本身。
    if mode == 'rand' and is_navigation(request):
        return rand_page(dict(row))
    # 直接返回图片内容，地址栏保持 /img/... 不变。
    # 今日图和随机图会变化，所以不缓存（带 date 的请求结果固定，可以缓存）。
    cache = 'public, max-age=86400' if date else 'no-store'
    return serve_image(dict(row), 'original' if mode.lower() == '4k' else 'preview', cache=cache)


def is_navigation(request):
    dest = request.headers.get('sec-fetch-dest')
    if dest:
        return dest == 'document'
    # 不支持 Sec-Fetch 的旧浏览器：仅当 Accept 首选 HTML 时视为页面访问
    return request.headers.get('accept', '').startswith('text/html')


def rand_page(row):
    e = lambda s: html.escape(str(s or ''), quote=True)
    day = row['date']
    # 页面里的图片用固定日期地址，避免再次随机拿到另一张
    search = '/?' + urlencode({'q': row['title'], 'month': day[:7]})
    source = row['copyright_link'] if str(row['copyright_link'] or '').startswith(('https://www.bing.com/', 'https://bing.com/')) else ''
    source_link = f'<a class="outline-button" href="{e(source)}" target="_blank" rel="noopener noreferrer">在必应了解更多 ↗</a>' if source else ''
    body = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(row['title'])} · 日序随机壁纸</title><link rel="icon" href="/static/bing.svg?v=20261012h" type="image/svg+xml"><link rel="stylesheet" href="/static/style.css?v=20261012h"></head>
<body class="rand-page">
<main class="rand-wrap">
<img class="rand-image" src="/media/{e(day)}/preview" alt="{e(row['title'])}" width="{e(row['width'])}" height="{e(row['height'])}">
<section class="rand-info">
<p class="detail-date">{e(day)} · {e(row['width'])} × {e(row['height'])}</p>
<h1>{e(row['title'])}</h1>
<p class="rand-copyright">{e(row['copyright'])}</p>
<div class="detail-actions">
<a class="button" href="/media/{e(day)}/original?download=true" download>下载原图 ↓</a>
<a class="outline-button" href="{e(search)}">在档案中查看</a>
<a class="outline-button" href="/img/rand">换一张 ↻</a>
{source_link}
</div>
</section>
</main>
</body></html>'''
    return HTMLResponse(body, headers={'Cache-Control': 'no-store', 'Vary': 'Sec-Fetch-Dest, Accept'})


@app.get('/healthz')
def health():
    with store.db() as c:
        c.execute('SELECT 1').fetchone()
    return {'ok': True}


@app.get('/')
def home():
    return FileResponse(STATIC / 'index.html', headers={'Cache-Control': 'no-cache'})


@app.get('/admin')
@app.get('/admin/')
def admin_page():
    return FileResponse(STATIC / 'admin.html')


app.mount('/static', StaticFiles(directory=STATIC), name='static')
