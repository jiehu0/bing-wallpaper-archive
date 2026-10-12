"""Conservative, resumable CN history import. Run with --apply after inspecting the plan."""
import argparse
import io
import json
import time
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
from PIL import Image

from .storage import MAX_IMAGE, Store, TZ, now, safe_error

INDEX = 'https://bing.npanuhin.me/CN-zh.json'


def image_id(url):
    parsed = urlsplit(url or '')
    if parsed.hostname not in ('bing.com', 'www.bing.com'):
        return None
    return parse_qs(parsed.query).get('id', [None])[0]


def plan_import(entries, existing, start, end, offset):
    if start > end:
        raise ValueError('开始日期不能晚于结束日期')
    existing_by_image = {image_id(r['source_url']): r['date'] for r in existing if image_id(r['source_url'])}
    existing_dates = {r['date'] for r in existing}
    aligned, items, seen = 0, [], set()
    for entry in entries:
        source_day = date.fromisoformat(entry['date'])
        day = (source_day + timedelta(days=offset)).isoformat()
        identity = image_id(entry.get('bing_url'))
        if identity and identity in existing_by_image:
            if existing_by_image[identity] != day:
                raise ValueError('历史源与现有壁纸日期不一致，请核对 --source-date-offset')
            aligned += 1
        if not start.isoformat() <= day <= end.isoformat() or day in existing_dates:
            continue
        if day in seen:
            raise ValueError('历史索引包含重复日期，已停止导入')
        seen.add(day)
        # Construct the trusted archive URL rather than requesting arbitrary metadata URLs.
        items.append({'date': day, 'source_date': entry['date'],
                      'url': f'https://bing.npanuhin.me/CN/zh/{source_day.isoformat()}.jpg',
                      'title': entry.get('caption') or entry.get('title') or day,
                      'copyright': ' '.join(x for x in (entry.get('title'), entry.get('copyright')) if x),
                      'copyrightlink': 'https://www.bing.com/search?q=' + entry['date'],
                      'source_url': entry.get('bing_url') if identity else f'https://bing.npanuhin.me/CN/zh/{source_day.isoformat()}.jpg'})
    if existing and not aligned:
        raise ValueError('没有找到可与现有图片交叉核对的日期，已停止导入')
    return sorted(items, key=lambda x: x['date'], reverse=True), aligned


def download(client, url):
    data = bytearray()
    with client.stream('GET', url) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > MAX_IMAGE:
                raise ValueError('历史图片超过允许大小')
    with Image.open(io.BytesIO(data)) as image:
        image.verify()
        if image.width < 1280 or image.height < 720:
            raise ValueError('历史图片分辨率异常，已跳过')
    return bytes(data)


def main():
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=date.fromisoformat, required=True)
    parser.add_argument('--end', type=date.fromisoformat, required=True)
    parser.add_argument('--source-date-offset', type=int, choices=(-1, 0, 1), required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()
    # Leave the recent window to the normal collector, avoiding cross-process writes to one day.
    if args.end > datetime.now(TZ).date() - timedelta(days=9):
        parser.error('--end 必须早于最近 8 天采集窗口')
    store = Store(os.getenv('DATA_DIR', '/data'))
    with store.db() as c:
        existing = [dict(r) for r in c.execute('SELECT date,source_url FROM wallpapers')]
    with httpx.Client(timeout=60, follow_redirects=True, trust_env=False,
                      headers={'User-Agent': 'BoywebWallpaperArchive/1.0 (historical backfill)'}) as client:
        response = client.get(INDEX)
        response.raise_for_status()
        items, aligned = plan_import(response.json(), existing, args.start, args.end, args.source_date_offset)
        if args.limit:
            items = items[:args.limit]
        report = {'source': INDEX, 'source_date_offset': args.source_date_offset,
                  'start': args.start.isoformat(), 'end': args.end.isoformat(),
                  'aligned_existing': aligned, 'planned': len(items), 'started_at': now(),
                  'added': [], 'failed': [], 'download_bytes': 0}
        print(json.dumps({'plan': {k: v for k, v in report.items() if k not in ('added', 'failed')}}, ensure_ascii=False), flush=True)
        if not args.apply:
            return
        for entry in items:
            try:
                with store.db() as c:
                    if c.execute('SELECT 1 FROM wallpapers WHERE date=?', (entry['date'],)).fetchone():
                        continue
                data = download(client, entry['url'])
                store.ingest(entry['date'], entry, entry['source_url'], data)
                report['added'].append(entry['date'])
                report['download_bytes'] += len(data)
                print(json.dumps({'added': entry['date'], 'completed': len(report['added']), 'total': len(items)}), flush=True)
            except Exception as exc:
                report['failed'].append({'date': entry['date'], 'error': safe_error(exc)})
                print(json.dumps({'failed': report['failed'][-1]}, ensure_ascii=False), flush=True)
            finally:
                report['updated_at'] = now()
                store.atomic(store.root / 'backfill-latest.json', json.dumps(report, ensure_ascii=False, indent=2).encode())
                time.sleep(.3)
        report['finished_at'] = now()
        store.atomic(store.root / 'backfill-latest.json', json.dumps(report, ensure_ascii=False, indent=2).encode())
        print(json.dumps({'finished': True, 'added': len(report['added']), 'failed': report['failed'],
                          'download_bytes': report['download_bytes']}, ensure_ascii=False), flush=True)
        if report['failed']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
