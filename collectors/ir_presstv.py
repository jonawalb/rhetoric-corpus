"""Press TV (IRIB's English news channel), www.presstv.ir -> served from www.presstv.co.uk. ALL SECTIONS since
2026-10-03 (v2; the v1 sampling rule below is kept for the record).

URL discovery (all robots-allowed; presstv.ir and presstv.co.uk have no robots.txt, HTTP 404):
  * RSS https://www.presstv.co.uk/rss.xml (~100 newest items), re-read every 30 min with --follow;
  * the site's monthly sitemaps /SiteMap/YYYY-MM/sitemap-news-YYYY-MM.xml, listed in /sitemap.xml
    (2014-12 -> 2023-11; ~670 URLs a month);
  * Wayback CDX URL index of www.presstv.ir/Detail/YYYY/ for 2010 -> now (URL list only; the article
    itself is fetched live). A failed CDX year is retried on the next run / daily with --follow.
Article URLs carry the date (/Detail/YYYY/MM/DD/<id>/<slug>); the stored date is the page's own <time>
date, checked against the URL date.

v2 (2026-10-03): topic-neutral, back to 2010. Every article URL is fetched (no slug or section filter) and
stored if it has a date, a title and >= 150 characters; `section` keeps the page's breadcrumb section. Items
the v1 rule fetched and rejected are re-visited (state keys "v2:<id>"); ids already stored are not refetched.

v1 SAMPLING RULE (2026-10-02 -> 2026-10-03, documented in SOURCES.md): an article is fetched unless its slug matches SKIP_SLUG
(sport, culture, arts, lifestyle, weather words), newest first, and STORED only if the page's first
breadcrumb section is one of KEEP_SECTIONS (Iran, Iran/Politics, Iran/Nuclear Energy, Iran/Defense,
West Asia, Palestine, US, UK, Europe, Asia-Pacific, Africa, Americas and all their sub-sections, and the
war sections). Economy,
Energy, Culture, Society, Arts, Sports, Shows and video sections are dropped. Rows carry `section` and
`sample` = rss | sitemap | cdx.

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/ir_presstv.py [--follow] [--limit N]
"""
from __future__ import annotations

import argparse
import html as _html
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from ir_common import write_docs_fast  # noqa: E402
from lib import RAW, State, clean_html, fetch, make_id, now_iso, setup_logging  # noqa: E402

SOURCE = "ir_presstv"
BASE = "https://www.presstv.co.uk"
START = "2010-01-01"
KEEP_SECTIONS = {"101": "Iran", "10101": "Politics", "10104": "Nuclear Energy", "10106": "Defense",
                 "10115": "Definitive Revenge", "10116": "People's President", "102": "West Asia",
                 "10202": "Palestine", "103": "US", "10301": "US Politics", "104": "Asia-Pacific",
                 "105": "Africa", "106": "Europe", "107": "Americas", "108": "UK"}
SKIP_SLUG = re.compile(r"(?i)football|soccer|futsal|volleyball|basketball|wrestl|weightlift|olympic|"
                       r"paralymp|fifa|world-cup|league|tournament|medal|athlet|taekwondo|karate|judo|chess|"
                       r"film|cinema|movie|festival|music|concert|art-|-art$|exhibition|museum|tourism|"
                       r"recipe|weather|celebrit|fashion|covid-vaccine-dose")
CDX = ("https://web.archive.org/cdx/search/cdx?url=www.presstv.ir/Detail/{y}/&matchType=prefix"
       "&collapse=urlkey&fl=original&filter=statuscode:200")
URL_RE = re.compile(r"(?i)/Detail/(20\d\d)/(\d{1,2})/(\d{1,2})/(\d+)/([^\s\"<>?#]*)")
log = setup_logging(SOURCE)


def keep_section(sid: Optional[str]) -> bool:
    """Iran politics/nuclear/defense/war sections, and every sub-section of the regional desks
    (102 West Asia ... 108 UK, e.g. 10401 Pakistan, 10301 US politics)."""
    return bool(sid) and (sid in KEEP_SECTIONS or (len(sid) > 3 and sid[:3] in KEEP_SECTIONS and sid[:3] != "101"))


def norm(url: str) -> Optional[Tuple[str, str, str]]:
    """(article id, URL date, canonical co.uk URL) for an article URL, else None."""
    m = URL_RE.search(_html.unescape(url))
    if not m:
        return None
    y, mo, d, nid, slug = m.groups()
    try:
        ud = date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return None
    return nid, ud, f"{BASE}/Detail/{y}/{mo}/{d}/{nid}/{slug}"


def from_rss() -> List[str]:
    xml = fetch(f"{BASE}/rss.xml", min_delay=4) or ""
    return re.findall(r"<link>\s*(https?://[^<]*?/Detail/[^<]*?)\s*</link>", xml)


def from_sitemaps(st: State) -> List[str]:
    out: List[str] = []
    idx = fetch(f"{BASE}/sitemap.xml", min_delay=4) or ""
    for sm in re.findall(r"<loc>\s*([^<]*sitemap-news-(\d{4}-\d\d)\.xml)\s*</loc>", idx):
        loc, ym = sm
        if ym < START[:7]:
            continue
        cache = RAW / SOURCE / f"sitemap-{ym}.xml"   # small (~140 KB); finished months never change
        xml = fetch(loc, min_delay=4, cache=cache) or ""
        out += re.findall(r"<loc>\s*([^<]*/Detail/[^<]*)\s*</loc>", xml)
    return out


