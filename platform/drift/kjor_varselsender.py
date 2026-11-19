"""Inngangspunkt for `disponit-varselsender.service`.

Tømmer e-postkøen én gang og skriver en linje til journalen. `Type=oneshot`:
hver kjøring er en egen prosess, og køen er tilstanden — ingen teller trengs i
minnet mellom kjøringer.

En feilet sending er IKKE en feilet kjøring. Varselet står i portalen uansett;
det er der det egentlig bor. Exit-koden er derfor 0 så lenge senderen selv
virket — driften skal ikke vekkes av at én adresse ikke tok imot, bare av at
senderen ikke kunne kjøre — eller av at et FORPASS ikke kjørte (kode 2):
da er det varsler som aldri ble køet, og dem henter ingen backoff inn.
"""
from __future__ import annotations

import os
import sys

from . import varselsender


def main() -> int:
    from db.pg import koble
    from db.hemmeligheter import last_credentials
    # `LoadCredential` legger hemmelighetene som FILER i
    # $CREDENTIALS_DIRECTORY — den setter ingen miljøvariabler. Uten dette
    # kallet fant senderen aldri sin DB-URL og avsluttet med 1 hvert 5.
    # minutt, mens køen ble liggende. Samme mekanisme som de andre unitene
    # bruker; jeg hadde bare lest credentialen som om den var en env-variabel.
    last_credentials()
    dsn = os.environ.get("DISPONIT_DATABASE_URL")
    if not dsn:
        print("AVBRUTT: DISPONIT_DATABASE_URL mangler", file=sys.stderr)
        return 1
    conn = koble(dsn)
    try:
        res = varselsender.kjor(conn)
    finally:
        conn.close()
    print("varselsender: " + " ".join(f"{k}={v}" for k, v in res.items()))
    # ET FORPASS SOM FEILET ER STØY, IKKE STILLHET (ADR-003 D2). Sendingen
    # har alt gått — forpassene er skjermet, så koden under endrer ingenting
    # i det som ble sendt. Men en sveip som ikke kjørte er varsler som ALDRI
    # BLE KØET, og som ingen backoff henter inn; med 0 her sto den bare som
    # et tall i en journallinje ingen leser. Kode 2 gjør uniten `failed`,
    # og det er den tilstanden driften faktisk ser etter.
    if res.get("forpass_feil"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
