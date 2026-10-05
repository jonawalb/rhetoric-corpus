"""Segmented store (scripts/segments.py + store_sync layout 2): seal/cut under concurrent appends, id skipping after
the cut, crash recovery, store merges, lazy part pulls, the index build from parts, and the one-time migration."""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "collectors"))
import build_index  # noqa: E402
import cn_common  # noqa: E402
import lib  # noqa: E402
import ru_common as rc  # noqa: E402
import segments  # noqa: E402
import store_sync  # noqa: E402


def row(i: int, src: str = "zz_t", cc: str = "ZZ", **kw) -> dict:
    return {"id": f"{src}:{i}", "country": cc, "source": src, "outlet": "official", "lang": "en",
            "date": "2024-01-01", "url": f"http://x/{src}/{i}", "text": f"text {i} plutonium", **kw}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class DirUploader:
    """segments.Uploader backed by a directory; `fail` makes the next upload raise (a network error)."""

    def __init__(self, store: Path):
        self.store, self.fail, self.uploads = store, False, 0

    def upload(self, local: Path, remote: str) -> None:
        if self.fail:
            self.fail = False
            raise IOError("network down")
        assert not (self.store / remote).exists(), "a part is never overwritten"
        (self.store / remote).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, self.store / remote)
        self.uploads += 1

    def verify(self, remote: str, sha256: str, lines: int) -> bool:
        p = self.store / remote
        return p.exists() and sha(p) == sha256 and segments.count_gz_lines(p) == lines


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(lib, "STATE", tmp_path / "state")
    lib._SEALED.clear()
    rc._IDS.clear()
    rc._SEALED_AT.clear()
    return tmp_path


def stored_ids(store: Path) -> list:
    out = []
    for p in sorted(store.rglob("*.jsonl.gz")):
        out += [json.loads(ln)["id"] for ln in segments.open_lines(p)]
    return out


def active_ids(root: Path, cc="ZZ", src="zz_t") -> list:
    p = root / "docs" / cc / f"{src}.jsonl"
    return [json.loads(ln)["id"] for ln in segments.open_lines(p)] if p.exists() else []


# ---------------------------------------------------------------------------------------------- seal / cut
def test_seal_cuts_only_verified_lines_and_ids_skip_afterwards(root):
    store = root / "store"
    lib.write_docs("ZZ", "zz_t", [row(i) for i in range(5)])
    assert rc.write_docs_fast("ZZ", "zz_t", [row(i) for i in range(7)]) == (2, 7)  # warms the fast writer's cache
    rep = segments.seal(root, DirUploader(store), writer="t")
    assert rep["lines"] == 7 and rep["part"]["lines"] == 7 and rep["cut"] == 1
    assert (root / "docs" / "ZZ" / "zz_t.jsonl").read_bytes() == b""              # local copy cut
    assert sorted(stored_ids(store)) == sorted(f"zz_t:{i}" for i in range(7))
    assert lib.sealed_ids("ZZ", "zz_t") == {f"zz_t:{i}" for i in range(7)}
    # every writer still skips the sealed ids (the fast writer had them cached by offset before the cut)
    assert lib.write_docs("ZZ", "zz_t", [row(3), row(8)]) == (1, 8)
    assert rc.write_docs_fast("ZZ", "zz_t", [row(i) for i in range(10)]) == (2, 10)
    assert active_ids(root) == ["zz_t:8", "zz_t:7", "zz_t:9"]
    assert lib.existing_ids(lib.docs_path("ZZ", "zz_t")) == {f"zz_t:{i}" for i in range(10)}
    sink = cn_common.Sink("cn_t")
    lib.write_docs("CN", "cn_t", [row(1, "cn_t", "CN")])
    segments.seal(root, DirUploader(store), writer="t")
    sink2 = cn_common.Sink("cn_t")
    assert sink2.has("cn_t:1") and not sink2.add(row(1, "cn_t", "CN")) and sink.add(row(2, "cn_t", "CN"))
    # nothing new: a second seal makes no part and loses nothing
    segments.seal(root, DirUploader(store), writer="u")
    assert sorted(stored_ids(store)) == sorted([f"zz_t:{i}" for i in range(10)] + ["cn_t:1"])


