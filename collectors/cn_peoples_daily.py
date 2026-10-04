"""People's Daily (人民日报): the printed paper's e-edition (zh) and People's Daily Online English (en).

Source name: cn_peoples_daily (docs/CN/cn_peoples_daily.jsonl), org PeoplesDaily, outlet state_media.

  epaper  paper.people.com.cn, every page (版) of every day, every article. Two live layouts:
            2024-12-01 -> now   rmrb/pc/layout/YYYYMM/DD/node_NN.html  -> rmrb/pc/content/YYYYMM/DD/content_N.html
            2023-01-01 -> 2024-11-30  rmrb/html/YYYY-MM/DD/nbs.D110000renmrb_NN.htm -> nw.D110000renmrb_YYYYMMDD_K-NN.htm
          Older days answer HTTP 403 (not open online); with --wayback-before they are read from Wayback copies of the
          same URLs (only where captured). robots.txt: none (404). 6 s between requests.
          Newest day first; the last 3 days are re-checked every pass (late pages). kind: editorial for 社论 /
          本报评论员, commentary for signed commentary columns (钟声, 任仲平, 国纪平, 人民论坛, 人民时评, 评论员观察, ...).
          Extra fields: page ("01版：要闻"), pretitle / subtitle when present, author.
  en      en.people.cn section list pages (index.html, index2.html, ... as far as the site serves them) and
          Wayback CDX month lists of en.people.cn / english.people.com.cn /n3/ URLs, fetched live first.
          robots.txt disallows only /rss_mobile/ and *.php. 5 s.
www.people.com.cn (Crawl-delay 120) is not used.

    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_peoples_daily.py epaper --follow [--until 2023-01-01]
    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_peoples_daily.py en --follow
    ... epaper --from 2026-10-03 --until 2026-10-01      (single pass over a date range, newest first)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
import cn_common as cc  # noqa: E402

SOURCE = "cn_peoples_daily"
PAPER = "https://paper.people.com.cn/rmrb/"
NEW_FROM = date(2024, 12, 1)
OLD_FROM = date(2023, 1, 1)
EP_DELAY = 6.0
EN_DELAY = 5.0
log = lib.setup_logging("cn_peoples_daily")

EDITORIAL = re.compile(r"社论|本报评论员|人民日报评论员")
COMMENTARY = re.compile(r"钟声|任仲平|国纪平|人民论坛|人民时评|评论员观察|仲音|宣言|望海楼|和音|今日谈|人民观点")


# ----------------------------------------------------------------------------------------- epaper
def beijing_today() -> date:
    """Today's date in Beijing (UTC+8): the paper's dateline, a day ahead of US evenings."""
    from datetime import datetime, timezone
    return (datetime.now(timezone.utc) + timedelta(hours=8)).date()


def ep_layout_urls(d: date) -> Tuple[str, str]:
    """(first page URL, layout family 'pc'|'html') for day d."""
    if d >= NEW_FROM:
        return PAPER + f"pc/layout/{d:%Y%m}/{d:%d}/node_01.html", "pc"
    return PAPER + f"html/{d:%Y-%m}/{d:%d}/nbs.D110000renmrb_01.htm", "html"


class PaperDay:
    def __init__(self, c: "EpaperCollector", d: date):
        self.c, self.d = c, d
        self.first, self.fam = ep_layout_urls(d)

    def get(self, url: str) -> Optional[str]:
        return self.c.get(url, self.d)

    def pages(self) -> Optional[List[str]]:
        html = self.get(self.first)
        if html is None:
            return None
        if self.fam == "pc":
            nodes = re.findall(r'href="(node_\d+\.html)"', html)
        else:
            nodes = re.findall(r'href=["\']?(nbs\.D110000renmrb_\d+\.htm)', html)
        base = self.first.rsplit("/", 1)[0] + "/"
        out = [self.first] + [base + n for n in dict.fromkeys(nodes)]
        return list(dict.fromkeys(out))

    def articles(self, page_url: str, html: str) -> List[str]:
        if self.fam == "pc":
            links = re.findall(r'href="([./]*content/\d{6}/\d{2}/content_\d+\.html)"', html)
        else:
            links = re.findall(r'href=["\']?(nw\.D110000renmrb_\d{8}_\d+-\d+\.htm)', html)
        return list(dict.fromkeys(urllib.parse.urljoin(page_url, l) for l in links))


def ep_parse(html: str) -> Optional[Dict]:
    m = re.search(r'id="articleContent">(.*?)</div>', html, re.S)
    if not m:
        return None
    text = cc.paragraphs(m.group(1).replace("<!--enpcontent-->", "").replace("<!--/enpcontent-->", ""))
    text = "\n".join(l.strip() for l in text.splitlines() if l.strip())
    if len(text) < 100:
        return None
    art = html[html.find('<div class="article">'):] if '<div class="article">' in html else html

    def hx(tag: str) -> str:
        mm = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", art, re.S)
        return re.sub(r"\s+", " ", lib.clean_html(mm.group(1))).strip() if mm else ""

    title, pre, sub = hx("h1"), hx("h3"), hx("h2")
    pg = re.search(r"第\s*(\d+)\s*版\s*[:：]\s*([^<\s'\"]*)", html)
    author = None
    am = re.search(r"<author>(.*?)</author>", html, re.S)
    if am:
        author = lib.clean_html(am.group(1)).strip() or None
    if not author:
        sm = re.search(r'<p class="sec">(.*?)<span class="date">', re.sub(r"<!--.*?-->", "", html, flags=re.S), re.S)
        if sm:
            author = re.sub(r"\s+", " ", lib.clean_html(sm.group(1))).strip() or None
    head = " ".join(filter(None, [pre, title, sub, author, text[:60]]))
    kind = "editorial" if EDITORIAL.search(head) else "commentary" if COMMENTARY.search(head) else "article"
    return {"title": title or pre or sub, "pretitle": pre or None, "subtitle": sub or None, "text": text,
            "page": f"{pg.group(1).zfill(2)}版：{pg.group(2)}" if pg else None, "author": author, "kind": kind}


class EpaperCollector:
    def __init__(self, wayback_before: Optional[date]):
        self.st = lib.State("cn_peoples_daily_epaper")
        self.sink = cc.Sink(SOURCE, self.st, flush_every=20)
        self.wayback_before = wayback_before
        self.forbidden_streak = 0

    def get(self, url: str, d: date) -> Optional[str]:
        if d < OLD_FROM:
            if not self.wayback_before or d < self.wayback_before:
                return None
            wb = lib.wayback_latest(url)
            return lib.fetch(wb, min_delay=5) if wb else None
        html = cc.get(url, EP_DELAY)
        st = lib.fetch.last.get("status")
        if html is None and st == 403:  # the host answers 403 when hit too fast: back off and retry once
            self.forbidden_streak += 1
            time.sleep(min(600, 60 * self.forbidden_streak))
            html = cc.get(url, EP_DELAY)
        if html is not None:
            self.forbidden_streak = 0
        return html

    def day(self, d: date) -> Tuple[int, bool]:
        """Collect one day. Returns (added, complete)."""
        pd = PaperDay(self, d)
        pages = pd.pages()
        if pages is None:
            return 0, False
        n, complete = 0, True
        for pu in pages:
            html = pd.get(pu)
            if html is None:
                complete = False
                continue
            pnum = re.search(r"(?:node_|renmrb_)(\d+)\.htm", pu)
            for au in pd.articles(pu, html):
                key = re.search(r"(content_\d+|renmrb_\d{8}_\d+-\d+)", au).group(1)
                did = lib.make_id(SOURCE, f"zh:{key}")
                if self.sink.has(did):
                    continue
                ah = pd.get(au)
                if ah is None:
                    complete = False
                    continue
                p = ep_parse(ah)
                if not p:
                    continue
                row = {"id": did, "country": "CN", "source": SOURCE, "outlet": "state_media", "org": "PeoplesDaily",
                       "lang": "zh", "date": d.isoformat(), "url": au, "title": p["title"] or None,
                       "speaker": None, "kind": p["kind"], "text": p["text"],
                       "via": "wayback" if d < OLD_FROM else "direct", "edition": "print",
                       "page": p["page"] or (f"{pnum.group(1)}版" if pnum else None), "author": p["author"]}
                if p["pretitle"]:
                    row["pretitle"] = p["pretitle"]
                if p["subtitle"]:
                    row["subtitle"] = p["subtitle"]
                if self.sink.add(row):
                    n += 1
        self.sink.flush()
        return n, complete

    def run(self, newest: date, oldest: date, recheck_days: int = 3) -> int:
        total = 0
        d = newest
        last_recheck = time.time()
        while d >= oldest:
            if time.time() - last_recheck > 2 * 3600 and (newest - d).days > recheck_days:
                today = beijing_today()
                for dd in (today - timedelta(days=i) for i in range(recheck_days + 1)):
                    total += self.day(dd)[0]
                last_recheck = time.time()
            key = d.isoformat()
            if not self.st.is_done(key):
                n, complete = self.day(d)
                total += n
                log.info("epaper %s: +%d%s", key, n, "" if complete else " (incomplete)")
                if complete and (newest - d).days >= recheck_days:
                    self.st.mark_done(key)
                    self.st.save()
            d -= timedelta(days=1)
        return total


# ----------------------------------------------------------------------------------------- english
EN_NOT_ARTICLES = {"9716018"}  # 'About People's Daily Online' (linked from every page)
EN_SECTIONS = ["90777", "90780", "90778", "90779", "90882", "90782", "90783", "90785", "90786", "205040",
               "202936", "102775", "102840", "102842", "518252", "102780"]
EN_ART = re.compile(r"(?:https?://(?:en\.people\.cn|english\.people\.com\.cn|english\.peopledaily\.com\.cn))?"
                    r"/n3?/(20\d\d)/(\d{4})/c(\d+)-(\d+)\.html")


def en_parse(html: str) -> Optional[Dict]:
    body = cc.balanced_div(html, 'class="w860 d2txtCon') or cc.balanced_div(html, 'class="main_l') \
        or cc.balanced_div(html, 'class="wb_12 wb_left') or cc.balanced_div(html, 'id="p_content"') \
        or cc.balanced_div(html, 'class="d2txt_con')
    if not body:
        return None
    text = cc.paragraphs(body)
    text = re.sub(r"\n?\(Web editor:.*$", "", text, flags=re.S).strip()
    if len(text) < 100:
        return None
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    title = re.sub(r"\s+", " ", lib.clean_html(m.group(1))).strip() if m else cc.title_tag(html).split(" - ")[0]
    kind = "commentary" if re.search(r"/90780/|Opinion|Editorial|commentary", cc.title_tag(html)) else "article"
    return {"title": title, "text": text, "kind": kind}


class EnCollector:
    def __init__(self):
        self.st = lib.State("cn_peoples_daily_en")
        self.sink = cc.Sink(SOURCE, self.st, flush_every=10)
        self.failed = set(self.st.get("failed", []))

    def take(self, url: str, wayback: Optional[str] = None) -> bool:
        m = EN_ART.search(url)
        if not m or m.group(4) in EN_NOT_ARTICLES:
            return False
        did = lib.make_id(SOURCE, f"en:{m.group(4)}")
        if self.sink.has(did) or did in self.failed:
            return False
        live = f"http://en.people.cn/n3/{m.group(1)}/{m.group(2)}/c{m.group(3)}-{m.group(4)}.html"
        html = cc.get(live, EN_DELAY)
        via, url_out = "direct", live
        if html is None and wayback:
            html, via = lib.fetch(wayback, min_delay=5), "wayback"
        p = en_parse(html) if html else None
        if not p:
            self.failed.add(did)
            return False
        d = cc.ymd(int(m.group(1)), int(m.group(2)[:2]), int(m.group(2)[2:]))
        if not d:
            return False
        row = {"id": did, "country": "CN", "source": SOURCE, "outlet": "state_media", "org": "PeoplesDaily",
               "lang": "en", "date": d, "url": url_out, "title": p["title"] or None, "speaker": None,
               "kind": p["kind"], "text": p["text"], "via": via, "edition": "online"}
        if via == "wayback":
            row["wayback"] = wayback
        return self.sink.add(row)

    def save(self) -> None:
        self.st["failed"] = sorted(self.failed)[-50000:]
        self.sink.flush()

    def recent(self, max_pages: int = 60) -> int:
        before = self.sink.added + len(self.sink.buf)
        for sec in EN_SECTIONS:
            stale = 0
            for i in range(1, max_pages + 1):
                u = f"http://en.people.cn/{sec}/index{'' if i == 1 else i}.html"
                html = cc.get(u, EN_DELAY)
                if not html:
                    break
                links = list(dict.fromkeys("http://en.people.cn" + mm.group(0) if mm.group(0).startswith("/") else mm.group(0)
                                           for mm in EN_ART.finditer(html)))
                new = sum(self.take(l) for l in links)
                self.save()
                stale = stale + 1 if new == 0 else 0
                if stale >= 3:  # three pages in a row with nothing new: the rest is collected already
                    break
        return self.sink.added + len(self.sink.buf) - before

    def backfill(self, oldest_year: int = 2012) -> int:
        n = 0
        last_recent = time.time()
        y, m = date.today().year, date.today().month
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        while y >= oldest_year:
            if time.time() - last_recent > 3 * 3600:
                n += self.recent()
                last_recent = time.time()
            for host in ("en.people.cn", "english.people.com.cn"):
                prefix = f"{host}/n3/{y}/{m:02d}"
                key = f"cdx:{prefix}"
                if self.st.is_done(key):
                    continue
                q = [("url", prefix), ("matchType", "prefix"), ("output", "json"), ("fl", "original,timestamp"),
                     ("filter", "statuscode:200"), ("collapse", "urlkey"), ("limit", "100000")]
                body = lib.fetch("https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(q),
                                 min_delay=5, timeout=300, retries=2)
                try:
                    rows = json.loads(body)[1:] if body else None
                except json.JSONDecodeError:
                    rows = None
                if rows is None:
                    log.warning("CDX unavailable for %s; retry later", prefix)
                    time.sleep(900)
                    continue
                log.info("en backfill %s: %d CDX rows", prefix, len(rows))
                for i, (o, t) in enumerate(rows):
                    if self.take(o, cc.wayback_raw(o, t)):
                        n += 1
                    if i % 50 == 49:
                        self.save()
                self.st.mark_done(key)
                self.save()
            y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("part", choices=("epaper", "en"))
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--from", dest="frm", default=None, help="epaper: newest day (default today)")
    ap.add_argument("--until", default=OLD_FROM.isoformat(), help="epaper: oldest day (default 2023-01-01)")
    ap.add_argument("--wayback-before", default=None, help="epaper: read days older than 2023-01-01 from Wayback "
                                                         "down to this date (e.g. 2015-01-01)")
    ap.add_argument("--oldest-year", type=int, default=2012, help="en: Wayback backfill down to this year")
    a = ap.parse_args()
    if a.part == "epaper":
        wb = date.fromisoformat(a.wayback_before) if a.wayback_before else None
        c = EpaperCollector(wb)
        oldest = min(date.fromisoformat(a.until), wb) if wb else date.fromisoformat(a.until)
        newest = date.fromisoformat(a.frm) if a.frm else beijing_today()
        if not a.follow:
            log.info("epaper pass: %d new", c.run(newest, oldest))
            return
        # first pass walks the whole archive (new days first); later passes re-check the newest days
        cc.follow(lambda: c.run(beijing_today(), oldest), 2 * 3600, "cn_peoples_daily_epaper")
    else:
        c2 = EnCollector()
        log.info("en recent: %d new", c2.recent())
        if not a.follow:
            return
        try:
            c2.backfill(a.oldest_year)
        except Exception:  # noqa: BLE001 - keep the follow loop below alive
            log.exception("en backfill failed")
        cc.follow(c2.recent, 2 * 3600, "cn_peoples_daily_en")


if __name__ == "__main__":
    main()
