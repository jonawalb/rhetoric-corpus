"""Sync the private corpus store (Hugging Face dataset repo, private) with this checkout.

The nightly GitHub Actions job has no disk that survives between runs: it pulls the store, collects, rebuilds, and
pushes the store back. The store holds everything a run needs to continue where the last one stopped:

  docs/**/*.jsonl                collected documents (append-only; never deleted or shrunk by a push)
  state/*.json, state/*.cookies  collector resume state
  raw/<CDX/sitemap caches>       the few listing caches collectors read instead of re-asking Wayback (RAW_DIRS)
  index/semantic/**              semantic store for incremental runs (models excluded: they come from the Hub)
  reports/**                     briefs and semantic reports (they quote texts, so they live here, not in git)
  logs/ci/<date>/                collector logs of the last 14 CI runs (private: they name URLs and titles)

index/corpus.sqlite is NOT stored: build_index.py rebuilds it from docs/ in a few minutes, and the semantic store is
keyed by document id, not corpus rowid.

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

Usage:
  uv run python scripts/store_sync.py pull   [--only docs,state]     # download changed files
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
INCLUDE = ["docs/*/*.jsonl", "state/*.json", "state/*.cookies", *[f"raw/{d}/*" for d in RAW_DIRS],
           "index/semantic/*", "index/semantic/**/*", "reports/*", "reports/**/*", "logs/ci/*"]
EXCLUDE = ["index/semantic/models/*", "*.sqlite-wal", "*.sqlite-shm", "*.tmp", "*.lock", "*/.*", ".*"]
DELETABLE = ("index/semantic/", "logs/ci/")
APPEND_ONLY = ("docs/",)
CARD = """---
viewer: false
---
# rhetoric-corpus store (private)

