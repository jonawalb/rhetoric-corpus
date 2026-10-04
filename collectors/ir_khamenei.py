"""Office of the Supreme Leader (khamenei.ir), English and Persian, via Wayback Machine copies.

english.khamenei.ir and farsi.khamenei.ir answer HTTP 403 to this host (robots.txt: EN none (404),
FA "Allow: /"); the 403 is not evaded. Item URLs come from the Wayback CDX index:
  en: english.khamenei.ir/news/<id>/<slug>          (speeches, messages, reports of meetings, excerpts)
  fa: farsi.khamenei.ir/speech-content?id=<id>       (بیانات: full speech texts)  -> kind transcript
      farsi.khamenei.ir/message-content?id=<id>      (پیام‌ها: messages, decrees)   -> kind statement
CDX rows are collapsed per URL key, which gives each item's EARLIEST capture. An item whose earliest
capture is older than START cannot have been published on/after START, so it is skipped without a fetch;
the rest are fetched (raw `id_` copy of that capture), newest id first, and kept only if the page's own
date is >= START. The original site's robots.txt is checked (lib.robots_allowed) before each Wayback copy.

2026-10-03: START moved to 1990 (whole archive). FA pages use a different layout from EN (div.Content with
span.oliveDate = Solar Hijri date, h3 = title; see parse_fa), checked on real Wayback captures.

Dates: meta article:published_time / datePublished (Gregorian) when present, else the page's Solar Hijri
date (ir_common). Items without a readable date are skipped. speaker = "Khamenei" for FA speeches and
messages, and for EN items whose title or lead names the Leader/Khamenei, up to LAST_DAY (2026-02-28, his
death); else null.

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/ir_khamenei.py --lang en|fa [--limit N]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from ir_common import balanced_div, write_docs_fast, jalali_str_to_iso, to_ascii_digits  # noqa: E402
from lib import RAW, State, clean_html, fetch, make_id, now_iso, setup_logging  # noqa: E402

START = "1990-01-01"   # whole archive (was 2021-01-01 until 2026-10-03)
# Ali Khamenei was killed on 2026-02-28 (the site's own item "Announcement of the Martyrdom of Grand Ayatollah
# Sayyid Ali Hosseini [Khamenei]", english.khamenei.ir/news/12103). Items dated after that cannot be his new
# words, so speaker is left null for them (the office site keeps publishing excerpts and commemorations).
LAST_DAY = "2026-02-28"
CDX = ("https://web.archive.org/cdx/search/cdx?url={p}&matchType=prefix&collapse=urlkey"
       "&fl=timestamp,original&filter=statuscode:200")
PREFIXES = {"en": [("english.khamenei.ir/news/", "news")],
            "fa": [("farsi.khamenei.ir/speech-content", "speech"), ("farsi.khamenei.ir/message-content", "message")]}
MIN_TEXT = 150


def item_id(url: str, lang: str) -> Optional[str]:
    u = to_ascii_digits(urllib.parse.unquote(url)).replace(" ", "")
    m = re.search(r"/news/(\d+)(?:/|$)", u) if lang == "en" else re.search(r"[?&]id=(\d+)(?:&|$)", u)
    return m.group(1) if m else None


def load_index(lang: str, log) -> Dict[str, Tuple[str, str, str]]:
    """id -> (earliest capture ts, original URL, section) for items first captured on/after START."""
    out: Dict[str, Tuple[str, str, str]] = {}
    for prefix, section in PREFIXES[lang]:
        cache = RAW / f"ir_khamenei_{lang}" / ("cdx_" + re.sub(r"\W", "_", prefix) + ".txt")
        body = None
        for attempt in range(8):
            body = fetch(CDX.format(p=prefix), min_delay=5, timeout=600, cache=cache)
            if body:
                break
            wait = min(1800, 120 * 2 ** attempt)
            log.warning("CDX listing for %s failed; retry in %ds", prefix, wait)
            time.sleep(wait)
            lib._robots.pop("https://web.archive.org", None)
        if not body:
            raise SystemExit(f"CDX listing failed: {prefix}")
        first: Dict[str, Tuple[str, str]] = {}
        for line in body.splitlines():
            p = line.split()
            if len(p) < 2:
                continue
            nid = item_id(p[1], lang)
            if nid and (nid not in first or p[0] < first[nid][0]):
                first[nid] = (p[0], p[1])
        kept = {k: (ts, u, section) for k, (ts, u) in first.items() if ts[:8] >= START.replace("-", "")}
        log.info("%s: %d item ids in CDX, %d first captured since %s", prefix, len(first), len(kept), START)
        for k, v in kept.items():
            out[f"{section}:{k}"] = v
    return out


def _meta_date(html: str) -> Optional[str]:
    m = re.search(r'(?:article:published_time|datePublished)"?[^>]*?content="(20\d\d-\d\d-\d\d)', html) or \
        re.search(r'"datePublished"\s*:\s*"(20\d\d-\d\d-\d\d)', html)
    return m.group(1) if m else None


def parse_fa(html: str) -> Optional[Dict]:
    """farsi.khamenei.ir {speech,message}-content layout (checked 2026-10-03 on Wayback captures):
    <div class="Content"><span class="oliveDate">1389/06/09</span><h5>kicker</h5><h3>title</h3> body </div>."""
    inner = balanced_div(html, 'class="Content"')
    if not inner:
        return None
    md = re.search(r'class="oliveDate"[^>]*>(.*?)</span>', inner, re.S)
    date = jalali_str_to_iso(clean_html(md.group(1))) if md else None
    mt = re.search(r"<h3[^>]*>(.*?)</h3>", inner, re.S)
    title = clean_html(mt.group(1)) if mt else ""
    if not title:
        og = re.search(r'<meta property="og:title" content="([^"]*)"', html)
        title = clean_html(og.group(1)) if og else ""
    body = re.sub(r'<span class="oliveDate".*?</span>|<h5[^>]*>.*?</h5>|<h3[^>]*>.*?</h3>', " ", inner, count=3,
                  flags=re.S)
    text = clean_html(re.sub(r"\s+", " ", body).replace("<br />", "<br/>"))
    if not date or not title:
        return None
    return {"title": title, "date": date, "text": text}


def parse(html: str, lang: str) -> Optional[Dict]:
    if lang == "fa" and 'class="Content"' in html:
        return parse_fa(html)
    i = html.find('class="item-body"')
    if i < 0:
        return None
    cut = [html.find(s, i) for s in ('<div class="box ', '<div class="item-sharing', '<div class="item-tags',
                                         '</article>')]
    cut = [c for c in cut if c > 0]
    text = clean_html(html[html.find(">", i) + 1:min(cut) if cut else i + 200000])
    mt = re.search(r'<div class="item-title">.*?<h[12][^>]*>(.*?)</h[12]>', html, re.S) or \
        re.search(r'<meta property="og:title" content="([^"]*)"', html)
    title = clean_html(mt.group(1)) if mt else ""
    date = _meta_date(html)
    if not date:
        md = re.search(r'<li class="date">(.*?)</li>', html, re.S)
        date = jalali_str_to_iso(clean_html(md.group(1))) if md else None
    if not date or not title:
        return None
    return {"title": title, "date": date, "text": text}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["en", "fa"], required=True)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    source = f"ir_khamenei_{a.lang}"
    log = setup_logging(source)
    st = State(source)
    index = load_index(a.lang, log)
    order = sorted(index, key=lambda k: int(k.split(":")[1]), reverse=True)
    log.info("%d candidate items (%d done)", len(order), sum(st.is_done(k) for k in order))
    n, i = 0, -1
    while i + 1 < len(order):
        i += 1
        key = order[i]
        if st.is_done(key):
            continue
        ts, orig, section = index[key]
        if not lib.robots_allowed(orig) and orig.split("/")[2] not in lib._robots_retry:
            log.warning("robots.txt of the original site disallows %s; skipped", orig)
            st.mark_done(key)
            continue
        html = fetch(f"https://web.archive.org/web/{ts}id_/{orig}", min_delay=6, timeout=90)
        if html is None and fetch.last.get("status", 0) not in (404, 410):
            wait = min(1800, 120 * 2 ** st.get("backoff", 0))
            st["backoff"] = st.get("backoff", 0) + 1
            st.save()
            log.warning("Wayback unreachable; sleeping %ds before retrying %s", wait, key)
            time.sleep(wait)
            lib._robots.pop("https://web.archive.org", None)
            i -= 1
            continue
        st["backoff"] = 0
        art = parse(html, a.lang) if html else None
        st.mark_done(key)
        if not art:
            log.info("unparsed capture %s %s", ts, orig)
        elif a.lang == "fa" and len(re.findall(r"[\u0600-\u06FF]", art["text"])) < 0.5 * len(re.findall(r"[^\W\d_]", art["text"])):
            log.info("not Persian (the FA host also served English pages, ids ~101xxx): %s", orig)
        elif art["date"] >= START and len(art["text"]) >= MIN_TEXT:
            if a.lang == "fa":
                kind = "transcript" if section == "speech" else "statement"
                speaker = "Khamenei" if art["date"] <= LAST_DAY else None
                url = f"https://farsi.khamenei.ir/{section}-content?id={key.split(':')[1]}"
            else:
                head = art["title"] + " " + art["text"][:400]
                kind = ("transcript" if re.search(r"(?i)\bspeech\b|full text|address(?:ed)? to|\bremarks\b",
                                                  art["title"]) else
                        "statement" if re.search(r"(?i)\bmessage\b|\bdecree\b|\bletter\b", art["title"]) else
                        "article")
                speaker = ("Khamenei" if re.search(r"Khamenei|\bLeader\b", head) and art["date"] <= LAST_DAY
                           else None)
                url = orig.replace("http://", "https://")
            write_docs_fast("IR", source, [{
                "id": make_id(source, key.split(":")[1] if a.lang == "en" else key.replace(":", "-")), "country": "IR", "source": source,
                "outlet": "official", "org": "Office of the Supreme Leader", "lang": a.lang,
                "date": art["date"], "url": url, "title": art["title"], "speaker": speaker, "kind": kind,
                "text": art["text"], "via": "wayback", "fetched": now_iso(), "wayback": ts}])
            n += 1
            if n % 25 == 0:
                log.info("written %d (at %s, %s)", n, key, art["date"])
        if i % 20 == 0:
            st.save()
        if a.limit and n >= a.limit:
            break
    st.save()
    log.info("finished: %d new docs", n)


if __name__ == "__main__":
    main()
