"""Iran Ministry of Foreign Affairs, English site (en.mfa.ir/portal/newsview/<id>), via Wayback Machine copies.

The live site sits behind an ArvanCloud cookie gate (every first request, robots.txt included, answers 307 to
itself + a session cookie), which lib does not pass and which is not evaded. Item URLs come from the Wayback
CDX index of en.mfa.ir/portal/newsview/ (fresh listing cached in raw/ir_mfa_en/, plus the 2026-09-29 listing
of the earlier collector copied to raw/ir_mfa_en/cdx_seed.txt). The newest capture of each id is fetched
(raw `id_` copy). Ids already in docs/IR/iran_mfa_en.jsonl (the imported 2021-> live crawl) are skipped, so
this source mainly adds the pre-2021 portal items. Output goes to its own file, ir_mfa_en (import_existing.py
rewrites iran_mfa_en).

Page layout (checked on cached real pages of the old collector, ~/.cache/rhetoric-global/iran_mfa/items):
div.news-text-full (h4 = title, then the text), span.nv-info "2020/10/18 - 09:29" = date, breadcrumb = category
(Statements / Spokesman / Minister ...). kind: briefing for spokesman press-conference items, statement for the
Statements category, else article.

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/ir_mfa.py [--limit N]
(Persian mfa.ir uses the same CMS but is not enabled: its pages could not be checked on real captures yet.)
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from ir_common import balanced_div, write_docs_fast  # noqa: E402

SRC = "ir_mfa_en"
CDX = ("https://web.archive.org/cdx/search/cdx?url=en.mfa.ir/portal/newsview/&matchType=prefix&collapse=urlkey"
       "&fl=timestamp,original&filter=statuscode:200")
MIN_TEXT = 100
log = lib.setup_logging(SRC)


def load_index() -> Dict[str, Tuple[str, str]]:
    """id -> (newest capture ts, original URL)."""
    rows = []
    seed = lib.RAW / SRC / "cdx_seed.txt"        # "<original> <timestamp> <length>"
    if seed.exists():
        for line in seed.read_text("utf-8").splitlines():
            p = line.split()
            if len(p) >= 2:
                rows.append((p[1], p[0]))
    body = lib.fetch(CDX, min_delay=5, timeout=600, cache=lib.RAW / SRC / "cdx_newsview.txt")
    if body:
        rows += [tuple(l.split()[:2]) for l in body.splitlines() if len(l.split()) >= 2]
    else:
        log.warning("fresh CDX listing unavailable; using the seed listing only (%d rows)", len(rows))
    out: Dict[str, Tuple[str, str]] = {}
    for ts, orig in rows:
        m = re.search(r"(?i)/newsview/(\d+)", orig)
        if m and (m.group(1) not in out or ts > out[m.group(1)][0]):
            out[m.group(1)] = (ts, orig)
    return out


_ROBOTS: Dict[str, object] = {}


def archived_robots_allow(orig: str) -> bool:
    """The live robots.txt cannot be read (cookie gate; lib then disallows the site), so the site's own rules
    are taken from the newest Wayback capture of its robots.txt. No readable capture -> wait and retry
    (never "allowed by default"); a capture that disallows the path -> False."""
    import urllib.parse
    import urllib.robotparser
    p = urllib.parse.urlsplit(orig)
    host = p.netloc.lower()
    tries = 0
    while host not in _ROBOTS:
        wb = lib.wayback_latest(f"{host}/robots.txt")
        body = lib.fetch(wb, min_delay=6, timeout=60) if wb else None
        rp = urllib.robotparser.RobotFileParser()
        if body is not None:
            rp.parse([] if "<html" in body.lower()[:500] else body.splitlines())
            _ROBOTS[host] = rp
            log.info("archived robots.txt for %s (%s): %d bytes", host, wb, len(body))
        elif tries >= 6:  # no capture found (or Wayback down for an hour): stay conservative
            log.warning("no readable Wayback capture of %s/robots.txt; exiting (nothing marked done)", host)
            raise SystemExit(1)
        else:
            tries += 1
            log.warning("archived robots.txt for %s unreadable now; retry in 10 min", host)
            time.sleep(600)
            lib._robots.pop("https://web.archive.org", None)
    return _ROBOTS[host].can_fetch(lib.UA, f"https://{host}{p.path}")  # type: ignore[attr-defined]


def parse(html: str) -> Optional[Dict]:
    inner = balanced_div(html, 'class="news-text-full"')
    md = re.search(r'<span class="nv-info">\s*((?:19|20)\d\d)/(\d\d)/(\d\d)', html)
    if not inner or not md:
        return None
    mt = re.search(r"<h4[^>]*>(.*?)</h4>", inner, re.S)
    title = lib.clean_html(mt.group(1)) if mt else ""
    body = re.sub(r"<h6[^>]*>.*?</h6>|<h4[^>]*>.*?</h4>", " ", inner, count=2, flags=re.S)
    text = lib.clean_html(re.sub(r"\s+", " ", body).replace("</p>", "</p>\n"))
    crumbs = re.findall(r'<li class="breadcrumb-item">\s*<a[^>]*>(.*?)</a>', html, re.S)
    cat = lib.clean_html(crumbs[-1]) if crumbs else None
    return {"title": title, "date": "-".join(md.groups()), "text": text, "category": cat}


def kind_of(cat: Optional[str], title: str) -> str:
    s = f"{cat or ''} {title}"
    if re.search(r"(?i)spokes(?:man|person).{0,40}(?:press|weekly|briefing|conference)|press (?:conference|briefing)", s):
        return "briefing"
    if re.search(r"(?i)statement", cat or ""):
        return "statement"
    return "article"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    st = lib.State(SRC)
    have = {i.split(":")[1] for i in lib.existing_ids(lib.docs_path("IR", "iran_mfa_en"))}  # file + sealed ids
    index = load_index()
    order = sorted((k for k in index if k not in have), key=int, reverse=True)
    log.info("%d ids in Wayback, %d not in iran_mfa_en (%d done)", len(index), len(order),
             sum(st.is_done(k) for k in order))
    n = 0
    for i, nid in enumerate(order):
        if st.is_done(nid):
            continue
        ts, orig = index[nid]
        if not archived_robots_allow(orig):
            log.warning("archived robots.txt of the original site disallows %s; skipped", orig)
            st.mark_done(nid)
            continue
        html = lib.fetch(f"https://web.archive.org/web/{ts}id_/{orig}", min_delay=6, timeout=90)
        if html is None and lib.fetch.last.get("status", 0) not in (404, 410):
            log.warning("Wayback unreachable; sleeping 10 min before retrying %s", nid)
            st.save()
            time.sleep(600)
            lib._robots.pop("https://web.archive.org", None)
            continue   # left undone; picked up on the next run
        st.mark_done(nid)
        art = parse(html) if html else None
        if art and art["title"] and len(art["text"]) >= MIN_TEXT:
            write_docs_fast("IR", SRC, [{
                "id": lib.make_id(SRC, nid), "country": "IR", "source": SRC, "outlet": "official", "org": "MFA",
                "lang": "en", "date": art["date"], "url": f"https://en.mfa.ir/portal/newsview/{nid}",
                "title": art["title"], "speaker": None, "kind": kind_of(art["category"], art["title"]),
                "text": art["text"], "via": "wayback", "fetched": lib.now_iso(), "wayback": ts,
                "category": art["category"]}])
            n += 1
        elif html:
            log.info("unparsed/short capture %s %s", ts, orig)
        if i % 20 == 0:
            st.save()
        if a.limit and n >= a.limit:
            break
    st.save()
    log.info("finished: %d new docs", n)


if __name__ == "__main__":
    main()
