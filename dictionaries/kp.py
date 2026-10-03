"""DRAFT phrase dictionary: North Korea (DPRK) — Rodong Sinmun English edition (docs/KP/*.jsonl).

Same format as tools/rhetoric-global/scripts/dictionary.py: id, cat, label, re (case-insensitive Python
regex that also runs in JavaScript: no look-behind, no inline flags), note.

DRAFT: written before the corpus landed, from the standing formulas of DPRK English-language state media.
Every pattern must be audited against docs/KP/*.jsonl once collection finishes (hit counts + a sample of
matches per phrase, as build_data.py --audit does) and kept only if it matches at least MIN_DOCS documents.
Caveats: the KP corpus is state media only (Rodong Sinmun via Wayback copies; KCNA itself is not reachable
from the collection machine), so it mixes leader coverage, economy and culture with foreign-policy
commentary — expect low per-document rates for the foreign-policy rows. Also: KCNA/Rodong English spells
"Phyongyang"-style names and uses "puppet" for South Korea mainly through 2023; after the 2024 "two hostile
states" turn the ROK is "the ROK" or "the Republic of Korea" (check the trend, don't assume it).
"""
from __future__ import annotations

MIN_DOCS = 8

KP_CATS = ["United States and allies", "Nuclear forces and deterrence", "South Korea", "War and exercises",
           "Sovereignty and self-reliance", "Russia and the anti-imperialist front"]

KP = [
    # United States and allies
    dict(id="hostilepol", cat="United States and allies", label="hostile policy", re=r"hostile polic\w*",
         note="The \"hostile policy\" of the U.S. as the root of tension on the peninsula."),
    dict(id="imperialists", cat="United States and allies", label="U.S. imperialists", re=r"u\.?s\.? imperialis\w*|imperialists?",
         note="\"U.S. imperialists\" and imperialism in general."),
    dict(id="gangsterlike", cat="United States and allies", label="gangster-like / gangster", re=r"gangster\w*",
         note="Characterising U.S. demands or conduct as gangster-like."),
    dict(id="trilateral", cat="United States and allies", label="U.S.-Japan-ROK trilateral / Asian NATO",
         re=r"tripartite|trilateral|asian (?:version of )?nato",
         note="The U.S.-Japan-ROK alignment, often framed as an \"Asian NATO\"."),
    dict(id="nucleargeo", cat="United States and allies", label="nuclear strategic assets", re=r"strategic assets?|nuclear (?:aircraft )?carrier|strategic bombers?|b-?52\w*|nuclear submarine",
         note="Deployments of U.S. strategic assets around the peninsula."),
    # Nuclear forces and deterrence
    dict(id="nucdeter", cat="Nuclear forces and deterrence", label="nuclear (war) deterrent",
         re=r"nuclear (?:war )?deterren\w*|war deterren\w*",
         note="The DPRK's own \"nuclear war deterrent\"."),
    dict(id="nucforce", cat="Nuclear forces and deterrence", label="nuclear force(s) / nuclear power",
         re=r"nuclear forces?|nuclear (?:weapons )?state|nuclear power\b(?! (?:plant|station))",
         note="Status claims: \"nuclear weapons state\", \"nuclear force\"."),
    dict(id="missiles", cat="Nuclear forces and deterrence", label="ICBM / Hwasong / hypersonic",
         re=r"\bicbm\w*|hwasong\w*|hypersonic|ballistic missiles?|cruise missiles?",
         note="Named missile systems and launch reporting."),
    dict(id="satellite", cat="Nuclear forces and deterrence", label="reconnaissance satellite", re=r"reconnaissance satellite|malligyong",
         note="The military reconnaissance satellite programme."),
    # South Korea
    dict(id="puppet", cat="South Korea", label="puppet(s)", re=r"puppets?\b",
         note="South Korean authorities or forces as \"puppets\"."),
    dict(id="twostates", cat="South Korea", label="two hostile states / principal enemy",
         re=r"two (?:hostile|belligerent) states|principal enemy|primary foe|most hostile state",
         note="The 2024 doctrine that the ROK is a separate, hostile state (not a reunification partner)."),
    dict(id="reunification", cat="South Korea", label="reunification", re=r"reunif\w*",
         note="Reunification language, which the 2024 turn should suppress."),
    # War and exercises
    dict(id="wardrills", cat="War and exercises", label="war drills / war rehearsal",
         re=r"(?:joint )?(?:military |war )?(?:drills?|exercises?) (?:for )?(?:northward )?invasion|war (?:drills?|rehearsals?|exercises?)|northward",
         note="U.S.-ROK exercises as rehearsals for invasion."),
    dict(id="freedomshield", cat="War and exercises", label="Freedom Shield / Ulchi Freedom", re=r"freedom shield|ulchi|freedom edge",
         note="Named U.S.-ROK exercises."),
    dict(id="brink", cat="War and exercises", label="brink of (nuclear) war", re=r"brink of (?:a )?(?:nuclear )?war|nuclear war|touch-and-go",
         note="Warnings that the peninsula is on the brink of nuclear war."),
    # Sovereignty and self-reliance
    dict(id="sovereignty", cat="Sovereignty and self-reliance", label="sovereignty / right to self-defence",
         re=r"sovereign\w*|right to self-defen[cs]e|self-defensive",
         note="Weapons tests as an exercise of sovereign self-defence."),
    dict(id="juche", cat="Sovereignty and self-reliance", label="Juche / self-reliance", re=r"\bjuche\b|self-relian\w*|self-supporting",
         note="Juche and economic self-reliance."),
    dict(id="sanctions", cat="Sovereignty and self-reliance", label="sanctions", re=r"sanction\w*",
         note="UN and U.S. sanctions."),
    # Russia and the anti-imperialist front
    dict(id="russia", cat="Russia and the anti-imperialist front", label="Russia / comprehensive strategic partnership",
         re=r"\brussia\w*|comprehensive strategic partnership",
         note="The 2024 DPRK-Russia treaty and partnership."),
    dict(id="overseasops", cat="Russia and the anti-imperialist front", label="overseas military operations / Kursk",
         re=r"overseas (?:military )?operations?|kursk",
         note="DPRK troops' role in the Kursk operation (acknowledged from 2025)."),
    dict(id="multipolar", cat="Russia and the anti-imperialist front", label="multipolar / hegemony",
         re=r"multipolar\w*|unipolar\w*|hegemon\w*",
         note="Shared Russia/China world-order vocabulary."),
]
