"""Rodong Sinmun (English edition) via Wayback Machine copies -> docs/KP/kp_rodong_en.jsonl.

rodong.rep.kp is not reachable from here directly, and kcna.kp (plus Wayback copies of it) is refused
by the local network web filter, so this collector reads only Wayback captures of rodong.rep.kp/en.
Two URL schemes exist:
  * old site (to ~2022): index.php?strPageID=SF01_02_01&newsID=YYYY-MM-DD-NNNN
  * new site (2022->):  index.php?<base64 of "12@YYYY-MM-DD-[A-Z]NNN@cat@pos@@0@n">
Each article key (date + serial) is captured under many URLs; we try captures until one renders the
article (title + date match), newest articles first. Publication date comes from the article key and
is cross-checked against the page date.

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/kp_rodong_en.py [--since 2021-01-01]
"""
from __future__ import annotations

import argparse
import base64
import collections
import re
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from lib import RAW, State, clean_html, fetch, make_id, now_iso, setup_logging, write_docs  # noqa: E402

SOURCE = "kp_rodong_en"
CDX = ("https://web.archive.org/cdx/search/cdx?url=rodong.rep.kp/en/&matchType=prefix&from={y}"
       "&collapse=urlkey&fl=timestamp,original&filter=statuscode:200")
BLOCK_MARK = "Web Page Blocked"
MAX_TRIES = 3

log = setup_logging(SOURCE)


def article_key(url: str):
    """('YYYY-MM-DD-XXXX', scheme) for an article URL, else None."""
    u = url.replace("&amp;", "&")
    m = re.search(r"newsID=(\d{4}-\d\d-\d\d-\d{4})(?:&|$)", u)
    if m and "SF01_02_01" in u:
        return m.group(1), "old"
    m = re.search(r"index\.php\?([A-Za-z0-9+/=]{16,})$", u)
    if not m:
        return None
    try:
        d = base64.b64decode(m.group(1) + "==").decode("latin-1")
    except Exception:  # noqa: BLE001 - not base64
        return None
    m = re.match(r"12@(\d{4}-\d\d-\d\d-[A-Z]?\d{3})@", d)
    return (m.group(1), "new") if m else None


def load_index(since: str) -> dict:
    cache = RAW / SOURCE / f"cdx_from{since[:4]}.txt"
    body = fetch(CDX.format(y=since[:4]), min_delay=5, timeout=600, cache=cache)
    if not body:
        raise SystemExit("CDX listing failed")
    keys = collections.defaultdict(list)
    for line in body.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        k = article_key(parts[1])
        if k and k[0][:10] >= since:
            keys[k[0]].append((parts[0], parts[1], k[1]))
    for v in keys.values():
        v.sort(reverse=True)  # newest capture first
    return keys


def parse(html: str, key: str, scheme: str):
    """(title, text) or None."""
    if scheme == "new" or "news_Title" in html:
        mt = re.search(r'class="[^"]*news_Title[^"]*">(.*?)</div>', html, re.S)
        md = re.search(r'class="NewsDate">\s*(\d{4})\.(\d{1,2})\.(\d{1,2})\.', html)
        if not mt or not md:
            return None
        if f"{int(md.group(1)):04d}-{int(md.group(2)):02d}-{int(md.group(3)):02d}" != key[:10]:
            return None
        title = clean_html(mt.group(1))
        paras = re.findall(r'<p class="ArticleContent"[^>]*>(.*?)</p>', html, re.S)
    else:
        paras = re.findall(r'<p class="ArticleContent"[^>]*>(.*?)</p>', html, re.S)
        if not paras:
            return None
        title = clean_html(paras[0])
        paras = paras[1:]
    text = "\n".join(t for t in (clean_html(p) for p in paras) if t)
    text = re.sub(r"[０-９]", lambda m: chr(ord(m.group(0)) - 0xFEE0), text)  # full-width digits
    title = re.sub(r"[０-９]", lambda m: chr(ord(m.group(0)) - 0xFEE0), title)
    return (title, text) if title else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2021-01-01")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    st = State(SOURCE)
    keys = load_index(a.since)
    order = sorted(keys, reverse=True)
    log.info("%d article keys since %s (%d done)", len(order), a.since, len(st.data.get("done", [])))
    n = 0
    i = -1
    while i + 1 < len(order):
        i += 1
        key = order[i]
        if st.is_done(key):
            continue
        got = None
        neterr = 0
        for ts, orig, scheme in keys[key][:MAX_TRIES]:
            wb = f"https://web.archive.org/web/{ts}id_/{orig}"
            html = fetch(wb, min_delay=6, timeout=90)
            if html is None and fetch.last.get("status", 0) not in (404, 410):
                neterr += 1  # connection refused / timeout: Wayback is throttling us
                continue
            if not html or BLOCK_MARK in html:
                continue
            res = parse(html, key, scheme)
            if res:
                got = (res, orig, scheme)
                break
        if got:
            st["backoff"] = 0
            (title, text), orig, scheme = got
            if len(text) >= 80:  # photo-only items carry no text
                url = orig.replace("&amp;", "&")
                write_docs("KP", SOURCE, [dict(
                    id=make_id(SOURCE, key), country="KP", source=SOURCE, outlet="state_media",
                    org="Rodong Sinmun", lang="en", date=key[:10], url=url, title=title, speaker=None,
                    kind="article", text=text, via="wayback", fetched=now_iso(), wayback_ts=ts)])
                n += 1
            st.mark_done(key)
        elif neterr:
            wait = min(1800, 120 * 2 ** st.get("backoff", 0))
            st["backoff"] = st.get("backoff", 0) + 1
            log.warning("Wayback unreachable; sleeping %ds before retrying %s", wait, key)
            st.save()
            time.sleep(wait)
            lib._robots.pop("https://web.archive.org", None)  # re-read robots once Wayback answers again
            i -= 1  # retry the same key
            continue
        else:
            fails = st.get("fail", {})
            fails[key] = fails.get(key, 0) + 1
            st["fail"] = fails
            if fails[key] >= 2:
                st.mark_done(key)
        st.save()
        if n and n % 50 == 0:
            log.info("written %d (at %s)", n, key)
        if a.limit and n >= a.limit:
            break
    log.info("finished: %d new docs", n)


if __name__ == "__main__":
    main()
