#!/usr/bin/env python
# coding: utf-8
"""SQLite cache of video metadata for the resolver service.

Listings pre-fill id/site/title/description/thumbnail/url; /api/detail adds
resolved_url (+ the headers the CDN needs) once it has scraped a page, so the
next open of the same video skips scraping entirely.

id/site/title/description never change for a given page, so they are kept
forever. resolved_url may not be: JableTV's m3u8 URLs carry an expiry
timestamp (/hls/<token>/<epoch>/...), so it is stored with resolved_expires
and only served while still valid. MissAV's URLs don't expire (NULL).

Keyed by the page URL, not the video code: MissAV has several pages per code
(uncensored-leak, chinese-subtitle, ...). URLs are normalised to the main
domain first, so jable.tv / fs1.app mirror links share a row, and MissAV's
optional /dm<N>/ routing prefix is dropped (/dm39/cn/ipx-771-uncensored-leak
and /cn/ipx-771-uncensored-leak are the same page).

id is the URL's last path segment, lower-cased (ipx-771,
ipx-771-uncensored-leak), so every page has its own. It always starts with
the video code, which is how code lookups find all pages of one code.

has_chinese_subtitle/is_uncensored_leak are MissAV's per-page flags (its
中文字幕/无码影片 badges), what actually tells a code's pages apart: the
suffix alone doesn't, ipx-771 itself is often the subtitled one. NULL means
not known yet (Jable pages, or MissAV rows not seen since the columns came).
"""

import json
import os
import re
import sqlite3
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from uav_downloader.core import config

DB_PATH = os.environ.get('RESOLVER_DB_PATH', '/data/resolver.db')

# Serve a cached resolved_url only if it stays valid at least this long, so
# playback that starts now doesn't die halfway through.
_EXPIRY_MARGIN = 30 * 60

_JABLE_EXPIRY_RE = re.compile(r'/hls/[^/]+/(\d{9,11})/')

_SITE_BY_MIRROR_KEY = {'jable': 'jabletv', 'missav': 'missav'}

