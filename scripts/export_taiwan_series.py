"""Export a monthly series of PRC rhetoric on Taiwan, official outlets vs state media, by source (Narrative Warfare paper).

One row per (outlet, source, language, month), 2021-01 onward, PRC (country CN) documents only, keyword-filtered
sample regimes (backfill, seed) excluded as in every semantic-layer series:
  docs                 documents in the stream-month (denominator)
  taiwan_docs          documents with at least one sentence naming Taiwan (semantic `mentions`, target TAIWAN)
  taiwan_share         taiwan_docs / docs
  taiwan_sentences     sentences naming Taiwan; taiwan_scored = those with a tone score
  sent_<dim>           mean tone over scored Taiwan sentences (sentence-weighted)
  doc_<dim>            mean over Taiwan-mentioning docs of each doc's mean Taiwan-sentence tone (doc-weighted; the
                       semantic layer's "stance" definition)
Pooled rows (level = pooled, source = ALL) combine every directly collected stream (IMPORTED copies left out) of an
outlet type and language per month. Streams enter and leave the corpus, so pooled levels move with source mix; use
the by-source rows for comparisons.

Read-only on index/semantic/semantic.sqlite. Tone is not yet human-validated (feeds_common.NOT_VALIDATED).

Usage (from the repo root):
  uv run python -m scripts.export_taiwan_series --out-dir "<paper>/data/taiwan_rhetoric_series_2026-10-05"
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from scripts.feeds_common import (DIMS, EXCLUDED_SAMPLES, NOT_VALIDATED, index_dir, new_dir, provenance, ro,
                                  write_csv)

logger = logging.getLogger("export_taiwan_series")
START = "2021-01-01"
# Imported copies of TSM's own tables (via=export in the corpus; SOURCES.md "imported"). They overlap the directly
# collected streams (mfa_cn_live/archive, mnd_cn_live, tao_cn_live, cn_xinhua, ...), so pooled rows leave them out.
IMPORTED = ("mfa_cn", "mnd_cn", "tao_cn", "prc_statemedia", "prc_statemedia_headlines")
FIELDS = ("level", "outlet", "source", "imported", "lang", "month", "docs", "taiwan_docs", "taiwan_share", "taiwan_sentences",
          "taiwan_scored", *(f"sent_{d}" for d in DIMS), *(f"doc_{d}" for d in DIMS))
CODEBOOK = [
    ("level", "source = one stream (source x language); pooled = all streams of the outlet type and language combined."),
    ("outlet", "official (ministries, State Council, embassies, TAO, MND, Qiushi, CCG) | state_media (Xinhua, People's Daily, CGTN, Global Times, China Daily, PLA Daily/China Military Online, CCTV, ...) | media (privately owned, state-aligned: Guancha)."),
    ("source", "Corpus source id (see rhetoric-corpus SOURCES.md); ALL on pooled rows. mnd_cn_live keeps both full MND transcripts and their per-question items, so its sentence counts double-count those briefings."),
    ("imported", "1 = imported copy of a TSM table (mfa_cn, mnd_cn, tao_cn, prc_statemedia, prc_statemedia_headlines); overlaps the directly collected streams and is excluded from pooled rows. tao_cn English is machine translation; prc_statemedia_headlines is headlines only."),
    ("lang", "Document language (en, zh)."),
    ("month", "Publication month YYYY-MM."),
    ("docs", "Documents in the stream-month (keyword-filtered samples excluded)."),
    ("taiwan_docs", "Documents with at least one sentence naming Taiwan (gazetteer target TAIWAN: Taiwan/Taipei/台湾/台独/台海 etc.; scripts/semantic/targets.py)."),
    ("taiwan_share", "taiwan_docs / docs."),
    ("taiwan_sentences", "Sentences naming Taiwan (all sentences of a document are tagged)."),
    ("taiwan_scored", "Taiwan sentences with a tone score (the tone model scores sentences with index < 80 plus target-mentioning sentences with index < 400)."),
    *[(f"sent_{d}", f"Mean {d} probability (0..1) over scored Taiwan sentences, sentence-weighted. NOT human-validated.") for d in DIMS],
    *[(f"doc_{d}", f"Mean over Taiwan-mentioning docs of the doc's mean {d} on its scored Taiwan sentences (doc-weighted stance). NOT human-validated.") for d in DIMS],
]


def export(index: Path, out: Path) -> Dict:
    sc = ro(index / "semantic" / "semantic.sqlite")
    ex = ",".join("?" * len(EXCLUDED_SAMPLES))
    docs: Dict[tuple, int] = {}
    for outlet, source, lang, month, n in sc.execute(
            f"SELECT outlet, source, lang, substr(date, 1, 7), COUNT(*) FROM docs WHERE country = 'CN' AND present = 1 "
            f"AND date >= ? AND date <= ? AND COALESCE(sample, 'all') NOT IN ({ex}) GROUP BY 1, 2, 3, 4",
            [START, datetime.now(timezone.utc).strftime("%Y-%m-%d"), *EXCLUDED_SAMPLES]):
        docs[(outlet, source, lang, month)] = n
    q = (f"SELECT d.outlet, d.source, d.lang, substr(d.date, 1, 7), m.doc_id, s.{', s.'.join(DIMS)} "
         f"FROM (SELECT DISTINCT doc_id, idx FROM mentions WHERE target = 'TAIWAN' AND self = 0) m "
         f"JOIN docs d ON d.doc_id = m.doc_id LEFT JOIN sent_scores s ON s.doc_id = m.doc_id AND s.idx = m.idx "
         f"WHERE d.country = 'CN' AND d.present = 1 AND d.date >= ? AND COALESCE(d.sample, 'all') NOT IN ({ex})")
    sents: Dict[tuple, list] = defaultdict(list)            # stream-month -> [(doc_id, scores|None)]
    for row in sc.execute(q, [START, *EXCLUDED_SAMPLES]):
        key, doc_id, vals = row[:4], row[4], row[5:]
        if key in docs:
            sents[key].append((doc_id, None if vals[0] is None else [v / 1000 for v in vals]))
    sc.close()

    def cell(level: str, key: tuple, n: int, items: list) -> Dict:
        scored = [v for _, v in items if v is not None]
        per_doc: Dict[str, list] = defaultdict(list)
        for d, v in items:
            if v is not None:
                per_doc[d].append(v)
        doc_means = [[sum(c) / len(c) for c in zip(*vs)] for vs in per_doc.values()]
        tdocs = len({d for d, _ in items})
        r = {"level": level, "outlet": key[0], "source": key[1], "imported": int(key[1] in IMPORTED), "lang": key[2],
             "month": key[3], "docs": n,
             "taiwan_docs": tdocs, "taiwan_share": round(tdocs / n, 4) if n else None, "taiwan_sentences": len(items),
             "taiwan_scored": len(scored)}
        for i, dname in enumerate(DIMS):
            r[f"sent_{dname}"] = round(sum(v[i] for v in scored) / len(scored), 4) if scored else None
            r[f"doc_{dname}"] = round(sum(v[i] for v in doc_means) / len(doc_means), 4) if doc_means else None
        return r

    rows: List[Dict] = [cell("source", k, n, sents.get(k, [])) for k, n in sorted(docs.items())]
    pooled_n: Dict[tuple, int] = defaultdict(int)
    pooled_s: Dict[tuple, list] = defaultdict(list)
    for (o, s, lang, m), n in docs.items():
        if s in IMPORTED:
            continue
        pooled_n[(o, "ALL", lang, m)] += n
        pooled_s[(o, "ALL", lang, m)] += sents.get((o, s, lang, m), [])
    rows += [cell("pooled", k, n, pooled_s[k]) for k, n in sorted(pooled_n.items())]

    new_dir(out)
    n = write_csv(out / "taiwan_rhetoric_monthly.csv", FIELDS, rows)
    write_csv(out / "taiwan_rhetoric_codebook.csv", ("variable", "definition"),
              [{"variable": v, "definition": d} for v, d in CODEBOOK])
    prov = provenance(index)
    prov.update(generated=datetime.now(timezone.utc).isoformat(timespec="seconds"), index=str(index), start=START,
                excluded_samples=list(EXCLUDED_SAMPLES))
    (out / "provenance.json").write_text(json.dumps(prov, ensure_ascii=False, indent=1), encoding="utf-8")
    streams = sorted({(r["outlet"], r["source"], r["lang"]) for r in rows if r["level"] == "source"})
    span = {s: (min(r["month"] for r in rows if (r["outlet"], r["source"], r["lang"]) == s),
                max(r["month"] for r in rows if (r["outlet"], r["source"], r["lang"]) == s)) for s in streams}
    (out / "README.txt").write_text(
        f"Narrative Warfare: monthly PRC rhetoric on Taiwan, official vs state media\n"
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d} by scripts/export_taiwan_series.py (rhetoric-corpus repo, "
        f"branch feat/paper-feeds).\n\nFiles\n  taiwan_rhetoric_monthly.csv   {n} rows (by-source and pooled)\n"
        f"  taiwan_rhetoric_codebook.csv  variable definitions\n  provenance.json               versions, agreement\n\n"
        f"CAVEAT: {NOT_VALIDATED}\n\nSources enter and leave the corpus at different dates, so pooled rows move with "
        f"source mix; compare within source. Imported copies of TSM tables (imported = 1) overlap the directly "
        f"collected streams and are excluded from pooled rows. mnd_cn_live keeps full MND transcripts and their "
        f"per-question items, so its Taiwan sentence counts double-count those briefings. Some state-media streams hold headlines only (prc_statemedia_headlines) "
        f"or a recent window only. Coverage by stream (first and last month):\n"
        + "".join(f"  {o:12s} {s:26s} {lang}  {a} .. {b}\n" for (o, s, lang), (a, b) in span.items()),
        encoding="utf-8")
    return {"out": str(out), "rows": n, "streams": len(streams)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--index", help="index/ directory (default: repo index/ or $RC_INDEX)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(export(index_dir(args.index), args.out_dir.expanduser())))


if __name__ == "__main__":
    main()
