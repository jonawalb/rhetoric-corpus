"""RU deep-expansion helpers (2026-10-03): spread order, incremental writer, BigState, URL patterns."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collectors"))

import lib  # noqa: E402
import ru_common as rc  # noqa: E402
import ru_sitemap_media as sm  # noqa: E402
import ru_statemedia as st_media  # noqa: E402


def test_spread_is_a_permutation_with_even_prefixes():
    order = sm.spread(10)
    assert sorted(order) == list(range(10))
    assert order[:2] == [0, 8]  # first, then roughly the middle


def test_write_docs_fast_dedupes_across_writers(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "DOCS", tmp_path)
    rows = [{"id": f"zz_t:{i}", "country": "ZZ", "source": "zz_t", "lang": "ru", "date": "2020-01-01",
             "url": f"http://x/{i}", "text": "t"} for i in range(4)]
    assert rc.write_docs_fast("ZZ", "zz_t", rows[:2]) == (2, 2)
    assert lib.write_docs("ZZ", "zz_t", rows[2:3]) == (1, 3)  # another writer appends
    assert rc.write_docs_fast("ZZ", "zz_t", rows) == (1, 4)


def test_bigstate_persists_done_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "STATE", tmp_path)
    s = rc.BigState("x")
    s.mark_done("a")
    s["k"] = 1
    s.save()
    s2 = rc.BigState("x")
    assert s2.is_done("a") and s2.get("k") == 1
    assert "a" not in (s2.data.get("done") or [])  # new keys live in x.done.txt, not the JSON


def test_url_patterns():
    assert sm.OneTv.pat.match("https://www.1tv.ru/news/2026-01-01/530120")
    assert sm.OneTv.pat.match("https://www.1tv.ru/news/2017-01-01/317209-torzhestvami")
    assert not sm.Izvestia.pat.match("https://iz.ru/2154188/video/boevaia-rabota")
    assert sm.RgRu.pat.match("https://rg.ru/2024/01/02/reg-ufo/zelenskij.html")
    assert st_media.SputnikEn.pat.match("https://sputnikglobe.com/20150131/1017602090.html")
    assert st_media.Ria.pat.match("https://ria.ru/20100131/207123918.html")  # pre-slug RIA URLs


def test_full_flag_uncaps_sections(monkeypatch):
    monkeypatch.setattr(st_media, "FULL", True)
    assert st_media.TassCom().sample_rule({"url": "https://tass.com/economy/1"}) == ("economy", None)
    monkeypatch.setattr(st_media, "FULL", False)
    assert st_media.TassCom().sample_rule({"url": "https://tass.com/economy/1"}) == ("economy", 100)
