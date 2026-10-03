"""Nightly CI plumbing: store_sync file selection and push safety, ci_collect tail repair, graceful SIGTERM in lib."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ci_collect  # noqa: E402
import store_sync  # noqa: E402


def test_store_selection():
    yes = ["docs/RU/kremlin_en.jsonl", "state/ru_mid.json", "state/by_mfa_en.cookies", "raw/ir_presstv/sitemap-2023-05.xml",
           "index/corpus.sqlite", "index/semantic/semantic.sqlite", "index/semantic/emb/p_000000001.npy", "index/semantic/aggregates/alerts.json",
           "reports/briefs/brief-2026-10-03.md", "logs/ci/2026-10-03/ru_mid.log"]
    no = ["index/corpus.sqlite-wal", "index/semantic/semantic.sqlite-wal", "index/semantic/models/blobs/abc",
          "state/ru_mid.log", "state/.slot_web.archive.org", "state/ru_mid.tmp", "docs/RU/kremlin_en.lock", "raw/ria_ru/1.html",
          "logs/nightly_publish.log", "staging/hf/data/current.json", "release/0.1.0/README.md"]
    assert [p for p in yes if not store_sync.wanted(p)] == []
    assert [p for p in no if store_sync.wanted(p)] == []


def test_diff():
    local = {"a": {"sha256": "1", "bytes": 1}, "b": {"sha256": "2", "bytes": 2}}
    remote = {"a": {"sha256": "1", "bytes": 1}, "b": {"sha256": "x", "bytes": 2}, "c": {"sha256": "3", "bytes": 3}}
    assert store_sync.diff(local, remote) == (["b"], ["c"])


class FakeApi:
    def __init__(self, remote):
        self.remote, self.commits = remote, []

    def create_repo(self, *a, **k):
        pass

    def repo_info(self, *a, **k):
        return type("I", (), {"private": True})()

    def create_commit(self, repo, repo_type, commit_message, operations):
        self.commits.append([getattr(o, "path_in_repo", None) for o in operations])


def test_push_safety(tmp_path, monkeypatch):
    import json
    (tmp_path / "docs" / "RU").mkdir(parents=True)
    (tmp_path / "docs" / "RU" / "a.jsonl").write_text('{"id": "a:1"}\n')
    (tmp_path / "index" / "semantic").mkdir(parents=True)
    (tmp_path / "index" / "semantic" / "x.npz").write_bytes(b"new")
    stored = tmp_path / "stored.jsonl"
    stored.write_text('{"id": "a:0"}\n' * 3)
    remote = {"docs/RU/a.jsonl": {"sha256": "old", "bytes": stored.stat().st_size},  # stored copy is longer
              "index/semantic/old.npy": {"sha256": "o", "bytes": 5}}           # gone locally
    fake = FakeApi(remote)
    marker = tmp_path / ".store_pulled"
    monkeypatch.setattr(store_sync, "ROOT", tmp_path)
    monkeypatch.setattr(store_sync, "MARKER", marker)
    monkeypatch.setattr(store_sync, "api", lambda: fake)
    monkeypatch.setattr(store_sync, "remote_manifest", lambda hf, repo: dict(remote))
    monkeypatch.setattr(store_sync, "_download", lambda repo, p: stored)
    # 1. pulled this run, nobody else wrote: a smaller docs file is refused, the vanished semantic file is deleted
    marker.write_text(json.dumps({"manifest": remote}))
    rep = store_sync.push("me/store")
    assert rep["refused_shrink"] == 1 and rep["delete"] == 1 and rep["merged_docs_files"] == 0
    assert fake.commits[0] == ["index/semantic/x.npz"] and "index/semantic/old.npy" in fake.commits[-1]
    # 2. no pull (laptop): the docs file is merged by id with the stored copy, nothing is deleted
    marker.unlink()
    fake.commits.clear()
    rep = store_sync.push("me/store")
    assert rep["merged_docs_files"] == 1 and rep["delete"] == 0 and "docs/RU/a.jsonl" in fake.commits[0]
    assert (tmp_path / "docs" / "RU" / "a.jsonl").read_text() == '{"id": "a:0"}\n' * 3 + '{"id": "a:1"}\n'


def test_repair_tails(tmp_path, monkeypatch):
    (tmp_path / "docs" / "RU").mkdir(parents=True)
    good, cut = tmp_path / "docs" / "RU" / "good.jsonl", tmp_path / "docs" / "RU" / "cut.jsonl"
    good.write_text('{"id": 1}\n')
    cut.write_text('{"id": 1}\n{"id": 2, "te')
    monkeypatch.setattr(ci_collect, "ROOT", tmp_path)
    assert ci_collect.repair_tails() == ["docs/RU/cut.jsonl"]
    assert cut.read_text() == '{"id": 1}\n' and good.read_text() == '{"id": 1}\n'
    assert ci_collect.doc_lines() == {"RU/cut.jsonl": 1, "RU/good.jsonl": 1}


def test_chains_match_resume_all():
    sh = (ROOT / "scripts" / "resume_all.sh").read_text()
    for c in ci_collect.CHAINS:
        for s in c.steps:
            assert s.args[0] in sh, s.args
    assert len({c.name for c in ci_collect.CHAINS}) == len(ci_collect.CHAINS)


def test_sigterm_waits_for_write_docs(tmp_path):
    """SIGTERM inside lib._critical() is held until the section ends, then exits with 143."""
    code = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(ROOT / 'collectors')!r})
        import lib
        with lib._critical():
            print("in", flush=True)
            time.sleep(1.5)
            open({str(tmp_path / 'done')!r}, "w").write("ok")
        time.sleep(30)
    """)
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "in"
    p.send_signal(signal.SIGTERM)
    t0 = time.time()
    assert p.wait(timeout=20) == 128 + signal.SIGTERM and time.time() - t0 < 10
    assert (tmp_path / "done").read_text() == "ok"


def test_merge_jsonl_unions_by_id(tmp_path):
    local, stored = tmp_path / "l.jsonl", tmp_path / "s.jsonl"
    stored.write_text('{"id": "a:1"}\n{"id": "a:2"}\n{"id": "a:3"}\n')        # store got a:3 from another writer
    local.write_text('{"id": "a:1"}\n{"id": "a:2"}\n{"id": "a:4"}\n{"id": "a:5", "x')
    assert store_sync.merge_jsonl(local, stored) == 1
    assert local.read_text() == '{"id": "a:1"}\n{"id": "a:2"}\n{"id": "a:3"}\n{"id": "a:4"}\n'
