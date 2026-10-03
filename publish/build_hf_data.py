"""Build the sealed Rhetoric Search data for its Hugging Face dataset host, and (only with --upload) upload it.

Steps
  1. build_public.py into a staging folder (never into the site repo): s/ t/ m/ shards and meta.json.gz.
  2. Full-document shards for OFFICIAL documents only (outlet == "official"): docs/<k>.json.gz, a JSON object
     {rowid: {date, source, org, lang, title, speaker, url, wayback, text}}, cut by corpus rowid ranges at about
     --doc-shard-kb of raw text (≈200-400 KB gzip), plus docs/index.json.gz {"ranges": [[first, last], ...]}.
     State media / media / commentary bodies never enter them: assert_no_media_text() fails the build otherwise.
  3. Seal every file with the Rhetoric Search tier key (scripts/build_site.py: tier_key + seal_file, the format
     gate.js decrypts) into  staging/hf/data/<build-id>/...  with a plaintext manifest.json (file names, sizes,
     SHA-256 of the sealed bytes), a sealed pointer  staging/hf/data/current.json  {"build": "<build-id>"}, the
     dataset card README.md and .gitattributes. staging/hf mirrors the dataset repo layout exactly.
  4. Keep the newest --keep builds in staging/hf/data (default 2) and delete the plaintext staging copy.
  5. --upload: create the dataset repo if needed (public), upload the new build in commits of 500 files, copying
     files whose sealed bytes equal the previous remote build's (CommitOperationCopy, no re-upload), then move
     current.json to it, then delete remote builds older than the newest --keep. Without --upload nothing leaves
     this machine; the script prints what an upload would add or copy.

The page (js/config.js HF_REPO) reads data/current.json, then every file from data/<build-id>/.

Vendored from tsm-strait-layers tools/rhetoric-search/scripts/ (keep in step); sealing from publish/vault.py. With
--trends DIR the Trends data (publish/build_trends.py) ships in the same build as <build-id>/trends/, so a nightly upload
refreshes the live Trends page without redeploying the site.

Usage (from the rhetoric-corpus repo):
  uv run python publish/build_hf_data.py [--trends staging/trends]
  ... --upload                      build, then upload (needs `hf auth login` or HF_TOKEN; repo: HF_DATA_REPO / --repo)
  ... --upload-only                 upload the newest staged build without rebuilding
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

HERE = Path(__file__).resolve().parent
DEFAULT_CORPUS = HERE.parent  # the rhetoric-corpus repo root (publish/..)
DEFAULT_REPO = "wallabee1/rhetoric-search-data"  # = HF_REPO in tsm-strait-layers tools/rhetoric-search/js/config.js
SITE, TIER = "deterrence", "t5"  # the Rhetoric Search password tier of Interactive Deterrence
DOC_KEYS = frozenset({"date", "source", "org", "lang", "title", "speaker", "url", "wayback", "text"})
CARD = """---
viewer: false
license: other
pretty_name: Rhetoric Search data (encrypted)
---
# Rhetoric Search data

