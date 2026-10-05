"""Generated documentation for the dataset release: README (dataset card), CODEBOOK, CHANGELOG, Zenodo metadata.

All functions take the build context assembled by scripts/export_dataset.py and return text/dicts; nothing
here touches the corpus. Static wording lives in release_notes.py.
"""
from __future__ import annotations

import html
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence

import release_notes as N
from release_data import MEDIA_SCHEMA, OFFICIAL_SCHEMA


def _cell(v: Any) -> str:
    return "" if v is None else str(v).replace("|", "\\|").replace("\n", " ")


def md_table(head: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    lines += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def html_table(head: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    th = "".join(f"<th>{html.escape(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(_cell(c))}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


def _fmt(n: int) -> str:
    return f"{n:,}"


def country_summary(ctx: Dict[str, Any]) -> List[List[Any]]:
    by: Dict[str, Counter] = defaultdict(Counter)
    span: Dict[str, List[str]] = {}
    for c in ctx["coverage"]:
        by[c["country"]][c.get("release", "full_text" if c["outlet"] == "official" else "metadata_only")] += c["n"]
        lo, hi = span.get(c["country"], [c["first"], c["last"]])
        span[c["country"]] = [min(lo, c["first"]), max(hi, c["last"])]
    rows = []
    for cc in sorted(by):
        rows.append([cc, _fmt(by[cc]["full_text"]), _fmt(by[cc]["metadata_only"]), span[cc][0], span[cc][1]])
    return rows


def coverage_rows(ctx: Dict[str, Any], official: bool) -> List[List[Any]]:
    rows = [c for c in ctx["coverage"]
            if c.get("release", "full_text" if c["outlet"] == "official" else "metadata_only")
            == ("full_text" if official else "metadata_only")]
    rows.sort(key=lambda c: (c["country"], c["outlet"], c["source"], c["lang"]))
    out = []
    for c in rows:
        base = [c["country"], c["source"], c["org"], c["lang"], c["first"], c["last"], _fmt(c["n"])]
        out.append(base if official else base[:2] + [c["outlet"]] + base[2:])
    return out


def citation(ctx: Dict[str, Any]) -> str:
    year = ctx["build_time"][:4]
    doi = ctx.get("doi")
    link = f"https://doi.org/{doi}" if doi else "https://doi.org/10.5281/zenodo.XXXXXXX (DOI to be assigned)"
    return f"Walberg, Jonathan. {year}. *{N.TITLE}*, v{ctx['version']} [Data set]. Zenodo. {link}. ORCID {N.ORCID}."


def _semantic_text(ctx: Dict[str, Any]) -> str:
    sem = ctx["semantic"]
    if sem.get("included"):
        return (f"`semantic/doc_scores.parquet` holds model scores for {_fmt(sem['rows'])} documents, keyed by `id`: "
                "tone (hostility, threat, conciliation, grievance, escalation, deescalation; probabilities 0-1), "
                "topic cluster and label, and the non-self targets mentioned. Tone comes from student classifiers "
                "trained on labels from a zero-shot NLI teacher model; teacher-student agreement is reported in "
                "`semantic/semantic_validation.json`.")
    return f"Not included in this version: {sem.get('reason', 'semantic layer not available')}."


def render_readme(ctx: Dict[str, Any]) -> str:
    t = ctx["totals"]
    present = sorted({c["source"] for c in ctx["coverage"]})
    notes = [[s, *N.SOURCE_NOTES[s]] for s in present if s in N.SOURCE_NOTES]
    missing = [s for s in present if s not in N.SOURCE_NOTES]
    parts = [
        f"# {N.TITLE} — v{ctx['version']}",
        f"Snapshot of the corpus as collected up to **{ctx['snapshot']['effective']}** (UTC; requested cut-off "
        f"{ctx['snapshot']['requested']}). Built {ctx['build_time']} from corpus commit `{ctx['git']['commit'][:12]}`. "
        f"{_fmt(t['official'])} official documents with full text and {_fmt(t['media'])} media items as metadata only, "
        f"{t['countries']} countries, {t['sources']} sources.",
        "## Purpose", N.PURPOSE,
        "## Contents",
        "```\n" + "\n".join(f"{f['path']:<40} {f['rows']:>9} rows  {f['bytes'] / 1e6:8.1f} MB"
                            for f in ctx["data_files"]) +
        "\ncoverage.csv  SOURCES.md  README.md  CODEBOOK.md  CHANGELOG.md  MANIFEST.json  SHA256SUMS  ZENODO_METADATA.json\n```",
        "- `official/<CC>.parquet` — every `outlet = official` document with full text and all fields (Parquet, zstd).\n"
        "- `official_jsonl/<CC>.jsonl.gz` — the same rows as gzip JSON Lines for systems without Parquet tools.\n"
        "- `media/<CC>.parquet` — state-media, media and commentary items: metadata, word count and SHA-256 of the "
        "text only. **No body text** (copyright). It also holds, as metadata only, the official-outlet sources "
        "that publish journalism (" + ", ".join(f"`{k}`" for k in N.METADATA_ONLY_SOURCES) + ") and forwarded "
        "posts in official Telegram channels; their `outlet` stays `official`.\n"
        "- `semantic/` — optional model scores (see below).\n"
        "- `coverage.csv` — one row per country × outlet × source × language: first and last date, documents.\n"
        "- `CODEBOOK.md` documents every field. `MANIFEST.json` lists every file with size, rows and SHA-256, the "
        "input files read, and rows dropped by validation.",
        "## Coverage by country", md_table(["Country", "Full-text docs", "Metadata-only items", "First", "Last"],
                                           country_summary(ctx)),
        "## Coverage: official documents (full text)",
        md_table(["Country", "Source", "Org", "Lang", "First", "Last", "Docs"], coverage_rows(ctx, True)),
        "## Coverage: media (metadata only)",
        md_table(["Country", "Source", "Outlet", "Org", "Lang", "First", "Last", "Items"], coverage_rows(ctx, False)),
        "## Collection method", "\n".join(f"- {x}" for x in N.COLLECTION_METHOD),
        "## Sampling rules per source",
        md_table(["Source", "Collection", "Sampling"], notes)
        + (f"\n\nNo notes yet for: {', '.join(missing)} (see SOURCES.md, the per-source collection log shipped with the release)." if missing else ""),
        "## Known gaps and blockers", "\n".join(f"- {x}" for x in N.KNOWN_GAPS),
        "## Analytic caveats", "\n".join(f"- {x}" for x in N.CAVEATS),
        "## Semantic scores", _semantic_text(ctx),
        "## How to load",
        "Python (pandas + pyarrow):\n```python\nimport pandas as pd\n"
        "official = pd.read_parquet(\"official/\")            # all countries\n"
        "ru = pd.read_parquet(\"official/RU.parquet\")\n"
        "media = pd.read_parquet(\"media/\")\n"
        "mfa = official[(official.source == \"mid_ru\") & (official.date >= pd.Timestamp(\"2025-01-01\").date())]\n```",
        "DuckDB (no Python needed):\n```sql\nSELECT country, source, count(*) AS n, min(date), max(date)\n"
        "FROM read_parquet('official/*.parquet') GROUP BY ALL ORDER BY ALL;\n\n"
        "SELECT date, source, title, url FROM read_parquet('official/*.parquet')\n"
        "WHERE country = 'CN' AND text ILIKE '%Taiwan%' ORDER BY date DESC LIMIT 20;\n```",
        "R (arrow):\n```r\nlibrary(arrow); library(dplyr)\n"
        "official <- open_dataset(\"official\") |> filter(country == \"IR\") |> collect()\n"
        "media <- read_parquet(\"media/RU.parquet\")\n```",
        "JSON Lines (any language):\n```python\nimport gzip, json\n"
        "with gzip.open(\"official_jsonl/RU.jsonl.gz\", \"rt\", encoding=\"utf-8\") as f:\n"
        "    docs = [json.loads(line) for line in f]\n```",
        "Verify the download: `sha256sum -c SHA256SUMS` (macOS: `shasum -a 256 -c SHA256SUMS`).",
        "## Citation", citation(ctx),
        "```bibtex\n@dataset{walberg_state_rhetoric_" + ctx["build_time"][:4] + ",\n"
        "  author    = {Walberg, Jonathan},\n"
        f"  title     = {{{N.TITLE}}},\n  version   = {{{ctx['version']}}},\n"
        f"  year      = {{{ctx['build_time'][:4]}}},\n  publisher = {{Zenodo}},\n"
        "  doi       = {" + (ctx.get("doi") or "10.5281/zenodo.XXXXXXX") + "},\n  note      = {ORCID " + N.ORCID
        + "}\n}\n```",
        "## License",
        "- Curation, metadata, derived fields and documentation: **CC BY 4.0**.\n"
        "- Underlying official texts belong to their issuing governments; they are redistributed here for research "
        "and analysis, with source URLs, and remain subject to the issuers' terms.\n"
        "- Media texts (state media, private media, commentary) are **not redistributed**: the release carries "
        "metadata, word counts and text hashes only. The same applies to official-outlet sources that publish "
        "journalism: " + "; ".join(f"`{k}` ({v})" for k, v in N.METADATA_ONLY_SOURCES.items()) + " "
        + N.FORWARDED_NOTE + "\n"
        "- Access on Zenodo is open.",
        "## Contact", f"{N.AUTHOR} ({N.AFFILIATION}), ORCID {N.ORCID}.",
    ]
    return "\n\n".join(parts) + "\n"


def render_codebook(ctx: Dict[str, Any]) -> str:
    present = {f.name for f in OFFICIAL_SCHEMA} | {f.name for f in MEDIA_SCHEMA}
    fields = [[f"`{n}`", t, a, m] for n, t, a, m in N.FIELDS if n in present]
    off_cols = ", ".join(f"`{f.name}`" for f in OFFICIAL_SCHEMA)
    med_cols = ", ".join(f"`{f.name}`" for f in MEDIA_SCHEMA)
    values = []
    for field, counts in sorted(ctx["values"].items()):
        top = ", ".join(f"{k} ({_fmt(v)})" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:25])
        values.append([f"`{field}`", top])
    speakers = [[s, ", ".join(f"{k} ({_fmt(v)})" for k, v in c.most_common(8))]
                for s, c in sorted(ctx["speakers"].items()) if c]
    parts = [
        f"# Codebook — {N.TITLE} v{ctx['version']}",
        "Types are Parquet/Arrow types; the JSONL mirror uses the same names (dates as ISO strings, `fetched` as "
        "ISO UTC, `extra` as a nested object). Missing values are null.",
        "## Files and columns",
        f"- `official/<CC>.parquet` and `official_jsonl/<CC>.jsonl.gz`: {off_cols}.\n"
        f"- `media/<CC>.parquet`: {med_cols}.",
        "## Fields", md_table(["Field", "Type", "Allowed values / format", "Meaning and provenance"], fields),
        "## Values observed in this release (counts over official + media rows)",
        md_table(["Field", "Values (count)"], values),
        "## Provenance fields",
        "- `via`: direct = fetched live from the issuing site; wayback = text from an Internet Archive capture "
        "(`wayback_timestamp`); rss = feed item; export = imported from an earlier project export (`input`); "
        "cache = re-parsed from the collector's own cached copy of a live page.\n"
        "- `sample`: selection regime. section = whole site section taken in full; section_random / random = "
        "seed-fixed uniform random subset (smallest sha256 of a salted URL); recent = dense recent window; rss = "
        "every feed item; sitemap / cdx = listed in the site sitemap / Wayback URL index; all = every item; "
        "backfill / seed = keyword-selected (topic-biased; exclude from topic shares). null = every reachable item.\n"
        "- `translation`: original = source-language text; official = translation published by the government; "
        "tsm = project translation; mt:google / mt:claude = machine translation.\n"
        "- `text_scope` and `truncated`: whether `text` is the full item, a lead, or the headline only.",
        "## Country and source notes", "\n".join(f"- {x}" for x in N.COUNTRY_NOTES),
        "## Speakers by source (top values)", md_table(["Source", "Speaker (count)"], speakers),
        "## Validation rules",
        "A row is dropped (and counted in MANIFEST.json `validation.dropped`) when: it is not a JSON object; "
        "any of id, country, source, lang, date, url, text is missing or empty; the id is not prefixed by "
        "`<source>:`; source or country disagree with the file path; outlet is not one of official / state_media "
        "/ media / commentary; date is not a valid ISO calendar date between 1990 and 2100; lang is not a 2-3 "
        "letter code; url is not http(s); org/title/speaker/kind/via are not strings; `fetched` is malformed; or "
        "the publication date is more than one day after the snapshot cut-off. "
        "Duplicate ids keep the first row read. Rows fetched after the snapshot cut-off are excluded.",
    ]
    if ctx["semantic"].get("included"):
        parts += ["## semantic/doc_scores.parquet",
                  "`id` joins to the data files. Tone dimensions are probabilities 0-1 averaged over scored "
                  "sentences; `topic` ids are specific to `topic_v`; `targets` = entities mentioned other than the "
                  "speaker's own country."]
    return "\n\n".join(parts) + "\n"


def render_changelog(ctx: Dict[str, Any], previous: Optional[Dict[str, Any]]) -> str:
    t = ctx["totals"]
    lines = [f"# Changelog — {N.TITLE}", "",
             f"## {ctx['version']} — {ctx['build_date_local']}", "",
             f"- Snapshot cut-off {ctx['snapshot']['effective']} (UTC).",
             f"- {_fmt(t['official'])} official documents (full text), {_fmt(t['media'])} media items (metadata), "
             f"{t['countries']} countries, {t['sources']} sources.",
             f"- {_fmt(ctx['dropped_total'])} rows dropped by validation (see MANIFEST.json).",
             f"- Semantic scores: {'included' if ctx['semantic'].get('included') else 'not included'}."]
    if previous is None:
        lines.append("- First release.")
    else:
        lines.append(f"- Changes since {previous['version']}:")
        old = previous.get("rows_by_country_outlet", {})
        for key in sorted(set(old) | set(ctx["rows_by_country_outlet"])):
            a, b = old.get(key, 0), ctx["rows_by_country_outlet"].get(key, 0)
            if a != b:
                lines.append(f"  - {key}: {_fmt(a)} → {_fmt(b)}")
    return "\n".join(lines) + "\n"


def zenodo_metadata(ctx: Dict[str, Any]) -> Dict[str, Any]:
    countries = sorted({c["country"] for c in ctx["coverage"]})
    desc = "".join([
        f"<p>{html.escape(N.PURPOSE)}</p>",
        f"<p>Version {ctx['version']}: snapshot up to {ctx['snapshot']['effective']} (UTC). "
        f"{_fmt(ctx['totals']['official'])} official documents with full text (Parquet and gzip JSON Lines) and "
        f"{_fmt(ctx['totals']['media'])} state-media and media items as metadata only (no body text).</p>",
        html_table(["Country", "Full-text docs", "Metadata-only items", "First", "Last"], country_summary(ctx)),
        "<p>Collection respected robots.txt and rate limits and did not circumvent blocks; archived copies come "
        "from the Internet Archive Wayback Machine and are flagged per document. Coverage is uneven: counts "
        "reflect collection, not total output. See README.md and CODEBOOK.md in the files.</p>",
        ("<p>Per-document tone scores (semantic/doc_scores.parquet) come from student classifiers trained on "
         "labels from a zero-shot NLI teacher model; teacher-student agreement is in semantic_validation.json.</p>"
         if ctx["semantic"].get("included") else ""),
        "<p>License: curation and metadata CC BY 4.0. Official texts belong to their issuing governments and are "
        "redistributed for research. Media texts are not redistributed.</p>",
    ])
    return {"metadata": {
        "upload_type": "dataset",
        "title": f"{N.TITLE} (v{ctx['version']})",
        "version": ctx["version"],
        "publication_date": ctx["build_date_local"],
        "creators": [{"name": N.AUTHOR, "affiliation": N.AFFILIATION, "orcid": N.ORCID}],
        "description": desc,
        "access_right": "open",
        "license": "cc-by-4.0",
        "keywords": ["political rhetoric", "official statements", "state media", "foreign ministry",
                     "multilingual corpus", "text as data", "international relations", "public diplomacy",
                     "propaganda", "strategic communication"] + [N.COUNTRY_NAMES.get(c, c) for c in countries],
        "notes": ("Draft metadata generated by scripts/export_dataset.py; not yet submitted. "
                  + (f"Reserved DOI {ctx['doi']}. " if ctx.get("doi") else
                     "DOI placeholder in README to be replaced after reservation. ")
                  + "Corpus commit " + ctx["git"]["commit"][:12] + "."),
        "prereserve_doi": True,
        "related_identifiers": [{"identifier": N.REPO_URL, "relation": "isSupplementedBy",
                                 "resource_type": "software"}],
    }}
