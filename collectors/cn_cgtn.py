"""CGTN (China Global Television Network) English articles from the site's own sitemaps.

Source name: cn_cgtn (org CGTN, outlet state_media, lang en). One document per article.

URL lists (robots.txt lists them): sitemap_latestnews.xml, sitemap_news.xml, sitemap_news_2/_3.xml (newest
~90k items) and sitemap_archived_YYYY[_N].xml for 2017 -> 2025 (each up to 30k items). Each entry carries
news:publication_date and news:title; the page is fetched for the text. Hosts: news.cgtn.com plus the
regional newsaf/newseu/newsus.cgtn.com (each host its own polite queue, one thread per host).
Text: the `text ... en` blocks inside #cmsMainContent (image captions and video players skipped). Video-only
items (text < 100 chars) are not stored but marked done so they are not refetched.

robots.txt: Disallow /*.do*, /*contents.0.shareBody.shareUrl*, /*account.user.cgtn.com* (wildcards honoured via
cn_common.robots_ok); news*.cgtn.com have no robots.txt. lib default delay (4 s per host).

Order: latest + news sitemaps first, then archived sitemaps newest -> oldest. --follow re-reads latest/news
every 2 h between backfill chunks.

    uv run --project ~/Projects/rhetoric-corpus python collectors/cn_cgtn.py [--follow] [--test URL]
"""
from __future__ import annotations

import argparse
import re
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
import cn_common as cc  # noqa: E402

SOURCE = "cn_cgtn"
BASE = "https://www.cgtn.com"
DELAY = 4
NEW_MAPS = ["sitemap_latestnews.xml", "sitemap_news.xml", "sitemap_news_2.xml", "sitemap_news_3.xml"]
NEW_EVERY_S = 2 * 3600
log = lib.setup_logging("cn_cgtn")
MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                   "dec"], 1)}


def item_key(url: str) -> Optional[str]:
    """Native id: the last path segment before /index.html or /p.html."""
    m = re.search(r"cgtn\.com/news/(?:\d{4}-\d{2}-\d{2}/)?([^/]+)/(?:index|p)\.html", url)
    if not m:
        return None
    k = m.group(1)
    tail = re.search(r"-([A-Za-z0-9]{8,14})$", k)
    return tail.group(1) if tail else k[-40:]


def _text_blocks(seg: str) -> List[str]:
    out = []
    for m in re.finditer(r'<div class="text[^"]*\ben\b[^"]*"[^>]*>', seg):
        depth, i = 0, m.start()
        for t in re.finditer(r"<(/?)div\b[^>]*>", seg[i:], re.I):
            depth += -1 if t.group(1) else 1
            if depth == 0:
                inner = seg[m.end():i + t.start()]
                txt = cc.paragraphs(inner) if "<p" in inner.lower() else lib.clean_html(inner)
                if txt:
                    out.append(txt)
                break
    return out


