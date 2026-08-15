"""Hvilket miljø verten ER — én avlesning, én tolkning (Codex P2 til PR #42).

`DISPONIT_MILJO` hadde to lesere som tolket den hver for seg:
`api/policyregister.tillatte_statuser` sammenlignet verdien rått, mens
`ui/server.ui_oppsett` `.strip()`-et den først. En padded verdi
(`DISPONIT_MILJO=" produksjon "`, satt for hånd utenfor `opp.sh`) leste de
derfor MOTSATT: forsiden lovet kunden produksjon, mens registeret fortsatt
bandt beslutninger med staging-statusene `utkast` og `validert_pilot`.
Løftet på skjermen og regelverket bak det er nøyaktig det paret som aldri kan
få lov til å sprike, og med to uavhengige avlesninger var det bare et spørsmål
om tid før de gjorde det igjen.

Regelen er fail-closed og eksakt, som `opp.sh` sin miljøport: KUN den nøyaktige
strengen `produksjon` er produksjon. Alt annet — skrivefeil, padding, tom,
uspesifisert — er staging, altså det trangeste løftet og det bredeste
statussettet. En verdi verten ikke kan uttale presist skal koste et løfte,
ikke gi et.

Ingen avhengigheter utover stdlib: `ui/server.py` rører ikke databasen, og
skal kunne lese miljøet uten å dra inn psycopg.
"""
from __future__ import annotations

import os

#: De to miljøene som finnes. `opp.sh` nekter å rulle ut på noe annet.
PRODUKSJON = "produksjon"
STAGING = "staging"


def miljo() -> str:
    """-> `"produksjon"` eller `"staging"`. Aldri noe annet, aldri et kast."""
    return PRODUKSJON if os.environ.get("DISPONIT_MILJO") == PRODUKSJON \
        else STAGING


def er_produksjon() -> bool:
    """Sant kun når verten sier `produksjon` eksakt."""
    return miljo() == PRODUKSJON
