"""Sync the private corpus store (Hugging Face dataset repo, private) with this checkout.

The nightly GitHub Actions job has no disk that survives between runs: it pulls the store, collects, rebuilds, and
pushes the store back. The store holds everything a run needs to continue where the last one stopped:

  docs-parts/**/*.jsonl.gz       collected documents, sealed into immutable parts (scripts/segments.py; layout 2)
  state/ids/<CC>/<source>.ids    sealed-id index per source: collectors skip these ids (merged by union on push/pull)
  docs/**/*.jsonl                layout 1 only (before the one-time `migrate`): whole per-source files, append-only
  state/*.json, state/*.cookies  collector resume state
  raw/<CDX/sitemap caches>       the few listing caches collectors read instead of re-asking Wayback (RAW_DIRS)
  index/semantic/**              semantic store for incremental runs (models excluded: they come from the Hub)
  reports/**                     briefs and semantic reports (they quote texts, so they live here, not in git)
  logs/ci/<date>/                collector logs of the last 14 CI runs (private: they name URLs and titles)
  index/corpus.sqlite            search index: stored so build_index.py stays incremental and corpus rowids stay stable
                                 (a rebuild renumbers every document, and the semantic store then re-tags all of them)

manifest.json (repo root) lists every stored file with its SHA-256 and size. `pull` downloads only files whose
local hash differs; `push` uploads only files whose hash differs from the remote manifest, then the new manifest in
a final commit (so the manifest never names a file that is not uploaded). Safety rules for push:
  * a docs/ file smaller than its stored copy is NOT uploaded (append-only; --allow-shrink overrides)
  * remote files missing locally are deleted only under index/semantic/ and only after a complete pull this run
    (marker .store_pulled), so a failed pull can never turn into deletions; docs/state/raw/reports are never deleted
    (logs/ci/ follows the same rule as index/semantic/)
  * SQLite files are checkpointed (WAL folded in) before hashing
  * a docs/ file changed in the store by someone else since this checkout pulled is merged by document id first
    (stored lines + local lines with new ids), so two writers never lose documents; other files: last push wins

Layout 2 (manifest.json "layout": 2, after `migrate`): local docs/<CC>/<source>.jsonl are only active buffers.
`seal` cuts their complete lines into a new part, uploads it, verifies it in the store (sha256 + line count), records
the ids in state/ids/ and only then cuts the sealed lines from the local files (scripts/segments.py). Parts are never
re-uploaded or rewritten; `push` never uploads docs/*.jsonl; `pull` downloads only the parts the stored index has not
indexed yet (index/corpus.sqlite `files` table), unless --only docs-parts asks for all of them.

Usage:
  uv run python scripts/store_sync.py pull   [--only docs,state]     # download changed files (+ parts not yet indexed)
  uv run python scripts/store_sync.py pull --only docs-parts         # every part (exports, full rebuilds)
  uv run python scripts/store_sync.py seal   [--keep-parts]          # layout 2: seal active docs into a verified part
  uv run python scripts/store_sync.py migrate                        # one time: layout 1 docs/*.jsonl -> parts
  uv run python scripts/store_sync.py layout                         # print the store layout (1 or 2)
  uv run python scripts/store_sync.py repair                         # layout 2: seal stray stored docs/*.jsonl
  uv run python scripts/store_sync.py push   [--dry-run]             # upload changed files
  uv run python scripts/store_sync.py push --only docs               # laptop: merge its extra documents into the store
  uv run python scripts/store_sync.py verify                         # compare local files with the stored manifest
  uv run python scripts/store_sync.py squash                         # drop old store history (frees Hub storage)
Repo: env RHETORIC_STORE_REPO (default wallabee1/rhetoric-corpus-store); token: HF_TOKEN or `hf auth login`.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import logging
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
REPO = os.environ.get("RHETORIC_STORE_REPO", "wallabee1/rhetoric-corpus-store")
MANIFEST = "manifest.json"
MARKER = ROOT / ".store_pulled"
RAW_DIRS = ("by_president", "ir_presstv", "kp_rodong_en", "ir_khamenei_en")
INCLUDE = ["docs/*/*.jsonl", "docs-parts/*.jsonl.gz", "state/ids/*.ids", "state/*.json", "state/*.done.txt", "state/*.cookies", *[f"raw/{d}/*" for d in RAW_DIRS],
           "index/corpus.sqlite", "index/semantic/*", "index/semantic/**/*", "reports/*", "reports/**/*", "logs/ci/*"]
EXCLUDE = ["index/semantic/models/*", "*.sqlite-wal", "*.sqlite-shm", "*.tmp", "*.lock", "*/.*", ".*"]
DELETABLE = ("index/semantic/", "logs/ci/")
APPEND_ONLY = ("docs/",)
PARTS = "docs-parts/"
IDS = "state/ids/"
_LAYOUT = {"remote": 1}  # layout of the store as of the last remote_manifest() read (only ever raised)
# Layout is sticky. 2026-10-04 an older checkout's push rewrote manifest.json without the "layout" key and silently
# turned the store back into layout 1. Since then layout 2 is recorded three ways, and the highest one wins:
#   * the LAYOUT marker file at the store root (no writer of any version ever deletes it);
#   * manifest.json {"layout": 2, "entries": {...}}: the file list moved from "files" to "entries", so code that
#     predates the segmented store fails with KeyError instead of rewriting the manifest;
#   * a layout read in this process never goes down again (_raise_layout), and a checkout that has once seen
#     layout 2 remembers it (staging/seal/layout): if the store then reads lower, store_sync raises.
LAYOUT_FILE = "LAYOUT"
CARD = """---
viewer: false
---
# rhetoric-corpus store (private)

