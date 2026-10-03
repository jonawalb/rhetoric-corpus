"""Solar Hijri date conversion used by the Iran collectors."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "collectors"))

from ir_common import jalali_str_to_iso, jalali_to_gregorian, president_on  # noqa: E402


def test_known_anchor_dates():
    assert jalali_to_gregorian(1399, 10, 12) == date(2021, 1, 1)
    assert jalali_to_gregorian(1399, 12, 30) == date(2021, 3, 20)    # 1399 was a leap year
    assert jalali_to_gregorian(1400, 1, 1) == date(2021, 3, 21)
    assert jalali_to_gregorian(1403, 1, 1) == date(2024, 3, 20)
    assert jalali_to_gregorian(1403, 12, 30) == date(2025, 3, 20)    # 1403 leap
    assert jalali_to_gregorian(1404, 1, 1) == date(2025, 3, 21)
    assert jalali_to_gregorian(1405, 1, 1) == date(2026, 3, 21)
    assert jalali_to_gregorian(1405, 7, 10) == date(2026, 10, 2)     # mfa.ir item dated with file stamp 20261002
    assert jalali_to_gregorian(1396, 5, 15) == date(2017, 8, 6)


def test_consecutive_days_are_continuous():
    """Every Jalali day 1399-1405 maps to the next Gregorian day; 30 Esfand exists only in leap years."""
    leap_years = set()
    prev = None
    for jy in range(1399, 1406):
        for jm in range(1, 13):
            for jd in range(1, 32 if jm <= 6 else 31):
                d = jalali_to_gregorian(jy, jm, jd)
                if jm == 12 and jd == 30:
                    if d == jalali_to_gregorian(jy + 1, 1, 1):
                        continue  # common year: no 30 Esfand
                    leap_years.add(jy)
                if prev is not None:
                    assert d == prev + timedelta(1), (jy, jm, jd)
                prev = d
    assert leap_years == {1399, 1403}


def test_string_forms():
    assert jalali_str_to_iso("۱۴۰۵/۰۷/۱۰- ۱۶:۱۷") == "2026-10-02"
    assert jalali_str_to_iso("جمعه 10 مهر 1405 - 15:32") == "2026-10-02"
    assert jalali_str_to_iso("يکشنبه 15 مرداد 1396 - 14:14") == "2017-08-06"
    assert jalali_str_to_iso("no date here") is None


def test_president_on():
    assert president_on("2021-01-01") == "Rouhani"
    assert president_on("2022-05-01") == "Raisi"
    assert president_on("2024-06-01") == "Mokhber"
    assert president_on("2026-10-02") == "Pezeshkian"
