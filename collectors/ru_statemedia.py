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
from ru_common import balanced_div, ctext, recent_cutoff, robots_ok, run_queue  # noqa: E402

START = "2021-01-01"
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

    # section -> (stratum group, cap per period or None = take all). Sections not listed are not sampled.
    sample_sections: Dict[str, Tuple[str, Optional[int]]] = {}

    def section(self, url: str) -> Optional[str]:
        m = self.pat.match(url)
        return m.group(1) if m else None

    def sample_rule(self, it: Dict) -> Optional[Tuple[str, Optional[int]]]:
        """(group, cap) for the topic-neutral sample, or None if the URL's section is not sampled."""
        return self.sample_sections.get(self.section(it["url"]) or "")

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
    pat = re.compile(r"https://ria\.ru/(\d{8})/([a-z0-9\-]+?)-?\d+\.html$")

    def enumerate(self) -> List[Dict]:
        idx = _get("https://ria.ru/sitemap_article_index.xml") or ""
        items = []
        for loc, _ in _locs(idx):
            m = re.search(r"date_start=(\d{8})", loc)
            if not m or m.group(1)[:6] < START.replace("-", "")[:6]:
                continue
            current = m.group(1)[:6] >= time.strftime("%Y%m")
            sm = _get(loc, None if current else lib.RAW / "ria_ru" / "sitemaps" / f"{m.group(1)}.xml") or ""
            for u, _ in _locs(sm):
                mm = self.pat.match(u)
                if mm:
                    d = mm.group(1)
                    items.append({"url": u, "date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "slug": mm.group(2)})
            log.info("ria sitemap %s: %d urls total", m.group(1), len(items))
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
        title = ctext(tm.group(1)) if tm else re.sub(r"\s+-\s+РИА Новости.*$", "", og_title(html))
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

    def enumerate(self) -> List[Dict]:
        items = []
        for n in range(10):
            sm = _get(f"https://tass.com/sitemap/sitemap_news{n}.xml") or ""
            for u, lm in _locs(sm):
                if self.pat.match(u) and lm:
                    items.append({"url": u, "date": lm})
            log.info("tass.com sitemap_news%d: %d urls total", n, len(items))
        return items

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

    def enumerate(self) -> List[Dict]:
        items = []
        for y in range(int(START[:4]), int(time.strftime("%Y")) + 1):
            sm = _get(f"https://www.rt.com/sitemap_{y}.xml") or ""
            for u, lm in _locs(sm):
                if self.pat.match(u) and lm:
                    items.append({"url": u, "date": lm})
            log.info("rt.com sitemap_%d: %d urls total", y, len(items))
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

    def enumerate(self) -> List[Dict]:
        items = []
        for y in range(int(START[:4]), int(time.strftime("%Y")) + 1):
            sm = _get(f"https://russian.rt.com/sitemap_{y}.xml") or ""
            for u, lm in _locs(sm):
                if self.pat.match(u) and lm:
                    items.append({"url": u, "date": lm, "period": str(y)})
            log.info("russian.rt.com sitemap_%d: %d urls total", y, len(items))
        return items

    def extract(self, html: str, url: str) -> Tuple[str, Optional[str], str]:
        title, d, text = super().extract(html, url)
        return re.sub(r"\s+—\s+РТ на русском.*$", "", title), d, text


SITES: Dict[str, Callable[[], Site]] = {"ria_ru": Ria, "tass_com": TassCom, "rt_com": RtCom, "rt_ru": RtRu}


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
                 "sample": it["tier"], **site.extra(html, url)}]
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
    ap = argparse.ArgumentParser()
    ap.add_argument("site", choices=sorted(SITES))
    ap.add_argument("--dry-run", action="store_true", help="enumerate and print selection counts only")
    ap.add_argument("--follow", action="store_true", help="after the sample queue, poll RSS every 30 min forever")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    site = SITES[a.site]()
    st = lib.State(f"ru_{a.site}")
    cut = recent_cutoff(a.days)
    parse = make_parser(site)
    if a.follow and not a.dry_run:  # pick up the newest items first
        run_queue(rss_items(site), st, parse, "RU", lambda r: r["source"])
    recent, sample, info = select(site, site.enumerate(), cut)
    have = lib.existing_ids(lib.docs_path("RU", site.source))
    todo_s = [i for i in sample if lib.make_id(site.source, i["url"]) not in have and not st.is_done(i["key"])]
    n_sel = sum(v[1] for v in info["strata"].values())
    sel = {"rule": "topic-neutral section/random sample (2026-10-02)", "seed": SAMPLE_SEED,
           "enumerated": info["enumerated"], "recent": len(recent), "cutoff": cut,
           "sample_selected": n_sel, "sample_todo": len(todo_s),
           "sample_by_tier": {t: sum(i["tier"] == t for i in sample) for t in ("section", "section_random", "random")}}
    log.info("%s selection: %s", a.site, json.dumps(sel))
    if a.dry_run:
        print(json.dumps(sel))
        return
    st["selection"] = sel
    st.save()
    queue = recent + todo_s
    if a.limit:
        queue = queue[:a.limit]
    run_queue(queue, st, parse, "RU", lambda r: r["source"])
    while a.follow:
        time.sleep(1800)
        run_queue(rss_items(site), st, parse, "RU", lambda r: r["source"])


if __name__ == "__main__":
    main()
