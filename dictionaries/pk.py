"""DRAFT phrase dictionary for Pakistan (MOFA spokesperson briefings/statements, ISPR press releases).

Same format as tools/rhetoric-global/scripts/dictionary.py: id, cat, label, re (case-insensitive
regex that also runs in JavaScript: no look-behind, no inline flags), note.

DRAFT: these patterns were written from knowledge of recurring Pakistani official formulas, NOT yet
checked against the corpus. Once docs/PK/*.jsonl has landed, audit every pattern (hit counts plus a
sample of matching sentences), drop phrases matching fewer than MIN_DOCS documents, and fix false
positives before any of this reaches the site.
"""
from __future__ import annotations

MIN_DOCS = 8

PK_CATS = ["Kashmir", "India", "Water", "Terrorism and the western border", "Deterrence and strategic stability",
           "China and partners", "Muslim world and Palestine", "Law and multilateralism", "Afghanistan",
           "United States"]

PK = [
    # Kashmir
    dict(id="iiojk", cat="Kashmir", label="IIOJK / occupied Kashmir",
         re=r"\biiojk\b|indian illegally occupied|occupied jammu (?:and|&) kashmir|indian occupied kashmir",
         note="Pakistan's official name for Indian-administered Kashmir."),
    dict(id="unscres", cat="Kashmir", label="UNSC resolutions on Kashmir",
         re=r"(?:relevant )?(?:unsc|security council) resolutions|right (?:to|of) self-determination",
         note="Settlement by UN resolutions and Kashmiri self-determination."),
    dict(id="aug5", cat="Kashmir", label="5 August 2019 / Youm-e-Istehsal",
         re=r"5(?:th)? august 2019|august 5,? 2019|youm-?e-?istehsal|article 370",
         note="India's revocation of Kashmir's special status."),
    # India
    dict(id="hindutva", cat="India", label="Hindutva / RSS-BJP", re=r"hindutva|\brss\b|rss-bjp|bjp-rss",
         note="Casting India's government as Hindu-nationalist."),
    dict(id="hegemonic", cat="India", label="hegemonic designs", re=r"hegemonic (?:designs|ambitions|mindset)|hegemon\w*",
         note="India as a would-be regional hegemon."),
    dict(id="falseflag", cat="India", label="false flag / irresponsible statements",
         re=r"false[- ]flag|irresponsible (?:and provocative )?(?:statements?|remarks)|baseless (?:allegations|propaganda)",
         note="Rejecting Indian accusations."),
    dict(id="sindoor", cat="India", label="May 2025 conflict (Bunyan-um-Marsoos / Sindoor)",
         re=r"bunyan[- ]?um[- ]?marsoos|marka-?e-?haq|operation sindoor|pahalgam",
         note="The April-May 2025 crisis and Pakistan's named operation."),
    # Water
    dict(id="iwt", cat="Water", label="Indus Waters Treaty", re=r"indus waters? treaty|\biwt\b|in abeyance",
         note="India's 2025 suspension of the treaty, which Pakistan calls an act of war."),
    dict(id="waterwar", cat="Water", label="weaponising water", re=r"weaponi[sz]\w* (?:of )?water|water as a weapon|act of war",
         note="Water stoppage framed as an act of war."),
    # Terrorism and the western border
    dict(id="statespon", cat="Terrorism and the western border", label="state-sponsored terrorism",
         re=r"state[- ]sponsored terror\w*|indian[- ]sponsored|sponsor\w* of terror\w*",
         note="Accusing India of sponsoring militancy in Pakistan."),
    dict(id="khawarij", cat="Terrorism and the western border", label="Fitna al-Khawarij / Fitna al-Hindustan",
         re=r"fitna al[- ]?khawarij|khawarij|fitna al[- ]?hindustan|\bttp\b|tehreek-?e-?taliban",
         note="The official 2024+ labels for the TTP and Baloch militants."),
    dict(id="ibo", cat="Terrorism and the western border", label="IBOs / terrorists neutralised",
         re=r"intelligence[- ]based operations?|\bibos?\b|terrorists? (?:were )?(?:neutralised|neutralized|sent to hell)",
         note="ISPR's operational formula for counter-terror raids."),
    # Deterrence and strategic stability
    dict(id="fsd", cat="Deterrence and strategic stability", label="full-spectrum / credible minimum deterrence",
         re=r"full[- ]spectrum deterrence|credible minimum deterrence",
         note="Pakistan's declared nuclear posture."),
    dict(id="restraint", cat="Deterrence and strategic stability", label="strategic restraint regime",
         re=r"strategic restraint(?: regime)?|strategic stability in south asia|strategic stability",
         note="Proposals for a South Asian restraint regime."),
    dict(id="nucweap", cat="Deterrence and strategic stability", label="nuclear weapons / missile tests",
         re=r"nuclear (?:weapons?|arsenal|deterrent|capable)|training launch|(?:ballistic|cruise) missile|shaheen|ghauri|babur|fatah|ababeel",
         note="Nuclear and missile references, incl. ISPR test announcements."),
    dict(id="quidproquo", cat="Deterrence and strategic stability", label="quid pro quo plus / befitting response",
         re=r"quid pro quo(?: plus)?|befitting (?:response|reply)|swift(?:, | and )(?:befitting|resolute)",
         note="Threatened retaliation formulas."),
    # China and partners
    dict(id="cpec", cat="China and partners", label="CPEC", re=r"\bcpec\b|china-?pakistan economic corridor",
         note="The China-Pakistan Economic Corridor."),
    dict(id="ironbro", cat="China and partners", label="iron brothers / all-weather",
         re=r"iron(?:-clad)? brothers?|all[- ]weather (?:strategic )?(?:cooperative )?partner\w*|higher than the himalayas|ironclad",
         note="Ritual formulas for China ties."),
    # Muslim world and Palestine
    dict(id="palestine", cat="Muslim world and Palestine", label="Palestine / Gaza / al-Quds",
         re=r"palestin\w*|\bgaza\b|al-?quds",
         note="Gaza and the Palestinian state on pre-1967 borders with al-Quds as capital."),
    dict(id="oic", cat="Muslim world and Palestine", label="OIC / Islamophobia",
         re=r"\boic\b|organi[sz]ation of islamic cooperation|islamophob\w*",
         note="The OIC and Islamophobia campaign."),
    # Law and multilateralism
    dict(id="uncharter", cat="Law and multilateralism", label="UN Charter / international law",
         re=r"un charter|charter of the united nations|international law",
         note="Appeals to the Charter and international law."),
    dict(id="sco", cat="Law and multilateralism", label="SCO", re=r"\bsco\b|shanghai cooperation",
         note="The Shanghai Cooperation Organisation (Pakistan chairs 2026-27)."),
    # Afghanistan
    dict(id="afgsoil", cat="Afghanistan", label="Afghan soil / interim Afghan government",
         re=r"afghan soil|interim afghan government|afghan taliban|\biag\b",
         note="Demands that Kabul stop militants using Afghan territory."),
    # United States
    dict(id="usa", cat="United States", label="United States named", re=r"united states|\bu\.s\.|washington",
         note="Every mention of the U.S., for scale."),
]
