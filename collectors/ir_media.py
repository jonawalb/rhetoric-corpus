"""Iranian state and state-affiliated news outlets served directly, FULL archives via the sites' own sitemaps.

One process per HOST (so lib's per-host gap is the site's whole request rate):

    uv run --project ~/Projects/rhetoric-corpus python collectors/ir_media.py <site> [--follow] [--limit N]
                                                                                    [--dry-run N]

Sites (see SOURCES.md for robots/rate notes). No keyword or topic filter anywhere: every article in the
archive sitemaps is fetched, newest first, and stored if it has a date and >= MIN_TEXT characters of text.
  CMS "didgah" (Didgah/Rasaneh CMS: {lang}-sitemap-newsarchive -> monthly urlsets -> /{lang}/news/<id>/<slug>)
    iribnews   www.irib-news.ir     IRIB News Agency (state broadcaster)              fa
    yjc        www.yjc.ir           Young Journalists Club (IRIB)                     fa
    mizan      www.mizanonline.ir   Mizan, the Judiciary's news agency                fa (en index empty)
    kayhan     kayhan.ir            Kayhan (editor appointed by the Leader)           fa, en
    javan      www.javanonline.ir   Javan (IRGC-affiliated daily)                     fa
    defapress  defapress.ir         Defa Press (Armed Forces news agency)             fa, en
    sobhesadegh sobhesadegh.ir      Sobh-e Sadegh (IRGC political bureau weekly)      fa
    snn        snn.ir               Student News Network (Basij-affiliated)           fa
    basijnews  basijnews.ir         Basij News (Basij Organization)                   fa
    jamejam    jamejamonline.ir     Jam-e Jam (IRIB-owned daily)                      fa
    iqna       iqna.ir              IQNA (state Quran news agency, ACECR)             fa, en
  CMS "newsstudio" (/sitemap/<Jalali year>/sitemap.xml -> daily urlsets -> /news/<id>/<slug>)
    mashregh   www.mashreghnews.ir  Mashregh News (IRGC-affiliated)                   fa
    icana      www.icana.ir         ICANA, the Majlis (parliament) news agency        fa
    shana      www.shana.ir         Shana, the Ministry of Petroleum's news agency    fa
  CMS "tasnim" (/{lang}/{lang}_sitemap.xml -> year -> month -> day urlsets -> /{lang}/news/Y/M/D/<id>/<slug>)
    tasnim     www.tasnimnews.ir    Tasnim News Agency (IRGC-affiliated)              fa, en
      (tasnimnews.com does not resolve from here; tasnimnews.ir serves the same site.)
  CMS "nour" (/sitemap.xml -> one urlset per day, base64-JSON names, all languages mixed; 2025-01-01 ->)
    nournews   nournews.ir          Nour News (outlet close to the Supreme National Security Council)  fa, en

Order: sitemap indexes are expanded lazily, newest leaf first (by lastmod, else by the numbers in the URL);
inside a leaf, highest article id first. A leaf whose items are all handled is recorded in state
("leaf_done") unless it is one of the newest LIVE_LEAVES of its index (those still grow). Closed indexes of
past years are cached under raw/<site>/. --follow: every FOLLOW_S seconds the site's "recent" sitemap is read
and new items go first; after the archive is exhausted the process keeps polling and re-walks the archive
once a day (only leaves not yet closed are fetched again).

Text = lead (subtitle / introtext) + body; title and the kicker (rutitr/uptitle) are kept separately.
Rows: outlet state_media, kind article, speaker null, extra fields section, kicker, site.
Persian text is stored as served (the index folds ي/ك); dates: article:published_time (Gregorian, converted
to the Tehran date) or the page's Solar Hijri date (ir_common).
"""
from __future__ import annotations

import argparse
import base64
import html as _html
import json
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from ir_common import (BigState, balanced_div, fa_norm, jalali_str_to_iso, robots_ok, tehran_date,  # noqa: E402
                       to_ascii_digits, write_docs_fast)

MIN_TEXT = 120
LIVE_LEAVES = 3
FOLLOW_S = 1800
MONTHS_EN = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                         "september", "october", "november", "december"), 1)}


