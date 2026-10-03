"""Stage `trends`: weekly/monthly series (tone, target stance, target salience, topic share) and alerts.

Streams = source x language x selection regime (`sample` field); keyword-filtered regimes (EXCLUDED_SAMPLES)
are left out. All statistics: scripts/semantic/stats.py. Output: aggregates/series_*.json and alerts.json.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List

import numpy as np
import pandas as pd

from . import evidence, stats
from .config import AGG_DIR, DIMS, EXCLUDED_SAMPLES, SETTINGS

logger = logging.getLogger(__name__)
METRICS = list(DIMS) + ["esc_balance"]
STANCE_METRICS = ["hostility", "threat", "conciliation", "grievance", "esc_balance"]
PERIODS = {"week": (SETTINGS.week_baseline, SETTINGS.week_min_base, SETTINGS.week_min_n),
           "month": (SETTINGS.month_baseline, SETTINGS.month_min_base, SETTINGS.month_min_n)}


def doc_frame(con: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        f"SELECT d.doc_id, d.crow, d.country, d.source, d.outlet, d.lang, d.sample, d.date, d.url, d.title,"
        f" {', '.join('t.' + x for x in DIMS)}, t.nsent FROM docs d JOIN doc_tone t USING(doc_id)"
        " WHERE d.present=1 AND d.date IS NOT NULL AND t.nsent > 0", con)
    df = df[~df["sample"].isin(EXCLUDED_SAMPLES)].copy()
    df["esc_balance"] = df["escalation"] - df["deescalation"]
    df["stream"] = df["source"] + "|" + df["lang"].fillna("?") + "|" + df["sample"].fillna("all")
    df["week"], df["month"] = stats.week_of(df["date"]), stats.month_of(df["date"])
    return df[df["week"].notna()]


def stance_frame(con: sqlite3.Connection, docs: pd.DataFrame) -> pd.DataFrame:
    """Doc x target rows: mean tone of the doc's sentences that mention the target (self-mentions excluded)."""
    q = (f"SELECT m.doc_id, m.target, {', '.join('avg(s.' + x + ')/1000.0 AS ' + x for x in DIMS)}, count(*) AS nsent"
         " FROM (SELECT DISTINCT doc_id, idx, target FROM mentions WHERE self=0) m"
         " JOIN sent_scores s ON s.doc_id=m.doc_id AND s.idx=m.idx GROUP BY m.doc_id, m.target")
    st = pd.read_sql_query(q, con)
    st["esc_balance"] = st["escalation"] - st["deescalation"]
    keep = docs[["doc_id", "stream", "country", "week", "month"]]
    return st.merge(keep, on="doc_id")


def _weights(docs: pd.DataFrame) -> Dict[str, float]:
    return {s: float(np.sqrt(n)) for s, n in docs.groupby("stream").size().items()}


def _series(frame: pd.DataFrame, period: str, metrics: List[str], key: str | None = None) -> pd.DataFrame:
    window, min_base, min_n = PERIODS[period]
    if key:
        parts = []
        for val, g in frame.groupby(key):
            s = stats.stream_series(g, period, metrics)
            s[key] = val
            parts.append(s)
        s = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        return stats.add_z(s, period, window, min_base, min_n, keys=("stream", "metric", key)) if len(s) else s
    s = stats.stream_series(frame, period, metrics)
    return stats.add_z(s, period, window, min_base, min_n)


