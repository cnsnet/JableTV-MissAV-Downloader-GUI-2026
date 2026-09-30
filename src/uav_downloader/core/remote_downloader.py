#!/usr/bin/env python
# coding: utf-8
"""Client for delegating a resolved m3u8 URL to a remote download server.

Once a site module has resolved the raw m3u8 playlist URL, the local
segment-download/merge pipeline is skipped and the URL is handed off to a
remote download server instead, which fetches and assembles the video on
its own.
"""

import json
import os
import threading

import requests

from uav_downloader.core.paths import product_data_dir

_TIMEOUT = 20

_lock = threading.Lock()
_auth_token = None
_settings = None


class RemoteDownloadError(Exception):
    """The remote download server could not be logged into or rejected a task."""


def _settings_path():
    return product_data_dir() / 'remote_downloader.json'


def _load_settings() -> tuple[str, str, str]:
    """(base_url, username, password). Never kept in source: the resolver
    container passes REMOTE_DL_* env vars; the desktop app, which has no
    env of its own, reads remote_downloader.json from its data folder
    ({"base_url": ..., "username": ..., "password": ...})."""
    global _settings
    if _settings is None:
        try:
            with open(_settings_path(), encoding='utf-8') as fh:
                stored = json.load(fh)
        except (OSError, ValueError):
            stored = {}
        if not isinstance(stored, dict):
            stored = {}
        base_url = os.environ.get('REMOTE_DL_BASE_URL') or stored.get('base_url') or ''
        username = os.environ.get('REMOTE_DL_USERNAME') or stored.get('username') or ''
        password = os.environ.get('REMOTE_DL_PASSWORD') or stored.get('password') or ''
        if not (base_url and username and password):
            raise RemoteDownloadError(
                '遠端下載伺服器未設定：請設定環境變數 REMOTE_DL_BASE_URL / '
                f'REMOTE_DL_USERNAME / REMOTE_DL_PASSWORD，或建立 {_settings_path()}')
        _settings = (base_url.rstrip('/'), username, password)
    return _settings


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
    base_url, username, password = _load_settings()
    try:
        resp = requests.post(
            f'{base_url}/api/auth/login',
            json={'username': username, 'password': password},
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
    base_url = _load_settings()[0]
    token = _get_token()
    body = {'url': m3u8_url, 'output_name': output_name}
    for attempt in range(2):
        try:
            resp = requests.post(
                f'{base_url}/api/tasks',
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
