#!/usr/bin/env python
# coding: utf-8
"""Background detail prefetch for the resolver service.

Listing endpoints hand the URLs they return to this queue; one daemon thread
resolves them one at a time (the same scrape /api/detail does) so the DB
already holds resolved_url by the time the user opens a video.

Getting rate-limited would cost far more than prefetch saves, so it runs
one page at a time: a random gap between pages, a pause while the user's
own requests are hitting the sites, and a steep backoff after a failure
(Cloudflare block, network). A JableTV page
also costs a MissAV title lookup, so one "page" may be two requests.
The newest listing goes to the front: the page the user is looking at right
now matters more than one they scrolled past, and when the queue is full the
oldest entries are dropped.
"""

import logging
import os
import random
import threading
import time
from collections import deque
from typing import Callable

logger = logging.getLogger('uav_resolver.prefetch')

ENABLED = os.environ.get('RESOLVER_PREFETCH', '1').strip().lower() not in (
    '0', 'false', 'no', 'off', '')
DELAY_MIN = float(os.environ.get('RESOLVER_PREFETCH_DELAY_MIN', '3'))
DELAY_MAX = float(os.environ.get('RESOLVER_PREFETCH_DELAY_MAX', '8'))
MAX_QUEUE = int(os.environ.get('RESOLVER_PREFETCH_QUEUE', '300'))

# Don't start a page within this many seconds of a user request.
_USER_QUIET = 10
# Extra wait after a failed fetch, doubled per consecutive failure.
_BACKOFF_BASE = 5 * 60
_BACKOFF_MAX = 60 * 60


class Prefetcher:
    def __init__(self, fetch: Callable[[str], object],
                 needs_fetch: Callable[[str], bool]):
        """*fetch* resolves and stores one URL (raising on failure);
        *needs_fetch* re-checks just before fetching, since the user may
        have opened the video in the meantime."""
        self._fetch = fetch
        self._needs_fetch = needs_fetch
        self._queue: deque[str] = deque()
        self._queued: set[str] = set()
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self._last_user = 0.0

    def user_activity(self):
        """Called on each user request that hits a site."""
        self._last_user = time.monotonic()

    def enqueue(self, urls: list[str]):
        if not ENABLED:
            return
        with self._cond:
            # Reversed appendleft keeps the listing's own order at the front.
            for url in reversed(urls):
                if not url:
                    continue
                if url in self._queued:
                    self._queue.remove(url)
                self._queue.appendleft(url)
                self._queued.add(url)
            while len(self._queue) > MAX_QUEUE:
                self._queued.discard(self._queue.pop())
            self._cond.notify()
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name='detail-prefetch', daemon=True)
                self._thread.start()

    def _next(self) -> str:
        with self._cond:
            while not self._queue:
                self._cond.wait()
            url = self._queue.popleft()
            self._queued.discard(url)
            return url

    def _wait_for_quiet(self):
        while True:
            left = self._last_user + _USER_QUIET - time.monotonic()
            if left <= 0:
                return
            time.sleep(left + random.uniform(0, 3))

    def _gap(self):
        time.sleep(random.uniform(DELAY_MIN, max(DELAY_MIN, DELAY_MAX)))

    def _run(self):
        failures = 0
        while True:
            url = self._next()
            try:
                if not self._needs_fetch(url):
                    continue
                self._wait_for_quiet()
                self._fetch(url)
                failures = 0
                logger.info('prefetched %s', url)
            except Exception as exc:
                if getattr(exc, 'status_code', 500) >= 500:
                    failures += 1
                    backoff = min(_BACKOFF_BASE * 2 ** (failures - 1), _BACKOFF_MAX)
                    logger.warning('prefetch failed for %s (%s); backing off %ds',
                                   url, getattr(exc, 'detail', exc), backoff)
                    time.sleep(backoff)
                else:
                    # The page itself can't be parsed; not a sign of blocking.
                    logger.info('prefetch skipped %s: %s',
                                url, getattr(exc, 'detail', exc))
            self._gap()
