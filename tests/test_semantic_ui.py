"""Analyst views (scripts/semantic_views.py), the private server's semantic endpoints (scripts/serve_search.py), the
public Trends build's media-text guard (tsm-strait-layers build_trends.py) and the shared UI file copies.
Runs on small fixture aggregates; no models, no corpus index needed."""
from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
import sys  # noqa: E402

sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import semantic_views as sv  # noqa: E402

TOOL = Path.home() / "Projects" / "tsm-strait-layers" / "tools" / "rhetoric-search"
OUTLETS = {"mid_ru": "official", "tass_com": "state_media"}
LONG = "x" * 400


def _series(stream, key, periods, mean, extra=None):
    n = len(periods)
    return {"stream": stream, **(extra or {}), "period": periods, "n": [30] * n, "mean": [mean] * n, "se": [0.01] * n,
            "base": [None] + [mean - 0.01] * (n - 1), "delta": [None] + [0.01] * (n - 1), "z": [None] + [1.0] * (n - 1)}


@pytest.fixture()
def agg(tmp_path: Path) -> sv.Aggregates:
    months, weeks = ["2026-07", "2026-08", "2026-09"], ["2026-09-07", "2026-09-14", "2026-09-21"]
    streams = [{"stream": "mid_ru|ru|all", "country": "RU", "source": "mid_ru", "lang": "ru", "sample": "all", "n_docs": 90,
                "weight": 9.5, "first": "2026-07-01", "last": "2026-09-30"},
               {"stream": "tass_com|en|recent", "country": "RU", "source": "tass_com", "lang": "en", "sample": "recent",
                "n_docs": 900, "weight": 30.0, "first": "2026-07-01", "last": "2026-09-30"}]
    tone = {"generated": "g", "streams": streams, "series": {
        "month": [_series(s["stream"], None, months, 0.1, {"metric": m}) for s in streams for m in ("hostility", "esc_balance")],
        "week": [_series(s["stream"], None, weeks, 0.1, {"metric": "hostility"}) for s in streams]}}
    country = {"generated": "g", "streams": None, "series": {pt: [{"country": "RU", "metric": "hostility", "period": ps,
               "value": [0.1] * 3, "adj_delta": [None, 0.01, 0.01], "z": [None, 1.0, 1.0], "n": [60] * 3, "n_streams": [2] * 3}]
               for pt, ps in (("month", months), ("week", weeks))}}
    stance = {"generated": "g", "streams": streams, "series": {"week": [], "month": [
        _series(s["stream"], None, months, 0.2, {"target": "US", "metric": "hostility"}) for s in streams]}}
    sal = {"generated": "g", "streams": streams, "series": {"week": [], "month": [
        {**_series(s["stream"], None, months, 0.3, {"target": "US"}), "k": [9] * 3} for s in streams]}}
    off = {"text": "Official sentence.", "outlet": "official", "date": "2026-09-08", "country": "RU", "source": "mid_ru",
           "title": "MFA", "url": "https://mid.ru/1", "score": 0.9, "metric": "hostility", "doc_id": "mid_ru:1"}
    med = {**off, "text": "Copyrighted media sentence.", "outlet": "state_media", "source": "tass_com", "title": "TASS headline",
           "url": "https://tass.com/1", "doc_id": "tass_com:1"}
    base = {"kind": "tone", "level": "stream", "period_type": "week", "country": "RU", "stream": "tass_com|en|recent",
            "metric": "hostility", "target": None, "topic": None, "start": "2026-09-07", "end": "2026-09-07", "n_periods": 1,
            "period": "2026-09-07", "mean": 0.2, "base": 0.1, "delta": 0.1, "z": 5.0, "n": 50, "score": 9, "recent": True,
            "direction": "up", "evidence_note": "note"}
    alerts = {"meta": {"data_last_date": "2026-09-25", "n_tests": 10, "tail_check": {}, "tiers": "t", "z_threshold": {}},
              "alerts": [{**base, "id": "a0", "tier": "strong", "evidence": [off, med]},
                         {**base, "id": "a1", "tier": "weak", "z": 3.1, "evidence": [off]}]}
    echoes = {"meta": {"threshold": 0.93, "window_days": 14, "clusters": 1, "clusters_written": 1, "pairs_by_country": {}},
              "clusters": [{"id": "e0", "n_countries": 2, "n_members": 2, "countries_in_order": ["RU", "BY"], "type": "mixed",
                            "official_countries": ["BY"], "first_seen": {"country": "RU", "date": "2026-09-08", "source": "tass_com"},
                            "last_date": "2026-09-09", "span_days": 1, "members": [{**med, "max_sim": 0.95},
                                                                                     {**off, "country": "BY", "max_sim": 0.95}]}]}
    topics = {"version": "v", "k": 2, "topics": [{"topic": 0, "label": "a / b", "n_docs": 5, "terms": {"en": ["a"]},
              "langs": {"en": 5}, "countries": {"RU": 5}, "exemplars": [{"doc_id": "tass_com:1", "title": "Headline", "url": "u"}]}],
              "prevalence": [{"country": "RU", "month": "2026-09", "topic": 0, "n_docs": 5, "country_docs": 60,
                              "share": 0.08, "balanced_share": 0.07}]}
    manifest = {"generated": "2026-09-25T00:00:00+00:00", "versions": {"tone_model": "{\"version\": \"x\"}"}, "totals": {"docs": 990},
                "coverage_by_country": {"RU": {"docs": 990, "embedded": 990, "scored": 990, "first": "2026-07-01", "last": "2026-09-30"}}}
    validation = {"model_version": "x", "n_heldout": 100, "note": "teacher only",
                  "dims": {"hostility": {"overall": {"n": 100, "auc": 0.9}, "random_arm": {}, "by_lang": {"en": {"n": 60, "pearson": 0.7}}}}}
    for name, obj in {"series_tone": tone, "series_country": country, "series_stance": stance, "series_salience": sal,
                      "alerts": alerts, "echoes": echoes, "topics": topics, "manifest": manifest, "validation": validation}.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(obj))
    return sv.Aggregates(tmp_path, outlets=OUTLETS)


