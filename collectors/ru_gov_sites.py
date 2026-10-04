"""Russian government bodies whose news pages have sequential numeric ids, fetched LIVE (added 2026-10-03):

  government_ru  Government of the Russian Federation, http://government.ru/news/<id>/ (Mishustin cabinet,
                 ids ~39,000 = 2020-01 -> ~60,000 = now; the 2012-2020 cabinet lives on archive.government.ru,
                 not covered here). https times out from this host; http answers. No robots.txt (the site
                 answers /robots.txt with its HTML home page = no rules).
  council_ru     Federation Council, http://council.gov.ru/events/news/<id>/ (ids 1 .. ~177,000, 2008 -> now;
                 Matviyenko, senators, committee news, laws approved). robots.txt 404 (no rules).
  premier_archive_ru  archive of Putin's Prime Minister site, http://archive.premier.gov.ru/events/news/<id>/
                 (2008-05 -> 2012-05, ids 1 .. ~18,900; robots.txt 404). speaker field stays null (texts quote
                 several people), org names the Putin premiership.

The collector reads the newest id from the listing page and walks DOWN, fetching every id, until `floor`
(or MISS_STOP consecutive missing ids). With --follow it re-reads the listing every 6 h and fetches new ids
upward first. ALL TOPICS. >= 4 s between requests (lib default). Rows -> docs/RU/<source>.jsonl.

    uv run --project ~/Projects/rhetoric-corpus python collectors/ru_gov_sites.py government_ru [--follow]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from ru_common import BigState, balanced_div, ctext, robots_ok, ru_date, write_docs_fast  # noqa: E402

log = lib.setup_logging("ru_gov_sites")
DELAY = 4
MISS_STOP = 2000


class GovSite:
    source = org = listing = item = ""
    floor = 1

    def url(self, i: int) -> str:
        return self.item.format(i)

    def extract(self, html: str) -> Optional[Tuple[str, str, str]]:
        raise NotImplementedError


class Government(GovSite):
    source, org = "government_ru", "Government"
    listing, item, floor = "http://government.ru/news/", "http://government.ru/news/{}/", 38000

    def extract(self, html: str) -> Optional[Tuple[str, str, str]]:
        t = re.search(r'<h3 class="reader_article_headline"[^>]*>(.*?)</h3>', html, re.S)
        d = re.search(r'reader_article_dateline__date">([^<]+)<', html)
        body = balanced_div(html, 'class="reader_article_body"')
        if not (t and d and body):
            return None
        body = re.sub(r'<p class="gallery[^"]*".*?</p>', " ", body, flags=re.S)
        paras = [ctext(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", body, re.S)]
        return ctext(t.group(1)), ru_date(d.group(1)) or "", "\n".join(p for p in paras if p)


class Council(GovSite):
    source, org = "council_ru", "Federation Council"
    listing, item, floor = "http://council.gov.ru/events/news/", "http://council.gov.ru/events/news/{}/", 1

    def extract(self, html: str) -> Optional[Tuple[str, str, str]]:
        t = re.search(r'<h1 class="header_title[^"]*"[^>]*>(.*?)</h1>', html, re.S)
        d = re.search(r'<time class="dt-published"\s+datetime="(\d{4}-\d{2}-\d{2})', html)  # 2009 and 2026 layouts
        body = balanced_div(html, 'class="body_text"')
        if not (t and d and body):
            return None
        body = body.split('<div class="article_additional__wrapper', 1)[0]
        body = re.sub(r'<span class="tooltip__text.*?</span>\s*</span>\s*</span>', " ", body, flags=re.S)
        paras = [ctext(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", body, re.S)]
        text = "\n".join(p for p in paras if p) or ctext(body)
        return ctext(t.group(1)), d.group(1), text


class PremierArchive(GovSite):
    """archive.premier.gov.ru: Putin's Prime Minister site 2008-05 .. 2012-05 (ids 1 .. ~18,911). No title
    element: the bold lead sentence is the title; Putin is the speaker of record."""
    source, org = "premier_archive_ru", "Government (Prime Minister Putin, 2008-2012)"
    listing, item, floor = "http://archive.premier.gov.ru/events/news/", "http://archive.premier.gov.ru/events/news/{}/", 1

    def extract(self, html: str) -> Optional[Tuple[str, str, str]]:
        box = balanced_div(html, 'id="mainEventBox"') or ""
        d = re.search(r'<div class="date">\s*<span>([^<]+)</span>', box)
        lead = re.search(r"<p>\s*<b>(.*?)</b>\s*</p>", box, re.S)
        body = balanced_div(html, 'id="recordBox"')
        if not (d and body):
            return None
        paras = [ctext(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", body, re.S)]
        title = ctext(lead.group(1)) if lead else ""
        text = "\n".join(([title] if title else []) + [p for p in paras if p])
        return title[:300], ru_date(d.group(1).replace(",", " ")) or "", text


SITES: Dict[str, Callable[[], GovSite]] = {"government_ru": Government, "council_ru": Council,
                                           "premier_archive_ru": PremierArchive}


def fetch_id(site: GovSite, st: lib.State, i: int, buf: List[Dict]) -> Optional[str]:
    """Fetch id i; append a row. Returns 'ok', 'missing' (404 / not an article), or None (failure, retry)."""
    url = site.url(i)
    if not robots_ok(url):
        return None
    html = lib.fetch(url, min_delay=DELAY, timeout=90)
    if html is None:
        if lib.fetch.last.get("status") in (404, 410):
            st.mark_done(str(i))
            return "missing"
        log.warning("fetch failed %s (status %s)", url, lib.fetch.last.get("status"))
        return None
    st.mark_done(str(i))
    r = site.extract(html)
    if not r or not r[1] or len(r[2]) < 40:
        return "missing"
    title, d, text = r
    buf.append({"id": lib.make_id(site.source, str(i)), "country": "RU", "source": site.source, "outlet": "official",
                "org": site.org, "lang": "ru", "date": d, "url": url, "title": title, "speaker": None,
                "kind": "statement", "text": text, "via": lib.fetch.last.get("via") or "direct"})
    return "ok"


def flush(site: GovSite, st: lib.State, buf: List[Dict], at: str) -> None:
    if buf:
        added, total = write_docs_fast("RU", site.source, buf)
        log.info("%s: wrote %d (total %d), at %s", site.source, added, total, at)
        buf.clear()
    st.save()


def newest_id(site: GovSite) -> Optional[int]:
    html = lib.fetch(site.listing, min_delay=DELAY, timeout=90) or ""
    path = re.escape(site.item.split("{}")[0].split("://", 1)[1].split("/", 1)[1])
    ids = [int(x) for x in re.findall(rf'href="(?:https?://[^/"]+)?/{path}(\d+)/"', html)]
    return max(ids) if ids else None


def run(site: GovSite, st: lib.State) -> None:
    top = newest_id(site)
    if not top:
        log.warning("%s: listing unreadable", site.source)
        return
    buf: List[Dict] = []
    hi = st.get("hi") or top
    for i in range(hi + 1, top + 1):  # new ids first
        if not st.is_done(str(i)):
            fetch_id(site, st, i, buf)
    st["hi"] = max(hi, top)
    flush(site, st, buf, "new")
    i, miss = int(st.get("cursor") or top), int(st.get("miss") or 0)
    while i >= site.floor and miss < MISS_STOP:
        if not st.is_done(str(i)):
            res = fetch_id(site, st, i, buf)
            if res is None:
                time.sleep(120)
                continue  # retry the same id
            miss = 0 if res == "ok" else miss + 1
        i -= 1
        if len(buf) >= 10 or i % 50 == 0:
            st["cursor"], st["miss"] = i, miss
            flush(site, st, buf, f"id {i}")
    st["cursor"], st["miss"] = i, miss
    flush(site, st, buf, f"id {i}")
    log.info("%s: downward walk finished at id %d (miss run %d)", site.source, i, miss)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("site", choices=sorted(SITES))
    ap.add_argument("--follow", action="store_true")
    a = ap.parse_args()
    site = SITES[a.site]()
    st = BigState(f"ru_{a.site}")
    run(site, st)
    while a.follow:
        time.sleep(6 * 3600)
        run(site, st)


if __name__ == "__main__":
    main()
