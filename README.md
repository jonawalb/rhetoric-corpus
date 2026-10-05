# rhetoric-corpus

Research collection code for a multi-country corpus of official statements and state media (Russia, Iran, China and
more): polite collectors, a full-text index, a local semantic layer, and the build of the sealed data behind the public
Rhetoric Search tool (Interactive Deterrence, jwalberg.com). **Data is not included in this repository**: collected
texts, collector state, indexes and reports live in a private store, and anything public ships only sentences of at
most 300 characters with a link. SOURCES.md lists every source, its robots/rate notes and blockers.

## Nightly job (GitHub Actions)

`.github/workflows/nightly.yml` runs daily at 07:00 UTC (and on demand): pull the private store
(`scripts/store_sync.py pull`, Hugging Face dataset `wallabee1/rhetoric-corpus-store`, private: state, id indexes, the
stored search index, and only the document parts that index has not indexed yet) → collect, time-boxed
(`scripts/ci_collect.py`: the collectors of `scripts/resume_all.sh`, all sources in parallel, new items before
backfill, SIGTERM at the deadline) → `store_sync.py seal --keep-parts` (the run's documents become one verified part)
→ `build_index.py` → semantic layer (incremental, CPU) → `publish/build_trends.py`
→ `publish/build_hf_data.py --trends staging/trends --upload` (sealed search + Trends data to the public dataset
`wallabee1/rhetoric-search-data`, which the live page reads, so no site redeploy is needed) → push the store (always,
even after a failed step). Sundays also run a full semantic pass and squash the Hub histories. Secrets: `HF_TOKEN`
(write to both datasets), `RHETORIC_TIER_PASSWORD` (the Rhetoric Search password tier; `publish/vault.py`).
Collector output goes to `logs/ci/<date>/` in the private store; the public run log shows counts only.

## Layout

```
docs/<COUNTRY>/<source>.jsonl   active buffer, one document per line (schema below); every collector appends here
docs-parts/<YYYY-MM>/*.jsonl.gz sealed parts (store layout 2): immutable gzip JSONL of many sources; on the laptop
                                they live only in the private store (`store_sync.py pull --only docs-parts` fetches all)
state/ids/<COUNTRY>/<source>.ids  ids already sealed into parts: writers skip them (lib.write_docs & co.)
raw/<source>/...                raw HTML/PDF caches (never shipped)
index/corpus.sqlite             search index built from docs/ (scripts/build_index.py; only that script writes it)
collectors/lib.py               shared polite-fetch library; collectors/<source>.py use it
scripts/                        import_existing.py, build_index.py, search.py, serve_search.py, term_report.py
terms/*.txt                     term lists for term_report.py (plutonium.txt = the pit test)
reports/                        term reports (Markdown)
state/                          resumable collector state and logs
SOURCES.md                      one row per source: URL, coverage, counts, collector, robots/rate notes, blockers
```

Git tracks code, SOURCES.md and terms; `docs/ raw/ index/ state/ logs/ reports/` are ignored (large, and private; the
nightly job keeps them in the private store).

Segmented store (`scripts/segments.py`, since 2026-10-04): `store_sync.py seal` (run by `scripts/laptop_offload.sh`
every 3 h, and by CI after collecting) cuts the complete lines of every active docs file into one gzip part, uploads
it, verifies it in the store (sha256 + line count of a fresh download), appends the ids to `state/ids/`, and only then
removes the sealed lines from the local files (under the collectors' `docs/<CC>/<source>.lock`). Parts are never
rewritten; `build_index.py` indexes each part once (rowids stay stable); readers that need the documents themselves
use `segments.iter_docs()` (parts + active files, each id once). `store_sync.py migrate` converted the old per-source
`docs/*.jsonl` of the store into `docs-parts/legacy/` once.

Document schema: `{"id": "<source>:<stable id>", "country": "RU", "source": "kremlin_en", "outlet":
"official|state_media|commentary", "org": "Kremlin", "lang": "en|ru|zh|fa|ko|…", "date": "YYYY-MM-DD", "url": …,
"title": …, "speaker": … | null, "kind": "transcript|briefing|statement|article|qa|interview|headline",
"text": "full text", "via": "direct|wayback|rss|export", "fetched": ISO time}` plus optional extra fields
(`translation`, `asker`, `url_kind`, `wayback`, …). One language per document.

## Writing a collector

```python
import sys; sys.path.insert(0, "collectors")
from lib import fetch, write_docs, make_id, clean_html, State, setup_logging

log = setup_logging("ru_mid")
st = State("ru_mid")                                   # state/ru_mid.json; resumable
html = fetch(url, min_delay=10, use_wayback_fallback=True)   # None if robots.txt disallows / 404 / failed
via = fetch.last["via"]                                # "direct" | "wayback" | "cache"
write_docs("RU", "mid_ru", [{"id": make_id("mid_ru", url), ...}])   # appends, skips ids already present
st.mark_done(url); st.save()
```

`fetch` checks robots.txt first, keeps a per-host start-to-start gap of at least 4 s (10 s for hosts in
`HOST_DELAY`), retries with backoff, stops at 401/403/404 (no evasion), and can fall back to the latest
HTTP-200 Wayback capture (CDX, raw `id_` copy, 5 s spacing). `write_docs` validates the schema and holds a
file lock while appending.

## Import existing data

```
uv run python scripts/import_existing.py        # rewrites docs/RU/kremlin_en, IR/iran_mfa_en, CN/* (~15 s)
```

## Index

```
uv run python scripts/build_index.py            # incremental: new lines appended to a file are added
uv run python scripts/build_index.py --rebuild  # from scratch (~1 min for 90k docs; ~490 MB)
```

Tables: `docs` (metadata + text), `fts_words` (FTS5, unicode61 remove_diacritics 2: Latin, Cyrillic, Persian;
indexed text is folded ё→е, ي→ی, ك→ک), `fts_cjk` (FTS5 trigram, zh/ko/ja documents only), `sentences`
(doc, idx, text ≤ 300 chars). Measured on 90,150 docs: split design 485 MB / 60 s; one trigram table for
everything (`--rebuild --trigram-all`) 681 MB / 100 s, so trigram is kept to CJK documents.

## Private search

CLI:
```
uv run python scripts/search.py "plutonium pit"
uv run python scripts/search.py 'плутониевый сердечник' --morph --country RU
uv run python scripts/search.py '"pit production" OR 钚芯' --from 2023-01-01 --count-by month
uv run python scripts/search.py 'Taiwan NEAR/5 independence' --source mfa_cn --export out.csv
```
Filters `--country --source --lang --from --to`; `--count-by week|month|year|source|country`; `--export csv`
(one row per hit); `--sort rank`; `--explain` prints the FTS5 expression.

Web UI (localhost only): `uv run python scripts/serve_search.py` → http://127.0.0.1:8950/ — search box,
filters, highlighted KWIC snippets with links, a month histogram (click a bar to filter), counts by source.

Query language: no operators = exact phrase; otherwise `"phrases"`, `AND` (implicit), `OR`, `NOT`, `( )`,
`prefix*`, `a NEAR/10 b`. Queries with Chinese/Korean/Japanese run on the trigram table (substring match); a
CJK term shorter than 3 characters (钚芯) falls back to scanning the CJK documents (~0.5 s). Mixed-script `OR`
queries run each branch on its own table and merge.

**Russian morphology** (`--morph`, checkbox in the web UI): uses **pymorphy3** (installs cleanly with uv).
Each Cyrillic word becomes a prefix on the stem shared by all its dictionary forms (плутониевый →
`плутониев*`, сердечник → `сердечник*`, разучились → `разуч*`); comparatives and short adjectives are left
out of the stem calculation. Where that stem would be under 4 letters (ядро/ядра/ядер) the word becomes an
OR of its forms. Unknown words fall back to the Snowball stemmer. Prefixes can over-match (разуч* also finds
разучивать); read the hits.

## Term reports

```
uv run python scripts/term_report.py terms/plutonium.txt      # -> reports/plutonium-<date>.md
```
Every query in the term file (one per line, `label | query`, `## group` headings) is run over the whole
index; Cyrillic queries get word-form expansion. The report lists counts and every hit (date, source,
speaker, title, snippet, URL). Rerun `build_index.py` then `term_report.py` after new sources land.

## Public search prototype

`~/Projects/tsm-strait-layers/tools/rhetoric-search/` (not in the site registry). Build its data with
`uv run --project ~/Projects/rhetoric-corpus python tools/rhetoric-search/scripts/build_public.py [--countries CN]`
from the site repo. See that script's docstring.

### Hosting on Hugging Face (search data off GitHub)

The public search data (s/ t/ m/ shards, meta, and full-document shards for official texts) is no longer committed
to tsm-strait-layers. `tools/rhetoric-search/scripts/build_hf_data.py` (in the site repo) builds it into
`staging/` here (gitignored), seals every file with the Rhetoric Search password tier (same format gate.js decrypts),
and with `--upload` pushes it to a public Hugging Face dataset repo (`HF_REPO` in `tools/rhetoric-search/js/config.js`):

```
staging/hf/README.md, .gitattributes   dataset card (says only: encrypted data for a research tool), LFS rules
staging/hf/data/current.json           sealed pointer {"build": "<id>"}; the page reads it first
staging/hf/data/<build-id>/            s/ t/ m/ docs/ meta.json.gz (sealed) + manifest.json (names, sizes, SHA-256)
```

Full documents (`docs/`) are official texts only (outlet == "official"): title, date, source, speaker, url, Wayback
link, full text. State media / media / commentary never ship text; `assert_no_media_text()` fails the build if a
non-official rowid or any text identical to a media document lands in them (tests/test_hf_data.py). The page keeps
the last 2 builds; an upload copies unchanged files from the previous build and moves current.json only after
the new build is complete. Trends data (`data/trends/`, ~1 MB) stays in the site.

```
cd ~/Projects/tsm-strait-layers
uv run --project ~/Projects/rhetoric-corpus --with cryptography python tools/rhetoric-search/scripts/build_hf_data.py              # stage only
uv run --project ~/Projects/rhetoric-corpus --with cryptography python tools/rhetoric-search/scripts/build_hf_data.py --upload-only # upload newest staged build
uv run --with playwright python tools/rhetoric-search/scripts/e2e_hf_reader.py                                                   # browser test vs staging
```

Nightly refresh (not scheduled): `scripts/nightly_publish.sh` runs build_index → semantic.run → build_trends →
build_hf_data --upload → build_site --site deterrence, with a lock (`.nightly_publish.lock/`) and an 8 GB free-disk
floor (`MIN_FREE_GB`). Log: `logs/nightly_publish.log`.

## Dataset release

```
uv run python scripts/export_dataset.py --version 0.1.0 --snapshot-date 2026-10-02 [--countries RU CN] [--from …] [--to …]
```
Builds `release/<version>/` (gitignored; nothing is uploaded) from `docs/**/*.jsonl` (with the segmented store: all
local parts + active files, materialized per source; fetch the parts first with `store_sync.py pull --only docs-parts`): `official/<CC>.parquet` (full
text, all fields, zstd) + `official_jsonl/<CC>.jsonl.gz`, `media/<CC>.parquet` (state media/media/commentary as
metadata, word count and text SHA-256 only, no body text), optional `semantic/` (only once
`index/semantic` has per-doc tone scores), README (dataset card), CODEBOOK, CHANGELOG, MANIFEST.json, SHA256SUMS and
a draft ZENODO_METADATA.json (restricted access). `--snapshot-date` keeps rows whose `fetched` is at or before the end
of that local day (capped at build start), so a release is reproducible while collectors append. Rows failing
validation are dropped and counted in the manifest; an existing version is never overwritten without `--overwrite`;
the build refuses if the input exceeds `--max-gb` (2) or free disk would fall below `--min-free-gb` (8). Static
wording (sampling rules, gaps, field docs) lives in `scripts/release_notes.py`.

## Semantic layer

Tone, targets, topics, trends, alerts and cross-country echo over the whole index, all computed locally (no text
leaves the machine). Code: `scripts/semantic/` (package); outputs: `index/semantic/` (gitignored); data contract for
UIs: `scripts/semantic/SCHEMA.md`; design: `plan/semantic-layer.md`.

```
uv run python scripts/build_index.py                       # first: bring the index up to date
uv run python -m scripts.semantic.run                      # all stages, incremental (only new docs are processed)
uv run python -m scripts.semantic.run --stage trends,brief # selected stages
uv run python -m scripts.semantic.run --full               # re-tag, re-score everything, refit topics
uv run python -m scripts.semantic.run search "warnings that arms deliveries cross a red line" --country RU,BY --k 10
uv run python -m scripts.semantic.run --stage audit        # gazetteer audit  -> reports/semantic/target_audit.md
uv run python -m scripts.semantic.run --stage echo-tune    # echo threshold audit -> reports/semantic/echo_threshold_audit.md
uv run python -m scripts.semantic.run --stage export       # blind human-coding sample -> reports/semantic/validation_sample.csv
uv run python -m scripts.semantic.run --stage export --per-lang-min 30   # top up that sample to >= 30 sentences per language
uv run python -m scripts.semantic.run validate coderA.xlsx [coderB.xlsx] [--key KEY.csv] [--out DIR]
                                                            # human codes vs student/teacher (AUC, F1, kappa) + Krippendorff's alpha
uv run --with openpyxl --with python-docx python -m scripts.semantic.packet --blind SAMPLE.csv --out DIR   # coder workbook + codebook
```

Analyst UI (private, local): `uv run python scripts/serve_search.py` → http://127.0.0.1:8950/ (keyword + semantic
search) and /semantic (alerts, country tone, stance/salience heatmap, topics, echoes, coverage, method). Views are built
by `scripts/semantic_views.py` from the aggregates; `scripts/ui/trends*.{js,css}` are shared verbatim with the public
Trends page in tsm-strait-layers (tools/rhetoric-search/trends.html, data from its scripts/build_trends.py).

Stages (in order): `sync` (mirror doc metadata) → `targets` → `embed` → `teacher` → `train` → `score` → `topics` →
`trends` → `echo` → `brief`. Each is incremental and can be re-run alone; `--budget SECONDS` time-boxes `embed`/`score`
(newest documents first).

**Method.**
- *Embeddings*: `intfloat/multilingual-e5-small` (384-d, MPS fp16). Per document: the title plus the first 4 chunks of
  consecutive sentences (≤200 tokens each); URLs are stripped before embedding and link-only/number-only passages are
  flagged as junk (kept, but excluded from search, topics and echoes). Search uses the `query:` prefix, stored passages `passage:`.
- *Tone*: six dimensions — hostility/confrontation, threat & coercive signalling, conciliation/cooperation,
  grievance/victimhood, escalation framing, de-escalation framing (series also carry `esc_balance` = escalation − de-escalation).
  A zero-shot multilingual NLI teacher (`MoritzLaurer/bge-m3-zeroshot-v2.0`) labels a stratified sample (country ×
  language × source; half random, half target-mentioning sentences). One logistic regression per dimension on e5
  `query:` sentence embeddings (soft labels) then scores every sentence in scope: the first 80 sentences of each
  document plus any later sentence (up to 400) that mentions a target. Document tone = mean sentence probability.
  The teacher was chosen over mDeBERTa-v3-xnli after a small hand-labelled comparison (reports/semantic/hypothesis_selection.md).
- *Targets*: regex gazetteer in `scripts/semantic/targets.py` (US, NATO, EU, UK, Japan, ROK, Taiwan, Philippines, Ukraine,
  Israel, China, Russia, Iran, India, Pakistan, DPRK, Australia, and "the West"; EN/RU/BE/ZH/FA/AR/TR/ES/UR/KO variants and
  epithets, veto phrases, Chinese wire datelines blanked). Every pattern's hit count and samples: `reports/semantic/target_audit.md`;
  patterns dropped after audit are listed in `DROPPED`. Self-mentions are excluded from stance and salience.
- *Topics*: MiniBatchKMeans on language-centred document vectors (k chosen by silhouette from 40–80), fitted on a
  stream-balanced sample; labels = per-language c-TF-IDF terms + 3 exemplar titles.
- *Trends & alerts*: all series are computed within a stream (source × language × selection regime); keyword-filtered
  regimes (RU media `backfill`) are excluded. Weekly and monthly z-scores against each stream's own rolling baseline
  (12 weeks / 6 months, minimum volume 8 / 20 documents); country values combine streams with fixed weights so a change
  in source mix cannot look like a tone shift (unit-tested). Alerts need |z| ≥ 3 (3.5 for stance, salience, topic share)
  and a change ≥ 0.03; each lists up to 5 evidence sentences (≤300 characters, date, source, link) and a tier:
  strong (|z| ≥ 4.5), moderate (|z| ≥ 3.5 or ≥ 2 consecutive periods), weak. Across ~260k period tests the count beyond
  |z| = 3 is at chance level, beyond 4.5 it is ~40× chance (`alerts.json` → `meta.tail_check`): weak alerts are leads only.
- *Echo*: passages from different countries within ±14 days above a cosine threshold tuned by inspection
  (reports/semantic/echo_threshold_audit.md), grouped into clusters with first-seen country/date.
- *Brief*: `reports/briefs/brief-<date>.md` — BLUF, shifts by country with quotes, echoes, method/confidence, caveats,
  and an empty analyst-interpretation section.

**State on 2026-10-03.** 191,379 documents in 12 countries (BY CN CU IN IR PK RU SY TR TW US VE; no KP documents
yet): all embedded (~530k passages) and tone-scored (~2.8M sentences); 1.6M target mentions. Teacher sample 17,969
sentences (+3,500 active-learning rows). Held-out teacher–student agreement (2,708 sentences from unseen documents),
AUC / Pearson r: hostility 0.95/0.77, threat 0.97/0.72, conciliation 0.95/0.83, grievance 0.97/0.72,
escalation 0.95/0.63, de-escalation 0.94/0.69 (reports/semantic/tone_validation.md). Full first build ≈ 1.5 h on the
M3 (embedding ~15 min, scoring ~50 min); an incremental run with 5,000 new documents took ~5 min including
`build_index.py`. Disk: index/semantic ≈ 2.7 GB (models 1.5 GB, vectors 0.4 GB, SQLite 0.6 GB, aggregates ~50 MB) plus
~0.9 GB of ML packages in .venv.

**Limits (read before using numbers).**
- Validation so far is teacher–student agreement only (reports/semantic/tone_validation.md); nobody has yet checked the
  teacher against expert human coding. The coder packet (418 sentences, >= 30 per language, codebook, blind key) is in
  `reports/semantic/validation/` (private store; see its README); code it and run `validate` before citing levels.
- Grievance/victimhood is under-detected by the teacher (low recall on the hand-labelled check).
- Stance is "tone of sentences mentioning X", not tone directed at X.
- Languages with little or no teacher data (fa, ur, tr; es/ar/ko once those sources land) have unmeasured accuracy.
- Coverage gaps from SOURCES.md carry straight into trends (e.g. CN state media full text only Apr–Jun 2026); alerts
  are leads to check against the evidence. `alerts.json` reports how many alerts chance alone would produce.
- Echo "first seen" means first in this corpus, not origin; matches can be quotation or shared wire copy, not amplification.

## Tests

```
uv run --group dev pytest -q
```
