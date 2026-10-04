"""Chinese state / state-aligned media, fetched LIVE, one process per site (added 2026-10-04):

  chinanews  cn_chinanews   www.chinanews.com.cn  China News Service (中新网), zh. Daily scroll archive
                            /scroll-news/YYYY/MMDD/news.shtml back to 2008-08-01, walked newest day first.
  ecns       cn_ecns        www.ecns.cn           China News Service English, en. Live listings for new items; the
                            archive (no live listing past ~10 pages) is enumerated from Wayback CDX and fetched LIVE.
  cctv       cn_cctv_zh     news.cctv.com         CCTV news (央视网), zh. 500-item JSONP feed for new items; archive
                            URLs (/YYYY/MM/DD/ARTI...) from Wayback CDX per year, fetched LIVE.
  xwlb       cn_cctv_xwlb   tv.cctv.com           Xinwen Lianbo (新闻联播) segment pages with the CCTV text of each item,
                            day pages /lm/xwlb/day/YYYYMMDD.shtml back to 2016-02, newest day first. zh.
  cctv_en    cn_cctv_en     english.cctv.com      CCTV English, en. api.cntv.cn page lists (newest ~1,000) + Wayback
                            CDX per year, fetched LIVE.
  huanqiu    cn_huanqiu     *.huanqiu.com         Huanqiu (环球网, Global Times Chinese), zh. Channel front pages +
                            the site's /api/list feed for every category node seen in an article (~10,000 items per
                            node, ~1 year) + Wayback CDX of {china,world,mil,opinion,taiwan}.huanqiu.com/article/.
  guancha    cn_guancha     www.guancha.cn        Guancha (观察者网; privately owned, state-aligned -> outlet "media"),
                            zh. Front page/columns for new items; archive = descending walk over the article id
                            (URL /<any>/YYYY_MM_DD_<id>.shtml needs the right date: a wrong one redirects to the home
                            page, so a date cursor is kept while walking down).

ALL TOPICS (no keyword or section filter). Every request goes through cn_common.get (wildcard robots + lib.fetch:
robots.txt, >= 4 s per host or the site's Crawl-delay if larger, no Wayback for articles). Pages are fetched as
bytes and decoded by their declared charset (GBK -> gb18030). Documents need a date and >= MIN_TEXT characters.
Writers: ru_common.write_docs_fast (skips ids already in docs/CN/<source>.jsonl or sealed into the store).

    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_media.py <site> [--follow] [--limit N] [--dry-run N]
"""
from __future__ import annotations

import argparse
import logging
import html as _html
import json
import re
import sys
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
import cn_common as cc  # noqa: E402
from ru_common import BigState, write_docs_fast  # noqa: E402

MIN_TEXT = 80          # characters (zh text is dense; 80 CJK chars ~ two sentences)
FOLLOW_S = 1800
CST = timezone(timedelta(hours=8))
log = logging.getLogger("rhetoric-corpus.cn_media")


