"""Taiwan news media: Focus Taiwan (CNA English, focustaiwan.tw), CNA Chinese (www.cna.com.tw) and the Taipei
Times (www.taipeitimes.com). One process per site:

    uv run --project ~/Projects/rhetoric-corpus python collectors/tw_media.py focustaiwan|cna|taipeitimes [--follow]

CNA (Central News Agency) is Taiwan's state-owned national news agency -> outlet "state_media", org "CNA".
The Taipei Times is privately owned -> outlet "media". Neither ever ships text publicly (headlines/links only).

Sampling (documented in SOURCES.md):
  * Sections only. Focus Taiwan: /politics/ and /cross-strait/. CNA: /news/aipl/ (政治) and /news/acn/ (兩岸).
    Taipei Times: /News/front/ and /News/editorials/ (all stored) and /News/taiwan/ (stored only when title or
    text matches TT_KEYWORD: cross-Strait, China/PLA, defence/military, diplomacy, US/Japan, parties,
    legislature, president/premier, recall/election). Every stored row carries `section` and `sample`.
  * RECENT (`sample: recent`): the sites' own robots-allowed discovery pages, all items in the sections above:
    CNA news sitemap (sitemap_fromremote_cfp.xml, ~1,000 newest URLs) + /list/aipl.aspx, /list/acn.aspx;
    Focus Taiwan Google-News sitemap + /politics, /cross-strait pages; Taipei Times /ajax_json/<page>/list/<section>
    pages 1-12 (the JSON behind the section pages' infinite scroll; ~2 weeks).
  * BACKFILL 2021-01-01 -> now (`sample: backfill`): article URLs are LISTED from Wayback CDX (one prefix query
    per section and month, newest month first) and each article is then FETCHED LIVE from the site. Coverage is
    therefore whatever Wayback has captured (CNA aipl Jan 2021: 844 distinct URLs, i.e. dense).
    The weekly Taipei Times sitemaps (/sitemap/YYYYWnn.xml) answer 403 AccessDenied, so are not used.
  * --follow: after the backfill, re-poll the RECENT pages every 30 min.
Dates: the page's article:published_time (Asia/Taipei); fallback the date encoded in the URL; else skipped.
Article pages are never cached on disk; CDX month listings are not cached either (state keeps the queue).
"""
from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import State, clean_html, fetch, make_id, setup_logging, write_docs  # noqa: E402
from tw_common import START, meta, sitemap_locs  # noqa: E402

CDX = ("https://web.archive.org/cdx/search/cdx?url={prefix}&matchType=prefix&collapse=urlkey"
       "&fl=original&filter=statuscode:200")
TT_KEYWORD = re.compile(
    r"(?i)\b(?:china|chinese|beijing|\bprc\b|\bpla\b|cross-strait|strait|xi jinping|taiwan affairs office|"
    r"mainland affairs|defen[cs]e|military|armed forces|army\b|navy|air force|coast guard|missile|drills?\b|"
    r"invasion|blockade|war\b|weapon|arms\b|f-16|submarine|security|espionage|spy\b|spies|infiltrat|united front|"
    r"diplomat|foreign minister|foreign affairs|ally\b|allies|embassy|united states|\bus\b|\bu\.s\.|washington|"
    r"trump|japan|philippine|sovereignty|independence|unification|kmt|kuomintang|\bdpp\b|democratic progressive|"
    r"taiwan people.s party|\btpp\b|legislat|lawmaker|president|premier|executive yuan|national security|"
    r"recall|election|referendum|ko wen-je|cheng li-wun|lai ching-te|tsai ing-wen|hsiao bi-khim|lin chia-lung)")

SITES: Dict[str, Dict] = {
    "focustaiwan": {
        "source": "tw_focustaiwan", "outlet": "state_media", "org": "CNA", "lang": "en",
        "sections": ["politics", "cross-strait"],
        "art": re.compile(r"^https?://focustaiwan\.tw/(politics|cross-strait)/(20\d{6})(\d{4})$"),
        "prefix": "focustaiwan.tw/{sec}/{ym}",
        "recent": ["https://focustaiwan.tw/GoogleNewsSitemap_fromRemote_cep2.xml",
                   "https://focustaiwan.tw/politics", "https://focustaiwan.tw/cross-strait"],
    },
    "cna": {
        "source": "tw_cna", "outlet": "state_media", "org": "CNA", "lang": "zh",
        "sections": ["aipl", "acn"],
        "art": re.compile(r"^https?://www\.cna\.com\.tw/news/(aipl|acn)/(20\d{6})(\d{4})\.aspx$"),
        "prefix": "www.cna.com.tw/news/{sec}/{ym}",
        "recent": ["https://www.cna.com.tw/sitemap_fromremote_cfp.xml",
                   "https://www.cna.com.tw/list/aipl.aspx", "https://www.cna.com.tw/list/acn.aspx"],
    },
    "taipeitimes": {
        "source": "tw_taipeitimes", "outlet": "media", "org": "Taipei Times", "lang": "en",
        "sections": ["front", "editorials", "taiwan"],
        "art": re.compile(r"^https?://www\.taipeitimes\.com/News/(front|editorials|taiwan)/archives/"
                          r"(\d{4})/(\d\d)/(\d\d)/(\d{7,12})$"),
        "prefix": "www.taipeitimes.com/News/{sec}/archives/{y}/{m}/",
        "recent": [f"https://www.taipeitimes.com/ajax_json/{p}/list/{s}"
                   for s in ("front", "editorials", "taiwan") for p in range(1, 13)],
    },
}
log = setup_logging("tw_media")


