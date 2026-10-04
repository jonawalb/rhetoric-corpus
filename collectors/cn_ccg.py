"""China Coast Guard (中国海警局), www.ccg.gov.cn - WAYBACK ONLY.

Source name: cn_ccg. outlet = official; org = CCG.

The live site sits behind a Huawei CloudWAF that answers our User-Agent with HTTP 418 "访问被拦截" (even for
/robots.txt), so it is not fetched live and the block is not worked around. Instead this enumerates the Wayback
Machine's captures of ccg.gov.cn (CDX, collapse=urlkey) and parses the raw `id_` copy of each article page
(.../<section>/<n>.html; /mhenu/ = English edition). Coverage = whatever Wayback captured (~2020 -> now).

Article template: <div class="section-cnt-tit"><h1>title</h1><p class="time">YYYY-MM-DD hh:mm</p>,
body <div class="article-main">.

    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_ccg.py [--follow]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Set

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cn_common as cc  # noqa: E402
import lib  # noqa: E402

SOURCE = "cn_ccg"
ART = re.compile(r"ccg\.gov\.cn(?::80)?/(?!uploadfile|statics)[\w/-]*?/\d+\.html$")
log = lib.setup_logging("cn_ccg")


def parse(html: str) -> Optional[Dict]:
    head = cc.balanced_div(html, 'class="section-cnt-tit') or ""
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", head, re.S)
    tm = re.search(r'class="time"[^>]*>([^<]+)<', head)
    body = cc.balanced_div(html, 'class="article-main"')
    if not h1 or not body:
        return None
    text = cc.paragraphs(body)
    if len(text) < 100:
        return None
    return {"title": lib.clean_html(h1.group(1)), "date": cc.first_date(tm.group(1)) if tm else None, "text": text}


def run_pass(sink: cc.Sink, st: lib.State) -> int:
    n0 = sink.added + len(sink.buf)
    bad: Set[str] = set(st.get("bad_urls", []))
    for orig, ts in cc.cdx_urls("ccg.gov.cn/", url_re=ART.pattern):
        canon = re.sub(r"^https?://(?:www\.)?ccg\.gov\.cn(?::80)?", "https://www.ccg.gov.cn", orig)
        did = lib.make_id(SOURCE, canon)
        if sink.has(did) or canon in bad:
            continue
        wb = cc.wayback_raw(orig, ts)
        html = lib.fetch(wb, min_delay=5)
        if html is None:
            time.sleep(60)  # Wayback refusing / throttling: not a verdict on the page
            continue
        p = parse(html)
        if not p or not p["date"]:
            bad.add(canon)
            st["bad_urls"] = sorted(bad)
            continue
        sink.add({"id": did, "outlet": "official", "org": "CCG", "lang": "en" if "/mhenu" in canon else "zh",
                  "date": p["date"], "url": canon, "title": p["title"], "speaker": None,
                  "kind": "statement" if re.search(r"发言人|声明|Spokesperson|statement", p["title"]) else "article",
                  "text": p["text"], "via": "wayback", "wayback": wb})
    st["bad_urls"] = sorted(bad)
    sink.flush()
    return sink.added + len(sink.buf) - n0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--follow", action="store_true", help="re-enumerate Wayback captures every 24 hours")
    a = ap.parse_args()
    st = lib.State(SOURCE)
    sink = cc.Sink(SOURCE, st)
    log.info("pass: %d new", run_pass(sink, st))
    if a.follow:
        time.sleep(24 * 3600)
        cc.follow(lambda: run_pass(sink, st), 24 * 3600, SOURCE)


if __name__ == "__main__":
    main()
