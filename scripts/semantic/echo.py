"""Stage `echo`: near-identical framings across different countries within +-14 days.

Edges: passage pairs from different countries, dated at most `echo_window_days` apart, with cosine similarity
>= `echo_threshold` (tuned by inspection; see reports/semantic/echo_threshold_audit.md, written by
`tune()` / `run --stage echo-tune`). Clusters = connected components of the edge graph. Each cluster lists its
members in date order, the first-seen country/date, and the order in which countries appear.

Interpretation limits: a cross-country match can be amplification (state media repeating another state's line),
plain reporting/quotation of the other side, or two outlets carrying the same wire copy. "First seen" means first
in THIS corpus, whose coverage differs by country and period (SOURCES.md); it is not proof of origin.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import store
from .config import AGG_DIR, REPORT_DIR, SETTINGS

logger = logging.getLogger(__name__)
CJK = ("zh", "ko", "ja")


def _candidates(con: sqlite3.Connection) -> Tuple[pd.DataFrame, np.ndarray]:
    pf = store.passage_frame(con)
    pids, E = store.load_passages(con)
    pos = np.searchsorted(pids, pf["pid"].to_numpy())
    pos = np.minimum(pos, max(len(pids) - 1, 0))
    ok = pids[pos] == pf["pid"].to_numpy()
    pf, pos = pf[ok].reset_index(drop=True), pos[ok]
    minc = np.where(pf["lang"].isin(CJK), SETTINGS.echo_min_chars_cjk, SETTINGS.echo_min_chars)
    nch = pd.read_sql_query("SELECT pid, nchars FROM passages", con).set_index("pid")["nchars"]
    keep = (pf["pid"].map(nch).to_numpy() >= minc) & pd.to_datetime(pf["date"], errors="coerce").notna().to_numpy()
    pf, pos = pf[keep].reset_index(drop=True), pos[keep]
    # Titles of official transcripts/briefings/statements are metadata ("Meeting with ...", "Press briefing ...");
    # only article/headline titles are kept as framing content. Per-source title templates are dropped too.
    drop = template_titles(pf) | ((pf["s0"] < 0) & ~pf["kind"].isin(["article", "headline"])).to_numpy()
    pf, pos = pf[~drop].reset_index(drop=True), pos[~drop]
    pf["day"] = (pd.to_datetime(pf["date"]) - pd.Timestamp("2000-01-01")).dt.days
    order = np.argsort(pf["day"].to_numpy(), kind="stable")
    pf, pos = pf.iloc[order].reset_index(drop=True), pos[order]
    return pf, E[pos]


def template_titles(pf: pd.DataFrame, min_repeats: int = 3) -> np.ndarray:
    """Title passages that follow a per-source template once digits/punctuation are removed ("Transcript of the Press
    Briefing by the Spokesperson on Thursday 2 April 2026", "MFA press conference, 2026-05-19"). They match the
    templates of other countries' briefings and are excluded from echo detection."""
    norm = pf["title"].fillna("").str.lower().str.replace(r"[\d\W_]+", " ", regex=True).str.strip()
    key = pf["source"] + "|" + norm
    counts = key.map(key[pf["s0"] < 0].value_counts()).fillna(0)
    return ((pf["s0"] < 0) & (counts >= min_repeats)).to_numpy()


def edges(pf: pd.DataFrame, X: np.ndarray, threshold: float, window: int = SETTINGS.echo_window_days) -> np.ndarray:
    """[(i, j, sim)] over row indices of pf (sorted by day): different countries, |day diff| <= window, sim >= thr."""
    day = pf["day"].to_numpy()
    ctry = pd.factorize(pf["country"])[0]
    out: List[np.ndarray] = []
    start = 0
    n = len(pf)
    while start < n:
        stop = np.searchsorted(day, day[start] + 7, side="left")
        hi = np.searchsorted(day, day[stop - 1] + window, side="right")
        Q = X[start:stop].astype(np.float32)
        Cn = X[start:hi].astype(np.float32)
        for a in range(0, len(Q), 2048):
            S = Q[a:a + 2048] @ Cn.T
            qi = np.arange(start + a, start + a + len(S))[:, None]
            cj = np.arange(start, hi)[None, :]
            mask = (S >= threshold) & (cj > qi) & (ctry[qi] != ctry[cj]) & (np.abs(day[cj] - day[qi]) <= window)
            ii, jj = np.nonzero(mask)
            if len(ii):
                out.append(np.c_[qi[ii, 0], cj[0, jj], S[ii, jj]])
        start = stop
    return np.concatenate(out) if out else np.zeros((0, 3))


def components(n: int, e: np.ndarray) -> Dict[int, List[int]]:
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j, _ in e:
        a, b = find(int(i)), find(int(j))
        if a != b:
            parent[max(a, b)] = min(a, b)
    groups: Dict[int, List[int]] = defaultdict(list)
    for v in {int(x) for x in e[:, 0]} | {int(x) for x in e[:, 1]}:
        groups[find(v)].append(v)
    return groups


def passage_text(cc: sqlite3.Connection, r) -> str:
    if r["s0"] < 0:
        return r["title"] or ""
    rows = cc.execute("SELECT text FROM sentences WHERE doc=? AND idx BETWEEN ? AND ? ORDER BY idx",
                      (int(r["crow"]), int(r["s0"]), int(r["s1"]))).fetchall()
    return " ".join(x[0] for x in rows)


def run_echo(con: sqlite3.Connection, threshold: Optional[float] = None, max_clusters: int = 500) -> Dict:
    thr = threshold or float(store.get_meta(con, "echo_threshold", str(SETTINGS.echo_threshold)))
    pf, X = _candidates(con)
    e = edges(pf, X, thr)
    groups = components(len(pf), e)
    sims = defaultdict(list)
    for i, j, s in e:
        sims[int(i)].append(s)
        sims[int(j)].append(s)
    clusters = []
    for members in groups.values():
        members = sorted(members, key=lambda k: (pf.at[k, "day"], k))
        countries = list(dict.fromkeys(pf.loc[members, "country"]))
        sub = pf.loc[members]
        official = list(dict.fromkeys(sub.loc[sub["outlet"] == "official", "country"]))
        clusters.append((len(countries), len(members), members, countries, official))
    clusters.sort(key=lambda c: (-c[0], -len(c[4]), -c[1], -pf.at[c[2][0], "day"]))
    cc = store.corpus()
    out = []
    for n_c, n_m, members, countries, official in clusters[:max_clusters]:
        shown = members[:12]
        mem = []
        for k in shown:
            r = pf.iloc[k]
            mem.append({"date": r["date"], "country": r["country"], "source": r["source"], "outlet": r["outlet"], "lang": r["lang"],
                        "doc_id": r["doc_id"], "title": (r["title"] or "")[:200], "url": r["url"],
                        "text": passage_text(cc, r)[:300], "max_sim": round(float(max(sims[k])), 4)})
        first = pf.iloc[members[0]]
        out.append({"id": f"e{len(out):04d}", "n_countries": n_c, "n_members": n_m, "countries_in_order": countries,
                    "official_countries": official,
                    "type": "official-official" if len(official) >= 2 else ("mixed" if official else "media-media"),
                    "first_seen": {"country": first["country"], "date": first["date"], "source": first["source"]},
                    "last_date": pf.iloc[members[-1]]["date"],
                    "span_days": int(pf.at[members[-1], "day"] - pf.at[members[0], "day"]), "members": mem})
    cc.close()
    AGG_DIR.mkdir(parents=True, exist_ok=True)
    meta = {"threshold": thr, "window_days": SETTINGS.echo_window_days, "passages_compared": int(len(pf)),
            "edges": int(len(e)), "clusters": len(clusters), "clusters_written": len(out),
            "pairs_by_country": _pair_counts(pf, e)}
    (AGG_DIR / "echoes.json").write_text(json.dumps({"meta": meta, "clusters": out}, ensure_ascii=False), encoding="utf-8")
    return {k: v for k, v in meta.items() if k != "pairs_by_country"}


def _pair_counts(pf: pd.DataFrame, e: np.ndarray) -> Dict[str, int]:
    """Directed counts 'A>B' = pairs where country A's passage is dated earlier than (or same day as) B's."""
    c = defaultdict(int)
    for i, j, _ in e:
        a, b = pf.iloc[int(i)], pf.iloc[int(j)]
        first, second = (a, b) if a["day"] <= b["day"] else (b, a)
        c[f"{first['country']}>{second['country']}"] += 1
    return dict(sorted(c.items(), key=lambda x: -x[1]))


def tune(con: sqlite3.Connection, low: float = 0.88, per_bin: int = 10) -> str:
    """Write reports/semantic/echo_threshold_audit.md: edge counts and random pairs per similarity bin."""
    pf, X = _candidates(con)
    e = edges(pf, X, low)
    rng = np.random.default_rng(SETTINGS.seed)
    cc = store.corpus()
    bins = [0.88, 0.90, 0.91, 0.92, 0.93, 0.94, 0.96, 1.01]
    L = ["# Echo threshold audit", "", f"{len(pf):,} passages compared; cross-country pairs within ±{SETTINGS.echo_window_days} days.", "",
         "| cosine bin | pairs |", "|---|---|"]
    for lo, hi in zip(bins, bins[1:]):
        L.append(f"| {lo:.2f}–{min(hi, 1):.2f} | {int(((e[:, 2] >= lo) & (e[:, 2] < hi)).sum()):,} |")
    for lo, hi in zip(bins, bins[1:]):
        sel = np.flatnonzero((e[:, 2] >= lo) & (e[:, 2] < hi))
        L += ["", f"## {lo:.2f}–{min(hi, 1):.2f}", ""]
        for k in rng.permutation(sel)[:per_bin]:
            i, j, s = e[k]
            a, b = pf.iloc[int(i)], pf.iloc[int(j)]
            L.append(f"- **{s:.3f}** {a['country']} {a['source']} {a['date']}: {passage_text(cc, a)[:200]}")
            L.append(f"  - {b['country']} {b['source']} {b['date']}: {passage_text(cc, b)[:200]}")
    cc.close()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    L += ["", "## Decision (2026-10-02)", "",
          "Threshold 0.93 (config `echo_threshold`). Before filtering, most pairs >= 0.94 were per-source title templates",
          "(\"Transcript of the Press Briefing by the Spokesperson on <date>\" vs other ministries' briefing titles); those",
          "are now excluded (`template_titles`). After filtering, pairs in 0.91-0.92 are mostly the same broad theme rather",
          "than the same statement; 0.92-0.93 is mixed; >= 0.93 is predominantly the same event, statement or joint text",
          "carried by two countries' outlets. A match is therefore evidence of shared content, not proof of a shared framing:",
          "read the members. Re-run this audit (`--stage echo-tune`) after large source additions."]
    out = REPORT_DIR / "echo_threshold_audit.md"
    out.write_text("\n".join(L), encoding="utf-8")
    return str(out)