def test_seal_under_concurrent_appends(root):
    """Two writer processes (lib.write_docs and write_docs_fast, overlapping ids) append while seals run."""
    store = root / "store"
    code = textwrap.dedent(f"""
        import sys
        from pathlib import Path
        sys.path.insert(0, {str(ROOT / 'collectors')!r})
        import lib, ru_common as rc
        lib.DOCS, lib.STATE = Path({str(root / 'docs')!r}), Path({str(root / 'state')!r})
        fast = sys.argv[1] == "fast"
        lo, hi = int(sys.argv[2]), int(sys.argv[3])
        for i in range(lo, hi, 3):
            rows = [dict(id=f"zz_t:{{j}}", country="ZZ", source="zz_t", lang="en", date="2024-01-01",
                         url=f"http://x/{{j}}", text="t" * 200) for j in range(i, min(i + 3, hi))]
            (rc.write_docs_fast if fast else lib.write_docs)("ZZ", "zz_t", rows)
    """)
    procs = [subprocess.Popen([sys.executable, "-c", code, "slow", "0", "900"]),
             subprocess.Popen([sys.executable, "-c", code, "fast", "600", "1500"])]
    up = DirUploader(store)
    seals = 0
    while any(p.poll() is None for p in procs):
        segments.seal(root, up, writer=f"w{seals}")
        seals += 1
        time.sleep(0.05)
    assert all(p.returncode == 0 for p in procs)
    segments.seal(root, up, writer="final")
    ids = stored_ids(store)
    assert seals >= 3 and up.uploads >= 2
    assert len(ids) == len(set(ids)) == 1500                       # nothing lost, nothing stored twice
    assert set(ids) == {f"zz_t:{i}" for i in range(1500)}
    assert active_ids(root) == []
    assert lib.sealed_ids("ZZ", "zz_t") == set(ids)


