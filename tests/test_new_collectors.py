"""Parser tests for cn_mfa_live, by_belta, ru_duma and the tass_ru Wayback extractor (markup copied from real
pages fetched 2026-10-02, trimmed)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "collectors"))

import lib  # noqa: E402

lib.setup_logging = lambda name, level=logging.INFO: logging.getLogger(name)  # keep tests out of state/*.log

import by_belta  # noqa: E402
import cn_mfa_live  # noqa: E402
import ru_duma  # noqa: E402
import ru_tass_ru  # noqa: E402

LONG = "Guo Jiakun: China supports resolving disputes through political and diplomatic means. " * 4


def test_mfa_en():
    html = ("<title>Foreign Ministry Spokesperson Guo Jiakun’s Regular Press Conference on September 30, 2026_Ministry"
            " of Foreign Affairs</title><div class=\"content_text\"><div class=\"view_dedault _default TRS_UEDITOR "
            "trs_paper_default trs_web\"><p><img src=\"x.jpg\"/></p><p><strong>CCTV: What's your comment?</strong></p>"
            f"<p>{LONG}</p></div>\n<div style=\"clear:both;\"></div></div><div class=\"links\">Links</div>")
    p = cn_mfa_live.parse("en", html)
    assert p["date"] == "2026-09-30" and p["speaker"] == "Guo Jiakun"
    assert p["text"].startswith("CCTV: What's your comment?") and "Links" not in p["text"]


def test_mfa_zh():
    body = "郭嘉昆：中方支持通过政治外交途径化解争端。" * 12
    html = ("<title>2026年9月30日外交部发言人郭嘉昆主持例行记者会_中华人民共和国外交部</title>"
            "<div class=\"news-main\" id=\"News_Body_Txt_A\"><div class=\"view_dedault _default TRS_UEDITOR trs_word\">"
            f"<p><strong>总台央视记者：有何评论？</strong></p><p>{body}</p></div></div><div class=\"news-foot\">附件</div>")
    p = cn_mfa_live.parse("zh", html)
    assert p["date"] == "2026-09-30" and p["speaker"] == "郭嘉昆" and "附件" not in p["text"]


def test_mfa_url_date():
    assert cn_mfa_live.url_date("https://www.mfa.gov.cn/web/x/202609/t20260930_12034297.shtml") == "2026-09-30"


def test_belta():
    html = ('<div class="date_full">02 October 2026, 11:54</div>\n<h1>Lukashenko urges EAEU countries</h1>'
            '<meta name="mediator_published_time" content="2026-10-02T11:54:00+00:00" />'
            '<div class="js-mediator-article">MINSK, 2 October (BelTA) – Belarusian President Aleksandr Lukashenko '
            'urged the countries of the Eurasian Economic Union to remain committed to the common market.'
            '<div class="video_add"><iframe src="v"></iframe></div><div>"The main thing," the president said.</div>'
            '</div><div class="invite_in_messagers"><div>Follow us on:</div></div>')
    p = by_belta.parse(html)
    assert p["date"] == "2026-10-02" and p["title"] == "Lukashenko urges EAEU countries"
    assert "Follow us" not in p["text"] and "main thing" in p["text"]
    m = by_belta.URL_RE.search('href="https://eng.belta.by/president/view/lukashenko-x-186877-2026/"')
    assert by_belta.norm(m) == "https://eng.belta.by/president/view/lukashenko-x-186877-2026/"
    old = by_belta.URL_RE.search("https://eng.belta.by/politics/view/old-story-12345-2019/")
    assert by_belta.norm(old) is None


def test_duma_person_card():
    html = ('<h1 class="article__title">\n Вячеслав Володин направил приветствие </h1>'
            '<div class="article__lead">Лид новости</div>'
            '<time datetime="2021-07-15 09:00:00" itemprop="datePublished"\n class="x">15 июля</time>'
            '<div class="article__content"><p>Текст приветствия участникам фестиваля, большой и содержательный, '
            'говорится в приветствии Председателя Государственной Думы <a href="/p/">'
            '<span class="person person--s"><img alt="Володин Вячеслав Викторович" src="v.jpg"/>'
            '<span class="person__title">Володин</span></span>'
            '<span class="person__content-tooltip">Председатель Государственной Думы</span></a>.</p></div>'
            '<footer class="article__footer">x</footer>')
    p = ru_duma.parse(html)
    assert p["date"] == "2021-07-15" and p["title"] == "Вячеслав Володин направил приветствие"
    assert p["text"].startswith("Лид новости") and "Володин Вячеслав Викторович" in p["text"]
    assert "Председатель Государственной Думы" not in p["text"]  # tooltip dropped


def test_tass_extract_jsonld():
    html = ('<script type="application/ld+json">{"@type":"NewsArticle","headline":"Заголовок",'
            '"datePublished":"2021-03-04T10:00:00+03:00","articleBody":"МОСКВА, 4 марта. /ТАСС/. Текст."}</script>')
    assert ru_tass_ru.extract(html) == ("Заголовок", "2021-03-04", "МОСКВА, 4 марта. /ТАСС/. Текст.")


def test_tass_extract_text_block():
    html = ('<meta property="og:title" content="Невское ПКБ представило проект"/>'
            '<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2021-01-18T10:00:00+03:00"}'
            '</script><div class="text-content"><div class="text-block"><p>МОСКВА, 18 января. /ТАСС/. Первый.</p>\n'
            '<p>Второй абзац.</p></div><div class="tags"><p>не текст</p></div></div>')
    assert ru_tass_ru.extract(html) == ("Невское ПКБ представило проект", "2021-01-18",
                                        "МОСКВА, 18 января. /ТАСС/. Первый.\nВторой абзац.")


def test_tass_extract_nextjs():
    html = ('<meta property="og:title" content="В России завершается регистрация"/>'
            '<script>self.__next_f.push([1,"{\\"datePublished\\":\\"2026-08-16T00:02:03+03:00\\"}"])</script>'
            '<p class="Paragraph_paragraph__2FiCb"><span>МОСКВА, 16 августа. /ТАСС/. Первый.</span></p>'
            '<p class="Paragraph_paragraph__2FiCb">Второй.</p>'
            '<p class="Paragraph_paragraph__2FiCb">© Информационное агентство ТАСС</p>'
            '<p class="Paragraph_paragraph__2FiCb">Свидетельство о регистрации СМИ</p>')
    assert ru_tass_ru.extract(html) == ("В России завершается регистрация", "2026-08-16",
                                        "МОСКВА, 16 августа. /ТАСС/. Первый.\nВторой.")
