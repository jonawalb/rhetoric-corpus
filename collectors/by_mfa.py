"""Belarus MFA (mfa.gov.by) news section, EN and RU -> docs/BY/by_mfa_<lang>.jsonl.

The "News" section (/press/news_mfa/, /en/press/news_mfa/) lists items through the page's own public
date-range search form (POST with the token printed in the page; a session cookie is kept in
state/). We query one day at a time, newest first (the form returns up to 10 items per query).
The "Statements" section is NOT collected: its pages carry no publication date.
Pages are windows-1251.

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/by_mfa.py --lang ru
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import STATE, UA, _wait, robots_allowed, State, clean_html, fetch, make_id, now_iso, setup_logging, write_docs  # noqa: E402

BASE = "https://mfa.gov.by"
BLOCK_MARK = "Web Page Blocked"
MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                          "september", "october", "november", "december"])}
MONTHS.update({m: i + 1 for i, m in enumerate(["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
                                               "августа", "сентября", "октября", "ноября", "декабря"])})
SPEAKERS = [("Ryzhenkov", "Ryzhenkov"), ("Рыженков", "Ryzhenkov"), ("Aleinik", "Aleinik"), ("Алейник", "Aleinik"),
            ("Makei", "Makei"), ("Макей", "Makei"), ("Varankou", "Varankou"), ("Воронков", "Varankou"),
            ("Glaz", "Glaz"), ("Глаз", "Glaz")]


def get(url: str):
    raw = fetch(url, min_delay=5, binary=True, timeout=60)
    if raw is None:
        return None
    h = raw.decode("cp1251", "replace")
    return None if BLOCK_MARK in h else h


def parse_date(s: str):
    m = re.search(r"(\d{1,2})\s+([A-Za-zА-Яа-я]+)\s+(\d{4})", s)
    if not m or m.group(2).lower() not in MONTHS:
        return None
    return f"{int(m.group(3)):04d}-{MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"


def parse(h: str):
    t = re.search(r"<h1>(.*?)</h1>", h, re.S)
    d = re.search(r'<span class="date _big">([^<]+)</span>(.*?)(?:<p>\s*<a href="/print/|<div class="content-page__social|</section>)', h, re.S)
    if not (t and d):
        return None
    date = parse_date(d.group(1))
    return (date, clean_html(t.group(1)), clean_html(d.group(2))) if date else None


def kind_of(title: str) -> str:
    tl = title.lower()
    if any(k in tl for k in ("speech", "address", "выступлен", "remarks")):
        return "transcript"
    if any(k in tl for k in ("interview", "интервью")):
        return "interview"
    if any(k in tl for k in ("answer", "comment", "ответ", "коммент", "question")):
        return "qa"
    return "statement"


def post_list(lang: str, day: dt.date, jar: Path, tok: list) -> list:
    """Article paths for one day, via the site's search form."""
    page = f"{BASE}/en/press/news_mfa/" if lang == "en" else f"{BASE}/press/news_mfa/"
    pref = "/en/press/news_mfa/" if lang == "en" else "/press/news_mfa/"
    if not robots_allowed(page):
        return []
    for attempt in range(3):
        if not tok:
            _wait("mfa.gov.by", 5)
            r = subprocess.run(["curl", "-s", "-m", "60", "-A", UA, "-c", str(jar), "-b", str(jar), page],
                               capture_output=True)
            m = re.search(rb'id="ggtoken"[^>]*value="([^"]+)"', r.stdout)
            if not m:
                time.sleep(30 * (attempt + 1))
                continue
            tok[:] = [m.group(1).decode()]
        d = day.strftime("%d.%m.%Y")
        _wait("mfa.gov.by", 5)
        r = subprocess.run(["curl", "-s", "-m", "60", "-A", UA, "-c", str(jar), "-b", str(jar), "-e", page,
                            "--data", f"page=1&ggtoken={tok[0]}&date_from={d}&date_to={d}", page], capture_output=True)
        h = r.stdout.decode("cp1251", "replace")
        if r.returncode != 0 or "tabs__item" not in h:
            tok.clear()
            time.sleep(10 * (attempt + 1))
            continue
        return re.findall(r'<a href="(' + re.escape(pref) + r'[0-9a-f]{16}\.html)"', h)
    return []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="en", choices=["en", "ru"])
    ap.add_argument("--since", default="2021-01-01")
    a = ap.parse_args()
    source = f"by_mfa_{a.lang}"
    log = setup_logging(source)
    st = State(source)
    jar, tok = STATE / f"{source}.cookies", []
    today = dt.date.today()
    day, stop = today, dt.date.fromisoformat(a.since)
    n = 0
    while day >= stop:
        dkey = "day:" + day.isoformat()
        if not st.is_done(dkey) or day >= today - dt.timedelta(days=2):
            links = list(dict.fromkeys(post_list(a.lang, day, jar, tok)))
            if len(links) >= 10:
                log.warning("%s: 10+ items, some may be missing", day)
            for path in links:
                url = BASE + path
                if st.is_done(url):
                    continue
                h = get(url)
                if h is None and fetch.last.get("status", 0) == 0:
                    log.warning("network failure on %s", url)
                    continue
                res = parse(h) if h else None
                if res and len(res[2]) >= 80:
                    date, title, text = res
                    spk = next((v for k, v in SPEAKERS if k in title), None)
                    write_docs("BY", source, [dict(
                        id=make_id(source, path.rsplit("/", 1)[-1][:-5]), country="BY", source=source,
                        outlet="official", org="MFA", lang=a.lang, date=date, url=url, title=title, speaker=spk,
                        kind=kind_of(title), text=text, via="direct", fetched=now_iso())])
                    n += 1
                elif h and not res:
                    log.warning("unparsed %s", url)
                st.mark_done(url)
            st.mark_done(dkey)
            st.save()
        if day.day == 1:
            log.info("reached %s, %d new docs so far", day, n)
        day -= dt.timedelta(days=1)
    log.info("finished: %d new docs", n)


if __name__ == "__main__":
    main()
