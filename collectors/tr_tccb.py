"""Türkiye Presidency (www.tccb.gov.tr): Erdoğan's speeches, interviews and articles, presidential press
statements/messages, Presidential Spokesperson statements, and Presidency news, 2021-01-01 -> today.
EN (--lang en -> source tr_tccb_en) and TR (--lang tr -> source tr_tccb_tr) are separate sources.

The site picks the language from a cookie (3c3L0f05=1 Turkish, 2 English) that it sets itself via
/Home/ChangeLanguage/<n>; without it /en/ pages redirect to the Turkish home page. We send that language
preference as a request header — no other cookies, no login.

Listings (`?&page=N`, 40 per page) carry the date (<dt class="date">01.10.2026</dt>); article pages carry
<h1> title, <h6> date and div#divContentArea body. Listing date is used; if the listing has none the
article's <h6> date is used; else the entry is skipped (logged). Sections are fetched in priority order
(speeches first, news last), newest first within each. No article caches are written.

kind / speaker: speeches -> transcript, speaker Erdoğan; interviews -> interview (Erdoğan); articles by
Erdoğan -> article (Erdoğan); press statements/messages -> statement (Erdoğan only when the title says it
is his message/statement); spokesperson -> statement (speaker from title, e.g. Kalın); news -> article,
speaker null (news items are Presidency write-ups that quote Erdoğan; they are not verbatim transcripts).

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/tr_tccb.py --lang en|tr [--max N]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import State, clean_html, fetch, make_id, setup_logging, write_docs  # noqa: E402
from tr_common import START, dmy_dots, speaker_in  # noqa: E402

BASE = "https://www.tccb.gov.tr"
LANG_COOKIE = {"tr": "3c3L0f05=1", "en": "3c3L0f05=2"}
# (listing path, section label, kind, default speaker) in priority order
SECTIONS = {
    "tr": [("/receptayyiperdogan/konusmalar/", "speeches", "transcript", "Erdoğan"),
           ("/receptayyiperdogan/mulakatlar/", "interviews", "interview", "Erdoğan"),
           ("/receptayyiperdogan/makaleler/", "articles", "article", "Erdoğan"),
           ("/faaliyetler/basinaciklamalari/", "press_statements", "statement", None),
           ("/faaliyetler/cumhurbaskanligisozculugunden/", "spokesperson", "statement", None),
           ("/haberler/", "news", "article", None)],
    "en": [("/en/receptayyiperdogan/speeches/", "speeches", "transcript", "Erdoğan"),
           ("/en/receptayyiperdogan/interviews/", "interviews", "interview", "Erdoğan"),
           ("/en/receptayyiperdogan/articles/", "articles", "article", "Erdoğan"),
           ("/en/activites/spokesperson/", "spokesperson", "statement", None),
           ("/en/news/", "news", "article", None)],
}
ROW = re.compile(r'<dt class="date">\s*([^<]+?)\s*</dt>\s*<dd>\s*<a href="([^"]+)"[^>]*>(.*?)</a>', re.S)
# EN "speeches" mixes speeches with messages and short announcements ("President X to visit Türkiye").
SPEECH = re.compile(r"(?i)konuşma|speech|address|remarks|hitab")
MESSAGE = re.compile(r"(?i)\bmessage\b|\bmesaj|tebrik|press statement|basın açıklaması|to visit|ziyaret")


def get(url: str, lang: str) -> Optional[str]:
    body = fetch(url, min_delay=6, headers={"Cookie": LANG_COOKIE[lang]})
    final = fetch.last.get("url") or url
    if body and lang == "en" and "/en/" in url and "/en/" not in final:
        log.warning("language redirect (got %s) for %s", final, url)
        return None
    return body


def parse_listing(html: str) -> List[Tuple[str, str, str]]:
    return [(d, h, clean_html(t)) for d, h, t in ROW.findall(html)]


def walk(lang: str, items: Dict[str, Dict]) -> None:
    for prio, (path, label, kind, spk) in enumerate(SECTIONS[lang]):
        page, prev_first = 1, None
        while True:
            url = f"{BASE}{path}" + (f"?&page={page}" if page > 1 else "")
            html = get(url, lang)
            if not html:
                log.warning("listing failed: %s", url)
                break
            rows = parse_listing(html)
            if not rows or rows[0][1] == prev_first:   # past the last page the site serves page 1 again
                break
            prev_first = rows[0][1]
            new, oldest = 0, "9999"
            for d, href, title in rows:
                full = BASE + href if href.startswith("/") else href
                date = dmy_dots(d)
                if date:
                    oldest = min(oldest, date)
                    if date < START:
                        continue
                if full not in items:
                    items[full] = {"date": date, "title": title, "section": label, "kind": kind,
                                   "speaker": spk, "prio": prio}
                    new += 1
            log.info("%s page %d: %d rows, %d new, oldest %s", path, page, len(rows), new, oldest)
            if oldest < START:
                break
            page += 1


def parse_article(html: str) -> Optional[Dict[str, str]]:
    m = re.search(r'<div id="news-detail">\s*<h1>(.*?)</h1>\s*(?:<h6>\s*([^<]*?)\s*</h6>)?', html, re.S)
    title = clean_html(m.group(1)) if m else ""
    date = dmy_dots(m.group(2)) if m and m.group(2) else None
    i = html.find('<div id="divContentArea">')
    if i < 0:
        return None
    j = html.find('<div id="icons_bottom_area_print_share"', i)
    text = clean_html(html[i:j if j > 0 else None])
    text = re.sub(r"\n(?:All News|Tüm Haberler)\s*$", "", text).strip()
    return {"title": title, "date": date, "text": text} if len(text) > 60 else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["en", "tr"], required=True)
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--skip-listings", action="store_true")
    a = ap.parse_args()
    source = f"tr_tccb_{a.lang}"
    st = State(source)
    items: Dict[str, Dict] = dict(st.get("items") or {})
    if not a.skip_listings:
        walk(a.lang, items)
        st["items"] = items
        st.save()
    order = sorted(items.items(), key=lambda kv: (-kv[1]["prio"], kv[1]["date"] or "0000"), reverse=True)
    log.info("%d items; %d already done", len(order), sum(st.is_done(u) for u, _ in order))
    n = 0
    for url, meta in order:
        if st.is_done(url):
            continue
        html = get(url, a.lang)
        if not html:
            log.warning("fetch failed: %s", url)
            continue
        art = parse_article(html)
        if not art:
            log.warning("no body: %s", url)
            st.mark_done(url)
            continue
        date = meta["date"] or art["date"]
        if not date:
            log.warning("no reliable date, skipped: %s", url)
            st.mark_done(url)
            continue
        if date < START:
            st.mark_done(url)
            continue
        title = art["title"] or meta["title"]
        kind, speaker = meta["kind"], meta["speaker"]
        if meta["section"] == "speeches" and MESSAGE.search(title) and not SPEECH.search(title):
            kind = "statement"
            speaker = "Erdoğan" if re.search(r"(?i)message|mesaj|tebrik", title) else None
        elif meta["section"] in ("press_statements", "spokesperson"):
            speaker = ("Erdoğan" if re.search(r"(?i)cumhurbaşkanımızın .*mesaj|erdo[gğ]an.*(message|mesaj)", title)
                       else speaker_in(title) if meta["section"] == "spokesperson" else None)
        row = {"id": make_id(source, url), "country": "TR", "source": source, "outlet": "official",
               "org": "Presidency", "lang": a.lang, "date": date, "url": url, "title": title,
               "speaker": speaker, "kind": kind, "text": art["text"], "via": "direct",
               "section": meta["section"]}
        write_docs("TR", source, [row])
        st.mark_done(url)
        n += 1
        if n % 10 == 0:
            st.save()
            log.info("%d new docs (at %s, %s)", n, date, meta["section"])
        if a.max and n >= a.max:
            break
    st.save()
    log.info("done: %d new docs", n)


if __name__ == "__main__":
    log = setup_logging(f"tr_tccb_{sys.argv[sys.argv.index('--lang') + 1]}" if "--lang" in sys.argv else "tr_tccb")
    main()
