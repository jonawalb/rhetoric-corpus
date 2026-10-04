"""Russian state media reachable directly: RIA Novosti (ria.ru, ru), TASS English (tass.com, en),
RT English (www.rt.com, en), RT Russian (russian.rt.com, ru). One process per site:

    uv run --project ~/Projects/rhetoric-corpus python collectors/ru_statemedia.py ria_ru [--dry-run] [--follow]

Sampling (topic-neutral since 2026-10-02; documented in SOURCES.md). No keyword filter is applied anywhere.
  * RECENT window (last 90 days, fetched first): as before -- RIA: URL slug matches RECENT_SLUG_RU (broad
    politics/security list); tass.com: sections politics/world/russia/defense; rt.com: /news/ + /russia/;
    russian.rt.com: /russia/ + /world/. All stored, `sample: recent`.
  * SAMPLE, 2021-01-01 -> now (all dates), chosen by URL section only (Site.sample_sections):
      tass_com  sections politics, world, russia, defense (+ small politics specials, see TassCom): ALL items;
                economy and society: random 100 per section per month.
      rt_com    sections news, russia, usa, uk, africa, india, op-ed, business: ALL items (sport, podcast,
                pop-culture, on-air, viral, sponsored-content left out).
      rt_ru     sections russia, world, ussr, business: random 15,000 per sitemap year (sitemap lastmod dates
                are not publication dates, so the stratum is the sitemap_YYYY.xml file); sport, nopolitics,
                science left out.
      ria_ru    RIA URLs carry no section, so: simple random sample of ALL ria.ru articles, 1,500 per month
                (~8% of ~18k/month); every row keeps the page's own rubric/tag list in `tags`.
    "Random" = the `cap` URLs of the stratum with the smallest sha256(f"{SAMPLE_SEED}|{url}") -- a uniform
    random sample that is reproducible and does not reshuffle when a sitemap gains URLs. Fetch order is
    round-robin over strata (rank 0 of every month, then rank 1, ...), so a partial run is month-balanced.
    Rows: `sample` = section (stratum taken in full) | section_random (capped section stratum) | random (RIA).
    URLs fetched under the old nuclear rule and rejected are re-parsed from the existing raw/ cache (state
    key "s2:<url>"); docs already stored are skipped.
  * --follow: poll the site's RSS every 30 min and store every new item (`sample: rss`, no filter).
Article HTML is no longer cached (pages already in raw/<source>/ are still read); only RIA's monthly
sitemaps are cached. URL discovery uses only robots-allowed sitemaps/RSS; article pages are robots-checked
(wildcard aware).

DEEP / FULL mode (added 2026-10-03; the defaults above are unchanged when the flags are absent):
  --start YYYY-MM-DD | earliest   floor of the all-dates queue (default 2021-01-01). "earliest" = Site.earliest,
                                  the oldest date the site's own sitemaps serve (checked 2026-10-03):
                                  ria.ru 2004-12, sputnikglobe.com 2004-01, www.rt.com 2005 (sitemap_2000/2006..),
                                  russian.rt.com 2008, tass.com sitemaps 2020-10 only (older tass.com article URLs are
                                  discovered from the Wayback CDX URL index per section/year and fetched LIVE).
  --full                          take the sampled sections IN FULL instead of the capped random subsets:
                                  tass_com economy/society all items; rt_ru russia/world/ussr/business all items;
                                  ria_ru / sputnik_en every article. Rows keep `sample` = section (whole section)
                                  or, for the RIA platform, random + `rank_pos` (see below).
  RIA platform (ria_ru, sputnik_en) in --full mode runs in PASSES so millions of URLs never sit in memory: pass p takes,
  from every monthly sitemap >= start, the URLs ranked p*1500 .. (p+1)*1500-1 by the same seeded sha256 rank, and
  fetches them round-robin over months. Pass 0 on 2021+ months is exactly the existing 1,500/month sample. Any
  prefix of a month's rank order is a uniform random sample of that month, so `rank_pos` (position in the month's
  rank order) lets users rebuild an exact random sample of any size <= what has been fetched; months whose
  passes are exhausted are complete.
  With --follow, RSS is polled every 30 min DURING the long backfill as well (interleaved), not only after it.
  russian.rt.com: the sitemap index lists sitemap_YYYY_2/_3/_4 parts; earlier runs read only sitemap_YYYY.xml
  (so rt_ru enumerated about a third of its URLs). Fixed: all parts are read (stratum = year).
  sputnik_en: sputnikglobe.com (Rossiya Segodnya's English service, formerly sputniknews.com / en.rian.ru), same
  platform and parser as ria.ru.
"""
from __future__ import annotations

