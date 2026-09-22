#!/usr/bin/env python
# coding: utf-8
"""Thin HTTP front for the m3u8 resolve step.

A client (the Android UI) posts a video page URL here. This service runs
the same site-scraping code the desktop app uses to turn that page into a
playable m3u8/direct URL, then hands it straight to the remote download
server. The client never touches m3u8 URLs or the download server's
credentials.
"""

import logging
import os
from urllib.parse import urlsplit, urlunsplit

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from uav_downloader import sites as M3U8Sites
from uav_downloader.core import remote_downloader
from uav_downloader.sites.base import MirrorsBlockedError

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


@app.post('/api/resolve')
def resolve(req: ResolveRequest, x_api_key: str | None = Header(default=None)):
    _check_api_key(x_api_key)

    url = (req.url or '').strip()
    if not url:
        raise HTTPException(status_code=400, detail='url is required')

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

    output_name = req.output_name or f'{job.target_name() or "video"}.mp4'

    try:
        remote_downloader.submit_task(resolved_url, output_name)
    except remote_downloader.RemoteDownloadError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    logger.info('resolved+submitted: %s -> %s', url, output_name)
    return {'ok': True, 'output_name': output_name, 'resolved_url': resolved_url}
