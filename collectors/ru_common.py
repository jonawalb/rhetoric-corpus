"""Shared helpers for the Russian (RU) collectors, on top of lib.py.

Adds what lib.py does not cover:
  * robots_ok(): Google-style robots.txt matching (``*`` and ``$`` wildcards, Allow/Disallow
    longest-match). Python's urllib.robotparser treats ``*`` literally, which would silently allow
    URLs that rules such as ``Disallow: /*/?*`` (ria.ru) forbid. Collectors call BOTH checks.
  * KEYWORD_RE (nuclear / strategic / U.S.-arms terms). Since 2026-10-02 it is NOT a collection filter for
    ru_statemedia.py or ru_mil.py (they sample topic-neutrally); kept for term reports and keyword_hit().
  * PLUTONIUM term list used for the plutonium-pit report.
  * small HTML utilities (balanced <div> extraction) and a batched, resumable queue runner.
"""
from __future__ import annotations

import logging
import re
import urllib.parse
from datetime import date, timedelta
from typing import Callable, Dict, Iterable, List, Optional

import lib

logger = logging.getLogger("rhetoric-corpus.ru")

# ----------------------------------------------------------------------------------------- robots
_RULES: Dict[str, List[tuple]] = {}


def _parse_robots(body: str) -> List[tuple]:
    """Rules (allow: bool, pattern) for the group that applies to our UA ('*' unless named)."""
    groups: List[tuple] = []  # (agents, rules)
    agents: List[str] = []
    rules: List[tuple] = []
    last_was_agent = False
    for raw in body.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (s.strip() for s in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if not last_was_agent and (agents or rules):
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(v.lower())
            last_was_agent = True
        elif k in ("allow", "disallow"):
            last_was_agent = False
            if v:
                rules.append((k == "allow", v))
        else:
            last_was_agent = False
    if agents or rules:
        groups.append((agents, rules))
    token = "rhetoric-corpus-research"
    for ags, rls in groups:
        if any(a != "*" and a in token for a in ags):
            return rls
    out: List[tuple] = []
    for ags, rls in groups:
        if "*" in ags:
            out.extend(rls)
    return out


def _pat_to_re(p: str) -> re.Pattern:
    end = p.endswith("$")
    body = re.escape(p[:-1] if end else p).replace(r"\*", ".*")
    return re.compile(body + ("$" if end else ""))


def robots_ok(url: str) -> bool:
    """Wildcard-aware robots.txt check (in addition to lib.robots_allowed)."""
    if not lib.robots_allowed(url):
        return False
    p = urllib.parse.urlsplit(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _RULES:
        body = ""
        try:
            res = lib.fetch_meta(base + "/robots.txt", retries=1, timeout=30)
            body = res["body"] or ""
        except Exception as e:  # noqa: BLE001 - unreachable robots = no rules
            logger.info("robots.txt unreadable for %s: %s", base, e)
        _RULES[base] = [] if "<html" in body.lower()[:500] else _parse_robots(body)
    path = p.path or "/"
    if p.query:
        path += "?" + p.query
    best = None  # (length, allow)
    for allow, pat in _RULES[base]:
        if _pat_to_re(pat).match(path):
            ln = len(pat)
            if best is None or ln > best[0] or (ln == best[0] and allow):
                best = (ln, allow)
    return True if best is None else best[1]


# ----------------------------------------------------------------------------------------- keywords
# Nuclear / strategic / U.S.-arms terms (the plutonium-pit test case). Not a collection filter for the RU
# state-media or MoD collectors any more (see ru_statemedia.py docstring); kept for reports.
KEYWORDS_RU = [
    r"ядерн", r"атомн\w* (?:оружи|бомб|арсенал|подводн)", r"плутони", r"оружейн\w* уран",
    r"стратегическ\w* (?:стабильн|наступательн|ядерн|вооружен|сдерживан|бомбардировщ)",
    r"\bД?СНВ\b", r"\bДРСМД\b", r"\bРСМД\b", r"контрол\w* над вооружени", r"нераспространени",
    r"боеголов", r"боезаряд", r"межконтинентальн", r"\bМБР\b", r"баллистическ", r"гиперзвук",
    r"Сармат", r"Посейдон", r"Буревестник", r"Орешник", r"Авангард", r"Минитмен", r"Сентинел",
    r"Лос-Аламос", r"Саванн\w*[- ]Ривер", r"\bNNSA\b", r"Роки[- ]Флэтс", r"\bW87", r"\bW93", r"\bB61",
    r"Золот\w* куп", r"\bПРО\b", r"противоракетн", r"ядерн\w* испытани", r"Росатом", r"Пентагон",
    r"разучил", r"сердечник",
]
KEYWORDS_EN = [
    r"nuclear", r"plutonium", r"strategic stability", r"strategic (?:offensive|arms|weapons|forces|bomber)",
    r"New START", r"\bSTART\b", r"\bINF\b", r"arms control", r"non-?proliferation", r"warhead", r"\bICBMs?\b",
    r"ballistic missile", r"hypersonic", r"Sarmat", r"Poseidon", r"Burevestnik", r"Oreshnik", r"Avangard",
    r"Minuteman", r"Sentinel", r"Los Alamos", r"Savannah River", r"\bNNSA\b", r"Rocky Flats", r"\bW87",
    r"\bW93", r"\bB61", r"Golden Dome", r"missile defen[cs]e", r"Rosatom", r"Pentagon",
]
KEYWORD_RE = re.compile("|".join(KEYWORDS_RU + KEYWORDS_EN), re.I)

# Plutonium-pit terms for the report (from the shared brief).
PLUTONIUM_TERMS = [
    r"плутониев\w* сердечник\w*", r"плутониев\w* ядр\w*", r"ядерн\w* сердечник\w*", r"«питы?»",
    r"Лос-Аламос\w*", r"Саванн\w*[- ]Ривер", r"\bW87-1\b", r"Роки[- ]Флэтс", r"\bNNSA\b",
    r"Национальн\w* управлени\w* по ядерной безопасности", r"ядерн\w* арсенал\w* США",
    r"модернизаци\w* ядерного арсенала", r"разучил\w*", r"деградаци\w* ядерн\w* инфраструктур\w*",
    r"оружейн\w* плутони\w*", r"соглашени\w* (?:по|об) утилизации плутония", r"утилизаци\w* плутония",
    r"plutonium pits?", r"plutonium cores?", r"pit production", r"war[- ]reserve pit", r"\bPF-4\b",
    r"diamond stamp", r"Los Alamos", r"Savannah River", r"плутони\w*", r"plutonium",
]
PLUTONIUM_RE = re.compile("|".join(PLUTONIUM_TERMS), re.I)


def keyword_hit(*texts: Optional[str]) -> bool:
    return any(t and KEYWORD_RE.search(t) for t in texts)


# ----------------------------------------------------------------------------------------- html
def balanced_div(html: str, marker: str) -> Optional[str]:
    """Inner HTML of the <div> whose opening tag contains `marker` (first match), or None."""
    i = html.find(marker)
    if i < 0:
        return None
    start = html.rfind("<div", 0, i)
    if start < 0:
        return None
    depth = 0
    for m in re.finditer(r"<(/?)div\b[^>]*>", html[start:], re.I):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            open_end = html.find(">", start) + 1
            return html[open_end:start + m.start()]
    return html[html.find(">", start) + 1:]


MONTHS_RU = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
             "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}


def ru_date(text: str) -> Optional[str]:
    """'20 августа 2026 года' -> '2026-08-20' (first Russian long-form date in text)."""
    m = re.search(r"(\d{1,2})\s+(январ|феврал|марта?|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\s+(\d{4})",
                  text, re.I)
    if not m:
        return None
    key = m.group(2).lower()
    mon = next(v for k, v in sorted(MONTHS_RU.items(), key=lambda kv: -len(kv[0])) if key.startswith(k))
    return f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(1)):02d}"


