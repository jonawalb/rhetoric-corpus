"""Segmented document store: active docs/<CC>/<source>.jsonl buffers are sealed into immutable gzip parts.

Layout (local checkout and private store alike)
  docs/<CC>/<source>.jsonl                       active file: collectors append here under docs/<CC>/<source>.lock
  docs-parts/<YYYY-MM>/<stamp>-<writer>-<sha8>.jsonl.gz
                                                 sealed part: complete JSONL lines of many sources; uploaded once,
                                                 never rewritten (sha8 = first 8 hex of its sha256: names are unique)
  docs-parts/legacy/<CC>/<source>.jsonl.gz       the stored docs/*.jsonl of the pre-segment layout (migrated once)
  state/ids/<CC>/<source>.ids                    sealed-id index: one id per line ('#' lines are seal markers);
                                                 lib.write_docs / write_docs_fast / cn_common.Sink skip these ids
  staging/seal/                                  local only: journal (pending.json), the part being sealed, a log,
                                                 quarantined unparsable lines (bad/)

seal(uploader):
  1. under each source's lock, note N = end of the last complete line of the active file;
  2. read [0, N) without the lock (only a seal changes bytes before the end of a file, and seals are serialised by
     staging/seal/.lock): a line whose id is already sealed is dropped (it is in the store), an unparsable line is
     copied to staging/seal/bad/, every other line goes into the new part;
  3. write the journal, upload the part and VERIFY it in the store (sha256 + line count of a fresh download);
  4. finalize: append the new ids and a '# seal <stamp>' marker to each scanned source's id index (fsync), then,
     under the source's lock, check that [0, N) is unchanged and replace the file by its bytes after N (os.replace,
     so collectors appending meanwhile lose nothing; their next append opens the new file).
A crash leaves either nothing or the journal; the next seal resumes from the journal (finalize when the part
verifies in the store, otherwise discard it: nothing was cut yet). RETAIN sources are sealed but never cut, because
collectors read their full documents (not only ids) at start-up.
"""
from __future__ import annotations

import contextlib
import fcntl
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Protocol, Tuple

ROOT = Path(__file__).resolve().parent.parent
PARTS_DIR = "docs-parts"
# Sealed into the store but never cut locally: cn_mfa.py reads mfa_cn_live / mfa_cn_archive rows (t-numbers, titles)
# and ru_kremlin.py reads kremlin_en dates at start-up, which an id index cannot answer.
RETAIN = {"CN/mfa_cn_live", "CN/mfa_cn_archive", "RU/kremlin_en"}
_ID_RE = re.compile(rb'\{"id": "((?:[^"\\]|\\.)*)"')
logger = logging.getLogger("segments")


class Uploader(Protocol):
    def upload(self, local: Path, remote: str) -> None: ...

    def verify(self, remote: str, sha256: str, lines: int) -> bool: ...


# ---------------------------------------------------------------------------------------------- paths / reading
def ids_file(root: Path, country: str, source: str) -> Path:
    return root / "state" / "ids" / country.upper() / f"{source}.ids"


def part_remote(stamp: str, writer: str, sha256: str) -> str:
    return f"{PARTS_DIR}/{stamp[:4]}-{stamp[4:6]}/{stamp}-{writer}-{sha256[:8]}.jsonl.gz"


def writer_name() -> str:
    name = os.environ.get("RHETORIC_WRITER") or ("ci" if os.environ.get("GITHUB_ACTIONS") else socket.gethostname())
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name.split(".")[0]).strip("-").lower() or "local"


def part_files(root: Path) -> List[Path]:
    """Local parts in index order: the migrated legacy parts first, then by name (= seal time)."""
    base = root / PARTS_DIR
    if not base.exists():
        return []
    return sorted(base.rglob("*.jsonl.gz"), key=lambda p: (p.relative_to(base).parts[0] != "legacy",
                                                           p.relative_to(base).as_posix()))


def open_lines(path: Path) -> Iterator[bytes]:
    """Complete lines of a part (.jsonl.gz) or active file (.jsonl); a partial last line is skipped."""
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rb") as f:
        for line in f:
            if line.endswith(b"\n"):
                yield line


def line_id(line: bytes) -> Optional[str]:
    m = _ID_RE.match(line)
    if m:
        return json.loads(b'"' + m.group(1) + b'"')
    try:
        return json.loads(line).get("id")
    except (ValueError, AttributeError):
        return None


