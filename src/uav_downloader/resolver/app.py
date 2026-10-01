#!/usr/bin/env python
# coding: utf-8
"""Thin HTTP front for the m3u8 resolve step.

A client (the Android UI) posts a video page URL here. This service runs
the same site-scraping code the desktop app uses to turn that page into a
playable m3u8/direct URL, then hands it straight to the remote download
server. The client never touches m3u8 URLs or the download server's
credentials.
"""

import hashlib
import hmac
import html
import logging
import os
import re
import threading
import time
from urllib.parse import quote, urlsplit, urlunsplit

import requests
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from uav_downloader import sites as M3U8Sites
from uav_downloader.core import remote_downloader
from uav_downloader.core.config import headers as _default_headers
from uav_downloader.sites.base import MirrorsBlockedError, fetch_with_mirrors
from uav_downloader.sites.jabletv import JableTVBrowser, SiteJableTV
from uav_downloader.sites.missav import MissAVBrowser, SiteMissAV
from uav_downloader.i18n.locales import set_lang
from uav_downloader.resolver import store
from uav_downloader.resolver.prefetch import ENABLED as PREFETCH_ENABLED, Prefetcher

# Category names come from the desktop app's i18n tables; the Android client
# is Chinese-only, so fix the language once here rather than per request.
set_lang('zh-Hans')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('uav_resolver')

RESOLVER_API_KEY = os.environ.get('RESOLVER_API_KEY', '')
if not RESOLVER_API_KEY:
    # This service is designed to sit on the public internet; refuse to boot
    # unauthenticated rather than silently accepting anonymous requests.
    raise RuntimeError(
        'RESOLVER_API_KEY is not set. Set it before starting this service '
        '(it is exposed to the public internet).')

app = FastAPI(title='UAV Resolver', version='1.0')


class ResolveRequest(BaseModel):
    url: str
    output_name: str | None = None


def _check_api_key(x_api_key: str | None):
    if x_api_key != RESOLVER_API_KEY:
        raise HTTPException(status_code=401, detail='invalid or missing X-API-Key')


def _url_candidates(raw_url: str) -> list[str]:
    """Site patterns were written for the desktop scraper's own canonical
    URLs (some require a trailing slash, none expect a query string). A
    human pasting or sharing a link from a phone browser is much less
    tidy, so try a few normalized variants before giving up."""
    url = raw_url.strip()
    split = urlsplit(url)
    bare = urlunsplit((split.scheme, split.netloc, split.path, '', ''))

    candidates = [url, bare]
    if bare.endswith('/'):
        candidates.append(bare.rstrip('/'))
    else:
        candidates.append(bare + '/')

    seen: set[str] = set()
    ordered = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def _create_site(url: str):
    for candidate in _url_candidates(url):
        job = M3U8Sites.CreateSite(candidate)
        if job is not None:
            return job
    return None


@app.get('/health')
def health():
    return {'ok': True}


def _version_flags(row: dict | None) -> dict:
    """has_chinese_subtitle/is_uncensored_leak of a stored row as JSON
    booleans; null while not known (see store)."""
    return {flag: None if not row or row.get(flag) is None else bool(row[flag])
            for flag in store.VERSION_FLAGS}


def _video_info(url: str) -> dict:
    """Title/id/description/thumbnail/resolved_url/headers for a video page.

    Served from the DB while its resolved_url is still valid; otherwise the
    page is scraped (Jable's Japanese titles swapped for MissAV's Chinese
    ones) and the result stored for next time."""
    try:
        row = store.get(url)
    except Exception:
        logger.exception('store read failed for %s', url)
        row = None
    if store.has_valid_resolved_url(row):
        return {
            'title': row['title'],
            'id': row['id'],
            'description': row['description'],
            'thumbnail': row['thumbnail'],
            'resolved_url': row['resolved_url'],
            'headers': store.row_headers(row),
            **_version_flags(row),
        }

    try:
        job = _create_site(url)
    except Exception as exc:
        logger.exception('CreateSite raised for %s', url)
        raise HTTPException(
            status_code=502, detail=f'resolve failed: {exc}') from exc

    if job is None:
        raise HTTPException(status_code=400, detail='unsupported url')
    if not job.is_url_vaildate():
        err = getattr(job, '_last_error', None)
        if isinstance(err, MirrorsBlockedError):
            raise HTTPException(status_code=502, detail=str(err))
        raise HTTPException(
            status_code=422, detail=f'parse failed: {err or "unknown error"}')

    resolved_url = job.raw_m3u8_url() or getattr(job, '_direct_url', None)
    if not resolved_url:
        raise HTTPException(
            status_code=422, detail='no playable URL found on that page')

    title = job.target_name() or ''
    code, description = _split_title(title, url)
    title_checked = False
    if isinstance(job, SiteJableTV) and code and _KANA_RE.search(title):
        title, title_checked = _jable_cn_title(code, title)
        _, description = _split_title(title, url)

    # MissAV pages only; None when the page didn't show them.
    flags = getattr(job, '_version_flags', None)
    cn_sub, uncensored = flags if flags else (None, None)
    info = {
        'title': title,
        'id': store.url_slug(url),
        'description': description,
        'thumbnail': getattr(job, '_imageUrl', None) or '',
        'resolved_url': resolved_url,
        'headers': getattr(job, '_extra_headers', {}) or {},
    }
    try:
        store.save_detail(
            url, title=title, description=description,
            thumbnail=info['thumbnail'], resolved_url=resolved_url,
            headers=info['headers'], title_checked=title_checked,
            has_chinese_subtitle=cn_sub, is_uncensored_leak=uncensored)
        # The row may know flags this scrape didn't find.
        row = store.get(url)
    except Exception:
        logger.exception('store write failed for %s', url)
        row = {'has_chinese_subtitle': cn_sub, 'is_uncensored_leak': uncensored}
    return {**info, **_version_flags(row)}


