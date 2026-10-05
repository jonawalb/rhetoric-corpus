"""Shared helpers for the paper/report feeds (export_cross_strait_pulse, export_transit_rhetoric, export_taiwan_series).

All three read index/corpus.sqlite and index/semantic/semantic.sqlite READ-ONLY (sqlite URI mode=ro) and write only
to the output directory they are given. `--index DIR` (or $RC_INDEX) points them at another copy of index/.
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from scripts.textnorm import sentences

ROOT = Path(__file__).resolve().parents[1]
DIMS: Tuple[str, ...] = ("hostility", "threat", "conciliation", "grievance", "escalation", "deescalation")
EXCLUDED_SAMPLES = ("backfill", "seed")

NOT_VALIDATED = ("Tone scores are machine estimates (a student classifier trained on a zero-shot NLI teacher). They are "
                 "NOT yet validated against human coding; human validation is in progress. The agreement figures in "
                 "provenance are teacher-student agreement, not agreement with human coders.")


def index_dir(arg: Optional[str] = None) -> Path:
    return Path(arg or os.environ.get("RC_INDEX") or ROOT / "index").expanduser()


def ro(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"missing database: {path}")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def connect(index: Path) -> Tuple[sqlite3.Connection, sqlite3.Connection]:
    """(corpus, semantic) read-only connections."""
    return ro(index / "corpus.sqlite"), ro(index / "semantic" / "semantic.sqlite")


def line_sentences(text: str) -> List[Tuple[str, int, int]]:
    """[(line, first_idx, end_idx)] for every line of `text`.

    The index builder stores sentences(text) (scripts/textnorm.py), which splits on newlines first, so sentence idx
    runs line by line; re-splitting each line gives the idx range of the sentences that came from it.
    """
    out, k = [], 0
    for line in (text or "").split("\n"):
        n = len(sentences(line))
        out.append((line, k, k + n))
        k += n
    return out


def sentence_scores(sc: sqlite3.Connection, doc_id: str) -> Dict[int, Dict[str, float]]:
    """{idx: {dim: probability}} for the scored sentences of a document (stored as integers 0..1000)."""
    q = f"SELECT idx, {', '.join(DIMS)} FROM sent_scores WHERE doc_id = ?"
    return {r[0]: {d: v / 1000 for d, v in zip(DIMS, r[1:])} for r in sc.execute(q, [doc_id])}


def mean_dims(rows: Iterable[Dict[str, float]]) -> Dict[str, Optional[float]]:
    rows = list(rows)
    return {d: (round(sum(r[d] for r in rows) / len(rows), 4) if rows else None) for d in DIMS}


def provenance(index: Path) -> Dict:
    """Semantic-layer versions and teacher-student agreement, copied so outputs stay interpretable after index/ goes."""
    agg = index / "semantic" / "aggregates"
    out: Dict = {}
    try:
        man = json.loads((agg / "manifest.json").read_text(encoding="utf-8"))
        out.update(semantic_generated=man.get("generated"), versions=man.get("versions"))
    except (OSError, ValueError):
        pass
    try:
        val = json.loads((agg / "validation.json").read_text(encoding="utf-8"))
        out["teacher_student_agreement"] = {k: {kk: v["overall"].get(kk) for kk in ("n", "accuracy", "pearson", "kappa", "auc")}
                                            for k, v in val.get("dims", {}).items()}
        out["agreement_note"] = val.get("note")
    except (OSError, ValueError):
        pass
    sc = ro(index / "semantic" / "semantic.sqlite")
    try:
        meta = dict(sc.execute("SELECT k, v FROM meta").fetchall())
        out["tone_model"] = meta.get("tone_train")
    finally:
        sc.close()
    out["validation_status"] = NOT_VALIDATED
    return out


def write_csv(path: Path, fields: Sequence[str], rows: Iterable[Dict], bom: bool = False) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("x", newline="", encoding="utf-8-sig" if bom else "utf-8") as fh:  # "x": never overwrite
        w = csv.DictWriter(fh, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
            n += 1
    return n


def new_dir(path: Path) -> Path:
    """Create `path`; refuse to reuse an existing directory (outputs are versioned, never overwritten)."""
    if path.exists():
        raise SystemExit(f"output directory exists, refusing to overwrite: {path}")
    path.mkdir(parents=True)
    return path
