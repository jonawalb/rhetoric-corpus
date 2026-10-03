"""Türkiye MFA (www.mfa.gov.tr): numbered press releases, spokesperson answers to questions (QA-/SC-),
joint statements, press conferences, minister's speeches and interviews, 2021-01-01 -> today.
EN (--lang en -> source tr_mfa_en) and TR (--lang tr -> source tr_mfa_tr) are separate sources.

The section pages are ASP.NET lists (sub.<lang>.mfa?<guid>); entries are either folders (years, or
sub-categories) or articles. Year folders before 2021 are skipped. Paging is a form POST (GridView
"Page$N" postback) done in tr_common.mfa_postback through the lib robots check + delay.

Date = the date printed in the listing title ("No: 187, 1 October 2026, ..."), else the first date in the
article heading, else the first date in the first 300 characters of the article text; entries with no
date are skipped and logged. No article caches are written.

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/tr_mfa.py --lang en|tr [--max N]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import State, fetch, make_id, setup_logging, write_docs  # noqa: E402
from tr_common import MFA, START, date_in_text, mfa_article, mfa_items, mfa_list_all, speaker_in  # noqa: E402

# (section guid, kind). Folder entries below these are walked recursively.
ROOTS = {
    "en": [("248a41bb-6744-4d91-91f7-500bd7a2cac1", "statement"),   # Press Releases & Statements
           ("18f39147-4c68-4352-ad7d-1535f1879b06", "qa"),          # Question & Answer (spokesperson)
           ("b5e241ce-5e51-4ef2-a6e6-f7453d560256", "statement"),   # Joint Declarations
           ("8f787923-31b2-4ba0-92c1-eb548658ce3f", "transcript"),  # Press Conferences
           ("e626bae4-6615-1813-9ab7-4d9e6c71f171", "transcript"),  # Speeches (Minister)
           ("4804c277-892f-4812-9371-1fe393b93a1c", "interview")],  # Interviews (Minister)
    "tr": [("ca94459c-608d-4909-838e-d8cb57fa3ce5", "statement"),   # Açıklamalar (Ortak / Bakanlık / Soruya Cevap)
           ("e5f4c44a-442a-4627-b16d-cb8a42c4feb3", "transcript"),  # Basın Toplantıları
           ("53e304f9-73af-43b3-83b5-3b43db38d51f", "transcript"),  # Konuşmalar
           ("4442b44d-24f2-8401-4120-34834b9a9389", "interview")],  # Mülakatlar
}


def kind_for(title: str, href: str, default: str) -> str:
    if re.match(r"(?i)(QA|SC)-\d+", title) or re.search(r"-sc\.\w\w\.mfa$", href) or "Soruya Cevap" in title:
        return "qa"
    if re.search(r"(?i)press conference|basın toplantısı", title):
        return "transcript"
    if re.search(r"(?i)interview|mülakat|röportaj", title):
        return "interview"
    return default


def walk(lang: str, guid: str, kind: str, out: Dict[str, Dict], depth: int = 0, seen=None) -> None:
    seen = seen if seen is not None else set()
    url = f"{MFA}/sub.{lang}.mfa?{guid}"
    if url in seen or depth > 4:
        return
    seen.add(url)
    for href, title in mfa_list_all(url):
        if href.startswith("/sub."):
            if re.fullmatch(r"(19|20)\d\d", title.strip()) and title.strip() < START[:4]:
                continue
            sub_kind = "qa" if re.search(r"(?i)soruya cevap|question", title) else kind
            walk(lang, href.split("?", 1)[1], sub_kind, out, depth + 1, seen)
            continue
        if not href.endswith(".mfa") or "redirect" in href:
            continue
        full = MFA + href if href.startswith("/") else href
        date = date_in_text(title)
        if date and date < START:
            continue
        if full not in out:
            out[full] = {"title": title, "date": date, "kind": kind_for(title, href, kind)}
    log.info("listed %s: %d items so far", url, len(out))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["en", "tr"], required=True)
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--skip-listings", action="store_true")
    a = ap.parse_args()
    source = f"tr_mfa_{a.lang}"
    st = State(source)
    items: Dict[str, Dict] = dict(st.get("items") or {})
    if not a.skip_listings:
        for guid, kind in ROOTS[a.lang]:
            walk(a.lang, guid, kind, items)
            st["items"] = items
            st.save()
    order = sorted(items.items(), key=lambda kv: kv[1]["date"] or "0000", reverse=True)
    log.info("%d items; %d already done", len(order), sum(st.is_done(u) for u, _ in order))
    n = 0
    for url, meta in order:
        if st.is_done(url):
            continue
        html = fetch(url, min_delay=6)
        if not html:
            log.warning("fetch failed: %s", url)
            continue
        art = mfa_article(html)
        if not art:
            log.warning("no body: %s", url)
            st.mark_done(url)
            continue
        date = meta["date"] or date_in_text(art["title"]) or date_in_text(art["text"][:300])
        if not date:
            log.warning("no reliable date, skipped: %s | %s", meta["title"][:100], url)
            st.mark_done(url)
            continue
        if date < START:
            st.mark_done(url)
            continue
        title = art["title"] or meta["title"]
        kind = meta["kind"]
        speaker = speaker_in(title) if kind in ("qa", "transcript", "interview") else None
        row = {"id": make_id(source, url), "country": "TR", "source": source, "outlet": "official",
               "org": "MFA", "lang": a.lang, "date": date, "url": url, "title": title, "speaker": speaker,
               "kind": kind, "text": art["text"], "via": "direct"}
        write_docs("TR", source, [row])
        st.mark_done(url)
        n += 1
        if n % 10 == 0:
            st.save()
            log.info("%d new docs (at %s)", n, date)
        if a.max and n >= a.max:
            break
    st.save()
    log.info("done: %d new docs", n)


if __name__ == "__main__":
    log = setup_logging(f"tr_mfa_{sys.argv[sys.argv.index('--lang') + 1]}" if "--lang" in sys.argv else "tr_mfa")
    main()
