"""Parser tests for us_dod (defense.gov / war.gov via Wayback) and us_usun (WordPress REST). Markup structure follows
pages seen 2026-10-05; all text is synthetic."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "collectors"))

import lib  # noqa: E402

lib.setup_logging = lambda name, level=logging.INFO: logging.getLogger(name)  # keep tests out of state/*.log

import us_dod  # noqa: E402
import us_usun  # noqa: E402

BODY = "Synthetic transcript sentence for the parser test. " * 6


def test_dod_parse():
    html = ('<header><h1 class="maintitle">\n   Synthetic Press Briefing Title\n </h1>'
            '<div class="date-line two-liner"><span class="date">\n Sept. 15, 2026\n </span></div></header>'
            f'<div class="content content-wrap"><div class="inside ntext"><div class="body"><p>{BODY}\n<p>Second.'
            '<div class="media"><img src="x.jpg"></div>\n</div><div class="tags">TAGS</div></div></div>')
    p = us_dod.parse(html)
    assert p["title"] == "Synthetic Press Briefing Title" and p["date"] == "2026-09-15"
    assert p["text"].startswith("Synthetic transcript") and "Second." in p["text"] and "TAGS" not in p["text"]
    assert us_dod.kind_of("transcript", p["title"]) == "briefing"
    assert us_dod.kind_of("transcript", "Synthetic Remarks") == "transcript"
    assert us_dod.parse_date("May 3, 2022") == "2022-05-03" and us_dod.parse_date("June 1, 2021") == "2021-06-01"


def test_dod_queue_dedupes_hosts(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "STATE", tmp_path)
    rows = {
        "www.war.gov/News/Transcripts/Transcript/Article/": [
            ["20260916004515", "https://www.war.gov/News/Transcripts/Transcript/Article/4602195/slug-a/"]],
        "www.defense.gov/News/Transcripts/Transcript/Article/": [
            ["20250101000000", "https://www.defense.gov/News/Transcripts/Transcript/Article/4602195/slug-b/"],
            ["20240101000000", "https://www.defense.gov/News/Transcripts/Transcript/Article/3600000/slug-c/"]],
        "www.war.gov/News/Releases/Release/Article/": [
            ["20260101000000", "https://www.war.gov/News/Releases/Release/Article/4700000/slug-d/"]],
        "www.defense.gov/News/Releases/Release/Article/": [],
    }
    monkeypatch.setattr(us_dod, "cdx", lambda prefix: rows[prefix])
    q = us_dod.build_queue(lib.State("us_dod"))
    assert [d["aid"] for d in q] == ["4602195", "3600000", "4700000"]  # transcripts first, newest first
    assert q[0]["ts"] == "20260916004515"


def test_usun_parse_post():
    post = {"date": "2026-09-04T10:00:00", "title": {"rendered": "Explanation of Vote on a Synthetic Resolution"},
            "content": {"rendered": "<div><h2>Explanation of Vote on a Synthetic Resolution</h2></div>"
                                    "<p>Ambassador Jane Example</p><p>U.S. Representative</p><p>New York, New York</p>"
                                    "<p>September 4, 2026</p><p>AS DELIVERED</p>"
                                    "<p>Thank you, Madam President. Synthetic remarks follow here.</p>"}}
    p = us_usun.parse_post(post)
    assert p["speaker"] == "Ambassador Jane Example" and p["date"] == "2026-09-04" and p["kind"] == "statement"
    assert p["text"].startswith("Ambassador Jane Example") and "Explanation of Vote" not in p["text"]
    post["title"]["rendered"] = "Remarks at a Synthetic Meeting"
    assert us_usun.parse_post(post)["kind"] == "speech"
