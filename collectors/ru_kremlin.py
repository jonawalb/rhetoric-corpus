"""Kremlin transcripts in RUSSIAN (kremlin.ru/events/president/transcripts), 2021-01-01 -> today,
paired with the existing English corpus (docs/RU/kremlin_en.jsonl) by transcript id (same numeric id on
kremlin.ru and en.kremlin.ru).

Rows go to docs/RU/kremlin_ru.jsonl:
  text        full transcript, all speakers, speaker labels kept inline ("В.Путин: ...")
  putin_text  only Putin's paragraphs (same rule as the English collector: labels switch speaker; an
              unlabelled single-voice text -- обращение/статья/заявление/послание/приветствие -- is Putin's)
  pair_id     "kremlin_en:<id>" when the English corpus has that transcript

http://kremlin.ru answers (https timed out on 2026-10-02); >= 15 s between requests; on HTTP 403 the
collector pauses 30 min (the site 403'd after ~150 quick requests in an earlier run) and lib falls back
to Wayback for that page.

    uv run --project ~/Projects/rhetoric-corpus python collectors/ru_kremlin.py

DEEP mode (added 2026-10-03):
  --start YYYY-MM-DD|earliest   floor (default 2021-01-01; earliest = 2000-01-01). The transcript listing
                                (/events/president/transcripts/page/N) reaches back to 2000 (~690 pages, checked
                                2026-10-03); listing resumes from the page where the 2021 run stopped.
  --events                      after the transcripts, walk EVERY presidential event id downward
                                (/events/president/news/<id>: readouts, meetings, calls, statements, greetings,
                                transcripts) from the newest id until 3,000 consecutive ids are missing or older than
                                --start. Rows -> docs/RU/kremlin_events_<lang>.jsonl (same fields as kremlin_ru:
                                full text with speaker labels + putin_text; kind = transcript when speaker-labelled,
                                else news). For lang ru, ids already in kremlin_ru are skipped (no duplicates).
  --lang en                     en.kremlin.ru (separate host, own delay): transcripts AND events both go to
                                docs/RU/kremlin_events_en.jsonl, because docs/RU/kremlin_en.jsonl (imported) holds
                                Putin-only text; `pair_id` links an events_en row to kremlin_en when that has the id.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from ru_common import robots_ok, run_queue, write_docs_fast  # noqa: E402

BASE = "http://kremlin.ru"  # --lang en switches to http://en.kremlin.ru
START = "2021-01-01"
EARLIEST = "2000-01-01"
MISS_STOP = 3000
DELAY = 15
LANG = "ru"
log = lib.setup_logging("ru_kremlin")
_TIP = re.compile(r'<span class="read__tooltip')
_SPAN = re.compile(r"<(/?)span\b[^>]*>")
_403 = {"n": 0}


def strip_person_tags(p: str) -> str:
    while (m := _TIP.search(p)):
        depth = 0
        for t in _SPAN.finditer(p, m.start()):
            depth += -1 if t.group(1) else 1
            if depth == 0:
                p = p[: m.start()] + p[t.end():]
                break
        else:
            p = p[: m.start()]
    return re.sub(r"</?a\b[^>]*>", "", p)


def get(url: str) -> Optional[str]:
    if not robots_ok(url):
        return None
    html = lib.fetch(url, min_delay=DELAY, use_wayback_fallback=True)
    if lib.fetch.last.get("status") == 403:
        _403["n"] += 1
        log.warning("403 from %s (%d so far); pausing 30 min", BASE, _403["n"])
        time.sleep(1800)
    return html


def list_ids(st: lib.State) -> Dict[str, str]:
    """{id: date} from the listing pages (/page/N, robots-allowed) back to START. A listing completed for a
    later floor (the 2021 run) is resumed from its last page when START is earlier."""
    ids: Dict[str, str] = st.get("listed") or {}
    if st.get("list_complete") and (st.get("list_floor") or "2021-01-01") <= START:
        return ids
    st["list_complete"] = False
    page = st.get("list_page") or 1
    while True:
        html = get(f"{BASE}/events/president/transcripts" + (f"/page/{page}" if page > 1 else ""))
        found = re.findall(r'href="/events/president/transcripts/(\d+)".*?datetime="(\d{4}-\d{2}-\d{2})"', html or "", re.S)
        if not found:
            if html is None and lib.fetch.last.get("status") not in (404, 410):
                log.warning("listing page %d failed; resuming there next run", page)
                return ids
            break  # past the last listing page
        for tid, d in found:
            ids.setdefault(tid, d)
        oldest = min(d for _, d in found)
        st["listed"], st["list_page"] = ids, page + 1
        st.save()
        log.info("listing page %d: %d ids, oldest %s", page, len(ids), oldest)
        if oldest < START:
            break
        page += 1
    st["list_complete"], st["list_floor"] = True, START
    st.save()
    return ids


def _c(s: str) -> str:
    """clean_html, with the source's hard line-wraps inside a paragraph joined."""
    return re.sub(r"\s*\n\s*", " ", lib.clean_html(s)).strip()


