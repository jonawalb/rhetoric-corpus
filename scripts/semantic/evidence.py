"""Evidence for alerts: the quotable sentences behind each flagged number (text <= 300 chars, date, source, link).

Sentences are re-read from corpus.sqlite by (doc, idx) and checked against the stored CRC; a sentence whose text
no longer matches (the corpus was re-split since scoring) is skipped rather than shown wrongly.
"""
from __future__ import annotations

import logging
import math
import sqlite3
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import stats, store
from .config import DIMS

logger = logging.getLogger(__name__)
MAX_EVIDENCE_ALERTS = 600
RECENT_WEEKS = 12


def _rank_metric(metric: str, delta: float) -> str:
    if metric == "esc_balance":
        return "escalation" if delta >= 0 else "deescalation"
    return metric if metric in DIMS else "hostility"


def top_sentences(con: sqlite3.Connection, cc: sqlite3.Connection, docs: pd.DataFrame, metric: str,
                  target: Optional[str] = None, k: int = 5) -> List[Dict]:
    """Best sentence per doc by `metric` (optionally only sentences mentioning `target`), top k docs."""
    if docs.empty:
        return []
    ids = docs["doc_id"].tolist()
    best: Dict[str, tuple] = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        ph = ",".join("?" * len(part))
        if target:
            q = (f"SELECT s.doc_id, s.idx, s.crc, s.{metric} FROM sent_scores s JOIN mentions m ON m.doc_id=s.doc_id"
                 f" AND m.idx=s.idx WHERE m.target=? AND m.self=0 AND s.doc_id IN ({ph})")
            args = [target, *part]
        else:
            q = f"SELECT doc_id, idx, crc, {metric} FROM sent_scores WHERE doc_id IN ({ph})"
            args = part
        for d, idx, c, v in con.execute(q, args):
            if d not in best or v > best[d][2]:
                best[d] = (idx, c, v)
    meta = docs.set_index("doc_id")
    out = []
    for d, (idx, c, v) in sorted(best.items(), key=lambda x: -x[1][2]):
        r = meta.loc[d]
        text = store.sentence_text(cc, int(r["crow"]), idx)
        if text is None or store.crc(text) != c:
            continue
        out.append({"text": text[:300], "score": round(v / 1000, 3), "metric": metric, "doc_id": d, "date": r["date"],
                    "country": r["country"], "source": r["source"], "outlet": r["outlet"],
                    "title": (r["title"] or "")[:200], "url": r["url"]})
        if len(out) >= k:
            break
    return out


def top_mentions(con: sqlite3.Connection, cc: sqlite3.Connection, docs: pd.DataFrame, target: str, k: int = 5) -> List[Dict]:
    """For salience alerts: the first sentence mentioning `target` in the k docs that mention it most often."""
    if docs.empty:
        return []
    ids = docs["doc_id"].tolist()
    hits: Dict[str, List[int]] = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for d, idx in con.execute(f"SELECT doc_id, idx FROM mentions WHERE target=? AND self=0 AND doc_id IN"
                                  f" ({','.join('?' * len(part))}) ORDER BY doc_id, idx", [target, *part]):
            hits.setdefault(d, []).append(idx)
    meta = docs.set_index("doc_id")
    out = []
    for d in sorted(hits, key=lambda x: (-len(hits[x]), x))[:k]:
        r = meta.loc[d]
        text = store.sentence_text(cc, int(r["crow"]), hits[d][0]) or ""
        out.append({"text": text[:300], "score": len(hits[d]), "metric": "mention_sentences", "doc_id": d,
                    "date": r["date"], "country": r["country"], "source": r["source"], "outlet": r["outlet"],
                    "title": (r["title"] or "")[:200], "url": r["url"]})
    return out


def _topic_docs(con: sqlite3.Connection, cc: sqlite3.Connection, docs: pd.DataFrame, topic: int, k: int = 5) -> List[Dict]:
    if docs.empty:
        return []
    ids = docs["doc_id"].tolist()
    sims = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for d, s in con.execute(f"SELECT doc_id, sim FROM doc_topic WHERE topic=? AND doc_id IN ({','.join('?' * len(part))})",
                                [topic, *part]):
            sims[d] = s
    meta = docs.set_index("doc_id")
    out = []
    for d in sorted(sims, key=sims.get, reverse=True)[:k]:
        r = meta.loc[d]
        first = store.sentence_text(cc, int(r["crow"]), 0) or ""
        out.append({"text": (r["title"] or first)[:300], "score": round(sims[d], 3), "metric": "topic_similarity",
                    "doc_id": d, "date": r["date"], "country": r["country"], "source": r["source"], "outlet": r["outlet"],
                    "title": (r["title"] or "")[:200], "url": r["url"]})
    return out


def attach(con: sqlite3.Connection, alerts: pd.DataFrame, docs: pd.DataFrame) -> List[Dict]:
    """Alert records (ranked) with evidence for the top MAX_EVIDENCE_ALERTS and every recent alert."""
    a = alerts.copy()
    a["score"] = a["z"].abs() * np.log10(a["n"].astype(float) + 10)
    last = pd.Timestamp(docs["date"].max())
    end_ts = pd.to_datetime(a["end"].astype(str).where(a["period_type"] == "week", a["end"].astype(str) + "-01"))
    a["recent"] = (last - end_ts).dt.days <= RECENT_WEEKS * 7
    a = a.sort_values(["recent", "score"], ascending=[False, False]).reset_index(drop=True)
    cc = store.corpus()
    recs = []
    for i, r in a.iterrows():
        rec = {k: _clean(r.get(k)) for k in ("kind", "level", "period_type", "country", "stream", "metric", "target",
                                            "topic", "start", "end", "n_periods", "period", "mean", "value", "base",
                                            "delta", "z", "n", "score", "recent")}
        if rec.get("mean") is None and rec.get("value") is not None:
            rec["mean"] = rec["value"]
        for key in ("topic", "n", "n_periods"):
            if rec.get(key) is not None:
                rec[key] = int(rec[key])
        rec["id"] = f"a{i:05d}"
        rec["direction"] = "up" if (r["z"] or 0) > 0 else "down"
        rec["tier"] = stats.tier(float(r["z"]), int(r.get("n_periods") or 1))
        if i < MAX_EVIDENCE_ALERTS or bool(r["recent"]):
            pcol = "week" if r["period_type"] == "week" else "month"
            sel = docs[docs[pcol] == r["period"]]
            sel = sel[sel["stream"] == r["stream"]] if r["level"] == "stream" else sel[sel["country"] == r["country"]]
            target = _clean(r.get("target"))
            if r["kind"] == "salience":
                rec["evidence"] = top_mentions(con, cc, sel, str(target))
                rec["evidence_note"] = (f"first sentence mentioning {target} in the peak-period documents that mention it most"
                                        " (score = number of mention sentences)")
            elif r["kind"] == "topic_share":
                rec["evidence"] = _topic_docs(con, cc, sel, int(r["topic"]))
                rec["evidence_note"] = "documents of this topic in the period, closest to the topic centroid"
            else:
                metric = _rank_metric(str(r["metric"]), float(r["delta"] or 0))
                rec["evidence"] = top_sentences(con, cc, sel, metric, target=target)
                rec["evidence_note"] = (f"highest-{metric} sentences in the peak period"
                                        + (f" that mention {target}" if target else "")
                                        + ("; the shift is DOWNWARD, so these show what remained, not what changed"
                                           if rec["direction"] == "down" else ""))
        recs.append(rec)
    cc.close()
    return recs


def _clean(v):
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return None if math.isnan(float(v)) else round(float(v), 5)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v
