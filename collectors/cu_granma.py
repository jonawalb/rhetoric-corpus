"""Granma International, English edition of the Communist Party of Cuba daily (en.granma.cu)
-> docs/CU/granma_en.jsonl.

Article URLs carry the publication date: /<section>/YYYY-MM-DD/<slug>. The site has no sitemap and its
section pages do not paginate, so URLs come from the site's own archive listing /archivo?s=<section>&page=N
(10 results per page, not in date order, all years): sections 2 Cuba (~597 pages), 3 World (~258) and
61 "Speeches by President Díaz-Canel" (~2). Every page of each listing is read once (state remembers the
last page), URLs dated >= --since are kept, then articles are fetched newest first.
Body = div.story-body-text paragraphs (figures/captions dropped); the URL date is checked against the
date line on the page (mismatches are logged, URL date kept).

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/cu_granma.py [--max N]
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import State, clean_html, fetch, make_id, setup_logging, write_docs  # noqa: E402

SOURCE = "granma_en"
BASE = "https://en.granma.cu"
START = "2021-01-01"
DELAY = 4.0   # robots.txt: Joomla/Drupal defaults only (/administrator/, /modules/ …); /archivo and articles allowed
SECTIONS = {"61": "speeches-diaz-canel", "3": "mundo", "2": "cuba"}
ITEM = re.compile(r'<article class="g-searchpage-story">.*?<h2><a href="/?([a-z0-9-]+/(\d{4}-\d\d-\d\d)/[^"]+)">(.*?)</a>', re.S)
log = setup_logging(SOURCE)


def parse(html: str) -> Optional[Dict]:
    t = re.search(r'<h1 itemprop="headline" class="g-story-heading">(.*?)</h1>', html, re.S)
    i = html.find('class="story-body-text')
    if not (t and i >= 0):
        return None
    body = html[i:]
    for end in ("<aside", '<footer class="g-story-footer"', "</article>"):
        k = body.find(end)
        if k > 0:
            body = body[:k]
            break
    body = re.sub(r"<figure\b.*?</figure>", " ", body[body.find(">") + 1:], flags=re.S)
    text = clean_html(body)
    m = re.search(r'class="g-story-meta-footer".*?([a-z]+ \d{1,2}, \d{4})\s+\d\d:\d\d', html, re.S | re.I)
    page_date = None
    if m:
        try:
            page_date = datetime.strptime(m.group(1).title(), "%B %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    a = re.search(r'<span class="byline-author" itemprop="name">(.*?)</span>', html, re.S)
    sub = re.search(r'<p class="g-story-description"[^>]*>(.*?)</p>', html, re.S)
    return {"title": clean_html(t.group(1)), "text": text, "page_date": page_date,
            "author": clean_html(a.group(1)) if a else None,
            "description": clean_html(sub.group(1)) if sub else None} if len(text) > 80 else None


def walk(st: State, since: str, sections: Dict[str, str]) -> Dict[str, Dict]:
    items: Dict[str, Dict] = dict(st.get("items") or {})
    for s, label in sections.items():
        if st.get(f"listing_{s}_finished"):
            continue
        page = int(st.get(f"listing_{s}_page") or 1)
        fails = 0
        while True:
            html = fetch(f"{BASE}/archivo?page={page}&q=&s={s}", min_delay=DELAY, timeout=90)
            if html is None:
                fails += 1
                log.warning("listing %s page %d failed (%d)", label, page, fails)
                if fails >= 5:
                    break
                continue
            fails = 0
            found = ITEM.findall(html)
            last = max([int(x) for x in re.findall(r"archivo\?page=(\d+)&amp;|archivo\?page=(\d+)&", html) for x in x if x]
                       + [int(st.get(f"listing_{s}_last") or 0)])
            st[f"listing_{s}_last"] = last
            if not found or page > last:
                log.info("listing %s ends at page %d", label, page)
                st[f"listing_{s}_finished"] = True
                break
            kept = 0
            for path, date, title in found:
                url = f"{BASE}/{path}"
                if date >= since and url not in items:
                    items[url] = {"date": date, "title": clean_html(title), "listing": label}
                    kept += 1
            if page % 10 == 0:
                log.info("listing %s page %d: %d results, %d kept (total %d)", label, page, len(found), kept, len(items))
            page += 1
            st[f"listing_{s}_page"] = page
            st["items"] = items
            if page % 10 == 0:
                st.save()
        st["items"] = items
        st.save()
    return items


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--since", default=START)
    ap.add_argument("--skip-listings", action="store_true")
    a = ap.parse_args()
    st = State(SOURCE)
    n = 0
    # one listing at a time, then its articles, so documents start landing early
    for s, label in (list(SECTIONS.items()) + [("", "")]):
        if s and a.skip_listings:
            continue
        items = walk(st, a.since, {s: label}) if s else dict(st.get("items") or {})
        log.info("after listing %s: %d article URLs dated >= %s; %d done", label or "(all)", len(items), a.since,
                 sum(st.is_done(u) for u in items))
        n = fetch_articles(st, items, n, a.max, log)
        if a.max and n >= a.max:
            break
    st.save()
    log.info("done: %d new docs", n)


def fetch_articles(st: State, items: Dict[str, Dict], n: int, max_new: int, log) -> int:
    for url, meta in sorted(items.items(), key=lambda kv: kv[1]["date"], reverse=True):
        if st.is_done(url):
            continue
        html = fetch(url, min_delay=DELAY, timeout=90)
        if html is None:
            log.warning("fetch failed (not marked done): %s", url)
            continue
        art = parse(html)
        st.mark_done(url)
        if not art:
            log.warning("no title/body, skipped: %s", url)
            continue
        if art["page_date"] and art["page_date"] != meta["date"]:
            log.warning("URL date %s != page date %s (URL date kept): %s", meta["date"], art["page_date"], url)
        section = url.split("/")[3]
        speech = section == "speeches-diaz-canel" or meta["listing"] == "speeches-diaz-canel"
        row = {"id": make_id(SOURCE, url), "country": "CU", "source": SOURCE, "outlet": "state_media",
               "org": "Granma", "lang": "en", "date": meta["date"], "url": url, "title": art["title"],
               "speaker": "Miguel Díaz-Canel" if speech else None, "kind": "transcript" if speech else "article",
               "text": art["text"], "via": fetch.last.get("via") or "direct", "section": section,
               "author": art["author"], "description": art["description"]}
        n += write_docs("CU", SOURCE, [row])[0]
        if n and n % 20 == 0:
            st.save()
            log.info("%d new docs (at %s)", n, meta["date"])
        if max_new and n >= max_new:
            break
    st.save()
    return n


if __name__ == "__main__":
    main()
