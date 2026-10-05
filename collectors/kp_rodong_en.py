"""Rodong Sinmun (English edition) via Wayback Machine copies -> docs/KP/kp_rodong_en.jsonl.

rodong.rep.kp is not reachable from here directly, and kcna.kp (plus Wayback copies of it) is refused
by the local network web filter, so this collector reads only Wayback captures of rodong.rep.kp/en.
Two URL schemes exist:
  * old site (to ~2022): index.php?strPageID=SF01_02_01&newsID=YYYY-MM-DD-NNNN
  * new site (2022->):  index.php?<base64 of "12@YYYY-MM-DD-[A-Z]NNN@cat@pos@@0@n">
Each article key (date + serial) is captured under many URLs; we try captures until one renders the
article (title + date match), newest articles first. Publication date comes from the article key and
is cross-checked against the page date. Three page layouts are parsed:
  * old site:                 <p class="ArticleContent"> paragraphs, first one = title, no page date
  * new site, 2022 -> ~2024:  div.news_Title + span.NewsDate "2023.3.13." + p.ArticleContent
  * new site, ~2025 ->:       div#article-date "Feb. 19, 2026 Thursday" + p.TitleP + p.TextP/MarkP/WriterP
Until 2026-10-05 only the first two were parsed, so every 2025-26 key (taken first, newest first) failed and was
marked done after two tries: no document was ever written. State from that parser (no "parser" key) is reset once:
done keys are kept only if their document exists. The CDX listing is refreshed weekly (it was cached forever).

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
PARSER = 2  # bump when parse() learns a layout, so keys failed by the old parser are retried
CDX_MAX_AGE_S = 7 * 86400
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
TEXT_P = re.compile(r'<p class="(TitleP|RevoTitleP|HeadP|SubTitleP|TextP|MarkP|WriterP)"[^>]*>(.*?)</p>', re.S)

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


def load_index(since: str, st: State) -> dict:
    cache = RAW / SOURCE / f"cdx_from{since[:4]}.txt"
    stamp = f"cdx_fetched_{since[:4]}"
    if cache.exists() and time.time() - st.get(stamp, 0) > CDX_MAX_AGE_S:
        cache.unlink()  # refresh: new captures appear every week
    if not cache.exists():
        st[stamp] = time.time()
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


def _digits(s: str) -> str:
    return re.sub(r"[０-９]", lambda m: chr(ord(m.group(0)) - 0xFEE0), s)  # full-width digits


def parse(html: str, key: str, scheme: str):
    """(title, text) or None."""
    if 'class="TitleP"' in html:  # 2025-> layout
        md = re.search(r'id="article-date">\s*([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),\s*(\d{4})', html)
        if not md or md.group(1).lower() not in MONTHS:
            return None
        if f"{md.group(3)}-{MONTHS[md.group(1).lower()]:02d}-{int(md.group(2)):02d}" != key[:10]:
            return None
        title, paras = "", []
        for cls, frag in TEXT_P.findall(html):
            t = clean_html(frag)
            if cls == "TitleP" and not title:
                title = t
            elif t:
                paras.append(t)
        title, text = _digits(title), _digits("\n".join(paras))
        return (title, text) if title else None
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
    text = _digits("\n".join(t for t in (clean_html(p) for p in paras) if t))
    title = _digits(title)
    return (title, text) if title else None


def reset_old_parser_state(st: State) -> None:
    """Keys marked done by an older parser without a document are retried (they failed on an unknown layout)."""
    if st.get("parser") == PARSER:
        return
    have = lib.existing_ids(lib.docs_path("KP", SOURCE))
    keep = {k for k in st._done if make_id(SOURCE, k) in have}
    log.info("parser %s -> %s: keeping %d of %d done keys, clearing %d failures", st.get("parser"), PARSER,
             len(keep), len(st._done), len(st.get("fail", {})))
    st._done = keep
    st["fail"] = {}
    st["backoff"] = 0
    st["parser"] = PARSER


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2021-01-01")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    st = State(SOURCE)
    reset_old_parser_state(st)
    keys = load_index(a.since, st)
    st.save()
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
