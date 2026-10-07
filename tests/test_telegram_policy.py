"""Telegram policy in build_index: only own posts of verified channels stay official; forwards and unverified channels
are indexed as telegram_unofficial_ru / media, both at insert time and for rows indexed earlier."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_index  # noqa: E402


def post(channel: str, n: int, forwarded: bool = False) -> dict:
    return {"id": f"telegram_ru:{channel}/{n}", "country": "RU", "source": "telegram_ru", "outlet": "official",
            "lang": "ru", "date": "2026-10-01", "url": f"https://t.me/{channel}/{n}", "text": "Текст поста.",
            "channel": channel, "forwarded": forwarded}


def labels(con):
    return dict(((i, (s, o)) for i, s, o in con.execute("SELECT id, source, outlet FROM docs")))


def test_insert_applies_policy(tmp_path):
    con = build_index.connect(tmp_path / "c.sqlite")
    build_index._insert_docs(con, "RU/telegram_ru.jsonl", [
        post("MID_Russia", 1), post("MID_Russia", 2, forwarded=True), post("council_gov_ru", 3), post("vv_volodin", 4)])
    got = labels(con)
    assert got["telegram_ru:MID_Russia/1"] == ("telegram_ru", "official")
    assert got["telegram_ru:vv_volodin/4"] == ("telegram_ru", "official")
    assert got["telegram_ru:MID_Russia/2"] == ("telegram_unofficial_ru", "media")
    assert got["telegram_ru:council_gov_ru/3"] == ("telegram_unofficial_ru", "media")


def test_policy_moves_rows_indexed_before(tmp_path):
    con = build_index.connect(tmp_path / "c.sqlite")
    for r in (post("mod_russia", 1), post("mod_russia", 2), post("council_gov_ru", 3)):  # as indexed before the policy
        con.execute("INSERT INTO docs(id, file, source, outlet, text) VALUES(?,?,?,?,?)",
                    (r["id"], "legacy", r["source"], r["outlet"], r["text"]))
    fwd = tmp_path / "fwd.txt"
    fwd.write_text("# comment\ntelegram_ru:mod_russia/2\n")
    assert build_index.apply_telegram_policy(con, fwd) == 2
    assert build_index.apply_telegram_policy(con, fwd) == 0  # idempotent
    got = labels(con)
    assert got["telegram_ru:mod_russia/1"] == ("telegram_ru", "official")
    assert got["telegram_ru:mod_russia/2"] == ("telegram_unofficial_ru", "media")
    assert got["telegram_ru:council_gov_ru/3"] == ("telegram_unofficial_ru", "media")
