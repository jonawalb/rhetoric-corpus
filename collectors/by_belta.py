"""BelTA (Belarusian Telegraph Agency, state news agency), English edition eng.belta.by.

Article URLs need their slug (https://eng.belta.by/<rubric>/view/<slug>-<id>-<year>/) and the site offers no
usable archive (day-archive parameters are ignored, rubric pagination repeats page 1, sitemap = rubric pages
only). So URLs come from:
  1. the Wayback CDX URL index of eng.belta.by/<rubric>/view/ (one query per rubric and year, 2021 ->);
  2. same-rubric links found on every article page fetched (neighbouring stories);
  3. the live rubric front pages (today's items), re-read every 6 h with --follow.
Article pages are then fetched LIVE (robots.txt allows everything except /printv*, *getResults*,
*extendedSearch*); only the CDX listing touches Wayback.

SECTION SAMPLE, ALL TOPICS: rubrics president + politics, article year (from the URL) >= 2021.

    uv run --project ~/Projects/rhetoric-corpus python collectors/by_belta.py [--follow]
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

SOURCE = "belta_en"
BASE = "https://eng.belta.by"
RUBRICS = ["president", "politics"]
YEARS = range(2021, 2027)
DELAY = 5
URL_RE = re.compile(r"https?://eng\.belta\.by/(president|politics)/view/[a-z0-9-]+-(\d+)-(20\d\d)/")
log = lib.setup_logging("by_belta")


def norm(m: re.Match) -> Optional[str]:
    if int(m.group(3)) < YEARS[0]:
        return None
    return "https://" + m.group(0).split("://", 1)[1]


def parse(html: str) -> Optional[Dict]:
    """{title, date, text} from an eng.belta.by article page, or None."""
    h1 = re.search(r"<h1>(.*?)</h1>", html, re.S)
    dm = re.search(r'name="mediator_published_time" content="(\d{4}-\d{2}-\d{2})', html)
    if not dm:  # fallback: <div class="date_full">02 October 2026, 11:54</div>
        df = re.search(r'class="date_full">\s*(\d{1,2}) ([A-Z][a-z]+) (\d{4})', html)
        if df:
            from datetime import datetime
            try:
                dm = [None, datetime.strptime(" ".join(df.groups()), "%d %B %Y").date().isoformat()]
            except ValueError:
                dm = None
    start = re.search(r'<div class="js-mediator-article">', html)
    if not (h1 and dm and start):
        return None
    rest = html[start.end():]
    end = re.search(r'<div class="invite_in_messagers"|<div class="news_tags|<div class="clear"', rest)
    body = rest[: end.start()] if end else rest
    body = re.sub(r'<div class="video_add">.*?</div>', " ", body, flags=re.S)
    text = lib.clean_html(body)
    if len(text) < 100:
        return None
    return {"title": _html.unescape(lib.clean_html(h1.group(1))), "date": dm[1], "text": text}


def cdx_urls(st: lib.State) -> int:
    """Fill st['queue'] from Wayback CDX; returns failures."""
    fails = 0
    for rub in RUBRICS:
        for yr in YEARS:
            key = f"cdx:{rub}:{yr}"
            if st.get(key) is not None:
                continue
            q = (f"https://web.archive.org/cdx/search/cdx?url=eng.belta.by/{rub}/view/&matchType=prefix"
                 f"&from={yr}&to={yr}&collapse=urlkey&fl=original&filter=statuscode:200")
            body = lib.fetch(q, min_delay=5, timeout=300, retries=6)
            if body is None:
                log.warning("cdx %s %s failed (status %s); rest of CDX next pass", rub, yr, lib.fetch.last.get("status"))
                return fails + 1
            urls = {u for u in (norm(m) for m in URL_RE.finditer(body)) if u}
            st[key] = len(urls)
            add(st, urls)
            st.save()
            log.info("cdx %s %s: %d article URLs (queue %d)", rub, yr, len(urls), len(st["queue"]))
    return fails


def add(st: lib.State, urls) -> int:
    q = st.get("queue") or []
    have = set(q)
    new = [u for u in urls if u not in have and not st.is_done(u)]
    st["queue"] = q + new
    return len(new)


def front_pages(st: lib.State) -> None:
    for rub in RUBRICS:
        html = lib.fetch(f"{BASE}/{rub}/", min_delay=DELAY) or ""
        n = add(st, {u for u in (norm(m) for m in URL_RE.finditer(html)) if u})
        log.info("front page %s: %d new URLs", rub, n)
    st.save()


def drain(st: lib.State) -> int:
    """Fetch every queued URL, newest id first. Returns failures."""
    fails, buf, n = 0, [], 0
    while True:
        pending = [u for u in st.get("queue") or [] if not st.is_done(u)]
        if not pending:
            break
        pending.sort(key=lambda u: -int(URL_RE.match(u).group(2)))
        st["queue"] = pending
        progressed = False
        for url in pending:
            html = lib.fetch(url, min_delay=DELAY)
            if html is None:
                status = lib.fetch.last.get("status")
                if status in (404, 410):
                    st.mark_done(url)
                else:
                    fails += 1
                    log.warning("fetch failed %s (status %s)", url, status)
                continue
            progressed = True
            p = parse(html)
            if p:
                m = URL_RE.match(url)
                buf.append({"id": lib.make_id(SOURCE, m.group(2)), "country": "BY", "source": SOURCE,
                            "outlet": "state_media", "org": "BelTA", "lang": "en", "date": p["date"], "url": url,
                            "title": p["title"], "speaker": None, "kind": "article", "text": p["text"],
                            "via": lib.fetch.last.get("via") or "direct", "sample": "section", "section": m.group(1)})
            else:
                log.warning("no article parsed %s", url)
            st.mark_done(url)
            new = add(st, {u for u in (norm(mm) for mm in URL_RE.finditer(html)) if u})
            n += 1
            if len(buf) >= 10:
                added, total = lib.write_docs("BY", SOURCE, buf)
                log.info("wrote %d (total %d), at %s; +%d linked URLs", added, total, buf[-1]["date"], new)
                buf = []
            if n % 20 == 0:
                st.save()
            if new and n % 50 == 0:
                break  # re-sort so newly found URLs take their place in the newest-first order
        if not progressed:
            break
    if buf:
        lib.write_docs("BY", SOURCE, buf)
    st.save()
    return fails


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--follow", action="store_true", help="after the backfill, re-read rubric pages every 6 h")
    a = ap.parse_args()
    st = lib.State("by_belta")
    while True:
        front_pages(st)
        cdx_fails = cdx_urls(st)
        fails = drain(st)
        log.info("pass done: cdx failures %d, fetch failures %d, done %d", cdx_fails, fails, len(st._done))
        if not (a.follow or cdx_fails or fails):
            return
        time.sleep(6 * 3600 if not (cdx_fails or fails) else 1800)


if __name__ == "__main__":
    main()
