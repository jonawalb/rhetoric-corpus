"""Row-level logic for the dataset release: snapshot reading, validation, flattening, Parquet/JSONL writers.

Used by scripts/export_dataset.py. Reads docs/<CC>/<source>.jsonl the same way build_index.py does: only
complete lines (a collector may be appending), bad JSON lines counted, never written to.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

from textnorm import CJK_RE

OFFICIAL_OUTLETS = ("official",)
MEDIA_OUTLETS = ("state_media", "media", "commentary")
REQUIRED = ("id", "country", "source", "lang", "date", "url", "text")
CORE = ("id", "country", "source", "outlet", "org", "lang", "date", "url", "title", "speaker", "kind", "via",
        "fetched", "text")
STR = pa.string()
# Collector extras with a column of their own (everything else goes to the JSON `extra` column).
EXTRAS: Dict[str, pa.DataType] = {
    "wayback": STR, "sample": STR, "section": STR, "tags": pa.list_(STR), "translation": STR, "period": STR,
    "category": STR, "url_kind": STR, "alt_url": STR, "asker": STR, "putin_text": STR, "labelled": pa.bool_(),
    "pair_id": STR, "note": STR, "channel": STR, "president": STR, "site": STR, "pr_no": STR, "dateline": STR,
    "unit": STR, "input": STR, "wayback_ts": STR, "truncated": STR,
}
CONSUMED = set(CORE) | set(EXTRAS) | {"text_scope"}

_BASE_FIELDS = [
    ("id", STR), ("country", STR), ("source", STR), ("outlet", STR), ("org", STR), ("lang", STR),
    ("date", pa.date32()), ("url", STR), ("title", STR), ("speaker", STR), ("kind", STR), ("via", STR),
    ("fetched", pa.timestamp("s", tz="UTC")),
]
OFFICIAL_SCHEMA = pa.schema(
    _BASE_FIELDS + [("text", STR), ("n_chars", pa.int32()), ("n_words", pa.int32()), ("text_sha256", STR),
                    ("text_scope", STR), ("wayback_timestamp", STR)]
    + list(EXTRAS.items()) + [("extra", STR)])
MEDIA_SCHEMA = pa.schema(
    _BASE_FIELDS + [("wayback", STR), ("wayback_timestamp", STR), ("sample", STR), ("section", STR),
                    ("tags", pa.list_(STR)), ("period", STR), ("text_scope", STR), ("n_chars", pa.int32()),
                    ("n_words", pa.int32()), ("text_sha256", STR), ("text_available_locally", pa.bool_())])

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_LANG_RE = re.compile(r"[a-z]{2,3}(-[A-Za-z0-9]+)?")
_STAMP_RE = re.compile(r"(?<!\d)(\d{14})(?!\d)")
_WORD_RE = re.compile(r"\w+")


def read_snapshot(path: Path) -> Tuple[List[Tuple[int, Any]], Dict[str, Any]]:
    """Complete lines of a JSONL file as (line_no, parsed); a partial last line is ignored, bad JSON counted."""
    data = path.read_bytes()
    end = data.rfind(b"\n") + 1
    rows: List[Tuple[int, Any]] = []
    bad: List[int] = []
    for i, line in enumerate(data[:end].split(b"\n"), 1):
        if not line.strip():
            continue
        try:
            rows.append((i, json.loads(line)))
        except json.JSONDecodeError:
            bad.append(i)
    info = {"bytes_read": end, "partial_tail_bytes": len(data) - end,
            "sha256_read": hashlib.sha256(data[:end]).hexdigest(), "lines": len(rows) + len(bad),
            "bad_json_lines": bad}
    return rows, info


def parse_ts(value: Any) -> Optional[datetime]:
    """ISO timestamp -> aware UTC datetime (naive = UTC); None if missing; ValueError if malformed."""
    if value in (None, ""):
        return None
    ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def check_row(row: Any, country_dir: str, source_file: str) -> Optional[str]:
    """Schema check; returns a drop reason or None. Mirrors collectors/lib.validate plus release rules."""
    if not isinstance(row, dict):
        return "not_an_object"
    miss = [k for k in REQUIRED if not isinstance(row.get(k), str) or not row[k].strip()]
    if miss:
        return "missing:" + ",".join(miss)
    if not row["id"].startswith(row["source"] + ":"):
        return "id_not_prefixed_by_source"
    if row["source"] != source_file:
        return "source_differs_from_file"
    if row["country"].upper() != country_dir:
        return "country_differs_from_dir"
    if row.get("outlet") not in OFFICIAL_OUTLETS + MEDIA_OUTLETS:
        return "bad_outlet"
    if not _DATE_RE.fullmatch(row["date"]):
        return "date_not_iso"
    try:
        d = date.fromisoformat(row["date"])
    except ValueError:
        return "date_invalid"
    if not 1990 <= d.year <= 2100:
        return "date_implausible"
    if not _LANG_RE.fullmatch(row["lang"]):
        return "bad_lang"
    if not row["url"].startswith(("http://", "https://")):
        return "bad_url"
    for k in ("org", "title", "speaker", "kind", "via"):
        if row.get(k) is not None and not isinstance(row[k], str):
            return f"bad_type:{k}"
    try:
        parse_ts(row.get("fetched"))
    except (ValueError, TypeError):
        return "bad_fetched"
    return None


def n_words(text: str) -> int:
    cjk = len(CJK_RE.findall(text))
    return cjk + len(_WORD_RE.findall(CJK_RE.sub(" ", text)))


def text_scope(row: Dict[str, Any]) -> str:
    if row.get("text_scope"):
        return str(row["text_scope"])
    text, title = (row.get("text") or "").strip(), (row.get("title") or "").strip()
    if row.get("kind") == "headline" or (title and text == title):
        return "headline"
    if row.get("truncated"):
        return f"lead_{row['truncated']}"
    return "full"


def wayback_stamp(row: Dict[str, Any]) -> Optional[str]:
    for k in ("wayback", "wayback_ts"):
        m = _STAMP_RE.search(str(row.get(k) or ""))
        if m:
            return m.group(1)
    return None


def _coerce(value: Any, typ: pa.DataType) -> Any:
    if value is None:
        return None
    if typ == pa.bool_():
        return value if isinstance(value, bool) else None
    if pa.types.is_list(typ):
        items = value if isinstance(value, list) else [value]
        return [x if isinstance(x, str) else json.dumps(x, ensure_ascii=False) for x in items]
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)


def _base(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: row.get(k) for k, _ in _BASE_FIELDS}
    out["country"] = row["country"].upper()
    out["date"] = date.fromisoformat(row["date"])
    out["fetched"] = parse_ts(row.get("fetched"))
    if out["fetched"] is not None:
        out["fetched"] = out["fetched"].replace(microsecond=0)
    return out


def official_record(row: Dict[str, Any]) -> Dict[str, Any]:
    text = row["text"]
    out = _base(row)
    out.update(text=text, n_chars=len(text), n_words=n_words(text),
               text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(), text_scope=text_scope(row),
               wayback_timestamp=wayback_stamp(row))
    extra: Dict[str, Any] = {}
    for k, typ in EXTRAS.items():
        out[k] = _coerce(row.get(k), typ)
        if row.get(k) is not None and out[k] is None:  # e.g. a non-bool `labelled`: keep the raw value
            extra[k] = row[k]
    extra.update({k: v for k, v in row.items() if k not in CONSUMED})
    out["extra"] = json.dumps(extra, ensure_ascii=False, sort_keys=True) if extra else None
    return out


def media_record(row: Dict[str, Any]) -> Dict[str, Any]:
    text = row["text"]
    scope = text_scope(row)
    out = _base(row)
    out.update(wayback=_coerce(row.get("wayback"), STR), wayback_timestamp=wayback_stamp(row),
               sample=_coerce(row.get("sample"), STR), section=_coerce(row.get("section"), STR),
               tags=_coerce(row.get("tags"), pa.list_(STR)), period=_coerce(row.get("period"), STR),
               text_scope=scope, n_chars=len(text), n_words=n_words(text),
               text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
               text_available_locally=scope != "headline")
    return out


def sort_key(rec: Dict[str, Any]) -> Tuple:
    return (rec["date"], rec["source"], rec["id"])


def write_parquet(records: List[Dict[str, Any]], schema: pa.Schema, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(sorted(records, key=sort_key), schema=schema)
    pq.write_table(table, path, compression="zstd", compression_level=9, row_group_size=20_000)


def jsonl_value(rec: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(rec)
    out["date"] = rec["date"].isoformat()
    out["fetched"] = rec["fetched"].isoformat().replace("+00:00", "Z") if rec["fetched"] else None
    if out.get("extra"):
        out["extra"] = json.loads(out["extra"])
    return out


def write_jsonl_gz(records: List[Dict[str, Any]], path: Path) -> None:
    """Deterministic gzip (mtime 0, no file name) so identical input gives identical bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0,
                                               compresslevel=9) as gz:
        for rec in sorted(records, key=sort_key):
            gz.write((json.dumps(jsonl_value(rec), ensure_ascii=False) + "\n").encode("utf-8"))
