"""Export daily, within-stream rhetoric series (tone, target stance, target salience) plus spike weeks with evidence,
for the Say-Do Gap Monitor in ~/Projects/tsm-strait-layers (tools/say-do-taiwan, tools/say-do-global).

Read-only on the semantic store. Every series is computed within ONE stream (source x language x selection regime);
streams are never pooled. Keyword-filtered regimes (EXCLUDED_SAMPLES) are left out, as in the trends stage.

Output (one JSON file):
  meta     corpus manifest date, model versions, teacher-student agreement, parameters, text policy
  streams  [{stream, country, source, outlet, lang, sample, n_docs, first, last}]
  daily    {stream: {"first": date, "cols": [...], "rows": [[day_offset, n, sums...], ...]}}  (days with docs only)
           tone sums are sums of document tone (mean = sum / n); stance sums are over docs mentioning the
           target (mean = sum / n_target); salience k = docs mentioning the target (share = k / n)
  spikes   {stream: {series_key: [{week, z, delta, mean, base, n, peak_day, evidence: [...]}]}}

Spike rule (weekly, upward only): delta = week mean - mean of the stream's previous 12 weeks with >= --min-n docs
(at least 6 such weeks; scripts/semantic/stats.rolling_z, which also gives the z reported). A week is a spike when
its delta is at or above the --q quantile of that series' deltas, >= --min-delta, and its z >= --min-z. Evidence: up to --k sentences
from the spike week. Text policy: official outlets keep the sentence (<= 300 chars); every other outlet keeps only the
headline (<= 200 chars) and the link.

Usage:
  uv run python -m scripts.say_do_export --countries CN --targets TAIWAN,US,JAPAN,PHILIPPINES --out <file.json>
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

from scripts.semantic import evidence, stats, store, trends
from scripts.semantic.config import AGG_DIR, DIMS

logger = logging.getLogger("say_do_export")
TONE = list(DIMS) + ["esc_balance"]
STANCE = ["hostility", "threat", "conciliation", "esc_balance"]


def stance_rows(con: sqlite3.Connection, doc_ids: List[str], targets: List[str]) -> pd.DataFrame:
    """Doc x target: mean tone of the doc's scored sentences mentioning the target (self-mentions excluded)."""
    con.execute("CREATE TEMP TABLE IF NOT EXISTS sd_ids(doc_id TEXT PRIMARY KEY)")
    con.execute("DELETE FROM sd_ids")
    con.executemany("INSERT INTO sd_ids VALUES (?)", [(d,) for d in doc_ids])
    ph = ",".join("?" * len(targets))
    q = (f"SELECT m.doc_id, m.target, {', '.join('avg(s.' + x + ')/1000.0 AS ' + x for x in DIMS)}"
         f" FROM (SELECT DISTINCT doc_id, idx, target FROM mentions WHERE self=0 AND target IN ({ph})"
         f"       AND doc_id IN (SELECT doc_id FROM sd_ids)) m"
         " JOIN sent_scores s ON s.doc_id=m.doc_id AND s.idx=m.idx GROUP BY m.doc_id, m.target")
    st = pd.read_sql_query(q, con, params=targets)
    st["esc_balance"] = st["escalation"] - st["deescalation"]
    q2 = (f"SELECT DISTINCT doc_id, target FROM mentions WHERE self=0 AND target IN ({ph})"
          " AND doc_id IN (SELECT doc_id FROM sd_ids)")
    sal = pd.read_sql_query(q2, con, params=targets)
    sal["k"] = 1
    return st.merge(sal, on=["doc_id", "target"], how="outer")


def series_frames(docs: pd.DataFrame, st: pd.DataFrame, targets: List[str]) -> Dict[str, pd.DataFrame]:
    """series_key -> doc-level frame with columns doc_id, stream, date, week, value (NaN = doc not in series)."""
    out = {}
    base = docs[["doc_id", "stream", "date", "week"]]
    for m in TONE:
        out[f"tone:{m}"] = base.assign(value=docs[m].to_numpy())
    for t in targets:
        s = st[st["target"] == t].set_index("doc_id")
        for m in STANCE:
            v = s[m].dropna()
            out[f"stance:{t}:{m}"] = base[base["doc_id"].isin(v.index)].assign(value=lambda f: f["doc_id"].map(v))
        k = s["k"].fillna(0)
        out[f"sal:{t}"] = base.assign(value=base["doc_id"].map(k).fillna(0.0).to_numpy())
    return out


