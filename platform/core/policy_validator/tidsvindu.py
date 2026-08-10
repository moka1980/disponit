"""Delt tidsvindu-kodevei mellom motoren (per-instant-medlemskap) og
klassifikatoren (mengdeinklusjon) — PR-013 v3 §2 / v4 §5, port 14.

ÉN parse, ÉN mengdefunksjon: motoren spør «er dette tidspunktet i vinduet?»,
klassifikatoren spør «hvilke ukeminutter dekker vinduet?». Begge svar kommer
fra `tillatte_ukeminutter`, så de KAN ALDRI divergere.

Et vindu er `"<dag>-<dag> HH:MM-HH:MM"` tolket i policyens IANA-tidssone. Både
dag-intervallet og klokke-intervallet kan WRAPPE (`fre-man`, `22:00-06:00` for
et nattvindu). Settet er lokale ukeminutter (0..10079); tidssonen bæres for
kalleren (klassifikatoren behandler tidssoneendring som en egen UTVIDER-regel,
og motoren mapper instanter til lokal tid selv).
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_DAGER = ["man", "tir", "ons", "tor", "fre", "lor", "son"]
_MIN_PER_UKE = 7 * 24 * 60


def _min_of(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _parse(vindu: str) -> tuple[int, int, int, int]:
    dager, klokke = vindu.split()
    d0, d1 = (_DAGER.index(x) for x in dager.split("-"))
    start, slutt = klokke.split("-")
    return d0, d1, _min_of(start), _min_of(slutt)


def tillatte_ukeminutter(vindu: str, tidssone: str | None = None) -> frozenset[int]:
    """Mengden av tillatte lokale UKEMINUTTER (0..10079) et vindu dekker.

    Dag- og klokke-intervall er INKLUSIVE i begge ender og kan wrappe. Vinduet
    er definert i lokal veggklokke, så mengden er tidssone-uavhengig på
    ukeminutt-nivå (tidssonen påvirker bare hvilke instanter som treffer den —
    det er motorens jobb, og en tidssoneendring er en egen klassifikatorregel).
    """
    d0, d1, s, e = _parse(vindu)
    dager = [d0] if d0 == d1 else (
        list(range(d0, d1 + 1)) if d0 < d1
        else list(range(d0, 7)) + list(range(0, d1 + 1)))
    # klokkeslett-minutter innen ett døgn (0..1439), inklusiv, med wrap.
    if s <= e:
        minutter = list(range(s, e + 1))
    else:                                    # nattvindu, wrapper midnatt
        minutter = list(range(s, 24 * 60)) + list(range(0, e + 1))
    return frozenset(wd * 1440 + m for wd in dager for m in minutter)


def lokal_ukeminutt(t: datetime, sone: ZoneInfo) -> int:
    lokal = t.astimezone(sone)
    return lokal.weekday() * 1440 + lokal.hour * 60 + lokal.minute


def i_vindu(vindu: str, t: datetime, sone: ZoneInfo) -> bool:
    """Motorens medlemskapssjekk — SAMME mengde som klassifikatoren måler."""
    return lokal_ukeminutt(t, sone) in tillatte_ukeminutter(vindu)
