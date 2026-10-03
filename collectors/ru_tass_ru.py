"""TASS in Russian (tass.ru). tass.ru article pages and robots.txt answer HTTP 403 (bot wall) to this
host (2026-10-02); the RSS feed https://tass.ru/rss/v2.xml answers 200.

Modes:
  rss       (default) poll the RSS feed (every 30 min with --follow) and store EVERY item with the text
            the feed publishes: headline + lead paragraph (row field text_scope = "rss_lead").
  backfill  full texts from Wayback copies (CDX index of tass.ru/<section>/<id>, one CDX query per section
            and half-year), newest first, for the sections politika (Russian domestic politics),
            mezhdunarodnaya-panorama (international) and armiya-i-opk (defence), 2021 -> now. SECTION SAMPLE,
            ALL TOPICS: every captured article in those sections dated 2021-01-01 or later is stored (no
            keyword filter; rows carry sample="section", section=<slug>). Coverage = what Wayback captured.
            Passes repeat (30 min apart) until no CDX query or capture fetch failed. Captures with CDX length < 5000
            are skipped (Wayback stored tass.ru's servicepipe JS bot-challenge page, not the article).
            tass.ru robots.txt (read from its Wayback copy, 2026-05-01) disallows only /search, /preview/,
            /n/, */api/*, /video/ and two single articles: these sections are allowed.

    uv run --project ~/Projects/rhetoric-corpus python collectors/ru_tass_ru.py rss --follow
    uv run --project ~/Projects/rhetoric-corpus python collectors/ru_tass_ru.py backfill
"""
from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from ru_common import ctext  # noqa: E402

RSS = "https://tass.ru/rss/v2.xml"
SECTIONS = ["politika", "mezhdunarodnaya-panorama", "armiya-i-opk"]
BACKFILL_YEARS = range(2021, 2027)
FLOOR = "2021-01-01"
log = lib.setup_logging("ru_tass_ru")


def row(url: str, d: str, title: str, text: str, via: str, scope: str, extra: Optional[Dict] = None) -> Dict:
    num = re.search(r"/(\d+)$", url)
    return {"id": lib.make_id("tass_ru", num.group(1) if num else url), "country": "RU", "source": "tass_ru",
            "outlet": "state_media", "org": "TASS", "lang": "ru", "date": d, "url": url, "title": title,
            "speaker": None, "kind": "article", "text": text, "via": via, "text_scope": scope, **(extra or {})}


