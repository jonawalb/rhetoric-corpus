"""Deterministic statistics for trends and alerts (pure functions; unit-tested in tests/test_semantic.py).

Design rules
- Series are computed WITHIN a stream (source x language x selection regime); a stream is compared only with
  its own past, so changes in which sources publish (composition) cannot appear as tone shifts.
- Country values combine streams with FIXED weights w_s = sqrt(total docs of stream), renormalised over the
  streams that have enough volume in that period. Country shifts are the weighted mean of within-stream
  deviations from each stream's own rolling baseline, and a weighted Stouffer combination of stream z-scores.
- Rolling baseline: the previous `window` calendar periods that have at least `min_n` docs; at least
  `min_base` of them are required. z = (m_t - mean_base) / sqrt(sd_base^2 + se_t^2), denominator floored.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


def week_of(dates: pd.Series) -> pd.Series:
    """ISO week label = date of that week's Monday (YYYY-MM-DD)."""
    d = pd.to_datetime(dates, errors="coerce")
    return (d - pd.to_timedelta(d.dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")


def month_of(dates: pd.Series) -> pd.Series:
    return dates.str[:7]


def calendar(first: str, last: str, period: str) -> List[str]:
    if period == "week":
        return [d.strftime("%Y-%m-%d") for d in pd.date_range(first, last, freq="W-MON")]
    return [d.strftime("%Y-%m") for d in pd.period_range(first, last, freq="M").to_timestamp()]


def rolling_z(means: np.ndarray, ns: np.ndarray, ses: np.ndarray, window: int, min_base: int, min_n: int,
              floor: float = 0.01, binomial: bool = False) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per period: (z, baseline mean, delta) against the previous `window` periods with n >= min_n.
    Values are NaN where the period itself has n < min_n or the baseline has < min_base valid periods.
    binomial=True (share series): the period's sampling error is the larger of the null error sqrt(p0 (1 - p0) / n)
    (p0 = baseline mean) and the supplied `ses` (continuity-corrected period error), so neither a drop to a share of
    0 nor a burst from a near-zero baseline gets an artificially small standard error."""
    T = len(means)
    z, mu, delta = (np.full(T, np.nan) for _ in range(3))
    valid = (ns >= min_n) & ~np.isnan(means)
    for t in range(T):
        if not valid[t]:
            continue
        lo = max(0, t - window)
        base = means[lo:t][valid[lo:t]]
        if len(base) < min_base:
            continue
        m = base.mean()
        sd = base.std(ddof=1) if len(base) > 1 else 0.0
        if binomial:
            se = max(float(np.sqrt(m * (1 - m) / ns[t])), 0.0 if np.isnan(ses[t]) else float(ses[t]))
        else:
            se = ses[t] if not np.isnan(ses[t]) else 0.0
        denom = max(float(np.sqrt(sd ** 2 + se ** 2)), floor)
        mu[t], delta[t], z[t] = m, means[t] - m, (means[t] - m) / denom
    return z, mu, delta


def stream_series(df: pd.DataFrame, period_col: str, metrics: List[str]) -> pd.DataFrame:
    """Long frame: stream, period, metric, n, mean, sd, se (one row per stream x period x metric)."""
    g = df.groupby(["stream", period_col])
    rows = []
    for mt in metrics:
        a = g[mt].agg(["count", "mean", "std"]).reset_index()
        a.columns = ["stream", "period", "n", "mean", "sd"]
        a["metric"] = mt
        rows.append(a)
    out = pd.concat(rows, ignore_index=True)
    out["sd"] = out["sd"].fillna(0.0)
    out["se"] = out["sd"] / np.sqrt(out["n"].clip(lower=1))
    return out


def add_z(series: pd.DataFrame, period: str, window: int, min_base: int, min_n: int,
          keys: Tuple[str, ...] = ("stream", "metric"), binomial: bool = False) -> pd.DataFrame:
    """Reindex each series to its full calendar and add z, base, delta columns."""
    out = []
    for key, g in series.groupby(list(keys), sort=False):
        g = g.set_index("period").sort_index()
        cal = calendar(g.index[0], g.index[-1], period)
        g = g.reindex(cal)
        g["n"] = g["n"].fillna(0)
        for i, k in enumerate(keys):
            g[k] = key[i] if isinstance(key, tuple) else key
        z, mu, d = rolling_z(g["mean"].to_numpy(float), g["n"].to_numpy(float), g["se"].to_numpy(float),
                             window, min_base, min_n, binomial=binomial)
        g["z"], g["base"], g["delta"] = z, mu, d
        out.append(g.reset_index().rename(columns={"index": "period"}))
    return pd.concat(out, ignore_index=True) if out else series.assign(z=np.nan, base=np.nan, delta=np.nan)


def combine(series: pd.DataFrame, stream_country: Dict[str, str], stream_weight: Dict[str, float], min_n: int,
            keys: Tuple[str, ...] = ("metric",)) -> pd.DataFrame:
    """Country x period x keys: fixed-weight value, weighted mean deviation, Stouffer z, streams used, docs."""
    s = series[series["n"] >= min_n].copy()
    if s.empty:
        return pd.DataFrame(columns=["country", "period", *keys, "value", "adj_delta", "z", "n", "n_streams"])
    s["country"] = s["stream"].map(stream_country)
    s["w"] = s["stream"].map(stream_weight)
    s["wm"] = s["w"] * s["mean"]
    has = s["delta"].notna() if "delta" in s else pd.Series(False, index=s.index)
    s["wd"] = np.where(has, s["w"] * s.get("delta", 0), 0.0)
    s["wd_w"] = np.where(has, s["w"], 0.0)
    s["wz"] = np.where(has, s["w"] * s.get("z", 0), 0.0)
    s["w2"] = np.where(has, s["w"] ** 2, 0.0)
    g = s.groupby(["country", "period", *keys])
    out = pd.DataFrame({"value": g["wm"].sum() / g["w"].sum(), "n": g["n"].sum(), "n_streams": g["stream"].nunique(),
                        "wd": g["wd"].sum(), "wd_w": g["wd_w"].sum(), "wz": g["wz"].sum(), "w2": g["w2"].sum()})
    out["adj_delta"] = np.where(out["wd_w"] > 0, out["wd"] / out["wd_w"].replace(0, np.nan), np.nan)
    out["z"] = np.where(out["w2"] > 0, out["wz"] / np.sqrt(out["w2"].replace(0, np.nan)), np.nan)
    return out.drop(columns=["wd", "wd_w", "wz", "w2"]).reset_index()


def flag(frame: pd.DataFrame, z_col: str = "z", delta_col: str = "delta", z_min: float = 3.0,
         min_effect: float = 0.03) -> pd.DataFrame:
    """Rows passing |z| >= z_min and |delta| >= min_effect."""
    m = (frame[z_col].abs() >= z_min) & (frame[delta_col].abs() >= min_effect)
    return frame[m.fillna(False)]


def tier(z: float, n_periods: int = 1) -> str:
    """'strong' |z| >= 4.5 (about 7 in a million under N(0,1)); 'moderate' |z| >= 3.5 or a run of >= 2 flagged periods;
    else 'weak'. Observed z tails are close to normal at 3 (see alerts.json meta), so weak alerts are often chance."""
    a = abs(z)
    if a >= 4.5:
        return "strong"
    if a >= 3.5 or n_periods >= 2:
        return "moderate"
    return "weak"


def merge_runs(alerts: pd.DataFrame, keys: List[str], period: str) -> pd.DataFrame:
    """Collapse consecutive flagged periods of the same series and direction into one alert (peak |z| kept)."""
    if alerts.empty:
        return alerts.assign(start=pd.Series(dtype=str), end=pd.Series(dtype=str), n_periods=pd.Series(dtype=int))
    a = alerts.copy()
    a["dir"] = np.sign(a["z"])
    step = pd.Timedelta(days=7) if period == "week" else None
    rows = []
    for _, g in a.sort_values("period").groupby(keys + ["dir"], sort=False):
        run: List = []
        prev = None
        for r in g.itertuples(index=False):
            cur = pd.Timestamp(r.period) if period == "week" else pd.Period(r.period, "M")
            consecutive = prev is not None and ((cur - prev) == step if period == "week" else (cur - prev).n == 1)
            if run and not consecutive:
                rows.append(_peak(run))
                run = []
            run.append(r)
            prev = cur
        if run:
            rows.append(_peak(run))
    return pd.DataFrame(rows)


def _peak(run: List) -> Dict:
    best = max(run, key=lambda r: abs(r.z))
    d = best._asdict()
    d.update(start=run[0].period, end=run[-1].period, n_periods=len(run))
    return d
