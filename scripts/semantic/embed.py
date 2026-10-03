"""Stage `embed`: passages (title + first N sentence chunks of <= ~200 tokens) -> e5 embeddings (float16 shards).

Also provides the shared E5 encoder used by tone scoring and search. e5 prefixes: stored passages use
"passage: " (asymmetric retrieval); search queries use "query: "; sentence features for tone use "query: "
(the e5 authors' advice for embeddings used as classifier features).
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import store
from .config import E5_MODEL, SETTINGS, device, model_path

logger = logging.getLogger(__name__)


_URL = re.compile(r"https?://\S+|www\.\S+")
_LETTER = re.compile(r"[^\W\d_]")
CJK = ("zh", "ko", "ja")


def clean(text: str) -> str:
    """Text as embedded: URLs removed, whitespace collapsed."""
    return re.sub(r"\s+", " ", _URL.sub(" ", text or "")).strip()


def is_junk(text: str, lang: Optional[str] = None) -> bool:
    """Passages with too little language to embed meaningfully (link lists, numbers, emoji). Such passages sit
    close to everything in e5 space, so they are kept for alignment but excluded from search, topics and echoes."""
    t = clean(text)
    letters = len(_LETTER.findall(t))
    return letters < (8 if lang in CJK else 20) or letters < 0.5 * len(t.replace(" ", ""))


class E5Encoder:
    """multilingual-e5-small, mean pooling, L2-normalised output (float32 numpy)."""

    def __init__(self, dev: Optional[str] = None) -> None:
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        self.dev = dev or device()
        path = model_path(E5_MODEL)
        self.tok = AutoTokenizer.from_pretrained(path)
        dtype = torch.float16 if self.dev == "mps" else torch.float32
        self.model = AutoModel.from_pretrained(path, dtype=dtype).to(self.dev).eval()

    def count_tokens(self, texts: Sequence[str]) -> List[int]:
        if not texts:
            return []
        return [len(x) for x in self.tok(list(texts), add_special_tokens=False)["input_ids"]]

    def encode(self, texts: Sequence[str], prefix: str, max_length: int = 256, batch: int = 64) -> np.ndarray:
        """Embed texts (prefix is 'query: ' or 'passage: '). Returns [n, 384] float32, unit length."""
        torch = self.torch
        out = np.zeros((len(texts), 384), dtype=np.float32)
        order = np.argsort([len(t) for t in texts])
        with torch.inference_mode():
            for i in range(0, len(order), batch):
                idx = order[i:i + batch]
                enc = self.tok([prefix + texts[j] for j in idx], padding=True, truncation=True,
                               max_length=max_length, return_tensors="pt").to(self.dev)
                h = self.model(**enc).last_hidden_state
                m = enc["attention_mask"].unsqueeze(-1).to(h.dtype)
                e = (h * m).sum(1) / m.sum(1).clamp(min=1)
                e = torch.nn.functional.normalize(e.float(), dim=-1)
                out[idx] = e.cpu().numpy()
        return out


def build_passages(title: str, sents: Sequence[Tuple[int, str, int]], max_tokens: int = SETTINGS.max_tokens,
                   max_chunks: int = SETTINGS.max_chunks) -> List[Tuple[int, int, int, str]]:
    """[(pidx, s0, s1, text)]: pidx 0 = title (s0=s1=-1), then up to max_chunks chunks of consecutive sentences
    (s0..s1 inclusive) of <= max_tokens tokens. A chunk identical to the title (headline-only docs) is dropped."""
    out: List[Tuple[int, int, int, str]] = []
    t = (title or "").strip()
    if t:
        out.append((0, -1, -1, t))
    cur: List[Tuple[int, str, int]] = []
    ntok = 0

    def close() -> None:
        text = " ".join(s for _, s, _ in cur).strip()
        if text and text != t:
            out.append((len(out) if t else len(out) + 1, cur[0][0], cur[-1][0], text))

    for idx, s, n in sents:
        if cur and ntok + n > max_tokens:
            close()
            cur, ntok = [], 0
            if sum(1 for p in out if p[0] > 0) >= max_chunks:
                return out
        cur.append((idx, s, n))
        ntok += n
    if cur and sum(1 for p in out if p[0] > 0) < max_chunks:
        close()
    return out


def mark_junk(con: sqlite3.Connection) -> int:
    """One-off: flag junk passages embedded before the junk rule existed (meta junk_v)."""
    if store.get_meta(con, "junk_v") == "1":
        return 0
    rows = con.execute("SELECT p.pid, p.s0, p.s1, d.crow, d.title, d.lang FROM passages p JOIN docs d USING(doc_id)").fetchall()
    cc = store.corpus()
    flags: List[Tuple[int]] = []
    redo: List[Tuple[int, str]] = []
    for pid, s0, s1, crow, title, lang in rows:
        if s0 < 0:
            text = title or ""
        else:
            text = " ".join(x[0] for x in cc.execute("SELECT text FROM sentences WHERE doc=? AND idx BETWEEN ? AND ?",
                                                      (crow, s0, s1)))
        if is_junk(text, lang):
            flags.append((pid,))
        elif _URL.search(text):
            redo.append((pid, clean(text)))
    cc.close()
    con.executemany("UPDATE passages SET junk=1 WHERE pid=?", flags)
    if redo:  # vectors computed with URLs in the text: recompute them on the cleaned text, in place
        enc = E5Encoder()
        emb = enc.encode([t for _, t in redo], "passage: ", max_length=256).astype(np.float16)
        new = dict(zip([p for p, _ in redo], emb))
        for lo, hi, name in con.execute("SELECT lo, hi, file FROM shards").fetchall():
            hit = [p for p in new if lo <= p < hi]
            if hit:
                path = store.EMB_DIR / name
                arr = np.load(path)
                for p in hit:
                    arr[p - lo] = new[p]
                np.save(path, arr)
    store.set_meta(con, "junk_v", "1")
    con.commit()
    logger.info("embed: %d of %d existing passages flagged as junk; %d re-embedded without URLs",
                len(flags), len(rows), len(redo))
    return len(flags)


def run_embed(con: sqlite3.Connection, budget_s: Optional[float] = None, max_docs: Optional[int] = None,
              enc: Optional[E5Encoder] = None) -> Dict[str, object]:
    """Embed passages of docs not embedded yet, newest first, in shards of SETTINGS.embed_batch_docs docs."""
    mark_junk(con)
    todo = con.execute("SELECT doc_id, crow, title, lang FROM docs WHERE present=1 AND embedded=0"
                       " ORDER BY date DESC, doc_id").fetchall()
    if max_docs:
        todo = todo[:max_docs]
    logger.info("embed: %d docs to embed", len(todo))
    if not todo:
        return {"docs_embedded": 0}
    enc = enc or E5Encoder()
    cc = store.corpus()
    t0, ndocs, npass = time.time(), 0, 0
    step = SETTINGS.embed_batch_docs
    for i in range(0, len(todo), step):
        part = todo[i:i + step]
        sents = store.sentences_for(cc, [r[1] for r in part], max_idx=200)
        flat = [s for r in part for _, s in sents.get(r[1], [])]
        counts = iter(enc.count_tokens(flat))
        rows: List[Tuple[str, int, int, int, str, int]] = []
        for doc_id, crow, title, lang in part:
            ss = [(idx, s, next(counts)) for idx, s in sents.get(crow, [])]
            for pidx, s0, s1, text in build_passages(title or "", ss):
                rows.append((doc_id, pidx, s0, s1, text, int(is_junk(text, lang))))
        emb = enc.encode([clean(r[4]) or r[4] for r in rows], "passage: ", max_length=256)
        lo = (con.execute("SELECT max(hi) FROM shards").fetchone()[0] or 1)
        store.write_shard(con, lo, emb)
        con.executemany("INSERT INTO passages(pid, doc_id, pidx, s0, s1, nchars, junk) VALUES(?,?,?,?,?,?,?)",
                        [(lo + k, r[0], r[1], r[2], r[3], len(r[4]), r[5]) for k, r in enumerate(rows)])
        con.executemany("UPDATE docs SET embedded=1 WHERE doc_id=?", [(r[0],) for r in part])
        con.commit()
        ndocs += len(part)
        npass += len(rows)
        el = time.time() - t0
        logger.info("embed: %d/%d docs, %d passages, %.0fs (%.0f passages/s)", ndocs, len(todo), npass, el, npass / el)
        if budget_s and el > budget_s:
            logger.warning("embed: time budget reached; %d docs left for the next run", len(todo) - ndocs)
            break
    cc.close()
    return {"docs_embedded": ndocs, "passages": npass, "docs_left": len(todo) - ndocs}
