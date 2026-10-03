"""Query parsing, Russian word-form expansion and search over index/corpus.sqlite.

Query language (case of operators matters):
  plutonium core              no operators: the whole query is one phrase
  "plutonium pit" OR 钚芯      quoted phrases, OR / AND / NOT (binary: a NOT b), parentheses
  pit* production             a trailing * makes a prefix term (implicit AND between terms)
  plutonium NEAR/10 Alamos    both terms within 10 words of each other
Queries containing Chinese/Korean/Japanese characters run on the trigram table (substring match). Terms
shorter than 3 CJK characters (e.g. 钚芯) cannot use the trigram index; those queries are answered by a
scan of the CJK documents (a second or so).

Russian morphology (morph=True): each Cyrillic word becomes a prefix term on the stem shared by all of its
dictionary forms (pymorphy3; Snowball stemmer for words pymorphy3 does not know), e.g. плутониевый ->
плутониев*, сердечник -> сердечник*. Where that stem would be shorter than 4 letters (ядро: ядро, ядра,
ядер...) the word expands to an OR of its dictionary forms instead.
"""
from __future__ import annotations

import bisect
import functools
import itertools
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from textnorm import CJK_LANGS, has_cjk, norm, term_regex

TOKEN = re.compile(r'"[^"]*"\*?|\(|\)|NEAR/\d+|\bNEAR\b|[^\s()"]+')
OPS = {"AND", "OR", "NOT"}
CYR = re.compile(r"[А-Яа-яЁё]")
MAX_EXPAND = 64


# ---------------------------------------------------------------------------------------------- morphology
@functools.lru_cache(maxsize=1)
def _morph():
    try:
        import pymorphy3  # type: ignore
        return pymorphy3.MorphAnalyzer()
    except Exception:  # not installed: Snowball only
        return None


@functools.lru_cache(maxsize=1)
def _snowball():
    import snowballstemmer  # type: ignore
    return snowballstemmer.stemmer("russian")


def morph_engine() -> str:
    return "pymorphy3" if _morph() else "snowball"


def ru_word(word: str) -> Tuple[str, object]:
    """('prefix', stem) | ('forms', [forms]) | ('exact', word) for one Russian word."""
    w = norm(word.lower())
    if not CYR.search(w) or len(w) < 4:
        return ("exact", w)
    m = _morph()
    forms: List[str] = []
    if m is not None:
        p = m.parse(w)[0]
        if p.is_known:
            # Comparatives (поядернее) and short adjectives (ядерен) would cut the shared stem too short.
            forms = sorted({norm(f.word) for f in p.lexeme if not ({"COMP", "ADJS"} & set(f.tag.grammemes))})
    if forms:
        lcp = forms[0]
        for f in forms[1:]:
            while not f.startswith(lcp):
                lcp = lcp[:-1]
        if len(lcp) >= 4:
            return ("prefix", lcp)
        return ("forms", forms)
    stem = _snowball().stemWord(w)
    return ("prefix", stem) if len(stem) >= 4 else ("exact", w)


# ---------------------------------------------------------------------------------------------- AST
@dataclass
class Term:
    text: str
    prefix: bool = False
    phrase: bool = False
    alts: List[List[Tuple[str, bool]]] = field(default_factory=list)  # alternatives: list of (word, prefix) sequences
    regex: str = ""

    @property
    def cjk(self) -> bool:
        return has_cjk(self.text)


