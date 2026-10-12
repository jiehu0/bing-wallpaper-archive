import io
import os
import json

import httpx
import pytest
from PIL import Image

from app import storage


def photo():
    buf = io.BytesIO()
    Image.new('RGB', (1200, 800), '#335544').save(buf, 'JPEG')
    return buf.getvalue()


@pytest.fixture
def store(tmp_path):
    return storage.Store(tmp_path)


@pytest.fixture
def dav(monkeypatch):
    files, methods = {}, []
    state = {'corrupt': False, 'fail_put': False, 'deny_delete': False}
    def handle(request):
        methods.append(request.method)
        path = request.url.path
        if request.method == 'PROPFIND':return httpx.Response(207)
        if request.method == 'MKCOL':return httpx.Response(201)
        if request.method == 'PUT':
            if state['fail_put']:return httpx.Response(507)
            files[path] = request.content
            return httpx.Response(201)
        if request.method == 'GET':return httpx.Response(200,content=b'corrupt' if state['corrupt'] else files[path])
        if request.method == 'DELETE':
            if state['deny_delete']:return httpx.Response(403)
            files.pop(path,None)
            return httpx.Response(204)
        return httpx.Response(405)
    transport = httpx.MockTransport(handle)
    original = storage.WebDAV
    monkeypatch.setattr(storage,'WebDAV',lambda cfg:original(cfg,transport=transport))
    cfg=storage.DEFAULTS | {'webdav_enabled':True,'webdav_url':'https://dav.example/dav/',
                            'webdav_username':'test','webdav_password':'secret'}
    return cfg,files,state,methods


def add(store, day):
    store.ingest(day,{'title':'海岸','copyright':'测试版权'},'https://www.bing.com/example.jpg',photo())


def test_quota_archives_oldest_first_and_stops_at_limit(store,dav):
    cfg,files,_,_=dav
    add(store,'2026-10-12');add(store,'2026-09-12');add(store,'2026-09-13')
    cfg['storage_limit_gb'] = (store.storage_bytes() - len(photo())) / storage.GB
    assert store.archive(cfg)==1
    assert not (store.root/'originals/2026-09-12.jpg').exists()
    assert (store.root/'originals/2026-09-13.jpg').exists()
    assert (store.root/'thumbs/2026-09-12.webp').exists()
    assert len(files)==2
    assert store.archive(cfg)==0
    assert store.status()['remote_count']==1


@pytest.mark.parametrize('failure',['corrupt','fail_put'])
def test_remote_failure_preserves_original(store,dav,failure):
    cfg,_,state,_=dav;state[failure]=True;add(store,'2026-08-01')
    cfg['storage_limit_gb'] = (store.storage_bytes() - 1) / storage.GB
    with pytest.raises((ValueError,httpx.HTTPStatusError)):
        store.archive(cfg)
    assert (store.root/'originals/2026-08-01.jpg').read_bytes()==photo()
    assert store.status()['local_count']==1
    assert store.status()['remote_count']==0


def test_not_configured_never_deletes(store):
    add(store,'2020-01-01');assert store.archive()==0
    assert (store.root/'originals/2020-01-01.jpg').exists()


def test_probe_cleans_temporary_file(dav):
    cfg,files,_,methods=dav
    probe=storage.WebDAV(cfg)
    result=probe.test();probe.close()
    assert result['ok'] and not files
    assert all(m in methods for m in ['PROPFIND','MKCOL','PUT','GET','DELETE'])


def test_probe_reports_delete_failure(dav):
    cfg,_,state,_=dav;state['deny_delete']=True
    probe=storage.WebDAV(cfg)
    result=probe.test();probe.close()
    assert not result['ok']
    assert '403' in result['error']
    assert any('清理失败' in step for step in result['steps'])


def test_webdav_download_redirect_does_not_forward_credentials():
    requests = []
    def handle(request):
        requests.append(request)
        if request.url.host == 'dav.example':
            assert request.headers.get('authorization', '').startswith('Basic ')
            return httpx.Response(302, headers={'Location': 'https://objects.example/signed-image.jpg?token=fixture'})
        assert 'authorization' not in request.headers
        return httpx.Response(200, content=b'original-image')
    cfg=storage.DEFAULTS | {'webdav_url':'https://dav.example/dav/', 'webdav_username':'test', 'webdav_password':'secret'}
    dav=storage.WebDAV(cfg, transport=httpx.MockTransport(handle))
    try:
        assert dav.read('image.jpg') == b'original-image'
        assert len(requests) == 2
    finally:
        dav.close()