def find_spikes(frame: pd.DataFrame, stream_docs: pd.DataFrame, key: str, args) -> List[Dict]:
    """Upward weekly spikes of one stream series (see module docstring)."""
    if frame.empty:
        return []
    is_share = key.startswith("sal:")
    g = frame.groupby("week")["value"].agg(["count", "mean", "std"])
    if is_share:  # share series: denominator = all docs of the stream that week
        tot = stream_docs.groupby("week").size()
        k = frame.groupby("week")["value"].sum().reindex(tot.index).fillna(0)
        g = pd.DataFrame({"count": tot, "mean": k / tot})
        pc = (k + 0.5) / (tot + 1)
        g["se"] = np.sqrt(pc * (1 - pc) / tot)
    else:
        g["se"] = g["std"].fillna(0) / np.sqrt(g["count"].clip(lower=1))
    cal = stats.calendar(g.index.min(), g.index.max(), "week")
    g = g.reindex(cal)
    n = g["count"].fillna(0).to_numpy(float)
    z, mu, d = stats.rolling_z(g["mean"].to_numpy(float), n, g["se"].to_numpy(float), 12, 6, args.min_n,
                               binomial=is_share)
    ok = ~np.isnan(d)
    if ok.sum() < 10:
        return []
    thr = max(float(np.quantile(d[ok], args.q)), args.min_delta)
    res = []
    for i, wk in enumerate(cal):
        if not ok[i] or d[i] < thr or z[i] < args.min_z:
            continue
        days = frame[frame["week"] == wk]
        if is_share:
            dd = stream_docs[stream_docs["week"] == wk].groupby("date").size().to_frame("n")
            dd["k"] = days.groupby("date")["value"].sum()
            contrib = dd["k"].fillna(0) - dd["n"] * mu[i]
        else:
            contrib = days.groupby("date")["value"].agg(lambda v: (v - mu[i]).sum())
        res.append({"week": wk, "z": round(float(z[i]), 2), "delta": round(float(d[i]), 4),
                    "mean": round(float(g["mean"].iloc[i]), 4), "base": round(float(mu[i]), 4), "n": int(n[i]),
                    "peak_day": str(contrib.idxmax())})
    return res


def attach_evidence(con, cc, spikes: List[Dict], key: str, stream_docs: pd.DataFrame, k: int = 3) -> None:
    parts = key.split(":")
    for sp in spikes:
        sel = stream_docs[stream_docs["week"] == sp["week"]]
        if parts[0] == "sal":
            ev = evidence.top_mentions(con, cc, sel, parts[1], k=k)
        elif parts[0] == "stance":
            ev = evidence.top_sentences(con, cc, sel, evidence._rank_metric(parts[2], 1.0), target=parts[1], k=k)
        else:
            ev = evidence.top_sentences(con, cc, sel, evidence._rank_metric(parts[1], 1.0), k=k)
        sp["evidence"] = [publishable(e) for e in ev]


def publishable(e: Dict) -> Dict:
    """Official: sentence <= 300 chars + link. Media: headline + link only (no body text)."""
    rec = {"date": e["date"], "source": e["source"], "outlet": e["outlet"], "url": e["url"],
           "title": (e.get("title") or "")[:200]}
    if e["outlet"] == "official":
        rec["text"] = (e.get("text") or "")[:300]
    return rec


