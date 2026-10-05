"""Tests for the paper/report feeds: export_cross_strait_pulse, export_transit_rhetoric, export_taiwan_series.

All fixtures are synthetic (invented sentences, example.org-style URLs); no corpus text is used.
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import export_cross_strait_pulse as P  # noqa: E402
from scripts import export_taiwan_series as TS  # noqa: E402
from scripts import export_transit_rhetoric as TR  # noqa: E402
from scripts.feeds_common import DIMS, line_sentences  # noqa: E402
from scripts.textnorm import sentences  # noqa: E402

# ------------------------------------------------------------------------------------------------ synthetic index
MFA_EN = "\n".join([
    "Guo Jiakun: Minister Example will visit Country A next week. Details will be released in due course.",
    "Outlet One: What is your comment on the Taiwan question raised by Country B?",
    "Guo Jiakun: The one-China principle is a red line. Country B should stop sending wrong signals.",
    "Bloom berg: Will the two leaders meet?",
    "Guo Jiakun: I have nothing to share at the moment.",
    "*****",
    "The following question was raised after the press conference: Any comment on the Canada report?",
    "Guo Jiakun: We noted the report on Canada. Our position is consistent.",
])
MFA_ZH = "\n".join(["郭嘉昆：甲部长下周访问。", "某某社记者：请问对台湾问题有何评论？", "郭嘉昆：一个中国原则是红线。",
                    "乙社记者：两国领导人会见吗？", "郭嘉昆：目前没有可以提供的消息。",
                    "丙社记者：对加拿大报告有何评论？", "郭嘉昆：我们注意到有关报告。"])
MND_ZH = "\n".join([
    "2026年9月国防部例行记者会文字实录",
    "时间：2026年9月24日15:00—16:00",
    "蒋斌： 大家下午好。我没有主动发布的信息，请大家提问。",
    "9月24日下午，国防部举行例行记者会，国防部新闻发言人蒋斌大校答记者问。某某 摄",
    "记者：据报道，某国军舰过航台湾海峡。请问有何评论？",
    "蒋斌： 我们敦促有关方面恪守一个中国原则，不要向“台独”分裂势力发出错误信号。",
    "记者：请介绍演习情况。",
    "蒋斌： 演习按计划进行。",
    "双方将继续加强合作。",
])
MND_EN = "\n".join([
    "Senior Colonel Jiang Bin, spokesperson, answered questions at a regular press conference.",
    "(The following English text is for reference. In case of any divergence, the Chinese text shall prevail.)",
    "Jiang Bin: Good afternoon. I have no information to announce. The floor is open.",
    "Journalist: A foreign warship transited the Taiwan Strait. What is your comment?",
    "Jiang Bin: We urge the relevant side to abide by the one-China principle.",
    "Journalist: Please introduce the exercise.",
    "Jiang Bin: The exercise is proceeding as planned.",
    "The two sides will continue to strengthen cooperation.",
])
MND_EXTRACT = "\n".join(["记者：据报道，某国军舰过航台湾海峡。请问有何评论？", "蒋斌： 我们敦促有关方面恪守一个中国原则。"])
TAO_PC = "\n".join([
    "9月2日上午10时，国务院台办举行例行新闻发布会。",
    "张晗： 各位记者朋友，大家上午好。下面请大家提问。",
    "某某报记者： 请问对民进党当局有关言论有何评论？",
    "张晗： 民进党当局的“台独”挑衅注定失败。",
    "我们坚持九二共识。",
    "某某台记者： 请介绍两岸交流情况。",
    "张晗： 两岸交流不断深化。",
])
TAO_ST = "\n".join(["在9月2日国务院台办新闻发布会上，有记者提问，请问对此有何评论？",
                    "国务院台办发言人张晗应询表示，“台独”是绝路。", "张晗表示，我们坚持一个中国原则。"])
SEP = "2026-09-0"


def _docs():
    """(id, source, outlet, lang, kind, date, url, title, speaker, text, sample)"""
    return [
        ("mfa_cn_live:1:en", "mfa_cn_live", "official", "en", "briefing", "2026-09-01",
         "https://www.fmprc.gov.cn/eng/xw/fyrbt/lxjzh/202609/t1.html",
         "Foreign Ministry Spokesperson Guo Jiakun's Regular Press Conference on September 1", "Guo Jiakun", MFA_EN, None),
        ("mfa_cn_archive:1:en", "mfa_cn_archive", "official", "en", "briefing", "2026-09-01",
         "https://www.fmprc.gov.cn/eng/xw/fyrbt/lxjzh/202609/t1.html", "duplicate of the live copy", None, MFA_EN, None),
        ("mfa_cn_live:1:zh", "mfa_cn_live", "official", "zh", "briefing", "2026-09-01",
         "https://www.mfa.gov.cn/web/fyrbt_673021/jzhsl_673025/202609/t1.shtml", "例行记者会", "郭嘉昆", MFA_ZH, None),
        ("mfa_cn_live:2:en", "mfa_cn_live", "official", "en", "briefing", "2026-08-31",
         "https://www.fmprc.gov.cn/eng/xw/fyrbt/lxjzh/202608/t2.html",
         "Foreign Ministry Spokesperson Lin Jian's Regular Press Conference", "Lin Jian",
         "Outlet One: Question about Japan?\nLin Jian: Japan should reflect on history.", None),
        ("mnd_cn_live:1:zh", "mnd_cn_live", "official", "zh", "briefing", "2026-09-24",
         "http://www.mod.gov.cn/gfbw/xwfyr/lxjzh_246940/1.html", "2026年9月国防部例行记者会文字实录", "蒋斌", MND_ZH, None),
        ("mnd_cn_live:2:zh", "mnd_cn_live", "official", "zh", "briefing", "2026-09-24",
         "http://www.mod.gov.cn/gfbw/xwfyr/lxjzh_246940/2.html", "国防部：某国军舰过航", "蒋斌", MND_EXTRACT, None),
        ("mnd_cn_live:3:en", "mnd_cn_live", "official", "en", "briefing", "2026-09-24",
         "http://eng.mod.gov.cn/2025xb/P/3.html", "Regular Press Conference", "Jiang Bin", MND_EN, None),
        ("mnd_cn_live:4:zh", "mnd_cn_live", "official", "zh", "news", "2026-09-04",
         "http://www.mod.gov.cn/gfbw/xwfyr/yzxwfb/4.html", "国防部：某条消息", "蒋斌",
         "记者：请介绍有关情况。\n蒋斌： 有关活动将在加拿大举行。", None),
        ("tao_cn_live:1:zh", "tao_cn_live", "official", "zh", "briefing", "2026-09-02",
         "https://www.gwytb.gov.cn/xwdt/xwfb/xwfbh/202609/t1.htm", "国务院台办新闻发布会辑录（2026-09-02）", "张晗", TAO_PC, None),
        ("tao_cn_live:2:zh", "tao_cn_live", "official", "zh", "statement", "2026-09-02",
         "https://www.gwytb.gov.cn/xwdt/xwfb/wyly/202609/t2.htm", "国台办：某声明", "张晗", TAO_ST, None),
        ("tao_cn_live:3:zh", "tao_cn_live", "official", "zh", "statement", "2026-09-05",
         "https://www.gwytb.gov.cn/xwdt/xwfb/wyly/202609/t3.htm", "国台办：另一声明", "张晗", TAO_ST, None),
        ("tao_cn_live:4:zh", "tao_cn_live", "official", "zh", "news", "2026-09-06",
         "https://www.gwytb.gov.cn/xwdt/newsb/202609/t4.htm", "交流活动举行", None, "两岸交流活动在某地举行。", None),
        ("cn_xinhua:1:en", "cn_xinhua", "state_media", "en", "article", "2026-09-03",
         "https://example.org/xinhua/1", "Taiwan story", None, "Taiwan is part of China. Unrelated sentence.", None),
        ("cn_xinhua:2:en", "cn_xinhua", "state_media", "en", "article", "2026-09-03",
         "https://example.org/xinhua/2", "Other story", None, "A story about trade.", None),
        ("cn_xinhua:3:en", "cn_xinhua", "state_media", "en", "article", "2026-09-03",
         "https://example.org/xinhua/3", "Seed story", None, "Taiwan keyword sample.", "seed"),
        ("prc_statemedia:1:en", "prc_statemedia", "state_media", "en", "article", "2026-09-03",
         "https://example.org/import/1", "Imported", None, "Taiwan imported copy.", None),
    ]


@pytest.fixture()
def index(tmp_path: Path) -> Path:
    idx = tmp_path / "index"
    (idx / "semantic" / "aggregates").mkdir(parents=True)
    cc = sqlite3.connect(idx / "corpus.sqlite")
    cc.executescript("""
        CREATE TABLE docs(rowid INTEGER PRIMARY KEY, id TEXT UNIQUE, file TEXT, country TEXT, source TEXT, outlet TEXT,
          org TEXT, lang TEXT, date TEXT, url TEXT, title TEXT, speaker TEXT, kind TEXT, via TEXT, text TEXT,
          nchars INTEGER, sample TEXT, wayback TEXT);
        CREATE TABLE sentences(doc INTEGER NOT NULL, idx INTEGER NOT NULL, text TEXT NOT NULL, PRIMARY KEY(doc, idx));
    """)
    sc = sqlite3.connect(idx / "semantic" / "semantic.sqlite")
    sc.executescript("""
        CREATE TABLE docs(doc_id TEXT PRIMARY KEY, crow INTEGER, file TEXT, country TEXT, source TEXT, outlet TEXT,
          org TEXT, lang TEXT, date TEXT, kind TEXT, url TEXT, title TEXT, sample TEXT, present INTEGER DEFAULT 1);
        CREATE TABLE sent_scores(doc_id TEXT, idx INTEGER, crc INTEGER, hostility INTEGER, threat INTEGER,
          conciliation INTEGER, grievance INTEGER, escalation INTEGER, deescalation INTEGER, PRIMARY KEY(doc_id, idx));
        CREATE TABLE mentions(doc_id TEXT, idx INTEGER, target TEXT, pat TEXT, self INTEGER,
          PRIMARY KEY(doc_id, idx, target, pat));
        CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
        INSERT INTO meta VALUES ('tone_train', '{"version": "test"}');
    """)
    for rowid, (i, src, outlet, lang, kind, d, url, title, spk, text, sample) in enumerate(_docs(), 1):
        cc.execute("INSERT INTO docs(rowid, id, country, source, outlet, lang, kind, date, url, title, speaker, text, "
                   "sample, via) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (rowid, i, "CN", src, outlet, lang, kind, d, url, title, spk, text, sample,
                    "export" if src == "prc_statemedia" else "direct"))
        sc.execute("INSERT INTO docs(doc_id, crow, country, source, outlet, lang, date, kind, url, title, sample) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (i, rowid, "CN", src, outlet, lang, d, kind, url, title, sample))
        for k, s in enumerate(sentences(text)):
            cc.execute("INSERT INTO sentences VALUES (?,?,?)", (rowid, k, s))
            # every sentence scored: hostility 0.5 when it names Taiwan / 台独, else 0.1
            hot = "Taiwan" in s or "台独" in s
            sc.execute("INSERT INTO sent_scores VALUES (?,?,0,?,?,?,?,?,?)",
                       (i, k, 500 if hot else 100, 200, 300, 0, 50, 100))
            if "Taiwan" in s or "台湾" in s or "台独" in s:
                sc.execute("INSERT INTO mentions VALUES (?,?,?,?,0)", (i, k, "TAIWAN", "p1"))
                sc.execute("INSERT INTO mentions VALUES (?,?,?,?,0)", (i, k, "TAIWAN", "p2"))   # 2 patterns, 1 sentence
    cc.commit()
    sc.commit()
    cc.close()
    sc.close()
    return idx


def read(path: Path):
    with path.open(encoding="utf-8-sig") as fh:
        r = csv.DictReader(fh)
        return r.fieldnames, list(r)


# ------------------------------------------------------------------------------------------------ splitting
def test_line_sentences_matches_index_builder():
    text = "First sentence. Second one!\n\nThird line here.\n第一句。第二句。"
    flat = [s for line, a, b in line_sentences(text) for s in sentences(line)]
    assert flat == sentences(text)
    ranges = [(a, b) for _, a, b in line_sentences(text)]
    assert ranges[-1][1] == len(sentences(text))


def test_mfa_split_matches_tsm_rule():
    pairs = P.split_turns(line_sentences(MFA_EN), P.mfa_classifier({"Guo Jiakun"}), False)
    assert [p.asker for p in pairs] == [P.ANNOUNCEMENT, "Outlet One", "Bloomberg",
                                       "The following question was raised after the press conference"]
    assert pairs[1].answer.startswith("The one-China principle")
    assert not pairs[0].q and pairs[2].q


def test_mnd_split_merges_and_drops_noise():
    zh = P.split_turns(line_sentences(MND_ZH), P.mnd_classifier("zh"), True)
    en = P.split_turns(line_sentences(MND_EN), P.mnd_classifier("en"), True)
    assert len(zh) == len(en) == 3
    assert not zh[0].q and "没有主动发布" in zh[0].answer
    assert zh[2].answer.endswith("双方将继续加强合作。")        # unlabelled continuation joins the answer
    assert "摄" not in "".join(p.answer for p in zh)
    assert en[2].answer.endswith("strengthen cooperation.")


def test_tao_split():
    pairs = P.split_turns(line_sentences(TAO_PC), P.tao_classify, True)
    assert [p.asker for p in pairs] == [P.ANNOUNCEMENT, "某某报记者", "某某台记者"]
    assert "九二共识" in pairs[1].answer


# ------------------------------------------------------------------------------------------------ pulse export
def test_pulse_export_formats_and_counts(index: Path, tmp_path: Path):
    out = tmp_path / "feed_v1"
    res = P.export(index, "2026-09", out)
    f, mfa = read(out / "mfa_2026-09.csv")
    assert tuple(f) == P.MFA_FIELDS
    assert len(mfa) == 4                       # archive duplicate dropped, zh not a row
    assert mfa[2]["Asker"] == "Bloomberg"      # outlet alias
    assert "Taiwan" in mfa[1]["Country"] and mfa[0]["Asker"] == P.ANNOUNCEMENT
    f, mnd = read(out / "mnd_2026-09.csv")
    assert tuple(f) == P.MND_FIELDS
    # full transcript (3) supersedes the same-day extract; the 09-04 written-briefing item has no transcript (1)
    assert len(mnd) == 4
    full = [r for r in mnd if r["Date"] == "2026-09-24"]
    assert all(r["Source"].startswith("Official English") for r in full)
    assert full[1]["Answer_verbatim"].startswith("We urge") and "一个中国原则" in full[1]["Answer_verbatim_zh"]
    f, tao = read(out / "tao_2026-09_source_zh.csv")
    assert tuple(f) == P.TAO_FIELDS
    assert len(tao) == 5 and all(r["Answer_EN_MT"] == "" for r in tao)
    assert tao[0]["Asker"] == "Not specified (announcement/opening)"
    _, summ = read(out / "stream_summary_2026-09.csv")
    val = {(r["stream"], r["month"], r["metric"], r["key"]): r["value"] for r in summ}
    assert val[("MFA", "2026-09", "exchanges", "")] == "3"
    assert val[("MFA", "2026-09", "opening_announcements", "")] == "1"
    assert val[("MFA", "2026-08", "exchanges", "")] == "1"
    assert val[("TAO", "2026-09", "releases", "")] == "3"
    assert val[("TAO", "2026-09", "off_cycle_releases", "")] == "1"     # 09-05 statement, no press conference
    assert val[("TAO", "2026-09", "other_tao_docs_not_counted", "")] == "1"
    assert val[("TAO", "2026-09", "phrase_rows", "Taiwan independence")] == "3"
    _, quotes = read(out / "top_quotes_2026-09.csv")
    assert quotes and all(len(q["quote"]) <= 300 for q in quotes)
    assert not any(q["quote"].startswith(("Guo Jiakun:", "蒋斌：", "张晗：")) for q in quotes)
    assert not any(q["quote"].endswith(("?", "？")) for q in quotes)
    frag = (out / "dashboard_fragment_2026-09.md").read_text(encoding="utf-8")
    assert "| MFA | 3 exchanges across 1 briefing days" in frag and "> — " in frag
    assert "NOT yet validated" in (out / "COVERAGE.md").read_text(encoding="utf-8")
    assert res["files"]["mfa_2026-09.csv"] == 4
    with pytest.raises(SystemExit):              # never overwrites an existing output folder
        P.export(index, "2026-09", out)


# ------------------------------------------------------------------------------------------------ transits
def test_group_transits():
    rows = [dict(date="2024-10-20", name="USS A", hull="1", country="USA"),
            dict(date="2024-10-20", name="HMCS B", hull="2", country="Canada"),
            dict(date="2025-02-10", name="USS C", hull="3", country="USA"),
            dict(date="2025-02-10", name="USNS D", hull="4", country="USA"),
            dict(date="2023-01-01", name="old", hull="", country="Japan")]
    t = TR.group_transits(rows, "2024-01-01")
    assert [(x["transit_id"], x["transit_group"]) for x in t] == [
        ("2024-10-20_CANADA", "joint"), ("2024-10-20_US", "joint"), ("2025-02-10_US", "us")]
    assert t[2]["ships"] == "USS C (3); USNS D (4)"
    with pytest.raises(SystemExit):
        TR.group_transits([dict(date="2025-01-01", name="X", hull="", country="Atlantis")], "2024-01-01")


def test_supplement_patterns_avoid_short_forms():
    gaz = TR.gazetteer()
    assert "CANADA" in TR.mentions(gaz, "我们注意到加拿大的报告。", "zh")
    assert "CANADA" not in TR.mentions(gaz, "参加方要增加方面的合作。", "zh")
    assert "NEW_ZEALAND" in TR.mentions(gaz, "New Zealand ships sailed.", "en")


def test_transit_export(index: Path, tmp_path: Path):
    transits = TR.group_transits([dict(date="2026-09-01", name="HMCS X", hull="9", country="Canada"),
                                  dict(date="2026-09-20", name="USS Y", hull="8", country="USA")], "2024-01-01")
    args = SimpleNamespace(from_tsm_js=None, transits="synthetic.csv", since="2024-01-01")
    res = TR.export(index, transits, tmp_path / "tr", args)
    assert res["rows"] == 2 * 3 * 22
    _, rows = read(tmp_path / "tr" / "transit_rhetoric_daily.csv")
    mfa0 = next(r for r in rows if r["transit_id"] == "2026-09-01_CANADA" and r["stream"] == "MFA"
                and r["day_offset"] == "0")
    # the Canada question line is a reporter's question and is excluded; the answer names Canada once
    assert mfa0["docs"] == "1" and mfa0["mention_sentences"] == "1" and mfa0["scored_mentions"] == "1"
    assert mfa0["hostility"] == "0.1" and mfa0["overlaps_other_transit"] == "0"
    mnd3 = next(r for r in rows if r["transit_id"] == "2026-09-01_CANADA" and r["stream"] == "MND"
                and r["day_offset"] == "3")
    assert mnd3["mention_sentences"] == "1"
    us = [r for r in rows if r["transit_id"] == "2026-09-20_US"]
    assert {r["overlaps_other_transit"] for r in us if r["day_offset"] == "-7"} == {"1"}   # 09-13 is in 09-01's window
    assert all(r["base_days"] == "30" for r in rows)
    _, cb = read(tmp_path / "tr" / "transit_rhetoric_codebook.csv")
    assert {c["variable"] for c in cb} == set(TR.ROW_FIELDS)
    assert "NOT yet validated" in (tmp_path / "tr" / "README.txt").read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------------------ taiwan series
def test_taiwan_series(index: Path, tmp_path: Path):
    TS.export(index, tmp_path / "tw")
    f, rows = read(tmp_path / "tw" / "taiwan_rhetoric_monthly.csv")
    assert tuple(f) == TS.FIELDS
    x = next(r for r in rows if r["level"] == "source" and r["source"] == "cn_xinhua" and r["month"] == "2026-09")
    assert x["docs"] == "2"                                   # seed sample excluded
    assert x["taiwan_docs"] == "1" and x["taiwan_sentences"] == "1" and x["sent_hostility"] == "0.5"
    imp = next(r for r in rows if r["source"] == "prc_statemedia")
    assert imp["imported"] == "1"
    pooled = next(r for r in rows if r["level"] == "pooled" and r["outlet"] == "state_media" and r["lang"] == "en"
                  and r["month"] == "2026-09")
    assert pooled["docs"] == "2"                              # imported copy left out of pooled rows
    _, cb = read(tmp_path / "tw" / "taiwan_rhetoric_codebook.csv")
    assert {c["variable"] for c in cb} == set(TS.FIELDS)
    prov = json.loads((tmp_path / "tw" / "provenance.json").read_text(encoding="utf-8"))
    assert "NOT yet validated" in prov["validation_status"]
    assert set(DIMS) <= {k.split("_", 1)[1] for k in TS.FIELDS if k.startswith("sent_")}
