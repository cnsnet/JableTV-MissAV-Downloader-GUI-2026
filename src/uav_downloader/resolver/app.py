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
from fastapi import FastAPI, Header, HTTPException
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
from uav_downloader.resolver.prefetch import Prefetcher

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
            headers=info['headers'], title_checked=title_checked)
    except Exception:
        logger.exception('store write failed for %s', url)
    return info


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

    _prefetcher.user_activity()
    info = _video_info(url)
    resolved_url = info['resolved_url']
    output_name = req.output_name or f'{info["title"] or "video"}.mp4'

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
                   from_user: bool = True) -> dict:
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
    return {'videos': _annotate_listing(site, videos)}


def _annotate_listing(site: str, videos: list[dict]) -> list[dict]:
    """Fill id/description for listing cards, record them in the DB and
    apply what the DB already knows."""
    for video in videos:
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
    for video in videos:
        row = known.get(video.get('url', ''))
        if row and row['title']:
            video['title'] = row['title']
            video['description'] = row['description']
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


def _parse_recombee_batch(data) -> list[dict]:
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
    recomms = inner.get('recomms') or []
    videos = []
    for item in recomms:
        item_id = item.get('id') or item.get('code')
        if not item_id:
            continue
        props = item.get('values', item)
        title = (props.get('title_cn') or props.get('full_title')
                  or props.get('title') or props.get('name') or '')
        has_cn_sub = props.get('has_chinese_subtitle') is True
        id_part = f'{item_id.upper()}[中文字幕]' if has_cn_sub else item_id.upper()
        full_title = f'{id_part} {title}'.strip() if title else id_part
        # "<code>[中文字幕] …" doesn't fit _split_title, so fill this here.
        videos.append({
            'url': f'https://missav.ai/cn/{item_id}',
            'title': full_title,
            'description': title.strip(),
            'thumbnail': f'https://fourhoi.com/{item_id}/cover-t.jpg',
            'duration': '',
        })
    return videos


@app.get('/api/browse/{site}/related')
def browse_related(
        site: str, url: str, count: int = 12,
        x_api_key: str | None = Header(default=None)):
    _check_api_key(x_api_key)
    _get_browser(site)

    if site == 'jabletv':
        # Jable renders its "猜你喜歡" block straight into the video page.
        return _fetch_listing(site, url, related=True)
    if site != 'missav':
        return {'videos': []}

    item_id = _missav_item_id(url)
    if not item_id:
        raise HTTPException(
            status_code=400, detail='could not extract item id from url')

    body = {
        'requests': [{
            'method': 'POST',
            'path': f'/recomms/items/{item_id}/items/',
            'params': {
                'targetUserId': 'anonymous',
                'count': count,
                'scenario': 'mobile-watch-next',
                'returnProperties': True,
                'includedProperties': ['title_cn', 'duration', 'dm'],
                'cascadeCreate': True,
            },
        }],
        'distinctRecomms': True,
    }

    try:
        resp = requests.post(_recombee_url('/batch/'), json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning('recombee related fetch failed for %s: %s', item_id, exc)
        return {'videos': []}

    return {'videos': _annotate_listing(site, _parse_recombee_batch(data))}


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
