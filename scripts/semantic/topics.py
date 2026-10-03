"""Stage `topics`: cross-lingual topic clusters over document embeddings.

Doc vector = mean of its passage embeddings. To reduce clustering by language rather than subject, each
language's mean vector is subtracted before clustering (language centring). The model (MiniBatchKMeans) is
fitted on a stream-balanced sample (<= topic_per_stream_cap docs per source x language) with k picked from
SETTINGS.topics_k_grid by silhouette on a 10k subsample; every doc is then assigned to its nearest centroid.
Incremental runs assign new docs to the saved centroids; the model is refitted with --full/--refit-topics or
automatically when the corpus has grown by more than 30 % since the fit (topic ids then change).

Labels: per language, top c-TF-IDF terms (words; character bigrams for Chinese) over title + first 5
sentences, plus 3 exemplar titles closest to the centroid (distinct countries where possible).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from . import store
from .config import AGG_DIR, EXCLUDED_SAMPLES, SETTINGS, TOPIC_MODEL

logger = logging.getLogger(__name__)
_CJK = re.compile(r"[一-鿿]+")
_HANGUL = re.compile(r"[가-힯]{2,}")
_WORD = re.compile(r"[^\W\d_]{3,}")
_STOP = set("""the and for that with this from are was were has have had will would been its their they them
his her our your which what who whom when where how also into about after over more than such said says not but
can may all any other some most very just only there here these those being said thus however mr mrs ms
это как что для его она они при так или все уже был была были быть также этот этого который которые
по на не из за от до же бы ли то мы вы он их ее её нас вам году года
tass xinhua breaking news photo photos video editor reuters rutube com www http https ria novosti
""".split())
# Page/wire boilerplate in Chinese scrapes (character bigrams): source lines, editors, site chrome.
_STOP_ZH = set("风闻 首页 财经 责编 编辑 来源 日电 记者 新华 华社 视频 图片 社招 社概 告服 载客 无障 下载 转载 网友 客户 户端 点击 关注 原标 标题".split())


def doc_vectors(con: sqlite3.Connection) -> Tuple[pd.DataFrame, np.ndarray]:
    """(doc frame, unit-length mean passage embedding per doc), for present docs with passages."""
    pf = store.passage_frame(con)
    pids, E = store.load_passages(con)
    if pf.empty or len(E) == 0:
        return pd.DataFrame(), np.zeros((0, 384), dtype=np.float32)
    pos = np.searchsorted(pids, pf["pid"].to_numpy())
    ok = (pos < len(pids)) & (pids[np.minimum(pos, len(pids) - 1)] == pf["pid"].to_numpy())
    pf, pos = pf[ok].reset_index(drop=True), pos[ok]
    codes, uniq = pd.factorize(pf["doc_id"], sort=False)
    starts = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
    if len(starts) != len(uniq):  # passages of a doc not contiguous (should not happen): sort first
        order = np.argsort(codes, kind="stable")
        pf, pos, codes = pf.iloc[order].reset_index(drop=True), pos[order], codes[order]
        starts = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
    V = np.add.reduceat(E[pos].astype(np.float32), starts, axis=0)
    V /= np.linalg.norm(V, axis=1, keepdims=True).clip(1e-9)
    meta = pf.iloc[starts][["doc_id", "crow", "country", "source", "lang", "date", "kind", "url", "title", "sample"]]
    return meta.reset_index(drop=True), V


def _center(V: np.ndarray, langs: np.ndarray, means: Dict[str, np.ndarray]) -> np.ndarray:
    X = V.copy()
    for lg, mu in means.items():
        X[langs == lg] -= mu
    return X / np.linalg.norm(X, axis=1, keepdims=True).clip(1e-9)


def fit(meta: pd.DataFrame, V: np.ndarray) -> Dict:
    from sklearn.cluster import MiniBatchKMeans
    from sklearn.metrics import silhouette_score

    langs = meta["lang"].fillna("?").to_numpy()
    means = {lg: V[langs == lg].mean(0) for lg in np.unique(langs) if (langs == lg).sum() >= 20}
    X = _center(V, langs, means)
    rng = np.random.default_rng(SETTINGS.seed)
    stream = (meta["source"] + "|" + meta["lang"].fillna("?")).to_numpy()
    idx = np.concatenate([rng.permutation(np.flatnonzero(stream == s))[:SETTINGS.topic_per_stream_cap] for s in np.unique(stream)])
    sub = rng.permutation(idx)[:10000]
    scores, models = {}, {}
    for k in SETTINGS.topics_k_grid:
        km = MiniBatchKMeans(n_clusters=k, random_state=SETTINGS.seed, n_init=3, batch_size=4096).fit(X[idx])
        scores[k] = float(silhouette_score(X[sub], km.predict(X[sub]), metric="cosine"))
        models[k] = km
        logger.info("topics: k=%d silhouette=%.4f", k, scores[k])
    k = max(scores, key=scores.get)
    C = models[k].cluster_centers_
    C = C / np.linalg.norm(C, axis=1, keepdims=True)
    version = hashlib.sha1(C.astype(np.float32).tobytes()).hexdigest()[:10]
    np.savez(TOPIC_MODEL, centroids=C.astype(np.float32), langs=np.array(list(means)),
             lang_means=np.stack(list(means.values())).astype(np.float32), version=version, fitted_n=len(V),
             k=k, silhouette=json.dumps(scores), sample_n=len(idx))
    return {"k": k, "silhouette": scores, "version": version, "fit_sample": int(len(idx))}


def _load() -> Dict:
    z = np.load(TOPIC_MODEL, allow_pickle=False)
    return {"C": z["centroids"], "means": dict(zip([str(x) for x in z["langs"]], z["lang_means"])),
            "version": str(z["version"]), "fitted_n": int(z["fitted_n"]), "k": int(z["k"]),
            "silhouette": json.loads(str(z["silhouette"])), "sample_n": int(z["sample_n"])}


def _tokens(text: str, lang: str) -> List[str]:
    if lang == "zh":
        return [g for r in _CJK.findall(text) for g in (r[i:i + 2] for i in range(len(r) - 1)) if g not in _STOP_ZH]
    if lang == "ko":
        return _HANGUL.findall(text)
    return [w for w in (m.lower() for m in _WORD.findall(text)) if w not in _STOP]


def labels(con: sqlite3.Connection, meta: pd.DataFrame, topic: np.ndarray, X: np.ndarray, C: np.ndarray) -> List[Dict]:
    """Per-topic distinctive terms by language + exemplar titles."""
    cc = store.corpus()
    first: Dict[int, List[str]] = defaultdict(list)
    for doc, text in cc.execute("SELECT doc, text FROM sentences WHERE idx < 5"):
        first[doc].append(text)
    cc.close()
    counts: Dict[str, Dict[int, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for (crow, lang, title, kind), t in zip(meta[["crow", "lang", "title", "kind"]].itertuples(index=False), topic):
        lang = lang or "?"
        text = (title or "") + " " + ("" if kind == "headline" else " ".join(first.get(crow, [])))
        counts[lang][int(t)].update(set(_tokens(text, lang)))
    terms: Dict[int, Dict[str, List[str]]] = defaultdict(dict)
    for lang, by_t in counts.items():
        df = Counter()
        for c in by_t.values():
            df.update(c)
        avg = sum(sum(c.values()) for c in by_t.values()) / max(len(by_t), 1)
        for t, c in by_t.items():
            tot = sum(c.values()) or 1
            sc = {w: (v / tot) * math.log(1 + avg / df[w]) for w, v in c.items() if v >= 3}
            top = sorted(sc, key=sc.get, reverse=True)[:8]
            if top and sum(c.values()) >= 50:
                terms[t][lang] = top
    out = []
    sims = (X * C[topic]).sum(1)
    for t in range(len(C)):
        members = np.flatnonzero(topic == t)
        order = members[np.argsort(-sims[members])]
        ex, seen = [], set()
        for pass_ in (0, 1):
            for i in order[:400]:
                r = meta.iloc[i]
                if len(ex) >= 3:
                    break
                if (pass_ == 0 and r["country"] in seen) or any(e["doc_id"] == r["doc_id"] for e in ex):
                    continue
                seen.add(r["country"])
                ex.append({"doc_id": r["doc_id"], "country": r["country"], "source": r["source"], "lang": r["lang"],
                           "date": r["date"], "title": (r["title"] or "")[:200], "url": r["url"]})
        langs = Counter(meta["lang"].iloc[members].fillna("?"))
        main = [lg for lg, _ in langs.most_common() if lg in terms[t]]
        lab = " / ".join(", ".join(terms[t][lg][:3]) for lg in (["en"] if "en" in terms[t] else []) + [lg for lg in main if lg != "en"][:1])
        out.append({"topic": t, "label": lab, "n_docs": int(len(members)), "terms": terms[t],
                    "langs": dict(langs.most_common()), "countries": dict(Counter(meta["country"].iloc[members]).most_common()),
                    "exemplars": ex})
    return out


def prevalence(meta: pd.DataFrame, topic: np.ndarray) -> Dict:
    """Topic counts and shares by country x month (raw, and stream-balanced with fixed stream weights)."""
    df = meta.assign(topic=topic, month=meta["date"].str[:7])
    df = df[~df["sample"].isin(EXCLUDED_SAMPLES) & df["month"].notna()]
    df["stream"] = df["source"] + "|" + df["lang"].fillna("?") + "|" + df["sample"].fillna("all")
    w = df.groupby("stream").size()
    tot = df.groupby(["stream", "month"]).size().rename("n")
    cnt = df.groupby(["stream", "month", "topic"]).size().rename("k").reset_index().join(tot, on=["stream", "month"])
    cnt["share"] = cnt["k"] / cnt["n"]
    cnt["country"] = cnt["stream"].map(df.drop_duplicates("stream").set_index("stream")["country"])
    cnt["w"] = cnt["stream"].map(w)
    sm = tot.reset_index()
    sm = sm[sm["n"] >= 5]
    sm["country"] = sm["stream"].map(df.drop_duplicates("stream").set_index("stream")["country"])
    sm["w"] = sm["stream"].map(w)
    wsum = sm.groupby(["country", "month"])["w"].sum()
    cnt = cnt.merge(sm[["stream", "month"]], on=["stream", "month"])
    cnt["ws"] = cnt["share"] * cnt["w"]
    bal = (cnt.groupby(["country", "month", "topic"])["ws"].sum() / wsum).rename("balanced_share")
    raw = df.groupby(["country", "month", "topic"]).size().rename("n_docs")
    ctot = df.groupby(["country", "month"]).size().rename("country_docs")
    res = pd.concat([raw, bal], axis=1).reset_index().join(ctot, on=["country", "month"])
    res["share"] = res["n_docs"] / res["country_docs"]
    res = res.fillna({"n_docs": 0, "balanced_share": 0})
    return {"rows": [{"country": r.country, "month": r.month, "topic": int(r.topic), "n_docs": int(r.n_docs),
                      "country_docs": int(r.country_docs) if not pd.isna(r.country_docs) else 0,
                      "share": round(float(r.share), 5) if not pd.isna(r.share) else None,
                      "balanced_share": round(float(r.balanced_share), 5)} for r in res.itertuples()]}


def run_topics(con: sqlite3.Connection, refit: bool = False) -> Dict:
    meta, V = doc_vectors(con)
    if meta.empty:
        return {"docs": 0}
    fit_info = None
    if refit or not TOPIC_MODEL.exists() or len(V) > 1.3 * _load()["fitted_n"]:
        fit_info = fit(meta, V)
    m = _load()
    X = _center(V, meta["lang"].fillna("?").to_numpy(), m["means"])
    topic = np.argmax(X @ m["C"].T, axis=1)
    sim = (X * m["C"][topic]).sum(1)
    con.execute("DELETE FROM doc_topic")
    con.executemany("INSERT INTO doc_topic(doc_id, topic, sim, topic_v) VALUES(?,?,?,?)",
                    [(d, int(t), float(s), m["version"]) for d, t, s in zip(meta["doc_id"], topic, sim)])
    con.commit()
    labs = labels(con, meta, topic, X, m["C"])
    mixed = sum(1 for lb in labs if sum(1 for v in lb["langs"].values() if v >= 0.1 * lb["n_docs"]) >= 2)
    AGG_DIR.mkdir(parents=True, exist_ok=True)
    out = {"version": m["version"], "k": m["k"], "silhouette_by_k": m["silhouette"], "fit_sample": m["sample_n"],
           "docs_assigned": int(len(meta)), "multilingual_topics": mixed, "topics": labs,
           "prevalence": prevalence(meta, topic)["rows"]}
    (AGG_DIR / "topics.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return {"k": m["k"], "version": m["version"], "refit": bool(fit_info), "docs": int(len(meta)),
            "multilingual_topics": mixed, "silhouette": m["silhouette"]}
