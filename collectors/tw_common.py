"""Shared helpers for the Taiwan (TW) collectors: ROC (Minguo) and Western date parsing, sitemap reading,
presidential speaker attribution, and a small generic run loop.

ROC years: 民國 N 年 = N + 1911 (民國113年 = 2024). Dates are only ever taken from what the page states;
anything ambiguous returns None and the item is skipped.

Nothing here caches article pages on disk (disk is tight); only lib.fetch is used for network access.
"""
from __future__ import annotations

import datetime as dt
import html as _html
import logging
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import fetch  # noqa: E402

log = logging.getLogger("tw_common")
START = "2021-01-01"
LAI_FROM = "2024-05-20"   # Lai Ching-te inaugurated; Tsai Ing-wen before

EN_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], 1)}
EN_MONTHS.update({k[:3]: v for k, v in list(EN_MONTHS.items())})
EN_MONTHS["sept"] = 9


def ymd(y: int, m: int, d: int) -> Optional[str]:
    """Valid calendar date as 'YYYY-MM-DD', else None."""
    try:
        return dt.date(y, m, d).isoformat()
    except (ValueError, TypeError):
        return None


def roc_to_ad(year: int) -> int:
    """ROC (Minguo) year -> Gregorian year: 113 -> 2024."""
    if not 1 <= year <= 300:
        raise ValueError(f"not a plausible ROC year: {year}")
    return year + 1911


_ROC_CJK = re.compile(r"(?:民國|中華民國)?\s*(?<!\d)(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_ROC_NUM = re.compile(r"(?<!\d)(\d{2,3})[./-](\d{1,2})[./-](\d{1,2})(?!\d)")
_AD_NUM = re.compile(r"(?<!\d)((?:19|20)\d\d)[./-](\d{1,2})[./-](\d{1,2})(?!\d)")
_AD_CJK = re.compile(r"((?:19|20)\d\d)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def roc_date(s: str) -> Optional[str]:
    """ROC date in `s` -> 'YYYY-MM-DD'. Accepts '民國113年5月20日', '113年05月20日', '113-05-20', '113.5.20'.

    Three-digit years are taken as ROC; a two-digit year is only accepted in the CJK form with 年."""
    if not s:
        return None
    m = _ROC_CJK.search(s)
    if m and not _AD_CJK.search(s):
        return ymd(roc_to_ad(int(m.group(1))), int(m.group(2)), int(m.group(3)))
    m = _ROC_NUM.search(s)
    if m and len(m.group(1)) == 3 and not _AD_NUM.search(s):
        return ymd(roc_to_ad(int(m.group(1))), int(m.group(2)), int(m.group(3)))
    return None


def ad_date(s: str) -> Optional[str]:
    """Gregorian date in `s`: '2024-05-20', '2024/5/20', '2024.05.20', '2024年5月20日', 'May 20, 2024',
    '20 May 2024'. Returns the first match, else None."""
    if not s:
        return None
    m = _AD_NUM.search(s) or _AD_CJK.search(s)
    if m:
        return ymd(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+((?:19|20)\d\d)\b", s)
    if m and m.group(1).lower() in EN_MONTHS:
        return ymd(int(m.group(3)), EN_MONTHS[m.group(1).lower()], int(m.group(2)))
    m = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+((?:19|20)\d\d)\b", s)
    if m and m.group(2).lower() in EN_MONTHS:
        return ymd(int(m.group(3)), EN_MONTHS[m.group(2).lower()], int(m.group(1)))
    return None


def any_date(s: str) -> Optional[str]:
    """Gregorian first, then ROC."""
    return ad_date(s) or roc_date(s)


def president_at(date: str) -> str:
    """Name of the ROC president in office on `date` (2016-05-20 onward)."""
    return "Lai Ching-te" if date >= LAI_FROM else "Tsai Ing-wen"


def sitemap_locs(xml: str) -> List[Tuple[str, str]]:
    """[(loc, lastmod)] from a sitemap index or urlset."""
    out = []
    for blk in re.findall(r"<(?:url|sitemap)>(.*?)</(?:url|sitemap)>", xml, re.S):
        loc = re.search(r"<loc>\s*(.*?)\s*</loc>", blk, re.S)
        lm = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", blk, re.S)
        if loc:
            out.append((_html.unescape(loc.group(1)), lm.group(1)[:10] if lm else ""))
    return out


def meta(html: str, name: str) -> Optional[str]:
    """content of <meta name|property=`name`>."""
    m = re.search(rf'<meta[^>]+(?:name|property)=["\']{re.escape(name)}["\'][^>]*content=["\']([^"\']*)["\']', html, re.I) \
        or re.search(rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:name|property)=["\']{re.escape(name)}["\']', html, re.I)
    return _html.unescape(m.group(1)).strip() if m else None


def get(url: str, delay: float = 4.0, wayback: bool = False) -> Optional[str]:
    """Polite fetch (robots + delay via lib), never cached on disk."""
    return fetch(url, min_delay=delay, use_wayback_fallback=wayback)


__all__ = ["START", "LAI_FROM", "ymd", "roc_to_ad", "roc_date", "ad_date", "any_date", "president_at",
           "sitemap_locs", "meta", "get"]
