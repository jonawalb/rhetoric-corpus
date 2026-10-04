"""Global Times (English), www.globaltimes.cn: every article, newest first, then a descending id backfill.

Source name: cn_globaltimes (org GlobalTimes, outlet state_media, lang en). One document per article.

URLs: article ids are one integer sequence.
  * id >= ~1,212,000 (Feb 2021 on):  https://www.globaltimes.cn/page/YYYYMM/<id>.shtml  (month must be right,
    else 404); the month is tracked as a cursor while walking ids downward (try cursor month, then the month
    before, then the month after).
  * older ids:                        https://www.globaltimes.cn/content/<id>.shtml
New items: sitemap.xml (latest ~12) + the section index pages (~250 links each). Backfill: ids downward from the
lowest id seen, until FLOOR. Gaps in the id space (runs of 404) are skipped by probing ahead in strides; items
inside a sparse stretch may be missed (logged).

robots.txt: no Disallow rules (User-agent: * / Sitemap). lib default delay (4 s), one request at a time.

    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_globaltimes.py [--follow] [--test N]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
import cn_common as cc  # noqa: E402

SOURCE = "cn_globaltimes"
BASE = "https://www.globaltimes.cn"
DELAY = 4
PAGE_FROM = 1209000      # below: /content/ only; between PAGE_FROM and CONTENT_TO: try both
CONTENT_TO = 1216000
FLOOR = 400000           # GT English launched April 2009; ids below this are not articles
MISS_RUN = 40            # consecutive 404s before probing ahead
STRIDE = 400
NEW_EVERY_S = 2 * 3600
SECTIONS = ["china/politics", "china/diplomacy", "china/military", "china/society", "china/scitech",
            "opinion/editorial", "opinion/observer", "opinion/viewpoint", "opinion/asian-review",
            "opinion/toptalk", "opinion/columnists", "opinion/cartoon", "source/gt-voice", "source/insight",
            "In-depth/hk-macao", "In-depth/cross-taiwan", "world/asia-pacific", "world/americas", "world/europe",
            "world/mid-east", "world/africa", "world/specialreports", "china/taiwan", "china/hk-macao",
            "china/diplomacy", "china/opinion", "business/economy", "business/companies", "business/markets",
            "life/culture", "life/entertainment", "sport", "china", "world", "opinion", "In-depth", "source",
            "business", "life"]
log = lib.setup_logging("cn_globaltimes")
MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                   "dec"], 1)}


def _month_add(ym: str, k: int) -> str:
    y, m = int(ym[:4]), int(ym[4:])
    m += k
    while m < 1:
        y, m = y - 1, m + 12
    while m > 12:
        y, m = y + 1, m - 12
    return f"{y:04d}{m:02d}"


def parse(html: str, url: str) -> Optional[Dict]:
    """{title, date, speaker, kind, text} or None when no body is found / text too short."""
    title = date = author = None
    kind = "article"
    if 'class="article_content"' in html or 'class="article_title"' in html:          # 2020+ template
        m = re.search(r'<div class="article_title">(.*?)</div>', html, re.S)
        title = lib.clean_html(m.group(1)) if m else None
        m = re.search(r'class="pub_time">\s*Published:\s*([A-Za-z]{3})\w* (\d{1,2}), (\d{4})', html)
        if m:
            date = cc.ymd(int(m.group(3)), MON.get(m.group(1).lower()[:3], 0), int(m.group(2)))
        m = re.search(r'class="author_name">(.*?)</a>', html, re.S)
        author = lib.clean_html(m.group(1)) if m else None
        col = re.search(r'<div class="article_column">(.*?)</div>', html, re.S)
        col_t = lib.clean_html(col.group(1)).upper() if col else ""
        body = cc.balanced_div(html, 'class="article_right"') or ""
    else:                                                                                # 2009-2020 template
        m = re.search(r'<div class="row-fluid article-title">\s*<h3>(.*?)</h3>', html, re.S)
        title = lib.clean_html(m.group(1)) if m else None
        src = re.search(r'<div class="span\d+ text-left">(.*?)</div>', html, re.S)
        src_t = lib.clean_html(src.group(1)) if src else ""
        date = cc.first_date(src_t)
        m = re.search(r"By\s+(.+?)\s+Source:", src_t)
        author = m.group(1).strip() if m else None
        m = re.search(r"Posted in:\s*(.*?)<", html, re.S)
        col_t = lib.clean_html(m.group(1)).upper() if m else ""
        body = cc.balanced_div(html, 'class="span12 row-content"') or ""
    if not title:
        title = cc.title_tag(html).replace(" - Global Times", "").strip()
    body = re.sub(r"<script\b.*?</script>", " ", body, flags=re.S | re.I)
    text = lib.clean_html(body)
    if len(text) < 100:
        return None
    if "EDITORIAL" in col_t:
        kind = "editorial"
    elif "OPINION" in col_t or "VIEWPOINT" in col_t or "OBSERVER" in col_t or "COLUMNIST" in col_t:
        kind = "opinion"
    return {"title": title, "date": date, "speaker": author, "kind": kind, "text": text,
            "section": col_t.title() or None}


class GT:
    def __init__(self) -> None:
        self.st = lib.State("cn_globaltimes")
        self.sink = cc.Sink(SOURCE, self.st, flush_every=10)

    def _store(self, gid: int, url: str, html: str, ym: Optional[str]) -> bool:
        p = parse(html, url)
        if not p:
            return False
        date = p["date"]
        if not date:
            log.warning("no date %s", url)
            return False
        row = {"id": f"{SOURCE}:{gid}", "country": "CN", "source": SOURCE, "outlet": "state_media",
               "org": "GlobalTimes", "lang": "en", "date": date, "url": url, "title": p["title"],
               "speaker": p["speaker"], "kind": p["kind"], "text": p["text"],
               "via": lib.fetch.last.get("via") or "direct", "section": p["section"]}
        return self.sink.add(row)

    # ---- one id, trying the URL forms; returns (found, month-of-page or None)
    def try_id(self, gid: int, cursor: str) -> Tuple[bool, Optional[str]]:
        cands: List[Tuple[str, Optional[str]]] = []
        if gid >= PAGE_FROM:
            for ym in (cursor, _month_add(cursor, -1), _month_add(cursor, 1)):
                cands.append((f"{BASE}/page/{ym}/{gid}.shtml", ym))
        if gid < CONTENT_TO:
            cands.append((f"{BASE}/content/{gid}.shtml", None))
        for url, ym in cands:
            html = cc.get(url, DELAY, retries=2)
            if html and "404 Not Found" not in html[:500]:
                self._store(gid, url, html, ym)
                return True, ym
        return False, None

    # ---- new items: sitemap + section pages
    def new_pass(self) -> int:
        before = self.sink.added
        urls: Dict[int, str] = {}
        sm = cc.get(f"{BASE}/sitemap.xml", DELAY) or ""
        for loc, _ in cc.sitemap_locs(sm):
            m = re.search(r"/(?:page/\d{6}|content)/(\d+)\.shtml", loc)
            if m:
                urls[int(m.group(1))] = loc
        for sec in dict.fromkeys(SECTIONS):
            html = cc.get(f"{BASE}/{sec}/index.html", DELAY) or ""
            for loc, gid in re.findall(r'href="((?:https://www\.globaltimes\.cn)?/page/\d{6}/(\d+)\.shtml)"', html):
                urls[int(gid)] = loc if loc.startswith("http") else BASE + loc
        for gid in sorted(urls, reverse=True):
            if self.sink.has(f"{SOURCE}:{gid}"):
                continue
            html = cc.get(urls[gid], DELAY)
            if html:
                self._store(gid, urls[gid], html, None)
            self.st["max_id"] = max(self.st.get("max_id") or 0, gid)
        if self.st.get("cursor_id") is None and urls:
            self.st["cursor_id"] = min(urls) - 1
            m = re.search(r"/page/(\d{6})/", urls[min(urls)])
            self.st["cursor_month"] = m.group(1) if m else "202610"
        self.sink.flush()
        return self.sink.added - before

    # ---- backfill: ids downward from cursor_id
    def backfill(self, seconds: int) -> int:
        before = self.sink.added
        end = time.time() + seconds
        gid = int(self.st.get("cursor_id") or 0)
        cursor = self.st.get("cursor_month") or "202610"
        misses = 0
        while gid > FLOOR and time.time() < end:
            if not self.sink.has(f"{SOURCE}:{gid}"):
                ok, ym = self.try_id(gid, cursor)
                if ok:
                    misses = 0
                    if ym:
                        cursor = ym
                else:
                    misses += 1
                if misses >= MISS_RUN:
                    gid = self.skip_gap(gid, cursor)
                    misses = 0
            gid -= 1
            self.st["cursor_id"] = gid
            self.st["cursor_month"] = cursor
            if gid % 50 == 0:
                self.sink.flush()
        self.sink.flush()
        return self.sink.added - before

    def skip_gap(self, gid: int, cursor: str) -> int:
        """After a run of misses at gid: probe gid-STRIDE, gid-2*STRIDE, ... until a hit, then bisect between
        that hit and the last missed probe for the top of the populated stretch; returns the id to resume at."""
        probe_fn = (lambda i: self.try_id(i, cursor)[0]) if gid < PAGE_FROM else \
            (lambda i: self._probe_page(i, cursor)[0])
        hi, probe = gid, gid - STRIDE
        while probe > FLOOR:
            if probe_fn(probe):
                lo = probe
                while hi - lo > 8:  # lo has an item, hi did not
                    mid = (lo + hi) // 2
                    if probe_fn(mid):
                        lo = mid
                    else:
                        hi = mid
                log.info("id gap: skipped %d..%d, resuming at %d", hi, gid, hi)
                return hi
            hi, probe = probe, probe - STRIDE
        return FLOOR

    def _probe_page(self, gid: int, cursor: str) -> Tuple[bool, Optional[str]]:
        for k in range(0, -4, -1):  # gaps may span months: look a few months back
            ym = _month_add(cursor, k)
            html = cc.get(f"{BASE}/page/{ym}/{gid}.shtml", DELAY, retries=2)
            if html and "404 Not Found" not in html[:500]:
                self._store(gid, f"{BASE}/page/{ym}/{gid}.shtml", html, ym)
                return True, ym
        return False, None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--follow", action="store_true", help="loop forever: new items every 2 h, backfill between")
    ap.add_argument("--test", type=int, default=0, help="parse N sample ids and print, no writing")
    a = ap.parse_args()
    if a.test:
        for url in ["https://www.globaltimes.cn/content/700000.shtml", "https://www.globaltimes.cn/content/1200050.shtml",
                    "https://www.globaltimes.cn/page/202103/1220000.shtml",
                    "https://www.globaltimes.cn/page/202609/1371581.shtml"][: a.test]:
            p = parse(cc.get(url, DELAY) or "", url)
            print(url, {k: (v[:150] if isinstance(v, str) else v) for k, v in (p or {}).items()})
        return
    gt = GT()
    n = gt.new_pass()
    log.info("new pass: %d", n)
    while True:
        n = gt.backfill(NEW_EVERY_S)
        log.info("backfill chunk: %d new, cursor id %s month %s", n, gt.st["cursor_id"], gt.st["cursor_month"])
        if not a.follow:
            break
        if gt.st["cursor_id"] is not None and gt.st["cursor_id"] <= FLOOR:
            time.sleep(NEW_EVERY_S)
        n = gt.new_pass()
        log.info("new pass: %d", n)


if __name__ == "__main__":
    main()
