"""Taiwan Presidential Office news releases (總統府新聞 / News releases), 2021-01-01 -> today, newest first.

    uv run --project ~/Projects/rhetoric-corpus python collectors/tw_president.py --lang zh|en [--max N]

URLs come from the site's yearly sitemaps (www|english.president.gov.tw/sitemap-news-YYYY.xml; robots.txt
allows everything). Article pages carry the publication date in div.pageDate1 (zh: ROC date "113年01月08日",
converted with tw_common.roc_date; en: "2024-12-05"). The JSON-LD datePublished on these pages is a template
value (2017) and is NOT used. Body = div.article1 (ckeditor block).

speaker = the sitting president (Tsai Ing-wen before 2024-05-20, Lai Ching-te from then) when the release
reports the president speaking (zh 總統表示/致詞/指出…, not 副總統; en "President Tsai/Lai … said/remarks"),
else null. kind = transcript for addresses/speeches/remarks (title), interview for interviews, else statement.
Sources tw_president_zh / tw_president_en. Resumable: state/tw_president_<lang>.json. No on-disk page cache.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import State, clean_html, make_id, setup_logging, write_docs  # noqa: E402
from tw_common import START, ad_date, get, president_at, roc_date, sitemap_locs  # noqa: E402

HOSTS = {"zh": "https://www.president.gov.tw", "en": "https://english.president.gov.tw"}
TRANSCRIPT_ZH = re.compile(r"就職演說|國慶.{0,4}(?:演說|致詞|談話)|談話全文|致詞全文|演說|演講|新年談話|記者會")
TRANSCRIPT_EN = re.compile(r"(?i)inaugural address|national day address|new year'?s? (?:address|remarks)|"
                           r"\baddress\b|\bspeech\b|press conference|delivers remarks|full text")
INTERVIEW = re.compile(r"(?i)專訪|受訪|interview")


def speaker_for(lang: str, date: str, text: str) -> Optional[str]:
    """The sitting president if the release reports him/her speaking, else None."""
    name = president_at(date)
    if lang == "zh":
        hit = re.search(r"(?<!副)總統(?:今.{0,12})?(?:致詞|表示|指出|強調|說|談話|期盼|提到|呼籲|重申|演說)", text)
    else:
        sur = name.split()[0]
        hit = re.search(rf"(?<!Vice )President {sur}(?: [A-Z][a-z-]+)?[^.]{{0,120}}?"
                        r"\b(?:said|stated|remarked|noted|emphasized|stressed|remarks|address|delivered|pointed out)",
                        text) or re.search(r"(?<!Vice )President['’]s remarks|translation of (?:the )?President",
                                           text)
    return name if hit else None


def parse(html: str, lang: str) -> Optional[Dict]:
    m = re.search(r'<div class="pageDate1">\s*<span class="date">([^<]+)</span>', html)
    raw_date = m.group(1).strip() if m else ""
    date = roc_date(raw_date) if lang == "zh" else ad_date(raw_date)
    t = re.search(r'<div class="pageTitle1">(.*?)</div>', html, re.S)
    t2 = re.search(r'<div class="pageTitle2">(.*?)</div>', html, re.S)
    b = re.search(r"<!-- ckeditor編輯start -->(.*?)<!-- ckeditor編輯end -->", html, re.S) \
        or re.search(r'<div class="article1">(.*?)<!-- 相關檔案 -->', html, re.S)
    if not (date and t and b):
        return None
    cat = re.search(r'"position":3,"name":"([^"]*)"', html)
    return {"date": date, "raw_date": raw_date, "title": clean_html(t.group(1)),
            "subtitle": clean_html(t2.group(1)) if t2 else "", "text": clean_html(b.group(1)),
            "category": cat.group(1) if cat else None}


def list_urls(lang: str, log) -> List[str]:
    host = HOSTS[lang]
    idx = get(f"{host}/sitemap_index.xml")
    if not idx:
        raise SystemExit("sitemap index unavailable")
    maps = [u for u, _ in sitemap_locs(idx)
            if (m := re.search(r"sitemap-news-(\d{4})\.xml$", u)) and int(m.group(1)) >= int(START[:4])]
    urls: List[str] = []
    for sm in sorted(maps, reverse=True):
        xml = get(sm)
        if not xml:
            log.warning("sitemap failed: %s", sm)
            continue
        locs = [u for u, _ in sitemap_locs(xml) if re.search(r"/NEWS/\d+$", u)]
        log.info("%s: %d urls", sm, len(locs))
        urls += locs
    # newest first by numeric id
    return sorted(set(urls), key=lambda u: int(u.rsplit("/", 1)[1]), reverse=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["zh", "en"], required=True)
    ap.add_argument("--max", type=int, default=0, help="stop after N new documents (0 = all)")
    ap.add_argument("--dry-run", action="store_true", help="parse and print, do not write")
    a = ap.parse_args()
    source = f"tw_president_{a.lang}"
    log = setup_logging(source)
    st = State(source)
    urls = list_urls(a.lang, log)
    log.info("%d urls; %d already done", len(urls), sum(st.is_done(u) for u in urls))
    n = 0
    for url in urls:
        if st.is_done(url):
            continue
        html = get(url)
        if not html:
            log.warning("fetch failed: %s", url)
            continue
        art = parse(html, a.lang)
        if not art:
            log.warning("unparsed (no date/title/body): %s", url)
            st.mark_done(url)
            continue
        if art["date"] < START:
            st.mark_done(url)
            continue
        if len(art["text"]) < 40:
            log.info("skip near-empty (%d chars): %s", len(art["text"]), url)
            st.mark_done(url)
            continue
        title = art["title"]
        kind = ("interview" if INTERVIEW.search(title) else
                "transcript" if (TRANSCRIPT_ZH if a.lang == "zh" else TRANSCRIPT_EN).search(title) else "statement")
        text = (art["subtitle"] + "\n" + art["text"]).strip() if art["subtitle"] else art["text"]
        row = {"id": make_id(source, url.rsplit("/", 1)[1]), "country": "TW", "source": source, "outlet": "official",
               "org": "Presidential Office", "lang": a.lang, "date": art["date"], "url": url, "title": title,
               "speaker": speaker_for(a.lang, art["date"], art["text"]), "kind": kind, "text": text,
               "via": "direct", "category": art["category"]}
        if a.dry_run:
            print(row["date"], art["raw_date"], row["kind"], row["speaker"], "|", title, "|", text[:200].replace("\n", " "))
        else:
            write_docs("TW", source, [row])
            st.mark_done(url)
        n += 1
        if n % 10 == 0 and not a.dry_run:
            st.save()
            log.info("%d new docs (at %s)", n, art["date"])
        if a.max and n >= a.max:
            break
    if not a.dry_run:
        st.save()
    log.info("done: %d new docs", n)


if __name__ == "__main__":
    main()
