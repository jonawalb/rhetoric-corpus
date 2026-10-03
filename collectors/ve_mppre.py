"""Venezuela MFA (Ministerio del Poder Popular para Relaciones Exteriores, mppre.gob.ve) publications
-> docs/VE/mppre_es.jsonl (Spanish).

Every publication lives at https://mppre.gob.ve/publicacion/<id> (sequential ids, 1 ≈ 2017, ~8,400 by
Oct 2026; the slug after the id is optional). The collector walks ids downward from the newest id linked on
the home page and stops after 150 consecutive existing items dated before --since. Ids that render an empty
template (deleted/unpublished) are skipped. Page date = the dd-mm-yyyy line under the byline; title = h4.sub-title;
body = text between the date and the tag/share block.

Section: ids found on the "Comunicados" (categoria/2) and "Discursos" (categoria/6) listings are tagged
section=comunicado / discurso (kind statement / transcript); everything else is section=noticia, kind article.

Run: uv run --project ~/Projects/rhetoric-corpus python collectors/ve_mppre.py [--max N]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import State, clean_html, fetch, make_id, setup_logging, write_docs  # noqa: E402

SOURCE = "mppre_es"
BASE = "https://mppre.gob.ve"
START = "2021-01-01"
DELAY = 4.0   # robots.txt: "Disallow:" (nothing disallowed)
CATS = {"2": ("comunicado", "statement"), "6": ("discurso", "transcript")}
log = setup_logging(SOURCE)


def parse(html: str) -> Optional[Dict]:
    t = re.search(r'<h4 class="sub-title[^"]*"[^>]*>(.*?)</h4>', html, re.S)
    # the date under the byline is either "<i class="la la-calendar"></i> dd-mm-yyyy" or a plain "dd/mm/yyyy" span
    a = html.find('class="author-side')
    d = re.compile(r">\s*(\d{2})[-/](\d{2})[-/](\d{4})\s*<").search(html, a) if a >= 0 else None
    if not (t and d):
        return None
    title = clean_html(t.group(1))
    date = f"{d.group(3)}-{d.group(2)}-{d.group(1)}"
    body = html[d.end() - 1:]
    # body ends where the tags / share / related block starts
    for end in ("Descargar Comunicado", "Descargar PDF", '<div class="btm-share-post', '<div class="btm-tags',
                ">Compartir<", "Conecta con nosotros"):
        k = body.find(end)
        if k > 0:
            body = body[:k]
    text = clean_html(body)
    text = re.sub(r"^(?:Fot[óo]grafo|Foto|Fotos)\s*:[^\n]*\n", "", text).strip()   # photo credit line
    return {"title": title, "date": date, "text": text} if title and len(text) > 60 else None


def section_ids(st: State) -> Dict[str, str]:
    tags: Dict[str, str] = dict(st.get("sections") or {})
    for cat, (sec, _) in CATS.items():
        page = 1
        while True:
            html = fetch(f"{BASE}/categoria/{cat}" + (f"?page={page}" if page > 1 else ""), min_delay=DELAY)
            ids = set(re.findall(r'href="https://mppre\.gob\.ve/publicacion/(\d+)-', html or ""))
            new = [i for i in ids if i not in tags]
            for i in new:
                tags[i] = sec
            if not html or not new:
                break
            page += 1
        log.info("categoria/%s (%s): %d ids tagged after %d pages", cat, sec, sum(v == sec for v in tags.values()), page)
    st["sections"] = tags
    st.save()
    return tags


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--since", default=START)
    a = ap.parse_args()
    st = State(SOURCE)
    home = fetch(BASE + "/", min_delay=DELAY) or ""
    top = max([int(x) for x in re.findall(r"/publicacion/(\d+)", home)] + [int(st.get("top") or 0)])
    if not top:
        raise SystemExit("could not read the newest publication id from the home page")
    st["top"] = top
    tags = section_ids(st)
    log.info("walking ids %d -> down (stop after 150 consecutive items before %s)", top, a.since)
    n, old_run = 0, 0
    for pid in range(top, 0, -1):
        url = f"{BASE}/publicacion/{pid}"
        if st.is_done(url):
            d = (st.get("dates") or {}).get(str(pid))
            if d:
                old_run = old_run + 1 if d < a.since else 0
            if old_run >= 150:
                break
            continue
        html = fetch(url, min_delay=DELAY)
        if html is None:
            log.warning("fetch failed (not marked done): %s", url)
            continue
        art = parse(html)
        st.mark_done(url)
        if not art:
            log.info("no publication at id %d (empty template or no date), skipped", pid)
            continue
        st.data.setdefault("dates", {})[str(pid)] = art["date"]
        if art["date"] < a.since:
            old_run += 1
            if old_run >= 150:
                log.info("150 consecutive items before %s at id %d; stopping", a.since, pid)
                break
            continue
        old_run = 0
        sec = tags.get(str(pid), "noticia")
        if sec == "noticia" and (re.match(r"(?i)\s*comunicado", art["title"]) or "Descargar Comunicado" in html):
            sec = "comunicado"
        kind = {"comunicado": "statement", "discurso": "transcript"}.get(sec, "article")
        row = {"id": make_id(SOURCE, str(pid)), "country": "VE", "source": SOURCE, "outlet": "official",
               "org": "MPPRE (MFA)", "lang": "es", "date": art["date"], "url": url, "title": art["title"],
               "speaker": None, "kind": kind, "text": art["text"], "via": fetch.last.get("via") or "direct",
               "section": sec}
        n += write_docs("VE", SOURCE, [row])[0]
        if n and n % 20 == 0:
            st.save()
            log.info("%d new docs (id %d, %s)", n, pid, art["date"])
        if a.max and n >= a.max:
            break
    st.save()
    log.info("done: %d new docs", n)


if __name__ == "__main__":
    main()
