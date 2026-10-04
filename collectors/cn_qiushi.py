"""Qiushi (求是), the CCP Central Committee's theory journal, and its sister Hongqi Wengao (红旗文稿).

Source name: cn_qiushi. outlet = official; org = Qiushi | HongqiWengao | Qiushi-EN.

  zh  issue archive by year: https://www.qstheory.cn/qs/mulu.htm (求是 2019 -> now) and /hqwglist/mulu.htm
      (红旗文稿 2020 -> now) -> year page -> issue tables of contents -> articles. Two URL schemes on the same
      template: /dukan/qs/YYYY-MM/DD/c_<n>.htm (to 2024) and /YYYYMMDD/<32 hex>/c.html (2025 ->). Pre-2019 year
      pages (dukan/qs/2014/..., zxdk/<year>/) answer 404 on the live site and are not collected.
  en  https://en.qstheory.cn/ English edition (bimonthly since 2024): latest issue, past-issue pages (n_*.htm) and
      the section pages (Xi's speeches, focus, opinion, ...).

robots.txt: 404 on both hosts (no rules). 5 s between requests. Article = <div class="highlight"> (zh) /
<div class="arcCont2"> (en); date = page's own pubtime / "Updated:" line; 来源 line kept as `issue`.

    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_qiushi.py [--follow]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Set

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cn_common as cc  # noqa: E402
import lib  # noqa: E402

SOURCE = "cn_qiushi"
DELAY = 5
ZH_INDEXES = {"Qiushi": "https://www.qstheory.cn/qs/mulu.htm", "HongqiWengao": "https://www.qstheory.cn/hqwglist/mulu.htm"}
ZH_LINK = re.compile(r'href="((?:https?:)?//www\.qstheory\.cn/(?:dukan/[\w/]+?/\d{4}-\d{2}/\d{2}/c_\d+\.htm'
                     r'|\d{8}/[0-9a-f]{32}/c\.html)|\.\./\d{8}/[0-9a-f]{32}/c\.html|\d{8}/[0-9a-f]{32}/c\.html)"')
EN_BASE = "https://en.qstheory.cn/"
EN_PAGES = ["latestissue.html", "pastissues.html", "xijinping.html", "exclusive.html", "xismoments.html",
            "xisspeeches.html", "XisWorks.html", "focus.html", "policyanalysis.html", "developmentexperience.html",
            "opinion.html", "chinaandtheworld.html"]
log = lib.setup_logging("cn_qiushi")


def links(page_url: str, html: str) -> List[str]:
    out = []
    for h in ZH_LINK.findall(html):
        u = urllib.parse.urljoin(page_url, h)
        out.append(re.sub(r"^https?:", "https:", u if not u.startswith("//") else "https:" + u))
    return [u for u in dict.fromkeys(out) if u != page_url]


def parse_zh(html: str) -> Optional[Dict]:
    inner = cc.balanced_div(html, 'class="inner"') or html
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", inner, re.S)
    title = lib.clean_html(h1.group(1)) if h1 else ""
    body = cc.balanced_div(inner, 'class="highlight"') or cc.balanced_div(inner, 'class="text"')
    if not title or not body:
        return None
    text = cc.paragraphs(body)
    pt = re.search(r'class="pubtime"[^>]*>([^<]+)<', inner)
    src = re.search(r"来源：\s*([^<]+)<", inner)
    au = re.search(r"作者：\s*([^<]+)<", inner)
    return {"title": title, "text": text, "date": cc.first_date(pt.group(1)) if pt else None,
            "issue": lib.clean_html(src.group(1)).strip() if src else None,
            "author": lib.clean_html(au.group(1)).strip() if au else None}


def url_date(u: str) -> Optional[str]:
    m = re.search(r"/(\d{4})(\d{2})(\d{2})/[0-9a-f]{32}/", u) or re.search(r"/(\d{4})-(\d{2})/(\d{2})/c_", u)
    return cc.ymd(*m.groups()) if m else None


def zh_pass(sink: cc.Sink, st: lib.State) -> int:
    """Breadth-first walk index -> year pages -> issue TOCs -> articles. A page is an article when it parses
    and its body links to fewer than 5 other qstheory pages (TOCs use the same template)."""
    import datetime as dt
    n0 = sink.added + len(sink.buf)
    bad: Set[str] = set(st.get("bad_urls", []))
    toc_done: Set[str] = set(st.get("issues_done", []))
    cutoff = (dt.date.today() - dt.timedelta(days=60)).isoformat()
    for org, idx in ZH_INDEXES.items():
        queue = [(idx, 0)]
        seen = {idx}
        while queue:
            url, depth = queue.pop(0)
            did = lib.make_id(SOURCE, url)
            if sink.has(did) or url in bad or url in toc_done:
                continue
            html = cc.get(url, DELAY)
            if html is None:
                continue
            p = parse_zh(html) if depth > 0 else None
            body = (cc.balanced_div(html, 'class="highlight"') or "") if p else ""
            sub = links(url, body if p else html)
            if p and len(sub) < 5:
                if p["date"] and len(p["text"]) >= 100:
                    sink.add({"id": did, "outlet": "official", "org": org, "lang": "zh", "date": p["date"],
                              "url": url, "title": p["title"], "speaker": None,
                              "kind": "speech" if p["author"] and "习近平" in p["author"] else "article",
                              "text": p["text"], "via": "direct", "issue": p["issue"], "author": p["author"]})
                else:
                    bad.add(url)
                continue
            if depth >= 3:
                continue
            for u in sub:
                if u not in seen:
                    seen.add(u)
                    queue.append((u, depth + 1))
            d = url_date(url)
            if depth >= 2 and d and d < cutoff:
                toc_done.add(url)  # an issue TOC older than two months no longer changes
            st["issues_done"] = sorted(toc_done)
            st["bad_urls"] = sorted(bad)
            sink.flush()
            log.info("%s listing %s (depth %d, %d links; %d new so far)", org, url, depth, len(sub),
                     sink.added + len(sink.buf) - n0)
    sink.flush()
    return sink.added + len(sink.buf) - n0


def parse_en(html: str) -> Optional[Dict]:
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    body = cc.balanced_div(html, 'class="arcCont2"') or cc.balanced_div(html, 'class="arcCont"')
    if not h1 or not body:
        return None
    info = cc.balanced_div(html, 'class="info2_l"') or ""
    up = re.search(r"Updated:\s*([0-9-]+)", lib.clean_html(info))
    by = re.search(r"<span>\s*By\s*(.*?)</span>", info, re.S)
    return {"title": lib.clean_html(h1.group(1)), "text": cc.paragraphs(body),
            "date": cc.first_date(up.group(1)) if up else None,
            "author": lib.clean_html(by.group(1)) if by else None}


def en_pass(sink: cc.Sink, st: lib.State) -> int:
    n0 = sink.added + len(sink.buf)
    bad: Set[str] = set(st.get("bad_urls", []))
    pages = [EN_BASE + p for p in EN_PAGES]
    seen = set(pages)
    arts: List[str] = []
    while pages:
        pu = pages.pop(0)
        html = cc.get(pu, DELAY) or ""
        for h in re.findall(r'href="((?:https?://en\.qstheory\.cn/)?n_\d+\.htm)"', html):
            u = urllib.parse.urljoin(EN_BASE, h).replace("http://", "https://")
            if u not in seen:
                seen.add(u)
                pages.append(u)
        for h in re.findall(r'href="((?:https?://en\.qstheory\.cn/)?\d{4}-\d{2}/\d{2}/c_\d+\.htm)"', html):
            arts.append(urllib.parse.urljoin(EN_BASE, h).replace("http://", "https://"))
    for au in dict.fromkeys(arts):
        did = lib.make_id(SOURCE, au)
        if sink.has(did) or au in bad:
            continue
        html = cc.get(au, DELAY)
        if html is None:
            continue
        p = parse_en(html)
        date = (p or {}).get("date") or cc.first_date(au)
        if not p or len(p["text"]) < 100:
            bad.add(au)
            continue
        sink.add({"id": did, "outlet": "official", "org": "Qiushi-EN", "lang": "en", "date": date, "url": au,
                  "title": p["title"], "speaker": None,
                  "kind": "speech" if p["author"] and "Xi Jinping" in p["author"] else "article",
                  "text": p["text"], "via": "direct", "author": p["author"]})
    st["bad_urls"] = sorted(bad)
    sink.flush()
    return sink.added + len(sink.buf) - n0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--follow", action="store_true", help="repeat every 6 hours")
    a = ap.parse_args()
    st = lib.State(SOURCE)
    sink = cc.Sink(SOURCE, st)

    def one() -> int:
        return en_pass(sink, st) + zh_pass(sink, st)

    log.info("pass: %d new", one())
    if a.follow:
        time.sleep(6 * 3600)
        cc.follow(one, 6 * 3600, SOURCE)


if __name__ == "__main__":
    main()
