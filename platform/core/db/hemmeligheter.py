"""systemd-credentials -> miljø (PR-009 v3 §5).

`LoadCredential=` er valgt for ALLE hemmeligheter: filene er root-eide,
eksponeres kun for riktig unit i `$CREDENTIALS_DIRECTORY`, og havner aldri
i `systemctl show-environment` eller i et environment-dump. Prosessene
leser dem HER, én gang ved oppstart — `EnvironmentFile` brukes kun for
ikke-hemmelig konfigurasjon.

Utenfor systemd (tester, CLI, utviklermaskin) finnes ikke katalogen, og
funksjonen er en no-op — miljøvariablene settes da som før. `setdefault`
gjør rekkefølgen ufarlig: en eksplisitt satt variabel vinner alltid over
credential-fila, så en test aldri kan arve stagings hemmeligheter.
"""
from __future__ import annotations

import os
from pathlib import Path

# PR-045 (Codex P2): `.strip()` er RIKTIG for hemmeligheter. En nøkkelfil
# provisjonert med `echo` i stedet for `printf` bærer et avsluttende
# linjeskift, ingen av hemmelighetene har blanktegn som betydning, og uten
# strippingen ville nøkkelen blitt ubrukelig på en måte som først viser seg
# ved første dekryptering.
#
# For KONFIGURASJON der blanktegn ER betydning, er den samme strippingen en
# stille oppgradering. `platform/core/miljo` lover fail-closed: alt annet enn
# den eksakte strengen `produksjon` er staging, «også ` produksjon ` med
# blanktegn fra en miljøfil». Det løftet holdt bare så lenge verdien kom fra
# miljøvariabelen. Etter at `DISPONIT_MILJO` ble en credential (PR-045) gikk
# den gjennom loopen under, og ` produksjon ` ble normalisert til
# `produksjon` FØR `miljo` fikk se den — altså produksjonsmodus fra en verdi
# verten selv ikke skrev som produksjon: `utkast` slutter å binde
# beslutninger, og forsiden lover «Tilgjengelig» uten kundedata.
#
# Denne modulen skal ikke avgjøre semantikk på vegne av leseren. Variabler
# der tolkningen tilhører noen andre, hydreres derfor RÅ — da er `miljo` den
# eneste som tolker `DISPONIT_MILJO`, uansett om verdien kom fra
# miljøvariabelen eller fra credential-fila.
EKSAKTE = frozenset({"DISPONIT_MILJO"})


def last_credentials() -> int:
    """-> antall variabler hentet fra $CREDENTIALS_DIRECTORY."""
    katalog = os.environ.get("CREDENTIALS_DIRECTORY")
    if not katalog:
        return 0
    antall = 0
    for fil in Path(katalog).iterdir():
        if fil.is_file() and fil.name not in os.environ:
            tekst = fil.read_text(encoding="utf-8")
            os.environ[fil.name] = tekst if fil.name in EKSAKTE \
                else tekst.strip()
            antall += 1
    return antall
