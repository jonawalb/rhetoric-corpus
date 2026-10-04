"""State Council of the PRC: www.gov.cn (Chinese) and english.www.gov.cn (English).

Source name: cn_govcn. outlet = official; org = StateCouncil (gazette rows: StateCouncilGazette).

Parts (one process, in order; --follow repeats every 4 h; already-stored ids are skipped):
  zhfeeds  the column JSON files the gov.cn list pages load (YAOWENLIEBIAO.json = 要闻, ZUIXINZHENGCE.json = 最新政策
           back to 2020, TONGYONGLIEBIAODRQ.json = 新闻发布 etc.; discovered from the column pages each pass)
  gazette  国务院公报 (State Council Gazette) 2000 -> now: /gongbao/gbgl.json lists every issue; each issue page lists
           its documents (State Council orders, regulations, notices, Xi/Li speeches, ministry rules, treaties).
  en       english.www.gov.cn sections (news, policies, archive, ...) via their page_N.html listings (back to
           ~Oct 2022 on the live site; older English URLs now redirect to the homepage).
  wayback  (--wayback, separate process, own state) older English articles (2014 -> 2022 URL scheme
           .../YYYY/MM/DD/content_2814*.htm) from Wayback raw captures.

robots.txt: www.gov.cn has ~20 Disallow lines (/premier/, /2016*/, /guowuyuan/yangjing/, ...) honoured through
cn_common.robots_ok (wildcard aware); english.www.gov.cn serves its homepage for /robots.txt (no rules).
5 s between requests per host.

    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_govcn.py [--follow] [--parts zhfeeds,gazette,en]
    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_govcn.py --wayback
"""
from __future__ import annotations

import argparse
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

SOURCE = "cn_govcn"
DELAY = 5
ZH = "https://www.gov.cn"
EN = "https://english.www.gov.cn"
ZH_COLUMNS = ["/yaowen/liebiao/", "/zhengce/zuixin/", "/lianbo/fabu/", "/lianbo/bumen/", "/lianbo/difang/",
              "/zhengce/jiedu/", "/zhengce/zhengcewenjianku/", "/xinwen/", "/lianbo/"]
EN_SEEDS = ["/news/", "/policies/", "/archive/", "/statecouncil/", "/institutions/", "/news/topnews/",
            "/news/speeches/", "/policies/latestreleases/", "/policies/featured/", "/policies/policywatch/",
            "/archive/whitepaper/", "/archive/statecouncilgazette/", "/news/pressbriefings/", "/statecouncil/premier/"]
EN_ART = re.compile(r"(?:https?:)?//english\.www\.gov\.cn/[\w/]+?/\d{6}/\d{2}/content_WS[0-9a-f]+\.html")
EN_SEC = re.compile(r"(?:https?:)?//english\.www\.gov\.cn((?:/[a-z][\w]*)+)/?\"")
EN_SKIP = re.compile(r"/(services|search|favicon|images|Homepage)\b", re.I)
log = lib.setup_logging("cn_govcn")


def kind_of(title: str, url: str) -> str:
    t = title.lower()
    if re.search(r"发布会|吹风会|press conference|briefing", t):
        return "briefing"
    if re.search(r"讲话|致辞|演讲|speech|remarks|address", t):
        return "speech"
    if "/zhengce/" in url or "/gongbao/" in url or re.search(r"通知|意见|决定|条例|办法|规定|令（|regulation|circular|guideline", t):
        return "policy"
    return "article"


# ------------------------------------------------------------------------------------------------ zh article
def parse_zh(html: str) -> Optional[Dict]:
    body = cc.balanced_div(html, 'id="UCAP-CONTENT"') or cc.balanced_div(html, 'class="pages_content"')
    if not body:
        return None
    text = cc.paragraphs(body)
    if len(text) < 100:
        return None
    title = re.split(r"_{1,2}", cc.title_tag(html))[0].strip() or (cc.meta(html, "ArticleTitle") or "")
    date = cc.first_date(cc.meta(html, "firstpublishedtime") or "") or cc.first_date(cc.meta(html, "PubDate") or "")
    src = re.search(r"来源：\s*(?:<[^>]+>\s*)*([^<\s][^<]*)<", html)
    return {"title": title, "date": date, "text": text, "column": cc.meta(html, "lanmu"),
            "orig_source": lib.clean_html(src.group(1)).strip() if src else None}


def store_zh(sink: cc.Sink, url: str, bad: Set[str], org: str = "StateCouncil", hint_date: Optional[str] = None,
             extra: Optional[Dict] = None) -> bool:
    url = url.replace("http://www.gov.cn", ZH)
    did = lib.make_id(SOURCE, url)
    if sink.has(did) or url in bad:
        return False
    html = cc.get(url, DELAY)
    if html is None:
        if lib.fetch.last.get("status") in (404, 410) or not cc.robots_ok(url):
            bad.add(url)
        return False
    p = parse_zh(html)
    date = (p or {}).get("date") or hint_date or cc.first_date(url, compact=False)
    if not p or not date:
        bad.add(url)
        return False
    row = {"id": did, "outlet": "official", "org": org, "lang": "zh", "date": date, "url": url,
           "title": p["title"], "speaker": None, "kind": kind_of(p["title"], url), "text": p["text"],
           "via": lib.fetch.last.get("via") or "direct", "column": p["column"], "orig_source": p["orig_source"]}
    row.update(extra or {})
    return sink.add(row)