def _share_series(flags: pd.DataFrame, docs: pd.DataFrame, period: str, key: str) -> pd.DataFrame:
    """Share of a stream's docs per period that carry `key` (target mentioned / topic assigned)."""
    window, min_base, min_n = PERIODS[period]
    tot = docs.groupby(["stream", period]).size().rename("n").reset_index()
    hits = flags.groupby(["stream", period, key]).size().rename("k").reset_index()
    parts = []
    for val, h in hits.groupby(key):
        s = tot.merge(h.drop(columns=[key]), on=["stream", period], how="left").fillna({"k": 0})
        p = s["k"] / s["n"]
        pc = (s["k"] + 0.5) / (s["n"] + 1)  # continuity-corrected share for the standard error
        s = pd.DataFrame({"stream": s["stream"], "period": s[period], "n": s["n"], "mean": p,
                          "sd": np.sqrt(p * (1 - p)), "se": np.sqrt(pc * (1 - pc) / s["n"].clip(lower=1)),
                          "k": s["k"], "metric": "share"})
        s[key] = val
        parts.append(s)
    if not parts:
        return pd.DataFrame()
    s = pd.concat(parts, ignore_index=True)
    return stats.add_z(s, period, window, min_base, min_n, keys=("stream", "metric", key), binomial=True)


def _columnar(df: pd.DataFrame, keys: List[str], values: List[str], drop_empty_k: bool = False) -> List[Dict]:
    """One object per series: key fields + aligned value lists (periods with n > 0 only; NaN -> null; 4 decimals).
    drop_empty_k: skip share series whose count k is never > 0 (target never mentioned / topic never used)."""
    out = []
    for key, g in df.groupby(keys, sort=True):
        if drop_empty_k and not (g["k"] > 0).any():
            continue
        g = g.sort_values("period")
        rec = dict(zip(keys, key if isinstance(key, tuple) else (key,)))
        rec = {k: (int(v) if isinstance(v, (np.integer, float)) and k == "topic" else v) for k, v in rec.items()}
        rec["period"] = g["period"].tolist()
        for v in values:
            col = g[v].astype(float).round(4)
            rec[v] = [None if np.isnan(x) else (int(x) if v in ("n", "k", "n_streams") else x) for x in col]
        out.append(rec)
    return out