import argparse
import hashlib
import html as _html
import json
import re
import sys
import time
from pathlib import Path
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from ru_common import BigState, balanced_div, ctext, recent_cutoff, robots_ok, run_queue  # noqa: E402
from ru_passes import run_passes, with_rss  # noqa: E402

START = "2021-01-01"  # default floor; --start overrides it (module global, read by enumerate/select)
FULL = False          # --full: sampled sections in full (see docstring)
SAMPLE_SEED = "rhetoric-corpus-ru-statemedia-2026-10-02"
NUKE_SLUG_RU = (r"yadern|jadern|plutoni|atom|raket|snv|start|drsmd|rsmd|boegolov|boezarjad|ryabkov|rjabkov|pentagon|"
                r"sarmat|posejdon|poseydon|burevestnik|oreshnik|ispytan|alamos|minitmen|sentinel|nerasprostr|"
                r"^pro$|^pro-|strateg|^mbr|giperzvuk|rosatom|avangard|bomb|kontrol-nad")
RECENT_SLUG_RU = NUKE_SLUG_RU + (r"|putin|lavrov|ssha|tramp|peskov|zah?arova|medvedev|^mid$|^mid-|nato|kreml|ushakov|"
                                 r"^oon|peregovor|sanktsi|rubio|vashington|amerik|bayden|zapad|minoborony|^mo$|"
                                 r"belousov|gerasimov|shojgu|shoygu|nebenz|antonov|darchiev|poljansk|polyansk|uljanov|"
                                 r"ulyanov|vitkoff|uitkoff|dmitriev|kndr|kitaj|kitay|iran|evrop|es$|britan|germani|"
                                 r"frantsi|polsha|ukrain|kiev|zelensk|svo|spetsoper|bezopasnost|oborona|vooruzh")
log = lib.setup_logging("ru_statemedia")


def _locs(xml: str) -> List[Tuple[str, str]]:
    """[(loc, lastmod)] from a sitemap/urlset."""
    out = []
    for blk in re.findall(r"<(?:url|sitemap)>(.*?)</(?:url|sitemap)>", xml, re.S):
        loc = re.search(r"<loc>\s*(.*?)\s*</loc>", blk, re.S)
        lm = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", blk, re.S)
        if loc:
            out.append((_html.unescape(loc.group(1)), lm.group(1)[:10] if lm else ""))
    return out


def _get(url: str, cache: Optional[Path] = None, delay: float = 4) -> Optional[str]:
    if not robots_ok(url):
        log.warning("robots disallows %s", url)
        return None
    return lib.fetch(url, min_delay=delay, cache=cache)


def ld_date(html: str) -> Optional[str]:
    m = re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', html) or \
        re.search(r'article:published_time"\s+content="(\d{4})-?(\d{2})-?(\d{2})', html)
    if not m:
        return None
    return m.group(1) if m.lastindex == 1 else f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def og_title(html: str) -> str:
    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', html) or re.search(r"<title>(.*?)</title>", html, re.S)
    return _html.unescape(m.group(1)).strip() if m else ""


