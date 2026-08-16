"""Inngangspunkt for `disponit-varselsender.service`.

Tømmer e-postkøen én gang og skriver en linje til journalen. `Type=oneshot`:
hver kjøring er en egen prosess, og køen er tilstanden — ingen teller trengs i
minnet mellom kjøringer.

En feilet sending er IKKE en feilet kjøring. Varselet står i portalen uansett;
det er der det egentlig bor. Exit-koden er derfor 0 så lenge senderen selv
virket — driften skal ikke vekkes av at én adresse ikke tok imot, bare av at
senderen ikke kunne kjøre.
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
