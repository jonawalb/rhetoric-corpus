"""Department of Defense / Department of War transcripts and press releases (US baseline corpus), via Wayback.

  www.defense.gov/News/Transcripts/Transcript/Article/<id>/<slug>/   (2021 -> 2025-09)
  www.war.gov/News/Transcripts/Transcript/Article/<id>/<slug>/       (2025-09 rename ->)
  www.defense.gov/News/Releases/Release/Article/<id>/<slug>/  and the same under www.war.gov

Both hosts answer our User-Agent with HTTP 403 (bot wall, robots.txt included), so the live site is not fetched:
the Wayback CDX lists every captured article URL (from 2021) and the newest capture of each article id is read
(raw `id_` copy). defense.gov and war.gov share article ids (same CMS), so an article captured under both names is
stored once (id us_dod:<article id>). Coverage = what Wayback captured: ~3,400 + ~2,400 transcript URLs and
~8,800 + ~6,600 release URLs (2026-10), many slugs per article.

kind: transcript (remarks, interviews, press conferences), briefing (titles with "Briefing"), statement (releases).
org = Department of Defense, or Department of War from 2025-09-05 (the renaming order). Date = the page's date line ("Sept. 15, 2026"); documents dated before 2021 are skipped. Transcripts first, then releases, newest article ids first;
the queue is rebuilt from the CDX weekly. Writes docs/US/us_dod.jsonl; state/us_dod.json.

Run: uv run python collectors/us_dod.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cn_common import balanced_div  # noqa: E402  (generic HTML helper)
from lib import State, clean_html, fetch, make_id, now_iso, setup_logging, write_docs  # noqa: E402
from us_common import decode_body  # noqa: E402

SOURCE = "us_dod"
START = "2021-01-01"
MIN_CHARS = 200
QUEUE_MAX_AGE_S = 7 * 86400
PREFIXES = [(host + path, kind) for path, kind in (("/News/Transcripts/Transcript/Article/", "transcript"),
                                                    ("/News/Releases/Release/Article/", "statement"))
            for host in ("www.war.gov", "www.defense.gov")]
ART_RE = re.compile(r"/Article/(\d{6,8})(?:/|$)")
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}

log = setup_logging(SOURCE)


def cdx(prefix: str) -> Optional[List[List[str]]]:
    q = urllib.parse.urlencode({"url": prefix, "matchType": "prefix", "from": START[:4], "collapse": "urlkey",
                                "fl": "timestamp,original", "filter": "statuscode:200", "output": "json"})
    body = fetch("https://web.archive.org/cdx/search/cdx?" + q, min_delay=5, timeout=300)
    if body is None:
        return None
    try:
        return json.loads(body or "[]")[1:]
    except ValueError:
        log.warning("CDX parse failed for %s", prefix)
        return None


def build_queue(st: State) -> List[Dict]:
    if st.get("queue") and time.time() - st.get("queue_built", 0) < QUEUE_MAX_AGE_S:
        return st["queue"]
    items: Dict[str, Dict] = {}
    for prefix, kind in PREFIXES:
        rows = cdx(prefix)
        if rows is None:
            log.warning("CDX unavailable for %s; keeping the old queue", prefix)
            return st.get("queue") or []
        for ts, orig in rows:
            m = ART_RE.search(orig)
            if not m:
                continue
            aid = m.group(1)
            if aid not in items or ts > items[aid]["ts"]:
                items[aid] = {"aid": aid, "ts": ts, "orig": orig, "kind": kind}
        log.info("CDX %s: %d rows", prefix, len(rows))
    q = sorted(items.values(), key=lambda d: (d["kind"] == "transcript", int(d["aid"])), reverse=True)
    st["queue"], st["queue_built"] = q, time.time()
    st.save()
    log.info("queue: %d articles", len(q))
    return q


def parse_date(s: str) -> Optional[str]:
    m = re.search(r"([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2}),\s*(\d{4})", s)
    if not m or m.group(1).lower() not in MONTHS:
        return None
    return f"{m.group(3)}-{MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"


def parse(html: str) -> Optional[Dict]:
    """{title, date, text} of a defense.gov / war.gov article page."""
    mt = re.search(r'<h1 class="maintitle">(.*?)</h1>', html, re.S)
    md = re.search(r'<span class="date">(.*?)</span>', html, re.S)
    body = balanced_div(html, 'class="body"')
    if not mt or not md or body is None:
        return None
    title = re.sub(r"\s+", " ", clean_html(mt.group(1))).strip()
    d = parse_date(clean_html(md.group(1)))
    text = clean_html(body)
    if not title or not d:
        return None
    return {"title": title, "date": d, "text": text}


def kind_of(feed_kind: str, title: str) -> str:
    if feed_kind == "transcript" and re.search(r"\bBriefing\b", title, re.I):
        return "briefing"
    return feed_kind


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="stop after N new documents (testing)")
    a = ap.parse_args()
    st = State(SOURCE)
    queue = build_queue(st)
    n = fails = 0
    for it in queue:
        key = it["aid"]
        if st.is_done(key):
            continue
        raw = fetch(f"https://web.archive.org/web/{it['ts']}id_/{it['orig']}", min_delay=5, binary=True, timeout=120)
        html = decode_body(raw)
        if html is None:
            if fetch.last.get("status", 0) in (404, 410):
                st.mark_done(key)
                continue
            fails += 1  # refused / timeout / filter page: not marked done
            if fails >= 5:
                log.warning("Wayback unreachable (5 failures in a row); stopping, re-run to resume")
                break
            time.sleep(min(60 * fails, 600))
            continue
        fails = 0
        p = parse(html)
        if not p or p["date"] < START or len(p["text"]) < MIN_CHARS:
            log.warning("skipped (parsed=%s): %s", bool(p) and (p["date"], len(p["text"])), it["orig"])
            st.mark_done(key)
            st.save()
            continue
        url = re.sub(r"^http://", "https://", it["orig"])
        row = dict(id=make_id(SOURCE, key), country="US", source=SOURCE, outlet="official",
                   org="Department of War" if p["date"] >= "2025-09-05" else "Department of Defense", lang="en",
                   date=p["date"], url=url, title=p["title"], speaker=None, kind=kind_of(it["kind"], p["title"]),
                   text=p["text"], via="wayback", fetched=now_iso(), wayback_ts=it["ts"])
        added, total = write_docs("US", SOURCE, [row])
        n += added
        st.mark_done(key)
        st.save()
        if added and n % 25 == 0:
            log.info("+%d (file total %d); last %s %s", n, total, p["date"], url)
        if a.limit and n >= a.limit:
            break
    log.info("finished: %d new docs", n)


if __name__ == "__main__":
    main()
