"""Fetch specific article URLs (e.g. found by web search for a topic, since the sites' own search pages
are robots-disallowed) with the regular site parsers, and store them with sample="seed".

    uv run --project ~/Projects/rhetoric-corpus python collectors/ru_seed.py URL [URL ...]   (or a file via @path)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402
import ru_statemedia as sm  # noqa: E402

log = lib.setup_logging("ru_seed")


def main() -> None:
    urls = []
    for a in sys.argv[1:]:
        urls += Path(a[1:]).read_text().split() if a.startswith("@") else [a]
    sites = [c() for c in sm.SITES.values()]
    for u in urls:
        site = next((s for s in sites if s.pat.match(u)), None)
        if not site:
            log.warning("no parser for %s", u)
            continue
        rows = sm.make_parser(site)({"url": u, "date": "", "tier": "seed"})
        for r in rows or []:
            r["sample"] = "seed"
        if rows:
            added, total = lib.write_docs("RU", site.source, rows)
            log.info("%s -> %s (%s) added %d", u, site.source, rows[0]["date"], added)
        else:
            log.warning("nothing stored for %s", u)


if __name__ == "__main__":
    main()
