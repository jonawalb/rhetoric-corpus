"""Parser tests for the SY / VE / CU collectors (HTML trimmed from real pages, 2026-10-02)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "collectors"))

import cu_granma  # noqa: E402
import cu_minrex  # noqa: E402
import sy_mofa  # noqa: E402
import sy_sana  # noqa: E402
import ve_mppre  # noqa: E402

LONG = "Damascus, Oct. 2 (SANA) The two diplomats reviewed bilateral relations and discussed cooperation mechanisms."


def test_sana_period_split():
    assert sy_sana.period_of("2024-12-07") == "assad"
    assert sy_sana.period_of("2024-12-08") == "transitional"


def test_sana_new_parse():
    html = ('<script>{"datePublished":"2026-10-02T22:38:42+03:00"}</script><h1 class="s-title">Title &#8217;x</h1>'
            f'<div class="entry-content rbct clearfix"><p>{LONG}</p><p>Kh.A / H.H</p></div><div class="efoot">TAGGED: x</div>')
    r = sy_sana.parse_new(html)
    assert r["date"] == "2026-10-02" and r["title"] == "Title ’x" and r["text"].startswith("Damascus") and "TAGGED" not in r["text"]


def test_sana_archive_parse():
    html = ('<meta property="article:published_time" content="2021-04-16T18:32:52+03:00"/>'
            '<h1 class="name post-title entry-title"><span itemprop="name">Old title</span></h1>'
            f'<div class="entry"><p>{LONG}</p><div id=\'gallery-2\'><img/></div></div><!-- .entry /--><p>footer</p>')
    r = sy_sana.parse_archive(html)
    assert r["date"] == "2021-04-16" and r["title"] == "Old title" and "footer" not in r["text"]


def test_mofa_parse_and_card():
    card = ('<span class="text-xs font-light text-teal-green font-en">2026-09-23</span></div><h2 class="x"><!--$-->'
            '<a href="/en/news/التقى-وزير-53">التقى وزير</a>')
    (date, href, title), = sy_mofa.CARD.findall(card)
    assert date == "2026-09-23" and sy_mofa.canon(href).startswith("https://mofaex.gov.sy/news/%D8")
    page = ('<h1 class="t">​التقى الوزير</h1><a>Home</a>/<a>News</a><span>2026-09-23</span>'
            '<p>​التقى وزير الخارجية والمغتربين السيد أسعد حسن الشيباني وزير خارجية أرمينيا في نيويورك.</p>'
            '<p>شارك هذه المقالة</p><p>Related news 2026-09-22 other</p>')
    r = sy_mofa.parse_article(page)
    assert r["date"] == "2026-09-23" and "Related" not in r["text"] and "​" not in r["text"]


def test_mppre_both_date_formats():
    body = "<p>El Gobierno de la República Bolivariana de Venezuela extiende sus felicitaciones al pueblo chino.</p>"
    a = ('<h4 class="sub-title fsz-28px mt-40"> Venezuela felicita </h4><div class="author-side"><a href="#">'
         '<i class="la la-calendar me-1"></i> 01-10-2026 </a></span>' + body + '<a>Descargar Comunicado</a>')
    b = ('<h4 class="sub-title fsz-28px mt-40"> Acuerdo </h4><div class="author-side"><span class="me-40">'
         ' 13/06/2026 </span>' + "<p>Fotógrafo: Prensa presidencial</p>" + body + '<div class="btm-tags"><a>tag</a>')
    assert ve_mppre.parse(a)["date"] == "2026-10-01"
    rb = ve_mppre.parse(b)
    assert rb["date"] == "2026-06-13" and rb["text"].startswith("El Gobierno") and "tag" not in rb["text"]


def test_granma_parse():
    html = ('<h1 itemprop="headline" class="g-story-heading">No more masks</h1><p class="g-story-description" '
            'itemprop="description">Sub</p><div class="g-story-meta-footer"><span class="byline-author" itemprop="name">'
            'R. Capote</span> october 2, 2026 14:10:20</div><div class="story-body-text story-content" itemprop="articleBody">'
            f'<figure><figcaption>Photo</figcaption></figure><p>{LONG}</p></div><aside></aside><footer class="g-story-footer">x</footer>')
    r = cu_granma.parse(html)
    assert r["page_date"] == "2026-10-02" and r["author"] == "R. Capote" and "Photo" not in r["text"]
    (path, date, _), = cu_granma.ITEM.findall('<article class="g-searchpage-story"><h3></h3><h2><a href="mundo/2026-09-14/cubas-voice">C</a></h2>')
    assert path == "mundo/2026-09-14/cubas-voice" and date == "2026-09-14"


def test_minrex_parse():
    html = ('<span property="schema:name" content="Cuba does not support terrorism" class="rdf-meta hidden"></span>'
            '<span property="schema:dateCreated" content="2025-05-15T03:01:30+00:00"></span><div class="node--main-content">'
            f'<div class="field field--name-body"><p>{LONG}</p></div><div class="field field--name-field-fuente">MINREX</div>')
    r = cu_minrex.parse(html)
    assert r["date"] == "2025-05-15" and r["title"].startswith("Cuba") and "MINREX" not in r["text"]
