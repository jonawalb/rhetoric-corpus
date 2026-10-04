"""More Russian official / state outlets, fetched LIVE (added 2026-10-04). One process per host:

  tvzvezda_ru     TV Zvezda (tvzvezda.ru), the Ministry of Defence's TV and radio company. No sitemap; the /news
                  listing is rendered client-side from /api (robots: Disallow /api*), so URLs come from
                  (a) the site's RSS feeds /export/rss.xml + /export/yandex.xml (~1.5 days of items),
                  (b) the Wayback CDX URL index of tvzvezda.ru/news/ per year (URL list only; articles are fetched
                      LIVE from tvzvezda.ru; old /news/<section>/content/<id>.htm URLs map to /news/<id>.htm),
                  (c) /news/<id>.html links found inside fetched articles.
                  Article = server-rendered page: JSON-LD NewsArticle (headline, description, datePublished) +
                  div.text (body). robots: Allow / (Disallow /api*, /vcard*); 4 s.
  redstar_ru      Krasnaya Zvezda (redstar.ru), the Ministry of Defence's newspaper. WordPress; the public REST
                  API /wp-json/wp/v2/posts (robots allows; no Disallow lines) returns 100 full posts per request,
                  walked newest -> oldest with a `before` date cursor (~42,000 posts). robots.txt sets
                  Crawl-delay: 60, which is honored (one request per minute). https is unreachable (TLS SNI
                  error); the site's own http:// address is used. archive.redstar.ru (2001-2017 issues) answers
                  every URL, robots.txt included, with a JavaScript cookie wall -> not collected.
  government_archive_ru  archive.government.ru, the Government portal 2008-05 -> 2013-05 (Putin and first
                  Medvedev cabinets). No robots.txt (404 = no rules). One id space shared by /docs/<id>/
                  (events, news), /stens/<id>/ (transcripts) and /gov/results/<id>/ (acts with explanatory notes);
                  each id is tried under those prefixes in that order, walking down from the newest id. 4 s.
                  (The 2013-05 -> 2020-01 Medvedev-cabinet pages are not on either government.ru host: old
                  government.ru/news/<id>/ ids 404.)

ALL TOPICS (no keyword or section filter). robots.txt checked for every URL (wildcard aware, ru_common.robots_ok);
>= 4 s between requests per host, more when the site's Crawl-delay says so. Rows -> docs/RU/<source>.jsonl via
ru_common.write_docs_fast (skips ids already in the file or sealed into the store).

    uv run --project ~/Projects/rhetoric-corpus python collectors/ru_more_media.py <site> [--follow] [--limit N]
"""
from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
import time
import urllib.parse
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Deque, Dict, Iterator, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from ru_common import BigState, balanced_div, ctext, robots_ok, ru_date, run_queue  # noqa: E402

log = lib.setup_logging("ru_more_media")
DELAY = 4.0
FOLLOW_S = 1800
MIN_TEXT = 80


def _get(url: str, delay: float = DELAY, timeout: int = 90, wayback_ok: bool = False) -> Optional[str]:
    if not robots_ok(url):
        log.warning("robots disallows %s", url)
        return None
    cd = lib.crawl_delay(url) or 0.0
    return lib.fetch(url, min_delay=max(delay, cd), timeout=timeout)


def _status() -> int:
    return lib.fetch.last.get("status") or 0


def _paras(fragment: str) -> str:
    ps = [ctext(p) for p in re.findall(r"<(?:p|h2|h3|blockquote)\b[^>]*>(.*?)</(?:p|h2|h3|blockquote)>", fragment, re.S)]
    return "\n".join(p for p in ps if p) or ctext(fragment)


# ============================================================================================ TV Zvezda
TZ_ID = re.compile(r"tvzvezda\.ru(?::\d+)?/news/(?:[\w-]+/content/)?(\d{8,12}-[A-Za-z0-9]{4,5})\.(html?)\b")


def tz_norm(url: str) -> Optional[Tuple[str, str]]:
    """(doc key, canonical https URL) for a TV Zvezda article URL in any of its historical forms, else None."""
    m = TZ_ID.search(url)
    if not m:
        return None
    return m.group(1), f"https://tvzvezda.ru/news/{m.group(1)}.{m.group(2)}"


def feed_links(xml: str) -> List[str]:
    """<link> URLs of an RSS feed (plain or CDATA-wrapped, as in tvzvezda's yandex.xml)."""
    return re.findall(r"<link>\s*(?:<!\[CDATA\[)?\s*(https?://[^<\s\]]+)", xml)


