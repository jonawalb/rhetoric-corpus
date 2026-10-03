"""Taiwan (ROC) Ministry of Foreign Affairs press releases and statements, 2021-01-01 -> today, newest first.

  --lang zh  www.mofa.gov.tw  新聞稿 (News.aspx?n=96) + 聲明及回應 (n=97)         -> docs/TW/tw_mofa_zh.jsonl
  --lang en  en.mofa.gov.tw   Press Releases (n=1329) + Statements and Responses (n=1330) -> docs/TW/tw_mofa_en.jsonl

Listing tables (PageSize=100) carry the publication date (YYYY-MM-DD, Gregorian) and the item id `s=`;
listings are walked until a whole page falls before START. Article pages give the title (<h3> in "simple-text title")
and body (div.essay). The listing date is the document date; the dateline inside the body is not used.
A row that is in both listings keeps the first category seen (press release). Resumable:
state/tw_mofa_<lang>.json (items + done ids). Article pages are not cached on disk.

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/tw_mofa.py --lang zh|en [--max N]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import State, clean_html, fetch, make_id, setup_logging, write_docs  # noqa: E402
from tw_common import START, ad_date  # noqa: E402

SITES = {
    "zh": ("https://www.mofa.gov.tw", [(96, 74, "press_release"), (97, 75, "statement_response")]),
    "en": ("https://en.mofa.gov.tw", [(1329, 272, "press_release"), (1330, 274, "statement_response")]),
}
ROW = re.compile(r'data-title="[^"]*"[^>]*><span>\s*(\d{4}-\d{2}-\d{2})\s*</span></td>\s*<td[^>]*>\s*<span>\s*'
                 r'<a href="News_Content\.aspx\?n=(\d+)(?:&amp;|&)(?:sms=\d+(?:&amp;|&))?s=(\d+)"[^>]*title="([^"]*)"', re.S)


def parse_listing(html: str):
    """[(date, n, s, title)] from a News.aspx table page."""
    return [(d, n, s, clean_html(t)) for d, n, s, t in ROW.findall(html)]


def parse_article(html: str) -> Optional[Dict]:
    m = re.search(r'class="simple-text title".*?<h3>(.*?)</h3>', html, re.S)
    title = clean_html(m.group(1)) if m else ""
    i = html.find('<div class="essay">')
    if i < 0:
        return None
    j = html.find('<div class="area-editor', i)
    if j < 0:
        j = html.find('class="group page-footer', i)
    text = clean_html(html[i:j if j > 0 else i + 200000])
    return {"title": title, "text": text} if len(text) > 40 else None


def walk_listings(base: str, cats, st: State, log) -> Dict[str, Dict]:
    items: Dict[str, Dict] = dict(st.get("items") or {})
    for n, sms, cat in cats:
        page = 1
        while True:
            url = f"{base}/News.aspx?n={n}&sms={sms}&page={page}&PageSize=100"
            html = fetch(url, min_delay=4)
            rows = parse_listing(html) if html else []
            if not rows:
                log.info("listing n=%d page %d: no rows, stop", n, page)
                break
            oldest, newest, new = "9999", "0000", 0
            for d, nn, s, title in rows:
                date = ad_date(d)
                if not date:
                    continue
                oldest, newest = min(oldest, date), max(newest, date)
                if date < START or s in items:
                    continue
                items[s] = {"date": date, "n": nn, "title": title, "category": cat}
                new += 1
            log.info("listing n=%d page %d: %d rows, %d new, oldest %s", n, page, len(rows), new, oldest)
            st["items"] = items
            st.save()
            if newest < START:   # whole page before START (listing order is only roughly by date)
                break
            page += 1
    return items


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["zh", "en"], required=True)
    ap.add_argument("--max", type=int, default=0, help="stop after N new documents (0 = all)")
    ap.add_argument("--skip-listings", action="store_true")
    a = ap.parse_args()
    source = f"tw_mofa_{a.lang}"
    log = setup_logging(source)
    base, cats = SITES[a.lang]
    st = State(source)
    items = dict(st.get("items") or {}) if a.skip_listings else walk_listings(base, cats, st, log)
    order = sorted(items.items(), key=lambda kv: (kv[1]["date"], int(kv[0])), reverse=True)
    log.info("%d items since %s; %d already done", len(order), START, sum(st.is_done(s) for s, _ in order))
    n = 0
    for s, meta in order:
        if st.is_done(s):
            continue
        url = f"{base}/News_Content.aspx?n={meta['n']}&s={s}"
        html = fetch(url, min_delay=4)
        if not html:
            log.warning("fetch failed: %s", url)
            continue
        art = parse_article(html)
        if not art:
            log.warning("no body: %s", url)
            st.mark_done(s)
            continue
        row = {"id": make_id(source, s), "country": "TW", "source": source, "outlet": "official", "org": "MOFA",
               "lang": a.lang, "date": meta["date"], "url": url, "title": art["title"] or meta["title"],
               "speaker": None, "kind": "statement", "text": art["text"], "via": fetch.last.get("via") or "direct",
               "category": meta["category"]}
        write_docs("TW", source, [row])
        st.mark_done(s)
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