def _check_not_removed(url: str):
    try:
        removed = store.is_removed(store.get(url))
    except Exception:
        logger.exception('store read failed for %s', url)
        removed = False
    if removed:
        raise HTTPException(status_code=404, detail='video removed')


def _needs_detail(url: str) -> bool:
    # Only pages never scraped; an expired Jable URL is left for /api/detail
    # to refresh on demand rather than costing a background request.
    return not store.has_detail(store.get(url))


# Listings queue their videos here so /api/detail usually hits the DB.
_prefetcher = Prefetcher(_video_info, _needs_detail)


@app.post('/api/resolve')
def resolve(req: ResolveRequest, x_api_key: str | None = Header(default=None)):
    _check_api_key(x_api_key)

    url = (req.url or '').strip()
    if not url:
        raise HTTPException(status_code=400, detail='url is required')

    _check_not_removed(url)
    _prefetcher.user_activity()
    info = _video_info(url)
    resolved_url = info['resolved_url']
    # Named by id, not title: some titles are too long for a file name.
    output_name = req.output_name or f'{info["id"] or "video"}.mp4'

    try:
        remote_downloader.submit_task(resolved_url, output_name)
    except remote_downloader.RemoteDownloadError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    logger.info('resolved+submitted: %s -> %s', url, output_name)
    return {'ok': True, 'output_name': output_name, 'resolved_url': resolved_url}


class DownloadRequest(BaseModel):
    resolved_url: str
    output_name: str | None = None


@app.post('/api/download')
def download(req: DownloadRequest, x_api_key: str | None = Header(default=None)):
    """Submit an already-resolved URL straight to the remote download queue,
    skipping the scrape step - for a client that already has a fresh
    resolved_url from a prior /api/detail call on the same page and doesn't
    need (or want) to re-parse it just to download."""
    _check_api_key(x_api_key)

    resolved_url = (req.resolved_url or '').strip()
    if not resolved_url:
        raise HTTPException(status_code=400, detail='resolved_url is required')
    output_name = (req.output_name or '').strip() or 'video.mp4'

    try:
        remote_downloader.submit_task(resolved_url, output_name)
    except remote_downloader.RemoteDownloadError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    logger.info('submitted (pre-resolved) -> %s', output_name)
    return {'ok': True, 'output_name': output_name, 'resolved_url': resolved_url}


@app.get('/api/detail')
def detail(url: str, x_api_key: str | None = Header(default=None)):
    """Resolve a video page for in-app preview/playback only — unlike
    /api/resolve this never touches the remote download queue. Returns the
    playable URL plus whatever headers (Referer/Origin) the CDN requires,
    so the client's player can set them itself."""
    _check_api_key(x_api_key)

    url = (url or '').strip()
    if not url:
        raise HTTPException(status_code=400, detail='url is required')

    _check_not_removed(url)
    _prefetcher.user_activity()
    return {'ok': True, **_video_info(url)}


# ── Browse: category/search listings for the Android UI ───────────────
# Scoped to JableTV and MissAV for now — the two sites that currently
# resolve reliably. Scraping stays server-side on purpose (see MirrorsBlockedError
# handling below); the client only ever sees plain JSON.

_BROWSERS = {
    'jabletv': JableTVBrowser,
    'missav': MissAVBrowser,
}

_SITE_LABELS = {
    'jabletv': 'JableTV',
    'missav': 'MissAV',
}


def _get_browser(site: str):
    browser = _BROWSERS.get(site)
    if browser is None:
        raise HTTPException(status_code=404, detail=f'unknown site: {site}')
    return browser


