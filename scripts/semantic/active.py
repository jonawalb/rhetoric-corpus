"""Stage `active` (runs once; --full repeats it): enrich the teacher sample with likely positives of rare dimensions.

With the zero-shot teacher, threat, grievance and escalation are positive in only 1-5 % of sentences, so a random
sample holds too few positives to train on. This stage embeds a random pool of sentences from TRAINING-split
documents, scores them with the current students, and sends the top `per_dim` sentences per dimension to the
teacher (arm `kind = 'active'`, split always 'train', never evaluated). Because these rows over-represent
positives, tone.run_train re-calibrates each student's intercept so that its mean prediction on the random-arm
training rows equals the teacher's mean there (calibration-in-the-large on the natural distribution).
"""
from __future__ import annotations

import logging
import random
import sqlite3
from typing import Dict, List, Optional

import numpy as np

from . import store
from .config import DIMS, SETTINGS
from .embed import E5Encoder
from .teacher import HYP_V, eligible, label, split_of

logger = logging.getLogger(__name__)
ACTIVE_DIMS = ("threat", "grievance", "escalation", "deescalation", "hostility")


def run_active(con: sqlite3.Connection, per_dim: int = 700, pool_docs: int = 40000, per_doc: int = 4,
               enc: Optional[E5Encoder] = None, force: bool = False) -> Dict:
    from .tone import _sigmoid, load_model

    if store.get_meta(con, "active_v") and not force:  # once; later hypothesis edits relabel these rows in place
        return {"skipped": True}
    W, b, version = load_model()
    rng = random.Random(SETTINGS.seed + 7)
    docs = [r for r in con.execute("SELECT doc_id, crow, country, lang, source FROM docs WHERE present=1 ORDER BY doc_id")
            if split_of(r[0]) == "train"]
    docs = rng.sample(docs, min(pool_docs, len(docs)))
    taken = {(d, i) for d, i in con.execute("SELECT doc_id, idx FROM teacher")}
    cc = store.corpus()
    pool: List[tuple] = []
    for k in range(0, len(docs), 1000):
        part = docs[k:k + 1000]
        sents = store.sentences_for(cc, [r[1] for r in part], max_idx=SETTINGS.sent_cap)
        for r in part:
            cand = [(i, t) for i, t in sents.get(r[1], []) if eligible(t, r[3]) and (r[0], i) not in taken]
            for i, t in rng.sample(cand, min(per_doc, len(cand))):
                pool.append((r, i, t))
    cc.close()
    logger.info("active: scoring a pool of %d sentences from %d training documents", len(pool), len(docs))
    enc = enc or E5Encoder()
    X = enc.encode([t for _, _, t in pool], "query: ", max_length=128, batch=256)
    P = _sigmoid(X @ W.T + b)
    chosen: Dict[int, str] = {}
    for d in ACTIVE_DIMS:
        j = DIMS.index(d)
        n = 0
        for k in np.argsort(-P[:, j]):
            if n >= per_dim:
                break
            if int(k) not in chosen:
                chosen[int(k)] = d
                n += 1
    rows = [(pool[k][0][0], pool[k][1], pool[k][2], pool[k][0][2], pool[k][0][3], pool[k][0][4],
             f"{pool[k][0][2]}|{pool[k][0][3]}|{pool[k][0][4]}", "active", "train") for k in chosen]
    con.executemany("INSERT OR IGNORE INTO teacher(doc_id, idx, text, country, lang, source, stratum, kind, split)"
                    " VALUES(?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    n = label(con)
    store.set_meta(con, "active_v", HYP_V)
    con.commit()
    return {"pool": len(pool), "active_rows": len(rows), "labelled": n, "from_model": version}
