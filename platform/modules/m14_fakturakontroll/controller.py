"""Controlleren for m14_fakturakontroll (ARC B bokføring, PR 3): claim →
kontroll → bygg bilaget → signert kvittering. M-23/M-44/M-17-
controllerens form — mekanikken er delt (`modules.felles.levering`),
dommene er bilagets.

Det payloaden bærer er referansen og tallene (165); det claim-svaret
gir i `utforelse` er bilaget fakturaen skal bli, lest av registeret i
API-ets tillit, og tilstanden spurt en gang til. Modulen har verken base
eller nøkler. Mangler feltet, eller bærer det en `hindring` (fakturaen
avvist eller alt bokført siden bestillingen, en kontroll ikke lenger
ren, manuell kontroll mangler), kvitteres `feilet` uten å bokføre.

INGEN EKSTERN SENDING i v1: koblingen er husets bilagsregister (M-13),
og bokføringen skjer når kvitteringen når registeret (PR 4). Modulen er
sømmen et regnskapssystem senere kobles på; da får `bokfor` et kall ut,
og kvitteringen bærer det systemets referanse i tillegg.

ALDRI TO BILAG: kvitteringen er idempotent på oppdraget (037), og
registeret bokfører én gang per faktura (PR 4).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from ..felles.levering import (feilutfall, kontraktsbrudd, kvittert, lever,
                               vindu_apent)
from . import bilag as bilagsform

#: ÉN oppdragstype for begge bokføringshandlingene: registeret nekter
#: to typer der den ene er strengprefiks av den andre, og arbeidet er det
#: samme. Handlingen (liten/stor) står på oppdraget, ikke i typen.
OPPDRAGSTYPER = ("faktura.bokfor",)


def http_frist_s() -> float:
    """Budsjettet per HTTP-kall innenfor bokføringens 15-minutters
    utførelsesfrist."""
    return 30.0


def _sov(sekunder: float) -> None:
    if sekunder > 0:
        time.sleep(sekunder)


def _hindring(claim: dict) -> str | None:
    utf = claim.get("utforelse")
    if not isinstance(utf, dict):
        return "utforelse_mangler"
    if utf.get("hindring"):
        return str(utf["hindring"])
    return None


def _samsvar(payload: dict, utf: dict) -> str | None:
    """Bilaget må være OPPDRAGETS faktura: samme referanse, samme brutto.
    Et claim-svar som beskriver en annen faktura enn payloaden er en
    plattformfeil, ikke et bilag."""
    if str(utf.get("faktura_id") or "") != str(payload.get("faktura_id")):
        return "utforelse_feil_faktura"
    if int(utf.get("belop_ore") or 0) != int(payload.get("brutto_ore") or -1):
        return "utforelse_feil_belop"
    return None


def kjor_en(klient, token: str, signer) -> dict:
    """-> {"utfall": "tomt"|"utfort"|"avbrutt"|"ukvittert", ...}."""
    hode = {"authorization": f"Bearer {token}"}
    r = klient.post("/v1/oppdrag/claim", json={}, headers=hode)
    if r.status_code == 204:
        return {"utfall": "tomt"}
    r.raise_for_status()
    claim = r.json()
    if not isinstance(claim, dict):
        return {"utfall": "avbrutt", "grunn": "claim_uleselig",
                "kvittering_status": r.status_code}
    payload = claim.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    fid = str(payload.get("faktura_id") or "")
    otype = claim.get("oppdragstype")
    basis = {
        "oppdrag_id": claim.get("oppdrag_id"), "tenant": claim.get("tenant"),
        "kvittering_jti": claim.get("kvittering_jti"),
        "repair_operation_id": claim.get("repair_operation_id"),
        "owner_claim_id": claim.get("owner_claim_id"),
        "owner_generation": claim.get("owner_generation"),
        "ressurs_id": f"faktura:{fid}" if fid else "",
    }

    def kvitter(kropp):
        return lever(klient, "/v1/oppdrag/kvittering", signer(kropp), hode,
                     claim.get("kvittering_utloper"), sov=_sov)

    def nei(rk, grunn, **ekstra):
        return feilutfall(rk, grunn, faktura=fid, **ekstra)

    if otype not in (None, *OPPDRAGSTYPER):
        rk = kvitter({**basis, "resultat": "feilet",
                      "feilkode": "oppdragstype_ukjent"})
        return nei(rk, "oppdragstype_ukjent")
    brudd = kontraktsbrudd(otype or OPPDRAGSTYPER[0], payload)
    if brudd:
        rk = kvitter({**basis, "resultat": "feilet",
                      "feilkode": "oppdrag_ugyldig"})
        return nei(rk, f"oppdrag_ugyldig:{brudd}")
    hindring = _hindring(claim)
    if hindring:
        rk = kvitter({**basis, "resultat": "feilet", "feilkode": hindring})
        return nei(rk, hindring)
    utf = dict(claim["utforelse"])
    avvik = _samsvar(payload, utf)
    if avvik:
        rk = kvitter({**basis, "resultat": "feilet", "feilkode": avvik})
        return nei(rk, avvik)
    if not vindu_apent(claim.get("utforelsesfrist")):
        rk = kvitter({**basis, "resultat": "feilet",
                      "feilkode": "frist_utilstrekkelig"})
        return nei(rk, "frist_utilstrekkelig")
    try:
        bilag = bilagsform.bygg(utf)
    except bilagsform.Bilagsfeil as e:
        rk = kvitter({**basis, "resultat": "feilet", "feilkode": "bilagfeil"})
        return nei(rk, f"bilagfeil:{e.kode}")
    bokfort_ts = datetime.now(timezone.utc).isoformat()
    rk = kvitter({**basis, "resultat": "utfort",
                  "bokfort_ts": bokfort_ts,
                  "malversjon": bilag["malversjon"],
                  "faktura_id": fid,
                  "bilagsnummer": bilag["bilagsnummer"],
                  "retning": bilag["retning"],
                  "belop_ore": bilag["belop_ore"],
                  "motpart": bilag["motpart"],
                  "utstedt": bilag["utstedt"],
                  "forfall": bilag["forfall"]})
    svar = {"kvittering_status": rk.status_code, "faktura": fid,
            "bilagsnummer": bilag["bilagsnummer"]}
    if not kvittert(rk):
        return {"utfall": "ukvittert", **svar}
    return {"utfall": "utfort", **svar}


__all__ = ["OPPDRAGSTYPER", "http_frist_s", "kjor_en"]