# Category/tag lists barely change (JableTV's tag sidebar and MissAV's fixed
# category set are effectively static; only the video counts drift), so
# there's no need to re-scrape them on every app launch. One shared
# in-memory cache per site, refreshed monthly, cuts that down to roughly
# one fetch/site/month instead of one per Browse-tab open.
_CATEGORY_CACHE_TTL = 30 * 24 * 3600
_category_cache: dict[str, tuple[float, list]] = {}
_category_cache_lock = threading.Lock()


def _build_page_url(site: str, base_url: str, page: int) -> str:
    if page <= 1:
        return base_url
    if site == 'jabletv':
        if '?' in base_url:
            return f'{base_url}&from={page}'
        return f'{base_url.rstrip("/")}/?from={page}'
    return _BROWSERS[site].page_url(base_url, page)


def _search_base_url(site: str, query: str) -> str:
    q = quote(query, safe='')
    if site == 'jabletv':
        return f'https://jable.tv/search/?q={q}'
    return f'https://missav.ai/cn/search/{q}'


@app.get('/api/browse/sites')
def browse_sites(x_api_key: str | None = Header(default=None)):
    _check_api_key(x_api_key)
    return {'sites': [{'key': k, 'name': v} for k, v in _SITE_LABELS.items()]}


@app.get('/api/browse/{site}/categories')
def browse_categories(
        site: str, refresh: bool = False,
        x_api_key: str | None = Header(default=None)):
    _check_api_key(x_api_key)
    browser = _get_browser(site)

    if not refresh:
        with _category_cache_lock:
            cached = _category_cache.get(site)
        if cached and (time.time() - cached[0]) < _CATEGORY_CACHE_TTL:
            return {'categories': cached[1]}

    try:
        if site == 'missav':
            categories = browser.fetch_categories(lang='cn')
        else:
            categories = browser.fetch_categories()
            if site == 'jabletv':
                categories.append({
                    'name': '🏷️ 按標籤',
                    'url': f'{browser._url_root}/tags/',
                    'count': 0,
                    'section': True,
                })
                for group, tags in browser.SIDEBAR_TAGS.items():
                    for name, slug in tags:
                        categories.append({
                            'name': name,
                            'url': browser.tag_url(slug),
                            'count': 0,
                            'group': group,
                        })
    except MirrorsBlockedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception('category fetch failed for %s', site)
        raise HTTPException(
            status_code=502, detail=f'category fetch failed: {exc}') from exc

    with _category_cache_lock:
        _category_cache[site] = (time.time(), categories)
    return {'categories': categories}


# Both sites title listing cards as "<番号> <description>", e.g.
# "SONE-001 エロめっちゃ…" / "FC2-PPV-1234567 …". Listing pages carry no
# separate synopsis, so the description is the title with the code removed.
_TITLE_CODE_RE = re.compile(r'^\s*([A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+)\s+(.*)$', re.S)


def _split_title(title: str, url: str) -> tuple[str, str]:
    match = _TITLE_CODE_RE.match(title or '')
    if match and re.search(r'\d', match.group(1)):
        return match.group(1).upper(), match.group(2).strip()
    # No code prefix in the title: fall back to the URL slug.
    slug = url.rstrip('/').rsplit('/', 1)[-1].split('?')[0]
    code = slug.upper() if re.search(r'\d', slug) else ''
    return code, (title or '').strip()


# JableTV only has Chinese titles for part of its catalogue; untranslated
# entries show the Japanese original even in Chinese mode. MissAV's /cn/
# pages translate nearly everything, so /api/detail looks those codes up
# there once and keeps the outcome in the DB (a code's title never changes);
# listings then pick the translated title up from the DB.
_KANA_RE = re.compile(r'[぀-ヿ]')
_OG_TITLE_RE = re.compile(r'og:title"\s+content="([^"]+)"')


def _lookup_cn_title(code: str) -> str | None:
    """MissAV's Chinese title for *code*; '' if MissAV has none, None if the
    lookup failed transiently (Cloudflare/network) and should be retried."""
    try:
        resp, _host, reason = fetch_with_mirrors(
            MissAVBrowser._get_scraper(), f'https://missav.ai/cn/{code.lower()}',
            'missav', lambda r: getattr(r, 'status_code', 0) == 200
            and 'og:title' in r.text, timeout=10)
    except Exception:
        logger.warning('missav title lookup failed for %s', code, exc_info=True)
        return None
    if reason == 'blocked':
        return None

    title = ''
    if reason == 'ok':
        match = _OG_TITLE_RE.search(resp.text)
        if match:
            title = html.unescape(match.group(1)).strip()
    # Reject MissAV's generic "not found" page, and pages MissAV hasn't
    # translated either. MissAV appends " - <actress>", which is often kana
    # even on translated titles, so only the part before it is checked.
    if (not title.upper().startswith(code.upper())
            or _KANA_RE.search(title.rsplit(' - ', 1)[0])):
        title = ''
    return title


