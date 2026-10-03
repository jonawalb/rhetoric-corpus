"""Taiwan helpers (collectors/tw_common.py): ROC (Minguo) date conversion and Western date parsing."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collectors"))

import tw_common as tw  # noqa: E402


def test_roc_year():
    assert tw.roc_to_ad(113) == 2024
    assert tw.roc_to_ad(110) == 2021
    assert tw.roc_to_ad(1) == 1912
    with pytest.raises(ValueError):
        tw.roc_to_ad(2024)


def test_roc_date():
    assert tw.roc_date("民國113年5月20日") == "2024-05-20"
    assert tw.roc_date("中華民國 110 年 1 月 1 日") == "2021-01-01"
    assert tw.roc_date("發布日期：115-10-02") == "2026-10-02"
    assert tw.roc_date("113.02.29") == "2024-02-29"      # leap year (2024)
    assert tw.roc_date("112.02.29") is None              # 2023 is not a leap year
    assert tw.roc_date("113年13月1日") is None
    assert tw.roc_date("2024年5月20日") is None          # Gregorian, not ROC
    assert tw.roc_date("2024-05-20") is None
    assert tw.roc_date("") is None


def test_ad_date():
    assert tw.ad_date("2024-05-20") == "2024-05-20"
    assert tw.ad_date("2024/5/2 10:00") == "2024-05-02"
    assert tw.ad_date("2024年5月20日") == "2024-05-20"
    assert tw.ad_date("May 20, 2024") == "2024-05-20"
    assert tw.ad_date("20 Sept 2024") == "2024-09-20"
    assert tw.ad_date("no date") is None
    assert tw.any_date("113年5月20日") == "2024-05-20"


def test_president_at():
    assert tw.president_at("2024-05-19") == "Tsai Ing-wen"
    assert tw.president_at("2024-05-20") == "Lai Ching-te"


def test_mofa_parsers():
    import tw_mofa
    lst = ('<td class="x" data-title="發布時間" headers="h"><span>2026-10-02</span></td><td class="y" data-title="主旨" '
           'headers="h"><span><a href="News_Content.aspx?n=95&s=123178" title="外交部發布影片"   >外交部發布影片</a></span></td>')
    assert tw_mofa.parse_listing(lst) == [("2026-10-02", "95", "123178", "外交部發布影片")]
    art = ('<div class="simple-text title" data-type="0"><div class="in"><h3>MOFA response</h3></div></div>'
           '<div class="essay"><div class="p"><p>September 25, 2026</p><p>The Ministry of Foreign Affairs notes that '
           'China has continued to distort the facts.</p></div></div><div class="area-editor system-info">x</div>')
    a = tw_mofa.parse_article(art)
    assert a["title"] == "MOFA response" and a["text"].endswith("distort the facts.") and "x" not in a["text"][-3:]


def test_ey_parsers():
    import tw_ey
    lst = ('<ul><li class="new_img hvr-outline-in"><div class="news_box"><a title="t" href="/Page/9277F759E41CCD91/'
           '9f078171-ca09-45a7-9a2e-92edf390159b"><span></span>\n<div class="title">卓揆籲立院</div>'
           '<span class="date">115-10-01</span><p>x</p></a></div></li>')
    assert tw_ey.parse_listing(lst, "9277F759E41CCD91", tw_ey.roc_date) == [
        ("2026-10-01", "9f078171-ca09-45a7-9a2e-92edf390159b", "卓揆籲立院")]
    art = ('<div class="words"><div class="graybg ail"><span class="h2">\n 卓揆籲立院</span><p class="first_p">'
           '<span class="date_style2"><span>日期：115-10-01</span></span></p><div class="words_content noimg">'
           '<p>行政院長卓榮泰今（1）日在行政院會聽取經濟部報告後表示，政府提供補貼有其必要性及正當性。</p>'
           '<!-- hidden --></div><h3>相關連結</h3>')
    assert tw_ey.page_date(art) == "2026-10-01"
    a = tw_ey.parse_article(art, "zh")
    assert a["title"] == "卓揆籲立院" and a["text"].endswith("正當性。")


def test_tw_media_parsers():
    import tw_media as tm
    assert tm.norm_url("taipeitimes", "News/taiwan/archives/2026/10/02/2003865286") == (
        "https://www.taipeitimes.com/News/taiwan/archives/2026/10/02/2003865286", "taiwan", "2003865286", "2026-10-02")
    assert tm.norm_url("cna", "https://www.cna.com.tw/news/aipl/202101010038.aspx?x=1")[1:] == (
        "aipl", "202101010038", "2021-01-01")
    assert tm.norm_url("cna", "https://www.cna.com.tw/news/afe/202101010038.aspx") is None
    assert tm.norm_url("focustaiwan", "/politics/202609290011")[3] == "2026-09-29"
    ft = ("<meta property=\"article:published_time\" content=\"2025-01-05T10:00:00+08:00\" />"
          "<h1><span class='h1t'>Lead only</span></h1><div class='paragraph'><p>Taipei, Jan. 5 (CNA) A long enough "
          "lead paragraph about cross-Strait relations for the test.</p><p>(Full text of the story is now in CNA "
          "English news archive. To view ...)</p></div><div class=\"jsAdSlot\"></div>")
    a = tm.parse_cna(ft)
    assert a["date"] == "2025-01-05" and a["title"] == "Lead only" and a["truncated"]
    assert "Full text" not in a["text"]
    tt = ("<meta property=\"article:published_time\" content=\"2026-10-03T00:00:00+08:00\" /><h1>T</h1>"
          "<font class=\"red hidden\"></font><ul></ul><p>Body one.</p><div class=\"imgboxa\"><p>Photo: CNA</p></div>"
          "<p>Body two.</p><p>\n <font class=\"red\">RELATED</font></p>")
    b = tm.parse_tt(tt)
    assert b["text"] == "Body one.\nBody two." and b["date"] == "2026-10-03"


def test_mac_parse():
    import tw_mac
    html = ('<h2>最新消息</h2><h2>反對中共對臺施壓</h2><ul><li><span >發布日期：111-03-25</span></li></ul>'
            '<div class="area-essay page-caption-p"><div class="essay"><div class="p"><p>陸委會新聞稿編號第002號</p>'
            '<p>大陸委員會今（25）日公布例行民調結果。</p></div></div></div><div class="area-editor system-info">x</div>')
    r = tw_mac.parse(html, "zh")
    assert r["date"] == "2022-03-25" and r["title"] == "反對中共對臺施壓" and r["category"] == "最新消息"
    assert r["text"].endswith("例行民調結果。")
    assert tw_mac.parse(html.replace("111-03-25", ""), "zh") is None
