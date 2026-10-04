"""Bounded-memory deep backfill for monthly-sitemap sites (RIA platform: ria.ru, sputnikglobe.com), used by
ru_statemedia.py --full. See the ru_statemedia docstring ("DEEP / FULL mode") for the sampling definition.

Pass p takes, from every monthly sitemap >= start, the URLs whose seeded rank position in their month is in
[p*width, (p+1)*width), and fetches them round-robin over months (all months' position k before position k+1).
Only one pass (<= width x months items) is in memory at a time; the monthly sitemaps are re-read each pass.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple

import lib
from ru_common import run_queue

logger = logging.getLogger("rhetoric-corpus.ru")


def with_rss(items: Iterable[Dict], rss_fn: Optional[Callable[[], List[Dict]]], every: int = 1800) -> Iterator[Dict]:
    """Yield `items`, and every `every` seconds first yield the site's current RSS items (run_queue skips the
    ones already done), so a weeks-long backfill does not stop the collection of new articles."""
    last = time.time()
    for it in items:
        if rss_fn is not None and time.time() - last >= every:
            last = time.time()
            try:
                yield from rss_fn()
            except Exception as e:  # noqa: BLE001 - an RSS hiccup must not stop the backfill
                logger.warning("rss poll failed: %s", e)
        yield it


def run_passes(site, st: "lib.State", parse: Callable[[Dict], Optional[List[Dict]]], start: str,
               rank: Callable[[str], int], rss_fn: Optional[Callable[[], List[Dict]]] = None,
               width: int = 1500, limit: int = 0) -> None:
    """Run (or resume) the rank-band passes for `site` (needs month_sitemaps(start) and month_urls(ym, loc))."""
    have = lib.existing_ids(lib.docs_path("RU", site.source))
    p = int(st.get("full_pass") or 0)
    while True:
        months = site.month_sitemaps(start)
        if not months:  # sitemap index unreachable (site down / timeouts): wait, do not end the run
            logger.warning("%s: sitemap index unreadable; retrying in 30 min", site.source)
            time.sleep(1800)
            continue
        queue: List[Tuple[int, int, str, str]] = []
        more = False
        for ym, loc in months:
            urls = site.month_urls(ym, loc)
            ranked = sorted({u: d for u, d in urls}.items(), key=lambda ud: rank(ud[0]))
            if len(ranked) > (p + 1) * width:
                more = True
            for pos in range(p * width, min(len(ranked), (p + 1) * width)):
                u, d = ranked[pos]
                if not st.is_done("s2:" + u) and lib.make_id(site.source, u) not in have:
                    queue.append((pos, -int(d.replace("-", "")), u, d))
            logger.info("%s pass %d month %s: %d urls, queue %d", site.source, p, ym, len(ranked), len(queue))
        queue.sort()
        if limit:
            queue = queue[:limit]
        st["full_pass_info"] = {"pass": p, "width": width, "start": start, "months": len(months), "todo": len(queue)}
        st.save()
        items = ({"url": u, "date": d, "tier": "random", "key": "s2:" + u, "rank_pos": pos} for pos, _, u, d in queue)
        run_queue(with_rss(items, rss_fn), st, parse, "RU", lambda r: r["source"], batch=20, save_every=50)
        if limit or not more:
            logger.info("%s: passes finished at pass %d (more=%s)", site.source, p, more)
            return
        p += 1
        st["full_pass"] = p
        st.save()


__all__ = ["with_rss", "run_passes"]
