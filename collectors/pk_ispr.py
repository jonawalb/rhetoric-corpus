"""Pakistan ISPR press releases via Wayback Machine copies only.

www.ispr.gov.pk sits behind a Cloudflare browser challenge (HTTP 403 to scripted clients); we do not try to
pass it. The Wayback CDX index lists ~1,900 distinct press-release-detail.php?id=N pages captured
2020-2024 (captcha-token URLs ignored). Each capture is fetched once (raw `id_` copy), its dateline
("Rawalpindi - May 10, 2022") parsed, and kept if dated >= 2021-01-01. Coverage is partial: only what
the Wayback crawler happened to capture. Highest ids first (they are the newest).

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/pk_ispr.py [--max N]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import RAW, State, clean_html, fetch, make_id, setup_logging, write_docs  # noqa: E402

SOURCE = "pk_ispr"
START = "2021-01-01"
BLOCKED = ("Web Page Blocked", "Attention Required! | Cloudflare", "cf-chl")
log = setup_logging(SOURCE)


def cdx_ids(st: State) -> Dict[str, list]:
    if st.get("ids"):
        return st["ids"]
    q = {"url": "ispr.gov.pk/press-release-detail", "matchType": "prefix", "from": "2020",
         "fl": "timestamp,original", "filter": "statuscode:200", "output": "json"}
    body = fetch("https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(q), min_delay=5, timeout=300)
    rows = json.loads(body or "[]")[1:]
    ids: Dict[str, list] = {}
    for ts, orig in rows:
        if "captcha" in orig or "__cf" in orig:
            continue
        m = re.search(r"[?&]id=(\d+)$", orig)
        if m:
            ids[m.group(1)] = [ts, orig]   # latest capture wins (rows are time-ordered per URL)
    st["ids"] = ids
    st.save()
    log.info("CDX: %d distinct ids", len(ids))
    return ids


def parse(html: str) -> Optional[Dict]:
    md = re.search(r'<h4 class="media-date">\s*(.*?)\s*</h4>', html, re.S)
    if not md:
        return None
    dl = clean_html(md.group(1))
    m = re.search(r"([A-Z][a-z]+)\s+(\d{1,2}),\s*(\d{4})", dl)
    if not m:
        return None
    try:
        date = datetime.strptime(" ".join(m.groups()), "%B %d %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None
    pr = re.search(r'<h4 class="media-heading">\s*(.*?)\s*</h4>', html, re.S)
    i = html.find("<div class='content-text'>")
    if i < 0:
        i = html.find('<div class="content-text">')
    if i < 0:
        return None
    j = html.find("READERS CORNER", i)
    frag = html[i:j if j > 0 else None]
    k = re.search(r"-0-0-0", frag)
    text = clean_html(frag[:k.start()] if k else frag)
    text = re.sub(r'(?:\n?">)+\s*$', "", text).strip()
    lines = [x for x in text.split("\n") if x.strip()]
    title = lines[0] if lines and len(lines[0]) < 250 and lines[0].upper() == lines[0] else ""
    return {"date": date, "dateline": dl, "pr_no": clean_html(pr.group(1)) if pr else None,
            "title": title, "text": text}


def lang_of(text: str) -> str:
    """'ur' when Arabic-script letters outnumber Latin ones (ISPR issues some releases in Urdu)."""
    ar = len(re.findall(r"[\u0600-\u06FF]", text))
    return "ur" if ar > len(re.findall(r"[A-Za-z]", text)) else "en"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0)
    a = ap.parse_args()
    st = State(SOURCE)
    ids = cdx_ids(st)
    order = sorted(ids, key=lambda x: int(x), reverse=True)
    n = 0
    fails = 0
    for pid in order:
        if st.is_done(pid):
            continue
        ts, orig = ids[pid]
        wb = f"https://web.archive.org/web/{ts}id_/{orig}"
        html = fetch(wb, min_delay=5, cache=RAW / SOURCE / f"{pid}.html")
        if html and any(b in html[:5000] for b in BLOCKED):
            log.warning("block/challenge page in capture %s; skipped", wb)
            (RAW / SOURCE / f"{pid}.html").unlink(missing_ok=True)
            st.mark_done(pid)
            continue
        if not html:
            fails += 1
            if fails >= 5:   # Wayback refusing connections (rate limit): stop; re-run later to resume
                log.error("5 consecutive Wayback failures; stopping. Re-run to resume.")
                break
            continue
        fails = 0
        d = parse(html)
        st.mark_done(pid)
        if not d or len(d["text"]) < 80:
            continue
        if d["date"] < START:
            continue
        url = re.sub(r"^https?://(www\.)?", "https://www.", orig)
        row = {"id": make_id(SOURCE, pid), "country": "PK", "source": SOURCE, "outlet": "official", "org": "ISPR",
               "lang": lang_of(d["text"]), "date": d["date"], "url": url, "title": d["title"] or d["pr_no"], "speaker": None,
               "kind": "statement", "text": d["text"], "via": "wayback", "wayback": wb, "pr_no": d["pr_no"],
               "dateline": d["dateline"]}
        write_docs("PK", SOURCE, [row])
        n += 1
        if n % 10 == 0:
            st.save()
            log.info("%d new docs (id %s, %s)", n, pid, d["date"])
        if a.max and n >= a.max:
            break
    st.save()
    log.info("done: %d new docs", n)


if __name__ == "__main__":
    main()
