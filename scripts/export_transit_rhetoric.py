"""Export PRC official rhetoric around Taiwan Strait transits (Calibrated Coercion, paper #21): a tidy daily dataset.

For every transit (one transiting state on one date; same-day ships of one navy are one transit) and every PRC
official stream, one row per day in the window [-7, +14] around the transit date, with:
  docs                  documents published that day (distinct URLs)
  sentences             distinct official sentences that day (reporters' questions in transcripts excluded)
  mention_sentences     distinct official sentences naming the transiting state; mention_docs = docs with one
  scored_mentions       mention sentences with a tone score; <dim> = their mean tone (hostility, threat, ...)
plus a pre-window baseline per transit x stream over days [-37, -8] (base_* columns: per-day means and the mean tone
of all mention sentences in the baseline), and flags for coverage and overlapping transit windows.

Streams (direct collection, country CN, outlet official):
  MFA  mfa_cn_live + mfa_cn_archive, English (all kinds: briefings, readouts, statements, speeches)
  MND  mnd_cn_live, Chinese (all three spokesperson channels; full transcripts and their per-topic extracts both
       appear, so sentences are de-duplicated by text within a stream-day)
  TAO  tao_cn_live, Chinese (press-conference transcripts, standalone releases, office news)
  The corpus has no Eastern Theater Command stream (ETC statements appear only as state-media carriage), so none is
  exported.

Mentions use the semantic layer's gazetteer (scripts/semantic/targets.py: US, JAPAN, UK, AUSTRALIA, ...) plus
SUPPLEMENT below for transiting states the gazetteer lacks (Canada, Germany, France, Netherlands, New Zealand).
Tone is not yet human-validated (feeds_common.NOT_VALIDATED). No modelling is done here.

Transit list: a CSV with columns date,name,hull,class,type,country (the TSM Transit Tracker layout), or
--from-tsm-js to read `transits` from tsm-strait-layers/shared/data/tsm.js with node.

Usage (from the repo root):
  uv run python -m scripts.export_transit_rhetoric --from-tsm-js ../tsm-strait-layers/shared/data/tsm.js \
      --since 2024-01-01 --out-dir "<paper>/data/transit_rhetoric_2026-10-05"
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import subprocess
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from scripts.export_cross_strait_pulse import (MFA_SPOKES, Pair, mfa_classifier, mnd_classifier, split_turns,
                                               tao_classify)
from scripts.feeds_common import (DIMS, NOT_VALIDATED, connect, index_dir, line_sentences, mean_dims, new_dir,
                                  provenance, sentence_scores, write_csv)
from scripts.semantic.targets import default as gazetteer

logger = logging.getLogger("export_transit_rhetoric")
PRE, POST, BASE_FROM, BASE_TO = 7, 14, -37, -8

STREAMS = {   # stream -> (sources in preference order, language)
    "MFA": (("mfa_cn_live", "mfa_cn_archive"), "en"),
    "MND": (("mnd_cn_live",), "zh"),
    "TAO": (("tao_cn_live",), "zh"),
}
COUNTRY_TARGET = {"USA": "US", "United States": "US", "Japan": "JAPAN", "UK": "UK", "United Kingdom": "UK",
                  "Australia": "AUSTRALIA", "Canada": "CANADA", "Germany": "GERMANY", "France": "FRANCE",
                  "Netherlands": "NETHERLANDS", "New Zealand": "NEW_ZEALAND"}
# Transiting states missing from the gazetteer: one regex per language, matched per sentence. Chinese uses full names
# and capitals only; the "X方" short forms (加方, 法方, 德方, 荷方, 新方) collide with common words (增加方, 做法方面).
SUPPLEMENT: Dict[str, Dict[str, str]] = {
    "CANADA": {"en": r"\bCanad(?:a|ian|ians)\b|\bOttawa\b", "zh": r"加拿大|渥太华"},
    "GERMANY": {"en": r"\bGerman(?:y|s)?\b|\bBerlin\b", "zh": r"德国|柏林"},
    "FRANCE": {"en": r"\bFrance\b|\bFrench\b|\bParis\b", "zh": r"法国|巴黎"},
    "NETHERLANDS": {"en": r"\bNetherlands\b|\bDutch\b|\bThe Hague\b", "zh": r"荷兰|海牙"},
    "NEW_ZEALAND": {"en": r"\bNew Zealand\b|\bWellington\b", "zh": r"新西兰|惠灵顿"},
}
SUPPLEMENT_RE = {t: {lang: re.compile(rx) for lang, rx in v.items()} for t, v in SUPPLEMENT.items()}
ROW_FIELDS = ("transit_id", "event_date", "transit_country", "target", "transit_group", "ships", "stream", "lang",
              "day_offset", "date", "stream_covered", "overlaps_other_transit", "docs", "sentences", "mention_docs",
              "mention_sentences", "scored_mentions", *DIMS, "base_days", "base_docs_per_day",
              "base_mention_sentences_per_day", "base_scored_mentions", *(f"base_{d}" for d in DIMS))


# ------------------------------------------------------------------------------------------------ transits
def load_transits_csv(path: Path) -> List[Dict]:
    with path.open(encoding="utf-8-sig") as fh:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(fh)]


def load_transits_js(path: Path) -> List[Dict]:
    js = ("import(process.argv[1]).then(m => process.stdout.write(JSON.stringify(m.TSM.transits)))")
    rows = json.loads(subprocess.run(["node", "-e", js, path.resolve().as_uri()], check=True, capture_output=True,
                                     text=True).stdout)
    return [dict(zip(("date", "name", "hull", "class", "type", "country"), r)) for r in rows]


def group_transits(rows: List[Dict], since: str) -> List[Dict]:
    """One transit per (date, country); group = us / ally / joint (joint = a US ship and another navy that day)."""
    by: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for r in rows:
        if r["date"] >= since:
            by[(r["date"], r["country"])].append(f"{r['name']} ({r['hull']})" if r.get("hull") else r["name"])
    days: Dict[str, Set[str]] = defaultdict(set)
    for d, c in by:
        days[d].add(c)
    out = []
    for (d, c), ships in sorted(by.items()):
        us = "USA" in days[d]
        target = COUNTRY_TARGET.get(c)
        if target is None:
            raise SystemExit(f"no gazetteer target for transit country {c!r}: add it to COUNTRY_TARGET/SUPPLEMENT")
        out.append({"transit_id": f"{d}_{target}", "event_date": d, "transit_country": c, "target": target,
                    "transit_group": ("joint" if us and len(days[d]) > 1 else "us" if us else "ally"),
                    "ships": "; ".join(ships)})
    return out


# ------------------------------------------------------------------------------------------------ corpus
def question_idx(text: str, stream: str, url: str) -> Set[int]:
    """Sentence idx of reporters' questions in transcripts (not the government's words)."""
    lines = line_sentences(text)
    if stream == "TAO" and "/wyly/" in url:   # standalone release: question lines precede the spokesperson's answer
        start = next((i for i, (l, _, _) in enumerate(lines) if re.search(r"发言人.{2,3}(?:应询)?(?:表示|指出)", l)), 0)
        return {k for _, a, b in lines[:start] for k in range(a, b)}
    classify = {"MFA": mfa_classifier(set(MFA_SPOKES.values())), "MND": mnd_classifier("zh" if "mod.gov.cn/gfbw" in url
                                                                                       else "en"),
                "TAO": tao_classify}[stream]
    pairs: List[Pair] = split_turns(lines, classify, stream != "MFA")
    return {k for p in pairs for _, a, b in p.q for k in range(a, b)}


def mentions(gaz, text: str, lang: str) -> Set[str]:
    found = set(gaz.targets(text, lang))
    found |= {t for t, rx in SUPPLEMENT_RE.items() if lang in rx and rx[lang].search(text)}
    return found


def collect(cc, sc, stream: str, lo: str, hi: str, gaz) -> Tuple[Dict[str, Dict], Optional[str]]:
    """{date: {"urls": set, "sents": {text: (targets, scores|None, url)}}} for one stream, plus its first date."""
    sources, lang = STREAMS[stream]
    q = (f"SELECT rowid, id, source, url, date, text FROM docs WHERE country = 'CN' AND source IN "
         f"({','.join('?' * len(sources))}) AND lang = ? AND date >= ? AND date < ? ORDER BY date")
    rank = {s: i for i, s in enumerate(sources)}
    best: Dict[str, Tuple] = {}
    for row in cc.execute(q, [*sources, lang, lo, hi]):
        if row[3] not in best or rank[row[2]] < rank[best[row[3]][2]]:
            best[row[3]] = row
    first = cc.execute(f"SELECT MIN(date) FROM docs WHERE country = 'CN' AND source IN ({','.join('?' * len(sources))})"
                       f" AND lang = ?", [*sources, lang]).fetchone()[0]
    days: Dict[str, Dict] = defaultdict(lambda: {"urls": set(), "sents": {}})
    for rowid, doc_id, _src, url, day, text in best.values():
        qs = question_idx(text or "", stream, url)
        sents = cc.execute("SELECT idx, text FROM sentences WHERE doc = ?", [rowid]).fetchall()
        scores = sentence_scores(sc, doc_id)
        cell = days[day]
        cell["urls"].add(url)
        for idx, s in sents:
            if idx in qs:
                continue
            key = re.sub(r"\s+", " ", s).strip()
            if key in cell["sents"]:
                if cell["sents"][key][1] is None and idx in scores:
                    cell["sents"][key] = (cell["sents"][key][0], scores[idx], url)
                continue
            cell["sents"][key] = (mentions(gaz, s, lang), scores.get(idx), url)
    return days, first


def day_stats(cell: Optional[Dict], target: str) -> Dict:
    if not cell:
        return {"docs": 0, "sentences": 0, "mention_docs": 0, "mention_sentences": 0, "scored": []}
    m = [(sc, url) for tg, sc, url in cell["sents"].values() if target in tg]
    return {"docs": len(cell["urls"]), "sentences": len(cell["sents"]), "mention_docs": len({u for _, u in m}),
            "mention_sentences": len(m), "scored": [sc for sc, _ in m if sc is not None]}


def shift(d: str, n: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


def build_rows(transits: List[Dict], data: Dict[str, Tuple[Dict, Optional[str]]]) -> List[Dict]:
    event_dates = sorted({t["event_date"] for t in transits})
    rows = []
    for t in transits:
        others = [d for d in event_dates if d != t["event_date"]]
        for stream, (days, first) in data.items():
            base = [day_stats(days.get(shift(t["event_date"], k)), t["target"]) for k in range(BASE_FROM, BASE_TO + 1)]
            nb = len(base)
            bscored = [s for b in base for s in b["scored"]]
            bvals = {"base_days": nb, "base_docs_per_day": round(sum(b["docs"] for b in base) / nb, 3),
                     "base_mention_sentences_per_day": round(sum(b["mention_sentences"] for b in base) / nb, 3),
                     "base_scored_mentions": len(bscored), **{f"base_{k}": v for k, v in mean_dims(bscored).items()}}
            for k in range(-PRE, POST + 1):
                d = shift(t["event_date"], k)
                st = day_stats(days.get(d), t["target"])
                rows.append({**t, "stream": stream, "lang": STREAMS[stream][1], "day_offset": k, "date": d,
                             "stream_covered": int(first is not None and d >= first),
                             "overlaps_other_transit": int(any(shift(o, -PRE) <= d <= shift(o, POST) for o in others)),
                             **{x: st[x] for x in ("docs", "sentences", "mention_docs", "mention_sentences")},
                             "scored_mentions": len(st["scored"]), **mean_dims(st["scored"]), **bvals})
    return rows


CODEBOOK = [
    ("transit_id", "Transit key: <event_date>_<target>. One transiting state on one date (same-day ships of one navy = one transit)."),
    ("event_date", "Transit date from the TSM Transit Tracker (tsm-strait-layers shared/data/tsm.js, corrected dates)."),
    ("transit_country", "Navy that transited (tracker spelling)."),
    ("target", "Gazetteer target used for mentions (scripts/semantic/targets.py; CANADA, GERMANY, FRANCE, NETHERLANDS, NEW_ZEALAND from the script's SUPPLEMENT)."),
    ("transit_group", "us = only US ships that day; ally = no US ship that day; joint = a US ship and another navy the same day (both states get a row set)."),
    ("ships", "Ships of this navy on this date (name and hull)."),
    ("stream", "MFA (mfa_cn_live + mfa_cn_archive, English, all kinds), MND (mnd_cn_live, Chinese, three spokesperson channels), TAO (tao_cn_live, Chinese). No Eastern Theater Command stream exists in the corpus."),
    ("lang", "Language of the stream's documents."),
    ("day_offset", "Days from event_date, -7..+14."),
    ("date", "Calendar date (document publication date as stored in the corpus)."),
    ("stream_covered", "1 if the date is on or after the stream's first document in the corpus; 0 means zeros are structural, not silence."),
    ("overlaps_other_transit", "1 if the date also falls in the [-7, +14] window of a transit on another date."),
    ("docs", "Distinct documents (URLs) published that day in the stream."),
    ("sentences", "Distinct official sentences that day (text-deduplicated within stream-day; reporters' questions in transcripts excluded)."),
    ("mention_docs", "Documents with at least one official sentence naming the transiting state (self-mentions impossible: all states are foreign)."),
    ("mention_sentences", "Distinct official sentences naming the transiting state."),
    ("scored_mentions", "Mention sentences with a tone score. The semantic layer scores sentences with index < 80 plus gazetteer-target sentences up to index 400; mentions of SUPPLEMENT states late in long transcripts can be unscored."),
    *[(d, f"Mean {d} probability (0..1) over scored mention sentences that day; empty when scored_mentions = 0. NOT human-validated.") for d in DIMS],
    ("base_days", "Baseline length in days: [-37, -8] before event_date (30 days ending the day before the window)."),
    ("base_docs_per_day", "Mean daily docs over the baseline days (days without documents count as 0)."),
    ("base_mention_sentences_per_day", "Mean daily mention sentences over the baseline."),
    ("base_scored_mentions", "Scored mention sentences in the whole baseline."),
    *[(f"base_{d}", f"Mean {d} over all scored mention sentences in the baseline (pooled, sentence-weighted). NOT human-validated.") for d in DIMS],
]


def readme(n_rows: int, transits: List[Dict], args, firsts: Dict[str, Optional[str]]) -> str:
    groups = defaultdict(int)
    for t in transits:
        groups[t["transit_group"]] += 1
    return f"""Calibrated Coercion: PRC official rhetoric around Taiwan Strait transits
