"""Russian official / state media with large sitemap inventories, fetched LIVE (added 2026-10-03):

  rg_ru     Rossiyskaya Gazeta (rg.ru) -- the government's official newspaper (publishes laws/decrees under
            rg.ru/documents/ as well as news). Daily sitemaps https://rg.ru/sitemaps/index.xml back to 2003-07
            (~8,500 days, ~200-400 URLs/day). outlet "official" (org "Rossiyskaya Gazeta").
  vesti_ru  Vesti.ru (VGTRK, federal state TV/radio company). https://www.vesti.ru/sitemap.xml -> 48 chunks of
            /article/<id> URLs (2000s -> now). outlet "state_media".
  1tv_ru    Channel One (Pervyi kanal, state-controlled) news texts, sitemap-news-YYYY.xml 2002 -> now (~21k/year).
            outlet "state_media".
  iz_ru     Izvestia (iz.ru; National Media Group, Kremlin-aligned but not state-owned -> outlet "media"),
            /export/sitemap/N/xml (66 chunks of 25k URLs; /video/ pages skipped). NOT RUN: since 2026-10-03 iz.ru
            answers robots.txt with HTTP 403 to our UA, which lib treats as "disallow everything".

Order: every child sitemap is read once and all its URLs are fetched before the next child. Children are taken
in a "spread" order (bit-reversed index: first child 0, then the middle, then the quarters, ...), so a partial run
covers the whole time range evenly instead of only the newest years; rows carry `sitemap` (the child it came from).
--follow: every 30 min the newest child sitemap(s) are re-read and new URLs fetched first (interleaved with the
backfill). ALL TOPICS (no keyword or section filter). robots.txt checked for every URL (wildcard aware); >= 4 s
between requests per host (lib default); article HTML is not cached.

    uv run --project ~/Projects/rhetoric-corpus python collectors/ru_sitemap_media.py rg_ru [--follow] [--limit N]
"""
from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from ru_common import BigState, balanced_div, ctext, robots_ok, run_queue  # noqa: E402

log = lib.setup_logging("ru_sitemap_media")
DELAY = 4


def _locs(xml: str) -> List[Tuple[str, str]]:
    out = []
    for blk in re.findall(r"<(?:url|sitemap)>(.*?)</(?:url|sitemap)>", xml, re.S):
        loc = re.search(r"<loc>\s*(.*?)\s*</loc>", blk, re.S)
        lm = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", blk, re.S)
        if loc:
            out.append((_html.unescape(loc.group(1)), lm.group(1)[:10] if lm else ""))
    return out


def _get(url: str, timeout: int = 90) -> Optional[str]:
    if not robots_ok(url):
        log.warning("robots disallows %s", url)
        return None
    return lib.fetch(url, min_delay=DELAY, timeout=timeout)


def json_ld_article(html: str) -> Dict:
    """First JSON-LD object with an articleBody (NewsArticle/Article), or {}."""
    for blob in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html, re.S):
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        objs = data if isinstance(data, list) else (data.get("@graph") or [data]) if isinstance(data, dict) else []
        for o in objs:
            if isinstance(o, dict) and o.get("articleBody"):
                return o
    return {}


def spread(n: int) -> List[int]:
    """0..n-1 in bit-reversed (van der Corput) order: evenly spread prefixes."""
    bits = max(1, (n - 1).bit_length())
    order = sorted(range(1 << bits), key=lambda i: int(format(i, f"0{bits}b")[::-1], 2))
    return [i for i in order if i < n]


class SmSite:
    source = org = outlet = lang = host = index = ""
    pat: re.Pattern = re.compile("$^")

    def children(self) -> List[Tuple[str, str]]:
        """[(child sitemap URL, sort key)] oldest -> newest."""
        return sorted(((loc, lm) for loc, lm in _locs(_get(self.index) or "")), key=lambda x: x[1])

    def newest(self, kids: List[Tuple[str, str]]) -> List[str]:
        return [kids[-1][0]] if kids else []

    def extract(self, html: str, url: str) -> Tuple[str, Optional[str], str, Dict]:
        raise NotImplementedError


