#!/usr/bin/env python3
"""Driftsvarsel: e-post til eier når en disponit-tjeneste står som feilet.

BAKGRUNNEN (20.11.2026). `systemctl --failed` var det eneste stedet en feil
ble synlig, og ingen så dit: prod var nede i fire uker i oktober
(tzdata-porten), og arbeiderne var døde i en måned (utløpte modultokener).
Arbeidervakten og rotasjonstimeren gjør slike feil til en FEILET UNIT; dette
skriptet gjør en feilet unit til en e-post.

Kjøres av `disponit-driftsvarsel.timer` hvert femte minutt. Tre meldinger:
  * NY FEIL       — en unit er feilet som ikke var det sist.
  * PÅMINNELSE    — noe har stått feilet i et døgn siden forrige melding.
  * FRISKMELDING  — ingenting er feilet lenger.
Ellers er den stille. Tilstanden (hva det sist ble varslet om) ligger i
`$STATE_DIRECTORY/tilstand.json` og skrives FØRST NÅR e-posten er tatt imot:
en sending som feilet, prøves igjen ved neste kjøring.

Mottakeren står på VERTEN (`DISPONIT_DRIFTSVARSEL_TIL`), aldri i repoet.
SMTP-oppsettet er varselsenderens (`/etc/disponit/varsel/smtp.env`).

Exit: 0 = målt (og eventuelt varslet), 1 = sendingen feilet, 2 = vakten er
blind (mangler mottaker, SMTP-oppsett eller svar fra systemd).
"""
from __future__ import annotations

import json
import os
import smtplib
import socket
import ssl
import subprocess
import sys
import time
from email.message import EmailMessage
from pathlib import Path

PAAMINN_S = int(os.environ.get("DISPONIT_DRIFTSVARSEL_PAAMINN_S", "86400"))
SELV = "disponit-driftsvarsel.service"


#: Sekunder mellom de to prøvene i `feilede_units`.
PROVEPAUSE_S = int(os.environ.get("DISPONIT_DRIFTSVARSEL_PROVEPAUSE_S", "25"))


