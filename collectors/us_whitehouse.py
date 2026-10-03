"""White House briefings, statements, releases, fact sheets and remarks (US baseline corpus).

Two sites, run as separate processes (different hosts, so they don't share a rate limit):
  --site current : www.whitehouse.gov (2025-01-20 ->), sections briefings-statements, releases,
                   fact-sheets, remarks (presidential-actions = legal texts, left out).
  --site biden   : bidenwhitehouse.archives.gov (2021-01-20 -> 2025-01-20), briefing-room/
                   press-briefings, statements-releases, speeches-remarks.
URLs come from the WordPress sitemaps; newest first (last 12 months before the backfill).
Writes docs/US/whitehouse.jsonl. Resumable: state/us_whitehouse_<site>.json.

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/us_whitehouse.py --site current
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import State, fetch, make_id, setup_logging, write_docs  # noqa: E402
from us_common import decode_body, paragraphs, title_of  # noqa: E402

SOURCE = "whitehouse"
SITES = {
    "current": dict(index="https://www.whitehouse.gov/sitemap_index.xml", sitemap_re=r"/post-sitemap\d*\.xml$",
                    url_re=r"^https://www\.whitehouse\.gov/(briefings-statements|releases|fact-sheets|remarks)/(\d{4})/(\d{2})/",
                    start="2025-01-20"),
    "biden": dict(index="https://bidenwhitehouse.archives.gov/sitemap_index.xml", sitemap_re=r"/post-sitemap\d*\.xml$",
                  url_re=r"^https://bidenwhitehouse\.archives\.gov/briefing-room/(press-briefings|statements-releases|speeches-remarks)/(\d{4})/(\d{2})/",
                  start="2021-01-20"),
}
KIND = {"press-briefings": "briefing", "speeches-remarks": "transcript", "remarks": "transcript"}
MIN_CHARS = 200


def list_urls(site: str, log) -> List[Tuple[str, str, str]]:
    """[(url, section, 'YYYY-MM')] from the sitemaps, newest month first."""
    cfg = SITES[site]
    idx = fetch(cfg["index"]) or ""
    maps = [u for u in re.findall(r"<loc>([^<]+)</loc>", idx) if re.search(cfg["sitemap_re"], u)]
    out = []
    for m in maps:
        body = fetch(m) or ""
        for u in re.findall(r"<loc>([^<]+)</loc>", body):
            mm = re.match(cfg["url_re"], u)
            if mm:
                out.append((u, mm.group(1), f"{mm.group(2)}-{mm.group(3)}"))
    log.info("%s: %d sitemaps, %d candidate URLs", site, len(maps), len(out))
    recent = (date.today() - timedelta(days=365)).strftime("%Y-%m")
    out.sort(key=lambda t: (t[2] >= recent, t[2]), reverse=True)
    return out


def parse(html: str, site: str) -> Tuple[Optional[str], str, str]:
    """(date, title, text) from a page."""
    title = title_of(html)
    m = re.search(r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})', html) or \
        re.search(r'article:published_time" content="(\d{4}-\d{2}-\d{2})', html)
    d = m.group(1) if m else None
    if site == "biden":
        i = html.find('<section class="body-content">')
        j = html.find("</section>", i)
        frag = html[i:j] if i >= 0 else ""
    else:
        i = html.find("</h1>")
        j = html.find("</main", i)
        frag = html[i:j] if i >= 0 else ""
    lines = [l for l in paragraphs(frag).split("\n")
             if not l.startswith(("Share this page", "http")) and l not in ("###", "# # #", "Tags")]
    return d, title, "\n".join(lines)


def speaker_of(title: str) -> Optional[str]:
    m = re.search(r"Press Secretary ([A-Z][\w.'-]+(?: [A-Z][\w.'-]+)+?)(?:,| and | on |$)", title)
    if m:
        return m.group(1)
    m = re.match(r"(?:Remarks|Statement) (?:by|from) President (Biden|Trump|Donald J\. Trump|Joe Biden)", title)
    if m:
        return "Biden" if "Biden" in m.group(1) else "Trump"
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", choices=list(SITES), required=True)
    ap.add_argument("--limit", type=int, default=0, help="stop after N new documents (testing)")
    a = ap.parse_args()
    log = setup_logging(f"us_whitehouse_{a.site}")
    st = State(f"us_whitehouse_{a.site}")
    urls = list_urls(a.site, log)
    added = 0
    for url, section, _ in urls:
        if st.is_done(url):
            continue
        body = decode_body(fetch(url, binary=True))
        if body is None:
            log.warning("no body (failed, blocked or robots): %s", url)
            continue
        d, title, text = parse(body, a.site)
        if not d or d < SITES[a.site]["start"] or len(text) < MIN_CHARS:
            log.warning("skipped (date=%s chars=%d): %s", d, len(text), url)
            st.mark_done(url)
            st.save()
            continue
        row = dict(id=make_id(SOURCE, url), country="US", source=SOURCE, outlet="official", org="White House",
                   lang="en", date=d, url=url, title=title, speaker=speaker_of(title),
                   kind=KIND.get(section, "statement"), text=text, via=fetch.last.get("via") or "direct",
                   section=section)
        n, total = write_docs("US", SOURCE, [row])
        added += n
        st.mark_done(url)
        st.save()
        if added % 25 == 0 and n:
            log.info("%s: +%d new (file total %d); last %s %s", a.site, added, total, d, url)
        if a.limit and added >= a.limit:
            break
    log.info("%s: done, %d new documents", a.site, added)


if __name__ == "__main__":
    main()
