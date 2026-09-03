"""To ting utrullingen med VILJE ikke gjør — pinnet, ikke bare skrevet.

Runden 3/9 gikk gjennom `opp.sh` og `oppsett-postgresql.sh` for samme
figur som hadde stoppet utrullingen to ganger: en forsynende liste som
slutter tidligere enn listen som krever noe. To par var lukket samme
døgn (#360 DSN-ene, #361 rollemedlemskapene). Det som sto igjen var to
AVVIK SOM ER RIKTIGE:

  * `disponit-m57.service` er den eneste unit-fila i `deploy/staging/`
    som deployen ikke installerer. Den krever `useradd`, en lokal
    modell og et modultoken, og skal ikke enables før modulen er aktiv.
    Dokumentert i `docs/RELEASE-M57.md` §3.

  * `DISPONIT_MODULTOKEN` er den eneste `LoadCredential` uten en
    `skriv_cred`. `opp.sh` rører ALDRI tokens, og `bootstrap-token.sh`
    nekter uten TTY. Tokenet plasseres av et menneske etter
    onboarding-seremonien.

BEGGE LEVDE BARE I PROSA, og det er problemet porten løser. En
bidragsyter som ser hullet — slik jeg gjorde — kan tette det i god tro,
og for modultokenet ville det bety at DEPLOYEN HÅNDTERER ET TOKEN. Det
er en sikkerhetsregresjon forkledd som opprydning.

Porten gjør unntaket til en avgjørelse som holdes fast: alt annet må
være dekket, og listen her må være nøyaktig — et unntak som ikke lenger
gjelder, er like rødt som et som mangler.
"""
from __future__ import annotations

import re
from pathlib import Path

ROT = Path(__file__).resolve().parents[3]
D = ROT / "deploy" / "staging"
OPP = D / "opp.sh"

#: Unit-filer utrullingen med VILJE ikke installerer, og hvorfor.
UNIT_UNNTAK: dict[str, str] = {
    "disponit-m57.service":
        "krever useradd, lokal modell og modultoken; enables først når"
        " modulen er aktiv (docs/RELEASE-M57.md §3)",
}

#: `LoadCredential`-navn `opp.sh` med VILJE ikke skriver, og hvorfor.
CREDENTIAL_UNNTAK: dict[str, str] = {
    "DISPONIT_MODULTOKEN":
        "opp.sh rører aldri tokens; bootstrap-token.sh nekter uten TTY"
        " (Codex-port 7). Plasseres av et menneske etter onboarding.",
}


def _aktiv(tekst: str, merke: str = "#") -> str:
    """Utkommentert kode installerer og skriver ingenting."""
    return "\n".join(l for l in tekst.splitlines()
                     if not l.lstrip().startswith(merke))


def _installerte_units() -> set[str]:
    m = re.search(r'UNITS="([^"]+)"', _aktiv(OPP.read_text(encoding="utf-8")),
                  re.S)
    assert m, "fant ingen UNITS-liste i opp.sh"
    return set(m.group(1).split())


def _unitfiler() -> set[str]:
    return {p.name for p in D.iterdir()
            if p.name.startswith("disponit-")
            and p.suffix in (".service", ".timer", ".socket")}


def _loadcredentials() -> dict[str, str]:
    """Credential-navn → første unit som ber om det."""
    ut: dict[str, str] = {}
    for p in sorted(D.glob("disponit-*.service")):
        for m in re.finditer(r"^LoadCredential=([A-Z0-9_]+):",
                             p.read_text(encoding="utf-8"), re.M):
            ut.setdefault(m.group(1), p.name)
    return ut


def _skrevne_credentials() -> set[str]:
    return set(re.findall(r"^skriv_cred \S+ ([A-Z0-9_]+)",
                          _aktiv(OPP.read_text(encoding="utf-8")), re.M))


def test_porten_maaler_noe():
    assert len(_unitfiler()) >= 50, len(_unitfiler())
    assert len(_loadcredentials()) >= 30, len(_loadcredentials())


def test_hver_unitfil_installeres_eller_star_som_navngitt_unntak():
    """MUTASJONEN SOM DREPER DENNE: legg en ny unit-fil i katalogen uten
    å føre den i `UNITS`."""
    udekket = sorted(_unitfiler() - _installerte_units()
                     - set(UNIT_UNNTAK))
    assert udekket == [], (
        "unit-filer som verken installeres av opp.sh eller står som"
        " navngitt unntak — endringer i dem når aldri verten: "
        + ", ".join(udekket))


def test_hver_loadcredential_skrives_eller_star_som_navngitt_unntak():
    """MUTASJONEN SOM DREPER DENNE: legg en `LoadCredential` i en unit
    uten en `skriv_cred` i opp.sh — da starter ikke tjenesten."""
    krevd = _loadcredentials()
    udekket = sorted(set(krevd) - _skrevne_credentials()
                     - set(CREDENTIAL_UNNTAK))
    assert udekket == [], (
        "LoadCredential uten skriv_cred og uten navngitt unntak —"
        " tjenesten nekter å starte: "
        + ", ".join(f"{n} ({krevd[n]})" for n in udekket))


def test_unntakene_gjelder_fortsatt():
    """…og den andre veien, som er halve poenget.

    Et unntak som ikke lenger gjelder er en gammel sannhet ingen holder
    ved like — nøyaktig formen `VENTENDE` hadde da den påsto at M-11 var
    ubygget. Blir `disponit-m57.service` en dag installert av deployen,
    skal listen her tømmes i samme commit.
    """
    for navn in UNIT_UNNTAK:
        assert navn in _unitfiler(), \
            f"{navn} står som unntak, men fila finnes ikke"
        assert navn not in _installerte_units(), \
            f"{navn} INSTALLERES nå — ta den ut av UNIT_UNNTAK"
    krevd = _loadcredentials()
    for navn in CREDENTIAL_UNNTAK:
        assert navn in krevd, \
            f"{navn} står som unntak, men ingen unit ber om det"
        # …og for modultokenet er dette ikke bare opprydning: en
        # `opp.sh` som skriver et token er en sikkerhetsregresjon, og
        # da skal noen ha bestemt det — ikke oppdaget det i ettertid.
        assert navn not in _skrevne_credentials(), (
            f"{navn} SKRIVES nå av opp.sh. Er det bestemt, ta den ut av"
            " CREDENTIAL_UNNTAK i samme commit — er det ikke bestemt, er"
            " det en utrulling som håndterer en hemmelighet.")
