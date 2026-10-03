"""Search the local corpus (index/corpus.sqlite) from the command line. Full texts stay local.

Examples
  uv run python scripts/search.py "plutonium pit"
  uv run python scripts/search.py 'плутониевый сердечник' --morph --country RU
  uv run python scripts/search.py '"pit production" OR "plutonium core"' --from 2023-01-01 --count-by month
  uv run python scripts/search.py 'Taiwan NEAR/5 independence' --source mfa_cn --lang en --export taiwan.csv
  uv run python scripts/search.py 钚芯 --lang zh

See scripts/searchlib.py for the query language. Output per document: date, country/source, speaker,
title, hit count, up to --snippets KWIC lines with the hit highlighted, URL.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import searchlib as sl  # noqa: E402
from build_index import DB  # noqa: E402


def _csv(v: Optional[str]) -> List[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Full-text search over the rhetoric corpus.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=sl.__doc__)
    ap.add_argument("query")
    ap.add_argument("--country", help="comma list, e.g. RU,IR,CN")
    ap.add_argument("--source", help="comma list of source ids, e.g. kremlin_en,mfa_cn")
    ap.add_argument("--lang", help="comma list, e.g. en,ru,zh,fa")
    ap.add_argument("--from", dest="date_from", default="", help="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", default="", help="YYYY-MM-DD")
    ap.add_argument("-m", "--morph", action="store_true", help="expand Russian words to their inflected forms")
    ap.add_argument("-n", "--limit", type=int, default=20)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--snippets", type=int, default=2, help="KWIC lines per document")
    ap.add_argument("--sort", choices=["date", "rank"], default="date")
    ap.add_argument("--count-by", choices=["week", "month", "year", "source", "country"])
    ap.add_argument("--export", metavar="CSV", help="write every matching document (one row per hit) to CSV")
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--explain", action="store_true", help="print the FTS5 expression used")
    a = ap.parse_args(argv)

    con = sl.sqlite3.connect(a.db)
    f = sl.Filters(_csv(a.country), _csv(a.source), _csv(a.lang), a.date_from, a.date_to)
    t0 = time.time()
    try:
        res = sl.search(con, a.query, f, a.limit, a.offset, a.morph, a.sort, max(a.snippets, 1))
    except (ValueError, sl.sqlite3.OperationalError) as e:
        print(f"query error: {e}", file=sys.stderr)
        return 2
    dt = time.time() - t0
    tty = sys.stdout.isatty()
    hl = (lambda s: f"\033[1;33m{s}\033[0m") if tty else (lambda s: f"[[{s}]]")
    dim = (lambda s: f"\033[2m{s}\033[0m") if tty else (lambda s: s)
    if a.explain:
        print(dim(f"table={res['table']}  fts={res['fts'] or '(python scan)'}  morph={sl.morph_engine() if a.morph else 'off'}"))
    if res["note"]:
        print(dim(res["note"]))

    if a.count_by:
        rows = sl.count_by(con, res["plan"], res["rows"], a.count_by)
        print(f"{'bucket':<12} {'docs':>6} {'hits':>6}")
        top = max((d for _, d, _ in rows), default=1)
        for k, d, h in rows:
            print(f"{k:<12} {d:>6} {h:>6}  {'█' * max(1, round(50 * d / top))}")
        print(f"{res['total_docs']} documents ({dt:.2f}s)")
        return 0

    if a.export:
        allrows = res["rows"]
        hits = sl.doc_hits(con, res["plan"], [r[0] for r in allrows], max_snips=1000)
        with open(a.export, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "country", "source", "org", "lang", "speaker", "title", "url", "doc_id", "doc_hits",
                        "hit", "snippet"])
            n = 0
            for r in allrows:
                d = hits[r[0]]
                for left, hit, right in d["snippets"] or [("", "", "")]:
                    w.writerow([d["date"], d["country"], d["source"], d["org"], d["lang"], d["speaker"] or "", d["title"],
                                d["url"], d["id"], d["hits"], hit, f"{left}{hit}{right}"])
                    n += 1
        print(f"wrote {n} rows for {len(allrows)} documents to {a.export}")
        return 0

    for d in res["results"]:
        who = f" · {d['speaker']}" if d["speaker"] else ""
        print(f"\n{d['date']}  {d['country']}/{d['source']} ({d['org']}, {d['lang']}){who}  [{d['hits']} hit{'s' if d['hits'] != 1 else ''}]")
        print(f"  {d['title'][:140]}")
        for left, hit, right in d["snippets"][: a.snippets]:
            print(f"  … {left}{hl(hit)}{right}")
        print(dim(f"  {d['url']}"))
    shown = len(res["results"])
    print(f"\n{res['total_docs']} matching documents; showing {a.offset + 1 if shown else 0}-{a.offset + shown} ({dt:.2f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
