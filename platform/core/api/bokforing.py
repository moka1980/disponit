"""M-14s bokføringsarm (ARC B bokføring, PR 3): det claim-veien gir
bokføringsmodulen ved siden av payloaden.

Egen fil, ikke `api/faktura.py`: registerets fil sier «bokfører
ingenting og attesterer ingenting», og det står (doktrineporten måler
den). Bokføringen er plattformens arm rundt registeret — bestillingsveien
(165), utløseren (166) og denne: claim-svarets `utforelse`.

HVA MODULEN FÅR: bilaget fakturaen skal bli — nummer, retning, beløp,
motpart, datoer — lest av registeret HER, i API-ets tillit, aldri av
modulen (den har verken base eller nøkler). En `hindring` betyr at
modulen skal kvittere `feilet` uten å bokføre: fakturaen er borte,
avvist eller alt bokført siden bestillingen, en kontroll er ikke lenger
ren, eller den manuelle kontrollen 106 krever over beløpsgrensen mangler.

v1-KOBLINGEN ER HUSETS BILAGSREGISTER (M-13, 101): kvitteringen (PR 4)
registrerer bilaget der, og fakturaen blir `bokfort`. Modulen er
sømmen et regnskapssystem senere kobles på — kjeden rundt den endres
ikke av det.
"""
from __future__ import annotations

BILAGSPREFIKS = "LF-"


def utforelse_for_bokforing(conn, tenant: str, faktura_id) -> dict:
    if not faktura_id:
        return {"hindring": "faktura_ukjent"}
    rad = conn.execute("SELECT * FROM m14_for_bokforing(%s,%s)",
                       (tenant, faktura_id)).fetchone()
    if rad is None:
        return {"hindring": "faktura_ukjent"}
    (status, lev, nummer, _netto, _mva, brutto, valuta, utstedt, forfall,
     dublett_ok, mva_ok, lev_ok, over, manuell_ok, _apne) = rad
    if status == "avvist":
        return {"hindring": "faktura_avvist"}
    if status == "bokfort":
        return {"hindring": "alt_bokfort"}
    if not (dublett_ok and mva_ok and lev_ok):
        return {"hindring": "kontroll_ikke_ren"}
    if over and not manuell_ok:
        return {"hindring": "manuell_kontroll_mangler"}
    return {"faktura_id": str(faktura_id),
            "fakturanummer": nummer, "leverandor_ref": lev,
            "bilagsnummer": BILAGSPREFIKS + str(nummer).strip(),
            "retning": "ut", "belop_ore": int(brutto), "valuta": valuta,
            "motpart": lev, "utstedt": str(utstedt),
            "forfall": str(forfall) if forfall is not None else None}


__all__ = ["BILAGSPREFIKS", "utforelse_for_bokforing"]