Encrypted data files for a password-protected research tool. Every file under `data/` is AES-256-GCM encrypted and
cannot be read without the tool's access password. This repository is not a usable dataset.
"""
GITATTRIBUTES = "*.gz filter=lfs diff=lfs merge=lfs -text\n"


class MediaTextError(AssertionError):
    """A full-document shard holds a document that is not official, or text identical to a media document."""


def gz(obj) -> bytes:
    return gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9, mtime=0)


def gunzip_json(path: Path):
    return json.loads(gzip.decompress(path.read_bytes()))


def config_repo() -> str:
    """The public dataset repo: env HF_DATA_REPO, else DEFAULT_REPO (the site's js/config.js HF_REPO)."""
    return os.environ.get("HF_DATA_REPO") or DEFAULT_REPO


# ---- full official documents -------------------------------------------------------------------------------
def normalize_wayback(url: str, w: str | None) -> str:
    """Stored capture (timestamp or full Wayback URL) as a readable Wayback link; else the latest capture of url."""
    w = (w or "").strip()
    if w.startswith(("http://", "https://")):
        return re.sub(r"(/web/\d+)id_/", r"\1/", w)
    if re.fullmatch(r"\d{4,14}", w):
        return f"https://web.archive.org/web/{w}/{url}"
    return f"https://web.archive.org/web/{url}" if url else ""


def load_wayback(corpus: Path, files: Iterable[str]) -> Dict[str, str]:
    """doc id -> stored `wayback` field, read from docs/<file>.jsonl (only lines that have one are parsed)."""
    out: Dict[str, str] = {}
    for f in sorted(set(files)):
        p = corpus / "docs" / f
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                if '"wayback"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("wayback") and d.get("id"):
                    out[d["id"]] = str(d["wayback"])
    return out


def build_doc_shards(con: sqlite3.Connection, out: Path, wayback: Dict[str, str], shard_kb: int = 900,
                     where: str = "1", args: tuple = ()) -> dict:
    """Write docs/<k>.json.gz + docs/index.json.gz for every official document (rowid order). Returns sizes."""
    out.mkdir(parents=True, exist_ok=True)
    ranges: List[List[int]] = []
    shard: Dict[str, dict] = {}
    size = 0
    sizes: List[int] = []
    limit = shard_kb * 1000

    def flush() -> None:
        nonlocal shard, size
        if not shard:
            return
        blob = gz(shard)
        (out / f"{len(ranges)}.json.gz").write_bytes(blob)
        keys = [int(k) for k in shard]
        ranges.append([min(keys), max(keys)])
        sizes.append(len(blob))
        shard, size = {}, 0

    rows = con.execute(f"SELECT rowid, id, outlet, source, org, lang, date, url, title, speaker, text FROM docs "
                       f"WHERE outlet = 'official' AND ({where}) ORDER BY rowid", args)
    n = 0
    for rowid, did, outlet, source, org, lang, date, url, title, speaker, text in rows:
        if outlet != "official":  # belt and braces; the query already filters
            continue
        shard[str(rowid)] = {"date": date, "source": source, "org": org or "", "lang": lang, "title": title or "",
                             "speaker": speaker or "", "url": url or "", "wayback": normalize_wayback(url, wayback.get(did)),
                             "text": text or ""}
        size += len((text or "").encode())
        n += 1
        if size >= limit:
            flush()
    flush()
    (out / "index.json.gz").write_bytes(gz({"ranges": ranges}))
    sizes.sort()
    return {"docs": n, "shards": len(ranges), "gz_mb": round(sum(sizes) / 1e6, 1),
            "shard_kb_min_median_max": [round(sizes[i] / 1e3) for i in (0, len(sizes) // 2, -1)] if sizes else []}


def assert_no_media_text(docs_dir: Path, con: sqlite3.Connection) -> int:
    """Fail unless every document in docs_dir is official in the corpus index, carries only DOC_KEYS, and no
    document's text equals any state media / media / commentary text. Returns the number of documents checked."""
    errors: List[str] = []
    ids: List[int] = []
    text_hash: Dict[str, int] = {}
    for p in sorted(docs_dir.glob("*.json.gz")):
        if p.name == "index.json.gz":
            continue
        for k, rec in gunzip_json(p).items():
            ids.append(int(k))
            if set(rec) - DOC_KEYS:
                errors.append(f"{p.name}:{k}: unexpected fields {sorted(set(rec) - DOC_KEYS)}")
            if len(rec.get("text", "")) >= 40:
                text_hash[hashlib.sha1(rec["text"].encode()).hexdigest()] = int(k)
    outlet: Dict[int, str] = {}
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        outlet.update(con.execute(f"SELECT rowid, outlet FROM docs WHERE rowid IN ({','.join('?' * len(chunk))})", chunk))
    errors += [f"doc {r}: outlet {outlet.get(r)!r} is not official" for r in ids if outlet.get(r) != "official"]
    for rowid, text in con.execute("SELECT rowid, text FROM docs WHERE outlet != 'official' AND length(text) >= 40"):
        h = hashlib.sha1(text.encode()).hexdigest()
        if h in text_hash:
            errors.append(f"doc {text_hash[h]}: text identical to media doc {rowid}")
    if errors:
        raise MediaTextError(f"{len(errors)} problem(s) in the full-document shards:\n" + "\n".join(errors[:20]))
    return len(ids)


def add_docs_to_meta(plain: Path, info: dict) -> None:
    meta = gunzip_json(plain / "meta.json.gz")
    meta["docs"] = {"shards": info["shards"], "docs": info["docs"]}
    (plain / "meta.json.gz").write_bytes(gz(meta))


# ---- sealing, pointer, builds --------------------------------------------------------------------------------
def seal_tree(plain: Path, out: Path, key: bytes, magic: bytes) -> dict:
    """Seal every file of plain into out (same relative paths). Returns {path: {"sha256", "bytes"}}."""
    from vault import seal_file  # noqa: E402  (publish/vault.py = build_site.py format; needs cryptography)
    files = {}
    for p in sorted(x for x in plain.rglob("*") if x.is_file()):
        rel = p.relative_to(plain).as_posix()
        blob = seal_file(key, magic, p.read_bytes())
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_bytes(blob)
        files[rel] = {"sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob)}
    return files


def write_pointer(hf_root: Path, build_id: str, key: bytes, magic: bytes) -> Path:
    """data/current.json, sealed: {"build": id, "written": iso time}. Written only after the build is complete."""
    from vault import seal_file  # noqa: E402
    if not (hf_root / "data" / build_id / "manifest.json").exists():
        raise SystemExit(f"build {build_id} has no manifest; refusing to point at it")
    body = json.dumps({"build": build_id, "written": datetime.now(timezone.utc).isoformat(timespec="seconds")}).encode()
    p = hf_root / "data" / "current.json"
    p.write_bytes(seal_file(key, magic, body))
    return p


def read_pointer(hf_root: Path, key: bytes) -> dict:
    from vault import unseal_file  # noqa: E402
    return json.loads(unseal_file(key, (hf_root / "data" / "current.json").read_bytes()))


def staged_builds(hf_root: Path) -> List[str]:
    d = hf_root / "data"
    return sorted(p.name for p in d.iterdir() if p.is_dir() and (p / "manifest.json").exists()) if d.exists() else []


def prune_builds(hf_root: Path, keep: int, current: str) -> List[str]:
    """Delete all but the newest `keep` builds (never the current one). Build ids sort by time."""
    d = hf_root / "data"
    builds = sorted(p.name for p in d.iterdir() if p.is_dir()) if d.exists() else []
    drop = [b for b in builds[:-keep] if b != current] if keep > 0 else []
    for b in drop:
        shutil.rmtree(d / b)
    return drop


def upload_plan(new: Dict[str, dict], prev: Dict[str, dict]) -> Tuple[List[str], List[str]]:
    """(paths whose sealed bytes equal the previous build's: copy remotely, paths to upload)."""
    copy = [p for p, v in new.items() if p in prev and prev[p]["sha256"] == v["sha256"]]
    add = [p for p in new if p not in set(copy)]
    return sorted(copy), sorted(add)


def size_report(hf_root: Path, build_id: str) -> dict:
    files = json.loads((hf_root / "data" / build_id / "manifest.json").read_text())["files"]
    by: Dict[str, List[int]] = {}
    for p, v in files.items():
        by.setdefault(p.split("/")[0] if "/" in p else p, []).append(v["bytes"])
    return {"build": build_id, "files": len(files), "sealed_mb": round(sum(v["bytes"] for v in files.values()) / 1e6, 1),
            "by_part_mb": {k: [len(v), round(sum(v) / 1e6, 1)] for k, v in sorted(by.items())}}


# ---- upload (only with --upload / --upload-only) -------------------------------------------------------------
def check_key_opens_remote(api, repo: str, remote: set, key: bytes) -> None:
    """Refuse to publish with a key that cannot open the live data/current.json (a wrong tier password would lock
    every visitor out). Skipped when the repo has no pointer yet."""
    from vault import unseal_file  # noqa: E402
    if "data/current.json" not in remote:
        return
    blob = Path(api.hf_hub_download(repo, "data/current.json", repo_type="dataset", force_download=True)).read_bytes()
    try:
        json.loads(unseal_file(key, blob))
    except Exception as e:  # noqa: BLE001  InvalidTag: another key sealed the live data
        raise SystemExit("the tier key does not open the published data/current.json (wrong rhetoric-tier password?); "
                         "nothing uploaded") from e


def upload(hf_root: Path, build_id: str, repo: str, keep: int, batch: int = 500, key: bytes | None = None) -> None:
    from huggingface_hub import CommitOperationAdd, CommitOperationCopy, CommitOperationDelete, HfApi
    api = HfApi()
    api.create_repo(repo, repo_type="dataset", private=False, exist_ok=True)
    remote = set(api.list_repo_files(repo, repo_type="dataset"))
    if key is not None:
        check_key_opens_remote(api, repo, remote, key)
    remote_builds = sorted({f.split("/")[1] for f in remote if f.startswith("data/") and f.count("/") >= 2})
    prev = next((b for b in reversed(remote_builds) if b != build_id and f"data/{b}/manifest.json" in remote), None)
    prev_files = {}
    if prev:
        mf = api.hf_hub_download(repo, f"data/{prev}/manifest.json", repo_type="dataset")
        prev_files = json.loads(Path(mf).read_text())["files"]
    new_files = json.loads((hf_root / "data" / build_id / "manifest.json").read_text())["files"]
    copy, add = upload_plan(new_files, prev_files)
    print(f"upload {build_id} to {repo}: {len(add)} files to upload, {len(copy)} copied from {prev}")
    root = f"data/{build_id}"
    api.create_commit(repo, repo_type="dataset", commit_message="Dataset card",
                      operations=[CommitOperationAdd("README.md", str(hf_root / "README.md")),
                                  CommitOperationAdd(".gitattributes", str(hf_root / ".gitattributes"))])
    ops = [CommitOperationCopy(f"data/{prev}/{p}", f"{root}/{p}") for p in copy]
    ops += [CommitOperationAdd(f"{root}/{p}", str(hf_root / root / p)) for p in add]
    for i in range(0, len(ops), batch):
        chunk = ops[i:i + batch]
        try:
            api.create_commit(repo, repo_type="dataset", operations=chunk, commit_message=f"Build {build_id} ({i // batch + 1})")
        except Exception as e:  # noqa: BLE001  a copy the Hub refuses: upload those files instead
            if not any(isinstance(o, CommitOperationCopy) for o in chunk):
                raise
            print(f"copy failed ({e}); uploading this batch instead")
            chunk = [CommitOperationAdd(o.path_in_repo, str(hf_root / o.path_in_repo)) if isinstance(o, CommitOperationCopy)
                     else o for o in chunk]
            api.create_commit(repo, repo_type="dataset", operations=chunk, commit_message=f"Build {build_id} ({i // batch + 1})")
        print(f"  committed {min(i + batch, len(ops))}/{len(ops)}")
    api.create_commit(repo, repo_type="dataset", commit_message=f"Build {build_id}: manifest + current",
                      operations=[CommitOperationAdd(f"{root}/manifest.json", str(hf_root / root / "manifest.json")),
                                  CommitOperationAdd("data/current.json", str(hf_root / "data" / "current.json"))])
    old = [b for b in sorted(set(remote_builds) | {build_id})[:-keep] if b != build_id] if keep > 0 else []
    if old:
        api.create_commit(repo, repo_type="dataset", commit_message=f"Remove old builds {', '.join(old)}",
                          operations=[CommitOperationDelete(f"data/{b}/") for b in old])
    print(f"current build {build_id}; removed {old or 'nothing'}")


# ---- main ----------------------------------------------------------------------------------------------------
def build(a: argparse.Namespace, key: bytes, magic: bytes) -> str:
    t0 = time.time()
    build_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    plain = a.staging / "plain" / build_id
    hf_root = a.staging / "hf"
    shutil.rmtree(a.staging / "plain", ignore_errors=True)
    plain.mkdir(parents=True)
    subprocess.run([sys.executable, str(HERE / "build_public.py"), "--corpus", str(a.corpus), "--out", str(plain)],
                   check=True)
    con = sqlite3.connect(f"file:{a.corpus / 'index' / 'corpus.sqlite'}?mode=ro", uri=True)
    files = [f for (f,) in con.execute("SELECT DISTINCT file FROM docs WHERE outlet = 'official'")]
    info = build_doc_shards(con, plain / "docs", load_wayback(a.corpus, files), a.doc_shard_kb)
    n = assert_no_media_text(plain / "docs", con)
    print(f"full documents: {json.dumps(info)}; media guard passed on {n} documents")
    add_docs_to_meta(plain, info)
    if a.trends:  # Trends page data (publish/build_trends.py output, already checked by its public-text guard)
        if not (a.trends / "index.json.gz").exists():
            raise SystemExit(f"{a.trends}: no index.json.gz (run publish/build_trends.py first)")
        shutil.copytree(a.trends, plain / "trends")
    out = hf_root / "data" / build_id
    manifest = {"build": build_id, "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "files": seal_tree(plain, out, key, magic)}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=0, sort_keys=True))
    (hf_root / "README.md").write_text(CARD)
    (hf_root / ".gitattributes").write_text(GITATTRIBUTES)
    write_pointer(hf_root, build_id, key, magic)
    shutil.rmtree(a.staging / "plain")
    dropped = prune_builds(hf_root, a.keep, build_id)
    print(f"sealed in {time.time() - t0:.0f} s; pruned staged builds {dropped or 'none'}")
    return build_id


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--staging", type=Path, default=DEFAULT_CORPUS / "staging")
    ap.add_argument("--repo", default=config_repo(), help="Hugging Face dataset repo, user/name (default: env HF_DATA_REPO or %s)" % DEFAULT_REPO)
    ap.add_argument("--trends", type=Path, default=None, help="Trends data dir to include as <build>/trends/ (e.g. staging/trends)")
    ap.add_argument("--doc-shard-kb", type=int, default=900, help="raw text per full-document shard (KB)")
    ap.add_argument("--keep", type=int, default=2, help="builds kept in staging and on the Hub")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--upload", action="store_true", help="after building, upload to the Hub")
    g.add_argument("--upload-only", action="store_true", help="upload the newest staged build, no rebuild")
    a = ap.parse_args()
    sys.path.insert(0, str(HERE))
    from vault import tier_key  # noqa: E402
    hf_root = a.staging / "hf"
    if (a.upload or a.upload_only) and not a.repo:
        raise SystemExit("No dataset repo: set HF_DATA_REPO or pass --repo user/name")
    key, magic = tier_key(SITE, TIER)
    if a.upload_only:
        builds = staged_builds(hf_root)
        if not builds:
            raise SystemExit(f"no staged build in {hf_root / 'data'}")
        build_id = builds[-1]
        if read_pointer(hf_root, key)["build"] != build_id:
            raise SystemExit(f"staged current.json does not point at the newest build {build_id}")
    else:
        build_id = build(a, key, magic)
    print(json.dumps(size_report(hf_root, build_id)))
    if a.upload or a.upload_only:
        upload(hf_root, build_id, a.repo, a.keep, key=key)
        return
    builds = staged_builds(hf_root)
    prev = builds[-2] if len(builds) > 1 else None
    new_files = json.loads((hf_root / "data" / build_id / "manifest.json").read_text())["files"]
    prev_files = json.loads((hf_root / "data" / prev / "manifest.json").read_text())["files"] if prev else {}
    copy, add = upload_plan(new_files, prev_files)
    add_mb = sum(new_files[p]["bytes"] for p in add) / 1e6
    print(f"dry run (no --upload): would upload {len(add)} files ({add_mb:.1f} MB) and copy {len(copy)} unchanged "
          f"from {prev or 'no previous staged build'} to {a.repo or '<HF_REPO unset>'}")


if __name__ == "__main__":
    main()
