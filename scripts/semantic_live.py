"""Private, on-demand queries for the local UI: cross-lingual semantic search and evidence sentences for any
country/stream x period x metric (x target) cell. Uses index/semantic/semantic.sqlite and corpus.sqlite; never
used by the public build. One model/vector load per process; calls are serialised (MPS is not thread-safe).
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
from datetime import date, timedelta
from typing import Dict, List, Optional

import pandas as pd

from scripts.semantic import evidence as ev
from scripts.semantic import store
from scripts.semantic.config import DIMS, EXCLUDED_SAMPLES

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_STATE: Dict[str, object] = {}
CC_RE = re.compile(r"^[A-Z]{2}$")
TARGET_RE = re.compile(r"^[A-Z]{2,12}$")
WEEK_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
METRICS = set(DIMS) | {"esc_balance", "share"}


def _con() -> sqlite3.Connection:
    if "con" not in _STATE:
        con = sqlite3.connect(f"file:{store.SEM_DB}?mode=ro", uri=True, check_same_thread=False, timeout=60)
        _STATE["con"] = con
    return _STATE["con"]  # type: ignore[return-value]


def semantic_search(query: str, k: int = 30, country: Optional[List[str]] = None, source: Optional[List[str]] = None,
                    lang: Optional[List[str]] = None, date_from: str = "", date_to: str = "") -> List[Dict]:
    """scripts.semantic.search.search with a cached encoder (first call loads the model and ~400 MB of vectors)."""
    from scripts.semantic.embed import E5Encoder
    from scripts.semantic.search import search

    with _LOCK:
        if "enc" not in _STATE:
            _STATE["enc"] = E5Encoder()
        return search(_con(), query, k, country or None, source or None, lang or None, date_from or None,
                      date_to or None, enc=_STATE["enc"])


def _period_range(period: str) -> tuple:
    if WEEK_RE.match(period):
        d0 = date.fromisoformat(period)
        return period, (d0 + timedelta(days=6)).isoformat()
    if MONTH_RE.match(period):
        y, m = map(int, period.split("-"))
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        return f"{period}-01", (nxt - timedelta(days=1)).isoformat()
    raise ValueError(f"bad period {period!r} (YYYY-MM or a Monday YYYY-MM-DD)")


def period_docs(country: str, period: str, stream: str = "", official_only: bool = False) -> pd.DataFrame:
    """Documents of one country (or stream) in one week/month, with the same exclusions as the series."""
    if not CC_RE.match(country):
        raise ValueError("bad country")
    d0, d1 = _period_range(period)
    q = ("SELECT doc_id, crow, date, country, source, outlet, lang, title, url, coalesce(nullif(sample, ''), 'all') AS sample"
         " FROM docs WHERE present=1 AND country=? AND date BETWEEN ? AND ?"
         f" AND coalesce(sample, '') NOT IN ({','.join('?' * len(EXCLUDED_SAMPLES))})")
    args: list = [country, d0, d1, *EXCLUDED_SAMPLES]
    if official_only:
        q += " AND outlet='official'"
    if stream:
        parts = stream.split("|")
        if len(parts) != 3:
            raise ValueError("bad stream")
        q += " AND source=? AND lang=? AND coalesce(nullif(sample, ''), 'all')=?"
        args += parts
    with _LOCK:
        return pd.read_sql_query(q, _con(), params=args)


def evidence(kind: str, country: str, period: str, metric: str = "hostility", target: str = "", stream: str = "",
             scope: str = "all", direction: str = "up", k: int = 8) -> Dict:
    """Top sentences behind one cell. kind: tone | stance | salience. Returns {docs, note, evidence: [...]}."""
    if kind not in ("tone", "stance", "salience"):
        raise ValueError("kind must be tone, stance or salience")
    if metric not in METRICS:
        raise ValueError("bad metric")
    if kind != "tone" and not TARGET_RE.match(target or ""):
        raise ValueError("target required")
    docs = period_docs(country, period, stream, official_only=(scope == "official"))
    if docs.empty:
        return {"docs": 0, "note": "no documents in this period", "evidence": []}
    k = max(1, min(int(k), 20))
    cc = store.corpus()
    try:
        with _LOCK:
            con = _con()
            if kind == "salience":
                n_m = _count_mentioning(con, docs, target)
                out = ev.top_mentions(con, cc, docs, target, k)
                note = (f"{n_m} of {len(docs)} documents mention {target}; first mentioning sentence of the documents "
                        "that mention it most")
            else:
                rank = ev._rank_metric(metric, 1.0 if direction == "up" else -1.0)
                out = ev.top_sentences(con, cc, docs, rank, target=target or None, k=k)
                note = (f"highest-{rank} sentences" + (f" that mention {target}" if target else "")
                        + f" among {len(docs)} documents")
    finally:
        cc.close()
    return {"docs": int(len(docs)), "note": note, "evidence": out}


def _count_mentioning(con: sqlite3.Connection, docs: pd.DataFrame, target: str) -> int:
    ids = docs["doc_id"].tolist()
    n = 0
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        n += con.execute(f"SELECT count(DISTINCT doc_id) FROM mentions WHERE target=? AND self=0 AND doc_id IN"
                         f" ({','.join('?' * len(part))})", [target, *part]).fetchone()[0]
    return n
