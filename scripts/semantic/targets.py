"""Multilingual target gazetteer: which actor(s) a sentence mentions.

Each pattern is a regex for one language; an optional veto regex discards a match that overlaps a veto hit
(e.g. 印度 inside 印度尼西亚, "Indian" in "Indian Ocean", 以方 in 可以方便). Patterns are run only on documents
of their language. Mentions of a document's own country are stored with self=1 and left out of stance.

Stance caveat: a sentence mentioning X is attributed to X; the tone may be directed at someone else in the
same sentence (see README "Semantic layer" limits). Audit: `run --stage audit` writes per-pattern hit counts and
samples to reports/semantic/target_audit.md. Patterns dropped after audit are listed in DROPPED with reasons.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple, Union

Spec = Union[str, Tuple[str, str]]

# Arabic-script word boundaries (Persian, Arabic, Urdu).
_AL = "؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿"
AB, AE = f"(?<![{_AL}])", f"(?![{_AL}])"
AR_PRE = "(?:[وفبلك]{0,2})"          # Arabic proclitics (wa-, fa-, bi-, li-, ka-)
FA_SUF = "(?:‌?(?:یی|ی|های|ها|ای|یان|ان))?"
AR_SUF = "(?:ية|يين|يون|ي|يا)?"
UR_SUF = "(?:ی|یوں|وں)?"


def fa(*words: str) -> str:
    return AB + "(?:" + "|".join(words) + ")" + FA_SUF + AE


def ar(*words: str, pre: bool = True) -> str:
    return AB + (AR_PRE if pre else "") + "(?:" + "|".join(words) + ")" + AR_SUF + AE


def ur(*words: str) -> str:
    return AB + "(?:" + "|".join(words) + ")" + UR_SUF + AE


TARGETS: Dict[str, Dict[str, List[Spec]]] = {
    "US": {
        "en": [r"\bU\.S\.(?:A\.)?", (r"\bUS\b(?!\$)", r"\bUS Open\b"), r"\bUSA\b", r"\bUnited States\b",
               (r"\bAmerica(?:n|ns)?\b", r"(?:Latin|South|Central|North|Ibero|Pan)[ -]America\w*|American Indian"),
               (r"\bWashington\b", r"Washington Post|George Washington|Washington State|Washington Consensus"),
               r"\bWhite House\b", r"\bPentagon\b", r"\bUncle Sam\b"],
        "ru": [r"\bСША\b", r"\bСоедин[её]нн\w+ Штат\w+", r"\bВашингтон\w*", r"\bБел\w+ дом\w*",
               (r"\b[Аа]мерикан\w+", r"[Лл]атиноамерикан\w+"), (r"\bАмерик\w*", r"(?:Латинск|Южн|Северн|Центральн)\w+ Америк\w*"),
               r"\bПентагон\w*"],
        "be": [r"\bЗША\b", r"\bЗлучан\w+ Штат\w+", r"\bВашынгтон\w*", r"\bамерыканск\w+"],
        "zh": ["美国", "美方", "美帝", "华盛顿", "白宫", "美军", "美政府", "美西方", "五角大楼", "中美", "美中", "美俄", "俄美",
               "美日", "美韩", "美菲", "美台", "美英", "英美", "美欧", "美澳", "对美", "赴美", "访美", "美舰", "美机",
               "美國", "華盛頓", "白宮", "美軍", "台美", "臺美"],
        "fa": [fa("آمریکا", "امریکا"), fa("ایالات متحده"), fa("واشنگتن"), fa("کاخ سفید"), fa("استکبار"), fa("شیطان بزرگ")],
        "ar": [ar("أمريكا", "أميركا", "امريكا", "اميركا"), ar("الولايات المتحدة"), ar("واشنطن"), ar("البيت الأبيض"),
               ar("الأمريكي", "الأميركي", "الامريكي")],
        "tr": [r"\bABD\b", r"\bAmerika\w*", r"\bWashington\w*", r"\bBeyaz Saray\w*"],
        "es": [r"\bEstados Unidos\b", r"\bEE\.?\s?UU\.?", r"\bEUA\b", r"\bWashington\b", r"\bCasa Blanca\b",
               r"\b[Ee]stadounidense\w*", r"\b[Nn]orteamerican\w+", r"\b[Yy]anqui\w*"],
        "ur": [ur("امریکہ", "امریکا", "امریکی"), ur("واشنگٹن")],
        "ko": ["미국", "미측", ("미제", "미제품"), "워싱턴", "백악관"],
    },
    "NATO": {
        "en": [r"\bNATO\b", r"\bNorth Atlantic (?:Treaty Organi[sz]ation|Alliance)\b"],
        "ru": [r"\bНАТО\b", r"\bСевероатлантическ\w+ альянс\w*", r"\bАльянс\w*"],
        "be": [r"\bНАТА\b"],
        "zh": ["北约", "北大西洋公约组织", "北約"],
        "fa": [fa("ناتو")], "ar": [ar("الناتو", "حلف شمال الأطلسي", "حلف الأطلسي")], "tr": [r"\bNATO\w*"],
        "es": [r"\bOTAN\b"], "ur": [ur("نیٹو")], "ko": ["나토", "북대서양조약기구"],
    },
    "EU": {
        "en": [r"\bEU\b", r"\bEuropean Union\b", r"\bEuropean Commission\b", r"\bBrussels\b"],
        "ru": [r"\bЕвросоюз\w*", r"\bЕС\b", r"\bЕвропейск\w+ союз\w*", r"\bЕврокомисси\w+", r"\bБрюссел\w*"],
        "be": [r"\bЕўрасаюз\w*", r"\bЕўрапейск\w+ саюз\w*", r"\bЕС\b"],
        "zh": ["欧盟", "欧方", "欧洲联盟", "布鲁塞尔", "歐盟"],
        "fa": [fa("اتحادیه اروپا")], "ar": [ar("الاتحاد الأوروبي", "الاتحاد الاوروبي")],
        "tr": [r"\bAB\b", r"\bAvrupa Birliği\w*"], "es": [r"\bUnión Europea\b", r"\bUE\b"],
        "ur": [ur("یورپی یونین")], "ko": ["유럽연합", "구라파동맹"],
    },
    "UK": {
        "en": [r"\bUK\b", r"\bU\.K\.", r"\bUnited Kingdom\b", r"\bBritain\b", r"\bBritish\b", r"\bDowning Street\b",
               (r"\bLondon\b", r"London Stock Exchange")],
        "ru": [r"\bВеликобритани\w+", r"\bБритани\w+", r"\bбританск\w+", r"\bЛондон\w*", r"\bАнгли[яиюей]\w*"],
        "be": [r"\bВялікабрытан\w+", r"\bбрытанск\w+", r"\bЛондан\w*"],
        "zh": ["英国", "英方", "伦敦", "英國", "倫敦"],
        "fa": [fa("انگلیس", "بریتانیا", "لندن")], "ar": [ar("بريطانيا", "المملكة المتحدة", "لندن")],
        "tr": [r"\bİngiltere\w*", r"\bBirleşik Krallık\w*", r"\bLondra\w*"],
        "es": [r"\bReino Unido\b", r"\bGran Bretaña\b", r"\b[Bb]ritánic\w+", r"\bLondres\b"],
        "ur": [ur("برطانیہ", "برطانوی")], "ko": ["영국"],
    },
    "JAPAN": {
        "en": [r"\bJapan(?:ese)?\b", r"\bTokyo\b"],
        "ru": [r"\bЯпони\w+", r"\bяпонск\w+", r"\bТокио\b"], "be": [r"\bЯпоні\w+", r"\bяпонск\w+"],
        "zh": ["日本", "日方", "日美", "中日", "日韩", "韩日", "日菲", "日澳", "日军", "东京", "東京", "日軍"],
        "fa": [fa("ژاپن")], "ar": [ar("اليابان", "طوكيو")], "tr": [r"\bJaponya\w*"],
        "es": [r"\bJapón\b", r"\b[Jj]apones\w*"], "ur": [ur("جاپان")], "ko": ["일본"],
    },
    "ROK": {
        "en": [r"\bSouth Korea(?:n|ns)?\b", r"\bRepublic of Korea\b", r"\bROK\b", r"\bSeoul\b"],
        "ru": [r"\bЮжн\w+ Коре\w+", r"\bРеспублик\w+ Корея\b", r"\bРК\b", r"\bСеул\w*", r"\bюжнокорейск\w+"],
        "be": [r"\bПаўднёв\w+ Кар\w+"],
        "zh": ["韩国", "韩方", "韩美", "美韩", "中韩", "日韩", "韩日", "首尔", "南朝鲜", "韓國", "南韓"],
        "fa": [fa("کره جنوبی")], "ar": [ar("كوريا الجنوبية")], "tr": [r"\bGüney Kore\w*"],
        "es": [r"\bCorea del Sur\b", r"\b[Ss]urcorean\w+", r"\bSeúl\b"], "ur": [ur("جنوبی کوریا")],
        "ko": [("한국", "한국어|한국전쟁|조선반도"), "대한민국", "남조선", "괴뢰"],
    },
    "TAIWAN": {
        "en": [r"\bTaiwan(?:ese)?\b", r"\bTaipei\b", r"\bDPP\b", r"\bLai Ching-te\b"],
        "ru": [r"\bТайван\w*"], "be": [r"\bТайван\w*"],
        "zh": ["台湾", "臺灣", "台灣", "台独", "台獨", "台当局", "民进党", "民進黨", "赖清德", "賴清德", "台北", "臺北"],
        "fa": [fa("تایوان")], "ar": [ar("تايوان")], "tr": [r"\bTayvan\w*"], "es": [r"\bTaiw[aá]n\b"],
        "ur": [ur("تائیوان")], "ko": ["대만", "타이완"],
    },
    "PHILIPPINES": {
        "en": [r"\bPhilippines?\b", r"\bPhilippine\b", r"\bFilipinos?\b", r"\bManila\b"],
        "ru": [r"\bФилиппин\w*"], "be": [r"\bФіліпін\w*"],
        "zh": ["菲律宾", "菲方", "菲军", "中菲", "美菲", "马尼拉", "菲律賓"],
        "fa": [fa("فیلیپین")], "ar": [ar("الفلبين")], "tr": [r"\bFilipinler\w*"], "es": [r"\bFilipinas\b"],
        "ur": [ur("فلپائن")], "ko": ["필리핀"],
    },
    "UKRAINE": {
        "en": [r"\bUkrain(?:e|ian|ians)\b", r"\bKyiv\b", r"\bKiev\b", r"\bZelensk(?:y|iy|yy|i)\b"],
        "ru": [r"\bУкраин\w*", r"\bукраинск\w+", r"\bКиев\w*", r"\bкиевск\w+", r"\bЗеленск\w+", r"\bВСУ\b"],
        "be": [r"\bУкраін\w*", r"\bукраінск\w+", r"\bКіеў\w*"],
        "zh": ["乌克兰", "乌方", "乌军", "俄乌", "基辅", "泽连斯基", "烏克蘭"],
        "fa": [fa("اوکراین", "کی‌یف", "کییف")], "ar": [ar("أوكرانيا", "اوكرانيا")], "tr": [r"\bUkrayna\w*", r"\bKiev\w*"],
        "es": [r"\bUcrania\b", r"\b[Uu]crania\w+"], "ur": [ur("یوکرین")], "ko": ["우크라이나"],
    },
    "ISRAEL": {
        "en": [r"\bIsrael(?:i|is)?\b", r"\bTel Aviv\b", r"\bZionists?\b", r"\bIDF\b", r"\bNetanyahu\b"],
        "ru": [r"\bИзраил\w*", r"\bизраильск\w+", r"\bТель-Авив\w*", r"\bсионист\w+", r"\bНетаньяху\b"],
        "be": [r"\bІзраіл\w*"],
        "zh": ["以色列", ("以方", "以方[便式法面案针向位圆]|[可所加予得难足用]以方"), "以军", "巴以", "以哈", "内塔尼亚胡", "特拉维夫"],
        "fa": [fa("اسرائیل"), fa("رژیم صهیونیستی", "صهیونیست"), fa("تل‌آویو", "تل آویو")],
        "ar": [ar("إسرائيل", "اسرائيل"), ar("الكيان الصهيوني", "الصهيوني", "الاحتلال الإسرائيلي")],
        "tr": [r"\bİsrail\w*", r"\bSiyonist\w*"], "es": [r"\bIsrael\b", r"\b[Ii]sraelí\w*", r"\b[Ss]ionista\w*"],
        "ur": [ur("اسرائیل", "صیہونی")], "ko": ["이스라엘"],
    },
    "CHINA": {
        "en": [(r"\bChina\b", r"(?:South|East) China Sea|China Daily|Chinese Taipei"), r"\bChinese\b", r"\bBeijing\b",
               r"\bPRC\b", r"\bXi Jinping\b"],
        "ru": [r"\bКита[йяюе]\w*", r"\bкитайск\w+", r"\bКНР\b", r"\bПекин\w*", r"\bСи Цзиньпин\w*"],
        "be": [r"\bКіта[йяюі]\w*", r"\bкітайск\w+", r"\bКНР\b"],
        "zh": ["中国", "中方", "中华人民共和国", "习近平", "中國", "中共", "習近平"],
        "fa": [fa("چین"), fa("پکن")], "ar": [ar("الصين", "بكين")], "tr": [r"\bÇin(?:'\w+|li\w*)?\b", r"\bPekin\w*"],
        "es": [r"\bChina\b", r"\bPekín\b", r"\bBeijing\b"], "ur": [ur("چین", "بیجنگ")], "ko": ["중국", "중화인민공화국"],
    },
    "RUSSIA": {
        "en": [r"\bRussia(?:n|ns)?\b", r"\bMoscow\b", r"\bKremlin\b", r"\bPutin\b"],
        "ru": [r"\bРосси[яиюей]\w*", r"\bроссийск\w+", r"\bМоскв\w+", r"\bКремл\w+", r"\bРФ\b", r"\bПутин\w*"],
        "be": [r"\bРасі[яіюй]\w*", r"\bрасійск\w+", r"\bМаскв\w+"],
        "zh": ["俄罗斯", "俄方", "俄军", "中俄", "俄乌", "美俄", "俄美", "莫斯科", "克里姆林宫", "普京", "俄羅斯", "俄國"],
        "fa": [fa("روسیه"), fa("مسکو"), fa("کرملین")], "ar": [ar("روسيا", "موسكو", "الكرملين")],
        "tr": [r"\bRusya\w*", r"\bMoskova\w*", r"\bKremlin\w*"], "es": [r"\bRusia\b", r"\bMoscú\b", r"\bKremlin\b"],
        "ur": [ur("روس")], "ko": ["러시아", "로씨야"],
    },
    "IRAN": {
        "en": [r"\bIran(?:ian|ians)?\b", r"\bTehran\b",
               (r"\bIslamic Republic\b", r"Islamic Republic of (?:Pakistan|Afghanistan|Mauritania)"), r"\bIRGC\b"],
        "ru": [r"\bИран\w*", r"\bиранск\w+", r"\bТегеран\w*"], "be": [r"\bІран\w*"],
        "zh": ["伊朗", "伊方", "德黑兰", "德黑蘭"],
        "fa": [fa("ایران"), fa("تهران")], "ar": [ar("إيران", "ايران", "طهران")], "tr": [r"\bİran\w*", r"\bTahran\w*"],
        "es": [r"\bIrán\b", r"\b[Ii]raní\w*", r"\bTeherán\b"], "ur": [ur("ایران")], "ko": ["이란"],
    },
    "INDIA": {
        "en": [r"\bIndia\b", (r"\bIndians?\b", r"Indian Ocean|American Indians?|West Indian"), r"\bNew Delhi\b", r"\bModi\b"],
        "ru": [r"\bИнди[яиюей]\b", r"\bиндийск\w+", r"\bНью-Дели\b"], "be": [r"\bІндыі?\w*"],
        "zh": [("印度", "印度尼西亚|印度洋|印度支那|印度教"), "印方", "新德里", "莫迪"],
        "fa": [fa("هند", "هندوستان"), fa("دهلی")], "ar": [ar("الهند", "نيودلهي")], "tr": [r"\bHindistan\w*"],
        "es": [r"\bIndia\b"], "ur": [ur("بھارت", "ہندوستان")],
        "ko": [("인도", "인도네시아|인도양|인도하|인도되|인도적|인도주의")],
    },
    "PAKISTAN": {
        "en": [r"\bPakistan(?:i|is)?\b", r"\bIslamabad\b", r"\bRawalpindi\b"],
        "ru": [r"\bПакистан\w*", r"\bпакистанск\w+", r"\bИсламабад\w*"], "be": [r"\bПакістан\w*"],
        "zh": ["巴基斯坦", "伊斯兰堡"], "fa": [fa("پاکستان")], "ar": [ar("باكستان")], "tr": [r"\bPakistan\w*"],
        "es": [r"\bPakistán\b"], "ur": [ur("پاکستان")], "ko": ["파키스탄"],
    },
    "DPRK": {
        "en": [r"\bDPRK\b", r"\bNorth Korea(?:n|ns)?\b", r"\bPyongyang\b", r"\bDemocratic People's Republic of Korea\b",
               r"\bKim Jong[ -]?Un\b"],
        "ru": [r"\bКНДР\b", r"\bСеверн\w+ Коре\w+", r"\bПхеньян\w*", r"\bсеверокорейск\w+", r"\bКим Чен Ын\w*"],
        "be": [r"\bКНДР\b"],
        "zh": [("朝鲜", "朝鲜半岛|朝鲜战争|朝鲜族"), "朝方", "平壤", "金正恩", "北韓"],
        "fa": [fa("کره شمالی")], "ar": [ar("كوريا الشمالية")], "tr": [r"\bKuzey Kore\w*"],
        "es": [r"\bCorea del Norte\b", r"\bRPDC\b", r"\b[Nn]orcorean\w+"], "ur": [ur("شمالی کوریا")],
        "ko": ["북한", "조선민주주의인민공화국", "북조선"],
    },
    "AUSTRALIA": {
        "en": [r"\bAustralia(?:n|ns)?\b", r"\bCanberra\b"],
        "ru": [r"\bАвстрали\w+", r"\bавстралийск\w+"], "be": [r"\bАўстрал\w+"],
        "zh": ["澳大利亚", "澳方", "澳军", "堪培拉", "中澳", "美澳", "澳洲", "澳大利亞"],
        "fa": [fa("استرالیا")], "ar": [ar("أستراليا", "استراليا")], "tr": [r"\bAvustralya\w*"],
        "es": [r"\bAustralia\b"], "ur": [ur("آسٹریلیا")], "ko": ["오스트레일리아", "호주"],
    },
    "WEST": {
        "en": [(r"\bthe West\b", r"the West (?:Bank|Coast|Africa|Asia)"), r"\bcollective West\b",
               r"\bWestern (?:countries|nations|powers|states|world|elites|sanctions|allies|media|governments|partners)\b"],
        "ru": [r"\bЗапад(?:а|у|ом|е)?\b", r"\bзападн\w+ (?:стран|элит|государств|санкци|партн[её]р|союзник|СМИ)\w*"],
        "be": [r"\bЗахад\w*"],
        "zh": [("西方", "东西方"), "美西方", ("欧美", "欧美同学会")],
        "fa": [(fa("غرب"), "غرب آسیا|غرب کشور|غرب ایران"), fa("غربی‌ها", "کشورهای غربی")],
        "ar": [ar("الغرب"), ar("الدول الغربية")],
        "tr": [(r"\bBatı(?:'\w+|lı\w*)?\b", r"Batı (?:Şeria|Asya|Afrika)")], "es": [r"\bOccidente\b"],
        "ur": [ur("مغرب", "مغربی ممالک")], "ko": ["서방"],
    },
}

# Patterns removed after the corpus audit (kept here so the decision is visible). Format: (target, lang, pattern, reason).
DROPPED: List[Tuple[str, str, str, str]] = [
    ("CHINA", "zh", "北京", "dateline noise: every Xinhua item opens 新华社北京…电"),
    ("CHINA", "zh", "我国", "self-reference; resolves to the speaker's own country in both CN and TW documents"),
    ("PAKISTAN", "zh", "巴方", "ambiguous: Pakistani side or Palestinian side"),
    ("PAKISTAN", "zh", "中巴", "ambiguous: China-Pakistan or China-Brazil"),
    ("UKRAINE", "ar", "كيف", "Kyiv spelling is identical to 'how'"),
    ("ROK", "ru", r"\bРК\b", "РК is as often the Republic of Kazakhstan"),
    ("NATO", "ru", r"\bАльянс\w*", "audit: also names of unrelated alliances (e.g. 'Альянс народной танцевальной культуры')"),
    ("US", "zh", "美机", "audit: mostly 驻美机构 (institutions in the US), 美机场"),
]

COUNTRY_SELF = {"US": "US", "CN": "CHINA", "RU": "RUSSIA", "IR": "IRAN", "IN": "INDIA", "PK": "PAKISTAN",
                "KP": "DPRK", "TW": "TAIWAN", "JP": "JAPAN", "KR": "ROK", "UA": "UKRAINE", "IL": "ISRAEL",
                "PH": "PHILIPPINES", "AU": "AUSTRALIA", "GB": "UK"}


@dataclass(frozen=True)
class Pattern:
    target: str
    lang: str
    key: str
    rx: "re.Pattern[str]"
    veto: Optional["re.Pattern[str]"]


# Wire-service datelines name a location, not a target ("新华社东京6月16日电" is not about Japan). Blanked before matching.
ZH_DATELINE = re.compile(r"(?:新华社|中新社|新华网|人民网|央视新闻|总台记者)[^，。（(]{0,12}?\d{1,2}月\d{1,2}日(?:电|讯)")


class Gazetteer:
    """Compiled patterns per language with a combined prefilter regex."""

    def __init__(self, table: Dict[str, Dict[str, List[Spec]]] = TARGETS, dropped: Sequence[Tuple] = DROPPED) -> None:
        drop = {(t, lang, p) for t, lang, p, _ in dropped}
        self.patterns: Dict[str, List[Pattern]] = {}
        for target, by_lang in table.items():
            for lang, specs in by_lang.items():
                for spec in specs:
                    src, veto = (spec, None) if isinstance(spec, str) else spec
                    if (target, lang, src) in drop:
                        continue
                    self.patterns.setdefault(lang, []).append(
                        Pattern(target, lang, f"{lang}:{readable(src)}", re.compile(src),
                                re.compile(veto) if veto else None))
        self.prefilter = {lang: re.compile("|".join(f"(?:{p.rx.pattern})" for p in pats))
                          for lang, pats in self.patterns.items()}
        h = hashlib.sha1(repr(sorted((p.key, p.target, p.veto.pattern if p.veto else "")
                                     for ps in self.patterns.values() for p in ps)).encode())
        self.version = h.hexdigest()[:12]

    def langs(self) -> List[str]:
        return sorted(self.patterns)

    def match(self, text: str, lang: Optional[str]) -> List[Tuple[str, str]]:
        """[(target, pattern key)] for every pattern with at least one non-vetoed match in `text`."""
        lang = lang if lang in self.patterns else "en"
        if lang == "zh":
            text = ZH_DATELINE.sub(lambda m: " " * len(m.group(0)), text)
        if not self.prefilter[lang].search(text):
            return []
        out = []
        for p in self.patterns[lang]:
            for m in p.rx.finditer(text):
                if p.veto and _vetoed(p.veto, text, m.start(), m.end()):
                    continue
                out.append((p.target, p.key))
                break
        return out

    def targets(self, text: str, lang: Optional[str]) -> List[str]:
        return sorted({t for t, _ in self.match(text, lang)})


def readable(src: str) -> str:
    """Pattern source without the Arabic-script boundary boilerplate (used as the audit key)."""
    for part in (FA_SUF, AR_SUF, UR_SUF, AR_PRE, AB, AE):
        src = src.replace(part, "")
    return src


def _vetoed(veto: "re.Pattern[str]", text: str, a: int, b: int) -> bool:
    for v in veto.finditer(text, max(0, a - 30), min(len(text), b + 30)):
        if v.start() < b and v.end() > a:
            return True
    return False


_DEFAULT: Optional[Gazetteer] = None


def default() -> Gazetteer:
    global _DEFAULT  # module-level cache of the compiled gazetteer
    if _DEFAULT is None:
        _DEFAULT = Gazetteer()
    return _DEFAULT


__all__ = ["Gazetteer", "TARGETS", "DROPPED", "COUNTRY_SELF", "default"]
