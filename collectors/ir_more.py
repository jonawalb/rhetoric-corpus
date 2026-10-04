"""Two Iranian state outlets without a usable archive sitemap (added 2026-10-04):

  parstoday       Pars Today (IRIB World Service), https://parstoday.ir -- en + fa. Its sitemaps list only RSS
                  feeds, so the archive is walked by article id: /en/news/x-i<id> and /fa/x-i<id> answer 301 to the
                  canonical URL (any slug works), missing ids answer HTTP 500. Ids are per language (en ~246k,
                  fa ~44k in Oct 2026). Newest first from the top id in the language's home RSS, down to id 1.
                  Sources ir_parstoday_en / ir_parstoday_fa, outlet state_media.
  irannewspaper   Iran (روزنامه ایران), the government's official daily (published by IRNA), https://irannewspaper.ir
                  -- fa. Per issue: /<issue> and /<issue>/<page> list the page's article areas; each text area's
                  JSON is at /ajax/<issue>/<page>/<id> (rootitr, title, zirtitr, lead, content, Jalali publish date).
                  Issues are walked newest first; the site holds issues from 7805 (1400-10-01 = 2021-12-22) on
                  (older numbers redirect to /error). Early issues are page-image crops without text (skipped).
                  Source ir_irannewspaper_fa, outlet official.

No keyword or topic filter. robots.txt checked (lib + wildcard-aware), lib's per-host delay (>= 4 s); one process per
site (the two languages of Pars Today alternate inside one process, so the host sees one request per gap).

    uv run --project ~/Projects/rhetoric-corpus python collectors/ir_more.py parstoday [--follow] [--limit N]
    uv run --project ~/Projects/rhetoric-corpus python collectors/ir_more.py irannewspaper [--follow] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from ir_common import jalali_to_gregorian, robots_ok, tehran_date, write_docs_fast  # noqa: E402
from ir_media import _ctext, _div_text, _meta  # noqa: E402

DELAY = 4.0
MIN_TEXT = 120
FOLLOW_S = 1800
log = logging.getLogger("ir_more")


def get(url: str, retries: int = 3) -> Tuple[Optional[str], int, str]:
    """(body, status, final url); status -1 when robots.txt disallows."""
    if not robots_ok(url):
        p = urllib.parse.urlsplit(url)
        if f"{p.scheme}://{p.netloc}" in lib._robots_retry:  # robots.txt unreachable: transient, not a "no"
            log.warning("%s unreachable; backing off %ds (not counted as missing) %s", p.netloc,
                        lib.TRANSIENT_BACKOFF_S, url)
            time.sleep(lib.TRANSIENT_BACKOFF_S)
            return None, 0, url
        log.warning("robots.txt disallows %s", url)
        return None, -1, url
    body = lib.fetch(url, min_delay=DELAY, timeout=90, retries=retries)
    return body, lib.fetch.last.get("status", 0), lib.fetch.last.get("url") or url


# ============================================================================================== Pars Today
PT = "https://parstoday.ir"
PT_LANGS = {"en": "/en/news/x-i{id}", "fa": "/fa/x-i{id}"}
PT_ORG = "Pars Today (IRIB World Service)"


def parse_parstoday(html: str) -> Optional[Dict]:
    m = re.search(r'<h1[^>]*class="item-title"[^>]*>(.*?)</h1>', html, re.S)
    title = _ctext(m.group(1)) if m else (_meta(html, "og:title") or "")
    pub = _meta(html, "article:published_time")
    date = tehran_date(pub) if pub else None
    body = _div_text(html, r'<div[^>]*class="item-text"[^>]*>')
    return {"title": title, "date": date, "text": body, "section": _meta(html, "og:article:section")}


def pt_top_id(lang: str) -> int:
    xml, _, _ = get(f"{PT}/{lang}/rss--mu__home")
    ids = [int(x) for x in re.findall(r"-i(\d+)-", xml or "")]
    return max(ids) if ids else 0


class ParsToday:
    def __init__(self, limit: int):
        self.st = lib.State("ir_more_parstoday")
        self.limit, self.written = limit, 0
        self.pending: Dict[str, List[Dict]] = {}

    def flush(self) -> None:
        for src, rows in self.pending.items():
            if rows:
                added, total = write_docs_fast("IR", src, rows)
                self.written += added
                log.info("%s: +%d (total %d)", src, added, total)
        self.pending.clear()
        self.st.save()

    def one(self, lang: str, i: int) -> str:
        """'ok' | 'skip' (exists, no usable text) | 'miss' (no such id) | 'fail' (network)."""
        src = f"ir_parstoday_{lang}"
        html, status, final = get(PT + PT_LANGS[lang].format(id=i), retries=1)
        if html is None:
            return "miss" if status in (404, 410, 500, -1) else "fail"
        p = parse_parstoday(html)
        if not p["date"] or not p["title"] or len(p["text"]) < MIN_TEXT:
            return "skip"
        self.pending.setdefault(src, []).append(
            {"id": lib.make_id(src, str(i)), "country": "IR", "source": src, "outlet": "state_media", "org": PT_ORG,
             "lang": lang, "date": p["date"], "url": final, "title": p["title"], "speaker": None, "kind": "article",
             "text": p["text"], "via": lib.fetch.last.get("via") or "direct", "section": p["section"]})
        if sum(len(v) for v in self.pending.values()) >= 20:
            self.flush()
        return "ok"

    def run(self, follow: bool) -> None:
        cur: Dict[str, int] = dict(self.st.get("cursor") or {})      # next id to fetch walking down
        top: Dict[str, int] = dict(self.st.get("top") or {})         # highest id already queued
        new: Dict[str, List[int]] = {}
        last_poll = 0.0
        anchor: Dict[str, int] = {}
        streak: Dict[str, List[int]] = {lang: [] for lang in PT_LANGS}
        while True:
            if time.time() - last_poll >= FOLLOW_S or not top:
                for lang in PT_LANGS:
                    t = pt_top_id(lang)
                    if not t:
                        continue
                    if lang not in top:
                        top[lang], cur[lang] = t, t
                    elif t > top[lang]:
                        new.setdefault(lang, []).extend(range(t, top[lang], -1))
                        top[lang] = t
                last_poll = time.time()
                self.st["top"], self.st["cursor"] = top, cur
            busy = False
            for lang in PT_LANGS:
                if new.get(lang):                       # new items first
                    self.one(lang, new[lang].pop(0))
                    busy = True
                    continue
                i = cur.get(lang, 0)
                if i < 1:
                    continue
                busy = True
                r = self.one(lang, i)
                if r == "fail":
                    time.sleep(60)
                    continue
                if r == "miss":
                    streak[lang].append(i)
                    if len(streak[lang]) >= 100:
                        # a long run of misses: make sure the site still answers before skipping past it
                        ok, st_, _ = get(PT + PT_LANGS[lang].format(id=anchor.get(lang) or top[lang]), retries=1)
                        if ok is None:
                            log.warning("parstoday %s: anchor id fails too (%s); rewinding to %d, pausing 15 min",
                                        lang, st_, streak[lang][0])
                            cur[lang] = streak[lang][0]
                            streak[lang] = []
                            self.flush()
                            time.sleep(900)
                            continue
                        streak[lang] = []
                else:
                    anchor[lang], streak[lang] = i, []
                cur[lang] = i - 1
                self.st["cursor"] = cur
                if i % 50 == 0:
                    self.flush()
                    log.info("parstoday %s at id %d; %d docs this run", lang, i, self.written)
            if self.limit and self.written + sum(len(v) for v in self.pending.values()) >= self.limit:
                break
            if not busy:
                self.flush()
                if not follow:
                    break
                time.sleep(FOLLOW_S)
        self.flush()


# ============================================================================================ Iran newspaper
IN = "https://irannewspaper.ir"
IN_SRC = "ir_irannewspaper_fa"
IN_ORG = "Iran (government daily, IRNA)"
IN_FLOOR = 7805
AREA_RE = re.compile(r'<article class="area-item" data-id="(\d+)"[^>]*data-type="([a-z]+)"[^>]*'
                     r'data-alt-href="/(\d+)/(\d+)/\d+"')


def issue_pages(html: str, issue: int) -> List[int]:
    return sorted({int(p) for p in re.findall(r'href="/%d/(\d+)"' % issue, html)})


def page_areas(html: str) -> List[int]:
    """Ids of the text ('content') areas on an issue page, in page order."""
    out = []
    for aid, typ, _, _ in AREA_RE.findall(html):
        if typ == "content" and int(aid) not in out:
            out.append(int(aid))
    return out


def parse_area(js: str) -> Optional[Dict]:
    try:
        d = json.loads(js)["dataset_v2"]
    except (ValueError, KeyError, TypeError):
        return None
    if d.get("data_type") != "content":
        return None
    pd = (d.get("newspaper") or {}).get("page_data") or {}
    try:
        date = jalali_to_gregorian(int(pd["publish_year"]), int(pd["publish_month"]), int(pd["publish_day"])).isoformat()
    except (KeyError, ValueError, TypeError):
        date = None
    section = None
    try:
        section = json.loads(pd["page"]["dataset"]["display"]).get("name")
    except (KeyError, ValueError, TypeError, AttributeError):
        pass
    lead = "\n".join(x for x in (_ctext(d.get("zirtitr")), _ctext(d.get("lead"))) if x)
    body = lib.clean_html(re.sub(r"\s+", " ", d.get("content") or "").replace("</p>", "</p>\n"))
    return {"title": _ctext(d.get("title")), "date": date, "text": "\n".join(x for x in (lead, body) if x),
            "kicker": _ctext(d.get("rootitr")) or None, "section": section, "issue": pd.get("number_int")}


class IranNewspaper:
    def __init__(self, limit: int):
        self.st = lib.State("ir_more_irannewspaper")
        self.limit, self.written = limit, 0
        self.have = lib.existing_ids(lib.docs_path("IR", IN_SRC))
        self.rows: List[Dict] = []

    def flush(self) -> None:
        if self.rows:
            added, total = write_docs_fast("IR", IN_SRC, self.rows)
            self.written += added
            log.info("%s: +%d (total %d)", IN_SRC, added, total)
            self.rows = []
        self.st.save()

    def newest_issue(self) -> int:
        html, _, _ = get(IN + "/")
        nums = [int(n) for n in re.findall(r'href="/(\d{4,5})"', html or "")]
        return max(nums) if nums else 0

    def issue(self, n: int) -> str:
        """'ok' | 'missing' | 'fail'. Fetches every page of issue n and every text area on it."""
        html, status, _ = get(f"{IN}/{n}")
        if html is None:
            return "missing" if status in (404, 410, -1) else "fail"
        pages = issue_pages(html, n) or [1]
        ok = True
        for p in pages:
            ph = html if p == 1 else get(f"{IN}/{n}/{p}")[0]
            if ph is None:
                ok = False
                continue
            for aid in page_areas(ph):
                doc_id = lib.make_id(IN_SRC, str(aid))
                if doc_id in self.have:
                    continue
                js, st_, _ = get(f"{IN}/ajax/{n}/{p}/{aid}")
                if js is None:
                    ok = ok and st_ in (404, 410, -1)
                    continue
                a = parse_area(js)
                self.have.add(doc_id)
                if not a or not a["date"] or not a["title"] or len(a["text"]) < MIN_TEXT:
                    continue
                self.rows.append({"id": doc_id, "country": "IR", "source": IN_SRC, "outlet": "official", "org": IN_ORG,
                                  "lang": "fa", "date": a["date"], "url": f"{IN}/{n}/{p}/{aid}", "title": a["title"],
                                  "speaker": None, "kind": "article", "text": a["text"], "via": "direct",
                                  "section": a["section"], "kicker": a["kicker"], "issue": n, "page": p})
                if len(self.rows) >= 20:
                    self.flush()
        return "ok" if ok else "fail"

    def run(self, follow: bool) -> None:
        done = set(self.st.get("issues_done") or [])
        while True:
            top = self.newest_issue()
            if not top:
                log.warning("irannewspaper: no issue list on the home page")
            missing = 0
            for n in range(top, IN_FLOOR - 1, -1):
                if n in done and n < top - 2:   # the newest issues are re-read (pages are added during the day)
                    continue
                r = self.issue(n)
                if r != "fail" and n < top - 2:
                    done.add(n)
                missing = missing + 1 if r == "missing" else 0
                self.st["issues_done"] = sorted(done)
                self.flush()
                log.info("irannewspaper issue %d: %s; %d docs this run", n, r, self.written)
                if self.limit and self.written >= self.limit:
                    return
                if missing >= 150:
                    break
            if not follow:
                return
            time.sleep(3 * 3600)


def main() -> None:
    global log
    ap = argparse.ArgumentParser()
    ap.add_argument("site", choices=["parstoday", "irannewspaper"])
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="stop after about N new docs (testing)")
    a = ap.parse_args()
    log = lib.setup_logging(f"ir_more_{a.site}")
    (ParsToday if a.site == "parstoday" else IranNewspaper)(a.limit).run(a.follow)


if __name__ == "__main__":
    main()
