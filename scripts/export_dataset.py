"""Build a versioned, self-documenting dataset release from the corpus documents -> release/<version>/.

Input: docs/**/*.jsonl, or, with the segmented store (docs-parts/ + state/ids/ present), every local part plus the
active docs/ files, materialized per source under staging/export_docs/ (all parts must be local:
`store_sync.py pull --only docs-parts`; the build refuses otherwise).

    uv run python scripts/export_dataset.py --version 0.1.0 [--snapshot-date 2026-10-02] [--countries RU CN]
                                            [--from 2021-01-01] [--to 2026-10-02]

Output (release/ is gitignored; nothing is uploaded anywhere):
  official/<CC>.parquet          outlet == official, full text + all fields (zstd)
  official_jsonl/<CC>.jsonl.gz   same rows as gzip JSON Lines
  media/<CC>.parquet             state_media / media / commentary: metadata only, no body text
  semantic/doc_scores.parquet    optional, when index/semantic has per-doc tone scores
  coverage.csv                   country x outlet x source x language: first/last date, documents
  README.md CODEBOOK.md CHANGELOG.md ZENODO_METADATA.json MANIFEST.json SHA256SUMS

Snapshot: --snapshot-date D keeps rows whose `fetched` is at or before the end of day D in the machine's local
time zone (or an ISO timestamp), and never later than the build start, so a release is reproducible while
collectors keep appending. --from/--to filter on publication `date`. The build writes to a hidden
release/.<version>.partial directory and renames it when complete; an existing version is never overwritten
unless --overwrite is given. Guards: refuses when the selected input exceeds --max-gb (output is always
smaller than the input) or when free disk after the build would drop below --min-free-gb.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_data as RD  # noqa: E402
import release_docs  # noqa: E402
import release_semantic  # noqa: E402
import segments  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger("export_dataset")
VALUE_FIELDS = ("outlet", "kind", "via", "lang", "sample", "translation", "url_kind", "period", "text_scope")


def resolve_cutoff(spec: Optional[str], now: datetime) -> Dict[str, str]:
    """End of the given local day (or an ISO timestamp), capped at `now`; returns ISO UTC strings."""
    if spec is None:
        req = now
    elif len(spec) == 10:
        req = datetime.combine(date.fromisoformat(spec), time(23, 59, 59)).astimezone()
    else:
        req = RD.parse_ts(spec)
    eff = min(req.astimezone(timezone.utc), now.astimezone(timezone.utc))
    return {"requested": req.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "effective": eff.isoformat(timespec="seconds"), "spec": spec or "build time"}


def git_info(repo: Path) -> Dict[str, Any]:
    def run(*args: str) -> str:
        try:
            return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                                  check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""
    commit = run("rev-parse", "HEAD") or "unknown"
    dirty = bool(run("status", "--porcelain", "--untracked-files=no"))
    return {"commit": commit, "tracked_changes_uncommitted": dirty}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def previous_manifest(out_root: Path, version: str) -> Optional[Dict[str, Any]]:
    def key(v: str) -> tuple:
        return tuple(int(x) if x.isdigit() else 0 for x in v.split("."))
    older = [p for p in out_root.glob("*/MANIFEST.json") if key(p.parent.name) < key(version)]
    if not older:
        return None
    return json.loads(max(older, key=lambda p: key(p.parent.name)).read_text(encoding="utf-8"))


def collect(files: List[Path], cutoff: datetime, d_from: Optional[str], d_to: Optional[str]) -> Dict[str, Any]:
    """Read, validate, filter and dedupe every input file; returns records grouped by country."""
    seen: set = set()
    official: Dict[str, List[Dict]] = defaultdict(list)
    media: Dict[str, List[Dict]] = defaultdict(list)
    dropped: Counter = Counter()
    examples: Dict[str, List[str]] = defaultdict(list)
    excluded: Counter = Counter()
    inputs = []
    for path in files:
        country, source = path.parent.name, path.stem
        rows, info = RD.read_snapshot(path)
        inputs.append({"path": f"docs/{country}/{path.name}", **{k: v for k, v in info.items() if k != "bad_json_lines"},
                       "bad_json": len(info["bad_json_lines"])})
        if info["bad_json_lines"]:
            dropped["bad_json"] += len(info["bad_json_lines"])
            examples["bad_json"] += [f"{path.name}:{n}" for n in info["bad_json_lines"][:5]]
        for line_no, row in rows:
            reason = RD.check_row(row, country, source)
            if reason:
                dropped[reason] += 1
                if len(examples[reason]) < 10:
                    examples[reason].append(f"{path.name}:{line_no} {str(row.get('id') if isinstance(row, dict) else '')[:80]}")
                continue
            fetched = RD.parse_ts(row.get("fetched"))
            if fetched is None:
                excluded["no_fetched_time_kept"] += 1
            elif fetched > cutoff:
                excluded["fetched_after_snapshot"] += 1
                continue
            if (d_from and row["date"] < d_from) or (d_to and row["date"] > d_to):
                excluded["outside_date_range"] += 1
                continue
            if row["id"] in seen:
                excluded["duplicate_id"] += 1
                continue
            seen.add(row["id"])
            if row["outlet"] == "official":
                official[country].append(RD.official_record(row))
            else:
                media[country].append(RD.media_record(row))
    return {"official": official, "media": media, "dropped": dropped, "examples": examples,
            "excluded": excluded, "inputs": inputs}


def summarise(data: Dict[str, Any]) -> Dict[str, Any]:
    groups: Dict[tuple, Dict[str, Any]] = {}
    values: Dict[str, Counter] = defaultdict(Counter)
    speakers: Dict[str, Counter] = defaultdict(Counter)
    for kind in ("official", "media"):
        for recs in data[kind].values():
            for r in recs:
                g = groups.setdefault((r["country"], r["outlet"], r["source"], r["lang"]),
                                      {"n": 0, "first": r["date"], "last": r["date"], "orgs": Counter()})
                g["n"] += 1
                g["first"], g["last"] = min(g["first"], r["date"]), max(g["last"], r["date"])
                g["orgs"][r.get("org") or ""] += 1
                for f in VALUE_FIELDS:
                    if f in r:
                        values[f][str(r[f]) if r[f] is not None else "null"] += 1
                if r.get("speaker"):
                    speakers[r["source"]][r["speaker"]] += 1
    coverage = []
    for (cc, outlet, src, lang), g in sorted(groups.items()):
        orgs = [o for o, _ in g["orgs"].most_common() if o]
        org = ", ".join(orgs) if len(orgs) <= 3 else f"{', '.join(orgs[:3])} +{len(orgs) - 3} more"
        coverage.append({"country": cc, "outlet": outlet, "source": src, "lang": lang, "org": org,
                         "first": g["first"].isoformat(), "last": g["last"].isoformat(), "n": g["n"]})
    by_co: Counter = Counter()
    for c in coverage:
        by_co[f"{c['country']}/{c['outlet']}"] += c["n"]
    totals = {"official": sum(len(v) for v in data["official"].values()),
              "media": sum(len(v) for v in data["media"].values()),
              "countries": len({c["country"] for c in coverage}), "sources": len({c["source"] for c in coverage})}
    return {"coverage": coverage, "values": values, "speakers": speakers, "totals": totals,
            "rows_by_country_outlet": dict(sorted(by_co.items()))}


def write_data(data: Dict[str, Any], out: Path) -> List[Dict[str, Any]]:
    files = []
    for cc, recs in sorted(data["official"].items()):
        RD.write_parquet(recs, RD.OFFICIAL_SCHEMA, out / "official" / f"{cc}.parquet")
        RD.write_jsonl_gz(recs, out / "official_jsonl" / f"{cc}.jsonl.gz")
        files += [{"path": f"official/{cc}.parquet", "rows": len(recs)},
                  {"path": f"official_jsonl/{cc}.jsonl.gz", "rows": len(recs)}]
    for cc, recs in sorted(data["media"].items()):
        RD.write_parquet(recs, RD.MEDIA_SCHEMA, out / "media" / f"{cc}.parquet")
        files.append({"path": f"media/{cc}.parquet", "rows": len(recs)})
    for f in files:
        f["bytes"] = (out / f["path"]).stat().st_size
    return sorted(files, key=lambda f: f["path"])


def build(a: argparse.Namespace) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    final = a.out_root / a.version
    if final.exists() and not a.overwrite:
        raise SystemExit(f"{final} exists; releases are never overwritten (use a new --version, or --overwrite)")
    if a.docs == ROOT / "docs" and ((ROOT / "docs-parts").exists() or (ROOT / "state" / "ids").exists()):
        # Segmented store: sealed documents live in docs-parts/, docs/ holds only the unsealed tail.
        a.docs = ROOT / "staging" / "export_docs"
        shutil.rmtree(a.docs, ignore_errors=True)
        n = segments.materialize(ROOT, a.docs, a.countries)
        logger.info("materialized %d documents from docs-parts/ + docs/ into %s", sum(n.values()), a.docs)
    files = sorted(p for p in a.docs.glob("*/*.jsonl")
                   if not a.countries or p.parent.name in {c.upper() for c in a.countries})
    in_bytes = sum(p.stat().st_size for p in files)
    if in_bytes > a.max_gb * 1e9:
        raise SystemExit(f"selected input is {in_bytes / 1e9:.2f} GB > --max-gb {a.max_gb}; ask before writing")
    a.out_root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(a.out_root).free
    if free - 2 * in_bytes < a.min_free_gb * 1e9:
        raise SystemExit(f"only {free / 1e9:.1f} GB free; the build could leave less than {a.min_free_gb} GB")
    snap = resolve_cutoff(a.snapshot_date, now)
    tmp = a.out_root / f".{a.version}.partial"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    data = collect(files, RD.parse_ts(snap["effective"]), a.date_from, a.date_to)
    summary = summarise(data)
    data_files = write_data(data, tmp)
    meta = {r["id"]: {"country": r["country"], "source": r["source"], "outlet": r["outlet"]}
            for kind in ("official", "media") for recs in data[kind].values() for r in recs}
    semantic = release_semantic.export(a.semantic_dir, meta, tmp)
    if semantic.get("included"):
        data_files.append({"path": "semantic/doc_scores.parquet", "rows": semantic["rows"],
                           "bytes": (tmp / "semantic" / "doc_scores.parquet").stat().st_size})
    ctx = {"version": a.version, "build_time": now.isoformat(timespec="seconds"), "snapshot": snap,
           "build_date_local": now.astimezone().date().isoformat(),
           "git": git_info(a.git_root), "semantic": semantic, "data_files": data_files,
           "dropped_total": sum(data["dropped"].values()), **summary}
    with (tmp / "coverage.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["country", "outlet", "source", "lang", "org", "first", "last", "n"])
        w.writeheader()
        w.writerows(summary["coverage"])
    prev = previous_manifest(a.out_root, a.version)
    (tmp / "README.md").write_text(release_docs.render_readme(ctx), encoding="utf-8")
    (tmp / "CODEBOOK.md").write_text(release_docs.render_codebook(ctx), encoding="utf-8")
    (tmp / "CHANGELOG.md").write_text(release_docs.render_changelog(ctx, prev), encoding="utf-8")
    (tmp / "ZENODO_METADATA.json").write_text(
        json.dumps(release_docs.zenodo_metadata(ctx), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    listing = []
    for p in sorted(x for x in tmp.rglob("*") if x.is_file()):
        rel = p.relative_to(tmp).as_posix()
        rows = next((f["rows"] for f in data_files if f["path"] == rel), None)
        listing.append({"path": rel, "bytes": p.stat().st_size, "rows": rows, "sha256": sha256_file(p)})
    manifest = {
        "dataset": release_docs.N.TITLE, "version": a.version, "build_time": ctx["build_time"],
        "snapshot": snap, "date_from": a.date_from, "date_to": a.date_to,
        "countries_filter": sorted(c.upper() for c in a.countries) if a.countries else None,
        "corpus_repo": {"path": str(a.git_root), **ctx["git"]},
        "totals": summary["totals"], "rows_by_country_outlet": summary["rows_by_country_outlet"],
        "coverage": summary["coverage"],
        "validation": {"dropped": dict(data["dropped"]), "dropped_total": ctx["dropped_total"],
                       "examples": dict(data["examples"]), "excluded": dict(data["excluded"])},
        "semantic": semantic, "inputs": data["inputs"], "files": listing,
        "total_bytes": sum(f["bytes"] for f in listing),
    }
    (tmp / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str) + "\n",
                                       encoding="utf-8")
    sums = [f"{f['sha256']}  {f['path']}" for f in listing]
    sums.append(f"{sha256_file(tmp / 'MANIFEST.json')}  MANIFEST.json")
    (tmp / "SHA256SUMS").write_text("\n".join(sorted(sums, key=lambda s: s.split("  ", 1)[1])) + "\n",
                                    encoding="utf-8")
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    return manifest


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Build a versioned dataset release (local only, no upload).")
    ap.add_argument("--version", required=True, help="release version, e.g. 0.1.0")
    ap.add_argument("--snapshot-date", help="YYYY-MM-DD (end of that local day) or ISO timestamp; default = now")
    ap.add_argument("--countries", nargs="+", help="country codes to include (default all)")
    ap.add_argument("--from", dest="date_from", help="earliest publication date YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="latest publication date YYYY-MM-DD")
    ap.add_argument("--docs", type=Path, default=ROOT / "docs")
    ap.add_argument("--out-root", type=Path, default=ROOT / "release")
    ap.add_argument("--semantic-dir", type=Path, default=ROOT / "index" / "semantic")
    ap.add_argument("--git-root", type=Path, default=ROOT)
    ap.add_argument("--max-gb", type=float, default=2.0, help="refuse if the selected input exceeds this")
    ap.add_argument("--min-free-gb", type=float, default=8.0, help="refuse if free disk could fall below this")
    ap.add_argument("--overwrite", action="store_true", help="replace an existing release/<version>")
    a = ap.parse_args(argv)
    for d in (a.date_from, a.date_to):
        if d:
            date.fromisoformat(d)
    return a


def main(argv: Optional[List[str]] = None) -> Dict[str, Any]:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    m = build(parse_args(argv))
    print(json.dumps({"version": m["version"], "snapshot": m["snapshot"]["effective"], **m["totals"],
                      "dropped": m["validation"]["dropped_total"], "excluded": m["validation"]["excluded"],
                      "semantic_included": m["semantic"].get("included"),
                      "total_mb": round(m["total_bytes"] / 1e6, 1)}, ensure_ascii=False))
    return m


if __name__ == "__main__":
    main()
