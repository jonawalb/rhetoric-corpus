"""Cuban Ministry of Foreign Affairs (MINREX, cubaminrex.cu) English pages -> docs/CU/minrex_en.jsonl,
via Wayback Machine copies only.

cubaminrex.cu does not resolve from here (DNS failure), so article URLs come from the Wayback CDX index
(prefix cubaminrex.cu/en/, HTTP 200 captures since 2021, one row per URL) and each page is read from its
latest raw `id_` capture. Listing/taxonomy/node/user/search URLs and files are dropped. Date = the page's own
Drupal node creation time (schema:dateCreated, a UTC timestamp, so a late-evening Havana posting can carry the next day's date); pages without
a readable date are skipped and logged. Documents dated before --since are dropped.

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/cu_minrex.py [--max N]
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
from lib import State, clean_html, fetch, make_id, setup_logging, wayback_latest, write_docs  # noqa: E402

SOURCE = "minrex_en"
START = "2021-01-01"
SKIP = re.compile(r"/en/(node|taxonomy|user|search|sites|files|print|rss|feed)\b|[?&](page|field_)|\.(jpe?g|png|gif|pdf|css|js|xml|ico)$", re.I)
log = setup_logging(SOURCE)


def cdx_urls() -> List[str]:
    q = {"url": "cubaminrex.cu/en/", "matchType": "prefix", "from": "2021", "filter": "statuscode:200",
         "collapse": "urlkey", "fl": "original", "output": "json", "limit": "100000"}
    body = fetch("https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(q), min_delay=5, timeout=180)
    rows = json.loads(body or "[]")[1:]
    out, seen = [], set()
    for (orig,) in rows:
        p = urllib.parse.urlsplit(orig)
        path = p.path.rstrip("/")
        if SKIP.search(orig) or path.count("/") != 2 or path in ("/en",):
            continue
        canon = "https://cubaminrex.cu" + path
        if canon not in seen:
            seen.add(canon)
            out.append(canon)
    return out


def page_date(html: str) -> Optional[str]:
    """Drupal node creation time (schema:dateCreated, UTC) -> YYYY-MM-DD; None if absent."""
    for pat in (r'property="schema:dateCreated" content="(\d{4}-\d\d-\d\d)', r'article:published_time" content="(\d{4}-\d\d-\d\d)',
                r'"datePublished":\s*"(\d{4}-\d\d-\d\d)'):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None


def parse(html: str) -> Optional[Dict]:
    t = re.search(r'<span property="schema:name" content="([^"]+)"', html) or re.search(r"<title>(.*?)\s*\|", html, re.S)
    i = html.find('class="node--main-content')
    k = html.find('field--name-body', i if i >= 0 else 0)
    d = page_date(html)
    if not (t and k >= 0 and d):
        return None
    body = html[html.find(">", k) + 1:]
    for end in ('<div class="field field--name-field-fuente', '<div class="field field--name-field-tags',
                '<div class="field field--name-field-categorias', "</article>"):
        e = body.find(end)
        if e > 0:
            body = body[:e]
            break
    text = clean_html(body)
    return {"title": clean_html(t.group(1)), "date": d, "text": text} if len(text) > 80 else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--since", default=START)
    a = ap.parse_args()
    st = State(SOURCE)
    urls = st.get("urls") or []
    while not urls:
        urls = cdx_urls()
        if not urls:
            log.warning("CDX listing unavailable (Wayback refusing?); retrying in 30 min")
            time.sleep(1800)
    st["urls"] = urls
    st.save()
    log.info("%d candidate article URLs from CDX; %d done", len(urls), sum(st.is_done(u) for u in urls))
    n, fails = 0, 0
    for url in urls:
        if st.is_done(url):
            continue
        # the live host does not resolve, so go straight to the latest raw Wayback capture
        wb = wayback_latest(url)
        html = fetch(wb, min_delay=5, timeout=120) if wb else None
        if not html:
            fails += 1
            log.warning("no copy (not marked done): %s", url)
            if fails >= 10:
                log.warning("10 failures in a row; pausing 30 min")
                time.sleep(1800)
                fails = 0
            continue
        fails = 0
        art = parse(html)
        st.mark_done(url)
        if not art:
            log.warning("no date/title/body, skipped: %s", url)
            continue
        if art["date"] < a.since:
            continue
        row = {"id": make_id(SOURCE, url), "country": "CU", "source": SOURCE, "outlet": "official",
               "org": "MINREX (MFA)", "lang": "en", "date": art["date"], "url": url, "title": art["title"],
               "speaker": None, "kind": "statement" if re.search(r"(?i)\b(statement|declaration)\b", art["title"] + art["text"][:300]) else "article",
               "text": art["text"], "via": "wayback", "wayback": wb}
        n += write_docs("CU", SOURCE, [row])[0]
        if n and n % 20 == 0:
            st.save()
            log.info("%d new docs", n)
        if a.max and n >= a.max:
            break
    st.save()
    log.info("done: %d new docs", n)


if __name__ == "__main__":
    main()
