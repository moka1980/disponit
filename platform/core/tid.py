"""Tidspunktformen, lest ETT sted — av core, for alle som måler den.

ETT PREDIKAT, TO KALLERE — OG DET BOR I CORE (Cursor P2-1 / Codex P1,
runde 5). `krev_biasmaaling` (m57s kjøretidsport) og
`manifestskjema._bias_utledet` (evidensgrensen) lover å måle det SAMME
tidspunktkravet, og fem runder har målt at to håndskrevne lesninger av
samme standard divergerer — først var grensen strengest, så kjøretiden.
Svaret er én lesning.

Den lesningen kan ikke bo i modulen. RUTINER §7: «Core importerer aldri
fra moduler.» En `from modules.m57_ats.evaluering import ...` inne i
`manifestskjema` snur modellgrensen: evidenslaget blir runtime-avhengig
av at modulpakken er importerbar — og CI-steget som validerer manifester
legger BARE `platform/core` på stien, så importen ville reist
`ModuleNotFoundError: No module named 'modules'` første gang et
`m57-v1`-punkt slås på. Her er den importerbar for begge: core som
`import tid`, modulen som `from tid import ...`, samme vei som
`oppdragskontrakt` og `policy_validator` alt går.

Fila har med vilje ingen andre avhengigheter enn `re` og `datetime`:
grensen skal kunne lese tidspunktet uten å dra inn et databaselag.
"""
from __future__ import annotations

import re
from datetime import datetime

#: RFC 3339 §5.6, som grammatikk. Ikke K4-brudd: dette er ti linjer ABNF
#: fra en lukket, uforanderlig standard, og det er VÅRT EGET felts
#: erklærte form — ikke et dokumentformat vi mottar. Formen måles her,
#: kalenderen av standardbiblioteket rett under.
_RFC3339 = re.compile(
    r"\A\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?"
    r"([Zz]|[+-]\d{2}:\d{2})\Z")


def rfc3339_lesbar(verdi: object) -> bool:
    """Er `verdi` et RFC 3339-tidspunkt som finnes i kalenderen?

    Skuddsekundet er tillatt der RFC 3339 §5.7 tillater det — ved
    minuttets slutt, altså `23:59:60` — og ingen andre steder.
    `2026-01-01T12:30:60Z` er ikke et tidspunkt som har eksistert.

    MERK at `api.artefaktskjema._er_rfc3339` er en ANNEN kontrakt, ikke en
    tredje lesning som skulle vært slått sammen med denne: den er en
    `format`-checker for vilkårlige artefaktskjemaer og godtar sekund 60
    hvor som helst, med vilje (en falsk avvisning av innhold er den ene
    feilen den ikke skal gjøre). Denne leser m57s egen `ts`, der en
    strengere lesning er kravet.
    """
    if not isinstance(verdi, str) or not _RFC3339.match(verdi):
        return False
    normalisert = re.sub(r"[Zz]\Z", "+00:00", verdi)
    if ":60" in normalisert[11:19]:
        # `time-second = 60` er BARE lovlig i det innskutte skuddsekundet,
        # og det står alltid sist i minuttet — sist i timen, sist i
        # døgnet. Uten denne avgrensningen ville substitusjonen under
        # gjort ethvert umulig sekund til et gyldig tidspunkt.
        if not normalisert[11:19].endswith("59:60"):
            return False
        normalisert = normalisert[:17] + "59" + normalisert[19:]
    try:
        datetime.fromisoformat(normalisert)
    except (TypeError, ValueError):
        return False
    return True