def _jable_cn_title(code: str, title: str) -> tuple[str, bool]:
    """(title to use, whether the MissAV lookup is settled for this code)."""
    try:
        known = store.translated_title_for('jabletv', code)
    except Exception:
        logger.exception('store read failed for %s', code)
        known = None
    if known is not None:
        return known or title, True
    cn_title = _lookup_cn_title(code)
    if cn_title is None:
        return title, False
    return cn_title or title, True


def _fetch_listing(site: str, url: str, related: bool = False,
                   from_user: bool = True, prefetch: bool = True) -> dict:
    if from_user:
        _prefetcher.user_activity()
    browser = _get_browser(site)
    try:
        videos = browser.fetch_related(url) if related else browser.fetch_page(url)
    except MirrorsBlockedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception('listing fetch failed for %s: %s', site, url)
        raise HTTPException(
            status_code=502, detail=f'listing fetch failed: {exc}') from exc
    return {'videos': _annotate_listing(site, videos, prefetch)}


def _annotate_listing(site: str, videos: list[dict],
                      prefetch: bool = True) -> list[dict]:
    """Fill id/description for listing cards, record them in the DB and
    apply what the DB already knows. *prefetch* queues their details."""
    for video in videos:
        # The DB's form of the URL, so the client, the prefetch queue and the
        # crawl all see one URL per page (MissAV links it with and without
        # a /dm<N>/ prefix).
        if video.get('url'):
            video['url'] = store.normalize_url(video['url'])[0]
        video['id'] = store.url_slug(video.get('url', ''))
        if 'description' not in video:
            _, video['description'] = _split_title(
                video.get('title', ''), video.get('url', ''))
    # Record new videos, and prefer what the DB already knows (e.g. a Chinese
    # title found by an earlier /api/detail) over the raw listing text.
    try:
        store.save_listing(site, videos)
        known = store.get_many([v.get('url', '') for v in videos])
    except Exception:
        logger.exception('store update failed for %s listing', site)
        known = {}
    # Removed videos stay out of every listing (and so out of the crawl).
    videos = [v for v in videos if not store.is_removed(known.get(v.get('url', '')))]
    for video in videos:
        row = known.get(video.get('url', ''))
        if row and row['title']:
            video['title'] = row['title']
            video['description'] = row['description']
        if row:
            video.update(_version_flags(row))
        else:
            video.update(_version_flags(video))
    if prefetch:
        _prefetcher.enqueue([
            v['url'] for v in videos
            if v.get('url') and not store.has_detail(known.get(v['url']))])
    return videos


@app.get('/api/browse/{site}/videos')
def browse_videos(
        site: str, category_url: str, page: int = 1,
        x_api_key: str | None = Header(default=None)):
    _check_api_key(x_api_key)
    _get_browser(site)
    url = _build_page_url(site, category_url, max(1, page))
    result = _fetch_listing(site, url)
    result['page'] = page
    return result


_CODE_QUERY_RE = re.compile(r'^[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+$')

# A code answered from the DB still gets one background site search, which
# stores any variant page (中文字幕/无码…) the DB hasn't seen yet. Variants
# rarely appear later, so the same code is re-searched at most once a day.
_CODE_REFRESH_TTL = 24 * 3600
_code_refreshed: dict[tuple[str, str], float] = {}
_code_refreshed_lock = threading.Lock()


def _refresh_code_search(site: str, code: str):
    key = (site, code.lower())
    with _code_refreshed_lock:
        last = _code_refreshed.get(key)
    if last is not None and time.monotonic() - last < _CODE_REFRESH_TTL:
        return
    url = _search_base_url(site, code)

    def run():
        _fetch_listing(site, url, from_user=False)
        # Marked only once it succeeded: a search dropped from the queue by
        # a block, or one that failed, is retried on the next lookup.
        with _code_refreshed_lock:
            _code_refreshed[key] = time.monotonic()

    _prefetcher.enqueue_call(f'search:{site}:{key[1]}', run)


def _search_cached_code(site: str, query: str, page: int) -> list[dict]:
    """Stored videos for a search that is just a video code, else []."""
    if not (_CODE_QUERY_RE.match(query) and re.search(r'\d', query)):
        return []
    try:
        rows = store.find_by_code(site, query)
    except Exception:
        logger.exception('store code lookup failed for %s', query)
        return []
    videos = [{
        'url': row['url'],
        'title': row['title'],
        'thumbnail': row['thumbnail'],
        'duration': '',
        'id': row['id'],
        'description': row['description'],
        **_version_flags(row),
    } for row in rows]
    if rows and page <= 1:
        _prefetcher.enqueue([row['url'] for row in rows if not store.has_detail(row)])
        _refresh_code_search(site, query)
    return videos