def read_ids(path: Path) -> set:
    if not path.exists():
        return set()
    with path.open("rb") as f:
        return {ln.rstrip(b"\n").decode("utf-8") for ln in f if ln.endswith(b"\n") and ln.strip()
                and not ln.startswith(b"#")}


def iter_docs(root: Path = ROOT, countries: Optional[Iterable[str]] = None) -> Iterator[Tuple[str, Dict]]:
    """(file label, document) for every document in the local parts and active files, each id once.
    Only parts present locally are read: `store_sync.py pull --only docs-parts` fetches all of them."""
    cc = {c.upper() for c in countries} if countries else None
    seen: set = set()
    files = part_files(root) + sorted((root / "docs").glob("*/*.jsonl"))
    for p in files:
        if cc and p.suffix == ".jsonl" and p.parent.name not in cc:
            continue
        label = p.relative_to(root).as_posix()
        for line in open_lines(p):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict) or d.get("id") in seen or (cc and str(d.get("country", "")).upper() not in cc):
                continue
            seen.add(d.get("id"))
            yield label, d


# ---------------------------------------------------------------------------------------------- locking
@contextlib.contextmanager
def seal_lock(root: Path, wait: bool = True):
    """Serialises seals and id-index merges in one checkout. Yields False when wait=False and it is held."""
    d = root / "staging" / "seal"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / ".lock", "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


