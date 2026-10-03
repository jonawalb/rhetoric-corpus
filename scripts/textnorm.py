"""Text helpers shared by the index builder, the search tools and the public builder.

norm() is a strict 1:1 character mapping (string length never changes), so offsets computed on the
normalised text are valid on the original. It folds variants the FTS tokenizer would not:
Russian ё -> е, Arabic yeh/kaf -> Persian forms, Arabic-Indic and Persian digits -> ASCII.
"""
from __future__ import annotations

import re
from typing import Iterator, List

_MAP = {"ё": "е", "Ё": "Е", "ي": "ی", "ى": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه"}
_MAP.update({chr(0x0660 + i): str(i) for i in range(10)})
_MAP.update({chr(0x06F0 + i): str(i) for i in range(10)})
_TABLE = str.maketrans(_MAP)
# Character classes for highlight regexes: a query letter also matches its folded variants.
_VARIANTS = {"е": "[еёЕЁ]", "Е": "[еёЕЁ]", "ی": "[یيى]", "ک": "[کك]", "ه": "[هۀة]"}

CJK_RE = re.compile(r"[ᄀ-ᇿ぀-ヿ㄰-㆏㐀-䶿一-鿿가-힯豈-﫿]")
CJK_LANGS = ("zh", "ko", "ja")
MAX_SENT = 300


def norm(s: str) -> str:
    return (s or "").translate(_TABLE)


def has_cjk(s: str) -> bool:
    return bool(CJK_RE.search(s or ""))


def char_class(ch: str) -> str:
    """Regex for one query character, matching case and folded variants."""
    if ch in _VARIANTS:
        return _VARIANTS[ch]
    low = ch.lower()
    if low in _VARIANTS:
        return _VARIANTS[low]
    return re.escape(ch)


def term_regex(term: str, prefix: bool = False, cjk: bool = False) -> str:
    """Regex source for a query term or phrase: flexible whitespace/punctuation between words."""
    words = re.findall(r"\w+", term)
    parts = ["".join(char_class(c) for c in w) for w in words]
    body = r"\W*".join(parts) if has_cjk(term) else r"\W+".join(parts)
    if cjk or has_cjk(term):
        return body + (r"\w*" if prefix else "")
    tail = r"\w*" if prefix else r"(?!\w)"
    return r"(?<!\w)" + body + tail


_SENT_END = re.compile(r"(?<=[.!?…])[\"'”»)\]]*\s+(?=[\"'“«(\[¿¡]?[A-ZÀ-ÖØ-ÞĞİŞА-ЯЁ0-9\u0600-\u06ff\u3400-\u9fff\uac00-\ud7af])|(?<=[。！？；])")


def _cut(piece: str, limit: int) -> Iterator[str]:
    """Split a long piece at the last comma/space before `limit` characters."""
    while len(piece) > limit:
        window = piece[:limit]
        cut = max(window.rfind("，"), window.rfind("、"), window.rfind(", "), window.rfind("; "), window.rfind("؛"),
                  window.rfind("،"))
        if cut < limit * 0.4:
            cut = window.rfind(" ")
        if cut < limit * 0.4:
            cut = limit - 1
        yield piece[: cut + 1].strip()
        piece = piece[cut + 1:].strip()
    if piece:
        yield piece


def sentences(text: str, limit: int = MAX_SENT) -> List[str]:
    """Split text into sentences of at most `limit` characters (long sentences are cut at a comma/space)."""
    out: List[str] = []
    for para in re.split(r"\n+", text or ""):
        para = re.sub(r"\s+", " ", para).strip()
        if not para:
            continue
        for s in _SENT_END.split(para):
            s = s.strip()
            if s:
                out.extend(_cut(s, limit))
    return [s for s in out if len(s) >= 2]


# ------------------------------------------------------------------------------ public-index tokenisation
# Mirrored exactly in tools/rhetoric-search/js/engine.js (fold / tokens / bucketOf). Keep them in sync.
import unicodedata as _ud  # noqa: E402
from functools import lru_cache as _lru  # noqa: E402

_CJK_CHAR = re.compile(r"[ᄀ-ᇿ぀-ヿ㄰-㆏㐀-䶿一-鿿가-힯豈-﫿]")
# CJK runs, else runs of letters/digits that are not CJK (so "NNSA近日" gives "nnsa" + 近, 日, 近日).
_CJK_CLS = r"\u1100-\u11ff\u3040-\u30ff\u3130-\u318f\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff"
_TOK = re.compile(rf"[{_CJK_CLS}]+|(?:(?![{_CJK_CLS}])[^\W_])+")


@_lru(maxsize=65536)
def fold_char(c: str) -> str:
    """One character, lower-cased, variant-folded and stripped of diacritics; always one character."""
    if "\uac00" <= c <= "\ud7af":  # Hangul syllables: NFD would reduce them to their first jamo
        return c
    c = c.translate(_TABLE).lower()[:1] or c
    base = _ud.normalize("NFD", c)[:1]
    return base if base else c


def fold(s: str) -> str:
    return "".join(fold_char(c) for c in s)


def index_tokens(text: str) -> set:
    """Tokens for the public shard index: folded words; CJK runs give every character and bigram."""
    out = set()
    for m in _TOK.finditer(fold(text)):
        w = m.group(0)
        if _CJK_CHAR.match(w):
            out.update(w)
            out.update(w[i:i + 2] for i in range(len(w) - 1))
        elif not (w.isdigit() and len(w) > 4):  # long numbers: not worth indexing
            out.add(w)
    return out


def bucket_of(token: str, n: int) -> int:
    """FNV-1a (32-bit) over the code points of the first two characters, mod n."""
    h = 0x811C9DC5
    for ch in token[:2]:
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h % n


def index_token_counts(text: str) -> dict:
    """Like index_tokens, but {token: occurrences}. Used for the public media index (no text shipped)."""
    out: dict = {}
    for m in _TOK.finditer(fold(text)):
        w = m.group(0)
        if _CJK_CHAR.match(w):
            grams = list(w) + [w[i:i + 2] for i in range(len(w) - 1)]
        elif w.isdigit() and len(w) > 4:
            continue
        else:
            grams = [w]
        for g in grams:
            out[g] = out.get(g, 0) + 1
    return out
