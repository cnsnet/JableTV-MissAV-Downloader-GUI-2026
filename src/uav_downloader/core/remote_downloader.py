#!/usr/bin/env python
# coding: utf-8
"""Client for delegating a resolved m3u8 URL to a remote download server.

Once a site module has resolved the raw m3u8 playlist URL, the local
segment-download/merge pipeline is skipped and the URL is handed off to a
remote download server instead, which fetches and assembles the video on
its own.
"""

import os
import threading

import requests

# Environment variables take precedence so the resolver service (deployed in
# its own Docker container) never needs the credentials baked into source;
# the literals remain as the default for the desktop app's own dev/test use.
REMOTE_BASE_URL = os.environ.get('REMOTE_DL_BASE_URL', 'http://s.cnsc.top:38090')
REMOTE_USERNAME = os.environ.get('REMOTE_DL_USERNAME', 'admin')
REMOTE_PASSWORD = os.environ.get('REMOTE_DL_PASSWORD', 'Weifang@2026#')
_TIMEOUT = 20

_lock = threading.Lock()
_auth_token = None


class RemoteDownloadError(Exception):
    """The remote download server could not be logged into or rejected a task."""


def _extract_token(resp):
    token = resp.cookies.get('auth_token')
    if token:
        return token
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return data.get('auth_token') or data.get('token')


def _login():
    global _auth_token
    try:
        resp = requests.post(
            f'{REMOTE_BASE_URL}/api/auth/login',
            json={'username': REMOTE_USERNAME, 'password': REMOTE_PASSWORD},
            timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise RemoteDownloadError(f'遠端下載伺服器登入失敗：{exc}') from exc
    if resp.status_code != 200:
        raise RemoteDownloadError(
            f'遠端下載伺服器登入失敗 (HTTP {resp.status_code})')
    token = _extract_token(resp)
    if not token:
        raise RemoteDownloadError('遠端下載伺服器登入失敗：回應中未取得 auth_token')
    _auth_token = token
    return token


def _get_token(force_relogin=False):
    with _lock:
        if force_relogin or not _auth_token:
            return _login()
        return _auth_token


def submit_task(m3u8_url: str, output_name: str) -> None:
    """Enqueue a download task on the remote server.

    Raises RemoteDownloadError if the task was not accepted; callers should
    treat that as a failed download rather than falling back to a local one.
    """
    token = _get_token()
    body = {'url': m3u8_url, 'output_name': output_name}
    for attempt in range(2):
        try:
            resp = requests.post(
                f'{REMOTE_BASE_URL}/api/tasks',
                json=body,
                headers={'Cookie': f'auth_token={token}'},
                timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise RemoteDownloadError(f'遠端下載伺服器提交任務失敗：{exc}') from exc
        if resp.status_code == 401 and attempt == 0:
            token = _get_token(force_relogin=True)
            continue
        if resp.status_code not in (200, 201, 202):
            raise RemoteDownloadError(
                f'遠端下載伺服器拒絕任務 (HTTP {resp.status_code}): '
                f'{resp.text[:300]}')
        return
    raise RemoteDownloadError('遠端下載伺服器提交任務失敗：重新登入後仍未通過驗證')
