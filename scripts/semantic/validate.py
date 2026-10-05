"""Human validation: blind coding sample export (stage `export`) and scoring of the coded file (`validate`).

  uv run python -m scripts.semantic.run --stage export        # -> reports/semantic/validation_sample.csv
  (code the 0/1 columns in a copy, e.g. validation_sample_coded.csv)
  uv run python -m scripts.semantic.run --stage export --per-lang-min 30   # top up the existing sample per language
  uv run python -m scripts.semantic.run validate coderA.xlsx [coderB.xlsx ...] [--key KEY.csv] [--out DIR]

Design: ~300 scored sentences, strata = country x language (sqrt-proportional quotas, >= 6 each). Half random,
half drawn from sentences the student scored >= 0.5 on at least one dimension (so positives exist to check
precision). The blind file shows no scores and no arm; the key (index/semantic/validation_key.csv) holds student
and NLI-teacher scores and the arm, so agreement can be reported overall and on the random arm alone.
The top-up (`--per-lang-min`) adds rows with the same arms until every language with data has the minimum.
`validate` takes one returned file per coder (.xlsx coding sheet or .csv) and reports per-coder AUC/F1/kappa against
student and teacher, agreement on consensus rows, and inter-coder Krippendorff's alpha when >= 2 coders are given.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import random
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np

from . import store
from .config import AGG_DIR, DIMS, REPORT_DIR, SEM_DIR, SETTINGS

logger = logging.getLogger(__name__)
KEY = SEM_DIR / "validation_key.csv"
BLIND = REPORT_DIR / "validation_sample.csv"


_COLS = (f"s.doc_id, s.idx, s.crc, {', '.join('s.' + d for d in DIMS)}, d.country, d.lang, d.source, d.date, d.url, d.crow")
_HI = " OR ".join(f"s.{d} >= 500" for d in DIMS)


def _draw_stratum(con: sqlite3.Connection, cc: sqlite3.Connection, country: str, lang: str, quotas, seen: set,
                  seen_text: Optional[set] = None) -> List[Dict]:
    """Draw sentences of one country x language stratum. quotas = ((arm, n), ...); arm "enriched" = student >= 0.5
    on at least one dimension, "random" = any scored sentence. `seen` holds (doc_id, idx) to skip and is updated;
    `seen_text` (optional) also skips sentences whose exact text was already drawn (syndicated duplicates)."""
    picks: List[Dict] = []
    for arm, n in quotas:
        if n <= 0:
            continue
        cond = f" AND ({_HI})" if arm == "enriched" else ""
        # Deterministic pseudo-random order: multiplicative hash of the sentence CRC.
        cand = con.execute(f"SELECT {_COLS} FROM sent_scores s JOIN docs d USING(doc_id) WHERE d.present=1 AND d.country IS ?"
                           f" AND d.lang IS ?{cond} ORDER BY (s.crc * 2654435761) % 4294967296 LIMIT ?",
                           (country, lang, n * 8)).fetchall()
        got = 0
        for r in cand:
            if got >= n:
                break
            if (r[0], r[1]) in seen:
                continue
            text = store.sentence_text(cc, r[14], r[1])
            if not text or store.crc(text) != r[2] or len(text) < 20:
                continue
            if seen_text is not None:
                if text in seen_text:
                    continue
                seen_text.add(text)
            seen.add((r[0], r[1]))
            picks.append({"doc_id": r[0], "idx": r[1], "text": text, "country": r[9], "lang": r[10], "source": r[11],
                          "date": r[12], "url": r[13], "arm": arm, **{d: r[3 + j] / 1000 for j, d in enumerate(DIMS)}})
            got += 1
    return picks


def export_sample(con: sqlite3.Connection, total: int = 300) -> Dict:
    rng = random.Random(SETTINGS.seed + 1)
    sizes = {(c, lg): n for c, lg, n in con.execute(
        "SELECT d.country, d.lang, count(*) FROM sent_scores s JOIN docs d USING(doc_id) WHERE d.present=1 GROUP BY 1, 2")}
    taken = {(a, b) for a, b in con.execute("SELECT doc_id, idx FROM teacher")}
    w = {k: math.sqrt(v) for k, v in sizes.items()}
    tot = sum(w.values())
    cc = store.corpus()
    picks: List[Dict] = []
    seen = set(taken)
    for (country, lang) in sorted(sizes, key=str):
        q = max(6, round(total * w[(country, lang)] / tot))
        picks += _draw_stratum(con, cc, country, lang, (("enriched", q // 2), ("random", q - q // 2)), seen)
    cc.close()
    rng.shuffle(picks)
    teacher = _teacher_scores([p["text"] for p in picks])
    ids = [f"v{i:04d}" for i in range(len(picks))]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    _write_csv(BLIND, BLIND_COLS, [_blind_row(i, p) for i, p in zip(ids, picks)])
    _write_csv(KEY, KEY_COLS, [_key_row(i, p, t) for i, p, t in zip(ids, picks, teacher)])
    return {"sample": len(picks), "blind_file": str(BLIND), "key_file": str(KEY)}


BLIND_COLS = ["sample_id", "country", "source", "lang", "date", "text", "url", *DIMS, "coder", "notes"]
KEY_COLS = ["sample_id", "doc_id", "idx", "arm", "lang", "country", *[f"student_{d}" for d in DIMS],
            *[f"teacher_{d}" for d in DIMS]]


def _blind_row(sid: str, p: Dict) -> List:
    return [sid, p["country"], p["source"], p["lang"], p["date"], p["text"], p["url"], *[""] * len(DIMS), "", ""]


def _key_row(sid: str, p: Dict, teacher: Optional[np.ndarray]) -> List:
    t = [""] * len(DIMS) if teacher is None else [round(float(x), 4) for x in teacher]
    return [sid, p["doc_id"], p["idx"], p["arm"], p["lang"], p["country"], *[round(p[d], 4) for d in DIMS], *t]


def _write_csv(path: Path, header: List[str], rows: List[List]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(header)
        wr.writerows(rows)


# ---------------------------------------------------------------------------------------------- top-up
def lang_deficits(counts: Dict[str, int], langs: Iterable[str], per_lang_min: int) -> Dict[str, int]:
    """Sentences still needed per language so that every language with data reaches `per_lang_min`."""
    return {lg: per_lang_min - counts.get(lg, 0) for lg in sorted(set(langs)) if counts.get(lg, 0) < per_lang_min}


def allocate(n: int, sizes: Dict[str, int]) -> Dict[str, int]:
    """Split n draws over strata sqrt-proportionally to their size (largest remainder; deterministic)."""
    w = {k: math.sqrt(v) for k, v in sizes.items() if v > 0}
    if n <= 0 or not w:
        return {}
    tot = sum(w.values())
    raw = {k: n * v / tot for k, v in w.items()}
    out = {k: int(x) for k, x in raw.items()}
    for k in sorted(raw, key=lambda k: (out[k] - raw[k], str(k)))[:n - sum(out.values())]:
        out[k] += 1
    return {k: v for k, v in out.items() if v}


def topup_sample(con: sqlite3.Connection, cc: sqlite3.Connection, per_lang_min: int, blind_in: Path, key_in: Path,
                 blind_out: Path, key_out: Path, prefix: Optional[str] = None, teacher: bool = True) -> Dict:
    """Add sentences to an existing blind sample until every language with scored data has >= per_lang_min rows.

    Same arms as export (half enriched, half random; an enriched shortfall is filled from random), same exclusions
    (teacher sentences and sentences already in the sample), and additionally no exact-duplicate texts. Within a language the extra draws are split over its
    countries sqrt-proportionally. Writes combined blind + key files (existing rows first, unchanged); new ids are
    `prefix` + 3 digits (default: the next free v<k>, e.g. v1000.. after the v0000.. export) and must not collide."""
    old_blind = list(csv.reader(blind_in.open(encoding="utf-8")))
    old_key = list(csv.reader(key_in.open(encoding="utf-8")))
    assert old_blind[0] == BLIND_COLS and old_key[0] == KEY_COLS, "unexpected sample/key columns"
    kh = {c: i for i, c in enumerate(KEY_COLS)}
    texts = {r[BLIND_COLS.index("text")] for r in old_blind[1:]}
    counts: Dict[str, int] = {}
    seen = {(a, int(b)) for a, b in con.execute("SELECT doc_id, idx FROM teacher")}
    for r in old_key[1:]:
        counts[r[kh["lang"]]] = counts.get(r[kh["lang"]], 0) + 1
        seen.add((r[kh["doc_id"]], int(r[kh["idx"]])))
    sizes: Dict[str, Dict[str, int]] = {}
    for c, lg, n in con.execute("SELECT d.country, d.lang, count(*) FROM sent_scores s JOIN docs d USING(doc_id)"
                                " WHERE d.present=1 GROUP BY 1, 2"):
        if lg:
            sizes.setdefault(lg, {})[c] = n
    picks: List[Dict] = []
    for lg, need in lang_deficits(counts, sizes, per_lang_min).items():
        for country, q in sorted(allocate(need, sizes[lg]).items()):
            got = _draw_stratum(con, cc, country, lg, (("enriched", q // 2),), seen, texts)
            got += _draw_stratum(con, cc, country, lg, (("random", q - len(got)),), seen, texts)
            if len(got) < q:
                logger.warning("top-up %s/%s: %d of %d sentences available", country, lg, len(got), q)
            picks += got
    random.Random(SETTINGS.seed + 2).shuffle(picks)
    if prefix is None:
        prefix = f"v{1 + max((int(r[0][1]) for r in old_key[1:] if len(r[0]) == 5 and r[0][1:].isdigit()), default=0)}"
    ids = [f"{prefix}{i:03d}" for i in range(len(picks))]
    clash = set(ids) & {r[0] for r in old_key[1:]}
    assert not clash, f"sample_id collision: {sorted(clash)[:5]}"
    tscores = _teacher_scores([p["text"] for p in picks]) if teacher and picks else [None] * len(picks)
    _write_csv(blind_out, BLIND_COLS, old_blind[1:] + [_blind_row(i, p) for i, p in zip(ids, picks)])
    _write_csv(key_out, KEY_COLS, old_key[1:] + [_key_row(i, p, t) for i, p, t in zip(ids, picks, tscores)])
    by_lang: Dict[str, int] = {}
    for p in picks:
        by_lang[p["lang"]] = by_lang.get(p["lang"], 0) + 1
    return {"added": len(picks), "added_by_lang": by_lang, "total": len(old_key) - 1 + len(picks),
            "teacher": bool(teacher), "blind_file": str(blind_out), "key_file": str(key_out)}


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


# ---------------------------------------------------------------------------------------------- scoring
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + ord(ch.upper()) - 64
    return n - 1


def read_xlsx(path: Path, sheet: str = "Coding") -> List[Dict[str, str]]:
    """Rows of one worksheet as dicts keyed by the header row (stdlib only; first sheet if `sheet` is absent)."""
    import xml.etree.ElementTree as ET
    import zipfile

    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        sheets = ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", _NS)
        rid = next((e.get(_REL) for e in sheets if e.get("name") == sheet), sheets[0].get(_REL))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = next(e.get("Target") for e in rels if e.get("Id") == rid)
        target = target.lstrip("/") if target.startswith("/") else "xl/" + target
        root = ET.fromstring(z.read(target))
    grid: List[List[str]] = []
    for row in root.iter(f"{{{_NS['m']}}}row"):
        vals: Dict[int, str] = {}
        for c in row.findall("m:c", _NS):
            t, v = c.get("t"), c.find("m:v", _NS)
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{_NS['m']}}}t"))
            else:
                val = v.text if v is not None and v.text is not None else ""
            vals[_col_index(c.get("r"))] = val
        grid.append([vals.get(i, "") for i in range(max(vals) + 1)] if vals else [])
    if not grid:
        return []
    head = [h.strip() for h in grid[0]]
    return [{h: (r[i] if i < len(r) else "") for i, h in enumerate(head) if h} for r in grid[1:] if any(x.strip() for x in r)]


def read_coded(path: str) -> List[Dict[str, str]]:
    """A returned coding file (.xlsx coding sheet or .csv). 0/1 cells are normalised to "0"/"1" (Excel may store 1.0)."""
    p = Path(path)
    rows = read_xlsx(p) if p.suffix.lower() in (".xlsx", ".xlsm") else list(csv.DictReader(p.open(encoding="utf-8-sig")))
    for r in rows:
        for d in DIMS:
            v = str(r.get(d, "") or "").strip()
            try:
                r[d] = str(int(float(v))) if v and float(v) in (0.0, 1.0) else v
            except ValueError:
                r[d] = v
    return rows


def krippendorff_alpha(units: List[List[Optional[str]]]) -> Optional[float]:
    """Krippendorff's alpha, nominal metric. units = one list of coder values per unit (None = not coded).
    Units with fewer than two values are not pairable and are dropped. None when undefined (no variation)."""
    from collections import Counter

    o: Counter = Counter()
    for vals in units:
        v = [x for x in vals if x is not None]
        m = len(v)
        if m < 2:
            continue
        for i, a in enumerate(v):
            for j, b in enumerate(v):
                if i != j:
                    o[(a, b)] += 1 / (m - 1)
    n_c: Counter = Counter()
    for (a, _), w in o.items():
        n_c[a] += w
    n = sum(n_c.values())
    if n <= 1:
        return None
    d_o = sum(w for (a, b), w in o.items() if a != b) / n
    d_e = sum(n_c[a] * n_c[b] for a in n_c for b in n_c if a != b) / (n * (n - 1))
    return None if d_e == 0 else round(1 - d_o / d_e, 4)


def score(coded_files: List[str], key_path: Path = KEY) -> Dict:
    """Agreement of human codes with student and teacher scores, per coder and on rows where all coders agree,
    plus inter-coder Krippendorff's alpha. Each file is one coder (name: its `coder` column, else the file stem)."""
    from .tone import agreement

    key = {r["sample_id"]: r for r in csv.DictReader(key_path.open(encoding="utf-8"))}
    coders: Dict[str, Dict[str, Dict[str, str]]] = {}
    for f in coded_files:
        rows = [r for r in read_coded(f) if r.get("sample_id") in key]
        names = {(r.get("coder") or "").strip() for r in rows} - {""}
        name = names.pop() if len(names) == 1 else Path(f).stem
        while name in coders:
            name += "'"
        coders[name] = {r["sample_id"]: r for r in rows}

    def fit(h: np.ndarray, ids: List[str], d: str, who: str) -> Dict:
        col = np.array([key[i][f"{who}_{d}"] for i in ids])
        ok = col != ""
        return agreement(h[ok], col[ok].astype(float)) if ok.any() else {"n": 0}

    res: Dict[str, Dict] = {"coders": {c: len(v) for c, v in coders.items()}, "dims": {}}
    for d in DIMS:
        m: Dict[str, object] = {"per_coder": {}}
        for c, rows in coders.items():
            ids = [i for i, r in rows.items() if r.get(d) in ("0", "1")]
            if not ids:
                continue
            h = np.array([float(rows[i][d]) for i in ids])
            rnd = np.array([key[i]["arm"] == "random" for i in ids])
            m["per_coder"][c] = {"human_vs_student": fit(h, ids, d, "student"), "human_vs_teacher": fit(h, ids, d, "teacher"),
                                 "random_arm_human_vs_student": fit(h[rnd], list(np.array(ids)[rnd]), d, "student")
                                 if rnd.any() else None}
        if len(coders) >= 2:
            units = [[(rows.get(i) or {}).get(d) if (rows.get(i) or {}).get(d) in ("0", "1") else None
                      for rows in coders.values()] for i in key]
            m["krippendorff_alpha"] = krippendorff_alpha(units)
            m["n_double_coded"] = sum(sum(v is not None for v in u) >= 2 for u in units)
            agreed = [(i, u[0]) for i, u in zip(key, units) if None not in u and len(set(u)) == 1]
            if agreed:
                h = np.array([float(v) for _, v in agreed])
                ids = [i for i, _ in agreed]
                m["consensus"] = {"human_vs_student": fit(h, ids, d, "student"), "human_vs_teacher": fit(h, ids, d, "teacher")}
        res["dims"][d] = m
    return res