def tz_parse(html: str) -> Dict:
    """{title, date, text, tags} of a TV Zvezda article page."""
    ld: Dict = {}
    for blob in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html, re.S):
        try:
            d = json.loads(blob)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("@type") == "NewsArticle":
            ld = d
            break
    title = _html.unescape(ld.get("headline") or "").strip()
    if not title:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        title = ctext(m.group(1)) if m else ""
    d = (ld.get("datePublished") or "")[:10] or None
    lead = _html.unescape(ld.get("description") or "").strip()
    body = balanced_div(html, 'class="text text18') or balanced_div(html, 'class="text ') or ""
    text = "\n".join(x for x in (lead, _paras(body)) if x)
    mk = re.search(r'<meta name="keywords" content="([^"]*)"', html)
    tags = [t.strip() for t in _html.unescape(mk.group(1)).split(",") if t.strip()] if mk else []
    return {"title": title, "date": d, "text": text if body else "", "tags": tags}


class TvZvezda:
    source, org, host = "tvzvezda_ru", "TV Zvezda (Ministry of Defence)", "tvzvezda.ru"
    feeds = ("https://tvzvezda.ru/export/yandex.xml", "https://tvzvezda.ru/export/rss.xml")
    first_year = 2010

    def __init__(self, st: BigState):
        self.st = st
        self.found: Deque[Dict] = deque()  # links discovered inside fetched articles
        self.queued: set = set()

    def _item(self, url: str, src: str) -> Optional[Dict]:
        n = tz_norm(url)
        if not n or n[0] in self.queued:
            return None
        self.queued.add(n[0])
        return {"url": n[1], "key": f"{self.source}:{n[0]}", "found": src}

    def feed_items(self) -> List[Dict]:
        out = []
        for f in self.feeds:
            for u in feed_links(_get(f) or ""):
                it = self._item(u, "rss")
                if it:
                    out.append(it)
        return out

    def cdx_year(self, y: int) -> List[str]:
        """Original URLs of Wayback captures under tvzvezda.ru/news/ in year y (closed years cached)."""
        import cn_common
        cache = lib.RAW / self.source / "cdx" / f"{y}.txt"
        if cache.exists():
            return cache.read_text("utf-8").split()
        urls = [u for u, _ in cn_common.cdx_urls("tvzvezda.ru/news/", r"/news/(?:[\w-]+/content/)?\d{8,12}-",
                                                  frm=str(y), to=str(y))]
        if y < datetime.now().year and urls:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text("\n".join(urls), "utf-8")
        return urls

    def items(self, follow: bool) -> Iterator[Dict]:
        last = time.time()
        yield from self.feed_items()
        # Live backward crawl first: links inside fetched articles (related / earlier stories) lead into the archive
        # without depending on Wayback; the CDX years then fill what the link graph does not reach.
        while self.found:
            yield self.found.popleft()
            if follow and time.time() - last >= FOLLOW_S:
                last = time.time()
                yield from self.feed_items()
        for y in range(datetime.now().year, self.first_year - 1, -1):
            if self.st.is_done(f"cdx:{y}") and y < datetime.now().year:
                continue
            urls = self.cdx_year(y)
            log.info("tvzvezda CDX %d: %d capture URLs", y, len(urls))
            todo = [it for it in (self._item(u, "wayback_cdx") for u in sorted(set(urls), reverse=True)) if it]
            for it in todo:
                while self.found:
                    yield self.found.popleft()
                if follow and time.time() - last >= FOLLOW_S:
                    last = time.time()
                    yield from self.feed_items()
                yield it
            if urls and y < datetime.now().year:
                yield {"url": f"cdx:{y}", "key": f"cdx:{y}", "marker": True}
        while self.found:
            yield self.found.popleft()

    def parse(self, it: Dict) -> Optional[List[Dict]]:
        if it.get("marker"):
            return []
        url = it["url"]
        html = _get(url, timeout=60)
        if html is None:
            st = _status()
            return [] if st in (404, 410) or not robots_ok(url) else None
        for u in re.findall(r'href="(https://tvzvezda\.ru/news/[^"]+)"', html):
            nit = self._item(u, "link")
            if nit:
                self.found.append(nit)
        p = tz_parse(html)
        if not p["date"] or not p["title"] or len(p["text"]) < MIN_TEXT:
            log.info("empty/short %s (%d chars, date %s)", url, len(p["text"]), p["date"])
            return []
        return [{"id": it["key"], "country": "RU", "source": self.source, "outlet": "state_media", "org": self.org,
                 "lang": "ru", "date": p["date"], "url": url, "title": p["title"], "speaker": None, "kind": "article",
                 "text": p["text"], "via": lib.fetch.last.get("via") or "direct", "tags": p["tags"] or None,
                 "found": it.get("found")}]