def norm_url(site: str, u: str) -> Optional[Tuple[str, str, str, str]]:
    """(canonical url, section, native id, url date) for an in-scope article URL, else None."""
    u = _html.unescape(u.strip()).split("?")[0].split("#")[0].replace("http://", "https://")
    if site == "focustaiwan" and u.startswith("/"):
        u = "https://focustaiwan.tw" + u
    if site == "cna" and u.startswith("/"):
        u = "https://www.cna.com.tw" + u
    if site == "taipeitimes" and not u.startswith("http"):
        u = "https://www.taipeitimes.com/" + u.lstrip("/")
    if site == "taipeitimes" and u.startswith("https://taipeitimes.com"):
        u = u.replace("https://taipeitimes.com", "https://www.taipeitimes.com")
    m = SITES[site]["art"].match(u)
    if not m:
        return None
    if site == "taipeitimes":
        return u, m.group(1), m.group(5), f"{m.group(2)}-{m.group(3)}-{m.group(4)}"
    d = m.group(2)
    return u, m.group(1), d + m.group(3), f"{d[:4]}-{d[4:6]}-{d[6:]}"


def discover_recent(site: str) -> List[str]:
    """In-scope article URLs from the site's own recent pages."""
    found: List[str] = []
    for page in SITES[site]["recent"]:
        body = fetch(page, min_delay=4)
        if not body:
            continue
        if site == "taipeitimes":
            try:
                found += [r.get("ar_url", "") for r in json.loads(body)]
            except (ValueError, AttributeError):
                log.warning("not JSON: %s", page)
            continue
        if page.endswith(".xml"):
            found += [loc for loc, _ in sitemap_locs(body)]
        else:
            found += re.findall(r'href=["\']([^"\']+)["\']', body)
    return [u for u in found if norm_url(site, u)]


def cdx_month(site: str, sec: str, y: int, m: int) -> Optional[List[str]]:
    """Wayback-captured article URLs of one section-month; None if Wayback failed (retry later)."""
    pre = SITES[site]["prefix"].format(sec=sec, ym=f"{y}{m:02d}", y=y, m=f"{m:02d}")
    body = fetch(CDX.format(prefix=pre), min_delay=5, timeout=300)
    if body is None:
        return None
    return [ln.strip() for ln in body.splitlines() if norm_url(site, ln.strip())]


# --------------------------------------------------------------------------------------------- parsing
PAYWALL = re.compile(r"Full text of the story is now in CNA English news archive")


def _paras(fragment: str) -> List[str]:
    fragment = re.sub(r"<figure\b.*?</figure>|<figcaption\b.*?</figcaption>", " ", fragment, flags=re.S | re.I)
    out = []
    for p in re.findall(r"<p(?:\s[^>]*)?>(.*?)</p>", fragment, re.S | re.I):
        t = clean_html(p).strip()
        if t and not re.match(r"(?i)^(enditem|photo:|\(by )", t):
            out.append(t)
    return out


def _title(html: str) -> str:
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    t = clean_html(m.group(1)) if m else ""
    if not t:
        t = re.sub(r"\s+[-|]\s+(Focus Taiwan|Taipei Times|.*中央社 CNA)$", "", meta(html, "og:title") or "")
    return t


def parse_cna(html: str) -> Optional[Dict]:
    """CNA and Focus Taiwan article page -> {title, date, text}."""
    m = re.search(r"""<div class=["']paragraph["']>""", html)
    if not m:
        return None
    rest = html[m.end():]
    ends = [i for i in (rest.find(x) for x in ("class=\"paragraph ", "<div class='author'", "</article>",
                                                "class=\"jsAdSlot")) if i > 0]
    paras = _paras(rest[:min(ends)] if ends else rest)
    # Focus Taiwan articles older than a few months show only the lead + a subscription notice (not evaded).
    kept = [p for p in paras if not PAYWALL.search(p)]
    pub = meta(html, "article:published_time") or ""
    return {"title": _title(html), "date": pub[:10] if re.match(r"\d{4}-\d\d-\d\d", pub) else None,
            "text": "\n".join(kept), "truncated": len(kept) < len(paras)}


