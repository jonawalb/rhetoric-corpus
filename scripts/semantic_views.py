"""Analyst views over the semantic-layer aggregates (index/semantic/aggregates/*.json).

One contract for two front ends: the private local UI (scripts/serve_search.py, /semantic) and the public Trends page
(tsm-strait-layers tools/rhetoric-search/scripts/build_trends.py writes these views to data/trends/*.json.gz).

  view(agg, "overview" | "alerts" | "heatmap" | "topics" | "echoes" | "tone_<CC>", public=bool) -> JSON-able dict

`public=True` applies the publication text policy of scripts/semantic/SCHEMA.md: a record whose `outlet` is not
"official" loses its text (headline + link only), official text is cut to 300 characters, weak alerts are dropped.
Only aggregates are read, plus corpus.sqlite (read-only) for each source's outlet type.
"""
from __future__ import annotations

import json
import logging
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from scripts.semantic import stats
from scripts.semantic.config import AGG_DIR, SETTINGS
from semantic_copy import CAVEATS, COUNTRY_NAMES, KNOWN_NOTES, METHOD

logger = logging.getLogger(__name__)

TONE_METRICS = ["hostility", "threat", "conciliation", "grievance", "escalation", "deescalation", "esc_balance"]
STANCE_METRICS = ["hostility", "threat", "conciliation", "grievance", "esc_balance"]
TEXT_KEYS = ("text", "passage")
MAX_PUBLIC_TEXT = 300
HEAT_MIN_N = 5             # stream-months with fewer docs (stance: docs mentioning the target) are left out of a cell
PUBLIC_TIERS = ("strong", "moderate")
VIEW_NAME = re.compile(r"^(overview|alerts|heatmap|topics|echoes|tone_[A-Z]{2})$")


class Aggregates:
    """Cached reader for the aggregate JSON files (reloads a file when its mtime changes)."""

    def __init__(self, path: Path = AGG_DIR, outlets: Optional[Dict[str, str]] = None) -> None:
        self.path = Path(path)
        self._cache: Dict[str, tuple] = {}
        self._outlets = outlets

    def load(self, name: str, default=None):
        p = self.path / f"{name}.json"
        if not p.exists():
            if default is not None:
                return default
            raise FileNotFoundError(p)
        mt = p.stat().st_mtime
        hit = self._cache.get(name)
        if not hit or hit[0] != mt:
            self._cache[name] = (mt, json.loads(p.read_text(encoding="utf-8")))
        return self._cache[name][1]

    def stamp(self) -> float:
        """Latest mtime over the aggregates (cache key for derived views)."""
        return max((p.stat().st_mtime for p in self.path.glob("*.json")), default=0.0)

    def outlets(self) -> Dict[str, str]:
        """source -> outlet ("official", "state_media", "media"), from corpus.sqlite (majority per source)."""
        if self._outlets is None:
            from scripts.semantic.store import corpus

            cc = corpus()
            best: Dict[str, tuple] = {}
            for src, outlet, n in cc.execute("SELECT source, outlet, count(*) FROM docs GROUP BY 1, 2"):
                if src not in best or n > best[src][1]:
                    best[src] = (outlet, n)
            cc.close()
            self._outlets = {s: o for s, (o, _) in best.items()}
        return self._outlets


# ------------------------------------------------------------------------------------------------ text policy
def public_record(r: Dict) -> Dict:
    """Publication copy of an evidence/echo record: official text <= 300 chars; other outlets headline + link only."""
    out = dict(r)
    official = r.get("outlet") == "official"
    for k in TEXT_KEYS:
        if k in out:
            out[k] = (out[k] or "")[:MAX_PUBLIC_TEXT] if official else None
    if not official:
        out["text_withheld"] = True
    if out.get("title"):
        out["title"] = out["title"][:200]
    return out


def _stream_rows(agg: Aggregates) -> List[Dict]:
    outlets = agg.outlets()
    rows = []
    for s in agg.load("series_tone")["streams"]:
        o = outlets.get(s["source"], "unknown")
        rows.append({**s, "outlet": o, "official": o == "official"})
    return rows


