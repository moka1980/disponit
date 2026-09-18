"""Dagen slik BASEN ser den — den eneste klokka testene har lov til å tro på.

Testfilene regnet lenge `datetime.date.today()` ÉN gang ved import, i
maskinens LOKALE sone. Modulene regner `current_date` i basens sone, og
de to er ikke samme dag:

  * krysser kjøringen midnatt mellom import og måling, blir et 40 døgn
    gammelt avvik 41 døgn, og en regel som utløp «i går» gjelder
    fortsatt;
  * står sonene fra hverandre, skjer det samme hele døgnet.

CI falt på nettopp dette 18.9.2026 kl. 00:07, på to tester i M-53 som
ikke var gale. Målt etterpå: 31 tester i ni filer faller når basens dato
står én dag fra Pythons.

Datoen HENTES derfor fra basen, per kall, over en egen tilkobling. Ett
rundtur-kall er billigere enn en test som faller på klokka — og en
testfil som ikke har en base å spørre, hopper likevel over.
"""
from __future__ import annotations

import datetime
import os

_KOBLING = None


def i_dag() -> datetime.date:
    """`current_date`, hentet fra basen nå."""
    global _KOBLING
    import psycopg
    dsn = (os.environ.get("DISPONIT_TEST_MIGRATOR_DSN")
           or os.environ.get("DISPONIT_TEST_DSN"))
    if not dsn:
        # INGEN BASE Å SPØRRE. Kalleren er en test som uansett hoppes
        # over; å falle her ville gjort en manglende DSN til en feil i
        # stedet for et hopp.
        return datetime.date.today()
    if _KOBLING is None or _KOBLING.closed:
        _KOBLING = psycopg.connect(dsn)
    dag = _KOBLING.execute("SELECT current_date").fetchone()[0]
    _KOBLING.rollback()
    return dag


def dag(n: int) -> datetime.date:
    """`n` døgn fra basens i dag (negativt = bakover)."""
    return i_dag() + datetime.timedelta(days=n)
