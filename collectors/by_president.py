"""president.gov.by (Belarus presidential site) events -> docs/BY/by_president_<lang>.jsonl.

URLs come from https://president.gov.by/sitemap.event.xml (all languages, ~38k entries). Each event
page carries JSON-LD datePublished, an <h1> title and the body in div.wysiwyg; tags such as
"Speeches" or "Commentaries" are kept. Documents dated before --since are skipped (marked done).
Order: newest lastmod first, so the most recent year lands first.

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/by_president.py --lang en
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import RAW, State, clean_html, fetch, make_id, now_iso, setup_logging, write_docs  # noqa: E402

SITEMAP = "https://president.gov.by/sitemap.event.xml"
BLOCK_MARK = "Web Page Blocked"


def kind_for(tags: list) -> str:
    t = " ".join(tags).lower()
    if "speech" in t or "выступлен" in t or "address" in t or "послани" in t:
        return "transcript"
    if "interview" in t or "интервью" in t:
        return "interview"
    return "statement"


def parse(html: str):
    m = re.search(r'"datePublished":\s*"(\d{4}-\d\d-\d\d)', html)
    t = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    b = re.search(r'<section[^>]*class="page-details__content"[^>]*>(.*?)(?:<ul class="tag-list"|</section>)', html, re.S)
    if not (m and t and b):
        return None
    tags = [clean_html(x) for x in re.findall(r'class="tag-list__it"><a[^>]*>(.*?)</a>', html, re.S)]
    text = clean_html(b.group(1))
    return m.group(1), clean_html(t.group(1)), text, tags


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="en", choices=["en", "ru", "be"])
    ap.add_argument("--since", default="2021-01-01")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    source = f"by_president_{a.lang}"
    log = setup_logging(source)
    st = State(source)
    xml = fetch(SITEMAP, min_delay=5, timeout=300, cache=RAW / "by_president" / "sitemap.event.xml")
    if not xml:
        raise SystemExit("sitemap fetch failed")
    rows = re.findall(r"<loc>(https://president\.gov\.by/" + a.lang + r"/events/[^<]+)</loc><lastmod>([^<]+)</lastmod>", xml)
    def sort_key(r):  # slug suffix is a creation timestamp when present; else lastmod
        m = re.search(r"-(1[5-9]\d{8})$", r[0])
        return m.group(1) if m else "0" + r[1]
    rows.sort(key=sort_key, reverse=True)
    log.info("%d %s event URLs (%d done)", len(rows), a.lang, len(st.data.get("done", [])))
    n = 0
    for url, _lm in rows:
        if st.is_done(url):
            continue
        html = fetch(url, min_delay=5, timeout=60)
        if html is None and fetch.last.get("status", 0) == 0:
            log.warning("network failure on %s; will retry next run", url)
            continue
        if html and BLOCK_MARK not in html:
            res = parse(html)
            if res:
                date, title, text, tags = res
                if date >= a.since and len(text) >= 80:
                    write_docs("BY", source, [dict(
                        id=make_id(source, url), country="BY", source=source, outlet="official",
                        org="Presidential Administration", lang=a.lang, date=date, url=url, title=title,
                        speaker="Lukashenko" if kind_for(tags) in ("transcript", "interview") else None,
                        kind=kind_for(tags), text=text, via="direct", fetched=now_iso(), tags=tags)])
                    n += 1
            else:
                log.warning("unparsed %s", url)
        st.mark_done(url)
        st.save()
        if n and n % 100 == 0:
            log.info("written %d", n)
        if a.limit and n >= a.limit:
            break
    log.info("finished: %d new docs", n)


if __name__ == "__main__":
    main()
