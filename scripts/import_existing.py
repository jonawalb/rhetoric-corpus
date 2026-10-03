"""Import the corpora collected before this store existed into docs/<COUNTRY>/<source>.jsonl.

Inputs (read-only):
  ~/.cache/rhetoric-global/corpus_kremlin.jsonl     -> docs/RU/kremlin_en.jsonl        (Putin's words per transcript)
  ~/.cache/rhetoric-global/corpus_iran_mfa.jsonl    -> docs/IR/iran_mfa_en.jsonl       (en.mfa.ir statements)
  ~/.cache/tsm-rhetoric/{mfa,mnd,tao}.pkl            -> docs/CN/{mfa_cn,mnd_cn,tao_cn}.jsonl (ALL rows, not only Taiwan)
  ~/Desktop/TSM!!!/10 - Data & Databases/june_july_2026_intake/{mfa,mnd,tao}/*.csv     (May-Jun 2026 intake)
  ~/Projects/tsm-strait-layers/tools/rhetoric-heatmap/scripts/cache/*_2026-07_to_2026-09.csv (Jul-Sep 2026)
  ~/Desktop/TSM!!!/10 - Data & Databases/ALEXA_EXPORT_2026-07/jsonl/*.jsonl -> docs/CN/prc_statemedia.jsonl
  ~/.cache/tsm-rhetoric/statemedia.pkl (headline index) -> docs/CN/prc_statemedia_headlines.jsonl (title only;
      rows whose article is already in the Alexa full-text export are skipped)

English and Chinese text of the same PRC Q&A become two documents (id suffix :en / :zh) so every document
has one language. Ids are content hashes (date + text), so re-running gives the same ids and a Q&A held
in two inputs (pickle and intake CSV) collapses to one document. Each run rewrites these files.

Usage: uv run python scripts/import_existing.py [--only kremlin,iran,prc,alexa,headlines]
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import logging
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collectors"))
from lib import write_docs  # noqa: E402

logger = logging.getLogger("import")
HOME = Path.home()
RG = HOME / ".cache" / "rhetoric-global"
PKL = HOME / ".cache" / "tsm-rhetoric"
TSMDATA = HOME / "Desktop" / "TSM!!!" / "10 - Data & Databases"
INTAKE = TSMDATA / "june_july_2026_intake"
ALEXA = TSMDATA / "ALEXA_EXPORT_2026-07" / "jsonl"
HEATMAP_CACHE = HOME / "Projects" / "tsm-strait-layers" / "tools" / "rhetoric-heatmap" / "scripts" / "cache"
MFA_EN_INDEX = "https://www.fmprc.gov.cn/eng/xw/fyrbt/lxjzh/"
MND_EN_INDEX = "http://eng.mod.gov.cn/2025xb/P/D_251802/index.html"
TAO_INDEX = "https://www.gwytb.gov.cn/xwdt/xwfb/xwfbh/"
CJK = re.compile(r"[㐀-鿿]")


def mtime_iso(p: Path) -> str:
    return datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat()


def ensure_local(p: Path) -> None:
    """Materialise an iCloud placeholder (dataless file) before reading it."""
    try:
        flags = subprocess.run(["ls", "-lO", str(p)], capture_output=True, text=True).stdout
        if "dataless" in flags:
            logger.info("brctl download %s", p)
            subprocess.run(["brctl", "download", str(p)], check=False)
    except FileNotFoundError:
        pass


def clean(s: object) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s).replace("　", " ").replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s.strip()


def lang_of(text: str) -> str:
    """'zh' when CJK characters make up over 20% of the letters, else 'en' (inputs are EN or ZH only)."""
    letters = re.findall(r"\w", text)
    if not letters:
        return "en"
    return "zh" if len(CJK.findall(text)) / len(letters) > 0.2 else "en"


def hid(*parts: str) -> str:
    h = hashlib.sha1("\x1f".join(p[:400] for p in parts).encode("utf-8")).hexdigest()
    return h[:16]


# ------------------------------------------------------------------------------------------- RU / IR
def import_kremlin() -> int:
    src = RG / "corpus_kremlin.jsonl"
    fetched = mtime_iso(src)
    rows = []
    for line in src.open(encoding="utf-8"):
        r = json.loads(line)
        if not (r.get("text") or "").strip():  # transcript with no words by Putin
            continue
        via = "wayback" if str(r.get("via", "")).startswith("wayback") else "direct"
        rows.append({"id": f"kremlin_en:{r['id']}", "country": "RU", "source": "kremlin_en", "outlet": "official",
                     "org": "Kremlin", "lang": "en", "date": r["date"], "url": r["url"], "title": r["title"],
                     "speaker": "Putin", "kind": "transcript", "text": r["text"], "via": via, "fetched": fetched,
                     "note": "Putin's own words only (speaker-labelled paragraphs)" if r.get("labelled") else
                             "single-voice text (address/article/statement)",
                     "wayback": r["via"].split(":", 1)[1] if via == "wayback" else None})
    return write_docs("RU", "kremlin_en", rows, replace=True)[1]


def import_iran() -> int:
    src = RG / "corpus_iran_mfa.jsonl"
    fetched = mtime_iso(src)
    rows = []
    for line in src.open(encoding="utf-8"):
        r = json.loads(line)
        if not (r.get("text") or "").strip():
            continue
        rows.append({"id": f"iran_mfa_en:{r['id']}", "country": "IR", "source": "iran_mfa_en", "outlet": "official",
                     "org": "MFA", "lang": "en", "date": r["date"], "url": r["url"], "title": r["title"],
                     "speaker": None, "kind": "statement", "text": r["text"],
                     "via": "wayback" if r.get("via") == "wayback" else "direct", "fetched": fetched})
    return write_docs("IR", "iran_mfa_en", rows, replace=True)[1]


# ------------------------------------------------------------------------------------------- PRC
class PrcSink:
    """Collects PRC Q&A documents for one source, one per language, de-duplicated by content id."""

    def __init__(self, source: str, org: str):
        self.source, self.org = source, org
        self.rows: Dict[str, Dict] = {}
        self.by_input: collections.Counter = collections.Counter()

    def add(self, *, date: str, lang: str, question: str, answer: str, url: str, title: str = "",
            speaker: str = "", asker: str = "", kind: str = "qa", translation: str = "", input_name: str,
            fetched: str, url_kind: str = "item", alt_url: str = "") -> None:
        question, answer = clean(question), clean(answer)
        if not (question or answer) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
            return
        text = "\n\n".join(x for x in (question, answer) if x)
        did = f"{self.source}:{hid(date, question, answer)}:{lang}"
        if did in self.rows:
            return
        sp = clean(speaker)
        if sp.lower() in ("", "nan", "answer", "n/a"):
            sp = ""
        self.rows[did] = {
            "id": did, "country": "CN", "source": self.source, "outlet": "official", "org": self.org, "lang": lang,
            "date": date, "url": url or {"MFA": MFA_EN_INDEX, "MND": MND_EN_INDEX}.get(self.org, TAO_INDEX),
            "title": clean(title) or f"{self.org} press conference, {date}", "speaker": sp or None, "kind": kind,
            "text": text, "via": "export", "fetched": fetched, "asker": clean(asker) or None,
            "translation": translation or None, "input": input_name,
            "url_kind": url_kind if url else "index", "alt_url": alt_url or None}
        self.by_input[input_name] += 1

    def write(self) -> int:
        rows = sorted(self.rows.values(), key=lambda r: (r["date"], r["id"]))
        return write_docs("CN", self.source, rows, replace=True)[1]


def _date(v) -> str:
    try:
        return pd.to_datetime(v).strftime("%Y-%m-%d")
    except Exception:
        return ""


def import_prc_pickle(name: str, sink: PrcSink, mfa_index: Dict[str, str]) -> None:
    p = PKL / f"{name}.pkl"
    df = pd.read_pickle(p)
    fetched = mtime_iso(p)
    for r in df.itertuples():
        d = _date(r.Date)
        url = clean(r.Original_URL)
        if r.Record_Stream == "archive_statement":
            text = clean(r.Text)
            if not text:
                continue
            lang = lang_of(text)
            day_url = mfa_index.get(d, "") if name == "mfa" and lang == "en" else ""
            sink.add(date=d, lang=lang, question="", answer=text, url=url or day_url, title=clean(r.Title),
                     speaker=clean(r.Spokesperson), kind="qa",
                     translation="tsm" if lang == "en" and not str(r.Source).lower().startswith("official") else
                     ("official" if lang == "en" else "original"),
                     input_name=f"{name}.pkl archive_statement", fetched=fetched,
                     url_kind="item" if url else ("day" if day_url else "index"))
            continue
        # pressconf_qa: English (TSM/official) and Chinese original as separate documents.
        q, a = clean(r.Question_verbatim), clean(r.Answer_verbatim)
        qz, az = clean(getattr(r, "Question_CN", "")), clean(getattr(r, "Answer_CN", ""))
        en_url = mfa_index.get(d, "") if name == "mfa" else ""
        title = clean(r.Title)
        if q or a:
            lang = lang_of(q + a)
            sink.add(date=d, lang=lang, question=q, answer=a, url=(en_url or url) if lang == "en" else url,
                     title=title, speaker=r.Spokesperson, asker=r.Asker, translation="tsm" if lang == "en" else "original",
                     input_name=f"{name}.pkl pressconf_qa", fetched=fetched, alt_url=url if en_url else "")
        if qz or az:
            sink.add(date=d, lang="zh", question=qz, answer=az, url=url or en_url, title=title,
                     speaker=clean(getattr(r, "Spokesperson_CN", "")) or r.Spokesperson,
                     asker=clean(getattr(r, "Asker_CN", "")) or r.Asker, translation="original",
                     input_name=f"{name}.pkl pressconf_qa", fetched=fetched)


def import_prc_csv(path: Path, sink: PrcSink, kind: str) -> None:
    if not path.exists():
        logger.warning("missing %s", path)
        return
    ensure_local(path)
    df = pd.read_csv(path, dtype=str).fillna("")
    fetched = mtime_iso(path)
    tag = path.name
    for r in df.to_dict("records"):
        d = _date(r.get("Date"))
        title = r.get("Title", "")
        if kind == "mfa":
            sink.add(date=d, lang=lang_of(r["Question_verbatim"] + r["Answer_verbatim"]), question=r["Question_verbatim"],
                     answer=r["Answer_verbatim"], url=r["Original_URL"], title=title, speaker=r["Spokesperson"],
                     asker=r.get("Asker", ""), translation="official", input_name=tag, fetched=fetched)
        elif kind == "mnd":
            official = r.get("Source", "").startswith("Official English")
            if r["Question_verbatim"] or r["Answer_verbatim"]:
                sink.add(date=d, lang=lang_of(r["Question_verbatim"] + r["Answer_verbatim"]), question=r["Question_verbatim"],
                         answer=r["Answer_verbatim"], url=r["Original_URL"] or r.get("Original_URL_zh", ""),
                         title=title or r.get("Briefing_Type", ""), speaker=r["Spokesperson"], asker=r.get("Asker", ""),
                         translation="official" if official else "tsm", input_name=tag, fetched=fetched)
            if r.get("Question_verbatim_zh") or r.get("Answer_verbatim_zh"):
                sink.add(date=d, lang="zh", question=r["Question_verbatim_zh"], answer=r["Answer_verbatim_zh"],
                         url=r.get("Original_URL_zh") or r["Original_URL"], title=title or r.get("Briefing_Type", ""),
                         speaker=r["Spokesperson"], asker=r.get("Asker", ""), translation="original",
                         input_name=tag, fetched=fetched)
        else:  # tao: Chinese original + machine translation
            sink.add(date=d, lang="zh", question=r["Question_verbatim"], answer=r["Answer_verbatim"],
                     url=r["Original_URL"], title=title, speaker=r["Spokesperson"], asker=r.get("Asker", ""),
                     translation="original", input_name=tag, fetched=fetched)
            if r.get("Answer_EN_MT") or r.get("Question_EN_MT"):
                eng = r.get("MT_Engine") or "google"
                sink.add(date=d, lang="en", question=r.get("Question_EN_MT", ""), answer=r.get("Answer_EN_MT", ""),
                         url=r["Original_URL"], title=r.get("Title_EN_MT") or title, speaker=r["Spokesperson"],
                         asker=r.get("Asker", ""), translation=f"mt:{eng}", input_name=tag, fetched=fetched)


def import_prc() -> Dict[str, int]:
    idx_p = HEATMAP_CACHE / "mfa_en_index.json"
    mfa_index = json.loads(idx_p.read_text()) if idx_p.exists() else {}
    out = {}
    for name, org in (("mfa", "MFA"), ("mnd", "MND"), ("tao", "TAO")):
        sink = PrcSink(f"{name}_cn", org)
        import_prc_pickle(name, sink, mfa_index)
        import_prc_csv(INTAKE / name / f"{name}_2026-05_to_2026-06.csv", sink, name)
        import_prc_csv(HEATMAP_CACHE / f"{name}_2026-07_to_2026-09.csv", sink, name)
        out[sink.source] = sink.write()
        logger.info("%s: %d docs; per input %s", sink.source, out[sink.source], dict(sink.by_input))
    return out


# ------------------------------------------------------------------------------------------- state media
def import_alexa() -> tuple:
    rows: Dict[str, Dict] = {}
    for p in sorted(ALEXA.glob("prc_state_media_*.jsonl")):
        ensure_local(p)
        lang = "zh" if "_CN_" in p.name else "en"
        fetched = mtime_iso(p)
        for line in p.open(encoding="utf-8"):
            r = json.loads(line)
            text = clean(r.get("body_text"))
            link = r.get("link") or ""
            if not text or not link:
                continue
            did = "prc_statemedia:" + hashlib.sha1(link.encode()).hexdigest()[:16]
            if did in rows:
                continue
            outlet = re.sub(r"_(EN|CN)$", "", r.get("outlet", ""))
            rows[did] = {"id": did, "country": "CN", "source": "prc_statemedia", "outlet": "state_media", "org": outlet,
                         "lang": lang, "date": r["day"], "url": link, "title": clean(r.get("title")), "speaker": None,
                         "kind": "article", "text": text, "via": "export", "fetched": fetched,
                         "category": r.get("category") or None, "input": p.name}
    n = write_docs("CN", "prc_statemedia", sorted(rows.values(), key=lambda r: (r["date"], r["id"])), replace=True)[1]
    return n, {r["url"] for r in rows.values()}


def import_headlines(have_urls: set) -> int:
    p = PKL / "statemedia.pkl"
    df = pd.read_pickle(p)
    fetched = mtime_iso(p)
    rows = {}
    for r in df.itertuples():
        link, title = clean(r.Link), clean(r.Title)
        d = _date(r.Date)
        if not link or not title or not d or link in have_urls:
            continue
        did = "prc_statemedia_headlines:" + hashlib.sha1(link.encode()).hexdigest()[:16]
        rows[did] = {"id": did, "country": "CN", "source": "prc_statemedia_headlines", "outlet": "state_media",
                     "org": clean(r.Outlet), "lang": "zh" if r.Language == "CN" else "en", "date": d, "url": link,
                     "title": title, "speaker": None, "kind": "headline", "text": title, "via": "export",
                     "fetched": fetched, "input": clean(r.Source_File)}
    return write_docs("CN", "prc_statemedia_headlines", sorted(rows.values(), key=lambda r: (r["date"], r["id"])),
                      replace=True)[1]


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", default="kremlin,iran,prc,alexa,headlines")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    only = set(a.only.split(","))
    counts = {}
    if "kremlin" in only:
        counts["kremlin_en"] = import_kremlin()
    if "iran" in only:
        counts["iran_mfa_en"] = import_iran()
    if "prc" in only:
        counts.update(import_prc())
    urls: set = set()
    if "alexa" in only or "headlines" in only:
        n, urls = import_alexa()
        counts["prc_statemedia"] = n
    if "headlines" in only:
        counts["prc_statemedia_headlines"] = import_headlines(urls)
    for k, v in counts.items():
        print(f"{k}\t{v}")


if __name__ == "__main__":
    main()
