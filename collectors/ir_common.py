"""Helpers shared by the Iran (IR) collectors: Solar Hijri (Jalali) dates, Persian digits, incumbents."""
from __future__ import annotations

import re
from datetime import date
from typing import Optional, Tuple

PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
JALALI_MONTHS = {"فروردین": 1, "اردیبهشت": 2, "خرداد": 3, "تیر": 4, "مرداد": 5, "شهریور": 6, "مهر": 7,
                 "آبان": 8, "آذر": 9, "دی": 10, "بهمن": 11, "اسفند": 12}


def to_ascii_digits(s: str) -> str:
    return s.translate(PERSIAN_DIGITS)


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> date:
    """Solar Hijri -> Gregorian (33-year arithmetic rule; matches the official calendar for 1300-1450 SH)."""
    if not (1 <= jm <= 12 and 1 <= jd <= (31 if jm <= 6 else 30)):
        raise ValueError(f"bad Jalali date {jy}/{jm}/{jd}")
    jy += 1595
    days = -355668 + 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + jd
    days += (jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0
    mdays = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gd > mdays[gm]:
        gd -= mdays[gm]
        gm += 1
    return date(gy, gm + 1, gd)


def jalali_str_to_iso(s: str) -> Optional[str]:
    """'۱۴۰۵/۰۷/۱۰', '1405/7/10', '1405-07-10' or '10 مهر 1405' (Persian or Latin digits) -> 'YYYY-MM-DD'."""
    s = to_ascii_digits(s).replace("ي", "ی").replace("ك", "ک")
    try:
        m = re.search(r"(?<!\d)(1[34]\d\d)[/\-.](\d{1,2})[/\-.](\d{1,2})(?!\d)", s)
        if m:
            return jalali_to_gregorian(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        m = re.search(r"(?<!\d)(\d{1,2})\s+(" + "|".join(JALALI_MONTHS) + r")\s+(1[34]\d\d)(?!\d)", s)
        if m:
            return jalali_to_gregorian(int(m.group(3)), JALALI_MONTHS[m.group(2)], int(m.group(1))).isoformat()
    except ValueError:
        return None
    return None


PRESIDENTS: Tuple[Tuple[str, str], ...] = (("2005-08-03", "Khatami"), ("2013-08-03", "Ahmadinejad"),
                                           ("2021-08-03", "Rouhani"), ("2024-05-19", "Raisi"),
                                           ("2024-07-28", "Mokhber"), ("9999-12-31", "Pezeshkian"))


def president_on(iso: str) -> str:
    """Incumbent (or acting) president on a date: Khatami to 2005-08-03, Ahmadinejad to 2013-08-03,
    Rouhani to 2021-08-03, Raisi to 2024-05-19, Mokhber
    (acting) to 2024-07-28, Pezeshkian after."""
    for end, name in PRESIDENTS:
        if iso <= end:
            return name
    return PRESIDENTS[-1][1]


# ------------------------------------------------------------------------- scale helpers (added 2026-10-03)
# The deep IR backfills (state-media archives back to the 2000s, 10^5-10^6 URLs per site) reuse the generic
# scale helpers written for the RU collectors: an append-only done-log state, an incremental-id JSONL writer,
# a wildcard-aware robots.txt check and a balanced <div> extractor. Re-exported here so IR collectors import
# from one place.
from ru_common import BigState, balanced_div, robots_ok, write_docs_fast  # noqa: E402


def fa_norm(s: str) -> str:
    """Arabic yeh/kaf -> Persian (ي→ی, ك→ک), Persian/Arabic digits -> ASCII. For matching/parsing only;
    stored text is kept as served (the index folds ي/ك itself)."""
    return to_ascii_digits(s).replace("ي", "ی").replace("ك", "ک")


def tehran_date(iso_ts: str) -> Optional[str]:
    """'2024-03-19T20:27:00Z' / '...+03:30' -> the calendar date in Tehran (UTC+03:30; +04:30 DST before 2022
    is ignored: at most a few late-night items shift by one day). Date-only strings pass through."""
    from datetime import datetime, timedelta, timezone
    s = iso_ts.strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}(?::\d{2})?)(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?)?", s)
    if not m:
        return None
    if not m.group(2) or not m.group(3) or m.group(3) not in ("Z", "+00:00", "+0000"):
        return m.group(1)
    t = datetime.fromisoformat(m.group(1) + "T" + m.group(2)).replace(tzinfo=timezone.utc)
    return (t + timedelta(hours=3, minutes=30)).date().isoformat()


__all__ = ["to_ascii_digits", "jalali_to_gregorian", "jalali_str_to_iso", "president_on", "JALALI_MONTHS",
           "fa_norm", "tehran_date", "BigState", "balanced_div", "robots_ok", "write_docs_fast"]
