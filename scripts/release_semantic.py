"""Optional semantic/ folder of the dataset release: per-document tone, topic and target scores keyed by id.

Reads index/semantic/semantic.sqlite READ-ONLY (the semantic agent may be writing it; see
scripts/semantic/SCHEMA.md). The folder is written only when the tone model has scored documents
(`doc_tone` non-empty); otherwise the release notes say it was skipped and why.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

import pyarrow as pa
import pyarrow.parquet as pq

DIMS = ("hostility", "threat", "conciliation", "grievance", "escalation", "deescalation")
SCHEMA = pa.schema(
    [("id", pa.string()), ("country", pa.string()), ("source", pa.string()), ("outlet", pa.string()),
     ("nsent_scored", pa.int32())] + [(d, pa.float32()) for d in DIMS]
    + [("tone_model_v", pa.string()), ("topic", pa.int32()), ("topic_label", pa.string()),
       ("topic_sim", pa.float32()), ("topic_v", pa.string()), ("targets", pa.list_(pa.string()))])


def _connect(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)


def status(sem_dir: Path) -> Dict[str, Any]:
    db = sem_dir / "semantic.sqlite"
    if not db.exists():
        return {"ready": False, "reason": f"{db} not found"}
    try:
        con = _connect(db)
        try:
            n = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                 for t in ("docs", "doc_tone", "doc_topic")}
            n["mentions_docs"] = con.execute("SELECT count(DISTINCT doc_id) FROM mentions").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error as e:
        return {"ready": False, "reason": f"semantic store unreadable: {e}"}
    if n["doc_tone"] == 0:
        return {"ready": False, "counts": n,
                "reason": (f"tone model has scored 0 documents yet (topics assigned to {n['doc_topic']:,} docs, "
                           f"target mentions found in {n['mentions_docs']:,} of {n['docs']:,}); semantic layer "
                           "still building")}
    return {"ready": True, "counts": n}


def _topic_labels(sem_dir: Path) -> Dict[int, str]:
    path = sem_dir / "aggregates" / "topics.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {int(t["topic"]): t.get("label") for t in data.get("topics", []) if "topic" in t}


def export(sem_dir: Path, meta: Dict[str, Dict[str, Optional[str]]], out_dir: Path) -> Dict[str, Any]:
    """Write semantic/doc_scores.parquet for documents in `meta` (id -> {country, source, outlet})."""
    st = status(sem_dir)
    if not st["ready"]:
        return {"included": False, **st}
    labels = _topic_labels(sem_dir)
    con = _connect(sem_dir / "semantic.sqlite")
    try:
        tone = {r[0]: r[1:] for r in con.execute(
            f"SELECT doc_id, nsent, {', '.join(DIMS)}, model_v FROM doc_tone")}
        topic = {r[0]: r[1:] for r in con.execute("SELECT doc_id, topic, sim, topic_v FROM doc_topic")}
        targets = {r[0]: sorted(set(r[1].split(","))) for r in con.execute(
            "SELECT doc_id, group_concat(DISTINCT target) FROM mentions WHERE self = 0 GROUP BY doc_id")}
    finally:
        con.close()
    rows = []
    for doc_id in sorted(set(tone) | set(topic)):
        if doc_id not in meta:
            continue
        t, tp = tone.get(doc_id), topic.get(doc_id)
        row = {"id": doc_id, **meta[doc_id], "nsent_scored": t[0] if t else None,
               "tone_model_v": t[-1] if t else None, "topic": tp[0] if tp else None,
               "topic_label": labels.get(tp[0]) if tp else None, "topic_sim": tp[1] if tp else None,
               "topic_v": tp[2] if tp else None, "targets": targets.get(doc_id)}
        row.update({d: (t[i + 1] if t else None) for i, d in enumerate(DIMS)})
        rows.append(row)
    dest = out_dir / "semantic"
    dest.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), dest / "doc_scores.parquet", compression="zstd")
    for name in ("validation.json", "manifest.json"):
        src = sem_dir / "aggregates" / name
        if src.exists():
            shutil.copyfile(src, dest / f"semantic_{name}")
    return {"included": True, "rows": len(rows), **st}
