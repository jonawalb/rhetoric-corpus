"""Stage `brief`: reports/briefs/brief-<date>.md from the aggregates (no model calls, no interpretation).

Every statement in the generated sections is a measured quantity with its evidence; the "Analyst
interpretation" section is left empty for a human. Static caveats summarise SOURCES.md as of 2026-10-02.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import defaultdict
from datetime import date
from typing import Dict, List

import pandas as pd

from .config import AGG_DIR, BRIEF_DIR, DIMS

logger = logging.getLogger(__name__)
WINDOW_WEEKS = 12
LABEL = {"hostility": "hostility/confrontation", "threat": "threat & coercive signalling", "conciliation": "conciliation/cooperation",
         "grievance": "grievance/victimhood", "escalation": "escalation framing", "deescalation": "de-escalation framing",
         "esc_balance": "escalation-minus-de-escalation balance", "share": "share of documents"}
STATIC_CAVEATS = [
    "Coverage differs sharply by country and period (SOURCES.md). CN state media full text covers 2026-04-01 to 2026-06-30 only; CN headlines 2025-01 to 2026-06-10; CN MFA Jul-Sep 2026 rows are Taiwan-only.",
    "RU media (RIA, TASS, RT): pre-90-day backfill was keyword-filtered (nuclear terms); those regimes are EXCLUDED from trends. 'recent' (politics sections) and 'rss' (all items) are separate streams. RT sitemaps are stale after 2026-06.",
    "US State Department: www.state.gov blocks the collector; current material comes from Wayback captures only. White House press briefings after 2025-01-20 are not published as text.",
    "DPRK: KCNA and Naenara are blocked from this network; Rodong Sinmun English via Wayback captures only.",
    "Telegram (Zakharova, Medvedev) comes from archived preview pages with gaps between captures; RU MoD (mil.ru) is effectively not covered after early 2025.",
    "Machine translations: TAO English is machine-translated; some CN MFA/MND archive English is TSM translation. Tone on translated text may differ from the original.",
]


def _load(name: str) -> Dict:
    p = AGG_DIR / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _q(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().replace("|", "/")[:300]


def _alert_line(a: Dict) -> str:
    what = LABEL.get(a.get("metric"), a.get("metric"))
    if a["kind"] == "stance":
        what = f"{what} in sentences mentioning {a['target']}"
    elif a["kind"] == "salience":
        what = f"share of documents mentioning {a['target']}"
    elif a["kind"] == "topic_share":
        what = f"share of documents in topic {a['topic']} ({a.get('topic_label', '')})"
    who = f"[{a.get('tier', '?')}] " + a["country"] + ("" if a["level"] == "country" else f" · {a['stream']}")
    per = f"{a['period_type']} of {a['period']}" + (f" (run {a['start']}→{a['end']}, {a['n_periods']} periods)" if a.get("n_periods", 1) > 1 else "")
    sign = "+" if (a.get("delta") or 0) >= 0 else ""
    if a["level"] == "country":
        return (f"**{who}** (all streams combined) — {what}: level {a.get('mean') or 0:.3f} in the {per}; change vs each"
                f" stream's own baseline {sign}{a.get('delta') or 0:.3f} (combined z = {a['z']:.1f}; n = {int(a.get('n') or 0)} docs)")
    return (f"**{who}** — {what}: {a.get('mean') or 0:.3f} in the {per} vs baseline {a.get('base') or 0:.3f}"
            f" ({sign}{a.get('delta') or 0:.3f}; z = {a['z']:.1f}; n = {int(a.get('n') or 0)} docs)")


def run_brief(con: sqlite3.Connection, today: str | None = None) -> Dict:
    today = today or date.today().isoformat()
    alerts_j, echoes_j, val, topics_j = _load("alerts.json"), _load("echoes.json"), _load("validation.json"), _load("topics.json")
    alerts = alerts_j.get("alerts", [])
    meta = alerts_j.get("meta", {})
    tlabel = {t["topic"]: t["label"] for t in topics_j.get("topics", [])}
    for a in alerts:
        if a.get("topic") is not None:
            a["topic"] = int(a["topic"])
            a["topic_label"] = tlabel.get(a["topic"], "")
    last = meta.get("data_last_date", today)
    recent = [a for a in alerts if a.get("recent")]
    L = [f"# Rhetoric brief — {today}", "",
         f"Data through {last}. Window for shifts: the {WINDOW_WEEKS} weeks before that date. Generated automatically from"
         " index/semantic/aggregates/ (scripts/semantic). Quotes are single sentences (≤300 characters) with links.", ""]
    # ---- BLUF
    L += ["## BLUF (what the data shows)", ""]
    rank = {"strong": 0, "moderate": 1, "weak": 2}
    key = lambda a: (rank.get(a.get("tier"), 3), -a["score"])  # noqa: E731
    ctry_recent = sorted([a for a in recent if a["level"] == "country"], key=key)
    top = [a for a in ctry_recent[:3] + sorted([a for a in recent if a["level"] == "stream"], key=key)[:4]
           if a.get("tier") != "weak"]
    if not top:
        L.append("- No strong or moderate shift passed the alert thresholds in the window.")
    for a in top:
        L.append(f"- {_alert_line(a)}.")
    ech = [c for c in echoes_j.get("clusters", []) if c.get("last_date", "") >= _minus_weeks(last, WINDOW_WEEKS)]
    ech.sort(key=lambda c: (c.get("type") == "media-media", -c["n_countries"], -c["n_members"]))
    if ech:
        c = ech[0]
        L.append(f"- Largest recent cross-country echo: {c['n_members']} passages across {c['n_countries']} countries "
                 f"({' → '.join(c['countries_in_order'])}), first seen {c['first_seen']['country']} {c['first_seen']['date']}.")
    tc = meta.get("tail_check", {})
    L += ["", f"Alert screen: {meta.get('n_tests', 0):,} period tests across all streams and series. Periods beyond"
          " |z| ≥ 3 / 4 / 4.5: " + " / ".join(f"{v['observed_periods']:,} observed vs {v['expected_if_normal']:,} expected by chance"
                                               for k, v in tc.items() if k in ("|z|>=3.0", "|z|>=4.0", "|z|>=4.5")) + ".",
          "Weak alerts (|z| < 3.5, single period) are about as frequent as chance would produce and are leads only;"
          " strong alerts (|z| ≥ 4.5) are rarely chance but can still reflect collection artefacts — check the evidence.", ""]
    # ---- coverage
    L += ["## Coverage in the window", "", "| country | docs (window) | docs (prior 12 weeks) | active streams |", "|---|---|---|---|"]
    for r in _coverage(con, last):
        L.append(f"| {r['country']} | {r['now']:,} | {r['prior']:,} | {r['streams']} |")
    L.append("")
    # ---- by country
    L += ["## Key shifts by country", ""]
    by_c: Dict[str, List[Dict]] = defaultdict(list)
    comp = {(a["country"], a.get("stream"), a["period"]) for a in recent if a.get("metric") in ("escalation", "deescalation")}
    for a in recent:
        if a.get("metric") == "esc_balance" and (a["country"], a.get("stream"), a["period"]) in comp:
            continue  # its escalation/de-escalation component is already listed
        by_c[a["country"]].append(a)
    for c in sorted(by_c):
        L += [f"### {c}", ""]
        for a in sorted(by_c[c], key=key)[:6]:
            L.append(f"- {_alert_line(a)}")
            if a.get("evidence_note"):
                L.append(f"  - Evidence ({a['evidence_note']}):")
            for e in (a.get("evidence") or [])[:3]:
                sc = f"{int(e['score'])}" if e["metric"] == "mention_sentences" else f"{e['score']:.2f}"
                L.append(f"    - {e['date']} {e['source']}: “{_q(e['text'])}” [{e['metric']} {sc}] <{e['url']}>")
        L.append("")
    if not by_c:
        L += ["No alerts in the window.", ""]
    quiet = sorted({r["country"] for r in _coverage(con, last) if r["now"] > 0} - set(by_c))
    if quiet:
        L += [f"No alerts in the window for: {', '.join(quiet)} (thresholds not met, or too little volume per period).", ""]
    # ---- echoes
    L += ["## Cross-country echoes (near-identical passages within ±14 days)", "",
          "Most clusters are the same event or the same joint text carried by several countries' outlets; official-official and"
          " mixed clusters are listed first. A cluster shows shared content, not by itself a coordinated framing.", ""]
    if not ech:
        L.append("None above the threshold in the window.")
    for c in ech[:6]:
        L.append(f"- **{' → '.join(c['countries_in_order'])}** ({c.get('type', '')}) — {c['n_members']} passages, first seen {c['first_seen']['country']}"
                 f" ({c['first_seen']['source']}) {c['first_seen']['date']}, span {c['span_days']} days")
        seen = set()
        for m in c["members"]:
            if m["country"] in seen:
                continue
            seen.add(m["country"])
            L.append(f"  - {m['date']} {m['country']}/{m['source']}: “{_q(m['text'])}” <{m['url']}>")
    L.append("")
    # ---- topics
    tops = sorted([a for a in recent if a["kind"] == "topic_share" and a["direction"] == "up"], key=lambda a: -a["score"])[:6]
    if tops:
        L += ["## Topics gaining share", ""]
        for a in tops:
            L.append(f"- {_alert_line(a)}")
            for e in (a.get("evidence") or [])[:2]:
                L.append(f"  - {e['date']} {e['source']}: {_q(e['title'] or e['text'])} <{e['url']}>")
        L.append("")
    # ---- method
    L += ["## Method and confidence", "",
          "- Tone: zero-shot multilingual NLI (bge-m3-zeroshot-v2.0) labelled a stratified sentence sample; logistic-regression"
          " students on multilingual-e5-small embeddings scored all sentences in scope. Document tone = mean sentence probability.",
          "- Trends are computed within stream (source × language × selection regime) against each stream's own rolling baseline"
          " (weekly: 12 weeks; monthly: 6 months); country values combine streams with fixed weights, so changes in source mix"
          " do not register as tone shifts.",
          "- Targets: multilingual gazetteer (audited; reports/semantic/target_audit.md). Stance = tone of sentences that mention"
          " the target, which may be directed at another actor in the same sentence.",
          "- Echo: cosine similarity of e5 passage embeddings across countries within ±14 days (threshold in echoes.json).", ""]
    if val:
        L += ["Teacher–student agreement on held-out documents (kappa at p = 0.5 / Pearson r):", ""]
        L.append("; ".join(f"{d} {val['dims'][d]['overall'].get('kappa', float('nan')):.2f} / {val['dims'][d]['overall'].get('pearson', float('nan')):.2f}"
                           for d in DIMS if d in val.get("dims", {})) + ".")
        L += ["", "**Confidence: low-to-moderate.** These numbers show the fast classifier reproduces the NLI teacher; they do not"
              " show that the teacher matches expert human judgement. Human validation (reports/semantic/validation_sample.csv)"
              " has not been completed. Treat levels as relative indicators within a stream, not absolute measures.", ""]
    # ---- caveats
    L += ["## Caveats", ""]
    L += [f"- {c}" for c in STATIC_CAVEATS]
    L += [f"- Stream ended before the window (no new documents): {s}" for s in _stale(con, last)]
    L += ["- Languages with little teacher data (fa, ur, tr, and any new es/ar/ko sources) have unmeasured classifier agreement.",
          "- 'First seen' in echoes means first in this corpus, not origin.", ""]
    L += ["## Analyst interpretation", "", "_To be completed by the analyst. Nothing in this section is machine-generated._", ""]
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    out = BRIEF_DIR / f"brief-{today}.md"
    out.write_text("\n".join(L), encoding="utf-8")
    return {"brief": str(out), "recent_alerts": len(recent), "echo_clusters_in_window": len(ech)}


def _minus_weeks(d: str, w: int) -> str:
    return (pd.Timestamp(d) - pd.Timedelta(weeks=w)).strftime("%Y-%m-%d")


def _coverage(con: sqlite3.Connection, last: str) -> List[Dict]:
    a, b = _minus_weeks(last, WINDOW_WEEKS), _minus_weeks(last, 2 * WINDOW_WEEKS)
    rows = con.execute("SELECT country, sum(date > ?), sum(date > ? AND date <= ?), count(DISTINCT CASE WHEN date > ?"
                       " THEN source || lang END) FROM docs WHERE present=1 GROUP BY country ORDER BY country",
                       (a, b, a, a)).fetchall()
    return [{"country": r[0], "now": r[1] or 0, "prior": r[2] or 0, "streams": r[3] or 0} for r in rows]


def _stale(con: sqlite3.Connection, last: str) -> List[str]:
    a = _minus_weeks(last, WINDOW_WEEKS)
    rows = con.execute("SELECT source, lang, max(date), count(*) FROM docs WHERE present=1 GROUP BY source, lang"
                       " HAVING max(date) < ? AND count(*) >= 50 ORDER BY source", (a,)).fetchall()
    return [f"{s} ({lg}, last {d}, {n:,} docs)" for s, lg, d, n in rows]