def parse(html: str) -> Optional[Dict]:
    i = html.find('id="cmsMainContent"')
    if i < 0:
        return None
    seg = html[i:]
    j = seg.find("sourceTextDiv")
    seg = seg[:j] if j > 0 else seg[:400000]
    text = "\n".join(_text_blocks(seg)).strip()
    m = re.search(r'<div class="news-title">(.*?)</div>', html, re.S)
    title = lib.clean_html(m.group(1)) if m else cc.title_tag(html).replace(" - CGTN", "").strip("​ ")
    m = re.search(r'class="news-section-date[^"]*">(.*?)</div>', html, re.S)
    sd = lib.clean_html(m.group(1)) if m else ""
    date = None
    dm = re.search(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", sd)
    if dm:
        date = cc.ymd(int(dm.group(3)), MON.get(dm.group(2).lower(), 0), int(dm.group(1)))
    section = re.sub(r"\s*\d{1,2}:\d{2},.*$", "", sd).strip() or None
    m = re.search(r'class="news-author[^"]*">(.*?)</div>', html, re.S)
    author = re.sub(r"\s+", " ", lib.clean_html(m.group(1))) if m else ""
    author = re.split(r"\bUpdated\b", author)[0].strip(" ,") or None
    kind = "opinion" if section and section.lower() in ("opinions", "opinion") else "article"
    return {"title": title, "date": date, "speaker": author or None, "kind": kind, "text": text,
            "section": section}


class CGTN:
    def __init__(self) -> None:
        self.st = lib.State("cn_cgtn")      # done = processed sitemap names (archived only); skip = short items
        self.sink = cc.Sink(SOURCE, self.st, flush_every=10)
        self.skip = set(self.st.get("skip") or [])
        self.lock = threading.Lock()

    def entries(self, name: str) -> List[Tuple[str, Optional[str], Optional[str]]]:
        """[(url, pubdate, title)] of one sitemap."""
        xml = cc.get(f"{BASE}/{name}", DELAY, timeout=180) or ""
        out = []
        for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
            loc = re.search(r"<loc>\s*(.*?)\s*</loc>", block, re.S)
            if not loc:
                continue
            pd = re.search(r"<news:publication_date>\s*(.*?)\s*</", block) or re.search(r"<lastmod>\s*(.*?)\s*<", block)
            ti = re.search(r"<news:title>\s*(.*?)\s*</news:title>", block, re.S)
            out.append((lib.clean_html(loc.group(1)), cc.first_date(pd.group(1)) if pd else None,
                        lib.clean_html(ti.group(1)) if ti else None))
        return out

    def one(self, url: str, pdate: Optional[str], ptitle: Optional[str]) -> None:
        key = item_key(url)
        if not key:
            return
        did = f"{SOURCE}:{key}"
        with self.lock:
            if self.sink.has(did) or key in self.skip:
                return
        html = cc.get(url, DELAY, retries=2)
        if html is None:
            return
        p = parse(html)
        if not p or len(p["text"]) < 100:
            with self.lock:
                self.skip.add(key)
            return
        date = p["date"] or pdate or cc.first_date(url)
        if not date:
            return
        row = {"id": did, "country": "CN", "source": SOURCE, "outlet": "state_media", "org": "CGTN", "lang": "en",
               "date": date, "url": url, "title": p["title"] or ptitle, "speaker": p["speaker"], "kind": p["kind"],
               "text": p["text"], "via": lib.fetch.last.get("via") or "direct", "section": p["section"]}
        with self.lock:
            self.sink.add(row)

    def run_list(self, items: List[Tuple[str, Optional[str], Optional[str]]], deadline: float) -> None:
        """Fetch items, one thread per host, until done or deadline."""
        by_host: Dict[str, List] = defaultdict(list)
        for it in items:
            by_host[re.sub(r"^https?://([^/]+)/.*$", r"\1", it[0])].append(it)

        def worker(lst: List) -> None:
            for n, (u, d, t) in enumerate(lst):
                if time.time() > deadline:
                    return
                try:
                    self.one(u, d, t)
                except Exception:  # noqa: BLE001 - one bad page must not stop the host queue
                    log.exception("item failed %s", u)
                if n % 100 == 99:
                    self.save()

        ths = [threading.Thread(target=worker, args=(lst,), daemon=True) for lst in by_host.values()]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        self.save()

    def save(self) -> None:
        with self.lock:
            self.st["skip"] = sorted(self.skip)
            self.sink.flush()

    def new_pass(self) -> int:
        before = self.sink.added
        items = []
        for name in NEW_MAPS:
            items += self.entries(name)
        items.sort(key=lambda x: x[1] or "", reverse=True)
        self.run_list(items, time.time() + 6 * 3600)
        return self.sink.added - before

    def archived_maps(self) -> List[str]:
        xml = cc.get(f"{BASE}/sitemap.xml", DELAY) or ""
        def key(n: str) -> Tuple[int, int]:
            m = re.search(r"_(\d{4})(?:_(\d+))?\.xml", n)
            return (int(m.group(1)), int(m.group(2) or 1)) if m else (0, 0)
        names = sorted({re.sub(r"^.*/", "", u) for u, _ in cc.sitemap_locs(xml) if "archived" in u}, key=key,
                       reverse=True)
        return names

    def backfill(self, seconds: int) -> int:
        before = self.sink.added
        deadline = time.time() + seconds
        for name in self.archived_maps():
            if self.st.is_done(name):
                continue
            if time.time() > deadline:
                break
            items = self.entries(name)
            log.info("%s: %d entries", name, len(items))
            self.run_list(items, deadline)
            if time.time() <= deadline and items:
                self.st.mark_done(name)
                self.save()
        return self.sink.added - before


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--test", nargs="*", help="parse these article URLs and print")
    a = ap.parse_args()
    if a.test is not None:
        for u in a.test:
            p = parse(cc.get(u, DELAY) or "")
            print(u, item_key(u), {k: (v[:200] if isinstance(v, str) else v) for k, v in (p or {}).items()},
                  len((p or {}).get("text") or ""))
        return
    c = CGTN()
    log.info("new pass: %d", c.new_pass())
    while True:
        n = c.backfill(NEW_EVERY_S)
        log.info("backfill chunk: %d new", n)
        if not a.follow:
            break
        log.info("new pass: %d", c.new_pass())


if __name__ == "__main__":
    main()