def test_seal_crash_recovery(root, monkeypatch):
    store = root / "store"
    up = DirUploader(store)
    lib.write_docs("ZZ", "zz_t", [row(i) for i in range(4)])
    up.fail = True
    with pytest.raises(IOError):
        segments.seal(root, up, writer="a")
    assert len(active_ids(root)) == 4 and lib.sealed_ids("ZZ", "zz_t") == set()   # nothing cut, nothing recorded
    lib.write_docs("ZZ", "zz_t", [row(4)])
    rep = segments.seal(root, up, writer="b")                       # resumes: uploads + finalizes the first part
    assert rep["resumed"]["finalized"] and rep["lines"] == 1
    # crash after the upload verified, before the cut: the next seal finalizes from the journal
    lib.write_docs("ZZ", "zz_t", [row(5), row(6)])
    real = segments.finalize
    monkeypatch.setattr(segments, "finalize", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        segments.seal(root, up, writer="c")
    monkeypatch.setattr(segments, "finalize", real)
    rep = segments.seal(root, up, writer="d")
    assert "-c-" in rep["resumed"]["finalized"] and rep["lines"] == 0
    ids = stored_ids(store)
    assert sorted(ids) == sorted(f"zz_t:{i}" for i in range(7)) and active_ids(root) == []


def test_retained_source_is_sealed_but_not_cut(root):
    store = root / "store"
    lib.write_docs("RU", "kremlin_en", [row(1, "kremlin_en", "RU")])
    segments.seal(root, DirUploader(store), writer="a")
    segments.seal(root, DirUploader(store), writer="b")
    assert active_ids(root, "RU", "kremlin_en") == ["kremlin_en:1"] and stored_ids(store) == ["kremlin_en:1"]


def test_bad_lines_are_quarantined_not_lost(root):
    p = root / "docs" / "ZZ" / "zz_t.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(row(1)) + "\n" + '{"id": "zz_t:2", "te{"id": "zz_t:3"}\n' + '{"id": "zz_t:4", "cut')
    rep = segments.seal(root, DirUploader(root / "store"), writer="a")
    assert rep["lines"] == 1 and rep["bad"] == 1
    assert p.read_text() == '{"id": "zz_t:4", "cut'                                  # partial tail stays
    assert "zz_t:3" in (root / "staging" / "seal" / "bad" / "ZZ_zz_t.jsonl").read_text()


# ---------------------------------------------------------------------------------------------- index
def test_index_from_parts_keeps_rowids(root):
    store = root / "store"
    db = root / "index" / "corpus.sqlite"
    lib.write_docs("ZZ", "zz_t", [row(1, wayback="20240101000000"), row(2)])
    con = build_index.connect(db)
    build_index.update(con, root / "docs")                               # indexed from the active file first
    before = dict(con.execute("SELECT id, rowid FROM docs"))
    con.execute("UPDATE docs SET wayback=NULL")                           # as in an index built before the column
    segments.seal(root, DirUploader(store), writer="a", keep_part=True)   # CI: the part stays in docs-parts/
    lib.write_docs("ZZ", "zz_t", [row(3, sample="random")])
    st = build_index.update(con, root / "docs")
    assert st["parts_indexed"] == 1 and st["docs_removed"] == 0 and st["dup_ids"] == 2 and st["docs_added"] == 1
    after = dict(con.execute("SELECT id, rowid FROM docs"))
    assert {k: after[k] for k in before} == before and len(after) == 3
    assert con.execute("SELECT wayback FROM docs WHERE id='zz_t:1'").fetchone()[0] == "20240101000000"
    assert con.execute("SELECT sample FROM docs WHERE id='zz_t:3'").fetchone()[0] == "random"
    # the part is not downloaded next time and the active file vanishes: indexed docs stay, the part stays known
    shutil.rmtree(root / "docs-parts")
    segments.seal(root, DirUploader(store), writer="b")
    (root / "docs" / "ZZ" / "zz_t.jsonl").unlink()
    st = build_index.update(con, root / "docs")
    assert st["docs_removed"] == 0 and len(dict(con.execute("SELECT id, rowid FROM docs"))) == 3
    assert con.execute("SELECT count(*) FROM files WHERE path LIKE 'docs-parts/%'").fetchone()[0] == 1
    hits = con.execute("SELECT count(*) FROM fts_words WHERE fts_words MATCH 'plutonium'").fetchone()[0]
    assert hits == 3


def test_iter_docs_and_materialize(root):
    store = root / "store"
    lib.write_docs("ZZ", "zz_t", [row(1), row(2)])
    segments.seal(root, DirUploader(store), writer="a", keep_part=True)
    lib.write_docs("ZZ", "zz_t", [row(3)])
    assert sorted(d["id"] for _, d in segments.iter_docs(root)) == ["zz_t:1", "zz_t:2", "zz_t:3"]
    n = segments.materialize(root, root / "out")
    assert n == {"ZZ/zz_t": 3} and len((root / "out" / "ZZ" / "zz_t.jsonl").read_text().splitlines()) == 3
    shutil.rmtree(root / "docs-parts")                                  # parts not local: refuse
    with pytest.raises(SystemExit):
        segments.materialize(root, root / "out2")


# ---------------------------------------------------------------------------------------------- store (fake hub)
class FakeFile:
    def __init__(self, path, size, sha256):
        self.path, self.size = path, size
        self.lfs = type("L", (), {"sha256": sha256})()


class FakeHub:
    """Enough of HfApi + hf_hub_download for store_sync, backed by a directory."""

    def __init__(self, d: Path):
        self.d, self.commits = d, []

    def repo_exists(self, *a, **k):
        return True

    def file_exists(self, repo, path, repo_type=None):
        return (self.d / path).exists()

    def create_repo(self, *a, **k):
        pass

    def repo_info(self, *a, **k):
        return type("I", (), {"private": True})()

    def create_commit(self, repo, repo_type, commit_message, operations):
        names = []
        for op in operations:
            dest = self.d / op.path_in_repo
            names.append(op.path_in_repo)
            if type(op).__name__ == "CommitOperationDelete":
                dest.unlink()
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            src = op.path_or_fileobj
            dest.write_bytes(src if isinstance(src, bytes) else Path(src).read_bytes())
        self.commits.append(names)

    def get_paths_info(self, repo, paths, repo_type=None):
        return [FakeFile(p, (self.d / p).stat().st_size, sha(self.d / p)) for p in paths if (self.d / p).exists()]

    def list_repo_tree(self, repo, path_in_repo, recursive, repo_type):
        base = self.d / path_in_repo
        if not base.exists():
            raise FileNotFoundError(path_in_repo)
        for p in sorted(base.rglob("*")):
            if p.is_file():
                yield FakeFile(p.relative_to(self.d).as_posix(), p.stat().st_size, sha(p))

    def download(self, repo, path, repo_type=None, local_dir=None, force_download=False, **k):
        out = Path(local_dir or (self.d.parent / "dl")) / path
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.d / path, out)
        return str(out)


@pytest.fixture
def hub(root, monkeypatch):
    import huggingface_hub
    h = FakeHub(root / "hub")
    h.d.mkdir()
    monkeypatch.setattr(store_sync, "ROOT", root)
    monkeypatch.setattr(store_sync, "MARKER", root / ".store_pulled")
    monkeypatch.setattr(store_sync, "api", lambda: h)
    monkeypatch.setattr(store_sync, "_download", lambda repo, p: Path(h.download(repo, p, local_dir=root / "dl")))
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", h.download)
    monkeypatch.setitem(store_sync._LAYOUT, "remote", 1)
    return h


def write_manifest(h: FakeHub, files: dict, layout: int = 1) -> None:
    meta = {"entries": files, "layout": layout} if layout > 1 else {"files": files}
    (h.d / "manifest.json").write_text(json.dumps(meta))
    if layout > 1:
        (h.d / "LAYOUT").write_text(f"{layout}\n")


def test_migrate_then_seal_then_ci_pull(root, hub):
    # layout-1 store: one docs file with 3 documents; the laptop's copy has one more (not pushed yet)
    stored = "".join(json.dumps(row(i)) + "\n" for i in range(3))
    (hub.d / "docs" / "ZZ").mkdir(parents=True)
    (hub.d / "docs" / "ZZ" / "zz_t.jsonl").write_text(stored)
    write_manifest(hub, {"docs/ZZ/zz_t.jsonl": {"sha256": sha(hub.d / "docs/ZZ/zz_t.jsonl"), "bytes": len(stored)},
                         "state/x.json": {"sha256": "s", "bytes": 1}})
    lib.write_docs("ZZ", "zz_t", [row(i) for i in range(4)])
    assert store_sync.seal("me/store") == {"layout": 1, "sealed": 0}           # layout 1: seal is a no-op
    rep = store_sync.migrate("me/store", force=True)
    assert rep["documents"] == 3 and rep["parts"] == 1
    meta = json.loads((hub.d / "manifest.json").read_text())
    assert meta["layout"] == 2 and "docs/ZZ/zz_t.jsonl" not in meta["entries"] and "state/x.json" in meta["entries"]
    assert (hub.d / "LAYOUT").read_text().strip() == "2"
    assert not (hub.d / "docs" / "ZZ" / "zz_t.jsonl").exists()
    assert lib.sealed_ids("ZZ", "zz_t") == {f"zz_t:{i}" for i in range(3)}
    # first laptop seal: only the unpushed document becomes a part, the local file is cut
    rep = store_sync.seal("me/store")
    assert rep["lines"] == 1 and rep["dropped_sealed"] == 3 and active_ids(root) == []
    meta = json.loads((hub.d / "manifest.json").read_text())
    parts = sorted(p for p in meta["entries"] if p.startswith("docs-parts/"))
    assert len(parts) == 2 and meta["layout"] == 2
    assert sorted(stored_ids(hub.d / "docs-parts")) == [f"zz_t:{i}" for i in range(4)]
    assert segments.read_ids(hub.d / "state/ids/ZZ/zz_t.ids") == {f"zz_t:{i}" for i in range(4)}
    # a "CI" checkout pulls: ids + only the parts its stored index has not indexed
    ci = root / "ci"
    (ci / "index").mkdir(parents=True)
    con = sqlite3.connect(ci / "index" / "corpus.sqlite")
    con.execute("CREATE TABLE files(path TEXT PRIMARY KEY, size INTEGER, mtime REAL, done INTEGER, prefix_sha TEXT,"
                " ndocs INTEGER)")
    con.execute("INSERT INTO files(path) VALUES(?)", (parts[0],))
    con.commit()
    con.close()
    (hub.d / "index").mkdir()
    shutil.copyfile(ci / "index" / "corpus.sqlite", hub.d / "index" / "corpus.sqlite")
    files = json.loads((hub.d / "manifest.json").read_text())["entries"]
    files["index/corpus.sqlite"] = {"sha256": sha(hub.d / "index/corpus.sqlite"),
                                    "bytes": (hub.d / "index/corpus.sqlite").stat().st_size}
    del files["state/x.json"]
    write_manifest(hub, files, 2)
    store_sync.ROOT = ci
    store_sync.MARKER = ci / ".store_pulled"
    store_sync._LAYOUT["remote"] = 1                                         # a fresh process (the CI job)
    rep = store_sync.pull("me/store", None)
    assert rep["parts_downloaded"] == 1 and rep["parts_indexed"] == 1
    assert not (ci / parts[0]).exists() and (ci / parts[1]).exists()
    assert segments.read_ids(ci / "state/ids/ZZ/zz_t.ids") == {f"zz_t:{i}" for i in range(4)}


def test_push_layout2_merges_ids_and_never_uploads_active_docs(root, hub):
    lib.write_docs("ZZ", "zz_t", [row(1)])
    segments.append_ids(root / "state/ids/ZZ/zz_t.ids", ["zz_t:1"], "local")
    (hub.d / "state" / "ids" / "ZZ").mkdir(parents=True)
    (hub.d / "state/ids/ZZ/zz_t.ids").write_text("zz_t:1\nzz_t:2\n# other writer\n")
    write_manifest(hub, {"state/ids/ZZ/zz_t.ids": {"sha256": sha(hub.d / "state/ids/ZZ/zz_t.ids"), "bytes": 30}}, 2)
    rep = store_sync.push("me/store", only=["docs", "state"])
    assert rep["upload"] == 1
    assert all("docs/ZZ/zz_t.jsonl" not in c for c in hub.commits)
    assert segments.read_ids(hub.d / "state/ids/ZZ/zz_t.ids") == {"zz_t:1", "zz_t:2"}     # union, nothing lost
    assert lib.sealed_ids("ZZ", "zz_t") == {"zz_t:1", "zz_t:2"}


def test_store_selection_includes_parts_and_ids():
    assert store_sync.wanted("docs-parts/2026-10/20261004T120000Z-mac.jsonl.gz")
    assert store_sync.wanted("docs-parts/legacy/RU/mid_ru.jsonl.gz")
    assert store_sync.wanted("state/ids/RU/mid_ru.ids")
    assert not store_sync.wanted("staging/seal/pending.json")


def test_legacy_part_conversion(tmp_path):
    src = tmp_path / "a.jsonl"
    src.write_text(json.dumps(row(1)) + "\n" + json.dumps(row(1)) + "\nnot json\n" + json.dumps(row(2)) + "\n")
    ids, n, bad = segments.legacy_part(src, tmp_path / "out" / "a.jsonl.gz")
    assert ids == ["zz_t:1", "zz_t:2"] and n == 2 and bad == 1
    with gzip.open(tmp_path / "out" / "a.jsonl.gz", "rt") as f:
        assert [json.loads(ln)["id"] for ln in f] == ["zz_t:1", "zz_t:2"]


# ---------------------------------------------------------------------------------------------- layout is sticky
def _migrated_store(root, hub) -> None:
    (hub.d / "docs" / "ZZ").mkdir(parents=True)
    (hub.d / "docs" / "ZZ" / "zz_t.jsonl").write_text("".join(json.dumps(row(i)) + "\n" for i in range(3)))
    write_manifest(hub, {"docs/ZZ/zz_t.jsonl": {"sha256": sha(hub.d / "docs/ZZ/zz_t.jsonl"), "bytes": 1}})
    store_sync.migrate("me/store", force=True)


def old_code_push_manifest(hub) -> None:
    """What store_sync before the segmented store did on any push: rewrite manifest.json as {updated, files}
    from what it read (it kept remote-only entries, dropped every other key, never deleted LAYOUT)."""
    meta = json.loads((hub.d / "manifest.json").read_text())
    files = meta["entries"] if "entries" in meta else meta["files"]       # (old code: meta["files"] -> KeyError)
    (hub.d / "manifest.json").write_text(json.dumps({"updated": "x", "files": files}))


def test_old_code_cannot_read_the_layout2_manifest(root, hub):
    _migrated_store(root, hub)
    with pytest.raises(KeyError):
        json.loads((hub.d / "manifest.json").read_text())["files"]      # pre-segment remote_manifest() fails loudly


def test_layout_survives_a_manifest_rewrite_without_the_key(root, hub):
    """Regression 2026-10-04: an older checkout's push dropped "layout" and every writer fell back to layout 1."""
    _migrated_store(root, hub)
    old_code_push_manifest(hub)
    store_sync._LAYOUT["remote"] = 1                                         # a new process
    store_sync.remote_manifest(hub, "me/store")
    assert store_sync.layout2()                                              # the LAYOUT marker wins
    lib.write_docs("ZZ", "zz_t", [row(7)])
    store_sync.push("me/store", only=["docs", "state"])
    assert not (hub.d / "docs" / "ZZ" / "zz_t.jsonl").exists()               # active docs never uploaded
    meta = json.loads((hub.d / "manifest.json").read_text())
    assert meta["layout"] == 2 and "entries" in meta                         # and the manifest is upgraded again


def test_checkout_that_saw_layout2_refuses_a_downgraded_store(root, hub):
    _migrated_store(root, hub)
    old_code_push_manifest(hub)
    (hub.d / "LAYOUT").unlink()                                              # even if the marker were lost too
    store_sync._LAYOUT["remote"] = 1
    with pytest.raises(SystemExit, match="downgraded"):
        store_sync.remote_manifest(hub, "me/store")
    with pytest.raises(SystemExit):
        store_sync.push("me/store", only=["docs"])


def test_layout1_push_refuses_when_the_store_becomes_layout2(root, hub, monkeypatch):
    (hub.d / "x").mkdir()
    write_manifest(hub, {})
    lib.write_docs("ZZ", "zz_t", [row(1)])
    real = store_sync.remote_manifest
    calls = []

    def flip(hf, repo, **kw):  # the second read (right before the manifest commit) sees a migrated store
        calls.append(1)
        if len(calls) == 2:
            write_manifest(hub, {}, 2)
        return real(hf, repo, **kw)
    monkeypatch.setattr(store_sync, "remote_manifest", flip)
    with pytest.raises(SystemExit, match="refusing"):
        store_sync.push("me/store", only=["docs"])
    assert json.loads((hub.d / "manifest.json").read_text())["layout"] == 2


def test_repair_seals_stray_docs_and_restores_layout(root, hub):
    _migrated_store(root, hub)                                               # zz_t:0..2 sealed (legacy part)
    old_code_push_manifest(hub)
    (hub.d / "LAYOUT").unlink()
    stray = "".join(json.dumps(row(i)) + "\n" for i in (1, 2, 5, 6, 6)) + "garbage\n"
    (hub.d / "docs" / "ZZ").mkdir(parents=True, exist_ok=True)
    (hub.d / "docs" / "ZZ" / "zz_t.jsonl").write_text(stray)
    meta = json.loads((hub.d / "manifest.json").read_text())
    meta["files"]["docs/ZZ/zz_t.jsonl"] = {"sha256": sha(hub.d / "docs/ZZ/zz_t.jsonl"), "bytes": len(stray)}
    (hub.d / "manifest.json").write_text(json.dumps(meta))
    store_sync._LAYOUT["remote"] = 1
    rep = store_sync.repair("me/store", force=True)
    assert rep["new"] == 2 and rep["already_sealed"] == 3 and rep["bad"] == 1
    meta = json.loads((hub.d / "manifest.json").read_text())
    assert meta["layout"] == 2 and (hub.d / "LAYOUT").exists() and not (hub.d / "docs" / "ZZ" / "zz_t.jsonl").exists()
    assert sorted(stored_ids(hub.d / "docs-parts")) == [f"zz_t:{i}" for i in (0, 1, 2, 5, 6)]   # all, once each
    assert segments.read_ids(hub.d / "state/ids/ZZ/zz_t.ids") >= {f"zz_t:{i}" for i in (0, 1, 2, 5, 6)}
    lib.write_docs("ZZ", "zz_t", [row(5), row(6), row(8)])                   # the local buffer: only zz_t:8 is new
    assert store_sync.seal("me/store")["lines"] == 1
    assert sorted(stored_ids(hub.d / "docs-parts")) == [f"zz_t:{i}" for i in (0, 1, 2, 5, 6, 8)]