def run_trends(con: sqlite3.Connection) -> Dict:
    docs = doc_frame(con)
    if docs.empty:
        return {"docs": 0}
    weights = _weights(docs)
    s_country = docs.drop_duplicates("stream").set_index("stream")["country"].to_dict()
    streams = [{"stream": s, "country": s_country[s], "source": s.split("|")[0], "lang": s.split("|")[1],
                "sample": s.split("|")[2], "n_docs": int(n), "weight": round(weights[s], 3),
                "first": g["date"].min(), "last": g["date"].max()}
               for s, n, g in ((s, len(g), g) for s, g in docs.groupby("stream"))]
    stance = stance_frame(con, docs)
    topics = pd.read_sql_query("SELECT doc_id, topic FROM doc_topic", con).merge(
        docs[["doc_id", "stream", "country", "week", "month"]], on="doc_id")
    mention_docs = stance[["doc_id", "stream", "country", "week", "month", "target"]]
    AGG_DIR.mkdir(parents=True, exist_ok=True)
    all_alerts: List[pd.DataFrame] = []
    n_tests, expected = 0, 0.0
    tests_by_z: List[float] = []
    out_series: Dict[str, Dict] = {"tone": {}, "country": {}, "stance": {}, "salience": {}, "topic_share": {}}
    for period in ("week", "month"):
        _, _, min_n = PERIODS[period]
        tone = _series(docs, period, METRICS)
        ctry = stats.combine(tone, s_country, weights, min_n)
        st = _series(stance, period, STANCE_METRICS, key="target") if len(stance) else pd.DataFrame()
        sal = _share_series(mention_docs.drop_duplicates(["doc_id", "target"]), docs, period, "target")
        tsh = _share_series(topics, docs, period, "topic") if len(topics) else pd.DataFrame()
        vals = ["n", "mean", "se", "base", "delta", "z"]
        out_series["tone"][period] = _columnar(tone[tone["n"] > 0], ["stream", "metric"], vals)
        out_series["country"][period] = _columnar(ctry, ["country", "metric"], ["value", "adj_delta", "z", "n", "n_streams"])
        if len(st):
            out_series["stance"][period] = _columnar(st[st["n"] > 0], ["stream", "target", "metric"], vals)
        out_series["salience"][period] = _columnar(sal[sal["n"] > 0], ["stream", "target"], vals + ["k"], drop_empty_k=True)
        if len(tsh):
            out_series["topic_share"][period] = _columnar(tsh[tsh["n"] > 0], ["stream", "topic"], vals + ["k"], drop_empty_k=True)
        for kind, frame, keys, zmin in (("tone", tone, ["stream", "metric"], SETTINGS.z_alert),
                                        ("stance", st, ["stream", "metric", "target"], SETTINGS.z_alert + 0.5),
                                        ("salience", sal, ["stream", "metric", "target"], SETTINGS.z_alert + 0.5),
                                        ("topic_share", tsh, ["stream", "metric", "topic"], SETTINGS.z_alert + 0.5)):
            if frame is None or len(frame) == 0:
                continue
            cnt = int(frame["z"].notna().sum())
            tests_by_z += frame["z"].dropna().abs().tolist()
            n_tests += cnt
            expected += cnt * 2 * (1 - _phi(zmin))
            f = stats.flag(frame, z_min=zmin, min_effect=SETTINGS.min_effect)
            m = stats.merge_runs(f, keys, period)
            if len(m):
                all_alerts.append(m.assign(kind=kind, level="stream", period_type=period,
                                           country=m["stream"].map(s_country)))
        cf = stats.flag(ctry.rename(columns={"adj_delta": "delta"}), z_min=SETTINGS.z_alert, min_effect=SETTINGS.min_effect)
        n_tests += int(ctry["z"].notna().sum())
        tests_by_z += ctry["z"].dropna().abs().tolist()
        expected += int(ctry["z"].notna().sum()) * 2 * (1 - _phi(SETTINGS.z_alert))
        m = stats.merge_runs(cf, ["country", "metric"], period)
        if len(m):
            all_alerts.append(m.assign(kind="tone", level="country", period_type=period, stream=None))
    for name, data in out_series.items():
        (AGG_DIR / f"series_{name}.json").write_text(json.dumps(
            {"generated": _now(), "streams": streams if name != "country" else None, "series": data},
            ensure_ascii=False), encoding="utf-8")
    alerts = pd.concat(all_alerts, ignore_index=True) if all_alerts else pd.DataFrame()
    recs = evidence.attach(con, alerts, docs) if len(alerts) else []
    meta = {"generated": _now(), "n_tests": n_tests,
            "expected_false_alerts_approx": round(expected, 1),
            "expected_note": "periods flagged by chance if every z were standard normal; before the min-effect filter"
                             " and before merging consecutive periods (an upper-bound style reference, not an FDR)",
            "z_threshold": {"tone": SETTINGS.z_alert, "stance/salience/topic_share": SETTINGS.z_alert + 0.5},
            "min_effect": SETTINGS.min_effect, "data_last_date": docs["date"].max(),
            "tail_check": {f"|z|>={t}": {"observed_periods": int((np.array(tests_by_z) >= t).sum()),
                                         "expected_if_normal": round(n_tests * 2 * (1 - _phi(t)), 1)}
                           for t in (3.0, 3.5, 4.0, 4.5)},
            "tiers": "strong |z|>=4.5; moderate |z|>=3.5 or >=2 consecutive periods; weak otherwise",
            "baseline": {p: {"window": v[0], "min_base": v[1], "min_n": v[2]} for p, v in PERIODS.items()},
            "excluded_samples": list(EXCLUDED_SAMPLES)}
    (AGG_DIR / "alerts.json").write_text(json.dumps({"meta": meta, "alerts": recs}, ensure_ascii=False, default=str),
                                         encoding="utf-8")
    return {"docs": int(len(docs)), "streams": len(streams), "alerts": len(recs), "tests": n_tests,
            "expected_false_alerts_approx": meta["expected_false_alerts_approx"]}


def _phi(z: float) -> float:
    from math import erf, sqrt

    return 0.5 * (1 + erf(z / sqrt(2)))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
