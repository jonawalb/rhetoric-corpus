"""China Daily (English www.chinadaily.com.cn; Chinese cn/china/world.chinadaily.com.cn) via section list pages.

Source name: cn_chinadaily (org ChinaDaily, outlet state_media). One document per article per language; English
and Chinese articles are different documents (China Daily's zh site is not a translation of the en site).

Listing: every section has <section>/page_N.html (N = 1..max; English sections go back to ~2014, Chinese
sections show 40 pages). Article URLs: //<host>/a/YYYYMM/DD/WS<24 hex>.html; doc id = cn_chinadaily:<WS id>.
Only page 1 of multi-page articles is stored (field `multipage: true` when a page_2 link exists).
Chinese reprints carry `repost_from` (the 来源 outlet, e.g. 人民日报) and stay in this source.

Sections: English = seed list + sub-sections discovered on the section front pages (www host only);
Chinese = fixed seed list. robots.txt: none on these hosts (404). lib default delay (4 s per host).

Run per language (separate processes, separate state):
    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_chinadaily.py --lang en [--follow]
    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_chinadaily.py --lang zh [--follow]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
import cn_common as cc  # noqa: E402

SOURCE = "cn_chinadaily"
DELAY = 4
NEW_EVERY_S = 2 * 3600
NEW_PAGES = 3          # pages per section re-read on each new pass
EN_TOP = ["china", "world", "opinion", "business", "culture", "life", "travel", "sports", "regional"]
EN_SEED = ["china/governmentandpolicy", "china/crossstraits", "china/society", "china/scitech", "china/coverstory",
           "china/newsmaker", "china/education", "china/environment", "china/59b8d010a3108c54ed7dfc27",
           "opinion/editorials", "opinion/columnists", "opinion/globalviews", "opinion/fromthechinesepress",
           "opinion/opinionline", "opinion/readers", "world/asia_pacific", "world/america", "world/europe",
           "world/middle_east", "world/africa", "world/cn_eu", "world/worldwatch", "business/economy",
           "business/companies", "business/tech", "business/money", "business/biz_industries", "culture/art",
           "culture/heritage", "culture/culturalexchange", "life/people", "travel/news", "sports/china", "regional"]
ZH_SEED = ["https://china.chinadaily.com.cn/5bd5639ca3101a87ca8ff636", "https://china.chinadaily.com.cn/5bd5639ca3101a87ca8ff634",
           "https://world.chinadaily.com.cn/5bd55927a3101a87ca8ff618", "https://cn.chinadaily.com.cn/yuanchuang",
           "https://china.chinadaily.com.cn/theory", "https://cn.chinadaily.com.cn/gtx",
           "https://caijing.chinadaily.com.cn/5b7620c4a310030f813cf452", "https://china.chinadaily.com.cn/xuexi"]
ART = re.compile(r'(?:https?:)?//([a-z]+)\.chinadaily\.com\.cn/a/(\d{6})/(\d{2})/(WS[0-9a-f]{24})\.html')


def parse(html: str, lang: str) -> Optional[Dict]:
    body = cc.balanced_div(html, 'id="Content"')
    if body is None:
        return None
    body = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", " ", body, flags=re.S | re.I)
    text = cc.paragraphs(body)
    title = cc.title_tag(html)
    title = re.sub(r"\s*-\s*(Chinadaily\.com\.cn|中国日报网|China Daily)\s*$", "", title).strip()
    date = cc.first_date(cc.meta(html, "publishdate") or "")
    if not date:
        m = re.search(r"(?:Updated|更新时间)[:：]\s*(\d{4}-\d{2}-\d{2})", html)
        date = m.group(1) if m else None
    author = None
    m = re.search(r'class="info_l">(.*?)</', html, re.S)
    info = lib.clean_html(m.group(1)) if m else ""
    am = re.search(r"By\s+(.+?)\s*\|", info)
    if am:
        author = am.group(1).strip()
    repost = None
    if lang == "zh":
        rm = re.search(r"来源[:：]\s*([^\s<|]{2,20})", html)
        if rm and "中国日报" not in rm.group(1) and "XXX" not in rm.group(1):
            repost = rm.group(1)
    return {"title": title, "date": date, "speaker": author, "text": text, "repost_from": repost,
            "multipage": bool(re.search(r'href="[^"]*WS[0-9a-f]{24}_2\.html"', html))}


class CD:
    def __init__(self, lang: str) -> None:
        self.lang = lang
        self.st = lib.State(f"cn_chinadaily_{lang}")   # sections: {url: {"max": int, "next": int}}
        self.sink = cc.Sink(SOURCE, self.st, flush_every=10)
        self.secs: Dict[str, Dict] = self.st.get("sections") or {}

    def section_urls(self) -> List[str]:
        if self.lang == "zh":
            urls = list(ZH_SEED)
        else:
            urls = [f"https://www.chinadaily.com.cn/{s}" for s in EN_SEED]
            for top in EN_TOP:
                html = cc.get(f"https://www.chinadaily.com.cn/{top}", DELAY) or ""
                for path in re.findall(r'href="(?:https:)?//www\.chinadaily\.com\.cn/([a-z_]+/[0-9a-z_]+)/?"', html):
                    if path.split("/")[0] in EN_TOP and not path.endswith(("video", "photo", "bizvideo")):
                        urls.append(f"https://www.chinadaily.com.cn/{path}")
        return list(dict.fromkeys(urls))

    def page(self, sec: str, n: int) -> List[str]:
        url = f"{sec}/page_{n}.html"
        html = cc.get(url, DELAY, retries=2) or ""
        if n == 1:
            nums = [int(x) for x in re.findall(rf'{re.escape(sec.split("//", 1)[1])}/page_(\d+)\.html', html)]
            info = self.secs.setdefault(sec, {"next": 2})
            info["max"] = max(nums or [1])
        out = []
        for m in ART.finditer(html):
            out.append(f"https://{m.group(1)}.chinadaily.com.cn/a/{m.group(2)}/{m.group(3)}/{m.group(4)}.html")
        return list(dict.fromkeys(out))

    def article(self, url: str) -> None:
        wsid = re.search(r"(WS[0-9a-f]{24})", url).group(1)
        did = f"{SOURCE}:{wsid}"
        if self.sink.has(did):
            return
        html = cc.get(url, DELAY, retries=2)
        if not html:
            return
        p = parse(html, self.lang)
        if not p or len(p["text"]) < 100:
            return
        m = re.search(r"/a/(\d{4})(\d{2})/(\d{2})/", url)
        date = p["date"] or (cc.ymd(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None)
        if not date:
            return
        kind = "article"
        low = (p["title"] + " " + url).lower()
        if "opinion/editorials" in low or p["title"].startswith(("Editorial", "社论")):
            kind = "editorial"
        row = {"id": did, "country": "CN", "source": SOURCE, "outlet": "state_media", "org": "ChinaDaily",
               "lang": self.lang, "date": date, "url": url, "title": p["title"], "speaker": p["speaker"],
               "kind": kind, "text": p["text"], "via": lib.fetch.last.get("via") or "direct"}
        if p["repost_from"]:
            row["repost_from"] = p["repost_from"]
        if p["multipage"]:
            row["multipage"] = True
        self.sink.add(row)

    def save(self) -> None:
        self.st["sections"] = self.secs
        self.sink.flush()

    def new_pass(self) -> int:
        before = self.sink.added
        for sec in self.section_urls():
            for n in range(1, NEW_PAGES + 1):
                urls = self.page(sec, n)
                fresh = [u for u in urls if not self.sink.has(f"{SOURCE}:{re.search(r'(WS[0-9a-f]{24})', u).group(1)}")]
                for u in fresh:
                    self.article(u)
                if not fresh or n >= self.secs.get(sec, {}).get("max", 1):
                    break
            self.save()
        return self.sink.added - before

    def backfill(self, seconds: int) -> int:
        """Round-robin over sections, a few list pages each, deepening each section's `next` cursor."""
        before = self.sink.added
        end = time.time() + seconds
        while time.time() < end:
            open_secs = [s for s, i in self.secs.items() if i.get("next", 2) <= i.get("max", 1)]
            if not open_secs:
                time.sleep(max(0.0, min(end - time.time(), NEW_EVERY_S)))
                break
            for sec in open_secs:
                info = self.secs[sec]
                for _ in range(3):
                    if info["next"] > info.get("max", 1) or time.time() > end:
                        break
                    for u in self.page(sec, info["next"]):
                        self.article(u)
                    info["next"] += 1
                self.save()
                if time.time() > end:
                    break
        self.save()
        return self.sink.added - before


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=("en", "zh"), required=True)
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--test", nargs="*", help="parse these article URLs and print")
    a = ap.parse_args()
    lib.setup_logging(f"cn_chinadaily_{a.lang}")
    if a.test is not None:
        for u in a.test:
            p = parse(cc.get(u, DELAY) or "", a.lang)
            print(u, {k: (v[:200] if isinstance(v, str) else v) for k, v in (p or {}).items()})
        return
    cd = CD(a.lang)
    log = lib.logger
    log.info("new pass: %d", cd.new_pass())
    while True:
        log.info("backfill chunk: %d", cd.backfill(NEW_EVERY_S))
        if not a.follow:
            break
        log.info("new pass: %d", cd.new_pass())


if __name__ == "__main__":
    main()
