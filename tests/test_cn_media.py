"""Parser and URL tests for collectors/cn_media.py. Fixtures in tests/fixtures/cn/ are real pages fetched
2026-10-04 (scripts/styles stripped; texts unchanged)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "collectors"))

import lib  # noqa: E402

lib.setup_logging = lambda name, level=logging.INFO: logging.getLogger(name)  # keep tests out of state/*.log

import cn_media as m  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "cn"


def fx(name: str) -> str:
    return (FIX / name).read_text("utf-8")


def test_decode_gbk_and_utf8():
    gbk = '<meta http-equiv="Content-Type" content="text/html; charset=gb2312" /><p>中新网</p>'.encode("gb18030")
    assert "中新网" in m.decode(gbk)
    assert "央视" in m.decode('<meta charset="utf-8"><p>央视</p>'.encode("utf-8"))


def test_chinanews_templates():
    p = m.parse_chinanews(fx("chinanews_2024.html"), "https://www.chinanews.com.cn/gj/2024/05-01/10210211.shtml")
    assert p["date"] == "2024-05-01" and p["title"].startswith("南非知名旅游胜地爆发山火")
    assert p["text"].startswith("中新网 约翰内斯堡5月1日电") and "编辑" not in p["text"] and p["speaker"] == "李润泽"
    p = m.parse_chinanews(fx("chinanews_2010.html"), "https://www.chinanews.com.cn/gn/news/2010/05-01/2258860.shtml")
    assert p["date"] == "2010-05-01" and p["title"] == "新疆中哈界河发生洪水 七千多人参加抢险" and "7000多人" in p["text"]
    p = m.parse_chinanews(fx("chinanews_2008.html"), "https://www.chinanews.com.cn/gn/news/2008/08-01/1333184.shtml")
    assert p["date"] == "2008-08-01" and "上海市政府新闻发言人1日" in p["text"] and "评论" not in p["text"]


def test_ecns_templates():
    p = m.parse_ecns(fx("ecns_2026.html"), "https://www.ecns.cn/china/politics/2026-10-03/detail-ihfkteqw7941628.shtml")
    assert p["date"] == "2026-10-03" and p["credit"] == "Xinhua" and p["section"] == "china/politics"
    assert p["text"].startswith("Honoring the glory") and "facebook" not in p["text"].lower()
    p = m.parse_ecns(fx("ecns_2018.html"), "https://www.ecns.cn/2018/05-03/301274.shtml")
    assert p["date"] == "2018-05-03" and p["title"].startswith("S Korean president") and p["section"] is None


def test_cctv_templates():
    p = m.parse_cctv(fx("cctv_news_2026.html"), "https://news.cctv.com/2026/10/04/ARTICmWGVh8QtuQ7rJqTkYge261004.shtml")
    assert p["date"] == "2026-10-04" and p["credit"] == "央视新闻" and "普天间基地" in p["text"]  # JS-string body
    p = m.parse_cctv(fx("cctv_en_2026.html"), "https://english.cctv.com/2026/09/30/ARTI5yGbcO1icRAvHHLd5he1260930.shtml")
    assert p["title"].startswith("China's manufacturing PMI") and p["text"].startswith("BEIJING, Sept. 30 (Xinhua)")
    p = m.parse_cctv(fx("xwlb_2024.html"), "https://tv.cctv.com/2024/05/01/VIDE6SPTFNc5zsxS7IfhtwTI240501.shtml")
    assert p["date"] == "2024-05-01" and p["title"].startswith("[视频]《求是》杂志") and "（新闻联播）" in p["text"]


def test_huanqiu():
    p = m.parse_huanqiu(fx("huanqiu_2026.html"), "https://china.huanqiu.com/article/4TTaoSTxMLp")
    assert p["date"] == "2026-10-04" and p["title"] == "一见·读懂总书记的丰收祝愿"
    assert p["section"] == "/e3pmh1nnq/e3pn60p0i" and p["host"] == "china.huanqiu.com" and len(p["text"]) > 1000


def test_guancha_article_and_redirect_stub():
    p = m.parse_guancha(fx("guancha_2026.html"), "https://www.guancha.cn/ZhengZhi/2026_10_04_903180.shtml")
    assert p["date"] == "2026-10-04" and p["title"] == "中国人民大学一校友，捐5.03亿" and p["speaker"] == "陆远声"
    assert p["text"].startswith("中国人民大学10月4日发布消息")
    stub = m.parse_guancha(fx("guancha_stub.html"), "https://www.guancha.cn/ZhengZhi/2026_10_04_903192.shtml")
    assert stub["text"] == ""  # link-out page (redirects to another outlet): no text -> not stored


def test_url_rules():
    r = m.Runner.__new__(m.Runner)
    cn = m.ChinaNews(r)
    assert cn.norm("http://www.chinanews.com/gn/2024/05-01/1.shtml") == "https://www.chinanews.com.cn/gn/2024/05-01/1.shtml"
    assert cn.art_re.search("https://www.chinanews.com.cn/ty/ty-nba/news/2010/05-01/2258862.shtml")
    assert cn.key("https://www.chinanews.com.cn/gn/news/2010/05-01/2258860.shtml") == "201005-01-2258860"
    e = m.Ecns(r)
    assert e.art_re.search(e.norm("//www.ecns.cn/cns-wire/2026-09-30/detail-ihfkteqw7937693.shtml"))
    assert e.key("https://www.ecns.cn/2018/05-03/301274.shtml") == "201805-03301274"
    h = m.Huanqiu(r)
    assert h.art_re.search("https://china.huanqiu.com/article/2016-05/8900000.html")
    assert h.key("https://world.huanqiu.com/article/9CaKrnJVle0") == "9CaKrnJVle0"
    g = m.Guancha(r)
    u = g.norm("/GuoJi·ZhanLue/2026_10_04_903191.shtml?s=x".join(["https://www.guancha.cn", ""]))
    assert g.art_re.search(u) and g.key(u) == "903191"
