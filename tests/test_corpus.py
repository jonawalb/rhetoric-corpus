"""Unit tests: collector lib, importers, index build (incl. incremental), search in EN/RU/ZH/FA."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "collectors"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_index  # noqa: E402
import import_existing  # noqa: E402
import lib  # noqa: E402
import searchlib as sl  # noqa: E402
from textnorm import bucket_of, fold, index_tokens, norm, sentences  # noqa: E402

DOCS = [
    {"id": "t_ru:1", "country": "RU", "source": "t_ru", "outlet": "state_media", "org": "RIA", "lang": "ru", "date": "2024-11-29",
     "url": "https://example.org/ru/1", "title": "Плутоний", "speaker": None, "kind": "article",
     "text": "США впервые за десятилетия изготовили плутониевые сердечники для боеголовок W87-1 в Лос-Аламосе. Это ядро программы."},
    {"id": "t_ru:2", "country": "RU", "source": "t_ru", "outlet": "commentary", "org": "Fondsk", "lang": "ru", "date": "2023-05-02",
     "url": "https://example.org/ru/2", "title": "Арсенал", "speaker": None, "kind": "article",
     "text": "Американцы разучились делать бомбы: производство плутониевых ядер остановлено с 1989 года. Ёлки зелёные."},
    {"id": "t_en:1", "country": "US", "source": "t_en", "outlet": "official", "org": "NNSA", "lang": "en", "date": "2024-10-01",
     "url": "https://example.org/en/1", "title": "Pit certified", "speaker": None, "kind": "statement",
     "text": "NNSA certified the first W87-1 war reserve plutonium pit produced at PF-4 at Los Alamos. Pit production resumes."},
    {"id": "t_zh:1", "country": "CN", "source": "t_zh", "outlet": "state_media", "org": "Xinhua", "lang": "zh", "date": "2024-10-03",
     "url": "https://example.org/zh/1", "title": "钚芯", "speaker": None, "kind": "article",
     "text": "美国洛斯阿拉莫斯国家实验室生产了首个钚芯。美国NNSA近日表示，钚弹芯生产恢复。"},
    {"id": "t_fa:1", "country": "IR", "source": "t_fa", "outlet": "state_media", "org": "IRNA", "lang": "fa", "date": "2024-10-05",
     "url": "https://example.org/fa/1", "title": "پلوتونیوم", "speaker": None, "kind": "article",
     "text": "آمریکا هسته پلوتونيوم جدید در لس آلاموس ساخت."},  # Arabic yeh in پلوتونيوم: must still match ی
]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    monkeypatch.setattr(lib, "DOCS", docs)
    by = {}
    for d in DOCS:
        by.setdefault((d["country"], d["source"]), []).append(d)
    for (c, s), rows in by.items():
        lib.write_docs(c, s, rows)
    db = tmp_path / "corpus.sqlite"
    con = build_index.connect(db)
    build_index.update(con, docs)
    yield tmp_path, docs, con
    con.close()


def ids(res):
    return sorted(d["id"] for d in res["results"])


# ------------------------------------------------------------------ lib
def test_write_docs_dedupes_and_validates(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "DOCS", tmp_path)
    row = dict(DOCS[2])
    assert lib.write_docs("US", "t_en", [row, row]) == (1, 1)
    assert lib.write_docs("US", "t_en", [row]) == (0, 1)
    bad = dict(row, id="other:1")
    with pytest.raises(ValueError):
        lib.write_docs("US", "t_en", [bad])
    with pytest.raises(ValueError):
        lib.write_docs("US", "t_en", [dict(row, id="t_en:9", date="1 Oct 2024")])
    stored = json.loads((tmp_path / "US" / "t_en.jsonl").read_text().splitlines()[0])
    assert stored["via"] == "direct" and stored["fetched"]


def test_make_id_and_state(tmp_path, monkeypatch):
    assert lib.make_id("s", "https://a.b/c") == lib.make_id("s", "https://a.b/c")
    assert lib.make_id("s", "123") == "s:123"
    monkeypatch.setattr(lib, "STATE", tmp_path)
    st = lib.State("x")
    st.mark_done("u1")
    st["cursor"] = 5
    st.save()
    st2 = lib.State("x")
    assert st2.is_done("u1") and st2["cursor"] == 5


def test_clean_html():
    assert lib.clean_html("<p>A&nbsp;b</p><script>x</script><p>c</p>") == "A b\nc"


# ------------------------------------------------------------------ text
def test_norm_is_length_preserving():
    s = "Ёлка يك ۱۲"
    assert len(norm(s)) == len(s) and norm(s) == "Елка یک 12"
    assert len(fold("Türkiye Ёж")) == len("Türkiye Ёж")


def test_sentences_max_300():
    text = ("Word " * 200).strip() + ". Next one. 中文句子。第二句！"
    ss = sentences(text)
    assert all(len(s) <= 300 for s in ss)
    assert "Next one." in ss and "中文句子。" in ss


def test_index_tokens_split_cjk_from_latin():
    toks = index_tokens("美国NNSA近日 W87-1")
    assert {"nnsa", "w87", "1", "近日", "美国", "美"} <= toks
    assert bucket_of("plutonium", 512) == bucket_of("pl", 512)


# ------------------------------------------------------------------ importers
def test_import_kremlin_and_iran(tmp_path, monkeypatch):
    rg = tmp_path / "rg"
    rg.mkdir()
    (rg / "corpus_kremlin.jsonl").write_text(json.dumps({"id": "1", "url": "http://en.kremlin.ru/x/1", "date": "2024-01-02",
                                                         "title": "T", "text": "Putin words.", "labelled": True,
                                                         "via": "wayback:20240101000000"}) + "\n" +
                                             json.dumps({"id": "2", "url": "u", "date": "2024-01-03", "title": "T",
                                                         "text": "", "labelled": True, "via": "kremlin"}) + "\n")
    (rg / "corpus_iran_mfa.jsonl").write_text(json.dumps({"id": "9", "url": "https://en.mfa.ir/9", "date": "2024-01-02",
                                                          "title": "S", "text": "Statement.", "via": "live"}) + "\n")
    monkeypatch.setattr(import_existing, "RG", rg)
    monkeypatch.setattr(lib, "DOCS", tmp_path / "docs")
    assert import_existing.import_kremlin() == 1  # empty-text row skipped
    assert import_existing.import_iran() == 1
    k = json.loads((tmp_path / "docs" / "RU" / "kremlin_en.jsonl").read_text())
    assert k["id"] == "kremlin_en:1" and k["via"] == "wayback" and k["speaker"] == "Putin" and k["wayback"] == "20240101000000"
    assert import_existing.import_kremlin() == 1  # rerun is idempotent


def test_prc_sink_ids_are_stable_and_split_by_language(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "DOCS", tmp_path)
    sink = import_existing.PrcSink("mfa_cn", "MFA")
    kw = dict(date="2026-01-05", question="Q?", answer="A.", url="https://x", speaker="Lin Jian", input_name="a", fetched="t")
    sink.add(lang="en", **kw)
    sink.add(lang="en", **kw)               # same Q&A from a second input collapses
    sink.add(lang="zh", **dict(kw, question="问？", answer="答。"))
    assert len(sink.rows) == 2
    assert {r["lang"] for r in sink.rows.values()} == {"en", "zh"}
    assert all(i.endswith((":en", ":zh")) for i in sink.rows)
    assert import_existing.lang_of("中华人民共和国外交部") == "zh" and import_existing.lang_of("Foreign Ministry") == "en"


# ------------------------------------------------------------------ index + search
def test_index_counts(store):
    _, _, con = store
    assert con.execute("SELECT count(*) FROM docs").fetchone()[0] == len(DOCS)
    assert con.execute("SELECT count(*) FROM fts_cjk").fetchone()[0] >= 1
    assert con.execute("SELECT max(length(text)) FROM sentences").fetchone()[0] <= 300


def test_search_english(store):
    _, _, con = store
    assert ids(sl.search(con, "plutonium pit")) == ["t_en:1"]
    assert ids(sl.search(con, '"pit production" OR "plutonium core"')) == ["t_en:1"]
    assert ids(sl.search(con, "PF-4")) == ["t_en:1"]
    assert ids(sl.search(con, "plutonium NEAR/10 Alamos")) == ["t_en:1"]
    assert ids(sl.search(con, "plutonium NEAR/2 Alamos")) == []
    r = sl.search(con, "Los Alamos")
    assert r["results"][0]["snippets"][0][1] == "Los Alamos"


def test_search_russian_morphology(store):
    _, _, con = store
    assert ids(sl.search(con, "плутониевый сердечник")) == []          # exact phrase: no such form
    assert ids(sl.search(con, "плутониевый сердечник", morph=True)) == ["t_ru:1"]
    assert ids(sl.search(con, "плутониевое ядро", morph=True)) == ["t_ru:2"]   # ядер via the forms list
    assert ids(sl.search(con, "разучились", morph=True)) == ["t_ru:2"]
    assert ids(sl.search(con, "Лос-Аламос*")) == ["t_ru:1"]
    assert ids(sl.search(con, "елки")) == ["t_ru:2"]                     # ё folded to е
    assert ids(sl.search(con, "W87-1", f=sl.Filters(country=["RU"]))) == ["t_ru:1"]


def test_search_chinese(store):
    _, _, con = store
    r = sl.search(con, "钚芯")                     # 2 characters: scan path
    assert r["table"] == "scan" and ids(r) == ["t_zh:1"]
    r = sl.search(con, "洛斯阿拉莫斯")               # trigram path
    assert r["table"] == "fts_cjk" and ids(r) == ["t_zh:1"]
    assert ids(sl.search(con, "钚弹芯")) == ["t_zh:1"]
    r = sl.search(con, "plutonium pit OR 钚芯".replace("plutonium pit", '"plutonium pit"'))
    assert r["table"] == "union" and ids(r) == ["t_en:1", "t_zh:1"]


def test_search_persian(store):
    _, _, con = store
    assert ids(sl.search(con, "هسته پلوتونیوم")) == ["t_fa:1"]
    assert ids(sl.search(con, "لس آلاموس")) == ["t_fa:1"]


def test_filters_and_count_by(store):
    _, _, con = store
    f = sl.Filters(date_from="2024-10-02", date_to="2024-12-31")
    r = sl.search(con, '"W87-1" OR 钚芯', f)
    assert ids(r) == ["t_ru:1", "t_zh:1"]
    rows = sl.count_by(con, r["plan"], r["rows"], "month")
    assert dict((k, d) for k, d, _ in rows) == {"2024-10": 1, "2024-11": 1}


def test_incremental_append_change_and_remove(store):
    tmp, docs, con = store
    new = dict(DOCS[2], id="t_en:2", date="2025-01-01", text="Another plutonium pit story.")
    lib.write_docs("US", "t_en", [new])
    st = build_index.update(con, docs)
    assert st["files_appended"] == 1 and st["docs_added"] == 1 and st["files_skipped"] == len(DOCS) - 2
    assert ids(sl.search(con, "plutonium pit")) == ["t_en:1", "t_en:2"]
    lib.write_docs("US", "t_en", [DOCS[2]], replace=True)      # rewrite: drops t_en:2
    st = build_index.update(con, docs)
    assert st["files_reindexed"] == 1 and st["docs_removed"] == 2
    assert ids(sl.search(con, "plutonium pit")) == ["t_en:1"]
    (docs / "IR" / "t_fa.jsonl").unlink()
    st = build_index.update(con, docs)
    assert st["files_removed"] == 1
    assert ids(sl.search(con, "لس آلاموس")) == []
    # FTS stays consistent with docs after deletes
    con.execute("INSERT INTO fts_words(fts_words) VALUES('integrity-check')")


def test_query_errors():
    with pytest.raises(ValueError):
        sl.parse('"a" OR')
    with pytest.raises(ValueError):
        sl.parse("(a OR b")