Working store of a research text collection: collector state, collected documents and derived indexes, synced by the
nightly job of github.com/jonawalb/rhetoric-corpus (scripts/store_sync.py). Private; not for redistribution.
"""
logger = logging.getLogger("store_sync")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import segments  # noqa: E402


def wanted(rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, p) for p in INCLUDE) and not any(fnmatch.fnmatch(rel, p) for p in EXCLUDE)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def checkpoint_sqlite(root: Path) -> None:
    """Fold WAL files into their databases so the stored .sqlite is complete on its own."""
    for db in [*(root / "index").glob("*.sqlite"), *(root / "index" / "semantic").glob("*.sqlite")]:
        try:
            con = sqlite3.connect(db, timeout=60)
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con.close()
        except sqlite3.Error as e:
            raise SystemExit(f"cannot checkpoint {db}: {e} (is another process writing it?)")


def local_manifest(root: Path, only: List[str] | None = None) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    only = _expand_only(only)
    for top in ("docs", "docs-parts", "state", "raw", "index", "reports", "logs"):
        base = root / top
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if wanted(rel) and (not only or any(rel.startswith(o.rstrip("/") + "/") for o in only)):
                out[rel] = {"sha256": sha256(p), "bytes": p.stat().st_size}
    return out


def _expand_only(only: List[str] | None) -> List[str] | None:
    """--only docs also means the sealed parts (docs-parts/); state includes state/ids/."""
    if not only:
        return only
    out = list(only)
    if "docs" in out and "docs-parts" not in out:
        out.append("docs-parts")
    return out


def _in_only(rel: str, only: List[str] | None) -> bool:
    return not only or any(rel.startswith(o.rstrip("/") + "/") for o in only)


def api():
    from huggingface_hub import HfApi
    return HfApi()


def _raise_layout(n: int) -> None:
    _LAYOUT["remote"] = max(_LAYOUT["remote"], n)
    if n >= 2:
        f = ROOT / "staging" / "seal" / "layout"
        if not f.exists() or int(f.read_text().strip() or 0) < n:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"{n}\n")


def _seen_layout() -> int:
    try:
        return int((ROOT / "staging" / "seal" / "layout").read_text().strip() or 1)
    except (OSError, ValueError):
        return 1


def remote_manifest(hf, repo: str, allow_downgraded: bool = False) -> Dict[str, dict]:
    """Stored file list; also sets the store layout (max of the LAYOUT marker, the manifest key and earlier reads).
    A store with the LAYOUT marker but no readable manifest raises instead of looking empty."""
    from huggingface_hub import hf_hub_download
    if not hf.repo_exists(repo, repo_type="dataset"):
        return {}
    if hf.file_exists(repo, LAYOUT_FILE, repo_type="dataset"):
        _raise_layout(int(Path(hf_hub_download(repo, LAYOUT_FILE, repo_type="dataset", force_download=True))
                          .read_text().strip() or 2))
    if not hf.file_exists(repo, MANIFEST, repo_type="dataset"):
        if layout2():
            raise SystemExit(f"{repo}: layout {_LAYOUT['remote']} store without {MANIFEST}; refusing to treat it as empty")
        return {}
    path = hf_hub_download(repo, MANIFEST, repo_type="dataset", force_download=True)
    meta = json.loads(Path(path).read_text())
    _raise_layout(int(meta.get("layout", 1)))
    if _seen_layout() > _LAYOUT["remote"] and not allow_downgraded:
        raise SystemExit(f"{repo} reads as layout {_LAYOUT['remote']} but this checkout has seen layout "
                         f"{_seen_layout()}: the layout was downgraded by some writer; run `store_sync.py repair`")
    return meta["entries"] if "entries" in meta else meta["files"]


def layout2() -> bool:
    return _LAYOUT["remote"] >= 2


def remote_parts(hf, repo: str, manifest: Dict[str, dict]) -> Dict[str, dict]:
    """Every stored part: the manifest's entries plus the repo tree (authoritative if a manifest write was lost)."""
    out = {p: v for p, v in manifest.items() if p.startswith(PARTS)}
    try:
        for f in hf.list_repo_tree(repo, path_in_repo=PARTS.rstrip("/"), recursive=True, repo_type="dataset"):
            if getattr(f, "size", None) is None or not f.path.endswith(".jsonl.gz") or f.path in out:
                continue
            lfs = getattr(f, "lfs", None)
            out[f.path] = {"sha256": getattr(lfs, "sha256", None) if lfs else None, "bytes": f.size}
    except Exception as e:  # noqa: BLE001 - no docs-parts/ folder yet
        logger.info("no part listing (%s)", type(e).__name__)
    return out