@app.get('/api/browse/{site}/search')
def browse_search(
        site: str, q: str, page: int = 1,
        x_api_key: str | None = Header(default=None)):
    _check_api_key(x_api_key)
    _get_browser(site)
    query = (q or '').strip()
    if not query:
        raise HTTPException(status_code=400, detail='q is required')
    cached = _search_cached_code(site, query, page)
    if cached:
        # A code names one video, so the DB rows are answered straight away
        # (the background search above picks up variants for next time);
        # later pages are empty rather than falling through to the site.
        return {'videos': cached if page <= 1 else [], 'page': page}
    url = _build_page_url(site, _search_base_url(site, query), max(1, page))
    result = _fetch_listing(site, url)
    result['page'] = page
    return result


# ── MissAV related videos ───────────────────────────────────────────────
# MissAV's own frontend gets "related" recommendations from a third-party
# recommendation engine (Recombee) rather than anything in the page HTML.
# Token/db/host below are the ones MissAV's frontend itself uses (also
# documented publicly by the EchterAlsFake/missAV_api project) — there is no
# server-side scraping alternative for this particular feature.
_RECOMBEE_TOKEN = 'Ikkg568nlM51RHvldlPvc2GzZPE9R4XGzaH9Qj4zK9npbbbTly1gj9K4mgRn0QlV'
_RECOMBEE_DB = 'missav-default'
_RECOMBEE_HOST = 'https://client-rapi-missav.recombee.com'


def _recombee_url(path: str) -> str:
    ts = int(time.time())
    sep = '&' if '?' in path else '?'
    msg = f'/{_RECOMBEE_DB}{path}{sep}frontend_timestamp={ts}'
    sign = hmac.new(
        _RECOMBEE_TOKEN.encode(), msg.encode(), hashlib.sha1).hexdigest()
    return f'{_RECOMBEE_HOST}{msg}&frontend_sign={sign}'


def _missav_item_id(url: str) -> str | None:
    match = re.search(SiteMissAV.website_dirname_pattern, url)
    return match.group(1).lower() if match else None


def _recombee_recomms(data) -> list[dict]:
    if isinstance(data, list):
        first = data[0] if data else None
    elif isinstance(data, dict):
        responses = data.get('responses') or []
        first = responses[0] if responses else None
    else:
        first = None
    if not first:
        return []
    inner = first.get('json') or {}
    return inner.get('recomms') or []


def _parse_recombee_batch(data) -> list[dict]:
    videos = []
    for item in _recombee_recomms(data):
        item_id = item.get('id') or item.get('code')
        if not item_id:
            continue
        props = item.get('values', item)
        title = (props.get('title_cn') or props.get('full_title')
                  or props.get('title') or props.get('name') or '')
        code = item_id.upper()
        videos.append({
            'url': f'https://missav.ai/cn/{item_id}',
            'title': f'{code} {title}'.strip() if title else code,
            'description': title.strip(),
            'thumbnail': f'https://fourhoi.com/{item_id}/cover-t.jpg',
            'duration': '',
            # The same flags MissAV's own cards badge as 中文字幕/无码影片.
            **{flag: props[flag] if isinstance(props.get(flag), bool) else None
               for flag in store.VERSION_FLAGS},
        })
    return videos


def _related_videos(site: str, url: str, count: int = 12,
                    from_user: bool = True, prefetch: bool = True) -> list[dict]:
    """Annotated related videos for a video page; raises HTTPException (5xx
    when the fetch itself failed)."""
    if site == 'jabletv':
        # Jable renders its "猜你喜歡" block straight into the video page.
        return _fetch_listing(site, url, related=True, from_user=from_user,
                              prefetch=prefetch)['videos']
    if site != 'missav':
        return []

    data = _recombee_related(url, count, ['title_cn', 'duration', 'dm',
                                          *store.VERSION_FLAGS])
    return _annotate_listing(site, _parse_recombee_batch(data), prefetch)


def _recombee_related(url: str, count: int,
                      properties: list[str] | None) -> object:
    """Recombee's raw batch response for a MissAV page's recommendations,
    with *properties* of each item (None: all of them)."""
    item_id = _missav_item_id(url)
    if not item_id:
        raise HTTPException(
            status_code=400, detail='could not extract item id from url')

    params = {
        'targetUserId': 'anonymous',
        'count': count,
        'scenario': 'mobile-watch-next',
        'returnProperties': True,
        'cascadeCreate': True,
    }
    if properties is not None:
        params['includedProperties'] = properties
    return _recombee_batch(f'/recomms/items/{item_id}/items/', params,
                           'related', item_id)


def _recombee_search(query: str, count: int) -> object:
    """Recombee's raw batch response for a keyword search (what MissAV's own
    search box uses), with all properties of each item."""
    return _recombee_batch('/search/users/anonymous/items/', {
        'searchQuery': query,
        'count': count,
        'cascadeCreate': True,
        'returnProperties': True,
    }, 'search', query)


