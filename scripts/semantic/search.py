"""Cross-lingual semantic search over stored passages (e5 "query: " prefix vs "passage: " passages).

  uv run python -m scripts.semantic.run search "warnings about NATO expansion" --country RU --k 10
  ... search "台湾问题是中国内政" --from 2026-01-01 --json

Returns the best passage per document, ranked by cosine similarity. Filters: --country --source --lang --from --to.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from . import store


@dataclass(frozen=True)
class Index:
    pf: "object"
    E: np.ndarray


_INDEX: Dict[str, Index] = {}


def load_index(con: sqlite3.Connection) -> Index:
    """Passage metadata + embeddings (cached per process)."""
    key = str(con.execute("SELECT max(hi) FROM shards").fetchone()[0])
    if key not in _INDEX:
        pf = store.passage_frame(con)
        pids, E = store.load_passages(con)
        pos = np.searchsorted(pids, pf["pid"].to_numpy())
        pos = np.minimum(pos, max(len(pids) - 1, 0))
        ok = pids[pos] == pf["pid"].to_numpy()
        _INDEX.clear()
        _INDEX[key] = Index(pf[ok].reset_index(drop=True), E[pos[ok]])
    return _INDEX[key]


def search(con: sqlite3.Connection, query: str, k: int = 10, country: Optional[List[str]] = None,
           source: Optional[List[str]] = None, lang: Optional[List[str]] = None, date_from: Optional[str] = None,
           date_to: Optional[str] = None, enc=None) -> List[Dict]:
    from .embed import E5Encoder

    idx = load_index(con)
    pf = idx.pf
    m = np.ones(len(pf), dtype=bool)
    for col, vals in (("country", country), ("source", source), ("lang", lang)):
        if vals:
            m &= pf[col].isin(vals).to_numpy()
    if date_from:
        m &= (pf["date"] >= date_from).to_numpy()
    if date_to:
        m &= (pf["date"] <= date_to).to_numpy()
    cand = np.flatnonzero(m)
    if not len(cand):
        return []
    enc = enc or E5Encoder()
    q = enc.encode([query], "query: ", max_length=128)[0]
    sims = np.empty(len(cand), dtype=np.float32)
    for i in range(0, len(cand), 100_000):
        sims[i:i + 100_000] = idx.E[cand[i:i + 100_000]].astype(np.float32) @ q
    order = cand[np.argsort(-sims)]
    sim_of = dict(zip(cand, sims))
    from .echo import passage_text

    cc = store.corpus()
    out, seen = [], set()
    for i in order:
        r = pf.iloc[i]
        if r["doc_id"] in seen:
            continue
        seen.add(r["doc_id"])
        out.append({"score": round(float(sim_of[i]), 4), "doc_id": r["doc_id"], "date": r["date"], "country": r["country"],
                    "source": r["source"], "outlet": r["outlet"], "lang": r["lang"], "title": r["title"], "url": r["url"],
                    "passage": passage_text(cc, r)[:600]})
        if len(out) >= k:
            break
    cc.close()
    return out


def _csv(v: Optional[str]) -> Optional[List[str]]:
    return [x for x in v.split(",") if x] if v else None


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(prog="run search", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--country")
    ap.add_argument("--source")
    ap.add_argument("--lang")
    ap.add_argument("--from", dest="date_from")
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    con = store.connect()
    res = search(con, a.query, a.k, _csv(a.country), _csv(a.source), _csv(a.lang), a.date_from, a.date_to)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    for r in res:
        print(f"{r['score']:.3f}  {r['date']}  {r['country']}/{r['source']}  {(r['title'] or '')[:90]}")
        print(f"       {r['passage'][:280]}")
        print(f"       {r['url']}")
