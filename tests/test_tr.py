"""Parsers of the Türkiye collectors (tr_common, tr_mfa, tr_tccb) on tiny inline fixtures."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collectors"))

import tr_common as tc  # noqa: E402
import tr_tccb  # noqa: E402

tr_tccb.log = logging.getLogger("test")

MFA_LIST = """<div class="sub_lstitm"><a href='/sub.en.mfa?abc' target='_self'>
\t\t\t\t\t2021</a></div><div class="sub_lstitm"><a href='/no_-187_-x-hk.en.mfa' target='_self'>
\t\t\t\t\tNo: 187, 1 October 2026, Regarding the Attack</a></div>
<a href="javascript:__doPostBack(&#39;sb$grd&#39;,&#39;Page$2&#39;)">2</a>"""
MFA_ART = """<span class="lead"><strong>QA-15, 8 September 2026, Statement of the Spokesperson, Öncü Keçeli</strong></span>
<span class="mfa-content-text"><p>The exploitation of the funeral ceremony must be condemned by all in the strongest terms.</p></span>
<script>var x;</script>"""
TCCB_ART = """<div id="news-detail">
 <h1><span>Kabine Toplantısı’nın Ardından Yaptıkları Konuşma</span></h1>
 <h6>28.09.2026</h6></div><div id="divContentArea"><p>Aziz Milletim,</p><p>Kabinemizin toplantısını az önce tamamladık ve kararlarımızı paylaşıyoruz.</p>
<p>All News</p></div><div id="icons_bottom_area_print_share">x</div>"""
TCCB_LIST = """<dl><dt class="date">01.10.2026</dt>
<dd><a href="/konusmalar/353/166418/tbmm">TBMM A&#231;ılış Konuşmaları</a></dd></dl>"""


def test_dates():
    assert tc.date_in_text("No: 187, 1 Ekim 2026, Husilerin") == "2026-10-01"
    assert tc.date_in_text("No: 12, 3 Şubat 2022, X") == "2022-02-03"
    assert tc.date_in_text("Conference, August 7, 2023, Ankara") == "2023-08-07"
    assert tc.date_in_text("Regarding 19 August World Humanitarian Day") is None
    assert tc.dmy_dots("01.10.2026") == "2026-10-01"
    assert tc.dmy_dots("31.02.2026") is None


def test_mfa_parsers():
    items = tc.mfa_items(MFA_LIST)
    assert items == [("/sub.en.mfa?abc", "2021"), ("/no_-187_-x-hk.en.mfa", "No: 187, 1 October 2026, Regarding the Attack")]
    assert tc.mfa_pages(MFA_LIST) == [2]
    art = tc.mfa_article(MFA_ART)
    assert art["title"].startswith("QA-15") and "funeral ceremony" in art["text"] and "var x" not in art["text"]
    assert tc.speaker_in(art["title"]) == "Keçeli"


def test_tccb_parsers():
    assert tr_tccb.parse_listing(TCCB_LIST) == [("01.10.2026", "/konusmalar/353/166418/tbmm", "TBMM Açılış Konuşmaları")]
    art = tr_tccb.parse_article(TCCB_ART)
    assert art["date"] == "2026-09-28" and art["title"].startswith("Kabine")
    assert art["text"].startswith("Aziz Milletim") and "All News" not in art["text"]


def test_aa_parser():
    import tr_aa
    html = ('<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-10-03T07:33:28.687",'
            '"articleBody":"Vance leads hours-long session"}</script><h1 class="x">Camp David talks</h1>'
            '<div dir="ltr" class="embed-responsive prose max-w-none"><p>US officials gathered on Friday at Camp David '
            'for closed-door discussions on the Iran war and the conflict in Yemen.</p><p>news_share</p></div><template id="P:1">')
    a = tr_aa.parse_article(html)
    assert a["date"] == "2026-10-03" and a["title"] == "Camp David talks"
    assert a["text"].startswith("Vance leads") and "Camp David for closed-door" in a["text"] and "news_share" not in a["text"]
    m = tr_aa.canon("https://www.aa.com.tr/en/politics/some-slug/2188751")
    assert m and m.group(2) == "politics" and m.group(3) == "2188751"
    assert tr_aa.canon("https://www.aa.com.tr/en/politics/-2023-should-be-") is None
    assert tr_aa.section_ok("en", "russia-ukraine-war") and not tr_aa.section_ok("en", "sports")
