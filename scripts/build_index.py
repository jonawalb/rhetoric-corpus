"""Build or update index/corpus.sqlite from the sealed parts (docs-parts/**/*.jsonl.gz) and docs/**/*.jsonl.

Tables
  files      one row per JSONL file: size, mtime, bytes indexed, hash of the indexed prefix.
  docs       document metadata + full text (rowid = integer doc key).
  fts_words  FTS5 over title+text, tokenizer unicode61 remove_diacritics 2 (Latin, Cyrillic, Persian, ...).
             External content (docs); the indexed values are textnorm.norm()-folded (ё->е, ي->ی, ...).
  fts_cjk    FTS5 trigram over title+text, only for zh/ko/ja documents (substring search, >= 3 characters).
  sentences  (doc, idx, text): every document split into sentences of at most 300 characters (snippets,
             public build).

Incremental: a part (immutable, scripts/segments.py) is indexed once and recorded in `files`; it is then never
read again, so it need not be present locally (the nightly job downloads only parts not yet in `files`). An active
docs file whose size and mtime are unchanged is skipped; one that only grew (the indexed prefix hashes the same) has
just its new lines added; any other change re-indexes that file. Re-indexing or removing an active file deletes only
its documents that are NOT sealed (state/ids/<CC>/<source>.ids): sealed documents moved into a part when the seal cut
them from the file, so they keep their rows and rowids. Documents also keep the `sample` and `wayback` fields
(filled in later for an existing row when a part repeats it), so publish/semantic steps need no JSONL files.
--rebuild starts from scratch (it needs every part locally: store_sync.py pull --only docs-parts).

Usage: uv run python scripts/build_index.py [--rebuild] [--trigram-all]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import segments  # noqa: E402
from textnorm import CJK_LANGS, norm, sentences  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DB = ROOT / "index" / "corpus.sqlite"
META = ("id", "country", "source", "outlet", "org", "lang", "date", "url", "title", "speaker", "kind", "via")
logger = logging.getLogger("build_index")

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, size INTEGER, mtime REAL, done INTEGER, prefix_sha TEXT, ndocs INTEGER);
CREATE TABLE IF NOT EXISTS docs(
  rowid INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, file TEXT NOT NULL,
  country TEXT, source TEXT, outlet TEXT, org TEXT, lang TEXT, date TEXT, url TEXT, title TEXT, speaker TEXT,
  kind TEXT, via TEXT, text TEXT, nchars INTEGER, sample TEXT, wayback TEXT);
CREATE INDEX IF NOT EXISTS docs_file ON docs(file);
CREATE INDEX IF NOT EXISTS docs_filter ON docs(country, source, date);
CREATE INDEX IF NOT EXISTS docs_date ON docs(date);
CREATE VIRTUAL TABLE IF NOT EXISTS fts_words USING fts5(title, text, content='docs', content_rowid='rowid',
  tokenize="unicode61 remove_diacritics 2");
CREATE VIRTUAL TABLE IF NOT EXISTS fts_cjk USING fts5(title, text, content='docs', content_rowid='rowid',
  tokenize='trigram');
CREATE TABLE IF NOT EXISTS sentences(doc INTEGER NOT NULL, idx INTEGER NOT NULL, text TEXT NOT NULL,
  PRIMARY KEY(doc, idx)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def connect(path: Path = DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(docs)")}
    for c in ("sample", "wayback"):  # added with the segmented store (2026-10-04)
        if c not in cols:
            con.execute(f"ALTER TABLE docs ADD COLUMN {c} TEXT")
    return con


def _prefix_sha(path: Path, n: int) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        left = n
        while left > 0:
            chunk = f.read(min(1 << 20, left))
            if not chunk:
                break
            h.update(chunk)
            left -= len(chunk)
    return h.hexdigest()


def _iter_part(path: Path) -> Iterable[Dict]:
    for line in segments.open_lines(path):
        try:
            yield json.loads(line)
        except json.JSONDecodeError as e:
            logger.warning("%s: bad JSON line skipped (%s)", path.name, e)


def _extra(r: Dict, k: str) -> Optional[str]:
    v = r.get(k)
    return None if v in (None, "") else str(v)


def _iter_lines(path: Path, start: int) -> Tuple[List[Dict], int]:
    """Parse complete JSON lines from byte offset `start`; returns (rows, offset after last complete line)."""
    rows = []
    with path.open("rb") as f:
        f.seek(start)
        data = f.read()
    end = data.rfind(b"\n") + 1  # ignore a partially written last line
    for line in data[:end].splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning("%s: bad JSON line skipped (%s)", path.name, e)
    return rows, start + end


def _delete_docs(con: sqlite3.Connection, where: str, args: tuple) -> int:
    cur = con.execute(f"SELECT rowid, title, text, lang FROM docs WHERE {where}", args)
    n = 0
    for rowid, title, text, lang in cur.fetchall():
        con.execute("INSERT INTO fts_words(fts_words, rowid, title, text) VALUES('delete', ?, ?, ?)",
                    (rowid, norm(title), norm(text)))
        if _use_cjk(con, lang):
            con.execute("INSERT INTO fts_cjk(fts_cjk, rowid, title, text) VALUES('delete', ?, ?, ?)",
                        (rowid, norm(title), norm(text)))
        con.execute("DELETE FROM sentences WHERE doc=?", (rowid,))
        con.execute("DELETE FROM docs WHERE rowid=?", (rowid,))
        n += 1
    return n


_TRIGRAM_ALL: Dict[int, bool] = {}


def _use_cjk(con: sqlite3.Connection, lang: Optional[str]) -> bool:
    key = id(con)
    if key not in _TRIGRAM_ALL:
        r = con.execute("SELECT v FROM meta WHERE k='trigram_all'").fetchone()
        _TRIGRAM_ALL[key] = bool(r and r[0] == "1")
    return _TRIGRAM_ALL[key] or (lang in CJK_LANGS)


def _insert_docs(con: sqlite3.Connection, rel: str, rows: Iterable[Dict]) -> Tuple[int, int]:
    added = dup = 0
    for r in rows:
        if not r.get("id") or not r.get("text"):
            continue
        vals = [r.get(k) for k in META]
        sample, wayback = _extra(r, "sample"), _extra(r, "wayback")
        cur = con.execute(
            "INSERT OR IGNORE INTO docs(id, country, source, outlet, org, lang, date, url, title, speaker, kind, via, file,"
            " text, nchars, sample, wayback) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (*vals, rel, r["text"], len(r["text"]), sample, wayback))
        if cur.rowcount == 0:
            dup += 1
            if sample or wayback:  # backfill the fields for rows indexed before they were kept
                con.execute("UPDATE docs SET sample=coalesce(sample, ?), wayback=coalesce(wayback, ?) WHERE id=?"
                            " AND (sample IS NULL OR wayback IS NULL)", (sample, wayback, r["id"]))
            continue
        rowid = cur.lastrowid
        title, text = norm(r.get("title") or ""), norm(r["text"])
        con.execute("INSERT INTO fts_words(rowid, title, text) VALUES(?,?,?)", (rowid, title, text))
        if _use_cjk(con, r.get("lang")):
            con.execute("INSERT INTO fts_cjk(rowid, title, text) VALUES(?,?,?)", (rowid, title, text))
        con.executemany("INSERT INTO sentences(doc, idx, text) VALUES(?,?,?)",
                        [(rowid, i, s) for i, s in enumerate(sentences(r["text"]))])
        added += 1
    return added, dup


def _delete_unsealed(con: sqlite3.Connection, rel: str, root: Path) -> int:
    """Delete the documents indexed from active file `rel` ('<CC>/<source>.jsonl') that are not sealed."""
    cc, name = rel.split("/", 1)
    sealed = segments.read_ids(segments.ids_file(root, cc, name[:-len(".jsonl")]))
    rowids = [rid for rid, did in con.execute("SELECT rowid, id FROM docs WHERE file=?", (rel,)) if did not in sealed]
    n = 0
    for i in range(0, len(rowids), 500):
        chunk = rowids[i:i + 500]
        n += _delete_docs(con, f"rowid IN ({','.join('?' * len(chunk))})", tuple(chunk))
    return n


def update(con: sqlite3.Connection, docs_dir: Path = DOCS, root: Optional[Path] = None) -> Dict[str, int]:
    """root: checkout holding docs-parts/ and state/ids/ (default: the parent of docs_dir)."""
    root = docs_dir.parent if root is None else root
    stats = {"files_skipped": 0, "files_appended": 0, "files_reindexed": 0, "files_removed": 0,
             "docs_added": 0, "docs_removed": 0, "dup_ids": 0, "parts_indexed": 0, "parts_known": 0}
    known = {r[0]: r for r in con.execute("SELECT path, size, mtime, done, prefix_sha FROM files")}
    for path in segments.part_files(root):  # immutable: once in `files`, never read again
        rel = path.relative_to(root).as_posix()
        if rel in known:
            stats["parts_known"] += 1
            continue
        added, dup = _insert_docs(con, rel, _iter_part(path))
        st = path.stat()
        con.execute("INSERT OR REPLACE INTO files(path, size, mtime, done, prefix_sha, ndocs) VALUES(?,?,?,?,?,?)",
                    (rel, st.st_size, st.st_mtime, st.st_size, _prefix_sha(path, st.st_size), added))
        con.commit()
        stats["parts_indexed"] += 1
        stats["docs_added"] += added
        stats["dup_ids"] += dup
        logger.info("%s: +%d docs (%d already indexed)", rel, added, dup)
    present = set()
    for path in sorted(docs_dir.glob("*/*.jsonl")):
        rel = str(path.relative_to(docs_dir))
        present.add(rel)
        st = path.stat()
        prev = known.get(rel)
        if prev and prev[1] == st.st_size and abs(prev[2] - st.st_mtime) < 1e-6:
            stats["files_skipped"] += 1
            continue
        start = 0
        if prev and st.st_size > prev[3] and _prefix_sha(path, prev[3]) == prev[4]:
            start = prev[3]
            stats["files_appended"] += 1
        else:
            if prev:
                stats["docs_removed"] += _delete_unsealed(con, rel, root)
            stats["files_reindexed"] += 1
        rows, done = _iter_lines(path, start)
        added, dup = _insert_docs(con, rel, rows)
        stats["docs_added"] += added
        stats["dup_ids"] += dup
        ndocs = con.execute("SELECT count(*) FROM docs WHERE file=?", (rel,)).fetchone()[0]
        con.execute("INSERT OR REPLACE INTO files(path, size, mtime, done, prefix_sha, ndocs) VALUES(?,?,?,?,?,?)",
                    (rel, st.st_size, st.st_mtime, done, _prefix_sha(path, done), ndocs))
        con.commit()
        logger.info("%s: +%d docs (%d duplicate ids), %d total", rel, added, dup, ndocs)
    for rel in set(known) - present:
        if rel.startswith(segments.PARTS_DIR + "/"):
            continue  # a part indexed earlier and not downloaded this run: it is still in the store
        stats["docs_removed"] += _delete_unsealed(con, rel, root)
        con.execute("DELETE FROM files WHERE path=?", (rel,))
        stats["files_removed"] += 1
    con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('built', datetime('now'))")
    con.commit()
    return stats


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Build/update the corpus search index.")
    ap.add_argument("--rebuild", action="store_true", help="delete the index and build from scratch")
    ap.add_argument("--trigram-all", action="store_true",
                    help="(with --rebuild) also put non-CJK documents in the trigram table (substring search everywhere)")
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--docs", type=Path, default=DOCS)
    ap.add_argument("--root", type=Path, default=None, help="checkout with docs-parts/ and state/ids/ (default: --docs/..)")
    ap.add_argument("--optimize", action="store_true", help="merge FTS segments and VACUUM afterwards")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if a.rebuild:
        for suffix in ("", "-wal", "-shm"):
            Path(str(a.db) + suffix).unlink(missing_ok=True)
    t0 = time.time()
    con = connect(a.db)
    if a.rebuild:
        con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('trigram_all', ?)", ("1" if a.trigram_all else "0",))
    stats = update(con, a.docs, a.root)
    if a.optimize or a.rebuild:
        con.execute("INSERT INTO fts_words(fts_words) VALUES('optimize')")
        con.execute("INSERT INTO fts_cjk(fts_cjk) VALUES('optimize')")
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("VACUUM")
    n = con.execute("SELECT count(*) FROM docs").fetchone()[0]
    ns = con.execute("SELECT count(*) FROM sentences").fetchone()[0]
    con.close()
    size = a.db.stat().st_size / 1e6
    print(json.dumps({**stats, "docs": n, "sentences": ns, "seconds": round(time.time() - t0, 1),
                      "db_mb": round(size, 1)}))


if __name__ == "__main__":
    main()
