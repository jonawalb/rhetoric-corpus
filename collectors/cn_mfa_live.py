"""PRC MFA regular press conference transcripts, fetched live (fills the Jul->Oct 2026 gap in the imported
mfa_cn rows, which hold Taiwan-only Q&A for that period).

Source name: mfa_cn_live (separate from the imported mfa_cn so the import stays untouched).
One document per language per press conference (the imported mfa_cn rows are one per Q&A item).
kind = briefing for regular press conferences; kind = statement for the written "Remarks" / 答记者问 pages the
same listings carry when no conference is held (e.g. early August recess).

  EN  https://www.fmprc.gov.cn/eng/xw/fyrbt/lxjzh/            (index.html, index_1.html, ...)
  ZH  https://www.mfa.gov.cn/web/fyrbt_673021/jzhsl_673025/   (index.shtml, index_1.shtml, ...)

robots.txt on both hosts redirects to an HTML "system" page (no rules). 5 s between requests.

    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_mfa_live.py [--since 2026-06-01] [--follow]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402

SOURCE = "mfa_cn_live"
LISTS = {
    "en": ("https://www.fmprc.gov.cn/eng/xw/fyrbt/lxjzh/", "html"),
    "zh": ("https://www.mfa.gov.cn/web/fyrbt_673021/jzhsl_673025/", "shtml"),
}
DELAY = 5
log = lib.setup_logging("cn_mfa_live")

EN_TITLE = re.compile(r"Spokesperson (.+?)[’']s (Regular Press Conference|Remarks) on ([A-Z][a-z]+ \d{1,2}, \d{4})")
ZH_TITLE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日外交部发言人(\S+?)(主持例行记者会|答记者问)")


def list_page(lang: str, page: int) -> List[Tuple[str, str]]:
    """[(absolute url, link text)] of conference links on listing page `page` (0 = first)."""
    base, ext = LISTS[lang]
    url = base + ("index." + ext if page == 0 else f"index_{page}.{ext}")
    html = lib.fetch(url, min_delay=DELAY) or ""
    out = []
    for href, text in re.findall(r'<a[^>]+href="(\./\d{6}/t\d{8}_\d+\.s?html)"[^>]*>(.*?)</a>', html, re.S):
        out.append((urllib.parse.urljoin(base, href), lib.clean_html(text)))
    return out


def url_date(url: str) -> Optional[str]:
    m = re.search(r"/t(\d{4})(\d{2})(\d{2})_", url)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def parse(lang: str, html: str) -> Optional[Dict]:
    """{title, date, speaker, text} from a conference page, or None if the body is not found."""
    tm = re.search(r"<title>(.*?)</title>", html, re.S)
    title = lib.clean_html(tm.group(1)).split("_")[0].strip() if tm else ""
    date = speaker = None
    kind = "briefing"
    if lang == "en":
        m = EN_TITLE.search(title)
        if m:
            speaker = m.group(1).strip()
            date = datetime.strptime(m.group(3), "%B %d, %Y").date().isoformat()
            kind = "briefing" if m.group(2).startswith("Regular") else "statement"
    else:
        m = ZH_TITLE.search(title)
        if m:
            speaker = m.group(4)
            date = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            kind = "briefing" if m.group(5) == "主持例行记者会" else "statement"
    start = re.search(r'<div class="[^"]*TRS_UEDITOR[^"]*">', html)
    if not start:
        return None
    rest = html[start.end():]
    end = re.search(r'<div style="clear:both|<div class="news-foot"|<div class="links"', rest)
    body = rest[: end.start()] if end else rest
    text = lib.clean_html(body)
    if len(text) < 200:
        return None
    return {"title": title, "date": date, "speaker": speaker, "kind": kind, "text": text}


def collect(since: str) -> int:
    st = lib.State("cn_mfa_live")
    added_all = 0
    for lang in ("en", "zh"):
        items: List[Tuple[str, str]] = []
        for page in range(0, 40):
            got = list_page(lang, page)
            if not got:
                break
            items += got
            if min(url_date(u) or "9999" for u, _ in got) < since:
                break
        for url, text in items:
            d0 = url_date(url)
            if not d0 or d0 < since or st.is_done(url):
                continue
            if lang == "en" and not re.search(r"Regular Press Conference|Remarks on", text):
                continue
            html = lib.fetch(url, min_delay=DELAY)
            if html is None:
                log.warning("fetch failed %s (status %s)", url, lib.fetch.last.get("status"))
                continue
            p = parse(lang, html)
            if not p:
                log.warning("no body parsed %s", url)
                st.mark_done(url)
                continue
            num = re.search(r"/t(\d{8}_\d+)\.", url).group(1)
            row = {"id": lib.make_id(SOURCE, f"{num}:{lang}"), "country": "CN", "source": SOURCE,
                   "outlet": "official", "org": "MFA", "lang": lang, "date": p["date"] or d0, "url": url,
                   "title": p["title"] or text, "speaker": p["speaker"], "kind": p["kind"], "text": p["text"],
                   "via": lib.fetch.last.get("via") or "direct", "translation": "original",
                   "unit": "conference"}
            added, total = lib.write_docs("CN", SOURCE, [row])
            added_all += added
            st.mark_done(url)
            st.save()
            log.info("%s %s %s: %d chars (added %d, total %d)", lang, row["date"], row["speaker"], len(p["text"]),
                     added, total)
    st.save()
    return added_all


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-06-01")
    ap.add_argument("--follow", action="store_true", help="re-check the listings every 6 hours")
    a = ap.parse_args()
    n = collect(a.since)
    log.info("pass done: %d new", n)
    while a.follow:
        time.sleep(6 * 3600)
        n = collect(a.since)
        log.info("pass done: %d new", n)


if __name__ == "__main__":
    main()
