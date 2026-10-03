"""Syrian Ministry of Foreign Affairs and Expatriates (mofaex.gov.sy) news and statements -> docs/SY/mofaex_ar.jsonl.

The current site (Next.js, relaunched under the transitional government) lists news at /news?page=N
(cards with an ISO date, title and /news/<arabic-slug> link). The English interface (/en/news) shows the
same Arabic texts, so documents are Arabic (lang ar). Pre-2025 (Assad-era) ministry pages are not on this
site; coverage starts with whatever the listing holds. Article body = text between the date line under the
<h1> and "شارك هذه المقالة" (share). Date = listing card date (checked against the article page).

Every row carries `period`: "assad" (< 2024-12-08) or "transitional".

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/sy_mofa.py [--max N]
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import State, clean_html, fetch, make_id, setup_logging, write_docs  # noqa: E402

SOURCE = "mofaex_ar"
BASE = "https://mofaex.gov.sy"
START = "2021-01-01"
REGIME_CHANGE = "2024-12-08"
DELAY = 4.0   # robots.txt: Allow /, Crawl-delay 1
CARD = re.compile(r'font-en">(\d{4}-\d\d-\d\d)</span></div><h2[^>]*>(?:<!--\$-->)?<a href="(/(?:en/)?news/[^"]+)">(.*?)</a>', re.S)
log = setup_logging(SOURCE)


def canon(href: str) -> str:
    path = re.sub(r"^/en/", "/", urllib.parse.unquote(href))
    return BASE + urllib.parse.quote(path, safe="/-")


def parse_article(html: str) -> Optional[Dict]:
    html = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", " ", html, flags=re.S)
    t = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    if not t:
        return None
    m = re.search(r">\s*(\d{4}-\d\d-\d\d)\s*<", html[t.end():])
    if not m:
        return None
    body = clean_html(html[t.end() + m.end() - 1:])
    for end in ("شارك هذه المقالة", "Share this article", "Related news", "أخبار ذات صلة"):
        k = body.find(end)
        if k >= 0:
            body = body[:k]
            break
    body = body.replace("​", "").strip()
    return {"title": clean_html(t.group(1)).replace("​", ""), "date": m.group(1), "text": body} if len(body) > 40 else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0)
    a = ap.parse_args()
    st = State(SOURCE)
    items: Dict[str, Dict] = {}
    page = 1
    while True:
        html = fetch(f"{BASE}/news" + (f"?page={page}" if page > 1 else ""), min_delay=DELAY)
        cards = CARD.findall(html or "")
        if not cards:
            log.info("listing ends at page %d", page)
            break
        new = 0
        for date, href, title in cards:
            url = canon(href)
            if url not in items:
                items[url] = {"date": date, "title": clean_html(title).replace("​", "")}
                new += 1
        oldest = min(c[0] for c in cards)
        log.info("page %d: %d cards (%d new), oldest %s", page, len(cards), new, oldest)
        if oldest < START or new == 0:
            break
        page += 1
    log.info("%d items listed", len(items))
    n = 0
    for url, meta in sorted(items.items(), key=lambda kv: kv[1]["date"], reverse=True):
        if st.is_done(url) or meta["date"] < START:
            continue
        html = fetch(url, min_delay=DELAY)
        if not html:
            log.warning("fetch failed (not marked done): %s", url)
            continue
        art = parse_article(html)
        st.mark_done(url)
        if not art:
            log.warning("no date/body, skipped: %s", url)
            continue
        if art["date"] != meta["date"]:
            log.warning("listing date %s != page date %s; using page date: %s", meta["date"], art["date"], url)
        date = art["date"]
        title = art["title"] or meta["title"]
        kind = "statement" if re.match(r"\s*(بيان|تصريح)", title) else "article"
        row = {"id": make_id(SOURCE, url), "country": "SY", "source": SOURCE, "outlet": "official",
               "org": "MFA (mofaex.gov.sy)", "lang": "ar", "date": date, "url": url, "title": title, "speaker": None,
               "kind": kind, "text": art["text"], "via": fetch.last.get("via") or "direct",
               "period": "assad" if date < REGIME_CHANGE else "transitional"}
        n += write_docs("SY", SOURCE, [row])[0]
        if n and n % 20 == 0:
            st.save()
            log.info("%d new docs (at %s)", n, date)
        if a.max and n >= a.max:
            break
    st.save()
    log.info("done: %d new docs", n)


if __name__ == "__main__":
    main()