@dataclass(frozen=True)
class Site:
    name: str
    base: str
    cms: str
    langs: Dict[str, str]                 # lang -> source name
    org: str
    min_delay: float = 4.0
    extra_hosts: Tuple[str, ...] = field(default_factory=tuple)
    outlet: str = "state_media"           # "media" for outlets that are not state-owned/-run
    gregorian: bool = False               # newsstudio: yearly indexes named by Gregorian (not Solar Hijri) year


SITES: Dict[str, Site] = {s.name: s for s in (
    Site("iribnews", "https://www.irib-news.ir", "didgah", {"fa": "ir_iribnews_fa"}, "IRIB News Agency"),
    Site("yjc", "https://www.yjc.ir", "didgah", {"fa": "ir_yjc_fa"}, "Young Journalists Club (IRIB)"),
    Site("mizan", "https://www.mizanonline.ir", "didgah", {"fa": "ir_mizan_fa"},
         "Mizan News Agency (Judiciary)"),
    Site("kayhan", "https://kayhan.ir", "didgah", {"fa": "ir_kayhan_fa", "en": "ir_kayhan_en"}, "Kayhan"),
    Site("javan", "https://www.javanonline.ir", "didgah", {"fa": "ir_javan_fa"}, "Javan (IRGC-affiliated)"),
    Site("defapress", "https://defapress.ir", "didgah", {"fa": "ir_defapress_fa", "en": "ir_defapress_en"},
         "Defa Press (Armed Forces)"),
    Site("sobhesadegh", "https://sobhesadegh.ir", "didgah", {"fa": "ir_sobhesadegh_fa"},
         "Sobh-e Sadegh (IRGC political bureau)"),
    Site("snn", "https://snn.ir", "didgah", {"fa": "ir_snn_fa"}, "Student News Network (Basij)"),
    Site("basijnews", "https://basijnews.ir", "didgah", {"fa": "ir_basijnews_fa"}, "Basij News"),
    Site("jamejam", "https://jamejamonline.ir", "didgah", {"fa": "ir_jamejam_fa"}, "Jam-e Jam (IRIB daily)"),
    Site("iqna", "https://iqna.ir", "didgah", {"fa": "ir_iqna_fa", "en": "ir_iqna_en"},
         "IQNA (International Quran News Agency, ACECR)"),
    Site("shana", "https://www.shana.ir", "newsstudio", {"fa": "ir_shana_fa"}, "Shana (Ministry of Petroleum)"),
    Site("mashregh", "https://www.mashreghnews.ir", "newsstudio", {"fa": "ir_mashregh_fa"},
         "Mashregh News (IRGC-affiliated)"),
    Site("icana", "https://www.icana.ir", "newsstudio", {"fa": "ir_icana_fa"}, "ICANA (Majlis news agency)",
         extra_hosts=("https://icana.ir",)),
    Site("tasnim", "https://www.tasnimnews.ir", "tasnim", {"fa": "ir_tasnim_fa", "en": "ir_tasnim_en"},
         "Tasnim News Agency"),
    Site("nournews", "https://nournews.ir", "nour", {"fa": "ir_nournews_fa", "en": "ir_nournews_en"},
         "Nour News (close to the Supreme National Security Council)"),
    # Added 2026-10-04 (verified with --dry-run on live pages first).
    Site("khabaronline", "https://www.khabaronline.ir", "newsstudio", {"fa": "ir_khabaronline_fa"},
         "Khabar Online (private, Larijani-aligned)", outlet="media"),
    Site("hamshahri", "https://www.hamshahrionline.ir", "newsstudio", {"fa": "ir_hamshahri_fa"},
         "Hamshahri Online (Tehran Municipality)", outlet="media"),
    Site("abna", "https://fa.abna24.com", "newsstudio", {"fa": "ir_abna_fa"},
         "ABNA (Ahl al-Bayt World Assembly)"),
    Site("abna_en", "https://en.abna24.com", "newsstudio", {"en": "ir_abna_en"},
         "ABNA (Ahl al-Bayt World Assembly)", gregorian=True),
    Site("hawzah", "https://www.hawzahnews.com", "newsstudio", {"fa": "ir_hawzah_fa"},
         "Hawzah News Agency (Seminary Management Center)"),
    Site("quds", "https://www.qudsonline.ir", "newsstudio", {"fa": "ir_quds_fa"},
         "Quds daily (Astan Quds Razavi)"),
    Site("ettelaat", "https://www.ettelaat.com", "newsstudio", {"fa": "ir_ettelaat_fa"},
         "Ettelaat (Leader-appointed management)"),
    Site("rasa", "https://rasanews.ir", "didgah", {"fa": "ir_rasa_fa", "en": "ir_rasa_en"},
         "Rasa News Agency (Qom seminary)", outlet="media"),
)}


