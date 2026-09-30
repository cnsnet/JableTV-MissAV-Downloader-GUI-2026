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
domain first, so jable.tv / fs1.app mirror links share a row.
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
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_videos_site_id ON videos (site, id);
'''

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
                _initialized = True
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_url(url: str) -> tuple[str, str]:
    """Return (canonical_url, site) — mirror hosts mapped to the main one."""
    parts = urlsplit((url or '').strip())
    host = parts.netloc.lower()
    if host.startswith('www.'):
        host = host[4:]
    for key, mirrors in config.MIRRORS.items():
        if host in mirrors:
            site = _SITE_BY_MIRROR_KEY.get(key, key)
            return urlunsplit(('https', mirrors[0], parts.path, parts.query, '')), site
    return urlunsplit(('https', host, parts.path, parts.query, '')), host


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
        rows.append((url, site, v.get('id', ''), v.get('title', ''),
                     v.get('description', ''), v.get('thumbnail', ''), now, now))
    if not rows:
        return
    with _connect() as conn:
        conn.executemany(
            'INSERT OR IGNORE INTO videos (url, site, id, title, description,'
            ' thumbnail, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            rows)
        # Fill a thumbnail the row was first stored without.
        conn.executemany(
            "UPDATE videos SET thumbnail = ? WHERE url = ? AND thumbnail = '' AND ? != ''",
            [(r[5], r[0], r[5]) for r in rows])


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


def save_detail(url: str, *, video_id: str, title: str, description: str,
                thumbnail: str, resolved_url: str, headers: dict,
                title_checked: bool):
    canonical, site = normalize_url(url)
    now = int(time.time())
    with _connect() as conn:
        conn.execute(
            '''INSERT INTO videos (url, site, id, title, description, thumbnail,
                   resolved_url, resolved_expires, headers, title_checked,
                   created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                   updated_at = excluded.updated_at''',
            (canonical, site, video_id, title, description, thumbnail,
             resolved_url, resolved_url_expiry(resolved_url),
             json.dumps(headers or {}), int(title_checked), now, now))


def find_by_code(site: str, code: str) -> list[dict]:
    """Rows for a video code, including variant pages whose id carries a
    suffix (SONE-001-CHINESE-SUBTITLE, ...). Ids only use [A-Z0-9_-], so
    [code, code + '.') holds exactly code and code-*; as one range it stays
    on the (site, id) index, unlike LIKE or an OR."""
    code = code.upper()
    with _connect() as conn:
        cur = conn.execute(
            'SELECT * FROM videos WHERE site = ? AND id >= ? AND id < ?'
            ' ORDER BY created_at, url',
            (site, code, code + '.'))
        return [dict(row) for row in cur]


def translated_title_for(site: str, video_id: str) -> str | None:
    """A Chinese title already found for this code on another row (e.g. the
    same Jable video reached through a different URL), or None."""
    with _connect() as conn:
        row = conn.execute(
            'SELECT title FROM videos WHERE site = ? AND id = ? AND title_checked = 1'
            ' ORDER BY updated_at DESC LIMIT 1', (site, video_id)).fetchone()
    return row['title'] if row else None
