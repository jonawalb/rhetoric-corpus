"""KCNA (Korean Central News Agency) English articles via Wayback Machine copies -> docs/KP/kp_kcna_en.jsonl.

kcna.kp is not collected directly: from the laptop's network it returns a "Web Page Blocked" filter page, and from
elsewhere it answers slowly or not at all; Wayback holds ~17,500 distinct English article captures dated 2022-2026
(articles back to 2019 were still online then). Two URL schemes:
  * www.kcna.kp/en/article/q/<32+ hex>.kcmsf  (to ~2026-06): div.article-main-title, div.content-wrapper,
    span.publish-time "www.kcna.kp (2025.12.14.)" or "(Juche113.3.9.)" (Juche year + 1911)
  * www.kcna.kp/en/article/detail/<32 hex>    (2026-06 ->): <h1 class="text-center ...">, <p> paragraphs, no page
    date: the date is the dateline "Pyongyang, September 10 (KCNA)" with the year of the capture (previous year if
    the dateline month/day lies after the capture date); `date_from: dateline`.

KCNA is the channel through which DPRK state organs publish their statements (Foreign Ministry spokesperson,
Kim Yo Jong, KPA General Staff, Defence Ministry, ...). Articles whose title (or opening sentence) marks them as such a
statement, answer or press release get outlet "official" and kind "statement" (`official_title()`); everything else is outlet
"state_media", kind "article". org is "KCNA" either way (the publisher); the issuing body is in the title.

Newest captures first; the CDX listing is refreshed weekly. The same article can appear under both URL schemes: a
second copy with the same date and title is skipped (`seen` in state). Pages that do not parse are retried once on a
later run, then marked done. A "Web Page Blocked" page (local web filter) stops the run without marking anything.

Run: uv run python collectors/kp_kcna_en.py [--since 2021-01-01] [--limit N]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from lib import RAW, State, clean_html, fetch, make_id, now_iso, setup_logging, write_docs  # noqa: E402

SOURCE = "kp_kcna_en"
CDX = ("https://web.archive.org/cdx/search/cdx?url=kcna.kp/en/article/&matchType=prefix&from={y}"
       "&collapse=urlkey&fl=timestamp,original&filter=statuscode:200")
CDX_MAX_AGE_S = 7 * 86400
BLOCK_MARK = "Web Page Blocked"
KEY_RE = re.compile(r"/en/article/(?:q/([0-9a-f]{32,})\.kcmsf|detail/([0-9a-f]{32}))$")
MONTHS = {m: i for i, m in enumerate("january february march april may june july august september october "
                                     "november december".split(), 1)}
OFFICIAL_RE = re.compile(
    r"\b(statement|press release|spokes(?:man|woman|person)|answers?\b.{0,80}\bquestion|"
    r"repl(?:y|ies)\b.{0,80}\bquestion|communique|notice of|report on .*(?:plenary|meeting|session)|"
    r"decision of|decree of|order of)\b", re.I)
# "Kim Yo Jong, Department Director of C.C., WPK, on Strategic Weapon Launch": an official speaking in their own name
OFFICER_TITLE_RE = re.compile(r"^[A-Z][^,]{2,60},\s.*\b(?:Director|Minister|Spokes\w+|Chief|Chairman|Vice|Secretary|"
                              r"Department|Ambassador|Representative|President|Premier|Commander)\b.*,\s+on\s")
OFFICIAL_TEXT_RE = re.compile(r"\b(?:released|issued|made public) (?:the following |a |an )?(?:press )?statement|"
                              r"\banswered (?:a |the )?question", re.I)

log = setup_logging(SOURCE)


def article_key(url: str):
    m = KEY_RE.search(url.split("?")[0])
    return (m.group(1) or m.group(2)) if m else None


def official_title(title: str, text: str = "") -> bool:
    """Statement / answer / press release of a state organ or official (published through KCNA)."""
    return bool(OFFICIAL_RE.search(title) or OFFICER_TITLE_RE.search(title) or OFFICIAL_TEXT_RE.search(text[:400]))


def load_index(since: str, st: State) -> dict:
    """{key: (timestamp, original)} of the newest listed capture per article."""
    cache = RAW / SOURCE / f"cdx_from{since[:4]}.txt"
    stamp = f"cdx_fetched_{since[:4]}"
    if cache.exists() and time.time() - st.get(stamp, 0) > CDX_MAX_AGE_S:
        cache.unlink()
    if not cache.exists():
        st[stamp] = time.time()
    body = fetch(CDX.format(y=since[:4]), min_delay=5, timeout=600, cache=cache)
    if not body:
        raise SystemExit("CDX listing failed")
    keys = {}
    for line in body.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        k = article_key(parts[1])
        if k and (k not in keys or parts[0] > keys[k][0]):
            keys[k] = (parts[0], parts[1])
    return keys


def _dateline_date(text: str, ts: str):
    m = re.match(r"\s*[A-Z][\w .'-]{1,40},\s*([A-Z][a-z]+)\s+(\d{1,2})\s*\(KCNA\)", text)
    if not m or m.group(1).lower() not in MONTHS:
        return None
    cap = date(int(ts[:4]), int(ts[4:6]), int(ts[6:8]))
    mo, d = MONTHS[m.group(1).lower()], int(m.group(2))
    y = cap.year if (mo, d) <= (cap.month, cap.day + 1) else cap.year - 1
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def parse(html: str, ts: str):
    """{"title", "text", "date", "date_from"} or None."""
    mt = re.search(r'<div class="article-main-title">(.*?)</div>', html, re.S)
    if mt:  # q/ layout
        mb = re.search(r'<div class="content-wrapper">(.*?)<span class=.publish-time', html, re.S)
        md = re.search(r"publish-time'?\"?>[^<(]*\((?:Juche)?(\d{2,4})\.(\d{1,2})\.(\d{1,2})\.?\)", html)
        if not mb or not md:
            return None
        y = int(md.group(1))
        y = y + 1911 if y < 1000 else y
        try:
            day = date(y, int(md.group(2)), int(md.group(3))).isoformat()
        except ValueError:
            return None
        title, body, date_from = clean_html(mt.group(1)), mb.group(1), "page"
    else:  # detail/ layout
        mt = re.search(r'<h1 class="text-center[^"]*">(.*?)</h1>', html, re.S)
        mb = re.search(r"<article>(.*?)</article>", html, re.S)
        if not mt or not mb:
            return None
        title, body, date_from = clean_html(mt.group(1)), mb.group(1), "dateline"
        body = re.sub(r"<h1\b.*?</h1>|<a\b.*?</a>", " ", body, flags=re.S)
        day = None
    paras = [clean_html(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", body, re.S)]
    text = "\n".join(p for p in paras if p)
    text = re.sub(r"\s*-0-\s*$", "", text).strip()
    if day is None:
        day = _dateline_date(text, ts)
    if not title or not text or not day:
        return None
    return {"title": title, "text": text, "date": day, "date_from": date_from}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2021-01-01")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    st = State(SOURCE)
    keys = load_index(a.since, st)
    st.save()
    order = sorted(keys, key=lambda k: keys[k][0], reverse=True)
    seen = set(st.get("seen", []))
    log.info("%d article keys (%d done)", len(order), len(st._done))
    n = neterr_run = 0
    for key in order:
        if st.is_done(key):
            continue
        ts, orig = keys[key]
        html = fetch(f"https://web.archive.org/web/{ts}id_/{orig}", min_delay=6, timeout=90)
        if html and BLOCK_MARK in html:
            raise SystemExit("local web filter blocks the Wayback copy (\"Web Page Blocked\"); stopping")
        if html is None and fetch.last.get("status", 0) not in (404, 410):
            neterr_run += 1
            if neterr_run >= 5:
                log.warning("Wayback unreachable (5 failures in a row); stopping, re-run to resume")
                break
            continue
        neterr_run = 0
        p = parse(html, ts) if html else None
        if p and p["date"] >= a.since:
            dt = f"{p['date']}|{p['title']}"
            if dt not in seen and len(p["text"]) >= 80:
                off = official_title(p["title"], p["text"])
                write_docs("KP", SOURCE, [dict(
                    id=make_id(SOURCE, key), country="KP", source=SOURCE, outlet="official" if off else "state_media",
                    org="KCNA", lang="en", date=p["date"], url=orig, title=p["title"], speaker=None,
                    kind="statement" if off else "article", text=p["text"], via="wayback", fetched=now_iso(),
                    wayback_ts=ts, date_from=p["date_from"])])
                seen.add(dt)
                n += 1
            st.mark_done(key)
        elif p:  # before --since
            st.mark_done(key)
        else:
            fails = st.get("fail", {})
            fails[key] = fails.get(key, 0) + 1
            st["fail"] = fails
            if fails[key] >= 2:
                st.mark_done(key)
        st["seen"] = sorted(seen)
        st.save()
        if n and n % 50 == 0:
            log.info("written %d", n)
        if a.limit and n >= a.limit:
            break
    log.info("finished: %d new docs", n)


if __name__ == "__main__":
    main()
