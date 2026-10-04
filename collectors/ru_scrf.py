"""Security Council of the Russian Federation (www.scrf.gov.ru/news/allnews/<id>/), 2021-01-01 -> today,
Russian. Article ids are sequential: start at the newest id on the listing page and walk down until
40 consecutive ids are older than 2021-01-01 (missing ids are skipped). No robots.txt (HTTP 404 on
2026-10-02 = no rules); >= 6 s between requests. Rows -> docs/RU/scrf_ru.jsonl.

    uv run --project ~/Projects/rhetoric-corpus python collectors/ru_scrf.py [--start YYYY-MM-DD|earliest]

--start (added 2026-10-03; default 2021-01-01, earliest = 2000-01-01): when the floor is lowered, the walk resumes
below the id where the earlier run stopped, and the ids that run checked and rejected as too old (the last
~40 before it stopped) are re-fetched.
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
from ru_common import balanced_div, ctext, robots_ok, ru_date  # noqa: E402

BASE = "http://www.scrf.gov.ru"
START = "2021-01-01"
EARLIEST = "2000-01-01"
SPEAKERS = [(r"Шойгу", "Shoigu"), (r"Патрушев", "Patrushev"), (r"Медведев", "Medvedev"), (r"Венедиктов", "Venediktov"),
            (r"Путин|Президент", "Putin"), (r"Гребенкин", "Grebenkin"), (r"Вахрукова|Вахруков", "Vakhrukov")]
log = lib.setup_logging("ru_scrf")


def parse(tid: int) -> Optional[Dict]:
    url = f"{BASE}/news/allnews/{tid}/"
    if not robots_ok(url):
        return None
    html = lib.fetch(url, min_delay=6, retries=2)
    if not html:
        if lib.fetch.last.get("status") not in (404, 410):
            return None  # timeout / outage: retried by main(), not marked done
        return {"missing": True, "status": lib.fetch.last.get("status")}
    t = re.search(r'<h1 class="read_title[^"]*">(.*?)</h1>', html, re.S)
    m = re.search(r'class="read_meta"><span>(.*?)</span>', html, re.S)
    body = balanced_div(html, 'class="read_content"') or ""
    body = re.sub(r'<div class="read_meta">.*', "", body, flags=re.S)
    if not (t and m):
        return {"missing": True, "status": "no-title"}
    title, d = ctext(t.group(1)), ru_date(ctext(m.group(1)))
    text = ctext(body)
    spk = next((n for p, n in SPEAKERS if re.search(p, title)), None)
    kind = "interview" if re.search(r"интервью", title, re.I) else (
        "qa" if re.search(r"^Вопрос", text) else ("statement" if re.search(r"заявлени|комментари|обращени", title, re.I)
                                                   else "article"))
    return {"id": lib.make_id("scrf_ru", str(tid)), "country": "RU", "source": "scrf_ru", "outlet": "official",
            "org": "Security Council", "lang": "ru", "date": d, "url": url, "title": title, "speaker": spk,
            "kind": kind, "text": text, "via": lib.fetch.last.get("via") or "direct"}


def main() -> None:
    global START
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=START)
    a = ap.parse_args()
    START = EARLIEST if a.start == "earliest" else a.start
    st = lib.State("ru_scrf")
    if (st.get("floor") or "2021-01-01") > START:  # deepen: re-check ids rejected as too old by the earlier run
        have = {i.split(":", 1)[1] for i in lib.existing_ids(lib.docs_path("RU", "scrf_ru"))}
        low = min((int(i) for i in have), default=0)
        for tid in range(max(1, low - 200), low):
            if st.is_done(str(tid)):
                st._done.discard(str(tid))
        st["complete"] = False
    st["floor"] = START
    top = st.get("top")
    if not top:
        html = lib.fetch(f"{BASE}/news/allnews/", min_delay=6) or ""
        ids = [int(x) for x in re.findall(r'href="/news/allnews/(\d+)/"', html)]
        top = max(ids)
        st["top"] = top
    old_run, buf = 0, []
    tid = top + 1
    while tid > 1:
        tid -= 1
        if st.is_done(str(tid)):
            continue
        r = parse(tid)
        if r is None:  # site or its robots.txt unreachable: wait and retry the same id (never skip it)
            log.warning("scrf.gov.ru unreachable at id %d; retrying in 5 min", tid)
            time.sleep(300)
            tid += 1
            continue
        if r.get("missing"):
            log.info("id %d missing (%s)", tid, r["status"])
        elif r["date"] and r["date"] < START:
            old_run += 1
            if old_run >= 40:
                log.info("reached ids older than %s at %d; done", START, tid)
                st.mark_done(str(tid))
                break
        elif r["date"] and r["text"]:
            old_run = 0
            buf.append(r)
        st.mark_done(str(tid))
        if len(buf) >= 10 or tid % 10 == 0:
            if buf:
                added, total = lib.write_docs("RU", "scrf_ru", buf)
                log.info("wrote %d (total %d), at id %d (%s)", added, total, tid, buf[-1]["date"])
                buf = []
            st.save()
    if buf:
        lib.write_docs("RU", "scrf_ru", buf)
    st["complete"] = True
    st.save()


if __name__ == "__main__":
    main()
