"""Controlleren for m17_kundeservice (ARC B kundeservice, PR 4): claim →
kontroll → bygg → send → signert kvittering. M-23/M-44-controllerens
form — mekanikken er delt (`modules.felles.levering`), dommene er
svarets.

Det payloaden aldri bærer, og claim-svaret gir i `utforelse` (163):
kundens adresse dekryptert, henvendelsens emne og det godkjente utkastet.
Modulen har verken KEK eller base. Mangler feltet, eller bærer det en
`hindring` (henvendelsen lukket eller i unntakskøen siden bestillingen,
utkastet ikke lenger godkjent, adresse mangler), kvitteres `feilet` uten
å sende.

ALDRI TO SENDINGER: `sending_avvist` bare for feil som beviselig skjedde
før serveren tok imot meldingen; alt annet er `sending_uviss` — terminalt.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from ..felles.levering import (FEIL_FOER_AKSEPT, Uteblitt, feilutfall,
                               kontraktsbrudd, kvittert, lever, vindu_apent)
from . import maler

OPPDRAGSTYPE = "kundeservice.svar.send"


def http_frist_s() -> float:
    """Budsjettet per HTTP-kall innenfor svarets 15-minutters
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
    if not isinstance(utf.get("mottaker_epost"), str) \
            or "@" not in utf["mottaker_epost"]:
        return "mottaker_mangler"
    return None


def kjor_en(klient, token: str, sender, signer) -> dict:
    """-> {"utfall": "tomt"|"utfort"|"avbrutt"|"ukvittert", ...}.

    `sender(til, emne, tekst, *, avsender_navn, svar_til) -> dict|None`
    gjør SMTP-kallet; unntakene den reiser dømmes her.
    """
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
    kid = str(payload.get("henvendelse_id") or "")
    mid = str(payload.get("utkast_id") or "")
    basis = {
        "oppdrag_id": claim.get("oppdrag_id"), "tenant": claim.get("tenant"),
        "kvittering_jti": claim.get("kvittering_jti"),
        "repair_operation_id": claim.get("repair_operation_id"),
        "owner_claim_id": claim.get("owner_claim_id"),
        "owner_generation": claim.get("owner_generation"),
        "ressurs_id": f"henvendelse:{kid}:{mid}" if kid and mid else "",
    }

    def kvitter(kropp):
        return lever(klient, "/v1/oppdrag/kvittering", signer(kropp), hode,
                     claim.get("kvittering_utloper"), sov=_sov)

    def nei(rk, grunn, **ekstra):
        # Journalen skal si HVILKET svar som ikke ble sendt.
        return feilutfall(rk, grunn, henvendelse=kid, utkast=mid, **ekstra)

    if claim.get("oppdragstype") not in (None, OPPDRAGSTYPE):
        rk = kvitter({**basis, "resultat": "feilet",
                      "feilkode": "oppdragstype_ukjent"})
        return nei(rk, "oppdragstype_ukjent")
    brudd = kontraktsbrudd(OPPDRAGSTYPE, payload)
    if brudd:
        rk = kvitter({**basis, "resultat": "feilet",
                      "feilkode": "oppdrag_ugyldig"})
        return nei(rk, f"oppdrag_ugyldig:{brudd}")
    hindring = _hindring(claim)
    if hindring:
        rk = kvitter({**basis, "resultat": "feilet", "feilkode": hindring})
        return nei(rk, hindring)
    if not vindu_apent(claim.get("utforelsesfrist")):
        rk = kvitter({**basis, "resultat": "feilet",
                      "feilkode": "frist_utilstrekkelig"})
        return nei(rk, "frist_utilstrekkelig")
    utf = dict(claim["utforelse"])
    try:
        melding = maler.bygg(utf)
    except maler.Flettefeil as e:
        rk = kvitter({**basis, "resultat": "feilet", "feilkode": "malfeil"})
        return nei(rk, f"malfeil:{e.kode}")
    try:
        sendt = sender(utf["mottaker_epost"], melding["emne"],
                       melding["tekst"],
                       avsender_navn=utf.get("avsender_navn")
                       or claim.get("tenant"),
                       svar_til=utf.get("svar_til"))
    except FEIL_FOER_AKSEPT as e:
        rk = kvitter({**basis, "resultat": "feilet",
                      "feilkode": "sending_avvist"})
        return nei(rk, "sending_avvist", feiltype=type(e).__name__)
    except Exception as e:                              # noqa: BLE001
        rk = kvitter({**basis, "resultat": "feilet",
                      "feilkode": "sending_uviss"})
        return nei(rk, "sending_uviss", feiltype=type(e).__name__)
    sendt_ts = datetime.now(timezone.utc).isoformat()
    rk = kvitter({**basis, "resultat": "utfort",
                  "sendt_ts": sendt_ts,
                  "malversjon": melding["malversjon"],
                  "henvendelse_id": kid, "utkast_id": mid,
                  "mottaker_maske": utf.get("mottaker_maske") or "",
                  "melding_id": (sendt or {}).get("melding_id") or ""})
    svar = {"kvittering_status": rk.status_code, "henvendelse": kid,
            "utkast": mid}
    if not kvittert(rk):
        return {"utfall": "ukvittert", **svar}
    return {"utfall": "utfort", **svar}


__all__ = ["OPPDRAGSTYPE", "Uteblitt", "http_frist_s", "kjor_en"]
