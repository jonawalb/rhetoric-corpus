"""Deterministic parts of the semantic layer: gazetteer matching, passage chunking, within-stream aggregation,
rolling-baseline z-scores and alert flagging. No models are loaded."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.semantic import stats  # noqa: E402
from scripts.semantic.embed import build_passages  # noqa: E402
from scripts.semantic.targets import Gazetteer, default  # noqa: E402
from scripts.semantic.teacher import quotas, split_of  # noqa: E402


# ------------------------------------------------------------------------------------------- gazetteer
@pytest.fixture(scope="module")
def gaz() -> Gazetteer:
    return default()


@pytest.mark.parametrize("text,lang,expected", [
    ("The US and NATO warned Russia.", "en", {"US", "NATO", "RUSSIA"}),
    ("Let us talk about it.", "en", set()),                                   # lower-case "us"
    ("Latin America and the Indian Ocean", "en", set()),                      # vetoes
    ("Shipping in the South China Sea", "en", set()),
    ("Beijing rejects the Kiev regime's claims", "en", {"CHINA", "UKRAINE"}),
    ("the West Bank settlements", "en", set()),                               # not "the West"
    ("Вашингтон и «коллективный Запад» давят на Пекин", "ru", {"US", "WEST", "CHINA"}),
    ("в районе реки Западный Буг", "ru", set()),
    ("МОСКВА, 3 окт - РИА Новости.", "ru", set()),                            # upper-case dateline, not a mention
    ("美方在台湾问题上的错误言行", "zh", {"US", "TAIWAN"}),
    ("印度尼西亚和印度洋", "zh", set()),                                         # Indonesia / Indian Ocean
    ("这样可以方便群众", "zh", set()),                                            # 以方 inside 可以方便
    ("新华社东京6月16日电 日本央行宣布", "zh", {"JAPAN"}),                       # 东京 dateline ignored, 日本 counts
    ("新华社东京6月16日电 央行宣布加息", "zh", set()),
    ("连通着东西方的审美", "zh", set()),
    ("رژیم صهیونیستی و آمریکایی‌ها", "fa", {"ISRAEL", "US"}),
    ("تحولات غرب آسیا", "fa", set()),
    ("أكدت الولايات المتحدة وروسيا", "ar", {"US", "RUSSIA"}),
    ("ABD'nin Rusya politikası", "tr", {"US", "RUSSIA"}),
    ("미국과 로씨야", "ko", {"US", "RUSSIA"}),
    ("한국어 교육", "ko", set()),
])
def test_gazetteer(gaz: Gazetteer, text: str, lang: str, expected: set) -> None:
    assert set(gaz.targets(text, lang)) == expected


def test_gazetteer_unknown_lang_falls_back_to_english(gaz: Gazetteer) -> None:
    assert gaz.targets("NATO summit", "xx") == ["NATO"]


def test_gazetteer_version_is_stable() -> None:
    assert Gazetteer().version == Gazetteer().version


# ------------------------------------------------------------------------------------------- passages
def test_build_passages_chunks_and_caps() -> None:
    sents = [(i, f"s{i}", 60) for i in range(20)]           # 60 tokens each -> 3 sentences per 200-token chunk
    ps = build_passages("Title", sents, max_tokens=200, max_chunks=4)
    assert ps[0] == (0, -1, -1, "Title")
    chunks = ps[1:]
    assert len(chunks) == 4
    assert [(p[1], p[2]) for p in chunks] == [(0, 2), (3, 5), (6, 8), (9, 11)]


def test_build_passages_headline_only() -> None:
    assert build_passages("Same headline", [(0, "Same headline", 3)]) == [(0, -1, -1, "Same headline")]


def test_build_passages_no_title() -> None:
    ps = build_passages("", [(0, "a", 5), (1, "b", 5)])
    assert ps == [(1, 0, 1, "a b")]


# ------------------------------------------------------------------------------------------- sampling
def test_quotas_and_split_deterministic() -> None:
    q = quotas({"a": 10000, "b": 100, "c": 5}, total=1000, cap=600)
    assert q["c"] == 5 and q["a"] <= 600 and q["a"] > q["b"]
    assert split_of("mfa_cn:1") == split_of("mfa_cn:1")
    share = np.mean([split_of(f"d:{i}") == "heldout" for i in range(5000)])
    assert 0.17 < share < 0.23


# ------------------------------------------------------------------------------------------- statistics
def _docs(stream: str, country: str, weeks: list, n_per_week: list, level: list, rng=None) -> pd.DataFrame:
    rows = []
    for w, n, lv in zip(weeks, n_per_week, level):
        for k in range(n):
            noise = 0.0 if rng is None else rng.normal(0, 0.01)
            rows.append({"stream": stream, "country": country, "week": w, "hostility": lv + noise})
    return pd.DataFrame(rows)


WEEKS = [d.strftime("%Y-%m-%d") for d in pd.date_range("2026-01-05", periods=20, freq="W-MON")]


def test_composition_shift_is_not_a_tone_shift() -> None:
    """Two streams with constant (different) tone; the mix moves from A to B. The pooled mean jumps, but the
    within-stream country series stays flat and raises no alert."""
    a = _docs("A", "XX", WEEKS, [50] * 20, [0.2] * 20)
    b = _docs("B", "XX", WEEKS, [10] * 10 + [100] * 10, [0.6] * 20)
    df = pd.concat([a, b])
    pooled = df.groupby("week")["hostility"].mean()
    assert pooled.iloc[-1] - pooled.iloc[0] > 0.15                   # naive pooling shows a fake shift
    s = stats.add_z(stats.stream_series(df, "week", ["hostility"]), "week", 12, 6, 8)
    weights = {"A": np.sqrt(1000), "B": np.sqrt(1100)}
    c = stats.combine(s, {"A": "XX", "B": "XX"}, weights, min_n=8)
    assert np.allclose(c["value"], (weights["A"] * 0.2 + weights["B"] * 0.6) / sum(weights.values()))
    assert c["adj_delta"].dropna().abs().max() < 1e-9
    assert stats.flag(c.rename(columns={"adj_delta": "delta"})).empty


def test_rolling_z_flags_spike_and_respects_min_volume() -> None:
    rng = np.random.default_rng(0)
    means = 0.2 + rng.normal(0, 0.01, 20)
    means[15] = 0.40
    ns = np.full(20, 30.0)
    ses = np.full(20, 0.01)
    z, mu, d = stats.rolling_z(means, ns, ses, window=12, min_base=6, min_n=8)
    assert np.isnan(z[:6]).all()                                        # not enough baseline yet
    assert z[15] > 3 and abs(d[15] - 0.2) < 0.03
    assert np.nanmax(np.abs(np.delete(z, 15))) < 3
    ns[15] = 3                                                          # same spike, too few docs
    z2, _, _ = stats.rolling_z(means, ns, ses, window=12, min_base=6, min_n=8)
    assert np.isnan(z2[15])


def test_rolling_z_ignores_low_volume_baseline_periods() -> None:
    means = np.array([0.2] * 10 + [0.9, 0.2])
    ns = np.array([20] * 10 + [2, 20], dtype=float)                     # the 0.9 week has only 2 docs
    z, mu, _ = stats.rolling_z(means, ns, np.zeros(12), window=12, min_base=6, min_n=8)
    assert mu[11] == pytest.approx(0.2)


def test_flag_requires_effect_size_and_merge_runs() -> None:
    f = pd.DataFrame({"stream": ["A"] * 4, "metric": ["hostility"] * 4, "period": WEEKS[:4],
                      "z": [3.5, 4.0, 1.0, 5.0], "delta": [0.05, 0.06, 0.06, 0.01]})
    flagged = stats.flag(f)
    assert list(flagged["period"]) == WEEKS[:2]                         # week 3: low z; week 4: tiny effect
    merged = stats.merge_runs(flagged, ["stream", "metric"], "week")
    assert len(merged) == 1 and merged.iloc[0]["start"] == WEEKS[0] and merged.iloc[0]["end"] == WEEKS[1]
    assert merged.iloc[0]["z"] == 4.0


def test_add_z_fills_calendar_gaps() -> None:
    df = _docs("A", "XX", [WEEKS[0], WEEKS[3]], [10, 10], [0.2, 0.3])
    s = stats.add_z(stats.stream_series(df, "week", ["hostility"]), "week", 12, 1, 8)
    assert list(s["period"]) == WEEKS[:4] and list(s["n"]) == [10, 0, 0, 10]


def test_binomial_share_z_handles_zero_share() -> None:
    means = np.array([0.25] * 10 + [0.0])
    ns = np.full(11, 14.0)
    z, mu, _ = stats.rolling_z(means, ns, np.zeros(11), window=12, min_base=6, min_n=8, binomial=True)
    assert mu[10] == pytest.approx(0.25)
    assert z[10] == pytest.approx(-0.25 / np.sqrt(0.25 * 0.75 / 14), rel=1e-6)   # about -2.2, not -25


def test_tier() -> None:
    assert stats.tier(5.0) == "strong" and stats.tier(-3.6) == "moderate" and stats.tier(3.1, 2) == "moderate"
    assert stats.tier(3.1) == "weak"
