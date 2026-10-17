import json
import os
import time

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
    rows, total = store.list_page(1, 10)
    assert total == 2
    row = next(r for r in rows if r['code'] == 'IPX-771')
    assert row['slug'] == 'ipx-771-uncensored-leak'
    assert (row['title_ja'], row['title_cn'], row['title_zh']) == (
        'オリジナル', '简体标题', '繁體標題')
    # No listing stored it first: the display title comes from title_cn.
    assert row['title'] == 'IPX-771-UNCENSORED-LEAK 简体标题'
    assert row['description'] == '简体标题'
    assert (row['has_chinese_subtitle'], row['has_english_subtitle'],
            row['is_uncensored_leak']) == (0, 1, 1)
    assert store.row_lists(row) == {
        'actors': ['男A'], 'actresses': ['女A', '女B'], 'genres': ['巨乳']}
    assert (row['duration'], row['released_at'], row['type']) == (
        7260, '2022-01-01', 'censored')
    assert row['thumbnail'] == 'https://fourhoi.com/ipx-771-uncensored-leak/cover-t.jpg'
    assert row['preview'] == 'https://fourhoi.com/ipx-771-uncensored-leak/preview.mp4'
    assert row['details_at'] is not None
    other = next(r for r in rows if r['code'] == 'SONE-001')
    assert json.loads(other['actresses']) == ['女C']
    assert other['duration'] == 3723
    assert other['has_english_subtitle'] is None

    # Seen again: not new, metadata refreshed, display title and row id kept.
    changed = json.loads(json.dumps(RECOMBEE))
    changed['responses'][0]['json']['recomms'][0]['values']['title_cn'] = '新标题'
    assert store.save_details('missav', resolver_app._parse_recombee_details(changed)) == []
    again = next(r for r in store.list_page(1, 10)[0] if r['code'] == 'IPX-771')
    assert again['title_cn'] == '新标题'
    assert again['title'] == 'IPX-771-UNCENSORED-LEAK 简体标题'
    assert again['id'] == row['id']


def test_details_fill_a_listing_row():
    url = 'https://missav.ai/cn/sone-001'
    store.save_listing('missav', [{'url': url, 'title': 'SONE-001 列表标题',
                                   'description': '列表标题', 'thumbnail': 'thumb',
                                   'is_uncensored_leak': False}])
    row = store.get(url)
    assert (row['slug'], row['code'], row['details_at']) == ('sone-001', 'SONE-001', None)

    # A listing row has no properties yet, so it still counts as new.
    assert url in store.save_details(
        'missav', resolver_app._parse_recombee_details(RECOMBEE))
    row = store.get(url)
    assert (row['title'], row['description'], row['thumbnail']) == (
        'SONE-001 列表标题', '列表标题', 'thumb')
    assert row['title_cn'] == '另一个'
    # Recombee doesn't know the flag, so the listing's stays.
    assert row['is_uncensored_leak'] == 0
    assert store.find_by_code('missav', 'sone-001')[0]['url'] == url