Generated {datetime.now(timezone.utc):%Y-%m-%d} by scripts/export_transit_rhetoric.py (rhetoric-corpus repo, branch feat/paper-feeds).

Files
  transit_rhetoric_daily.csv   {n_rows} rows: one per transit x day (-7..+14) x stream, with a pre-window baseline
  transit_rhetoric_codebook.csv  variable definitions
  transits_used.csv            the {len(transits)} transits ({dict(groups)}) from {len({t['event_date'] for t in transits})} event dates since {args.since}
  provenance.json              corpus/semantic versions and teacher-student agreement

This is a dataset only: no models were fitted and no claims are made.

CAVEAT: {NOT_VALIDATED}

Notes
- Transit list: TSM Transit Tracker rows in tsm-strait-layers/shared/data/tsm.js (as of the export), which carries
  the 2026-09-30 date corrections. The paper's v2 draft reports 24 events for Jan 2024-Sep 2026; the current tracker
  yields {len({t['event_date'] for t in transits})} event dates (the 2026-09-18 New Zealand transit was added after the draft's count and some
  dates were corrected). Joint same-day transits give one row set per state.
- Streams start dates in the corpus: {', '.join(f'{k} {v}' for k, v in firsts.items())}. MFA uses the English
  edition; MND and TAO the Chinese originals. There is no Eastern Theater Command stream.
