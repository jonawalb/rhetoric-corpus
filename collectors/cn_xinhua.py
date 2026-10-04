"""Xinhua News Agency (新华社), English and Chinese full-text articles.

Source name: cn_xinhua (docs/CN/cn_xinhua.jsonl), org Xinhua, outlet state_media. One language per document.

Discovery:
  recent    hub pages (english.news.cn home + section/list pages; www.news.cn home + section pages) carry
            `datasource:<32 hex>` blocks; each block's feed is <page dir>/ds_<id>.json (up to 1,000 newest items
            per block, published by the site for its own list pages). All item URLs on news.cn / xinhuanet.com
            are fetched live.
  backfill  Wayback CDX URL lists per month and path prefix (old www.xinhuanet.com/english/YYYY-MM/DD/c_N.htm and
            www.xinhuanet.com/<section>/YYYY-MM/DD/c_N.htm; new english.news.cn/YYYYMMDD/<hex>/c.html and
            www.news.cn/<section>/YYYYMMDD/<hex>/c.html). Pages are fetched LIVE first; the Wayback raw copy
            (the capture the CDX row names) only when the live page is gone. Newest month first.

robots.txt: www.news.cn / www.xinhuanet.com allow all; english.news.cn has none (404). 5 s between requests
per host (lib.fetch, robots checked).

    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_xinhua.py --lang en --follow
    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_xinhua.py --lang zh --follow
    ... recent | backfill [--months 2026-09:2015-01]       (single passes, for tests / CI)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
import cn_common as cc  # noqa: E402

SOURCE = "cn_xinhua"
DELAY = 5.0
log = lib.setup_logging("cn_xinhua")

HUBS = {
    "en": ["https://english.news.cn/", "https://english.news.cn/list/latestnews.htm"],
    "zh": ["https://www.news.cn/", "https://www.news.cn/politics/", "https://www.news.cn/world/",
           "https://www.news.cn/mil/index.htm", "https://www.news.cn/tw/index.htm", "https://www.news.cn/gangao/index.htm",
           "https://www.news.cn/comments/index.htm", "https://www.news.cn/legal/index.htm",
           "https://www.news.cn/fortune/index.htm", "https://www.news.cn/local/index.htm"],
}
HOSTS = ("news.cn", "xinhuanet.com")
# Wayback backfill prefixes (no scheme / www, as CDX wants). {ym} = YYYY-MM (old layout), {ym2} = YYYYMM (new).
ZH_SECTIONS = ("politics", "world", "mil", "tw", "gangao", "comments", "legal", "fortune", "local", "talking")
BACKFILL = {
    "en": ["xinhuanet.com/english/{ym}/", "news.cn/english/{ym}/", "english.news.cn/{ym2}"],
    "zh": [f"xinhuanet.com/{s}/{{ym}}/" for s in ZH_SECTIONS]
          + [f"news.cn/{s}/{{ym}}/" for s in ZH_SECTIONS]
          + [f"news.cn/{s}/{{ym2}}" for s in ZH_SECTIONS],
}
BODY_MARKERS = ('id="detail"', 'id="p-detail"', 'id="Content"', 'id="content"', 'class="content"',
                'class="article"', 'class="main-aticle"', 'id="articleContent"')
ARTICLE_RE = re.compile(r"(/\d{4}-\d{2}/\d{2}/c_\d+\.htm|/20\d{6}/[0-9a-f]{32}/c\.html)$")
FIRST_MONTH = "2010-01"


# ----------------------------------------------------------------------------------------- parsing
def canon(url: str) -> str:
    """Scheme-less, www-normalised key for de-duplication: 'news.cn/politics/20260929/<hex>/c.html'."""
    p = urllib.parse.urlsplit(url)
    host = p.netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host + p.path


def doc_id(url: str) -> str:
    m = re.search(r"/(20\d{6})/([0-9a-f]{32})/c\.html", url)
    if m:
        return lib.make_id(SOURCE, m.group(1) + m.group(2))
    m = re.search(r"/c_(\d+)\.htm", url)
    if m:
        return lib.make_id(SOURCE, "c_" + m.group(1))
    return lib.make_id(SOURCE, "https://" + canon(url))


def url_date(url: str) -> Optional[str]:
    m = re.search(r"/(20\d\d)(\d\d)(\d\d)/[0-9a-f]{32}/", url) or re.search(r"/(20\d\d)-(\d\d)/(\d\d)/c_", url)
    return cc.ymd(*m.groups()) if m else None


def parse(html: str, url: str, lang: str) -> Optional[Dict]:
    """{title, date, text, kind} or None when no body is found."""
    text = ""
    for marker in BODY_MARKERS:  # newest layout first; older xinhuanet.com layouts after
        body = cc.balanced_div(html, marker)
        if body:
            text = cc.paragraphs(body)
            if len(text) >= 100:
                break
    text = re.sub(r"\n?【纠错】.*$|\n?\[责任编辑[:：].*$|\n?责任编辑[:：].*$", "", text, flags=re.S).strip()
    if len(text) < 100:
        return None
    title = ""
    m = re.search(r'<span class="title">(.*?)</span>', html, re.S)
    if m:
        title = lib.clean_html(m.group(1))
    if not title:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        title = lib.clean_html(m.group(1)) if m else ""
    title = title or cc.meta(html, "og:title") or cc.title_tag(html).split("-")[0].strip()
    title = re.sub(r"\s+", " ", title).strip()
    d = cc.first_date(cc.meta(html, "publishdate")) or url_date(url)
    if not d:
        m = re.search(r'class="header-time[^"]*">(.*?)</div>', html, re.S)
        d = cc.first_date(re.sub(r"\s+", "", lib.clean_html(m.group(1))).replace("/", "-")) if m else None
    if not d:
        m = re.search(r'<p class="time">(.*?)</p>|class="h-time">(.*?)<', html, re.S)
        d = cc.first_date(lib.clean_html(next(g for g in m.groups() if g))) if m else None
    if not d:
        return None
    kind = "article"
    head = title + " " + text[:200]
    if lang == "zh" and re.search(r"新华社评论员|新华时评|新华锐评|新华社短评|辛识平|国纪平|宣言", head):
        kind = "commentary"
    if lang == "en" and re.search(r"^(Commentary|Xinhua Commentary|Editorial|Opinion)\b", title):
        kind = "commentary"
    return {"title": title, "date": d, "text": text, "kind": kind}


# ----------------------------------------------------------------------------------------- collecting
class Collector:
    def __init__(self, lang: str):
        self.lang = lang
        self.st = lib.State(f"cn_xinhua_{lang}")
        self.sink = cc.Sink(SOURCE, self.st, flush_every=10)
        self.failed = set(self.st.get("failed", []))

    def save(self) -> None:
        self.st["failed"] = sorted(self.failed)[-50000:]
        self.sink.flush()

    def take(self, url: str, wayback: Optional[str] = None) -> bool:
        """Fetch + parse + buffer one article; True if added."""
        did = doc_id(url)
        if self.sink.has(did) or did in self.failed:
            return False
        if not url.startswith("http"):
            url = "https://" + url
        html = cc.get(url, DELAY)
        via = "direct"
        p = parse(html, url, self.lang) if html else None
        if p is None and wayback:  # gone live, or a stub page: use the capture the CDX row names
            html = lib.fetch(wayback, min_delay=5)
            via = "wayback"
            p = parse(html, url, self.lang) if html else None
        if not p:
            self.failed.add(did)
            return False
        row = {"id": did, "country": "CN", "source": SOURCE, "outlet": "state_media", "org": "Xinhua",
               "lang": self.lang, "date": p["date"], "url": url, "title": p["title"] or None, "speaker": None,
               "kind": p["kind"], "text": p["text"], "via": via}
        if via == "wayback":
            row["wayback"] = wayback
        return self.sink.add(row)

    # -- recent: hub pages -> datasource feeds
    def feeds(self) -> List[str]:
        out: List[str] = []
        pages = list(HUBS[self.lang])
        if self.lang == "en":  # one level of section / list pages linked from the home page
            home = cc.get(pages[0], DELAY) or ""
            for href in re.findall(r'href="(/[a-zA-Z-]+/index\.htm|/list/[A-Za-z-]+\.htm)"', home):
                u = urllib.parse.urljoin(pages[0], href)
                if u not in pages and "video" not in u and "photo" not in u:
                    pages.append(u)
        for page in pages:
            html = cc.get(page, DELAY) or ""
            base = page if page.endswith("/") else page.rsplit("/", 1)[0] + "/"
            for ds in dict.fromkeys(re.findall(r"datasource:([0-9a-f]{32})", html)):
                out.append(base + f"ds_{ds}.json")
        return list(dict.fromkeys(out))

    def recent(self) -> int:
        """One pass over the datasource feeds, articles taken feed by feed (newest feed items first).

        The feed list (hub pages -> ds ids) is rebuilt once a day; feeds that held < 100 items last time
        (top-story boxes, mostly repeats of the big lists) are only re-read on that daily rebuild."""
        before = self.sink.added + len(self.sink.buf)
        today = date.today().isoformat()
        sizes: Dict[str, int] = self.st.get("feed_sizes") or {}
        daily = self.st.get("feeds_day") != today or not sizes
        if daily:
            sizes = {f: sizes.get(f, 10 ** 6) for f in self.feeds()}
        for feed in sorted(sizes, key=lambda f: -sizes[f]):
            if not daily and sizes[feed] < 100:
                continue
            body = cc.get(feed, DELAY)
            try:
                rows = json.loads(body)["datasource"] if body else []
            except (ValueError, KeyError, TypeError):
                rows = []
            sizes[feed] = len(rows)
            urls = []
            for r in rows:
                u = urllib.parse.urljoin(feed, r.get("publishUrl") or "")
                if ARTICLE_RE.search(u) and urllib.parse.urlsplit(u).netloc.endswith(HOSTS):
                    urls.append(u)
            n = 0
            for u in sorted(dict.fromkeys(urls), key=lambda x: url_date(x) or "", reverse=True):
                n += self.take(u)
                if n and n % 50 == 0:
                    self.save()
            log.info("%s feed %s: %d items, %d new", self.lang, feed.rsplit("/", 1)[-1], len(rows), n)
            self.st["feed_sizes"] = sizes
            self.save()
        self.st["feeds_day"] = today
        self.save()
        return self.sink.added + len(self.sink.buf) - before

    # -- backfill: Wayback CDX month lists
    def cdx(self, prefix: str) -> List[Tuple[str, str]]:
        q = [("url", prefix), ("matchType", "prefix"), ("output", "json"), ("fl", "original,timestamp"),
             ("filter", "statuscode:200"), ("collapse", "urlkey"), ("limit", "100000")]
        url = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(q)
        for attempt in range(5):
            body = lib.fetch(url, min_delay=5, timeout=300, retries=2)
            if body is not None:
                try:
                    rows = json.loads(body or "[]")
                except json.JSONDecodeError:
                    rows = None
                if rows is not None:
                    return [(o, t) for o, t in rows[1:] if ARTICLE_RE.search(urllib.parse.urlsplit(o).path)]
            time.sleep(120 * (attempt + 1))
        raise IOError(f"CDX unavailable for {prefix}")

    def backfill_month(self, ym: str) -> int:
        n = 0
        for tpl in BACKFILL[self.lang]:
            prefix = tpl.format(ym=ym, ym2=ym.replace("-", ""))
            key = f"cdx:{prefix}"
            if self.st.is_done(key):
                continue
            rows = self.cdx(prefix)
            seen = set()
            todo = []
            for o, t in rows:
                did = doc_id(o)
                if did in seen or self.sink.has(did) or did in self.failed:
                    continue
                seen.add(did)
                todo.append((o, t))
            log.info("%s backfill %s: %d CDX rows, %d to fetch", self.lang, prefix, len(rows), len(todo))
            for i, (o, t) in enumerate(todo):
                live = re.sub(r"^https?://", "https://", o.replace(":80/", "/"))
                if self.take(live, wayback=cc.wayback_raw(o, t)):
                    n += 1
                if i % 50 == 49:
                    self.save()
            self.st.mark_done(key)
            self.save()
        return n


def months(spec: Optional[str]) -> List[str]:
    """'2026-09:2015-01' -> newest-first list of YYYY-MM; default: last full month back to FIRST_MONTH."""
    today = date.today()
    hi, lo = (spec.split(":") if spec else (f"{today.year:04d}-{today.month:02d}", FIRST_MONTH))
    y, m = map(int, hi.split("-"))
    out = []
    while f"{y:04d}-{m:02d}" >= lo:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=("en", "zh"), required=True)
    ap.add_argument("mode", nargs="?", choices=("recent", "backfill"), default=None)
    ap.add_argument("--months", default=None, help="backfill range newest:oldest, e.g. 2026-09:2015-01")
    ap.add_argument("--follow", action="store_true", help="recent pass every 2 h, backfill months in between")
    a = ap.parse_args()
    c = Collector(a.lang)
    if a.mode == "recent" or (a.follow and a.mode is None):
        log.info("recent pass: %d new", c.recent())
    if a.mode == "backfill" and not a.follow:
        for ym in months(a.months):
            log.info("backfill %s: %d new", ym, c.backfill_month(ym))
        return
    if not a.follow:
        return
    last = time.time()
    current = date.today().strftime("%Y-%m")
    for ym in months(a.months):
        # the current month's CDX list is incomplete: never mark it done
        if ym == current:
            continue
        try:
            log.info("backfill %s: %d new", ym, c.backfill_month(ym))
        except IOError as e:
            log.warning("%s; continuing with recent passes only for now", e)
            time.sleep(1800)
        if time.time() - last > 2 * 3600:
            log.info("recent pass: %d new", c.recent())
            last = time.time()
    cc.follow(c.recent, 2 * 3600, f"cn_xinhua_{a.lang}")


if __name__ == "__main__":
    main()