def test_upload_waits_for_eventual_readability_without_reupload(monkeypatch):
    methods = []
    delays = []
    def handle(request):
        methods.append(request.method)
        if request.method == 'PUT':
            return httpx.Response(201)
        if methods.count('GET') <= 2:
            return httpx.Response(404)
        return httpx.Response(200, content=b'original-image')
    monkeypatch.setattr(storage.time, 'sleep', delays.append)
    cfg = storage.DEFAULTS | {'webdav_url': 'https://dav.example/dav/'}
    dav = storage.WebDAV(cfg, transport=httpx.MockTransport(handle))
    try:
        dav.put_verified('image.jpg', b'original-image')
        assert methods.count('PUT') == 1
        assert methods.count('GET') == 3
        assert delays == [2, 4]
    finally:
        dav.close()


@pytest.mark.parametrize('status,expected_reads', [(404, 5), (403, 1)])
def test_upload_read_retries_are_bounded(monkeypatch, status, expected_reads):
    methods = []
    def handle(request):
        methods.append(request.method)
        return httpx.Response(201 if request.method == 'PUT' else status)
    monkeypatch.setattr(storage.time, 'sleep', lambda _: None)
    cfg = storage.DEFAULTS | {'webdav_url': 'https://dav.example/dav/'}
    dav = storage.WebDAV(cfg, transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            dav.put_verified('image.jpg', b'original-image')
        assert methods.count('PUT') == 1
        assert methods.count('GET') == expected_reads
    finally:
        dav.close()


def test_encrypted_settings_and_password_preserved(store,dav):
    cfg,_,_,_=dav
    store.save_config(cfg)
    assert b'secret' not in (store.root/'settings.enc').read_bytes()
    store.save_config({'webdav_password':'','storage_limit_gb':31})
    assert store.config()['webdav_password']=='secret'
    assert (store.root/'settings.enc').stat().st_mode & 0o777==0o600


def test_archive_target_cannot_change_after_eviction(store,dav):
    cfg,_,_,_=dav;add(store,'2026-08-01')
    cfg['storage_limit_gb'] = (store.storage_bytes() - 1) / storage.GB
    store.save_config(cfg)
    store.archive()
    with pytest.raises(ValueError,match='不能直接更换'):
        store.save_config({'webdav_directory':'elsewhere'})


def test_recover_interruption_after_unlink(store,dav):
    cfg,files,_,_=dav;add(store,'2026-08-01')
    with store.db() as c:
        c.execute("UPDATE wallpapers SET remote_name='already.jpg' WHERE date='2026-08-01'")
    files['/dav/bing-wallpaper/already.jpg']=photo()
    (store.root/'originals/2026-08-01.jpg').unlink()
    assert store.archive(cfg)==1
    assert store.status()['local_count']==0


def test_invalid_image_not_committed(store):
    with pytest.raises(Exception):
        store.ingest('2026-10-12',{},'',b'not-an-image')
    assert store.status()['total']==0


def test_api_auth_filters_and_image_integrity(tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path))
    monkeypatch.setenv('SCHEDULER_ENABLED','0')
    monkeypatch.setenv('COOKIE_SECURE','0')
    monkeypatch.setenv('PUBLIC_URL','http://testserver')
    from app import main
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main,'store',storage.Store(tmp_path))
    monkeypatch.setenv('ADMIN_PASSWORD_SALT','testsalt')
    monkeypatch.setenv('ADMIN_PASSWORD_HASH',main.password_hash('testpassword','testsalt'))
    add(main.store,'2026-10-12')
    with TestClient(main.app) as client:
        assert client.get('/api/admin/settings').status_code==401
        assert client.post('/api/admin/login',json={'password':'bad'}).status_code==401
        assert client.post('/api/admin/login',json={'password':'testpassword'}).status_code==200
        assert client.get('/api/admin/settings').status_code==200
        assert 'webdav_password' not in client.get('/api/admin/settings').json()
        assert client.put('/api/admin/settings',json={'storage_limit_gb':0}).status_code==400
        assert client.put('/api/admin/settings',json={},headers={'Origin':'https://evil.example'}).status_code==403
        assert client.get('/api/wallpapers?q=海岸').json()['total']==1
        assert client.get('/api/wallpapers?month=2026-09').json()['total']==0
        assert client.get('/media/2026-10-12/original').content==photo()
        assert client.get('/media/2026-10-12/original?download=true').headers['content-disposition'].startswith('attachment')
        assert client.get('/img/?date=20260230').status_code==400
        assert client.get('/img/?type=json').json()['date']=='2026-10-12'
        r=client.get('/img/4k',follow_redirects=False)
        assert r.status_code==200 and r.headers['content-type']=='image/jpeg' and r.headers['cache-control']=='no-store'
        r=client.get('/img/rand',follow_redirects=False)
        assert r.status_code==200 and r.headers['content-type']=='image/webp'
        r=client.get('/img/rand',headers={'sec-fetch-dest':'image','accept':'text/html'})
        assert r.headers['content-type']=='image/webp'
        r=client.get('/img/rand',headers={'sec-fetch-dest':'document'})
        assert r.status_code==200 and r.headers['content-type'].startswith('text/html') and r.headers['cache-control']=='no-store'
        assert '/media/2026-10-12/preview' in r.text and '/media/2026-10-12/original?download=true' in r.text and '<script' not in r.text
        r=client.get('/img/rand',headers={'accept':'text/html,application/xhtml+xml'})
        assert r.headers['content-type'].startswith('text/html')
        r=client.get('/img/',follow_redirects=False)
        assert r.status_code==200 and r.headers['content-type']=='image/webp'
        assert client.post('/api/admin/webdav/test',json={'webdav_url':'bad'}).json()['ok'] is False
        assert client.post('/api/admin/logout').status_code==200
        assert client.get('/api/admin/settings').status_code==401