# ------------------------------------------------------------------------------------------------ views
def test_public_alerts_drop_media_text_and_weak_tier(agg):
    pub = sv.view(agg, "alerts", public=True)
    assert [a["id"] for a in pub["alerts"]] == ["a0"]
    off, med = pub["alerts"][0]["evidence"]
    assert off["text"] == "Official sentence." and med["text"] is None and med["text_withheld"]
    assert med["title"] == "TASS headline" and med["url"] == "https://tass.com/1"
    priv = sv.view(agg, "alerts", public=False)
    assert len(priv["alerts"]) == 2 and priv["alerts"][0]["evidence"][1]["text"] == "Copyrighted media sentence."


def test_public_echoes_keep_headline_only_for_media(agg):
    members = sv.view(agg, "echoes", public=True)["clusters"][0]["members"]
    assert members[0]["text"] is None and members[0]["title"] == "TASS headline"
    assert members[1]["text"] == "Official sentence."


def test_public_record_truncates_official_text():
    assert len(sv.public_record({"outlet": "official", "text": LONG})["text"]) == 300


def test_tone_public_has_official_streams_only_and_error_bands(agg):
    pub, priv = sv.view(agg, "tone_RU", True), sv.view(agg, "tone_RU", False)
    assert set(pub["streams"]) == {"mid_ru|ru|all"}
    assert set(priv["streams"]) == {"mid_ru|ru|all", "tass_com|en|recent"}
    se = pub["combined"]["month"]["hostility"]["se"]
    # sqrt((9.5*.01)^2 + (30*.01)^2) / 39.5
    assert se[0] == pytest.approx(((9.5 * .01) ** 2 + (30 * .01) ** 2) ** .5 / 39.5, abs=1e-4)


def test_heatmap_combines_streams_with_fixed_weights(agg):
    hm = sv.view(agg, "heatmap", public=True)
    cell = hm["cells"]["stance:hostility:all"]["RU"]["US"][0]
    assert cell[1] == pytest.approx(0.2) and cell[2] == 60 and cell[5] == 2
    assert hm["cells"]["stance:hostility:official"]["RU"]["US"][0][2] == 30
    assert "salience:share:all" in hm["cells"]


def test_overview_and_bad_view_name(agg):
    ov = sv.view(agg, "overview", public=True)
    assert ov["validation"]["note"] == "teacher only" and ov["tiers_shown"] == ["strong", "moderate"]
    assert {s["stream"]: s["official"] for s in ov["streams"]} == {"mid_ru|ru|all": True, "tass_com|en|recent": False}
    with pytest.raises(ValueError):
        sv.view(agg, "../manifest", public=True)


