"""U.S. Mission to the United Nations (usun.usmission.gov): remarks, statements, explanations of vote (US baseline).

The site is WordPress with its public REST API open (robots.txt: no rules for *): /wp-json/wp/v2/posts returns 10
posts per page (newest first) with date, link, title and the full rendered body, so one request yields ten
documents (~152 pages, 1,516 posts on 2026-10-05). The site holds posts from 2023-06 on (plus a handful older);
Biden-era 2021 to mid-2023 remarks were taken down when the site was migrated (gap; Wayback has captures, not
collected yet).

The body starts with the title, then speaker name, role, place and date lines ("Linda Thomas-Greenfield /
U.S. Representative to the United Nations / New York, New York / January 4, 2024"); the speaker is the line
after the title (or the one after that, when the body repeats a longer title) when a date, place or "AS
DELIVERED/PREPARED" line follows within the next four lines. kind speech for "Remarks ..." titles,
statement otherwise. One pass walks pages newest first and stops at the first page with nothing new once the full
backfill has completed once (state "complete"). Writes docs/US/us_usun.jsonl; state/us_usun.json.

Run: uv run python collectors/us_usun.py [--limit N]
"""
from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from lib import State, clean_html, fetch, make_id, now_iso, setup_logging, write_docs  # noqa: E402

SOURCE = "us_usun"
START = "2021-01-01"
API = "https://usun.usmission.gov/wp-json/wp/v2/posts?page={p}&_fields=id,date,link,title,content"
DATE_LINE = re.compile(r"^(January|February|March|April|May|June|July|August|September|October|November|December)"
                       r"\s+\d{1,2},\s+\d{4}$")
ANCHOR = re.compile(r"^(?:New York|Washington|Geneva|AS (?:PREPARED|DELIVERED))", re.I)

log = setup_logging(SOURCE)


def parse_post(post: Dict) -> Optional[Dict]:
    title = re.sub(r"\s+", " ", _html.unescape(clean_html(post["title"]["rendered"]))).strip()
    lines = [re.sub(r"\s+", " ", l).strip() for l in clean_html(post["content"]["rendered"]).split("\n")]
    lines = [l for l in lines if l]
    if lines and lines[0].rstrip(" .") == title.rstrip(" ."):
        lines = lines[1:]
    speaker = None
    for i in range(min(2, len(lines))):  # line 0 can be a longer variant of the title
        if len(lines[i]) <= 60 and any(DATE_LINE.match(l) or ANCHOR.match(l) for l in lines[i + 1:i + 5]):
            speaker = lines[i]
            break
    text = "\n".join(lines)
    if not title or not text:
        return None
    return {"title": title, "date": post["date"][:10], "text": text, "speaker": speaker,
            "kind": "speech" if title.lower().startswith("remarks") else "statement"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="stop after N new documents (testing)")
    a = ap.parse_args()
    st = State(SOURCE)
    have = lib.existing_ids(lib.docs_path("US", SOURCE))
    n, page = 0, 1
    while True:
        body = fetch(API.format(p=page), min_delay=5, timeout=90)
        if body is None:
            status = lib.fetch.last.get("status")
            if status == 400:  # past the last page
                st["complete"] = True
            else:
                log.warning("page %d failed (status %s); stopping, re-run to resume", page, status)
            break
        try:
            posts = json.loads(body)
        except ValueError:
            log.warning("page %d: not JSON; stopping", page)
            break
        if not posts:
            st["complete"] = True
            break
        rows = []
        for post in posts:
            doc_id = make_id(SOURCE, str(post["id"]))
            if doc_id in have:
                continue
            p = parse_post(post)
            if not p or p["date"] < START or len(p["text"]) < 80:
                continue
            rows.append(dict(id=doc_id, country="US", source=SOURCE, outlet="official", org="USUN", lang="en",
                             date=p["date"], url=post["link"], title=p["title"], speaker=p["speaker"],
                             kind=p["kind"], text=p["text"], via="direct", fetched=now_iso()))
        added, _ = write_docs("US", SOURCE, rows)
        have |= {r["id"] for r in rows}
        n += added
        log.info("page %d: +%d", page, added)
        if (not rows and st.get("complete")) or (a.limit and n >= a.limit):
            break
        page += 1
    st.save()
    log.info("finished: %d new docs", n)


if __name__ == "__main__":
    main()
