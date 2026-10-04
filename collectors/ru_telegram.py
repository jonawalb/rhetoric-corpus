"""Telegram channels of Russian officials and state media, from the public web preview t.me/s/<channel>.

LIVE mode (default since 2026-10-03): t.me answers again from this host and has no robots.txt (HTTP 404 =
no rules), so the collector walks each channel's public preview page by page, newest -> oldest, with
t.me/s/<channel>?before=<post id> (~20 posts per page, >= 4 s between pages), down to post 1 or --start.
Resumable (per-channel cursor in state/ru_telegram_live.json). With --follow it re-reads every channel's newest
page every 30 min and walks down to the newest post it already has.

WAYBACK mode (--wayback, the original 2026-10-02 method, kept for reference): archived copies of t.me/s/<channel>/<N>
picked from the CDX index (see captures()).

Channels (handles verified 2026-10-03):
  official  -> docs/RU/telegram_ru.jsonl        Zakharova, Medvedev, MFA (MID_Russia), MoD (mod_russia),
                                                 Government (government_rus), Federation Council (council_gov_ru),
                                                 Duma chairman Volodin (vv_volodin)
  state media -> docs/RU/telegram_media_ru.jsonl RIA Novosti (rian_ru), TASS (tass_agency) -- run after the official ones
One row per post with text (>= 20 chars; photo-only posts skipped); `forwarded` = True when the post is a forward.
ALL TOPICS, no filter.

    uv run --project ~/Projects/rhetoric-corpus python collectors/ru_telegram.py [--follow] [--start 2000-01-01] [channel ...]
    uv run --project ~/Projects/rhetoric-corpus python collectors/ru_telegram.py --wayback [channel ...]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from ru_common import ctext, robots_ok, write_docs_fast  # noqa: E402

# handle -> (speaker, org, source file, outlet)
CHANNELS: Dict[str, Tuple[Optional[str], str, str, str]] = {
    "MariaVladimirovnaZakharova": ("Zakharova", "MFA", "telegram_ru", "official"),
    "medvedev_telegram": ("Medvedev", "Security Council", "telegram_ru", "official"),
    "MID_Russia": (None, "MFA", "telegram_ru", "official"),
    "mod_russia": (None, "MoD", "telegram_ru", "official"),
    "government_rus": (None, "Government", "telegram_ru", "official"),
    "council_gov_ru": (None, "Federation Council", "telegram_ru", "official"),
    "vv_volodin": ("Volodin", "State Duma", "telegram_ru", "official"),
    "rian_ru": (None, "RIA Novosti", "telegram_media_ru", "state_media"),
    "tass_agency": (None, "TASS", "telegram_media_ru", "state_media"),
}
WAYBACK_START = "2021-01-01"  # floor of the original Wayback mode
STEP = 15
DELAY = 4
log = lib.setup_logging("ru_telegram")


def parse_posts(html: str, channel: str, start: str) -> Tuple[List[Dict], List[int]]:
    """(rows, all post ids seen on the page). Rows only for posts with >= 20 chars of text dated >= start."""
    spk, org, src, outlet = CHANNELS[channel]
    out, ids = [], []
    for blk in re.split(r'(?=<div class="tgme_widget_message_wrap)', html)[1:]:
        pm = re.search(rf'data-post="{channel}/(\d+)"', blk, re.I)
        if not pm:
            continue
        ids.append(int(pm.group(1)))
        tm = re.search(r'<time[^>]+datetime="(\d{4}-\d{2}-\d{2})', blk)
        txt = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', blk, re.S)
        if not (tm and txt):
            continue
        text = ctext(txt.group(1))
        if len(text) < 20 or tm.group(1) < start:
            continue
        pid = pm.group(1)
        out.append({"id": lib.make_id(src, f"{channel}/{pid}"), "country": "RU", "source": src,
                    "outlet": outlet, "org": org, "lang": "ru", "date": tm.group(1), "url": f"https://t.me/{channel}/{pid}",
                    "title": text.split("\n", 1)[0][:140], "speaker": spk, "kind": "statement", "text": text,
                    "via": "direct", "channel": channel, "forwarded": "tgme_widget_message_forwarded_from" in blk})
    return out, ids


def _page(channel: str, before: Optional[int]) -> Optional[str]:
    url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
    if not robots_ok(url):
        log.warning("robots disallows %s", url)
        return None
    return lib.fetch(url, min_delay=DELAY, timeout=60)


def _store(rows: List[Dict]) -> Tuple[int, int]:
    added, total = 0, 0
    for src in sorted({r["source"] for r in rows}):
        a, total = write_docs_fast("RU", src, [r for r in rows if r["source"] == src])
        added += a
    return added, total


def walk_down(channel: str, st: lib.State, start: str) -> None:
    """Backfill: newest page first, then ?before=<lowest id seen> until post 1 or posts older than start."""
    key = f"live:{channel}"
    cur = st.get(key) or {}
    if cur.get("complete"):
        return
    before = cur.get("cursor")
    fails = 0
    while True:
        html = _page(channel, before)
        if html is None:
            fails += 1
            log.warning("%s page before=%s failed (status %s)", channel, before, lib.fetch.last.get("status"))
            if fails >= 5:
                return  # cursor kept; the next run / follow loop resumes here
            time.sleep(60)
            continue
        fails = 0
        rows, ids = parse_posts(html, channel, start)
        if not ids:
            cur["complete"] = True
            break
        if before is None:
            cur["top"] = max(ids)
        added, total = _store(rows) if rows else (0, 0)
        oldest = min((r["date"] for r in rows), default="-")
        log.info("%s before=%s: %d posts, %d rows, %d new (file total %d), oldest %s", channel, before, len(ids),
                 len(rows), added, total, oldest)
        before = min(ids)
        cur["cursor"] = before
        st[key] = cur
        st.save()
        if before <= 1 or (oldest != "-" and oldest < start):
            cur["complete"] = True
            break
    st[key] = cur
    st.save()
    log.info("%s: backfill complete", channel)


def catch_up(channel: str, st: lib.State, start: str) -> None:
    """Newest posts: walk down from the newest page until the highest id stored before (`top`)."""
    key = f"live:{channel}"
    cur = st.get(key) or {}
    top = cur.get("top")
    if not top:
        return
    before, new_top = None, None
    while True:
        html = _page(channel, before)
        if html is None:
            return
        rows, ids = parse_posts(html, channel, start)
        if not ids:
            break
        new_top = new_top or max(ids)
        fresh = [r for r in rows if int(r["url"].rsplit("/", 1)[1]) > top]
        added, _ = _store(fresh) if fresh else (0, 0)
        log.info("%s follow before=%s: %d new", channel, before, added)
        if min(ids) <= top + 1:
            break
        before = min(ids)
    if new_top:
        cur["top"] = max(top, new_top)
        st[key] = cur
        st.save()


# --------------------------------------------------------------------------------------- wayback mode
def captures(channel: str, st: lib.State) -> List[tuple]:
    key = f"cdx:{channel}"
    if st.get(key):
        return [tuple(x) for x in st[key]]
    q = (f"https://web.archive.org/cdx/search/cdx?url=t.me/s/{channel}/&matchType=prefix&fl=timestamp,original"
         "&filter=statuscode:200&collapse=urlkey&output=json")
    body = lib.fetch(q, min_delay=5, timeout=240, retries=6) or "[]"
    rows = []
    for ts, orig in json.loads(body)[1:] if body.strip().startswith("[") else []:
        m = re.search(rf"t\.me/s/{channel}/(\d+)(?:\?.*)?$", orig, re.I)
        if m and "?" not in orig:
            rows.append((int(m.group(1)), ts, orig))
    rows.sort(reverse=True)
    kept, last = [], None  # keep captures >= STEP ids apart (each page holds ~20 posts)
    for n, ts, orig in rows:
        if last is None or n <= last - STEP:
            kept.append((n, ts, orig))
            last = n
    st[key] = kept
    st.save()
    log.info("%s: %d captures in CDX, %d selected", channel, len(rows), len(kept))
    return kept


def wayback(ch: str) -> None:
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
        posts, _ = parse_posts(html, ch, WAYBACK_START)
        for p in posts:
            p["via"] = "wayback"
        added, total = _store(posts) if posts else (0, 0)
        log.info("%s page %d (%s): %d posts, %d new (file total %d)", ch, n, ts, len(posts), added, total)
        st.mark_done(key)
        st.save()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("channels", nargs="*", help=f"default: all of {list(CHANNELS)}")
    ap.add_argument("--wayback", action="store_true", help="original Wayback-capture mode")
    ap.add_argument("--follow", action="store_true", help="live: keep polling every channel every 30 min")
    ap.add_argument("--start", default="2000-01-01", help="live: oldest post date to keep (YYYY-MM-DD)")
    a = ap.parse_args()
    chans = a.channels or list(CHANNELS)
    if a.wayback:
        for ch in chans:
            wayback(ch)
        return
    st = lib.State("ru_telegram_live")
    for ch in chans:  # newest posts of every channel first, then the deep walks
        if (st.get(f"live:{ch}") or {}).get("top"):
            catch_up(ch, st, a.start)
    for ch in chans:
        walk_down(ch, st, a.start)
    while a.follow:
        time.sleep(1800)
        for ch in chans:
            catch_up(ch, st, a.start)
            walk_down(ch, st, a.start)  # resumes an unfinished walk (e.g. after failures)


if __name__ == "__main__":
    main()