def test_migration_merges_old_tables():
    import sqlite3

    with sqlite3.connect(store.DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE videos (
                url TEXT PRIMARY KEY, site TEXT NOT NULL, id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
                thumbnail TEXT NOT NULL DEFAULT '', resolved_url TEXT,
                resolved_expires INTEGER, headers TEXT,
                title_checked INTEGER NOT NULL DEFAULT 0,
                has_chinese_subtitle INTEGER, is_uncensored_leak INTEGER,
                deleted_at INTEGER, created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL);
            CREATE INDEX idx_videos_site_id ON videos (site, id);
            CREATE INDEX idx_videos_created ON videos (created_at);
            CREATE TABLE video_details (
                id INTEGER PRIMARY KEY AUTOINCREMENT, site TEXT NOT NULL,
                code TEXT NOT NULL DEFAULT '', url TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
                title_cn TEXT NOT NULL DEFAULT '', title_zh TEXT NOT NULL DEFAULT '',
                has_chinese_subtitle INTEGER, has_english_subtitle INTEGER,
                is_uncensored_leak INTEGER, actors TEXT NOT NULL DEFAULT '[]',
                actresses TEXT NOT NULL DEFAULT '[]', genres TEXT NOT NULL DEFAULT '[]',
                duration INTEGER, released_at TEXT, type TEXT,
                thumbnail TEXT NOT NULL DEFAULT '', resolved_url TEXT,
                resolved_expires INTEGER, headers TEXT,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                deleted_at INTEGER);
            CREATE INDEX idx_video_details_site_code ON video_details (site, code);
            PRAGMA user_version = 4;
        """)
        conn.executemany(
            'INSERT INTO videos (url, site, id, title, description, thumbnail,'
            ' resolved_url, title_checked, created_at, updated_at)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [('https://missav.ai/cn/ipx-805', 'missav', 'ipx-805', 'IPX-805 中文',
              '中文', '', 'https://cdn/a.m3u8', 1, 5, 6),
             ('https://jable.tv/videos/sone-001/', 'jabletv', 'sone-001',
              'SONE-001 标题', '标题', 'jt', None, 0, 1, 2)])
        conn.executemany(
            'INSERT INTO video_details (id, site, code, url, description, title,'
            ' title_cn, actresses, duration, thumbnail, created_at, updated_at,'
            ' deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [(7, 'missav', 'IPX-805', 'https://missav.ai/cn/ipx-805', 'cn',
              'オリジナル', 'cn', '["女A"]', 60, 'https://fourhoi.com/ipx-805/cover-t.jpg',
              3, 9, 8),
             (8, 'missav', 'X-1', 'https://missav.ai/cn/x-1', '', '', '', '[]',
              None, '', 4, 4, None)])
        # video_details' flag wins; one it doesn't know comes from videos.
        conn.execute("UPDATE videos SET is_uncensored_leak = 0, has_chinese_subtitle = 1"
                     " WHERE id = 'ipx-805'")
        conn.execute("UPDATE video_details SET is_uncensored_leak = 1 WHERE id = 7")

    rows = {r['url']: r for r in store.list_page(1, 10)[0]
            + store.list_page(1, 10, deleted=True)[0]}
    assert len(rows) == 3
    both = rows['https://missav.ai/cn/ipx-805']
    assert (both['id'], both['slug'], both['code']) == (7, 'ipx-805', 'IPX-805')
    assert (both['title'], both['description'], both['title_ja']) == (
        'IPX-805 中文', '中文', 'オリジナル')
    assert both['resolved_url'] == 'https://cdn/a.m3u8'
    assert (both['is_uncensored_leak'], both['has_chinese_subtitle']) == (1, 1)
    assert both['preview'] == 'https://fourhoi.com/ipx-805/preview.mp4'
    assert store.row_lists(both)['actresses'] == ['女A']
    assert (both['title_checked'], both['details_at'], both['deleted_at']) == (1, 9, 8)
    assert (both['created_at'], both['updated_at']) == (3, 9)
    assert rows['https://missav.ai/cn/x-1']['preview'] == ''
    jable = rows['https://jable.tv/videos/sone-001/']
    assert jable['id'] > 8
    assert (jable['slug'], jable['code'], jable['details_at']) == ('sone-001', 'SONE-001', None)
    with store._connect() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == store._SCHEMA_VERSION
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert 'video_details' not in tables


def test_resolved_url_shared_by_scrape_and_details():
    url = 'https://missav.ai/cn/sone-001'
    store.save_detail(url, title='t', description='d', thumbnail='', title_checked=True,
                      resolved_url='https://cdn/a.m3u8', headers={'Referer': 'x'})
    store.save_details('missav', resolver_app._parse_recombee_details(RECOMBEE))
    row = store.get(url)
    assert row['resolved_url'] == 'https://cdn/a.m3u8'
    assert store.row_headers(row) == {'Referer': 'x'}
    assert (row['title'], row['title_cn']) == ('t', '另一个')

    other = 'https://missav.ai/cn/ipx-771-uncensored-leak'
    store.save_detail(other, title='t', description='d', thumbnail='', title_checked=True,
                      resolved_url='https://cdn/b.m3u8', headers={})
    row = store.get(other)
    assert row['resolved_url'] == 'https://cdn/b.m3u8'
    assert row['title_ja'] == 'オリジナル'
    assert store.list_page(1, 10)[1] == 2


def test_search_and_remove():
    store.save_details('missav', resolver_app._parse_recombee_details(RECOMBEE))
    assert [r['code'] for r in store.list_page(1, 10, search='ipx771')[0]] == ['IPX-771']
    assert [r['code'] for r in store.list_page(1, 10, search='繁體')[0]] == ['IPX-771']
    assert [r['code'] for r in store.list_page(1, 10, search='オリジナル')[0]] == ['IPX-771']
    assert store.remove('https://missav.ai/cn/sone-001')
    assert not store.remove('https://missav.ai/cn/sone-001')
    assert [r['code'] for r in store.list_page(1, 10)[0]] == ['IPX-771']
    assert [r['code'] for r in store.list_page(1, 10, deleted=True)[0]] == ['SONE-001']


def test_list_videos_endpoint():
    store.save_details('missav', resolver_app._parse_recombee_details(RECOMBEE))
    result = resolver_app.list_videos(1, 10, None, False, 'ipx771',
                                      x_api_key=resolver_app.RESOLVER_API_KEY)
    [video] = result['videos']
    assert (video['id'], video['code']) == ('ipx-771-uncensored-leak', 'IPX-771')
    assert isinstance(video['row_id'], int)
    assert (video['duration'], video['duration_seconds']) == ('2:01:00', 7260)
    assert video['actresses'] == ['女A', '女B']
    assert (video['has_detail'], video['resolved_valid']) == (False, False)
    assert (video['favourite'], video['favourite_at']) == (False, None)


def test_favourites():
    store.save_details('missav', resolver_app._parse_recombee_details(RECOMBEE))
    key = resolver_app.RESOLVER_API_KEY
    url = 'https://missav.ai/cn/sone-001'
    # Mirror / /dm<N>/ links name the same row.
    assert resolver_app.add_favourite('https://missav.ws/dm5/cn/sone-001',
                                      x_api_key=key)['favourite']
    first = store.get(url)['favourite']
    assert store.set_favourite(url, True)
    assert store.get(url)['favourite'] == first
    result = resolver_app.list_videos(1, 10, favourite=True, x_api_key=key)
    assert [v['id'] for v in result['videos']] == ['sone-001']
    assert result['videos'][0]['favourite']

    listed = resolver_app._annotate_listing(
        'missav', [{'url': url, 'title': 'SONE-001 x'}], prefetch=False)
    assert listed[0]['favourite']

    resolver_app.remove_favourite(url, x_api_key=key)
    assert store.list_page(1, 10, favourite=True)[1] == 0
    with pytest.raises(resolver_app.HTTPException) as exc:
        resolver_app.add_favourite('https://missav.ai/cn/nope-001', x_api_key=key)
    assert exc.value.status_code == 404
    # A removed video can't be favourited.
    store.remove(url)
    assert not store.set_favourite(url, True)


def test_video_code_strips_variant_suffixes():
    assert store.video_code('ipx-771-uncensored-leak') == 'IPX-771'
    assert store.video_code('sone-001-chinese-subtitle') == 'SONE-001'
    assert store.video_code('fc2-ppv-1234567') == 'FC2-PPV-1234567'


def test_crawl_counts_videos_new_to_details(monkeypatch):
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
    crawler = resolver_app._Crawler('crawl-full:')
    crawler._remaining = 10
    crawler._visit('missav', 'https://missav.ai/cn/abc-123')

    assert 'includedProperties' not in bodies[0]['requests'][0]['params']
    assert crawler.status()['found'] == 2
    assert store.list_page(1, 10)[1] == 2
    # Page details queued (no resolved_url yet) and both walked.
    assert 'https://missav.ai/cn/sone-001' in queued
    assert 'crawl-full:https://missav.ai/cn/sone-001' in queued

    # A second crawler sees them as known: walked, but not counted.
    queued.clear()
    second = resolver_app._Crawler('crawl-full:')
    second._remaining = 10
    second._visit('missav', 'https://missav.ai/cn/abc-123')
    assert second.status()['found'] == 0
    assert 'crawl-full:https://missav.ai/cn/sone-001' in queued
    # Its page still has no resolved_url, so the detail is queued again.
    assert 'https://missav.ai/cn/sone-001' in queued


def test_backfill_retries_unresolved_pages(monkeypatch):
    from fastapi import HTTPException

    ok, broken, failing = (f'https://missav.ai/cn/{s}' for s in ('ok-1', 'bad-1', 'net-1'))
    store.save_listing('missav', [{'url': u} for u in (ok, broken, failing)])
    calls = {}
    monkeypatch.setattr(resolver_app._prefetcher, 'enqueue_background',
                        lambda key, call=None: calls.setdefault(key, call) is call)

    def video_info(url):
        if url == ok:
            store.save_detail(url, title='t', description='', thumbnail='',
                              resolved_url='https://cdn/x.m3u8', headers={},
                              title_checked=False)
        else:
            raise HTTPException(status_code=422 if url == broken else 502)

    monkeypatch.setattr(resolver_app, '_video_info', video_info)
    backfill = resolver_app._Backfill()
    backfill.on_idle()
    assert calls == {}  # no crawl started yet

    backfill.start()
    rounds = []
    while True:
        backfill.on_idle()
        if not calls:
            break
        rounds.append(sorted(calls))
        for call in list(calls.values()):
            try:
                call()
            except HTTPException:
                pass
        calls.clear()
    assert rounds[0] == sorted(f'backfill:{u}' for u in (ok, broken, failing))
    # 422 is given up on at once; 502 is retried up to BACKFILL_ATTEMPTS.
    assert rounds[1:] == [[f'backfill:{failing}']] * (resolver_app.BACKFILL_ATTEMPTS - 1)
    assert backfill.status() == {'backfill_active': False,
                                 'backfill_sites': ['missav'], 'unresolved': 2}

    # Only the sites it was started for: no Jable rows, nothing to queue.
    backfill.start(('jabletv',))
    assert backfill.on_idle() == 0
    assert calls == {}
    assert backfill.status() == {'backfill_active': False,
                                 'backfill_sites': ['jabletv'], 'unresolved': 0}


def test_crawl_from_keyword_search(monkeypatch):
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
    crawler = resolver_app._Crawler('crawl-full:')
    assert crawler.add_search('missav', '女A 巨乳', 10, 30)
    assert not crawler.add_search('missav', '女A 巨乳', 10, 30)
    calls.pop('crawl-full:search:女A 巨乳')()

    request = bodies[0]['requests'][0]
    assert request['path'] == '/search/users/anonymous/items/'
    assert request['params'] == {'searchQuery': '女A 巨乳', 'count': 30,
                                 'cascadeCreate': True, 'returnProperties': True}
    assert crawler.status()['found'] == 2
    rows = {r['url']: r for r in store.list_page(1, 10)[0]}
    assert rows['https://missav.ai/cn/mida-278']['title_cn'] == '种子'
    # Search results are walked like related ones.
    assert 'crawl-full:https://missav.ai/cn/mida-278' in calls
    assert 'crawl-full:https://missav.ai/cn/mida-278-uncensored-leak' in calls


def test_full_crawl_endpoint_takes_a_query(monkeypatch):
    from fastapi import HTTPException

    searches = []
    monkeypatch.setattr(resolver_app, 'PREFETCH_ENABLED', True)
    monkeypatch.setattr(resolver_app._search_crawler, 'add_search',
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


class _FakeResp:
    def __init__(self, content, url, content_type=''):
        self.content = content
        self.url = url
        self.status_code = 200
        self.headers = {'Content-Type': content_type} if content_type else {}


def test_play_hls_rewrites_playlists_and_proxies_segments(monkeypatch):
    from urllib.parse import parse_qsl
    from fastapi import HTTPException
    calls = []
    master = b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n720p/video.m3u8\n'
    media = (b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXTINF:4,\n'
             b'seg0.jpeg\n#EXTINF:4,\nhttps://other.cdn/seg1.jpeg\n#EXT-X-ENDLIST\n')

    def fake_get(url, headers=None, timeout=20):
        calls.append((url, headers))
        if url.endswith('playlist.m3u8'):
            return _FakeResp(master, url)
        if url.endswith('video.m3u8'):
            return _FakeResp(media, url)
        return _FakeResp(b'Gsegment', url, 'image/jpeg')

    monkeypatch.setattr(resolver_app, '_http_get', fake_get)
    ref = 'https://missav.ai/'

    def get(proxied):
        """Follows one rewritten playlist line, as the player would."""
        assert proxied.startswith('hls?')
        return resolver_app.play_hls(x_api_key=None, **dict(parse_qsl(proxied[4:])))

    with pytest.raises(HTTPException) as exc:
        resolver_app.play_hls(x_api_key=None, u='https://cdn/a/playlist.m3u8')
    assert exc.value.status_code == 401
    resp = resolver_app.play_hls(x_api_key=None, u='https://cdn/a/playlist.m3u8', ref=ref, api_key='test-key')
    assert resp.media_type == 'application/vnd.apple.mpegurl'
    variant = resp.body.decode().splitlines()[2]
    assert variant == ('hls?u=https%3A%2F%2Fcdn%2Fa%2F720p%2Fvideo.m3u8'
                       '&ref=https%3A%2F%2Fmissav.ai%2F&api_key=test-key')
    assert calls[-1][1]['Referer'] == ref and calls[-1][1]['Origin'] == 'https://missav.ai'

    lines = get(variant).body.decode().splitlines()
    assert 'URI="hls?u=https%3A%2F%2Fcdn%2Fa%2F720p%2Fkey.bin&' in lines[1]
    assert lines[3].startswith('hls?u=https%3A%2F%2Fcdn%2Fa%2F720p%2Fseg0.jpeg&')
    assert lines[5].startswith('hls?u=https%3A%2F%2Fother.cdn%2Fseg1.jpeg&')
    assert calls[-1][1]['Referer'] == ref

    seg = get(lines[3])
    assert seg.body == b'Gsegment'
    assert seg.media_type == 'video/mp2t'

    with pytest.raises(HTTPException) as exc:
        resolver_app.play_hls(x_api_key=None, u='file:///etc/passwd', api_key='test-key')
    assert exc.value.status_code == 400


def test_web_client_is_served():
    routes = {getattr(r, 'path', None): r for r in resolver_app.app.routes}
    assert resolver_app.web_root().headers['location'] == '/app/'
    assert (resolver_app._WEB_DIR / 'index.html').is_file()
    assert (resolver_app._WEB_DIR / 'login.html').is_file()
    assert (resolver_app._WEB_DIR / 'manifest.webmanifest').is_file()
    assert '/app' in routes


def _request(cookie=None, client='1.2.3.4', scheme='http'):
    from starlette.requests import Request
    headers = [(b'cookie', f'{resolver_app.SESSION_COOKIE}={cookie}'.encode())] if cookie else []
    return Request({'type': 'http', 'method': 'GET', 'path': '/', 'query_string': b'',
                    'headers': headers, 'client': (client, 0), 'scheme': scheme,
                    'server': ('test', 80)})


def test_web_session_cookie(monkeypatch):
    import asyncio
    session = resolver_app._new_session()
    assert resolver_app._valid_session(session)
    expires, _, sig = session.partition('.')
    assert not resolver_app._valid_session(f'{int(expires) + 1}.{sig}')
    assert not resolver_app._valid_session('')
    assert not resolver_app._valid_session(f'{int(time.time()) - 1}.'
                                           + resolver_app._session_sig(int(time.time()) - 1))

    seen = []

    async def inner(scope, receive, send):
        seen.append(dict(scope['headers']).get(b'x-api-key'))

    middleware = resolver_app._SessionAuth(inner)

    def run(path, cookie):
        headers = [(b'cookie', f'a=1; {resolver_app.SESSION_COOKIE}={cookie}'.encode())]
        asyncio.run(middleware({'type': 'http', 'path': path, 'headers': headers}, None, None))
        return seen[-1]

    assert run('/api/videos', session) == b'test-key'
    assert run('/api/videos', 'forged.0000') is None
    assert run('/app/', session) is None


def test_web_login(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(resolver_app, '_login_failures', {})
    resp = resolver_app.login(resolver_app.LoginRequest(api_key=' test-key '), _request())
    cookie = resp.headers['set-cookie']
    assert cookie.startswith(resolver_app.SESSION_COOKIE + '=')
    assert 'HttpOnly' in cookie and 'SameSite=lax' in cookie and 'Secure' not in cookie

    for _ in range(resolver_app._LOGIN_MAX_FAILURES):
        with pytest.raises(HTTPException) as exc:
            resolver_app.login(resolver_app.LoginRequest(api_key='wrong'), _request())
        assert exc.value.status_code == 401
    # Locked out, even with the right key; other clients are not.
    with pytest.raises(HTTPException) as exc:
        resolver_app.login(resolver_app.LoginRequest(api_key='test-key'), _request())
    assert exc.value.status_code == 429
    resolver_app.login(resolver_app.LoginRequest(api_key='test-key'), _request(client='5.6.7.8'))


def test_web_app_needs_login():
    assert resolver_app.web_app(_request()).path.name == 'login.html'
    assert resolver_app.web_app(_request('forged.0000')).path.name == 'login.html'
    page = resolver_app.web_app(_request(resolver_app._new_session()))
    assert page.path.name == 'index.html'
    assert page.headers['cache-control'] == 'no-store'


def test_play_hls_keeps_header_key_out_of_playlist(monkeypatch):
    monkeypatch.setattr(resolver_app, '_http_get', lambda url, headers=None, timeout=20:
                        _FakeResp(b'#EXTM3U\nseg0.ts\n', url))
    # As the session middleware passes a cookie-authenticated request on.
    resp = resolver_app.play_hls(x_api_key='test-key', u='https://cdn/a/v.m3u8', api_key=None)
    assert resp.body.decode().splitlines()[1] == 'hls?u=https%3A%2F%2Fcdn%2Fa%2Fseg0.ts&ref='
