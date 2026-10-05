"""Parser tests for the KP collectors (Rodong Sinmun and KCNA via Wayback). Markup structure follows captures seen
2026-10-05; all text is synthetic."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "collectors"))

import lib  # noqa: E402

lib.setup_logging = lambda name, level=logging.INFO: logging.getLogger(name)  # keep tests out of state/*.log

import kp_kcna_en  # noqa: E402
import kp_rodong_en  # noqa: E402

BODY = "Synthetic body sentence one for the parser test, long enough to count as article text here."


def test_rodong_2025_layout():
    html = ('<div class="article-modal-header row"><div id="article-homepage">Rodong Sinmun</div>'
            '<div id="article-date">Feb. 19, 2026 Thursday [Photo]</div></div>'
            '<div id="articleContent" class="article-content"><div class="container" id="ContDIV">'
            '<br><p class="TitleP" style="text-align:center;">Synthetic Headline Alpha</p><br>'
            f'<p class="TextP">{BODY}</p><p class="TextP">Second paragraph with ２０２６ digits.</p>'
            '<script>$(document).ready(function () {});</script></div></div>')
    title, text = kp_rodong_en.parse(html, "2026-02-19-003", "new")
    assert title == "Synthetic Headline Alpha"
    assert text.startswith("Synthetic body") and "2026 digits" in text and "document" not in text
    assert kp_rodong_en.parse(html, "2026-02-18-003", "new") is None  # page date must match the key


def test_rodong_2025_layout_long_month():
    html = ('<div id="article-date">Sept. 3, 2025 Wednesday </div><p class="TitleP">T</p>'
            f'<p class="TextP">{BODY}</p>')
    assert kp_rodong_en.parse(html, "2025-09-03-001", "new") == ("T", BODY)


def test_rodong_2023_layout():
    html = ('<div class="container-fluid text-center news_Title">Synthetic Headline Beta</div><br>'
            '<p class="News_Detail"><span class="NewsDate">2023.3.13.</span></p>'
            f'<p class="ArticleContent" style="text-align: justify;">{BODY}</p>')
    assert kp_rodong_en.parse(html, "2023-03-13-H001", "new") == ("Synthetic Headline Beta", BODY)


def test_rodong_old_layout():
    html = ('<p class="ArticleContent" align="center"><font>Synthetic <nobr>Name Here</nobr> Headline '
            f'Gamma</font></p><p class="ArticleContent">{BODY}</p>')
    title, text = kp_rodong_en.parse(html, "2021-09-12-0009", "old")
    assert title == "Synthetic Name Here Headline Gamma" and text == BODY


def test_rodong_article_key():
    assert kp_rodong_en.article_key("http://www.rodong.rep.kp/en/index.php?MTJAMjAyNi0wMi0xOS0wMDNAMUAxQEAwQDhA==") \
        == ("2026-02-19-003", "new")
    assert kp_rodong_en.article_key(
        "http://www.rodong.rep.kp/en/index.php?strPageID=SF01_02_01&newsID=2021-09-12-0009") == ("2021-09-12-0009", "old")


def test_rodong_state_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "STATE", tmp_path)
    monkeypatch.setattr(lib, "existing_ids", lambda path: {"kp_rodong_en:2024-01-01-H001"})
    st = lib.State("kp_rodong_en")
    st._done = {"2024-01-01-H001", "2026-02-19-003"}
    st["fail"] = {"2026-02-19-003": 2}
    kp_rodong_en.reset_old_parser_state(st)
    assert st._done == {"2024-01-01-H001"} and st["fail"] == {} and st["parser"] == kp_rodong_en.PARSER
    st._done.add("x")
    kp_rodong_en.reset_old_parser_state(st)  # same parser version: untouched
    assert "x" in st._done


def test_kcna_q_layout():
    html = ('<div class="article-content-title"><div class="article-main-title"><strong>Synthetic <nobr>'
            "<span class='fSpecCs'>Name Here</span></nobr> Headline Delta</strong></div></div>"
            '<div class="media-icon"><a href="/en/media/photo/q/x.kcmsf"><i class="fa fa-camera"></i></a></div>'
            f'<div class="article-content-body"><div class="content-wrapper"><p>Pyongyang, December 10 (KCNA) -- {BODY}'
            "</p><p>Closing synthetic line. -0-</p> <span class='publish-time'>www.kcna.kp (Juche108.12.10.)</span>")
    p = kp_kcna_en.parse(html, "20220406032257")
    assert p["title"] == "Synthetic Name Here Headline Delta"
    assert p["date"] == "2019-12-10" and p["date_from"] == "page"
    assert p["text"].endswith("Closing synthetic line.") and "-0-" not in p["text"]
    p2 = kp_kcna_en.parse(html.replace("Juche108.12.10.", "2025.12.14."), "20260123202022")
    assert p2["date"] == "2025-12-14"


def test_kcna_detail_layout_dateline_year():
    html = ('<main><article><div class="container"><h1 class="text-center text-danger">Synthetic Headline Epsilon</h1>'
            '<h1 class="text-center text-danger"></h1>'
            f'<p>Pyongyang, December 30 (KCNA) -- {BODY}</p><p>Second. -0-</p>'
            '<a class="right_button gallery_button" href="/en/gallery/detail/x"><i class="fa fa-camera"></i></a>'
            '</div></article></main>')
    p = kp_kcna_en.parse(html, "20260102010101")  # captured in January: the dateline is from the previous year
    assert p["title"] == "Synthetic Headline Epsilon" and p["date"] == "2025-12-30" and p["date_from"] == "dateline"
    assert kp_kcna_en.parse(html, "20261231000000")["date"] == "2026-12-30"


def test_kcna_keys_and_official():
    assert kp_kcna_en.article_key("http://www.kcna.kp/en/article/q/04dcac6deb9f11a3e3d36f56f8d63da9.kcmsf") \
        == "04dcac6deb9f11a3e3d36f56f8d63da9"
    assert kp_kcna_en.article_key("http://kcna.kp/en/article/detail/05503f758d64c1a0328e43c2f83e4469") \
        == "05503f758d64c1a0328e43c2f83e4469"
    assert kp_kcna_en.article_key("http://kcna.kp/en/article/list/701e7819068d44ac7432ad3daf1821f2") is None
    off = kp_kcna_en.official_title
    assert off("Press Statement of Kim Yo Jong, Vice Department Director of WPK Central Committee")
    assert off("Answer of Director General to KCNA Question") and off("DPRK FM Spokesperson Slams US")
    assert off("Kim Yo Jong, Department Director of C.C., WPK, on Strategic Weapon Launching Drill")
    assert off("Talk", "Pyongyang, May 2 (KCNA) -- The DPRK Foreign Ministry released the following press statement:")
    assert not off("New Houses Built at Farms") and not off("Press Review")