def parse_page(tid: str, html: str) -> Optional[Dict]:
    title = re.search(r'<h1 class="entry-title[^"]*"[^>]*>(.*?)</h1>', html, re.S)
    meta = html.find("read__meta")
    d = re.search(r'datetime="(\d{4}-\d{2}-\d{2})"', html[meta:] if meta > 0 else html)
    body_at = html.find('itemprop="articleBody"')
    if not (title and d and body_at > 0):
        return None
    end = html.find("read__bottom", body_at)
    paras = [strip_person_tags(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", html[body_at:end if end > 0 else None], re.S)]
    lab = re.compile(r"\s*((?:<b>(?:(?!</b>).)*</b>\s*)+)(?:\((?:синхронный )?перевод\)\s*)?(:?)(.*)$", re.S)

    def label(p: str):
        m = lab.match(p)
        if not m:
            return None
        name = lib.clean_html(m.group(1))
        if not (name.endswith(":") or m.group(2)) or len(name) > 300:
            return None
        return name.rstrip(": "), m.group(3)

    ttl = _c(title.group(1))
    labelled = any(label(p) for p in paras)
    solo = re.search(r"обращени|стать|заявлени|приветстви|послани|выступлени|поздравлени|"
                     r"address|article|statement|greeting|message|speech|congratulat", ttl, re.I) and \
        not re.search(r"совместн|joint", ttl, re.I)
    speaker, full, putin = ("Путин" if not labelled and solo else ""), [], []
    for p in paras:
        lb = label(p)
        if lb:
            speaker, p = lb
            t = _c(p)
            if t:
                full.append(f"{speaker}: {t}")
        else:
            t = _c(p)
            if t and t != "* * *":
                full.append(t)
        t = _c(p)
        if t and ("Путин" in speaker or "Putin" in speaker):
            putin.append(t)
    return {"date": d.group(1), "title": ttl, "text": "\n".join(full), "putin_text": "\n".join(putin),
            "labelled": labelled}


def row(source: str, tid: str, url: str, r: Dict, kind: str, pair: Optional[str]) -> Dict:
    return {"id": lib.make_id(source, tid), "country": "RU", "source": source, "outlet": "official", "org": "Kremlin",
            "lang": LANG, "date": r["date"], "url": url, "title": r["title"], "speaker": "Putin", "kind": kind,
            "text": r["text"], "putin_text": r["putin_text"], "labelled": r["labelled"], "pair_id": pair,
            "via": "wayback" if lib.fetch.last.get("via") == "wayback" else "direct"}


def transcripts(st: lib.State, en: Dict[str, str]) -> None:
    listed = list_ids(st)
    src = "kremlin_ru" if LANG == "ru" else "kremlin_events_en"
    ids = {k: v for k, v in listed.items() if v >= START}
    if LANG == "ru":
        ids.update(en)
    queue = [{"url": f"{BASE}/events/president/transcripts/{tid}", "tid": tid, "date": d}
             for tid, d in sorted(ids.items(), key=lambda kv: -int(kv[0]))]
    log.info("%d transcript ids (%d listed, floor %s)", len(queue), len(listed), START)

    def parse(it: Dict) -> Optional[List[Dict]]:
        html = get(it["url"])
        if not html:
            return None
        r = parse_page(it["tid"], html)
        if not r or not r["text"] or r["date"] < START:
            return []
        pair = f"kremlin_en:{it['tid']}" if it["tid"] in en else None
        return [row(src, it["tid"], it["url"], r, "transcript", pair)]

    run_queue(queue, st, parse, "RU", lambda r: r["source"], batch=5)


def events(st: lib.State, en: Dict[str, str]) -> None:
    """Walk /events/president/news/<id> downward from the newest id (see module docstring)."""
    src = f"kremlin_events_{LANG}"
    skip = set()
    if LANG == "ru":
        skip = {i.split(":", 1)[1] for i in lib.existing_ids(lib.docs_path("RU", "kremlin_ru"))}
    have = {i.split(":", 1)[1] for i in lib.existing_ids(lib.docs_path("RU", src))}
    top = st.get("ev_top")
    if not top:
        html = get(f"{BASE}/events/president/news") or ""
        ids = [int(x) for x in re.findall(r'href="/events/president/news/(\d+)"', html)]
        if not ids:
            log.warning("events listing unreadable; retry next run")
            return
        top = max(ids)
        st["ev_top"] = top
    i, miss, buf = int(st.get("ev_cursor") or top), int(st.get("ev_miss") or 0), []
    while i > 0 and miss < MISS_STOP:
        tid = str(i)
        if tid in skip or tid in have:
            miss = 0
        else:
            url = f"{BASE}/events/president/news/{tid}"
            html = get(url)
            r = parse_page(tid, html) if html else None
            if html is None and lib.fetch.last.get("status") not in (404, 410):
                log.warning("event %s failed (status %s); retry in 5 min", tid, lib.fetch.last.get("status"))
                time.sleep(300)
                continue
            if r and r["text"] and r["date"] >= START:
                miss = 0
                pair = f"kremlin_en:{tid}" if tid in en else None
                buf.append(row(src, tid, url, r, "transcript" if r["labelled"] else "news", pair))
            else:
                miss += 1
        i -= 1
        if len(buf) >= 5 or i % 50 == 0:
            if buf:
                added, total = write_docs_fast("RU", src, buf)
                log.info("events: wrote %d (total %d), at id %d (%s)", added, total, i, buf[-1]["date"])
                buf = []
            st["ev_cursor"], st["ev_miss"] = i, miss
            st.save()
    if buf:
        write_docs_fast("RU", src, buf)
    st["ev_cursor"], st["ev_miss"] = i, miss
    st["ev_complete"] = True
    st.save()
    log.info("events walk finished at id %d (miss run %d)", i, miss)


def main() -> None:
    global BASE, START, LANG
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["ru", "en"], default="ru")
    ap.add_argument("--start", default=START, help="floor YYYY-MM-DD or 'earliest' (2000-01-01)")
    ap.add_argument("--events", action="store_true", help="after transcripts, walk all event ids (see docstring)")
    a = ap.parse_args()
    LANG = a.lang
    BASE = "http://kremlin.ru" if LANG == "ru" else "http://en.kremlin.ru"
    START = EARLIEST if a.start == "earliest" else a.start
    st = lib.State("ru_kremlin" if LANG == "ru" else "ru_kremlin_en")
    en: Dict[str, str] = {}
    p = lib.docs_path("RU", "kremlin_en")
    if p.exists():
        for r in lib.read_docs(p):
            en[r["id"].split(":", 1)[1]] = r["date"]
    transcripts(st, en)
    if a.events and not st.get("ev_complete"):
        events(st, en)


if __name__ == "__main__":
    main()
