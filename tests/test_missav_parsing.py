# coding: utf-8
"""MissAV parsing tests: video-vs-category URL validation, the ReDoS-hardened code regex
(bounded time on a pathological input), the packer-unpacker guards, pagination, and the
中文字幕/无码影片 version flags."""
import re
import sys
import time
import types
from types import SimpleNamespace


def _stub(name, factory=None):
    try:
        __import__(name)
    except ImportError:
        sys.modules[name] = factory() if factory else types.ModuleType(name)


_stub('cloudscraper')
_stub('customtkinter')

from bs4 import BeautifulSoup

from uav_downloader.sites.missav import (
    SiteMissAV, MissAVBrowser, _unpack_js_eval, card_version_flags, page_version_flags)


def test_validate_accepts_video_pages():
    assert SiteMissAV.validate_url('https://missav.ai/sone-543')
    assert SiteMissAV.validate_url('https://missav.ai/cn/sone-543-chinese-subtitle')
    assert SiteMissAV.validate_url('https://missav.ai/dm1151/092014_887')
    assert SiteMissAV.validate_url('https://missav.ai/dm464/081012-097')


def test_validate_rejects_category_and_foreign_pages():
    assert not SiteMissAV.validate_url('https://missav.ai/dm278/chinese-subtitle')
    assert not SiteMissAV.validate_url('https://missav.ai/dm539/new')
    assert not SiteMissAV.validate_url('https://jable.tv/videos/x/')


def test_code_regex_is_not_redos():
    # A ~40k-char string that reaches the code group but never satisfies the trailing
    # [-_]\d ran QUADRATICALLY under the old pattern (measured ~8s) — a GUI freeze via the
    # 800ms clipboard poller. The linear rewrite handles it instantly; a regression to the
    # old nested-quantifier pattern would blow this time budget.
    evil = 'https://missav.ai/a' + '_a' * 20000 + '!'
    t0 = time.time()
    re.match(SiteMissAV.website_dirname_pattern, evil, flags=re.I)
    assert time.time() - t0 < 0.5


def test_unpack_guards_bad_base():
    # base<=1 would make to_base loop forever; must return None instead.
    packed = "eval(function(p,a,c,k,e,d){}('x',1,1,'a'.split('|')"
    assert _unpack_js_eval(packed) is None


def test_unpack_guards_absurd_count():
    # a huge `c` would allocate an unbounded lookup dict; must bail fast.
    packed = "eval(function(p,a,c,k,e,d){}('x',36,999999999,'a'.split('|')"
    t0 = time.time()
    assert _unpack_js_eval(packed) is None
    assert time.time() - t0 < 0.5


def test_unpack_returns_none_on_non_packer():
    assert _unpack_js_eval('just some normal <script> here') is None


def test_page_url_pagination():
    assert MissAVBrowser.page_url('https://missav.ai/dm539/new', 1) == 'https://missav.ai/dm539/new'
    assert MissAVBrowser.page_url('https://missav.ai/dm539/new', 3) == 'https://missav.ai/dm539/new?page=3'
    assert MissAVBrowser.page_url('https://missav.ai/x?a=1', 2) == 'https://missav.ai/x?a=1&page=2'


def test_listing_fetch_rejects_404_grid_page(monkeypatch):
    response = SimpleNamespace(
        status_code=404,
        content=b'<div class="grid"><div><a href="/ad-123">ad</a></div></div>',
        url='https://missav.ai/genres/not-found',
    )

    def fake_fetch(_scraper, _url, _site, validator, **_kwargs):
        assert validator(response) is False
        return response, 'missav.ai', 'failed'

    monkeypatch.setattr(MissAVBrowser, '_get_scraper',
                        classmethod(lambda cls: object()))
    monkeypatch.setattr(
        'uav_downloader.sites.missav.fetch_with_mirrors', fake_fetch)

    assert MissAVBrowser.fetch_page(response.url) == []


def _card(badge_class=None):
    badge = (f'<span class="absolute bottom-1 left-1 rounded-lg {badge_class}">x</span>'
             if badge_class else '')
    html = ('<div class="thumbnail"><a href="#"><img></a>'
            f'{badge}<span class="absolute bottom-1 right-1">1:57:53</span></div>')
    return BeautifulSoup(html, 'html.parser').div


_CN_SEARCH = 'https://missav.ai/cn/search/ipx-771'


def test_card_flags_from_badges():
    # A plain slug can be the subtitled page: only the badge says so.
    assert card_version_flags(_card('bg-red-800'), 'https://missav.ai/cn/ipx-771',
                              _CN_SEARCH) == (True, None)
    assert card_version_flags(_card('bg-blue-800'), 'https://missav.ai/cn/ipx-771-uncensored-leak',
                              _CN_SEARCH) == (False, True)
    assert card_version_flags(_card(), 'https://missav.ai/cn/mida-278',
                              _CN_SEARCH) == (False, False)


def test_card_flags_unknown_where_hidden():
    # English pages never show 中文字幕; any other badge hides 无码影片.
    en = 'https://missav.ai/en/search/x'
    assert card_version_flags(_card(), 'https://missav.ai/en/ipx-771', en) == (None, False)
    assert card_version_flags(_card('bg-green-800'), 'https://missav.ai/en/ipx-771', en) == (None, None)
    # ...but a URL suffix is always right.
    assert card_version_flags(_card(), 'https://missav.ai/en/mida-278-chinese-subtitle',
                              en) == (True, False)


def _video_page(type_links):
    links = ''.join(f'<a href="https://missav.ai/cn/{t}">t</a>, ' for t in type_links)
    html = f"""<nav><span><a href="https://missav.ai/dm817/cn/uncensored-leak">menu</a>
      <a href="https://missav.ai/dm278/cn/chinese-subtitle">menu</a>
      <a href="https://missav.ai/dm1/cn/genres/x">menu</a></span></nav>
      <div class="text-secondary"><span>类型:</span>
      {links}<a href="https://missav.ai/dm96/cn/genres/HD">高清</a></div>"""
    return BeautifulSoup(html, 'html.parser')


def test_page_flags_from_genre_row():
    assert page_version_flags(_video_page(['chinese-subtitle']),
                              'https://missav.ai/cn/ipx-771') == (True, False)
    assert page_version_flags(_video_page(['uncensored-leak']),
                              'https://missav.ai/cn/ipx-771-uncensored-leak') == (False, True)
    # The site menu's /dm<N>/ category links don't count.
    assert page_version_flags(_video_page([]), 'https://missav.ai/cn/mida-278') == (False, False)
    assert page_version_flags(_video_page([]), 'https://missav.ai/en/ipx-771') == (None, False)
    assert page_version_flags(BeautifulSoup('<p></p>', 'html.parser'),
                              'https://missav.ai/cn/ipx-771') is None