def _q(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def _phrase_fts(seq: List[Tuple[str, bool]]) -> str:
    """FTS5 phrase from (word, prefix) pairs: "a"* + "b"."""
    return " + ".join(_q(w) + ("*" if p else "") for w, p in seq)


def make_term(raw: str, morph: bool) -> Term:
    phrase = raw.startswith('"')
    prefix = raw.endswith("*")
    text = raw.rstrip("*").strip('"').strip()
    text = norm(text)
    t = Term(text=text, prefix=prefix, phrase=phrase)
    if t.cjk:
        t.alts = [[(text, prefix)]]
        t.regex = term_regex(text, prefix, cjk=True)
        return t
    words = re.findall(r"\w+", text)
    per_word: List[List[Tuple[str, bool]]] = []
    rx_words: List[str] = []
    for i, w in enumerate(words):
        last = i == len(words) - 1
        if morph and CYR.search(w) and not (prefix and last):
            kind, val = ru_word(w)
            if kind == "prefix":
                per_word.append([(val, True)])
                rx_words.append(_rx_word(val, True))
                continue
            if kind == "forms":
                per_word.append([(f, False) for f in val])
                rx_words.append("(?:" + "|".join(_rx_word(f, False) for f in sorted(val, key=len, reverse=True)) + ")")
                continue
        p = prefix and last
        per_word.append([(w.lower(), p)])
        rx_words.append(_rx_word(w, p))
    combos = list(itertools.islice(itertools.product(*per_word), MAX_EXPAND))
    t.alts = [list(c) for c in combos]
    t.regex = r"(?<!\w)" + r"\W+".join(rx_words) + ("" if prefix else r"(?!\w)")
    return t


def _rx_word(w: str, prefix: bool) -> str:
    from textnorm import char_class
    return "".join(char_class(c) for c in w) + (r"\w*" if prefix else "")


def parse(query: str, morph: bool = False):
    """Parse a query into an AST of ('term', Term) / ('and'|'or', [..]) / ('not', a, b) / ('near', [Term], n)."""
    q = query.strip()
    toks = TOKEN.findall(q)
    has_ops = any(t in OPS or t in "()" or t.startswith('"') or t.startswith("NEAR") or t.endswith("*") for t in toks)
    if not has_ops:
        return ("term", make_term('"' + q.replace('"', "") + '"', morph))
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def take():
        nonlocal pos
        if pos >= len(toks):
            raise ValueError("query ends unexpectedly")
        pos += 1
        return toks[pos - 1]

    def primary():
        t = peek()
        if t is None:
            raise ValueError("query ends unexpectedly")
        if t == "(":
            take()
            e = or_expr()
            if take() != ")":
                raise ValueError("missing )")
            return e
        if t in OPS or t == ")" or t.startswith("NEAR"):
            raise ValueError(f"unexpected {t!r}")
        return ("term", make_term(take(), morph))

    def near_expr():
        left = primary()
        while peek() and peek().startswith("NEAR"):
            op = take()
            n = int(op.split("/")[1]) if "/" in op else 10
            right = primary()
            if left[0] not in ("term", "near") or right[0] != "term":
                raise ValueError("NEAR joins plain terms or phrases only")
            terms = (left[1] if left[0] == "near" else [left[1]]) + [right[1]]
            left = ("near", terms, n)
        return left

    def not_expr():
        left = near_expr()
        while peek() == "NOT":
            take()
            left = ("not", left, near_expr())
        return left

    def and_expr():
        items = [not_expr()]
        while peek() and peek() not in ("OR", ")"):
            if peek() == "AND":
                take()
            items.append(not_expr())
        return items[0] if len(items) == 1 else ("and", items)

    def or_expr():
        items = [and_expr()]
        while peek() == "OR":
            take()
            items.append(and_expr())
        return items[0] if len(items) == 1 else ("or", items)

    ast = or_expr()
    if pos != len(toks):
        raise ValueError(f"unexpected {toks[pos]!r}")
    return ast


def terms_of(ast) -> List[Term]:
    if ast[0] == "term":
        return [ast[1]]
    if ast[0] == "near":
        return list(ast[1])
    if ast[0] == "not":
        return terms_of(ast[1]) + terms_of(ast[2])
    return [t for a in ast[1] for t in terms_of(a)]


def positive_terms(ast) -> List[Term]:
    if ast[0] == "term":
        return [ast[1]]
    if ast[0] == "near":
        return list(ast[1])
    if ast[0] == "not":
        return positive_terms(ast[1])
    return [t for a in ast[1] for t in positive_terms(a)]


def to_fts(ast) -> str:
    k = ast[0]
    if k == "term":
        alts = [_phrase_fts(a) for a in ast[1].alts]
        return alts[0] if len(alts) == 1 else "(" + " OR ".join(alts) + ")"
    if k == "near":
        combos = itertools.islice(itertools.product(*[t.alts for t in ast[1]]), MAX_EXPAND)
        groups = ["NEAR(" + " ".join(_phrase_fts(a) for a in c) + f", {ast[2]})" for c in combos]
        return groups[0] if len(groups) == 1 else "(" + " OR ".join(groups) + ")"
    if k == "not":
        return f"({to_fts(ast[1])} NOT {to_fts(ast[2])})"
    joiner = " AND " if k == "and" else " OR "
    return "(" + joiner.join(to_fts(a) for a in ast[1]) + ")"


# ---------------------------------------------------------------------------------------------- python evaluation
_WORD = re.compile(r"\w+")


def _rx(t: Term) -> re.Pattern:
    return _compile(t.regex)


@functools.lru_cache(maxsize=512)
def _compile(src: str) -> re.Pattern:
    return re.compile(src, re.I)


def evaluate(ast, text: str) -> bool:
    k = ast[0]
    if k == "term":
        return bool(_rx(ast[1]).search(text))
    if k == "and":
        return all(evaluate(a, text) for a in ast[1])
    if k == "or":
        return any(evaluate(a, text) for a in ast[1])
    if k == "not":
        return evaluate(ast[1], text) and not evaluate(ast[2], text)
    # near: some match of every term within n words
    starts = [m.start() for m in _WORD.finditer(text)]
    pos = []
    for t in ast[1]:
        ps = [bisect.bisect_right(starts, m.start()) for m in _rx(t).finditer(text)]
        if not ps:
            return False
        pos.append(ps)
    n = ast[2]
    for p0 in pos[0]:
        if all(any(abs(p - p0) <= n + 4 for p in ps) for ps in pos[1:]):
            return True
    return False


def hit_spans(ast, text: str) -> List[Tuple[int, int]]:
    spans = []
    for t in positive_terms(ast):
        spans += [(m.start(), m.end()) for m in _rx(t).finditer(text) if m.end() > m.start()]
    spans.sort()
    merged: List[Tuple[int, int]] = []
    for s, e in spans:
        if merged and s < merged[-1][1]:
            continue
        merged.append((s, e))
    return merged


def kwic(text: str, span: Tuple[int, int], width: int = 110) -> Tuple[str, str, str]:
    """(left, hit, right) context around span, trimmed to word boundaries, newlines flattened."""
    s, e = span
    a = max(0, s - width)
    b = min(len(text), e + width)
    left, hit, right = text[a:s], text[s:e], text[e:b]
    if a > 0:
        sp = left.find(" ")
        left = "…" + (left[sp + 1:] if 0 <= sp < 25 else left)
    if b < len(text):
        sp = right.rfind(" ")
        right = (right[:sp] if sp > len(right) - 25 else right) + "…"
    flat = lambda x: re.sub(r"\s+", " ", x)
    return flat(left), flat(hit), flat(right)


# ---------------------------------------------------------------------------------------------- search
@dataclass
class Filters:
    country: Sequence[str] = ()
    source: Sequence[str] = ()
    lang: Sequence[str] = ()
    date_from: str = ""
    date_to: str = ""

    def sql(self, alias: str = "d") -> Tuple[str, list]:
        w, args = [], []
        for col, vals in (("country", self.country), ("source", self.source), ("lang", self.lang)):
            vals = [v for v in vals if v]
            if vals:
                w.append(f"{alias}.{col} IN ({','.join('?' * len(vals))})")
                args += [v.upper() if col == "country" else v for v in vals]
        if self.date_from:
            w.append(f"{alias}.date >= ?")
            args.append(self.date_from)
        if self.date_to:
            w.append(f"{alias}.date <= ?")
            args.append(self.date_to)
        return (" AND ".join(w) or "1"), args


@dataclass
class Plan:
    ast: object
    table: str            # fts_words | fts_cjk | scan
    fts: str
    note: str = ""


def _route(ast) -> Tuple[str, str]:
    """(table, note) for a sub-query whose terms are all in one script family."""
    terms = terms_of(ast)
    if any(t.cjk for t in terms):
        if any(t.cjk and len(re.sub(r"\s", "", t.text)) < 3 for t in terms):
            return "scan", "CJK term shorter than 3 characters: scanning CJK documents"
        return "fts_cjk", ""
    return "fts_words", ""


def _mixed(ast) -> bool:
    kinds = {t.cjk for t in terms_of(ast)}
    return len(kinds) > 1


def plan(query: str, morph: bool = False) -> Plan:
    ast = parse(query, morph)
    if _mixed(ast) and ast[0] == "or":
        parts = [(_route(b)[0], to_fts(b) if _route(b)[0] != "scan" else "") for b in ast[1]]
        notes = {_route(b)[1] for b in ast[1]} - {""}
        return Plan(ast, "union", " | ".join(f"{t}:{x}" for t, x in parts), "; ".join(sorted(notes)))
    table, note = _route(ast)
    if _mixed(ast):
        table, note = "scan", "Chinese and non-Chinese terms combined with AND/NEAR/NOT: scanning CJK documents"
    return Plan(ast, table, to_fts(ast) if table != "scan" else "", note)


def _rows(con: sqlite3.Connection, ast, table: str, f: Filters, order: str) -> List[tuple]:
    where, args = f.sql("d")
    if table == "scan":
        langs = [l for l in (f.lang or CJK_LANGS) if l in CJK_LANGS] or ["__none__"]
        # Prefilter on the longest positive CJK term with LIKE (case-sensitive is fine for CJK), then evaluate.
        pos = sorted((t for t in positive_terms(ast) if t.cjk), key=lambda t: -len(t.text))
        like = f"%{pos[0].text}%" if pos and ast[0] != "or" else "%"
        sql = (f"SELECT d.rowid, d.date, d.source, d.country, d.title, d.text FROM docs d WHERE {where} AND d.lang IN "
               f"({','.join('?' * len(langs))}) AND (d.text LIKE ? OR d.title LIKE ?)")
        rows = con.execute(sql, [*args, *langs, like, like]).fetchall()
        return [r[:4] for r in rows if evaluate(ast, norm((r[4] or "") + "\n" + r[5]))]
    sql = (f"SELECT d.rowid, d.date, d.source, d.country FROM {table} JOIN docs d ON d.rowid = {table}.rowid "
           f"WHERE {table} MATCH ? AND {where} ")
    sql += f"ORDER BY bm25({table})" if order == "rank" else ""
    return con.execute(sql, [to_fts(ast), *args]).fetchall()


def candidate_rows(con: sqlite3.Connection, p: Plan, f: Filters, order: str = "date") -> List[tuple]:
    """(rowid, date, source, country) of every matching document; newest first (or by bm25 rank)."""
    if p.table == "union":
        seen: Dict[int, tuple] = {}
        for b in p.ast[1]:
            for r in _rows(con, b, _route(b)[0], f, "date"):
                seen.setdefault(r[0], r)
        rows = list(seen.values())
    else:
        rows = _rows(con, p.ast, p.table, f, order)
        if order == "rank" and p.table != "scan":
            return rows
    return sorted(rows, key=lambda r: (r[1], r[0]), reverse=True)


def doc_hits(con: sqlite3.Connection, p: Plan, rowids: Sequence[int], max_snips: int = 3) -> Dict[int, Dict]:
    out: Dict[int, Dict] = {}
    for chunk in (rowids[i:i + 500] for i in range(0, len(rowids), 500)):
        q = ("SELECT rowid, id, country, source, outlet, org, lang, date, url, title, speaker, kind, via, text FROM docs "
             f"WHERE rowid IN ({','.join('?' * len(chunk))})")
        for r in con.execute(q, list(chunk)):
            d = dict(zip(("rowid", "id", "country", "source", "outlet", "org", "lang", "date", "url", "title",
                          "speaker", "kind", "via"), r[:13]))
            text = r[13]
            spans = hit_spans(p.ast, text)
            tspans = hit_spans(p.ast, d["title"] or "")
            d["hits"] = len(spans) + len(tspans)
            d["snippets"] = [kwic(text, s) for s in spans[:max_snips]]
            if not d["snippets"] and tspans:
                d["snippets"] = [kwic(d["title"], tspans[0])]
            out[r[0]] = d
    return out


def search(con: sqlite3.Connection, query: str, f: Filters = Filters(), limit: int = 50, offset: int = 0,
           morph: bool = False, order: str = "date", max_snips: int = 3) -> Dict:
    p = plan(query, morph)
    rows = candidate_rows(con, p, f, order)
    page = [r[0] for r in rows[offset:offset + limit]]
    hits = doc_hits(con, p, page, max_snips)
    return {"query": query, "fts": p.fts, "table": p.table, "note": p.note, "total_docs": len(rows),
            "results": [hits[i] for i in page if i in hits], "rows": rows, "plan": p}


def hit_counts(con: sqlite3.Connection, p: Plan, rowids: Sequence[int]) -> Dict[int, int]:
    """{rowid: number of hits in title + text}."""
    out: Dict[int, int] = {}
    for chunk in (rowids[i:i + 500] for i in range(0, len(rowids), 500)):
        q = f"SELECT rowid, title, text FROM docs WHERE rowid IN ({','.join('?' * len(chunk))})"
        for rowid, title, text in con.execute(q, list(chunk)):
            out[rowid] = len(hit_spans(p.ast, title or "")) + len(hit_spans(p.ast, text))
    return out


def count_by(con: sqlite3.Connection, p: Plan, rows: Sequence[tuple], by: str,
             hits: Optional[Dict[int, int]] = None) -> List[Tuple[str, int, int]]:
    """[(bucket, documents, hits)] over all matching rows; by = week | month | year | source | country."""
    import datetime as _dt
    if hits is None:
        hits = hit_counts(con, p, [r[0] for r in rows]) if rows else {}
    agg: Dict[str, List[int]] = {}
    for rowid, date, source, country in rows:
        if by == "month":
            k = date[:7]
        elif by == "year":
            k = date[:4]
        elif by == "week":
            dt = _dt.date.fromisoformat(date)
            k = (dt - _dt.timedelta(days=dt.weekday())).isoformat()
        elif by == "country":
            k = country
        else:
            k = source
        a = agg.setdefault(k, [0, 0])
        a[0] += 1
        a[1] += hits.get(rowid, 0)
    return sorted(((k, v[0], v[1]) for k, v in agg.items()), key=lambda x: x[0])


def facets(con: sqlite3.Connection) -> Dict:
    q = lambda sql: [r for r in con.execute(sql)]
    return {"countries": q("SELECT country, count(*) FROM docs GROUP BY country ORDER BY 2 DESC"),
            "sources": q("SELECT source, country, count(*), min(date), max(date) FROM docs GROUP BY source ORDER BY country, source"),
            "langs": q("SELECT lang, count(*) FROM docs GROUP BY lang ORDER BY 2 DESC"),
            "range": q("SELECT min(date), max(date), count(*) FROM docs")[0]}
