"""Taiwan (ROC) Executive Yuan news releases, 2021-01-01 -> today, newest first.

  --lang zh  www.ey.gov.tw      本院新聞 › 一般新聞 (/Page/A2EC1FEF9BC39AD0; items /Page/9277F759E41CCD91/<uuid>)
  --lang en  english.ey.gov.tw  Executive Yuan Press Releases (/Page/5A898E83D438145A; items /Page/61BF20C3E89B856/<uuid>)

Listing pages (?page=N&PS=100) carry title, item link and date: ROC dates on the Chinese site ("115-10-01" =
2026-10-01, converted with tw_common.roc_date), Gregorian on the English site. Video-only items (影音新聞) are
not in the 一般新聞 listing. Article body = div.words_content (zh) / div.words (en), cut at 相關連結/相關檔案.
Speaker null (releases paraphrase the premier and ministers in the third person). Resumable via
state/tw_ey_<lang>.json; pages are not cached on disk.

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/tw_ey.py --lang zh|en [--max N]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import State, clean_html, fetch, make_id, setup_logging, write_docs  # noqa: E402
from tw_common import START, ad_date, roc_date  # noqa: E402

SITES = {"zh": ("https://www.ey.gov.tw", "/Page/A2EC1FEF9BC39AD0", "9277F759E41CCD91", roc_date),
         "en": ("https://english.ey.gov.tw", "/Page/5A898E83D438145A", "61BF20C3E89B856", ad_date)}
END_MARKS = ("相關連結", "相關檔案", "<footer", 'id="accesskey_r"')


def parse_listing(html: str, item_key: str, to_date) -> List[Tuple[str, str, str]]:
    """[(date YYYY-MM-DD, uuid, title)]; rows without a parsable date are dropped."""
    out = []
    for blk in re.split(r"<li class=\"new_", html)[1:]:
        m = re.search(rf'href="/Page/{item_key}/([0-9a-f-]{{36}})"', blk)
        d = re.search(r'<span class="date">\s*([^<]+?)\s*</span>', blk)
        t = re.search(r'<div class="title">(.*?)</div>', blk, re.S)
        if not (m and d):
            continue
        date = to_date(d.group(1))
        if date:
            out.append((date, m.group(1), clean_html(t.group(1)) if t else ""))
    return out


def parse_article(html: str, lang: str) -> Optional[Dict]:
    if lang == "zh":
        m = re.search(r'<span class="h2">\s*(.*?)</span>', html, re.S)
        i = html.find('class="words_content')
    else:
        m = re.search(r'<h2 class="main_h2">\s*(.*?)</h2>', html, re.S)
        i = html.find('<div class="words graybg')
    if i < 0:
        return None
    ends = [j for j in (html.find(e, i) for e in END_MARKS) if j > 0]
    body = html[i:min(ends) if ends else i + 200000]
    body = re.sub(r"<!--.*?(?:-->|$)", " ", body[body.find(">") + 1:], flags=re.S)
    text = re.sub(r"\n(?:Close|關閉)\s*$", "", clean_html(body)).strip()
    return {"title": clean_html(m.group(1)) if m else "", "text": text} if len(text) > 40 else None


def page_date(html: str) -> Optional[str]:
    """The date printed on the article page (日期：115-10-01 / Date: 2026-09-24)."""
    m = re.search(r'class="date_style2">\s*<span>\s*(?:日期：|Date:(?:&nbsp;|\s)*)([^<]+)</span>', html)
    return (roc_date(m.group(1)) or ad_date(m.group(1))) if m else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["zh", "en"], required=True)
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--skip-listings", action="store_true")
    a = ap.parse_args()
    source = f"tw_ey_{a.lang}"
    log = setup_logging(source)
    base, listing, item_key, to_date = SITES[a.lang]
    st = State(source)
    items: Dict[str, Dict] = dict(st.get("items") or {})
    page = 1
    while not a.skip_listings:
        html = fetch(f"{base}{listing}?page={page}&PS=100&", min_delay=4)
        rows = parse_listing(html, item_key, to_date) if html else []
        if not rows:
            log.info("listing page %d: no rows, stop", page)
            break
        new = 0
        for date, uid, title in rows:
            if date >= START and uid not in items:
                items[uid] = {"date": date, "title": title}
                new += 1
        newest = max(r[0] for r in rows)
        log.info("listing page %d: %d rows, %d new, %s..%s", page, len(rows), new, min(r[0] for r in rows), newest)
        st["items"] = items
        st.save()
        if newest < START:
            break
        page += 1
    order = sorted(items.items(), key=lambda kv: kv[1]["date"], reverse=True)
    log.info("%d items since %s; %d already done", len(order), START, sum(st.is_done(u) for u, _ in order))
    n = 0
    for uid, meta in order:
        if st.is_done(uid):
            continue
        url = f"{base}/Page/{item_key}/{uid}"
        html = fetch(url, min_delay=4)
        if not html:
            log.warning("fetch failed: %s", url)
            continue
        art = parse_article(html, a.lang)
        if not art:
            log.warning("no body: %s", url)
            st.mark_done(uid)
            continue
        pd = page_date(html)
        if pd and pd != meta["date"]:
            log.warning("date mismatch listing %s vs page %s: %s (page date used)", meta["date"], pd, url)
        row = {"id": make_id(source, uid), "country": "TW", "source": source, "outlet": "official",
               "org": "Executive Yuan", "lang": a.lang, "date": pd or meta["date"], "url": url,
               "title": art["title"] or meta["title"], "speaker": None, "kind": "statement", "text": art["text"],
               "via": fetch.last.get("via") or "direct"}
        write_docs("TW", source, [row])
        st.mark_done(uid)
        n += 1
        if n % 10 == 0:
            st.save()
            log.info("%d new docs (at %s)", n, meta["date"])
        if a.max and n >= a.max:
            break
    st.save()
    log.info("done: %d new docs", n)


if __name__ == "__main__":
    main()