def indexed_parts(db: Path) -> set:
    """Parts already in index/corpus.sqlite (build_index.py records each part it indexed in `files`)."""
    if not db.exists():
        return set()
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return {r[0] for r in con.execute("SELECT path FROM files WHERE path LIKE 'docs-parts/%'")}
        finally:
            con.close()
    except sqlite3.Error as e:
        logger.warning("cannot read indexed parts from %s: %s", db, e)
        return set()


def diff(local: Dict[str, dict], remote: Dict[str, dict]) -> Tuple[List[str], List[str]]:
    """(paths whose local copy differs from or is missing in remote, remote paths missing locally)."""
    changed = sorted(p for p, v in local.items() if remote.get(p, {}).get("sha256") != v["sha256"])
    missing = sorted(p for p in remote if p not in local)
    return changed, missing


def _download(repo: str, p: str, tries: int = 3) -> Path:
    from huggingface_hub import hf_hub_download
    for i in range(tries):
        try:
            return Path(hf_hub_download(repo, p, repo_type="dataset", local_dir=ROOT / ".store_dl",
                                        force_download=i > 0))
        except (RuntimeError, OSError) as e:  # e.g. a transient Xet "File size mismatch"
            if i == tries - 1:
                raise
            logger.warning("%s: download failed (%s); retrying", p, e)
            time.sleep(5 * (i + 1))
    raise AssertionError("unreachable")


