"""Russian Ministry of Defence news (function.mil.ru/news_page/country/more.htm?id=N@egNews) via Wayback.

mil.ru and function.mil.ru do not answer from this host (timeouts / connection failures on 2026-10-02),
so only Wayback copies are used. The old function.mil.ru news archive is captured 2021 -> early 2025;
the new mil.ru site (UUID URLs, 2025->) has only a handful of captures. Since 2026-10-02 EVERY captured news
item dated >= 2021 with > 80 chars of text is stored (`sample: all`), daily SMO combat reports included
(~49k captured ids, a few hundred MB of text). Rows stored earlier under the old nuclear KEYWORD prefilter
have no `sample` field; ids checked and rejected under that rule are re-fetched (state key "all:<id>").
Rows -> docs/RU/mil_ru.jsonl.

    uv run --project ~/Projects/rhetoric-corpus python collectors/ru_mil.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from ru_common import ctext, dmy  # noqa: E402

START = "2021-01-01"
log = lib.setup_logging("ru_mil")


def captures(st: lib.State) -> List[tuple]:
    if st.get("cdx"):
        return [tuple(x) for x in st["cdx"]]
    q = ("https://web.archive.org/cdx/search/cdx?url=function.mil.ru/news_page/country/more.htm&matchType=prefix"
         "&from=2021&collapse=urlkey&fl=timestamp,original&filter=statuscode:200")
    body = lib.fetch(q, min_delay=8, timeout=300, retries=6) or ""
    caps = {}
    for ln in body.splitlines():
        parts = ln.split()
        m = re.search(r"more\.htm\?id=(\d+)@egNews&?$", parts[1]) if len(parts) == 2 else None
        if m:
            caps.setdefault(int(m.group(1)), (parts[0], parts[1]))
    out = [(n, ts, orig) for n, (ts, orig) in sorted(caps.items(), reverse=True)]
    if out:
        st["cdx"] = out
        st.save()
    log.info("CDX: %d MoD news ids", len(out))
    return out


def main() -> None:
    st = lib.State("ru_mil")
    buf = []
    have = lib.existing_ids(lib.docs_path("RU", "mil_ru"))
    for n, ts, orig in captures(st):
        key = f"all:{n}"  # bare str(n) keys = checked under the old keyword rule
        if st.is_done(key) or lib.make_id("mil_ru", str(n)) in have:
            continue
        html = lib.fetch(f"https://web.archive.org/web/{ts}id_/{orig}", min_delay=8, timeout=90)
        if html is None:
            log.warning("capture failed %s (status %s)", n, lib.fetch.last.get("status"))
            continue
        m = re.search(r'<span class="gray"[^>]*>([\d.]+)[^<]*</span>\s*<h1>(.*?)</h1>(.*?)<div style="margin:10px 0; text-align:right;">',
                      html, re.S)
        st.mark_done(key)
        if not m:
            continue
        d, title = dmy(m.group(1)), ctext(m.group(2))
        body = re.sub(r"<(style|script)\b.*?</\1>", " ", m.group(3), flags=re.S)
        text = re.sub(r"Для просмотра видео необходим.{0,40}?Flash Player.{0,30}?Flash Player\s*", "", ctext(body))
        lines = [ln for ln in text.split("\n") if ln.strip() and ln.strip() != title]
        text = "\n".join(lines).strip()
        if d and d >= START and len(text) > 80:
            url = f"https://function.mil.ru/news_page/country/more.htm?id={n}@egNews"
            buf.append({"id": lib.make_id("mil_ru", str(n)), "country": "RU", "source": "mil_ru", "outlet": "official",
                        "org": "MoD", "lang": "ru", "date": d, "url": url, "title": title, "speaker": None,
                        "kind": "statement", "text": text, "via": "wayback", "wayback_ts": ts, "sample": "all"})
        if len(buf) >= 5 or n % 20 == 0:
            if buf:
                added, total = lib.write_docs("RU", "mil_ru", buf)
                log.info("wrote %d (total %d), at %s", added, total, buf[-1]["date"])
                buf = []
            st.save()
    if buf:
        lib.write_docs("RU", "mil_ru", buf)
    st.save()


if __name__ == "__main__":
    main()