def parse_tt(html: str) -> Optional[Dict]:
    """Taipei Times article page -> {title, date, text}."""
    m = re.search(r"<h1[^>]*>.*?</h1>", html, re.S)
    if not m:
        return None
    rest = html[m.end():]
    first = re.search(r"<p>(?!\s)", rest)
    if not first:
        return None
    rest = rest[first.start():]
    ends = [i for i in (rest.find(x) for x in ('<font class="red', 'class="popular', 'class="footer')) if i > 0]
    text = "\n".join(_paras(rest[:min(ends)] if ends else rest))
    pub = meta(html, "article:published_time") or ""
    return {"title": _title(html), "date": pub[:10] if re.match(r"\d{4}-\d\d-\d\d", pub) else None, "text": text}


# --------------------------------------------------------------------------------------------- run
def store(site: str, url: str, sample: str, st: State) -> bool:
    cfg = SITES[site]
    url, sec, nid, url_date = norm_url(site, url)  # type: ignore[misc]
    if st.is_done(url):
        return False
    html = fetch(url, min_delay=4)
    if not html:
        st.mark_done(url)
        return False
    art = (parse_tt if site == "taipeitimes" else parse_cna)(html)
    if not art or len(art["text"]) < 80:
        log.warning("no body: %s", url)
        st.mark_done(url)
        return False
    d = art["date"] or url_date
    if d and art["date"] and abs((date.fromisoformat(art["date"]) - date.fromisoformat(url_date)).days) > 2:
        log.warning("page date %s vs URL date %s: %s", art["date"], url_date, url)
    st.mark_done(url)
    if not d or d < START:
        return False
    if site == "taipeitimes" and sec == "taiwan" and not TT_KEYWORD.search(art["title"] + " " + art["text"][:800]):
        return False
    row = {
        "id": make_id(cfg["source"], nid), "country": "TW", "source": cfg["source"], "outlet": cfg["outlet"],
        "org": cfg["org"], "lang": cfg["lang"], "date": d, "url": url, "title": art["title"], "speaker": None,
        "kind": "article", "text": art["text"], "via": "direct", "section": sec, "sample": sample}
    if art.get("truncated"):
        row["truncated"] = "archive_paywall"   # lead paragraph only; full text needs a CNA archive subscription
    write_docs("TW", cfg["source"], [row])
    return True


def run_urls(site: str, urls: List[str], sample: str, st: State) -> int:
    seen, order = set(), []
    for u in urls:
        n = norm_url(site, u)
        if n and n[0] not in seen:
            seen.add(n[0])
            order.append(n)
    order.sort(key=lambda x: (x[3], x[2]), reverse=True)
    added = 0
    for i, (u, *_r) in enumerate(order):
        added += store(site, u, sample, st)
        if i % 20 == 19:
            st.save()
    st.save()
    return added


def months(start: str) -> List[Tuple[int, int]]:
    y, m = date.today().year, date.today().month
    out = []
    while (y, m) >= (int(start[:4]), int(start[5:7])):
        out.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("site", choices=sorted(SITES))
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--no-backfill", action="store_true")
    a = ap.parse_args()
    site = a.site
    st = State(SITES[site]["source"])
    n = run_urls(site, discover_recent(site), "recent", st)
    log.info("%s recent: %d new docs", site, n)
    if not a.no_backfill:
        for rnd in range(3):  # Wayback refuses connections at times: up to 3 passes over months not yet listed
            listed = set(st.get("cdx_done") or [])
            todo = [(s, y, m) for (y, m) in months(START) for s in SITES[site]["sections"]
                    if f"{s}:{y}-{m:02d}" not in listed]
            if not todo:
                break
            for s, y, m in todo:
                key = f"{s}:{y}-{m:02d}"
                urls = cdx_month(site, s, y, m)
                if urls is None:
                    log.warning("CDX failed for %s (pass %d)", key, rnd + 1)
                    continue
                n = run_urls(site, urls, "backfill", st)
                log.info("%s backfill %s: %d captured URLs, %d new docs", site, key, len(set(urls)), n)
                if (y, m) != (date.today().year, date.today().month):  # current month is re-listed next run
                    st["cdx_done"] = sorted(set(st.get("cdx_done") or []) | {key})
                st.save()
    while a.follow:
        time.sleep(1800)
        n = run_urls(site, discover_recent(site), "recent", st)
        log.info("%s follow: %d new docs", site, n)


if __name__ == "__main__":
    main()
