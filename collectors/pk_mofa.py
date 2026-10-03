"""Pakistan MOFA (mofa.gov.pk): spokesperson weekly press-briefing transcripts, statements, speeches and
press releases, 2021-01-01 -> today, newest first.

Listing cards carry the publication date ("01 Oct 2026"); article pages carry title + body
(div.m-5.text-justify). Listings are walked until cards fall before START; articles are then fetched
newest first. Resumable: state/pk_mofa.json (done article URLs) + raw HTML cache in raw/pk_mofa/.

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/pk_mofa.py [--max N]
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import RAW, State, clean_html, fetch, make_id, setup_logging, write_docs  # noqa: E402

SOURCE = "pk_mofa"
BASE = "https://mofa.gov.pk"
START = "2021-01-01"
# (listing path, kind); media briefings first so transcripts land early.
LISTINGS = [("/press-releases/categories/media-briefings", "briefing"),
            ("/press-releases/categories/statements", "statement"),
            ("/press-releases/categories/speeches", "speech"),
            ("/press-releases", "statement")]
BLOCKED = "Web Page Blocked"
log = setup_logging(SOURCE)

CARD = re.compile(r'<a href="(https://mofa\.gov\.pk/press-releases/[^"?]+)(?:\?[^"]*)?">\s*<div class="card">.*?'
                  r'<h5[^>]*>(.*?)</h5>.*?fa-calendar-alt me-1"></i>\s*([^<]+?)\s*</p>', re.S)


def parse_date(s: str) -> Optional[str]:
    s = s.replace(",", " ").strip()
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(re.sub(r"\s+", " ", s), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def get(url: str, cache: Optional[Path] = None) -> Optional[str]:
    body = fetch(url, min_delay=4, cache=cache)
    if body and BLOCKED in body[:3000]:
        log.error("network filter block page for %s; not used", url)
        if cache is not None and cache.exists():
            cache.unlink()
        return None
    return body


def walk_listings(st: State) -> Dict[str, Dict]:
    items: Dict[str, Dict] = dict(st.get("items") or {})
    for path, kind in LISTINGS:
        page = 1
        while True:
            url = f"{BASE}{path}" + (f"?page={page}" if page > 1 else "")
            html = get(url)
            if not html:
                log.warning("listing failed: %s", url)
                break
            cards = CARD.findall(html)
            if not cards:
                break
            oldest = "9999"
            new = 0
            for href, title, d in cards:
                date = parse_date(d)
                if not date:
                    continue
                oldest = min(oldest, date)
                if date < START:
                    continue
                if href not in items:
                    new += 1
                    items[href] = {"title": clean_html(title), "date": date, "kind": kind}
                elif kind != "statement":   # a specific category beats the generic listing
                    items[href]["kind"] = kind
            log.info("%s page %d: %d cards, %d new, oldest %s", path, page, len(cards), new, oldest)
            if oldest < START:
                break
            page += 1
        st["items"] = items
        st.save()
    return items


def speaker_of(text: str) -> Optional[str]:
    m = re.search(r"(?:Spokesperson|Spokeswoman|Spokesman)[,:]?\s+(?:Ambassador\s+|Mr\.?\s+|Ms\.?\s+)?"
                  r"([A-Z][a-z]+(?: [A-Z][a-z]+){1,3})", text[:600])
    return m.group(1) if m else None


def parse_article(html: str) -> Optional[Dict]:
    m = re.search(r'<div class="filter-title">\s*<h2[^>]*>(.*?)</h2>', html, re.S)
    title = clean_html(m.group(1)) if m else ""
    i = html.find('<div class="m-5 text-justify">')
    if i < 0:
        return None
    j = html.find("</section>", i)
    body = html[i:j if j > 0 else None]
    # cut the related-articles cards that follow the article body
    k = body.find('<div class="card')
    if k > 0:
        body = body[:k]
    text = clean_html(body)
    text = re.split(r"\n(?:Related Stories|Print|Share)\s*(?:\n|$)", text)[0].strip()
    return {"title": title, "text": text} if len(text) > 80 else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0, help="stop after N new documents (0 = all)")
    ap.add_argument("--skip-listings", action="store_true")
    a = ap.parse_args()
    st = State(SOURCE)
    items = dict(st.get("items") or {}) if a.skip_listings else walk_listings(st)
    order = sorted(items.items(), key=lambda kv: kv[1]["date"], reverse=True)
    log.info("%d items since %s; %d already done", len(order), START, sum(st.is_done(u) for u, _ in order))
    n = 0
    for url, meta in order:
        if st.is_done(url):
            continue
        cache = RAW / SOURCE / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".html")
        html = get(url, cache=cache)
        if not html:
            continue
        art = parse_article(html)
        if not art:
            log.warning("no body: %s", url)
            st.mark_done(url)
            continue
        title = art["title"] or meta["title"]
        kind = "briefing" if re.search(r"(?i)press briefing|media briefing", title) else meta["kind"]
        row = {"id": make_id(SOURCE, url), "country": "PK", "source": SOURCE, "outlet": "official", "org": "MOFA",
               "lang": "en", "date": meta["date"], "url": url, "title": title,
               "speaker": speaker_of(art["text"]) if kind == "briefing" else None, "kind": kind,
               "text": art["text"], "via": "direct"}
        write_docs("PK", SOURCE, [row])
        st.mark_done(url)
        n += 1
        if n % 10 == 0:
            st.save()
            log.info("%d new docs (at %s)", n, meta["date"])
        if a.max and n >= a.max:
            break
    st.save()
    log.info("done: %d new docs", n)


if __name__ == "__main__":
    main()
