"""Shared helpers for the China (CN) collectors, on top of lib.py.

What lib.py does not cover:
  * robots_ok(): Google-style robots.txt matching (``*`` / ``$`` wildcards, longest match wins). Python's
    urllib.robotparser treats ``*`` literally; collectors call this, which also runs lib.robots_allowed.
  * sitemap_locs(): <loc> (+ optional <lastmod>) entries of a sitemap or sitemap index (gz handled by curl).
  * meta(): <meta name/property=...> lookup; first_date(): first YYYY-MM-DD / YYYY年M月D日 in a string.
  * balanced_div(): inner HTML of the <div> whose opening tag contains a marker.
  * Sink: buffered writer for one docs/CN/<source>.jsonl that skips ids already on disk and saves a small
    State every N documents (keeps state files small: done-ness = the doc id being present).
  * follow(): run a pass, sleep, repeat (for --follow).
  * cdx_urls(): Wayback CDX prefix enumeration (url list only; collapse=urlkey), paged and polite.

Nothing here caches article pages on disk. All network access goes through lib.fetch (robots + per-host delay).
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402

log = logging.getLogger("rhetoric-corpus.cn")
COUNTRY = "CN"

# ----------------------------------------------------------------------------------------- robots
_RULES: Dict[str, List[Tuple[bool, str]]] = {}


def _parse_robots(body: str) -> List[Tuple[bool, str]]:
    groups: List[Tuple[List[str], List[Tuple[bool, str]]]] = []
    agents: List[str] = []
    rules: List[Tuple[bool, str]] = []
    last_agent = False
    for raw in body.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (s.strip() for s in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if not last_agent and (agents or rules):
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(v.lower())
            last_agent = True
        elif k in ("allow", "disallow"):
            last_agent = False
            if v:
                rules.append((k == "allow", v))
        else:
            last_agent = False
    if agents or rules:
        groups.append((agents, rules))
    for ags, rls in groups:
        if any(a != "*" and a in "rhetoric-corpus-research" for a in ags):
            return rls
    return [r for ags, rls in groups if "*" in ags for r in rls]


def _pat(p: str) -> re.Pattern:
    end = p.endswith("$")
    return re.compile(re.escape(p[:-1] if end else p).replace(r"\*", ".*") + ("$" if end else ""))


def robots_ok(url: str) -> bool:
    """Wildcard-aware robots.txt check, in addition to lib.robots_allowed (which runs first)."""
    if not lib.robots_allowed(url):
        return False
    p = urllib.parse.urlsplit(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _RULES:
        body = ""
        try:
            body = lib.fetch_meta(base + "/robots.txt", retries=1, timeout=30)["body"] or ""
        except Exception as e:  # noqa: BLE001 - unreadable robots: lib.robots_allowed already decided
            log.info("robots.txt unreadable for %s: %s", base, e)
        _RULES[base] = [] if "<html" in body.lower()[:500] else _parse_robots(body)
    path = (p.path or "/") + (("?" + p.query) if p.query else "")
    best: Optional[Tuple[int, bool]] = None
    for allow, pat in _RULES[base]:
        if _pat(pat).match(path):
            if best is None or len(pat) > best[0] or (len(pat) == best[0] and allow):
                best = (len(pat), allow)
    return True if best is None else best[1]


def get(url: str, delay: float, wayback: bool = False, **kw) -> Optional[str]:
    """lib.fetch after the wildcard robots check; None if disallowed or failed."""
    if not robots_ok(url):
        log.warning("robots.txt disallows %s", url)
        return None
    return lib.fetch(url, min_delay=delay, use_wayback_fallback=wayback, **kw)


# ----------------------------------------------------------------------------------------- parsing
def sitemap_locs(xml: str) -> List[Tuple[str, Optional[str]]]:
    """[(loc, lastmod or None)] from a sitemap / sitemap index."""
    out = []
    for block in re.findall(r"<(?:url|sitemap)>(.*?)</(?:url|sitemap)>", xml, re.S | re.I):
        loc = re.search(r"<loc>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</loc>", block, re.S | re.I)
        if not loc:
            continue
        lm = re.search(r"<(?:lastmod|news:publication_date)>\s*(.*?)\s*</", block, re.S | re.I)
        out.append((lib.clean_html(loc.group(1)).strip(), lm.group(1).strip() if lm else None))
    if not out:  # bare <loc> list
        out = [(lib.clean_html(u).strip(), None) for u in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml, re.S | re.I)]
    return out


def meta(html: str, name: str) -> Optional[str]:
    """content of <meta name|property|itemprop="name" content="..."> (either attribute order)."""
    n = re.escape(name)
    for pat in (rf'<meta[^>]+(?:name|property|itemprop)=["\']{n}["\'][^>]*?content=["\'](.*?)["\']',
                rf'<meta[^>]+content=["\'](.*?)["\'][^>]*?(?:name|property|itemprop)=["\']{n}["\']'):
        m = re.search(pat, html, re.I | re.S)
        if m:
            return lib.clean_html(m.group(1)).strip() or None
    return None


def title_tag(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return lib.clean_html(m.group(1)).strip() if m else ""


_D_NUM = re.compile(r"(?<!\d)((?:19|20)\d\d)[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
_D_CJK = re.compile(r"((?:19|20)\d\d)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_D_COMPACT = re.compile(r"(?<!\d)((?:19|20)\d\d)(\d\d)(\d\d)(?!\d)")


def ymd(y: int, m: int, d: int) -> Optional[str]:
    import datetime as dt
    try:
        return dt.date(int(y), int(m), int(d)).isoformat()
    except (TypeError, ValueError):
        return None


def first_date(s: Optional[str], compact: bool = False) -> Optional[str]:
    """First valid date in `s` ('2024-05-01', '2024/5/1', '2024年5月1日'; '20240501' when compact=True)."""
    if not s:
        return None
    pats = [_D_NUM, _D_CJK] + ([_D_COMPACT] if compact else [])
    best = None
    for p in pats:
        for m in p.finditer(s):
            d = ymd(*m.groups())
            if d and (best is None or m.start() < best[0]):
                best = (m.start(), d)
                break
    return best[1] if best else None


def balanced_div(html: str, marker: str) -> Optional[str]:
    """Inner HTML of the first <div> whose opening tag contains `marker`, or None."""
    i = html.find(marker)
    if i < 0:
        return None
    start = html.rfind("<div", 0, i)
    if start < 0:
        return None
    depth = 0
    for m in re.finditer(r"<(/?)div\b[^>]*>", html[start:], re.I):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return html[html.find(">", start) + 1:start + m.start()]
    return html[html.find(">", start) + 1:]


def paragraphs(fragment: str, min_len: int = 1) -> str:
    """Text of the <p> elements in `fragment` (one per line); falls back to clean_html of the whole."""
    ps = [lib.clean_html(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", fragment, re.S | re.I)]
    ps = [p for p in ps if len(p) >= min_len]
    return "\n".join(ps) if ps else lib.clean_html(fragment)


# ----------------------------------------------------------------------------------------- writing
class Sink:
    """Buffered writer for docs/CN/<source>.jsonl. `has(id)` is true for ids on disk or buffered."""

    def __init__(self, source: str, state: Optional[lib.State] = None, flush_every: int = 20):
        self.source = source
        self.state = state
        self.flush_every = flush_every
        self.ids = lib.existing_ids(lib.docs_path(COUNTRY, source))
        self.buf: List[Dict] = []
        self.added = 0

    def has(self, doc_id: str) -> bool:
        return doc_id in self.ids

    def add(self, row: Dict) -> bool:
        row.setdefault("country", COUNTRY)
        row.setdefault("source", self.source)
        if row["id"] in self.ids:
            return False
        lib.validate(row)  # raise early, before buffering
        self.ids.add(row["id"])
        self.buf.append(row)
        if len(self.buf) >= self.flush_every:
            self.flush()
        return True

    def flush(self) -> None:
        if self.buf:
            added, total = lib.write_docs(COUNTRY, self.source, self.buf)
            self.added += added
            log.info("%s: +%d (total %d)", self.source, added, total)
            self.buf = []
        if self.state is not None:
            self.state.save()


def follow(pass_fn: Callable[[], int], every_s: int, name: str) -> None:
    """Run pass_fn forever, sleeping every_s between passes (errors are logged, not fatal)."""
    while True:
        try:
            n = pass_fn()
            log.info("%s pass done: %d new", name, n)
        except Exception:  # noqa: BLE001 - keep a follow loop alive across one bad pass
            log.exception("%s pass failed", name)
        time.sleep(every_s)


# ----------------------------------------------------------------------------------------- wayback
def cdx_urls(prefix: str, url_re: Optional[str] = None, frm: Optional[str] = None, to: Optional[str] = None,
             page_size: int = 5000) -> Iterator[Tuple[str, str]]:
    """Yield (original_url, timestamp) of HTTP-200 Wayback captures under `prefix` (collapse=urlkey), oldest
    first, optionally filtered by regex `url_re`. Uses resumeKey paging; one request per page at the shared
    Wayback gap (lib.SHARED_HOST_GAP)."""
    rx = re.compile(url_re) if url_re else None
    resume = None
    while True:
        q = {"url": prefix, "matchType": "prefix", "output": "json", "fl": "original,timestamp",
             "filter": "statuscode:200", "collapse": "urlkey", "limit": str(page_size), "showResumeKey": "true"}
        if frm:
            q["from"] = frm
        if to:
            q["to"] = to
        if resume:
            q["resumeKey"] = resume
        url = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(q)
        body = None
        for attempt in range(4):
            body = lib.fetch(url, min_delay=5, timeout=180, retries=2)
            if body is not None:
                break
            time.sleep(60 * (attempt + 1))
        if body is None:
            log.warning("CDX failed for %s (resume=%s)", prefix, resume)
            return
        try:
            rows = json.loads(body or "[]")
        except json.JSONDecodeError:
            log.warning("CDX non-JSON for %s", prefix)
            return
        resume = None
        if rows and rows[0] == ["original", "timestamp"]:
            rows = rows[1:]
        if len(rows) >= 2 and rows[-2] == [] and len(rows[-1]) == 1:
            resume = rows[-1][0]
            rows = rows[:-2]
        for r in rows:
            if len(r) == 2 and (rx is None or rx.search(r[0])):
                yield r[0], r[1]
        if not resume:
            return


def wayback_raw(original: str, ts: str) -> str:
    return f"https://web.archive.org/web/{ts}id_/{original}"


__all__ = ["COUNTRY", "robots_ok", "get", "sitemap_locs", "meta", "title_tag", "ymd", "first_date", "balanced_div",
           "paragraphs", "Sink", "follow", "cdx_urls", "wayback_raw"]
