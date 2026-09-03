"""`SET ROLE` krever MEDLEMSKAP — ikke bare at rollen finnes.

DENNE PORTEN ER SVARET PÅ EN NEDETID. 3/9 stoppet utrullingen midt i
migrasjonssettet på

    permission denied to set role "disponit_faktura_eier"

Rollen fantes; `disponit_migrator` var bare ikke MEDLEM av den. Løkka i
`oppsett-postgresql.sh` oppretter hver ny eierrolle, `ci.yml` hadde
medlemskapene, og verten hadde dem ikke — så CI var grønn på alle fem
modulene i klyngen mens verten aldri kunne kjøre én av dem.

DET KOSTET MER ENN EN RØD DEPLOY. Basen hadde alt flyttet seg forbi
forrige release (101–105 var kjørt), og da nekter selv-reverseringen med
vilje: en gammel arbeider mot et nytt skjema er verre enn en stoppet
arbeider. Enhetene ble stående stoppet til noen rullet FRAMOVER.

Porten måler nedenfra, fra det som faktisk feiler: hver rolle en
MIGRASJON bytter til, må være en rolle migrator er medlem av i
oppsettskriptet. Den er derfor rød i det øyeblikket en ny modul skriver
sin første `SET LOCAL ROLE` uten å legge medlemskapet inn — altså før
merge, ikke under utrulling.
"""
from __future__ import annotations

import re
from pathlib import Path

ROT = Path(__file__).resolve().parents[3]
OPPSETT = ROT / "deploy" / "staging" / "oppsett-postgresql.sh"
CI = ROT / ".github" / "workflows" / "ci.yml"
MIGRASJONER = ROT / "platform" / "core" / "db" / "migrations"

#: Rollen migrasjonene KJØRES som. Den trenger ikke medlemskap i seg
#: selv, og en `SET ROLE disponit_migrator` er dessuten en tilbakestilling.
KJORER = "disponit_migrator"


def _uten_kommentar(tekst: str, merke: str = "--") -> str:
    """En `SET LOCAL ROLE` i en KOMMENTAR bytter ingen rolle.

    Uten dette ville porten krevd medlemskap i «der», «feiler» og «og» —
    ord som står etter «SET LOCAL ROLE» i forklarende prosa.
    """
    return "\n".join(l for l in tekst.splitlines()
                     if not l.lstrip().startswith(merke))


def _byttet_til() -> dict[str, str]:
    """Rolle → første migrasjon som bytter til den."""
    ut: dict[str, str] = {}
    for p in sorted(MIGRASJONER.glob("*.sql")):
        tekst = _uten_kommentar(p.read_text(encoding="utf-8"))
        for m in re.finditer(
                r"^\s*SET\s+(?:LOCAL\s+)?ROLE\s+([a-z0-9_]+)\s*;",
                tekst, re.I | re.M):
            ut.setdefault(m.group(1), p.name)
    ut.pop(KJORER, None)
    return ut


def _shellvariabler(tekst: str) -> dict[str, str]:
    return dict(re.findall(r"^([A-Z0-9_]+)=(disponit[a-z0-9_]*)", tekst,
                           re.M))


def _medlemskap_pa_verten() -> set[str]:
    """Rollene oppsettskriptet gjør migrator til MEDLEM av.

    Utkommentert kode gir ingen rettighet, og teller derfor ikke.
    `WITH INHERIT FALSE` er IKKE et krav her: `disponit_authenticator`
    gis uten, og det er `SET ROLE`-retten porten handler om.
    """
    tekst = OPPSETT.read_text(encoding="utf-8")
    var = _shellvariabler(tekst)
    aktiv = _uten_kommentar(tekst, "#")
    return {var.get(m, m) for m in
            re.findall(r"GRANT \$(\w+) TO \$MIGRATOR\b", aktiv)}


def _medlemskap_i_ci() -> set[str]:
    return set(re.findall(r"GRANT (disponit\w+) TO disponit_migrator\b",
                          _uten_kommentar(CI.read_text(encoding="utf-8"),
                                          "#")))


def test_porten_maaler_noe():
    """To tomme mengder ville gjort begge portene under grønne."""
    assert len(_byttet_til()) >= 20, sorted(_byttet_til())
    assert len(_medlemskap_pa_verten()) >= 20, \
        sorted(_medlemskap_pa_verten())


def test_hver_rolle_en_migrasjon_bytter_til_er_gitt_migrator():
    """PORTEN SOM MÅLER DET SOM FAKTISK FEILER."""
    gitt = _medlemskap_pa_verten()
    mangler = {r: fil for r, fil in _byttet_til().items()
               if r not in gitt}
    assert mangler == {}, (
        "migrasjonen gjør SET ROLE til en rolle migrator ikke er medlem"
        " av — utrullingen stopper på «permission denied to set role»,"
        " og basen står da mellom to releaser: "
        + ", ".join(f"{r} ({fil})" for r, fil in sorted(mangler.items())))


def test_ci_og_verten_gir_migrator_de_samme_rollene():
    """…og de to oppsettene skal ikke drifte fra hverandre.

    Det var nettopp denne driften som gjorde nedetiden usynlig i CI:
    `ci.yml` hadde de fem medlemskapene, verten hadde dem ikke, og
    ingenting sammenlignet listene.
    """
    vert, ci = _medlemskap_pa_verten(), _medlemskap_i_ci()
    assert sorted(ci - vert) == [], (
        "ci.yml gir migrator medlemskap verten ikke gir — CI blir grønn"
        " på noe verten ikke kan kjøre: " + ", ".join(sorted(ci - vert)))
    assert sorted(vert - ci) == [], (
        "verten gir migrator medlemskap ci.yml ikke gir — da måler CI"
        " noe annet enn det som rulles ut: "
        + ", ".join(sorted(vert - ci)))