# ------------------------------------------------------------------------------------------------ public build guard
def _build_trends():
    p = ROOT / "publish" / "build_trends.py"  # vendored copy the nightly CI job runs
    spec = importlib.util.spec_from_file_location("build_trends", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_guard_accepts_every_public_view(agg):
    bt = _build_trends()
    for name in sv.view_names(agg):
        bt.assert_public_safe(sv.view(agg, name, public=True), name)


@pytest.mark.parametrize("bad", [
    {"evidence": [{"outlet": "state_media", "text": "media body sentence", "url": "u"}]},   # media text
    {"members": [{"outlet": "media", "passage": "media passage"}]},                          # other text key
    {"x": {"text": "text without provenance"}},                                               # no outlet at all
    {"evidence": [{"outlet": "official", "text": LONG}]},                                      # official > 300 chars
    {"title": LONG},                                                                           # stray long string
])
def test_guard_rejects_media_text(bad):
    bt = _build_trends()
    with pytest.raises(bt.PublicTextError):
        bt.assert_public_safe(bad)


def test_guard_rejects_private_views(agg):
    bt = _build_trends()
    with pytest.raises(bt.PublicTextError):
        bt.assert_public_safe(sv.view(agg, "alerts", public=False))
    with pytest.raises(bt.PublicTextError):
        bt.assert_public_safe(sv.view(agg, "echoes", public=False))


def test_shared_ui_copies_are_identical():
    pairs = [(ROOT / "scripts" / "ui" / f, TOOL / "js" / f) for f in ("trends.js", "trends-more.js", "trends-charts.js")]
    pairs.append((ROOT / "scripts" / "ui" / "trends.css", TOOL / "trends.css"))
    if not all(b.exists() for _, b in pairs):
        pytest.skip("tsm-strait-layers copies not available")
    for a, b in pairs:
        assert a.read_bytes() == b.read_bytes(), f"{b} differs from {a}: copy scripts/ui/ over"


# ------------------------------------------------------------------------------------------------ server endpoints
@pytest.fixture()
def server(agg, monkeypatch):
    import semantic_live
    import serve_search

    monkeypatch.setattr(serve_search, "AGG", agg)
    serve_search._views.clear()
    monkeypatch.setattr(semantic_live, "semantic_search", lambda q, k, *a: [{"score": 0.9, "doc_id": "d", "passage": q}][:k])
    real = semantic_live.evidence

    def fake_evidence(kind, country, period, *a):
        if kind == "bogus":
            return real(kind, country, period, *a)  # validation path, raises before touching the store
        return {"docs": 3, "note": f"{kind} {country} {period}", "evidence": []}

    monkeypatch.setattr(semantic_live, "evidence", fake_evidence)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), serve_search.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _get(url):
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.mark.parametrize("name", ["overview", "alerts", "heatmap", "topics", "echoes", "tone_RU"])
def test_view_endpoints(server, name):
    code, body = _get(f"{server}/api/view/{name}")
    assert code == 200 and isinstance(json.loads(body), dict)


def test_private_views_keep_full_text(server):
    body = json.loads(_get(f"{server}/api/view/alerts")[1])
    assert body["alerts"][0]["evidence"][1]["text"] == "Copyrighted media sentence."
    assert any(a["tier"] == "weak" for a in body["alerts"])


def test_semsearch_and_evidence_endpoints(server):
    code, body = _get(f"{server}/api/semsearch?q=red+lines&k=1&country=RU")
    assert code == 200 and json.loads(body)["results"][0]["passage"] == "red lines"
    assert _get(f"{server}/api/semsearch?q=")[0] == 400
    code, body = _get(f"{server}/api/evidence?kind=tone&country=RU&period=2026-09")
    assert code == 200 and json.loads(body)["note"] == "tone RU 2026-09"
    assert _get(f"{server}/api/evidence?kind=bogus&country=RU&period=2026-09")[0] == 400
    assert _get(f"{server}/api/view/nope")[0] == 400


def test_pages_and_static_whitelist(server):
    assert _get(f"{server}/semantic")[0] == 200
    for f in ("trends.js", "trends-more.js", "trends-charts.js", "trends.css"):
        assert _get(f"{server}/ui/{f}")[0] == 200
    assert _get(f"{server}/ui/../serve_search.py")[0] == 404