# ------------------------------------------------------------------------------------------------ fetch
def decode(body: bytes) -> str:
    head = body[:3000].decode("latin-1")
    m = re.search(r'charset=["\']?([A-Za-z0-9_-]+)', head)
    cs = (m.group(1).lower() if m else "utf-8")
    if cs in ("gb2312", "gbk", "gb18030", "x-gbk"):
        cs = "gb18030"
    try:
        return body.decode(cs, "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


def get(url: str, delay: float, timeout: int = 60) -> Tuple[Optional[str], int, str]:
    """(text or None, status, final url). status -1 = robots disallow."""
    if not cc.robots_ok(url):
        log.warning("robots.txt disallows %s", url)
        return None, -1, url
    body = lib.fetch(url, min_delay=delay, binary=True, timeout=timeout)
    last = lib.fetch.last
    if body is None:
        return None, last.get("status", 0), url
    return decode(body), 200, last.get("url") or url


def text_of(fragment: str) -> str:
    frag = re.sub(r"<!--.*?-->", " ", fragment or "", flags=re.S)
    frag = re.sub(r"<(script|style|table)\b.*?</\1>", " ", frag, flags=re.S | re.I)
    t = cc.paragraphs(re.sub(r"\s+", " ", frag))
    return "\n".join(x.strip() for x in t.split("\n") if x.strip())


def _h(fragment: Optional[str]) -> str:
    return lib.clean_html(re.sub(r"\s+", " ", fragment or "")).strip()


# ------------------------------------------------------------------------------------------------ parsers
# Each returns {"title","date","text", optional "speaker","section","credit"} or None.
def parse_chinanews(html: str, url: str) -> Optional[Dict]:
    m = re.search(r"/(\d{4})/(\d\d)-(\d\d)/", url)
    d = cc.ymd(*m.groups()) if m else None
    mt = (re.search(r'<h1 class="content_left_title">(.*?)</h1>', html, re.S)
          or re.search(r'<div class="title0">(.*?)</div>', html, re.S))
    title = _h(mt.group(1)) if mt else re.sub(r"\s*[—-]+\s*中新网\s*$", "", cc.title_tag(html))
    body = cc.balanced_div(html, 'class="left_zw"') or cc.balanced_div(html, "class=left_zw") \
        or cc.balanced_div(html, 'id="ad0"') or ""
    body = re.sub(r'<div class="adEditor">.*', "", body, flags=re.S)
    ma = re.search(r'id="author_baidu">作者：([^<]*)<', html)
    ms = re.search(r'id="source_baidu">来源：(?:<a[^>]*>)?([^<]*)<', html)
    return {"title": title, "date": d, "text": text_of(body), "speaker": (ma.group(1).strip() or None) if ma else None,
            "credit": ms.group(1).strip() if ms else None, "section": url.split("/")[3] if url.count("/") > 3 else None}


def parse_ecns(html: str, url: str) -> Optional[Dict]:
    m = re.search(r"/(\d{4})-(\d\d)-(\d\d)/", url) or re.search(r"/(\d{4})/(\d\d)-(\d\d)/", url)
    d = cc.ymd(*m.groups()) if m else None
    mt = re.search(r'<h1[^>]*id="contitle"[^>]*>(.*?)</h1>', html, re.S)
    title = _h(mt.group(1)) if mt else cc.title_tag(html)
    body = cc.balanced_div(html, 'id="yanse"') or ""
    body = re.sub(r'<p style="text-align:center;">\s*<a href="//www\.facebook\.com.*?</p>', "", body, flags=re.S)
    info = re.search(r'<div class="downinfo[^"]*"[^>]*>.*?<span>[\d: -]+</span><span>([^<]*)</span>', html, re.S)
    path = urllib.parse.urlsplit(url).path.strip("/").split("/")
    sec = "/".join(p for p in path[:2] if not re.match(r"\d", p)) or None
    return {"title": title, "date": d, "text": text_of(body), "credit": info.group(1).strip() if info else None,
            "section": sec}


def parse_cctv(html: str, url: str) -> Optional[Dict]:
    m = re.search(r"/(\d{4})/(\d\d)/(\d\d)/", url)
    d = cc.ymd(*m.groups()) if m else None
    mt = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    title = _h(mt.group(1)) if mt else re.sub(r"_[^_]*_央视网\(cctv\.com\)$", "", cc.title_tag(html))
    if "tv.cctv.com" in url:  # Xinwen Lianbo segment pages carry the title in <title> / og meta
        title = cc.meta(html, "description") if not mt else title
        title = re.sub(r"_[^_]*_央视网\(cctv\.com\)$", "", cc.title_tag(html)) or title
    body = cc.balanced_div(html, 'id="content_area"') or ""
    if not body.strip():  # 2026 news.cctv.com template: the text sits in a JS string filled in by script
        mc = re.search(r"var contentdate\s*=\s*'((?:[^'\\]|\\.)*)'", html, re.S)
        body = re.sub(r"\\(.)", r"\1", mc.group(1)) if mc else ""
    mi =re.search(r'<div class="info">\s*(?:来源：|Source:)\s*(?:<a[^>]*>)?([^<|]*?)\s*(?:</a>|\||\d)', html)
    return {"title": title, "date": d, "text": text_of(body), "credit": (mi.group(1).strip() or None) if mi else None}


def _textarea(html: str, cls: str) -> str:
    m = re.search(r'<textarea class="%s">(.*?)</textarea>' % re.escape(cls), html, re.S)
    return m.group(1) if m else ""


def parse_huanqiu(html: str, url: str) -> Optional[Dict]:
    ms = _textarea(html, "article-time")
    if not ms.strip().isdigit():
        return None
    d = datetime.fromtimestamp(int(ms) / 1000, CST).date().isoformat()
    body = _textarea(html, "article-content")
    if "&lt;p" in body[:200]:
        body = _html.unescape(body)
    author = _h(_textarea(html, "article-author")) or None
    return {"title": _h(_textarea(html, "article-title")), "date": d, "text": text_of(body), "speaker": author,
            "credit": _h(_textarea(html, "article-source-name")) or None,
            "section": _textarea(html, "article-catnode").strip() or None,
            "host": _textarea(html, "article-host").strip() or None}


def parse_guancha(html: str, url: str) -> Optional[Dict]:
    m = re.search(r"/(\d{4})_(\d\d)_(\d\d)_\d+\.shtml", url)
    d = cc.ymd(*m.groups()) if m else None
    mt = re.search(r'<li class="left left-main"[^>]*>\s*<h3>(.*?)</h3>', html, re.S) or re.search(r"<h3>(.*?)</h3>", html, re.S)
    title = _h(mt.group(1)) if mt else cc.title_tag(html)
    body = cc.balanced_div(html, 'class="content all-txt"') or ""
    ma = re.search(r'<div class="editor-intro fix">.*?<p><a[^>]*>([^<]*)</a>', html, re.S)
    ms = re.search(r"<span>来源：([^<]*)</span>", html)
    return {"title": title, "date": d, "text": text_of(body), "speaker": (ma.group(1).strip() or None) if ma else None,
            "credit": ms.group(1).strip() if ms else None}


# ------------------------------------------------------------------------------------------------ sites
class Site:
    name = source = lang = org = host = ""
    outlet = "state_media"
    delay = 4.0
    parse: Callable[[str, str], Optional[Dict]] = staticmethod(lambda h, u: None)
    art_re = re.compile("$^")

    def __init__(self, run: "Runner"):
        self.r = run

    def key(self, url: str) -> str:
        return url

    def norm(self, url: str) -> str:
        return url

    def recent(self) -> Iterator[str]:
        return iter(())

    def archive(self) -> Iterator[str]:
        return iter(())

    def links(self, html: str, base: str) -> List[str]:
        out, seen = [], set()
        for h in re.findall(r'href=["\']?([^"\' >]+)', html):
            u = self.norm(urllib.parse.urljoin(base, _html.unescape(h)))
            if self.art_re.search(u) and u not in seen:
                seen.add(u)
                out.append(u)
        return out


class ChinaNews(Site):
    name, source, lang, org, host = "chinanews", "cn_chinanews", "zh", "China News Service", "www.chinanews.com.cn"
    parse = staticmethod(parse_chinanews)
    art_re = re.compile(r"^https://www\.chinanews\.com\.cn/[\w/-]+/\d{4}/\d\d-\d\d/\d+\.shtml$")
    FIRST = date(2008, 8, 1)

    def norm(self, url: str) -> str:
        return re.sub(r"^https?://(?:www\.)?chinanews\.com(?:\.cn)?/", "https://www.chinanews.com.cn/", url)

    def key(self, url: str) -> str:
        m = re.search(r"/(\d{4})/(\d\d-\d\d)/(\d+)\.shtml", url)
        return f"{m.group(1)}{m.group(2)}-{m.group(3)}" if m else url

    def recent(self) -> Iterator[str]:
        for n in range(1, 11):
            html, st, fin = get(f"https://{self.host}/scroll-news/news{n}.html", self.delay)
            if html:
                yield from self.links(html, fin)

    def archive(self) -> Iterator[str]:
        d = date.fromisoformat(self.r.st.get("day") or (date.today() - timedelta(days=1)).isoformat())
        while d >= self.FIRST:
            html, st, fin = get(f"https://{self.host}/scroll-news/{d.year}/{d:%m%d}/news.shtml", self.delay)
            if html is None and st not in (404, 410, -1):
                time.sleep(300)
                continue
            for u in self.links(html or "", fin):
                yield u
            self.r.st["day"] = (d - timedelta(days=1)).isoformat()
            self.r.flush()
            d -= timedelta(days=1)


class CdxSite(Site):
    """Archive URLs enumerated once from Wayback CDX (cached under raw/cn_media_<site>/), fetched live."""
    cdx_prefixes: Tuple[str, ...] = ()
    cdx_re = ""

    def archive(self) -> Iterator[str]:
        d = lib.RAW / f"cn_media_{self.name}"
        d.mkdir(parents=True, exist_ok=True)
        for pref in self.cdx_prefixes:
            f = d / ("cdx_" + re.sub(r"\W", "_", pref) + ".txt")
            if not (self.r.st.get("cdx_done") or {}).get(pref):
                urls = []
                for orig, _ts in cc.cdx_urls(pref, self.cdx_re):
                    u = self.norm(orig)
                    if self.art_re.search(u):
                        urls.append(u)
                if not urls:  # CDX failed or empty: try again on the next pass
                    log.warning("%s: no CDX rows for %s (Wayback unreachable?)", self.name, pref)
                    continue
                f.write_text("\n".join(dict.fromkeys(urls)) + "\n", "utf-8")
                self.r.st["cdx_done"] = {**(self.r.st.get("cdx_done") or {}), pref: lib.now_iso()}
                self.r.flush()
                log.info("%s: CDX %s -> %d URLs", self.name, pref, len(urls))
            urls = [u for u in f.read_text("utf-8").split("\n") if u]
            yield from sorted(urls, reverse=True)  # date-bearing paths: newest first


class Ecns(CdxSite):
    name, source, lang, org, host = "ecns", "cn_ecns", "en", "China News Service (ECNS)", "www.ecns.cn"
    parse = staticmethod(parse_ecns)
    art_re = re.compile(r"^https://www\.ecns\.cn/(?:[\w/-]+/)?(?:\d{4}-\d\d-\d\d/detail-\w+|\d{4}/\d\d-\d\d/\d+)\.shtml$")
    cdx_prefixes = ("www.ecns.cn/",)
    cdx_re = r"(\d{4}-\d\d-\d\d/detail-\w+|\d{4}/\d\d-\d\d/\d+)\.shtml$"
    LISTS = ["", "china/politics/", "china/national/", "china/regional/", "world/", "business/", "cns-wire/",
             "voices/", "opinion/", "military/", "sci-tech/", "travel/", "culture/", "society/", "hongkong/",
             "taiwan/"] + [f"cns-wire/index_{n}.shtml" for n in range(2, 11)]

    def norm(self, url: str) -> str:
        return re.sub(r"^(?:https?:)?//(?:www\.)?ecns\.cn(?::80)?/", "https://www.ecns.cn/", url.split("#")[0])

    def key(self, url: str) -> str:
        m = re.search(r"detail-(\w+)\.shtml", url) or re.search(r"/(\d{4}/\d\d-\d\d/\d+)\.shtml", url)
        return m.group(1).replace("/", "") if m else url

    def recent(self) -> Iterator[str]:
        for p in self.LISTS:
            html, st, fin = get(f"https://{self.host}/{p}", self.delay)
            if html:
                yield from self.links(html, fin)


class Cctv(CdxSite):
    name, source, lang, org, host = "cctv", "cn_cctv_zh", "zh", "CCTV (央视网)", "news.cctv.com"
    parse = staticmethod(parse_cctv)
    art_re = re.compile(r"^https://news\.cctv\.com/\d{4}/\d\d/\d\d/ARTI\w+\.shtml$")
    cdx_re = r"/\d{4}/\d\d/\d\d/ARTI\w+\.shtml$"

    @property
    def cdx_prefixes(self) -> Tuple[str, ...]:  # type: ignore[override]
        return tuple(f"{self.host}/{y}/" for y in range(datetime.now().year, 2008, -1))

    def norm(self, url: str) -> str:
        return re.sub(r"^https?://([\w.]+\.cctv\.com)(?::80)?/", r"https://\1/", url.split("?")[0])

    def key(self, url: str) -> str:
        m = re.search(r"/((?:ARTI|VIDE)\w+)\.shtml", url)
        return m.group(1) if m else url

    def recent(self) -> Iterator[str]:
        for n in range(1, 8):
            js, st, _ = get(f"https://{self.host}/2019/07/gaiban/cmsdatainterface/page/news_{n}.jsonp?cb=news", self.delay)
            for u in re.findall(r'"url":"(https?:[^"]+)"', js or ""):
                u = self.norm(u.replace("\\/", "/"))
                if self.art_re.search(u):
                    yield u


class CctvEn(Cctv):
    name, source, lang, org, host = "cctv_en", "cn_cctv_en", "en", "CCTV English", "english.cctv.com"
    art_re = re.compile(r"^https://english\.cctv\.com/\d{4}/\d\d/\d\d/ARTI\w+\.shtml$")
    PAGES = ("PAGE1394789601117162", "PAGE5hlSTQke6t8017S7lGYg230209", "PAGEwOasl7Qr6J65StjCGpVh221115")

    def recent(self) -> Iterator[str]:
        html, _, fin = get(f"https://{self.host}/", self.delay)
        yield from self.links(html or "", fin)
        for pid in self.PAGES:
            for p in range(1, 11):
                js, st, _ = get(f"https://api.cntv.cn/newList/getMixListByPageId?serviceId=pcenglish&id={pid}&p={p}&n=100",
                                self.delay)
                us = [self.norm(u.replace("\\/", "/")) for u in re.findall(r'"url":"(https?:[^"]+)"', js or "")]
                if not us:
                    break
                yield from (u for u in us if self.art_re.search(u))


class Xwlb(Site):
    name, source, lang, org, host = "xwlb", "cn_cctv_xwlb", "zh", "CCTV Xinwen Lianbo (新闻联播)", "tv.cctv.com"
    parse = staticmethod(parse_cctv)
    art_re = re.compile(r"^https://tv\.cctv\.com/\d{4}/\d\d/\d\d/VIDE\w+\.shtml$")
    FIRST = date(2016, 2, 1)
    norm = Cctv.norm
    key = Cctv.key

    def day(self, d: date) -> Tuple[Optional[List[str]], int]:
        html, st, fin = get(f"https://{self.host}/lm/xwlb/day/{d:%Y%m%d}.shtml", self.delay)
        return (self.links(html, fin) if html else None), st

    def recent(self) -> Iterator[str]:
        for k in range(0, 3):
            us, _ = self.day(datetime.now(CST).date() - timedelta(days=k))
            yield from us or []

    def archive(self) -> Iterator[str]:
        d = date.fromisoformat(self.r.st.get("day") or (datetime.now(CST).date() - timedelta(days=1)).isoformat())
        while d >= self.FIRST:
            us, st = self.day(d)
            if us is None and st not in (404, 410, -1):
                time.sleep(300)
                continue
            yield from us or []
            self.r.st["day"] = (d - timedelta(days=1)).isoformat()
            self.r.flush()
            d -= timedelta(days=1)


class Huanqiu(CdxSite):
    name, source, lang, org, host = "huanqiu", "cn_huanqiu", "zh", "Huanqiu (环球网, Global Times Chinese)", "www.huanqiu.com"
    parse = staticmethod(parse_huanqiu)
    art_re = re.compile(r"^https://\w+\.huanqiu\.com/article/(?:\w{11}|\d{4}-\d\d/\d+\.html)$")
    CHANNELS = ("www", "china", "world", "mil", "opinion", "taiwan", "finance", "tech", "society")
    cdx_prefixes = tuple(f"{c}.huanqiu.com/article/" for c in ("china", "world", "mil", "opinion", "taiwan"))
    cdx_re = r"/article/(\w{11}|\d{4}-\d\d/\d+\.html)$"

    def norm(self, url: str) -> str:
        return re.sub(r"^(?:https?:)?//(\w+)\.huanqiu\.com(?::80)?/", r"https://\1.huanqiu.com/", url.split("?")[0])

    def key(self, url: str) -> str:
        m = re.search(r"/article/(\w{11})$", url) or re.search(r"/article/(\d{4}-\d\d/\d+)\.html$", url)
        return m.group(1) if m else url

    def seen_node(self, host: Optional[str], node: Optional[str]) -> None:
        if host and node and re.fullmatch(r"(/\w+)+", node):
            nodes = self.r.st.get("nodes") or {}
            if node not in nodes:
                nodes[node] = {"host": host, "off": 0}
                self.r.st["nodes"] = nodes

    def api(self, node: str, off: int) -> Optional[List[str]]:
        info = self.r.st["nodes"][node]
        q = urllib.parse.quote(f'"{node}"')
        js, st, _ = get(f"https://{info['host']}/api/list?node={q}&offset={off}&limit=24", self.delay)
        if js is None:
            return None
        try:
            items = json.loads(js).get("list") or []
        except ValueError:
            return []
        return [f"https://{x.get('host') or info['host']}/article/{x['aid']}" for x in items if x.get("aid")]

    def recent(self) -> Iterator[str]:
        for c in self.CHANNELS:
            html, _, fin = get(f"https://{c}.huanqiu.com/", self.delay)
            yield from self.links(html or "", fin)
        for node in list((self.r.st.get("nodes") or {})):
            yield from (self.api(node, 0) or [])

    def archive(self) -> Iterator[str]:
        done_nodes: set = set()
        while True:  # node feeds first (live, ~1 year each), re-checking for nodes discovered meanwhile
            todo = [n for n in (self.r.st.get("nodes") or {}) if n not in done_nodes]
            if not todo:
                break
            for node in todo:
                off = self.r.st["nodes"][node].get("off", 0)
                while off < 20000:
                    us = self.api(node, off)
                    if not us:
                        break
                    yield from us
                    off += 24
                    self.r.st["nodes"][node]["off"] = off
                done_nodes.add(node)
        yield from super().archive()


class Guancha(Site):
    name, source, lang, host = "guancha", "cn_guancha", "zh", "www.guancha.cn"
    org, outlet = "Guancha (观察者网; privately owned, state-aligned)", "media"
    parse = staticmethod(parse_guancha)
    art_re = re.compile(r"^https://www\.guancha\.cn/[\w%·.-]+/\d{4}_\d\d_\d\d_\d+\.shtml$")
    LISTS = ("", "mainnews-yw/", "column/GuoJi·ZhanLue", "column/JunShi", "column/ZhengZhi", "column/CaiJing",
             "column/ChanJing", "column/GongYe·KeJi", "column/ChengShi")
    FLOOR = 1

    def norm(self, url: str) -> str:
        return re.sub(r"^(?:https?:)?//(?:www\.)?guancha\.cn/", "https://www.guancha.cn/", url.split("#")[0].split("?")[0])

    def key(self, url: str) -> str:
        m = re.search(r"_(\d+)\.shtml$", url)
        return m.group(1) if m else url

    def recent(self) -> Iterator[str]:
        for p in self.LISTS:
            html, _, fin = get(f"https://{self.host}/{p}", self.delay)
            for u in self.links(html or "", fin):
                n = int(self.key(u))
                hi = self.r.st.get("max_id") or 0
                if n > hi:
                    self.r.st["max_id"], self.r.st["max_date"] = n, re.search(r"/(\d{4}_\d\d_\d\d)_", u).group(1)
                yield u

    def archive(self) -> Iterator[str]:
        """Descending id walk with a date cursor; yields resolved article URLs (fetch already done: cached)."""
        if not self.r.st.get("cursor"):
            list(self.recent())
            self.r.st["cursor"] = (self.r.st.get("max_id") or 0) - 1
            self.r.st["cur_date"] = self.r.st.get("max_date")
        misses = 0
        while self.r.st["cursor"] >= self.FLOOR and self.r.st.get("cur_date"):
            n = self.r.st["cursor"]
            cur = datetime.strptime(self.r.st["cur_date"], "%Y_%m_%d").date()
            hit = None
            if not self.r.st_done(str(n)):
                for k in (0, -1, -2, 1):
                    dd = cur + timedelta(days=k)
                    url = f"https://{self.host}/a/{dd:%Y_%m_%d}_{n}.shtml"
                    html, st, fin = get(url, self.delay)
                    if html and re.search(r"_\d+\.shtml$", fin):
                        hit = (url, html, dd)
                        break
                    if html is None and st not in (404, 410, -1):  # network trouble: retry the same id later
                        time.sleep(120)
                        break
                else:
                    self.r.mark(str(n))
            if hit:
                misses = 0
                self.r.cache[hit[0]] = hit[1]
                self.r.st["cur_date"] = hit[2].strftime("%Y_%m_%d")
                yield hit[0]
            else:
                misses += 1
                if misses >= 400:  # a long gap (site change / pause): step the date back a week
                    self.r.st["cur_date"] = (cur - timedelta(days=7)).strftime("%Y_%m_%d")
                    misses = 0
            self.r.st["cursor"] = n - 1


SITES: Dict[str, type] = {c.name: c for c in (ChinaNews, Ecns, Cctv, CctvEn, Xwlb, Huanqiu, Guancha)}


# ------------------------------------------------------------------------------------------------ runner
class Runner:
    def __init__(self, name: str, dry: int = 0, limit: int = 0):
        self.log = lib.setup_logging(f"cn_media_{name}")
        self.dry, self.limit = dry, limit
        self.st = BigState(f"cn_media_{name}")
        self.site: Site = SITES[name](self)
        self.ids = set() if dry else lib.existing_ids(lib.docs_path("CN", self.site.source))
        self.buf: List[Dict] = []
        self.cache: Dict[str, str] = {}
        self.written = self.n = 0
        self.last_recent = 0.0

    def st_done(self, key: str) -> bool:
        return self.st.is_done(key)

    def mark(self, key: str) -> None:
        if not self.dry:
            self.st.mark_done(key)

    def flush(self) -> None:
        if self.buf and not self.dry:
            added, total = write_docs_fast("CN", self.site.source, self.buf)
            self.written += added
            self.log.info("%s: +%d (total %d)", self.site.source, added, total)
        self.buf = []
        if not self.dry:
            self.st.save()

    def item(self, url: str) -> None:
        s = self.site
        key = s.key(url)
        doc_id = lib.make_id(s.source, key)
        if self.st.is_done(key) or doc_id in self.ids:
            self.cache.pop(url, None)
            return
        html = self.cache.pop(url, None)
        final = url
        if html is None:
            html, st, final = get(url, s.delay)
            if html is None:
                if st in (404, 410, -1):
                    self.mark(key)
                return  # network failure: not marked, retried on a later pass
        final = s.norm(final)
        try:
            p = s.parse(html, final if s.art_re.search(final) else url)
        except Exception as e:  # noqa: BLE001 - one odd page must not stop a long run
            self.log.exception("parse error %s: %s", url, e)
            p = None
        self.mark(key)
        if isinstance(s, Huanqiu) and p:
            s.seen_node(p.pop("host", None), p.get("section"))
            nk = s.key(final)  # old numeric URLs redirect to the 11-char aid: store under the aid
            if nk != key:
                key, doc_id = nk, lib.make_id(s.source, nk)
                if doc_id in self.ids:
                    return
                self.mark(nk)
        if not p or not p.get("date") or not p.get("title") or len(p.get("text") or "") < MIN_TEXT:
            if self.dry:
                print("SKIP", url, p and {k: (v[:60] if isinstance(v, str) else v) for k, v in p.items()})
            return
        row = {"id": doc_id, "country": "CN", "source": s.source, "outlet": s.outlet, "org": s.org, "lang": s.lang,
               "date": p["date"], "url": final, "title": p["title"], "speaker": p.get("speaker"), "kind": "article",
               "text": p["text"], "via": "direct", "section": p.get("section"), "credit": p.get("credit"),
               "site": s.name}
        if isinstance(s, Xwlb):
            row["kind"] = "broadcast"
        self.n += 1
        if self.dry:
            print(f"OK {row['date']} {row['title'][:60]} | {len(row['text'])} ch | {row['text'][:100]!r}")
            return
        self.ids.add(doc_id)
        self.buf.append(row)
        if len(self.buf) >= 20:
            self.flush()

    def recent(self) -> None:
        for u in self.site.recent():
            self.item(u)
            if self.stop():
                break
        self.flush()
        self.last_recent = time.time()
        self.log.info("%s: recent pass done; %d written this run", self.site.name, self.written)

    def stop(self) -> bool:
        return bool((self.limit and self.written + len(self.buf) >= self.limit) or (self.dry and self.n >= self.dry))

    def run(self, follow: bool) -> None:
        self.recent()
        while not self.stop():
            for i, u in enumerate(self.site.archive()):
                self.item(u)
                if self.stop():
                    break
                if i % 50 == 0:
                    self.flush()
                if follow and time.time() - self.last_recent > FOLLOW_S:
                    self.recent()
            self.flush()
            self.log.info("%s: archive pass finished; %d written this run", self.site.name, self.written)
            if not follow or self.stop():
                return
            until = time.time() + 86400
            while time.time() < until:
                time.sleep(FOLLOW_S)
                self.recent()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("site", choices=sorted(SITES))
    ap.add_argument("--follow", action="store_true", help="re-read the new-items lists every 30 min, forever")
    ap.add_argument("--limit", type=int, default=0, help="stop after N new docs (testing)")
    ap.add_argument("--dry-run", type=int, default=0, metavar="N", help="parse N documents and print; write nothing")
    a = ap.parse_args()
    Runner(a.site, a.dry_run, a.limit).run(a.follow and not a.dry_run)


if __name__ == "__main__":
    main()