def _snapshot(rel: str, dest_root: Path) -> Path:
    """Copy ROOT/rel to dest_root/rel so the bytes uploaded (and hashed) cannot change mid-upload while collectors
    keep appending. docs/*.jsonl are copied under the collectors' write lock (as lib.write_docs)."""
    import fcntl
    import shutil
    src, dst = ROOT / rel, dest_root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if rel.startswith("docs/") and rel.endswith(".jsonl"):
        with open(src.with_suffix(".lock"), "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            shutil.copyfile(src, dst)
    else:
        shutil.copyfile(src, dst)
    return dst


def pull(repo: str, only: List[str] | None, workers: int = 8, all_parts: bool = False) -> dict:
    """Download changed files. Parts (docs-parts/) are immutable: only those the stored index has not indexed yet are
    fetched, or all of them with all_parts / --only docs-parts. A stored id index is merged into an existing local
    one by union (never replaced: local ids may not be pushed yet)."""
    hf = api()
    MARKER.unlink(missing_ok=True)
    remote = remote_manifest(hf, repo)
    if not remote:
        raise SystemExit(f"{repo}: no {MANIFEST}; nothing to pull (run the one-time migration push first)")
    all_parts = all_parts or bool(only and "docs-parts" in only)
    sel = {p: v for p, v in remote.items() if not p.startswith(PARTS) and _in_only(p, only)}
    todo = []
    for p, v in sel.items():
        f = ROOT / p
        if not (f.exists() and f.stat().st_size == v["bytes"] and sha256(f) == v["sha256"]):
            todo.append(p)
    t0, got = time.time(), 0
    synced_now: Dict[str, str] = {}

    def one(p: str) -> int:
        tmp = _download(repo, p)
        h = sha256(tmp)
        if remote[p].get("sha256") and h != remote[p]["sha256"]:  # a concurrent push replaced the file: keep it
            logger.warning("%s: stored file is newer than the manifest; using the stored file", p)
        remote[p] = {**remote[p], "sha256": h, "bytes": tmp.stat().st_size}
        dest = ROOT / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        if p.startswith(IDS) and dest.exists():
            with segments.seal_lock(ROOT):
                segments.merge_ids(dest, tmp, f"merge store {time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
            tmp.unlink()
        else:
            os.replace(tmp, dest)
        if p.startswith(IDS):
            synced_now[p] = h
        return remote[p]["bytes"]

    with ThreadPoolExecutor(workers) as ex:
        for n in ex.map(one, todo):
            got += n
    if synced_now:
        _ids_synced({**_ids_synced(), **synced_now})
    parts_rep = {}
    if _in_only(PARTS, only) or all_parts:  # after index/corpus.sqlite is in place: it says what is indexed
        parts = remote_parts(hf, repo, remote)
        done = set() if all_parts else indexed_parts(ROOT / "index" / "corpus.sqlite")
        ptodo = []
        for p, v in parts.items():
            f = ROOT / p
            if p in done or (f.exists() and f.stat().st_size == v["bytes"]):  # immutable: same size = same part
                continue
            ptodo.append(p)
            remote.setdefault(p, v)
        with ThreadPoolExecutor(workers) as ex:
            for n in ex.map(one, ptodo):
                got += n
        parts_rep = {"parts_stored": len(parts), "parts_indexed": len(done & set(parts)), "parts_downloaded": len(ptodo)}
        todo += ptodo
    if not only:  # the snapshot push() merges against, and permission to mirror deletions under DELETABLE
        MARKER.write_text(json.dumps({"repo": repo, "time": time.time(), "manifest": remote}))
    rep = {"stored": len(sel), "downloaded": len(todo), "mb": round(got / 1e6, 1), "seconds": round(time.time() - t0),
           "layout": _LAYOUT["remote"], **parts_rep}
    logger.info("pull %s: %s", repo, rep)
    return rep


def merge_jsonl(local: Path, stored: Path) -> int:
    """Rewrite `local` as the stored file plus every local line whose id the stored file lacks. Returns lines added.
    Holds the collectors' write lock (docs/<CC>/<source>.lock, as lib.write_docs) while it rewrites the file."""
    import fcntl
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collectors"))
    from lib import existing_ids  # noqa: E402
    have = existing_ids(stored)
    with open(local.with_suffix(".lock"), "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        extra = []
        with local.open("rb") as f:
            for line in f:
                if not line.endswith(b"\n"):
                    continue  # a cut last line
                try:
                    did = json.loads(line)["id"]
                except (ValueError, KeyError):
                    continue
                if did not in have:
                    have.add(did)
                    extra.append(line)
        tmp = local.with_suffix(".merge")
        last = b"\n"
        with tmp.open("wb") as out, stored.open("rb") as src:
            for chunk in iter(lambda: src.read(1 << 22), b""):
                out.write(chunk)
                last = chunk[-1:]
            if last != b"\n":
                out.write(b"\n")
            out.writelines(extra)
        os.replace(tmp, local)
    return len(extra)


def push(repo: str, dry_run: bool = False, allow_shrink: bool = False, only: List[str] | None = None,
         batch: int = 100, no_merge: bool = False, extra: Dict[str, dict] | None = None) -> dict:
    """Upload changed files. A docs file that someone else changed in the store since our pull (or, without a pull
    this run, any docs file that differs) is first merged by document id, so concurrent writers never lose lines.
    Layout 2: active docs/*.jsonl are never uploaded (seal() stores them as parts), a stored part is never replaced,
    and an id index that differs from the stored one is first merged with it by union. `extra` adds manifest entries
    for files already uploaded (a sealed part)."""
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete
    hf = api()
    if not only or any(o.startswith("index") for o in only):
        checkpoint_sqlite(ROOT)
    local = local_manifest(ROOT, only)
    remote = remote_manifest(hf, repo)
    pulled = json.loads(MARKER.read_text()).get("manifest") if MARKER.exists() else None
    if layout2():
        local = {p: v for p, v in local.items() if not p.startswith(APPEND_ONLY)}
        for p in [p for p, v in local.items() if p.startswith(PARTS) and p in remote
                  and remote[p].get("sha256") != v["sha256"]]:
            logger.warning("%s: differs from the stored part; parts are immutable, not uploading", p)
            local[p] = remote[p]
        unlisted = [p for p in local if p.startswith(PARTS) and p not in remote]
        if unlisted:  # stored but missing from the manifest (a lost manifest write): list it, do not re-upload
            tree = remote_parts(hf, repo, {})
            for p in unlisted:
                if p in tree and tree[p]["bytes"] == local[p]["bytes"]:
                    remote[p] = local[p]
        synced = _ids_synced()
        for p in [p for p, v in local.items() if p.startswith(IDS) and p in remote
                  and remote[p]["sha256"] != v["sha256"] and not dry_run]:
            if synced.get(p) != remote[p]["sha256"]:  # the stored index has ids this checkout lacks: union first
                with segments.seal_lock(ROOT):
                    segments.merge_ids(ROOT / p, _download(repo, p),
                                       f"merge store {time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
            local[p] = {"sha256": sha256(ROOT / p), "bytes": (ROOT / p).stat().st_size}
    changed, missing = diff(local, remote)
    merged = 0
    for p in [p for p in changed if p.startswith(APPEND_ONLY) and p in remote and not no_merge]:
        if pulled is not None and pulled.get(p, {}).get("sha256") == remote[p]["sha256"]:
            continue  # nobody else touched it: our copy extends the stored one
        if dry_run:
            merged += 1
            continue
        merged += 1
        n = merge_jsonl(ROOT / p, _download(repo, p))
        local[p] = {"sha256": sha256(ROOT / p), "bytes": (ROOT / p).stat().st_size}
        logger.info("merged %s with the stored copy: %d local documents added", p, n)
    changed, missing = diff(local, remote)
    shrunk = [p for p in changed if p.startswith(APPEND_ONLY) and p in remote and local[p]["bytes"] < remote[p]["bytes"]]
    if shrunk and not allow_shrink:
        logger.warning("NOT uploading %d docs file(s) smaller than the stored copy: %s", len(shrunk), shrunk[:10])
        changed = [p for p in changed if p not in shrunk]
        for p in shrunk:
            local[p] = remote[p]
    deletes = [p for p in missing if p.startswith(DELETABLE)] if (MARKER.exists() and not only) else []
    keep = {p: remote[p] for p in missing if p not in deletes}  # stays in the manifest: still stored, not here
    new_manifest = {**keep, **local, **(extra or {})}
    rep = {"local_files": len(local), "upload": len(changed), "upload_mb": round(sum(local[p]["bytes"] for p in changed) / 1e6, 1),
           "merged_docs_files": merged, "delete": len(deletes), "kept_remote_only": len(keep), "refused_shrink": len(shrunk)}
    logger.info("push plan for %s: %s", repo, rep)
    if dry_run:
        return rep
    if layout2() and not changed and not deletes and not extra:
        return rep  # nothing to store: no empty manifest commit (seal runs every few hours)
    hf.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)
    if not hf.repo_info(repo, repo_type="dataset").private:
        raise SystemExit(f"{repo} is not private; refusing to push")
    t0 = time.time()
    import shutil
    import tempfile
    snap_root = Path(tempfile.mkdtemp(prefix="store_snap_", dir=ROOT))
    try:
        for i in range(0, len(changed), batch):
            part = changed[i:i + batch]
            snaps = {p: _snapshot(p, snap_root) for p in part}
            for p, f in snaps.items():  # the manifest describes exactly the bytes uploaded
                new_manifest[p] = local[p] = {"sha256": sha256(f), "bytes": f.stat().st_size}
            hf.create_commit(repo, repo_type="dataset", commit_message=f"store: {len(part)} files",
                             operations=[CommitOperationAdd(p, str(f)) for p, f in snaps.items()])
            for f in snaps.values():
                f.unlink()
            logger.info("  uploaded %d/%d", min(i + batch, len(changed)), len(changed))
    finally:
        shutil.rmtree(snap_root, ignore_errors=True)
    # Re-read the stored manifest right before replacing it, so entries another writer added meanwhile (e.g. the
    # laptop's sealed parts during a CI run) are kept: ours win only for the files this push uploaded.
    planned_layout2 = layout2()
    fresh = remote_manifest(hf, repo)
    if layout2() and not planned_layout2:  # planned as layout 1 (e.g. docs files merged + uploaded): do not record it
        raise SystemExit(f"{repo} became layout {_LAYOUT['remote']} during this push; refusing to write a layout-1 "
                         "manifest (re-run: the push is planned again for the current layout)")
    ours = set(changed) | set(extra or {})
    new_manifest = {**fresh, **{p: new_manifest[p] for p in ours}, **{p: v for p, v in new_manifest.items() if p not in fresh}}
    for p in deletes:
        new_manifest.pop(p, None)
    ops = [CommitOperationDelete(p) for p in deletes]
    ops += [CommitOperationAdd("README.md", CARD.encode()), *_manifest_ops(new_manifest)]
    hf.create_commit(repo, repo_type="dataset", commit_message="store: manifest", operations=ops)
    if any(p.startswith(IDS) for p in changed):
        _ids_synced({**_ids_synced(), **{p: new_manifest[p]["sha256"] for p in changed if p.startswith(IDS)}})
    if MARKER.exists() and not only:  # what we just stored is the new base for a later push in this checkout
        MARKER.write_text(json.dumps({"repo": repo, "time": time.time(), "manifest": new_manifest}))
    rep["seconds"] = round(time.time() - t0)
    logger.info("push done: %s", rep)
    return rep


def _manifest_ops(files: Dict[str, dict], layout: int | None = None) -> list:
    """Commit operations that store the manifest (+ the LAYOUT marker from layout 2 on). Never writes a layout
    below the highest one this process has seen."""
    from huggingface_hub import CommitOperationAdd
    if layout is not None:
        _raise_layout(layout)
    lay = _LAYOUT["remote"]
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if lay < 2:
        return [CommitOperationAdd(MANIFEST, json.dumps({"updated": stamp, "files": files}, indent=0,
                                                        sort_keys=True).encode())]
    meta = {"updated": stamp, "layout": lay, "entries": files}
    return [CommitOperationAdd(MANIFEST, json.dumps(meta, indent=0, sort_keys=True).encode()),
            CommitOperationAdd(LAYOUT_FILE, f"{lay}\n".encode())]


# ---------------------------------------------------------------------------------------------- layout 2: seal
class HubUploader:
    """segments.Uploader for the private store: one commit per part; verify = stored sha256 (LFS pointer) and a
    fresh download whose sha256 and line count match."""

    def __init__(self, hf, repo: str):
        self.hf, self.repo = hf, repo

    def upload(self, local: Path, remote: str) -> None:
        from huggingface_hub import CommitOperationAdd
        if self.hf.get_paths_info(self.repo, [remote], repo_type="dataset"):
            raise RuntimeError(f"{remote} already stored with other content; parts are never overwritten")
        self.hf.create_commit(self.repo, repo_type="dataset", commit_message=f"store: part {Path(remote).name}",
                              operations=[CommitOperationAdd(remote, str(local))])

    def verify(self, remote: str, sha: str, lines: int) -> bool:
        from huggingface_hub import hf_hub_download
        try:
            info = self.hf.get_paths_info(self.repo, [remote], repo_type="dataset")
        except Exception as e:  # noqa: BLE001
            logger.warning("%s: cannot stat (%s)", remote, e)
            return False
        if not info:
            return False
        lfs = getattr(info[0], "lfs", None)
        if lfs is not None and lfs.sha256 != sha:
            return False
        tmp = Path(hf_hub_download(self.repo, remote, repo_type="dataset", local_dir=ROOT / ".store_dl" / "verify",
                                   force_download=True))
        try:
            ok = sha256(tmp) == sha and segments.count_gz_lines(tmp) == lines
        finally:
            tmp.unlink(missing_ok=True)
        if not ok:
            logger.error("%s: stored part does not match (sha256/lines)", remote)
        return ok


def refresh_ids(hf, repo: str, remote: Dict[str, dict]) -> int:
    """Union every stored id index this checkout has not merged or uploaded yet into the local one (caller holds
    the seal lock). Returns ids added."""
    n = 0
    synced = _ids_synced()
    for p, v in remote.items():
        if not p.startswith(IDS) or synced.get(p) == v["sha256"]:
            continue
        f = ROOT / p
        if not (f.exists() and sha256(f) == v["sha256"]):
            n += segments.merge_ids(f, _download(repo, p), f"merge store {time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
        synced[p] = v["sha256"]
    _ids_synced(synced)
    return n


def _ids_synced(update: Dict[str, str] | None = None) -> Dict[str, str]:
    """stored id index path -> sha256 of the stored version already contained in the local file (local cache, so
    an unchanged stored index is not downloaded again on every seal)."""
    f = ROOT / "staging" / "seal" / "ids_synced.json"
    if update is not None:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(update))
        os.replace(tmp, f)
        return update
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return {}


def seal(repo: str, keep_parts: bool = False, writer: str | None = None) -> dict:
    """Layout 2: seal the active docs files into one verified part, then push the id indexes + manifest."""
    hf = api()
    remote = remote_manifest(hf, repo)
    if not layout2():
        logger.info("store %s is layout 1 (not migrated): nothing sealed", repo)
        return {"layout": 1, "sealed": 0}
    rep = segments.seal(ROOT, HubUploader(hf, repo), writer=writer, keep_part=keep_parts,
                        before=lambda: refresh_ids(hf, repo, remote))
    if rep.get("skipped"):
        logger.info("seal skipped: %s", rep["skipped"])
        return rep
    parts = [x["part"] for x in (rep, rep.get("resumed") or {}) if x.get("part")]
    extra = {x["remote"]: {"sha256": x["sha256"], "bytes": x["bytes"], "lines": x["lines"]} for x in parts}
    rep["push"] = push(repo, only=["state/ids"], extra=extra)
    logger.info("seal: %s", {k: v for k, v in rep.items() if k != "push"})
    return rep


def migrate(repo: str, force: bool = False) -> dict:
    """One time: every stored docs/<CC>/<source>.jsonl -> docs-parts/legacy/<CC>/<source>.jsonl.gz (verified in the
    store) + state/ids/<CC>/<source>.ids; then one commit deletes the old files and marks the manifest layout 2.
    Local active files are cut by the first seal() afterwards (their stored lines are then in the id index)."""
    import subprocess
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete
    if not force:
        r = subprocess.run(["gh", "run", "list", "--repo", "jonawalb/rhetoric-corpus", "--status", "in_progress",
                            "--json", "databaseId", "-q", ".[].databaseId"], capture_output=True, text=True)
        if r.returncode != 0 or r.stdout.strip():
            raise SystemExit(f"a CI run is in progress (or gh failed: {r.stderr.strip()}); not migrating")
    hf = api()
    remote = remote_manifest(hf, repo)
    if layout2():
        return {"layout": 2, "note": "already migrated"}
    legacy = sorted(p for p in remote if p.startswith("docs/") and p.endswith(".jsonl"))
    work = ROOT / "staging" / "migrate"
    up = HubUploader(hf, repo)
    entries: Dict[str, dict] = {}
    ids_new: Dict[str, Path] = {}
    bad_total = 0
    with segments.seal_lock(ROOT):
        for p in legacy:
            cc, src = p.split("/")[1], Path(p).stem
            dl = _download(repo, p)
            if sha256(dl) != remote[p]["sha256"]:
                raise SystemExit(f"{p}: stored file changed since the manifest was read; re-run migrate")
            rp = f"{PARTS}legacy/{cc}/{src}.jsonl.gz"
            dest = work / rp
            ids, n, bad = segments.legacy_part(dl, dest)
            dl.unlink()
            bad_total += bad
            idf = work / IDS / cc / f"{src}.ids"
            idf.parent.mkdir(parents=True, exist_ok=True)
            idf.write_text("".join(i + "\n" for i in ids) + "# migrated from " + p + "\n", encoding="utf-8")
            ids_new[f"{IDS}{cc}/{src}.ids"] = idf
            entries[rp] = {"sha256": sha256(dest), "bytes": dest.stat().st_size, "lines": n, "from": p,
                           "from_sha256": remote[p]["sha256"]}
            logger.info("%s -> %s: %d documents, %d bad lines", p, rp, n, bad)
        todo = [rp for rp, v in entries.items() if not up.verify(rp, v["sha256"], v["lines"])]
        for i in range(0, len(todo), 20):
            ops = [CommitOperationAdd(rp, str(work / rp)) for rp in todo[i:i + 20]]
            hf.create_commit(repo, repo_type="dataset", commit_message=f"store: legacy parts {i + len(ops)}/{len(todo)}",
                             operations=ops)
        failed = [rp for rp, v in entries.items() if not up.verify(rp, v["sha256"], v["lines"])]
        if failed:
            raise SystemExit(f"parts do not verify in the store: {failed[:5]}; nothing deleted")
        fresh = remote_manifest(hf, repo)
        moved = [p for p in legacy if fresh.get(p, {}).get("sha256") != remote[p]["sha256"]]
        if moved or layout2():
            raise SystemExit(f"stored docs changed during the migration ({moved[:5]}); re-run migrate")
        files = {p: v for p, v in fresh.items() if p not in legacy}
        files.update(entries)
        for rel, f in ids_new.items():
            files[rel] = {"sha256": sha256(f), "bytes": f.stat().st_size}
        ops = [CommitOperationDelete(p) for p in legacy]
        ops += [CommitOperationAdd(rel, str(f)) for rel, f in ids_new.items()]
        ops += [CommitOperationAdd("README.md", CARD.encode()), *_manifest_ops(files, layout=2)]
        hf.create_commit(repo, repo_type="dataset", commit_message="store: segmented layout (docs -> docs-parts)",
                         operations=ops)
        _LAYOUT["remote"] = 2
        for rel, f in ids_new.items():  # the store now says these ids are sealed: so may the local index
            segments.merge_ids(ROOT / rel, f, "migrated legacy store")
    MARKER.unlink(missing_ok=True)
    shutil_rm(work)
    rep = {"legacy_files": len(legacy), "parts": len(entries), "documents": sum(v["lines"] for v in entries.values()),
           "bad_lines": bad_total, "parts_mb": round(sum(v["bytes"] for v in entries.values()) / 1e6, 1)}
    logger.info("migrated: %s", rep)
    return rep


def repair(repo: str, force: bool = False) -> dict:
    """Layout 2 store with stray docs/<CC>/<source>.jsonl (uploaded by a writer that saw layout 1): restore the
    layout, seal every stray line whose id is not sealed yet into one verified part, record the ids, then delete the
    stray files from the store. A store counts as migrated when it holds docs-parts/legacy/ parts."""
    import subprocess
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete
    if not force:
        r = subprocess.run(["gh", "run", "list", "--repo", "jonawalb/rhetoric-corpus", "--status", "in_progress",
                            "--json", "databaseId", "-q", ".[].databaseId"], capture_output=True, text=True)
        if r.returncode != 0 or r.stdout.strip():
            raise SystemExit(f"a CI run is in progress (or gh failed: {r.stderr.strip()}); not repairing")
    hf = api()
    remote = remote_manifest(hf, repo, allow_downgraded=True)
    if not layout2():
        if not any(p.startswith(PARTS + "legacy/") for p in remote_parts(hf, repo, remote)):
            raise SystemExit(f"{repo} was never migrated (no {PARTS}legacy/ parts); nothing to repair")
        logger.warning("%s: layout key lost; restoring layout 2", repo)
        _raise_layout(2)
    stray_tree = {}
    try:  # also files uploaded by a push that then refused to write its manifest (not listed in it)
        for f in hf.list_repo_tree(repo, path_in_repo="docs", recursive=True, repo_type="dataset"):
            if getattr(f, "size", None) is not None and f.path.endswith(".jsonl") and f.path not in remote:
                lfs = getattr(f, "lfs", None)
                stray_tree[f.path] = {"sha256": getattr(lfs, "sha256", None) if lfs else None, "bytes": f.size}
    except Exception as e:  # noqa: BLE001 - no docs/ folder
        logger.info("no docs/ in the store tree (%s)", type(e).__name__)
    remote = {**stray_tree, **remote}
    stray = sorted(p for p in remote if p.startswith("docs/") and p.endswith(".jsonl"))
    work = ROOT / "staging" / "repair"
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    rep = {"stray_files": len(stray), "stray_lines": 0, "already_sealed": 0, "new": 0, "bad": 0}
    with segments.seal_lock(ROOT):
        refresh_ids(hf, repo, remote)
        work.mkdir(parents=True, exist_ok=True)
        local_part = work / f"{stamp}-repair.jsonl.gz"
        new_ids: Dict[str, List[str]] = {}
        import gzip
        with local_part.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            for p in stray:
                cc, src = p.split("/")[1], Path(p).stem
                sealed = segments.read_ids(segments.ids_file(ROOT, cc, src))
                mine = new_ids.setdefault(f"{cc}/{src}", [])
                seen = set(mine)
                dl = _download(repo, p)
                for line in segments.open_lines(dl):
                    rep["stray_lines"] += 1
                    did = segments.line_id(line)
                    try:
                        ok = did is not None and isinstance(json.loads(line), dict)
                    except ValueError:
                        ok = False
                    if not ok:
                        with (work / f"{cc}_{src}.bad.jsonl").open("ab") as q:
                            q.write(line)
                        rep["bad"] += 1
                    elif did in sealed or did in seen:
                        rep["already_sealed"] += 1
                    else:
                        seen.add(did)
                        mine.append(did)
                        gz.write(line)
                dl.unlink()
        rep["new"] = sum(len(v) for v in new_ids.values())
        entry = {}
        if rep["new"]:
            h = sha256(local_part)
            remote_path = segments.part_remote(stamp, "repair", h)
            up = HubUploader(hf, repo)
            if not up.verify(remote_path, h, rep["new"]):
                up.upload(local_part, remote_path)
                if not up.verify(remote_path, h, rep["new"]):
                    raise SystemExit(f"{remote_path} does not verify in the store; nothing deleted")
            entry = {remote_path: {"sha256": h, "bytes": local_part.stat().st_size, "lines": rep["new"]}}
            rep["part"] = remote_path
            for key, ids in new_ids.items():  # stored and verified: now the ids count as sealed
                if ids:
                    cc, src = key.split("/", 1)
                    segments.append_ids(segments.ids_file(ROOT, cc, src), ids, f"repair {stamp} {remote_path}")
        _raise_layout(2)
        fresh = remote_manifest(hf, repo, allow_downgraded=True)
        late = [p for p in fresh if p.startswith("docs/") and p.endswith(".jsonl") and p not in stray]
        if late or any(fresh[p]["sha256"] != remote[p]["sha256"] for p in stray if p in fresh and p not in stray_tree):
            raise SystemExit(f"stray docs changed during the repair ({late[:5]}); part kept, nothing deleted; re-run")
        files = {p: v for p, v in fresh.items() if p not in stray}
        files.update(entry)
        ops = [CommitOperationDelete(p) for p in stray if p in fresh or p in stray_tree]
        ops += [CommitOperationAdd("README.md", CARD.encode()), *_manifest_ops(files, layout=2)]
        hf.create_commit(repo, repo_type="dataset", commit_message=f"store: repair layout 2, seal {len(stray)} stray docs files",
                         operations=ops)
        local_part.unlink(missing_ok=True)
    rep["ids_push"] = push(repo, only=["state/ids"])
    logger.info("repair: %s", rep)
    return rep


def shutil_rm(path: Path) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def verify(repo: str) -> dict:
    local = local_manifest(ROOT)
    remote = remote_manifest(api(), repo)
    if layout2():  # active docs files are local buffers, not stored copies
        local = {p: v for p, v in local.items() if not p.startswith(APPEND_ONLY)}
    changed, missing = diff(local, remote)
    by_top: Dict[str, List[int]] = {}
    for p, v in remote.items():
        by_top.setdefault(p.split("/")[0], [0, 0])
        by_top[p.split("/")[0]][0] += 1
        by_top[p.split("/")[0]][1] += v["bytes"]
    rep = {"remote_files": len(remote), "local_files": len(local), "differ": len(changed), "remote_only": len(missing),
           "remote_by_top": {k: {"files": n, "mb": round(b / 1e6, 1)} for k, (n, b) in sorted(by_top.items())},
           "differ_sample": changed[:10]}
    print(json.dumps(rep, indent=1))
    return rep


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["pull", "push", "verify", "squash", "seal", "migrate", "layout", "repair"])
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--only", help="comma list of top folders (docs,state,raw,index/semantic,reports,logs/ci)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-shrink", action="store_true", help="push docs files even if smaller than the stored copy")
    ap.add_argument("--no-merge", action="store_true",
                    help="push: upload local docs files as-is instead of merging with the stored copy (only when this "
                         "checkout is the sole writer since the last push, e.g. to repair a bad stored file)")
    ap.add_argument("--keep-parts", action="store_true", help="seal: keep the sealed part under docs-parts/ (CI)")
    ap.add_argument("--writer", help="seal: writer name in the part file name (default: ci / host name)")
    ap.add_argument("--all-parts", action="store_true", help="pull: every part, not only the ones not yet indexed")
    ap.add_argument("--force", action="store_true", help="migrate: skip the CI-in-progress check")
    a = ap.parse_args()
    if a.cmd in ("push", "seal", "migrate", "repair"):  # one store writer per checkout at a time (offload loop vs manual runs)
        import fcntl
        (ROOT / "staging" / "seal").mkdir(parents=True, exist_ok=True)
        lockf = open(ROOT / "staging" / "seal" / ".store.lock", "w")
        fcntl.flock(lockf, fcntl.LOCK_EX)
    if a.cmd == "pull":
        print(json.dumps(pull(a.repo, a.only.split(",") if a.only else None, all_parts=a.all_parts)))
    elif a.cmd == "seal":
        print(json.dumps({k: v for k, v in seal(a.repo, a.keep_parts, a.writer).items()}, default=str))
    elif a.cmd == "migrate":
        print(json.dumps(migrate(a.repo, a.force)))
    elif a.cmd == "repair":
        print(json.dumps(repair(a.repo, a.force)))
    elif a.cmd == "layout":
        remote_manifest(api(), a.repo)
        print(_LAYOUT["remote"])
    elif a.cmd == "push":
        print(json.dumps(push(a.repo, a.dry_run, a.allow_shrink, a.only.split(",") if a.only else None,
                              no_merge=a.no_merge)))
    elif a.cmd == "verify":
        rep = verify(a.repo)
        sys.exit(1 if rep["differ"] else 0)
    else:
        api().super_squash_history(a.repo, repo_type="dataset", commit_message="store: squash history")
        print("squashed")


if __name__ == "__main__":
    main()