# A MissAV video page behind a /dm<N>/ prefix; category pages (/dm278/
# chinese-subtitle, /dm4854130/1pondo) lack the "-<digit>" a video slug has.
_MISSAV_DM_VIDEO_RE = re.compile(
    r'^/dm\d+(/(?:[a-z]{2}/)?[a-z0-9][a-z0-9_-]*[-_]\d[a-z0-9_-]*/?)$', re.I)

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS videos (
    url              TEXT PRIMARY KEY,
    site             TEXT NOT NULL,
    id               TEXT NOT NULL DEFAULT '',
    title            TEXT NOT NULL DEFAULT '',
    description      TEXT NOT NULL DEFAULT '',
    thumbnail        TEXT NOT NULL DEFAULT '',
    resolved_url     TEXT,
    resolved_expires INTEGER,
    headers          TEXT,
    -- 1 once the Jable->MissAV Chinese-title lookup has run (hit or miss),
    -- so a later re-resolve doesn't repeat it.
    title_checked    INTEGER NOT NULL DEFAULT 0,
    has_chinese_subtitle INTEGER,
    is_uncensored_leak   INTEGER,
    -- Set by remove(): the row is kept (listings won't re-add it) but the
    -- video is no longer returned anywhere except list_page(deleted=True).
    deleted_at       INTEGER,
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_videos_site_id ON videos (site, id);
CREATE INDEX IF NOT EXISTS idx_videos_created ON videos (created_at);

-- Full metadata from MissAV's Recombee item properties (see save_details),
-- one row per page like videos. actors/actresses/genres are JSON arrays.
-- resolved_url/resolved_expires/headers mirror videos' once a page scrape
-- has run for the URL.
CREATE TABLE IF NOT EXISTS video_details (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    site             TEXT NOT NULL,
    code             TEXT NOT NULL DEFAULT '',
    url              TEXT NOT NULL UNIQUE,
    description      TEXT NOT NULL DEFAULT '',
    title            TEXT NOT NULL DEFAULT '',
    title_cn         TEXT NOT NULL DEFAULT '',
    title_zh         TEXT NOT NULL DEFAULT '',
    has_chinese_subtitle INTEGER,
    has_english_subtitle INTEGER,
    is_uncensored_leak   INTEGER,
    actors           TEXT NOT NULL DEFAULT '[]',
    actresses        TEXT NOT NULL DEFAULT '[]',
    genres           TEXT NOT NULL DEFAULT '[]',
    duration         INTEGER,
    released_at      TEXT,
    type             TEXT,
    thumbnail        TEXT NOT NULL DEFAULT '',
    resolved_url     TEXT,
    resolved_expires INTEGER,
    headers          TEXT,
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL,
    deleted_at       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_video_details_site_code ON video_details (site, code);
CREATE INDEX IF NOT EXISTS idx_video_details_created ON video_details (created_at);
'''

# PRAGMA user_version: 1 = id is the URL slug (was the upper-case code),
# 2 = deleted_at column, 3 = has_chinese_subtitle/is_uncensored_leak columns,
# 4 = MissAV URLs without the /dm<N>/ prefix.
_SCHEMA_VERSION = 4

VERSION_FLAGS = ('has_chinese_subtitle', 'is_uncensored_leak')

_init_lock = threading.Lock()
_initialized = False


def _connect() -> sqlite3.Connection:
    global _initialized
    if not _initialized:
        with _init_lock:
            if not _initialized:
                os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute('PRAGMA journal_mode=WAL')
                    conn.executescript(_SCHEMA)
                    _migrate(conn)
                _initialized = True
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate(conn: sqlite3.Connection):
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version < 1:
        rows = conn.execute('SELECT url FROM videos').fetchall()
        conn.executemany('UPDATE videos SET id = ? WHERE url = ?',
                         [(url_slug(url), url) for (url,) in rows])
    # A new DB already got these columns from _SCHEMA.
    columns = {row[1] for row in conn.execute('PRAGMA table_info(videos)')}
    if version < 2 and 'deleted_at' not in columns:
        conn.execute('ALTER TABLE videos ADD COLUMN deleted_at INTEGER')
    if version < 3:
        for flag in VERSION_FLAGS:
            if flag not in columns:
                conn.execute(f'ALTER TABLE videos ADD COLUMN {flag} INTEGER')
        # Only a suffix is certain; the rest stays unknown until a listing
        # or detail scrape sees the page again.
        conn.execute("UPDATE videos SET has_chinese_subtitle = 1 WHERE site = 'missav'"
                     " AND (id LIKE '%-chinese-subtitle' OR id LIKE '%-chinese-subtitles')")
        conn.execute("UPDATE videos SET is_uncensored_leak = 1 WHERE site = 'missav'"
                     " AND id LIKE '%-uncensored-leak'")
    if version < 4:
        _merge_missav_dm_rows(conn)
    conn.execute(f'PRAGMA user_version = {_SCHEMA_VERSION}')


def _merge_missav_dm_rows(conn: sqlite3.Connection):
    """Move /dm<N>/ rows to their canonical URL, merged into the row already
    stored there if any (it keeps what it has, gaps filled from the other)."""
    rows = conn.execute(
        "SELECT url, thumbnail, resolved_url, resolved_expires, headers,"
        " title_checked, has_chinese_subtitle, is_uncensored_leak, deleted_at,"
        " created_at, updated_at FROM videos"
        " WHERE site = 'missav' AND url LIKE 'https://missav.ai/dm%'").fetchall()
    for (url, *values) in rows:
        canonical, _ = normalize_url(url)
        if canonical == url:
            continue
        if conn.execute('SELECT 1 FROM videos WHERE url = ?', (canonical,)).fetchone():
            (thumbnail, resolved_url, resolved_expires, headers, title_checked,
             cn_sub, uncensored, deleted_at, created_at, updated_at) = values
            conn.execute(
                """UPDATE videos SET
                       thumbnail = CASE WHEN thumbnail = '' THEN ? ELSE thumbnail END,
                       resolved_expires = CASE WHEN resolved_url IS NULL
                                               THEN ? ELSE resolved_expires END,
                       headers = CASE WHEN resolved_url IS NULL THEN ? ELSE headers END,
                       resolved_url = COALESCE(resolved_url, ?),
                       title_checked = MAX(title_checked, ?),
                       has_chinese_subtitle = COALESCE(has_chinese_subtitle, ?),
                       is_uncensored_leak = COALESCE(is_uncensored_leak, ?),
                       deleted_at = COALESCE(deleted_at, ?),
                       created_at = MIN(created_at, ?),
                       updated_at = MAX(updated_at, ?)
                   WHERE url = ?""",
                (thumbnail, resolved_expires, headers, resolved_url, title_checked,
                 cn_sub, uncensored, deleted_at, created_at, updated_at, canonical))
            conn.execute('DELETE FROM videos WHERE url = ?', (url,))
        else:
            conn.execute('UPDATE videos SET url = ? WHERE url = ?', (canonical, url))


def url_slug(url: str) -> str:
    """A page's id: the last path segment, lower-cased."""
    return urlsplit((url or '').strip()).path.rstrip('/').rsplit('/', 1)[-1].lower()


def normalize_url(url: str) -> tuple[str, str]:
    """Return (canonical_url, site) — mirror hosts mapped to the main one."""
    parts = urlsplit((url or '').strip())
    host = parts.netloc.lower()
    if host.startswith('www.'):
        host = host[4:]
    for key, mirrors in config.MIRRORS.items():
        if host in mirrors:
            site = _SITE_BY_MIRROR_KEY.get(key, key)
            path = parts.path
            if site == 'missav':
                match = _MISSAV_DM_VIDEO_RE.match(path)
                if match:
                    path = match.group(1)
            return urlunsplit(('https', mirrors[0], path, parts.query, '')), site
    return urlunsplit(('https', host, parts.path, parts.query, '')), host


def _flag(value) -> int | None:
    return None if value is None else int(bool(value))


def resolved_url_expiry(resolved_url: str) -> int | None:
    match = _JABLE_EXPIRY_RE.search(resolved_url or '')
    return int(match.group(1)) if match else None


def save_listing(site: str, videos: list[dict]):
    """Insert listing rows that aren't known yet; existing rows are left alone
    (their title may already be the translated one)."""
    now = int(time.time())
    rows = []
    for v in videos:
        if not v.get('url'):
            continue
        url, _ = normalize_url(v['url'])
        rows.append((url, site, url_slug(url), v.get('title', ''),
                     v.get('description', ''), v.get('thumbnail', ''),
                     _flag(v.get('has_chinese_subtitle')),
                     _flag(v.get('is_uncensored_leak')), now, now))
    if not rows:
        return
    with _connect() as conn:
        conn.executemany(
            'INSERT OR IGNORE INTO videos (url, site, id, title, description,'
            ' thumbnail, has_chinese_subtitle, is_uncensored_leak, created_at,'
            ' updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            rows)
        # Fill a thumbnail the row was first stored without, and take the
        # listing's flags (the site's current view of the page) when it has them.
        conn.executemany(
            "UPDATE videos SET thumbnail = CASE WHEN thumbnail = '' THEN ? ELSE thumbnail END,"
            ' has_chinese_subtitle = COALESCE(?, has_chinese_subtitle),'
            ' is_uncensored_leak = COALESCE(?, is_uncensored_leak)'
            ' WHERE url = ?',
            [(r[5], r[6], r[7], r[0]) for r in rows])


def get_many(urls: list[str]) -> dict[str, dict]:
    """Stored rows for the given (un-normalised) URLs, keyed by the input URL."""
    by_norm = {normalize_url(u)[0]: u for u in urls if u}
    if not by_norm:
        return {}
    result = {}
    with _connect() as conn:
        keys = list(by_norm)
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            cur = conn.execute(
                f'SELECT * FROM videos WHERE url IN ({",".join("?" * len(chunk))})',
                chunk)
            for row in cur:
                result[by_norm[row['url']]] = dict(row)
    return result


def get(url: str) -> dict | None:
    return get_many([url]).get(url)


def _like_escape(text: str) -> str:
    return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _compact(column: str) -> str:
    """*column* with its separators dropped, so "ipx789" / "IPX 789" find
    ipx-789."""
    return f"REPLACE(REPLACE(LOWER({column}), '-', ''), '_', '')"


def list_page(page: int, size: int, site: str | None = None,
              deleted: bool = False,
              search: str | None = None) -> tuple[list[dict], int]:
    """One page of stored rows, newest first, plus their count: the live
    ones, or with *deleted* the removed ones (most recently removed first).
    *search* matches the code anywhere in the id (ipx -> ipx-789,
    ipx-789-chinese-subtitle ...) or the title; exact and prefix code
    matches come first."""
    return _list_rows('videos', 'id', ('title',),
                      page, size, site, deleted, search)


def _list_rows(table: str, code_column: str, title_columns: tuple[str, ...],
               page: int, size: int, site: str | None, deleted: bool,
               search: str | None) -> tuple[list[dict], int]:
    if deleted:
        where, order = 'WHERE deleted_at IS NOT NULL', 'deleted_at DESC, url'
    else:
        where, order = 'WHERE deleted_at IS NULL', 'created_at DESC, url'
    args, order_args = [], []
    if site:
        where, args = where + ' AND site = ?', [site]
    search = (search or '').strip()
    if search:
        code = re.sub(r'[^0-9a-z]', '', search.lower())
        conds = [f"{column} LIKE ? ESCAPE '\\'" for column in title_columns]
        args.extend([f'%{_like_escape(search)}%'] * len(title_columns))
        if code:
            compact = _compact(code_column)
            conds.append(f'{compact} LIKE ?')
            args.append(f'%{code}%')
            order = (f'CASE WHEN {compact} = ? THEN 0'
                     f' WHEN {compact} LIKE ? THEN 1 ELSE 2 END, {order}')
            order_args = [code, f'{code}%']
        where += f' AND ({" OR ".join(conds)})'
    with _connect() as conn:
        total = conn.execute(f'SELECT COUNT(*) FROM {table} {where}', args).fetchone()[0]
        cur = conn.execute(
            f'SELECT * FROM {table} {where} ORDER BY {order}'
            ' LIMIT ? OFFSET ?', [*args, *order_args, size, (page - 1) * size])
        return [dict(row) for row in cur], total


def _remove_row(table: str, url: str) -> bool:
    canonical, _ = normalize_url(url)
    with _connect() as conn:
        cur = conn.execute(
            f'UPDATE {table} SET deleted_at = ? WHERE url = ? AND deleted_at IS NULL',
            (int(time.time()), canonical))
        return cur.rowcount > 0


def remove(url: str) -> bool:
    """Soft-delete a row; False if the URL isn't stored or already removed."""
    return _remove_row('videos', url)


def is_removed(row: dict | None) -> bool:
    return bool(row and row.get('deleted_at') is not None)


def has_detail(row: dict | None) -> bool:
    """Whether the page was scraped at least once (resolved_url may have
    expired since). Listing-only rows have no resolved_url."""
    return bool(row and row.get('resolved_url'))


def has_valid_resolved_url(row: dict | None) -> bool:
    if not row or not row.get('resolved_url'):
        return False
    expires = row.get('resolved_expires')
    return expires is None or expires - time.time() > _EXPIRY_MARGIN


def row_headers(row: dict) -> dict:
    try:
        return json.loads(row.get('headers') or '{}') or {}
    except ValueError:
        return {}


def save_detail(url: str, *, title: str, description: str,
                thumbnail: str, resolved_url: str, headers: dict,
                title_checked: bool, has_chinese_subtitle: bool | None = None,
                is_uncensored_leak: bool | None = None):
    """Store a scraped page. A flag left None keeps what the row has."""
    canonical, site = normalize_url(url)
    now = int(time.time())
    with _connect() as conn:
        conn.execute(
            '''INSERT INTO videos (url, site, id, title, description, thumbnail,
                   resolved_url, resolved_expires, headers, title_checked,
                   has_chinese_subtitle, is_uncensored_leak,
                   created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                   id = excluded.id,
                   title = excluded.title,
                   description = excluded.description,
                   thumbnail = CASE WHEN videos.thumbnail = ''
                                    THEN excluded.thumbnail ELSE videos.thumbnail END,
                   resolved_url = excluded.resolved_url,
                   resolved_expires = excluded.resolved_expires,
                   headers = excluded.headers,
                   title_checked = MAX(videos.title_checked, excluded.title_checked),
                   has_chinese_subtitle = COALESCE(excluded.has_chinese_subtitle,
                                                   videos.has_chinese_subtitle),
                   is_uncensored_leak = COALESCE(excluded.is_uncensored_leak,
                                                 videos.is_uncensored_leak),
                   updated_at = excluded.updated_at''',
            (canonical, site, url_slug(canonical), title, description, thumbnail,
             resolved_url, resolved_url_expiry(resolved_url),
             json.dumps(headers or {}), int(title_checked),
             _flag(has_chinese_subtitle), _flag(is_uncensored_leak), now, now))
        conn.execute(
            'UPDATE video_details SET resolved_url = ?, resolved_expires = ?,'
            ' headers = ?, updated_at = ? WHERE url = ?',
            (resolved_url, resolved_url_expiry(resolved_url),
             json.dumps(headers or {}), now, canonical))


# Ids only use [a-z0-9_-], so [code, code + '.') holds exactly code and
# code-*; as one range it stays on the (site, id) index, unlike LIKE or OR.
_CODE_RANGE = 'id >= ? AND id < ?'


def find_by_code(site: str, code: str) -> list[dict]:
    """Rows for a video code, including variant pages whose id carries a
    suffix (sone-001-chinese-subtitle, ...)."""
    code = code.lower()
    with _connect() as conn:
        cur = conn.execute(
            f'SELECT * FROM videos WHERE site = ? AND {_CODE_RANGE}'
            ' AND deleted_at IS NULL ORDER BY created_at, url',
            (site, code, code + '.'))
        return [dict(row) for row in cur]


def translated_title_for(site: str, code: str) -> str | None:
    """A Chinese title already found for this code on any of its pages (e.g.
    ipx-771-c when resolving ipx-771), or None."""
    code = code.lower()
    with _connect() as conn:
        row = conn.execute(
            f'SELECT title FROM videos WHERE site = ? AND {_CODE_RANGE}'
            ' AND title_checked = 1 ORDER BY updated_at DESC LIMIT 1',
            (site, code, code + '.')).fetchone()
    return row['title'] if row else None


# ── video_details ────────────────────────────────────────────────────────
# Filled by the full related crawl from Recombee's item properties, which
# only exist for recommended items: a seed itself gets a row once some other
# video recommends it. Metadata is refreshed whenever an item is seen again
# (Recombee's current view); resolved_url comes from save_detail.

DETAIL_FLAGS = ('has_chinese_subtitle', 'has_english_subtitle', 'is_uncensored_leak')
DETAIL_LISTS = ('actors', 'actresses', 'genres')

_DETAIL_COLUMNS = ('site', 'code', 'url', 'description', 'title', 'title_cn',
                   'title_zh', *DETAIL_FLAGS, *DETAIL_LISTS, 'duration',
                   'released_at', 'type', 'thumbnail')
# What a re-seen item overwrites: everything but its identity.
_DETAIL_REFRESHED = [c for c in _DETAIL_COLUMNS if c not in ('site', 'url')]

_VARIANT_SUFFIX_RE = re.compile(
    r'-(?:uncensored-leak|chinese-subtitles?|english-subtitles?)$')


def video_code(slug: str) -> str:
    """The code of a page slug, variant suffixes dropped (IPX-771 for
    ipx-771-uncensored-leak)."""
    slug = (slug or '').lower()
    while True:
        stripped = _VARIANT_SUFFIX_RE.sub('', slug)
        if stripped == slug:
            return slug.upper()
        slug = stripped


def _string_list(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        value = [value]
    return [str(v).strip() for v in value if v is not None and str(v).strip()]


def _duration_seconds(value) -> int | None:
    """Seconds from a number or an "[h:]mm:ss" string; None if neither."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    parts = str(value).strip().split(':')
    if not all(p.isdigit() for p in parts) or len(parts) > 3:
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def _text(value) -> str:
    return '' if value is None else str(value).strip()


def save_details(site: str, items: list[dict]) -> list[str]:
    """Upsert video_details rows (keys as in _DETAIL_COLUMNS; lists as
    Python lists) and return the canonical URLs that were new. A new row
    takes the resolved_url videos already has for its URL."""
    now = int(time.time())
    rows = {}
    for item in items:
        if not item.get('url'):
            continue
        url, _ = normalize_url(item['url'])
        row = {
            'site': site,
            'code': item.get('code') or video_code(url_slug(url)),
            'url': url,
            **{c: _text(item.get(c))
               for c in ('description', 'title', 'title_cn', 'title_zh', 'thumbnail')},
            **{c: _flag(item.get(c)) for c in DETAIL_FLAGS},
            **{c: json.dumps(_string_list(item.get(c)), ensure_ascii=False)
               for c in DETAIL_LISTS},
            'duration': _duration_seconds(item.get('duration')),
            'released_at': _text(item.get('released_at')) or None,
            'type': _text(item.get('type')) or None,
        }
        rows[url] = tuple(row[c] for c in _DETAIL_COLUMNS) + (now, now)
    if not rows:
        return []
    columns = ', '.join(_DETAIL_COLUMNS)
    updates = ', '.join(f'{c} = excluded.{c}' for c in _DETAIL_REFRESHED)
    with _connect() as conn:
        urls = list(rows)
        existing = set()
        for i in range(0, len(urls), 500):
            chunk = urls[i:i + 500]
            existing.update(r[0] for r in conn.execute(
                f'SELECT url FROM video_details WHERE url IN ({",".join("?" * len(chunk))})',
                chunk))
        conn.executemany(
            f'INSERT INTO video_details ({columns}, created_at, updated_at)'
            f' VALUES ({", ".join("?" * (len(_DETAIL_COLUMNS) + 2))})'
            f' ON CONFLICT(url) DO UPDATE SET {updates},'
            ' updated_at = excluded.updated_at',
            list(rows.values()))
        new = [url for url in urls if url not in existing]
        for url in new:
            known = conn.execute(
                'SELECT resolved_url, resolved_expires, headers FROM videos'
                ' WHERE url = ? AND resolved_url IS NOT NULL', (url,)).fetchone()
            if known:
                conn.execute(
                    'UPDATE video_details SET resolved_url = ?, resolved_expires = ?,'
                    ' headers = ? WHERE url = ?', (*known, url))
    return new


def detail_row_lists(row: dict) -> dict:
    """actors/actresses/genres of a video_details row as lists."""
    result = {}
    for column in DETAIL_LISTS:
        try:
            result[column] = json.loads(row.get(column) or '[]')
        except ValueError:
            result[column] = []
    return result


def list_details_page(page: int, size: int, site: str | None = None,
                      deleted: bool = False,
                      search: str | None = None) -> tuple[list[dict], int]:
    """list_page for video_details: *search* matches the code or any of
    the three titles."""
    return _list_rows('video_details', 'code', ('title', 'title_cn', 'title_zh'),
                      page, size, site, deleted, search)


def remove_details(url: str) -> bool:
    """Soft-delete a video_details row; False if not stored or already removed."""
    return _remove_row('video_details', url)
