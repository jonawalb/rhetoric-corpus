"""Taiwan Affairs Office of the State Council (国务院台湾事务办公室 / 中共中央台办), fetched live from www.gwytb.gov.cn:

  xwdt/xwfb/xwfbh/   新闻发布会 transcripts (实录 / 辑录), 2000-09 (first conference) -> now      kind briefing
  xwdt/xwfb/wyly/    发言人答记者问 / 要闻 items (spokesperson statements and answers)         kind statement
  xwdt/zwyw/         政务要闻 (leadership activities, meetings)                               kind news
  xwdt/newsb/        台办动态 (office news)                                                    kind news

Source name: tao_cn_live (separate from the imported tao_cn so the import stays untouched). Chinese only (the
TAO has no English site: www.gwytb.gov.cn/en/ is 404). Pages are GBK: fetched as bytes and decoded by the page
charset (gb18030 superset). No robots.txt (404 -> no rules). >= 6 s between requests. Listings are TRS
createPageHTML(n, ...) pages: index.htm, index_1.htm ... index_{n-1}.htm, all walked on every pass (cheap), so the
first pass is the full backfill and later passes (--follow, every 6 h) pick up new items.

www.taiwan.cn mirrors the same conference transcripts (not collected: duplicates of the TAO originals).

    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_tao.py [--follow] [--sections xwfbh,wyly]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cn_common as cc  # noqa: E402
import lib  # noqa: E402

SOURCE = "tao_cn_live"
DELAY = 6
BASE = "https://www.gwytb.gov.cn/xwdt/"
SECTIONS: Dict[str, Tuple[str, str]] = {  # name -> (path under BASE, kind)
    "xwfbh": ("xwfb/xwfbh/", "briefing"),
    "wyly": ("xwfb/wyly/", "statement"),
    "zwyw": ("zwyw/", "news"),
    "newsb": ("newsb/", "news"),
}
log = lib.setup_logging("cn_tao")
ITEM = re.compile(r'href="((?:https://www\.gwytb\.gov\.cn/xwdt/|\./)[^"]*?t(\d{8})_(\d+)\.htm)"[^>]*?(?:title="([^"]*)")?')


def get_text(url: str) -> Optional[str]:
    """Page as text, decoded by its declared charset (GBK pages -> gb18030)."""
    body = lib.fetch(url, min_delay=DELAY, binary=True)
    if body is None:
        return None
    head = body[:3000].decode("ascii", "ignore").lower()
    m = re.search(r'charset=["\']?([a-z0-9_-]+)', head)
    cs = (m.group(1) if m else "utf-8")
    if cs in ("gb2312", "gbk", "gb18030", "x-gbk"):
        cs = "gb18030"
    try:
        return body.decode(cs, "ignore")
    except LookupError:
        return body.decode("utf-8", "ignore")


def listing(path: str) -> List[Tuple[str, str]]:
    """[(article url, url date)] across all pages of one section, newest first."""
    first = get_text(BASE + path)
    if not first:
        return []
    m = re.search(r"createPageHTML\(\s*(\d+)\s*,", first)
    pages = int(m.group(1)) if m else 1
    out: Dict[str, str] = {}
    for i in range(pages):
        html = first if i == 0 else get_text(BASE + path + f"index_{i}.htm")
        if not html:
            continue
        for href, d8, _num, _t in ITEM.findall(html):
            url = href if href.startswith("http") else BASE + path + href[2:]
            if f"/xwdt/{path}" not in url:  # sidebar links to other sections
                continue
            out.setdefault(url, f"{d8[:4]}-{d8[4:6]}-{d8[6:]}")
    log.info("%s: %d pages, %d items", path, pages, len(out))
    return list(out.items())


def parse(html: str) -> Optional[Dict]:
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    title = lib.clean_html(h1.group(1)) if h1 else cc.title_tag(html)
    info = re.search(r'<div class="info">(.*?)</div>', html, re.S)
    date = cc.first_date(lib.clean_html(info.group(1))) if info else None
    body = cc.balanced_div(html, 'id="contentArea"') or cc.balanced_div(html, "TRS_Editor")
    if not body or not title:
        return None
    text = cc.paragraphs(body)
    return {"title": title, "date": date, "text": text}


SPK = re.compile(r"发言人([一-龥]{2,3}?)(?:就|在|表示|回答|应询|答|指出|说|主持|介绍|重申|强调|宣布|称|：)")


def speaker_of(title: str, text: str) -> Optional[str]:
    m = SPK.search(title) or SPK.search(text[:300])
    return m.group(1) if m else None


def run_pass(sink: cc.Sink, st: lib.State, sections: List[str]) -> int:
    before = sink.added + len(sink.buf)
    for name in sections:
        path, kind = SECTIONS[name]
        for url, d_url in listing(path):
            num = re.search(r"t(\d{8}_\d+)\.htm$", url).group(1)
            doc_id = lib.make_id(SOURCE, f"{num}:zh")
            if sink.has(doc_id) or st.is_done(url):
                continue
            html = get_text(url)
            if html is None:
                log.warning("fetch failed %s (status %s)", url, lib.fetch.last.get("status"))
                if lib.fetch.last.get("status") in (404, 410):
                    st.mark_done(url)
                continue
            p = parse(html)
            if not p or len(p["text"]) < 60:
                log.warning("no body parsed %s", url)
                st.mark_done(url)
                continue
            row = {"id": doc_id, "outlet": "official", "org": "TAO", "lang": "zh", "date": p["date"] or d_url,
                   "url": url, "title": p["title"], "speaker": speaker_of(p["title"], p["text"]), "kind": kind,
                   "text": p["text"], "via": lib.fetch.last.get("via") or "direct", "translation": "original",
                   "section": name}
            sink.add(row)
            st.mark_done(url)
        sink.flush()
    return sink.added + len(sink.buf) - before


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--follow", action="store_true", help="re-walk the listings every 6 hours")
    ap.add_argument("--sections", default=",".join(SECTIONS))
    a = ap.parse_args()
    secs = [s for s in a.sections.split(",") if s in SECTIONS]
    st = lib.State("cn_tao")
    sink = cc.Sink(SOURCE, st, flush_every=10)
    if a.follow:
        cc.follow(lambda: run_pass(sink, st, secs), 6 * 3600, "cn_tao")
    else:
        log.info("pass done: %d new", run_pass(sink, st, secs))


if __name__ == "__main__":
    main()