def _r(x, nd: int = 4):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), nd)


# ------------------------------------------------------------------------------------------------ overview
def coverage(agg: Aggregates) -> Dict:
    """Monthly docs per stream and country (as used in the series), and flags for coverage-driven jumps."""
    rows = {s["stream"]: s for s in _stream_rows(agg)}
    per: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    months = set()
    for obj in agg.load("series_tone")["series"]["month"]:
        if obj["metric"] != "hostility":
            continue
        cc = rows.get(obj["stream"], {}).get("country", "?")
        for p, n in zip(obj["period"], obj["n"]):
            per[cc][obj["stream"]][p] = n
            months.add(p)
    months = sorted(months)
    last = agg.load("alerts")["meta"].get("data_last_date", months[-1] if months else "")[:7]
    out, flags = {}, []
    for cc in sorted(per):
        streams = per[cc]
        total = [sum(st.get(m, 0) for st in streams.values()) for m in months]
        out[cc] = {"total": total, "streams": {s: [st.get(m, 0) for m in months] for s, st in sorted(streams.items())}}
        flags += _flags(cc, months, total, streams, last)
    return {"months": months, "by_country": out, "flags": flags, "partial_month": last,
            "note": ("Documents per month that enter the trend series (keyword-filtered regimes excluded). "
                     f"{last} is still partial. Flags: a stream that starts or stops while carrying at least a quarter "
                     "of a country's documents, or a month with 3x / one third of the prior 3-month median volume.")}


def _flags(cc: str, months: List[str], total: List[int], streams: Dict[str, Dict[str, int]], last: str) -> List[Dict]:
    """Stream entries/exits that carry >= 25 % of a country-month, and month volumes >= 3x or <= 1/3 the prior median."""
    out = []
    idx = {m: i for i, m in enumerate(months)}
    first_c = next((m for m, t in zip(months, total) if t), None)
    for s, st in streams.items():
        ps = sorted(p for p, n in st.items() if n)
        if not ps:
            continue
        a, b = ps[0], ps[-1]
        if a != first_c and total[idx[a]] and st[a] / total[idx[a]] >= 0.25:
            out.append({"country": cc, "period": a, "kind": "stream_start", "stream": s,
                        "detail": f"{s} starts: {st[a]:,} of {total[idx[a]]:,} documents this month"})
        nxt = months[idx[b] + 1] if idx[b] + 1 < len(months) else None
        if nxt and nxt < last and total[idx[b]] and st[b] / total[idx[b]] >= 0.25:
            out.append({"country": cc, "period": nxt, "kind": "stream_end", "stream": s,
                        "detail": f"{s} ends after {b} ({st[b]:,} of {total[idx[b]]:,} documents that month)"})
    for i in range(3, len(months)):
        if months[i] >= last:  # the current month is still partial
            continue
        prior = sorted(total[i - 3:i])[1]
        cur = total[i]
        if max(prior, cur) >= 30 and min(prior, cur) > 0 and (cur >= 3 * prior or cur * 3 <= prior):
            out.append({"country": cc, "period": months[i], "kind": "volume_jump", "stream": None,
                        "detail": f"volume {cur:,} documents vs {prior:,} (median of the prior 3 months)"})
    return sorted(out, key=lambda f: (f["period"], f["kind"]))


def validation_table(agg: Aggregates) -> Dict:
    v = agg.load("validation", default={})
    keep = ("n", "teacher_pos", "student_pos", "teacher_mean", "student_mean", "accuracy", "kappa", "f1", "auc", "pearson")
    dims = {}
    for d, blk in (v.get("dims") or {}).items():
        dims[d] = {"overall": {k: blk.get("overall", {}).get(k) for k in keep},
                   "random_arm": {k: blk.get("random_arm", {}).get(k) for k in ("n", "kappa", "pearson", "auc")},
                   "by_lang": {lang: {k: x.get(k) for k in ("n", "kappa", "pearson", "auc")}
                               for lang, x in (blk.get("by_lang") or {}).items()}}
    return {"model_version": v.get("model_version"), "n_train": v.get("n_train"), "n_heldout": v.get("n_heldout"),
            "note": v.get("note"), "dims": dims, "human": agg.load("validation_human", default={}) or None}


