"""PRC Ministry of National Defense (国防部) spokesperson output, fetched live: regular / special press conferences
(full transcripts and the per-question items the site also posts), spokesperson statements and answers to
reporters (谈话和答记者问), monthly releases.

Source name: mnd_cn_live (separate from the imported mnd_cn so the import stays untouched).

  ZH  http://www.mod.gov.cn/gfbw/xwfyr/{lxjzh_246940,yzxwfb,fyrthhdjzw,ztjzh,lxjzhzt/<year>/<month>}/
  EN  http://eng.mod.gov.cn/2025xb/P/   (Press: regular press conferences, spokesperson remarks)

https on both hosts serves a certificate for another name, so the site is used over http (what its own links
use). No robots.txt (404 -> no rules). >= 6 s between requests. The CMS caps every listing at ~10 pages, so the
live archive reaches ~2018 (statements), ~2024 (conference items), 2026-06 (EN); older years live only at
pre-2023 URLs that now 404 -> --wayback enumerates them through the Wayback CDX (raw copies, `via: wayback`).

kind: briefing (conference transcript or one of its items; `unit` = conference | qa), statement (spokesperson
remarks / answers to reporters), news (monthly releases). lang zh or en, one language per document.

    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_mnd.py [--follow] [--wayback]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cn_common as cc  # noqa: E402
import lib  # noqa: E402

SOURCE = "mnd_cn_live"
DELAY = 6
log = lib.setup_logging("cn_mnd")

# (lang, crawl root, allowed URL prefix)
ROOTS: List[Tuple[str, str, str]] = [
    ("zh", "http://www.mod.gov.cn/gfbw/xwfyr/lxjzh_246940/index.html", "http://www.mod.gov.cn/gfbw/xwfyr/"),
    ("zh", "http://www.mod.gov.cn/gfbw/xwfyr/fyrthhdjzw/index.html", "http://www.mod.gov.cn/gfbw/xwfyr/"),
    ("zh", "http://www.mod.gov.cn/gfbw/xwfyr/yzxwfb/index.html", "http://www.mod.gov.cn/gfbw/xwfyr/"),
    ("zh", "http://www.mod.gov.cn/gfbw/xwfyr/ztjzh/index.html", "http://www.mod.gov.cn/gfbw/xwfyr/"),
    ("zh", "http://www.mod.gov.cn/gfbw/xwfyr/lxjzhzt/index.html", "http://www.mod.gov.cn/gfbw/xwfyr/"),
    ("en", "http://eng.mod.gov.cn/2025xb/P/index.html", "http://eng.mod.gov.cn/2025xb/P/"),
]
# Pre-2023 URL schemes (now 404 live) for the Wayback backfill: (lang, CDX prefix, article regex)
WAYBACK: List[Tuple[str, str, str]] = [
    ("zh", "mod.gov.cn/jzhzt/", r"/jzhzt/.*content_\d+\.htm$"),
    ("zh", "mod.gov.cn/xwfyr/", r"/xwfyr/.*content_\d+\.htm$"),
    ("zh", "mod.gov.cn/gfbw/xwfyr/", r"/gfbw/xwfyr/.*(?:content_)?\d+\.html?$"),
    ("en", "eng.mod.gov.cn/news/", r"/news/\d{4}-\d{2}/\d{2}/content_\d+\.htm$"),
    ("en", "eng.mod.gov.cn/xb/", r"/xb/.*\d+\.html?$"),
    ("en", "eng.chinamil.com.cn/view/", r"/view/\d{4}-\d{2}/\d{2}/content_\d+\.htm$"),
]
ART = re.compile(r"/(\d{6,9})\.html$")


def section_kind(url: str, title: str) -> Tuple[str, Optional[str]]:
    """(kind, unit) from the URL section and title."""
    if "实录" in title or re.search(r"Regular Press Conference|Press Conference of", title):
        return "briefing", "conference"
    if "/fyrthhdjzw/" in url or re.search(r"答记者问|谈话|发表声明", title):
        return "statement", None
    if "/yzxwfb/" in url:
        return "news", None
    if re.search(r"/lxjzh|/ztjzh/|/lxjzhzt/", url):
        return "briefing", "qa"
    return "statement", None


def list_links(html: str, url: str, prefix: str) -> Tuple[List[str], List[str]]:
    """(listing pages, article pages) linked from a listing page, restricted to `prefix`."""
    hrefs = set(re.findall(r'href=["\']\s*(http://[^"\'\s]+)["\']', html))
    base = url.rsplit("/", 1)[0] + "/"
    hrefs |= {base + h for h in re.findall(r'href=["\']\.?/?((?:[\w-]+/)*[\w-]+\.html)["\']', html)
              if not h.startswith("http")}
    lists, arts = [], []
    for h in hrefs:
        if not h.startswith(prefix) or "/fyrjj/" in h:
            continue
        if re.search(r"/index(?:_\d+)?\.html$", h):
            lists.append(h)
        elif ART.search(h):
            arts.append(h)
    m = re.search(r"createPageHTML\(\s*'?(\d+)'?\s*,\s*'?(\d+)'?\s*,\s*'index'\s*,\s*'html'", html)
    if m and url.endswith("/index.html"):
        lists += [url.replace("/index.html", f"/index_{i}.html") for i in range(1, int(m.group(1)))]
    return lists, arts


def parse(html: str, lang: str) -> Optional[Dict]:
    """{title, date, text, pages} of an article page (date from publishdate meta or the info line)."""
    title = ""
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    if h1:
        title = lib.clean_html(h1.group(1))
    if not title:
        title = cc.meta(html, "apple-mobile-web-app-title") or cc.title_tag(html).split(" - ")[0].strip()
    dm = re.search(r'publishdate"\s+content="(\d{4}-\d{2}-\d{2})', html)
    date = dm.group(1) if dm else None
    if not date:
        info = re.search(r'<div class="info">(.*?)</div>', html, re.S)
        date = cc.first_date(lib.clean_html(info.group(1))) if info else None
    parts = re.split(r"<!--HTMLBOX-->", html)
    if len(parts) >= 3:
        body = parts[1]
    else:
        body = cc.balanced_div(html, 'id="article-content"') or ""
    body = re.sub(r'<div id="(?:mediaurl|cm-player|cmplayer)".*?</div>', " ", body, flags=re.S)
    text = cc.paragraphs(body)
    pc = re.search(r"createManuscriptPageHTML\(\s*'(\d+)'", html)
    if not title or not date:
        return None
    return {"title": title, "date": date, "text": text, "pages": int(pc.group(1)) if pc else 1}


def speaker_of(text: str, title: str, lang: str) -> Optional[str]:
    if lang == "zh":
        m = re.search(r"(?:国防部)?(?:新闻)?发言人、?(?:国防部新闻局副局长、)?([一-龥]{2,3}?)(?:大校|上校|少将|就|在|表示|答|回答|发表)",
                      title + "\n" + text[:400])
        return m.group(1) if m else None
    m = re.search(r"(?:Senior Colonel|Colonel|Major General|spokesperson)\s+([A-Z][a-z]+ [A-Z][a-z]+(?:[a-z]+)?)",
                  title + "\n" + text[:600])
    return m.group(1) if m else None


def fetch_article(url: str, lang: str, wayback_ts: Optional[str] = None) -> Optional[Dict]:
    src = cc.wayback_raw(url, wayback_ts) if wayback_ts else url
    html = lib.fetch(src, min_delay=DELAY)
    if html is None:
        return None
    via = "wayback" if wayback_ts else (lib.fetch.last.get("via") or "direct")
    p = parse(html, lang)
    if p is None:
        p = parse_old(html, lang)
        if p is None:
            return None
    texts = [p["text"]]
    for n in range(2, min(p.get("pages", 1), 30) + 1):
        more = lib.fetch(re.sub(r"\.html$", f"_{n}.html", src), min_delay=DELAY)
        q = parse(more, lang) if more else None
        if q:
            texts.append(q["text"])
    p["text"] = "\n".join(t for t in texts if t)
    p["via"] = via
    return p


def parse_old(html: str, lang: str) -> Optional[Dict]:
    """Pre-2023 MND/chinamil pages (Wayback copies): title tag + first date + largest text block."""
    title = cc.title_tag(html).split(" - ")[0].split("_")[0].strip()
    date = cc.first_date(cc.meta(html, "publishdate") or "") or cc.first_date(
        re.sub(r"<script.*?</script>", "", html, flags=re.S)[:20000])
    cand = [cc.balanced_div(html, m) for m in ('id="article-content"', 'class="article-content', 'id="zoom"',
                                                'class="TRS_Editor', 'id="content"', 'class="content"')]
    cand = [c for c in cand if c]
    if not title or not date or not cand:
        return None
    text = max((cc.paragraphs(c) for c in cand), key=len)
    return {"title": title, "date": date, "text": text, "pages": 1}


def store(sink: cc.Sink, st: lib.State, url: str, lang: str, p: Dict, key: str) -> bool:
    text = p["text"].strip()
    if len(text) < 80:
        log.warning("short body (%d chars), skipped %s", len(text), url)
        st.mark_done(url)
        return False
    kind, unit = section_kind(url, p["title"])
    sig = f"{lang}|{p['date']}|{p['title']}|{unit}"
    if sig in st.data.setdefault("sigs", []):  # same item reposted under another section
        st.mark_done(url)
        return False
    row = {"id": lib.make_id(SOURCE, f"{key}:{lang}"), "outlet": "official", "org": "MND", "lang": lang,
           "date": p["date"], "url": url, "title": p["title"], "speaker": speaker_of(text, p["title"], lang),
           "kind": kind, "text": text, "via": p["via"], "translation": "original"}
    if unit:
        row["unit"] = unit
    if p["via"] == "wayback":
        row["wayback"] = True
    ok = sink.add(row)
    st.data["sigs"].append(sig)
    st.mark_done(url)
    return ok


def live_pass(sink: cc.Sink, st: lib.State) -> int:
    before = sink.added + len(sink.buf)
    for lang, root, prefix in ROOTS:
        seen_lists: Set[str] = set()
        queue = deque([root])
        arts: List[str] = []
        while queue:
            u = queue.popleft()
            if u in seen_lists:
                continue
            seen_lists.add(u)
            html = lib.fetch(u, min_delay=DELAY)
            if not html:
                continue
            lists, a = list_links(html, u, prefix)
            # stay inside the root's own section tree (other xwfyr sections have their own root)
            sect = root.rsplit("/", 1)[0] + "/"
            queue.extend(x for x in lists if x.startswith(sect) and x not in seen_lists)
            arts += [x for x in a if x.startswith(sect)]
        new = [x for x in dict.fromkeys(arts) if not st.is_done(x)]
        log.info("%s %s: %d listing pages, %d articles, %d new", lang, root, len(seen_lists), len(set(arts)), len(new))
        for url in new:
            p = fetch_article(url, lang)
            if p is None:
                log.warning("unparsed / failed %s (status %s)", url, lib.fetch.last.get("status"))
                if lib.fetch.last.get("status") in (404, 410, 200):
                    st.mark_done(url)
                continue
            store(sink, st, url, lang, p, ART.search(url).group(1))
        sink.flush()
    return sink.added + len(sink.buf) - before


def wayback_pass(sink: cc.Sink, st: lib.State) -> int:
    before = sink.added
    for lang, prefix, rx in WAYBACK:
        if st.get("wb_done_" + prefix):
            continue
        n = 0
        for orig, ts in cc.cdx_urls(prefix, rx):
            url = re.sub(r"^https://", "http://", orig.replace(":80/", "/"))
            if st.is_done(url):
                continue
            p = fetch_article(url, lang, wayback_ts=ts)
            if p is None:
                st.mark_done(url)
                continue
            key = "wb" + (re.search(r"(\d{5,})\.html?$", url).group(1) if re.search(r"(\d{5,})\.html?$", url)
                          else lib.make_id("x", url).split(":")[1])
            store(sink, st, url, lang, p, key)
            n += 1
            if n % 20 == 0:
                sink.flush()
        sink.flush()
        if n == 0:  # CDX unreachable or empty: try again next pass
            log.info("wayback %s: nothing fetched this pass", prefix)
            continue
        st["wb_done_" + prefix] = True
        st.save()
        log.info("wayback %s done (%d fetched)", prefix, n)
    return sink.added - before


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--follow", action="store_true", help="after the backfill, re-check listings every 6 h")
    ap.add_argument("--wayback", action="store_true", help="also backfill pre-2023 pages via Wayback CDX")
    a = ap.parse_args()
    st = lib.State("cn_mnd")
    sink = cc.Sink(SOURCE, st, flush_every=10)

    def one() -> int:
        n = live_pass(sink, st)
        if a.wayback:
            n += wayback_pass(sink, st)
        sink.flush()
        return n

    if a.follow:
        cc.follow(one, 6 * 3600, "cn_mnd")
    else:
        log.info("pass done: %d new", one())


if __name__ == "__main__":
    main()
