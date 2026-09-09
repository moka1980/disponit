"""Controlleren for m23_fordring (ARC B, PR 4): claim → kontroll → flett
→ send → signert kvittering. m56-formen, speilet — avviket er at
ARBEIDET er en e-post, og en e-post er irreversibel.

Derfor to ting m56 ikke har:
  * `utforelse` i claim-svaret: adressen og tallene plattformen
    dekrypterte for oss (149). Modulen har verken KEK eller base, og
    payloaden bærer aldri adressen. Mangler feltet, eller bærer det en
    `hindring`, kvitteres `feilet` uten å sende.
  * ALDRI TO SENDINGER. Feil som beviselig skjedde FØR serveren tok
    imot meldingen (`FEIL_FOER_AKSEPT`) kvitteres `sending_avvist`. Alt
    annet som går galt under sendingen — timeout midt i dialogen, brutt
    forbindelse etter DATA — kvitteres `sending_uviss`: TERMINALT, så
    ingen ny claim kan sende den samme purringen én gang til. «Kan alt
    ha gått ut» er et menneskes dom (M-57-utsenderens lærdom), aldri en
    retry.

Alt som kan hindre en gyldig sending måles FØR e-posten går: kontrakt,
hindring, frist, fletting. Hvert utfall er et KODET ord til
plattformen, aldri taushet.
"""
from __future__ import annotations

import smtplib
import time
from datetime import datetime, timezone

from . import maler

OPPDRAGSTYPE = "purring.send"

#: Statusene plattformen svarer med når kvitteringen faktisk skiftet
#: oppdragets tilstand (eller alt hadde gjort det).
_STATUSSKIFTE = ("utfort", "feilet", "idempotent")

LEVERINGSFORSOK = 4
LEVERINGSPAUSE_S = 2.0

#: SMTP-feil som reises FØR serveren har tatt imot meldingen. Bare disse
#: er trygge å kalle «ikke sendt».
FEIL_FOER_AKSEPT = (smtplib.SMTPRecipientsRefused,
                    smtplib.SMTPSenderRefused,
                    smtplib.SMTPAuthenticationError,
                    smtplib.SMTPHeloError,
                    smtplib.SMTPConnectError,
                    ConnectionRefusedError)


def http_frist_s() -> float:
    """Budsjettet per HTTP-kall: kvitteringen prøves LEVERINGSFORSOK
    ganger innenfor purringens 15-minutters utførelsesfrist."""
    return 30.0


def _sov(sekunder: float) -> None:
    if sekunder > 0:
        time.sleep(sekunder)


class _Uteblitt:
    """Et svar som aldri kom: status 0, tom kropp."""
    status_code = 0

    def __init__(self, grunn: str = "intet svar"):
        self.grunn = grunn

    def json(self):
        raise ValueError(self.grunn)


