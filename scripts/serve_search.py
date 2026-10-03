"""Private local web UI for the corpus: http://127.0.0.1:8950/  (binds to localhost only).

  uv run python scripts/serve_search.py [--port 8950]

Pages: /  keyword + semantic search      /semantic  analyst views (alerts, country, heatmap, topics, echo, coverage, method)
Endpoints:
  /api/meta   /api/search?q=&country=&source=&lang=&from=&to=&morph=1&offset=&limit=
  /api/semsearch?q=&country=&source=&lang=&from=&to=&k=          cross-lingual "find statements like this"
  /api/view/<overview|alerts|heatmap|topics|echoes|tone_CC>        views over index/semantic/aggregates (full text)
  /api/evidence?kind=tone|stance|salience&country=&period=&metric=&target=&stream=&scope=all|official&dir=up|down
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import searchlib as sl  # noqa: E402
import semantic_views as sv  # noqa: E402
from build_index import DB  # noqa: E402

PAGE = HERE / "search_ui.html"
UI_DIR = HERE / "ui"
UI_FILES = {"semantic.html": "text/html; charset=utf-8", "trends.js": "text/javascript; charset=utf-8",
            "trends-charts.js": "text/javascript; charset=utf-8",
            "trends-more.js": "text/javascript; charset=utf-8", "trends.css": "text/css; charset=utf-8"}
_local = threading.local()
AGG = sv.Aggregates()
_views: dict = {}


def con() -> sqlite3.Connection:
    if not hasattr(_local, "con"):
        _local.con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, check_same_thread=False)
    return _local.con


def _csv(v: str):
    return [x for x in (v or "").split(",") if x]


def api_search(qs: dict) -> dict:
    g = lambda k, d="": (qs.get(k) or [d])[0]
    q = g("q").strip()
    if not q:
        return {"error": "empty query"}
    f = sl.Filters(_csv(g("country")), _csv(g("source")), _csv(g("lang")), g("from"), g("to"))
    limit, offset = min(int(g("limit", "50")), 200), int(g("offset", "0"))
    t0 = time.time()
    res = sl.search(con(), q, f, limit, offset, g("morph") == "1", g("sort", "date"), max_snips=3)
    # Hit counts need the full texts; above 5,000 documents the chart counts documents only.
    hits = sl.hit_counts(con(), res["plan"], [r[0] for r in res["rows"]]) if len(res["rows"]) <= 5000 else {}
    months = sl.count_by(con(), res["plan"], res["rows"], "month", hits)
    by_source = sl.count_by(con(), res["plan"], res["rows"], "source", hits)
    return {"query": q, "fts": res["fts"], "table": res["table"], "note": res["note"], "total_docs": res["total_docs"],
            "total_hits": sum(h for _, _, h in months) if hits or not res["rows"] else None, "months": months, "by_source": by_source,
            "results": [{k: d[k] for k in ("id", "country", "source", "org", "lang", "date", "url", "title", "speaker",
                                           "kind", "hits", "snippets")} for d in res["results"]],
            "offset": offset, "limit": limit, "ms": round((time.time() - t0) * 1000),
            "morph": sl.morph_engine() if g("morph") == "1" else None}


def api_view(name: str) -> dict:
    """A semantic view (full text, private), cached until the aggregates change."""
    key = (name, AGG.stamp())
    if key not in _views:
        if len(_views) > 64:
            _views.clear()
        _views[key] = sv.view(AGG, name, public=False)
    return _views[key]


def api_semsearch(qs: dict) -> dict:
    import semantic_live

    g = lambda k, d="": (qs.get(k) or [d])[0]
    q = g("q").strip()
    if not q:
        return {"error": "empty query"}
    k = max(1, min(int(g("k", "30")), 100))
    t0 = time.time()
    res = semantic_live.semantic_search(q, k, _csv(g("country")), _csv(g("source")), _csv(g("lang")), g("from"), g("to"))
    return {"query": q, "k": k, "results": res, "ms": round((time.time() - t0) * 1000),
            "note": "Best passage per document by cosine similarity (multilingual-e5-small); scores are relative, not calibrated."}


def api_evidence(qs: dict) -> dict:
    import semantic_live

    g = lambda k, d="": (qs.get(k) or [d])[0]
    return semantic_live.evidence(g("kind", "tone"), g("country"), g("period"), g("metric", "hostility"), g("target"),
                                  g("stream"), g("scope", "all"), g("dir", "up"), int(g("k", "8")))


def api_meta() -> dict:
    f = sl.facets(con())
    return {"countries": f["countries"], "sources": f["sources"], "langs": f["langs"], "range": f["range"]}


class H(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        u = urllib.parse.urlsplit(self.path)
        qs = urllib.parse.parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if u.path in ("/semantic", "/semantic/"):
                return self._send(200, (UI_DIR / "semantic.html").read_bytes(), UI_FILES["semantic.html"])
            if u.path.startswith("/ui/") and u.path[4:] in UI_FILES:
                return self._send(200, (UI_DIR / u.path[4:]).read_bytes(), UI_FILES[u.path[4:]])
            if u.path.startswith("/api/view/"):
                return self._json(api_view(u.path[len("/api/view/"):]))
            if u.path == "/api/semsearch":
                return self._json(api_semsearch(qs))
            if u.path == "/api/evidence":
                return self._json(api_evidence(qs))
            if u.path == "/api/meta":
                return self._send(200, json.dumps(api_meta(), ensure_ascii=False).encode(), "application/json")
            if u.path == "/api/search":
                out = api_search(qs)
                return self._send(400 if "error" in out else 200, json.dumps(out, ensure_ascii=False).encode(),
                                  "application/json")
            if u.path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            self._send(404, b"not found", "text/plain")
        except (ValueError, sqlite3.OperationalError) as e:
            self._send(400, json.dumps({"error": str(e)}).encode(), "application/json")
        except FileNotFoundError as e:
            self._send(404, json.dumps({"error": f"missing: {e}"}).encode(), "application/json")

    def _json(self, out: dict) -> None:
        self._send(400 if "error" in out else 200, json.dumps(out, ensure_ascii=False).encode(), "application/json")

    def log_message(self, fmt, *args):  # quieter log
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8950)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print(f"Corpus search on http://127.0.0.1:{a.port}/  (Ctrl-C to stop)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
