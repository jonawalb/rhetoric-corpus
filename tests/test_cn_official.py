"""cn_mnd / cn_tao: pager probing, 2023-25 English MND titles, Wayback failure handling, TAO CDX enumeration (synthetic
markup; no network)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "collectors"))

import lib  # noqa: E402

lib.setup_logging = lambda name, level=logging.INFO: logging.getLogger(name)  # keep tests out of state/*.log

import cn_common as cc  # noqa: E402
import cn_mnd  # noqa: E402

BODY = "Synthetic spokesperson answer used only in this test, long enough to pass the length check of the store step."


def test_pager_includes_extra_page():
    root = "http://www.mod.gov.cn/gfbw/xwfyr/lxjzh_246940/index.html"
    html = "<script>createPageHTML(\n'10',\n'1',\n'index',\n'html');</script>"
    lists, _ = cn_mnd.list_links(html, root, "http://www.mod.gov.cn/gfbw/xwfyr/")
    assert root.replace("index.html", "index_10.html") in lists and root.replace("index.html", "index_9.html") in lists


def test_en_title_skips_site_name_h1():
    html = ('<title>Synthetic Headline: Defense Spokesperson - Ministry of National Defense</title>'
            '<meta name="publishdate" content="2022-05-26">'
            '<h1 class="site-name">Ministry of National Defense</h1><h2> Synthetic Headline</h2>'
            f'<div id="article-content"><p>{BODY}</p></div>')
    p = cn_mnd.parse(html, "en")
    assert p["title"] == "Synthetic Headline: Defense Spokesperson" and p["date"] == "2022-05-26"


class FakeSink:
    def __init__(self, ids=()):
        self.ids, self.rows, self.added = set(ids), [], 0

    def has(self, i):
        return i in self.ids

    def add(self, row):
        self.rows.append(row)
        return True

    def flush(self):
        pass


def test_reopen_wayback(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "STATE", tmp_path)
    st = lib.State("cn_mnd")
    have = "http://www.mod.gov.cn/jzhzt/2021-01/28/content_4878000.htm"
    lost = "http://www.mod.gov.cn/jzhzt/2021-01/28/content_4878001.htm"
    live = "http://www.mod.gov.cn/gfbw/xwfyr/fyrthhdjzw/16400000.html"
    st._done = {have, lost, live}
    st["wb_done_mod.gov.cn/jzhzt/"] = True
    sink = FakeSink({lib.make_id(cn_mnd.SOURCE, "wb4878000:zh")})
    cn_mnd.reopen_wayback(st, sink)
    assert st._done == {have, live} and "wb_done_mod.gov.cn/jzhzt/" not in st.data
    st._done.add(lost)
    cn_mnd.reopen_wayback(st, sink)  # once per WB_RETRY
    assert lost in st._done


def test_wayback_network_failure_not_done(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "STATE", tmp_path)
    st = lib.State("cn_mnd")
    st["wb_retry"] = cn_mnd.WB_RETRY
    monkeypatch.setattr(cn_mnd, "WAYBACK", [("zh", "mod.gov.cn/jzhzt/", r"content_\d+\.htm$", False, None)])
    urls = [("http://www.mod.gov.cn/jzhzt/a/content_1000001.htm", "20210101000000"),
            ("http://www.mod.gov.cn/jzhzt/a/content_1000002.htm", "20210101000000")]
    monkeypatch.setattr(cc, "cdx_urls", lambda prefix, rx: iter(urls))

    def fake_fetch(url, lang, wayback_ts=None, live_first=False):
        if url.endswith("1000001.htm"):
            lib.fetch.last = {"status": 0}  # refused connection
            return None
        lib.fetch.last = {"status": 200}
        return {"title": "合成标题", "date": "2021-01-01", "text": BODY, "via": "wayback", "pages": 1}

    monkeypatch.setattr(cn_mnd, "fetch_article", fake_fetch)
    sink = FakeSink()
    cn_mnd.wayback_pass(sink, st)
    assert not st.is_done(urls[0][0]) and st.is_done(urls[1][0])
    assert not st.get("wb_done_mod.gov.cn/jzhzt/")  # a failure keeps the prefix open
    assert sink.rows[0]["id"] == "mnd_cn_live:wb1000002:zh"


def test_wayback_title_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "STATE", tmp_path)
    st = lib.State("cn_mnd")
    st["wb_retry"] = cn_mnd.WB_RETRY
    monkeypatch.setattr(cn_mnd, "WAYBACK", [("en", "eng.chinamil.com.cn/view/", r"content_\d+\.htm$", False,
                                             cn_mnd.SPOKES_EN)])
    urls = [("http://eng.chinamil.com.cn/view/2017-01/01/content_7000001.htm", "20170101000000"),
            ("http://eng.chinamil.com.cn/view/2017-01/01/content_7000002.htm", "20170101000000")]
    monkeypatch.setattr(cc, "cdx_urls", lambda prefix, rx: iter(urls))
    titles = {"7000001": "Synthetic sports news", "7000002": "Defense Ministry spokesperson synthetic remarks"}

    def fake_fetch(url, lang, wayback_ts=None, live_first=False):
        lib.fetch.last = {"status": 200}
        return {"title": titles[url[-11:-4]], "date": "2017-01-01", "text": BODY, "via": "wayback", "pages": 1}

    monkeypatch.setattr(cn_mnd, "fetch_article", fake_fetch)
    sink = FakeSink()
    cn_mnd.wayback_pass(sink, st)
    assert [r["title"] for r in sink.rows] == [titles["7000002"]] and st.get("wb_done_eng.chinamil.com.cn/view/")


# ------------------------------------------------------------------ cn_tao: Wayback CDX enumeration beyond the listing cap
import cn_tao  # noqa: E402


def test_tao_cdx_items(monkeypatch):
    caps = [("http://www.gwytb.gov.cn/xwdt/xwfb/wyly/202101/t20210107_12315774.htm", "20210108000000"),
            ("https://www.gwytb.gov.cn:443/xwdt/xwfb/wyly/202309/t20230927_12570000.htm", "20231001000000"),
            ("http://www.gwytb.gov.cn/xwdt/xwfb/xwfbh/202101/t20210113_12316000.htm", "20210114000000")]
    monkeypatch.setattr(cc, "cdx_urls", lambda prefix, rx: iter(caps))
    assert cn_tao.cdx_items("xwfb/wyly/") == [
        ("https://www.gwytb.gov.cn/xwdt/xwfb/wyly/202309/t20230927_12570000.htm", "2023-09-27"),
        ("https://www.gwytb.gov.cn/xwdt/xwfb/wyly/202101/t20210107_12315774.htm", "2021-01-07")]
