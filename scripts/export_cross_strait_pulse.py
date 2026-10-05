"""Export one month of Cross-Strait Pulse inputs (MFA, MND, TAO streams) from the corpus.

The Pulse (TSM!!!/3 - Cross-Strait Pulse/CROSS STRAIT PULSE NEW) is built by 02_Sources/build_csp_from_md.js from a
hand-written markdown file; every figure in that markdown is derived from the per-agency utterance tables in
03_Data/<agency>/ (one row per Q&A exchange, schema of the TSM intake scrapers). This script rebuilds those tables
from the corpus for any month, in the same column layout, and adds what the hand pipeline did not have:

  mfa_<M>.csv              MFA exchanges, official English (10 columns, = 03_Data/mfa/mfa_*.csv)
  mnd_<M>.csv              MND entries from all three channels (16 columns, = 03_Data/mnd/mnd_*.csv)
  tao_<M>_source_zh.csv    TAO rows, Chinese source text (14 columns, = 03_Data/tao/tao_*_source_zh.csv); the
                           *_EN_MT columns are left empty: the corpus holds no TAO English and none is invented
  row_tone_<M>.csv         per row: scored answer sentences and mean tone, Taiwan-mention flags, phrase hits
  stream_summary_<M>.csv   long table (stream, month, metric, key, value) for the month and the comparison month:
                           counts, briefing days, country tags, podium and questioners, phrase hits, mean tone
  phrase_hits_<M>.csv      every row that uses a tracked formula (PHRASES), with date, speaker and link
  top_quotes_<M>.csv       up to 10 candidate quotes per stream (single official sentences <= 300 characters, with
                           link), ranked Taiwan-related first, then by hostility + threat + escalation
  dashboard_fragment_<M>.md  §II tables and §VI quote candidates in the markdown syntax build_csp_from_md.js reads
                           (a draft fragment to paste from, not an issue)
  COVERAGE.md / provenance.json  sources, rules, counts, and the not-yet-validated caveat for tone

Streams (direct collection only; the `export` copies of TSM's own intake tables in the corpus are not used, so the
output is an independent check on the hand pipeline):
  MFA  mfa_cn_live / mfa_cn_archive, kind=briefing, fmprc.gov.cn/eng/xw/fyrbt/lxjzh/ (English); the Chinese
       mfa.gov.cn transcripts are counted per day as a control
  MND  mnd_cn_live: 例行记者会 (/lxjzh*), 例行新闻发布 (/yzxwfb/), 发言人谈话和答记者问 (/fyrthhdjzw/). Full
       transcripts are split into Q&A; per-topic extracts are used only when a channel-day has no full transcript.
       Official English (eng.mod.gov.cn full transcript, kind=briefing) is attached when its Q&A count matches.
  TAO  tao_cn_live: press-conference transcripts (/xwfbh/, split into Q&A) and standalone releases (/wyly/)

Quotation rule carried into top_quotes: MFA official English verbatim; MND and TAO Chinese verbatim (any English in
an issue must be labelled as a TSM translation). Tone is not yet human-validated (see feeds_common.NOT_VALIDATED).

Usage (from the repo root):
  uv run python -m scripts.export_cross_strait_pulse --month 2026-09 --out-dir "<CSP>/03_Data/corpus_feed_2026-09_v1"
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from scripts.feeds_common import (DIMS, NOT_VALIDATED, connect, index_dir, line_sentences, mean_dims, new_dir,
                                  provenance, sentence_scores, write_csv)
from scripts.semantic.targets import default as gazetteer

logger = logging.getLogger("export_cross_strait_pulse")

MFA_FIELDS = ("Year-Month", "Date", "Title", "Spokesperson", "Asker", "Question_verbatim", "Answer_verbatim", "Text",
              "Country", "Original_URL")
MND_FIELDS = ("Year-Month", "Date", "Title", "Spokesperson", "Asker", "Question_verbatim", "Answer_verbatim", "Text",
              "Country/Topic", "Source", "Original_URL", "Question_verbatim_zh", "Answer_verbatim_zh", "Original_URL_zh",
              "Briefing_Type", "Seq")
TAO_FIELDS = ("Year-Month", "Date", "Title", "Spokesperson", "Asker", "Question_verbatim", "Answer_verbatim", "Text",
              "Country/Topic", "Source", "Original_URL", "Title_EN_MT", "Question_EN_MT", "Answer_EN_MT")
TONE_FIELDS = ("stream", "Date", "Original_URL", "Seq", "Asker", "Spokesperson", "doc_id", "answer_sentences",
               "scored", *DIMS, "taiwan_sentences", "taiwan_scored", *(f"taiwan_{d}" for d in DIMS), "phrases")
QUOTE_FIELDS = ("stream", "rank", "Date", "Spokesperson", "lang", "quote", "chars", "Original_URL", "Seq", "taiwan",
                "targets", *DIMS, "official_english_url", "quote_rule")

# ------------------------------------------------------------------------------------------------ vocabularies
# MFA country tags: copied from TSM's aug_sep_2026_intake/mfa/process_mfa.py (COUNTRY_KW_EN) so counts are comparable.
MFA_COUNTRY: Dict[str, Tuple[str, ...]] = {
    "Taiwan": (r"Taiwan", r"Taipei", r"\bDPP\b", r"Democratic Progressive Party", r"Lai Ching-te", r"Tsai Ing-wen",
               r"cross-Strait", r"Taiwan Strait"),
    "United States": (r"United States", r"\bU\.S\.", r"\bUS\b", r"American", r"Washington", r"White House", r"Trump",
                      r"Biden", r"State Department", r"Pentagon", r"US Congress"),
    "Japan": (r"Japan", r"Japanese", r"Tokyo", r"Takaichi", r"Ishiba", r"Kishida", r"Koizumi", r"Yasukuni", r"Diaoyu",
              r"Senkaku", r"Self-Defense Force"),
    "Philippines": (r"Philippines", r"Philippine", r"Manila", r"Marcos", r"South China Sea", r"Ren'ai Jiao",
                    r"Second Thomas Shoal", r"Huangyan Dao", r"Scarborough", r"Nansha"),
    "South Korea": (r"\bROK\b", r"South Korea", r"Republic of Korea", r"Seoul", r"Lee Jae-myung", r"Yoon Suk-yeol"),
    "DPRK / North Korea": (r"\bDPRK\b", r"North Korea", r"Pyongyang", r"Kim Jong"),
    "Russia": (r"Russia", r"Russian", r"Putin", r"Moscow"),
    "Ukraine": (r"Ukraine", r"Ukrainian", r"Zelensky", r"Zelenskyy", r"Kyiv"),
    "India": (r"\bIndia\b", r"\bIndian\b", r"New Delhi", r"\bModi\b"),
    "EU / Europe": (r"European Union", r"\bEU\b", r"Europe", r"\bNATO\b", r"Brussels", r"Germany", r"France",
                    r"United Kingdom", r"\bUK\b", r"Britain", r"Italy", r"Netherlands"),
    "Middle East / Iran / Israel": (r"Iran", r"Israel", r"Hamas", r"Gaza", r"Red Sea", r"Houthi", r"Netanyahu",
                                    r"Tehran", r"Middle East"),
}
MFA_COUNTRY_RE = {k: re.compile("|".join(v)) for k, v in MFA_COUNTRY.items()}
MFA_SPOKES = {"郭嘉昆": "Guo Jiakun", "毛宁": "Mao Ning", "林剑": "Lin Jian", "汪文斌": "Wang Wenbin",
              "赵立坚": "Zhao Lijian", "华春莹": "Hua Chunying"}
OUTLET_ALIASES = {"Bloom berg": "Bloomberg", "Pheonix TV": "Phoenix TV", "Associated Press": "AP",
                  "The Associated Press": "AP", "Agence France-Presse": "AFP"}
ANNOUNCEMENT = "N/A — opening announcement (no press Q)"

# MND topic tags: copied from aug_sep_2026_intake/mnd/process_mnd.py (TOPIC_KEYWORDS).
MND_TOPICS: Dict[str, Tuple[str, ...]] = {
    "Taiwan": ("Taiwan", "DPP", "Lai Ching-te", "cross-Strait", "reunification", "台湾", "台独", "民进党", "赖清德", "台海",
               "两岸", "统一"),
    "United States": ("United States", "U.S.", "US ", "Washington", "Pentagon", "Trump", "美国", "美方", "美军", "特朗普",
                      "中美"),
    "Japan": ("Japan", "Japanese", "Takaichi", "militarism", "Diaoyu", "Self-Defense Force", "日本", "日方", "高市",
              "军国主义", "钓鱼岛", "自卫队", "防卫省"),
    "Philippines": ("Philippines", "Philippine", "Manila", "Zhongye", "Ren'ai", "Xianbin", "菲律宾", "菲方", "仁爱礁",
                    "中业岛", "仙宾礁"),
    "South China Sea": ("South China Sea", "Nansha", "Xisha", "Huangyan", "Spratly", "南海", "南沙", "西沙", "黄岩岛"),
    "India": ("India", "Indian", "Zangnan", "印度", "印方", "藏南"),
    "ASEAN / Southeast Asia": ("ASEAN", "Thailand", "Cambodia", "Viet Nam", "Vietnam", "Laos", "Singapore", "Indonesia",
                               "Malaysia", "Myanmar", "东盟", "泰国", "柬埔寨", "越南", "老挝", "新加坡", "印尼", "缅甸"),
    "Vietnam": ("Viet Nam", "Vietnam", "越南", "越方"),
    "Russia": ("Russia", "Putin", "俄罗斯", "俄方", "普京"),
    "Ukraine": ("Ukraine", "乌克兰"),
    "North Korea / Korean Peninsula": ("DPRK", "Korean Peninsula", "ROK", "Pyongyang", "朝鲜", "半岛", "韩国"),
    "Middle East": ("Iran", "Israel", "Gaza", "Red Sea", "Houthi", "Hormuz", "Gulf", "伊朗", "以色列", "加沙", "红海",
                    "霍尔木兹", "海湾"),
    "NATO / Europe": ("NATO", "Netherlands", "Dutch", "Serbia", "Germany", "France", "European", "北约", "荷兰",
                      "塞尔维亚", "德国", "法国", "欧洲", "欧盟"),
    "Australia": ("Australia", "Australian", "澳大利亚", "澳方"),
    "Africa": ("Africa", "South Africa", "Abyei", "Seychelles", "非洲", "南非", "阿卜耶伊", "塞舌尔"),
    "Pacific Islands": ("Pacific Island", "太平洋岛国"),
    "Military Exercises": ("exercise", "drill", "joint training", "patrol", "combat readiness", "演习", "联训", "演训",
                           "巡航", "战备"),
    "Nuclear / Arms Control": ("nuclear", "arms control", "missile", "Typhon", "warhead", "核", "军控", "导弹", "堤丰"),
    "Military Diplomacy": ("dialogue", "Xiangshan", "Shangri-La", "consultation", "visit", "exchange",
                           "cooperation between the two militaries", "对话", "香山", "香格里拉", "磋商", "两军关系", "交往",
                           "合作"),
    "Defense Budget / Spending": ("defense budget", "defense expenditure", "arms purchase", "military spending",
                                  "国防开支", "国防预算", "军费", "军购"),
    "Internal Military Affairs": ("recruitment", "conscription", "cadet", "academies", "discipline", "veterans",
                                  "training class", "征兵", "招收", "院校", "纪律", "退役"),
    "AI / Emerging Tech": ("artificial intelligence", "unmanned", "cyber", "space domain", "satellite", "人工智能", "无人",
                           "网络空间", "太空", "卫星"),
    "Peacekeeping / HADR": ("peacekeeping", "UNIFIL", "disaster relief", "hospital ship", "维和", "救灾", "医院船"),
}
MND_SPOKES = {"蒋斌": "Jiang Bin", "吴谦": "Wu Qian", "张晓刚": "Zhang Xiaogang", "谭克非": "Tan Kefei", "陈曦": "Chen Xi"}
MND_CHANNELS = (("/fyrthhdjzw/", "Spokesperson statement (发言人谈话和答记者问)"),
                ("/yzxwfb/", "Regular press briefing (例行新闻发布)"),
                ("/lxjzh", "Regular monthly press conference (例行记者会)"))
MND_FULL_TITLE = re.compile(r"国防部例行新闻发布$|例行记者会文字实录|例行新闻发布文字实录")
MND_ASKER = 'Not specified by source (MND uses generic "Reporter:" prefix without outlet attribution)'
MND_ANNOUNCEMENT = "N/A — spokesperson opening announcement (no press Q)"

# TAO tags: copied from aug_sep_2026_intake/tao/process_tao.py (COUNTRY_KW_TAO; Taiwan always tagged).
TAO_TAGS: Dict[str, Tuple[str, ...]] = {
    "United States": ("美国", "美方", "美军", "美对台", "拜登", "特朗普", "华盛顿"),
    "Japan": ("日本", "日方", "日美"),
    "DPP / Lai administration": ("民进党", "赖清德", "蔡英文", "台当局", "DPP"),
    "Cross-strait integration": ("融合发展", "示范区", "两岸交流", "两岸合作", "闽台", "ECFA", "共同市场"),
    "Reunification / one-China": ("统一", "一个中国", "九二共识", "反分裂", "台独"),
}
TAO_SPOKES = {"陈斌华": "Chen Binhua", "朱凤莲": "Zhu Fenglian", "张晗": "Zhang Han", "彭庆恩": "Peng Qing'en",
              "马晓光": "Ma Xiaoguang", "安峰山": "An Fengshan", "李隽": "Li Jun"}

# Tracked formulas (Pulse §V alignment checks). Regexes per language; a row counts once per phrase.
PHRASES: Dict[str, Tuple[str, str]] = {
    "greatest common denominator": (r"greatest common (?:ground|denominator)", r"最大公约数"),
    "utmost prudence": (r"utmost prudence|with prudence|prudently", r"慎之又慎|慎重处理台湾问题"),
    "'two systems' Taiwan plan": (r"two systems.{0,3} (?:Taiwan )?(?:plan|proposal)|one country, two systems",
                                  r"两制.{0,2}台湾方案|一国两制"),
    "seek independence by force": (r"(?:seek|pursue)\w* (?:'?\"?Taiwan independence'?\"? )?by force|"
                                   r"reject\w* reunification by force", r"以武谋独|以武拒统"),
    "Taiwan independence": (r"Taiwan independence", r"台独"),
    "one-China principle": (r"one-China principle", r"一个中国原则"),
    "1992 Consensus": (r"1992 Consensus", r"九二共识"),
    "peaceful reunification": (r"peaceful reunification", r"和平统一"),
    "core of core interests": (r"core of China's core interests|core interests", r"核心利益中的核心|核心利益"),
    "red line": (r"red line", r"红线"),
    "strategic stability (China-U.S.)": (r"strategic stability", r"战略稳定"),
    "survival-threatening situation": (r"survival-threatening situation", r"存亡危机事态"),
    "UNGA Resolution 2758": (r"Resolution 2758", r"2758号决议"),
    "external forces / interference": (r"external (?:forces|interference)|foreign interference", r"外部势力|外来干涉"),
    "playing with fire": (r"play\w* with fire", r"玩火"),
    "provocation": (r"provocat\w+", r"挑衅"),
}
PHRASE_RE = {k: (re.compile(en, re.I), re.compile(zh)) for k, (en, zh) in PHRASES.items()}
SPEAKER_PREFIX = re.compile(rf"^(?:{'|'.join([*MFA_SPOKES, *MFA_SPOKES.values(), *MND_SPOKES, *MND_SPOKES.values(), *TAO_SPOKES])})\s*[:：]\s*")
TAIWAN_ZH = re.compile(r"台独|台海|两岸|台湾|赖清德|民进党")


# ------------------------------------------------------------------------------------------------ turn splitting
@dataclass
class Pair:
    asker: str = ""
    spokes: str = ""
    q: List[Tuple[str, int, int]] = field(default_factory=list)   # (text, first sentence idx, end idx)
    a: List[Tuple[str, int, int]] = field(default_factory=list)

    @property
    def question(self) -> str:
        return "\n".join(t for t, _, _ in self.q if t).strip()

    @property
    def answer(self) -> str:
        return "\n".join(t for t, _, _ in self.a if t).strip()

    @property
    def answer_idx(self) -> List[int]:
        return [i for _, a, b in self.a for i in range(a, b)]


Classifier = Callable[[str], Optional[Tuple[str, str, str]]]   # line -> ("A"|"Q"|"N", label, rest) or None
NOISE = ("N", "", "")


def split_turns(lines: Sequence[Tuple[str, int, int]], classify: Classifier, merge_answers: bool) -> List[Pair]:
    """Group transcript lines into Q&A pairs.

    A spokesperson turn before any question opens an announcement pair (no question). A spokesperson turn right after
    an answered pair is appended to that answer when `merge_answers` (MND, TAO), else it opens a new pair with an empty
    asker (MFA, as in TSM's process_mfa.py). Unlabelled lines continue the current side; lines before the first label
    form a preamble, returned as an announcement only when it is non-empty and `merge_answers` is False (MFA).
    """
    pairs: List[Pair] = []
    cur: Optional[Pair] = None
    pre: List[Tuple[str, int, int]] = []
    for line, i0, i1 in lines:
        s = line.strip()
        if not s:
            continue
        c = classify(s)
        if c == NOISE:
            continue
        if c is None:
            if cur is None:
                pre.append((s, i0, i1))
            else:
                (cur.a if cur.a else cur.q).append((s, i0, i1))
            continue
        kind, label, rest = c
        item = (rest, i0, i1)
        if kind == "Q":
            cur = Pair(asker=label, q=[item])
            pairs.append(cur)
        elif cur is not None and cur.q and not cur.a:
            cur.spokes, cur.a = label, [item]
        elif cur is not None and cur.a and (merge_answers or not cur.q and not pairs[:-1]):
            cur.a.append(item)
        else:
            cur = Pair(asker="" if pairs or pre else ANNOUNCEMENT, spokes=label, a=[item])
            pairs.append(cur)
    if pre and not merge_answers:
        pairs.insert(0, Pair(asker=ANNOUNCEMENT, a=pre))
    return [p for p in pairs if p.answer]


def _label(rx: "re.Pattern[str]", line: str) -> Optional[Tuple[str, str]]:
    m = rx.match(line)
    return (m.group(1).strip(), line[m.end():].strip()) if m else None


MFA_LABEL = re.compile(r"^([^:：]{2,60}?)\s*[:：]\s+")


def mfa_classifier(spokes: Sequence[str]) -> Classifier:
    def classify(line: str):
        if re.fullmatch(r"\*{5,}", line):
            return NOISE
        lab = _label(MFA_LABEL, line)
        if lab is None:
            return None
        label, rest = re.sub(r"\s+", " ", lab[0]), lab[1]
        if label in spokes:
            return ("A", label, rest)
        if "," in label or label.endswith((".", "?", "!")):
            return None
        return ("Q", OUTLET_ALIASES.get(label, label), rest)
    return classify


MND_ZH_NOISE = re.compile(r"^(?:.{0,60}摄|来源：.*|责任编辑：.*|时间：.*|地点：.*|发布人：.*|第[一二三四五六七八九十]+条消息|"
                          r"\d{1,2}月\d{1,2}日(?:上午|下午)，国防部.*|\d{4}年\d{1,2}月.{0,6}国防部例行.*)$")
MND_EN_NOISE = re.compile(r"^\((?:The following English text is for reference|mod\.gov\.cn).*|"
                          r"^Senior Colonel .{0,80}(?:answered questions|released news).*$", re.I)


def mnd_classifier(lang: str) -> Classifier:
    names = "|".join(MND_SPOKES) if lang == "zh" else "|".join(MND_SPOKES.values())
    spokes_rx = re.compile(rf"^({names})\s*[：:]\s*")
    asker_rx = re.compile(r"^(记者)\s*[：:]\s*" if lang == "zh" else r"^(Journalist|Reporter)\s*:\s*", re.I)
    noise = MND_ZH_NOISE if lang == "zh" else MND_EN_NOISE

    def classify(line: str):
        if noise.match(line):
            return NOISE
        q = _label(asker_rx, line)
        if q:
            return ("Q", MND_ASKER, q[1])
        a = _label(spokes_rx, line)
        if a:
            return ("A", MND_SPOKES.get(a[0], a[0]), a[1])
        return None
    return classify


TAO_SPOKES_RX = re.compile(rf"^({'|'.join(TAO_SPOKES)})\s*[：:]\s*")
TAO_ASKER_RX = re.compile(r"^(.{2,60}?(?:记者|提问))\s*[：:]\s*")
TAO_INTRO = re.compile(r"^\d{1,2}月\d{1,2}日.*新闻发布")


def tao_classify(line: str):
    if TAO_INTRO.match(line):
        return NOISE
    a = _label(TAO_SPOKES_RX, line)
    if a:
        return ("A", a[0], a[1])
    q = _label(TAO_ASKER_RX, line)
    if q:
        return ("Q", q[0], q[1])
    return None


# ------------------------------------------------------------------------------------------------ helpers
def month_range(month: str) -> Tuple[str, str]:
    y, m = map(int, month.split("-"))
    end = date(y + (m == 12), m % 12 + 1, 1)
    return f"{month}-01", end.isoformat()


def prev_month(month: str) -> str:
    y, m = map(int, month.split("-"))
    return f"{y - (m == 1)}-{(m - 2) % 12 + 1:02d}"


def days_in(month: str) -> int:
    a, b = month_range(month)
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def first_sentence(text: str, limit: int = 140) -> str:
    s = re.split(r"(?<=[.!?。！？])\s*", text.strip(), maxsplit=1)[0].strip().rstrip(".")
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0]


def phrases_in(*texts: str) -> List[str]:
    out = []
    for k, (en, zh) in PHRASE_RE.items():
        if any(t and (en.search(t) or zh.search(t)) for t in texts):
            out.append(k)
    return out


def fetch(cc: sqlite3.Connection, sources: Sequence[str], lo: str, hi: str) -> List[Dict]:
    q = (f"SELECT rowid, id, source, lang, kind, date, url, title, speaker, text FROM docs WHERE country = 'CN' AND "
         f"source IN ({','.join('?' * len(sources))}) AND date >= ? AND date < ? ORDER BY date, url, source")
    cols = ("rowid", "id", "source", "lang", "kind", "date", "url", "title", "speaker", "text")
    return [dict(zip(cols, r)) for r in cc.execute(q, [*sources, lo, hi])]


def dedupe(docs: List[Dict], prefer: Sequence[str]) -> List[Dict]:
    """One document per URL, preferring sources earlier in `prefer`."""
    rank = {s: i for i, s in enumerate(prefer)}
    best: Dict[str, Dict] = {}
    for d in docs:
        k = d["url"]
        if k not in best or rank.get(d["source"], 99) < rank.get(best[k]["source"], 99):
            best[k] = d
    return sorted(best.values(), key=lambda d: (d["date"], d["url"]))


# ------------------------------------------------------------------------------------------------ streams
def mfa_rows(cc, lo: str, hi: str) -> Tuple[List[Dict], Dict]:
    docs = dedupe([d for d in fetch(cc, ("mfa_cn_live", "mfa_cn_archive"), lo, hi) if d["kind"] == "briefing"],
                  ("mfa_cn_live", "mfa_cn_archive"))
    en = [d for d in docs if "/eng/" in d["url"] and "lxjzh" in d["url"]]
    zh = [d for d in docs if "jzhsl" in d["url"] or ("mfa.gov.cn/web/" in d["url"] and d["lang"] == "zh")]
    rows, stats = [], {"briefing_days": len({d["date"] for d in en}), "zh_control": {}, "en_by_day": {}}
    for d in en:
        m = re.search(r"Spokesperson ([A-Z][a-z]+ [A-Z][a-z]+)", d["title"] or "")
        spokes = m.group(1) if m else (d["speaker"] or "")
        pairs = split_turns(line_sentences(d["text"]), mfa_classifier(set(MFA_SPOKES.values()) | {spokes}), False)
        stats["en_by_day"][d["date"]] = sum(1 for p in pairs if p.q)
        for seq, p in enumerate(pairs, 1):
            countries = [k for k, rx in MFA_COUNTRY_RE.items() if rx.search(f"{p.question}\n{p.answer}")]
            asker = p.asker
            sp = p.spokes or spokes
            qline = f"{asker}: {p.question}" if p.question else ""
            rows.append({"Year-Month": d["date"][:7], "Date": d["date"],
                         "Title": f"Ministry of Foreign Affairs: {first_sentence(p.answer)}", "Spokesperson": sp,
                         "Asker": asker, "Question_verbatim": p.question, "Answer_verbatim": p.answer,
                         "Text": "\n\n".join(x for x in (qline, f"{sp}: {p.answer}") if x),
                         "Country": "; ".join(countries or ["Other / General"]), "Original_URL": d["url"],
                         "_doc": d, "_pair": p, "_lang": "en", "Seq": seq, "_q_only": p.question,
                         "_announcement": p.asker in ("", ANNOUNCEMENT)})
    for d in zh:   # control: Q&A count from the Chinese transcript
        lines = [l.strip() for l in (d["text"] or "").split("\n") if l.strip()]
        n = sum(1 for i, l in enumerate(lines[:-1]) if re.match(r"^[^：:]{2,40}[：:]", l)
                and not re.match(rf"^(?:{'|'.join(MFA_SPOKES)})", l) and re.match(rf"^(?:{'|'.join(MFA_SPOKES)})", lines[i + 1]))
        stats["zh_control"][d["date"]] = n
    return rows, stats


def mnd_topics(text: str) -> str:
    """TSM process_mnd.py classify(): ASCII keywords case-insensitive, Chinese exact; fallback Other / General."""
    low = text.lower()
    hits = [k for k, kws in MND_TOPICS.items() if any((w.lower() in low) if w.isascii() else (w in text) for w in kws)]
    return "; ".join(hits) if hits else "Other / General"


def mnd_rows(cc, lo: str, hi: str) -> Tuple[List[Dict], Dict]:
    docs = dedupe(fetch(cc, ("mnd_cn_live",), lo, hi), ("mnd_cn_live",))

    def channel(url: str) -> Optional[str]:
        return next((name for key, name in MND_CHANNELS if key in url), None)

    zh = [d for d in docs if d["lang"] == "zh" and "mod.gov.cn/gfbw/" in d["url"] and channel(d["url"])]
    en_full = [d for d in docs if d["lang"] == "en" and d["kind"] == "briefing" and "eng.mod.gov.cn" in d["url"]]
    groups: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)
    for d in zh:
        groups[(d["date"], channel(d["url"]))].append(d)
    rows: List[Dict] = []
    events = []
    for (day, ch), ds in sorted(groups.items()):
        full = [d for d in ds if MND_FULL_TITLE.search(d["title"] or "")]
        statement = ch.startswith("Spokesperson statement")
        units = full[:1] if full and not statement else ds
        events.append({"date": day, "type": ch, "docs": len(ds), "full_transcript": bool(full and not statement)})
        en_pairs: List[Pair] = []
        en_doc = None
        if full and not statement:
            en_doc = next((e for e in en_full if e["date"] == day), None)
            if en_doc:
                en_pairs = split_turns(line_sentences(en_doc["text"]), mnd_classifier("en"), True)
        zh_pairs: List[Tuple[Dict, Pair]] = []
        for d in units:
            ps = split_turns(line_sentences(d["text"]), mnd_classifier("zh"), True)
            if statement or not ps:   # one row per standalone statement
                lines = line_sentences(d["text"])
                p = Pair(asker="Not specified (spokesperson statement)", spokes=MND_SPOKES.get(d["speaker"] or "", ""),
                         q=[x for pp in ps for x in pp.q], a=[x for pp in ps for x in pp.a] or [l for l in lines if l[0].strip()])
                ps = [p]
            zh_pairs.extend((d, p) for p in ps)
        aligned = bool(en_pairs) and len(en_pairs) == len(zh_pairs)
        if en_doc and not aligned:
            logger.warning("MND %s: English transcript has %d pairs vs %d Chinese; English not attached",
                           day, len(en_pairs), len(zh_pairs))
        for seq, (d, p) in enumerate(zh_pairs, 1):
            e = en_pairs[seq - 1] if aligned else None
            sp = p.spokes or MND_SPOKES.get(d["speaker"] or "", d["speaker"] or "")
            announcement = not p.q and not statement
            asker = MND_ANNOUNCEMENT if announcement else p.asker
            q, a = (e.question, e.answer) if e else (p.question, p.answer)
            title = (f"MND opening announcement at the {ch}" if announcement
                     else f"MND ({sp}): {first_sentence(a)}")
            rows.append({"Year-Month": day[:7], "Date": day, "Title": title, "Spokesperson": sp, "Asker": asker,
                         "Question_verbatim": q, "Answer_verbatim": a,
                         "Text": "\n".join(x for x in ((f"{'Journalist' if e else '记者'}: {q}" if q else ""),
                                                        f"{sp}: {a}") if x),
                         "Country/Topic": mnd_topics(f"{p.question} {p.answer} {q} {a}"),
                         "Source": ("Official English Translation (eng.mod.gov.cn)" if e else
                                    "Chinese Original (mod.gov.cn) — no official English transcript in the corpus"),
                         "Original_URL": en_doc["url"] if e else d["url"], "Question_verbatim_zh": p.question,
                         "Answer_verbatim_zh": p.answer, "Original_URL_zh": d["url"], "Briefing_Type": ch, "Seq": seq,
                         "_doc": d, "_pair": p, "_lang": "zh", "_announcement": announcement,
                         "_en_url": en_doc["url"] if e else ""})
    return rows, {"events": events, "en_transcripts": [e["url"] for e in en_full]}


def tao_rows(cc, lo: str, hi: str) -> Tuple[List[Dict], Dict]:
    docs = dedupe([d for d in fetch(cc, ("tao_cn_live",), lo, hi) if d["lang"] == "zh"], ("tao_cn_live",))
    pcs = [d for d in docs if "/xwfbh/" in d["url"]]
    sts = [d for d in docs if "/wyly/" in d["url"]]
    rows: List[Dict] = []

    def tags(text: str) -> str:
        return "; ".join(["Taiwan"] + [k for k, kws in TAO_TAGS.items() if any(w in text for w in kws)])

    for d in pcs:
        pairs = split_turns(line_sentences(d["text"]), tao_classify, True)
        sp0 = next((p.spokes for p in pairs if p.spokes), d["speaker"] or "")
        for seq, p in enumerate(pairs, 1):
            asker = p.asker if p.q else "Not specified (announcement/opening)"
            text = (f"{asker}：\n{p.question}\n" if p.question else "") + f"{p.spokes or sp0}：\n{p.answer}"
            rows.append({"Year-Month": d["date"][:7], "Date": d["date"], "Title": d["title"],
                         "Spokesperson": TAO_SPOKES.get(p.spokes or sp0, p.spokes or sp0), "Asker": asker,
                         "Question_verbatim": p.question, "Answer_verbatim": p.answer, "Text": text,
                         "Country/Topic": tags(text), "Source": "Press Conference (gwytb.gov.cn)",
                         "Original_URL": d["url"], "Title_EN_MT": "", "Question_EN_MT": "", "Answer_EN_MT": "",
                         "_doc": d, "_pair": p, "_lang": "zh", "Seq": seq, "_announcement": not p.q})
    spk = "|".join(TAO_SPOKES)
    for d in sts:
        lines = line_sentences(d["text"])
        start = next((i for i, (l, _, _) in enumerate(lines)
                      if re.match(rf"^(?:国务院台办)?发言人(?:{spk})|^(?:{spk})(?:应询)?(?:表示|指出|回答|答)", l.strip())), 0)
        q, a = [l for l in lines[:start] if l[0].strip()], [l for l in lines[start:] if l[0].strip()]
        m = re.search(rf"发言人({spk})", d["text"] or "")
        sp = TAO_SPOKES.get(m.group(1), "Not identified in source") if m else "Not identified in source"
        qtext = "\n".join(l for l, _, _ in q)
        outlet = re.search(r"([^\s，,。]{2,20}记者)(?:提问|问)", qtext)
        asker = outlet.group(1) if outlet else ("Not specified (reporter question, outlet not named)" if q
                                                else "Not specified (spokesperson statement)")
        p = Pair(asker=asker, spokes=sp, q=q, a=a)
        rows.append({"Year-Month": d["date"][:7], "Date": d["date"], "Title": d["title"], "Spokesperson": sp,
                     "Asker": asker, "Question_verbatim": p.question, "Answer_verbatim": p.answer,
                     "Text": d["text"], "Country/Topic": tags(d["text"] or ""),
                     "Source": "Spokesperson Statement (gwytb.gov.cn)", "Original_URL": d["url"], "Title_EN_MT": "",
                     "Question_EN_MT": "", "Answer_EN_MT": "", "_doc": d, "_pair": p, "_lang": "zh", "Seq": 1,
                     "_announcement": False})
    pc_days = {d["date"] for d in pcs}
    other = [d for d in docs if d not in pcs and d not in sts]
    return rows, {"press_conferences": [d["date"] for d in pcs], "standalone": len(sts),
                  "off_cycle": sum(1 for d in sts if d["date"] not in pc_days), "other_tao_docs_not_counted": len(other)}


# ------------------------------------------------------------------------------------------------ tone and quotes
def attach_tone(cc, sc, stream: str, rows: List[Dict], gaz) -> Tuple[List[Dict], List[Dict]]:
    """Per-row tone over answer sentences + quote candidates (scored official sentences of the answer)."""
    tone_rows, cands = [], []
    cache: Dict[str, Tuple[Dict, Dict]] = {}
    for r in rows:
        d, p = r["_doc"], r["_pair"]
        if d["id"] not in cache:
            sents = dict(cc.execute("SELECT idx, text FROM sentences WHERE doc = ?", [d["rowid"]]).fetchall())
            cache[d["id"]] = (sents, sentence_scores(sc, d["id"]))
        sents, scores = cache[d["id"]]
        idx = p.answer_idx
        scored = [scores[i] for i in idx if i in scores]
        tw = [i for i in idx if i in sents and (TAIWAN_ZH.search(sents[i]) if d["lang"] == "zh"
                                                 else "TAIWAN" in gaz.targets(sents[i], d["lang"]))]
        tw_scored = [scores[i] for i in tw if i in scores]
        ph = phrases_in(p.answer, r.get("Answer_verbatim", ""))
        r["_phrases"] = ph
        tone_rows.append({"stream": stream, "Date": r["Date"], "Original_URL": r["Original_URL"], "Seq": r["Seq"],
                          "Asker": r["Asker"], "Spokesperson": r["Spokesperson"], "doc_id": d["id"],
                          "answer_sentences": len(idx), "scored": len(scored), **mean_dims(scored),
                          "taiwan_sentences": len(tw), "taiwan_scored": len(tw_scored),
                          **{f"taiwan_{k}": v for k, v in mean_dims(tw_scored).items()}, "phrases": "; ".join(ph)})
        r["_tone"] = mean_dims(scored)
        r["_scored"] = len(scored)
        lo_len = 12 if d["lang"] == "zh" else 30
        for i in idx:
            s = SPEAKER_PREFIX.sub("", sents.get(i) or "").strip()
            if (not s or i not in scores or not lo_len <= len(s) <= 300 or s.endswith(("?", "？"))
                    or (d["lang"] == "en" and s[0].islower())):   # lower-case start = tail of a cut long sentence
                continue
            tg = gaz.targets(s, d["lang"])
            cands.append({"stream": stream, "Date": r["Date"], "Spokesperson": r["Spokesperson"], "lang": d["lang"],
                          "quote": s, "chars": len(s), "Original_URL": d["url"], "Seq": r["Seq"],
                          "taiwan": int("TAIWAN" in tg or bool(d["lang"] == "zh" and TAIWAN_ZH.search(s))),
                          "targets": "; ".join(t for t in tg if t != "CHINA"),
                          **{k: round(v, 3) for k, v in scores[i].items()},
                          "official_english_url": r.get("_en_url", ""),
                          "quote_rule": {"MFA": "official English verbatim (fmprc.gov.cn)",
                                         "MND": "Chinese verbatim (mod.gov.cn); quote official English from "
                                                "official_english_url if present, else label any English as a TSM "
                                                "translation",
                                         "TAO": "Chinese verbatim (gwytb.gov.cn has no English edition); label any "
                                                "English as a TSM translation"}[stream]})
    return tone_rows, cands


def top_quotes(cands: List[Dict], k: int = 10) -> List[Dict]:
    out = []
    for stream in ("MFA", "MND", "TAO"):
        cs = sorted((c for c in cands if c["stream"] == stream),
                    key=lambda c: (-c["taiwan"], -(c["hostility"] + c["threat"] + c["escalation"]), c["Date"]))
        seen = set()
        for c in cs:
            key = c["quote"][:60]
            if key in seen:
                continue
            seen.add(key)
            out.append({**c, "rank": len(seen)})
            if len(seen) == k:
                break
    return out


# ------------------------------------------------------------------------------------------------ summary
def summarize(month: str, mfa: List[Dict], mfa_st: Dict, mnd: List[Dict], mnd_st: Dict, tao: List[Dict],
              tao_st: Dict) -> List[Dict]:
    out: List[Dict] = []

    def put(stream, metric, value, key=""):
        out.append({"stream": stream, "month": month, "metric": metric, "key": key, "value": value})

    ex = [r for r in mfa if not r["_announcement"]]
    put("MFA", "exchanges", len(ex))
    put("MFA", "opening_announcements", len(mfa) - len(ex))
    put("MFA", "briefing_days", mfa_st["briefing_days"])
    put("MFA", "briefing_dates", "; ".join(sorted(mfa_st["en_by_day"])))
    put("MFA", "exchanges_per_30d", round(len(ex) * 30 / days_in(month), 1))
    put("MFA", "zh_control_exchanges", sum(mfa_st["zh_control"].values()))
    put("MFA", "zh_en_days_disagreeing", sum(1 for d, n in mfa_st["zh_control"].items()
                                             if mfa_st["en_by_day"].get(d) not in (None, n)))
    multi = sum(1 for r in ex if ";" in r["Country"])
    put("MFA", "exchanges_multi_tag", multi)
    put("MFA", "tag_instances", sum(len(r["Country"].split("; ")) for r in ex))
    for k, v in Counter(t for r in ex for t in r["Country"].split("; ")).most_common():
        put("MFA", "country_tag", v, k)
    for k, v in Counter(t for r in ex for t in r["Country"].split("; ") if t != "Other / General"
                        and MFA_COUNTRY_RE[t].search(r["Question_verbatim"])).most_common():
        put("MFA", "country_tag_question_only", v, k)
    for k, v in Counter(r["Spokesperson"] for r in ex).most_common():
        put("MFA", "spokesperson", v, k)
    for k, v in Counter(r["Asker"] for r in ex if r["Asker"]).most_common():
        put("MFA", "questioner", v, k)

    put("MND", "entries", len(mnd))
    put("MND", "events", len({(r["Date"], r["Briefing_Type"]) for r in mnd}))
    put("MND", "opening_announcements", sum(r["_announcement"] for r in mnd))
    put("MND", "entries_official_english", sum(r["Source"].startswith("Official English") for r in mnd))
    for e in mnd_st["events"]:
        put("MND", "event", sum(1 for r in mnd if r["Date"] == e["date"] and r["Briefing_Type"] == e["type"]),
            f"{e['date']} {e['type']}{'' if e['full_transcript'] else ' (per-topic extracts or statement)'}")
    for k, v in Counter(t for r in mnd for t in r["Country/Topic"].split("; ") if t).most_common():
        put("MND", "topic_tag", v, k)
    for k, v in Counter(r["Spokesperson"] for r in mnd).most_common():
        put("MND", "spokesperson", v, k)

    put("TAO", "releases", len({r["Original_URL"] for r in tao}))
    put("TAO", "press_conference_transcripts", len(tao_st["press_conferences"]))
    put("TAO", "standalone_releases", tao_st["standalone"])
    put("TAO", "off_cycle_releases", tao_st["off_cycle"])
    put("TAO", "rows", len(tao))
    put("TAO", "releases_per_30d", round(len({r["Original_URL"] for r in tao}) * 30 / days_in(month), 1))
    put("TAO", "other_tao_docs_not_counted", tao_st["other_tao_docs_not_counted"])
    for k, v in Counter(t for r in tao for t in r["Country/Topic"].split("; ")).most_common():
        put("TAO", "topic_tag", v, k)
    for k, v in Counter(r["Spokesperson"] for r in tao).most_common():
        put("TAO", "spokesperson", v, k)

    for stream, rows in (("MFA", mfa), ("MND", mnd), ("TAO", tao)):
        for k, v in Counter(ph for r in rows for ph in r.get("_phrases", [])).most_common():
            put(stream, "phrase_rows", v, k)
        scored = [r for r in rows if r.get("_scored")]
        put(stream, "rows_with_scored_answer", len(scored))
        for dim in DIMS:   # mean of row means (each exchange weighted equally)
            vals = [r["_tone"][dim] for r in scored]
            put(stream, "mean_tone", round(sum(vals) / len(vals), 4) if vals else None, dim)
    return out


def metric(summary: List[Dict], stream: str, name: str, key: str = ""):
    return next((s["value"] for s in summary if s["stream"] == stream and s["metric"] == name and s["key"] == key), 0)


def dashboard(month: str, cur: List[Dict], prev: List[Dict], quotes: List[Dict]) -> str:
    pm = prev_month(month)
    g = lambda s, *a: metric(s, *a)   # noqa: E731

    def rate(n, m):
        return round(n * 30 / days_in(m), 1)

    def delta(a, b):
        return f"{(a - b) / b * 100:+.1f}%" if b else "n/a"

    lines = [f"<!-- Draft fragment generated {datetime.now(timezone.utc):%Y-%m-%d} by scripts/export_cross_strait_pulse.py"
             f" from the rhetoric corpus. Figures are corpus counts; check COVERAGE.md before use. -->", "",
             "## II. RHETORICAL DASHBOARD: THREE AGENCIES", "",
             f"| Agency | Statements ({month}) | Comparison window ({pm}) | Note |",
             "|--------|----------|----------|------|"]
    mc, mp = g(cur, "MFA", "exchanges"), g(prev, "MFA", "exchanges")
    lines.append(f"| MFA | {mc} exchanges across {g(cur, 'MFA', 'briefing_days')} briefing days, plus "
                 f"{g(cur, 'MFA', 'opening_announcements')} opening announcements | {mp} exchanges across "
                 f"{g(prev, 'MFA', 'briefing_days')} briefing days | Per-30d rate {rate(mc, month)} against "
                 f"{rate(mp, pm)}, Δ {delta(rate(mc, month), rate(mp, pm))}. |")
    nc, npv = g(cur, "MND", "entries"), g(prev, "MND", "entries")
    lines.append(f"| MND | {nc} entries across {g(cur, 'MND', 'events')} events "
                 f"({g(cur, 'MND', 'opening_announcements')} opening announcements) | {npv} entries across "
                 f"{g(prev, 'MND', 'events')} events | Three-channel capture. Official English attached for "
                 f"{g(cur, 'MND', 'entries_official_english')} of {nc} entries. |")
    tc, tp = g(cur, "TAO", "releases"), g(prev, "TAO", "releases")
    lines.append(f"| TAO | {tc} releases: {g(cur, 'TAO', 'standalone_releases')} standalone plus "
                 f"{g(cur, 'TAO', 'press_conference_transcripts')} press-conference transcripts | {tp} releases "
                 f"({g(prev, 'TAO', 'standalone_releases')} standalone plus {g(prev, 'TAO', 'press_conference_transcripts')}"
                 f" transcripts) | Per-30d rate {rate(tc, month)} against {rate(tp, pm)}, Δ "
                 f"{delta(rate(tc, month), rate(tp, pm))}. Off-cycle releases: {g(cur, 'TAO', 'off_cycle_releases')}. |")
    lines += ["", f"**MFA country focus: {month} vs. {pm}**", "",
              f"| Country focus | {month} | {pm} | {month} / 30d | {pm} / 30d | Δ % (rate) |",
              "|---------------|-----|-----|-----|-----|-----|"]
    for c in ("United States", "Japan", "Taiwan", "Philippines"):
        a, b = g(cur, "MFA", "country_tag", c), g(prev, "MFA", "country_tag", c)
        lines.append(f"| {c} | {a} | {b} | {rate(a, month)} | {rate(b, pm)} | **{delta(rate(a, month), rate(b, pm))}** |")
    lines.append(f"| **All exchanges** | **{mc}** | **{mp}** | **{rate(mc, month)}** | **{rate(mp, pm)}** | "
                 f"**{delta(rate(mc, month), rate(mp, pm))}** |")
    lines += ["", f"MFA briefing dates in the corpus, {pm}: {g(prev, 'MFA', 'briefing_dates')}"
              " (a comparison month with missing briefing days understates the base; see COVERAGE.md)."]
    lines += ["", f"Multi-label: {g(cur, 'MFA', 'exchanges_multi_tag')} of {mc} exchanges carry more than one tag; "
              f"tag instances total {g(cur, 'MFA', 'tag_instances')}.", "",
              f"**MFA podium and questioner distribution, {month}**", "",
              f"| Measure | {month} | Share of {mc} | {pm} |", "|---------|-----|-----|-----|"]
    for s in [x for x in cur if x["stream"] == "MFA" and x["metric"] == "spokesperson"]:
        lines.append(f"| {s['key']} | {s['value']} | {s['value'] / mc:.0%} | {g(prev, 'MFA', 'spokesperson', s['key'])} |")
    for s in [x for x in cur if x["stream"] == "MFA" and x["metric"] == "questioner"][:6]:
        lines.append(f"| {s['key']} questions | {s['value']} | {s['value'] / mc:.0%} | "
                     f"{g(prev, 'MFA', 'questioner', s['key'])} |")
    lines += ["", "## VI. NOTABLE QUOTES OF THE MONTH (candidates; machine-ranked, select by hand)", ""]
    for stream in ("MND", "MFA", "TAO"):
        lines += [f"**{stream}**", ""]
        for q in [q for q in quotes if q["stream"] == stream][:4]:
            body = f"\"{q['quote']}\"" if q["lang"] == "en" else f"「{q['quote']}」"
            src = {"MFA": "", "MND": " (Chinese verbatim, mod.gov.cn)", "TAO": " (Chinese verbatim, gwytb.gov.cn)"}[stream]
            lines += [f"> {body}", f"> — {q['Spokesperson']}, {stream}, {q['Date']}{src} [{q['Original_URL']}]", ""]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------------------ main
def build(cc, sc, month: str):
    lo, hi = month_range(month)
    mfa, mfa_st = mfa_rows(cc, lo, hi)
    mnd, mnd_st = mnd_rows(cc, lo, hi)
    tao, tao_st = tao_rows(cc, lo, hi)
    return mfa, mfa_st, mnd, mnd_st, tao, tao_st


def export(index: Path, month: str, out: Path) -> Dict:
    cc, sc = connect(index)
    gaz = gazetteer()
    mfa, mfa_st, mnd, mnd_st, tao, tao_st = build(cc, sc, month)
    tone, cands = [], []
    for stream, rows in (("MFA", mfa), ("MND", mnd), ("TAO", tao)):
        t, c = attach_tone(cc, sc, stream, rows, gaz)
        tone += t
        cands += c
    pm = prev_month(month)
    p_mfa, p_mfa_st, p_mnd, p_mnd_st, p_tao, p_tao_st = build(cc, sc, pm)
    for stream, rows in (("MFA", p_mfa), ("MND", p_mnd), ("TAO", p_tao)):
        attach_tone(cc, sc, stream, rows, gaz)
    cur = summarize(month, mfa, mfa_st, mnd, mnd_st, tao, tao_st)
    prev = summarize(pm, p_mfa, p_mfa_st, p_mnd, p_mnd_st, p_tao, p_tao_st)
    quotes = top_quotes(cands)

    new_dir(out)
    counts = {
        f"mfa_{month}.csv": write_csv(out / f"mfa_{month}.csv", MFA_FIELDS, mfa, bom=True),
        f"mnd_{month}.csv": write_csv(out / f"mnd_{month}.csv", MND_FIELDS, mnd, bom=True),
        f"tao_{month}_source_zh.csv": write_csv(out / f"tao_{month}_source_zh.csv", TAO_FIELDS, tao, bom=True),
        f"row_tone_{month}.csv": write_csv(out / f"row_tone_{month}.csv", TONE_FIELDS, tone),
        f"stream_summary_{month}.csv": write_csv(out / f"stream_summary_{month}.csv",
                                                 ("stream", "month", "metric", "key", "value"), cur + prev),
        f"phrase_hits_{month}.csv": write_csv(
            out / f"phrase_hits_{month}.csv", ("phrase", "stream", "Date", "Spokesperson", "Asker", "Original_URL", "Seq"),
            [{"phrase": ph, "stream": s, **r} for s, rows in (("MFA", mfa), ("MND", mnd), ("TAO", tao))
             for r in rows for ph in r["_phrases"]]),
        f"top_quotes_{month}.csv": write_csv(out / f"top_quotes_{month}.csv", QUOTE_FIELDS, quotes),
    }
    (out / f"dashboard_fragment_{month}.md").write_text(dashboard(month, cur, prev, quotes), encoding="utf-8")
    prov = provenance(index)
    prov.update(generated=datetime.now(timezone.utc).isoformat(timespec="seconds"), month=month, comparison_month=pm,
                index=str(index), files=counts, mfa=dict(mfa_st), mnd=mnd_st, tao=tao_st, phrases=PHRASES)
    (out / "provenance.json").write_text(json.dumps(prov, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "COVERAGE.md").write_text(coverage_md(month, pm, cur, prev, mnd_st, tao_st, counts), encoding="utf-8")
    cc.close()
    sc.close()
    return {"out": str(out), "files": counts}


def coverage_md(month, pm, cur, prev, mnd_st, tao_st, counts) -> str:
    g = metric
    ev = "\n".join(f"| {e['date']} | {e['type']} | {e['docs']} | {'yes' if e['full_transcript'] else 'no'} | "
                   f"{g(cur, 'MND', 'event', next(s['key'] for s in cur if s['metric'] == 'event' and s['key'].startswith(e['date'] + ' ' + e['type'])))} |"
                   for e in mnd_st["events"])
    return f"""# Corpus feed for the Cross-Strait Pulse, {month}

Generated by `scripts/export_cross_strait_pulse.py` (rhetoric-corpus repo, branch feat/paper-feeds) from the
rhetoric corpus (index/corpus.sqlite, read-only). Comparison month: {pm}. Nothing here edits the issue or the
build script; the CSVs use the 03_Data column layouts so they can be compared with, or substituted for, the
hand-collected intake tables.

## Counts

| Stream | {month} | {pm} |
|---|---:|---:|
| MFA exchanges (excl. opening announcements) | {g(cur, 'MFA', 'exchanges')} | {g(prev, 'MFA', 'exchanges')} |
| MFA opening announcements | {g(cur, 'MFA', 'opening_announcements')} | {g(prev, 'MFA', 'opening_announcements')} |
| MFA briefing days | {g(cur, 'MFA', 'briefing_days')} | {g(prev, 'MFA', 'briefing_days')} |
| MFA briefing dates | {g(cur, 'MFA', 'briefing_dates')} | {g(prev, 'MFA', 'briefing_dates')} |
| MFA Chinese-transcript control (Q&A, approximate) | {g(cur, 'MFA', 'zh_control_exchanges')} | {g(prev, 'MFA', 'zh_control_exchanges')} |
| MND entries | {g(cur, 'MND', 'entries')} | {g(prev, 'MND', 'entries')} |
| MND events | {g(cur, 'MND', 'events')} | {g(prev, 'MND', 'events')} |
| MND entries with official English attached | {g(cur, 'MND', 'entries_official_english')} | {g(prev, 'MND', 'entries_official_english')} |
| TAO releases (distinct URL) | {g(cur, 'TAO', 'releases')} | {g(prev, 'TAO', 'releases')} |
| TAO press-conference transcripts | {g(cur, 'TAO', 'press_conference_transcripts')} | {g(prev, 'TAO', 'press_conference_transcripts')} |
| TAO standalone releases | {g(cur, 'TAO', 'standalone_releases')} | {g(prev, 'TAO', 'standalone_releases')} |
| TAO rows | {g(cur, 'TAO', 'rows')} | {g(prev, 'TAO', 'rows')} |

MND events, {month}:

| Date | Channel | Corpus docs | Full transcript | Rows |
|---|---|---:|---|---:|
{ev}

Files: {', '.join(f'{k} ({v} rows)' for k, v in counts.items())}.

## Rules

- MFA: official English transcripts only; Chinese transcripts counted per day as a control. Exchanges split on
  "<Outlet>: " / "<Spokesperson>: " line labels (TSM process_mfa.py rule); a labelled spokesperson turn before any
  question, or unlabelled text before the first label, is an opening announcement. Country tags = TSM MFA vocabulary,
  multi-label on question + answer, so tag counts exceed exchanges.
- MND: all three channels. A full transcript, when the corpus has one for a channel-day, is split into Q&A and the
  per-topic extracts of that day are dropped as duplicates. Consecutive spokesperson turns are one entry (several
  opening announcements form one row, as in the intake). Official English is attached only when the eng.mod.gov.cn
  transcript of the same day splits into the same number of entries; otherwise the row carries the Chinese.
- TAO: /xwfbh/ transcripts split into Q&A (opening greeting is a row, as in the intake); /wyly/ standalone releases
  one row each; other gwytb sections ({tao_st['other_tao_docs_not_counted']} docs this month) are not spokesperson
  releases and are not counted. Off-cycle = standalone release on a day without a press conference.
- The corpus's `mfa_cn` / `mnd_cn` / `tao_cn` sources (`via=export`) are copies of TSM's own intake tables and are
  not used, so these counts are an independent re-derivation.

## Tone (row_tone, stream_summary mean_tone, top_quotes)

{NOT_VALIDATED}

Tone is the mean over the scored sentences of the official answer only (reporters' questions excluded). The
semantic layer scores sentences with index < 80 plus sentences that mention a gazetteer target up to index 400, so
late passages of long transcripts can be unscored; `answer_sentences` vs `scored` in row_tone shows the coverage.
MND tone and quotes come from the Chinese transcript; TAO from Chinese; MFA from official English.

## Quotation rule

MFA quotes: official English verbatim. MND: official English where published (see official_english_url), else
Chinese verbatim plus a labelled TSM translation. TAO: Chinese verbatim plus a labelled TSM translation. The
dashboard fragment's quotes are machine-ranked candidates, not selections.
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--month", required=True, help="YYYY-MM")
    ap.add_argument("--out-dir", required=True, type=Path, help="new directory (refuses to overwrite)")
    ap.add_argument("--index", help="index/ directory (default: repo index/ or $RC_INDEX)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(export(index_dir(args.index), args.month, args.out_dir.expanduser()), ensure_ascii=False))


if __name__ == "__main__":
    main()
