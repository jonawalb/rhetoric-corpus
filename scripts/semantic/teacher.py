"""Stage `teacher`: stratified sentence sample -> zero-shot NLI labels (bge-m3-zeroshot-v2.0, local).

Sample design (deterministic, seed in config): strata = country x language x source. Stratum quota is
proportional to sqrt(docs in stratum), clipped to [30, teacher_stratum_cap], scaled to teacher_total. Within a
stratum half the sentences are drawn at random (one per randomly drawn doc, idx < sent_cap) and half from
sentences that mention a non-self target (enriches political content so rarer dimensions have positives).
Each row records its arm (`kind` = random | mention) so metrics can be reported on the random arm alone.
Held-out split: 20 % of documents by a hash of doc_id (whole documents, so no leakage between splits).

Label = P(entailment) from the NLI softmax for the dimension's hypothesis (config.HYPOTHESES).
Incremental: re-running tops up new strata (new countries/sources) and labels only unlabeled rows.
"""
from __future__ import annotations

import hashlib
import logging
import math
import random
import sqlite3
import time
import zlib
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from . import store
from .config import DIMS, HYPOTHESES, NLI_MODEL, SETTINGS, device, model_path

logger = logging.getLogger(__name__)
CJK = ("zh", "ko", "ja")
HYP_V = hashlib.sha1((NLI_MODEL + repr(sorted(HYPOTHESES.items()))).encode()).hexdigest()[:10]


def split_of(doc_id: str, frac: float = SETTINGS.heldout_frac) -> str:
    return "heldout" if zlib.crc32(doc_id.encode()) % 1000 < frac * 1000 else "train"


def eligible(text: str, lang: Optional[str]) -> bool:
    n = len(text.strip())
    return n >= (10 if lang in CJK else 25)


def quotas(sizes: Dict[str, int], total: int, cap: int) -> Dict[str, int]:
    """sqrt-proportional allocation, each stratum in [min(30, size), cap], never above its size."""
    w = {k: math.sqrt(v) for k, v in sizes.items() if v > 0}
    tot = sum(w.values()) or 1.0
    return {k: int(min(sizes[k], cap, max(30, round(total * w[k] / tot)))) for k in w}


def draw_sample(con: sqlite3.Connection) -> int:
    """Top up the teacher table to the stratified design. Returns rows added."""
    docs = con.execute("SELECT doc_id, crow, country, lang, source FROM docs WHERE present=1 ORDER BY doc_id").fetchall()
    by_stratum: Dict[str, List[Tuple]] = defaultdict(list)
    for r in docs:
        by_stratum[f"{r[2]}|{r[3]}|{r[4]}"].append(r)
    have = defaultdict(int)
    for s, n in con.execute("SELECT stratum, count(*) FROM teacher GROUP BY stratum"):
        have[s] = n
    taken = {(d, i) for d, i in con.execute("SELECT doc_id, idx FROM teacher")}
    q = quotas({k: len(v) for k, v in by_stratum.items()}, SETTINGS.teacher_total, SETTINGS.teacher_stratum_cap)
    ment = defaultdict(list)
    for doc_id, idx in con.execute("SELECT DISTINCT doc_id, idx FROM mentions WHERE self=0 AND idx < ?", (SETTINGS.sent_cap,)):
        ment[doc_id].append(idx)
    cc = store.corpus()
    added = 0
    for stratum in sorted(q):
        need = q[stratum] - have[stratum]
        if need <= 0:
            continue
        rows = by_stratum[stratum]
        srng = random.Random(f"{SETTINGS.seed}:{stratum}:{have[stratum]}")
        pool = rows[:]
        srng.shuffle(pool)
        mdocs = [r for r in pool if r[0] in ment]
        picks: List[Tuple[Tuple, int, str]] = []
        n_mention = need // 2 if mdocs else 0
        for arm, src, n in (("mention", mdocs, n_mention), ("random", pool, need - n_mention)):
            got = 0
            for r in src:
                if got >= n:
                    break
                sents = store.sentences_for(cc, [r[1]], max_idx=SETTINGS.sent_cap).get(r[1], [])
                cand = [(i, t) for i, t in sents if eligible(t, r[3]) and (r[0], i) not in taken]
                if arm == "mention":
                    cand = [(i, t) for i, t in cand if i in set(ment[r[0]])]
                if not cand:
                    continue
                i, t = cand[srng.randrange(len(cand))]
                taken.add((r[0], i))
                picks.append((r, i, arm))
                got += 1
        for r, i, arm in picks:
            text = store.sentence_text(cc, r[1], i)
            con.execute("INSERT OR IGNORE INTO teacher(doc_id, idx, text, country, lang, source, stratum, kind, split)"
                        " VALUES(?,?,?,?,?,?,?,?,?)", (r[0], i, text, r[2], r[3], r[4], stratum, arm, split_of(r[0])))
            added += 1
        con.commit()
    cc.close()
    logger.info("teacher: %d sample rows added (%d strata)", added, len(q))
    return added


