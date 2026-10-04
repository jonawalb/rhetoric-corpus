"""Parser tests for the Iran sources added 2026-10-04: ir_more.py (Pars Today id walk, Iran newspaper issue/area
JSON) and the new ir_media.py sites (NewsStudio JSON day leaves, Didgah 'body-news body', Gregorian year indexes).
Fixtures under tests/fixtures/ir/ are trimmed copies of real pages fetched 2026-10-04."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "ir"
sys.path.insert(0, str(ROOT / "collectors"))

import lib  # noqa: E402

lib.setup_logging = lambda name, level=logging.INFO: logging.getLogger(name)  # keep tests out of state/*.log

import ir_media  # noqa: E402
import ir_more  # noqa: E402


def fx(name: str) -> str:
    return (FIX / name).read_text("utf-8")


def test_parstoday_en():
    p = ir_more.parse_parstoday(fx("parstoday_en.html"))
    assert p["title"] == "US oil collapses to $11 as world awash with crude"
    assert p["date"] == "2020-04-20" and p["section"] == "World"
    assert p["text"].startswith("US oil prices dived to 22-year lows") and "Tags:" not in p["text"]
    assert len(p["text"]) > ir_more.MIN_TEXT


def test_parstoday_fa():
    p = ir_more.parse_parstoday(fx("parstoday_fa.html"))
    assert p["date"] == "2026-10-04" and p["section"] == "غرب آسیا"
    assert p["title"].startswith("حزب‌الله") and p["text"].startswith("پارس تودی")


def test_parstoday_url_scheme():
    assert ir_more.PT_LANGS["en"].format(id=120001) == "/en/news/x-i120001"
    assert ir_more.PT_LANGS["fa"].format(id=44114) == "/fa/x-i44114"


def test_irannewspaper_area_json():
    a = ir_more.parse_area(fx("irannewspaper_area.json"))
    assert a["title"] == "نماز از محراب به متن زندگی راه یابد"
    assert a["date"] == "2026-10-04"          # publish 1405/7/12
    assert a["section"] == "سیاسی" and a["issue"] == 9139
    assert a["kicker"].startswith("دو پیام جداگانه پزشکیان")
    assert "پزشکیان" in a["text"] and "<p>" not in a["text"] and "&zwnj;" not in a["text"]


def test_irannewspaper_page_listing():
    html = fx("irannewspaper_page.html")
    assert ir_more.issue_pages(html, 9139) == list(range(1, 17))
    assert ir_more.page_areas(html) == [164755, 164756, 164757]


def test_irannewspaper_cropped_areas_skipped():
    """Early issues (e.g. 7805, Dec 2021) are page-image crops without text: no areas to fetch."""
    assert ir_more.page_areas(fx("irannewspaper_page_cropped.html")) == []
    assert ir_more.parse_area('{"dataset_v2": {"data_type": "cropped", "content": ""}}') is None


def test_newsstudio_json_day_leaf():
    """Old khabaronline day leaves come back as JSON {"urls": [...]} instead of a urlset."""
    is_index, items = ir_media.locs(fx("khabaronline_leaf_1390.json"))
    assert not is_index and len(items) == 3
    assert items[0][0].startswith("https://www.khabaronline.ir/news/172665/")
    assert ir_media.ID_RE["newsstudio"].search(items[0][0]).group(1) == "172665"
    assert ir_media.locs('{"urls":[]}') == (False, [])


def test_newsstudio_khabaronline_page():
    a = ir_media.parse_newsstudio(fx("khabaronline_newsstudio.html"), "fa")
    assert a["title"].startswith("تبلیغ متفاوت حجاب") and a["date"] == "2026-10-05"
    assert a["lead"].startswith("تصویری از یک تبلیغ متفاوت")
    assert a["section"] and "عکس" in a["section"]


def test_didgah_body_news_class():
    a = ir_media.parse_didgah(fx("rasa_didgah.html"), "fa")
    assert a["title"] == "انقلاب و امام پیش از ما آنجا بودند" and a["date"] == "2026-10-01"
    assert a["body"].startswith("به گزارش خبرنگار") and a["lead"].startswith("رهبر شهید")


def test_new_sites_config():
    s = ir_media.SITES
    assert s["khabaronline"].outlet == "media" and s["hamshahri"].outlet == "media" and s["rasa"].outlet == "media"
    assert s["quds"].outlet == "state_media" and s["abna_en"].langs == {"en": "ir_abna_en"}
    roots = ir_media.archive_roots(s["abna_en"], "en", None)
    assert roots[0].endswith("/sitemap/all/sitemap.xml") and roots[-1].endswith("/sitemap/1997/sitemap.xml")
    assert ir_media.archive_roots(s["quds"], "fa", None)[-1].endswith("/sitemap/1376/sitemap.xml")


def test_nour_day_tolerates_other_names():
    assert ir_media._nour_day("https://x.ir/sitemap/1405/07/12/sitemap.xml") == ""
    assert ir_media._nour_day("https://x.ir/sitemap/" + ir_media._nour_name("2025-01-02") + ".xml") == "2025-01-02"


def test_unreachable_robots_is_transient_not_missing(monkeypatch):
    """A robots.txt timeout must not turn every id into a 'miss' (the walk would skip the whole archive)."""
    monkeypatch.setattr(ir_more, "robots_ok", lambda url: False)
    monkeypatch.setattr(lib, "TRANSIENT_BACKOFF_S", 0)
    monkeypatch.setitem(lib._robots_retry, "https://parstoday.ir", 9e18)
    assert ir_more.get("https://parstoday.ir/en/news/x-i5")[1] == 0
    lib._robots_retry.pop("https://parstoday.ir")
    assert ir_more.get("https://parstoday.ir/en/news/x-i5")[1] == -1


def test_ir_media_fetcher_unreachable_robots_not_done(monkeypatch):
    monkeypatch.setattr(ir_media, "robots_ok", lambda url: False)
    monkeypatch.setattr(lib, "TRANSIENT_BACKOFF_S", 0)
    monkeypatch.setattr(lib, "crawl_delay", lambda url: None)
    f = ir_media.Fetcher(ir_media.SITES["quds"], logging.getLogger("t"))
    monkeypatch.setitem(lib._robots_retry, "https://www.qudsonline.ir", 9e18)
    assert f.get("https://www.qudsonline.ir/news/1/x") == (None, 0)     # 0 = transient: item() will retry it
    lib._robots_retry.pop("https://www.qudsonline.ir")
    assert f.get("https://www.qudsonline.ir/news/1/x") == (None, -1)
