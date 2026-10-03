"""Build the static, client-side search data for tools/rhetoric-search/ from the private corpus index.

Reads  ~/Projects/rhetoric-corpus/index/corpus.sqlite  (docs + sentences tables; see that repo's README)
Writes tools/rhetoric-search/data/
  meta.json.gz          sources, shards (country, date range, counts), totals, build info
  s/<n>.json.gz         one shard: documents (date, source, title, url, speaker, sentence count, lang, org, corpus
                        rowid) and their
                        sentences (each <= 300 characters), newline-separated, in document order
  t/<b>.json.gz         token dictionary bucket b: {token: [shard ids]} for every folded word, CJK character
                        and CJK bigram; the bucket is FNV-1a of the token's first two characters (prefix search)

Official texts (outlet == "official") ship as sentences; state media / commentary ship NO text: only
  m/t/<b>.json.gz  {token: [doc deltas] | [doc deltas, counts] | -1} postings (token -> media doc ids + counts)
  m/d/<k>.json.gz  title, link, outlet, speaker, headline flag for 2,000 media docs
  m/docs.json.gz   date, source and language of every media doc (filters, month chart)
Official sentences are each <= 300 characters, with their document's link. Shards are about --shard-kb of raw text, grouped by
country and year, newest first. A query reads one dictionary bucket per term, then only the shards that hold
every token of the query. Files are gzip-compressed JSON; the page inflates them with DecompressionStream.

Usage (from the rhetoric-corpus repo; vendored from tsm-strait-layers tools/rhetoric-search/scripts/, keep in step):
  uv run python publish/build_public.py --out <dir>
  ... --countries CN               (TSM site: PRC only)
  ... --exclude-sources prc_statemedia_headlines --from 2022-01-01
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
import re
from array import array
from pathlib import Path
from typing import Dict, List

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent  # the rhetoric-corpus repo root (publish/..)
DEFAULT_CORPUS = TOOL
STAGING = DEFAULT_CORPUS / "staging"
BUCKETS = 512
MEDIA_COMMON = 0.3  # a token in more than this share of media documents is stored as 'every document'
MEDIA_CHUNK = 2000  # media documents per metadata file (title, link, outlet)
COMMON = 0.6  # a token in more than this share of shards is stored as "every shard" (-1)
_CJK1 = re.compile(r"[\u1100-\u11ff\u3040-\u30ff\u3130-\u318f\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")
ORG_NAMES = {"Kremlin": "Kremlin", "MFA": "Foreign Ministry", "MND": "Defense Ministry", "TAO": "Taiwan Affairs Office"}


def gz(obj) -> bytes:
    return gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9, mtime=0)


def enc_size(n_gz: int) -> int:
    """Size after scripts/build_site.py seals a data file: magic + base64(iv + gzip(gz) + tag)."""
    return 10 + 4 * ((12 + n_gz + 30 + 16 + 2) // 3)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--out", type=Path, default=STAGING / "public")
    ap.add_argument("--countries", help="comma list, e.g. CN (default: all)")
    ap.add_argument("--sources", help="comma list of sources to include (default: all)")
    ap.add_argument("--exclude-sources", default="")
    ap.add_argument("--from", dest="date_from", default="")
    ap.add_argument("--to", dest="date_to", default="")
    ap.add_argument("--shard-kb", type=int, default=600, help="target raw text per shard (KB)")
    ap.add_argument("--min-chars", type=int, default=12, help="drop sentences shorter than this")
    ap.add_argument("--no-dedupe", dest="dedupe", action="store_false",
                    help="ship repeated sentences of a source every time (default: once, under the newest document)")
    a = ap.parse_args()
    sys.path.insert(0, str(a.corpus / "scripts"))
    from textnorm import bucket_of, index_token_counts, index_tokens  # noqa: E402

    t0 = time.time()
    con = sqlite3.connect(f"file:{a.corpus / 'index' / 'corpus.sqlite'}?mode=ro", uri=True)
    where, args = ["1"], []
    for col, val in (("country", a.countries), ("source", a.sources)):
        vals = [v.strip() for v in (val or "").split(",") if v.strip()]
        if vals:
            where.append(f"{col} IN ({','.join('?' * len(vals))})")
            args += [v.upper() if col == "country" else v for v in vals]
    excl = [v.strip() for v in a.exclude_sources.split(",") if v.strip()]
    if excl:
        where.append(f"source NOT IN ({','.join('?' * len(excl))})")
        args += excl
    if a.date_from:
        where.append("date >= ?")
        args.append(a.date_from)
    if a.date_to:
        where.append("date <= ?")
        args.append(a.date_to)
    docs = con.execute(f"SELECT rowid, country, source, org, outlet, lang, date, url, title, speaker, kind FROM docs "
                       f"WHERE {' AND '.join(where)} ORDER BY country, date DESC, rowid", args).fetchall()
    srcs = sorted({(d[2], d[1], d[3] if d[2] not in ("prc_statemedia", "prc_statemedia_headlines") else "", d[4])
                   for d in docs})
    src_ids = {}
    sources = []
    for s, c, org, outlet in srcs:
        if s in src_ids:
            continue
        src_ids[s] = len(sources)
        sources.append({"id": s, "country": c, "outlet": outlet})
    langs = sorted({d[5] for d in docs})
    lang_ids = {l: i for i, l in enumerate(langs)}

    # Clear only what this script writes; data/trends/ belongs to build_trends.py.
    for sub in ("s", "t", "m"):
        if (a.out / sub).exists():
            shutil.rmtree(a.out / sub)
    (a.out / "meta.json.gz").unlink(missing_ok=True)
    (a.out / "s").mkdir(parents=True)
    (a.out / "t").mkdir(parents=True)
    (a.out / "m" / "t").mkdir(parents=True)
    (a.out / "m" / "d").mkdir(parents=True)

    postings: Dict[str, List[int]] = collections.defaultdict(list)
    shards = []
    by_cy: Dict[tuple, List[tuple]] = collections.defaultdict(list)
    official = [d for d in docs if d[4] == "official"]
    media = [d for d in docs if d[4] != "official"]  # state media / commentary: no body text ships
    for d in official:
        by_cy[(d[1], d[6][:4])].append(d)
    total_sents = raw_bytes = gz_bytes = enc_bytes = 0
    limit = a.shard_kb * 1000

    def flush(rows, sents, country):
        nonlocal raw_bytes, gz_bytes, enc_bytes
        if not rows:
            return
        sid = len(shards)
        text = "\n".join(sents)
        blob = gz({"docs": rows, "text": text})
        (a.out / "s" / f"{sid}.json.gz").write_bytes(blob)
        toks = set()
        for s in sents:
            toks |= index_tokens(s)
        for t in toks:
            postings[t].append(sid)
        dates = [r[0] for r in rows]
        shards.append([sid, country, min(dates), max(dates), len(rows), len(sents), len(blob),
                       sorted({r[1] for r in rows}), sorted({r[6] for r in rows})])
        raw_bytes += len(text.encode())
        gz_bytes += len(blob)
        enc_bytes += enc_size(len(blob))

    seen: set = set()
    n_dup = [0]
    for (country, year) in sorted(by_cy, key=lambda k: (k[0], k[1]), reverse=True):
        rows, sents, size = [], [], 0
        for d in by_cy[(country, year)]:
            ss = [s for (s,) in con.execute("SELECT text FROM sentences WHERE doc=? ORDER BY idx", (d[0],))
                  if len(s) >= a.min_chars]
            if not ss:
                continue
            ss = [s.replace("\n", " ") for s in ss]
            if a.dedupe:  # a sentence already shipped for this source (repost, boilerplate) ships once, newest first
                keep = []
                for s in ss:
                    k = (d[2], hash(s))
                    if k in seen:
                        n_dup[0] += 1
                        continue
                    seen.add(k)
                    keep.append(s)
                ss = keep
                if not ss:
                    continue
            title = (d[8] or "")[:160]
            if len(ss) == 1 and ss[0].startswith(title):  # headline-only document: the sentence is the title
                title = ""
            rows.append([d[6], src_ids[d[2]], title, d[7], d[9] or "", len(ss), lang_ids[d[5]],
                         d[3] if d[2] in ("prc_statemedia", "prc_statemedia_headlines") else "",
                         d[0]])  # corpus rowid: key into the full-document shards (build_hf_data.py)
            sents += ss
            sr = sources[src_ids[d[2]]]
            sr["docs"] = sr.get("docs", 0) + 1
            sr["from"] = min(sr.get("from", d[6]), d[6])
            sr["to"] = max(sr.get("to", d[6]), d[6])
            size += sum(len(s.encode()) for s in ss)
            total_sents += len(ss)
            if size >= limit:
                flush(rows, sents, country)
                rows, sents, size = [], [], 0
        flush(rows, sents, country)

    n_sh = len(shards)
    buckets: Dict[int, Dict[str, object]] = collections.defaultdict(dict)
    n_common = 0
    for t, ids in postings.items():
        if len(ids) > COMMON * n_sh and n_sh > 10:
            buckets[bucket_of(t, BUCKETS)][t] = -1
            n_common += 1
        else:
            # delta-encode sorted shard ids
            ids = sorted(ids)
            buckets[bucket_of(t, BUCKETS)][t] = [ids[0]] + [ids[i] - ids[i - 1] for i in range(1, len(ids))]
    dict_gz = dict_enc = 0
    for b in range(BUCKETS):
        blob = gz(buckets.get(b, {}))
        (a.out / "t" / f"{b}.json.gz").write_bytes(blob)
        dict_gz += len(blob)
        dict_enc += enc_size(len(blob))
    # ---- media / commentary: token -> [(doc, count)] postings, doc metadata (title, link) only -------------
    media.sort(key=lambda d: (d[6], d[0]), reverse=True)  # media doc id = position, newest first
    mpost: Dict[str, array] = {}
    days, msrc, mlang = [], [], []
    epoch = datetime(2000, 1, 1).date()
    chunk_rows: List[list] = []
    m_meta_gz = m_meta_enc = 0
    for i, d in enumerate(media):
        text = con.execute("SELECT text FROM docs WHERE rowid=?", (d[0],)).fetchone()[0]
        counts = index_token_counts((d[8] or "") + "\n" + text)
        for t, c in counts.items():
            if len(t) == 1 and _CJK1.match(t):  # single CJK characters: too common to be worth their postings
                continue
            arr = mpost.get(t)
            if arr is None:
                arr = mpost[t] = array("I")
            arr.append(i)
            arr.append(c)
        days.append((datetime.fromisoformat(d[6]).date() - epoch).days)
        msrc.append(src_ids[d[2]])
        mlang.append(lang_ids[d[5]])
        sr = sources[src_ids[d[2]]]
        sr["docs"] = sr.get("docs", 0) + 1
        sr["from"] = min(sr.get("from", d[6]), d[6])
        sr["to"] = max(sr.get("to", d[6]), d[6])
        chunk_rows.append([(d[8] or "")[:200], d[7], d[3] or "", d[9] or "", 1 if d[10] == "headline" else 0])
        if len(chunk_rows) == MEDIA_CHUNK or i == len(media) - 1:
            blob = gz(chunk_rows)
            (a.out / "m" / "d" / f"{i // MEDIA_CHUNK}.json.gz").write_bytes(blob)
            m_meta_gz += len(blob)
            m_meta_enc += enc_size(len(blob))
            chunk_rows = []
    lite = gz({"day0": days[-1] if days else 0, "days": [x - (days[-1] if days else 0) for x in days], "src": msrc, "lang": mlang})
    (a.out / "m" / "docs.json.gz").write_bytes(lite)
    mbuckets: Dict[int, Dict[str, list]] = collections.defaultdict(dict)
    n_postings = 0
    n_mcommon = 0
    for t, arr in mpost.items():
        ids_, cnt = arr[0::2], arr[1::2]
        if len(ids_) > MEDIA_COMMON * len(media) and len(media) > 1000:
            mbuckets[bucket_of(t, BUCKETS)][t] = -1  # in most articles: "every document", no counts
            n_mcommon += 1
            continue
        n_postings += len(ids_)
        deltas = [ids_[0]] + [ids_[k] - ids_[k - 1] for k in range(1, len(ids_))]
        # [doc deltas] when every count is 1, else [doc deltas, counts]
        mbuckets[bucket_of(t, BUCKETS)][t] = deltas if max(cnt) == 1 else [deltas, list(cnt)]
    mdict_gz = mdict_enc = 0
    for b in range(BUCKETS):
        blob = gz(mbuckets.get(b, {}))
        (a.out / "m" / "t" / f"{b}.json.gz").write_bytes(blob)
        mdict_gz += len(blob)
        mdict_enc += enc_size(len(blob))
    largest_mbucket = max((len(gz(v)) for v in mbuckets.values()), default=0)
    del mpost, mbuckets

    meta = {"built": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "buckets": BUCKETS,
            "langs": langs, "sources": sources,
            "shards": [s[:6] + [s[7], s[8]] for s in shards],  # id, country, from, to, docs, sentences, sources, langs
            "media": {"docs": len(media), "chunk": MEDIA_CHUNK},
            "totals": {"docs": sum(sh[4] for sh in shards), "media_docs": len(media), "sentences": total_sents, "dup_dropped": n_dup[0], "dedupe": a.dedupe, "shards": n_sh, "tokens": len(postings)},
            "filter": {"countries": a.countries or "all", "sources": a.sources or "all", "exclude": excl,
                       "from": a.date_from, "to": a.date_to}}
    mblob = gz(meta)
    (a.out / "meta.json.gz").write_bytes(mblob)
    total_gz = gz_bytes + dict_gz + len(mblob) + m_meta_gz + len(lite) + mdict_gz
    total_enc = enc_bytes + dict_enc + enc_size(len(mblob)) + m_meta_enc + enc_size(len(lite)) + mdict_enc
    shipped_docs = sum(sh[4] for sh in shards)
    report = {"docs": shipped_docs, "docs_selected": len(docs), "sentences": total_sents, "duplicate_sentences_dropped": n_dup[0], "shards": n_sh, "tokens": len(postings),
              "common_tokens": n_common, "raw_text_mb": round(raw_bytes / 1e6, 1),
              "shards_gz_mb": round(gz_bytes / 1e6, 1), "dict_gz_mb": round(dict_gz / 1e6, 1),
              "media_docs": len(media), "media_postings": n_postings, "media_common_tokens": n_mcommon, "media_dict_gz_mb": round(mdict_gz / 1e6, 1),
              "media_meta_gz_mb": round((m_meta_gz + len(lite)) / 1e6, 1),
              "largest_media_bucket_kb": round(largest_mbucket / 1e3),
              "total_gz_mb": round(total_gz / 1e6, 1), "est_encrypted_mb": round(total_enc / 1e6, 1),
              "largest_shard_kb": round(max(s[6] for s in shards) / 1e3) if shards else 0,
              "largest_bucket_kb": round(max(len(gz(v)) for v in buckets.values()) / 1e3) if buckets else 0,
              "seconds": round(time.time() - t0, 1)}
    STAGING.mkdir(parents=True, exist_ok=True)
    (STAGING / "last_build.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
