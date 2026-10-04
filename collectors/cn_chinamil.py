"""China Military Online (English, eng.chinamil.com.cn), 中国军网 (Chinese, www.81.cn) and the PLA Daily /
China National Defense News e-papers (解放军报 / 中国国防报), the CMC's own outlets.

Source name: cn_chinamil. outlet = state_media; org = ChinaMilitaryOnline (EN site), ChinaMilitaryOnline-zh (81.cn
site sections), PLADaily (解放军报 e-paper), ChinaNationalDefenseNews (中国国防报 e-paper).

Parts (one process runs them in order, then repeats with --follow):
  epaper   http://www.81.cn/_szb/{jfjb,zggfb}/YYYY/MM/DD/index.json - the e-paper's own data file; it carries every
           article's full text, so one request per paper per day. Published from 2026-01-01 (earlier dates 404;
           the old /jfjbmap/content/ archive is gone from the live site).
  sections every section listing linked from the two homepages (index.html .. index_N.html; the CMS only exposes
           the newest ~5 pages per section), then each new article page.
  wayback  (--wayback, separate process) older English articles from Wayback captures of eng.chinamil.com.cn content_*.htm pages
           (2016 -> 2025 URL scheme), raw id_ copies; slow, shared Wayback gap.

robots.txt: both hosts answer 404 (no rules). https on these hosts has a certificate for another name, so http is
used (what the sites link to themselves). 5 s between requests per host.

    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_chinamil.py [--follow] [--parts epaper,sections]
    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_chinamil.py --wayback     # separate process
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cn_common as cc  # noqa: E402
import lib  # noqa: E402

SOURCE = "cn_chinamil"
DELAY = 5
EPAPER_START = dt.date(2026, 1, 1)
PAPERS = {"jfjb": "PLADaily", "zggfb": "ChinaNationalDefenseNews"}
HOMES = {"en": "http://eng.chinamil.com.cn/", "zh": "http://www.81.cn/"}
ART_RE = {"en": re.compile(r"http://eng\.chinamil\.com\.cn/[\w/]+/(\d{7,9})\.html"),
          "zh": re.compile(r"http://www\.81\.cn/[\w/]+/(\d{7,9})\.html")}
SEC_RE = {"en": re.compile(r"http://eng\.chinamil\.com\.cn/[\w/]+/index\.html"),
          "zh": re.compile(r"http://www\.81\.cn/[\w/]+/index\.html")}
SKIP_SEC = re.compile(r"/(ysym|tp_|szb|m/|kt/|pk/|syjdt|jwdy)", re.I)  # search/help pages, photo walls, e-paper shells
log = lib.setup_logging("cn_chinamil")


# ------------------------------------------------------------------------------------------------ e-paper
def epaper_pass(sink: cc.Sink, st: lib.State) -> int:
    n0 = sink.added + len(sink.buf)
    today = dt.date.today()
    done: Set[str] = set(st.get("epaper_done", []))
    d = today
    while d >= EPAPER_START:
        for paper, org in PAPERS.items():
            key = f"{paper}:{d.isoformat()}"
            if key in done:
                continue
            url = f"http://www.81.cn/_szb/{paper}/{d:%Y/%m/%d}/index.json"
            body = cc.get(url, DELAY)
            if body is None:
                if lib.fetch.last.get("status") == 404 and (today - d).days > 2:
                    done.add(key)  # no issue that day (Sundays, holidays)
                continue
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                log.warning("bad JSON %s", url)
                continue
            for page in data.get("paperInfo") or []:
                web = page.get("webUrl") or f"http://www.81.cn/szb_223187/szblb/index.html?paperName={paper}"
                for it in page.get("xyList") or []:
                    text = cc.paragraphs(it.get("content") or "")
                    title = lib.clean_html(it.get("title") or "")
                    if len(text) < 100 or not title:
                        continue
                    sink.add({"id": lib.make_id(SOURCE, f"{paper}:{it.get('id')}"), "outlet": "state_media",
                              "org": org, "lang": "zh", "date": page.get("paperData") or d.isoformat(),
                              "url": f"{web}&id={it.get('id')}", "title": title, "speaker": None,
                              "kind": "article", "text": text, "via": "direct",
                              "subtitle": lib.clean_html(it.get("title2") or "") or None,
                              "author": lib.clean_html(it.get("author") or "") or None,
                              "page": f"{page.get('paperNumber')} {page.get('paperBk') or ''}".strip()})
            if (today - d).days > 2:
                done.add(key)
            st["epaper_done"] = sorted(done)
            sink.flush()
        d -= dt.timedelta(days=1)
    sink.flush()
    return sink.added + len(sink.buf) - n0


# ------------------------------------------------------------------------------------------------ sections
def page_count(html: str) -> int:
    m = re.search(r"create(?:Manuscript)?PageHTML\(\s*'(\d+)'", html)
    return min(int(m.group(1)), 50) if m else 1


def sections(lang: str) -> List[str]:
    html = cc.get(HOMES[lang], DELAY) or ""
    secs = sorted(set(SEC_RE[lang].findall(html)))
    return [s for s in secs if not SKIP_SEC.search(s)]


def article_links(lang: str, sec: str, found: Optional[Set[str]] = None) -> List[str]:
    """Article URLs on all listing pages of `sec`; sub-section links seen on page 1 are added to `found`."""
    first = cc.get(sec, DELAY)
    if not first:
        return []
    if found is not None:
        found.update(s for s in SEC_RE[lang].findall(first) if not SKIP_SEC.search(s))
    out = list(dict.fromkeys(m.group(0) for m in ART_RE[lang].finditer(first)))
    for p in range(2, page_count(first) + 1):
        html = cc.get(sec.replace("index.html", f"index_{p}.html"), DELAY)
        if not html:
            break
        out += [m.group(0) for m in ART_RE[lang].finditer(html)]
    return list(dict.fromkeys(out))


def parse_article(html: str, lang: str) -> Optional[Dict]:
    h1 = (re.search(r'class="article-header".*?<h1[^>]*>(.*?)</h1>', html, re.S)
          or re.search(r'class="title"[^>]*>\s*<h1[^>]*>(.*?)</h1>', html, re.S)
          or re.search(r'<h1>(.*?)</h1>', html, re.S))
    title = lib.clean_html(re.sub(r"<span[^>]*>.*?</span>", "", h1.group(1), flags=re.S)) if h1 else ""
    if not title:
        title = re.sub(r"\s*-\s*(China Military|中国军网)\s*$", "", cc.title_tag(html))
    date = cc.first_date(cc.meta(html, "publishdate") or "")
    if not date:
        tm = re.search(r'class="time"[^>]*>([^<]+)<', html) or re.search(r"<dt>Time</dt><dd>([^<]+)<", html)
        date = cc.first_date(tm.group(1)) if tm else None
    body = cc.balanced_div(html, 'id="main-news-list"') or cc.balanced_div(html, 'id="article-content"')
    if not body or not date:
        return None
    body = re.sub(r'<div[^>]+id="displaypagenum".*', "", body, flags=re.S)
    text = cc.paragraphs(body)
    if len(text) < 100:
        return None
    src = re.search(r"(?:来源：|<dt>Source</dt><dd>(?:<a[^>]*>)?)\s*([^<]+)<", html)
    return {"title": title, "date": date, "text": text,
            "orig_source": lib.clean_html(src.group(1)).strip() if src else None}


def sections_pass(sink: cc.Sink, st: lib.State) -> int:
    n0 = sink.added + len(sink.buf)
    bad: Set[str] = set(st.get("bad_urls", []))
    for lang in ("en", "zh"):
        queue = sections(lang)
        seen: Set[str] = set(queue)
        while queue:
            sec = queue.pop(0)
            found: Set[str] = set()
            links = article_links(lang, sec, found)
            for s in sorted(found - seen):
                seen.add(s)
                queue.append(s)
            for url in links:
                did = lib.make_id(SOURCE, url)
                if sink.has(did) or url in bad:
                    continue
                html = cc.get(url, DELAY)
                if html is None:
                    continue
                p = parse_article(html, lang)
                if not p:
                    bad.add(url)
                    continue
                sink.add({"id": did, "outlet": "state_media",
                          "org": "ChinaMilitaryOnline" if lang == "en" else "ChinaMilitaryOnline-zh",
                          "lang": lang, "date": p["date"], "url": url, "title": p["title"], "speaker": None,
                          "kind": "article", "text": p["text"], "via": "direct",
                          "orig_source": p["orig_source"]})
            st["bad_urls"] = sorted(bad)
            sink.flush()
            log.info("section done %s (%d new so far)", sec, sink.added + len(sink.buf) - n0)
    return sink.added + len(sink.buf) - n0


# ------------------------------------------------------------------------------------------------ wayback
OLD_EN = re.compile(r"eng\.chinamil\.com\.cn(?::80)?/(?:[\w-]+/)*\d{4}-\d{2}/\d{2}/content_\d+\.htm$")  # page 1 only


def parse_old(html: str) -> Optional[Dict]:
    """Pre-2025 English template (content_*.htm): body between <!--enpcontent--> markers, title in enpproperty."""
    tm = re.search(r"<!--enpproperty.*?<title>(.*?)</title>", html, re.S)
    title = lib.clean_html(tm.group(1)) if tm else re.sub(r"\s*-\s*China Military.*$", "", cc.title_tag(html))
    i = html.rfind("<!--enpcontent-->")
    j = html.find("<!--/enpcontent-->", i)
    body = html[i:j] if i >= 0 and j > i else (cc.balanced_div(html, 'class="article-content"') or "")
    body = re.sub(r"<script\b.*?</script>", " ", body, flags=re.S | re.I)
    text = cc.paragraphs(body)
    if not title or len(text) < 100:
        return None
    return {"title": title, "text": text}


def wayback_pass(sink: cc.Sink, st: lib.State) -> int:
    n0 = sink.added + len(sink.buf)
    bad: Set[str] = set(st.get("wb_bad", []))
    for orig, ts in cc.cdx_urls("eng.chinamil.com.cn/", url_re=OLD_EN.pattern):
        canon = re.sub(r"^https?://([^/:]+)(:80)?", r"http://\1", orig)
        did = lib.make_id(SOURCE, canon)
        if sink.has(did) or canon in bad:
            continue
        m = re.search(r"/(\d{4})-(\d{2})/(\d{2})/content_", canon)
        date = cc.ymd(*m.groups()) if m else None
        html = lib.fetch(cc.wayback_raw(orig, ts), min_delay=5)
        if html is None:  # Wayback unreachable / throttled: do not mark bad, wait and move on
            time.sleep(60)
            continue
        p = parse_old(html) if date else None
        if not p:
            bad.add(canon)
            st["wb_bad"] = sorted(bad)
            continue
        sink.add({"id": did, "outlet": "state_media", "org": "ChinaMilitaryOnline", "lang": "en", "date": date,
                  "url": canon, "title": p["title"], "speaker": None, "kind": "article", "text": p["text"],
                  "via": "wayback", "wayback": cc.wayback_raw(orig, ts)})
    sink.flush()
    return sink.added + len(sink.buf) - n0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--follow", action="store_true", help="repeat every 4 hours")
    ap.add_argument("--parts", default="epaper,sections")
    ap.add_argument("--wayback", action="store_true",
                    help="instead of the live parts, backfill old English articles via Wayback (own state file)")
    a = ap.parse_args()
    if a.wayback:  # separate process + state file, so it can run beside the live --follow process
        st = lib.State(f"{SOURCE}_wayback")
        sink = cc.Sink(SOURCE, st)
        # repeat every 12 h: a pass can end early when Wayback refuses connections; stored ids are skipped
        cc.follow(lambda: wayback_pass(sink, st), 12 * 3600, f"{SOURCE}_wayback")
    st = lib.State(SOURCE)
    sink = cc.Sink(SOURCE, st)
    parts = a.parts.split(",")

    def one() -> int:
        n = 0
        if "epaper" in parts:
            n += epaper_pass(sink, st)
        if "sections" in parts:
            n += sections_pass(sink, st)
        return n

    log.info("pass: %d new", one())
    if a.follow:
        time.sleep(4 * 3600)
        cc.follow(one, 4 * 3600, SOURCE)


if __name__ == "__main__":
    main()
