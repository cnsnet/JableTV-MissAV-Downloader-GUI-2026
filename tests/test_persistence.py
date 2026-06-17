import csv
import os
import sys
import types


def _stub_runtime_dependency(name, factory=None):
    try:
        __import__(name)
    except ImportError:
        sys.modules[name] = factory() if factory else types.ModuleType(name)


def _cloudscraper_stub():
    mod = types.ModuleType('cloudscraper')
    mod.create_scraper = lambda *args, **kwargs: None
    return mod


def _m3u8_stub():
    mod = types.ModuleType('m3u8')
    mod.load = lambda *args, **kwargs: None
    return mod


def _customtkinter_stub():
    mod = types.ModuleType('customtkinter')

    class CTk:
        pass

    mod.CTk = CTk
    mod.CTkLabel = CTk
    return mod


_stub_runtime_dependency('cloudscraper', _cloudscraper_stub)
_stub_runtime_dependency('m3u8', _m3u8_stub)
_stub_runtime_dependency('customtkinter', _customtkinter_stub)

import config
from gui_modern import DownloadManager


def test_queue_csv_path_uses_appdata_download_queue(monkeypatch, tmp_path):
    monkeypatch.setenv('APPDATA', str(tmp_path))

    assert config.queue_csv_path() == os.path.join(
        str(tmp_path), 'JableTV Downloader', 'download_queue.csv')


def test_download_queue_csv_round_trip_preserves_destination(tmp_path):
    path = tmp_path / 'download_queue.csv'
    mgr = DownloadManager()
    item = mgr.add_item(
        'https://supjav.com/12345.html',
        name='Example',
        state='未完成',
        dest=r'C:\Videos')
    item.progress = 42

    mgr.save_csv(str(path))

    loaded = DownloadManager()
    loaded.load_csv(str(path))
    loaded_items = loaded.get_items()

    assert len(loaded_items) == 1
    restored = loaded_items[0]
    assert restored.url == 'https://supjav.com/12345.html'
    assert restored.name == 'Example'
    assert restored.state == '未完成'
    assert restored.progress == 42
    assert restored.dest == r'C:\Videos'


def test_download_queue_csv_load_tolerates_missing_destination_column(tmp_path):
    path = tmp_path / 'old_queue.csv'
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['狀態', '名稱', '進度', '速度', '網址'])
        writer.writerow(['未完成', 'Old Example', '7%', '', 'https://jable.tv/videos/abc/'])

    mgr = DownloadManager()
    mgr.load_csv(str(path))
    restored = mgr.get_items()[0]

    assert restored.url == 'https://jable.tv/videos/abc/'
    assert restored.name == 'Old Example'
    assert restored.state == '未完成'
    assert restored.progress == 7
    assert restored.dest == ''