# ------------------------------------------------------------------------------------------------ fetch
class Fetcher:
    """lib.fetch with robots (wildcard-aware), the site's Crawl-delay and a stop on persistent 403s."""

    def __init__(self, site: Site, log):
        self.site, self.log, self.r403, self.fails = site, log, 0, 0
        cd = lib.crawl_delay(site.base + "/")
        self.delay = max(site.min_delay, cd or 0.0)

    def get(self, url: str, cache: Optional[Path] = None) -> Tuple[Optional[str], int]:
        if not robots_ok(url):
            p = urllib.parse.urlsplit(url)
            if f"{p.scheme}://{p.netloc}" in lib._robots_retry:  # robots.txt unreachable: transient, retry later
                self.log.warning("%s unreachable; backing off %ds (not marked done) %s", p.netloc,
                                 lib.TRANSIENT_BACKOFF_S, url)
                time.sleep(lib.TRANSIENT_BACKOFF_S)
                return None, 0
            self.log.warning("robots.txt disallows %s", url)
            return None, -1
        body = lib.fetch(url, min_delay=self.delay, cache=cache, timeout=90)
        status = lib.fetch.last.get("status", 0)
        if body is None and status == 403:
            self.r403 += 1
            if self.r403 >= 30:
                self.log.error("30 consecutive HTTP 403 from %s: the site is refusing us; pausing 6 h", self.site.base)
                time.sleep(6 * 3600)
                self.r403 = 0
        elif body is not None:
            self.r403 = 0
        if body is None and status == 0:
            self.fails += 1
            if self.fails >= 20:
                self.log.warning("20 consecutive network failures; pausing 15 min")
                time.sleep(900)
                self.fails = 0
        elif body is not None:
            self.fails = 0
        return body, status


# --------------------------------------------------------------------------------------------- sitemaps
def locs(xml: str) -> Tuple[bool, List[Tuple[str, str]]]:
    """(is_index, [(loc, lastmod)]) of a sitemap index or urlset."""
    if xml.lstrip().startswith("{"):  # some NewsStudio sites serve old day leaves as JSON {"urls": [{"loc": ...}]}
        try:
            data = json.loads(xml)
        except ValueError:
            return False, []
        return False, [(u["loc"], u.get("lastmode") or u.get("lastmod") or "") for u in data.get("urls") or []
                       if isinstance(u, dict) and u.get("loc")]
    is_index = "<sitemapindex" in xml[:2000]
    out = []
    for blk in re.findall(r"<(?:url|sitemap)>(.*?)</(?:url|sitemap)>", xml, re.S):
        loc = re.search(r"<loc>\s*(.*?)\s*</loc>", blk, re.S)
        lm = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", blk, re.S)
        if loc:
            out.append((_html.unescape(loc.group(1)).strip(), lm.group(1).strip() if lm else ""))
    return is_index, out


def _nums(url: str) -> Tuple[int, ...]:
    tail = urllib.parse.urlsplit(url).path
    return tuple(int(x) for x in re.findall(r"\d+", tail))


def _nour_name(day: str) -> str:
    raw = json.dumps({"model": "newsstudioDateRange", "date": day}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().replace("=", ",")


def _nour_day(url: str) -> str:
    """'YYYY-MM-DD' of a nournews day sitemap URL, '' for its other sitemaps (menus)."""
    b = url.rsplit("/", 1)[-1].removesuffix(".xml").replace(",", "=")
    try:
        d = json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)))
    except ValueError:
        return ""
    return d.get("date", "") if isinstance(d, dict) and d.get("model") == "newsstudioDateRange" else ""


