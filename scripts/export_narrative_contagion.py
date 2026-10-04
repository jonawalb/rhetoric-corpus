"""Export full narrative-echo detail for the Narrative Contagion Network tool (tsm-strait-layers/tools/narrative-contagion).

Re-runs the semantic layer's echo detection (scripts/semantic/echo.py: same candidates, threshold and window, read-only)
and writes ONE private JSON file with, for EVERY cluster (not only the 500 / first-12-members in echoes.json):
  - all members (date, country, source, outlet, lang, kind, doc_id, title, url, topic, max_sim),
  - the matched passage text (<= 300 chars) for OFFICIAL members only (outlet == "official"),
  - every edge (member index pairs, cosine similarity).
plus per-country / per-source / per-month document counts so the tool can show coverage.

This script writes nothing inside this repo. Usage (from the repo root):
    uv run python -m scripts.export_narrative_contagion --out ~/.cache/narrative-contagion/contagion_export.json
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


from scripts.semantic import store
from scripts.semantic.config import SETTINGS
from scripts.semantic.echo import _candidates, components, edges, passage_text

logger = logging.getLogger(__name__)
TEXT_CAP = 300


def _doc_topics(con) -> Dict[str, int]:
    rows = con.execute("SELECT doc_id, topic FROM doc_topic").fetchall()
    return {d: int(t) for d, t in rows if t is not None}


def _coverage(con) -> List[list]:
    """[country, source, outlet, month, docs] for every present doc whose sample is not keyword-filtered."""
    q = ("SELECT country, source, outlet, substr(date,1,7) AS m, COUNT(*) FROM docs WHERE present=1 AND date IS NOT NULL"
         " AND COALESCE(sample,'all') NOT IN ('backfill','seed') GROUP BY 1,2,3,4 ORDER BY 1,2,4")
    return [list(r) for r in con.execute(q).fetchall()]


def export(out: Path) -> Dict:
    con = store.connect()
    thr = float(store.get_meta(con, "echo_threshold", str(SETTINGS.echo_threshold)))
    pf, X = _candidates(con)
    e = edges(pf, X, thr)
    groups = components(len(pf), e)
    topics = _doc_topics(con)
    cc = store.corpus()

    edge_by_root: Dict[int, List[tuple]] = defaultdict(list)
    root_of: Dict[int, int] = {}
    for root, members in groups.items():
        for m in members:
            root_of[m] = root
    sims: Dict[int, float] = defaultdict(float)
    for i, j, s in e:
        i, j = int(i), int(j)
        edge_by_root[root_of[i]].append((i, j, float(s)))
        sims[i] = max(sims[i], float(s))
        sims[j] = max(sims[j], float(s))

    clusters = []
    for root, members in groups.items():
        members = sorted(members, key=lambda k: (pf.at[k, "day"], k))
        pos = {m: n for n, m in enumerate(members)}
        mem = []
        for k in members:
            r = pf.iloc[k]
            official = r["outlet"] == "official"
            item = {"date": r["date"], "country": r["country"], "source": r["source"], "outlet": r["outlet"],
                    "lang": r["lang"], "kind": r["kind"], "doc_id": r["doc_id"], "title": (r["title"] or "")[:300],
                    "url": r["url"], "topic": topics.get(r["doc_id"]), "max_sim": round(sims[k], 4),
                    "title_passage": bool(r["s0"] < 0)}
            if official:  # copyright policy: passage text ships for official texts only
                item["text"] = passage_text(cc, r)[:TEXT_CAP]
            mem.append(item)
        ed = sorted({(min(pos[i], pos[j]), max(pos[i], pos[j]), round(s, 4)) for i, j, s in edge_by_root[root]})
        clusters.append({"members": mem, "edges": [list(x) for x in ed]})
    cc.close()
    clusters.sort(key=lambda c: (-len({m["country"] for m in c["members"]}), -len(c["members"]), c["members"][0]["date"]))

    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "threshold": thr, "window_days": SETTINGS.echo_window_days,
        "min_chars": SETTINGS.echo_min_chars, "min_chars_cjk": SETTINGS.echo_min_chars_cjk,
        "passages_compared": int(len(pf)), "edges": int(len(e)), "clusters": clusters,
        "coverage": _coverage(con),
        "passages_by_country": dict(Counter(pf["country"])),
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    con.close()
    return {"clusters": len(clusters), "edges": int(len(e)), "passages_compared": int(len(pf)), "out": str(out)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path.home() / ".cache" / "narrative-contagion" / "contagion_export.json")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(export(args.out.expanduser())))


if __name__ == "__main__":
    main()