def overview(agg: Aggregates, public: bool) -> Dict:
    man = agg.load("manifest")
    meta = agg.load("alerts")["meta"]
    tm = man.get("versions", {}).get("tone_model")
    try:
        tm = json.loads(tm) if isinstance(tm, str) else tm
    except json.JSONDecodeError:
        pass
    return {"generated": man.get("generated"), "data_last_date": meta.get("data_last_date"), "public": public,
            "totals": man.get("totals"), "versions": {**man.get("versions", {}), "tone_model": tm},
            "countries": man.get("coverage_by_country"), "country_names": COUNTRY_NAMES,
            "streams": _stream_rows(agg), "coverage": coverage(agg), "known_notes": KNOWN_NOTES,
            "validation": validation_table(agg), "method": METHOD, "caveats": CAVEATS,
            "alerts_meta": {k: meta.get(k) for k in ("n_tests", "expected_false_alerts_approx", "expected_note",
                                                     "z_threshold", "min_effect", "tail_check", "tiers", "baseline")},
            "metrics": TONE_METRICS, "stance_metrics": STANCE_METRICS,
            "tiers_shown": list(PUBLIC_TIERS) if public else ["strong", "moderate", "weak"]}


# ------------------------------------------------------------------------------------------------ tone
def tone(agg: Aggregates, country: str, public: bool) -> Dict:
    """Combined country series (value with error band, adj_delta, z, n) and per-stream series for one country."""
    rows = {s["stream"]: s for s in _stream_rows(agg)}
    mine = {s: r for s, r in rows.items() if r["country"] == country and (r["official"] or not public)}
    st = agg.load("series_tone")["series"]
    streams: Dict[str, Dict] = defaultdict(lambda: {"week": {}, "month": {}})
    for pt in ("week", "month"):
        for obj in st[pt]:
            if obj["stream"] in mine:
                streams[obj["stream"]][pt][obj["metric"]] = {k: obj[k] for k in
                                                             ("period", "n", "mean", "se", "base", "delta", "z")}
    combined: Dict[str, Dict] = {"week": {}, "month": {}}
    for pt in ("week", "month"):
        min_n = SETTINGS.week_min_n if pt == "week" else SETTINGS.month_min_n
        se_idx = _stream_se(st[pt], rows, country, min_n)
        dse_idx = _delta_se(st[pt], rows, country, min_n)
        for obj in agg.load("series_country")["series"][pt]:
            if obj["country"] != country:
                continue
            m = obj["metric"]
            se = [_r(se_idx.get((m, p))) for p in obj["period"]]
            dse = [_r(dse_idx.get((m, p))) for p in obj["period"]]
            combined[pt][m] = {**{k: obj[k] for k in ("period", "value", "adj_delta", "z", "n", "n_streams")},
                               "se": se, "delta_se": dse}
    return {"country": country, "combined": combined, "streams": dict(streams),
            "stream_info": [mine[s] for s in sorted(mine)],
            "streams_note": "official streams only" if public else "all streams in the series"}


def _stream_se(objs: List[Dict], rows: Dict[str, Dict], country: str, min_n: int) -> Dict[tuple, float]:
    """(metric, period) -> standard error of the fixed-weight country value (streams with n >= min_n)."""
    acc: Dict[tuple, List[float]] = defaultdict(lambda: [0.0, 0.0])
    for obj in objs:
        r = rows.get(obj["stream"])
        if not r or r["country"] != country:
            continue
        w = r["weight"]
        for p, n, se in zip(obj["period"], obj["n"], obj["se"]):
            if n >= min_n and se is not None:
                a = acc[(obj["metric"], p)]
                a[0] += w
                a[1] += (w * se) ** 2
    return {k: math.sqrt(v[1]) / v[0] for k, v in acc.items() if v[0] > 0}


