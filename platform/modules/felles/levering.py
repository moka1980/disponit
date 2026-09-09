"""Leveringsformen eiermodulene deler (ARC B, PR 4 kampanje): et svar som
uteblir, et vindu som lukkes, en kvittering som telles, og kvitteringens
retryløkke. Skrevet for m23_fordring; speilet ut hit da m44_kampanje
trengte NØYAKTIG det samme — én form, to moduler som er enige.

Dommene (hva som kvitteres) bor i hver modul; her bor bare mekanikken.
"""
from __future__ import annotations

import smtplib
from datetime import datetime, timezone

#: Statusene plattformen svarer med når kvitteringen faktisk skiftet
#: oppdragets tilstand (eller alt hadde gjort det).
STATUSSKIFTE = ("utfort", "feilet", "idempotent")

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


class Uteblitt:
    """Et svar som aldri kom: status 0, tom kropp."""
    status_code = 0

    def __init__(self, grunn: str = "intet svar"):
        self.grunn = grunn

    def json(self):
        raise ValueError(self.grunn)


def tidspunkt(raa) -> datetime | None:
    if not isinstance(raa, str):
        return None
    try:
        t = datetime.fromisoformat(raa.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


def vindu_apent(raa) -> bool:
    t = tidspunkt(raa)
    return t is None or datetime.now(timezone.utc) < t


def kvittert(rk) -> bool:
    """2xx OG en kropp som sier at oppdraget skiftet tilstand. Et 202
    «lagret uten statusendring» er evidens, ikke en avslutning."""
    if not (200 <= getattr(rk, "status_code", 0) < 300):
        return False
    try:
        kropp = rk.json()
    except Exception:                                   # noqa: BLE001
        return False
    return isinstance(kropp, dict) and kropp.get("status") in STATUSSKIFTE


def feilutfall(rk, grunn: str, **ekstra) -> dict:
    return {"utfall": "avbrutt", "grunn": grunn,
            "kvittering_status": getattr(rk, "status_code", 0), **ekstra}


def kontraktsbrudd(oppdragstype: str, payload) -> list[str]:
    """Plattformens egen feltkontrakt, målt FØR arbeidet — raden kan
    være skrevet av en eldre release, og en utfører som stoler på at
    noen andre alt har sjekket, sjekker ikke."""
    from oppdragskontrakt import bryter_feltkontrakten, mangler_paakrevde
    if not isinstance(payload, dict):
        return ["payload"]
    return sorted(set(mangler_paakrevde(oppdragstype, payload))
                  | set(bryter_feltkontrakten(oppdragstype, payload)))


def lever(klient, sti: str, kropp: dict, hode: dict, utloper, *, sov,
          forsok: int = LEVERINGSFORSOK, pause: float = LEVERINGSPAUSE_S):
    """POST med retry på 5xx og uteblitt svar, så lenge vinduet er åpent.
    `sov` er modulens egen (testene bytter den ut)."""
    rk = Uteblitt()
    for n in range(forsok):
        if n:
            if not vindu_apent(utloper):
                break
            sov(pause * n)
        try:
            rk = klient.post(sti, json=kropp, headers=hode)
        except Exception as e:                          # noqa: BLE001
            rk = Uteblitt(f"{type(e).__name__}: intet svar")
            continue
        if rk.status_code < 500:
            break
    return rk
