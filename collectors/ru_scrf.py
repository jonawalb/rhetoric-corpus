"""Security Council of the Russian Federation (www.scrf.gov.ru/news/allnews/<id>/), 2021-01-01 -> today,
Russian. Article ids are sequential: start at the newest id on the listing page and walk down until
40 consecutive ids are older than 2021-01-01 (missing ids are skipped). No robots.txt (HTTP 404 on
2026-10-02 = no rules); >= 6 s between requests. Rows -> docs/RU/scrf_ru.jsonl.

    uv run --project ~/Projects/rhetoric-corpus python collectors/ru_scrf.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from ru_common import balanced_div, ctext, robots_ok, ru_date  # noqa: E402

BASE = "http://www.scrf.gov.ru"
START = "2021-01-01"
SPEAKERS = [(r"Шойгу", "Shoigu"), (r"Патрушев", "Patrushev"), (r"Медведев", "Medvedev"), (r"Венедиктов", "Venediktov"),
            (r"Путин|Президент", "Putin"), (r"Гребенкин", "Grebenkin"), (r"Вахрукова|Вахруков", "Vakhrukov")]
log = lib.setup_logging("ru_scrf")


def parse(tid: int) -> Optional[Dict]:
    url = f"{BASE}/news/allnews/{tid}/"
    if not robots_ok(url):
        return None
    html = lib.fetch(url, min_delay=6, retries=2)
    if not html:
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
    st = lib.State("ru_scrf")
    top = st.get("top")
    if not top:
        html = lib.fetch(f"{BASE}/news/allnews/", min_delay=6) or ""
        ids = [int(x) for x in re.findall(r'href="/news/allnews/(\d+)/"', html)]
        top = max(ids)
        st["top"] = top
    old_run, buf = 0, []
    for tid in range(top, 0, -1):
        if st.is_done(str(tid)):
            continue
        r = parse(tid)
        if r is None:
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
