"""State Department press briefings and spokesperson statements (US baseline corpus).

Three feeds, newest first (last 12 months before the backfill):
  wb_brief : www.state.gov/briefings/department-press-briefing-* (2025 ->) via Wayback raw copies.
             www.state.gov answers our User-Agent with HTTP 403 "Technical Difficulties" (bot wall), so
             the live site is not fetched; one capture per unique briefing URL.
  wb_rel   : www.state.gov/releases/office-of-the-spokesperson/YYYY/MM/* (2025 ->) via Wayback:
             Secretary / spokesperson press statements.
  archive  : 2021-2025.state.gov department press briefings (2021-01 -> 2025-01), fetched directly
             from the archived-administration site (robots allows, crawl-delay 5), listed by its
             state_briefing sitemap.
Briefings share an id built from the URL slug, so a briefing seen on both sites is stored once.
Writes docs/US/state_dept.jsonl. Resumable: state/us_state_briefings.json.

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/us_state_briefings.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import State, clean_html, fetch, make_id, setup_logging, write_docs  # noqa: E402
from us_common import decode_body, paragraphs, parse_long_date, title_of  # noqa: E402

SOURCE = "state_dept"
START = "2021-01-01"
MIN_CHARS = 200
MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"


def cdx(prefix: str, log) -> List[Tuple[str, str]]:
    """[(timestamp, original)] latest-ish 200 capture per unique URL under `prefix`, captured 2025->."""
    q = urllib.parse.urlencode({"url": prefix, "matchType": "prefix", "from": "2025", "collapse": "urlkey",
                                "fl": "timestamp,original", "filter": "statuscode:200", "output": "json"})
    body = fetch("https://web.archive.org/cdx/search/cdx?" + q, timeout=300) or "[]"
    try:
        rows = json.loads(body)[1:]
    except ValueError:
        log.warning("CDX parse failed for %s", prefix)
        return []
    return [(r[0], r[1]) for r in rows]


def canon(u: str) -> str:
    u = re.sub(r"[?#].*$", "", u).replace("http://", "https://")
    u = re.sub(r"^https://(www\.)?state\.gov", "https://www.state.gov", u)
    return u if u.endswith("/") else u + "/"


def slug_date(slug: str) -> str:
    """Rough sort key 'YYYY-MM' from a briefing slug like department-press-briefing-july-29-2025."""
    m = re.search(rf"({MONTHS})-(\d{{1,2}})-(\d{{4}})", slug)
    if not m:
        return "0000-00"
    mon = MONTHS.split("|").index(m.group(1)) + 1
    return f"{m.group(3)}-{mon:02d}"


def build_queue(st: State, log) -> List[dict]:
    if st.get("queue"):
        return st["queue"]
    items = {}
    for ts, orig in cdx("www.state.gov/briefings/department-press-briefing", log):
        c = canon(orig)
        slug = c.rstrip("/").rsplit("/", 1)[-1]
        k = slug_date(slug)
        if k < "2025-01":
            continue  # pre-2025 briefings come from the 2021-2025 archive site
        items[c] = dict(feed="wb_brief", url=c, fetch=f"https://web.archive.org/web/{ts}id_/{orig}", key=k,
                        id=make_id(SOURCE, "brief-" + slug), kind="briefing")
    for ts, orig in cdx("www.state.gov/releases/office-of-the-spokesperson/", log):
        c = canon(orig)
        m = re.search(r"/office-of-the-spokesperson/(\d{4})/(\d{2})/[^/]+/$", c)
        if not m:
            continue
        items[c] = dict(feed="wb_rel", url=c, fetch=f"https://web.archive.org/web/{ts}id_/{orig}",
                        key=f"{m.group(1)}-{m.group(2)}", id=make_id(SOURCE, c), kind="statement")
    sm = fetch("https://2021-2025.state.gov/state_briefing-sitemap.xml", min_delay=5) or ""
    for u in re.findall(r"<loc>([^<]+)</loc>", sm):
        slug = u.rstrip("/").rsplit("/", 1)[-1]
        if "department-press-briefing" not in slug and "press-briefing" not in slug:
            continue
        items.setdefault(u, dict(feed="archive", url=u, fetch=u, key=slug_date(slug),
                                 id=make_id(SOURCE, "brief-" + slug), kind="briefing"))
    recent = (date.today() - timedelta(days=365)).strftime("%Y-%m")
    q = sorted(items.values(), key=lambda d: (d["key"] >= recent, d["key"]), reverse=True)
    log.info("queue: %d items (%s)", len(q), {f: sum(1 for d in q if d["feed"] == f) for f in ("wb_brief", "wb_rel", "archive")})
    st["queue"] = q
    st.save()
    return q


def parse(html: str) -> Tuple[Optional[str], str, Optional[str], str]:
    """(date, title, speaker, text) from a state.gov article page."""
    title = title_of(html)
    m = re.search(r'article-meta__publish-date">([^<]+)<', html)
    d = parse_long_date(m.group(1)) if m else None
    m = re.search(r'article-meta__author-bureau">(.*?)</p>', html, re.S)
    speaker = clean_html(m.group(1)).split(",")[0].strip() if m else None
    i = html.find("</section>", html.find("article-meta"))
    j = html.find("<!-- .entry-content -->", i)
    frag = html[i:j] if i >= 0 and j > i else ""
    frag = re.sub(r'<div class="wp-block-summary-article-index.*?</ul>', " ", frag, flags=re.S)
    lines = [l for l in paragraphs(frag).split("\n") if l not in ("Tags", "# # #", "###")]
    return d, title, speaker or None, "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="stop after N new documents (testing)")
    a = ap.parse_args()
    log = setup_logging("us_state_briefings")
    st = State("us_state_briefings")
    added = 0
    queue = build_queue(st, log)
    for rnd in range(3):  # later passes retry fetches that failed (Wayback refuses connections at times)
        if rnd:
            log.info("pass %d: retrying failed items after a pause", rnd + 1)
            time.sleep(600)
        added += run_pass(queue, st, log, a.limit, added)
        if a.limit and added >= a.limit:
            break
    log.info("done, %d new documents", added)


def run_pass(queue: List[dict], st: State, log, limit: int, prior: int) -> int:
    added = 0
    fails = 0  # consecutive failures: back off so a Wayback outage doesn't burn through the queue
    for it in queue:
        if st.is_done(it["url"]):
            continue
        delay = 5.0
        body = decode_body(fetch(it["fetch"], min_delay=delay, binary=True, timeout=120))
        if body is None:
            log.warning("no body (failed, blocked or robots): %s", it["fetch"])
            fails += 1
            time.sleep(min(60 * fails, 900))
            continue
        fails = 0
        d, title, speaker, text = parse(body)
        if not d or d < START or len(text) < MIN_CHARS or title.startswith("Public Schedule"):
            log.warning("skipped (date=%s chars=%d): %s", d, len(text), it["url"])
            st.mark_done(it["url"])
            st.save()
            continue
        row = dict(id=it["id"], country="US", source=SOURCE, outlet="official", org="State Dept", lang="en",
                   date=d, url=it["url"], title=title, speaker=speaker, kind=it["kind"], text=text,
                   via="wayback" if it["feed"].startswith("wb") else "direct")
        n, total = write_docs("US", SOURCE, [row])
        added += n
        st.mark_done(it["url"])
        st.save()
        if n and added % 25 == 0:
            log.info("+%d new this pass (file total %d); last %s %s", added, total, d, it["url"])
        if limit and prior + added >= limit:
            break
    return added


if __name__ == "__main__":
    main()
