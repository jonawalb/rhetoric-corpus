"""Stages `train` and `score`: tone students on e5 sentence embeddings, then scoring of corpus sentences.

Student = one logistic regression per dimension on "query: " e5 embeddings, trained on the teacher's soft
labels (each sentence enters twice, as positive with weight p and negative with weight 1-p). C is chosen by
3-fold cross-validation on the training split. Held-out agreement with the teacher (documents never seen in
training) is written to index/semantic/aggregates/validation.json and reports/semantic/tone_validation.md.
These numbers measure agreement with the NLI teacher, NOT validity against human judgement.

Scored sentences per doc: idx < sent_cap, plus target-mentioning sentences with idx < mention_cap; sentences
shorter than 15 characters (6 for CJK) are skipped. Sentence scores are stored as integers 0..1000 (p x 1000);
doc tone = mean sentence probability (= expected share of sentences expressing the dimension).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
import zlib
from typing import Dict, List, Optional

import numpy as np

from . import store
from .config import AGG_DIR, DIMS, REPORT_DIR, SETTINGS, TONE_MODEL
from .embed import E5Encoder

logger = logging.getLogger(__name__)
CJK = ("zh", "ko", "ja")


def _min_len(lang: Optional[str]) -> int:
    return 6 if lang in CJK else 15


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def _fit_soft(X: np.ndarray, p: np.ndarray, C: float):
    from sklearn.linear_model import LogisticRegression

    Xd = np.vstack([X, X])
    yd = np.r_[np.ones(len(X)), np.zeros(len(X))]
    wd = np.r_[p, 1 - p] + 1e-6
    return LogisticRegression(C=C, max_iter=2000).fit(Xd, yd, sample_weight=wd)


def _soft_logloss(p: np.ndarray, q: np.ndarray) -> float:
    q = np.clip(q, 1e-6, 1 - 1e-6)
    return float(-np.mean(p * np.log(q) + (1 - p) * np.log(1 - q)))


def agreement(teacher: np.ndarray, student: np.ndarray) -> Dict[str, float]:
    """Teacher-student agreement for one dimension (binary at 0.5 + rank/linear correlation)."""
    from scipy.stats import pearsonr, spearmanr
    from sklearn.metrics import cohen_kappa_score, f1_score, roc_auc_score

    tb, sb = teacher >= 0.5, student >= 0.5
    if len(teacher) == 0:
        return {"n": 0}
    out = {"n": int(len(teacher)), "teacher_pos": round(float(tb.mean()), 4), "student_pos": round(float(sb.mean()), 4),
           "teacher_mean": round(float(teacher.mean()), 4), "student_mean": round(float(student.mean()), 4),
           "accuracy": round(float((tb == sb).mean()), 4)}
    if len(teacher) >= 10 and teacher.std() > 0 and student.std() > 0:
        out["pearson"] = round(float(pearsonr(teacher, student)[0]), 4)
        out["spearman"] = round(float(spearmanr(teacher, student)[0]), 4)
    if 0 < tb.sum() < len(tb):
        out["kappa"] = round(float(cohen_kappa_score(tb, sb)), 4)
        out["f1"] = round(float(f1_score(tb, sb)), 4)
        out["auc"] = round(float(roc_auc_score(tb, student)), 4)
    return out


def _calibrate_intercept(z: np.ndarray, target_mean: float) -> float:
    """Intercept b such that mean(sigmoid(z + b)) == target_mean (bisection; mean is monotone in b)."""
    lo, hi = -20.0, 20.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if _sigmoid(z + mid).mean() < target_mean:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _ensure_teacher_emb(con: sqlite3.Connection, enc: Optional[E5Encoder]) -> Optional[E5Encoder]:
    todo = con.execute("SELECT sid, text FROM teacher WHERE emb IS NULL").fetchall()
    if todo:
        enc = enc or E5Encoder()
        e = enc.encode([t for _, t in todo], "query: ", max_length=128, batch=128).astype(np.float16)
        con.executemany("UPDATE teacher SET emb=? WHERE sid=?", [(e[k].tobytes(), todo[k][0]) for k in range(len(todo))])
        con.commit()
    return enc


def run_train(con: sqlite3.Connection, enc: Optional[E5Encoder] = None, force: bool = False) -> Dict[str, object]:
    """Fit the students on the train split and evaluate them on the held-out split.

    Skipped (the existing model is kept, so no full re-score is triggered) unless the labelled teacher sample
    grew by >= 10 % since the last fit, the hypotheses changed, or force=True."""
    from .teacher import HYP_V

    n_lab = con.execute("SELECT count(*) FROM teacher WHERE hyp_v=?", (HYP_V,)).fetchone()[0]
    last = store.get_meta(con, "tone_train")
    if TONE_MODEL.exists() and last and not force:
        prev = json.loads(last)
        if prev.get("hyp_v") == HYP_V and n_lab < 1.1 * prev.get("n", 0):
            return {"skipped": True, "model_version": prev.get("version"), "labelled": n_lab}
    _ensure_teacher_emb(con, enc)
    rows = con.execute(f"SELECT doc_id, lang, country, kind, split, emb, {', '.join(DIMS)} FROM teacher"
                       " WHERE hyp_v=? AND emb IS NOT NULL", (HYP_V,)).fetchall()
    if len(rows) < 200:
        raise SystemExit("train: fewer than 200 labelled teacher rows; run --stage teacher first")
    X = np.stack([np.frombuffer(r[5], dtype=np.float16).astype(np.float32) for r in rows])
    P = np.array([r[6:] for r in rows], dtype=np.float64)
    split = np.array([r[4] for r in rows])
    lang = np.array([r[1] or "?" for r in rows])
    arm = np.array([r[3] for r in rows])
    country = np.array([r[2] for r in rows])
    tr, ho = split == "train", split == "heldout"
    fold = np.array([zlib.crc32(r[0].encode()) % 3 for r in rows])
    W, b, Cs, metrics = np.zeros((len(DIMS), X.shape[1])), np.zeros(len(DIMS)), [], {}
    for j, dim in enumerate(DIMS):
        best = None
        for C in (0.25, 1.0, 4.0, 16.0):
            losses = []
            for f in range(3):
                fit_idx, val_idx = tr & (fold != f), tr & (fold == f)
                m = _fit_soft(X[fit_idx], P[fit_idx, j], C)
                losses.append(_soft_logloss(P[val_idx, j], m.predict_proba(X[val_idx])[:, 1]))
            if best is None or np.mean(losses) < best[1]:
                best = (C, float(np.mean(losses)))
        m = _fit_soft(X[tr], P[tr, j], best[0])
        W[j], b[j] = m.coef_[0], m.intercept_[0]
        cal = tr & (arm == "random")
        b_fit = b[j]
        if (arm[tr] == "active").any() and cal.sum() >= 100:
            b[j] = _calibrate_intercept(X[cal] @ W[j], float(P[cal, j].mean()))
        Cs.append(best[0])
        s = _sigmoid(X[ho] @ W[j] + b[j])
        t = P[ho, j]
        metrics[dim] = {"C": best[0], "intercept_fit": round(float(b_fit), 4), "intercept_calibrated": round(float(b[j]), 4),
                        "overall": agreement(t, s),
                        "random_arm": agreement(t[arm[ho] == "random"], s[arm[ho] == "random"]),
                        "by_lang": {lg: agreement(t[lang[ho] == lg], s[lang[ho] == lg]) for lg in sorted(set(lang[ho]))},
                        "by_country": {c: agreement(t[country[ho] == c], s[country[ho] == c]) for c in sorted(set(country[ho]))}}
    version = hashlib.sha1(W.tobytes() + b.tobytes()).hexdigest()[:10]
    np.savez(TONE_MODEL, W=W.astype(np.float32), b=b.astype(np.float32), dims=np.array(DIMS), version=version, hyp_v=HYP_V)
    summary = {"model_version": version, "hyp_v": HYP_V, "n_train": int(tr.sum()), "n_heldout": int(ho.sum()),
               "langs_train": {lg: int(((lang == lg) & tr).sum()) for lg in sorted(set(lang))},
               "arms_train": {a_: int(((arm == a_) & tr).sum()) for a_ in sorted(set(arm))}, "dims": metrics,
               "note": "Agreement between the student classifiers and the zero-shot NLI teacher on held-out documents. "
                       "Not a measure of agreement with human coders."}
    store.set_meta(con, "tone_train", json.dumps({"hyp_v": HYP_V, "n": n_lab, "version": version}))
    con.commit()
    AGG_DIR.mkdir(parents=True, exist_ok=True)
    (AGG_DIR / "validation.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    _write_report(summary)
    return {"model_version": version, "n_train": summary["n_train"], "n_heldout": summary["n_heldout"],
            "kappa": {d: metrics[d]["overall"].get("kappa") for d in DIMS},
            "pearson": {d: metrics[d]["overall"].get("pearson") for d in DIMS}}


def _write_report(s: Dict) -> None:
    L = ["# Tone classifiers: held-out teacher-student agreement", "",
         f"Model `{s['model_version']}` (hypotheses `{s['hyp_v']}`); train n={s['n_train']} {s.get('arms_train', {})},"
         f" held-out n={s['n_heldout']} (held-out = whole documents not used in training; random + target-mention arms only).", "",
         "Doc tone uses the mean probability, so the continuous measures (AUC, Pearson r, and teacher vs student mean) matter"
         " more than kappa at 0.5, which is unstable for rare dimensions.", "",
         "**What this measures:** how well the fast student (logistic regression on e5 embeddings) reproduces the slow"
         " zero-shot NLI teacher. It does **not** measure validity against human judgement; see"
         " reports/semantic/validation_sample.csv for the human-coding sample.", "",
         "Binary metrics threshold both teacher and student at p = 0.5. AUC = student score ranking teacher-positive"
         " sentences above teacher-negative ones.", "",
         "| dimension | n | teacher pos. rate | student pos. rate | teacher mean p | student mean p | accuracy | kappa | F1 | AUC | Pearson r | random-arm kappa | random-arm r | random-arm AUC |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for d, m in s["dims"].items():
        o, r = m["overall"], m["random_arm"]
        L.append(f"| {d} | {o['n']} | {o['teacher_pos']:.3f} | {o['student_pos']:.3f} | {o['teacher_mean']:.3f} | {o['student_mean']:.3f}"
                 f" | {o['accuracy']:.3f} | {o.get('kappa', float('nan')):.3f}"
                 f" | {o.get('f1', float('nan')):.3f} | {o.get('auc', float('nan')):.3f} | {o.get('pearson', float('nan')):.3f}"
                 f" | {r.get('kappa', float('nan')):.3f} | {r.get('pearson', float('nan')):.3f} | {r.get('auc', float('nan')):.3f} |")
    L += ["", "## By language (kappa / Pearson r / n)", "", "| dimension | " + " | ".join(sorted(next(iter(s['dims'].values()))['by_lang'])) + " |",
          "|---|" + "---|" * len(next(iter(s['dims'].values()))['by_lang'])]
    for d, m in s["dims"].items():
        cells = [f"{v.get('kappa', float('nan')):.2f} / {v.get('pearson', float('nan')):.2f} / {v['n']}" for _, v in sorted(m["by_lang"].items())]
        L.append(f"| {d} | " + " | ".join(cells) + " |")
    L += ["", "Languages with n < 50 in the held-out split give unstable estimates; read them as indicative only.", ""]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "tone_validation.md").write_text("\n".join(L), encoding="utf-8")


def load_model():
    z = np.load(TONE_MODEL, allow_pickle=False)
    return z["W"], z["b"], str(z["version"])


def run_score(con: sqlite3.Connection, full: bool = False, budget_s: Optional[float] = None,
              enc: Optional[E5Encoder] = None) -> Dict[str, object]:
    """Score sentences of docs not scored with the current model version (all docs with full=True)."""
    W, b, version = load_model()
    if full:
        con.execute("UPDATE docs SET scored_v=NULL")
    todo = con.execute("SELECT doc_id, crow, lang FROM docs WHERE present=1 AND targets_v IS NOT NULL"
                       " AND (scored_v IS NULL OR scored_v<>?) ORDER BY date DESC", (version,)).fetchall()
    logger.info("score: %d docs to score (model %s)", len(todo), version)
    if not todo:
        return {"docs_scored": 0, "model_version": version}
    enc = enc or E5Encoder()
    cc = store.corpus()
    t0, nd, ns = time.time(), 0, 0
    for i in range(0, len(todo), 1500):
        part = todo[i:i + 1500]
        sents = store.sentences_for(cc, [r[1] for r in part], max_idx=SETTINGS.mention_cap)
        ids = [r[0] for r in part]
        ment: Dict[str, set] = {}
        for k in range(0, len(ids), 500):
            sub = ids[k:k + 500]
            for d, idx in con.execute(f"SELECT DISTINCT doc_id, idx FROM mentions WHERE doc_id IN ({','.join('?' * len(sub))})", sub):
                ment.setdefault(d, set()).add(idx)
        items: List[tuple] = []
        for doc_id, crow, lang in part:
            m = ment.get(doc_id, set())
            for idx, text in sents.get(crow, []):
                if (idx < SETTINGS.sent_cap or idx in m) and len(text.strip()) >= _min_len(lang):
                    items.append((doc_id, idx, text))
        X = enc.encode([t for _, _, t in items], "query: ", max_length=128, batch=256) if items else np.zeros((0, 384))
        Pr = _sigmoid(X @ W.T + b)
        con.executemany("DELETE FROM sent_scores WHERE doc_id=?", [(d,) for d in ids])
        con.executemany(f"INSERT INTO sent_scores(doc_id, idx, crc, {', '.join(DIMS)}) VALUES(?,?,?,?,?,?,?,?,?)",
                        [(d, idx, store.crc(t), *[int(round(v * 1000)) for v in Pr[k]]) for k, (d, idx, t) in enumerate(items)])
        agg: Dict[str, List[np.ndarray]] = {}
        for k, (d, _, _) in enumerate(items):
            agg.setdefault(d, []).append(Pr[k])
        rows = []
        for d in ids:
            arr = np.array(agg.get(d, []))
            means = arr.mean(0) if len(arr) else np.full(len(DIMS), np.nan)
            rows.append((d, len(arr), *[None if np.isnan(v) else float(v) for v in means], version))
        con.executemany(f"INSERT OR REPLACE INTO doc_tone(doc_id, nsent, {', '.join(DIMS)}, model_v) VALUES(?,?,?,?,?,?,?,?,?)", rows)
        con.executemany("UPDATE docs SET scored_v=? WHERE doc_id=?", [(version, d) for d in ids])
        con.commit()
        nd += len(part)
        ns += len(items)
        el = time.time() - t0
        logger.info("score: %d/%d docs, %d sentences, %.0fs (%.0f sent/s)", nd, len(todo), ns, el, ns / el)
        if budget_s and el > budget_s:
            logger.warning("score: time budget reached; %d docs left", len(todo) - nd)
            break
    cc.close()
    return {"docs_scored": nd, "sentences_scored": ns, "docs_left": len(todo) - nd, "model_version": version}