def dmy(s: str) -> Optional[str]:
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", s or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def recent_cutoff(days: int = 90) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


# ----------------------------------------------------------------------------------------- runner
def run_queue(items: Iterable[Dict], state: "lib.State", parse: Callable[[Dict], Optional[List[Dict]]],
              country: str, source_of: Callable[[Dict], str], batch: int = 10, save_every: int = 10) -> Dict[str, int]:
    """Process queue items {"url":..., ...}: parse(item) -> rows (or None = failed, [] = skipped).

    The state key is item["key"] when present, else item["url"] (lets a new sampling rule re-visit URLs
    that an older rule fetched and rejected).

    Marks parsed items done in `state`; failed ones are listed under state['failed'] and are NOT marked
    done, so a restart retries them."""
    pending: Dict[str, List[Dict]] = {}
    stats = {"seen": 0, "rows": 0, "skipped": 0, "failed": 0}
    failed = set(state.get("failed", []))

    def flush() -> None:
        for src, rows in pending.items():
            if rows:
                added, total = lib.write_docs(country, src, rows)
                stats["rows"] += added
                logger.info("wrote %d rows to %s (total %d)", added, src, total)
        pending.clear()
        state["failed"] = sorted(failed)
        state.save()

    for n, it in enumerate(items, 1):
        key = it.get("key") or it["url"]
        if state.is_done(key):
            continue
        stats["seen"] += 1
        try:
            rows = parse(it)
        except Exception as e:  # noqa: BLE001 - parser bugs must not kill a multi-hour run
            logger.exception("parse error %s: %s", key, e)
            rows = None
        if rows is None:
            stats["failed"] += 1
            failed.add(key)
        else:
            failed.discard(key)
            if not rows:
                stats["skipped"] += 1
            for r in rows:
                pending.setdefault(source_of(r), []).append(r)
            state.mark_done(key)
        if sum(len(v) for v in pending.values()) >= batch or stats["seen"] % save_every == 0:
            flush()
    flush()
    logger.info("queue finished: %s", stats)
    return stats


def ctext(fragment: str) -> str:
    """HTML -> text where only block tags (<p>, <br>, <div>, <li>, headings) make line breaks: the
    source's hard line-wraps are collapsed first, so paragraphs are not split mid-sentence."""
    return lib.clean_html(re.sub(r"\s+", " ", fragment or ""))
