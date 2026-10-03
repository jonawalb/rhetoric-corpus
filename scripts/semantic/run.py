"""CLI for the semantic layer.

  uv run python -m scripts.semantic.run                       # all stages, incremental
  uv run python -m scripts.semantic.run --stage embed,score   # selected stages
  uv run python -m scripts.semantic.run --full                # recompute (re-tag, re-score, refit topics)
  uv run python -m scripts.semantic.run search "query" [--country RU] [--k 10]
  uv run python -m scripts.semantic.run validate reports/semantic/validation_sample_coded.csv

Stages, in order: sync, targets, embed, teacher, train, active, train, score, topics, trends, echo, brief\n(`active` runs once; `train` re-fits only when the teacher sample grew >= 10 %).
Extra stages (not in the default run): audit (gazetteer audit report), export (validation sample),\necho-tune (echo threshold audit report).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from typing import Dict, List, Optional

from . import store

ORDER = ["sync", "targets", "embed", "teacher", "train", "active", "train", "score", "topics", "trends", "echo", "brief"]
logger = logging.getLogger("semantic")


def _stage(name: str, con, a: argparse.Namespace) -> Dict:
    if name == "sync":
        return store.sync_docs(con)
    if name == "targets":
        from .tag import run_targets
        return run_targets(con, full=a.full)
    if name == "audit":
        from .tag import audit
        return {"report": audit(con)}
    if name == "embed":
        from .embed import run_embed
        return run_embed(con, budget_s=a.budget, max_docs=a.max_docs)
    if name == "teacher":
        from .teacher import run_teacher
        return run_teacher(con)
    if name == "train":
        from .tone import run_train
        return run_train(con, force=a.full)
    if name == "active":
        from .active import run_active
        return run_active(con, force=a.full)
    if name == "score":
        from .tone import run_score
        return run_score(con, full=a.full, budget_s=a.budget)
    if name == "export":
        from .validate import export_sample
        return export_sample(con)
    if name == "topics":
        from .topics import run_topics
        return run_topics(con, refit=a.full or a.refit_topics)
    if name == "trends":
        from .trends import run_trends
        return run_trends(con)
    if name == "echo":
        from .echo import run_echo
        return run_echo(con)
    if name == "echo-tune":
        from .echo import tune
        return {"report": tune(con)}
    if name == "brief":
        from .brief import run_brief
        return run_brief(con)
    raise SystemExit(f"unknown stage {name!r}; stages: {', '.join(ORDER + ['audit', 'export', 'echo-tune'])}")


def write_manifest(con, report: Dict) -> None:
    """aggregates/manifest.json: versions, coverage and last run report (for the UI and for provenance)."""
    from dataclasses import asdict
    from datetime import datetime, timezone

    from .config import AGG_DIR, NLI_MODEL, SETTINGS
    from .targets import default

    cov = {}
    for c, n, e, sc, mn, mx in con.execute(
            "SELECT country, count(*), sum(embedded), sum(scored_v IS NOT NULL), min(date), max(date)"
            " FROM docs WHERE present=1 GROUP BY country ORDER BY country"):
        cov[c] = {"docs": n, "embedded": e or 0, "scored": sc or 0, "first": mn, "last": mx}
    q = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    man = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "versions": {"gazetteer": default().version, "tone_model": store.get_meta(con, "tone_train"),
                        "e5": "intfloat/multilingual-e5-small", "nli_teacher": NLI_MODEL},
           "totals": {"docs": q("SELECT count(*) FROM docs WHERE present=1"), "passages": q("SELECT count(*) FROM passages"),
                      "sentences_scored": q("SELECT count(*) FROM sent_scores"), "mentions": q("SELECT count(*) FROM mentions"),
                      "teacher_sample": q("SELECT count(*) FROM teacher")},
           "coverage_by_country": cov, "settings": asdict(SETTINGS), "last_run": report}
    AGG_DIR.mkdir(parents=True, exist_ok=True)
    (AGG_DIR / "manifest.json").write_text(json.dumps(man, indent=1, ensure_ascii=False, default=str), encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if argv and argv[0] == "search":
        from .search import main as search_main
        return search_main(argv[1:])
    if argv and argv[0] == "validate":
        from .validate import main as validate_main
        return validate_main(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", default=",".join(ORDER), help="comma-separated stages (default: all, in order)")
    ap.add_argument("--incremental", action="store_true", help="(default behaviour) only process new/changed docs")
    ap.add_argument("--full", action="store_true", help="recompute tags/scores and refit topics")
    ap.add_argument("--refit-topics", action="store_true")
    ap.add_argument("--budget", type=float, default=None, help="seconds per embedding/scoring stage (time box)")
    ap.add_argument("--max-docs", type=int, default=None, help="cap on docs embedded this run (newest first)")
    a = ap.parse_args(argv)
    con = store.connect()
    report = {}
    for name in [s.strip() for s in a.stage.split(",") if s.strip()]:
        t0 = time.time()
        logger.info("== stage %s", name)
        out = _stage(name, con, a)
        out = dict(out or {}, seconds=round(time.time() - t0, 1))
        report[name if name not in report else f"{name}_2"] = out
        logger.info("== %s done: %s", name, json.dumps(out, ensure_ascii=False, default=str)[:600])
    report["disk_mb"] = store.disk_usage()
    write_manifest(con, report)
    print(json.dumps(report, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
