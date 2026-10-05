"""Tests for the dataset release export (scripts/export_dataset.py and its release_* modules)."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import export_dataset as ED  # noqa: E402
import release_data as RD  # noqa: E402

F_OLD = "2026-10-01T12:00:00+00:00"
F_LATE = "2030-01-01T00:00:00+00:00"


def doc(source, n, country="RU", outlet="official", **kw):
    d = {"id": f"{source}:{n}", "country": country, "source": source, "outlet": outlet, "org": "Org",
         "lang": "ru", "date": "2024-05-01", "url": f"https://example.org/{source}/{n}", "title": f"T{n}",
         "speaker": None, "kind": "statement", "text": f"Текст номер {n}. Second sentence.", "via": "direct",
         "fetched": F_OLD}
    d.update(kw)
    return d


def write(docs_dir: Path, country: str, source: str, rows, tail: str = "") -> None:
    p = docs_dir / country / f"{source}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    body = "".join((r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)) + "\n" for r in rows)
    p.write_text(body + tail, encoding="utf-8")
    (docs_dir / country / f"{source}.lock").write_text("")


@pytest.fixture()
def corpus(tmp_path):
    docs = tmp_path / "docs"
    write(docs, "RU", "mid_ru", [
        doc("mid_ru", 1, speaker="Zakharova", wayback="20240501120000", sample="all", tags=["a", "b"],
            labelled=True, custom={"k": 1}),
        doc("mid_ru", 1, title="duplicate"),                       # duplicate id: first wins
        doc("mid_ru", 2, date="2024-13-45"),                       # invalid date -> dropped
        doc("mid_ru", 3, date="01.05.2024"),                       # not ISO -> dropped
        doc("mid_ru", 4, fetched=F_LATE),                          # after snapshot -> excluded
        doc("mid_ru", 5, text=""),                                 # missing text -> dropped
        "{not json",                                               # bad JSON line
        doc("mid_ru", 6, kind="transcript", wayback="https://web.archive.org/web/20230101000000id_/https://x"),
    ], tail='{"id": "mid_ru:7", "partial')                          # collector mid-append
    write(docs, "RU", "ria_ru", [
        doc("ria_ru", 1, outlet="state_media", kind="article", sample="random", tags=["Политика"]),
        doc("ria_ru", 2, outlet="state_media", kind="headline", text="T2", title="T2"),
    ])
    write(docs, "CN", "mfa_cn", [doc("mfa_cn", 1, country="CN", lang="zh", text="中国外交部发言人表示 Taiwan 问题。",
                                     translation="original")])
    write(docs, "TW", "tw_taipeitimes", [doc("tw_taipeitimes", 1, country="TW", outlet="media", lang="en",
                                             truncated="archive_paywall")])
    write(docs, "RU", "bad_src", [doc("other", 1)])                 # source differs from file -> dropped
    return tmp_path


def run(tmp: Path, *extra: str):
    return ED.main(["--version", "0.1.0", "--snapshot-date", "2026-10-02", "--docs", str(tmp / "docs"),
                    "--out-root", str(tmp / "release"), "--semantic-dir", str(tmp / "semantic"),
                    "--git-root", str(tmp), "--min-free-gb", "0", *extra])


def test_release_layout_and_rows(corpus):
    m = run(corpus)
    out = corpus / "release" / "0.1.0"
    assert not (corpus / "release" / ".0.1.0.partial").exists()
    ru = pq.read_table(out / "official" / "RU.parquet").to_pylist()
    assert [r["id"] for r in ru] == ["mid_ru:1", "mid_ru:6"]
    r1 = ru[0]
    assert r1["title"] == "T1" and r1["speaker"] == "Zakharova" and r1["tags"] == ["a", "b"] and r1["labelled"] is True
    assert json.loads(r1["extra"]) == {"custom": {"k": 1}}
    assert r1["text_sha256"] == hashlib.sha256(r1["text"].encode()).hexdigest()
    assert r1["wayback_timestamp"] == "20240501120000" and ru[1]["wayback_timestamp"] == "20230101000000"
    assert str(r1["date"]) == "2024-05-01" and r1["text_scope"] == "full"
    cn = pq.read_table(out / "official" / "CN.parquet").to_pylist()[0]
    assert cn["n_words"] == 13  # 12 CJK characters + "Taiwan"
    # media: no text column, flags set
    media = pq.read_table(out / "media" / "RU.parquet")
    assert "text" not in media.column_names
    rows = {r["id"]: r for r in media.to_pylist()}
    assert rows["ria_ru:1"]["text_available_locally"] is True
    assert rows["ria_ru:2"]["text_available_locally"] is False and rows["ria_ru:2"]["text_scope"] == "headline"
    tw = pq.read_table(out / "media" / "TW.parquet").to_pylist()[0]
    assert tw["outlet"] == "media" and tw["text_scope"] == "lead_archive_paywall"
    # JSONL mirror matches the Parquet ids and keeps full text
    with gzip.open(out / "official_jsonl" / "RU.jsonl.gz", "rt", encoding="utf-8") as f:
        js = [json.loads(line) for line in f]
    assert [d["id"] for d in js] == ["mid_ru:1", "mid_ru:6"] and js[0]["date"] == "2024-05-01"
    assert js[0]["extra"] == {"custom": {"k": 1}} and js[0]["text"].startswith("Текст")
    # validation accounting
    v = m["validation"]
    assert v["dropped"]["date_invalid"] == 1 and v["dropped"]["date_not_iso"] == 1
    assert v["dropped"]["missing:text"] == 1 and v["dropped"]["bad_json"] == 1
    assert v["dropped"]["source_differs_from_file"] == 1
    assert v["excluded"]["duplicate_id"] == 1 and v["excluded"]["fetched_after_snapshot"] == 1
    assert m["totals"] == {"official": 3, "media": 3, "countries": 3, "sources": 4}
    assert next(i for i in m["inputs"] if i["path"].endswith("mid_ru.jsonl"))["partial_tail_bytes"] > 0


def test_drops_publication_dates_after_snapshot(corpus):
    write(corpus / "docs", "IR", "ir_x", [doc("ir_x", 1, country="IR", outlet="state_media", lang="fa"),
                                          doc("ir_x", 2, country="IR", outlet="state_media", lang="fa",
                                              date="2026-12-13")])
    m = run(corpus)
    assert m["validation"]["dropped"]["date_after_snapshot"] == 1
    ids = pq.read_table(corpus / "release" / "0.1.0" / "media" / "IR.parquet").column("id").to_pylist()
    assert ids == ["ir_x:1"]


def test_official_journalism_and_forwards_are_metadata_only(corpus):
    write(corpus / "docs", "RU", "rg_ru", [doc("rg_ru", 1, kind="article")])
    write(corpus / "docs", "RU", "telegram_ru", [doc("telegram_ru", 1, channel="MID_Russia"),
                                                 doc("telegram_ru", 2, channel="MID_Russia", forwarded=True)])
    run(corpus)
    out = corpus / "release" / "0.1.0"
    official = set(pq.read_table(out / "official" / "RU.parquet").column("id").to_pylist())
    media = pq.read_table(out / "media" / "RU.parquet")
    assert "telegram_ru:1" in official and not official & {"rg_ru:1", "telegram_ru:2"}
    assert {"rg_ru:1", "telegram_ru:2"} <= set(media.column("id").to_pylist()) and "text" not in media.column_names
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "`rg_ru`" in readme
    # country table counts released full texts, not the corpus outlet class
    assert "| RU | 3 | 4 |" in readme
    cov = {(r["source"], r["release"]): int(r["n"]) for r in csv.DictReader((out / "coverage.csv").open())}
    assert cov[("rg_ru", "metadata_only")] == 1 and cov[("telegram_ru", "full_text")] == 1


def test_reserved_doi_is_cited(corpus):
    m = run(corpus, "--doi", "10.5281/zenodo.123")
    out = corpus / "release" / "0.1.0"
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "https://doi.org/10.5281/zenodo.123" in readme and "XXXXXXX" not in readme
    assert m["doi"] == "10.5281/zenodo.123"


def test_docs_manifest_and_checksums(corpus):
    run(corpus)
    out = corpus / "release" / "0.1.0"
    for name in ("README.md", "CODEBOOK.md", "CHANGELOG.md", "ZENODO_METADATA.json", "MANIFEST.json", "SHA256SUMS"):
        assert (out / name).exists(), name
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "| RU | mid_ru | Org | ru | 2024-05-01 | 2024-05-01 | 2 |" in readme
    assert "CC BY 4.0" in readme and "0009-0000-2065-8481" in readme and "Not included" in readme
    codebook = (out / "CODEBOOK.md").read_text(encoding="utf-8")
    assert "`wayback_timestamp`" in codebook and "Solar Hijri" in codebook and "Zakharova (1)" in codebook
    z = json.loads((out / "ZENODO_METADATA.json").read_text(encoding="utf-8"))["metadata"]
    assert z["upload_type"] == "dataset" and z["access_right"] == "open" and "access_conditions" not in z
    assert z["creators"][0] == {"name": "Walberg, Jonathan", "affiliation": "University of Virginia",
                                "orcid": "0009-0000-2065-8481"}
    for line in (out / "SHA256SUMS").read_text().splitlines():
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256((out / rel).read_bytes()).hexdigest() == digest
    m = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
    assert {f["path"] for f in m["files"]} >= {"official/RU.parquet", "media/RU.parquet", "README.md"}
    assert m["semantic"]["included"] is False


def test_never_overwrites_and_filters(corpus):
    run(corpus)
    with pytest.raises(SystemExit):
        run(corpus)
    m = ED.main(["--version", "0.2.0", "--countries", "CN", "--docs", str(corpus / "docs"), "--out-root",
                 str(corpus / "release"), "--semantic-dir", str(corpus / "none"), "--git-root", str(corpus),
                 "--min-free-gb", "0", "--to", "2024-12-31"])
    assert m["totals"]["official"] == 1 and m["countries_filter"] == ["CN"]
    assert "0.1.0" in (corpus / "release" / "0.2.0" / "CHANGELOG.md").read_text(encoding="utf-8")


def test_size_guard(corpus):
    with pytest.raises(SystemExit):
        run(corpus, "--max-gb", "0.000001")
    assert not (corpus / "release" / "0.1.0").exists()


def test_cutoff_is_capped_at_build_time():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 3, 6, 0, tzinfo=timezone.utc)
    c = ED.resolve_cutoff("2099-01-01", now)
    assert c["effective"] == "2026-10-03T06:00:00+00:00"
    assert ED.resolve_cutoff("2026-01-01T00:00:00Z", now)["effective"] == "2026-01-01T00:00:00+00:00"


def _semantic_db(path: Path, scored: bool) -> None:
    path.mkdir(parents=True)
    con = sqlite3.connect(path / "semantic.sqlite")
    con.executescript("""
      CREATE TABLE docs(doc_id TEXT PRIMARY KEY);
      CREATE TABLE doc_tone(doc_id TEXT PRIMARY KEY, nsent INTEGER, hostility REAL, threat REAL,
        conciliation REAL, grievance REAL, escalation REAL, deescalation REAL, model_v TEXT);
      CREATE TABLE doc_topic(doc_id TEXT PRIMARY KEY, topic INTEGER, sim REAL, topic_v TEXT);
      CREATE TABLE mentions(doc_id TEXT, idx INTEGER, target TEXT, pat TEXT, self INTEGER);
      INSERT INTO docs VALUES('mid_ru:1');
      INSERT INTO doc_topic VALUES('mid_ru:1', 3, 0.5, 'tv1');
      INSERT INTO mentions VALUES('mid_ru:1', 0, 'US', 'p', 0), ('mid_ru:1', 1, 'NATO', 'p', 0),
                                 ('mid_ru:1', 2, 'RUSSIA', 'p', 1), ('nope:1', 0, 'US', 'p', 0);
    """)
    if scored:
        con.execute("INSERT INTO doc_tone VALUES('mid_ru:1', 4, .9, .8, .1, .2, .3, .05, 'm1')")
        con.execute("INSERT INTO doc_tone VALUES('not_in_release:1', 4, .9, .8, .1, .2, .3, .05, 'm1')")
    con.commit()
    con.close()
    (path / "aggregates").mkdir()
    (path / "aggregates" / "topics.json").write_text(json.dumps({"topics": [{"topic": 3, "label": "sanctions"}]}))


def test_semantic_skipped_when_unscored(corpus):
    _semantic_db(corpus / "semantic", scored=False)
    m = run(corpus)
    assert m["semantic"]["included"] is False and "0 documents" in m["semantic"]["reason"]
    assert not (corpus / "release" / "0.1.0" / "semantic").exists()


def test_semantic_included_when_scored(corpus):
    _semantic_db(corpus / "semantic", scored=True)
    m = run(corpus)
    assert m["semantic"]["included"] is True and m["semantic"]["rows"] == 1
    rows = pq.read_table(corpus / "release" / "0.1.0" / "semantic" / "doc_scores.parquet").to_pylist()
    assert rows[0]["id"] == "mid_ru:1" and rows[0]["topic_label"] == "sanctions"
    assert rows[0]["targets"] == ["NATO", "US"] and abs(rows[0]["hostility"] - 0.9) < 1e-6
    assert rows[0]["validation_status"] == "not_human_validated"
    out = corpus / "release" / "0.1.0"
    meta = pq.read_schema(out / "semantic" / "doc_scores.parquet").metadata
    assert meta[b"validation_status"] == b"not_human_validated"
    assert "not yet human-validated" in (out / "README.md").read_text(encoding="utf-8")
    zen = json.loads((out / "ZENODO_METADATA.json").read_text(encoding="utf-8"))["metadata"]
    assert "NOT yet been validated" in zen["description"]


def test_coverage_csv_and_repo_link(corpus):
    run(corpus)
    out = corpus / "release" / "0.1.0"
    rows = list(csv.DictReader((out / "coverage.csv").open(encoding="utf-8")))
    assert {r["source"] for r in rows} >= {"mid_ru", "ria_ru", "mfa_cn"}
    assert sum(int(r["n"]) for r in rows) == sum(c["n"] for c in json.loads(
        (out / "MANIFEST.json").read_text(encoding="utf-8"))["coverage"])
    assert "coverage.csv" in (out / "SHA256SUMS").read_text(encoding="utf-8")
    zen = json.loads((out / "ZENODO_METADATA.json").read_text(encoding="utf-8"))["metadata"]
    assert zen["related_identifiers"][0]["identifier"].startswith("https://github.com/")


def test_check_row_reasons():
    assert RD.check_row(doc("mid_ru", 1), "RU", "mid_ru") is None
    assert RD.check_row(doc("mid_ru", 1, outlet="blog"), "RU", "mid_ru") == "bad_outlet"
    assert RD.check_row(doc("mid_ru", 1, lang="Russian"), "RU", "mid_ru") == "bad_lang"
    assert RD.check_row(doc("mid_ru", 1), "CN", "mid_ru") == "country_differs_from_dir"
    assert RD.check_row(doc("mid_ru", 1, fetched="yesterday"), "RU", "mid_ru") == "bad_fetched"
    assert RD.check_row(["x"], "RU", "mid_ru") == "not_an_object"
