"""Scan docs/RU/*.jsonl for plutonium-pit terms; print JSON lines of hits with sentence snippets.

    python3 scripts/plutonium_scan_ru.py > /tmp/hits.jsonl
Groups: pit = pit/core production terms; plutonium = any other plutonium mention; degrade = 'разучил'/
'деградация ядерной инфраструктуры' (only when in the same doc as a nuclear term)."""
import glob
import json
import re

PIT = re.compile(r"плутониев\w* (?:сердечник|ядр)|ядерн\w* сердечник|сердечник\w* (?:для|из) плутони|«питы?»|"
                 r"\bW87-1\b|Лос-Аламос|Саванн\w*[- ]Ривер|Роки[- ]Флэтс|\bNNSA\b|Национальн\w* управлени\w* по ядерной безопасности|"
                 r"plutonium (?:pit|core)s?|\bpit production|war[- ]reserve pit|\bPF-4\b|diamond stamp|Los Alamos|Savannah River|Rocky Flats", re.I)
PLU = re.compile(r"плутони|plutonium", re.I)
DEG = re.compile(r"разучил\w*[^.!?]{0,80}(?:ядерн|бомб|боеголов|оружи)|деградаци\w* ядерн\w* (?:инфраструктур|комплекс)|can'?t (?:make|build) (?:nuclear )?(?:bombs|weapons)", re.I)
NUC = re.compile(r"ядерн|nuclear", re.I)


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?…»\"])\s+|\n", text) if s.strip()]


for f in sorted(glob.glob("docs/RU/*.jsonl")):
    for line in open(f, encoding="utf-8"):
        r = json.loads(line)
        t = (r.get("title") or "") + "\n" + r["text"]
        grp = "pit" if PIT.search(t) else ("plutonium" if PLU.search(t) else ("degrade" if DEG.search(t) and NUC.search(t) else None))
        if not grp:
            continue
        rx = {"pit": PIT, "plutonium": PLU, "degrade": DEG}[grp]
        snips = [s[:400] for s in sentences(t) if rx.search(s)]
        print(json.dumps({"group": grp, "source": r["source"], "org": r.get("org"), "date": r["date"],
                          "speaker": r.get("speaker"), "title": r.get("title"), "url": r["url"], "snips": snips[:6]},
                         ensure_ascii=False))
