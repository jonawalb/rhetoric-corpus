"""DRAFT phrase dictionary for India (MEA weekly media briefings, speeches and statements).

Same format as tools/rhetoric-global/scripts/dictionary.py: id, cat, label, re (case-insensitive
regex that also runs in JavaScript: no look-behind, no inline flags), note.

DRAFT: these patterns were written from knowledge of recurring Indian official formulas, NOT yet
checked against the corpus. Once docs/IN/in_mea.jsonl has landed, audit every pattern (hit counts plus a
sample of matching sentences), drop phrases matching fewer than MIN_DOCS documents, and fix false
positives (e.g. "quad" in other words, "LAC" in Latin-American contexts) before any of this reaches the site.
"""
from __future__ import annotations

MIN_DOCS = 8

IN_CATS = ["Pakistan and terrorism", "China and the border", "World order", "Indo-Pacific", "Nuclear",
           "Civilisational framing", "Russia and Ukraine", "West Asia", "Neighbourhood", "Diaspora and economy"]

IN = [
    # Pakistan and terrorism
    dict(id="xbterror", cat="Pakistan and terrorism", label="cross-border terrorism",
         re=r"cross-?border terror\w*", note="The core formula on Pakistan."),
    dict(id="zerotol", cat="Pakistan and terrorism", label="zero tolerance for terrorism",
         re=r"zero[- ]tolerance|terror(?:ism)? and talks? (?:can ?not|cannot) go together|blood and water",
         note="No dialogue while terrorism continues."),
    dict(id="sindoor", cat="Pakistan and terrorism", label="Operation Sindoor / Pahalgam",
         re=r"operation sindoor|\bsindoor\b|pahalgam", note="The April-May 2025 attack and India's response."),
    dict(id="pok", cat="Pakistan and terrorism", label="PoK / integral part",
         re=r"pakistan[- ]occupied|\bpok\b|\bpojk\b|integral (?:and inalienable )?part of india",
         note="Kashmir as an integral part of India."),
    dict(id="iwtabey", cat="Pakistan and terrorism", label="Indus Waters Treaty in abeyance",
         re=r"indus waters? treaty|in abeyance", note="The 2025 suspension of the treaty."),
    # China and the border
    dict(id="lac", cat="China and the border", label="LAC / disengagement / border areas",
         re=r"line of actual control|\blac\b|disengagement|de-?escalation|peace and tranquill?ity in the border areas",
         note="The China frontier and the 2020-24 standoff."),
    dict(id="threemut", cat="China and the border", label="three mutuals",
         re=r"mutual respect, mutual sensitivity and mutual interest|three mutuals?",
         note="India's stated basis for China ties."),
    # World order
    dict(id="globalsouth", cat="World order", label="Global South / Voice of the Global South",
         re=r"global south", note="India as voice of the developing world."),
    dict(id="stratauto", cat="World order", label="strategic autonomy / multi-alignment",
         re=r"strategic autonomy|multi-?align\w*", note="Independence from blocs."),
    dict(id="reformmulti", cat="World order", label="reformed multilateralism / UNSC reform",
         re=r"reformed multilateralism|(?:unsc|security council) reform|reform of the (?:un )?security council|permanent (?:seat|membership)",
         note="Demand for a permanent UNSC seat."),
    dict(id="multipolar", cat="World order", label="multipolar world", re=r"multipolar\w*|rebalancing",
         note="A multipolar world and Asia."),
    dict(id="brics", cat="World order", label="BRICS / SCO / G20",
         re=r"\bbrics\b|\bsco\b|shanghai cooperation|\bg-?20\b", note="Groupings India uses."),
    # Indo-Pacific
    dict(id="quad", cat="Indo-Pacific", label="Quad", re=r"\bquad\b|quadrilateral", note="The U.S.-Japan-Australia-India Quad."),
    dict(id="fip", cat="Indo-Pacific", label="free, open and inclusive Indo-Pacific / SAGAR",
         re=r"free,? open,? (?:and )?inclusive|indo-?pacific|\bsagar\b|mahasagar|rules-based",
         note="India's Indo-Pacific vocabulary."),
    # Nuclear
    dict(id="nfu", cat="Nuclear", label="no first use / credible minimum deterrence",
         re=r"no[- ]first[- ]use|credible minimum deterrence|nuclear blackmail",
         note="India's doctrine; \"nuclear blackmail\" as the 2025 formula against Pakistan."),
    dict(id="nonprolif", cat="Nuclear", label="non-proliferation / NSG / disarmament",
         re=r"non-?proliferation|\bnsg\b|nuclear suppliers group|disarmament|nuclear weapons?",
         note="Arms control and export regimes."),
    # Civilisational framing
    dict(id="vasudhaiva", cat="Civilisational framing", label="Vasudhaiva Kutumbakam / one earth one family",
         re=r"vasudhaiva kutumbakam|one earth,? one family|vishwa ?bandhu|vishwamitra",
         note="Civilisational slogans of Indian diplomacy."),
    dict(id="viksit", cat="Civilisational framing", label="Viksit Bharat / Amrit Kaal",
         re=r"viksit bharat|amrit ?kaal", note="Development-nationalist slogans."),
    # Russia and Ukraine
    dict(id="notwar", cat="Russia and Ukraine", label="era of war / dialogue and diplomacy",
         re=r"(?:not|isn't) (?:an|the) era of war|dialogue and diplomacy",
         note="India's line on Ukraine and other conflicts."),
    dict(id="oil", cat="Russia and Ukraine", label="energy security / Russian oil",
         re=r"energy security|russian oil|crude oil|tariff\w*",
         note="Defending oil purchases and tariff disputes with the U.S."),
    # West Asia
    dict(id="twostate", cat="West Asia", label="two-state solution / Gaza",
         re=r"two-?state solution|\bgaza\b|palestin\w*", note="India's line on Israel-Palestine."),
    # Neighbourhood
    dict(id="nbfirst", cat="Neighbourhood", label="Neighbourhood First / Act East",
         re=r"neighbou?rhood first|act east|bangladesh|sri lanka|nepal|maldives",
         note="Regional policy labels and neighbours."),
    # Diaspora and economy
    dict(id="diaspora", cat="Diaspora and economy", label="Indian nationals / diaspora / Operation (evacuation)",
         re=r"indian nationals|diaspora|evacuat\w*|operation (?:ganga|kaveri|ajay|sindhu)",
         note="Protection and evacuation of Indians abroad."),
]