@contextlib.contextmanager
def source_lock(active: Path):
    """The collectors' write lock for docs/<CC>/<source>.jsonl (as lib.write_docs)."""
    with open(active.with_suffix(".lock"), "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def append_ids(path: Path, ids: Iterable[str], marker: str) -> None:
    """Append ids plus a marker line (always, so cached readers notice the seal), fsync'd."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as f:
        f.write(b"".join(i.encode("utf-8") + b"\n" for i in ids))
        f.write(f"# {marker}\n".encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())


def merge_ids(local: Path, other: Path, marker: str) -> int:
    """Union `other` (e.g. the stored copy) into the local id index; returns ids added."""
    have = read_ids(local)
    new = [i for i in sorted(read_ids(other)) if i not in have]
    if new or not local.exists():
        append_ids(local, new, marker)
    return len(new)


# ---------------------------------------------------------------------------------------------- seal
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _prefix_sha1(path: Path, n: int) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        left = n
        while left > 0:
            chunk = f.read(min(1 << 22, left))
            if not chunk:
                break
            h.update(chunk)
            left -= len(chunk)
    return h.hexdigest()


def _complete_end(path: Path) -> int:
    """Offset just after the last newline of the file (0 if none)."""
    size = path.stat().st_size
    with path.open("rb") as f:
        pos = size
        while pos > 0:
            step = min(1 << 16, pos)
            f.seek(pos - step)
            buf = f.read(step)
            i = buf.rfind(b"\n")
            if i >= 0:
                return pos - step + i + 1
            pos -= step
    return 0


def count_gz_lines(path: Path) -> int:
    n = 0
    with gzip.open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            n += chunk.count(b"\n")
    return n


def _write_json(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)


def scan(root: Path, stamp: str, writer: str, sources: Optional[Iterable[str]] = None) -> dict:
    """Steps 1-2: build the part from the active files. Returns the journal (not yet written)."""
    sdir = root / "staging" / "seal"
    sdir.mkdir(parents=True, exist_ok=True)
    local_part = sdir / f"{stamp}-{writer}.jsonl.gz"
    want = set(sources) if sources else None
    files: Dict[str, dict] = {}
    new_ids: Dict[str, List[str]] = {}
    lines = dropped = bad = 0
    with local_part.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        for active in sorted((root / "docs").glob("*/*.jsonl")):
            key = f"{active.parent.name}/{active.stem}"
            if want is not None and key not in want:
                continue
            with source_lock(active):
                n = _complete_end(active)
            if n == 0:
                continue
            sealed = read_ids(ids_file(root, active.parent.name, active.stem))
            mine: List[str] = []
            seen: set = set()
            h = hashlib.sha1()
            read = 0
            with active.open("rb") as f:
                for line in f:
                    if read >= n:
                        break
                    if read + len(line) > n:  # cannot happen (n is a line end) unless the file changed: stop
                        break
                    read += len(line)
                    h.update(line)
                    did = line_id(line)
                    ok = did is not None
                    if ok:
                        try:
                            ok = isinstance(json.loads(line), dict)
                        except ValueError:
                            ok = False
                    if not ok:
                        if line.strip():
                            bd = sdir / "bad"
                            bd.mkdir(exist_ok=True)
                            with (bd / f"{active.parent.name}_{active.stem}.jsonl").open("ab") as q:
                                q.write(line)
                            bad += 1
                        continue
                    if did in sealed or did in seen:
                        dropped += 1
                        continue
                    seen.add(did)
                    mine.append(did)
                    gz.write(line)
                    lines += 1
            if read != n:
                raise RuntimeError(f"{active}: read {read} of {n} bytes; the file changed under the seal")
            files[f"{key}.jsonl"] = {"n": n, "sha1": h.hexdigest(), "retain": key in RETAIN, "cut": False}
            if mine:
                new_ids[key] = mine
    rec = {"stamp": stamp, "writer": writer, "local": str(local_part), "remote": None, "lines": lines, "dropped_sealed": dropped, "bad": bad, "files": files, "ids": new_ids,
           "uploaded": lines == 0, "ids_done": False}
    if lines:
        rec["sha256"], rec["bytes"] = _sha256(local_part), local_part.stat().st_size
        rec["remote"] = part_remote(stamp, writer, rec["sha256"])
    else:
        local_part.unlink()
    return rec


def finalize(root: Path, rec: dict, journal: Path) -> dict:
    """Step 4 (idempotent: resumable from the journal)."""
    if not rec["uploaded"]:
        raise RuntimeError("finalize before the part is verified in the store")
    marker = f"seal {rec['stamp']}-{rec['writer']}" + (f" {rec['remote']}" if rec["lines"] else "")
    if not rec["ids_done"]:
        for rel in rec["files"]:
            key = rel[:-len(".jsonl")]
            cc, src = key.split("/", 1)
            append_ids(ids_file(root, cc, src), rec["ids"].get(key, []), marker)
        rec["ids_done"] = True
        _write_json(journal, rec)
    cut = skipped = 0
    for rel, f in rec["files"].items():
        if f["retain"] or f["cut"]:
            continue
        active = root / "docs" / rel
        with source_lock(active):
            if active.exists() and active.stat().st_size >= f["n"] and _prefix_sha1(active, f["n"]) == f["sha1"]:
                tmp = active.with_suffix(".cut")
                with active.open("rb") as src, tmp.open("wb") as dst:
                    src.seek(f["n"])
                    shutil.copyfileobj(src, dst, 1 << 22)
                os.replace(tmp, active)
                f["cut"] = True
                cut += 1
            else:  # already cut by an interrupted finalize, or changed by someone else: never cut blindly
                logger.warning("%s: sealed prefix no longer matches; not cut (lines stay; the next seal drops "
                               "the sealed ones)", rel)
                f["cut"] = "skipped"
                skipped += 1
        _write_json(journal, rec)
    return {"cut": cut, "cut_skipped": skipped}


def seal(root: Path, uploader: Uploader, writer: Optional[str] = None, keep_part: bool = False,
         sources: Optional[Iterable[str]] = None, before: Optional[Callable[[], object]] = None) -> dict:
    """Seal every active file (or `sources`, 'CC/source') into one part. Returns a report including 'part'
    ({remote, sha256, bytes, lines} or None) for the store manifest. `before` runs under the seal lock first
    (store_sync: merge the stored id indexes into the local ones)."""
    sdir = root / "staging" / "seal"
    journal = sdir / "pending.json"
    with seal_lock(root, wait=False) as got:
        if not got:
            return {"skipped": "another seal is running"}
        rep: dict = {}
        if before is not None:
            rep["before"] = before()
        if journal.exists():
            rep["resumed"] = _resume(root, uploader, journal, keep_part)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        rec = scan(root, stamp, writer or writer_name(), sources)
        _write_json(journal, rec)
        if rec["lines"]:
            _upload_verified(uploader, rec)
            _write_json(journal, rec)
        rep.update(finalize(root, rec, journal))
        _done(root, rec, journal, keep_part)
        rep.update({k: rec[k] for k in ("lines", "dropped_sealed", "bad")})
        rep["files"] = len(rec["files"])
        rep["part"] = _part_entry(rec)
        return rep


def _part_entry(rec: dict) -> Optional[dict]:
    if not rec["lines"]:
        return None
    return {"remote": rec["remote"], "sha256": rec["sha256"], "bytes": rec["bytes"], "lines": rec["lines"]}


def _upload_verified(uploader: Uploader, rec: dict) -> None:
    if not uploader.verify(rec["remote"], rec["sha256"], rec["lines"]):
        uploader.upload(Path(rec["local"]), rec["remote"])
        if not uploader.verify(rec["remote"], rec["sha256"], rec["lines"]):
            raise RuntimeError(f"{rec['remote']}: uploaded part does not verify in the store; nothing cut")
    rec["uploaded"] = True


def _resume(root: Path, uploader: Uploader, journal: Path, keep_part: bool) -> dict:
    rec = json.loads(journal.read_text())
    local = Path(rec["local"])
    if not rec["uploaded"]:
        if uploader.verify(rec["remote"], rec["sha256"], rec["lines"]):
            rec["uploaded"] = True
        elif local.exists() and _sha256(local) == rec["sha256"]:
            _upload_verified(uploader, rec)
        else:  # nothing was cut and no id appended before the upload verified: safe to forget
            logger.warning("discarding unfinished seal %s (part not in the store)", rec["remote"])
            local.unlink(missing_ok=True)
            journal.unlink()
            return {"discarded": rec["remote"]}
        _write_json(journal, rec)
    out = finalize(root, rec, journal)
    _done(root, rec, journal, keep_part)
    return {"finalized": rec["remote"], **out, "part": _part_entry(rec)}


def _done(root: Path, rec: dict, journal: Path, keep_part: bool) -> None:
    local = Path(rec["local"])
    if rec["lines"] and local.exists():
        if keep_part:  # CI: the index build reads it from docs-parts/
            dest = root / rec["remote"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(local, dest)
        else:
            local.unlink()
    log = {k: v for k, v in rec.items() if k not in ("ids", "files")}
    log["sources"] = len(rec["files"])
    log["ids"] = sum(len(v) for v in rec["ids"].values())
    with (root / "staging" / "seal" / "log.jsonl").open("a") as f:
        f.write(json.dumps(log) + "\n")
    journal.unlink(missing_ok=True)


# ---------------------------------------------------------------------------------------------- legacy migration
def legacy_part(src: Path, dest: Path) -> Tuple[List[str], int, int]:
    """Gzip one stored legacy docs/<CC>/<source>.jsonl into a part: complete, parsable lines, first copy of each
    id. Returns (ids, lines, bad lines). Bad lines go to dest.parent/<name>.bad (kept)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    ids: List[str] = []
    seen: set = set()
    bad = 0
    with dest.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz, \
            src.open("rb") as f:
        for line in f:
            if not line.endswith(b"\n"):
                line += b"\n"  # a stored file always ends complete; be safe
            did = line_id(line)
            try:
                ok = did is not None and isinstance(json.loads(line), dict)
            except ValueError:
                ok = False
            if not ok:
                if line.strip():
                    with dest.with_suffix(".bad").open("ab") as q:
                        q.write(line)
                    bad += 1
                continue
            if did in seen:
                continue
            seen.add(did)
            ids.append(did)
            gz.write(line)
    return ids, len(ids), bad


def materialize(root: Path, dest: Path, countries: Optional[Iterable[str]] = None) -> Dict[str, int]:
    """Write every document (local parts + active files, each id once) to dest/<CC>/<source>.jsonl, for tools that
    read per-source files (export_dataset.py). Refuses when an id the local id indexes list as sealed is not in a
    local part (fetch them: store_sync.py pull --only docs-parts). Returns documents per <CC>/<source>."""
    dest.mkdir(parents=True, exist_ok=True)
    counts: Dict[str, int] = {}
    seen: Dict[str, set] = {}
    handles: Dict[str, object] = {}
    try:
        for _, d in iter_docs(root, countries):
            key = f"{str(d.get('country', '')).upper()}/{d.get('source')}"
            if key not in handles:
                (dest / key).parent.mkdir(parents=True, exist_ok=True)
                handles[key] = (dest / f"{key}.jsonl").open("w", encoding="utf-8")
            handles[key].write(json.dumps(d, ensure_ascii=False) + "\n")
            counts[key] = counts.get(key, 0) + 1
            seen.setdefault(key, set()).add(d["id"])
    finally:
        for h in handles.values():
            h.close()
    cc = {c.upper() for c in countries} if countries else None
    missing = 0
    for f in sorted((root / "state" / "ids").glob("*/*.ids")):
        if cc and f.parent.name not in cc:
            continue
        missing += len(read_ids(f) - seen.get(f"{f.parent.name}/{f.stem}", set()))
    if missing:
        raise SystemExit(f"{missing} sealed documents are not in the local parts; fetch them first: "
                         "uv run python scripts/store_sync.py pull --only docs-parts")
    return counts
