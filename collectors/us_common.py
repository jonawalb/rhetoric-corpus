"""Shared parsing helpers for the US baseline collectors (State Department, White House)."""
from __future__ import annotations

import gzip
import re
from datetime import datetime
from typing import Optional

from lib import clean_html

BLOCK_MARK = "Web Page Blocked"  # local network web filter page: never treat as a document
_BLOCKS = re.compile(r"<(p|h[2-4]|li|blockquote)\b[^>]*>(.*?)</\1>", re.S | re.I)


def decode_body(body: Optional[bytes]) -> Optional[str]:
    """Bytes -> text, gunzipping raw Wayback `id_` copies; None for empty or filter pages."""
    if not body:
        return None
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except OSError:
            return None
    html = body.decode("utf-8", "ignore")
    if BLOCK_MARK in html[:5000]:
        return None
    return html


def paragraphs(fragment: str) -> str:
    """Join the text of <p>/<h2-4>/<li>/<blockquote> blocks of an HTML fragment, one per line."""
    out = []
    for _, inner in _BLOCKS.findall(fragment):
        t = clean_html(inner).replace("\n", " ").strip()
        if t:
            out.append(t)
    return "\n".join(out)


def parse_long_date(s: str) -> Optional[str]:
    """'July 29, 2025' -> '2025-07-29'."""
    s = re.sub(r"\s+", " ", s).strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def title_of(html: str) -> str:
    m = re.search(r"<h1\b[^>]*>(.*?)</h1>", html, re.S | re.I)
    return clean_html(m.group(1)).replace("\n", " ").strip() if m else ""
