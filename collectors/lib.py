"""Shared helpers for rhetoric-corpus collectors: polite fetching, resumable state, document writing.

Typical collector:

    from lib import fetch, write_docs, make_id, clean_html, State, now_iso

    st = State("mid_ru")                       # state/mid_ru.json, survives restarts
    html = fetch(url, min_delay=10, use_wayback_fallback=True)    # str, or None if robots/404/failed
    if html:
        write_docs("RU", "mid_ru", [{"id": make_id("mid_ru", url), "country": "RU", "source": "mid_ru",
                    "outlet": "official", "org": "MFA", "lang": "ru", "date": "2024-11-29", "url": url,
                    "title": "...", "speaker": "Zakharova", "kind": "briefing", "text": "...",
                    "via": fetch.last["via"]}])
        st.mark_done(url); st.save()

Rules this module enforces:
  * robots.txt is checked (urllib.robotparser) before the first request to each path; disallowed URLs
    return None (Wayback copies of a disallowed URL are not fetched either).
  * Per-host start-to-start delay >= max(min_delay, HOST_DELAY[host], 4 s), enforced across threads.
  * Retries with exponential backoff on network errors / 429 / 5xx; 404/410 give up at once.
  * Descriptive User-Agent; no cookies beyond the session, no CAPTCHA handling, no login.
  * Wayback fallback (optional) uses the CDX API for the latest 200 capture and fetches the raw
    `id_` copy, at >= 5 s spacing.

Run with: uv run --project ~/Projects/rhetoric-corpus python collectors/<source>.py
"""
from __future__ import annotations

import _thread
import contextlib
import fcntl
import hashlib
import html as _html
import json
import logging
import os
import re
import signal
import subprocess
import threading
import urllib.error
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

ROOT = Path(__file__).resolve().parent.parent          # ~/Projects/rhetoric-corpus
DOCS = ROOT / "docs"
RAW = ROOT / "raw"
STATE = ROOT / "state"
UA = "Mozilla/5.0 (compatible; rhetoric-corpus-research/1.0; academic text research; +https://jwalberg.com/)"
MIN_DELAY = 4.0
# Hosts known to rate-limit get longer gaps (seconds between request starts).
HOST_DELAY: Dict[str, float] = {"en.kremlin.ru": 10.0, "kremlin.ru": 10.0, "web.archive.org": 5.0,
                                "en.mfa.ir": 4.0, "mid.ru": 10.0}
REQUIRED = ("id", "country", "source", "lang", "date", "url", "text")
FIELDS = ("id", "country", "source", "outlet", "org", "lang", "date", "url", "title", "speaker", "kind",
          "text", "via", "fetched")

logger = logging.getLogger("rhetoric-corpus")
_last_start: Dict[str, float] = {}
_lock = threading.Lock()
_robots: Dict[str, urllib.robotparser.RobotFileParser] = {}
# Sites whose robots.txt could not be read because of a network-level failure (timeout, refused, 5xx/429):
# base -> time after which we try again. Until then the site counts as disallowed, but the verdict is not
# cached forever, so an outage (e.g. Wayback refusing connections) does not stall a long-running collector.
_robots_retry: Dict[str, float] = {}
ROBOTS_RETRY_S = 900
TRANSIENT_BACKOFF_S = 300

# Graceful stop for time-boxed runs (scripts/ci_collect.py sends SIGTERM at the deadline): SIGTERM raises
# SystemExit in the main thread, but never while write_docs or State.save is writing, so a stop never leaves a
# half-written JSONL line or state file. A signal that arrives inside such a section is delivered when it ends.
_crit_lock = threading.Lock()
_crit_depth = 0
_term_pending = False


def _on_sigterm(signum, frame):  # noqa: ARG001
    global _term_pending
    with _crit_lock:
        if _crit_depth:
            _term_pending = True
            return
    raise SystemExit(128 + signum)


@contextlib.contextmanager
def _critical():
    global _crit_depth, _term_pending
    with _crit_lock:
        _crit_depth += 1
    try:
        yield
    finally:
        with _crit_lock:
            _crit_depth -= 1
            fire = _term_pending and not _crit_depth
            if fire:
                _term_pending = False
        if fire:
            if threading.current_thread() is threading.main_thread():
                raise SystemExit(128 + signal.SIGTERM)
            _thread.interrupt_main(signal.SIGTERM)