# ============================================================================================ Krasnaya Zvezda
RS_API = "http://redstar.ru/wp-json/wp/v2/posts"
RS_FIELDS = "id,date,link,title,content,excerpt,categories"


def rs_rows(posts: List[Dict], cats: Dict[int, str]) -> List[Dict]:
    """docs rows from WordPress REST post objects (Krasnaya Zvezda)."""
    out = []
    for p in posts:
        title = ctext((p.get("title") or {}).get("rendered") or "")
        text = _paras((p.get("content") or {}).get("rendered") or "")
        d = (p.get("date") or "")[:10]
        if not re.fullmatch(r"\d{4}-\d\d-\d\d", d) or not title or len(text) < MIN_TEXT:
            continue
        secs = [cats[c] for c in p.get("categories") or [] if c in cats]
        out.append({"id": f"redstar_ru:{p['id']}", "country": "RU", "source": "redstar_ru", "outlet": "state_media",
                    "org": "Krasnaya Zvezda (Ministry of Defence)", "lang": "ru", "date": d,
                    "url": p.get("link") or f"http://redstar.ru/?p={p['id']}", "title": title, "speaker": None,
                    "kind": "article", "text": text, "via": "direct", "section": ", ".join(secs) or None})
    return out


class RedStar:
    source, host = "redstar_ru", "redstar.ru"

    def __init__(self, st: BigState):
        self.st = st
        self.cats: Dict[int, str] = {}

    def api(self, **q) -> Optional[List[Dict]]:
        url = RS_API + "?" + urllib.parse.urlencode({"per_page": 100, "_fields": RS_FIELDS, **q})
        body = _get(url, timeout=180)
        if body is None:
            return None
        try:
            data = json.loads(body)
        except ValueError:
            log.warning("redstar: non-JSON answer for %s", url)
            return None
        return data if isinstance(data, list) else None

    def load_cats(self) -> None:
        for page in (1, 2, 3):
            body = _get(f"http://redstar.ru/wp-json/wp/v2/categories?per_page=100&page={page}&_fields=id,name")
            try:
                rows = json.loads(body or "[]")
            except ValueError:
                rows = []
            self.cats.update({c["id"]: _html.unescape(c["name"]) for c in rows if isinstance(c, dict)})
            if len(rows) < 100:
                break

    def page_items(self, posts: List[Dict]) -> List[Dict]:
        return [{"url": p.get("link") or str(p["id"]), "key": f"redstar_ru:{p['id']}", "post": p} for p in posts]

    def items(self, follow: bool) -> Iterator[Dict]:
        if not self.cats:
            self.load_cats()
        last = time.time()
        newest = self.api()
        if newest:
            yield from self.page_items(newest)
        cursor = self.st.get("before") or (newest[-1]["date"] if newest else None)
        while cursor:
            # +1 s so posts sharing the boundary second are not skipped (they are de-duplicated by id)
            b = (datetime.fromisoformat(cursor) + timedelta(seconds=1)).isoformat()
            posts = self.api(before=b, order="desc", orderby="date")
            if posts is None:
                log.warning("redstar: page before %s failed; retrying in 10 min", cursor)
                time.sleep(600)
                continue
            if not posts:
                break
            yield from self.page_items(posts)
            oldest = min(p["date"] for p in posts)
            if oldest >= cursor:  # > 100 posts in one second cannot happen; step past it anyway
                oldest = (datetime.fromisoformat(cursor) - timedelta(seconds=1)).isoformat()
            cursor = oldest
            self.st["before"] = cursor
            if follow and time.time() - last >= FOLLOW_S:
                last = time.time()
                yield from self.page_items(self.api() or [])
        log.info("redstar: archive walk reached the oldest post (%s)", cursor)
        self.st["archive_done"] = True

    def parse(self, it: Dict) -> Optional[List[Dict]]:
        return rs_rows([it["post"]], self.cats)


# ============================================================================================ archive.government.ru
GA_BASE = "http://archive.government.ru"
GA_PREFIXES = (("docs", None), ("stens", "transcript"), ("gov/results", "document"))
GA_KIND = {"Событие": "event", "Стенограмма": "transcript"}


