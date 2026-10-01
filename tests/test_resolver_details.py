import json
import os

import pytest

os.environ.setdefault('RESOLVER_API_KEY', 'test-key')

from uav_downloader.resolver import app as resolver_app  # noqa: E402
from uav_downloader.resolver import store  # noqa: E402


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / 'resolver.db'))
    monkeypatch.setattr(store, '_initialized', False)


RECOMBEE = {'responses': [{'json': {'recomms': [
    {'id': 'ipx-771-uncensored-leak', 'values': {
        'title': 'オリジナル', 'title_cn': '简体标题', 'title_zh': '繁體標題',
        'has_chinese_subtitle': False, 'has_english_subtitle': True,
        'is_uncensored_leak': True, 'actors': ['男A'], 'actresses': ['女A', '女B'],
        'genres': ['巨乳'], 'duration': 7260, 'released_at': '2022-01-01',
        'type': 'censored'}},
    {'id': 'sone-001', 'values': {'title_cn': '另一个', 'actresses': '女C',
                                  'duration': '1:02:03'}},
]}}]}


def test_save_details_new_then_refresh():
    new = store.save_details('missav', resolver_app._parse_recombee_details(RECOMBEE))
    assert new == ['https://missav.ai/cn/ipx-771-uncensored-leak',
                   'https://missav.ai/cn/sone-001']
    rows, total = store.list_details_page(1, 10)
    assert total == 2
    row = next(r for r in rows if r['code'] == 'IPX-771')
    assert (row['title'], row['title_cn'], row['title_zh']) == (
        'オリジナル', '简体标题', '繁體標題')
    assert row['description'] == '简体标题'
    assert (row['has_chinese_subtitle'], row['has_english_subtitle'],
            row['is_uncensored_leak']) == (0, 1, 1)
    assert store.detail_row_lists(row) == {
        'actors': ['男A'], 'actresses': ['女A', '女B'], 'genres': ['巨乳']}
    assert (row['duration'], row['released_at'], row['type']) == (
        7260, '2022-01-01', 'censored')
    other = next(r for r in rows if r['code'] == 'SONE-001')
    assert json.loads(other['actresses']) == ['女C']
    assert other['duration'] == 3723
    assert other['has_english_subtitle'] is None

    # Seen again: not new, metadata refreshed, row id kept.
    changed = json.loads(json.dumps(RECOMBEE))
    changed['responses'][0]['json']['recomms'][0]['values']['title_cn'] = '新标题'
    assert store.save_details('missav', resolver_app._parse_recombee_details(changed)) == []
    again = next(r for r in store.list_details_page(1, 10)[0] if r['code'] == 'IPX-771')
    assert again['title_cn'] == '新标题'
    assert again['id'] == row['id']


def test_resolved_url_follows_videos_table():
    url = 'https://missav.ai/cn/sone-001'
    store.save_detail(url, title='t', description='d', thumbnail='', title_checked=True,
                      resolved_url='https://cdn/a.m3u8', headers={'Referer': 'x'})
    store.save_details('missav', resolver_app._parse_recombee_details(RECOMBEE))
    rows = {r['url']: r for r in store.list_details_page(1, 10)[0]}
    assert rows[url]['resolved_url'] == 'https://cdn/a.m3u8'
    assert store.row_headers(rows[url]) == {'Referer': 'x'}

    other = 'https://missav.ai/cn/ipx-771-uncensored-leak'
    store.save_detail(other, title='t', description='d', thumbnail='', title_checked=True,
                      resolved_url='https://cdn/b.m3u8', headers={})
    rows = {r['url']: r for r in store.list_details_page(1, 10)[0]}
    assert rows[other]['resolved_url'] == 'https://cdn/b.m3u8'


def test_search_and_remove():
    store.save_details('missav', resolver_app._parse_recombee_details(RECOMBEE))
    assert [r['code'] for r in store.list_details_page(1, 10, search='ipx771')[0]] == ['IPX-771']
    assert [r['code'] for r in store.list_details_page(1, 10, search='繁體')[0]] == ['IPX-771']
    assert store.remove_details('https://missav.ai/cn/sone-001')
    assert not store.remove_details('https://missav.ai/cn/sone-001')
    assert [r['code'] for r in store.list_details_page(1, 10)[0]] == ['IPX-771']
    assert [r['code'] for r in store.list_details_page(1, 10, deleted=True)[0]] == ['SONE-001']


