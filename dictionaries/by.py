"""DRAFT phrase dictionary: Belarus — president.gov.by events and MFA news (docs/BY/*.jsonl), EN and RU.

Same format as tools/rhetoric-global/scripts/dictionary.py: id, cat, label, re (case-insensitive Python
regex that also runs in JavaScript: no look-behind, no inline flags), note. Belarus is collected in both
English and Russian, so BY holds the English patterns and BY_RU the Russian stems for the same ids.

DRAFT: written before the corpus landed. Every pattern must be audited against docs/BY/*.jsonl once
collection finishes (hit counts + a sample of matches per phrase, as build_data.py --audit does) and
kept only if it matches at least MIN_DOCS documents. Caveats: president.gov.by is dominated by greetings,
meetings and personnel decisions, so rates are low; "sovereignty" and "NATO" will be broad; Russian
stems need checking for false hits (e.g. "союз" also matches other unions — hence the narrower pattern).
"""
from __future__ import annotations

MIN_DOCS = 8

BY_CATS = ["Union State and Russia", "The West and world order", "Security and nuclear", "Sovereignty and the regime",
           "Neighbours and the border"]

BY = [
    # Union State and Russia
    dict(id="unionstate", cat="Union State and Russia", label="Union State", re=r"union state",
         note="The Belarus-Russia Union State."),
    dict(id="brotherly", cat="Union State and Russia", label="brotherly / fraternal Russia", re=r"brotherly|fraternal",
         note="Russia as the \"brotherly\" people/state."),
    dict(id="eaeu", cat="Union State and Russia", label="EAEU / CSTO / CIS", re=r"\beaeu\b|eurasian economic union|\bcsto\b|collective security treaty|\bcis\b",
         note="Eurasian integration structures."),
    # The West and world order
    dict(id="collwest", cat="The West and world order", label="collective West", re=r"collective west",
         note="Borrowed Russian framing of the West as one bloc."),
    dict(id="multipolar", cat="The West and world order", label="multipolar / Eurasian security",
         re=r"multipolar\w*|eurasian security|eurasian charter",
         note="Minsk's Eurasian security architecture initiative and multipolarity."),
    dict(id="sanctions", cat="The West and world order", label="sanctions / unfriendly", re=r"sanction\w*|unfriendly|illegitimate (?:restrictive|unilateral)",
         note="Western sanctions and \"unfriendly countries\"."),
    dict(id="colourrev", cat="The West and world order", label="colour revolution / 2020 mutiny",
         re=r"colou?r revolution|attempted coup|mutiny|blitzkrieg|2020 (?:events|riots)",
         note="The 2020 protests framed as a Western-backed coup attempt."),
    # Security and nuclear
    dict(id="tactnuc", cat="Security and nuclear", label="tactical nuclear weapons", re=r"(?:tactical|non-strategic) nuclear|nuclear weapons? (?:in|on the territory of) belarus",
         note="Russian tactical nuclear weapons stationed in Belarus (from 2023)."),
    dict(id="oreshnik", cat="Security and nuclear", label="Oreshnik", re=r"oreshnik",
         note="The Oreshnik intermediate-range system deployed to Belarus (2025)."),
    dict(id="nato", cat="Security and nuclear", label="NATO / militarisation", re=r"\bnato\b|militari[sz]\w*",
         note="NATO build-up on the western border."),
    dict(id="zapad", cat="Security and nuclear", label="Zapad / joint exercises", re=r"zapad|joint (?:military )?exercises?",
         note="Zapad and other Union State exercises."),
    dict(id="secguarantee", cat="Security and nuclear", label="security guarantees", re=r"security guarantees?",
         note="The 2024 Union State security-guarantees treaty."),
    # Sovereignty and the regime
    dict(id="sovereignty", cat="Sovereignty and the regime", label="sovereignty and independence", re=r"sovereign\w*|independen\w*",
         note="Sovereignty claims (broad: also Independence Day greetings — audit)."),
    dict(id="peaceful", cat="Sovereignty and the regime", label="peace and stability / peaceful sky", re=r"peaceful sky|peace and (?:stability|tranquil\w*)|peace(?:ful)? and quiet",
         note="Lukashenko's \"peaceful sky\" stability narrative."),
    dict(id="extremist", cat="Sovereignty and the regime", label="extremist / fugitives", re=r"extremis\w*|fugitive\w*|runaways?",
         note="Opposition and exiles as extremists or fugitives."),
    # Neighbours and the border
    dict(id="ukraine", cat="Neighbours and the border", label="Ukraine / special military operation", re=r"ukrain\w*|special military operation",
         note="The war next door."),
    dict(id="poland", cat="Neighbours and the border", label="Poland / Lithuania / Baltic", re=r"\bpol(?:and|ish)\b|lithuani\w*|latvi\w*|baltic",
         note="Western neighbours, border closures and migrant crises."),
]

# Russian stems for the same ids (Belarusian-Russian official texts). Same audit rule applies.
BY_RU = [
    dict(id="unionstate", re=r"союзн\w* государств\w*"),
    dict(id="brotherly", re=r"братск\w*"),
    dict(id="eaeu", re=r"еаэс|евразийск\w* экономическ\w* союз\w*|одкб|\bснг\b"),
    dict(id="collwest", re=r"коллективн\w* запад\w*"),
    dict(id="multipolar", re=r"многополярн\w*|однополярн\w*|евразийск\w* безопасност\w*|евразийск\w* харти\w*"),
    dict(id="sanctions", re=r"санкци\w*|недружественн\w*|нелегитимн\w* (?:ограничительн|односторонн)\w*"),
    dict(id="colourrev", re=r"цветн\w* революци\w*|попытк\w* (?:государственного )?переворот\w*|мятеж\w*|блицкриг\w*"),
    dict(id="tactnuc", re=r"тактическ\w* ядерн\w*|нестратегическ\w* ядерн\w*|ядерн\w* оружи\w* (?:в|на территории) беларус\w*"),
    dict(id="oreshnik", re=r"орешник\w*"),
    dict(id="nato", re=r"\bнато\b|милитаризац\w*"),
    dict(id="zapad", re=r"запад-20\d\d|«запад|совместн\w* (?:военн\w* )?учени\w*"),
    dict(id="secguarantee", re=r"гаранти\w* безопасност\w*"),
    dict(id="sovereignty", re=r"суверенит\w*|суверенн\w*|независимост\w*"),
    dict(id="peaceful", re=r"мирн\w* неб\w*|мир\w* и стабильност\w*|мир\w* и спокойстви\w*"),
    dict(id="extremist", re=r"экстремис\w*|беглы\w*|сбежавш\w*"),
    dict(id="ukraine", re=r"украин\w*|специальн\w* военн\w* операци\w*"),
    dict(id="poland", re=r"польш\w*|польск\w*|литв\w*|литовск\w*|латви\w*|прибалт\w*"),
]
