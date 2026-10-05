"""Impossible/future dates: China Daily URL date, ir_media.parse_date future cut-off, the shared writer guard
(lib.guard_date in write_docs / write_docs_fast / cn_common.Sink) and build_index date corrections."""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "collectors"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_index  # noqa: E402
import cn_chinadaily  # noqa: E402
import cn_common  # noqa: E402
import ir_media  # noqa: E402
import lib  # noqa: E402
import ru_common  # noqa: E402

TODAY = dt.date(2026, 10, 4)
CD_URL = "https://www.chinadaily.com.cn/a/202605/17/WS6a09b808a310d6866eb490be.html"
# Trimmed from the live page (2026-10-04): the Buddhist Era year is in the meta and in the 'Updated' line.
CD_HTML = """<html><head><title>All involved accountable for AI-related IP violations - Opinion - Chinadaily.com.cn</title>
<meta name="publishdate" content="2569-05-17" /></head><body>
<div class="info"><span class="info_l">By Zhang San | China Daily | Updated: 2569-05-17 20:43</span></div>
<div id="Content"><p>""" + "Everyone involved in AI-related intellectual property violations must be held accountable. " * 3 + \
    """</p></div></body></html>"""
# Trimmed from https://basijnews.ir/fa/news/9788052/ (2026-10-04): future published_time, modified 2026-09-30.
BASIJ_HTML = """<html><head><meta property="article:published_time" content="2026-12-13T10:22:15+03:30">
<meta property="article:modified_time" content="2026-09-30T10:24:42+03:30"></head><body>
<h1 class="title">فرزندآوری و لزوم افزایش جمعیت</h1><div class="news_pdate_c">%s</div>
<div class="body"><p>متن خبر</p></div></body></html>"""


def row(date, url="https://example.org/a/1", did="t_x:1", source="t_x", country="CN"):
    return {"id": did, "country": country, "source": source, "lang": "en", "date": date, "url": url, "text": "body"}


# ------------------------------------------------------------------ China Daily
def test_chinadaily_parse_reads_buddhist_era_and_pick_date_prefers_url():
    p = cn_chinadaily.parse(CD_HTML, "en")
    assert p["date"] == "2569-05-17"          # what the page says (meta rejected by first_date; 'Updated' line)
    assert cn_chinadaily.pick_date(p["date"], "2026-05-17", TODAY) == "2026-05-17"


@pytest.mark.parametrize("parsed,url_d,want", [
    ("2026-05-17", "2026-05-17", "2026-05-17"),   # agree
    ("2026-05-18", "2026-05-17", "2026-05-18"),   # within a few days: trust the page
    ("2026-05-30", "2026-05-17", "2026-05-17"),   # disagree by > 3 days
    ("2026-10-06", "2026-10-04", "2026-10-04"),   # page date in the future
    (None, "2026-05-17", "2026-05-17"),
    ("2026-05-17", None, "2026-05-17"),
])
def test_chinadaily_pick_date(parsed, url_d, want):
    assert cn_chinadaily.pick_date(parsed, url_d, TODAY) == want


