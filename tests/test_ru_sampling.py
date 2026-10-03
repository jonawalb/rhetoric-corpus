"""Topic-neutral sampling of the RU state-media collector (ru_statemedia.select) on synthetic URL lists."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collectors"))

import ru_statemedia as sm  # noqa: E402

CUT = "2026-07-04"


def _tass(n: int, section: str, month: str) -> list:
    return [{"url": f"https://tass.com/{section}/{month.replace('-', '')}{i:04d}", "date": f"{month}-15"}
            for i in range(n)]


def test_tass_core_sections_taken_in_full_all_dates_no_keyword_filter():
    items = _tass(30, "politics", "2022-03") + _tass(5, "world", "2026-08") + _tass(7, "sports", "2022-03")
    recent, sample, info = sm.select(sm.TassCom(), items, CUT)
    assert len(recent) == 5  # world, inside the 90-day window
    assert len(sample) == 30  # all politics; recent world items not repeated; sports never sampled
    assert {i["tier"] for i in sample} == {"section"}
    assert info["strata"]["core|2022-03"] == [30, 30]


def test_tass_economy_capped_random_and_deterministic():
    items = _tass(250, "economy", "2023-01")
    _, s1, info = sm.select(sm.TassCom(), items, CUT)
    _, s2, _ = sm.select(sm.TassCom(), list(reversed(items)), CUT)
    assert len(s1) == 100 and info["strata"]["economy|2023-01"] == [250, 100]
    assert {i["url"] for i in s1} == {i["url"] for i in s2}  # seed-fixed, order-independent
    assert {i["tier"] for i in s1} == {"section_random"}
    # adding URLs to a stratum only displaces items whose rank is beaten, never reshuffles the rest
    _, s3, _ = sm.select(sm.TassCom(), items + _tass(1, "economy", "2023-01"), CUT)
    assert len({i["url"] for i in s1} & {i["url"] for i in s3}) >= 99


def test_ria_random_per_month_round_robin():
    items = [{"url": f"https://ria.ru/2024{m:02d}15/futbol-{m}{i:05d}.html", "date": f"2024-{m:02d}-15"}
             for m in (1, 2) for i in range(2000)]
    _, sample, info = sm.select(sm.Ria(), items, CUT)
    assert info["strata"]["all|2024-01"] == [2000, 1500]
    assert len(sample) == 3000 and {i["tier"] for i in sample} == {"random"}
    assert {i["date"][:7] for i in sample[:2]} == {"2024-01", "2024-02"}  # interleaved months


def test_rt_ru_stratum_is_sitemap_year():
    items = [{"url": f"https://russian.rt.com/world/news/{i}-x", "date": "2025-01-10", "period": "2024"}
             for i in range(10)]
    _, sample, info = sm.select(sm.RtRu(), items, CUT)
    assert "core|2024" in info["strata"] and len(sample) == 10
    assert all(i["key"].startswith("s2:") for i in sample)


def test_rt_com_excludes_sport():
    items = [{"url": "https://www.rt.com/sport/1-a/", "date": "2022-01-01"},
             {"url": "https://www.rt.com/op-ed/2-b/", "date": "2022-01-01"}]
    _, sample, _ = sm.select(sm.RtCom(), items, CUT)
    assert [i["url"] for i in sample] == ["https://www.rt.com/op-ed/2-b/"]
