"""State Duma news (duma.gov.ru/news/<id>/), Russian. Includes Chairman Vyacheslav Volodin's statements.

News ids are sequential (64201 = 2026-10-01, 52000 = 2021-07-15). The collector reads the newest id from
http://duma.gov.ru/news/ and walks DOWN, fetching every id live, until it has seen 100 consecutive
articles dated before 2021-01-01. ALL TOPICS (no keyword filter). speaker = "Volodin" when the title names
Володин, else null. With --follow it re-reads the listing every 6 h and fetches new ids upward.

robots.txt (2026-10-02): disallows only /search/, /systems/law/?name=, /analytics/tv/. 5 s between requests.

    uv run --project ~/Projects/rhetoric-corpus python collectors/ru_duma.py [--follow] [--start YYYY-MM-DD|earliest]

--start (added 2026-10-03; default 2021-01-01, earliest = 2000-01-01): when the floor is lowered, the downward walk
resumes ~200 ids above where the earlier run stopped (re-checking the ids it rejected as too old) and continues
until 100 consecutive articles are older than the new floor (or id 1).
"""
from __future__ import annotations

import argparse
import html as _html
import re
import sys
import time
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402

SOURCE = "duma_ru"
BASE = "http://duma.gov.ru"
FLOOR = "2021-01-01"
EARLIEST = "2000-01-01"
DELAY = 5
log = lib.setup_logging("ru_duma")


def parse(html: str) -> Optional[Dict]:
    """{title, date, lead, text} from a duma.gov.ru news page, or None."""
    t = re.search(r'<h1 class="article__title"[^>]*>(.*?)</h1>', html, re.S)
    d = re.search(r'<time datetime="(\d{4}-\d{2}-\d{2})[^"]*"\s+itemprop="datePublished"', html)
    c = re.search(r'<div class="article__content">(.*?)<footer class="article__footer"', html, re.S)
    if not (t and d and c):
        return None
    lead = re.search(r'<div class="article__lead">(.*?)</div>', html, re.S)
    lead_t = lib.clean_html(lead.group(1)) if lead else ""
    # inline person cards (photo, name, tooltip with the job title) -> just the name from the photo's alt text
    raw = re.sub(r'<span class="person person--s".*?<span class="person__content-tooltip">.*?</span>',
                 lambda m: " " + ((re.search(r'alt="([^"]*)"', m.group(0)) or [None, ""])[1]) + " ",
                 c.group(1), flags=re.S)
    body = re.sub(r" +([.,;:])", r"\1", lib.clean_html(raw))
    text = (lead_t + "\n" + body).strip() if lead_t and lead_t not in body else body
    if len(text) < 80:
        return None
    return {"title": _html.unescape(lib.clean_html(t.group(1))), "date": d.group(1), "text": text}


def newest_id() -> Optional[int]:
    html = lib.fetch(f"{BASE}/news/", min_delay=DELAY) or ""
    ids = [int(x) for x in re.findall(r'href="/news/(\d+)/"', html)]
    return max(ids) if ids else None


def fetch_id(st: lib.State, i: int, buf: list) -> Optional[str]:
    """Fetch news id i; append a row to buf. Returns its date, 'missing' for 404, or None on failure."""
    url = f"{BASE}/news/{i}/"
    html = lib.fetch(url, min_delay=DELAY)
    if html is None:
        if lib.fetch.last.get("status") in (404, 410):
            st.mark_done(str(i))
            return "missing"
        log.warning("fetch failed %s (status %s)", url, lib.fetch.last.get("status"))
        return None
    st.mark_done(str(i))
    p = parse(html)
    if not p:
        log.warning("no article parsed %s", url)
        return "missing"
    if p["date"] >= FLOOR:
        buf.append({"id": lib.make_id(SOURCE, str(i)), "country": "RU", "source": SOURCE, "outlet": "official",
                    "org": "State Duma", "lang": "ru", "date": p["date"], "url": url, "title": p["title"],
                    "speaker": "Volodin" if "Володин" in p["title"] else None, "kind": "statement",
                    "text": p["text"], "via": lib.fetch.last.get("via") or "direct"})
    return p["date"]


def flush(buf: list, at: str) -> None:
    if buf:
        added, total = lib.write_docs("RU", SOURCE, buf)
        log.info("wrote %d (total %d), at %s", added, total, at)
        buf.clear()


def run(st: lib.State) -> None:
    top = newest_id() or st.get("hi")  # listing unreachable: continue the backfill from the stored cursor
    if not top:
        log.warning("could not read the news listing")
        return
    buf: list = []
    # upward: ids newer than the highest seen before
    hi = st.get("hi") or top
    for i in range(hi + 1, top + 1):
        if not st.is_done(str(i)):
            fetch_id(st, i, buf)
    st["hi"] = max(hi, top)
    flush(buf, "new")
    # downward backfill
    i = st.get("cursor") or top
    old_run = st.get("old_run") or 0
    while i > 0 and old_run < 100:
        if not st.is_done(str(i)):
            d = fetch_id(st, i, buf)
            if d is None:
                time.sleep(60)
                continue  # retry the same id
            if d != "missing":
                old_run = old_run + 1 if d < FLOOR else 0
        i -= 1
        if len(buf) >= 10:
            flush(buf, f"id {i}")
            st["cursor"], st["old_run"] = i, old_run
            st.save()
    flush(buf, f"id {i}")
    st["cursor"], st["old_run"] = i, old_run
    st.save()
    if old_run >= 100:
        log.info("backfill reached %s (id %d)", FLOOR, i)


def main() -> None:
    global FLOOR
    ap = argparse.ArgumentParser()
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--start", default=FLOOR)
    a = ap.parse_args()
    FLOOR = EARLIEST if a.start == "earliest" else a.start
    st = lib.State("ru_duma")
    if (st.get("floor") or "2021-01-01") > FLOOR and st.get("cursor"):
        cur = int(st["cursor"])
        have = {i.split(":", 1)[1] for i in lib.existing_ids(lib.docs_path("RU", SOURCE))}
        for i in range(cur, cur + 200):
            if str(i) not in have:
                st._done.discard(str(i))
        st["cursor"], st["old_run"] = cur + 200, 0
    st["floor"] = FLOOR
    st.save()
    run(st)
    while a.follow:
        time.sleep(6 * 3600)
        run(st)


if __name__ == "__main__":
    main()