def test_chinadaily_article_stores_url_date(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(lib, "STATE", tmp_path / "state")
    monkeypatch.setattr(cn_common, "get", lambda url, delay, **kw: CD_HTML)
    cd = cn_chinadaily.CD("en")
    cd.article(CD_URL)
    cd.save()
    rows = list(lib.read_docs(lib.docs_path("CN", "cn_chinadaily")))
    assert [(r["id"], r["date"]) for r in rows] == [("cn_chinadaily:WS6a09b808a310d6866eb490be", "2026-05-17")]


# ------------------------------------------------------------------ ir_media.parse_date
def test_ir_parse_date_skips_future_candidates():
    # future published_time -> falls through to the visible Jalali date (8 Mehr 1405 = 2026-09-30)
    assert ir_media.parse_date("2026-12-13T10:22:15+03:30", "۸ مهر ۱۴۰۵", "fa", today="2026-10-04") == "2026-09-30"
    # every candidate in the future (the Basij page: visible 22 Azar 1405 = 2026-12-13) -> None (doc skipped)
    assert ir_media.parse_date("2026-12-13T10:22:15+03:30", "۲۲ آذر ۱۴۰۵", "fa", today="2026-10-04") is None
    assert ir_media.parse_date("1405/09/22", "", "fa", today="2026-10-04") is None
    # today (Tehran) + 1 day is still allowed; normal dates unchanged
    assert ir_media.parse_date("2026-10-05T01:00:00+03:30", "", "fa", today="2026-10-04") == "2026-10-05"
    assert ir_media.parse_date("2026-09-30T10:24:42+03:30", "", "fa", today="2026-10-04") == "2026-09-30"


def test_ir_parse_didgah_basij_future_date():
    future = ir_media.parse_didgah(BASIJ_HTML % "۲۲ آذر ۱۴۰۵", "fa")
    assert future["date"] is None                       # Runner.item skips a dateless article
    ok = ir_media.parse_didgah(BASIJ_HTML % "۸ مهر ۱۴۰۵", "fa")
    assert ok["date"] == "2026-09-30"


# ------------------------------------------------------------------ shared guard
@pytest.mark.parametrize("url,want", [
    ("https://x.org/2026/05/17/story", "2026-05-17"),
    ("https://www.chinadaily.com.cn/a/202605/17/WS6a09.html", "2026-05-17"),
    ("https://x.org/news/2026-05-17-story", "2026-05-17"),
    ("https://x.org/n/20260517/1.html", "2026-05-17"),
    ("https://basijnews.ir/fa/news/9788052/", None),
    ("https://x.org/2026/13/40/story", None),
])
def test_url_date(url, want):
    assert lib.url_date(url) == want


def test_guard_date():
    assert lib.guard_date(row("2026-10-06"), TODAY)["date"] == "2026-10-06"        # within 2 days: kept
    assert lib.guard_date(row("2026-10-07", "https://x.org/2026/09/01/a"), TODAY)["date"] == "2026-09-01"
    assert lib.guard_date(row("2569-05-17", CD_URL), TODAY)["date"] == "2026-05-17"  # URL first
    assert lib.guard_date(row("2569-05-17"), TODAY)["date"] == "2026-05-17"          # Buddhist Era
    assert lib.guard_date(row("2026-12-13", "https://basijnews.ir/fa/news/9788052/"), TODAY) is None
    assert lib.guard_date(row("2570-01-01"), TODAY) is None                           # -543 still future
    r = row("2569-05-17")
    lib.guard_date(r, TODAY)
    assert r["date"] == "2569-05-17"                                                  # input not mutated


def test_writers_apply_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(lib, "STATE", tmp_path / "state")
    good, be, bad = row("2026-01-02", did="t_x:1"), row("2569-05-17", did="t_x:2"), row("2999-01-01", did="t_x:3")
    assert lib.write_docs("CN", "t_x", [good, be, bad]) == (2, 2)
    assert ru_common.write_docs_fast("CN", "t_x", [row("2999-01-01", did="t_x:4"), row("2026-01-03", did="t_x:5")]) \
        == (1, 3)
    sink = cn_common.Sink("t_x", flush_every=100)
    assert sink.add(row("2999-01-01", did="t_x:6")) is False and sink.has("t_x:6")
    assert sink.add(row("2569-05-17", "https://x.org/2026/05/16/a", did="t_x:7")) is True
    sink.flush()
    got = {r["id"]: r["date"] for r in lib.read_docs(lib.docs_path("CN", "t_x"))}
    assert got == {"t_x:1": "2026-01-02", "t_x:2": "2026-05-17", "t_x:5": "2026-01-03", "t_x:7": "2026-05-16"}


# ------------------------------------------------------------------ build_index corrections
def test_build_index_applies_corrections(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    monkeypatch.setattr(lib, "DOCS", docs)
    monkeypatch.setattr(lib, "STATE", tmp_path / "state")
    path = docs / "CN" / "t_x.jsonl"
    path.parent.mkdir(parents=True)
    rows = [row("2569-05-17", did="t_x:1"), row("2026-12-13", did="t_x:2"), row("2026-01-02", did="t_x:3")]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")  # as stored before the guard
    empty, corr = tmp_path / "none.json", tmp_path / "corr.json"
    empty.write_text('{"corrections": {}}', encoding="utf-8")
    corr.write_text(json.dumps({"corrections": {"t_x:1": "2026-05-17", "t_x:2": None}}), encoding="utf-8")

    def dates(con):
        return dict(con.execute("SELECT id, date FROM docs ORDER BY id"))

    # incremental: indexed first without corrections, then the next run fixes the existing rows
    con = build_index.connect(tmp_path / "a.sqlite")
    build_index.update(con, docs, corrections=empty)
    assert dates(con) == {"t_x:1": "2569-05-17", "t_x:2": "2026-12-13", "t_x:3": "2026-01-02"}
    st = build_index.update(con, docs, corrections=corr)
    assert st["dates_corrected"] == 2
    assert dates(con) == {"t_x:1": "2026-05-17", "t_x:3": "2026-01-02"}
    assert build_index.update(con, docs, corrections=corr)["dates_corrected"] == 0
    con.execute("INSERT INTO fts_words(fts_words) VALUES('integrity-check')")
    con.close()
    # fresh build: corrections applied while indexing
    con = build_index.connect(tmp_path / "b.sqlite")
    st = build_index.update(con, docs, corrections=corr)
    assert st["dates_corrected"] == 0 and dates(con) == {"t_x:1": "2026-05-17", "t_x:3": "2026-01-02"}
    con.close()


def test_committed_corrections_file():
    corr = build_index.load_corrections()
    assert corr["cn_chinadaily:WS6a09b808a310d6866eb490be"] == "2026-05-17"
    assert corr["ir_basijnews_fa:9788052"] is None
    notes = json.loads(build_index.CORRECTIONS.read_text(encoding="utf-8"))["notes"]
    assert set(notes) == set(corr)
