"""PRC Ministry of Foreign Affairs archive: press conferences, spokesperson statements, speeches and MFA news,
EN and ZH (one language per document), back to ~2010.

Source name: mfa_cn_archive (separate from the imported mfa_cn and the live mfa_cn_live).

Two kinds of work, one process (single writer):
  * listings: the current section listings, newest first. mfa.gov.cn (ZH) caps every listing at 29 pages
    (20 items), fmprc.gov.cn/eng (EN) at 143 pages; e.g. EN press conferences reach Jul 2022, ZH 答记者问 2010.
    Re-run every --every hours (default 6); a section stops paging at the first page that is all known.
  * archive: older URLs enumerated from the Wayback CDX index (prefixes in ARCHIVE, priority order: press
    conferences, statements, speeches/news). Each URL is fetched LIVE first (old pages migrated to the
    /web/<section>/<YYYYMM>/t<YYYYMMDD>_<n>.shtml scheme still resolve) and from the raw Wayback copy only if the
    live page is gone (MFA answers removed pages with a 200 "system" page, detected by its URL). A prefix whose
    first live attempts all fail is marked wayback-only. CDX lists are cached in state/cn_mfa_cdx/. Each prefix is
    walked newest URL first (since 2026-10-05; before, oldest first, so 2021 EN and all pre-2022-07 ZH conferences
    were still queued behind the 2010s).

Dedupe: press conferences have id mfa_cn_archive:presser:<date>:<lang> (one per day per language; a second
same-day conference is skipped); other documents mfa_cn_archive:<t-number>:<lang>, plus a (lang, date, title)
check so the same page under an old and a new URL is stored once. Anything already in mfa_cn_live (same
t-number+lang, or a conference of the same date+lang) is skipped. mfa_cn (imported, one doc per Q&A item) is
NOT deduplicated against: it is a different unit; dedupe by date+lang downstream if needed.

robots.txt on both hosts redirects to an HTML page (no rules). 5 s between requests per host (as cn_mfa_live).

    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_mfa.py [--part listings|archive|both]
        [--follow] [--every 6] [--limit N] [--test URL ...]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
import cn_common as cc  # noqa: E402

SOURCE = "mfa_cn_archive"
DELAY = 5
log = lib.setup_logging("cn_mfa")
# The listings and archive processes share the MFA hosts: enforce the 5 s gap across processes (lib's slot file).
for _h in ("www.fmprc.gov.cn", "www.mfa.gov.cn"):
    lib.SHARED_HOST_GAP.setdefault(_h, float(DELAY))
CDX_DIR = lib.STATE / "cn_mfa_cdx"

EN = "https://www.fmprc.gov.cn/eng/"
ZH = "https://www.mfa.gov.cn/web/"
# (lang, listing base, extension, default kind)
LISTINGS: List[Tuple[str, str, str, str]] = [
    ("en", EN + "xw/fyrbt/lxjzh/", "html", "briefing"),
    ("zh", ZH + "fyrbt_673021/jzhsl_673025/", "shtml", "briefing"),
    ("en", EN + "xw/fyrbt/fyrbt/", "html", "statement"),
    ("zh", ZH + "fyrbt_673021/dhdw_673027/", "shtml", "statement"),
    ("en", EN + "xw/zyjh/", "html", "speech"),
    ("en", EN + "wjb/wjbz/jh/", "html", "speech"),
    ("zh", ZH + "wjbz_673089/zyjh_673099/", "shtml", "speech"),
    ("en", EN + "zy/gb/", "html", "statement"),
    ("zh", ZH + "wjdt_674879/cfhsl_674891/", "shtml", "briefing"),
    ("en", EN + "wjb/wjbz/hd/", "html", "news"),
    ("zh", ZH + "wjbz_673089/xghd_673097/", "shtml", "news"),
    ("en", EN + "xw/wjbxw/", "html", "news"),
    ("zh", ZH + "wjdt_674879/wjbxw_674885/", "shtml", "news"),
    ("en", EN + "xw/zyxw/", "html", "news"),
    ("zh", ZH + "wjdt_674879/sjxw_674887/", "shtml", "news"),
    ("en", EN + "xw/zwbd/", "html", "news"),
    ("zh", ZH + "wjdt_674879/zwbd_674895/", "shtml", "news"),
]
# (key, lang, CDX prefix, default kind) in priority order.
ARCHIVE: List[Tuple[str, str, str, str]] = [
    ("en_presser_new", "en", "fmprc.gov.cn/eng/xw/fyrbt/lxjzh/", "briefing"),
    ("en_presser_665", "en", "fmprc.gov.cn/mfa_eng/xwfw_665399/s2510_665401/2511_665403/", "briefing"),
    ("zh_presser_mfa", "zh", "mfa.gov.cn/web/fyrbt_673021/jzhsl_673025/", "briefing"),
    ("zh_presser_fmprc", "zh", "fmprc.gov.cn/web/fyrbt_673021/jzhsl_673025/", "briefing"),
    ("en_presser_old", "en", "fmprc.gov.cn/eng/xwfw/s2510/2511/", "briefing"),  # pre-2014 scheme: after the ZH 2021-22 gap
    ("zh_presser_old", "zh", "fmprc.gov.cn/chn/gxh/tyb/fyrbt/jzhsl/", "briefing"),
    ("en_remarks_665", "en", "fmprc.gov.cn/mfa_eng/xwfw_665399/s2510_665401/", "statement"),
    ("en_remarks_old", "en", "fmprc.gov.cn/eng/xwfw/s2510/", "statement"),
    ("en_remarks_new", "en", "fmprc.gov.cn/eng/xw/fyrbt/fyrbt/", "statement"),
    ("zh_fyr_fmprc", "zh", "fmprc.gov.cn/web/fyrbt_673021/", "statement"),
    ("zh_fyr_mfa", "zh", "mfa.gov.cn/web/fyrbt_673021/", "statement"),
    ("zh_fyr_old", "zh", "fmprc.gov.cn/chn/gxh/tyb/fyrbt/", "statement"),
    ("en_wjdt_665", "en", "fmprc.gov.cn/mfa_eng/wjdt_665385/", "news"),
    ("en_zxxx_665", "en", "fmprc.gov.cn/mfa_eng/zxxx_662805/", "news"),
    ("en_wjb_665", "en", "fmprc.gov.cn/mfa_eng/wjb_663304/wjbz_663308/", "news"),
    ("en_new_xw", "en", "fmprc.gov.cn/eng/xw/", "news"),
    ("en_old_zxxx", "en", "fmprc.gov.cn/eng/zxxx/", "news"),
    ("en_old_wjdt", "en", "fmprc.gov.cn/eng/wjdt/", "news"),
    ("zh_wjbz_fmprc", "zh", "fmprc.gov.cn/web/wjbz_673089/", "news"),
    ("zh_wjbz_mfa", "zh", "mfa.gov.cn/web/wjbz_673089/", "news"),
    ("zh_wjdt_fmprc", "zh", "fmprc.gov.cn/web/wjdt_674879/", "news"),
    ("zh_wjdt_mfa", "zh", "mfa.gov.cn/web/wjdt_674879/", "news"),
    ("zh_zyjh_old", "zh", "fmprc.gov.cn/web/ziliao_674904/zyjh_674906/", "speech"),
    ("zh_old_zxxx", "zh", "fmprc.gov.cn/chn/gxh/tyb/zxxx/", "news"),
    ("zh_old_wjbxw", "zh", "fmprc.gov.cn/chn/gxh/tyb/wjbxw/", "news"),
]
ARTICLE_RE = re.compile(r"/t\d+(?:_\d+)?\.s?html?$")
SYSTEM_RE = re.compile(r"/web/system/|index_17321")

EN_PRESSER = re.compile(r"Spokesperson\s+(.+?)[’'`]s\s+Regular Press Conference\s+on\s+([A-Z][a-z]+\.?\s+\d{1,2},?\s+\d{4})")
ZH_PRESSER = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*外交部发言人(\S{2,4}?)主持例行记者会")
EN_SPOKES = re.compile(r"Spokesperson\s+([A-Z][a-z]+ [A-Z][a-z]+(?:[ -][A-Z][a-z]+)?)[’'`]s\s+(Remarks|Statement|Answers?|Comments?)")
ZH_SPOKES = re.compile(r"外交部发言人(\S{2,3}?)(?:就|答|表示|发表)")
EN_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                        "september", "october", "november", "december"], 1)}
EN_MONTHS.update({k[:3]: v for k, v in list(EN_MONTHS.items())})
EN_MONTHS["sept"] = 9
EN_DATE = re.compile(r"\b([A-Z][a-z]{2,8})\.?\s+(\d{1,2}),?\s+((?:19|20)\d\d)\b")
BODY_MARKERS = ('id="News_Body_Txt_A"', "TRS_UEDITOR", "TRS_Editor", 'class="news_content"', 'id="News_Body_Txt"',
                'class="content"', 'id="content"')


# --------------------------------------------------------------------------------------------- parsing
def en_date(s: str) -> Optional[str]:
    for m in EN_DATE.finditer(s or ""):
        mo = EN_MONTHS.get(m.group(1).lower())
        if mo:
            d = cc.ymd(int(m.group(3)), mo, int(m.group(2)))
            if d:
                return d
    return None


def url_date(url: str) -> Optional[str]:
    m = re.search(r"/t(\d{4})(\d{2})(\d{2})_\d+\.", url)
    return cc.ymd(*map(int, m.groups())) if m else None


def tnum(url: str) -> Optional[str]:
    m = re.search(r"/t(\d+(?:_\d+)?)\.s?html?$", url)
    return m.group(1) if m else None


def classify(lang: str, title: str, default: str) -> str:
    if lang == "en":
        if re.search(r"Regular Press Conference", title):
            return "briefing"
        if re.search(r"Spokesperson", title):
            return "statement"
        if re.search(r"\b(Speech|Address|Remarks by|Toast|Keynote|Statement by|Remarks at|Lecture)\b", title):
            return "speech"
    else:
        if "主持例行记者会" in title:
            return "briefing"
        if re.search(r"发言人|答记者问|声明", title):
            return "statement"
        if re.search(r"讲话|致辞|演讲|主旨发言|发言（|祝酒", title):
            return "speech"
    return "news" if default == "briefing" else default


def parse(lang: str, html: str, url: str, default_kind: str) -> Optional[Dict]:
    """{title, date, speaker, kind, text} or None when no body / date can be found."""
    title = cc.meta(html, "ArticleTitle") or ""
    if not title:
        for pat in (r'<div[^>]+id="News_Body_Title"[^>]*>(.*?)</div>', r"<h1[^>]*>(.*?)</h1>",
                    r'<div[^>]+class="title"[^>]*>(.*?)</div>'):
            m = re.search(pat, html, re.S | re.I)
            if m and lib.clean_html(m.group(1)).strip():
                title = lib.clean_html(m.group(1)).strip()
                break
    if not title:
        title = re.split(r"\s*[_—–-]\s*(?:中华人民共和国外交部|Ministry of Foreign Affairs)", cc.title_tag(html))[0].strip()
    title = re.sub(r"\s+", " ", title)
    body = None
    trs = re.search(r'<meta\s+name="TRSWCM\.ContStart"\s*/?>(.*?)<meta\s+name="TRSWCM\.ContEnd"', html, re.S | re.I)
    if trs and len(lib.clean_html(trs.group(1))) >= 80:
        body = trs.group(1)
    for mk in BODY_MARKERS if body is None else ():
        if mk in html:
            body = cc.balanced_div(html, mk)
            if body and len(lib.clean_html(body)) >= 80:
                break
            body = None
    if body is None:
        return None
    body = re.sub(r"</?(?:span|font|b|strong|em|i|u|a|o:p)\b[^>]*>", "", body, flags=re.I)  # inline tags split words
    text = lib.clean_html(body)
    text = re.sub(r"\n?(【\s*打印\s*】|【\s*关闭\s*】|\[\s*Print\s*\]|\[\s*Close\s*\]|分享到：?).*$", "", text, flags=re.S).strip()
    if len(text) < 80:
        return None
    kind = classify(lang, title, default_kind)
    speaker = None
    date = None
    if lang == "en":
        m = EN_PRESSER.search(title)
        if m:
            speaker, date = m.group(1).strip(), en_date(m.group(2))
        else:
            m = EN_SPOKES.search(title)
            speaker = m.group(1) if m else None
    else:
        m = ZH_PRESSER.search(title)
        if m:
            speaker, date = m.group(4), cc.ymd(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        else:
            m = ZH_SPOKES.search(title)
            speaker = m.group(1) if m and m.group(1) not in ("就", "答") else None
    if kind == "briefing" and not date and lang == "en":
        date = en_date(title)
    date = date or cc.first_date(cc.meta(html, "PubDate") or "")
    if not date:
        m = re.search(r'id="News_Body_Time"[^>]*>(.*?)</', html, re.S | re.I) or \
            re.search(r'class="time"[^>]*>(.*?)</p>', html, re.S | re.I)
        date = cc.first_date(lib.clean_html(m.group(1))) if m else None
    date = date or url_date(url)
    if not date and trs:  # old TRS pages: the header cell just before the body carries the date
        head = html[max(0, trs.start() - 4000):trs.start()]
        ds = [cc.first_date(x) for x in re.findall(r"(?:19|20)\d\d[-/.年]\d{1,2}[-/.月]\d{1,2}日?", head)]
        date = next((d for d in reversed(ds) if d), None)
    if not date:
        return None
    return {"title": title, "date": date, "speaker": speaker, "kind": kind, "text": text}


# --------------------------------------------------------------------------------------------- dedupe
def norm_title(t: str) -> str:
    return re.sub(r"[\W_]+", "", (t or "").lower())[:80]


class Dedupe:
    """Title keys of our own file (re-read incrementally, since the listings and archive processes both append
    to it) and the t-numbers / conference dates already in mfa_cn_live."""

    def __init__(self) -> None:
        self.titles: Set[Tuple[str, str, str]] = set()
        self.live_t: Set[str] = set()
        self.live_presser: Set[Tuple[str, str]] = set()
        self._off = 0
        self.refresh()
        p = lib.docs_path("CN", "mfa_cn_live")
        if p.exists():
            for r in lib.read_docs(p):
                self.live_t.add(r["id"].split(":")[1] + ":" + r["lang"])
                if r.get("kind") == "briefing":
                    self.live_presser.add((r["date"], r["lang"]))

    def refresh(self) -> None:
        p = lib.docs_path("CN", SOURCE)
        if not p.exists():
            return
        with p.open("rb") as f:
            f.seek(self._off)
            for line in f:
                if not line.endswith(b"\n"):
                    break
                self._off += len(line)
                r = json.loads(line)
                self.titles.add((r["lang"], r["date"], norm_title(r.get("title") or "")))

    def skip_live(self, url: str, lang: str) -> bool:
        t = tnum(url)
        return bool(t) and f"{t}:{lang}" in self.live_t


# --------------------------------------------------------------------------------------------- collector
class Collector:
    def __init__(self, part: str, limit: Optional[int] = None):
        self.st = lib.State(f"cn_mfa_{part}")  # one state file per process
        self.sink = cc.Sink(SOURCE, state=self.st, flush_every=1)
        self.dd = Dedupe()
        self.limit = limit
        self.n = 0
        self.seen_paths: Set[str] = set(self.st.get("seen_paths", []))

    def doc_id(self, url: str, lang: str, kind: str, date: str) -> str:
        if kind == "briefing":
            return lib.make_id(SOURCE, f"presser:{date}:{lang}")
        return lib.make_id(SOURCE, f"t{tnum(url) or lib.make_id('x', url).split(':')[1]}:{lang}")

    def store(self, url: str, lang: str, p: Dict, via: str) -> bool:
        did = self.doc_id(url, lang, p["kind"], p["date"])
        if self.sink.has(did):
            return False
        if p["kind"] == "briefing" and (p["date"], lang) in self.dd.live_presser:
            return False
        key = (lang, p["date"], norm_title(p["title"]))
        self.sink.flush()  # make our own buffered rows visible to the other process's refresh
        self.dd.refresh()
        if p["kind"] != "briefing" and key in self.dd.titles:
            return False
        row = {"id": did, "country": "CN", "source": SOURCE, "outlet": "official", "org": "MFA", "lang": lang,
               "date": p["date"], "url": url, "title": p["title"], "speaker": p["speaker"], "kind": p["kind"],
               "text": p["text"], "via": via, "translation": "original", "unit": "document"}
        if p["kind"] == "briefing":
            row["unit"] = "conference"
        self.sink.add(row)
        self.dd.titles.add(key)
        self.n += 1
        log.info("%s %s %s %s: %d chars [%s]", lang, p["date"], p["kind"], (p["title"] or "")[:60], len(p["text"]), via)
        return True

    def mark(self, path: str) -> None:
        self.seen_paths.add(path)

    def save(self) -> None:
        self.st["seen_paths"] = sorted(self.seen_paths)
        self.sink.flush()

    def done_limit(self) -> bool:
        return self.limit is not None and self.n >= self.limit

    # ------------------------------------------------------------------ listings
    def list_page(self, base: str, ext: str, page: int) -> Tuple[List[Tuple[str, str]], Optional[int]]:
        url = base + ("index." + ext if page == 0 else f"index_{page}.{ext}")
        html = cc.get(url, DELAY) or ""
        if SYSTEM_RE.search(lib.fetch.last.get("url") or ""):
            return [], 0
        cp = re.search(r"countPage\s*=\s*(\d+)", html)
        out = []
        for href, text in re.findall(r'<a[^>]+href="(\./\d{6}/t\d{8}_\d+\.s?html)"[^>]*>(.*?)</a>', html, re.S):
            out.append((urllib.parse.urljoin(base, href), lib.clean_html(text)))
        return out, int(cp.group(1)) if cp else None

    def listings_pass(self) -> int:
        n0 = self.n
        for lang, base, ext, kind in LISTINGS:
            pages = None
            page = 0
            while pages is None or page < pages:
                items, cp = self.list_page(base, ext, page)
                if pages is None:
                    pages = cp or 1
                if not items:
                    break
                new = 0
                for url, _ in items:
                    path = urllib.parse.urlsplit(url).path
                    if path in self.seen_paths or self.dd.skip_live(url, lang):
                        continue
                    new += 1
                    self.fetch_one(url, lang, kind, None, wayback_ok=False)
                    if self.done_limit():
                        self.save()
                        return self.n - n0
                self.save()
                if new == 0 and self.st.get(f"listed:{base}"):
                    break  # caught up with an earlier pass
                page += 1
            self.st[f"listed:{base}"] = True
            self.save()
        return self.n - n0

    # ------------------------------------------------------------------ fetching
    def fetch_one(self, url: str, lang: str, kind: str, wb_ts: Optional[str], wayback_ok: bool = True,
                  live_ok: bool = True) -> Tuple[Optional[str], bool]:
        """Fetch, parse, store. Returns (via that yielded a parseable page or None, stored?)."""
        path = urllib.parse.urlsplit(url).path
        p, via = None, None
        if live_ok:
            html = cc.get(url, DELAY)
            if html is not None and not SYSTEM_RE.search(lib.fetch.last.get("url") or ""):
                p = parse(lang, html, url, kind)
                via = "direct" if p else None
        if p is None and wayback_ok and wb_ts:
            ts, _, orig = wb_ts.partition("|")
            html = lib.fetch(cc.wayback_raw(orig or url, ts), min_delay=5, timeout=90)
            if html is not None:
                p = parse(lang, html, url, kind)
                via = "wayback"
                if p is None:
                    self.mark(path)  # a real archived page we cannot parse: do not retry forever
                    log.warning("no body/date parsed %s [wayback]", url)
        if p is None:
            return None, False
        self.mark(path)
        return via, self.store(url, lang, p, via)

    # ------------------------------------------------------------------ archive
    def cdx(self, key: str, prefix: str) -> Optional[List[Tuple[str, str]]]:
        """All (original, timestamp) article captures under prefix, cached; None if the CDX could not be read."""
        CDX_DIR.mkdir(parents=True, exist_ok=True)
        f = CDX_DIR / f"{key}.json"
        if f.exists():
            return [tuple(r) for r in json.loads(f.read_text("utf-8"))]
        rows: Dict[str, Tuple[str, str]] = {}
        resume = None
        while True:
            q = [("url", prefix), ("matchType", "prefix"), ("output", "json"), ("fl", "original,timestamp"),
                 ("filter", "statuscode:200"), ("filter", "mimetype:text/html"), ("collapse", "urlkey"),
                 ("limit", "20000"), ("showResumeKey", "true")]
            if resume:
                q.append(("resumeKey", resume))
            body = lib.fetch("https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(q), min_delay=5,
                             timeout=240, retries=2)
            if body is None:
                log.warning("CDX unavailable for %s (status %s); will retry later", prefix, lib.fetch.last.get("status"))
                return None
            try:
                data = json.loads(body or "[]")
            except json.JSONDecodeError:
                log.warning("CDX non-JSON for %s; will retry later", prefix)
                return None
            if data and data[0] == ["original", "timestamp"]:
                data = data[1:]
            resume = None
            if len(data) >= 2 and data[-2] == [] and len(data[-1]) == 1:
                resume = data[-1][0]
                data = data[:-2]
            for r in data:
                if len(r) != 2 or not ARTICLE_RE.search(urllib.parse.urlsplit(r[0]).path):
                    continue
                path = urllib.parse.urlsplit(r[0]).path
                if path not in rows or r[1] > rows[path][1]:
                    rows[path] = (r[0], r[1])
            if not resume:
                break
        out = sorted(rows.values(), key=lambda r: urllib.parse.urlsplit(r[0]).path)
        f.write_text(json.dumps(out), "utf-8")
        log.info("CDX %s: %d article URLs", prefix, len(out))
        return out

    @staticmethod
    def live_url(original: str) -> str:
        p = urllib.parse.urlsplit(original)
        path = re.sub(r"/{2,}", "/", p.path)
        host = "www.mfa.gov.cn" if path.startswith("/web/") else "www.fmprc.gov.cn"
        return f"https://{host}{path}"

    def archive_pass(self, budget_s: Optional[float] = None) -> Tuple[int, bool]:
        """Work through ARCHIVE; returns (new docs, all prefixes complete)."""
        t0 = time.time()
        n0 = self.n
        complete = True
        for key, lang, prefix, kind in ARCHIVE:
            if self.st.get(f"done:{key}"):
                continue
            rows = self.cdx(key, prefix)
            if rows is None:
                complete = False
                continue
            live_stat = self.st.get(f"live:{key}") or {"ok": 0, "fail": 0}
            fails = 0
            for original, ts in reversed(rows):  # newest first: the 2021-22 gaps fill before 2010s pages
                url = self.live_url(original)
                path = urllib.parse.urlsplit(url).path
                if path in self.seen_paths or self.dd.skip_live(url, lang):
                    continue
                live_ok = not (live_stat["ok"] == 0 and live_stat["fail"] >= 5)
                via, _ = self.fetch_one(url, lang, kind, f"{ts}|{original}", wayback_ok=True, live_ok=live_ok)
                if live_ok:
                    live_stat["ok" if via == "direct" else "fail"] += 1
                    self.st[f"live:{key}"] = live_stat
                if via is None and path not in self.seen_paths:
                    fails += 1  # leave unmarked: retried on a later pass
                if len(self.seen_paths) % 10 == 0:
                    self.save()
                if self.done_limit() or (budget_s and time.time() - t0 > budget_s):
                    self.save()
                    return self.n - n0, False
            self.save()
            if fails == 0:
                self.st[f"done:{key}"] = True
            else:
                complete = False
                log.info("%s: %d fetch failures left for a later pass", key, fails)
            self.save()
        return self.n - n0, complete


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=("listings", "archive", "both"), default="both")
    ap.add_argument("--follow", action="store_true", help="keep running: listings every --every hours, archive between")
    ap.add_argument("--every", type=float, default=6.0)
    ap.add_argument("--limit", type=int, default=None, help="stop after N new documents (testing)")
    ap.add_argument("--test", nargs="*", default=None, help="parse URL(s) (lang:kind:url) and print, no writing")
    a = ap.parse_args()
    if a.test is not None:
        for spec in a.test:
            lang, kind, url = spec.split(":", 2)
            html = lib.fetch(url, min_delay=DELAY)
            p = parse(lang, html or "", url, kind) if html else None
            print(json.dumps({"url": url, "final": lib.fetch.last.get("url"), **(p or {"parsed": None})},
                             ensure_ascii=False)[:700])
        return
    c = Collector(a.part, limit=a.limit)
    every = a.every * 3600
    last_list = -1e18
    complete = a.part == "listings"
    while True:
        if a.part in ("listings", "both") and time.time() - last_list >= every:
            log.info("listings pass: %d new", c.listings_pass())
            last_list = time.time()
        progressed = False
        if a.part in ("archive", "both") and not complete and not c.done_limit():
            budget = max(600.0, every - (time.time() - last_list)) if (a.follow and a.part == "both") else None
            n, complete = c.archive_pass(budget)
            progressed = n > 0
            log.info("archive pass: %d new (complete=%s)", n, complete)
        c.save()
        if not a.follow or c.done_limit() or (a.part == "archive" and complete):
            break
        until_list = every - (time.time() - last_list) if a.part != "archive" else 1e18
        wait = until_list if complete else (60 if progressed else 1800)
        time.sleep(max(60.0, min(wait, until_list)))


if __name__ == "__main__":
    main()