def _recombee_batch(path: str, params: dict, what: str, subject: str) -> object:
    body = {
        'requests': [{'method': 'POST', 'path': path, 'params': params}],
        'distinctRecomms': True,
    }
    try:
        resp = requests.post(_recombee_url('/batch/'), json=body, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.warning('recombee %s fetch failed for %s: %s', what, subject, exc)
        raise HTTPException(
            status_code=502, detail=f'{what} fetch failed: {exc}') from exc


def _parse_recombee_details(data) -> list[dict]:
    """video_details rows (see store.save_details) from a Recombee batch
    response fetched with all properties."""
    details = []
    for item in _recombee_recomms(data):
        item_id = item.get('id') or item.get('code')
        if not item_id:
            continue
        props = item.get('values', item)
        title_cn = props.get('title_cn') or ''
        details.append({
            'url': f'https://missav.ai/cn/{item_id}',
            'code': store.video_code(item_id),
            'description': props.get('description') or title_cn,
            'title': props.get('title') or '',
            'title_cn': title_cn,
            'title_zh': props.get('title_zh') or '',
            'thumbnail': f'https://fourhoi.com/{item_id}/cover-t.jpg',
            **{flag: props[flag] if isinstance(props.get(flag), bool) else None
               for flag in store.DETAIL_FLAGS},
            **{key: props.get(key) for key in (*store.DETAIL_LISTS, 'duration',
                                                'released_at', 'type')},
        })
    return details


def _related_full(url: str, count: int = 12) -> tuple[list[dict], list[str]]:
    """_related_videos for a MissAV page that also stores every item's full
    properties in video_details: (annotated videos, URLs new to that table)."""
    return _store_full(_recombee_related(url, count, None), url)


def _search_full(query: str, count: int = 12) -> tuple[list[dict], list[str]]:
    """_related_full for a Recombee keyword search."""
    return _store_full(_recombee_search(query, count), query)


def _store_full(data, subject: str) -> tuple[list[dict], list[str]]:
    videos = _annotate_listing('missav', _parse_recombee_batch(data), prefetch=False)
    # Only what survived annotation: removed videos stay out of this table too.
    live = {v['url'] for v in videos if v.get('url')}
    details = [d for d in _parse_recombee_details(data)
               if store.normalize_url(d['url'])[0] in live]
    try:
        new = store.save_details('missav', details)
    except Exception:
        logger.exception('store write failed for video details of %s', subject)
        raise HTTPException(status_code=503, detail='video details store failed')
    return videos, new


@app.get('/api/browse/{site}/related')
def browse_related(
        site: str, url: str, count: int = 12,
        x_api_key: str | None = Header(default=None)):
    _check_api_key(x_api_key)
    _get_browser(site)
    try:
        return {'videos': _related_videos(site, url, count)}
    except HTTPException as exc:
        # A failed Recombee call has always meant "no recommendations" here.
        if site == 'missav' and exc.status_code >= 500:
            return {'videos': []}
        raise


# ── Related crawl ───────────────────────────────────────────────────────
# Breadth-first walk over "related" from seed videos: every video found is
# stored, has its detail queued unless the DB already has it, and has its own
# related fetched in turn, until CRAWL_LIMIT details have been queued. All of it runs in the
# prefetcher's background queue, so it shares its pacing/backoff and waits
# whenever the user's own browsing has queued something.

CRAWL_LIMIT = int(os.environ.get('RESOLVER_CRAWL_LIMIT', '10000'))


class _Crawler:
    def __init__(self, prefix: str, full: bool = False):
        """With *full*, each related fetch asks Recombee for every property
        and stores them in video_details; the limit then counts videos new
        to that table (their page detail is still queued when missing). It
        is started from a Recombee keyword search (add_search) rather than
        a seed page: every video the search finds is stored and walked."""
        self._prefix = prefix
        self._full = full
        self._lock = threading.Lock()
        # Canonical URLs already visited this process, so the graph's many
        # cycles (A related to B related to A) aren't walked twice.
        self._seen: set[str] = set()
        self._remaining = 0
        self._found = 0

    def add_seed(self, site: str, url: str, limit: int) -> bool:
        """Start (or top up) the crawl from *url*: up to *limit* more detail
        fetches from now on. False if its related are already queued."""
        with self._lock:
            self._remaining = limit
            self._seen.add(store.normalize_url(url)[0])
        if _needs_detail(url):
            _prefetcher.enqueue_background(url)
        return self._schedule(site, url)

    def add_search(self, site: str, query: str, limit: int, count: int) -> bool:
        """Start (or top up) a full crawl from the *count* videos a Recombee
        search for *query* finds. False if that search is already queued."""
        with self._lock:
            self._remaining = limit
        return _prefetcher.enqueue_background(
            f'{self._prefix}search:{query}',
            lambda: self._visit(site, query, search_count=count))

    def status(self) -> dict:
        main, background = _prefetcher.pending()
        with self._lock:
            return {'found': self._found, 'remaining': self._remaining,
                    'seen': len(self._seen), 'queued': main,
                    'queued_background': background}

    def _schedule(self, site: str, url: str) -> bool:
        return _prefetcher.enqueue_background(
            f'{self._prefix}{url}', lambda: self._visit(site, url))

    def _visit(self, site: str, url: str, search_count: int | None = None):
        """Walk *url*'s related, or with *search_count* the results of a
        search for *url* (then a query)."""
        try:
            if search_count is not None:
                videos, new_details = _search_full(url, search_count)
            elif self._full:
                videos, new_details = _related_full(url)
            else:
                videos, new_details = _related_videos(
                    site, url, from_user=False, prefetch=False), []
        except HTTPException as exc:
            if exc.status_code >= 500:
                # Blocked/network: try this page again after the backoff
                # rather than losing its branch (or, for a seed, the crawl).
                if search_count is not None:
                    self.add_search(site, url, self._remaining, search_count)
                else:
                    self._schedule(site, url)
            raise
        new_details = set(new_details)

        # The listing rows were just saved, so "already in the DB" means a
        # detail was scraped before: those skip the detail queue and don't
        # count toward the limit, but their related are still walked.
        urls = [v['url'] for v in videos if v.get('url')]
        known = store.get_many(urls)
        fresh, walk = [], []
        with self._lock:
            for new_url in urls:
                key = store.normalize_url(new_url)[0]
                seen = key in self._seen
                has_detail = store.has_detail(known.get(new_url))
                # A URL is new to video_details only once, so in full mode it
                # counts even if already seen.
                counts = key in new_details if self._full else not (seen or has_detail)
                if counts:
                    if self._remaining <= 0:
                        continue
                    self._remaining -= 1
                    self._found += 1
                    if not (seen or has_detail):
                        fresh.append(new_url)
                if seen:
                    continue
                self._seen.add(key)
                walk.append(new_url)
            done = self._remaining <= 0
        for new_url in fresh:
            _prefetcher.enqueue_background(new_url)
        for new_url in walk:
            self._schedule(site, new_url)

        if done:
            # Enough found: pending related fetches would only find more.
            dropped = _prefetcher.drop_background(
                lambda key: key.startswith(self._prefix))
            logger.info('crawl reached its limit (%d found); dropped %d pending'
                        ' related fetches', self._found, dropped)


_crawler = _Crawler('crawl:')
_full_crawler = _Crawler('crawl-full:', full=True)


class CrawlRequest(BaseModel):
    url: str
    limit: int | None = None


def _add_crawl_seed(crawler: _Crawler, req: CrawlRequest) -> dict:
    url = (req.url or '').strip()
    if not url:
        raise HTTPException(status_code=400, detail='url is required')
    if not PREFETCH_ENABLED:
        raise HTTPException(status_code=409, detail='prefetch is disabled')
    site = store.normalize_url(url)[1]
    # MissAV only: its related come from Recombee, one light API call, while
    # a Jable page would cost two page loads (related + detail) per video.
    if site != 'missav':
        raise HTTPException(status_code=400, detail='only MissAV urls are supported')
    if not _missav_item_id(url):
        raise HTTPException(
            status_code=400, detail='could not extract item id from url')

    limit = CRAWL_LIMIT if req.limit is None else max(0, req.limit)
    seed_queued = crawler.add_seed(site, url, limit)
    logger.info('%sseed %s (limit %d)', crawler._prefix, url, limit)
    return {'ok': True, 'seed_queued': seed_queued, **crawler.status()}


@app.post('/api/crawl')
def crawl_seed(req: CrawlRequest, x_api_key: str | None = Header(default=None)):
    """Queue a seed video for the related crawl (see _Crawler)."""
    _check_api_key(x_api_key)
    return _add_crawl_seed(_crawler, req)


@app.get('/api/crawl')
def crawl_status(x_api_key: str | None = Header(default=None)):
    _check_api_key(x_api_key)
    return _crawler.status()


class FullCrawlRequest(BaseModel):
    query: str
    limit: int | None = None
    # How many results the starting search returns.
    count: int = 12


@app.post('/api/crawl/full')
def full_crawl_search(req: FullCrawlRequest,
                      x_api_key: str | None = Header(default=None)):
    """Like POST /api/crawl, but seeded by a MissAV (Recombee) search for
    *query* - a code, actress, title keyword... - instead of a page, and
    every video found gets its full Recombee properties stored in
    video_details (GET /api/video-details); limit counts videos new to that
    table."""
    _check_api_key(x_api_key)
    query = (req.query or '').strip()
    if not query:
        raise HTTPException(status_code=400, detail='query is required')
    if not PREFETCH_ENABLED:
        raise HTTPException(status_code=409, detail='prefetch is disabled')
    if not 1 <= req.count <= 100:
        raise HTTPException(status_code=400, detail='count must be 1-100')

    limit = CRAWL_LIMIT if req.limit is None else max(0, req.limit)
    search_queued = _full_crawler.add_search('missav', query, limit, req.count)
    logger.info('full crawl search %r (limit %d)', query, limit)
    return {'ok': True, 'search_queued': search_queued, **_full_crawler.status()}


@app.get('/api/crawl/full')
def full_crawl_status(x_api_key: str | None = Header(default=None)):
    _check_api_key(x_api_key)
    return _full_crawler.status()


@app.get('/api/videos')
def list_videos(
        page: int = Query(1, ge=1), size: int = Query(12, ge=1, le=24),
        site: str | None = None, deleted: bool = False,
        search: str | None = None,
        x_api_key: str | None = Header(default=None)):
    """Videos stored in the DB, newest first; with deleted=true, the ones
    removed via DELETE /api/videos instead (most recently removed first).
    search= fuzzy-matches the code (ipx -> ipx-789, ipx-789-uncensored-leak
    ...) or the title. Card fields only: resolved_url may have expired, so
    playback still goes through /api/detail."""
    _check_api_key(x_api_key)
    rows, total = store.list_page(page, size, site or None, deleted, search)
    videos = [{
        'url': row['url'],
        'site': row['site'],
        'id': row['id'],
        'title': row['title'],
        'description': row['description'],
        'thumbnail': row['thumbnail'],
        'has_detail': store.has_detail(row),
        **_version_flags(row),
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
        **({'deleted_at': row['deleted_at']} if deleted else {}),
    } for row in rows]
    return {'videos': videos, 'page': page, 'size': size, 'total': total,
            'pages': (total + size - 1) // size}


@app.delete('/api/videos')
def remove_video(url: str, x_api_key: str | None = Header(default=None)):
    """Soft-delete a stored video: the row is kept, but the video is no
    longer returned by any endpoint except /api/videos?deleted=true."""
    _check_api_key(x_api_key)
    url = (url or '').strip()
    if not url:
        raise HTTPException(status_code=400, detail='url is required')
    if not store.remove(url):
        raise HTTPException(status_code=404, detail='video not found')
    return {'ok': True}


def _detail_flags(row: dict) -> dict:
    return {flag: None if row.get(flag) is None else bool(row[flag])
            for flag in store.DETAIL_FLAGS}


@app.get('/api/video-details')
def list_video_details(
        page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
        site: str | None = None, deleted: bool = False,
        search: str | None = None,
        x_api_key: str | None = Header(default=None)):
    """Rows of video_details (filled by /api/crawl/full), newest first;
    deleted/search as for /api/videos (search also matches title_cn and
    title_zh). resolved_url is as last scraped and may have expired:
    resolved_valid says whether it is still usable."""
    _check_api_key(x_api_key)
    rows, total = store.list_details_page(page, size, site or None, deleted, search)
    videos = [{
        'id': row['id'],
        'site': row['site'],
        'code': row['code'],
        'url': row['url'],
        'description': row['description'],
        'title': row['title'],
        'title_cn': row['title_cn'],
        'title_zh': row['title_zh'],
        **_detail_flags(row),
        **store.detail_row_lists(row),
        'duration': row['duration'],
        'released_at': row['released_at'],
        'type': row['type'],
        'thumbnail': row['thumbnail'],
        'resolved_url': row['resolved_url'],
        'resolved_expires': row['resolved_expires'],
        'resolved_valid': store.has_valid_resolved_url(row),
        'headers': store.row_headers(row),
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
        'deleted_at': row['deleted_at'],
    } for row in rows]
    return {'videos': videos, 'page': page, 'size': size, 'total': total,
            'pages': (total + size - 1) // size}


@app.delete('/api/video-details')
def remove_video_details(url: str, x_api_key: str | None = Header(default=None)):
    """Soft-delete a video_details row (the videos row is left alone)."""
    _check_api_key(x_api_key)
    url = (url or '').strip()
    if not url:
        raise HTTPException(status_code=400, detail='url is required')
    if not store.remove_details(url):
        raise HTTPException(status_code=404, detail='video not found')
    return {'ok': True}


@app.get('/api/browse/thumb')
def browse_thumb(
        url: str, x_api_key: str | None = Header(default=None),
        api_key: str | None = None):
    # Image loaders (Coil, etc.) load plain URLs without custom headers, so
    # this one endpoint also accepts the key as a query param.
    if (x_api_key or api_key) != RESOLVER_API_KEY:
        raise HTTPException(status_code=401, detail='invalid or missing API key')
    try:
        resp = requests.get(url, headers=dict(_default_headers), timeout=12)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502, detail=f'thumbnail fetch failed: {exc}') from exc
    content_type = resp.headers.get('Content-Type', 'image/jpeg')
    return Response(content=resp.content, media_type=content_type)