if threading.current_thread() is threading.main_thread():
    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except ValueError:  # not the main interpreter thread
        pass


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------------------------- robots
def _ssl_ctx():
    """TLS context with a real CA bundle (certifi if installed; python.org builds on macOS ship none)."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()

def robots_allowed(url: str) -> bool:
    """True if the site's robots.txt lets our User-Agent fetch `url`.

    A missing robots.txt (404/410) means no rules. Any other failure to read it (TLS error, timeout, 5xx,
    401/403) is treated conservatively as "disallow everything" for that site, so a broken check can never
    turn into permission to crawl."""
    p = urllib.parse.urlsplit(url)
    base = f"{p.scheme}://{p.netloc}"
    if base in _robots_retry:
        if time.time() < _robots_retry[base]:
            return False
        del _robots_retry[base]
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        lines: list = []
        try:
            req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx()) as r:
                    body = r.read().decode("utf-8", "ignore")
            except urllib.error.URLError as e:
                if "CERTIFICATE_VERIFY_FAILED" not in str(e):
                    raise
                # e.g. mid.ru uses a Russian state CA that no standard trust store carries. Reading the rules
                # without verification only ever makes us MORE restrictive, so retry unverified for robots.txt.
                import ssl
                with urllib.request.urlopen(req, timeout=30, context=ssl._create_unverified_context()) as r:
                    body = r.read().decode("utf-8", "ignore")
            # Sites without robots.txt sometimes answer with an HTML page: treat as no rules.
            lines = [] if "<html" in body.lower()[:500] else body.splitlines()
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                logger.info("no robots.txt for %s (%s)", base, e.code)
            elif e.code == 429 or e.code >= 500:
                logger.warning("robots.txt for %s unavailable (HTTP %s): treating site as disallowed for %ds",
                               base, e.code, ROBOTS_RETRY_S)
                _robots_retry[base] = time.time() + ROBOTS_RETRY_S
                return False
            else:
                logger.warning("robots.txt for %s unreadable (HTTP %s): disallowing the site", base, e.code)
                lines = ["User-agent: *", "Disallow: /"]
        except Exception as e:  # network failure: be conservative now, but re-check later
            logger.warning("robots.txt for %s unreachable (%s): treating site as disallowed for %ds",
                           base, e, ROBOTS_RETRY_S)
            _robots_retry[base] = time.time() + ROBOTS_RETRY_S
            return False
        rp.parse(lines)
        _robots[base] = rp
    return _robots[base].can_fetch(UA, url)


def crawl_delay(url: str) -> Optional[float]:
    """Crawl-delay from robots.txt for our UA, if the site sets one."""
    robots_allowed(url)
    p = urllib.parse.urlsplit(url)
    rp = _robots.get(f"{p.scheme}://{p.netloc}")
    d = rp.crawl_delay(UA) if rp else None
    return float(d) if d else None


# ---------------------------------------------------------------------------------------------- fetch
# Hosts that several collector processes hit at once: their start-to-start gap is enforced across processes
# through a locked slot file, so the combined request rate stays at one per gap (Wayback rate-limits by IP).
SHARED_HOST_GAP: Dict[str, float] = {"web.archive.org": 4.0}


def _shared_slot(host: str, gap: float) -> float:
    """Reserve the next start time for `host` across all processes; returns it."""
    path = STATE / f".slot_{host}"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            last = float(f.read().strip() or 0)
            start = max(time.time(), last + gap)
            f.seek(0)
            f.truncate()
            f.write(f"{start:.3f}")
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return start


def _wait(host: str, min_delay: float) -> None:
    gap = max(MIN_DELAY, min_delay, HOST_DELAY.get(host, 0.0))
    with _lock:
        start = max(time.time(), _last_start.get(host, 0.0) + gap)
        _last_start[host] = start
    if host in SHARED_HOST_GAP:
        start = max(start, _shared_slot(host, SHARED_HOST_GAP[host]))
    time.sleep(max(0.0, start - time.time()))


def _curl(url: str, binary: bool, timeout: int, headers: Optional[Dict[str, str]]) -> tuple:
    """(status_code, body_bytes, final_url). Uses curl for a hard total deadline."""
    cmd = ["curl", "-sS", "-L", "--compressed", "--max-time", str(timeout), "-A", UA,
           "-w", "\n%{http_code} %{url_effective}"]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    res = subprocess.run(cmd + [url], capture_output=True, timeout=timeout + 15)
    out = res.stdout
    body, _, tail = out.rpartition(b"\n")
    code, _, final = tail.decode("latin-1").partition(" ")
    if res.returncode != 0:
        raise IOError(f"curl rc={res.returncode} {res.stderr.decode('utf-8', 'ignore')[:200]}")
    return int(code or 0), body, final or url


def wayback_latest(url: str, before: Optional[str] = None) -> Optional[str]:
    """Raw (`id_`) URL of the latest HTTP-200 Wayback capture of `url`, or None.

    before: optional 'YYYYMMDD' upper bound on the capture timestamp."""
    q = {"url": url, "output": "json", "filter": "statuscode:200", "limit": "-1", "fl": "timestamp,original"}
    if before:
        q["to"] = before
    cdx = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(q)
    _wait("web.archive.org", 5.0)
    try:
        code, body, _ = _curl(cdx, False, 60, None)
        rows = json.loads(body.decode("utf-8", "ignore") or "[]") if code == 200 else []
    except Exception as e:
        logger.warning("CDX lookup failed for %s: %s", url, e)
        return None
    if len(rows) < 2:
        return None
    ts, orig = rows[-1][0], rows[-1][1]
    return f"https://web.archive.org/web/{ts}id_/{orig}"


def fetch_meta(url: str, min_delay: float = MIN_DELAY, use_wayback_fallback: bool = False, binary: bool = False,
               retries: int = 3, timeout: int = 60, headers: Optional[Dict[str, str]] = None,
               cache: Optional[Path] = None) -> Dict:
    """Fetch politely. Returns {"body": str|bytes|None, "status": int, "url": final_url, "via": "direct"|"wayback"|"cache"|None}.

    cache: optional file path; when it exists (non-empty) it is returned without a request, and a
    successful fetch is written there (put caches under RAW / <source> / ...)."""
    if cache is not None and cache.exists() and cache.stat().st_size > 0:
        data = cache.read_bytes()
        return {"body": data if binary else data.decode("utf-8", "ignore"), "status": 200, "url": url, "via": "cache"}
    if not robots_allowed(url):
        p = urllib.parse.urlsplit(url)
        if f"{p.scheme}://{p.netloc}" in _robots_retry:  # site unreachable: back off instead of racing the queue
            logger.warning("%s unreachable; backing off %ds (not marked done) %s", p.netloc, TRANSIENT_BACKOFF_S, url)
            time.sleep(TRANSIENT_BACKOFF_S)
        else:
            logger.warning("robots.txt disallows %s", url)
        return {"body": None, "status": 0, "url": url, "via": None}
    host = urllib.parse.urlsplit(url).netloc
    status = 0
    for attempt in range(retries):
        _wait(host, min_delay)
        try:
            status, body, final = _curl(url, binary, timeout, headers)
        except Exception as e:  # network error: back off and retry
            logger.warning("fetch error (%d/%d) %s: %s", attempt + 1, retries, url, e)
            time.sleep(4 * 2 ** attempt)
            continue
        if status == 200:
            if cache is not None:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(body)
            return {"body": body if binary else body.decode("utf-8", "ignore"), "status": 200, "url": final, "via": "direct"}
        if status in (404, 410):
            break
        logger.warning("HTTP %s (%d/%d) %s", status, attempt + 1, retries, url)
        if status in (401, 403):  # walled / bot check: do not hammer
            break
        time.sleep(4 * 2 ** attempt)
    if use_wayback_fallback:
        wb = wayback_latest(url)
        if wb:
            for attempt in range(retries):
                _wait("web.archive.org", 5.0)
                try:
                    code, body, _ = _curl(wb, binary, timeout, None)
                except Exception as e:
                    logger.warning("wayback error (%d/%d) %s: %s", attempt + 1, retries, wb, e)
                    time.sleep(5 * 2 ** attempt)
                    continue
                if code == 200:
                    if cache is not None:
                        cache.parent.mkdir(parents=True, exist_ok=True)
                        cache.write_bytes(body)
                    return {"body": body if binary else body.decode("utf-8", "ignore"), "status": 200, "url": wb,
                            "via": "wayback"}
                time.sleep(5 * 2 ** attempt)
    return {"body": None, "status": status, "url": url, "via": None}


def fetch(url: str, min_delay: float = MIN_DELAY, use_wayback_fallback: bool = False, binary: bool = False,
          **kw) -> Optional[Union[str, bytes]]:
    """Body of `url` as text (or bytes with binary=True), or None. fetch.last holds the full result dict."""
    res = fetch_meta(url, min_delay, use_wayback_fallback, binary, **kw)
    fetch.last = res  # type: ignore[attr-defined]
    return res["body"]


fetch.last = {}  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------------------------- text
def clean_html(fragment: str) -> str:
    """HTML fragment -> plain text; block tags become newlines, runs of spaces collapse."""
    s = re.sub(r"<(script|style|noscript)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</(p|div|li|h[1-6]|tr|blockquote)>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s).replace("\xa0", " ").replace("　", " ")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r" *\n[ \n]*", "\n", s)
    return s.strip()


def make_id(source: str, key: str) -> str:
    """Stable document id: '<source>:<key>' for a native id, or '<source>:<sha1(url)[:16]>' for a URL."""
    if key.startswith(("http://", "https://")):
        key = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"{source}:{key}"


# ---------------------------------------------------------------------------------------------- docs
def docs_path(country: str, source: str) -> Path:
    return DOCS / country.upper() / f"{source}.jsonl"


def read_docs(path: Path) -> Iterable[Dict]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def existing_ids(path: Path) -> set:
    if not path.exists():
        return set()
    ids = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            m = re.match(r'\{"id": "((?:[^"\\]|\\.)*)"', line)
            if m:
                ids.add(json.loads(f'"{m.group(1)}"'))
            elif line.strip():
                ids.add(json.loads(line)["id"])
    return ids


def validate(row: Dict) -> Dict:
    """Fill defaults and check required fields; returns a new dict in canonical key order."""
    miss = [k for k in REQUIRED if not row.get(k)]
    if miss:
        raise ValueError(f"document {row.get('id')!r} lacks {miss}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["date"]):
        raise ValueError(f"document {row['id']!r}: date must be YYYY-MM-DD, got {row['date']!r}")
    if not row["id"].startswith(row["source"] + ":"):
        raise ValueError(f"document id {row['id']!r} must start with '{row['source']}:'")
    out = {k: row.get(k) for k in FIELDS}
    out["country"] = out["country"].upper()
    out["via"] = out["via"] or "direct"
    out["fetched"] = out["fetched"] or now_iso()
    for k, v in row.items():  # keep any extra fields a source needs (e.g. "parent", "part")
        if k not in out:
            out[k] = v
    return out


def write_docs(country: str, source: str, rows: Iterable[Dict], replace: bool = False) -> tuple:
    """Append documents to docs/<COUNTRY>/<source>.jsonl, skipping ids already present.

    replace=True rewrites the file with exactly `rows` (still de-duplicated by id; the last copy wins).
    Returns (added, total). Safe against concurrent writers via an flock on the file."""
    path = docs_path(country, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [validate(r) for r in rows]
    for r in rows:
        if r["source"] != source or r["country"] != country.upper():
            raise ValueError(f"{r['id']}: country/source must match the file ({country}/{source})")
    lockf = path.with_suffix(".lock")
    with _critical(), open(lockf, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            if replace:
                uniq = {r["id"]: r for r in rows}
                tmp = path.with_suffix(".tmp")
                with tmp.open("w", encoding="utf-8") as f:
                    for r in uniq.values():
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
                os.replace(tmp, path)
                return len(uniq), len(uniq)
            seen = existing_ids(path)
            added = 0
            with path.open("a", encoding="utf-8") as f:
                for r in rows:
                    if r["id"] in seen:
                        continue
                    seen.add(r["id"])
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                    added += 1
            return added, len(seen)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------------------------- state
class State:
    """Small resumable JSON state at state/<name>.json: {"done": [...], plus any keys you set}."""

    def __init__(self, name: str):
        self.path = STATE / f"{name}.json"
        self.data: Dict = json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}
        self._done = set(self.data.get("done", []))

    def __getitem__(self, k):
        return self.data.get(k)

    def __setitem__(self, k, v):
        self.data[k] = v

    def get(self, k, default=None):
        return self.data.get(k, default)

    def is_done(self, key: str) -> bool:
        return key in self._done

    def mark_done(self, key: str) -> None:
        self._done.add(key)

    def save(self) -> None:
        with _critical():
            self._save()

    def _save(self) -> None:
        self.data["done"] = sorted(self._done)
        self.data["updated"] = now_iso()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=0), "utf-8")
        os.replace(tmp, self.path)


def setup_logging(name: str, level: int = logging.INFO) -> logging.Logger:
    """Log to stderr and to state/<name>.log."""
    STATE.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        h = logging.StreamHandler()
        h.setFormatter(fmt)
        root.addHandler(h)
    fh = logging.FileHandler(STATE / f"{name}.log", encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    return logging.getLogger(name)


__all__ = ["ROOT", "DOCS", "RAW", "STATE", "UA", "HOST_DELAY", "robots_allowed", "crawl_delay", "wayback_latest",
           "fetch", "fetch_meta", "clean_html", "make_id", "docs_path", "read_docs", "existing_ids", "validate",
           "write_docs", "State", "setup_logging", "now_iso"]
