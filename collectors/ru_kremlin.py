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
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from ru_common import robots_ok, run_queue  # noqa: E402

BASE = "http://kremlin.ru"
START = "2021-01-01"
DELAY = 15
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
        log.warning("403 from kremlin.ru (%d so far); pausing 30 min", _403["n"])
        time.sleep(1800)
    return html


def list_ids(st: lib.State) -> Dict[str, str]:
    """{id: date} from the listing pages (/page/N, robots-allowed) back to START."""
    ids: Dict[str, str] = st.get("listed") or {}
    if st.get("list_complete"):
        return ids
    page = st.get("list_page") or 1
    while True:
        html = get(f"{BASE}/events/president/transcripts" + (f"/page/{page}" if page > 1 else ""))
        if not html:
            log.warning("listing page %d failed", page)
            break
        found = re.findall(r'href="/events/president/transcripts/(\d+)".*?datetime="(\d{4}-\d{2}-\d{2})"', html, re.S)
        if not found:
            break
        for tid, d in found:
            ids.setdefault(tid, d)
        oldest = min(d for _, d in found)
        st["listed"], st["list_page"] = ids, page + 1
        st.save()
        log.info("listing page %d: %d ids, oldest %s", page, len(ids), oldest)
        if oldest < START:
            st["list_complete"] = True
            st.save()
            break
        page += 1
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
    solo = re.search(r"обращени|стать|заявлени|приветстви|послани|выступлени|поздравлени", ttl, re.I) and \
        not re.search(r"совместн", ttl, re.I)
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
        if t and "Путин" in speaker:
            putin.append(t)
    return {"date": d.group(1), "title": ttl, "text": "\n".join(full), "putin_text": "\n".join(putin),
            "labelled": labelled}


def main() -> None:
    st = lib.State("ru_kremlin")
    en: Dict[str, str] = {}
    p = lib.docs_path("RU", "kremlin_en")
    if p.exists():
        for r in lib.read_docs(p):
            en[r["id"].split(":", 1)[1]] = r["date"]
    listed = list_ids(st)
    ids = {**{k: v for k, v in listed.items() if v >= START}, **en}
    queue = [{"url": f"{BASE}/events/president/transcripts/{tid}", "tid": tid, "date": d}
             for tid, d in sorted(ids.items(), key=lambda kv: -int(kv[0]))]
    log.info("%d transcript ids (%d from the English corpus, %d listed)", len(queue), len(en), len(listed))

    def parse(it: Dict) -> Optional[List[Dict]]:
        html = get(it["url"])
        if not html:
            return None
        r = parse_page(it["tid"], html)
        if not r or not r["text"] or r["date"] < START:
            return []
        return [{"id": lib.make_id("kremlin_ru", it["tid"]), "country": "RU", "source": "kremlin_ru",
                 "outlet": "official", "org": "Kremlin", "lang": "ru", "date": r["date"], "url": it["url"],
                 "title": r["title"], "speaker": "Putin", "kind": "transcript", "text": r["text"],
                 "putin_text": r["putin_text"], "labelled": r["labelled"],
                 "pair_id": f"kremlin_en:{it['tid']}" if it["tid"] in en else None,
                 "via": "wayback" if lib.fetch.last.get("via") == "wayback" else "direct"}]

    run_queue(queue, st, parse, "RU", lambda r: r["source"], batch=5)


if __name__ == "__main__":
    main()
