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


def bokfor_faktura_bokfort(conn, tenant: str, oppdrag_id: int,
                           kvittering: dict, aktor: str) -> dict:
    """Kvitteringens vei tilbake til registeret (167). -> {bokfort,
    bilag_registrert, bilag_id} eller {avvik: <grunn>} — aldri et unntak
    ut: kalleren har alt en signert kvittering å stå inne for.

    RESSURSEN MÅ VÆRE OPPDRAGETS: kvitteringen er signert av modulen, men
    modulen kunne navngi en annen faktura enn den oppdraget gjaldt.
    Payloaden dekrypteres her, som ved claim, og fakturaen sammenlignes
    FØR noe bokføres. Bilagets tall sammenlignes med fakturaens i døra.
    """
    import uuid as uuidlib
    from datetime import date, datetime

    import psycopg

    ressurs = str(kvittering.get("ressurs_id") or "")
    if not ressurs.startswith("faktura:"):
        return {"avvik": "ressurs_id_ikke_faktura"}
    try:
        fid = uuidlib.UUID(ressurs.split(":", 1)[1])
    except ValueError:
        return {"avvik": "kvittering_uten_gyldig_ressurs"}
    from db import kryptering
    orad = conn.execute(
        "SELECT payload_kryptert, key_id, nonce FROM oppdrag"
        " WHERE tenant=%s AND id=%s", (tenant, int(oppdrag_id))).fetchone()
    if orad is None:
        return {"avvik": "oppdrag_ukjent"}
    nok = conn.execute(
        "SELECT wrapped_dek FROM tenant_nokler WHERE tenant=%s"
        " AND key_id=%s", (tenant, orad[1])).fetchone()
    try:
        dek = kryptering._pakk_ut((orad[1], nok[0]), tenant)[1]
        payload = kryptering.dekrypter(dek, bytes(orad[0]), bytes(orad[2]),
                                       tenant, orad[1])
    except Exception:                                   # noqa: BLE001
        return {"avvik": "oppdrag_uleselig"}
    if str(payload.get("faktura_id") or "") != str(fid):
        return {"avvik": "ressurs_avvik"}
    nummer = str(kvittering.get("bilagsnummer") or "").strip()[:100]
    if not nummer:
        return {"avvik": "kvittering_uten_bilagsnummer"}
    belop = kvittering.get("belop_ore")
    if isinstance(belop, bool) or not isinstance(belop, int):
        return {"avvik": "kvittering_uten_belop"}

    def _dato(raa):
        if raa is None:
            return None
        try:
            return date.fromisoformat(str(raa))
        except ValueError:
            return "ugyldig"
    utstedt, forfall = _dato(kvittering.get("utstedt")), \
        _dato(kvittering.get("forfall"))
    if utstedt in (None, "ugyldig") or forfall == "ugyldig":
        return {"avvik": "kvittering_uten_gyldige_datoer"}
    raa_ts = kvittering.get("bokfort_ts")
    try:
        bokfort_ts = (datetime.fromisoformat(str(raa_ts).replace("Z", "+00:00"))
                      if raa_ts else None)
        if bokfort_ts is not None and bokfort_ts.tzinfo is None:
            bokfort_ts = None
    except ValueError:
        bokfort_ts = None
    try:
        with conn.transaction():
            rad = conn.execute(
                "SELECT bokfort, bilag_registrert, bilag_id"
                " FROM m14_faktura_bokfort(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "%s)",
                (tenant, fid, int(oppdrag_id), nummer,
                 str(kvittering.get("retning") or ""), belop,
                 str(kvittering.get("motpart") or "")[:300], utstedt, forfall,
                 bokfort_ts,
                 str(kvittering.get("malversjon") or "")[:64] or None,
                 aktor)).fetchone()
    except psycopg.Error as e:
        return {"avvik": f"dor_nektet:{type(e).__name__}"}
    if rad is None:
        return {"avvik": "dor_uten_svar"}
    return {"bokfort": bool(rad[0]), "bilag_registrert": bool(rad[1]),
            "bilag_id": str(rad[2]) if rad[2] else None}


__all__ = ["BILAGSPREFIKS", "bokfor_faktura_bokfort",
           "utforelse_for_bokforing"]