def _delta_se(objs: List[Dict], rows: Dict[str, Dict], country: str, min_n: int) -> Dict[tuple, float]:
    """(metric, period) -> standard error of adj_delta. Each stream's z denominator is sqrt(sd_base^2 + se^2) = delta/z;
    it is read where |z| >= 0.5 (rounding makes it noisy near 0) and otherwise taken as the stream's median."""
    acc: Dict[tuple, List[float]] = defaultdict(lambda: [0.0, 0.0])
    for obj in objs:
        r = rows.get(obj["stream"])
        if not r or r["country"] != country:
            continue
        den = [abs(d / z) for d, z in zip(obj["delta"], obj["z"]) if d is not None and z is not None and abs(z) >= 0.5]
        if not den:
            continue
        med = sorted(den)[len(den) // 2]
        w = r["weight"]
        for p, n, d, z in zip(obj["period"], obj["n"], obj["delta"], obj["z"]):
            if n >= min_n and d is not None and z is not None:
                a = acc[(obj["metric"], p)]
                a[0] += w
                a[1] += (w * (abs(d / z) if abs(z) >= 0.5 else med)) ** 2
    return {k: math.sqrt(v[1]) / v[0] for k, v in acc.items() if v[0] > 0}


# ------------------------------------------------------------------------------------------------ heatmap
def heatmap(agg: Aggregates, public: bool) -> Dict:
    """Country x target x month cells for stance metrics and salience, all streams and official streams only."""
    rows = {s["stream"]: s for s in _stream_rows(agg)}
    sc = {s: r["country"] for s, r in rows.items()}
    w = {s: r["weight"] for s, r in rows.items()}
    frames = {"stance": _frame(agg.load("series_stance")["series"]["month"], "metric"),
              "salience": _frame(agg.load("series_salience")["series"]["month"], None)}
    months = sorted(set(frames["stance"]["period"]) | set(frames["salience"]["period"]))
    mi = {m: i for i, m in enumerate(months)}
    cells: Dict[str, Dict] = {}
    for scope in ("all", "official"):
        for kind, df in frames.items():
            if scope == "official":
                df = df[df["stream"].map(lambda s: rows.get(s, {}).get("official", False))]
            df = df[df["stream"].isin(sc)]
            comb = stats.combine(df, sc, w, HEAT_MIN_N, keys=("target", "metric"))
            for (m, cc, tg), g in comb.groupby(["metric", "country", "target"]):
                key = f"{kind}:{m}:{scope}"
                cells.setdefault(key, {}).setdefault(cc, {})[tg] = [
                    [mi[r.period], _r(r.value), int(r.n), _r(r.z, 2), _r(r.adj_delta), int(r.n_streams)]
                    for r in g.sort_values("period").itertuples()]
    targets = sorted({t for c in cells.values() for cc in c.values() for t in cc})
    countries = sorted({cc for c in cells.values() for cc in c})
    return {"months": months, "countries": countries, "targets": targets, "cells": cells, "min_n": HEAT_MIN_N,
            "fields": ["month_index", "value", "n_docs", "z", "adj_delta", "n_streams"],
            "metrics": {"stance": STANCE_METRICS, "salience": ["share"]},
            "note": ("Stance = tone of the sentences that mention the target (not tone directed at it); salience = share "
                     "of documents that mention the target, self-mentions excluded. Cells combine a country's streams "
                     f"with fixed weights; stream-months with fewer than {HEAT_MIN_N} documents are left out; "
                     "z and change are against each stream's own 6-month baseline.")}


def _frame(objs: List[Dict], metric_key: Optional[str]) -> pd.DataFrame:
    recs = []
    for o in objs:
        m = o[metric_key] if metric_key else "share"
        for i, p in enumerate(o["period"]):
            recs.append((o["stream"], o["target"], m, p, o["n"][i], o["mean"][i], o["delta"][i], o["z"][i]))
    df = pd.DataFrame(recs, columns=["stream", "target", "metric", "period", "n", "mean", "delta", "z"])
    return df.astype({"delta": float, "z": float})


# ------------------------------------------------------------------------------------------------ topics
def topics(agg: Aggregates, public: bool) -> Dict:
    t = agg.load("topics")
    prev: Dict[str, Dict] = {}
    by_c: Dict[str, List[Dict]] = defaultdict(list)
    for r in t.get("prevalence", []):
        by_c[r["country"]].append(r)
    for cc, rs in by_c.items():
        months = sorted({r["month"] for r in rs})
        mi = {m: i for i, m in enumerate(months)}
        docs = [0] * len(months)
        bal: Dict[int, List[float]] = defaultdict(lambda: [0.0] * len(months))
        cnt: Dict[int, List[int]] = defaultdict(lambda: [0] * len(months))
        for r in rs:
            i = mi[r["month"]]
            docs[i] = r["country_docs"]
            bal[r["topic"]][i] = _r(r["balanced_share"])
            cnt[r["topic"]][i] = r["n_docs"]
        prev[cc] = {"months": months, "country_docs": docs, "balanced_share": {str(k): v for k, v in bal.items()},
                    "n_docs": {str(k): v for k, v in cnt.items()}}
    tl = []
    for x in t.get("topics", []):
        ex = [{k: e.get(k) for k in ("doc_id", "country", "source", "lang", "date", "title", "url")} for e in x.get("exemplars", [])]
        tl.append({**{k: x.get(k) for k in ("topic", "label", "n_docs", "terms", "langs", "countries")}, "exemplars": ex})
    return {**{k: t.get(k) for k in ("version", "k", "fit_sample", "docs_assigned", "multilingual_topics", "silhouette_by_k")},
            "topics": tl, "prevalence": prev,
            "note": ("Topics: k-means clusters of multilingual document embeddings, labelled by their top terms in each "
                     "language. balanced_share = fixed-weight mean of within-stream shares, so a change in source mix "
                     "does not move it. Topic ids change when the model is refitted.")}


# ------------------------------------------------------------------------------------------------ alerts, echoes
def alerts(agg: Aggregates, public: bool) -> Dict:
    a = agg.load("alerts")
    labels = {x["topic"]: x["label"] for x in agg.load("topics").get("topics", [])}
    outlets = agg.outlets()
    out = []
    for r in a["alerts"]:
        if public and r.get("tier") not in PUBLIC_TIERS:
            continue
        rec = {k: v for k, v in r.items() if k != "evidence"}
        if r.get("topic") is not None:
            rec["topic_label"] = labels.get(r["topic"])
        if r.get("stream"):
            rec["stream_outlet"] = outlets.get(r["stream"].split("|")[0])
        ev = r.get("evidence")
        if ev is not None:
            rec["evidence"] = [public_record(e) for e in ev] if public else ev
        out.append(rec)
    meta = {k: v for k, v in a["meta"].items()}
    meta["tiers_shown"] = list(PUBLIC_TIERS) if public else ["strong", "moderate", "weak"]
    return {"meta": meta, "alerts": out}


def echoes(agg: Aggregates, public: bool) -> Dict:
    e = agg.load("echoes")
    cl = []
    for c in e["clusters"]:
        cl.append({**c, "members": [public_record(m) for m in c["members"]] if public else c["members"]})
    return {"meta": e["meta"], "clusters": cl,
            "note": ("Near-identical passages from different countries within ±14 days. 'First seen' = first in this "
                     "corpus, not origin. Many clusters are the same event carried by several outlets, quotation, or "
                     "shared wire copy; official-official and mixed clusters are the likelier repeated lines.")}


def view(agg: Aggregates, name: str, public: bool) -> Dict:
    if not VIEW_NAME.match(name):
        raise ValueError(f"unknown view {name!r}")
    if name.startswith("tone_"):
        return tone(agg, name[5:], public)
    return {"overview": overview, "alerts": alerts, "heatmap": heatmap, "topics": topics, "echoes": echoes}[name](agg, public)


def view_names(agg: Aggregates) -> List[str]:
    countries = sorted(agg.load("manifest").get("coverage_by_country", {}))
    return ["overview", "alerts", "heatmap", "topics", "echoes"] + [f"tone_{c}" for c in countries]
