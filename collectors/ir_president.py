"""Office of the President of Iran (president.ir): Persian and English news items, speeches, messages,
interviews, 2021-01-01 -> today, newest first.

president.ir numbers every item in one id space shared by all its languages (/fa/<id>, /en/<id>, /ar/<id>,
…); an id in the wrong language redirects to /404.html. The collector walks ids downward from the newest id
in the site's own sitemap (https://president.ir/view/sitemap.php?page=1), tries /fa/<id> and, if that is a
404, /en/<id>. It stops after STOP_RUN consecutive found items dated before START. With --follow it then
re-reads the sitemap every hour for new ids.

Dates: FA pages print a Solar Hijri date with a Persian month name ("جمعه 10 مهر 1405 - 19:05",
converted by ir_common); EN pages print a Gregorian date ("2026/09/29"). Items with no readable date are
skipped. Body = the <article> block with HTML comments removed; items under MIN_TEXT characters (photo or
video posts) are skipped.

kind: transcript for speech categories/titles, interview, statement for messages/letters, else article.
speaker = the incumbent president (ir_common.president_on) for transcript/interview/statement, else null.
Extra fields: category (site category name), president (incumbent on that date).

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/ir_president.py [--follow] [--max N]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ir_common import jalali_str_to_iso, president_on  # noqa: E402
from lib import State, clean_html, fetch, make_id, setup_logging, write_docs  # noqa: E402

BASE = "https://president.ir"
START = "2021-01-01"
FLOOR_ID = 117000      # id 120000 is dated 1399-12-14 (2021-03-04); walk a little below the expected floor
STOP_RUN = 200
MIN_TEXT = 150
SRC = {"fa": "ir_president_fa", "en": "ir_president_en"}
log = setup_logging("ir_president")

SPEECH = re.compile(r"سخنرانی|بیانات|متن کامل|(?i:\bspeech|\baddress(?:es|ed)?\b|\bremarks\b)")
INTERVIEW = re.compile(r"مصاحبه|گفت‌وگوی (?:تلویزیونی|اختصاصی)|(?i:\binterview)")
MESSAGE = re.compile(r"پیام|نامه|(?i:\bmessage|\bletter)")


def newest_id() -> Optional[int]:
    xml = fetch(f"{BASE}/view/sitemap.php?page=1", min_delay=4)
    ids = [int(x) for x in re.findall(r"president\.ir/(?:news|fa|en)/(\d+)", xml or "")]
    return max(ids) if ids else None


def parse(html: str, lang: str) -> Optional[Dict]:
    m = re.search(r'<h1 class="header-c1">(.*?)</h1>', html, re.S)
    if not m:
        return None
    title = clean_html(m.group(1))
    cat = re.search(r'<a href="/cat/\d+">([^<]+)</a>', html[m.end():m.end() + 3000])
    head = html[m.end():m.end() + 4000]
    head = re.sub(r"<\?.*?\?>", " ", head, flags=re.S)
    if lang == "fa":
        date = jalali_str_to_iso(clean_html(re.sub(r"<!--.*?-->", " ", head, flags=re.S)))
    else:
        d = re.search(r"\b(20\d\d)/(\d\d)/(\d\d)\b", re.sub(r"<!--.*?-->", " ", head, flags=re.S))
        date = "-".join(d.groups()) if d else None
    a, b = html.find("<article>", m.end()), html.find("</article>", m.end())
    if a < 0 or b < 0 or not date:
        return None
    body = re.sub(r"<!--.*?-->", " ", html[a:b], flags=re.S)
    text = clean_html(body)
    return {"title": title, "date": date, "text": text, "category": clean_html(cat.group(1)) if cat else None}


def kind_of(title: str, cat: Optional[str]) -> str:
    cat = (cat or "").replace("ي", "ی").replace("ك", "ک")
    s = f"{cat} {title}".replace("ي", "ی").replace("ك", "ک")
    if SPEECH.search(s):
        return "transcript"
    if INTERVIEW.search(s):
        return "interview"
    if MESSAGE.search(cat):
        return "statement"
    return "article"


def get_item(nid: int) -> Optional[tuple]:
    """(lang, url, parsed) for the FA or EN page of `nid`; ('none', ..) if neither exists; None on failure."""
    for lang in ("fa", "en"):
        url = f"{BASE}/{lang}/{nid}"
        html = fetch(url, min_delay=4)
        if html is None:
            if fetch.last.get("status", 0) == 0:
                return None  # network failure: retry later
            continue
        if fetch.last.get("url", "").endswith("/404.html") or len(html) < 3000:
            continue
        return lang, url, parse(html, lang)
    return "none", None, None


def handle(nid: int, st: State) -> Optional[str]:
    """Process one id; returns its date if an item was found."""
    res = get_item(nid)
    if res is None:
        log.warning("network failure on id %d; will retry on the next run", nid)
        return None
    lang, url, art = res
    st.mark_done(str(nid))
    if lang == "none":
        return None
    if not art:
        log.info("no dated <article> block (photo/video page?): %s", url)
        return None
    if art["date"] < START or len(art["text"]) < MIN_TEXT:
        return art["date"]
    kind = kind_of(art["title"], art["category"])
    src = SRC[lang]
    write_docs("IR", src, [{
        "id": make_id(src, str(nid)), "country": "IR", "source": src, "outlet": "official",
        "org": "Presidency", "lang": lang, "date": art["date"], "url": url, "title": art["title"],
        "speaker": president_on(art["date"]) if kind in ("transcript", "interview", "statement") else None,
        "kind": kind, "text": art["text"], "via": "direct", "category": art["category"],
        "president": president_on(art["date"])}])
    return art["date"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--follow", action="store_true", help="after the backfill, poll for new ids hourly")
    ap.add_argument("--max", type=int, default=0, help="stop after N ids (testing)")
    a = ap.parse_args()
    st = State("ir_president")
    top = newest_id() or st.get("top")
    if not top:
        log.error("could not read the sitemap and no saved top id")
        return
    st["top"] = max(top, st.get("top") or 0)
    st.save()
    log.info("walking ids %d -> floor (stop after %d consecutive pre-%s items)", top, STOP_RUN, START)
    old_run, n = 0, 0
    for nid in range(top, FLOOR_ID - 1, -1):
        if st.is_done(str(nid)):
            continue
        d = handle(nid, st)
        n += 1
        if d:
            old_run = old_run + 1 if d < START else 0
        if n % 25 == 0:
            st.save()
            log.info("id %d (%s); %d ids this run", nid, d, n)
        if old_run >= STOP_RUN or (a.max and n >= a.max):
            break
    st.save()
    log.info("backfill pass finished at %d ids this run", n)
    while a.follow:
        time.sleep(3600)
        top = newest_id()
        if not top:
            continue
        for nid in range(top, st["top"], -1):
            if not st.is_done(str(nid)):
                handle(nid, st)
        st["top"] = max(top, st["top"])
        st.save()


if __name__ == "__main__":
    main()
