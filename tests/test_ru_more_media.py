"""Parser tests for collectors/ru_more_media.py (TV Zvezda, Krasnaya Zvezda, archive.government.ru). Fixtures in
tests/fixtures/ru/ are trimmed copies of real pages / API answers fetched 2026-10-04."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "ru"
sys.path.insert(0, str(ROOT / "collectors"))

import lib  # noqa: E402

lib.setup_logging = lambda name, level=logging.INFO: logging.getLogger(name)  # keep tests out of state/*.log

import ru_more_media as m  # noqa: E402


def test_tvzvezda_article():
    p = m.tz_parse((FIX / "tvzvezda_article.html").read_text("utf-8"))
    assert p["title"] == "Дочь маршала Конева поблагодарила Фицо за памятник отцу в Словакии"
    assert p["date"] == "2026-10-05"
    assert p["text"].startswith("Премьер-министр страны выдвинул инициативу")  # lead from JSON-LD description
    assert "Открытие памятника маршалу СССР Ивану Коневу" in p["text"]
    assert "Подписывайтесь" not in p["text"]  # nothing after the text block
    assert "история" in p["tags"]


def test_tvzvezda_url_forms():
    assert m.tz_norm("http://tvzvezda.ru:80/news/crim/content/201403131808-ab1c.htm") == (
        "201403131808-ab1c", "https://tvzvezda.ru/news/201403131808-ab1c.htm")
    assert m.tz_norm("https://tvzvezda.ru/news/202696219-rfCM9.html")[1] == "https://tvzvezda.ru/news/202696219-rfCM9.html"
    assert m.tz_norm("https://tvzvezda.ru/news/search/%23x") is None


def test_redstar_rows():
    rows = m.rs_rows(json.loads((FIX / "redstar_posts.json").read_text("utf-8")), {6: "Новости", 382: "СВО"})
    assert [r["id"] for r in rows] == ["redstar_ru:274126", "redstar_ru:274117"]
    r = rows[1]
    assert r["date"] == "2026-10-05" and r["title"] == "Владимир ПУТИН: Россия никому не угрожает"
    assert r["lang"] == "ru" and r["section"] == "СВО, Новости"
    assert r["url"].startswith("http://redstar.ru/") and "<p>" not in r["text"]
    for row in rows:
        lib.validate(row)


def test_govarchive_event():
    p = m.ga_parse((FIX / "govarchive_docs.html").read_text("utf-8"), "docs")
    assert p["title"] == "Дмитрий Медведев посетил Всероссийский детский центр «Орлёнок» в Туапсе"
    assert p["date"] == "2013-05-20" and p["kind"] == "event"
    assert p["participants"] == ["Медведев Дмитрий Анатольевич"]
    assert p["text"].startswith("Глава Правительства ознакомился")


def test_govarchive_transcript():
    p = m.ga_parse((FIX / "govarchive_stens.html").read_text("utf-8"), "stens")
    assert p["title"] == "Дмитрий Медведев дал интервью газете «Комсомольская правда»"
    assert p["date"] == "2013-05-21" and p["kind"] == "transcript"
    assert p["text"].startswith("Вопрос:")


def test_govarchive_act():
    p = m.ga_parse((FIX / "govarchive_results.html").read_text("utf-8"), "gov/results")
    assert p["title"].startswith("Постановление от 8 мая 2013 г. №403 О мерах")
    assert p["date"] == "2013-05-15" and p["kind"] == "document"
    assert "Минэнерго России" in p["text"]


def test_feed_links_cdata():
    xml = ("<item><link><![CDATA[ https://tvzvezda.ru/news/2026105030-knbAw.html ]]></link></item>"
           "<item><link>https://tvzvezda.ru/news/20261042321-3E4tx.html</link></item>")
    assert m.feed_links(xml) == ["https://tvzvezda.ru/news/2026105030-knbAw.html",
                                 "https://tvzvezda.ru/news/20261042321-3E4tx.html"]
