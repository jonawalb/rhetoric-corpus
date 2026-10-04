"""Export nuclear-rhetoric series for the Nuclear Signals Observatory (tsm-strait-layers/tools/nuclear-signals).

Read-only on index/corpus.sqlite and index/semantic/semantic.sqlite. Writes one JSON file (default
../tsm-strait-layers/tools/nuclear-signals/scripts/rhetoric_export.json); the tool's builder turns it into a data module.

What it measures, per country and month (official outlets only, 2021-01 onward, sample regimes backfill/seed excluded):
  * docs        official documents in the month (denominator)
  * docs_w      documents with at least one nuclear-weapons sentence (regex W below)
  * sent_w      nuclear-weapons sentences; sent_a = arms-control / programme sentences (regex A), counted only
  * scored      W sentences that the semantic layer scored for tone (it scores idx < 80, plus target-mentioning
                sentences with idx < 400; see scripts/semantic/tone.py)
  * threat / escalation / hostility / deesc: mean sentence probability over scored W sentences (0..1)
  * hi          W sentences with threat >= 0.5
  * tgt         {target: [W sentences mentioning target, mean threat of those scored]} (self-mentions excluded)
  * z_threat    (mean - base) / sqrt(sd_base^2 + se^2), base = mean of the previous 12 monthly means with scored >= 10,
                floored denominator 0.01; null when the month has < 10 scored sentences or < 6 baseline months
Quotes: per country-month, the highest-threat official W sentences (<= 300 characters, at most 4, <= 2 per non-English
language), with link. Media: state-media headlines (title + link only) matching W, top 3 per country-month by the
document's threat tone. No state-media sentence text is exported.

Reporters' questions in briefing transcripts (sentences that start with a question marker or end with "?") are skipped.

Run: uv run python scripts/export_nuclear_signals.py [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "index" / "corpus.sqlite"
SEM = ROOT / "index" / "semantic" / "semantic.sqlite"
AGG = ROOT / "index" / "semantic" / "aggregates"
DEFAULT_OUT = ROOT.parent / "tsm-strait-layers" / "tools" / "nuclear-signals" / "scripts" / "rhetoric_export.json"

COUNTRIES = ["RU", "CN", "US", "IN", "PK", "IR"]
LANGS = ("en", "ru", "zh", "fa")
START = "2021-01-01"
EXCLUDED_SAMPLES = ("backfill", "seed")
DIMS = ("threat", "escalation", "hostility", "deescalation")

W = {
    "en": re.compile(
        r"\bnuclear[- ](?:weapon|arm|arsenal|deterr|war\b|warfare|strike|attack|forces?\b|warhead|missile|threat|doctrine|"
        r"test|posture|capab|blackmail|escalat|umbrella|sharing|triad|bomb|option|retaliat|use\b|response|exercise|drill|"
        r"powers\b|states?\b|shield|counterattack|counter-attack|submarine|bomber|first use|sabre|saber|rhetoric|"
        r"coercion|catastroph|apocalyp|holocaust|confrontation|conflict|annihilat|dimension|ultimatum)"
        r"|\b(?:atomic bombs?|nukes?|nuked|thermonuclear|no[- ]first[- ]use|ICBMs?|SLBMs?|intercontinental ballistic)\b",
        re.I),
    "ru": re.compile(
        r"ядерн\w*\s+(?:оруж|арсенал|сдерж|войн|удар|сил|боеголов|боезаряд|ракет|угроз|доктрин|испытан|потенциал|шантаж|"
        r"эскалац|зонтик|триад|держав|ответ|применени|учени|конфликт|апокалипс|катастроф|возмезд|подлод|бомб|щит|"
        r"государств|ультиматум)"
        r"|атомн\w*\s+(?:бомб|оруж)|межконтинентальн\w*\s+баллистическ|\b(?:Сармат|Орешник|Буревестник|Посейдон)", re.I),
    "zh": re.compile(
        r"核武|核力量|核威慑|核战|核打击|核弹|核试验|核潜艇|核态势|核政策|核讹诈|核共享|核保护伞|核三位一体|核导弹|核大国|"
        r"核国家|核反击|核报复|核恐吓|核冲突|核门槛|首先使用核|不首先使用|核常兼备|洲际弹道导弹|洲际导弹"),
    "fa": re.compile(
        r"(?:سلاح|سلاح[\s‌]*های|بمب|جنگ|بازدارندگی|حمله|کلاهک|زرادخانه)[\s‌]*(?:هسته[\s‌]*ای|اتمی)"),
}
A = {
    "en": re.compile(
        r"\bnuclear[- ](?:non-?proliferation|disarmament|arms control|program|programme|deal|talks|facilit|site|issue|file|"
        r"material|safety|security|agreement|negotiat)|\bNPT\b|\bNew START\b|\bCTBT\b|\bJCPOA\b|\bIAEA\b|\benrichment\b", re.I),
    "ru": re.compile(
        r"нераспространени|ДСНВ|\bСНВ|ДВЗЯИ|ДРСМД|МАГАТЭ|обогащени|ядерн\w*\s+(?:программ|сделк|объект|разоружен|безопасност)",
        re.I),
    "zh": re.compile(r"核不扩散|核扩散|核裁军|核军控|军控|核问题|核计划|核设施|核协议|全面禁止核试验|伊核|朝核|无核化|原子能机构|浓缩"),
    "fa": re.compile(
        r"(?:برنامه|توافق|مذاکرات|تاسیسات|تأسیسات|پرونده|صنعت)[\s‌]*(?:هسته|اتمی)|غنی[\s‌]*سازی|آژانس|برجام"),
}
FTS_WORDS = ('nuclear OR nukes OR nuke OR thermonuclear OR ICBM OR ICBMs OR SLBM OR NPT OR CTBT OR JCPOA OR IAEA OR '
             'enrichment OR "intercontinental" OR "atomic" OR ядерн* OR атомн* OR межконтинентальн* OR Сармат* OR '
             'Орешник* OR Буревестник* OR Посейдон* OR нераспространени* OR ДСНВ OR СНВ OR ДВЗЯИ OR ДРСМД OR МАГАТЭ OR '
             'обогащени* OR هسته* OR اتمی OR غنی* OR آژانس OR برجام')
FTS_CJK = '"核武" OR "核力量" OR "核威慑" OR "核战" OR "核打击" OR "核弹" OR "核试验" OR "核潜艇" OR "核态势" OR "核政策" OR ' \
          '"核讹诈" OR "核共享" OR "核保护伞" OR "核导弹" OR "核大国" OR "核国家" OR "核反击" OR "核报复" OR "核恐吓" OR ' \
          '"核冲突" OR "核门槛" OR "首先使用" OR "洲际" OR "核不扩散" OR "核扩散" OR "核裁军" OR "核军控" OR "军控" OR ' \
          '"核问题" OR "核计划" OR "核设施" OR "核协议" OR "伊核" OR "朝核" OR "无核化" OR "原子能" OR "浓缩"'


# Reporters' questions in briefing transcripts are not the government's words: skip them everywhere.
QUESTION = re.compile(r"^\s*(?:QUESTION|Question|Supplementary [Qq]uestion|Q\s*[:.]|\([^)]{2,80}\)\s*:|Вопрос|问[:：]|记者[:：]|سوال|پرسش)")


def is_question(text: str) -> bool:
    t = text.strip()
    return bool(QUESTION.match(t)) or t.endswith(("?", "？", "؟"))


def ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def month(d: str) -> str:
    return d[:7]


def load_docs(cc: sqlite3.Connection, sc: sqlite3.Connection) -> dict:
    """semantic docs (official + state media) for COUNTRIES, keyed by corpus rowid."""
    q = (f"SELECT doc_id, crow, country, source, outlet, lang, date, url, title, sample, kind FROM docs "
         f"WHERE country IN ({','.join('?' * len(COUNTRIES))}) AND date >= ? AND present = 1")
    out = {}
    for doc_id, crow, c, src, outlet, lang, date, url, title, sample, kind in sc.execute(q, [*COUNTRIES, START]):
        if (sample or "") in EXCLUDED_SAMPLES or outlet not in ("official", "state_media"):
            continue
        out[crow] = dict(doc_id=doc_id, country=c, source=src, outlet=outlet, lang=lang, date=date, url=url,
                         title=title or "", kind=kind)
    return out


def add_speakers(cc: sqlite3.Connection, docs: dict, rows: set) -> None:
    ids = sorted(rows)
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for rowid, org, speaker in cc.execute(
                f"SELECT rowid, org, speaker FROM docs WHERE rowid IN ({','.join('?' * len(part))})", part):
            docs[rowid]["org"], docs[rowid]["speaker"] = org, speaker


def candidates(cc: sqlite3.Connection, docs: dict) -> set:
    rows = set()
    for table, q in (("fts_words", FTS_WORDS), ("fts_cjk", FTS_CJK)):
        for (rid,) in cc.execute(f"SELECT rowid FROM {table} WHERE {table} MATCH ?", [q]):
            if rid in docs:
                rows.add(rid)
    return rows


def scores_for(sc: sqlite3.Connection, doc_id: str) -> dict:
    return {idx: dict(threat=t / 1000, escalation=e / 1000, hostility=h / 1000, deescalation=d / 1000)
            for idx, t, e, h, d in sc.execute(
                "SELECT idx, threat, escalation, hostility, deescalation FROM sent_scores WHERE doc_id = ?", [doc_id])}


def targets_for(sc: sqlite3.Connection, doc_id: str) -> dict:
    out = defaultdict(set)
    for idx, tgt in sc.execute("SELECT idx, target FROM mentions WHERE doc_id = ? AND self = 0", [doc_id]):
        out[idx].add(tgt)
    return out


def zscores(months: list, cell: dict) -> None:
    """Fill cell[m]['z_threat'] using the previous 12 months with scored >= 10 as baseline."""
    hist = []
    for m in months:
        c = cell.get(m)
        if not c:
            continue
        c["z_threat"] = None
        base = [h for h in hist[-12:]]
        if c["scored"] >= 10 and len(base) >= 6:
            b = statistics.fmean(base)
            sdb = statistics.pstdev(base)
            se = c["_sd"] / math.sqrt(c["scored"]) if c["scored"] > 1 else 0.0
            c["z_threat"] = round((c["threat"] - b) / max(0.01, math.sqrt(sdb ** 2 + se ** 2)), 2)
            c["base_threat"] = round(b, 4)
        if c["scored"] >= 10:
            hist.append(c["threat"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    cc, sc = ro(CORPUS), ro(SEM)
    docs = load_docs(cc, sc)
    cand = candidates(cc, docs)
    add_speakers(cc, docs, cand)

    denom = defaultdict(lambda: defaultdict(int))          # country -> month -> official docs
    streams = defaultdict(lambda: defaultdict(lambda: [0, None, None]))   # country -> source -> [n, first, last]
    for d in docs.values():
        if d["outlet"] != "official":
            continue
        denom[d["country"]][month(d["date"])] += 1
        s = streams[d["country"]][(d["source"], d["lang"])]
        s[0] += 1
        s[1] = d["date"] if s[1] is None or d["date"] < s[1] else s[1]
        s[2] = d["date"] if s[2] is None or d["date"] > s[2] else s[2]

    cell = defaultdict(dict)       # country -> month -> stats
    vals = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    tgt = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    quotes = defaultdict(lambda: defaultdict(list))
    media = defaultdict(lambda: defaultdict(list))
    media_n = defaultdict(lambda: defaultdict(lambda: [0, 0]))   # state media: [docs, docs with W headline]
    for d in docs.values():
        if d["outlet"] == "state_media":
            media_n[d["country"]][month(d["date"])][0] += 1

    for rid in sorted(cand):
        d = docs[rid]
        lang = d["lang"] if d["lang"] in LANGS else None
        if lang is None:
            continue
        c, m = d["country"], month(d["date"])
        if d["outlet"] == "state_media":
            if W[lang].search(d["title"]):
                media_n[c][m][1] += 1
                tone = sc.execute("SELECT threat FROM doc_tone WHERE doc_id = ?", [d["doc_id"]]).fetchone()
                media[c][m].append(dict(date=d["date"], src=d["source"], lang=lang, title=d["title"][:240], url=d["url"],
                                        threat=round(tone[0], 3) if tone and tone[0] is not None else None))
            continue
        sents = cc.execute("SELECT idx, text FROM sentences WHERE doc = ? ORDER BY idx", [rid]).fetchall()
        sco = scores_for(sc, d["doc_id"])
        tg = targets_for(sc, d["doc_id"])
        st = cell[c].setdefault(m, dict(docs_w=0, sent_w=0, sent_a=0, scored=0, hi=0))
        hit = False
        for idx, text in sents:
            if is_question(text):
                continue
            is_w = bool(W[lang].search(text))
            if not is_w:
                if A[lang].search(text):
                    st["sent_a"] += 1
                continue
            hit = True
            st["sent_w"] += 1
            s = sco.get(idx)
            for t in tg.get(idx, ()):
                tgt[c][m][t].append(s["threat"] if s else None)
            if not s:
                continue
            st["scored"] += 1
            st["hi"] += s["threat"] >= 0.5
            for k in DIMS:
                vals[c][m][k].append(s[k])
            if 30 <= len(text) <= 300:
                quotes[c][m].append(dict(date=d["date"], src=d["source"], org=d.get("org"), speaker=d.get("speaker"),
                                         lang=lang, title=d["title"][:200], url=d["url"], text=text.strip(),
                                         threat=round(s["threat"], 3), esc=round(s["escalation"], 3),
                                         tg=sorted(tg.get(idx, ()))))
        if hit:
            st["docs_w"] += 1

    months_all = sorted({m for c in denom for m in denom[c]})
    out_c = {}
    for c in COUNTRIES:
        for m, st in cell[c].items():
            v = vals[c][m]
            for k in DIMS:
                st[k] = round(statistics.fmean(v[k]), 4) if v[k] else None
            st["_sd"] = statistics.pstdev(v["threat"]) if len(v["threat"]) > 1 else 0.0
            st["tgt"] = {t: [len(x), round(statistics.fmean([y for y in x if y is not None]), 3)
                             if any(y is not None for y in x) else None] for t, x in sorted(tgt[c][m].items())}
        zscores(months_all, cell[c])
        rows = []
        for m in months_all:
            n = denom[c].get(m, 0)
            if not n:
                continue
            st = cell[c].get(m, dict(docs_w=0, sent_w=0, sent_a=0, scored=0, hi=0, tgt={}))
            st.pop("_sd", None)
            rows.append(dict(m=m, docs=n, **{k: st.get(k) for k in
                             ("docs_w", "sent_w", "sent_a", "scored", "hi", "threat", "escalation", "hostility",
                              "deescalation", "z_threat", "base_threat", "tgt")}))
        q_out = []
        for m, qs in sorted(quotes[c].items()):
            qs.sort(key=lambda q: -q["threat"])
            keep, per_lang, seen = [], defaultdict(int), set()
            for q in qs:
                key = q["text"][:80]
                if key in seen or (q["lang"] != "en" and per_lang[q["lang"]] >= 2):
                    continue
                seen.add(key)
                per_lang[q["lang"]] += 1
                keep.append(q)
                if len(keep) == 4:
                    break
            q_out.extend(keep)
        m_out = []
        for m, hs in sorted(media[c].items()):
            hs.sort(key=lambda h: -(h["threat"] or 0))
            m_out.extend(hs[:3])
        out_c[c] = dict(
            streams=[dict(source=s, lang=lg, n=v[0], first=v[1], last=v[2])
                     for (s, lg), v in sorted(streams[c].items(), key=lambda kv: -kv[1][0])],
            months=rows, quotes=q_out, media=m_out,
            media_counts=[[m, *media_n[c][m]] for m in sorted(media_n[c]) if media_n[c][m][0]])

    manifest = json.loads((AGG / "manifest.json").read_text(encoding="utf-8"))
    validation = json.loads((AGG / "validation.json").read_text(encoding="utf-8"))
    meta = dict(
        built=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        semantic_generated=manifest.get("generated"), versions=manifest.get("versions"),
        start=START, countries=COUNTRIES, langs=list(LANGS), excluded_samples=list(EXCLUDED_SAMPLES),
        regex_w={k: v.pattern for k, v in W.items()}, regex_a={k: v.pattern for k, v in A.items()},
        validation={k: {kk: validation["dims"][k]["overall"].get(kk) for kk in ("n", "accuracy", "pearson", "kappa", "f1", "auc")}
                    for k in ("threat", "escalation", "hostility")},
        validation_note=validation.get("note"),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(dict(meta=meta, countries=out_c), ensure_ascii=False), encoding="utf-8")
    tot = {c: (sum(r["docs"] for r in out_c[c]["months"]), sum(r["sent_w"] for r in out_c[c]["months"]),
               len(out_c[c]["quotes"]), len(out_c[c]["media"])) for c in COUNTRIES}
    print(f"wrote {args.out} ({args.out.stat().st_size // 1024} KB); docs, W sentences, quotes, headlines: {tot}")


if __name__ == "__main__":
    main()
