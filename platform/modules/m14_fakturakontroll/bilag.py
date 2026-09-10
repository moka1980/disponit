"""Bilagets form (ARC B bokføring, PR 3). Tallene er REGISTERETS — de
kommer i claim-svarets `utforelse` (165, lest i API-ets tillit) og
skrives inn i kvitteringen slik de står. Modulen regner ikke om, runder
ikke av og finner ikke på et nummer: et bilag er en avskrift av
fakturaen, og hver forskjell mellom dem ville vært en feil ingen kunne
forklare.
"""
from __future__ import annotations

from datetime import date

MALVERSJON = "bilag-v1"
RETNINGER = ("inn", "ut")


class Bilagsfeil(Exception):
    def __init__(self, kode: str):
        super().__init__(kode)
        self.kode = kode


def _dato(verdi, felt: str, *, valgfri: bool = False):
    if verdi is None:
        if valgfri:
            return None
        raise Bilagsfeil(f"felt_mangler:{felt}")
    try:
        return date.fromisoformat(str(verdi)).isoformat()
    except ValueError:
        raise Bilagsfeil(f"felt_ugyldig:{felt}") from None


def bygg(utforelse: dict) -> dict:
    """-> {malversjon, bilagsnummer, retning, belop_ore, motpart, utstedt,
    forfall}. Et manglende eller ugyldig felt er en Bilagsfeil — aldri
    et halvt bilag."""
    nummer = utforelse.get("bilagsnummer")
    if not isinstance(nummer, str) or not nummer.strip():
        raise Bilagsfeil("felt_mangler:bilagsnummer")
    retning = utforelse.get("retning")
    if retning not in RETNINGER:
        raise Bilagsfeil("felt_ugyldig:retning")
    belop = utforelse.get("belop_ore")
    if isinstance(belop, bool) or not isinstance(belop, int) or belop <= 0:
        raise Bilagsfeil("felt_ugyldig:belop_ore")
    motpart = utforelse.get("motpart")
    if not isinstance(motpart, str) or not motpart.strip():
        raise Bilagsfeil("felt_mangler:motpart")
    utstedt = _dato(utforelse.get("utstedt"), "utstedt")
    forfall = _dato(utforelse.get("forfall"), "forfall", valgfri=True)
    if forfall is not None and forfall < utstedt:
        raise Bilagsfeil("felt_ugyldig:forfall")
    return {"malversjon": MALVERSJON, "bilagsnummer": nummer.strip(),
            "retning": retning, "belop_ore": belop,
            "motpart": motpart.strip(), "utstedt": utstedt,
            "forfall": forfall}