- MND publishes full transcripts and per-topic extracts of the same briefing; sentences are de-duplicated by text
  within each stream-day, but `docs` counts both.
- Mentions are pattern matches on the state's name (and capital / "X side" forms); a sentence naming a state is not
  necessarily about the transit.
"""


def export(index: Path, transits: List[Dict], out: Path, args) -> Dict:
    cc, sc = connect(index)
    gaz = gazetteer()
    lo = shift(min(t["event_date"] for t in transits), BASE_FROM)
    hi = shift(max(t["event_date"] for t in transits), POST + 1)
    data = {s: collect(cc, sc, s, lo, hi, gaz) for s in STREAMS}
    rows = build_rows(transits, data)
    new_dir(out)
    n = write_csv(out / "transit_rhetoric_daily.csv", ROW_FIELDS, rows)
    write_csv(out / "transit_rhetoric_codebook.csv", ("variable", "definition"),
              [{"variable": v, "definition": d} for v, d in CODEBOOK])
    write_csv(out / "transits_used.csv", ("transit_id", "event_date", "transit_country", "target", "transit_group",
                                          "ships"), transits)
    prov = provenance(index)
    prov.update(generated=datetime.now(timezone.utc).isoformat(timespec="seconds"), index=str(index),
                window=[-PRE, POST], baseline=[BASE_FROM, BASE_TO], streams={k: list(v[0]) for k, v in STREAMS.items()},
                supplement_patterns=SUPPLEMENT, transit_source=args.from_tsm_js or args.transits, since=args.since)
    (out / "provenance.json").write_text(json.dumps(prov, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "README.txt").write_text(readme(n, transits, args, {k: v[1] for k, v in data.items()}), encoding="utf-8")
    cc.close()
    sc.close()
    return {"out": str(out), "rows": n, "transits": len(transits)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--transits", help="CSV with date,name,hull,class,type,country")
    src.add_argument("--from-tsm-js", help="path to tsm-strait-layers/shared/data/tsm.js (read with node)")
    ap.add_argument("--since", default="2024-01-01")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--index", help="index/ directory (default: repo index/ or $RC_INDEX)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raw = load_transits_js(Path(args.from_tsm_js).expanduser()) if args.from_tsm_js else \
        load_transits_csv(Path(args.transits).expanduser())
    print(json.dumps(export(index_dir(args.index), group_transits(raw, args.since), args.out_dir.expanduser(), args)))


if __name__ == "__main__":
    main()
