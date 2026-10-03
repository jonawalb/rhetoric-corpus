"""Build the public Trends data for tools/rhetoric-search/trends.html from the semantic-layer aggregates.

Reads  ~/Projects/rhetoric-corpus/index/semantic/aggregates/*.json  via that repo's scripts/semantic_views.py (aggregates only)
Writes staging/trends/ (default; publish/build_hf_data.py --trends puts it into the sealed Hugging Face build)
  overview.json.gz   coverage table + flags, known gaps, method text, teacher-student validation, alert tail check
  alerts.json.gz     strong + moderate alerts; evidence text ONLY from outlet == "official" (<= 300 chars), media = headline + link
  heatmap.json.gz    stance (tone of sentences mentioning a target) and salience: country x target x month
  topics.json.gz     topic terms per language, exemplar headlines + links, balanced share by country x month
  echoes.json.gz     cross-country echo clusters; official passages <= 300 chars, media members headline + link
  tone_<CC>.json.gz  combined country tone series (all streams) + per-stream series for official streams only
  index.json.gz      build info and file list
publish/build_hf_data.py seals it with the rhetoric-tier key; nothing is published from here.

Copyright guard: assert_public_safe() walks every output and FAILS the build if any `text`/`passage` value comes from
a record whose outlet is not "official", if any official text exceeds 300 characters, or if any other string longer
than 300 characters appears outside the method/notes keys. It is independent of the view builder's own filtering.

Usage (from the rhetoric-corpus repo; vendored from tsm-strait-layers tools/rhetoric-search/scripts/, keep in step):
  uv run python publish/build_trends.py [--out staging/trends]
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent  # the rhetoric-corpus repo root (publish/..)
DEFAULT_CORPUS = TOOL
STAGING = DEFAULT_CORPUS / "staging"
TEXT_KEYS = frozenset({"text", "passage", "sentence", "snippet", "body"})
MAX_TEXT = 300
LONG_OK = frozenset({"paragraphs", "note", "expected_note", "caveats", "evidence_note", "detail", "notes", "url"})
MAX_TOTAL_GZ = 5_000_000


class PublicTextError(AssertionError):
    """Raised when an output would publish non-official text or over-long passages."""


def assert_public_safe(obj, where: str = "$") -> None:
    """Fail if `obj` holds text from a non-official outlet, official text > 300 chars, or stray long strings."""
    errors: List[str] = []

    def walk(x, key, path: str) -> None:
        if isinstance(x, dict):
            for k in TEXT_KEYS & x.keys():
                v = x[k]
                if v is None or v == "":
                    continue
                if x.get("outlet") != "official":
                    errors.append(f"{path}.{k}: text from outlet {x.get('outlet')!r} (only official text may ship)")
                elif not isinstance(v, str) or len(v) > MAX_TEXT:
                    errors.append(f"{path}.{k}: official text longer than {MAX_TEXT} characters")
            for k, v in x.items():
                walk(v, k, f"{path}.{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, key, f"{path}[{i}]")
        elif isinstance(x, str) and len(x) > MAX_TEXT and key not in LONG_OK:
            errors.append(f"{path}: string of {len(x)} characters outside the allowed keys")

    walk(obj, None, where)
    if errors:
        raise PublicTextError(f"{len(errors)} public-text violation(s):\n" + "\n".join(errors[:25]))


def gz(obj) -> bytes:
    return gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9, mtime=0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--out", type=Path, default=STAGING / "trends")
    a = ap.parse_args()
    sys.path[:0] = [str(a.corpus), str(a.corpus / "scripts")]
    import semantic_views as sv  # noqa: E402

    t0 = time.time()
    agg = sv.Aggregates()
    outs = {name: sv.view(agg, name, public=True) for name in sv.view_names(agg)}
    for name, obj in outs.items():  # every file is checked before anything is written
        assert_public_safe(obj, name)
    tmp = a.out.with_name(a.out.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    sizes = {}
    for name, obj in outs.items():
        blob = gz(obj)
        (tmp / f"{name}.json.gz").write_bytes(blob)
        sizes[name] = len(blob)
    total = sum(sizes.values())
    if total > MAX_TOTAL_GZ:
        shutil.rmtree(tmp)
        raise SystemExit(f"public trend data is {total / 1e6:.1f} MB gz, over the {MAX_TOTAL_GZ / 1e6:.0f} MB budget")
    index = {"built": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
             "aggregates_generated": outs["overview"]["generated"], "data_last_date": outs["overview"]["data_last_date"],
             "views": sorted(sizes), "bytes_gz": sizes}
    (tmp / "index.json.gz").write_bytes(gz(index))
    if a.out.exists():
        shutil.rmtree(a.out)
    tmp.rename(a.out)
    report = {"files": len(sizes) + 1, "total_gz_mb": round(total / 1e6, 2),
              "largest": max(sizes, key=sizes.get), "largest_kb": round(max(sizes.values()) / 1e3),
              "alerts": len(outs["alerts"]["alerts"]), "echo_clusters": len(outs["echoes"]["clusters"]),
              "data_last_date": index["data_last_date"], "seconds": round(time.time() - t0, 1)}
    STAGING.mkdir(parents=True, exist_ok=True)
    (STAGING / "last_trends_build.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