def _tidspunkt(raa) -> datetime | None:
    if not isinstance(raa, str):
        return None
    try:
        t = datetime.fromisoformat(raa.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


def _vindu_apent(raa) -> bool:
    t = _tidspunkt(raa)
    return t is None or datetime.now(timezone.utc) < t


def _kvittert(rk) -> bool:
    """2xx OG en kropp som sier at oppdraget skiftet tilstand. Et 202
    «lagret uten statusendring» er evidens, ikke en avslutning."""
    if not (200 <= getattr(rk, "status_code", 0) < 300):
        return False
    try:
        kropp = rk.json()
    except Exception:                                   # noqa: BLE001
        return False
    return isinstance(kropp, dict) and kropp.get("status") in _STATUSSKIFTE


def _feilutfall(rk, grunn: str, **ekstra) -> dict:
    return {"utfall": "avbrutt", "grunn": grunn,
            "kvittering_status": getattr(rk, "status_code", 0), **ekstra}


def _kontraktsbrudd(payload: dict) -> list[str]:
    """Plattformens egen feltkontrakt, målt FØR sendingen — raden kan
    være skrevet av en eldre release, og en utfører som stoler på at
    noen andre alt har sjekket, sjekker ikke."""
    from oppdragskontrakt import bryter_feltkontrakten, mangler_paakrevde
    if not isinstance(payload, dict):
        return ["payload"]
    return sorted(set(mangler_paakrevde(OPPDRAGSTYPE, payload))
                  | set(bryter_feltkontrakten(OPPDRAGSTYPE, payload)))


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
    fid = str(payload.get("fordring_id") or "") if isinstance(payload,
                                                              dict) else ""
    # KONVOLUTTEN LESES DEFENSIVT (CodeRabbit på PR 4): et fremmed
    # claim-svar skal nå den kodede `oppdragstype_ukjent`-kvitteringen,
    # ikke dø i en KeyError før modulen har sagt ett ord.
    basis = {
        "oppdrag_id": claim.get("oppdrag_id"), "tenant": claim.get("tenant"),
        "kvittering_jti": claim.get("kvittering_jti"),
        "repair_operation_id": claim.get("repair_operation_id"),
        "owner_claim_id": claim.get("owner_claim_id"),
        "owner_generation": claim.get("owner_generation"),
        "ressurs_id": f"fordring:{fid}" if fid else "",
    }

    def lever(sti, kropp, utloper):
        rk = _Uteblitt()
        for forsok in range(LEVERINGSFORSOK):
            if forsok:
                if not _vindu_apent(utloper):
                    break
                _sov(LEVERINGSPAUSE_S * forsok)
            try:
                rk = klient.post(sti, json=kropp, headers=hode)
            except Exception as e:                      # noqa: BLE001
                rk = _Uteblitt(f"{type(e).__name__}: intet svar")
                continue
            if rk.status_code < 500:
                break
        return rk

    def kvitter(kropp):
        return lever("/v1/oppdrag/kvittering", signer(kropp),
                     claim.get("kvittering_utloper"))

    if claim.get("oppdragstype") not in (None, OPPDRAGSTYPE):
        rk = kvitter({**basis, "resultat": "feilet",
                      "feilkode": "oppdragstype_ukjent"})
        return _feilutfall(rk, "oppdragstype_ukjent")
    brudd = _kontraktsbrudd(payload)
    if not brudd:
        try:
            trinn = int(payload["trinn"])
            if isinstance(payload["trinn"], bool):
                raise TypeError("bool")
        except (KeyError, TypeError, ValueError):
            brudd = ["trinn"]
    if brudd:
        rk = kvitter({**basis, "resultat": "feilet",
                      "feilkode": "oppdrag_ugyldig"})
        return _feilutfall(rk, f"oppdrag_ugyldig:{brudd}")
    hindring = _hindring(claim)
    if hindring:
        rk = kvitter({**basis, "resultat": "feilet", "feilkode": hindring})
        return _feilutfall(rk, hindring)
    if not _vindu_apent(claim.get("utforelsesfrist")):
        rk = kvitter({**basis, "resultat": "feilet",
                      "feilkode": "frist_utilstrekkelig"})
        return _feilutfall(rk, "frist_utilstrekkelig")
    utf = dict(claim["utforelse"])
    utf.setdefault("tenant", claim["tenant"])
    # Trinnet som sendes er OPPDRAGETS (døra valgte det ved bestillingen).
    # Har fordringen flyttet seg siden, er sendingen ikke lenger den
    # bestilte: ingen e-post, et kodet nei.
    if utf.get("neste_trinn") is not None \
            and int(utf["neste_trinn"]) != trinn:
        rk = kvitter({**basis, "resultat": "feilet",
                      "feilkode": "trinn_flyttet"})
        return _feilutfall(rk, "trinn_flyttet")
    try:
        melding = maler.flett(payload["handling_trinn"],
                              maler.felter_fra(utf))
    except maler.Flettefeil as e:
        rk = kvitter({**basis, "resultat": "feilet", "feilkode": "malfeil"})
        return _feilutfall(rk, f"malfeil:{e.kode}")
    try:
        sendt = sender(utf["mottaker_epost"], melding["emne"],
                       melding["tekst"],
                       avsender_navn=utf.get("avsender_navn")
                       or claim["tenant"],
                       svar_til=utf.get("svar_til"))
    except FEIL_FOER_AKSEPT as e:
        rk = kvitter({**basis, "resultat": "feilet",
                      "feilkode": "sending_avvist"})
        return _feilutfall(rk, "sending_avvist", feiltype=type(e).__name__)
    except Exception as e:                              # noqa: BLE001
        # UVISST — og derfor TERMINALT: kvitteringen stenger oppdraget
        # så ingen ny claim sender purringen én gang til.
        rk = kvitter({**basis, "resultat": "feilet",
                      "feilkode": "sending_uviss"})
        return _feilutfall(rk, "sending_uviss", feiltype=type(e).__name__)
    sendt_ts = datetime.now(timezone.utc).isoformat()
    rk = kvitter({**basis, "resultat": "utfort",
                  "sendt_ts": sendt_ts,
                  "malversjon": melding["malversjon"],
                  "handling_trinn": payload["handling_trinn"],
                  "trinn": trinn,
                  "mottaker_maske": utf.get("mottaker_maske") or "",
                  "melding_id": (sendt or {}).get("melding_id") or ""})
    svar = {"kvittering_status": rk.status_code, "trinn": trinn,
            "handling_trinn": payload["handling_trinn"]}
    if not _kvittert(rk):
        return {"utfall": "ukvittert", **svar}
    return {"utfall": "utfort", **svar}
