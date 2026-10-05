"""Static text for the dataset release: per-source collection/sampling notes, known gaps, field codebook.

Condensed from SOURCES.md and the collector docstrings (2026-10-02). Edit here, not in the generated files.
Sources present in the data but missing from SOURCE_NOTES are listed as "no notes" in the README.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

TITLE = "Multilingual State Rhetoric Corpus"
AUTHOR = "Walberg, Jonathan"
ORCID = "0009-0000-2065-8481"
AFFILIATION = "University of Virginia"
# Public code repository (collectors, export script); linked from the Zenodo record as supplementary software.
REPO_URL = "https://github.com/jonawalb/rhetoric-corpus"

PURPOSE = (
    "A multilingual corpus of official government statements (foreign ministries, presidencies, defence "
    "ministries, security councils, spokespeople) from Russia, China, Iran, Belarus, Pakistan, India, Türkiye, "
    "Taiwan, Syria, Venezuela, Cuba and the United States, with document-level metadata for a large sample of "
    "state-media and media articles. It is built for comparative research on state rhetoric: tone, threat "
    "signalling, framing of adversaries, and how official messaging moves over time and across languages. "
    "Files are plain Parquet and gzip JSONL so they can be loaded into an offline or air-gapped system "
    "without any outside web service."
)

COLLECTION_METHOD = [
    "Documents not imported from earlier exports were fetched by purpose-built collectors with a descriptive "
    "user agent. Collectors read robots.txt before any request and do not fetch disallowed paths; since the "
    "2026-10-02 library fix an unreadable robots.txt counts as disallow (hosts read before the fix were "
    "re-checked by hand, see SOURCES.md).",
    "Rate limits: at least 4 s between requests to one host (10-15 s for slower government hosts, 5-8 s for the "
    "Wayback Machine), with retries and back-off. Collection stops at HTTP 401/403/404; bot walls, Cloudflare/"
    "ArvanCloud challenges, paywalls and logins were never circumvented. Those sources are listed under "
    "Known gaps.",
    "When a live site was unreachable or blocked, the collector used the latest HTTP-200 capture in the "
    "Internet Archive Wayback Machine (raw `id_` copy). Such rows have `via = wayback`; where the collector "
    "recorded the capture, its timestamp is in `wayback_timestamp` (not recorded for telegram_ru, state_dept "
    "and the imported iran_mfa_en Wayback rows; their `url` is the original address).",
    "Some sources were imported from earlier project exports (`via = export`): PRC MFA/MND/TAO transcripts "
    "and PRC state media. The `input` field names the export file.",
    "One language per document. Bilingual sources (e.g. Kremlin EN/RU, Taiwan MOFA EN/ZH) are separate "
    "sources and are not merged; translations are flagged in `translation`.",
    "No keyword filter is used for collection unless stated per source below. Old keyword-filtered rows are "
    "flagged (`sample = backfill` or `seed`) and should be excluded from topic-share estimates.",
]

# source -> (collection, sampling rule). Keep each to one or two sentences.
SOURCE_NOTES: Dict[str, Tuple[str, str]] = {
    "kremlin_en": ("en.kremlin.ru transcripts; ~half read from Wayback copies.",
                   "All transcripts 2021→; text = Putin's own words only (`note` says how the text was cut); 56 transcripts with no Putin text skipped."),
    "kremlin_ru": ("kremlin.ru transcripts by id; Wayback fallback on 403.",
                   "All transcripts 2021→; `text` = full transcript with speaker labels, `putin_text` = Putin's paragraphs; `pair_id` links to kremlin_en."),
    "mid_ru": ("mid.ru press service (briefings, answers, comments, statements, Lavrov and deputy-minister speeches), fetched live.",
               "All items reachable: robots.txt forbids listing pagination, so older URLs come from the Wayback CDX index and in-page links (coverage of older items is partial)."),
    "mid_en": ("mid.ru/en, same sections as mid_ru (no deputy-minister section).", "As mid_ru."),
    "scrf_ru": ("Security Council of Russia news by sequential id.", "All items 2021→."),
    "duma_ru": ("State Duma news by sequential id (duma.gov.ru/news/<id>/).", "All items 2021→; speaker = Volodin when the title names him."),
    "telegram_ru": ("Zakharova and Medvedev Telegram channels, Wayback copies of t.me/s/ preview pages only.",
                    "One row per post on each archived page; gaps between captures."),
    "mil_ru": ("Russian MoD news, Wayback only (function.mil.ru).",
               "Every captured item 2021→ (`sample = all`); 95 older rows without `sample` came from a nuclear keyword prefilter. New mil.ru site (2025→) effectively not covered."),
    "ria_ru": ("ria.ru monthly sitemaps + RSS.",
               "SAMPLE: last 90 days dense (`recent`, politics/security slugs); all dates 2021→ = seed-fixed simple random sample of 1,500 articles per month (`random`); RSS items (`rss`). `tags` = RIA's own rubric list."),
    "tass_com": ("tass.com news sitemaps + RSS.",
                 "SAMPLE: politics/world/russia/defense (+ related) sections in full (`section`); economy/society 100 random per section per month (`section_random`); last 90 days `recent`."),
    "tass_ru": ("tass.ru RSS only (articles and robots.txt return 403).", "Headline + lead only (`text_scope = rss_lead`)."),
    "rt_com": ("rt.com year sitemaps, news sitemap, RSS.",
               "SAMPLE: news/russia/usa/uk/africa/india/op-ed/business sections in full (`section`); sport/pop-culture etc. left out; 2,965 `backfill` rows from the old nuclear filter."),
    "rt_ru": ("russian.rt.com year sitemaps + RSS.",
              "SAMPLE: russia/world/ussr/business, 15,000 random per sitemap year (`section_random`); 2,611 `backfill` rows from the old nuclear filter."),
    "iran_mfa_en": ("en.mfa.ir news (imported; 1,698 live + 405 Wayback).", "All readable items 2021→."),
    "ir_president_fa": ("president.ir/fa by id.", "All items 2021→ except galleries/videos; Solar Hijri dates converted."),
    "ir_president_en": ("president.ir/en by id (when the FA id is 404).", "All items 2021→ except galleries/videos."),
    "ir_khamenei_en": ("english.khamenei.ir via Wayback only (live site 403).", "All captured items 2021→."),
    "ir_presstv": ("Press TV monthly sitemaps, RSS, Wayback CDX (2023-12→).",
                   "SAMPLE: Iran/politics/nuclear/defence/regional sections; economy, culture, sport, video dropped."),
    "mfa_cn": ("PRC MFA press conferences and statements (imported TSM exports).",
               "ALL rows, not only Taiwan, to 2026-06; Jul–Sep 2026 import holds Taiwan Q&A only (see mfa_cn_live). EN and ZH of one Q&A are separate docs."),
    "mfa_cn_live": ("fmprc.gov.cn / mfa.gov.cn regular press conference transcripts, live.",
                    "All conferences from 2026-06/07 (fills the import gap); one doc per conference per language."),
    "mnd_cn": ("PRC Ministry of National Defense statements and press conferences (imported).", "All rows; archive rows carry no item URL."),
    "tao_cn": ("PRC Taiwan Affairs Office press conferences (imported).", "All rows 2026-04→; English is machine or TSM translation."),
    "prc_statemedia": ("Xinhua, People's Daily, CGTN, CCTV, China Daily, Global Times, PLA Daily, Qiushi and others (imported export; org = outlet).",
                       "Full-text articles 2026-04 → 2026-06 only."),
    "prc_statemedia_headlines": ("Same outlets, headline + link only (imported).", "2025-01 → 2026-06; `kind = headline`."),
    "pk_mofa": ("mofa.gov.pk press releases, briefings, statements, speeches.", "All items 2021→."),
    "pk_ispr": ("ISPR press releases via Wayback only (live site Cloudflare).", "All captured ids; no captures after mid-2024; some Urdu."),
    "in_mea": ("mea.gov.in media briefings + speeches/statements via the site's own listing endpoints.", "All items 2021→ (English only)."),
    "by_president_en": ("president.gov.by events via sitemap.", "All events 2021→; mostly greetings/meetings, low rhetoric density."),
    "by_president_ru": ("president.gov.by/ru events via sitemap.", "All events 2021→."),
    "by_mfa_ru": ("mfa.gov.by news via the site's public date-search form.", "All items 2021→; days with 10 results may be incomplete."),
    "by_mfa_en": ("mfa.gov.by/en news, same method.", "As by_mfa_ru."),
    "belta_en": ("BelTA English; URLs from Wayback CDX + in-page links, articles fetched live.", "SAMPLE: president + politics rubrics, 2021→."),
    "tr_mfa_en": ("mfa.gov.tr press releases, statements, Q&A, speeches (EN).", "All listed items 2021→."),
    "tr_mfa_tr": ("mfa.gov.tr (TR) equivalents.", "All listed items 2021→."),
    "tr_tccb_en": ("tccb.gov.tr (EN) speeches, interviews, articles, spokesperson, news.", "All listed items 2021→."),
    "tr_tccb_tr": ("tccb.gov.tr (TR) incl. full Erdoğan speech transcripts.", "All listed items 2021→."),
    "tr_aa_en": ("Anadolu Agency EN: news sitemap + Wayback CDX URL index, fetched live.",
                 "SAMPLE: politics/world/regional (recent) and politics+türkiye backfill limited to Wayback captures."),
    "tr_aa_tr": ("Anadolu Agency TR, same method.", "SAMPLE: politika section; backfill limited to Wayback captures."),
    "tw_mofa_en": ("en.mofa.gov.tw press releases + statements/responses.", "All listed items 2021→."),
    "tw_mofa_zh": ("mofa.gov.tw (ZH) press releases + statements/responses.", "All listed items 2021→; ROC dates converted."),
    "tw_ey_en": ("Executive Yuan (EN) news.", "All listed items 2021→ (videos, ministry news, cabinet items excluded)."),
    "tw_ey_zh": ("Executive Yuan (ZH) news.", "As tw_ey_en; ROC dates converted."),
    "tw_mac_zh": ("Mainland Affairs Council via Wayback only (live site Cloudflare).", "Captured press releases 2021→; ROC dates converted."),
    "tw_president_en": ("english.president.gov.tw news sitemap.", "All items 2021→."),
    "tw_cna": ("Central News Agency (ZH) politics + cross-strait sections.", "SAMPLE: aipl + acn sections; backfill = Wayback captures."),
    "tw_focustaiwan": ("Focus Taiwan (CNA English) politics + cross-strait.", "SAMPLE: two sections; older items truncated to the lead (`text_scope = lead_archive_paywall`)."),
    "tw_taipeitimes": ("Taipei Times (private newspaper).", "SAMPLE: front page + editorials in full; Taiwan News section only on a keyword match (TT_KEYWORD)."),
    "state_dept": ("2021-2025.state.gov briefings (direct) + current state.gov briefings/spokesperson releases via Wayback (www.state.gov 403).",
                   "All briefings; spokesperson statements 2025-04→ as captured."),
    "whitehouse": ("whitehouse.gov post sitemaps (2025-01-20→) + bidenwhitehouse.archives.gov briefing room.", "All statements, releases, fact sheets, remarks; Biden-era briefing transcripts."),
    "mofaex_ar": ("Syrian MFA (mofaex.gov.sy) news listing, Arabic.", "All listed items; site holds transitional-government era only (`period`)."),
    "sana_en": ("SANA English politics + presidency (new site) and archive.sana.sy categories.", "Sections politics / Syria and the World; `period` splits Assad / transitional."),
    "mppre_es": ("Venezuela MFA publications by sequential id, Spanish.", "All items; `section` = comunicado / discurso / noticia."),
    "minrex_en": ("Cuban MFA English pages via Wayback only (DNS failure live).", "All captured pages 2021→."),
    "granma_en": ("Granma International (party daily, EN) archive listings.", "Sections Cuba, World, Díaz-Canel speeches."),
    "kp_rodong_en": ("Rodong Sinmun English via Wayback only.", "All captured articles 2021→; coverage uneven."),
}

KNOWN_GAPS: List[str] = [
    "Collection was still running at the snapshot: many sources are partial (newest-first walkers have not "
    "reached 2021). Compare `first`/`last` dates in the coverage table before treating a series as complete.",
    "North Korea: KCNA and Wayback copies of kcna.kp are blocked by the local network filter; Naenara, VOK and "
    "Uriminzokkiri do not answer. Rodong Sinmun English (Wayback) is the only DPRK source and may be empty in "
    "this version.",
    "US: www.state.gov answers 403 to the collector (bot wall), so post-January 2025 State Department material "
    "comes from Wayback captures only; 2021-2025 press statements under opaque URLs were not collected. White "
    "House press-secretary briefings after January 2025 are video only and not covered.",
    "China: full-text state media covers 2026-04 → 2026-06 only (headlines 2025-01 → 2026-06). The imported "
    "MFA rows for Jul–Sep 2026 are Taiwan Q&A only; `mfa_cn_live` fills the conference transcripts. MND archive "
    "rows have no item URL.",
    "Russia: mid.ru forbids listing pagination in robots.txt, so older MFA items depend on the Wayback URL "
    "index; mil.ru is Wayback-only and the new 2025 site is not covered; tass.ru is RSS leads only (403); "
    "RT year sitemaps stopped updating in June 2026 (gap mid-June → late September 2026).",
    "Iran: live mfa.ir is behind an ArvanCloud challenge (only the imported English set is present); IRNA and "
    "Tasnim are blocked or unreachable; khamenei.ir is Wayback-only (English; Persian not started); leader.ir "
    "not collected (ambiguous robots response).",
    "Pakistan: ISPR is Wayback-only, no captures after mid-2024. India: Hindi MEA pages not collected.",
    "Taiwan: Presidential Office ZH is blocked by a CDN cookie loop; MND robots.txt disallows all; MAC is "
    "Wayback-only; Focus Taiwan archive items are truncated to the lead behind a subscription notice.",
    "Türkiye: iletisim.gov.tr not collected (declares ai-input=no). Belarus: MFA 'Statements' section has no "
    "dates and is not collected; Belarusian-language pages not collected.",
    "Syria: the MFA site holds the transitional-government era only. Israel (gov.il, 403) is not covered.",
    "State-media samples are section or random samples (see sampling rules), not complete output.",
]

CAVEATS: List[str] = [
    "Coverage is uneven across countries, sources, languages and years. Document counts reflect what was "
    "collected, not how much each government or outlet actually published. Do not compare raw counts across "
    "sources or over time without normalising by the source's own coverage.",
    "Sampling regimes differ (`sample`). Rows with `sample` in {backfill, seed} were selected with a nuclear/"
    "strategic keyword filter and over-represent that topic.",
    "Wayback-based sources reflect what the Internet Archive happened to capture.",
    "Some English texts are translations (`translation`): official, TSM project translation, or machine "
    "translation (`mt:google`, `mt:claude`). Use original-language rows for wording-sensitive analysis.",
    "Speaker attribution is rule-based (title patterns, office holder by date) and incomplete; null does not "
    "mean the text has no identifiable speaker.",
]

# field -> (type, allowed values / format, meaning and provenance). Order = codebook order.
FIELDS: List[Tuple[str, str, str, str]] = [
    ("id", "string", "`<source>:<key>`", "Stable document id; unique in the release. Key = native id or first 16 hex of sha1(url)."),
    ("country", "string", "ISO-like code (RU, CN, IR, KP, BY, US, PK, IN, TR, TW, SY, VE, CU)", "Country whose government or media produced the text."),
    ("source", "string", "see coverage table", "Collector source name; one source = one site/language (and one sampling rule)."),
    ("outlet", "string", "official, state_media, media, commentary", "official = government body; state_media = state-owned/-run media; media = private press; commentary = opinion sites."),
    ("org", "string", "free text", "Issuing organisation or outlet (e.g. Kremlin, MFA, Xinhua)."),
    ("lang", "string", "ISO 639-1 (en, ru, zh, fa, ar, tr, es, ur, ko, ...)", "Language of `text`. One language per document."),
    ("date", "date (Parquet date32; ISO YYYY-MM-DD in JSONL)", "valid calendar date", "Publication date as given by the source, converted to the Gregorian calendar (Solar Hijri for Persian Iranian pages, ROC/Minguo years for Taiwan ZH pages)."),
    ("url", "string", "http(s) URL", "Canonical URL of the item on the issuing site (for Wayback rows: the original URL, not the archive URL)."),
    ("title", "string | null", "", "Headline or title."),
    ("speaker", "string | null", "", "Principal speaker when rule-based attribution found one (see per-source notes)."),
    ("kind", "string", "transcript, briefing, statement, article, qa, interview, speech, headline", "Document type."),
    ("via", "string", "direct, wayback, rss, export, cache", "How the text was obtained: live fetch, Wayback capture, RSS feed, imported export, or local HTML cache of an earlier live fetch."),
    ("fetched", "timestamp (UTC)", "", "When the collector stored the row (import time for `export` rows). The release snapshot cut-off applies to this field."),
    ("text", "string", "official set only", "Full text (paragraphs separated by newlines). Not included for media."),
    ("n_chars", "int32", ">= 1", "Length of the full text in characters (computed for media too, from the local copy)."),
    ("n_words", "int32", ">= 0", "Word count: runs of letters/digits, with each CJK character counted as one word."),
    ("text_sha256", "string", "64 hex", "SHA-256 of the UTF-8 text as stored; lets holders of a copy verify or join texts without redistribution."),
    ("text_scope", "string", "full, headline, rss_lead, lead_<reason>", "How much of the item the stored text covers. Derived: collector `text_scope`, `truncated`, or headline (text equals title / kind headline)."),
    ("text_available_locally", "bool", "media set only", "True when the private corpus holds body text for the item (any scope except headline). The text itself is not in the release."),
    ("wayback", "string | null", "14-digit stamp or archive URL", "Wayback capture used, as the collector recorded it."),
    ("wayback_timestamp", "string | null", "YYYYMMDDhhmmss", "Normalised capture timestamp, from `wayback` or `wayback_ts`. Use with `url` to rebuild the archive link: https://web.archive.org/web/<stamp>/<url>."),
    ("sample", "string | null", "all, section, section_random, random, recent, rss, sitemap, cdx, backfill, seed", "Selection regime (see sampling rules). null = every item the collector could reach. backfill and seed are keyword-selected."),
    ("section", "string | null", "", "Site section or rubric the item was listed under."),
    ("tags", "list<string> | null", "", "The page's own tag/rubric list (RIA, Belarus presidency)."),
    ("translation", "string | null", "original, official, tsm, mt:google, mt:claude", "Translation status of `text` (PRC imported sources)."),
    ("period", "string | null", "assad, transitional", "Syria only: before or from 2024-12-08 (fall of the Assad government)."),
    ("category", "string | null", "", "Source category (PRC state-media URL channel, Taiwan MOFA press_release/statement_response, Iran presidency category)."),
    ("url_kind", "string | null", "item, day, index", "PRC imported rows: whether `url` points to the item, the day's transcript, or an index page."),
    ("alt_url", "string | null", "", "Alternative URL (other-language transcript of the same PRC MFA day)."),
    ("asker", "string | null", "", "Questioner's outlet in press-conference Q&A rows."),
    ("putin_text", "string | null", "", "kremlin_ru: Putin's paragraphs only (same rule as kremlin_en)."),
    ("labelled", "bool | null", "", "kremlin_ru: transcript has speaker labels."),
    ("pair_id", "string | null", "", "kremlin_ru: id of the matching kremlin_en document."),
    ("note", "string | null", "", "kremlin_en: how the Putin-only text was cut."),
    ("channel", "string | null", "", "telegram_ru: channel name."),
    ("president", "string | null", "", "Iran presidency: president in office on `date`."),
    ("site", "string | null", "sana.sy, archive.sana.sy", "SANA: which site generation the item came from."),
    ("pr_no", "string | null", "", "ISPR press-release number."),
    ("dateline", "string | null", "", "ISPR dateline as printed."),
    ("unit", "string | null", "conference", "mfa_cn_live: one document per press conference."),
    ("input", "string | null", "", "Imported rows: export file the row came from."),
    ("wayback_ts", "string | null", "", "mil_ru: raw capture timestamp (also in wayback_timestamp)."),
    ("truncated", "string | null", "archive_paywall", "Text cut by the site (Focus Taiwan archive)."),
    ("extra", "string (JSON object) | null", "", "Any other collector field not listed above, as a JSON object (Parquet) or nested object (JSONL)."),
]

COUNTRY_NOTES: List[str] = [
    "IR — Persian presidency pages carry Solar Hijri dates (e.g. '10 مهر 1405'); they are converted to Gregorian by the collector (ir_common, tested). English pages are Gregorian.",
    "IR — `ir_khamenei_en`: the collector reports that site item news/12103 (2026-03-01) announces Ali Khamenei's death on 2026-02-28, so `speaker = Khamenei` is set only up to that date and null after. This is AGENT-REPORTED from the archived page and NOT independently verified; check before relying on it.",
    "TW — Chinese-language government pages use ROC (Minguo) years (year + 1911); converted by tw_common.roc_date. The Presidential Office's JSON-LD datePublished is a bogus template value and is not used.",
    "SY — `period` = assad (date < 2024-12-08) or transitional; the corpus straddles a change of government, so split analyses by period.",
    "CU — MINREX dates are the Drupal node creation time in UTC, so a late-evening Havana posting can carry the next day's date.",
    "CN — imported English MFA/MND/TAO rows are often translations (`translation`); EN and ZH versions of one Q&A are separate documents (`:en` / `:zh` id suffix).",
    "RU — `kremlin_en.text` holds only Putin's words; `kremlin_ru.text` holds the full transcript and `putin_text` the Putin-only part.",
    "IN — MEA listing date is the posting date and can be a day after the briefing.",
]

COUNTRY_NAMES: Dict[str, str] = {
    "RU": "Russia", "CN": "China", "IR": "Iran", "KP": "North Korea", "BY": "Belarus", "US": "United States",
    "PK": "Pakistan", "IN": "India", "TR": "Türkiye", "TW": "Taiwan", "SY": "Syria", "VE": "Venezuela",
    "CU": "Cuba", "IL": "Israel",
}
