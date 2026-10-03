# Semantic layer — data contract

All outputs live in `index/semantic/` (gitignored, private). The UI should read only
`index/semantic/aggregates/*.json`; the SQLite store and `.npy` shards are internal. Rebuild with
`uv run python -m scripts.semantic.run` (incremental). JSON is UTF-8, `ensure_ascii=False`; missing values are `null`.

**Text policy.** Aggregates contain single sentences/passages (≤300 characters; search passages ≤600) with links,
from both official sources and state media. Anything published outside this machine must drop `text`/`passage`
for records whose `outlet` is not `"official"` (same rule as the public Rhetoric Search: official sentences ≤300 chars only).

## Identifiers and vocabularies

| name | meaning |
|---|---|
| `doc_id` | corpus document id (`docs.id` in corpus.sqlite), stable across re-indexing |
| `stream` | `source|lang|sample` (e.g. `mid_ru|ru|all`, `tass_com|en|recent`). The unit within which every series is computed. `sample` = the document's selection regime (`all` when the collector did not set one). Regimes `backfill` and `seed` (keyword-filtered) are excluded from all series and alerts. |
| `country` | ISO-like code used in docs/ (`RU`, `CN`, `IR`, `KP`, `BY`, `US`, `PK`, `IN`, `TR`, `SY`, `TW`, …) |
| `metric` | tone dimension: `hostility`, `threat`, `conciliation`, `grievance`, `escalation`, `deescalation`, or `esc_balance` (= escalation − deescalation, range −1..1); `share` for salience/topic-share series |
| `target` | `US NATO EU UK JAPAN ROK TAIWAN PHILIPPINES UKRAINE ISRAEL CHINA RUSSIA IRAN INDIA PAKISTAN DPRK AUSTRALIA WEST` (WEST = "collective West"/西方/غرب epithets). Self-mentions (target = speaker's own country) are excluded from stance and salience. |
| `period` | weekly: Monday date `YYYY-MM-DD` (ISO week); monthly: `YYYY-MM` |

Tone values are probabilities in 0..1. A document's tone = mean over its scored sentences (= expected share of its
sentences expressing the dimension). A series `mean` = mean of document tones in the period (each doc weighted equally).

## manifest.json
`generated` (UTC ISO), `versions` {`gazetteer`, `tone_model` (JSON string: `hyp_v`, `n`, `version`), `e5`, `nli_teacher`},
`totals` {`docs`, `passages`, `sentences_scored`, `mentions`, `teacher_sample`},
`coverage_by_country` {CC: {`docs`, `embedded`, `scored`, `first`, `last`}}, `settings` (all knobs), `last_run` (per-stage stats).

## validation.json (teacher–student agreement; NOT human validation)
`model_version`, `hyp_v`, `n_train`, `n_heldout`, `langs_train` {lang: n}, `note`,
`dims` {dim: {`C`, `overall`, `random_arm`, `by_lang` {lang: …}, `by_country` {CC: …}}} where each agreement block is
{`n`, `teacher_pos`, `student_pos`, `accuracy`, `pearson`, `spearman`, `kappa`, `f1`, `auc`} (binary metrics at p = 0.5;
keys absent when undefined, e.g. no positives). `random_arm` = only randomly drawn sentences (no target enrichment).
`validation_human.json` appears after `run validate <coded.csv>`: {dim: {`human_vs_student`, `human_vs_teacher`, `random_arm_human_vs_student`}}.

