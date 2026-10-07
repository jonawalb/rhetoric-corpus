"""The semantic store (index/semantic/semantic.sqlite + float16 .npy shards) and read access to the corpus.

corpus.sqlite is opened read-only; nothing here writes to it. Documents are keyed by their stable string id
(`docs.id`), never by the corpus rowid (which changes when build_index re-indexes a file).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import zlib
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .config import CORPUS_DB, EMB_DIR, EMB_DIM, SEM_DB, SEM_DIR

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
  doc_id TEXT PRIMARY KEY, crow INTEGER, file TEXT, country TEXT, source TEXT, outlet TEXT, org TEXT, lang TEXT,
  date TEXT, kind TEXT, url TEXT, title TEXT, sample TEXT, present INTEGER DEFAULT 1,
  targets_v TEXT, embedded INTEGER DEFAULT 0, scored_v TEXT, nsent INTEGER);
CREATE INDEX IF NOT EXISTS docs_crow ON docs(crow);
CREATE INDEX IF NOT EXISTS docs_cs ON docs(country, source, date);
CREATE TABLE IF NOT EXISTS passages(pid INTEGER PRIMARY KEY, doc_id TEXT NOT NULL, pidx INTEGER, s0 INTEGER,
  s1 INTEGER, nchars INTEGER);
CREATE INDEX IF NOT EXISTS passages_doc ON passages(doc_id);
CREATE TABLE IF NOT EXISTS shards(lo INTEGER PRIMARY KEY, hi INTEGER, file TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS mentions(doc_id TEXT, idx INTEGER, target TEXT, pat TEXT, self INTEGER,
  PRIMARY KEY(doc_id, idx, target, pat)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS mentions_target ON mentions(target);
CREATE TABLE IF NOT EXISTS sent_scores(doc_id TEXT, idx INTEGER, crc INTEGER, hostility INTEGER, threat INTEGER,
  conciliation INTEGER, grievance INTEGER, escalation INTEGER, deescalation INTEGER,
  PRIMARY KEY(doc_id, idx)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS doc_tone(doc_id TEXT PRIMARY KEY, nsent INTEGER, hostility REAL, threat REAL,
  conciliation REAL, grievance REAL, escalation REAL, deescalation REAL, model_v TEXT);
CREATE TABLE IF NOT EXISTS teacher(sid INTEGER PRIMARY KEY, doc_id TEXT, idx INTEGER, text TEXT, country TEXT,
  lang TEXT, source TEXT, stratum TEXT, kind TEXT, split TEXT, hyp_v TEXT, hostility REAL, threat REAL,
  conciliation REAL, grievance REAL, escalation REAL, deescalation REAL, emb BLOB,
  UNIQUE(doc_id, idx));
CREATE TABLE IF NOT EXISTS doc_topic(doc_id TEXT PRIMARY KEY, topic INTEGER, sim REAL, topic_v TEXT);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def connect(path: Path = SEM_DB) -> sqlite3.Connection:
    """Open (and create) the semantic store."""
    path.parent.mkdir(parents=True, exist_ok=True)
    EMB_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(passages)")}
    if "junk" not in cols:
        con.execute("ALTER TABLE passages ADD COLUMN junk INTEGER DEFAULT 0")
    return con


def corpus(path: Path = CORPUS_DB) -> sqlite3.Connection:
    """Read-only connection to the corpus index."""
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)


def get_meta(con: sqlite3.Connection, k: str, default: Optional[str] = None) -> Optional[str]:
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def set_meta(con: sqlite3.Connection, k: str, v: str) -> None:
    con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (k, v))


def crc(text: str) -> int:
    """Signed 32-bit-safe CRC of a sentence (detects a changed sentence split in the corpus)."""
    return zlib.crc32(text.encode("utf-8")) & 0x7FFFFFFF


# ---------------------------------------------------------------------------------------------- sync
def _samples_for(files: Iterable[str], wanted: set) -> Dict[str, str]:
    """doc id -> 'sample' field: from the corpus index's `sample` column (kept since the segmented store, so sealed
    documents need no JSONL file), plus any docs/<file>.jsonl still present locally."""
    out: Dict[str, str] = {}
    try:
        cc = corpus()
        try:
            if "sample" in {r[1] for r in cc.execute("PRAGMA table_info(docs)")}:
                ids = sorted(wanted)
                for i in range(0, len(ids), 500):
                    part = ids[i:i + 500]
                    out.update(cc.execute(f"SELECT id, sample FROM docs WHERE sample IS NOT NULL AND id IN "
                                          f"({','.join('?' * len(part))})", part))
        finally:
            cc.close()
    except sqlite3.Error as e:
        logger.warning("sample lookup in the corpus index failed: %s", e)
    docs_root = CORPUS_DB.parent.parent / "docs"
    for rel in files:
        p = docs_root / rel
        if not p.exists():
            continue
        with p.open("rb") as f:
            for line in f:
                if b'"sample"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("id") in wanted and d.get("sample"):
                    out[d["id"]] = str(d["sample"])
    return out


def sync_docs(con: sqlite3.Connection) -> Dict[str, int]:
    """Mirror corpus doc metadata into the store: add new docs, refresh corpus rowids, flag removed docs."""
    cc = corpus()
    rows = cc.execute("SELECT rowid, id, file, country, source, outlet, org, lang, date, kind, url, title FROM docs").fetchall()
    cc.close()
    known = {r[0]: (r[1], r[2], r[3], r[4]) for r in con.execute("SELECT doc_id, crow, present, source, outlet FROM docs")}
    new = [r for r in rows if r[1] not in known]
    moved = [(r[0], r[1]) for r in rows if r[1] in known and (known[r[1]][0] != r[0] or not known[r[1]][1])]
    # The corpus index can reclassify a document (build_index.py Telegram policy): follow its source and outlet.
    relabelled = [(r[4], r[5], r[1]) for r in rows if r[1] in known and known[r[1]][2:] != (r[4], r[5])]
    present = {r[1] for r in rows}
    gone = [d for d, (_, pr, *_x) in known.items() if pr and d not in present]
    samples = _samples_for({r[2] for r in new}, {r[1] for r in new}) if new else {}
    con.executemany(
        "INSERT INTO docs(crow, doc_id, file, country, source, outlet, org, lang, date, kind, url, title, sample)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", [(*r, samples.get(r[1])) for r in new])
    # A re-indexed file may also have changed its sentence split: re-run targets for moved docs.
    con.executemany("UPDATE docs SET crow=?, present=1, targets_v=NULL WHERE doc_id=?", moved)
    con.executemany("UPDATE docs SET present=0 WHERE doc_id=?", [(d,) for d in gone])
    con.executemany("UPDATE docs SET source=?, outlet=? WHERE doc_id=?", relabelled)
    con.commit()
    stats = {"corpus_docs": len(rows), "new": len(new), "moved": len(moved), "gone": len(gone),
             "relabelled": len(relabelled)}
    logger.info("sync: %s", stats)
    return stats


# ---------------------------------------------------------------------------------------------- reading
def sentences_for(cc: sqlite3.Connection, crows: Sequence[int], max_idx: Optional[int] = None
                  ) -> Dict[int, List[Tuple[int, str]]]:
    """corpus rowid -> [(idx, text)] in order (optionally idx < max_idx)."""
    out: Dict[int, List[Tuple[int, str]]] = defaultdict(list)
    for i in range(0, len(crows), 500):
        part = list(crows[i:i + 500])
        q = f"SELECT doc, idx, text FROM sentences WHERE doc IN ({','.join('?' * len(part))})"
        args: list = list(part)
        if max_idx is not None:
            q += " AND idx < ?"
            args.append(max_idx)
        for doc, idx, text in cc.execute(q + " ORDER BY doc, idx", args):
            out[doc].append((idx, text))
    return out


def sentence_text(cc: sqlite3.Connection, crow: int, idx: int) -> Optional[str]:
    r = cc.execute("SELECT text FROM sentences WHERE doc=? AND idx=?", (crow, idx)).fetchone()
    return r[0] if r else None


# ---------------------------------------------------------------------------------------------- embeddings
def write_shard(con: sqlite3.Connection, lo: int, emb: np.ndarray) -> str:
    """Save passage embeddings for pids lo..lo+len-1 (float16). Caller commits the passage rows afterwards."""
    name = f"p_{lo:09d}.npy"
    np.save(EMB_DIR / name, emb.astype(np.float16))
    con.execute("INSERT OR REPLACE INTO shards(lo, hi, file, created) VALUES(?,?,?,datetime('now'))",
                (lo, lo + len(emb), name))
    return name


def load_passages(con: sqlite3.Connection) -> Tuple[np.ndarray, np.ndarray]:
    """(pids, embeddings float16 [n, 384]) for all passages that have a shard, in pid order."""
    pids, embs = [], []
    for lo, hi, name in con.execute("SELECT lo, hi, file FROM shards ORDER BY lo"):
        p = EMB_DIR / name
        if not p.exists():
            logger.warning("missing shard %s", name)
            continue
        e = np.load(p, mmap_mode="r")
        pids.append(np.arange(lo, lo + len(e)))
        embs.append(np.asarray(e))
    if not embs:
        return np.zeros(0, dtype=np.int64), np.zeros((0, EMB_DIM), dtype=np.float16)
    return np.concatenate(pids), np.concatenate(embs)


def passage_frame(con: sqlite3.Connection, with_junk: bool = False):
    """pandas frame: pid, doc_id, pidx, s0, s1 + doc metadata, for passages of present docs (junk excluded)."""
    import pandas as pd

    return pd.read_sql_query(
        "SELECT p.pid, p.doc_id, p.pidx, p.s0, p.s1, p.junk, d.crow, d.country, d.source, d.outlet, d.lang, d.date, d.kind, d.url,"
        " d.title, d.sample FROM passages p JOIN docs d USING(doc_id) WHERE d.present=1"
        + ("" if with_junk else " AND p.junk=0") + " ORDER BY p.pid", con)


def disk_usage() -> Dict[str, float]:
    """MB used by the semantic store, by part."""
    def mb(p: Path) -> float:
        if p.is_file():
            return p.stat().st_size / 1e6
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink()) / 1e6 if p.exists() else 0.0

    parts = {"models": SEM_DIR / "models", "emb": EMB_DIR, "aggregates": SEM_DIR / "aggregates"}
    out = {k: round(mb(v), 1) for k, v in parts.items()}
    out["sqlite"] = round(sum(mb(p) for p in SEM_DIR.glob("semantic.sqlite*")), 1)
    out["total"] = round(mb(SEM_DIR), 1)
    return out
