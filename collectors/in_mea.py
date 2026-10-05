"""India MEA (mea.gov.in): media-briefing transcripts, speeches/statements, press releases and interviews,
2021-01-01 -> today. Press releases (51) and interviews (52) added 2026-10-05; before that only 49/50 were
collected (~250 docs a year).

The site's own listing and detail endpoints (the same ones its pages call):
  /FrontEnd/FetchPublicationListingData?publicationId=49|50|51|52&SortBy=new&page=N&PageSize=50&PLngId=1
  /FrontEnd/FetchPublicationDetailData?pkid=<id>&languageId=1
Listing cards give date ("30 September, 2026"), title and the public detail URL
(/media-briefings?dtl/<pkid>/<slug>); the detail fragment gives the full text (div.description).
Resumable: state/in_mea.json; raw detail fragments cached in raw/in_mea/.

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/in_mea.py [--max N]
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import RAW, State, clean_html, fetch, make_id, setup_logging, write_docs  # noqa: E402

SOURCE = "in_mea"
BASE = "https://www.mea.gov.in"
START = "2021-01-01"
# 49 Media Briefings, 50 Speeches & Statements, 51 Press Releases, 52 Interviews
PUBS = [(49, "briefing"), (50, "statement"), (51, "statement"), (52, "interview")]
BLOCKED = "Web Page Blocked"
log = setup_logging(SOURCE)

CARD = re.compile(r'<span class="date">\s*([^<]+?)\s*</span>.*?<h3 class="pressTitle">\s*<a href="([^"]+)">\s*(.*?)\s*</a>',
                  re.S)


def parse_date(s: str) -> Optional[str]:
    s = re.sub(r"\s+", " ", s.replace(",", " ")).strip()
    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
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


def walk(st: State) -> Dict[str, Dict]:
    items: Dict[str, Dict] = dict(st.get("items") or {})
    for pub, kind in PUBS:
        page = 1
        while True:
            url = (f"{BASE}/FrontEnd/FetchPublicationListingData?publicationId={pub}&KeywordName=&SortBy=new"
                   f"&page={page}&PageSize=50&DateRange=&IsInternalMEA=false&PLngId=1")
            html = get(url)
            cards = CARD.findall(html or "")
            if not cards:
                log.warning("listing %s page %d empty", pub, page)
                break
            oldest, new = "9999", 0
            for d, href, title in cards:
                date = parse_date(d)
                m = re.search(r"\?dtl/(\d+)/", href)
                if not date or not m:
                    continue
                oldest = min(oldest, date)
                if date >= START and m.group(1) not in items:
                    items[m.group(1)] = {"date": date, "title": clean_html(title), "kind": kind,
                                         "url": BASE + href if href.startswith("/") else href}
                    new += 1
            log.info("pub %d page %d: %d cards, oldest %s, total %d", pub, page, len(cards), oldest, len(items))
            if oldest < START:
                st[f"walked:{pub}"] = True  # listing walked back to START once
                break
            if not new and st.get(f"walked:{pub}"):
                break  # incremental run: the rest of the listing is known
            page += 1
        st["items"] = items
        st.save()
    return items


SPK = re.compile(r'<span class="fw-bold">\s*(?:Shri|Smt\.?|Dr\.?)?\s*([^<:,]+?),\s*Official Spokesperson', re.I)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--skip-listings", action="store_true")
    a = ap.parse_args()
    st = State(SOURCE)
    items = dict(st.get("items") or {}) if a.skip_listings else walk(st)
    order = sorted(items.items(), key=lambda kv: kv[1]["date"], reverse=True)
    log.info("%d items since %s", len(order), START)
    n = 0
    for pkid, meta in order:
        if st.is_done(pkid):
            continue
        html = get(f"{BASE}/FrontEnd/FetchPublicationDetailData?pkid={pkid}&languageId=1",
                   cache=RAW / SOURCE / f"{pkid}.html")
        if not html:
            continue
        i = html.find('<div class="description">')
        j = html.find('<div class="d-flex tags', i) if i >= 0 else -1
        if i < 0:
            log.warning("no body: %s", pkid)
            st.mark_done(pkid)
            continue
        text = clean_html(html[i:j if j > 0 else html.find("<script", i)])
        mt = re.search(r'<h2 class="titleText[^"]*">(.*?)</h2>', html, re.S)
        title = clean_html(mt.group(1)) if mt else meta["title"]
        if len(text) < 80:
            st.mark_done(pkid)
            continue
        kind = "briefing" if re.search(r"(?i)media briefing|press briefing", title) else meta["kind"]
        ms = SPK.search(html)
        row = {"id": make_id(SOURCE, pkid), "country": "IN", "source": SOURCE, "outlet": "official", "org": "MEA",
               "lang": "en", "date": meta["date"], "url": meta["url"], "title": title,
               "speaker": ms.group(1).strip() if (ms and kind == "briefing") else None, "kind": kind,
               "text": text, "via": "direct"}
        write_docs("IN", SOURCE, [row])
        st.mark_done(pkid)
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
