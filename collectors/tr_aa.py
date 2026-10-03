"""Anadolu Agency (www.aa.com.tr), Türkiye's state news agency: a SAMPLE of politics / security / world news,
EN (source tr_aa_en) and TR (source tr_aa_tr). outlet = state_media.

Sampling rule (not a full capture; AA publishes ~150 EN + ~250 TR news items a day):
  * recent  : the site's News sitemap (/<lang>/SiteMap/News, the latest ~1000 items, about one week),
              keeping only URLs whose section is in SECTIONS[lang]; with --follow it is re-read every 30 min.
              EN sections: turkiye, politics, world, middle-east, europe, americas, asia-pacific, eurasia,
              africa and any '*-war' section (e.g. russia-ukraine-war). TR: gundem, politika, dunya.
              (Economy, sports, culture, science, health, energy, opinion/analysis are left out.)
  * backfill: 2021 -> now for the narrower BACKFILL prefixes only (EN /en/politics/ and /en/turkiye/,
              TR /tr/politika/): article URLs are listed from the Wayback CDX URL index (what Wayback has
              captured — uneven), then each article is fetched LIVE from aa.com.tr. Rows carry
              `sample` = recent | backfill.
Date = JSON-LD NewsArticle datePublished on the article page (Turkey local time; the date part is kept).
Items without it are skipped and logged. Text = lead (JSON-LD articleBody/description) + body paragraphs.
robots.txt: allows articles and sitemaps (disallows /api/, ?s= search, previews). No article caches.

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/tr_aa.py --lang en|tr recent [--follow]
     uv run --project ~/Projects/rhetoric-corpus python collectors/tr_aa.py --lang en|tr backfill
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import State, clean_html, fetch, make_id, setup_logging, write_docs  # noqa: E402
from tr_common import START  # noqa: E402

BASE = "https://www.aa.com.tr"
SECTIONS = {"en": {"turkiye", "politics", "world", "middle-east", "europe", "americas", "asia-pacific", "eurasia",
                   "africa"},
            "tr": {"gundem", "politika", "dunya"}}
BACKFILL = {"en": ["aa.com.tr/en/politics/", "aa.com.tr/en/turkiye/"], "tr": ["aa.com.tr/tr/politika/"]}
ART = re.compile(r"^https?://(?:www\.)?aa\.com\.tr/(en|tr)/([a-z0-9-]+)/[^/?#]+/(\d{5,9})/?$")
DELAY = 10   # four aa.com.tr processes (en/tr x recent/backfill) run at once: ~0.4 req/s combined


def section_ok(lang: str, section: str) -> bool:
    return section in SECTIONS[lang] or (lang == "en" and section.endswith("-war"))


def canon(url: str) -> Optional[re.Match]:
    return ART.match(url.split("?")[0].split("#")[0].replace("http://", "https://"))


def parse_article(html: str) -> Optional[Dict[str, str]]:
    art = None
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        for x in d if isinstance(d, list) else [d]:
            if isinstance(x, dict) and x.get("@type") in ("NewsArticle", "Article"):
                art = x
    if not art or not re.match(r"\d{4}-\d{2}-\d{2}", str(art.get("datePublished") or "")):
        return None
    i = html.find('<div dir="ltr" class="embed-responsive prose')
    if i < 0:
        i = html.find('class="embed-responsive prose')
    if i < 0:
        return None
    j = html.find("<template", i)
    body = clean_html(html[i:j if j > 0 else None])
    body = re.sub(r"^[^>]*>", "", body).strip()          # tail of the opening tag when sliced mid-tag
    # ombudsman / correction box that some pages render inside the body container
    body = re.split(r"\n[^\n]*(?:Okur Temsilci|Bu haberde bir hata mı var|Is there an error in this story|"
                    r"contact_the_ombudsman|is_there_an_error|news_share|subscription_contact)", "\n" + body)[0].strip()
    lead = clean_html(str(art.get("articleBody") or art.get("description") or ""))
    text = (lead + "\n" + body).strip() if lead and lead not in body else body
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    title = clean_html(m.group(1)) if m else clean_html(str(art.get("headline") or ""))
    return {"date": art["datePublished"][:10], "title": title, "text": text,
            "section_name": str(art.get("articleSection") or "")} if len(body) > 80 else None


def store(lang: str, url: str, sample: str, st: State) -> bool:
    m = canon(url)
    if not m:
        return False
    key = m.group(3)
    if st.is_done(key):
        return False
    url = m.group(0)
    html = fetch(url, min_delay=DELAY)
    if not html:
        log.warning("fetch failed: %s", url)
        if fetch.last.get("status") in (404, 410):
            st.mark_done(key)
        return False
    art = parse_article(html)
    if not art:
        log.warning("no date/body, skipped: %s", url)
        st.mark_done(key)
        return False
    st.mark_done(key)
    if art["date"] < START:
        return False
    source = f"tr_aa_{lang}"
    row = {"id": make_id(source, key), "country": "TR", "source": source, "outlet": "state_media",
           "org": "Anadolu Agency", "lang": lang, "date": art["date"], "url": url, "title": art["title"],
           "speaker": None, "kind": "article", "text": art["text"], "via": "direct", "section": m.group(2),
           "sample": sample}
    write_docs("TR", source, [row])
    return True


def recent(lang: str, st: State) -> int:
    xml = fetch(f"{BASE}/{lang}/SiteMap/News", min_delay=DELAY) or ""
    urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
    keep = [u for u in urls if (m := canon(u)) and section_ok(lang, m.group(2))]
    log.info("sitemap: %d urls, %d in sampled sections", len(urls), len(keep))
    n = 0
    for u in keep:
        if store(lang, u, "recent", st):
            n += 1
            if n % 10 == 0:
                st.save()
    st.save()
    return n


def cdx_urls(prefix: str) -> Optional[List[str]]:
    """Captured article URLs under `prefix` (2021+), or None if any CDX request failed (caller retries).

    Uses the CDX resumeKey protocol (limit + showResumeKey), which works together with collapse=urlkey."""
    q = {"url": prefix, "matchType": "prefix", "from": "2021", "filter": "statuscode:200",
         "collapse": "urlkey", "fl": "original", "output": "json", "limit": "20000", "showResumeKey": "true"}
    base = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(q)
    out: List[str] = []
    key: Optional[str] = None
    while True:
        body = fetch(base + (f"&resumeKey={urllib.parse.quote(key)}" if key else ""), min_delay=5, timeout=180)
        if body is None:
            log.warning("CDX request failed for %s (resumeKey %s)", prefix, key)
            return None
        try:
            rows = json.loads(body or "[]")
        except ValueError:
            log.warning("CDX answer unreadable for %s: %r", prefix, body[:200])
            return None
        key = None
        if len(rows) >= 2 and rows[-2] == []:            # [..., [], [resumeKey]]
            key = rows[-1][0]
            rows = rows[:-2]
        out += [r[0] for r in rows[1:] if r]
        log.info("CDX %s: %d urls so far", prefix, len(out))
        if not key:
            return out


def backfill(lang: str, st: State) -> int:
    queue: Dict[str, str] = dict(st.get("backfill_queue") or {})   # article id -> url
    listed = set(st.get("backfill_listed") or [])
    for prefix in BACKFILL[lang]:
        if prefix in listed:
            continue
        urls = None
        for attempt in range(24):          # Wayback often refuses connections from here: retry for ~6 h
            urls = cdx_urls(prefix)
            if urls is not None:
                break
            log.warning("CDX listing of %s incomplete; retrying in 15 min (%d/24)", prefix, attempt + 1)
            time.sleep(900)
        if urls is None:
            log.error("CDX listing of %s failed; prefix left unlisted (re-run backfill later)", prefix)
            continue
        for u in urls:
            m = canon(u)
            if m and m.group(3) not in queue:
                queue[m.group(3)] = m.group(0)
        listed.add(prefix)
        st["backfill_queue"] = queue
        st["backfill_listed"] = sorted(listed)
        st.save()
    todo = sorted(((k, u) for k, u in queue.items() if not st.is_done(k)), key=lambda kv: -int(kv[0]))
    log.info("backfill queue: %d ids, %d to fetch (newest first)", len(queue), len(todo))
    n = 0
    for k, u in todo:
        if store(lang, u, "backfill", st):
            n += 1
            if n % 10 == 0:
                st.save()
                log.info("backfill: %d new docs (id %s)", n, k)
    st.save()
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["en", "tr"], required=True)
    ap.add_argument("mode", choices=["recent", "backfill"])
    ap.add_argument("--follow", action="store_true", help="recent: re-read the sitemap every 30 min")
    a = ap.parse_args()
    st = State(f"tr_aa_{a.lang}_{a.mode}")
    if a.mode == "backfill":
        log.info("backfill done: %d new docs", backfill(a.lang, st))
        return
    while True:
        log.info("recent: %d new docs", recent(a.lang, st))
        if not a.follow:
            break
        time.sleep(1800)


if __name__ == "__main__":
    _lang = sys.argv[sys.argv.index("--lang") + 1] if "--lang" in sys.argv else "xx"
    log = setup_logging(f"tr_aa_{_lang}")
    main()
