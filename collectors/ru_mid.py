"""Russian MFA (mid.ru): spokesperson briefings / answers / comments / statements, Lavrov speeches and
interviews, deputy ministers' (Ryabkov etc.) speeches -- Russian (docs/RU/mid_ru.jsonl) and English
(docs/RU/mid_en.jsonl), 2021-01-01 -> today.

Phase 1 walks each section's listing (?PAGEN_1=N, allowed by robots) back to 2021-01-01 and stores the
item list in state; phase 2 fetches items newest-first (2024+ first, then 2021-2023).
Resumable: re-run the same command. Polite: mid.ru is spaced >= 10 s (lib.HOST_DELAY).

    uv run --project ~/Projects/rhetoric-corpus python collectors/ru_mid.py [--list-only] [--limit N]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
from ru_common import balanced_div, ctext, dmy, robots_ok, run_queue  # noqa: E402

BASE = "https://mid.ru"
START = "2021-01-01"
# (lang, path, default kind, default speaker)
SECTIONS = [
    ("ru", "/ru/press_service/spokesman/briefings/", "briefing", "Zakharova"),
    ("en", "/en/press_service/spokesman/briefings/", "briefing", "Zakharova"),
    ("ru", "/ru/press_service/minister_speeches/", "transcript", "Lavrov"),
    ("en", "/en/press_service/minister_speeches/", "transcript", "Lavrov"),
    ("ru", "/ru/press_service/deputy_ministers_speeches/", "transcript", None),
    ("en", "/en/press_service/deputy_ministers_speeches/", "transcript", None),
    ("ru", "/ru/press_service/spokesman/answers/", "qa", "Zakharova"),
    ("en", "/en/press_service/spokesman/answers/", "qa", "Zakharova"),
    ("ru", "/ru/press_service/spokesman/kommentarii/", "statement", None),
    ("en", "/en/press_service/spokesman/comments/", "statement", None),
    ("ru", "/ru/press_service/spokesman/official_statement/", "statement", None),
    ("en", "/en/press_service/spokesman/official_statement/", "statement", None),
]
SPEAKERS = [  # (regex on title, canonical name)
    (r"Захаров|Zakharova", "Zakharova"), (r"Лавров|Lavrov", "Lavrov"), (r"Рябков|Ryabkov", "Ryabkov"),
    (r"Грушко|Grushko", "Grushko"), (r"Галузин|Galuzin", "Galuzin"), (r"Руденко|Rudenko", "Rudenko"),
    (r"Вершинин|Vershinin", "Vershinin"), (r"Богданов|Bogdanov", "Bogdanov"), (r"Панкин|Pankin", "Pankin"),
    (r"Сыромолотов|Syromolotov", "Syromolotov"), (r"Пантелеев|Panteleyev", "Panteleyev"),
    (r"Алимов|Alimov", "Alimov"), (r"Небензя|Nebenzia", "Nebenzia"), (r"Ульянов|Ulyanov", "Ulyanov"),
    (r"Дарчиев|Darchiev", "Darchiev"), (r"Антонов|Antonov", "Antonov"), (r"Федоров|Fedorov", "Fedorova"),
]
log = lib.setup_logging("ru_mid")


def speaker_of(title: str, default: Optional[str]) -> Optional[str]:
    hits = [(m.start(), name) for pat, name in SPEAKERS for m in [re.search(pat, title)] if m]
    return min(hits)[1] if hits else default


def kind_of(title: str, default: str) -> str:
    t = title.lower()
    if re.search(r"интервью|interview", t):
        return "interview"
    if re.search(r"^статья|article by|^article", t):
        return "article"
    if re.search(r"брифинг|briefing", t):
        return "briefing"
    if re.search(r"ответ\w* на вопрос|answers? to (?:a )?(?:media )?question|replies? to", t):
        return "qa"
    if re.search(r"^заявлени|^statement|^комментари|^comment", t):
        return "statement"
    return default


EN_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                           "september", "october", "november", "december"], 1)}


def any_date(s: str) -> Optional[str]:
    """'20.08.2026' or '30 September 2026' -> ISO date."""
    d = dmy(s)
    if d:
        return d
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", s or "")
    if m and m.group(2).lower() in EN_MONTHS:
        return f"{m.group(3)}-{EN_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    return None


ITEM_RE = re.compile(r"https?://(?:www\.)?mid\.ru(/(?:ru|en)/[a-z_/\-]+/)(\d{6,8})/?$")


def item_for(url: str) -> Optional[Dict]:
    """Queue item for an article URL that belongs to one of SECTIONS, else None."""
    m = ITEM_RE.match(url)
    if not m:
        return None
    for lang, path, kind, spk in SECTIONS:
        if m.group(1) == path:
            return {"url": f"{BASE}{path}{m.group(2)}/", "id": int(m.group(2)), "lang": lang, "kind": kind,
                    "speaker": spk, "section": path, "date": "", "title": ""}
    return None


def discover(st: lib.State) -> Dict[str, Dict]:
    """Article URLs per section from (a) the first listing page (robots forbids ?PAGEN_ paging) and
    (b) the Wayback CDX URL index (URL list only; the articles themselves are fetched from mid.ru)."""
    items: Dict[str, Dict] = st.get("items") or {}
    for lang, path, kind, spk in SECTIONS:
        html = lib.fetch(BASE + path, min_delay=10) if robots_ok(BASE + path) else None
        n0 = len(items)
        for href in re.findall(r'class="announce__link[^"]*" href="([^"]+)"', html or ""):
            it = item_for(BASE + href if href.startswith("/") else href)
            if it and it["url"] not in items:
                items[it["url"]] = it
        if not st.get(f"cdx:{path}"):
            q = ("https://web.archive.org/cdx/search/cdx?url=mid.ru" + path +
                 "&matchType=prefix&collapse=urlkey&fl=original&filter=statuscode:200")
            body = lib.fetch(q, min_delay=5, timeout=180) or ""
            for line in body.splitlines():
                it = item_for(line.strip().replace(":80/", "/"))
                if it and it["url"] not in items:
                    items[it["url"]] = it
            st[f"cdx:{path}"] = bool(body)
        log.info("discover %s: +%d (total %d)", path, len(items) - n0, len(items))
        st["items"] = items
        st.save()
    return items


def strip_toc(inner: str) -> str:
    """Drop the briefing table of contents: the 'Содержание'/'Contents' heading and every <li>/<p>
    that links to an in-page anchor (href="#N"), plus the bare "Из ответов на вопросы" TOC header."""
    inner = re.sub(r"<h2[^>]*>(?:(?!</h2>).){0,400}?(Содержание|Contents)(?:(?!</h2>).)*</h2>", " ", inner[:8000], count=1,
                   flags=re.S | re.I) + inner[8000:]
    inner = re.sub(r"<(li|p)\b[^>]*>(?:(?!</\1>).)*?href=\"#[^\"]*\"(?:(?!</\1>).)*</\1>", " ", inner, flags=re.S | re.I)
    return inner


def parse(it: Dict) -> Optional[List[Dict]]:
    url = it["url"]
    if not robots_ok(url):
        return []
    rid = re.search(r"/(\d+)/?$", url)
    cache = lib.RAW / "mid" / it["lang"] / f"{rid.group(1) if rid else lib.make_id('x', url).split(':')[1]}.html"
    html = lib.fetch(url, min_delay=10, cache=cache)
    if not html:
        log.warning("fetch failed %s (status %s)", url, lib.fetch.last.get("status"))
        return None
    inner = balanced_div(html, 'class="text article-content"')
    if inner is None:
        log.warning("no article body %s", url)
        return []
    text = ctext(strip_toc(inner))
    text = re.sub(r"\n?\s*(Назад к оглавлению|Back to (?:the )?(?:table of )?contents)\s*", "\n", text, flags=re.I).strip()
    tm = re.search(r'class="photo-content__title"[^>]*>(.*?)</', html, re.S)
    title = lib.clean_html(tm.group(1)) if tm else it["title"]
    dm = re.search(r'class="photo-content__date"[^>]*>([^<]+)<', html)
    d = any_date(dm.group(1)) if dm else None
    if len(text) < 40 or not d:
        log.info("no text/date %s", url)
        return []
    if d < START:
        return []
    for href in re.findall(r'href="(/(?:ru|en)/[a-z_/\-]+/\d{6,8}/)"', html):
        new = item_for(BASE + href)
        if new and new["url"] not in NEW and new["url"] not in KNOWN:
            NEW[new["url"]] = new
    src = "mid_ru" if it["lang"] == "ru" else "mid_en"
    return [{"id": lib.make_id(src, rid.group(1) if rid else url), "country": "RU", "source": src,
             "outlet": "official", "org": "MFA", "lang": it["lang"], "date": d, "url": url,
             "title": title, "speaker": speaker_of(title, it["speaker"]), "kind": kind_of(title, it["kind"]),
             "text": text, "via": lib.fetch.last.get("via") or "direct", "section": it.get("section")}]


NEW: Dict[str, Dict] = {}
KNOWN: Dict[str, Dict] = {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    st = lib.State("ru_mid")
    KNOWN.update(discover(st))
    if a.discover_only:
        return
    # rounds: fetch newest-first by article id (ids grow with time across sections), then follow
    # same-section links found on fetched pages (e.g. "previous briefing") until nothing new appears
    for rnd in range(50):
        queue = sorted((i for i in KNOWN.values() if not st.is_done(i["url"])), key=lambda i: -i["id"])
        if a.limit:
            queue = queue[:a.limit]
        if not queue:
            break
        log.info("round %d: %d items to fetch", rnd, len(queue))
        run_queue(queue, st, parse, "RU", lambda r: r["source"])
        KNOWN.update(NEW)
        NEW.clear()
        st["items"] = KNOWN
        st.save()
        if a.limit:
            break


if __name__ == "__main__":
    main()
