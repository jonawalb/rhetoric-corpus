"""DRAFT phrase dictionary: United States baseline (State Department briefings/statements, White House).

Same format as tools/rhetoric-global/scripts/dictionary.py: id, cat, label, re (case-insensitive Python
regex that also runs in JavaScript: no look-behind, no inline flags), note.

DRAFT: written before the corpus landed. Every pattern must be audited against docs/US/*.jsonl once
collection finishes (hit counts + a sample of matches per phrase, as build_data.py --audit does), and
kept only if it matches at least MIN_DOCS documents. Expect to drop or narrow some (e.g. "coercion",
"sanctions" are broad; "ironclad" may be mostly about Israel/Japan/Korea/Philippines alliances).
"""
from __future__ import annotations

MIN_DOCS = 8

US_CATS = ["World order", "Alliances and deterrence", "Nuclear and arms control", "China and the Indo-Pacific",
           "Russia and Ukraine", "Korean Peninsula", "Pressure tools", "Middle East and Iran"]

US = [
    # World order
    dict(id="rulesorder", cat="World order", label="rules-based (international) order",
         re=r"rules-based (?:international )?order",
         note="The core Biden-era formula for the order the U.S. defends."),
    dict(id="freeopen", cat="World order", label="free and open", re=r"free and open (?:indo-pacific|internet|seas?)",
         note="\"Free and open Indo-Pacific\" and kindred formulas."),
    dict(id="intlaw", cat="World order", label="international law / UN Charter",
         re=r"international law|un charter|charter of the united nations",
         note="Appeals to international law (compare with Russia/Iran use)."),
    dict(id="sovterr", cat="World order", label="sovereignty and territorial integrity",
         re=r"sovereignty and territorial integrity|territorial integrity",
         note="Mostly about Ukraine; also Taiwan-adjacent and Middle East uses."),
    dict(id="americafirst", cat="World order", label="America First / peace through strength",
         re=r"america first|peace through strength",
         note="Second Trump administration framing; expect near-zero before 2025."),
    # Alliances and deterrence
    dict(id="ironclad", cat="Alliances and deterrence", label="ironclad commitment",
         re=r"iron-?clad",
         note="\"Ironclad\" alliance commitments (Japan, ROK, Philippines, Israel, NATO)."),
    dict(id="extdeter", cat="Alliances and deterrence", label="extended deterrence",
         re=r"extended deterrence",
         note="The nuclear umbrella over allies, mostly ROK and Japan."),
    dict(id="integdeter", cat="Alliances and deterrence", label="integrated deterrence",
         re=r"integrated deterrence",
         note="The 2022 National Defense Strategy concept."),
    dict(id="article5", cat="Alliances and deterrence", label="Article 5 / every inch",
         re=r"article (?:5|five)\b|every inch of nato",
         note="NATO collective-defence pledge."),
    dict(id="mdt", cat="Alliances and deterrence", label="Mutual Defense Treaty",
         re=r"mutual defen[cs]e treaty|treaty of mutual cooperation and security|article (?:iv|v) of the",
         note="Treaty commitments to the Philippines, Japan, ROK."),
    dict(id="aukus", cat="Alliances and deterrence", label="AUKUS / Quad",
         re=r"\baukus\b|\bquad\b|quadrilateral security dialogue",
         note="Minilateral Indo-Pacific groupings."),
    # Nuclear and arms control
    dict(id="stratstab", cat="Nuclear and arms control", label="strategic stability",
         re=r"strategic stability",
         note="Arms-control framing of the nuclear balance (shared with RU dictionary)."),
    dict(id="newstart", cat="Nuclear and arms control", label="New START / arms control",
         re=r"new start|arms control|strategic offensive arms",
         note="New START and arms control generally."),
    dict(id="nonprolif", cat="Nuclear and arms control", label="nonproliferation / NPT",
         re=r"non-?proliferation|\bnpt\b",
         note="Nonproliferation regime and the NPT."),
    dict(id="nukerhet", cat="Nuclear and arms control", label="irresponsible nuclear rhetoric",
         re=r"(?:irresponsible|reckless|dangerous) nuclear rhetoric|nuclear saber-?rattling|nuclear threats?",
         note="Criticism of Russian (and DPRK) nuclear signalling."),
    dict(id="nucweap", cat="Nuclear and arms control", label="nuclear weapons / arsenal",
         re=r"nuclear (?:weapons?|arsenal|forces|warheads?|triad|testing|tests?)",
         note="Nuclear weapons talk, not energy. Also catches plutonium-pit / modernization mentions for the test case."),
    # China and the Indo-Pacific
    dict(id="tsps", cat="China and the Indo-Pacific", label="peace and stability across the Taiwan Strait",
         re=r"peace and stability (?:across|in) the taiwan strait|cross-strait (?:peace|stability)",
         note="Standard Taiwan formula."),
    dict(id="onechina", cat="China and the Indo-Pacific", label="one China policy / status quo",
         re=r"one china policy|taiwan relations act|six assurances|status quo",
         note="Taiwan policy anchors (\"status quo\" is broad: audit)."),
    dict(id="prccoerce", cat="China and the Indo-Pacific", label="coercion / coercive",
         re=r"\bcoerc\w*",
         note="PRC (and Russian) coercion; broad, audit by target."),
    dict(id="scs", cat="China and the Indo-Pacific", label="South China Sea / 2016 arbitral ruling",
         re=r"south china sea|arbitral (?:ruling|award|tribunal)|second thomas shoal|scarborough",
         note="Maritime disputes and the 2016 award."),
    dict(id="malign", cat="China and the Indo-Pacific", label="malign influence / activities",
         re=r"malign (?:influence|activit\w*|behaviou?r|actors?)",
         note="\"Malign\" as an adjective for PRC, Russian, Iranian conduct."),
    # Russia and Ukraine
    dict(id="warofagg", cat="Russia and Ukraine", label="war of aggression / full-scale invasion",
         re=r"war of aggression|full-scale invasion|unprovoked|brutal war|premeditated",
         note="Biden-era framing of Russia's war."),
    dict(id="standwith", cat="Russia and Ukraine", label="as long as it takes / stand with Ukraine",
         re=r"as long as it takes|stand with (?:ukraine|the people of ukraine)",
         note="Support-for-Ukraine formulas."),
    dict(id="peacedeal", cat="Russia and Ukraine", label="end the war / peace deal",
         re=r"end the (?:war|killing|bloodshed)|peace (?:deal|agreement) (?:between|for|in) (?:russia|ukraine)|durable peace",
         note="Settlement framing, expected to rise after 2025."),
    # Korean Peninsula
    dict(id="denuc", cat="Korean Peninsula", label="complete denuclearization",
         re=r"(?:complete )?denucleari[sz]ation(?: of the korean peninsula)?",
         note="The DPRK denuclearization goal."),
    dict(id="dprkviol", cat="Korean Peninsula", label="UN Security Council resolutions / ballistic missile",
         re=r"(?:violation|violates?) of (?:multiple )?(?:un )?security council resolutions|ballistic missile launch\w*",
         note="Condemnation formula for DPRK launches."),
    dict(id="dprkrus", cat="Korean Peninsula", label="DPRK-Russia military cooperation",
         re=r"(?:dprk|north korea\w*)[^.]{0,80}russia|russia[^.]{0,80}(?:dprk|north korea\w*)",
         note="DPRK troops/munitions for Russia (sentence-level co-mention; audit)."),
    # Pressure tools
    dict(id="sanctions", cat="Pressure tools", label="sanctions / designations",
         re=r"sanction\w*|designat(?:es?|ed|ing|ion) (?:\w+ ){0,3}(?:pursuant|under)|ofac",
         note="Sanctions and OFAC designations."),
    dict(id="maxpress", cat="Pressure tools", label="maximum pressure",
         re=r"maximum pressure",
         note="Iran (and earlier DPRK) policy by name; compare with IR dictionary."),
    dict(id="fto", cat="Pressure tools", label="Foreign Terrorist Organization / cartels",
         re=r"foreign terrorist organi[sz]ation|\bfto\b|cartels?",
         note="Terrorism designations, incl. 2025 cartel designations."),
    # Middle East and Iran
    dict(id="iranbomb", cat="Middle East and Iran", label="Iran must never have a nuclear weapon",
         re=r"iran (?:can|will|must) never (?:have|obtain|acquire|get) a nuclear weapon",
         note="Bipartisan Iran formula."),
    dict(id="israeldef", cat="Middle East and Iran", label="Israel's right to defend itself",
         re=r"israel(?:'s|’s)? right to defend itself",
         note="Standard formula on Israel."),
    dict(id="hostages", cat="Middle East and Iran", label="hostages / ceasefire",
         re=r"hostages?|cease-?fire",
         note="Gaza hostage and ceasefire diplomacy (broad; audit)."),
]