def zhfeeds_pass(sink: cc.Sink, st: lib.State) -> int:
    n0 = sink.added + len(sink.buf)
    bad: Set[str] = set(st.get("bad_urls", []))
    feeds: Set[str] = set()
    for col in ZH_COLUMNS:
        html = cc.get(ZH + col, DELAY) or ""
        for name in set(re.findall(r"([A-Z][A-Z0-9_]+\.json)", html)):
            feeds.add(urllib.parse.urljoin(ZH + col, name))
    for feed in sorted(feeds):
        body = cc.get(feed, DELAY)
        try:
            items = json.loads(body or "[]")
        except json.JSONDecodeError:
            log.warning("bad JSON %s", feed)
            continue
        log.info("feed %s: %d items", feed, len(items))
        for it in items if isinstance(items, list) else []:
            u = (it.get("URL") or "").strip()
            if not u.startswith(("http://www.gov.cn", "https://www.gov.cn")):
                continue
            store_zh(sink, u, bad, hint_date=cc.first_date(it.get("DOCRELPUBTIME") or ""))
        st["bad_urls"] = sorted(bad)
        sink.flush()
    return sink.added + len(sink.buf) - n0


def gazette_pass(sink: cc.Sink, st: lib.State) -> int:
    n0 = sink.added + len(sink.buf)
    bad: Set[str] = set(st.get("bad_urls", []))
    done_issues: Set[str] = set(st.get("gazette_done", []))
    body = cc.get(ZH + "/gongbao/gbgl.json", DELAY)
    try:
        years = json.loads(body or "[]")[0]["values"]
    except (json.JSONDecodeError, IndexError, KeyError):
        log.warning("gazette catalogue unreadable")
        return 0
    issues: List[Tuple[str, str]] = []
    for y in sorted(years, reverse=True):
        for name, info in years[y].items():
            if info.get("gname"):
                issues.append((f"{y}{name}", info["gname"]))
    latest = {u for _, u in issues[:2]}  # re-check the two newest issues every pass
    for label, iu in issues:
        if iu in done_issues and iu not in latest:
            continue
        html = cc.get(iu, DELAY)
        if html is None:
            continue
        links = list(dict.fromkeys(urllib.parse.urljoin(iu, h) for h in
                                   re.findall(r'href="([^"]*content_\d+\.html?)"', html)))
        links = [u for u in links if "/gongbao/" in u]
        for u in links:
            store_zh(sink, u, bad, org="StateCouncilGazette", extra={"issue": label})
        done_issues.add(iu)
        st["gazette_done"] = sorted(done_issues)
        st["bad_urls"] = sorted(bad)
        sink.flush()
        log.info("gazette %s: %d links", label, len(links))
    return sink.added + len(sink.buf) - n0


# ------------------------------------------------------------------------------------------------ english
def parse_en(html: str) -> Optional[Dict]:
    body = None
    for marker in ('class="Artical_Content"', 'id="UCAP-CONTENT"', 'class="pages_content"', 'id="Zoom"',
                   'class="article"', 'class="content"'):
        body = cc.balanced_div(html, marker)
        if body and len(cc.paragraphs(body)) >= 100:
            break
        body = None
    if not body:
        return None
    tm = re.search(r'class="Artical_Title[^"]*"[^>]*>(.*?)</div>', html, re.S)
    title = lib.clean_html(tm.group(1)) if tm else re.sub(r"\s*[-_|]\s*(english\.gov\.cn|The State Council.*)$", "",
                                                          cc.title_tag(html))
    date = cc.first_date(cc.meta(html, "publishdate") or "")
    if not date:
        im = re.search(r'class="Artical_Info[^"]*"[^>]*>(.*?)</div>', html, re.S)
        date = cc.first_date(lib.clean_html(im.group(1))) if im else None
    return {"title": title, "date": date, "text": cc.paragraphs(body), "orig_source": cc.meta(html, "source")}


def en_page_urls(sec: str, html: str) -> List[str]:
    nums = [int(n) for n in re.findall(r"page_(\d+)\.html", html)]
    last = max(nums) if nums else 1
    return [f"{EN}{sec}page_{p}.html" for p in range(2, last + 1)]


