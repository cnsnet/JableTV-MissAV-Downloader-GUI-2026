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


_stub_runtime_dependency('cloudscraper', _cloudscraper_stub)
_stub_runtime_dependency('m3u8', _m3u8_stub)

from M3U8Sites.M3U8Crawler import M3U8Crawler


_TITLES = {}


class DummyCrawler(M3U8Crawler):
    website_pattern = r'https://example\.test/.+/$'
    website_dirname_pattern = r'https://example\.test/([^/]+)/$'

    def get_url_infos(self):
        self._targetName = _TITLES[self._dirName]
        self._imageUrl = None
        self._m3u8url = 'https://cdn.example.test/master.m3u8'


def _make_site(dest, dirname, title):
    _TITLES[dirname] = title
    return DummyCrawler(f'https://example.test/{dirname}/', savepath=str(dest), silence=True)


def test_long_target_name_is_capped_with_uid_suffix(tmp_path):
    dest = tmp_path / ('normal_dest_' + 'x' * 40)
    title = 'MIDA-644 ' + 'A' * (240 - len('MIDA-644 '))

    site = _make_site(dest, 'uid12345', title)
    final_part = os.path.join(site.dest_folder(), site.target_name() + '.mp4.part')

    assert len(final_part) <= 255
    assert site.target_name().startswith('MIDA-644')
    assert site.target_name().endswith('_uid12345')


def test_long_titles_with_same_prefix_get_different_filenames(tmp_path):
    dest = tmp_path / ('normal_dest_' + 'x' * 40)
    prefix = 'ABCD-001 ' + 'B' * 200

    site1 = _make_site(dest, 'aaaa1111', prefix + ' first title tail')
    site2 = _make_site(dest, 'bbbb2222', prefix + ' second title tail')

    assert site1.target_name() != site2.target_name()
    assert site1.target_name().endswith('_aaaa1111')
    assert site2.target_name().endswith('_bbbb2222')


def test_symbol_only_title_falls_back_to_dirname(tmp_path):
    site = _make_site(tmp_path, 'fallback123', ':?*<>|')

    assert site.target_name() == 'fallback123'


def test_dirname_is_capped_for_temp_folder(tmp_path):
    dirname = 'x' * 100
    site = _make_site(tmp_path, dirname, 'ABCD-001 title')

    assert len(site._dirName) == 80
    assert site._temp_folder == os.path.join(site.dest_folder(), 'x' * 80)