def ga_parse(html: str, prefix: str) -> Dict:
    """{title, date, text, kind, participants} of an archive.government.ru page."""
    md = re.search(r'<p class="date"[^>]*>(.*?)</p>', html, re.S)
    d = ru_date(ctext(md.group(1))) if md else None
    if prefix == "gov/results":
        h3 = re.search(r'<div class="per66">.*?<h3>(.*?)</h3>\s*(?:<p class="description">(.*?)</p>)?', html, re.S)
        title = " ".join(x for x in (ctext(h3.group(1)), ctext(h3.group(2) or "")) if x) if h3 else ""
        kind = "document"
    else:
        mt = re.search(r"<!-- TITLE -->(.*?)<!-- (?:TITLE END|LEADPERSON) -->", html, re.S)
        title = _paras(mt.group(1)) if mt else ""
        tab = re.search(r'<a class="disabled"[^>]*>(.*?)</a>', html, re.S)
        kind = GA_KIND.get(ctext(tab.group(1)) if tab else "", "transcript" if prefix == "stens" else "article")
    lead = ctext(balanced_div(html, 'class="per16 quote"') or "")
    body = _paras(balanced_div(html, 'class="per15 text-main"') or balanced_div(html, 'class="text-main"') or "")
    part = balanced_div(html, 'class="participants"') or ""
    people = [ctext(a) for a in re.findall(r"<a[^>]*>(.*?)</a>", part, re.S)]
    return {"title": title, "date": d, "text": "\n".join(x for x in (lead, body) if x), "kind": kind,
            "participants": [p for p in people if p] or None}


class GovArchive:
    source, host = "government_archive_ru", "archive.government.ru"
    org = "Government of the Russian Federation (2008-2013 portal)"

    def __init__(self, st: BigState):
        self.st = st

    def top_id(self) -> int:
        ids = [int(x) for page in ("/news/", "/transcripts/", "/")
               for x in re.findall(r'href="/(?:docs|stens|gov/results)/(\d+)/', _get(GA_BASE + page) or "")]
        return max(ids) if ids else 24400

    def items(self, follow: bool) -> Iterator[Dict]:
        top = self.top_id() + 50  # a few ids above the newest listed one (acts are published with a delay)
        log.info("government_archive_ru: walking ids %d -> 1", top)
        for n in range(top, 0, -1):
            yield {"url": f"{GA_BASE}/docs/{n}/", "key": f"{self.source}:{n}", "n": n}

    def parse(self, it: Dict) -> Optional[List[Dict]]:
        n = it["n"]
        for prefix, _ in GA_PREFIXES:
            url = f"{GA_BASE}/{prefix}/{n}/"
            html = _get(url, timeout=90)
            if html is None:
                if _status() in (404, 410):
                    continue
                return None
            p = ga_parse(html, prefix)
            if not p["date"] or not p["title"] or len(p["text"]) < MIN_TEXT:
                log.info("empty/short %s (%d chars, date %s)", url, len(p["text"]), p["date"])
                return []
            return [{"id": f"{self.source}:{n}", "country": "RU", "source": self.source, "outlet": "official",
                     "org": self.org, "lang": "ru", "date": p["date"], "url": url, "title": p["title"],
                     "speaker": None, "kind": p["kind"], "text": p["text"], "via": "direct",
                     "participants": p["participants"]}]
        return []


SITES: Dict[str, Callable[[BigState], object]] = {"tvzvezda_ru": TvZvezda, "redstar_ru": RedStar,
                                                  "government_archive_ru": GovArchive}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("site", choices=sorted(SITES))
    ap.add_argument("--follow", action="store_true", help="re-read the newest items every 30 min, forever")
    ap.add_argument("--limit", type=int, default=0, help="stop after N queue items (testing)")
    a = ap.parse_args()
    st = BigState(f"ru_{a.site}")
    site = SITES[a.site](st)
    have = lib.existing_ids(lib.docs_path("RU", site.source))
    while True:
        gen = (it for it in site.items(a.follow) if it.get("marker") or it["key"] not in have)
        if a.limit:
            gen = (x for _, x in zip(range(a.limit), gen))
        run_queue(gen, st, site.parse, "RU", lambda r: r["source"], batch=10, save_every=25)
        if not a.follow or a.limit or a.site == "government_archive_ru":
            return
        time.sleep(FOLLOW_S)
        have = lib.existing_ids(lib.docs_path("RU", site.source))


if __name__ == "__main__":
    main()
