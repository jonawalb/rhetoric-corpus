"""PRC embassies abroad: spokesperson remarks, ambassador speeches / signed articles, embassy news, in the
embassy's local-language and Chinese editions (one language per document).

Source name: cn_embassy. outlet = official; org = Embassy-<CC> (e.g. Embassy-GB, Embassy-JP).

All embassies run the same MFA TRS CMS: https://<cc>.china-embassy.gov.cn/<eng|chn|jpn|kor|fra>/ with section
listings (index.htm, index_1.htm, ... up to `countPage`) and articles at .../YYYYMM/tYYYYMMDD_<n>.htm. The collector
discovers sections from the edition's homepage (and sub-sections from section pages), skips consular / visa /
contact pages, and stores every article it finds (all topics; the editions also repost MFA spokesperson remarks
in the local language). Date = the tYYYYMMDD in the URL. Body = the TRS_UEDITOR / TRS_Editor div.

Not collected: us.china-embassy.gov.cn - its /robots.txt answers a redirect loop, which lib.robots_allowed treats
as unreadable = disallow (not worked around); eu mission host does not resolve.
robots.txt elsewhere redirects to the homepage HTML (no rules). 5 s between requests per host.

    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_embassy.py [--follow] [--only gb,jp]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cn_common as cc  # noqa: E402
import lib  # noqa: E402

SOURCE = "cn_embassy"
DELAY = 5
LANGS = {"eng": "en", "chn": "zh", "jpn": "ja", "kor": "ko", "fra": "fr"}
EMBASSIES: List[Tuple[str, Tuple[str, ...]]] = [
    ("gb", ("eng", "chn")), ("jp", ("jpn", "chn")), ("ph", ("eng", "chn")), ("au", ("eng", "chn")),
    ("in", ("eng", "chn")), ("ca", ("eng", "chn")), ("kr", ("kor", "chn")), ("fr", ("fra", "chn")),
    ("de", ("chn",)),
]
SKIP = re.compile(r"/(lsfw|lszc|lsbh|lsyw|visa|qz\w*|hzlx|lqfw|consular|zjsg|sgbgsjjdz|contact\w*|lxwm|sggbm|"
                  r"sgjss|xyxx|lxgz|jyjl|images|material)/", re.I)
ART = re.compile(r"t(\d{8})_\d+\.html?$")
BODY_MARKERS = ("TRS_UEDITOR", "TRS_Editor", "trs_editor_view", 'id="News_Body_Txt_A"')
log = lib.setup_logging("cn_embassy")


def norm(u: str) -> str:
    return u.split("#")[0].split("?")[0].replace("http://", "https://")


def listing_links(page_url: str, html: str, root: str) -> Tuple[List[str], List[str]]:
    """(article urls, sub-section urls) under `root` linked from a listing page."""
    arts, secs = [], []
    for h in re.findall(r'href="([^"#]+)"', html):
        u = norm(urllib.parse.urljoin(page_url, h.strip()))
        if not u.startswith(root) or SKIP.search(u):
            continue
        if ART.search(u):
            arts.append(u)
        elif u.endswith("/") and u != root and u.count("/") - root.count("/") <= 3:
            secs.append(u)
    return list(dict.fromkeys(arts)), list(dict.fromkeys(secs))


def page_count(html: str) -> int:
    m = re.search(r"countPage\s*=\s*(\d+)", html) or re.search(r"createPageHTML\(\s*'?(\d+)", html)
    return min(int(m.group(1)), 200) if m else 1


def parse(html: str) -> Optional[Dict]:
    best = ""
    for mk in BODY_MARKERS:
        frag = cc.balanced_div(html, mk)
        if frag:
            t = cc.paragraphs(frag)
            if len(t) > len(best):
                best = t
    if len(best) < 100:
        return None
    tm = (re.search(r'<(?:div|td|h1)[^>]+class="(?:bigtitle|title|news-title)"[^>]*>(.*?)</(?:div|td|h1)>', html, re.S)
          or re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S))
    title = lib.clean_html(tm.group(1)) if tm else ""
    if not title:
        title = re.split(r"_|——", cc.title_tag(html))[0].strip()
    return {"title": title, "text": best}


def kind_of(url: str, title: str) -> str:
    t = title.lower()
    if re.search(r"spokes|fyr|发言人|報道官|대변인|porte-parole", url.lower() + " " + t):
        return "statement"
    if re.search(r"speech|remarks|address|讲话|致辞|演讲|挨拶|講演|연설|축사|discours", t):
        return "speech"
    return "article"


def crawl_edition(sink: cc.Sink, st: lib.State, cc_code: str, path: str) -> int:
    n0 = sink.added + len(sink.buf)
    host = f"https://{cc_code}.china-embassy.gov.cn"
    root = f"{host}/{path}/"
    lang = LANGS[path]
    bad: Set[str] = set(st.get("bad_urls", []))
    full: Set[str] = set(st.get("full_done", []))
    home = cc.get(root, DELAY)
    if not home:
        log.warning("%s unreachable", root)
        return 0
    arts0, queue = listing_links(root, home, root)
    seen = set(queue)
    todo: List[str] = list(arts0)
    while queue:
        sec = queue.pop(0)
        first = cc.get(sec + "index.htm", DELAY) or cc.get(sec, DELAY)
        if not first:
            continue
        a, s = listing_links(sec, first, root)
        for x in s:
            if x not in seen:
                seen.add(x)
                queue.append(x)
        pages_a = [a]
        for p in range(1, page_count(first)):
            html = cc.get(f"{sec}index_{p}.htm", DELAY)
            if not html:
                break
            pa = listing_links(sec, html, root)[0]
            pages_a.append(pa)
            if sec in full and all(sink.has(lib.make_id(SOURCE, u)) or u in bad for u in pa):
                break  # walked before; nothing new on this page
        full.add(sec)
        for pa in pages_a:
            todo += pa
        n_new = _store_all(sink, st, bad, todo, cc_code, lang)
        todo = []
        st["full_done"] = sorted(full)
        log.info("%s section %s: +%d", cc_code, sec, n_new)
    _store_all(sink, st, bad, todo, cc_code, lang)
    return sink.added + len(sink.buf) - n0


def _store_all(sink: cc.Sink, st: lib.State, bad: Set[str], urls: List[str], cc_code: str, lang: str) -> int:
    n = 0
    for u in dict.fromkeys(urls):
        did = lib.make_id(SOURCE, u)
        if sink.has(did) or u in bad:
            continue
        html = cc.get(u, DELAY)
        if html is None:
            if lib.fetch.last.get("status") in (404, 410):
                bad.add(u)
            continue
        p = parse(html)
        m = ART.search(u)
        date = cc.first_date(m.group(1), compact=True) if m else None
        if not p or not date:
            bad.add(u)
            continue
        if sink.add({"id": did, "outlet": "official", "org": f"Embassy-{cc_code.upper()}", "lang": lang,
                     "date": date, "url": u, "title": p["title"], "speaker": None, "kind": kind_of(u, p["title"]),
                     "text": p["text"], "via": "direct"}):
            n += 1
    st["bad_urls"] = sorted(bad)
    sink.flush()
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--follow", action="store_true", help="repeat every 6 hours")
    ap.add_argument("--only", default="", help="comma list of embassy codes (gb,jp,...)")
    a = ap.parse_args()
    st = lib.State(SOURCE)
    sink = cc.Sink(SOURCE, st)
    only = set(filter(None, a.only.split(",")))

    def one() -> int:
        n = 0
        for code, paths in EMBASSIES:
            if only and code not in only:
                continue
            for p in paths:
                n += crawl_edition(sink, st, code, p)
        return n

    log.info("pass: %d new", one())
    if a.follow:
        time.sleep(6 * 3600)
        cc.follow(one, 6 * 3600, SOURCE)


if __name__ == "__main__":
    main()