def label(con: sqlite3.Connection, batch: int = 24) -> int:
    """NLI-label rows for the current hypothesis set. Unlabelled rows get all dimensions; rows labelled under an
    older set get only the dimensions whose hypothesis changed (meta 'teacher_hyps' records the labelled set)."""
    import json

    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    prev = json.loads(store.get_meta(con, "teacher_hyps") or "{}")
    changed = [d for d in DIMS if prev.get("model") != NLI_MODEL or prev.get(d) != HYPOTHESES[d]]
    jobs = [(list(DIMS), con.execute("SELECT sid, text FROM teacher WHERE hyp_v IS NULL ORDER BY length(text)").fetchall())]
    if changed:
        jobs.append((changed, con.execute("SELECT sid, text FROM teacher WHERE hyp_v IS NOT NULL AND hyp_v<>?"
                                          " ORDER BY length(text)", (HYP_V,)).fetchall()))
    else:
        con.execute("UPDATE teacher SET hyp_v=? WHERE hyp_v IS NOT NULL", (HYP_V,))
    total = sum(len(rows) for _, rows in jobs)
    if not total:
        return 0
    dev = device()
    path = model_path(NLI_MODEL)
    tok = AutoTokenizer.from_pretrained(path)
    model = AutoModelForSequenceClassification.from_pretrained(
        path, dtype=torch.float16 if dev == "mps" else torch.float32).to(dev).eval()
    ent = {v.lower(): k for k, v in model.config.id2label.items()}["entailment"]
    t0, done = time.time(), 0
    with torch.inference_mode():
        for dims, todo in jobs:
            hyps = [HYPOTHESES[d] for d in dims]
            logger.info("teacher: %d rows x %d dimensions (%s)", len(todo), len(dims), ",".join(dims))
            for i in range(0, len(todo), batch):
                part = todo[i:i + batch]
                prem = [t for _, t in part for _ in hyps]
                hyp = [h for _ in part for h in hyps]
                enc = tok(prem, hyp, padding=True, truncation="only_first", max_length=160, return_tensors="pt").to(dev)
                p = model(**enc).logits.float().softmax(-1)[:, ent].reshape(len(part), len(hyps)).cpu().numpy()
                con.executemany(f"UPDATE teacher SET hyp_v=?, {', '.join(d + '=?' for d in dims)} WHERE sid=?",
                                [(HYP_V, *map(float, p[k]), part[k][0]) for k in range(len(part))])
                done += len(part)
                if (i // batch) % 50 == 0:
                    con.commit()
                    logger.info("teacher: labelled %d/%d (%.1f rows/s)", done, total, done / max(time.time() - t0, 1e-6))
    store.set_meta(con, "teacher_hyps", json.dumps({"model": NLI_MODEL, **HYPOTHESES}))
    con.commit()
    return total


def run_teacher(con: sqlite3.Connection) -> Dict[str, int]:
    added = draw_sample(con)
    n = label(con)
    total = con.execute("SELECT count(*) FROM teacher").fetchone()[0]
    return {"sample_added": added, "labelled": n, "sample_total": total, "hyp_v": HYP_V}