def poll_rss() -> int:
    x = lib.fetch(RSS, min_delay=4) or ""
    rows = []
    for it in re.findall(r"<item>(.*?)</item>", x, re.S):
        g = lambda tag: (re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", it, re.S) or [None, ""])[1]  # noqa: E731
        url, title, desc, pub = g("link").strip(), _html.unescape(g("title")).strip(), _html.unescape(g("description")).strip(), g("pubDate")
        if not (url and title and pub):
            continue
        try:
            d = parsedate_to_datetime(pub).date().isoformat()
        except (TypeError, ValueError):
            continue
        rows.append(row(url, d, title, f"{title}\n{desc}".strip(), "rss", "rss_lead", {"category": g("category")}))
    added, total = lib.write_docs("RU", "tass_ru", rows) if rows else (0, 0)
    log.info("rss: %d items, %d new (total %d)", len(rows), added, total)
    return added


def extract(html: str) -> tuple:
    """(title, date, text) from an archived tass.ru page: JSON-LD articleBody if present, else <p> text."""
    title = d = text = ""
    for blob in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        for obj in data if isinstance(data, list) else [data]:
            if isinstance(obj, dict) and obj.get("articleBody"):
                title = obj.get("headline", "") or title
                d = (obj.get("datePublished") or "")[:10]
                text = obj["articleBody"]
    if not text:  # 2021-era pages: no articleBody in JSON-LD; body paragraphs sit in <div class="text-block">
        blocks = [html[m.end():].split("</div>", 1)[0] for m in re.finditer(r'<div class="text-block"[^>]*>', html)]
        if not blocks:
            art = re.search(r"<article\b.*?</article>", html, re.S)
            blocks = [art.group(0)] if art else []
        paras = [ctext(p) for b in blocks for p in re.findall(r"<p\b[^>]*>(.*?)</p>", b, re.S)]
        if not paras:  # 2025-26 Next.js pages: <p class="Paragraph_paragraph__…">, page footer starts with ©
            paras = [ctext(p) for p in re.findall(r'<p class="Paragraph_paragraph[^"]*"[^>]*>(.*?)</p>', html, re.S)]
            cut = next((i for i, p in enumerate(paras) if p.startswith("©")), len(paras))
            paras = paras[:cut]
        text = "\n".join(p for p in paras if p)
    if not title:
        m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', html) or re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        title = ctext(m.group(1)) if m else ""
    if not d:
        # also matches the escaped Next.js payload (\"datePublished\":\"2026-08-16T00:02:03+03:00\", Moscow time)
        m = re.search(r'datePublished\\*"\s*:\s*\\*"(\d{4}-\d{2}-\d{2})', html)
        d = m.group(1) if m else ""
    return _html.unescape(title).strip(), d, text.strip()


STUB_LEN = 5000  # CDX `length` (compressed bytes) below this = the servicepipe JS bot-challenge page Wayback
                 # captured instead of the article (seen on 2026 captures, ~1.8 kB uncompressed)


def pick_captures(cdx_body: str, sec: str) -> list:
    """[[timestamp, original]]: per article URL the latest capture whose size rules out a bot-challenge stub."""
    best: Dict[str, list] = {}
    for ln in cdx_body.splitlines():
        parts = ln.split()
        if len(parts) < 3 or not re.search(rf"tass\.ru/{sec}/\d+$", parts[1]):
            continue
        if not parts[2].isdigit() or int(parts[2]) < STUB_LEN:
            continue
        num = re.search(r"/(\d+)$", parts[1]).group(1)
        if num not in best or parts[0] > best[num][0]:
            best[num] = parts[:2]
    return list(best.values())


def backfill() -> int:
    """One pass over the Wayback captures; returns the number of failures (CDX queries + captures)."""
    st = lib.State("ru_tass_ru_backfill")
    failures = 0
    caps: Dict[str, tuple] = {}
    for sec in SECTIONS:
        for yr, (frm, to) in [(y, h) for y in BACKFILL_YEARS for h in (("0101", "0630"), ("0701", "1231"))]:
            key = f"cdx2:{sec}:{yr}{frm}"
            if st.get(key) is None and not failures:  # after one CDX failure, leave the rest to the next pass
                q = (f"https://web.archive.org/cdx/search/cdx?url=tass.ru/{sec}/&matchType=prefix&from={yr}{frm}"
                     f"&to={yr}{to}&fl=timestamp,original,length&filter=statuscode:200")
                body = lib.fetch(q, min_delay=5, timeout=300, retries=6)
                if body is None:
                    log.warning("cdx %s %s%s failed (status %s); retry next pass", sec, yr, frm, lib.fetch.last.get("status"))
                    failures += 1
                    continue
                st[key] = pick_captures(body, sec)
                st.save()
                log.info("cdx %s %s%s: %d captures", sec, yr, frm, len(st[key]))
            for ts, orig in st.get(key) or []:
                url = "https://" + re.sub(r"^https?://(www\.)?tass\.ru(:\d+)?", "tass.ru", orig)
                if url not in caps:
                    caps[url] = (ts, orig, sec)
    # newest first by article id; ids longer than 8 digits are not article ids (error/other pages) -> last
    def order(kv: tuple) -> tuple:
        num = re.search(r"/(\d+)$", kv[0]).group(1)
        return (len(num) > 8, -int(num))
    queue = sorted(caps.items(), key=order)
    log.info("backfill pass: %d distinct article URLs, %d already done", len(queue),
             sum(1 for u, _ in queue if st.is_done(u)))
    buf, n = [], 0
    for url, (ts, orig, sec) in queue:
        if st.is_done(url):
            continue
        html = lib.fetch(f"https://web.archive.org/web/{ts}id_/{orig}", min_delay=5, timeout=90)
        if html is None:
            log.warning("capture failed %s (status %s)", orig, lib.fetch.last.get("status"))
            failures += 1
            continue
        if "servicepipe" in html[:3000]:  # bot-challenge stub captured by Wayback: no article text
            st.mark_done(url)
            continue
        title, d, text = extract(html)
        if d and d >= FLOOR and len(text) > 150:
            buf.append(row(url, d, title, text, "wayback", "full",
                           {"wayback_ts": ts, "sample": "section", "section": sec}))
        elif not text or not d:
            log.warning("no text/date extracted (%d chars html) %s", len(html), orig)
        st.mark_done(url)
        n += 1
        if len(buf) >= 10:
            added, total = lib.write_docs("RU", "tass_ru", buf)
            log.info("backfill wrote %d (total %d), at %s %s", added, total, d, url)
            buf = []
        if n % 25 == 0:
            st.save()
    if buf:
        lib.write_docs("RU", "tass_ru", buf)
    st.save()
    return failures


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["rss", "backfill"], nargs="?", default="rss")
    ap.add_argument("--follow", action="store_true")
    a = ap.parse_args()
    if a.mode == "backfill":
        while True:
            failures = backfill()
            log.info("backfill pass finished with %d failures", failures)
            if not failures:
                return
            time.sleep(1800)
    poll_rss()
    while a.follow:
        time.sleep(1800)
        poll_rss()


if __name__ == "__main__":
    main()