Working store of a research text collection: collector state, collected documents and derived indexes, synced by the
nightly job of github.com/jonawalb/rhetoric-corpus (scripts/store_sync.py). Private; not for redistribution.
"""
logger = logging.getLogger("store_sync")


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
    for db in (root / "index" / "semantic").glob("*.sqlite"):
        try:
            con = sqlite3.connect(db, timeout=60)
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con.close()
        except sqlite3.Error as e:
            raise SystemExit(f"cannot checkpoint {db}: {e} (is another process writing it?)")


def local_manifest(root: Path, only: List[str] | None = None) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for top in ("docs", "state", "raw", "index", "reports", "logs"):
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


def api():
    from huggingface_hub import HfApi
    return HfApi()


def remote_manifest(hf, repo: str) -> Dict[str, dict]:
    from huggingface_hub import hf_hub_download
    from huggingface_hub.utils import EntryNotFoundError, RepositoryNotFoundError
    try:
        path = hf_hub_download(repo, MANIFEST, repo_type="dataset", force_download=True)
    except (EntryNotFoundError, RepositoryNotFoundError):
        return {}
    return json.loads(Path(path).read_text())["files"]


def diff(local: Dict[str, dict], remote: Dict[str, dict]) -> Tuple[List[str], List[str]]:
    """(paths whose local copy differs from or is missing in remote, remote paths missing locally)."""
    changed = sorted(p for p, v in local.items() if remote.get(p, {}).get("sha256") != v["sha256"])
    missing = sorted(p for p in remote if p not in local)
    return changed, missing


def _download(repo: str, p: str) -> Path:
    from huggingface_hub import hf_hub_download
    return Path(hf_hub_download(repo, p, repo_type="dataset", local_dir=ROOT / ".store_dl"))


def pull(repo: str, only: List[str] | None, workers: int = 8) -> dict:
    hf = api()
    MARKER.unlink(missing_ok=True)
    remote = remote_manifest(hf, repo)
    if not remote:
        raise SystemExit(f"{repo}: no {MANIFEST}; nothing to pull (run the one-time migration push first)")
    sel = {p: v for p, v in remote.items() if not only or any(p.startswith(o.rstrip("/") + "/") for o in only)}
    todo = []
    for p, v in sel.items():
        f = ROOT / p
        if not (f.exists() and f.stat().st_size == v["bytes"] and sha256(f) == v["sha256"]):
            todo.append(p)
    t0, got = time.time(), 0

    def one(p: str) -> int:
        tmp = _download(repo, p)
        h = sha256(tmp)
        if h != remote[p]["sha256"]:  # a concurrent push replaced the file after the manifest was read: keep it
            logger.warning("%s: stored file is newer than the manifest; using the stored file", p)
            remote[p] = {"sha256": h, "bytes": tmp.stat().st_size}
        dest = ROOT / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, dest)
        return remote[p]["bytes"]

    with ThreadPoolExecutor(workers) as ex:
        for n in ex.map(one, todo):
            got += n
    if not only:  # the snapshot push() merges against, and permission to mirror deletions under DELETABLE
        MARKER.write_text(json.dumps({"repo": repo, "time": time.time(), "manifest": remote}))
    rep = {"stored": len(sel), "downloaded": len(todo), "mb": round(got / 1e6, 1), "seconds": round(time.time() - t0)}
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
         batch: int = 100) -> dict:
    """Upload changed files. A docs file that someone else changed in the store since our pull (or, without a pull
    this run, any docs file that differs) is first merged by document id, so concurrent writers never lose lines."""
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete
    hf = api()
    if not only or any(o.startswith("index") for o in only):
        checkpoint_sqlite(ROOT)
    local = local_manifest(ROOT, only)
    remote = remote_manifest(hf, repo)
    pulled = json.loads(MARKER.read_text()).get("manifest") if MARKER.exists() else None
    changed, missing = diff(local, remote)
    merged = 0
    for p in [p for p in changed if p.startswith(APPEND_ONLY) and p in remote]:
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
    new_manifest = {**keep, **local}
    rep = {"local_files": len(local), "upload": len(changed), "upload_mb": round(sum(local[p]["bytes"] for p in changed) / 1e6, 1),
           "merged_docs_files": merged, "delete": len(deletes), "kept_remote_only": len(keep), "refused_shrink": len(shrunk)}
    logger.info("push plan for %s: %s", repo, rep)
    if dry_run:
        return rep
    hf.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)
    if not hf.repo_info(repo, repo_type="dataset").private:
        raise SystemExit(f"{repo} is not private; refusing to push")
    t0 = time.time()
    for i in range(0, len(changed), batch):
        part = changed[i:i + batch]
        hf.create_commit(repo, repo_type="dataset", commit_message=f"store: {len(part)} files",
                         operations=[CommitOperationAdd(p, str(ROOT / p)) for p in part])
        logger.info("  uploaded %d/%d", min(i + batch, len(changed)), len(changed))
    ops = [CommitOperationDelete(p) for p in deletes]
    ops += [CommitOperationAdd("README.md", CARD.encode()),
            CommitOperationAdd(MANIFEST, json.dumps({"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                                     "files": new_manifest}, indent=0, sort_keys=True).encode())]
    hf.create_commit(repo, repo_type="dataset", commit_message="store: manifest", operations=ops)
    if MARKER.exists() and not only:  # what we just stored is the new base for a later push in this checkout
        MARKER.write_text(json.dumps({"repo": repo, "time": time.time(), "manifest": new_manifest}))
    rep["seconds"] = round(time.time() - t0)
    logger.info("push done: %s", rep)
    return rep


def verify(repo: str) -> dict:
    local = local_manifest(ROOT)
    remote = remote_manifest(api(), repo)
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
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["pull", "push", "verify", "squash"])
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--only", help="comma list of top folders (docs,state,raw,index/semantic,reports,logs/ci)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-shrink", action="store_true", help="push docs files even if smaller than the stored copy")
    a = ap.parse_args()
    if a.cmd == "pull":
        print(json.dumps(pull(a.repo, a.only.split(",") if a.only else None)))
    elif a.cmd == "push":
        print(json.dumps(push(a.repo, a.dry_run, a.allow_shrink, a.only.split(",") if a.only else None)))
    elif a.cmd == "verify":
        rep = verify(a.repo)
        sys.exit(1 if rep["differ"] else 0)
    else:
        api().super_squash_history(a.repo, repo_type="dataset", commit_message="store: squash history")
        print("squashed")


if __name__ == "__main__":
    main()
