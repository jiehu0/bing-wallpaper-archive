import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import httpx
from cryptography.fernet import Fernet
from PIL import Image

TZ = ZoneInfo('Asia/Shanghai')
MAX_IMAGE = 32 * 1024 * 1024
DEFAULTS = {'retention_days': 30, 'webdav_enabled': False, 'webdav_url': '',
            'webdav_directory': 'bing-wallpaper', 'webdav_username': '', 'webdav_password': ''}


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_error(exc):
    # Avoid leaking credential-bearing URLs from HTTP client exceptions.
    if isinstance(exc, httpx.HTTPStatusError):
        return f'远端返回 HTTP {exc.response.status_code}'
    if isinstance(exc, httpx.TimeoutException):
        return '远端请求超时，请稍后重试'
    if isinstance(exc, httpx.RequestError):
        return '连接远端失败，请检查地址、网络与 TLS 证书'
    if isinstance(exc, ValueError):
        return str(exc)
    return f'操作失败（{type(exc).__name__}）'


class WebDAV:
    def __init__(self, config, transport=None):
        parsed = urlsplit(config['webdav_url'])
        schemes = ('https', 'http') if os.getenv('ALLOW_HTTP_WEBDAV') == '1' else ('https',)
        if parsed.scheme not in schemes or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('请填写 HTTPS WebDAV 地址，不要在地址中包含账号或参数')
        parts = config['webdav_directory'].strip('/').split('/')
        if any(p in ('', '.', '..') or '\\' in p for p in parts):
            raise ValueError('请填写独立的 WebDAV 目录，例如 bing-wallpaper')
        self.base = config['webdav_url'].rstrip('/') + '/'
        self.directory = '/'.join(parts)
        self.client = httpx.Client(auth=(config['webdav_username'], config['webdav_password']),
                                   timeout=httpx.Timeout(60, connect=15), follow_redirects=False,
                                   transport=transport, trust_env=False)

    def close(self):
        self.client.close()

    def url(self, path=''):
        return self.base + quote(self.directory + ('/' + path if path else '/'), safe='/')

    def ensure_directory(self):
        r = self.client.request('PROPFIND', self.base, headers={'Depth': '0'})
        r.raise_for_status()
        current = self.base
        for part in self.directory.split('/'):
            current += quote(part, safe='') + '/'
            r = self.client.request('MKCOL', current)
            if r.status_code not in (200, 201, 204, 405):
                r.raise_for_status()
        self.client.request('PROPFIND', self.url(), headers={'Depth': '0'}).raise_for_status()

    def read(self, name):
        chunks, size = [], 0
        # WebDAV services may redirect downloads to a signed object-storage URL.
        # HTTPX strips Authorization when a redirect crosses origins.
        with self.client.stream('GET', self.url(name), follow_redirects=True) as r:
            r.raise_for_status()
            for chunk in r.iter_bytes():
                size += len(chunk)
                if size > MAX_IMAGE:
                    raise ValueError('远端文件超过允许大小')
                chunks.append(chunk)
        return b''.join(chunks)

    def put_verified(self, name, data):
        self.client.put(self.url(name), content=data).raise_for_status()
        for attempt in range(5):
            try:
                received = self.read(name)
                break
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                transient = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code in (404, 429, 500, 502, 503, 504)
                if attempt == 4 or not transient:
                    raise
                # Some WebDAV gateways accept a PUT before the download becomes readable.
                # Retry the read, not the upload, to avoid creating redundant cloud versions.
                time.sleep(min(2 ** (attempt + 1), 12))
        if digest(received) != digest(data):
            raise ValueError('上传后的文件校验失败，已保留本地原图')

    def test(self):
        name = '.connection-test-' + secrets.token_hex(12) + '.txt'
        steps = []
        written = False
        try:
            self.ensure_directory()
            steps.append('目录访问正常')
            payload = secrets.token_bytes(64)
            self.client.put(self.url(name), content=payload).raise_for_status()
            written = True
            steps.append('上传成功')
            if self.read(name) != payload:
                raise ValueError('下载内容与上传内容不一致')
            steps.append('下载与内容校验通过')
            self.client.delete(self.url(name)).raise_for_status()
            written = False
            steps.append('临时文件已删除')
            return {'ok': True, 'steps': steps}
        except Exception as exc:
            if written:
                try:
                    self.client.delete(self.url(name)).raise_for_status()
                    steps.append('临时文件已清理')
                except Exception:
                    steps.append('临时文件清理失败：' + name)
            return {'ok': False, 'steps': steps, 'error': safe_error(exc)}


