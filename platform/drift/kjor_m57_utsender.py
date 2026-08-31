"""Inngangspunkt for `disponit-m57-utsending.service`.

Konsumerer signerte utsendingslister én gang (oneshot bak timeren) og
skriver en linje til journalen. Kvitteringstabellen er tilstanden —
ingen teller i minnet mellom kjøringer, som varselsenderen.

En feilet sending er IKKE en feilet kjøring: utfallet står på raden
(`feilet` prøves igjen, `uviss` venter på et menneske). Exit-koden er 0
så lenge senderen selv virket.
"""
from __future__ import annotations

import os
import sys

from . import m57_utsender


def main() -> int:
    from db.hemmeligheter import last_credentials
    from db.pg import koble
    last_credentials()
    dsn = os.environ.get("DISPONIT_DATABASE_URL")
    if not dsn:
        print("AVBRUTT: DISPONIT_DATABASE_URL mangler", file=sys.stderr)
        return 1
    conn = koble(dsn)
    try:
        res = m57_utsender.kjor(conn)
    finally:
        conn.close()
    print("m57-utsender: " + " ".join(f"{k}={v}" for k, v in res.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