def test_old_photos_kept_below_quota_without_contacting_webdav(store, dav):
    cfg, _, _, methods = dav
    add(store, '2020-01-01')
    cfg['storage_limit_gb'] = store.storage_bytes() / storage.GB
    assert store.archive(cfg) == 0
    assert store.status()['local_count'] == 1
    assert methods == []
    expected = sum(p.stat().st_size for folder in ('originals', 'thumbs', 'previews') for p in (store.root / folder).iterdir())
    assert store.status()['storage_bytes'] == expected > store.status()['local_bytes']


def test_legacy_settings_migrate_without_losing_credentials(store, dav):
    cfg, _, _, _ = dav
    cfg.pop('storage_limit_gb')
    cfg['retention_days'] = 90
    store.atomic(store.root / 'settings.enc', store.cipher.encrypt(json.dumps(cfg).encode()))
    assert store.config()['storage_limit_gb'] == 40
    assert 'retention_days' not in store.config()
    store.save_config({'storage_limit_gb': 40})
    assert store.config()['webdav_password'] == 'secret'


@pytest.mark.parametrize('limit', [0, -1, True, '40', None, float('nan'), float('inf'), 1000001])
def test_invalid_quota_rejected(store, limit):
    with pytest.raises(ValueError, match='容量上限'):
        store.candidate({'storage_limit_gb': limit})


def test_existing_verified_remote_copy_is_reused(store, dav):
    cfg, files, _, methods = dav
    add(store, '2020-01-01')
    with store.db() as c:
        c.execute("UPDATE wallpapers SET remote_name='2020/01/already.jpg'")
    files['/dav/bing-wallpaper/2020/01/already.jpg'] = photo()
    cfg['storage_limit_gb'] = (store.storage_bytes() - 1) / storage.GB
    assert store.archive(cfg) == 1
    assert 'PUT' not in methods
    assert store.status()['local_count'] == 0


def test_unattainable_quota_preserves_previews_and_reports_error(store, dav):
    cfg, _, _, _ = dav
    add(store, '2020-01-01')
    cfg['storage_limit_gb'] = 1 / storage.GB
    with pytest.raises(ValueError, match='请提高上限'):
        store.archive(cfg)
    assert store.status()['local_count'] == 0
    assert (store.root / 'previews/2020-01-01.webp').exists()
    with pytest.raises(ValueError, match='请提高上限'):
        store.archive(cfg)
