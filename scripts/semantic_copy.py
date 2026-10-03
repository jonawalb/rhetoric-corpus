"""Fixed copy for the analyst views (scripts/semantic_views.py): country names, method text, caveats and known
coverage notes (from SOURCES.md and README "Semantic layer"). Plain, neutral wording; shown in both the private UI
and the public Trends page. Update KNOWN_NOTES when SOURCES.md records a new gap or window."""
from __future__ import annotations

COUNTRY_NAMES = {"RU": "Russia", "CN": "China", "IR": "Iran", "KP": "North Korea", "BY": "Belarus", "US": "United States",
                 "PK": "Pakistan", "IN": "India", "TR": "Türkiye", "SY": "Syria", "VE": "Venezuela", "CU": "Cuba",
                 "TW": "Taiwan"}

KNOWN_NOTES = [
    {"country": "CN", "period": "2026-04", "note": "PRC state media (prc_statemedia) has full text for April–June 2026 "
     "only; outside those months China's series rest on official sources and headlines. Country levels move when that "
     "stream enters and leaves; within-stream changes do not."},
    {"country": "RU", "period": "2026-07", "note": "Russian state media (RIA, TASS, RT) keep a dense 'recent' window "
     "for the last 90 days and a thinner section sample before it. Document volume jumps when the window starts; each "
     "regime is its own stream with its own baseline, so the jump is not a tone change, but country levels mix them."},
    {"country": "RU", "period": "2026-06", "note": "rt.com / russian.rt.com year sitemaps were last regenerated in June "
     "2026: section-sample coverage thins from mid-June to late September 2026."},
    {"country": "RU", "period": None, "note": "Older RT rows collected under a nuclear/strategic keyword filter "
     "('backfill') and web-search 'seed' rows are excluded from every series and alert."},
    {"country": "CN", "period": "2026-07", "note": "PRC Foreign Ministry: July–September 2026 imported rows are "
     "Taiwan-related only; the live collector (mfa_cn_live) fills full press conferences from June 2026."},
    {"country": "TR", "period": "2026-09", "note": "Anadolu Agency 'recent' stream starts 2026-09-25; its earlier "
     "'backfill' rows are limited to archived captures and are excluded from series."},
    {"country": "US", "period": None, "note": "State Department spokesperson releases cover April 2025 onward only."},
    {"country": "RU", "period": None, "note": "Zakharova/Medvedev Telegram posts come from archived preview pages, "
     "with gaps between captures."},
    {"country": "KP", "period": None, "note": "No North Korean documents yet (sources unreachable from the collector)."},
]

METHOD = [
    {"h": "What is measured", "paragraphs": [
        "Documents are official statements (presidential, foreign and defence ministries, other government bodies) "
        "and state or national media, collected per source. Every series is computed within a stream (source × "
        "language × selection regime). Keyword-filtered regimes are excluded so that a topic filter cannot look like "
        "a shift in tone.",
        "Tone has six dimensions: hostility/confrontation, threat and coercive signalling, conciliation/cooperation, "
        "grievance/victimhood, escalation framing and de-escalation framing (plus the balance escalation minus "
        "de-escalation). Each sentence gets a probability per dimension; a document's tone is the mean over its "
        "scored sentences, read as the expected share of its sentences that express the dimension.",
        "Stance toward a target is the tone of the sentences that mention the target. It is not a measure of tone "
        "directed at the target: a sentence can mention the United States while attacking someone else. Salience is "
        "the share of documents that mention the target; a country's mentions of itself are excluded."]},
    {"h": "Change, baselines and alerts", "paragraphs": [
        "Each stream is compared with its own recent past: the mean of the previous 12 weeks (or 6 months), using only "
        "periods with enough documents (8 per week, 20 per month). z = change / combined standard error. A country's "
        "change is the weighted mean of its streams' changes and its z a weighted Stouffer combination, with fixed "
        "weights (square root of each stream's documents). Levels move when streams enter or leave; change measures "
        "do not.",
        "Alerts need |z| ≥ 3 for tone (3.5 for stance, salience and topic share) and a change of at least 0.03. Tiers: "
        "strong |z| ≥ 4.5; moderate |z| ≥ 3.5 or two or more consecutive flagged periods; weak otherwise. Across about "
        "260,000 period tests the number of periods beyond |z| = 3 is at the level chance would produce, while beyond "
        "4.5 it is far above chance. Weak alerts are leads only. Every alert lists the sentences behind it; a "
        "downward shift's evidence shows what remained, not what disappeared."]},
    {"h": "How tone is scored, and how far it is validated", "paragraphs": [
        "A zero-shot multilingual natural-language-inference model (the 'teacher', MoritzLaurer/bge-m3-zeroshot-v2.0) "
        "labelled a stratified sample of about 18,000 sentences. A fast classifier per dimension (logistic regression "
        "on multilingual-e5-small sentence embeddings) was trained to reproduce the teacher and scores every sentence.",
        "Validation so far measures only agreement between that classifier and the AI teacher on held-out documents. "
        "Nobody has yet checked the teacher, or the classifier, against expert human coding. Treat levels as "
        "model-relative indicators, compare a stream with itself over time, and read the evidence sentences before "
        "drawing a conclusion. Grievance is under-detected by the teacher; languages with little teacher data "
        "(Persian, Urdu, Turkish, Spanish, Arabic) have less certain accuracy."]},
    {"h": "Topics and echoes", "paragraphs": [
        "Topics are clusters of multilingual document embeddings (k chosen by silhouette), labelled by their top "
        "terms in each language and three exemplar documents. Shares are balanced across streams.",
        "Echoes are near-identical passages from different countries within ±14 days (cosine similarity ≥ 0.93). "
        "'First seen' means first in this corpus, not origin. A match can be quotation, a joint statement or shared "
        "wire copy; it shows shared content, not by itself coordination."]},
]

CAVEATS = [
    "Tone is validated only against an AI teacher model, not yet against human coders.",
    "Stance = tone of sentences that mention a target, not tone directed at it.",
    "Coverage gaps carry straight into the series (see Coverage); levels shift when sources enter or leave.",
    "Weak alerts occur about as often as chance predicts: leads only.",
    "Echo 'first seen' is first in this corpus, not the origin of a line.",
]
