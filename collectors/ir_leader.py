"""Office of the Supreme Leader, www.leader.ir: Persian and English content items (speeches, messages, decrees,
news of the Office), whole archive (1989 ->), newest first.

leader.ir numbers content in one id space shared by its languages (/fa/content/<id>, /en/content/<id>, ...);
an id that does not exist in a language answers 404 (/error). The collector walks ids downward from the
newest id linked on the /fa and /en home pages to 1, fetching /fa/content/<id> and /en/content/<id>; with
--follow it re-reads the home pages hourly for new ids. robots.txt (2026-10-03): only /js/, /css/, /images/
disallowed. (On 2026-10-02 the robots.txt URL answered an HTML "403 Forbidden" page with HTTP 200; lib treats
such a body as "no rules", and the file now serves real rules, so the site is collected.)

Date: the page's <time> ("9 /مهر/ 1405" Solar Hijri on FA pages, "22 /Sep/ 2026" on EN pages).
kind: transcript for speech categories (بیانات / Speech), statement for messages, decrees, letters, appointments
(پیام / حکم / نامه / Message / Decree / Letter / Appointment), else article.
speaker: "Khamenei" for transcript/statement items dated up to LAST_DAY (2026-02-28, Ali Khamenei's death per
the Office's own site); null otherwise (the successor is not attributed automatically). Extra field category.

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/ir_leader.py [--follow] [--max N]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from ir_common import BigState, fa_norm, jalali_str_to_iso, write_docs_fast  # noqa: E402

BASE = "https://www.leader.ir"
SRC = {"fa": "ir_leader_fa", "en": "ir_leader_en"}
LAST_DAY = "2026-02-28"
MIN_TEXT = 120
MON = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                   "dec"), 1)}
SPEECH = re.compile(r"بیانات|سخنرانی|(?i:\bspeech|\bspeeches|\bremarks\b)")
STATEMENT = re.compile(r"پیام|حکم|احکام|نامه|انتصاب|عفو|(?i:\bmessage|\bdecree|\bletter|\bappointment|\bedict)")
log = lib.setup_logging("ir_leader")


def newest_id() -> Optional[int]:
    ids = []
    for lang in SRC:
        html = lib.fetch(f"{BASE}/{lang}", min_delay=4) or ""
        ids += [int(x) for x in re.findall(r"/(?:fa|en)/content/(\d+)", html)]
    return max(ids) if ids else None


def parse_date(raw: str, lang: str) -> Optional[str]:
    t = fa_norm(raw).replace("/", " ")
    if lang == "fa":
        return jalali_str_to_iso(t)
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+((?:19|20)\d\d)", t)
    if m and m.group(2).lower() in MON:
        return f"{m.group(3)}-{MON[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    return None


def parse(html: str, lang: str) -> Optional[Dict]:
    i = html.find('<article id="heading"')
    j = html.find('<article id="details"')
    if i < 0 or j < 0:
        return None
    mt = re.search(r"<time>\s*<h6>(.*?)</h6>", html[i:j], re.S)
    date = parse_date(lib.clean_html(mt.group(1)), lang) if mt else None
    og = re.search(r'<meta property="og:title" content="([^"]*)"', html)
    head = re.search(r'class="btitr">(.*?)</h3>', html[i:j], re.S)
    kick = re.search(r"<h4><small>(.*?)</small></h4>", html[i:j], re.S)
    title = lib.clean_html(og.group(1)) if og else (lib.clean_html(head.group(1)) if head else "")
    k = html.find('<div class="text', j)
    if k < 0:
        return None
    depth, body = 0, ""
    for m in re.finditer(r"<(/?)div\b[^>]*>", html[k:], re.I):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            body = html[html.find(">", k) + 1:k + m.start()]
            break
    bc = re.search(r'<ol class="bread-crumb">(.*?)</ol>', html, re.S)
    crumbs = re.findall(r"<li><a[^>]*>(.*?)</a></li>", bc.group(1)) if bc else []
    category = lib.clean_html(crumbs[-1]) if len(crumbs) > 1 else None
    text = lib.clean_html(re.sub(r"\s+", " ", body).replace("</p>", "</p>\n"))
    return {"date": date, "title": title, "headline": lib.clean_html(head.group(1)) if head else None,
            "kicker": (lib.clean_html(kick.group(1)) or None) if kick else None, "text": text, "category": category}


def kind_of(category: Optional[str], title: str) -> str:
    s = fa_norm(f"{category or ''} {title}")
    if SPEECH.search(s):
        return "transcript"
    if STATEMENT.search(fa_norm(category or "")) or re.match(r"(پیام|حکم|نامه)", fa_norm(title)) or \
            re.match(r"(?i)(message|decree|letter)\b", title):
        return "statement"
    return "article"


def handle(nid: int, st: BigState, pending: list) -> bool:
    """Fetch both languages of one id. False on a transient failure (id not marked done)."""
    ok = True
    for lang in SRC:
        key = f"{lang}:{nid}"
        if st.is_done(key):
            continue
        url = f"{BASE}/{lang}/content/{nid}"
        html = lib.fetch(url, min_delay=4)
        status = lib.fetch.last.get("status", 0)
        if html is None:
            if status in (404, 410):
                st.mark_done(key)
            else:
                ok = False
            continue
        st.mark_done(key)
        if lib.fetch.last.get("url", "").rstrip("/").endswith("/error"):
            continue
        art = parse(html, lang)
        if not art or not art["date"] or not art["title"] or len(art["text"]) < MIN_TEXT:
            continue
        kind = kind_of(art["category"], art["title"])
        pending.append({
            "id": lib.make_id(SRC[lang], str(nid)), "country": "IR", "source": SRC[lang], "outlet": "official",
            "org": "Office of the Supreme Leader", "lang": lang, "date": art["date"], "url": url,
            "title": art["title"], "speaker": "Khamenei" if kind != "article" and art["date"] <= LAST_DAY else None,
            "kind": kind, "text": art["text"], "via": "direct", "category": art["category"],
            "headline": art["headline"], "kicker": art["kicker"]})
    return ok


def flush(pending: list, st: BigState) -> None:
    for lang, src in SRC.items():
        rows = [r for r in pending if r["source"] == src]
        if rows:
            write_docs_fast("IR", src, rows)
    pending.clear()
    st.save()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--max", type=int, default=0)
    a = ap.parse_args()
    st = BigState("ir_leader")
    top = newest_id() or st.get("top")
    if not top:
        log.error("no newest id")
        return
    st["top"] = max(top, st.get("top") or 0)
    pending: list = []
    n = 0
    for nid in range(st["top"], 0, -1):
        handle(nid, st, pending)
        n += 1
        if n % 10 == 0:
            flush(pending, st)
        if n % 200 == 0:
            log.info("id %d; %d ids this run", nid, n)
        if a.max and n >= a.max:
            break
    flush(pending, st)
    log.info("archive walk finished (%d ids)", n)
    while a.follow:
        time.sleep(3600)
        top = newest_id()
        if top:
            for nid in range(top, st["top"] - 50, -1):
                handle(nid, st, pending)
            st["top"] = max(top, st["top"])
            flush(pending, st)


if __name__ == "__main__":
    main()