class Store:
    def __init__(self, directory):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        for p in ('originals', 'thumbs', 'previews'):
            (self.root / p).mkdir(exist_ok=True)
        self.lock = threading.Lock()
        self.config_lock = threading.RLock()
        keyfile = self.root / '.encryption-key'
        if not keyfile.exists():
            fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(Fernet.generate_key())
        self.cipher = Fernet(keyfile.read_bytes())
        with self.db() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS wallpapers (
                date TEXT PRIMARY KEY, title TEXT NOT NULL, copyright TEXT NOT NULL,
                copyright_link TEXT NOT NULL, source_url TEXT NOT NULL,
                sha256 TEXT NOT NULL, size INTEGER NOT NULL, width INTEGER, height INTEGER,
                remote_name TEXT, local INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.root / 'archive.sqlite3', timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def config(self):
        with self.config_lock:
            path = self.root / 'settings.enc'
            return DEFAULTS | (json.loads(self.cipher.decrypt(path.read_bytes())) if path.exists() else {})

    def candidate(self, patch):
        cfg = self.config()
        for key in DEFAULTS:
            if key in patch and not (key == 'webdav_password' and patch[key] == ''):
                cfg[key] = patch[key]
        if not isinstance(cfg['retention_days'], int) or not 1 <= cfg['retention_days'] <= 3650:
            raise ValueError('本地保留天数应在 1 到 3650 之间')
        for key in ('webdav_url', 'webdav_directory', 'webdav_username', 'webdav_password'):
            if not isinstance(cfg[key], str) or len(cfg[key]) > 4096:
                raise ValueError('WebDAV 配置格式不正确')
        cfg['webdav_enabled'] = bool(cfg['webdav_enabled'])
        if cfg['webdav_enabled'] and not cfg['webdav_url']:
            raise ValueError('启用归档前请填写 WebDAV 地址')
        if cfg['webdav_url']:
            dav = WebDAV(cfg)
            dav.close()
        return cfg

    def save_config(self, patch):
        with self.lock, self.config_lock:
            cfg = self.candidate(patch)
            previous = self.config()
            with self.db() as c:
                archived = c.execute('SELECT COUNT(*) FROM wallpapers WHERE remote_name IS NOT NULL').fetchone()[0]
            if archived and any(cfg[k].rstrip('/') != previous[k].rstrip('/') for k in ('webdav_url', 'webdav_directory')):
                raise ValueError('已有远端归档，不能直接更换存储地址或目录；请先迁移归档文件')
            self.atomic(self.root / 'settings.enc', self.cipher.encrypt(json.dumps(cfg).encode()))
            return cfg

    @staticmethod
    def atomic(path, data):
        temp = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
        try:
            with open(temp, 'xb') as f:
                os.chmod(temp, 0o600)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def status(self):
        with self.db() as c:
            totals = dict(c.execute('SELECT COUNT(*) total, COALESCE(SUM(local),0) local_count, '
                                   'COALESCE(SUM(CASE WHEN local=1 THEN size ELSE 0 END),0) local_bytes, '
                                   'COALESCE(SUM(CASE WHEN remote_name IS NOT NULL THEN 1 ELSE 0 END),0) remote_count '
                                   'FROM wallpapers').fetchone())
            row = c.execute("SELECT value FROM meta WHERE key='last_job'").fetchone()
        return totals | {'running': self.lock.locked(), 'last_job': json.loads(row[0]) if row else None}

    def record_job(self, result):
        with self.db() as c:
            c.execute("INSERT OR REPLACE INTO meta VALUES ('last_job',?)", (json.dumps(result, ensure_ascii=False),))

    def collect(self):
        added, errors = 0, []
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; BoywebWallpaper/1.0)', 'Referer': 'https://www.bing.com/'}
        with httpx.Client(timeout=60, headers=headers, follow_redirects=True, trust_env=False) as client:
            r = client.get('https://www.bing.com/HPImageArchive.aspx',
                           params={'format': 'js', 'idx': 0, 'n': 8, 'mkt': 'zh-CN'})
            r.raise_for_status()
            items = r.json().get('images', [])
            if not items:
                raise ValueError('Bing 未返回图片，请稍后重试')
            for item in items:
                try:
                    day = datetime.strptime(item['startdate'], '%Y%m%d').date().isoformat()
                    with self.db() as c:
                        if c.execute('SELECT 1 FROM wallpapers WHERE date=?', (day,)).fetchone():
                            continue
                    base = item['urlbase']
                    if not base.startswith('/th?id=OHR.'):
                        raise ValueError('Bing 返回了无法识别的图片地址')
                    url = 'https://www.bing.com' + base + '_UHD.jpg'
                    data = bytearray()
                    with client.stream('GET', url) as image:
                        image.raise_for_status()
                        for chunk in image.iter_bytes():
                            data.extend(chunk)
                            if len(data) > MAX_IMAGE:
                                raise ValueError('原图超过允许大小')
                    self.ingest(day, item, url, bytes(data))
                    added += 1
                except Exception as exc:
                    errors.append(safe_error(exc))
        return added, errors

    def ingest(self, day, item, url, data):
        import io
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            width, height = image.size
            for folder, max_size, quality in [('thumbs', (640, 420), 76), ('previews', (1920, 1200), 83)]:
                copy = image.convert('RGB')
                copy.thumbnail(max_size)
                out = io.BytesIO()
                copy.save(out, 'WEBP', quality=quality)
                self.atomic(self.root / folder / (day + '.webp'), out.getvalue())
        self.atomic(self.root / 'originals' / (day + '.jpg'), data)
        with self.db() as c:
            c.execute('INSERT OR IGNORE INTO wallpapers '
                      '(date,title,copyright,copyright_link,source_url,sha256,size,width,height,created_at) '
                      'VALUES (?,?,?,?,?,?,?,?,?,?)',
                      (day, item.get('title') or item.get('copyright', '').split(' (')[0],
                       item.get('copyright', ''), item.get('copyrightlink', ''), url,
                       digest(data), len(data), width, height, now()))

    def archive(self, config=None, today=None):
        cfg = config or self.config()
        if not cfg['webdav_enabled']:
            return 0
        today = today or datetime.now(TZ).date()
        # Includes today: with 30 days, the earliest retained day is today - 29.
        cutoff = (today - timedelta(days=cfg['retention_days'] - 1)).isoformat()
        with self.db() as c:
            rows = [dict(r) for r in c.execute('SELECT * FROM wallpapers WHERE date < ? AND local=1', (cutoff,))]
        dav, count = WebDAV(cfg), 0
        try:
            dav.ensure_directory()
            for row in rows:
                path = self.root / 'originals' / (row['date'] + '.jpg')
                if not path.exists() and row['remote_name']:
                    # Recover a crash after unlink but before the local flag was committed.
                    if digest(dav.read(row['remote_name'])) != row['sha256']:
                        raise ValueError('远端文件校验失败，归档已停止')
                    with self.db() as c:
                        c.execute('UPDATE wallpapers SET local=0 WHERE date=?', (row['date'],))
                    count += 1
                    continue
                data = path.read_bytes()
                if digest(data) != row['sha256']:
                    raise ValueError('本地原图校验失败，归档已停止')
                name = row['date'] + '-' + row['sha256'][:16] + '.jpg'
                dav.put_verified(name, data)
                dav.put_verified(name + '.json', json.dumps(row, ensure_ascii=False).encode())
                with self.db() as c:
                    c.execute('UPDATE wallpapers SET remote_name=? WHERE date=?', (name, row['date']))
                # Commit remote reference before removing local data; retries remain idempotent.
                path.unlink()
                with self.db() as c:
                    c.execute('UPDATE wallpapers SET local=0 WHERE date=?', (row['date'],))
                count += 1
            # Metadata and credentials remain local; remote manifest files carry per-image provenance.
        finally:
            dav.close()
        return count

    def run_job(self):
        if not self.lock.acquire(blocking=False):
            return
        result = {'started_at': now(), 'added': 0, 'archived': 0, 'errors': []}
        try:
            try:
                result['added'], result['errors'] = self.collect()
            except Exception as exc:
                result['errors'].append(safe_error(exc))
            try:
                result['archived'] = self.archive()
            except Exception as exc:
                result['errors'].append(safe_error(exc))
        finally:
            result['finished_at'] = now()
            try:
                self.record_job(result)
            finally:
                self.lock.release()
