# Semantic layer: tone, targets, topics, trends, alerts (design, 2026-10-02)

Goal (Jonathan): make the corpus useful to US IC / DoD analysts studying the rhetoric and behaviour of other
states — what they say, how hard, about whom, and when it shifts.

Constraints: all models run locally (no text leaves the machine; no API keys); Apple M3, 16 GB RAM, ~14 GB
free disk; every number must trace back to quotable source sentences with links; methods and limits documented
(analytic-standards style: sourcing, confidence, caveats). Never claim validity that was not measured.

## Components
1. Embeddings — multilingual sentence model (intfloat/multilingual-e5-small, 384-d), passages per doc
   (capped), incremental by doc id. Enables cross-lingual semantic search ("find statements like this one").
2. Tone dimensions per sentence → doc → series: hostility/confrontation, threat & coercive signalling,
   conciliation/cooperation, grievance/victimhood, escalation vs de-escalation framing. Method: multilingual NLI
   (zero-shot) as teacher on a stratified sample → light classifiers on embeddings for full-corpus scoring;
   report teacher/student agreement on held-out data; export a blind sample for human validation.
3. Targets — multilingual gazetteer (US, NATO, EU, Japan, ROK, Taiwan, Philippines, Ukraine, Israel, China,
   Russia, India, Pakistan, …) → per-target stance = tone of sentences that mention the target.
4. Topics — clustering of doc embeddings (cross-lingual), labelled by top terms + exemplar titles; prevalence
   by country × month.
5. Trends & alerts — weekly/monthly series per country × source × dimension × target, computed within source
   (composition shifts must not masquerade as tone shifts); rolling-baseline z-scores + change points →
   "notable shifts" list with top evidence sentences and links.
6. Narrative echo — semantically near-identical framings appearing across countries within a window
   (e.g. RU→CN→IR amplification), with first-seen ordering.
7. Outputs — private: semantic search + dashboards in serve_search.py; auto weekly brief (BLUF, shifts,
   evidence, confidence, caveats) in reports/briefs/. Public (jwalberg.com): aggregates and charts only
   (no media text; official sentences ≤300 chars as today).