## Series files: common columnar layout
Every `series_*.json` is `{"generated", "streams", "series": {"week": [obj…], "month": [obj…]}}` where each `obj` is one
series: its key fields plus aligned lists over `period` (only periods with n > 0 are listed; gaps = no documents):
```
{"stream": "mid_ru|ru|all", "metric": "hostility", "period": ["2026-08-31", …], "n": [12, …], "mean": [0.081, …],
 "se": […], "base": […], "delta": […], "z": […]}
```
`n` = docs in the period; `mean` as defined above; `se` = sd/√n; `base` = mean of the stream's previous 12 weeks
(6 months) with n ≥ 8 (20); `delta` = mean − base; `z` = delta / √(sd_base² + se²) (denominator floored at 0.01).
`base/delta/z` are null when the period or its baseline has too little volume. Values rounded to 4 decimals.
`streams` (null in series_country): [{`stream`,`country`,`source`,`lang`,`sample`,`n_docs`,`weight`,`first`,`last`}];
`weight` = √(stream's total docs), the fixed weight used when combining streams into country values.

| file | series key | value lists |
|---|---|---|
| series_tone.json | `stream`, `metric` (6 dims + `esc_balance`) | n, mean, se, base, delta, z |
| series_country.json | `country`, `metric` | value, adj_delta, z, n, n_streams |
| series_stance.json | `stream`, `target`, `metric` (hostility, threat, conciliation, grievance, esc_balance) | n, mean, se, base, delta, z |
| series_salience.json | `stream`, `target` | n, mean (= k/n), se, base, delta, z, k |
| series_topic_share.json | `stream`, `topic` | n, mean (= k/n), se, base, delta, z, k |

- **country**: `value` = fixed-weight mean over the country's streams with n ≥ min_n that period; `adj_delta` = weighted
  mean of within-stream deltas; `z` = weighted Stouffer combination of stream z-scores. Use `adj_delta`/`z` for change,
  `value` for level (levels still move when streams enter/leave; change measures do not).
- **stance**: per doc, the tone of its sentences that mention the target is averaged; `n` = docs mentioning the target.
- **salience** / **topic_share**: share of the stream's docs that mention the target (non-self) / are assigned to the
  topic. For these, z uses the larger of the null binomial error √(p0(1−p0)/n) and the continuity-corrected period error.
  Series whose `k` is never > 0 are omitted.

## alerts.json
```
{"meta": {"generated","n_tests","expected_false_alerts_approx","expected_note","z_threshold","min_effect",
          "data_last_date","baseline","excluded_samples","tail_check","tiers"},
 "alerts": [{"id","kind","level","period_type","country","stream","metric","target","topic",
             "start","end","n_periods","period","mean","base","delta","z","n","score","recent","direction","tier",
             "evidence": [{"text","score","metric","doc_id","date","country","source","outlet","title","url"}],
             "evidence_note"}]}
```
- `kind`: `tone` | `stance` | `salience` | `topic_share`; `level`: `stream` | `country` (country = combined tone only;
  `stream` is null). Thresholds: |z| ≥ 3 for tone, ≥ 3.5 for stance/salience/topic share, and |delta| ≥ 0.03.
- Consecutive flagged periods of one series in one direction are merged: `start`..`end`, `n_periods`; `period`, `mean`,
  `z`, `n` are those of the peak (largest |z|) period.
- Order: `recent` (ends within 12 weeks of `data_last_date`) first, then `score` = |z|·log10(n+10).
- `evidence`: up to 5 sentences, one per document, from the peak period (and stream/country), ranked by the metric
  (for `esc_balance`: escalation if up, deescalation if down; for stance/salience: only sentences mentioning the target;
  for topic_share: documents closest to the topic centroid, `text` = title). Present for every recent alert and the
  top 600 overall; otherwise absent. Downward alerts show what remained, not what disappeared (see `evidence_note`).
- `tier`: `strong` (|z| ≥ 4.5), `moderate` (|z| ≥ 3.5 or ≥ 2 consecutive flagged periods), `weak` (otherwise).
  `meta.tail_check` compares observed period counts beyond |z| = 3, 3.5, 4, 4.5 with what N(0,1) would give (on the
  current corpus: about chance level at 3, far above chance at 4.5). Show weak alerts as leads only.
- `meta.expected_false_alerts_approx`: periods expected to pass the z threshold by chance if all z were N(0,1).

## topics.json
`version`, `k`, `silhouette_by_k`, `fit_sample`, `docs_assigned`, `multilingual_topics` (topics where ≥ 2 languages each
hold ≥ 10 % of docs), `topics`: [{`topic`, `label`, `n_docs`, `terms` {lang: [top c-TF-IDF terms]}, `langs`, `countries`,
`exemplars`: [{`doc_id`,`country`,`source`,`lang`,`date`,`title`,`url`}]}],
`prevalence`: [{`country`,`month`,`topic`,`n_docs`,`country_docs`,`share`,`balanced_share`}] — `share` = raw share of the
country's docs; `balanced_share` = fixed-weight mean of within-stream shares (robust to source-mix changes).
Topic ids change when the model is refitted (`version` changes).

## echoes.json
`meta` {`threshold`,`window_days`,`passages_compared`,`edges`,`clusters`,`clusters_written`,
`pairs_by_country` {"A>B": pairs where A's passage is dated no later than B's}},
`clusters`: [{`id`,`n_countries`,`n_members`,`countries_in_order`,`official_countries` (countries with an official-outlet
member), `type` (`official-official` | `mixed` | `media-media`), `first_seen` {`country`,`date`,`source`},`last_date`,
`span_days`,`members` (first 12, date order): [{`date`,`country`,`source`,`outlet`,`lang`,`doc_id`,`title`,`url`,`text`,`max_sim`}]}],
sorted by number of countries, then number of official countries, then size, then recency. Title passages are used only
for articles/headlines (official titles are formulaic) and per-source title templates are excluded. "First seen" = first
in this corpus, not origin. Many clusters are the same news event carried by several countries' media; `official-official`
and `mixed` clusters are the likelier candidates for coordinated or repeated lines.

## Semantic search (Python)
`scripts.semantic.search.search(con, query, k, country=[…], source=[…], lang=[…], date_from, date_to)` →
[{`score`,`doc_id`,`date`,`country`,`source`,`outlet`,`lang`,`title`,`url`,`passage`}] (one best passage per doc).
`con = scripts.semantic.store.connect()`. Loads the e5 model once per process (~1–2 s) and all passage vectors
(~300 MB float16) on first call.

## Internal store (for maintainers)
`index/semantic/semantic.sqlite`: `docs` (metadata mirror + per-stage state), `passages` (pid → doc, sentence range),
`shards` (pid range → `emb/p_*.npy`, float16 [n, 384], row i = pid lo+i), `mentions` (doc_id, idx, target, pattern, self),
`sent_scores` (doc_id, idx, crc, six dims as integers 0..1000), `doc_tone`, `teacher` (sample, NLI labels, e5 vectors),
`doc_topic`, `meta`. `tone_model.npz`, `topic_model.npz`, `validation_key.csv`, `models/` (HF weights).
