"""Scan the documents of the given countries (docs-parts/ + docs/<CC>/*.jsonl) for the plutonium-pit test-case
terms (BRIEF.md) and print a Markdown report.

Run: uv run --project ~/Projects/rhetoric-corpus python scripts/plutonium_scan.py KP BY US PK IN
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
sys.path.insert(0, str(DOCS.parent / "scripts"))
import segments  # noqa: E402
TERMS = {
    "EN plutonium pit/core": r"plutonium (?:pits?|cores?)",
    "EN pit production / war reserve pit": r"pit production|war[- ]reserve pits?",
    "EN PF-4 / diamond stamp": r"\bpf-4\b|diamond stamp",
    "EN W87-1": r"w87-1",
    "EN Los Alamos": r"los alamos",
    "EN Savannah River": r"savannah river",
    "EN Rocky Flats": r"rocky flats",
    "EN NNSA": r"\bnnsa\b|national nuclear security administration",
    "EN weapons-grade plutonium / PMDA": r"weapons?-grade plutonium|plutonium (?:management and )?disposition",
    "EN plutonium (any)": r"plutonium",
    "RU плутониевый сердечник/ядро": r"плутониев\w* (?:сердечник|ядр)\w*|ядерн\w* сердечник\w*",
    "RU «питы»": r"«пит\w*»|\bпиты\b",
    "RU Лос-Аламос / Саванна-Ривер / Роки-Флэтс": r"лос-аламос\w*|саванна-ривер|роки-флэтс",
    "RU NNSA (рус.)": r"национальн\w* управлени\w* по ядерной безопасности",
    "RU оружейный плутоний / утилизация": r"оружейн\w* плутони\w*|утилизаци\w* плутони\w*",
    "RU разучились / деградация ядерной инфраструктуры": r"разучил\w*|деградаци\w* ядерн\w* инфраструктур\w*",
    "RU модернизация ядерного арсенала США": r"модернизаци\w* ядерн\w* арсенал\w*|ядерн\w* арсенал\w* сша",
    "RU плутоний (any)": r"плутони\w*",
}


def main(countries: list) -> None:
    pats = {k: re.compile(v, re.I) for k, v in TERMS.items()}
    print("| country | source | docs | date range |\n|---|---|---|---|")
    hits = []
    per_source: dict = {}
    for _, d in segments.iter_docs(DOCS.parent, countries):  # sealed parts (docs-parts/) + active docs/<CC>/*.jsonl
        cc, src = d["country"].upper(), d["source"]
        per_source.setdefault((cc, src), []).append(d["date"])
        blob = (d.get("title") or "") + "\n" + d["text"]
        for k, p in pats.items():
            for m in p.finditer(blob):
                s = max(0, m.start() - 140)
                hits.append((k, cc, src, d["date"], d["url"], blob[s:m.end() + 140].replace("\n", " ")))
                break
    for (cc, src), dates in sorted(per_source.items(), key=lambda kv: ([c.upper() for c in countries].index(kv[0][0]), kv[0][1])):
        print(f"| {cc} | {src} | {len(dates)} | {min(dates)} → {max(dates)} |")
    print("\n## Hits by term\n")
    c = Counter((h[0], h[1]) for h in hits)
    print("| term | country | docs |\n|---|---|---|")
    for (k, cc), v in sorted(c.items()):
        print(f"| {k} | {cc} | {v} |")
    if not c:
        print("| (none) | | 0 |")
    print("\n## Matches (term, country, source, date, url, context ≤300 chars)\n")
    for h in hits:
        print(f"- **{h[0]}** — {h[1]} {h[2]} {h[3]} <{h[4]}>: …{h[5][:300]}…")


if __name__ == "__main__":
    main(sys.argv[1:] or ["KP", "BY", "US", "PK", "IN"])