class RgRu(SmSite):
    source, org, outlet, lang, host = "rg_ru", "Rossiyskaya Gazeta", "official", "ru", "rg.ru"
    index = "https://rg.ru/sitemaps/index.xml"
    pat = re.compile(r"https://rg\.ru/(?:documents/)?\d{4}/\d{2}/\d{2}/[^?#]+\.html$")

    def children(self) -> List[Tuple[str, str]]:
        out = []
        for loc, _ in _locs(_get(self.index, timeout=180) or ""):
            m = re.search(r"date_start=(\d+)", loc)
            if m:
                out.append((loc, datetime.fromtimestamp(int(m.group(1)), timezone.utc).strftime("%Y-%m-%d")))
        return sorted(out, key=lambda x: x[1])

    def newest(self, kids: List[Tuple[str, str]]) -> List[str]:
        return [k for k, _ in kids[-2:]]

    def extract(self, html: str, url: str) -> Tuple[str, Optional[str], str, Dict]:
        o = json_ld_article(html)
        title = _html.unescape(o.get("headline") or "").strip()
        d = (o.get("datePublished") or "")[:10] or None
        text = ctext(_html.unescape(o.get("articleBody") or "").replace("\n", "<br>"))
        if not text:  # documents and older layouts: lead + text wrapper paragraphs
            body = balanced_div(html, "PageArticleContent_textWrapper") or balanced_div(html, "PageDocumentContent") or ""
            text = "\n".join(t for t in (ctext(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", body, re.S)) if t)
        if not title:
            m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
            title = ctext(m.group(1)) if m else ""
        if not d:
            m = re.search(r"rg\.ru/(?:documents/)?(\d{4})/(\d{2})/(\d{2})/", url)
            d = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
        sec = o.get("articleSection")
        extra = {"section": sec if isinstance(sec, str) else (", ".join(sec) if isinstance(sec, list) else None),
                 "kind_hint": "document" if "/documents/" in url else None}
        return title, d, text, extra


class VestiRu(SmSite):
    source, org, outlet, lang, host = "vesti_ru", "VGTRK (Vesti)", "state_media", "ru", "www.vesti.ru"
    index = "https://www.vesti.ru/sitemap.xml"
    pat = re.compile(r"https://www\.vesti\.ru/article/\d+$")

    def children(self) -> List[Tuple[str, str]]:
        kids = [(loc, lm) for loc, lm in _locs(_get(self.index) or "") if "sitemap-article-" in loc]
        return sorted(kids, key=lambda x: int(re.search(r"article-(\d+)", x[0]).group(1)))

    def newest(self, kids: List[Tuple[str, str]]) -> List[str]:
        return [max(kids, key=lambda x: x[1])[0]] if kids else []

    def extract(self, html: str, url: str) -> Tuple[str, Optional[str], str, Dict]:
        m = re.search(r'<h1[^>]*itemprop="headline"[^>]*>(.*?)</h1>', html, re.S)
        title = ctext(m.group(1)) if m else ""
        m = re.search(r'itemprop="datePublished"[^>]*datetime="(\d{4}-\d{2}-\d{2})', html)
        body = balanced_div(html, 'class="article-body"') or ""
        body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
        return title, m.group(1) if m else None, ctext(body), {}


class OneTv(SmSite):
    source, org, outlet, lang, host = "1tv_ru", "Channel One", "state_media", "ru", "www.1tv.ru"
    index = "https://www.1tv.ru/sitemap.xml"
    pat = re.compile(r"https://www\.1tv\.ru/news/\d{4}-\d{2}-\d{2}/\d+(?:-[^/?#]+)?$")  # 2026: no slug

    def children(self) -> List[Tuple[str, str]]:
        kids = [(loc, re.search(r"news-(\d{4})", loc).group(1)) for loc, _ in _locs(_get(self.index) or "")
                if re.search(r"sitemap-news-\d{4}\.xml$", loc)]
        return sorted(kids, key=lambda x: x[1])

    def extract(self, html: str, url: str) -> Tuple[str, Optional[str], str, Dict]:
        m = re.search(r'<h1 class="Heading_title[^"]*"[^>]*>(.*?)</h1>', html, re.S)
        title = ctext(m.group(1)) if m else ""
        d = re.search(r"/news/(\d{4}-\d{2}-\d{2})/", url)
        body = balanced_div(html, 'class="WysiwygContent_content') or ""
        paras = [ctext(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", body, re.S)]
        return title, d.group(1) if d else None, "\n".join(p for p in paras if p) or ctext(body), {}


class Izvestia(SmSite):
    source, org, outlet, lang, host = "iz_ru", "Izvestia", "media", "ru", "iz.ru"
    index = "https://iz.ru/sitemap.xml"
    pat = re.compile(r"https://iz\.ru/(?:news/\d+|\d+/(?!video/)[^?#]+)$")

    def children(self) -> List[Tuple[str, str]]:
        kids = [(loc, int(m.group(1))) for loc, _ in _locs(_get(self.index) or "")
                for m in [re.search(r"/sitemap/(\d+)/xml$", loc)] if m]
        return [(loc, f"{n:04d}") for loc, n in sorted(kids, key=lambda x: x[1])]

    def newest(self, kids: List[Tuple[str, str]]) -> List[str]:
        return ["https://iz.ru/export/sitemap/last/xml"]

    def extract(self, html: str, url: str) -> Tuple[str, Optional[str], str, Dict]:
        m = re.search(r'<h1[^>]*itemprop="headline"[^>]*>(.*?)</h1>', html, re.S)
        title = ctext(m.group(1)) if m else ""
        d = re.search(r'article:published_time" content="(\d{4}-\d{2}-\d{2})', html)
        body = balanced_div(html, 'class="text-article__inside"') or ""
        paras = [ctext(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", body, re.S)]
        return title, d.group(1) if d else None, "\n".join(p for p in paras if p) or ctext(body), {}


SITES: Dict[str, Callable[[], SmSite]] = {"rg_ru": RgRu, "vesti_ru": VestiRu, "1tv_ru": OneTv, "iz_ru": Izvestia}


def make_parser(site: SmSite) -> Callable[[Dict], Optional[List[Dict]]]:
    def parse(it: Dict) -> Optional[List[Dict]]:
        url = it["url"]
        if not robots_ok(url):
            return []
        html = lib.fetch(url, min_delay=DELAY, timeout=60)
        if not html:
            st = lib.fetch.last.get("status")
            log.warning("fetch failed %s (status %s)", url, st)
            return [] if st in (404, 410) else None
        title, d, text, extra = site.extract(html, url)
        if len(text) < 80 or not d:
            log.info("empty/short %s (%d chars, date %s)", url, len(text), d)
            return []
        kind = extra.pop("kind_hint", None) or "article"
        return [{"id": lib.make_id(site.source, url), "country": "RU", "source": site.source, "outlet": site.outlet,
                 "org": site.org, "lang": site.lang, "date": d, "url": url, "title": title, "speaker": None,
                 "kind": kind, "text": text, "via": lib.fetch.last.get("via") or "direct",
                 "sitemap": it.get("sitemap"), **{k: v for k, v in extra.items() if v}}]
    return parse


HAVE: set = set()  # ids already in the docs file (the done-set's .done.txt is not synced by store_sync / CI)


def child_items(site: SmSite, loc: str, st: lib.State) -> List[Dict]:
    xml = _get(loc, timeout=180)
    if xml is None:
        return []
    return [{"url": u, "date": lm, "sitemap": loc} for u, lm in _locs(xml)
            if site.pat.match(u) and not st.is_done(u) and lib.make_id(site.source, u) not in HAVE]


def items(site: SmSite, st: lib.State, follow: bool) -> Iterator[Dict]:
    HAVE.update(lib.existing_ids(lib.docs_path("RU", site.source)))
    kids = site.children()
    log.info("%s: %d child sitemaps (%s .. %s)", site.source, len(kids), kids[0][1] if kids else "-", kids[-1][1] if kids else "-")
    last = 0.0
    for i in spread(len(kids)):
        loc = kids[len(kids) - 1 - i][0]  # spread order, starting from the newest child
        if st.is_done("sm:" + loc) and loc not in site.newest(kids):
            continue
        todo = child_items(site, loc, st)
        log.info("%s: child %s: %d URLs to fetch", site.source, loc.rsplit("/", 1)[-1], len(todo))
        for it in todo:
            if follow and time.time() - last >= 1800:
                last = time.time()
                for nloc in site.newest(kids):
                    yield from child_items(site, nloc, st)
            yield it
        yield {"url": "sm:" + loc, "marker": True}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("site", choices=sorted(SITES))
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    site = SITES[a.site]()
    st = BigState(f"ru_{a.site}")
    parse = make_parser(site)
    done_children: Dict[str, int] = {}

    def wrapped(it: Dict) -> Optional[List[Dict]]:
        if it.get("marker"):  # end of a child sitemap: mark it done unless some of its URLs failed
            return [] if not st.get("failed") or not done_children.get(it["url"]) else None
        rows = parse(it)
        if rows is None:
            done_children["sm:" + it["sitemap"]] = done_children.get("sm:" + it["sitemap"], 0) + 1
        return rows

    while True:
        gen = items(site, st, a.follow)
        if a.limit:
            gen = (x for _, x in zip(range(a.limit), gen))
        run_queue(gen, st, wrapped, "RU", lambda r: r["source"], batch=20, save_every=50)
        if not a.follow or a.limit:
            return
        time.sleep(1800)


if __name__ == "__main__":
    main()