def from_cdx(st: State) -> List[str]:
    out: List[str] = []
    done = set(st.get("cdx_done") or [])
    for y in range(2010, datetime.now().year + 1):
        cache = RAW / SOURCE / f"cdx-{y}.txt"
        if str(y) in done and y < datetime.now().year and cache.exists():
            out += cache.read_text("utf-8").splitlines()
            continue
        if cache.exists() and y == datetime.now().year:
            cache.unlink()  # current year: refresh
        body = fetch(CDX.format(y=y), min_delay=5, timeout=600, cache=cache)
        if body:
            out += body.splitlines()
            done.add(str(y))
        else:
            log.warning("CDX for %d unavailable now; will retry later", y)
            lib._robots.pop("https://web.archive.org", None)
    st["cdx_done"] = sorted(done)
    return [u for u in out if "/detail/20" in u.lower()]


def parse(html: str) -> Optional[Dict]:
    i = html.find('class="body-content details-page"')
    if i < 0:
        return None
    sec = re.search(r'<div class="news-section-container">.*?<a href="/Section/(\d+)">([^<]*)</a>', html[i:i + 3000],
                    re.S)
    mt = re.search(r'<h1 class="news-title-container">(.*?)</h1>', html[i:], re.S)
    md = re.search(r'<time class="news-modifydate-container"[^>]*datetime="([^"]+)"', html[i:])
    a = html.find('<div class="col-md-9"', i)
    b = html.find('<div class="col-md-12"', a) if a > 0 else -1
    if not (mt and md and a > 0):
        return None
    try:
        d = datetime.strptime(re.sub(r"\s+", " ", md.group(1)).strip(), "%A, %d %B %Y %I:%M %p").date().isoformat()
    except ValueError:
        return None
    body = re.sub(r'<div data-oembed-url=.*?</div>\s*</div>\s*(?:<a [^>]*>.*?</a>)?\s*</div>', " ",
                  html[a:b if b > 0 else a + 100000], flags=re.S)  # drop embedded related-story cards
    return {"title": clean_html(mt.group(1)), "date": d, "text": clean_html(body),
            "section_id": sec.group(1) if sec else None, "section": clean_html(sec.group(2)) if sec else None}


STORED: set = set()


def process(queue: List[Tuple[str, str, str, str]], st: State, limit: int) -> int:
    n = 0
    for nid, ud, url, how in queue:
        if st.is_done("v2:" + nid) or make_id(SOURCE, nid) in STORED:
            continue
        html = fetch(url, min_delay=4)
        if html is None and fetch.last.get("status", 0) == 0:
            log.warning("network failure on %s; retry next run", url)
            time.sleep(60)
            continue
        st.mark_done("v2:" + nid)
        art = parse(html) if html else None
        if not art:
            if html:
                log.info("unparsed %s", url)
            continue
        if art["date"] < START or len(art["text"]) < 150 or not art["title"]:
            continue
        if abs((date.fromisoformat(art["date"]) - date.fromisoformat(ud)).days) > 2:
            log.warning("page date %s far from URL date %s: %s (URL date kept out; skipped)", art["date"], ud, url)
            continue
        write_docs_fast("IR", SOURCE, [{
            "id": make_id(SOURCE, nid), "country": "IR", "source": SOURCE, "outlet": "state_media",
            "org": "Press TV", "lang": "en", "date": art["date"], "url": fetch.last.get("url") or url,
            "title": art["title"], "speaker": None, "kind": "article", "text": art["text"], "via": "direct",
            "fetched": now_iso(), "section": art["section"], "sample": how}])
        n += 1
        if n % 50 == 0:
            st.save()
            log.info("written %d (at %s %s)", n, art["date"], how)
        if limit and n >= limit:
            break
    st.save()
    return n


def build_queue(urls: List[Tuple[str, str]]) -> List[Tuple[str, str, str, str]]:
    seen: Dict[str, Tuple[str, str, str, str]] = {}
    for u, how in urls:
        r = norm(u)
        if not r or r[1] < START or r[0] in seen:
            continue
        seen[r[0]] = (r[0], r[1], r[2], how)
    return sorted(seen.values(), key=lambda t: int(t[0]), reverse=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    st = State(SOURCE)
    STORED.update(lib.existing_ids(lib.docs_path("IR", SOURCE)))
    urls = [(u, "rss") for u in from_rss()]
    urls += [(u, "sitemap") for u in from_sitemaps(st)]
    q = build_queue(urls)
    log.info("%d candidate articles (RSS + sitemaps) since %s (%d done or stored)", len(q), START,
             sum(st.is_done("v2:" + t[0]) or make_id(SOURCE, t[0]) in STORED for t in q))
    n = process(q, st, a.limit)   # live-listed URLs first: the CDX years below depend on Wayback being up
    urls += [(u, "cdx") for u in from_cdx(st)]
    q = build_queue(urls)
    log.info("%d candidate articles incl. CDX", len(q))
    n += process(q, st, a.limit)
    log.info("backfill pass: %d new docs", n)
    last_cdx = time.time()
    while a.follow:
        time.sleep(1800)
        extra = [(u, "rss") for u in from_rss()]
        if time.time() - last_cdx > 86400:
            extra += [(u, "cdx") for u in from_cdx(st)]
            last_cdx = time.time()
        process(build_queue(extra), st, 0)


if __name__ == "__main__":
    main()