# ------------------------------------------------------------------------------------------- sites
class Site:
    source = ""
    org = ""
    lang = ""
    rss = ""

    def enumerate(self) -> List[Dict]:
        raise NotImplementedError

    def recent_ok(self, url: str) -> bool:
        raise NotImplementedError

    earliest = START  # oldest date the site's own sitemaps serve (for --start earliest)
    # section -> (stratum group, cap per period or None = take all). Sections not listed are not sampled.
    sample_sections: Dict[str, Tuple[str, Optional[int]]] = {}

    def section(self, url: str) -> Optional[str]:
        m = self.pat.match(url)
        return m.group(1) if m else None

    def sample_rule(self, it: Dict) -> Optional[Tuple[str, Optional[int]]]:
        """(group, cap) for the topic-neutral sample, or None if the URL's section is not sampled.
        With --full every sampled section is taken whole (cap None)."""
        rule = self.sample_sections.get(self.section(it["url"]) or "")
        return (rule[0], None) if rule and FULL else rule

    def extra(self, html: str, url: str) -> Dict:
        """Extra row fields (the URL section, so users can filter)."""
        sec = self.section(url)
        return {"section": sec} if sec else {}

    def extract(self, html: str, url: str) -> Tuple[str, Optional[str], str]:
        raise NotImplementedError

    def cache(self, url: str) -> Path:
        return lib.RAW / self.source / (lib.make_id("x", url).split(":")[1] + ".html")


class Ria(Site):
    source, org, lang, rss = "ria_ru", "RIA Novosti", "ru", "https://ria.ru/export/rss2/archive/index.xml"
    host, earliest = "ria.ru", "2004-12-01"
    pat = re.compile(r"https://ria\.ru/(\d{8})/([a-z0-9\-]+?)-?\d+\.html$")

    def month_sitemaps(self, start: str) -> List[Tuple[str, str]]:
        """[(YYYYMM, monthly sitemap URL)] for months >= start, newest first."""
        idx = _get(f"https://{self.host}/sitemap_article_index.xml") or ""
        out = []
        for loc, _ in _locs(idx):
            m = re.search(r"date_start=(\d{8})", loc)
            if m and m.group(1)[:6] >= start.replace("-", "")[:6]:
                out.append((m.group(1)[:6], loc))
        return sorted(set(out), reverse=True)

    def month_urls(self, ym: str, loc: str) -> List[Tuple[str, str]]:
        """[(url, date)] of one monthly sitemap. Sitemaps cached by earlier runs (raw/<source>/sitemaps/) are
        read; new ones are cached only for the default 2021+ window (disk), older months are re-fetched."""
        current = ym >= time.strftime("%Y%m")
        cache = lib.RAW / self.source / "sitemaps" / f"{ym}01.xml"
        use_cache = not current and (cache.exists() or ym >= "202101")
        sm = _get(loc, cache if use_cache else None) or ""
        out = []
        for u, _ in _locs(sm):
            mm = self.pat.match(u)
            if mm:
                d = mm.group(1)
                out.append((u, f"{d[:4]}-{d[4:6]}-{d[6:]}"))
        return out

    def enumerate(self) -> List[Dict]:
        items = []
        for ym, loc in sorted(self.month_sitemaps(START)):
            items.extend({"url": u, "date": d} for u, d in self.month_urls(ym, loc))
            log.info("%s sitemap %s: %d urls total", self.host, ym, len(items))
        return items

    def recent_ok(self, url: str) -> bool:
        m = self.pat.match(url)
        return bool(m and re.search(RECENT_SLUG_RU, m.group(2)))

    def sample_rule(self, it: Dict) -> Optional[Tuple[str, Optional[int]]]:
        return ("all", 1500)  # no section in RIA URLs: simple random sample of every article, 1,500/month

    def extra(self, html: str, url: str) -> Dict:
        m = re.search(r'"articleSection"\s*:\s*\[(.*?)\]', html, re.S)
        tags = [_html.unescape(t).strip() for t in re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))] if m else []
        return {"tags": [t for t in tags if t]}

    def extract(self, html: str, url: str) -> Tuple[str, Optional[str], str]:
        tm = re.search(r'<(?:div|h1) class="article__title"[^>]*>(.*?)</(?:div|h1)>', html, re.S)
        title = ctext(tm.group(1)) if tm else re.sub(r"\s+-\s+(?:РИА Новости|\d{2}\.\d{2}\.\d{4},\s+Sputnik).*$", "",
                                                     og_title(html))
        parts = []
        st = re.search(r'<(?:div|h2) class="article__second-title"[^>]*>(.*?)</(?:div|h2)>', html, re.S)
        if st:
            parts.append(ctext(st.group(1)))
        for blk in re.findall(r'<div class="article__(?:text|quote-text)[^"]*"[^>]*>(.*?)</div>', html, re.S):
            t = ctext(blk)
            if t:
                parts.append(t)
        return title, ld_date(html), "\n".join(parts)


