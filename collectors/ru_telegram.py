"""Telegram channels of Russian officials, via Wayback copies of the public web preview t.me/s/<channel>/<N>
(t.me itself is unreachable from this host: TLS failure on 2026-10-02, so its robots.txt could not be
read; only archived copies are used). Each preview page shows ~20 posts up to post N; the collector
picks captures spaced <= 15 posts apart from the CDX index, newest first, and stores each post once.

Channels: Zakharova (@MariaVladimirovnaZakharova), Medvedev (@medvedev_telegram).
Rows -> docs/RU/telegram_ru.jsonl (one row per post; kind 'statement'; outlet 'official').

    uv run --project ~/Projects/rhetoric-corpus python collectors/ru_telegram.py [channel ...]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from ru_common import ctext  # noqa: E402

CHANNELS = {"MariaVladimirovnaZakharova": ("Zakharova", "MFA"), "medvedev_telegram": ("Medvedev", "Security Council")}
START = "2021-01-01"
STEP = 15
log = lib.setup_logging("ru_telegram")


def captures(channel: str, st: lib.State) -> List[tuple]:
    key = f"cdx:{channel}"
    if st.get(key):
        return [tuple(x) for x in st[key]]
    q = (f"https://web.archive.org/cdx/search/cdx?url=t.me/s/{channel}/&matchType=prefix&fl=timestamp,original"
         "&filter=statuscode:200&collapse=urlkey&output=json")
    body = lib.fetch(q, min_delay=5, timeout=240, retries=6) or "[]"
    rows = []
    import json
    for ts, orig in json.loads(body)[1:] if body.strip().startswith("[") else []:
        m = re.search(rf"t\.me/s/{channel}/(\d+)(?:\?.*)?$", orig, re.I)
        if m and "?" not in orig:
            rows.append((int(m.group(1)), ts, orig))
    rows.sort(reverse=True)
    # greedy: keep captures so that consecutive kept ids are >= STEP apart (each page holds ~20 posts)
    kept, last = [], None
    for n, ts, orig in rows:
        if last is None or n <= last - STEP:
            kept.append((n, ts, orig))
            last = n
    st[key] = kept
    st.save()
    log.info("%s: %d captures in CDX, %d selected", channel, len(rows), len(kept))
    return kept


def parse_posts(html: str, channel: str) -> List[Dict]:
    spk, org = CHANNELS[channel]
    out = []
    for blk in re.split(r'(?=<div class="tgme_widget_message_wrap)', html)[1:]:
        pm = re.search(rf'data-post="{channel}/(\d+)"', blk, re.I)
        tm = re.search(r'<time[^>]+datetime="(\d{4}-\d{2}-\d{2})', blk)
        txt = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', blk, re.S)
        if not (pm and tm and txt):
            continue
        text = ctext(txt.group(1))
        if len(text) < 20 or tm.group(1) < START:
            continue
        pid = pm.group(1)
        url = f"https://t.me/{channel}/{pid}"
        out.append({"id": lib.make_id("telegram_ru", f"{channel}/{pid}"), "country": "RU", "source": "telegram_ru",
                    "outlet": "official", "org": org, "lang": "ru", "date": tm.group(1), "url": url,
                    "title": text.split("\n", 1)[0][:140], "speaker": spk, "kind": "statement", "text": text,
                    "via": "wayback", "channel": channel})
    return out


def main() -> None:
    chans = sys.argv[1:] or list(CHANNELS)
    for ch in chans:
        # per-channel state; the first run (2026-10-02) used state/ru_telegram.json for Zakharova
        st = lib.State("ru_telegram" if ch == "MariaVladimirovnaZakharova" else f"ru_telegram_{ch}")
        for n, ts, orig in captures(ch, st):
            key = f"{ch}/{n}"
            if st.is_done(key):
                continue
            html = lib.fetch(f"https://web.archive.org/web/{ts}id_/{orig}", min_delay=5, timeout=90,
                             cache=lib.RAW / "telegram" / ch / f"{n}_{ts}.html")
            if html is None:
                log.warning("capture failed %s %s (status %s)", ch, n, lib.fetch.last.get("status"))
                continue
            posts = parse_posts(html, ch)
            added, total = lib.write_docs("RU", "telegram_ru", posts) if posts else (0, 0)
            log.info("%s page %d (%s): %d posts, %d new (file total %d), oldest %s", ch, n, ts, len(posts), added,
                     total, min((p["date"] for p in posts), default="-"))
            st.mark_done(key)
            st.save()
            if posts and max(p["date"] for p in posts) < START:
                break


if __name__ == "__main__":
    main()
