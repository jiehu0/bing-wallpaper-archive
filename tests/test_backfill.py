from datetime import date

import pytest

from app.backfill import plan_import


def record(day, name):
    return {'date':day,'title':'风景','caption':'标题','copyright':'© Photo',
            'bing_url':f'https://bing.com/th?id=OHR.{name}_ZH-CN123_UHD.jpg'}


def test_plan_aligns_bing_startdate_and_skips_existing():
    data=[record('2026-10-05','A'),record('2026-10-04','B'),record('2026-07-12','Old')]
    existing=[{'date':'2026-10-04','source_url':'https://www.bing.com/th?id=OHR.A_ZH-CN123_UHD.jpg'}]
    plan,aligned=plan_import(data,existing,date(2026,7,12),date(2026,10,4),-1)
    assert aligned==1
    assert [x['date'] for x in plan]==['2026-10-03']
    assert plan[0]['url']=='https://bing.npanuhin.me/CN/zh/2026-10-04.jpg'


def test_reject_wrong_date_offset():
    data=[record('2026-10-05','A')]
    existing=[{'date':'2026-10-04','source_url':data[0]['bing_url']}]
    with pytest.raises(ValueError,match='日期不一致'):
        plan_import(data,existing,date(2026,7,12),date(2026,10,4),0)


def test_reject_unverified_source_alignment():
    with pytest.raises(ValueError,match='交叉核对'):
        plan_import([record('2026-08-01','A')],[{'date':'2026-10-04','source_url':'https://bing.com/th?id=OHR.Unknown'}],date(2026,7,12),date(2026,10,4),-1)


def test_release_zip_matches_image_identity_and_startdate(tmp_path):
    import zipfile
    from app.backfill import plan_release_import
    entries = [{'enddate': '20231013', 'urlbase': '/th?id=OHR.Coast_ZH-CN123',
                'title': '海岸', 'copyright': 'Photo', 'copyrightlink': 'https://www.bing.com/search?q=coast'},
               {'enddate': '20261005', 'urlbase': '/th?id=OHR.Recent_ZH-CN456'}]
    existing = [{'date': '2026-10-04', 'source_url': 'https://www.bing.com/th?id=OHR.Recent_ZH-CN456_UHD.jpg'}]
    with zipfile.ZipFile(tmp_path / 'fixture.zip', 'w') as z:
        z.writestr('2023/20231013_OHR.Coast_ZH-CN123.jpg', b'fixture-image')
    with zipfile.ZipFile(tmp_path / 'fixture.zip') as z:
        plan, aligned = plan_release_import(entries, existing, date(2023,10,12), date(2023,10,12), -1, z)
        assert aligned == 1
        assert plan[0]['date'] == '2023-10-12'
        assert plan[0]['member'] == '2023/20231013_OHR.Coast_ZH-CN123.jpg'
        assert plan[0]['copyright'] == 'Photo'
        assert plan[0]['source_url'].endswith('OHR.Coast_ZH-CN123_UHD.jpg')
        entries[0]['urlbase'] = '/th?id=OHR.Wrong_ZH-CN123'
        with pytest.raises(ValueError, match='缺少所需原图'):
            plan_release_import(entries, existing, date(2023,10,12), date(2023,10,12), -1, z)
