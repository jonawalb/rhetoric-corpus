"""Stage `targets`: tag every corpus sentence with the targets it mentions; stage `audit`: per-pattern report."""
from __future__ import annotations

import logging
import random
import re
import sqlite3
import time
from collections import Counter, defaultdict
from typing import Dict, List

from . import store
from .config import REPORT_DIR
from .targets import COUNTRY_SELF, Gazetteer, default

logger = logging.getLogger(__name__)


def run_targets(con: sqlite3.Connection, gaz: Gazetteer | None = None, full: bool = False) -> Dict[str, int]:
    """Tag sentences of docs whose targets_v differs from the gazetteer version (all docs with full=True)."""
    gaz = gaz or default()
    if full:
        con.execute("UPDATE docs SET targets_v=NULL")
    todo = con.execute("SELECT doc_id, crow, lang, country FROM docs WHERE present=1 AND (targets_v IS NULL OR targets_v<>?)",
                       (gaz.version,)).fetchall()
    logger.info("targets: %d docs to tag (gazetteer %s)", len(todo), gaz.version)
    cc = store.corpus()
    t0, nment = time.time(), 0
    for i in range(0, len(todo), 2000):
        part = todo[i:i + 2000]
        sents = store.sentences_for(cc, [r[1] for r in part])
        rows, done = [], []
        for doc_id, crow, lang, country in part:
            self_t = COUNTRY_SELF.get(country or "")
            for idx, text in sents.get(crow, []):
                for target, key in gaz.match(text, lang):
                    rows.append((doc_id, idx, target, key, int(target == self_t)))
            done.append((gaz.version, len(sents.get(crow, [])), doc_id))
        con.executemany("DELETE FROM mentions WHERE doc_id=?", [(r[0],) for r in part])
        con.executemany("INSERT OR IGNORE INTO mentions(doc_id, idx, target, pat, self) VALUES(?,?,?,?,?)", rows)
        con.executemany("UPDATE docs SET targets_v=?, nsent=? WHERE doc_id=?", done)
        con.commit()
        nment += len(rows)
        if (i // 2000) % 10 == 0:
            logger.info("targets: %d/%d docs, %d mentions, %.0fs", i + len(part), len(todo), nment, time.time() - t0)
    cc.close()
    return {"docs_tagged": len(todo), "mentions_added": nment}


def audit(con: sqlite3.Connection, samples: int = 6, seed: int = 13) -> str:
    """Write reports/semantic/target_audit.md: hits per pattern (sentences, docs, countries) + random samples."""
    gaz = default()
    rng = random.Random(seed)
    rows = con.execute("SELECT m.pat, m.target, m.doc_id, m.idx, d.country, d.crow, m.self FROM mentions m"
                       " JOIN docs d USING(doc_id) WHERE d.present=1").fetchall()
    by_pat: Dict[str, List] = defaultdict(list)
    for r in rows:
        by_pat[r[0]].append(r)
    cc = store.corpus()
    pat_obj = {p.key: p for ps in gaz.patterns.values() for p in ps}
    lines = ["# Target gazetteer audit", "", f"Gazetteer version `{gaz.version}`; {len(rows):,} sentence-level mentions.",
             "Samples are random; the match is shown in **bold** within ±70 characters.", "",
             "## Dropped patterns", ""]
    from .targets import DROPPED
    lines += [f"- {t} `{lang}:{p}` — {why}" for t, lang, p, why in DROPPED]
    lines += ["", "## Hits per pattern", "", "| target | pattern | sentences | docs | self % | by country |", "|---|---|---|---|---|---|"]
    for key in sorted(pat_obj, key=lambda k: (pat_obj[k].target, k)):
        hits = by_pat.get(key, [])
        cn = Counter(r[4] for r in hits)
        selfp = 100 * sum(r[6] for r in hits) / len(hits) if hits else 0
        lines.append(f"| {pat_obj[key].target} | `{key.replace('|', chr(92) + '|')}` | {len(hits):,} | {len({r[2] for r in hits}):,} | {selfp:.0f} |"
                     f" {', '.join(f'{c}:{n}' for c, n in cn.most_common(6))} |")
    lines += ["", "## Samples", ""]
    for key in sorted(by_pat, key=lambda k: -len(by_pat[k])):
        p = pat_obj.get(key)
        if p is None:
            continue
        lines.append(f"### {p.target} `{key}` ({len(by_pat[key]):,})")
        for r in rng.sample(by_pat[key], min(samples, len(by_pat[key]))):
            text = store.sentence_text(cc, r[5], r[3]) or ""
            m = p.rx.search(text)
            if m:
                a, b = max(0, m.start() - 70), min(len(text), m.end() + 70)
                snip = text[a:m.start()] + "**" + m.group(0) + "**" + text[m.end():b]
            else:
                snip = text[:160]
            snip = re.sub(r"\s+", " ", snip)
            lines.append(f"- {r[4]} `{r[2]}`: {snip}")
        lines.append("")
    cc.close()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / "target_audit.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return str(out)
