"""Shared helpers for the Türkiye (TR) collectors: date parsing (EN/TR), the mfa.gov.tr ASP.NET list pages
(GridView pagination is a form POST "postback"), and speaker detection.

POSTs go through lib's robots check and per-host delay (same pattern as collectors/by_mfa.py); nothing is
cached on disk.
"""
from __future__ import annotations

import html as _html
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import UA, _wait, clean_html, fetch, robots_allowed  # noqa: E402

log = logging.getLogger("tr_common")
START = "2021-01-01"

MONTHS = {  # English and Turkish month names (Turkish lower-cased with Turkish i)
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "mayis": 5, "haziran": 6,
    "temmuz": 7, "ağustos": 8, "agustos": 8, "eylül": 9, "eylul": 9, "ekim": 10, "kasım": 11, "kasim": 11,
    "aralık": 12, "aralik": 12,
}
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
_DMY = re.compile(rf"\b(\d{{1,2}})\s+({_MON})\s*,?\s+((?:19|20)\d\d)\b", re.I)
_MDY = re.compile(rf"\b({_MON})\s+(\d{{1,2}})\s*,\s*((?:19|20)\d\d)\b", re.I)
_NUM = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./]((?:19|20)\d\d)\b")


def _lower_tr(s: str) -> str:
    return s.replace("İ", "i").replace("I", "ı").lower() if any(c in s for c in "İıŞşĞğ") else s.lower()


def _ymd(y: int, m: int, d: int) -> Optional[str]:
    import datetime as dt
    try:
        return dt.date(y, m, d).isoformat()
    except ValueError:
        return None


def date_in_text(s: str) -> Optional[str]:
    """First 'D Month YYYY', 'Month D, YYYY' (EN or TR month names) or 'DD.MM.YYYY' date in `s`, as YYYY-MM-DD."""
    best: Optional[Tuple[int, str]] = None
    for m in _DMY.finditer(s):
        mon = MONTHS.get(_lower_tr(m.group(2))) or MONTHS.get(m.group(2).lower())
        v = _ymd(int(m.group(3)), mon, int(m.group(1))) if mon else None
        if v:
            best = min(best or (10**9, ""), (m.start(), v))
            break
    for m in _MDY.finditer(s):
        mon = MONTHS.get(_lower_tr(m.group(1))) or MONTHS.get(m.group(1).lower())
        v = _ymd(int(m.group(3)), mon, int(m.group(2))) if mon else None
        if v:
            best = min(best or (10**9, ""), (m.start(), v))
            break
    for m in _NUM.finditer(s):
        v = _ymd(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if v:
            best = min(best or (10**9, ""), (m.start(), v))
            break
    return best[1] if best else None


def dmy_dots(s: str) -> Optional[str]:
    """'01.10.2026' -> '2026-10-01'."""
    m = re.fullmatch(r"\s*(\d{2})\.(\d{2})\.(\d{4})\s*", s)
    return _ymd(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


# ------------------------------------------------------------------------------------------ mfa.gov.tr
MFA = "https://www.mfa.gov.tr"
_ITEM = re.compile(r"""<div class="sub_lstitm"><a href='([^']+)'[^>]*>\s*(.*?)</a>""", re.S)
_HIDDEN = re.compile(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"')


def mfa_items(page_html: str) -> List[Tuple[str, str]]:
    """(href, title) of the list entries on an mfa.gov.tr sub.*.mfa page."""
    return [(h, clean_html(t)) for h, t in _ITEM.findall(page_html)]


def mfa_pages(page_html: str) -> List[int]:
    return sorted({int(n) for n in re.findall(r"Page\$(\d+)", page_html)})


def mfa_postback(url: str, page_html: str, page: int, delay: float = 6.0) -> Optional[str]:
    """POST the GridView pager event 'Page$<page>' back to `url` (the page's own form)."""
    if not robots_allowed(url):
        log.warning("robots.txt disallows %s", url)
        return None
    form = {k: _html.unescape(v) for k, v in _HIDDEN.findall(page_html)}
    if "__VIEWSTATE" not in form:
        return None
    form["__EVENTTARGET"] = "sb$grd"
    form["__EVENTARGUMENT"] = f"Page${page}"
    args: List[str] = []
    for k, v in form.items():
        args += ["--data-urlencode", f"{k}={v}"]
    for attempt in range(3):
        _wait("www.mfa.gov.tr", delay)
        try:
            r = subprocess.run(["curl", "-sS", "-m", "90", "--compressed", "-A", UA, "-e", url,
                                "-w", "\n%{http_code}"] + args + [url], capture_output=True, timeout=120)
            body, _, code = r.stdout.decode("utf-8", "ignore").rpartition("\n")
            if code.strip() == "200" and "sub_lstitm" in body:
                return body
            log.warning("postback %s page %d: HTTP %s (%d/3)", url, page, code.strip(), attempt + 1)
            if code.strip() in ("401", "403", "404"):
                return None
        except Exception as e:
            log.warning("postback %s page %d failed (%d/3): %s", url, page, attempt + 1, e)
    return None


def mfa_list_all(url: str, stop_before: Optional[str] = START, delay: float = 6.0) -> List[Tuple[str, str]]:
    """All (href, title) entries of a list page, following the pager. Stops paging once a whole page of
    dated entries is older than `stop_before` (lists are newest first)."""
    html = fetch(url, min_delay=delay)
    if not html:
        return []
    out = mfa_items(html)
    seen_pages = {1}
    page = 1
    while True:
        nxt = [p for p in mfa_pages(html) if p > page]
        if not nxt:
            break
        if stop_before:
            dates = [d for d in (date_in_text(t) for _, t in mfa_items(html)) if d]
            if dates and max(dates) < stop_before:
                break
        page = nxt[0]
        if page in seen_pages:
            break
        seen_pages.add(page)
        body = mfa_postback(url, html, page, delay)
        if not body:
            log.warning("pager stopped at page %d of %s", page, url)
            break
        html = body
        out += mfa_items(html)
    return out


def mfa_article(page_html: str) -> Optional[Dict[str, str]]:
    m = re.search(r'<span class="lead"><strong>(.*?)</strong>', page_html, re.S)
    title = clean_html(m.group(1)) if m else ""
    i = page_html.find('<span class="mfa-content-text">')
    if i < 0:
        return None
    j = page_html.find("<script", i)
    body = page_html[i:j if j > 0 else None]
    text = clean_html(body)
    return {"title": title, "text": text} if len(text) > 60 else None


# ------------------------------------------------------------------------------------------ speakers
SPEAKERS = [  # (regex, canonical name)
    (r"Erdo[gğ]an", "Erdoğan"), (r"Hakan Fidan", "Fidan"), (r"Mevl[uü]t [CÇ]avu[sş]o[gğ]lu", "Çavuşoğlu"),
    (r"Tanju Bilgi[cç]", "Bilgiç"), (r"[OÖ]nc[uü] Ke[cç]eli", "Keçeli"), (r"[İI]brahim Kal[ıi]n", "Kalın"),
    (r"Fahrettin Altun", "Altun"), (r"Burhanettin Duran", "Duran"), (r"Cevdet Y[ıi]lmaz", "Yılmaz"),
    (r"Akif [CÇ]a[gğ]atay K[ıi]l[ıi][cç]", "Kılıç"),
]


def speaker_in(title: str) -> Optional[str]:
    for rx, name in SPEAKERS:
        if re.search(rx, title):
            return name
    return None