def test_video_code_strips_variant_suffixes():
    assert store.video_code('ipx-771-uncensored-leak') == 'IPX-771'
    assert store.video_code('sone-001-chinese-subtitle') == 'SONE-001'
    assert store.video_code('fc2-ppv-1234567') == 'FC2-PPV-1234567'


def test_full_crawl_counts_new_detail_rows(monkeypatch):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return RECOMBEE

    bodies = []
    monkeypatch.setattr(resolver_app.requests, 'post',
                        lambda url, json, timeout: bodies.append(json) or Resp())
    queued = []
    monkeypatch.setattr(resolver_app._prefetcher, 'enqueue_background',
                        lambda key, call=None: queued.append(key) or True)
    crawler = resolver_app._Crawler('crawl-full:', full=True)
    crawler._remaining = 10
    crawler._visit('missav', 'https://missav.ai/cn/abc-123')

    assert 'includedProperties' not in bodies[0]['requests'][0]['params']
    assert crawler.status()['found'] == 2
    assert store.list_details_page(1, 10)[1] == 2
    # Page details queued (no resolved_url yet) and both walked.
    assert 'https://missav.ai/cn/sone-001' in queued
    assert 'crawl-full:https://missav.ai/cn/sone-001' in queued

    # A second crawler sees them as known: walked, but not counted.
    queued.clear()
    second = resolver_app._Crawler('crawl-full:', full=True)
    second._remaining = 10
    second._visit('missav', 'https://missav.ai/cn/abc-123')
    assert second.status()['found'] == 0
    assert 'crawl-full:https://missav.ai/cn/sone-001' in queued


def test_full_crawl_from_keyword_search(monkeypatch):
    search_result = {'responses': [{'json': {'recomms': [
        {'id': 'mida-278', 'values': {'title_cn': '种子', 'actresses': ['女A']}},
        {'id': 'mida-278-uncensored-leak', 'values': {'is_uncensored_leak': True}},
    ]}}]}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return search_result

    bodies = []
    monkeypatch.setattr(resolver_app.requests, 'post',
                        lambda url, json, timeout: bodies.append(json) or Resp())
    calls = {}
    monkeypatch.setattr(resolver_app._prefetcher, 'enqueue_background',
                        lambda key, call=None: calls.setdefault(key, call) is call)
    crawler = resolver_app._Crawler('crawl-full:', full=True)
    assert crawler.add_search('missav', '女A 巨乳', 10, 30)
    assert not crawler.add_search('missav', '女A 巨乳', 10, 30)
    calls.pop('crawl-full:search:女A 巨乳')()

    request = bodies[0]['requests'][0]
    assert request['path'] == '/search/users/anonymous/items/'
    assert request['params'] == {'searchQuery': '女A 巨乳', 'count': 30,
                                 'cascadeCreate': True, 'returnProperties': True}
    assert crawler.status()['found'] == 2
    rows = {r['url']: r for r in store.list_details_page(1, 10)[0]}
    assert rows['https://missav.ai/cn/mida-278']['title_cn'] == '种子'
    # Search results are walked like related ones.
    assert 'crawl-full:https://missav.ai/cn/mida-278' in calls
    assert 'crawl-full:https://missav.ai/cn/mida-278-uncensored-leak' in calls


def test_full_crawl_endpoint_takes_a_query(monkeypatch):
    from fastapi import HTTPException

    searches = []
    monkeypatch.setattr(resolver_app, 'PREFETCH_ENABLED', True)
    monkeypatch.setattr(resolver_app._full_crawler, 'add_search',
                        lambda site, query, limit, count: searches.append(
                            (site, query, limit, count)) or True)
    key = resolver_app.RESOLVER_API_KEY
    result = resolver_app.full_crawl_search(
        resolver_app.FullCrawlRequest(query=' 三上悠亚 ', limit=5), key)
    assert result['search_queued'] is True
    assert searches == [('missav', '三上悠亚', 5, 12)]
    with pytest.raises(HTTPException) as exc:
        resolver_app.full_crawl_search(resolver_app.FullCrawlRequest(query=' '), key)
    assert exc.value.status_code == 400
