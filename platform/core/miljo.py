"""Miljøet prosessen kjører i — én variabel, én tolkning.

`DISPONIT_MILJO` avgjør to ting som MÅ følge hverandre: hvilke policystatuser
som får binde en beslutning (`api.policyregister.tillatte_statuser`), og om
forsiden kan love en modul til en kunde (`ui.server` → `/ui/oppsett.json`).
Tolker flaten variabelen mildere enn registeret, lover den «Tilgjengelig»
mens registeret fortsatt står i staging og lar `utkast` avgjøre ekte
forespørsler. Derfor bor sammenligningen her, ett sted, og ikke som to
uavhengige uttrykk som kan drive fra hverandre uten at noen ser det.

EKSAKT, INGEN NORMALISERING. Alt annet enn strengen `produksjon` er staging —
også ` produksjon ` med blanktegn fra en miljøfil. Det er ikke en mangel:
fail-closed betyr at en skrivefeil skal koste et løfte, ikke gi et, og en
verdi verten ikke selv regner som produksjon skal ingen flate regne som det.
"""
from __future__ import annotations

import os

PRODUKSJON = "produksjon"
STAGING = "staging"


def er_produksjon() -> bool:
    """Kjører denne prosessen i produksjonsmodus?"""
    return os.environ.get("DISPONIT_MILJO") == PRODUKSJON


def gjeldende_miljo() -> str:
    """-> `produksjon` eller `staging`. Alt ukjent er staging."""
    return PRODUKSJON if er_produksjon() else STAGING
