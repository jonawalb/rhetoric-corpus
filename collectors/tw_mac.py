"""Taiwan Mainland Affairs Council (陸委會, www.mac.gov.tw) news items, 2021-01-01 -> today — Wayback copies only.

    uv run --project ~/Projects/rhetoric-corpus python collectors/tw_mac.py --lang zh|en [--max N] [--dry-run]

www.mac.gov.tw answers our User-Agent with a Cloudflare browser challenge (HTTP 403 "Just a moment..."); it
is not attempted. robots.txt (served normally) only disallows /public. Item URLs are enumerated from the
Wayback CDX index (prefix www.mac.gov.tw/News_Content.aspx, or /en/News_Content.aspx), de-duplicated on the
item key `s=` (the same item appears under several menu nodes `n=`), and the latest HTTP-200 capture of each
is read as a raw `id_` copy through lib.fetch (robots-checked, >= 5 s spacing). Press-release nodes
(zh n=05B73310C5C3A632 本會新聞稿, n=B383123AEADAEE52) go first.

Date = the page's own 發布日期 (ROC, e.g. 111-03-25 -> 2022-03-25, via tw_common.roc_date) or the English page's
date line; items without a readable date are skipped. Coverage = whatever Wayback captured (partial).
Sources tw_mac_zh / tw_mac_en; org "MAC"; speaker null (releases are institutional). Resumable:
state/tw_mac_<lang>.json. No on-disk page cache.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import State, clean_html, fetch, make_id, robots_allowed, setup_logging, write_docs  # noqa: E402
from tw_common import START, any_date, roc_date  # noqa: E402

PREFIX = {"zh": "www.mac.gov.tw/News_Content.aspx", "en": "www.mac.gov.tw/en/News_Content.aspx"}
FIRST_NODES = ("05B73310C5C3A632", "B383123AEADAEE52")
WB_DELAY = 5.0


def cdx_items(lang: str, log) -> Dict[str, Dict]:
    """{s_key: {"url": original, "ts": latest 200 timestamp, "n": node}} from the Wayback CDX index."""
    q = {"url": PREFIX[lang], "matchType": "prefix", "filter": "statuscode:200", "from": START[:4],
         "fl": "timestamp,original", "limit": "50000"}
    body = fetch("https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(q), min_delay=WB_DELAY,
                 timeout=180)
    if not body:
        raise SystemExit("CDX listing unavailable (Wayback down or refusing); re-run later")
    items: Dict[str, Dict] = {}
    for line in body.splitlines():
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue
        ts, orig = parts[0], parts[1].replace("&amp;", "&")
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(orig).query)
        s, n = (qs.get("s") or [""])[0], (qs.get("n") or [""])[0]
        if not re.fullmatch(r"[0-9A-F]{16}", s):
            continue
        cur = items.get(s)
        if cur is None or ts > cur["ts"] or (n in FIRST_NODES and cur["n"] not in FIRST_NODES):
            items[s] = {"url": orig, "ts": ts, "n": n}
    log.info("CDX: %d lines, %d distinct items", len(body.splitlines()), len(items))
    return items


def parse(html: str, lang: str) -> Optional[Dict]:
    """date / title / text / category from a MAC News_Content page."""
    m = re.search(r"(?:發布日期|Date)\s*[:：]?\s*(?:<[^>]+>\s*)*([0-9]{2,4}[-/.][0-9]{1,2}[-/.][0-9]{1,2})", html)
    raw = m.group(1) if m else ""
    date = (roc_date(raw) if re.match(r"\d{2,3}[-/.]", raw) else any_date(raw)) if raw else None
    h2 = [clean_html(x) for x in re.findall(r"<h2[^>]*>(.*?)</h2>", html, re.S)]
    i = html.find("area-essay page-caption-p")
    if not (date and i > 0 and len(h2) >= 2):
        return None
    seg = html[i:]
    j = seg.find("area-editor")
    seg = seg[:seg.rfind("<", 0, j) if j > 0 else 60000]
    p = seg.find('<div class="p">')
    text = clean_html(seg[p:] if p > 0 else seg)
    return {"date": date, "raw_date": raw, "title": h2[1], "category": h2[0], "text": text}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["zh", "en"], required=True)
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    source = f"tw_mac_{a.lang}"
    log = setup_logging(source)
    st = State(source)
    items = cdx_items(a.lang, log)
    order: List[str] = sorted(items, key=lambda s: (items[s]["n"] not in FIRST_NODES, s))
    log.info("%d items; %d already done", len(order), sum(st.is_done(s) for s in order))
    n = fails = 0
    for s in order:
        if st.is_done(s):
            continue
        it = items[s]
        if not robots_allowed(it["url"]):
            log.warning("robots disallows %s", it["url"])
            continue
        wb = f"https://web.archive.org/web/{it['ts']}id_/{it['url']}"
        html = fetch(wb, min_delay=WB_DELAY)
        if not html:
            log.warning("wayback fetch failed: %s", wb)
            fails += 1
            if fails >= 5:  # Wayback refusing connections: stop this pass; the wrapper loop re-runs later
                log.error("5 consecutive Wayback failures; ending this pass")
                break
            continue
        fails = 0
        art = parse(html, a.lang)
        if not art:
            log.info("unparsed / no date: %s", wb)
            st.mark_done(s)
            continue
        if art["date"] < START or len(art["text"]) < 40:
            st.mark_done(s)
            continue
        title = art["title"]
        kind = ("briefing" if re.search(r"記者會|press conference|briefing", title, re.I) else
                "transcript" if re.search(r"致詞|講稿|演講|remarks|speech|address", title, re.I) else "statement")
        row = {"id": make_id(source, s), "country": "TW", "source": source, "outlet": "official", "org": "MAC",
               "lang": a.lang, "date": art["date"], "url": it["url"], "title": title, "speaker": None,
               "kind": kind, "text": art["text"], "via": "wayback", "wayback": it["ts"],
               "category": art["category"]}
        if a.dry_run:
            print(row["date"], art["raw_date"], kind, "|", title, "|", row["text"][:200].replace("\n", " "))
        else:
            write_docs("TW", source, [row])
            st.mark_done(s)
        n += 1
        if n % 10 == 0 and not a.dry_run:
            st.save()
            log.info("%d new docs (at %s)", n, art["date"])
        if a.max and n >= a.max:
            break
    if not a.dry_run:
        st.save()
    log.info("done: %d new docs", n)


if __name__ == "__main__":
    main()