class TassCom(Site):
    source, org, lang, rss = "tass_com", "TASS", "en", "https://tass.com/rss/v2.xml"
    pat = re.compile(r"https://tass\.com/([a-z\-]+)/(\d+)$")
    recent_sections = {"politics", "world", "russia", "defense", "military-and-defense"}
    sample_sections = {**{s: ("core", None) for s in (
        "politics", "world", "russia", "defense", "military-and-defense", "russias-foreign-policy",
        "military-operation-in-ukraine", "ukraine-crisis", "middle-east-conflict", "domestic-policy")},
        "economy": ("economy", 100), "society": ("society", 100)}

    earliest = "2014-01-01"  # tass.com sitemaps start 2020-10; 2014-2020 URLs come from the Wayback CDX index
    SITEMAP_FLOOR = "2020-10-01"

    def enumerate(self) -> List[Dict]:
        items = []
        for n in range(10):
            sm = _get(f"https://tass.com/sitemap/sitemap_news{n}.xml") or ""
            for u, lm in _locs(sm):
                if self.pat.match(u) and lm:
                    items.append({"url": u, "date": lm})
            log.info("tass.com sitemap_news%d: %d urls total", n, len(items))
        if START < self.SITEMAP_FLOOR:
            items.extend(self.cdx_items())
        return items

    def cdx_items(self) -> List[Dict]:
        """Article URLs of the sampled sections from the Wayback CDX URL index, per section and year
        (START year .. 2020); the URL list is cached in raw/tass_com/cdx/. date = first capture date (an upper
        bound of the publication date; rows get the page's own date). Articles are fetched live from tass.com."""
        out = []
        for sec in self.sample_sections:
            for y in range(int(START[:4]), int(self.SITEMAP_FLOOR[:4]) + 1):
                cache = lib.RAW / self.source / "cdx" / f"{sec}_{y}.txt"
                if cache.exists():
                    body = cache.read_text("utf-8")
                else:
                    q = (f"https://web.archive.org/cdx/search/cdx?url=tass.com/{sec}/&matchType=prefix&from={y}&to={y}"
                         "&fl=timestamp,original&collapse=urlkey&filter=statuscode:200")
                    body = lib.fetch(q, min_delay=5, timeout=300, retries=4)
                    if body is None:
                        log.warning("tass.com CDX %s %d failed; retried on the next run", sec, y)
                        continue
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(body, "utf-8")
                n0 = len(out)
                for ln in body.splitlines():
                    parts = ln.split()
                    if len(parts) != 2:
                        continue
                    u = re.sub(r"^https?://(?:www\.)?tass\.com(?::\d+)?", "https://tass.com", parts[1])
                    if self.pat.match(u):
                        out.append({"url": u, "date": f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:8]}"})
                log.info("tass.com CDX %s %d: %d urls", sec, y, len(out) - n0)
        return out

    def recent_ok(self, url: str) -> bool:
        m = self.pat.match(url)
        return bool(m and m.group(1) in self.recent_sections)

    def extract(self, html: str, url: str) -> Tuple[str, Optional[str], str]:
        title = re.sub(r"\s+-\s+[^-]*-\s+TASS$|\s+-\s+TASS$", "", og_title(html))
        body = balanced_div(html, 'class="text-content"') or ""
        paras = [ctext(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", body, re.S)]
        text = "\n".join(p for p in paras if p)
        if not text:
            text = ctext(body)
        lead = re.search(r'class="news-header__lead"[^>]*>(.*?)</', html, re.S)
        if lead and ctext(lead.group(1)) not in text:
            text = ctext(lead.group(1)) + "\n" + text
        return title, ld_date(html), text


class RtCom(Site):
    source, org, lang, rss = "rt_com", "RT", "en", "https://www.rt.com/rss/"
    pat = re.compile(r"https://www\.rt\.com/([a-z\-]+)/(\d+)-([a-z0-9\-]+)/?$")
    recent_sections = {"news", "russia"}
    sample_sections = {s: ("core", None) for s in ("news", "russia", "usa", "uk", "africa", "india", "op-ed", "business")}
    earliest, index = "2005-01-01", "https://www.rt.com/sitemap.xml"

    def year_sitemaps(self) -> List[Tuple[int, str]]:
        """[(year, sitemap URL)] from the site's sitemap index (sitemap_YYYY.xml and sitemap_YYYY_N.xml parts)."""
        out = []
        for loc, _ in _locs(_get(self.index) or ""):
            m = re.search(r"sitemap_(\d{4})(?:_\d+)?\.xml$", loc)
            if m and int(m.group(1)) >= int(START[:4]):
                out.append((int(m.group(1)), loc))
        return sorted(out)

    def year_items(self) -> List[Dict]:
        """Items of the yearly sitemaps. lastmod is a regeneration date, not a publication date, so date =
        min(lastmod, 31 Dec of the sitemap year) and the stratum (`period`) is the sitemap year."""
        items = []
        for y, loc in self.year_sitemaps():
            for u, lm in _locs(_get(loc) or ""):
                if self.pat.match(u) and lm:
                    items.append({"url": u, "date": min(lm, f"{y}-12-31"), "period": str(y)})
            log.info("%s %s: %d urls total", self.source, loc.rsplit("/", 1)[1], len(items))
        return items

    def enumerate(self) -> List[Dict]:
        items = self.year_items()
        for u, lm in _locs(_get("https://www.rt.com/newssitemap.xml") or ""):
            if self.pat.match(u):
                items.append({"url": u, "date": lm or time.strftime("%Y-%m-%d")})
        return items

    def recent_ok(self, url: str) -> bool:
        m = self.pat.match(url)
        return bool(m and m.group(1) in self.recent_sections)

    def extract(self, html: str, url: str) -> Tuple[str, Optional[str], str]:
        tm = re.search(r'<h1[^>]*class="article__heading"[^>]*>(.*?)</h1>', html, re.S)
        title = ctext(tm.group(1)) if tm else re.sub(r"\s+—\s+RT.*$", "", og_title(html))
        summ = balanced_div(html, 'class="article__summary') or ""
        body = balanced_div(html, 'class="article__text') or ""
        body = re.sub(r'<div class="read-more.*?</div>\s*</div>', " ", body, flags=re.S)
        paras = [ctext(p) for p in re.findall(r"<(?:p|h2|h3|blockquote)\b[^>]*>(.*?)</(?:p|h2|h3|blockquote)>", body, re.S)]
        text = "\n".join([ctext(summ)] + [p for p in paras if p]).strip()
        return title, ld_date(html), text


class RtRu(RtCom):
    source, org, lang, rss = "rt_ru", "RT", "ru", "https://russian.rt.com/rss"
    pat = re.compile(r"https://russian\.rt\.com/([a-z]+)/(?:news|article)/(\d+)-([a-z0-9\-]+)/?$")
    recent_sections = {"russia", "world"}
    sample_sections = {s: ("core", 15000) for s in ("russia", "world", "ussr", "business")}  # cap per sitemap year
    earliest, index = "2008-01-01", "https://russian.rt.com/sitemap.xml"

    def enumerate(self) -> List[Dict]:
        return self.year_items()  # all sitemap_YYYY[_N].xml parts (before 2026-10-03 only sitemap_YYYY.xml was read)

    def extract(self, html: str, url: str) -> Tuple[str, Optional[str], str]:
        title, d, text = super().extract(html, url)
        return re.sub(r"\s+—\s+РТ на русском.*$", "", title), d, text


class SputnikEn(Ria):
    """sputnikglobe.com: Rossiya Segodnya's English service (ex-sputniknews.com / en.rian.ru); RIA platform."""
    source, org, lang, rss = "sputnik_en", "Sputnik", "en", "https://sputnikglobe.com/export/rss2/archive/index.xml"
    host, earliest = "sputnikglobe.com", "2004-01-01"
    pat = re.compile(r"https://sputnikglobe\.com/(\d{8})/([a-z0-9\-]+?)-?\d+\.html$")

    def recent_ok(self, url: str) -> bool:
        return False  # no recent slug window: RSS + the all-articles passes cover it

    def month_urls(self, ym: str, loc: str) -> List[Tuple[str, str]]:
        sm = _get(loc) or ""  # never cached (disk)
        out = []
        for u, _ in _locs(sm):
            mm = self.pat.match(u)
            if mm:
                d = mm.group(1)
                out.append((u, f"{d[:4]}-{d[4:6]}-{d[6:]}"))
        return out


SITES: Dict[str, Callable[[], Site]] = {"ria_ru": Ria, "tass_com": TassCom, "rt_com": RtCom, "rt_ru": RtRu,
                                        "sputnik_en": SputnikEn}
RIA_PLATFORM = ("ria_ru", "sputnik_en")


# ------------------------------------------------------------------------------------------- run
def make_parser(site: Site) -> Callable[[Dict], Optional[List[Dict]]]:
    def parse(it: Dict) -> Optional[List[Dict]]:
        url = it["url"]
        if not robots_ok(url):
            return []
        cache = site.cache(url)  # read pages cached by earlier runs; new pages are not cached (disk)
        html = lib.fetch(url, min_delay=4, cache=cache if cache.exists() else None)
        if not html:
            st = lib.fetch.last.get("status")
            log.warning("fetch failed %s (status %s)", url, st)
            return [] if st in (404, 410) else None
        title, d, text = site.extract(html, url)
        d = d or it["date"]
        if len(text) < 80 or not d:
            log.info("empty/short article %s (%d chars)", url, len(text))
            return []
        via = lib.fetch.last.get("via") or "direct"
        return [{"id": lib.make_id(site.source, url), "country": "RU", "source": site.source, "outlet": "state_media",
                 "org": site.org, "lang": site.lang, "date": d, "url": url, "title": title, "speaker": None,
                 "kind": "article", "text": text, "via": "rss" if it["tier"] == "rss" else ("direct" if via == "cache" else via),
                 "sample": it["tier"], **({"rank_pos": it["rank_pos"]} if "rank_pos" in it else {}),
                 **site.extra(html, url)}]
    return parse


def sample_rank(url: str) -> int:
    """Deterministic uniform random rank of a URL (fixed seed SAMPLE_SEED)."""
    return int(hashlib.sha256(f"{SAMPLE_SEED}|{url}".encode()).hexdigest()[:16], 16)


def select(site: Site, items: List[Dict], cut: str) -> Tuple[List[Dict], List[Dict], Dict]:
    """(recent queue, sample queue, per-stratum stats). Recent = last-90-day window (fetched first);
    sample = topic-neutral section/random sample over ALL dates >= START (see module docstring).
    A URL already in the recent queue is not repeated in the sample queue (it still counts in its stratum)."""
    seen, recent = set(), []
    strata: Dict[Tuple[str, str], List[Tuple[int, Dict, Optional[int]]]] = defaultdict(list)
    for it in items:
        if it["url"] in seen or it["date"] < START:
            continue
        seen.add(it["url"])
        if it["date"] >= cut and site.recent_ok(it["url"]):
            recent.append({**it, "tier": "recent"})
        rule = site.sample_rule(it)
        if rule:
            group, cap = rule
            strata[(group, it.get("period") or it["date"][:7])].append((sample_rank(it["url"]), it, cap))
    in_recent = {i["url"] for i in recent}
    sample, stats = [], {}
    for (group, period), lst in strata.items():
        lst.sort(key=lambda x: x[0])
        cap = lst[0][2]
        chosen = lst if cap is None else lst[:cap]
        tier = "section" if cap is None else ("random" if group == "all" else "section_random")
        stats[f"{group}|{period}"] = [len(lst), len(chosen)]
        for pos, (_, it, _) in enumerate(chosen):
            if it["url"] not in in_recent:
                sample.append({**it, "tier": tier, "key": "s2:" + it["url"], "pos": pos})
    recent.sort(key=lambda i: -int(i["date"].replace("-", "")))
    sample.sort(key=lambda i: (i["pos"], -int(i["date"].replace("-", ""))))  # round-robin over strata
    return recent, sample, {"enumerated": len(seen), "strata": stats}


def rss_items(site: Site) -> List[Dict]:
    x = lib.fetch(site.rss, min_delay=4) or ""
    links = re.findall(r"<link>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</link>", x, re.S)
    out = []
    for u in links:
        u = re.sub(r"\?utm_.*$", "", _html.unescape(u.strip()))
        if site.pat.match(u):
            out.append({"url": u, "date": time.strftime("%Y-%m-%d"), "tier": "rss"})
    return out


def main() -> None:
    global START, FULL
    ap = argparse.ArgumentParser()
    ap.add_argument("site", choices=sorted(SITES))
    ap.add_argument("--dry-run", action="store_true", help="enumerate and print selection counts only")
    ap.add_argument("--follow", action="store_true", help="poll RSS every 30 min (also during the backfill), forever")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--start", default=START, help="floor date YYYY-MM-DD, or 'earliest' (Site.earliest)")
    ap.add_argument("--full", action="store_true", help="sampled sections in full (RIA platform: all articles, in passes)")
    a = ap.parse_args()
    site = SITES[a.site]()
    START = site.earliest if a.start == "earliest" else a.start
    FULL = a.full
    st = BigState(f"ru_{a.site}")
    cut = recent_cutoff(a.days)
    parse = make_parser(site)
    rss_fn = (lambda: rss_items(site)) if a.follow else None
    if a.follow and not a.dry_run:  # pick up the newest items first
        run_queue(rss_items(site), st, parse, "RU", lambda r: r["source"])
    if a.site in RIA_PLATFORM and (FULL or a.site == "sputnik_en"):
        if a.dry_run:
            print(json.dumps({"months": len(site.month_sitemaps(START)), "start": START}))
            return
        run_passes(site, st, parse, START, sample_rank, rss_fn, limit=a.limit)
    else:
        recent, sample, info = select(site, site.enumerate(), cut)
        have = lib.existing_ids(lib.docs_path("RU", site.source))
        todo_s = [i for i in sample if lib.make_id(site.source, i["url"]) not in have and not st.is_done(i["key"])]
        n_sel = sum(v[1] for v in info["strata"].values())
        sel = {"rule": "topic-neutral section/random sample (2026-10-02)" + (", --full" if FULL else ""),
               "seed": SAMPLE_SEED, "start": START, "enumerated": info["enumerated"], "recent": len(recent),
               "cutoff": cut, "sample_selected": n_sel, "sample_todo": len(todo_s),
               "sample_by_tier": {t: sum(i["tier"] == t for i in sample) for t in ("section", "section_random", "random")}}
        log.info("%s selection: %s", a.site, json.dumps(sel))
        if a.dry_run:
            print(json.dumps(sel))
            return
        st["selection"] = sel
        st.save()
        queue = recent + todo_s
        del sample, todo_s
        if a.limit:
            queue = queue[:a.limit]
        run_queue(with_rss(queue, rss_fn), st, parse, "RU", lambda r: r["source"], batch=20, save_every=50)
    while a.follow:
        time.sleep(1800)
        run_queue(rss_items(site), st, parse, "RU", lambda r: r["source"])


if __name__ == "__main__":
    main()