def daily_table(sdocs: pd.DataFrame, st: pd.DataFrame, targets: List[str]) -> Dict:
    first = pd.Timestamp(sdocs["date"].min())
    g = sdocs.groupby("date")
    tab = pd.DataFrame({"n": g.size()})
    cols = ["n"]
    for m in TONE:
        tab[f"t_{m}"] = g[m].sum()
        cols.append(f"tone:{m}")
    s = st[st["doc_id"].isin(sdocs["doc_id"])].merge(sdocs[["doc_id", "date"]], on="doc_id")
    for t in targets:
        sg = s[s["target"] == t].groupby("date")
        tab[f"k_{t}"] = sg["k"].sum()
        cols.append(f"sal:{t}")
        for m in STANCE:
            tab[f"nt_{t}_{m}"] = sg[m].count()
            tab[f"s_{t}_{m}"] = sg[m].sum()
            cols += [f"stance_n:{t}:{m}", f"stance:{t}:{m}"]
    tab = tab.fillna(0).sort_index()
    rows = []
    for d, r in tab.iterrows():
        off = (pd.Timestamp(d) - first).days
        vals = [round(float(x), 4) if abs(x - round(x)) > 1e-9 else int(round(x)) for x in r.to_numpy(float)]
        rows.append([off, *vals])
    return {"first": first.strftime("%Y-%m-%d"), "cols": ["day", *cols], "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--countries", required=True)
    ap.add_argument("--targets", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-docs", type=int, default=300, help="minimum docs for a stream to be exported")
    ap.add_argument("--min-span-days", type=int, default=60)
    ap.add_argument("--min-n", type=int, default=3, help="minimum docs in a week for a weekly z")
    ap.add_argument("--q", type=float, default=0.9, help="spike = weekly delta at or above this quantile of the series")
    ap.add_argument("--min-delta", type=float, default=0.01)
    ap.add_argument("--min-z", type=float, default=1.5, help="spike weeks also need z >= this (guards thin weeks)")
    ap.add_argument("--k", type=int, default=2, help="evidence sentences per spike week")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    countries, targets = args.countries.split(","), args.targets.split(",")

    con, cc = store.connect(), store.corpus()
    docs = trends.doc_frame(con)
    docs = docs[docs["country"].isin(countries)].copy()
    info = docs.groupby("stream").agg(country=("country", "first"), source=("source", "first"),
                                      outlet=("outlet", "first"), lang=("lang", "first"),
                                      sample=("sample", "first"), n_docs=("doc_id", "size"),
                                      first=("date", "min"), last=("date", "max")).reset_index()
    info["sample"] = info["sample"].fillna("all")
    span = (pd.to_datetime(info["last"]) - pd.to_datetime(info["first"])).dt.days
    info = info[(info["n_docs"] >= args.min_docs) & (span >= args.min_span_days)]
    docs = docs[docs["stream"].isin(info["stream"])]
    logger.info("%d streams, %d docs", len(info), len(docs))
    st = stance_rows(con, docs["doc_id"].tolist(), targets)
    logger.info("stance/salience rows: %d", len(st))

    out_daily, out_spikes = {}, {}
    for stream, sdocs in docs.groupby("stream"):
        out_daily[stream] = daily_table(sdocs, st, targets)
        frames = series_frames(sdocs, st[st["doc_id"].isin(sdocs["doc_id"])], targets)
        sp_all = {}
        for key, fr in frames.items():
            sp = find_spikes(fr, sdocs, key, args)
            if sp:
                attach_evidence(con, cc, sp, key, sdocs, k=args.k)
                sp_all[key] = sp
        out_spikes[stream] = sp_all
        logger.info("%s: %d days, %d spikes", stream, len(out_daily[stream]["rows"]), sum(map(len, sp_all.values())))

    manifest = json.loads((AGG_DIR / "manifest.json").read_text())
    val = json.loads((AGG_DIR / "validation.json").read_text())
    meta = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "corpus_manifest": manifest.get("generated"), "versions": manifest.get("versions"),
            "validation_note": val.get("note"), "n_heldout": val.get("n_heldout"),
            "validation": {d: {k: val["dims"][d]["overall"].get(k) for k in ("auc", "pearson", "n")} for d in val["dims"]},
            "params": vars(args), "tone": TONE, "stance": STANCE, "targets": targets,
            "text_policy": "official: sentence <= 300 chars + link; other outlets: headline + link only"}
    rec = {"meta": meta, "streams": info.to_dict("records"), "daily": out_daily, "spikes": out_spikes}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rec, ensure_ascii=False, separators=(",", ":"), default=str))
    logger.info("wrote %s (%.1f MB)", args.out, Path(args.out).stat().st_size / 1e6)


if __name__ == "__main__":
    main()
