"""Rhetoric Search data for the Hugging Face host (publish/build_hf_data.py, vendored from tsm-strait-layers):
the full-document shards hold official documents only (media guard), and the sealed current.json pointer and build
pruning keep clients on one complete build."""
import gzip
import json
import sqlite3
import sys
from pathlib import Path

import pytest

PUBLISH = Path(__file__).resolve().parent.parent / "publish"  # vendored copy the nightly CI job runs
SCRIPT = PUBLISH / "build_hf_data.py"
pytest.importorskip("cryptography")
sys.path[:0] = [str(PUBLISH)]
import build_hf_data as H  # noqa: E402
import vault as build_site  # noqa: E402  (same API as tsm-strait-layers scripts/build_site.py)

KEY = bytes(range(32))
MAGIC = build_site.MAGIC5
MEDIA_TEXT = "Xinhua commentary body text that must never be published in full, however it gets there. " * 2


def make_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE docs(rowid INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, file TEXT NOT NULL, country TEXT, "
                "source TEXT, outlet TEXT, org TEXT, lang TEXT, date TEXT, url TEXT, title TEXT, speaker TEXT, kind TEXT, "
                "via TEXT, text TEXT, nchars INTEGER)")
    rows = [
        (1, "mfa_cn:1", "CN/mfa_cn.jsonl", "CN", "mfa_cn", "official", "MFA", "zh", "2026-01-02", "https://mfa.example/1",
         "例行记者会", "毛宁", "briefing", "direct", "外交部发言人主持例行记者会。\n台湾是中国的一部分。", 0),
        (2, "xinhua:2", "CN/prc_statemedia.jsonl", "CN", "prc_statemedia", "state_media", "Xinhua", "en", "2026-01-03",
         "https://xinhua.example/2", "Commentary", "", "article", "rss", MEDIA_TEXT, 0),
        (3, "kremlin_en:3", "RU/kremlin_en.jsonl", "RU", "kremlin_en", "official", "Kremlin", "en", "2026-01-04",
         "https://kremlin.example/3", "Meeting", "Putin", "transcript", "wayback", "Long official transcript text. " * 50, 0),
        (4, "tass:4", "RU/tass.jsonl", "RU", "tass_com", "commentary", "TASS", "en", "2026-01-05", "https://tass.example/4",
         "Op-ed", "", "article", "rss", "Commentary body " * 20, 0),
    ]
    con.executemany("INSERT INTO docs VALUES (" + ",".join("?" * 16) + ")", rows)
    con.commit()
    return con


def shard_docs(d: Path) -> dict:
    out = {}
    for p in d.glob("*.json.gz"):
        if p.name != "index.json.gz":
            out.update(json.loads(gzip.decompress(p.read_bytes())))
    return out


def test_doc_shards_hold_official_documents_only(tmp_path):
    con = make_db(tmp_path / "c.sqlite")
    info = H.build_doc_shards(con, tmp_path / "docs", {"kremlin_en:3": "20230131155447"}, shard_kb=0)
    docs = shard_docs(tmp_path / "docs")
    assert sorted(docs) == ["1", "3"] and info["docs"] == 2
    assert all(set(r) == H.DOC_KEYS for r in docs.values())
    assert docs["3"]["wayback"] == "https://web.archive.org/web/20230131155447/https://kremlin.example/3"
    assert docs["1"]["wayback"] == "https://web.archive.org/web/https://mfa.example/1"
    index = json.loads(gzip.decompress((tmp_path / "docs" / "index.json.gz").read_bytes()))
    assert index["ranges"] == [[1, 1], [3, 3]]  # shard_kb=0: one document per shard, by rowid range
    assert H.assert_no_media_text(tmp_path / "docs", con) == 2
    blob = b"".join(gzip.decompress(p.read_bytes()) for p in (tmp_path / "docs").glob("*.json.gz"))
    assert MEDIA_TEXT[:60].encode() not in blob and b"Commentary body" not in blob


def _write_shard(d: Path, docs: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "0.json.gz").write_bytes(H.gz(docs))


def _rec(text: str) -> dict:
    return {"date": "2026-01-03", "source": "x", "org": "", "lang": "en", "title": "", "speaker": "", "url": "",
            "wayback": "", "text": text}


def test_media_guard_rejects_a_media_document(tmp_path):
    con = make_db(tmp_path / "c.sqlite")
    _write_shard(tmp_path / "docs", {"1": _rec("ok"), "2": _rec(MEDIA_TEXT)})
    with pytest.raises(H.MediaTextError, match="doc 2: outlet 'state_media'"):
        H.assert_no_media_text(tmp_path / "docs", con)