def _en_prove() -> set[str] | None:
    try:
        r = subprocess.run(
            ["systemctl", "list-units", "disponit-*", "--state=failed",
             "--no-legend", "--plain", "--no-pager"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return {linje.split()[0] for linje in r.stdout.splitlines()
            if linje.split()}


def feilede_units(*, prove=_en_prove, sov=time.sleep,
                  pause: int | None = None) -> list[str] | None:
    """Feilede disponit-units, eller None når systemd ikke svarte.

    TO PRØVER, IKKE ÉN (funnet 25.11.2026). En tjeneste som startes av en
    timer og feiler, står som `failed` MELLOM kjøringene og som `activating`
    mens den prøver igjen. systemd fyrer timere som ligger nær hverandre i
    samme øyeblikk, så varselet og planarbeideren startet i samme sekund —
    og varselet så «ingen feil» hver eneste gang i tre og en halv time mens
    planarbeideren feilet hvert femte minutt. Den andre prøven tas etter at
    en slik kjøring har rukket å feile; en unit som er feilet i ÉN av de to,
    er feilet.
    """
    forste = prove()
    if forste is None:
        return None
    sov(PROVEPAUSE_S if pause is None else pause)
    andre = prove()
    ut = forste | (andre or set())
    # Oss selv kan vi ikke varsle om: er vi feilet, kjører vi ikke.
    ut.discard(SELV)
    return sorted(ut)


def avgjor(forrige: dict, feilede: list[str], naa: float) -> str | None:
    """Hvilken melding som skal ut, eller None. Ren funksjon av tilstanden."""
    kjente = set(forrige.get("feilede", []))
    if not feilede:
        return "frisk" if kjente else None
    if set(feilede) - kjente:
        return "ny"
    if naa - float(forrige.get("sist_sendt", 0)) >= PAAMINN_S:
        return "paaminnelse"
    return None


def siste_linjer(unit: str, n: int = 6) -> str:
    try:
        r = subprocess.run(
            ["journalctl", "-u", unit, "-n", str(n), "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=30)
        return r.stdout.strip()[-1500:]
    except (OSError, subprocess.TimeoutExpired):
        return ""


def bygg(melding: str, feilede: list[str], vert: str,
         detaljer=siste_linjer) -> tuple[str, str]:
    if melding == "frisk":
        return (f"[Disponit] Alt i orden igjen på {vert}",
                "Ingen disponit-tjenester står som feilet lenger.\n")
    hode = {"ny": "FEIL", "paaminnelse": "FORTSATT FEIL"}[melding]
    emne = f"[Disponit] {hode}: {len(feilede)} tjeneste(r) på {vert}"
    deler = [f"Disse tjenestene står som feilet på {vert}:", ""]
    for u in feilede:
        deler.append(f"- {u}")
        logg = detaljer(u)
        if logg:
            deler += ["    " + linje for linje in logg.splitlines()]
        deler.append("")
    deler += ["Se selv:  systemctl --failed   og   journalctl -u <tjeneste> -n 50",
              "Du får en påminnelse én gang i døgnet til feilen er borte,"
              " og en melding når alt er i orden igjen.", ""]
    return emne, "\n".join(deler)


def smtp_oppsett() -> dict | None:
    n = {k: os.environ.get(f"DISPONIT_SMTP_{k.upper()}")
         for k in ("vert", "port", "bruker", "passord", "avsender")}
    if not all(n.values()):
        return None
    n["port"] = int(n["port"])
    return n


def send_ekte(oppsett: dict, til: str, emne: str, tekst: str) -> None:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = oppsett["avsender"], til, emne
    m.set_content(tekst)
    with smtplib.SMTP(oppsett["vert"], oppsett["port"], timeout=20) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(oppsett["bruker"], oppsett["passord"])
        s.send_message(m)


def kjor(tilstandsfil: Path, *, feilede=feilede_units, send=None,
         til: str | None = None, naa=time.time, vert: str | None = None,
         detaljer=siste_linjer) -> int:
    til = til if til is not None else os.environ.get(
        "DISPONIT_DRIFTSVARSEL_TIL", "").strip()
    if not til:
        print("driftsvarsel: BLIND — DISPONIT_DRIFTSVARSEL_TIL er ikke satt")
        return 2
    if send is None:
        oppsett = smtp_oppsett()
        if oppsett is None:
            print("driftsvarsel: BLIND — SMTP-oppsettet mangler")
            return 2
        send = lambda t, e, x: send_ekte(oppsett, t, e, x)  # noqa: E731
    liste = feilede()
    if liste is None:
        print("driftsvarsel: BLIND — systemd svarte ikke")
        return 2
    try:
        forrige = json.loads(tilstandsfil.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        forrige = {}
    t = naa()
    melding = avgjor(forrige, liste, t)
    if melding is None:
        # Units som er blitt friske mens andre står, glemmes — feiler de
        # igjen, er det en NY feil og skal varsles på nytt.
        if set(forrige.get("feilede", [])) != set(liste):
            forrige["feilede"] = liste
            tilstandsfil.write_text(json.dumps(forrige), encoding="utf-8")
        print(f"driftsvarsel: feilet={len(liste)} sendt=ingen")
        return 0
    emne, tekst = bygg(melding, liste, vert or socket.gethostname(), detaljer)
    try:
        send(til, emne, tekst)
    except Exception as f:  # noqa: BLE001 — enhver sendefeil prøves igjen
        print(f"driftsvarsel: feilet={len(liste)} SENDING FEILET"
              f" ({type(f).__name__})")
        return 1
    tilstandsfil.write_text(
        json.dumps({"feilede": liste, "sist_sendt": t}), encoding="utf-8")
    print(f"driftsvarsel: feilet={len(liste)} sendt={melding}")
    return 0


if __name__ == "__main__":
    kat = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/disponit-driftsvarsel"))
    sys.exit(kjor(kat / "tilstand.json"))