def en_pass(sink: cc.Sink, st: lib.State) -> int:
    n0 = sink.added + len(sink.buf)
    bad: Set[str] = set(st.get("bad_urls", []))
    full: Set[str] = set(st.get("en_full_done", []))  # sections whose every page was walked once
    queue = list(EN_SEEDS)
    seen = set(queue)
    while queue:
        sec = queue.pop(0)
        first = cc.get(EN + sec, DELAY)
        if not first:
            continue
        for m in EN_SEC.finditer(first):
            s = m.group(1).rstrip("/") + "/"
            if s not in seen and not EN_SKIP.search(s) and s.count("/") <= 4 and "content_" not in s:
                seen.add(s)
                queue.append(s)
        for pu in [None] + en_page_urls(sec, first):
            html = first if pu is None else cc.get(pu, DELAY)
            if not html:
                break
            new = [u for u in _en_links([html]) if not sink.has(lib.make_id(SOURCE, u)) and u not in bad]
            for u in new:
                _store_en(sink, u, bad)
            sink.flush()
            if pu is not None and sec in full and not new:
                break  # section walked before: stop at the first page with nothing new
        full.add(sec)
        st["en_full_done"] = sorted(full)
        st["bad_urls"] = sorted(bad)
        sink.flush()
        log.info("en section %s done (%d new so far)", sec, sink.added + len(sink.buf) - n0)
    return sink.added + len(sink.buf) - n0


def _en_links(pages: Iterable[str]) -> List[str]:
    out = []
    for html in pages:
        for m in EN_ART.finditer(html):
            u = m.group(0)
            out.append("https:" + u if u.startswith("//") else u.replace("http://", "https://"))
    return list(dict.fromkeys(out))


def _store_en(sink: cc.Sink, url: str, bad: Set[str], via_wayback: Optional[str] = None,
              hint_date: Optional[str] = None) -> bool:
    did = lib.make_id(SOURCE, url)
    if sink.has(did) or url in bad:
        return False
    html = lib.fetch(via_wayback, min_delay=5) if via_wayback else cc.get(url, DELAY)
    if html is None:
        if lib.fetch.last.get("status") in (404, 410):
            bad.add(url)
        elif via_wayback:
            time.sleep(60)  # Wayback refusing connections: slow down instead of racing through the list
        return False
    p = parse_en(html)
    date = (p or {}).get("date") or hint_date
    if not p or not date or len(p["text"]) < 100:
        bad.add(url)
        return False
    return sink.add({"id": did, "outlet": "official", "org": "StateCouncil", "lang": "en", "date": date, "url": url,
                     "title": p["title"], "speaker": None, "kind": kind_of(p["title"], url), "text": p["text"],
                     "via": "wayback" if via_wayback else "direct", "orig_source": p["orig_source"],
                     **({"wayback": via_wayback} if via_wayback else {})})


OLD_EN = re.compile(r"english\.(?:www\.)?gov\.cn(?::80)?/[\w/]+/(\d{4})/(\d{2})/(\d{2})/content_\d+\.htm$")


def wayback_pass(sink: cc.Sink, st: lib.State) -> int:
    n0 = sink.added + len(sink.buf)
    bad: Set[str] = set(st.get("bad_urls", []))
    for orig, ts in cc.cdx_urls("english.www.gov.cn/", url_re=OLD_EN.pattern):
        m = OLD_EN.search(orig)
        canon = re.sub(r"^https?://([^/:]+)(:80)?", r"http://\1", orig)
        _store_en(sink, canon, bad, via_wayback=cc.wayback_raw(orig, ts), hint_date=cc.ymd(*m.groups()))
        if len(bad) % 50 == 0:
            st["bad_urls"] = sorted(bad)
    st["bad_urls"] = sorted(bad)
    sink.flush()
    return sink.added + len(sink.buf) - n0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--follow", action="store_true", help="repeat every 4 hours")
    ap.add_argument("--parts", default="zhfeeds,en,gazette")
    ap.add_argument("--wayback", action="store_true", help="backfill old English pages via Wayback (own state)")
    a = ap.parse_args()
    if a.wayback:
        st = lib.State(f"{SOURCE}_wayback")
        sink = cc.Sink(SOURCE, st)
        # repeat every 12 h: a pass can end early when Wayback refuses connections; stored ids are skipped
        cc.follow(lambda: wayback_pass(sink, st), 12 * 3600, f"{SOURCE}_wayback")
    parts = a.parts.split(",")
    # one state file per part set, so e.g. "--parts en" and "--parts zhfeeds,gazette" can run side by side
    st = lib.State(SOURCE if a.parts == ap.get_default("parts") else f"{SOURCE}_{'_'.join(parts)}")
    sink = cc.Sink(SOURCE, st)
    fns = {"zhfeeds": zhfeeds_pass, "en": en_pass, "gazette": gazette_pass}

    def one() -> int:
        return sum(fns[p](sink, st) for p in parts if p in fns)

    log.info("pass: %d new", one())
    if a.follow:
        time.sleep(4 * 3600)
        cc.follow(one, 4 * 3600, SOURCE)


if __name__ == "__main__":
    main()
