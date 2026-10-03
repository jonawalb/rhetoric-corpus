"""SANA English (Syrian Arab News Agency, state news agency) politics news -> docs/SY/sana_en.jsonl.

SANA relaunched its site in Aug 2025. Two parts, run as separate processes (different hosts):

  --part new      https://sana.sy/en/{politics,presidency}/page/N/ (WordPress; listing back to ~2025-08-31).
                  Article date = JSON-LD datePublished; body = div.entry-content <p> paragraphs.
  --part archive  https://archive.sana.sy/en/?cat=N&paged=P — the pre-relaunch site (old ?p=<id> URLs
                  redirect there). Categories: 2 "Politics" (under Local) and 106 "Syria and the World".
                  Listing pages (10 items, no year on the cards) are walked from the newest; the articles of
                  each page are fetched right away; a category stops after a page whose articles are all
                  dated before --since. Date = meta article:published_time. The host is slow (15-65 s per
                  page) and answers 503 at times: articles fall back to the latest Wayback capture.

Every row carries `period`: "assad" (date < 2024-12-08) or "transitional" (from 2024-12-08), and
`section` (the listing it came from). Items without a readable date are skipped and logged.

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/sy_sana.py --part new|archive [--max N]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import State, clean_html, fetch, make_id, setup_logging, wayback_latest, write_docs  # noqa: E402

SOURCE = "sana_en"
START = "2021-01-01"
REGIME_CHANGE = "2024-12-08"
NEW_CATS = ["politics", "presidency"]
ARCHIVE_CATS = {"2": "Politics", "106": "Syria and the World"}
DELAY = 5.0   # sana.sy robots.txt: Crawl-delay 5 (applied to the archive host too)


def period_of(date: str) -> str:
    return "assad" if date < REGIME_CHANGE else "transitional"


def paragraphs(fragment: str) -> str:
    paras = [clean_html(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", fragment, re.S)]
    return "\n".join(p for p in paras if p)


# ------------------------------------------------------------------------------------------ new site
def parse_new(html: str) -> Optional[Dict]:
    d = re.search(r'"datePublished":"(\d{4}-\d\d-\d\d)', html)
    t = re.search(r'<h1 class="s-title"[^>]*>(.*?)</h1>', html, re.S)
    i = html.find('<div class="entry-content')
    if not (d and t and i >= 0):
        return None
    body = html[i:]
    for end in ('<div class="efoot', 'class="entry-bottom', "TAGGED:", 'class="e-shared', "</article>"):
        k = body.find(end)
        if k > 0:
            body = body[:k]
            break
    # cut inline "related posts" blocks
    body = re.sub(r'<div class="[^"]*(related|inline-post|block-wrap)[^"]*".*?</div>\s*</div>', " ", body, flags=re.S)
    text = paragraphs(body)
    return {"date": d.group(1), "title": clean_html(t.group(1)), "text": text} if len(text) > 80 else None


def run_new(st: State, log, max_new: int) -> int:
    n = 0
    for cat in NEW_CATS:
        page = 1
        while True:
            url = f"https://sana.sy/en/{cat}/" + (f"page/{page}/" if page > 1 else "")
            html = fetch(url, min_delay=DELAY)
            final = fetch.last.get("url", url)
            if not html or (page > 1 and "/page/" not in final):
                log.info("%s: end of listing at page %d", cat, page)
                break
            links: List[str] = []
            for h in re.findall(r'href="(https://sana\.sy/en/(?:%s)/\d+/)"' % "|".join(NEW_CATS), html):
                if h not in links:
                    links.append(h)
            dates = re.findall(r'datetime="(\d{4}-\d\d-\d\d)', html)
            log.info("%s page %d: %d links, dates %s..%s", cat, page, len(links), min(dates or ["?"]), max(dates or ["?"]))
            for link in links:
                if st.is_done(link):
                    continue
                n += store(link, fetch(link, min_delay=DELAY), parse_new, cat, "sana.sy", st, log)
                if max_new and n >= max_new:
                    return n
            st.save()
            if dates and max(dates) < START:
                break
            page += 1
    return n


# ------------------------------------------------------------------------------------------ archive site
def parse_archive(html: str) -> Optional[Dict]:
    d = re.search(r'article:published_time" content="(\d{4}-\d\d-\d\d)', html)
    t = re.search(r'<h1 class="name post-title entry-title"><span itemprop="name">(.*?)</span>', html, re.S)
    i = html.find('<div class="entry">')
    if not (d and t and i >= 0):
        return None
    body = html[i:]
    k = body.find("<!-- .entry /-->")
    body = body[:k] if k > 0 else body[:60000]
    body = re.sub(r"<div id='gallery-.*", " ", body, flags=re.S)
    text = paragraphs(body)
    return {"date": d.group(1), "title": clean_html(t.group(1)), "text": text} if len(text) > 80 else None


def fetch_archive(pid: str) -> Optional[str]:
    link = f"https://archive.sana.sy/en/?p={pid}"
    html = fetch(link, min_delay=DELAY, timeout=150, use_wayback_fallback=True)
    if not html:  # captures usually sit under the pre-relaunch address sana.sy/en/?p=<id>
        wb = wayback_latest(f"https://sana.sy/en/?p={pid}")
        html = fetch(wb, min_delay=DELAY, timeout=150) if wb else None
        if html:
            fetch.last["via"] = "wayback"
    return html


def run_archive(st: State, log, max_new: int) -> int:
    n = 0
    retry = list(st.get("retry") or [])   # articles whose fetch failed in an earlier pass
    if retry:
        log.info("retrying %d articles that failed before", len(retry))
    for pid, label in retry:
        link = f"https://archive.sana.sy/en/?p={pid}"
        if st.is_done(link):
            continue
        html_a = fetch_archive(pid)
        if html_a:
            n += store(link, html_a, parse_archive, label, "archive.sana.sy", st, log)
            st["retry"] = [r for r in st["retry"] if r[0] != pid]
            st.save()
    for cat, label in ARCHIVE_CATS.items():
        if st.get(f"archive_{cat}_finished"):
            continue
        page = int(st.get(f"archive_{cat}_page") or 1)
        fails = 0
        while True:
            url = f"https://archive.sana.sy/en/?cat={cat}" + (f"&paged={page}" if page > 1 else "")
            html = fetch(url, min_delay=DELAY, timeout=150)
            if not html:
                fails += 1
                log.warning("listing failed (%d): %s", fails, url)
                if fails >= 5:
                    log.error("giving up on %s for this run at page %d (re-run to resume)", label, page)
                    break
                continue
            fails = 0
            ids: List[str] = []
            for pid in re.findall(r'<h2 class="post-box-title">\s*<a href="https://archive\.sana\.sy/en/\?p=(\d+)"', html):
                if pid not in ids:
                    ids.append(pid)
            if not ids:
                log.info("%s: no items on page %d; done", label, page)
                st[f"archive_{cat}_finished"] = True
                break
            dates: List[str] = []
            for pid in ids:
                link = f"https://archive.sana.sy/en/?p={pid}"
                if st.is_done(link):
                    d = (st.get("dates") or {}).get(pid)
                    if d:
                        dates.append(d)
                    continue
                html_a = fetch_archive(pid)
                if not html_a:
                    st.data.setdefault("retry", []).append([pid, label])
                art = parse_archive(html_a) if html_a else None
                if art:
                    dates.append(art["date"])
                    st.data.setdefault("dates", {})[pid] = art["date"]
                n += store(link, html_a, parse_archive, label, "archive.sana.sy", st, log, parsed=art)
            log.info("%s page %d: %d items, dates %s..%s, %d new docs so far", label, page, len(ids),
                     min(dates or ["?"]), max(dates or ["?"]), n)
            page += 1
            st[f"archive_{cat}_page"] = page
            st.save()
            if dates and max(dates) < START:
                log.info("%s: page %d is entirely before %s; category done", label, page - 1, START)
                st[f"archive_{cat}_finished"] = True
                break
            if max_new and n >= max_new:
                return n
        st.save()
    return n


# ------------------------------------------------------------------------------------------ shared
def store(link: str, html: Optional[str], parser, section: str, site: str, st: State, log,
          parsed: Optional[Dict] = None) -> int:
    if not html:
        log.warning("fetch failed (not marked done): %s", link)
        return 0
    art = parsed or parser(html)
    if not art:
        log.warning("no date/title/body, skipped: %s", link)
        st.mark_done(link)
        return 0
    st.mark_done(link)
    if art["date"] < START:
        return 0
    row = {"id": make_id(SOURCE, link), "country": "SY", "source": SOURCE, "outlet": "state_media", "org": "SANA",
           "lang": "en", "date": art["date"], "url": link, "title": art["title"], "speaker": None, "kind": "article",
           "text": art["text"], "via": fetch.last.get("via") or "direct", "period": period_of(art["date"]),
           "section": section, "site": site}
    if row["via"] == "wayback":
        row["wayback"] = fetch.last.get("url")
    added, _ = write_docs("SY", SOURCE, [row])
    return added


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=["new", "archive"], required=True)
    ap.add_argument("--max", type=int, default=0, help="stop after N new docs (0 = all)")
    a = ap.parse_args()
    name = f"{SOURCE}_{a.part}"
    log = setup_logging(name)
    st = State(name)
    n = run_new(st, log, a.max) if a.part == "new" else run_archive(st, log, a.max)
    st.save()
    log.info("done: %d new docs", n)


if __name__ == "__main__":
    main()
