"""Hver sveip-DSN `opp.sh` KREVER, skal også SKRIVES av oppsettet.

DENNE PORTEN ER SVARET PÅ EN DRIFT SOM VARTE I TI MODULER.

`opp.sh` avbryter utrullingen når en sveips DSN mangler i miljøfila. Det
er riktig — det er slik en manglende rolle skal oppdages, og
`oppsett-postgresql.sh` sier det selv i en kommentar skrevet etter #324:
«en sveiperolle uten DSN i miljøfila kan ikke autentisere på en fersk
install, og da stopper deployen når modulen deres lander».

Lærdommen ble skrevet ned, og deretter ikke anvendt. Rollene ble
opprettet for hver nye sveip fra M-13 og utover; DSN-ene ble ikke
skrevet. `opp.sh` krevde fjorten, oppsettet skrev fire — og en utrulling
på en fersk maskin stoppet på den FØRSTE manglende.

Ingen port sa fra, fordi ingen port sammenlignet de to listene. Denne
gjør det, i SAMME tre, så den ikke kan bli grønn av at noen husket det
ene stedet og glemte det andre.

MUTASJONEN SOM DREPER DENNE: legg en `DISPONIT_NYSVEIP_URL`-sjekk i
`opp.sh` uten å legge DSN-en i oppsettet.
"""
from __future__ import annotations

import re
from pathlib import Path

ROT = Path(__file__).resolve().parents[3]
OPP = ROT / "deploy" / "staging" / "opp.sh"
OPPSETT = ROT / "deploy" / "staging" / "oppsett-postgresql.sh"

_URL = re.compile(r"DISPONIT_[A-Z0-9]*SVEIP_URL")


def _krevd() -> set[str]:
    """Sveip-DSN-ene `opp.sh` nekter å rulle ut uten."""
    return set(_URL.findall(OPP.read_text(encoding="utf-8")))


def _skrevet() -> set[str]:
    """…og de oppsettet FAKTISK skriver til miljøfila.

    En `*_DSN=(...)`-liste som aldri sendes til `sikre_rolle_dsn` skriver
    ingenting, og teller derfor ikke. Det er nettopp forskjellen mellom
    «rollen finnes» og «rollen kan logge inn» som var feilen.
    """
    # UTKOMMENTERT KODE SKRIVER INGENTING. En `# sikre_rolle_dsn …` ville
    # ellers holdt porten grønn på en DSN som aldri havner i miljøfila —
    # altså nøyaktig den fail-open-formen porten finnes for å hindre
    # (CodeRabbit).
    aktiv = "\n".join(
        linje for linje in OPPSETT.read_text(encoding="utf-8").splitlines()
        if not linje.lstrip().startswith("#"))
    brukt = set(re.findall(
        r'^\s*sikre_rolle_dsn\s+"\$\w+"\s+"\$\{(\w+)_DSN\[@\]\}"\s*$',
        aktiv, re.M))
    ut: set[str] = set()
    for m in re.finditer(r"^(\w+)_DSN=\((.*?)\)\s*$", aktiv,
                         re.M | re.S):
        if m.group(1) in brukt:
            ut |= set(_URL.findall(m.group(2)))
    return ut


def test_porten_maaler_noe():
    """En tom liste på begge sider ville gjort begge portene grønne."""
    assert len(_krevd()) >= 14, sorted(_krevd())
    assert len(_skrevet()) >= 14, sorted(_skrevet())


def test_hver_sveip_dsn_opp_krever_blir_ogsa_skrevet():
    mangler = sorted(_krevd() - _skrevet())
    assert mangler == [], (
        "opp.sh avbryter utrullingen uten disse, men"
        " oppsett-postgresql.sh skriver dem aldri: " + ", ".join(mangler))


def test_hver_sveip_dsn_oppsettet_skriver_blir_ogsa_krevd():
    """…og den andre veien: en DSN ingen krever er en rolle uten jobb.

    Uten denne halvdelen kunne porten holdes grønn ved å legge alle
    tenkelige DSN-er i oppsettet, og da måler den ingenting.
    """
    overflodig = sorted(_skrevet() - _krevd())
    assert overflodig == [], (
        "oppsett-postgresql.sh skriver DSN-er ingen sveip krever: "
        + ", ".join(overflodig))