def newest_first(children: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    if children and any(_nour_day(u) for u, _ in children):
        return sorted(((u, lm) for u, lm in children if _nour_day(u)), key=lambda c: _nour_day(c[0]), reverse=True)
    if children and all(lm[:4].isdigit() for _, lm in children):
        return sorted(children, key=lambda c: (c[1][:19], _nums(c[0])), reverse=True)
    return sorted(children, key=lambda c: _nums(c[0]), reverse=True)


def _closed(url: str) -> bool:
    """True for an index whose URL names a year before the current one (Gregorian or Solar Hijri)."""
    now = datetime.now(timezone.utc)
    years = [n for n in _nums(url) if 1300 <= n <= 1500 or 1990 <= n <= 2100]
    if not years:
        return False
    y = years[0]
    return y < (now.year if y > 1900 else now.year - 622)


def archive_roots(site: Site, lang: str, fx: Fetcher) -> List[str]:
    if site.cms == "didgah":
        return [f"{site.base}/{lang}-sitemap-newsarchive"]
    if site.cms == "tasnim":
        return [f"{site.base}/{lang}/{lang}_sitemap.xml"]
    if site.cms == "nour":
        return [f"{site.base}/sitemap.xml"]
    # newsstudio: current year in /sitemap/all/, past (Jalali or Gregorian) years in /sitemap/<year>/
    now = datetime.now(timezone.utc).year
    years = range(now, 1996, -1) if site.gregorian else range(now - 621, 1375, -1)
    return [f"{site.base}/sitemap/all/sitemap.xml"] + [f"{site.base}/sitemap/{y}/sitemap.xml" for y in years]


def recent_leaves(site: Site, lang: str) -> List[str]:
    if site.cms == "didgah":
        return [f"{site.base}/{lang}-sitemap-news"]
    if site.cms == "newsstudio":
        return [f"{site.base}/sitemap/news/sitemap.xml"]
    t = datetime.now(timezone.utc) + timedelta(hours=3, minutes=30)
    if site.cms == "nour":
        return [f"{site.base}/sitemap/{_nour_name(d.date().isoformat())}.xml" for d in (t, t - timedelta(days=1))]
    out = []
    for d in (t, t - timedelta(days=1)):
        out.append(f"{site.base}/{lang}/{d.year}/{d.month}/{d.day}/{lang}_{d.year}_{d.month}_{d.day}_sitemap.xml")
    return out


def leaves(url: str, site: Site, fx: Fetcher, log, depth: int = 0) -> Iterator[Tuple[str, int]]:
    """Leaf urlset URLs under an index, newest first: yields (leaf_url, rank among its siblings)."""
    cache = lib.RAW / f"ir_media_{site.name}" / ("idx_" + re.sub(r"\W", "_", urllib.parse.urlsplit(url).path) + ".xml") \
        if depth > 0 and _closed(url) else None
    xml, status = fx.get(url, cache=cache)
    if not xml:
        if status not in (404, 410):
            log.warning("index unavailable (%s): %s", status, url)
        return
    is_index, kids = locs(xml)
    if not is_index:
        yield url, 0
        return
    for rank, (loc, _) in enumerate(newest_first(kids)):
        loc = loc.replace("http://", "https://")
        if site.cms != "tasnim" or depth >= 2:   # didgah/newsstudio: root children are urlsets; tasnim: lang>year>month>day
            yield loc, rank
        else:
            yield from leaves(loc, site, fx, log, depth + 1)


# ------------------------------------------------------------------------------------------------ parse
def _meta(html: str, prop: str) -> Optional[str]:
    m = re.search(r'<meta[^>]+(?:property|name|itemprop)="%s"[^>]*content="([^"]*)"' % re.escape(prop), html) or \
        re.search(r'<meta[^>]+content="([^"]*)"[^>]*(?:property|name|itemprop)="%s"' % re.escape(prop), html)
    return _html.unescape(m.group(1)).strip() if m else None


def _div_at(html: str, start: int) -> str:
    """Inner HTML of the <div>/<section> whose opening tag starts at `start` (balanced on nested tags of that name)."""
    tag = re.match(r"<(\w+)", html[start:start + 20]).group(1).lower()
    depth = 0
    for m in re.finditer(r"<(/?)%s\b[^>]*>" % tag, html[start:], re.I):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return html[html.find(">", start) + 1:start + m.start()]
    return html[html.find(">", start) + 1:]


def _div_text(html: str, pattern: str) -> str:
    m = re.search(pattern, html)
    if not m:
        return ""
    inner = _div_at(html, m.start())
    return lib.clean_html(re.sub(r"\s+", " ", inner or "").replace("</p>", "</p>\n"))


def _ctext(fragment: Optional[str]) -> str:
    return lib.clean_html(re.sub(r"\s+", " ", fragment or ""))


def parse_date(raw: Optional[str], fallback_text: str, lang: str) -> Optional[str]:
    if raw:
        r = to_ascii_digits(raw)
        if re.match(r"(19|20)\d\d-\d\d-\d\d", r):
            return tehran_date(r)
        d = jalali_str_to_iso(fa_norm(raw))
        if d:
            return d
    if fallback_text:
        t = fa_norm(fallback_text).replace("/", " ")
        d = jalali_str_to_iso(t)
        if d:
            return d
        m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+((?:19|20)\d\d)", t)
        if m and m.group(2).lower() in MONTHS_EN:
            return f"{m.group(3)}-{MONTHS_EN[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    return None


def parse_didgah(html: str, lang: str) -> Optional[Dict]:
    mt = re.search(r'<h1[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</h1>', html, re.S)
    title = _ctext(mt.group(1)) if mt else (_meta(html, "og:title") or "")
    pd = re.search(r'news_pdate_c[^>]*>(.*?)</div>', html, re.S)
    date = parse_date(_meta(html, "article:published_time"), _ctext(pd.group(1)) if pd else "", lang)
    body = _div_text(html, r'<(?:div|section)[^>]*class="body(?:\s[^"]*)?"')
    if not body:  # e.g. rasanews: class="body-news body"
        body = _div_text(html, r'<(?:div|section)[^>]*class="[^"]*\sbody(?:\s[^"]*)?"')
    lead = _div_text(html, r'<div[^>]*class="subtitle(?:\s[^"]*)?"')
    kicker = _div_text(html, r'<div[^>]*class="rutitr(?:\s[^"]*)?"')
    path = balanced_div(html, 'class="news_path') or ""
    section = " > ".join(x for x in (_ctext(a) for a in re.findall(r"<a[^>]*>(.*?)</a>", path, re.S)) if x) or None
    return {"title": title, "date": date, "lead": lead, "body": body, "kicker": kicker or None, "section": section}


def parse_newsstudio(html: str, lang: str) -> Optional[Dict]:
    mt = re.search(r'<h1[^>]*itemprop="headline"[^>]*>(.*?)</h1>', html, re.S) or \
        re.search(r'<h1[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</h1>', html, re.S)
    title = _ctext(mt.group(1)) if mt else (_meta(html, "og:title") or "")
    date = parse_date(_meta(html, "article:published_time") or _meta(html, "datePublished"), "", lang)
    ml = re.search(r'<p[^>]*class="[^"]*\bintrotext\b[^"]*"[^>]*>(.*?)</p>', html, re.S)
    mk = re.search(r'<[a-z0-9]+[^>]*class="[^"]*\buptitle\b[^"]*"[^>]*>(.*?)</[a-z0-9]+>', html, re.S)
    body = _div_text(html, r'<div[^>]*itemprop="articleBody"[^>]*>')
    bc = re.findall(r'<li[^>]*class="[^"]*breadcrumb-item[^"]*"[^>]*>(.*?)</li>', html, re.S)
    section = _meta(html, "article:section") or (" > ".join(x for x in map(_ctext, bc) if x) or None)
    return {"title": title, "date": date, "lead": _ctext(ml.group(1)) if ml else "", "body": body,
            "kicker": (_ctext(mk.group(1)) or None) if mk else None, "section": section}


def parse_tasnim(html: str, lang: str) -> Optional[Dict]:
    mt = re.search(r'<h1[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</h1>', html, re.S)
    ogt = _meta(html, "og:title") or ""
    title = _ctext(mt.group(1)) if mt else ogt.split(" - ")[0]
    date = parse_date(_meta(html, "datePublished") or _meta(html, "article:published_time"), "", lang)
    ml = re.search(r'<h3[^>]*class="[^"]*\blead\b[^"]*"[^>]*>(.*?)</h3>', html, re.S)
    body = _div_text(html, r'<div[^>]*class="story(?:\s[^"]*)?"[^>]*>')
    sec = re.search(r" - (.+?) news - ", ogt) or re.search(r" - (.+?) - ", ogt)
    mk = re.search(r'<h2[^>]*class="[^"]*\bsubtitle\b[^"]*"[^>]*>(.*?)</h2>', html, re.S)
    return {"title": title, "date": date, "lead": _ctext(ml.group(1)) if ml else "", "body": body,
            "kicker": (_ctext(mk.group(1)) or None) if mk else None, "section": sec.group(1) if sec else None}


def parse_nour(html: str, lang: str) -> Optional[Dict]:
    ld = {}
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("@type") == "NewsArticle":
            ld = d
    i = html.find("<!-- NEWS LEAD -->")
    ml = re.search(r"<p[^>]*>(.*?)</p>", html[i:i + 5000], re.S) if i > 0 else None
    a, b = html.find("<!-- NEWS CONTENT -->"), html.find("<!-- END OF NEWS CONTENT -->")
    body = lib.clean_html(re.sub(r"\s+", " ", html[a:b]).replace("</p>", "</p>\n")) if 0 < a < b else ""
    up = re.search(r"<!-- NEWS uptitle -->\s*<[^>]+>(.*?)</", html, re.S)
    return {"title": (ld.get("headline") or _meta(html, "og:title") or "").strip(),
            "date": parse_date(ld.get("datePublished"), "", lang), "lead": _ctext(ml.group(1)) if ml else "",
            "body": body, "kicker": (_ctext(up.group(1)) or None) if up else None,
            "section": ld.get("articleSection") or None}


PARSERS = {"didgah": parse_didgah, "newsstudio": parse_newsstudio, "tasnim": parse_tasnim, "nour": parse_nour}
ID_RE = {"didgah": re.compile(r"/news/(\d+)"), "newsstudio": re.compile(r"/news/(\d+)"),
         "tasnim": re.compile(r"/news/\d{4}/\d\d/\d\d/(\d+)"), "nour": re.compile(r"/news/(\d+)")}


# ------------------------------------------------------------------------------------------------- run
class Runner:
    def __init__(self, site: Site, dry: int, limit: int):
        self.site, self.dry, self.limit = site, dry, limit
        self.log = lib.setup_logging(f"ir_media_{site.name}")
        self.fx = Fetcher(site, self.log)
        self.st = BigState(f"ir_media_{site.name}")
        self.leaf_done = set(self.st.get("leaf_done") or [])
        self.pending: Dict[str, List[Dict]] = {}
        self.written = 0
        self.last_recent = 0.0

    def flush(self) -> None:
        for src, rows in self.pending.items():
            if rows and not self.dry:
                added, total = write_docs_fast("IR", src, rows)
                self.written += added
        self.pending.clear()
        self.st["leaf_done"] = sorted(self.leaf_done)
        if not self.dry:
            self.st.save()

    def item(self, url: str, lang: str) -> bool:
        """Fetch/parse/store one article. False = transient failure (not marked done)."""
        m = ID_RE[self.site.cms].search(url)
        if not m:
            return True
        key = f"{lang}:{m.group(1)}"
        if self.st.is_done(key):
            return True
        html, status = self.fx.get(url)
        if html is None:
            if status in (404, 410, -1):
                self.st.mark_done(key)
                return True
            return False
        try:
            art = PARSERS[self.site.cms](html, lang)
        except Exception as e:  # noqa: BLE001 - one odd page must not stop a months-long run
            self.log.exception("parse error %s: %s", url, e)
            art = None
        if not self.dry:
            self.st.mark_done(key)
        text = "\n".join(x for x in (art["lead"], art["body"]) if x) if art else ""
        if not art or not art["date"] or not art["title"] or len(text) < MIN_TEXT:
            if self.dry:
                print("SKIP", url, art and {k: (v[:80] if isinstance(v, str) else v) for k, v in art.items()})
            return True
        src = self.site.langs[lang]
        row = {"id": lib.make_id(src, m.group(1)), "country": "IR", "source": src, "outlet": self.site.outlet,
               "org": self.site.org, "lang": lang, "date": art["date"], "url": lib.fetch.last.get("url") or url,
               "title": art["title"], "speaker": None, "kind": "article", "text": text, "via": "direct",
               "fetched": lib.now_iso(), "section": art["section"], "kicker": art["kicker"], "site": self.site.name}
        if self.dry:
            print(f"OK {art['date']} [{art['section']}] {art['title'][:70]} | {len(text)} ch | {text[:160]!r}")
        self.pending.setdefault(src, []).append(row)
        if sum(len(v) for v in self.pending.values()) >= 20:
            self.flush()
        return True

    def leaf(self, url: str, lang: str, closable: bool) -> int:
        if url in self.leaf_done:
            return 0
        xml, status = self.fx.get(url)
        if not xml:
            return 0
        _, items = locs(xml)
        items = [(u.replace("http://", "https://"), lm) for u, lm in items if ID_RE[self.site.cms].search(u)]
        if self.site.cms == "nour":  # one urlset per day for all languages
            items = [t for t in items if f"/{lang}/news/" in t[0]]
        items.sort(key=lambda t: int(ID_RE[self.site.cms].search(t[0]).group(1)), reverse=True)
        n, ok = 0, True
        for u, _ in items:
            if self.dry and n >= self.dry:
                return n
            ok = self.item(u, lang) and ok
            n += 1
            if self.limit and self.written >= self.limit:
                break
        if ok and closable and not self.dry:
            self.leaf_done.add(url)
        self.flush()
        return n

    def recent(self) -> None:
        for lang in self.site.langs:
            for u in recent_leaves(self.site, lang):
                self.leaf(u, lang, closable=False)
        self.last_recent = time.time()
        self.log.info("recent pass done; %d docs written this run", self.written)

    def run(self, follow: bool) -> None:
        if self.dry:
            for lang in self.site.langs:
                for root in archive_roots(self.site, lang, self.fx):
                    for lf, _ in leaves(root, self.site, self.fx, self.log):
                        print("LEAF", lf)
                        self.leaf(lf, lang, closable=False)
                        break
                    break
            return
        if follow:
            self.recent()
        while True:
            if self.archive_pass(follow):
                return
            if not follow:
                return
            until = time.time() + 86400   # then poll; re-walk the archive daily (leaves that failed, e.g. 504s)
            while time.time() < until:
                time.sleep(FOLLOW_S)
                self.recent()
                self.flush()

    def archive_pass(self, follow: bool) -> bool:
        """One newest-first walk over all archive leaves. True if --limit was reached."""
        gens = {lang: (lf for root in archive_roots(self.site, lang, self.fx)
                       for lf in leaves(root, self.site, self.fx, self.log)) for lang in self.site.langs}
        active = list(gens)
        nleaf, seen = 0, {lang: 0 for lang in gens}
        while active:  # alternate languages leaf by leaf
            for lang in list(active):
                try:
                    lf, rank = next(gens[lang])
                except StopIteration:
                    active.remove(lang)
                    continue
                seen[lang] += 1
                self.leaf(lf, lang, closable=seen[lang] > LIVE_LEAVES)
                nleaf += 1
                if nleaf % 5 == 0:
                    self.log.info("%s %s leaf %s; %d docs written this run", self.site.name, lang, lf, self.written)
                if follow and time.time() - self.last_recent > FOLLOW_S:
                    self.recent()
                if self.limit and self.written >= self.limit:
                    self.flush()
                    return True
        self.flush()
        self.log.info("archive pass finished: %d docs written", self.written)
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("site", choices=sorted(SITES))
    ap.add_argument("--follow", action="store_true", help="read the recent sitemap every 30 min, forever")
    ap.add_argument("--limit", type=int, default=0, help="stop after N new docs (testing)")
    ap.add_argument("--dry-run", type=int, default=0, metavar="N",
                    help="parse N items of the newest archive leaf per language and print; write nothing")
    a = ap.parse_args()
    Runner(SITES[a.site], a.dry_run, a.limit).run(a.follow)


if __name__ == "__main__":
    main()
