#!/usr/bin/env python
# coding: utf-8
"""Background detail prefetch for the resolver service.

Listing endpoints hand the URLs they return to this queue; one daemon thread
resolves them one at a time (the same scrape /api/detail does) so the DB
already holds resolved_url by the time the user opens a video.

Getting rate-limited would cost far more than prefetch saves, so it runs
one page at a time: a random gap between pages, a pause while the user's
own requests are hitting the sites, and a steep backoff after a failure
(Cloudflare block, network). A block also drops everything still queued:
fetch_with_mirrors has already tried every mirror by then, so pressing on
would only add requests; the next listing the user opens re-queues its own
videos, which run once the backoff is over. A JableTV page
also costs a MissAV title lookup, so one "page" may be two requests.
The newest listing goes to the front: the page the user is looking at right
now matters more than one they scrolled past, and when the queue is full the
oldest entries are dropped.

Besides page URLs the queue takes arbitrary jobs (enqueue_call), e.g. the
site search that refreshes a code answered from the DB, so every background
request shares the same pacing.

A second, background queue (enqueue_background) holds long-running work such
as the related-video crawl. It only runs while the main queue is empty, is not
capped by RESOLVER_PREFETCH_QUEUE and survives a block (it just waits out the
backoff): its owner bounds its size, and nothing would re-queue it.
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
        self._calls: dict[str, Callable[[], object]] = {}
        self._background: deque[str] = deque()
        self._bg_queued: set[str] = set()
        self._bg_calls: dict[str, Callable[[], object]] = {}
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self._last_user = 0.0

    def user_activity(self):
        """Called on each user request that hits a site."""
        self._last_user = time.monotonic()

    def enqueue(self, urls: list[str]):
        """Queue page URLs for *fetch*, ahead of everything already queued."""
        if not ENABLED:
            return
        with self._cond:
            # Reversed appendleft keeps the listing's own order at the front.
            for url in reversed(urls):
                if url:
                    self._push_front(url)
            self._wake()

    def enqueue_call(self, key: str, call: Callable[[], object]):
        """Queue a job at the front; a pending job with the same *key* is
        replaced. It may raise like *fetch* does, with the same backoff."""
        if not ENABLED:
            return
        with self._cond:
            self._push_front(key)
            self._calls[key] = call
            self._wake()

    def enqueue_background(self, key: str,
                           call: Callable[[], object] | None = None) -> bool:
        """Queue a page URL (*call* None) or a job at the back of the
        background queue; False if it is already queued."""
        if not ENABLED:
            return False
        with self._cond:
            if key in self._queued or key in self._bg_queued:
                return False
            self._background.append(key)
            self._bg_queued.add(key)
            if call is not None:
                self._bg_calls[key] = call
            self._wake()
            return True

    def drop_background(self, predicate: Callable[[str], bool]) -> int:
        """Remove pending background entries whose key matches."""
        with self._cond:
            keep = deque(k for k in self._background if not predicate(k))
            dropped = len(self._background) - len(keep)
            for key in self._background:
                if predicate(key):
                    self._bg_queued.discard(key)
                    self._bg_calls.pop(key, None)
            self._background = keep
            return dropped

    def pending(self) -> tuple[int, int]:
        """(main, background) queue lengths."""
        with self._cond:
            return len(self._queue), len(self._background)

    def _push_front(self, key: str):
        if key in self._queued:
            self._queue.remove(key)
        self._queue.appendleft(key)
        self._queued.add(key)

    def _wake(self):
        while len(self._queue) > MAX_QUEUE:
            key = self._queue.pop()
            self._queued.discard(key)
            self._calls.pop(key, None)
        self._cond.notify()
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name='detail-prefetch', daemon=True)
            self._thread.start()

    def _next(self) -> tuple[str, Callable[[], object] | None]:
        with self._cond:
            while not self._queue and not self._background:
                self._cond.wait()
            if self._queue:
                key = self._queue.popleft()
                self._queued.discard(key)
                return key, self._calls.pop(key, None)
            key = self._background.popleft()
            self._bg_queued.discard(key)
            return key, self._bg_calls.pop(key, None)

    def _clear(self) -> int:
        with self._cond:
            dropped = len(self._queue)
            self._queue.clear()
            self._queued.clear()
            self._calls.clear()
            return dropped

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
            url, call = self._next()
            try:
                if call is None and not self._needs_fetch(url):
                    continue
                self._wait_for_quiet()
                if call is None:
                    self._fetch(url)
                else:
                    call()
                failures = 0
                logger.info('prefetched %s', url)
            except Exception as exc:
                if getattr(exc, 'status_code', 500) >= 500:
                    failures += 1
                    backoff = min(_BACKOFF_BASE * 2 ** (failures - 1), _BACKOFF_MAX)
                    dropped = self._clear()
                    logger.warning('prefetch failed for %s (%s); dropped %d queued,'
                                   ' backing off %ds', url,
                                   getattr(exc, 'detail', exc), dropped, backoff)
                    time.sleep(backoff)
                else:
                    # The page itself can't be parsed; not a sign of blocking.
                    logger.info('prefetch skipped %s: %s',
                                url, getattr(exc, 'detail', exc))
            self._gap()