def _fmt(x: Optional[float]) -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.2f}"


def report_md(res: Dict, coded_files: List[str]) -> str:
    L = ["# Human validation of tone scores", "", f"Coded files: {', '.join(f'`{f}`' for f in coded_files)}.",
         "Coders (rows matched to the key): " + ", ".join(f"{c} ({n})" for c, n in res["coders"].items()) + ".", "",
         "Model scores are binarised at 0.5 for F1/kappa; AUC uses the continuous score. "
         "'consensus' = rows on which all coders gave the same code.", "",
         "| dimension | coder | n | human pos. rate | student AUC | student F1 | student kappa | teacher AUC | teacher F1 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for d, m in res["dims"].items():
        rows = list(m["per_coder"].items()) + ([("consensus", m["consensus"])] if "consensus" in m else [])
        for c, v in rows:
            hs, ht = v["human_vs_student"], v["human_vs_teacher"]
            L.append(f"| {d} | {c} | {hs['n']} | {_fmt(hs.get('teacher_pos'))} | {_fmt(hs.get('auc'))} | {_fmt(hs.get('f1'))}"
                     f" | {_fmt(hs.get('kappa'))} | {_fmt(ht.get('auc'))} | {_fmt(ht.get('f1'))} |")
    if len(res["coders"]) >= 2:
        L += ["", "## Inter-coder reliability (Krippendorff's alpha, nominal)", "", "| dimension | units coded by >= 2 | alpha |",
              "|---|---|---|"]
        L += [f"| {d} | {m['n_double_coded']} | {_fmt(m['krippendorff_alpha'])} |" for d, m in res["dims"].items()]
    return "\n".join(L) + "\n"


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(prog="run validate")
    ap.add_argument("coded", nargs="+", help="returned coding files (.xlsx or .csv), one per coder")
    ap.add_argument("--key", default=str(KEY), help=f"answer key (default {KEY})")
    ap.add_argument("--out", default=None, help="directory for human_validation.md/.json (default: reports/semantic"
                                                " and index/semantic/aggregates)")
    a = ap.parse_args(argv)
    res = score(a.coded, Path(a.key))
    md = report_md(res, a.coded)
    jdir, mdir = (Path(a.out), Path(a.out)) if a.out else (AGG_DIR, REPORT_DIR)
    for p in (jdir, mdir):
        p.mkdir(parents=True, exist_ok=True)
    (jdir / "validation_human.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    (mdir / "human_validation.md").write_text(md, encoding="utf-8")
    print(md)
