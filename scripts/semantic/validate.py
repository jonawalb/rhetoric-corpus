"""Human validation: blind coding sample export (stage `export`) and scoring of the coded file (`validate`).

  uv run python -m scripts.semantic.run --stage export        # -> reports/semantic/validation_sample.csv
  (code the 0/1 columns in a copy, e.g. validation_sample_coded.csv)
  uv run python -m scripts.semantic.run validate reports/semantic/validation_sample_coded.csv

Design: ~300 scored sentences, strata = country x language (sqrt-proportional quotas, >= 6 each). Half random,
half drawn from sentences the student scored >= 0.5 on at least one dimension (so positives exist to check
precision). The blind file shows no scores and no arm; the key (index/semantic/validation_key.csv) holds student
and NLI-teacher scores and the arm, so agreement can be reported overall and on the random arm alone.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import random
import sqlite3
from typing import Dict, List, Optional

import numpy as np

from . import store
from .config import AGG_DIR, DIMS, REPORT_DIR, SEM_DIR, SETTINGS

logger = logging.getLogger(__name__)
KEY = SEM_DIR / "validation_key.csv"
BLIND = REPORT_DIR / "validation_sample.csv"


def export_sample(con: sqlite3.Connection, total: int = 300) -> Dict:
    rng = random.Random(SETTINGS.seed + 1)
    sizes = {(c, lg): n for c, lg, n in con.execute(
        "SELECT d.country, d.lang, count(*) FROM sent_scores s JOIN docs d USING(doc_id) WHERE d.present=1 GROUP BY 1, 2")}
    taken = {(a, b) for a, b in con.execute("SELECT doc_id, idx FROM teacher")}
    w = {k: math.sqrt(v) for k, v in sizes.items()}
    tot = sum(w.values())
    cols = f"s.doc_id, s.idx, s.crc, {', '.join('s.' + d for d in DIMS)}, d.country, d.lang, d.source, d.date, d.url, d.crow"
    hi_cond = " OR ".join(f"s.{d} >= 500" for d in DIMS)
    cc = store.corpus()
    picks: List[Dict] = []
    seen = set()
    for (country, lang) in sorted(sizes, key=str):
        q = max(6, round(total * w[(country, lang)] / tot))
        for arm, cond, n in (("enriched", f" AND ({hi_cond})", q // 2), ("random", "", q - q // 2)):
            # Deterministic pseudo-random order: multiplicative hash of the sentence CRC.
            cand = con.execute(f"SELECT {cols} FROM sent_scores s JOIN docs d USING(doc_id) WHERE d.present=1 AND d.country IS ?"
                               f" AND d.lang IS ?{cond} ORDER BY (s.crc * 2654435761) % 4294967296 LIMIT ?",
                               (country, lang, n * 8)).fetchall()
            got = 0
            for r in cand:
                if got >= n:
                    break
                if (r[0], r[1]) in taken or (r[0], r[1]) in seen:
                    continue
                text = store.sentence_text(cc, r[14], r[1])
                if not text or store.crc(text) != r[2] or len(text) < 20:
                    continue
                seen.add((r[0], r[1]))
                picks.append({"doc_id": r[0], "idx": r[1], "text": text, "country": r[9], "lang": r[10], "source": r[11],
                              "date": r[12], "url": r[13], "arm": arm, **{d: r[3 + j] / 1000 for j, d in enumerate(DIMS)}})
                got += 1
    cc.close()
    rng.shuffle(picks)
    teacher = _teacher_scores([p["text"] for p in picks])
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with BLIND.open("w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["sample_id", "country", "source", "lang", "date", "text", "url", *DIMS, "coder", "notes"])
        for i, p in enumerate(picks):
            wr.writerow([f"v{i:04d}", p["country"], p["source"], p["lang"], p["date"], p["text"], p["url"],
                         *[""] * len(DIMS), "", ""])
    with KEY.open("w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["sample_id", "doc_id", "idx", "arm", "lang", "country", *[f"student_{d}" for d in DIMS],
                     *[f"teacher_{d}" for d in DIMS]])
        for i, p in enumerate(picks):
            wr.writerow([f"v{i:04d}", p["doc_id"], p["idx"], p["arm"], p["lang"], p["country"],
                         *[round(p[d], 4) for d in DIMS], *[round(float(x), 4) for x in teacher[i]]])
    return {"sample": len(picks), "blind_file": str(BLIND), "key_file": str(KEY)}


def _teacher_scores(texts: List[str]) -> np.ndarray:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    from .config import HYPOTHESES, NLI_MODEL, device, model_path

    dev = device()
    path = model_path(NLI_MODEL)
    tok = AutoTokenizer.from_pretrained(path)
    model = AutoModelForSequenceClassification.from_pretrained(
        path, dtype=torch.float16 if dev == "mps" else torch.float32).to(dev).eval()
    ent = {v.lower(): k for k, v in model.config.id2label.items()}["entailment"]
    hyps = [HYPOTHESES[d] for d in DIMS]
    out = np.zeros((len(texts), len(DIMS)))
    with torch.inference_mode():
        for i in range(0, len(texts), 16):
            part = texts[i:i + 16]
            enc = tok([t for t in part for _ in hyps], [h for _ in part for h in hyps], padding=True,
                      truncation="only_first", max_length=160, return_tensors="pt").to(dev)
            out[i:i + len(part)] = model(**enc).logits.float().softmax(-1)[:, ent].reshape(len(part), -1).cpu().numpy()
    return out


def main(argv: Optional[List[str]] = None) -> None:
    from .tone import agreement

    ap = argparse.ArgumentParser(prog="run validate")
    ap.add_argument("coded", help="the blind CSV with 0/1 values filled in")
    a = ap.parse_args(argv)
    key = {r["sample_id"]: r for r in csv.DictReader(KEY.open(encoding="utf-8"))}
    coded = [r for r in csv.DictReader(open(a.coded, encoding="utf-8")) if r["sample_id"] in key]
    res: Dict[str, Dict] = {}
    for d in DIMS:
        rows = [r for r in coded if r.get(d, "").strip() in ("0", "1")]
        if not rows:
            continue
        h = np.array([float(r[d]) for r in rows])
        s = np.array([float(key[r["sample_id"]][f"student_{d}"]) for r in rows])
        t = np.array([float(key[r["sample_id"]][f"teacher_{d}"]) for r in rows])
        rnd = np.array([key[r["sample_id"]]["arm"] == "random" for r in rows])
        res[d] = {"human_vs_student": agreement(h, s), "human_vs_teacher": agreement(h, t),
                  "random_arm_human_vs_student": agreement(h[rnd], s[rnd]) if rnd.any() else None}
    (AGG_DIR / "validation_human.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    L = ["# Human validation of tone scores", "", f"Coded file: `{a.coded}`; {len(coded)} rows matched the key.", "",
         "| dimension | n coded | human pos. rate | student kappa | student AUC | teacher kappa | teacher AUC |", "|---|---|---|---|---|---|---|"]
    for d, m in res.items():
        hs, ht = m["human_vs_student"], m["human_vs_teacher"]
        L.append(f"| {d} | {hs['n']} | {hs['teacher_pos']:.2f} | {hs.get('kappa', float('nan')):.2f} | {hs.get('auc', float('nan')):.2f}"
                 f" | {ht.get('kappa', float('nan')):.2f} | {ht.get('auc', float('nan')):.2f} |")
    (REPORT_DIR / "human_validation.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