def test_media_guard_rejects_media_text_under_an_official_id(tmp_path):
    con = make_db(tmp_path / "c.sqlite")
    _write_shard(tmp_path / "docs", {"1": _rec(MEDIA_TEXT)})
    with pytest.raises(H.MediaTextError, match="doc 1: shipped text is not the official document's own text"):
        H.assert_no_media_text(tmp_path / "docs", con)


def test_media_guard_allows_official_text_that_media_reprinted_verbatim(tmp_path, capsys):
    con = make_db(tmp_path / "c.sqlite")
    speech = "Official New Year address text, published on the president's own site. " * 3
    con.execute("UPDATE docs SET text = ? WHERE rowid IN (3, 4)", (speech,))  # doc 4 (media) reprints doc 3
    _write_shard(tmp_path / "docs", {"3": _rec(speech)})
    assert H.assert_no_media_text(tmp_path / "docs", con) == 1
    assert "doc 3 (official) reprinted verbatim by media doc 4" in capsys.readouterr().out


def test_media_guard_rejects_extra_fields(tmp_path):
    con = make_db(tmp_path / "c.sqlite")
    _write_shard(tmp_path / "docs", {"1": {**_rec("ok"), "body": "x"}})
    with pytest.raises(H.MediaTextError, match="unexpected fields"):
        H.assert_no_media_text(tmp_path / "docs", con)


def _stage_build(hf: Path, build_id: str) -> None:
    plain = hf.parent / "plain" / build_id
    (plain / "s").mkdir(parents=True)
    (plain / "s" / "0.json.gz").write_bytes(H.gz({"docs": [], "text": build_id}))
    out = hf / "data" / build_id
    files = H.seal_tree(plain, out, KEY, MAGIC)
    (out / "manifest.json").write_text(json.dumps({"build": build_id, "files": files}))


def test_pointer_is_sealed_and_names_the_build(tmp_path):
    hf = tmp_path / "hf"
    _stage_build(hf, "20260101T000000Z")
    p = H.write_pointer(hf, "20260101T000000Z", KEY, MAGIC)
    raw = p.read_bytes()
    assert raw.startswith(MAGIC) and b"20260101" not in raw  # sealed: the build id is not readable
    assert H.read_pointer(hf, KEY)["build"] == "20260101T000000Z"
    sealed = (hf / "data" / "20260101T000000Z" / "s" / "0.json.gz").read_bytes()
    assert json.loads(gzip.decompress(build_site.unseal_file(KEY, sealed)))["text"] == "20260101T000000Z"


def test_pointer_refuses_an_incomplete_build(tmp_path):
    hf = tmp_path / "hf"
    (hf / "data" / "20260102T000000Z").mkdir(parents=True)  # no manifest: still being written
    with pytest.raises(SystemExit):
        H.write_pointer(hf, "20260102T000000Z", KEY, MAGIC)


def test_prune_keeps_the_newest_builds_and_the_current_one(tmp_path):
    hf = tmp_path / "hf"
    ids = ["20260101T000000Z", "20260102T000000Z", "20260103T000000Z"]
    for b in ids:
        _stage_build(hf, b)
    H.write_pointer(hf, ids[-1], KEY, MAGIC)
    assert H.prune_builds(hf, keep=2, current=ids[-1]) == [ids[0]]
    assert H.staged_builds(hf) == ids[1:]
    assert H.prune_builds(hf, keep=1, current=ids[1]) == []  # the build current.json names is never deleted
    assert (hf / "data" / "current.json").exists()


def test_upload_plan_copies_unchanged_files():
    new = {"a": {"sha256": "1", "bytes": 1}, "b": {"sha256": "2", "bytes": 1}, "c": {"sha256": "3", "bytes": 1}}
    prev = {"a": {"sha256": "1", "bytes": 1}, "b": {"sha256": "9", "bytes": 1}}
    assert H.upload_plan(new, prev) == (["a"], ["b", "c"])
    assert H.upload_plan(new, {}) == ([], ["a", "b", "c"])


def test_deterministic_sealing_makes_unchanged_files_identical(tmp_path):
    a = build_site.seal_file(KEY, MAGIC, b"same bytes")
    assert a == build_site.seal_file(KEY, MAGIC, b"same bytes") != build_site.seal_file(KEY, MAGIC, b"other")


def test_stats_are_plaintext_counts_only(tmp_path):
    plain, hf = tmp_path / "plain", tmp_path / "hf"
    plain.mkdir()
    (hf / "data").mkdir(parents=True)
    (plain / "meta.json.gz").write_bytes(H.gz({"totals": {"docs": 12, "media_docs": 30, "sentences": 99}, "sources": []}))
    p = H.write_stats(plain, hf, "2026-01-01T00:00:00+00:00")
    assert json.loads(p.read_text()) == {"official_docs": 12, "media_docs": 30, "documents": 42,
                                         "built": "2026-01-01T00:00:00+00:00"}
