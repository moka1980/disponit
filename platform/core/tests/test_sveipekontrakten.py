"""KONTRAKTEN ALLE SVEIPENE DELER, målt på alle ni samtidig.

Hver nattlig sveip i plattformen har samme form: ta en advisory-lås, kall
ÉN dør som returnerer NØYAKTIG ÉN rad, valider raden, commit, rapporter.
Formen er kopiert fra sveip til sveip, og det er nettopp derfor den
trenger én port som måler alle — en form som kopieres, kopieres også når
den er gal.

DEN ENE REGELEN SOM MÅLES HER ER REKKEFØLGEN: kontrakten valideres FØR
commit. De seks eldste sveipene committet FØRST og oppdaget så at raden
manglet — altså en transaksjon som ble stående mens kjøringen rapporterte
feilet. For en funnskrivende jobb betyr det at funnene fra en kjøring
ingen stolte på likevel ble stående i registeret, mens journalen sa at
kjøringen var mislykket. De to utsagnene kan ikke begge være sanne.

DEN ANDRE ER `fetchall()` FRAMFOR `fetchone()`: ingen rad og FLERE rader
er den samme feilen fra hver sin kant, og `fetchone()` tidde om den
andre.

PORTEN KREVER AT LISTEN ER KOMPLETT. En tiende sveip som ikke står her
faller på `test_hver_sveip_i_drift_er_dekket` — ellers ville neste modul
fått formen uten porten, og det er akkurat slik den gale formen spredte
seg til seks filer.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

ROT = Path(__file__).resolve().parents[3]
DRIFT = ROT / "platform" / "drift"

#: Modulnavn → SQL-fragment som identifiserer sveipedøren. Fragmentet er
#: med for at den falske tilkoblingen skal kunne skille sveipekallet fra
#: låsen og opplåsingen — ikke for å låse SQL-teksten fast.
SVEIPENE = (
    ("avstemmingssveip", "m13_sveip_avstemming"),
    ("fakturasveip", "m14_sveip_fakturaer"),
    ("begrepssveip", "m9_sveip_utlopte"),
    ("betalingssveip", "m41_sveip_betalinger"),
    ("adressesveip", "m19_sveip_adresser"),
    ("lonnssveip", "m39_sveip_lonnsgrunnlag"),
    ("kampanjesveip", "m44_sveip_kampanjer"),
    ("motpartssveip", "m48_sveip_motparter"),
    ("sanksjonssveip", "m49_sveip_sanksjoner"),
    ("anbudssveip", "m46_sveip_anbud"),
    ("tilskuddssveip", "m51_sveip_tilskudd"),
    ("merkevaresveip", "m55_sveip_merkevare"),
    ("ehfsveip", "m54_sveip_ehf"),
    ("tollkodesveip", "m52_sveip_tollkode"),
    ("myndighetssveip", "m47_sveip_myndighetsplikt"),
    ("postjournalsveip", "m50_sveip_postjournal"),
    ("hmssveip", "m53_sveip_hms"),
    ("likviditetssveip", "m15_sveip_likviditet"),
    ("prognosesveip", "m33_sveip_prognose"),
    ("optimalisatorsveip", "m36_sveip_optimalisering"),
    ("motesveip", "m7_sveip_moter"),
    ("innholdssveip", "m20_sveip_innhold"),
    ("telefonisveip", "m43_sveip_telefoni"),
    ("compliancesveip", "m34_sveip_etterprovinger"),
    ("fordringssveip", "m23_sveip_fordringer"),
    ("henvendelsessveip", "m17_sveip_henvendelser"),
    ("kontovaktsveip", "m42_sveip_konto"),
    ("lagersveip", "m27_sveip_lager"),
    ("leverandorsveip", "m24_sveip_leverandorer"),
    ("onboardingsveip", "m18_sveip_onboarding"),
    ("prisboksveip", "m26_sveip_prisbok"),
    ("prosjektsveip", "m25_sveip_prosjekter"),
    ("personvernsveip", "m30_sveip_frister"),
    ("tilgangssveip", "m12_sveip_gjennomganger"),
)


class FalskTilkobling:
    """En tilkobling som svarer det testen ber om og HUSKER REKKEFØLGEN.

    Rekkefølgen er hele poenget: at `feilet` ble satt sier ingenting om
    hvorvidt transaksjonen ble stående.
    """

    def __init__(self, rader, *, fikk_las: bool = True):
        self.rader = list(rader)
        self.fikk_las = fikk_las
        self.spor: list[str] = []

    def execute(self, sql, *args):
        tekst = str(sql)
        if "advisory_lock" in tekst:
            self.spor.append("las")
        elif "advisory_unlock" in tekst:
            self.spor.append("opplas")
        else:
            self.spor.append("sveip")
        eier = self

        class Resultat:
            @staticmethod
            def fetchone():
                if "advisory_lock" in tekst:
                    return (eier.fikk_las,)
                return eier.rader[0] if eier.rader else None

            @staticmethod
            def fetchall():
                return list(eier.rader)

        return Resultat()

    def commit(self):
        self.spor.append("commit")

    def rollback(self):
        self.spor.append("rollback")


def _modul(navn: str):
    return importlib.import_module(f"drift.{navn}")


#: En rad med rikelig med kolonner. Sveipene pakker ut fire eller fem;
#: seks holder for alle uten at testen må kjenne hver enkelt arity.
EN_RAD = (1, 2, 3, 4, 5, 6)


def test_hver_sveip_i_drift_er_dekket():
    """LISTEN SKAL VÆRE KOMPLETT.

    Uten denne ville en tiende sveip fått den kopierte formen uten
    porten — og det er akkurat slik den gale formen spredte seg til seks
    filer før noen så den.
    """
    paa_disk = sorted(
        p.stem for p in DRIFT.glob("*sveip.py")
        if not p.stem.startswith("kjor_"))
    assert paa_disk == sorted(n for n, _sql in SVEIPENE), paa_disk


def test_hver_sveip_har_sin_egen_dsn_i_ci():
    """EN MANGLENDE DSN I `ci.yml` GJØR PORTEN STILLE, IKKE RØD.

    Hver sveiptest kobler seg med `koble(<SVEIP>_DSN or MIGRATOR_DSN)`
    — fallbacken er husets form, og den er riktig lokalt. Men i CI
    betyr en glemt DSN at testen kjører som MIGRATOR, som ikke har
    EXECUTE på sveipedøra. Da feiler porten med «permission denied»
    og ser ut som en kodefeil i modulen.

    DET SKJEDDE PÅ M-53 (127): rollen `disponit_hmssveip` ble
    opprettet av klyngefundamentet, men DSN-en manglet i `ci.yml`, og
    fem porter feilet i CI etter å ha vært grønne lokalt.

    Denne porten leser `ci.yml` som tekst og krever at hver sveip i
    `SVEIPENE` har sin egen `DISPONIT_TEST_<NAVN>_DSN` der.

    MUTASJONEN SOM DREPER DENNE: fjern én DSN-linje fra `ci.yml`.
    """
    import re
    ci = (ROT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8")
    # NAVNET HENTES FRA KJØREREN, IKKE FRA FILNAVNET. De to henger
    # ikke sammen: `begrepssveip.py` kjøres av `kjor_begrepssveip.py`,
    # som leser `DISPONIT_KUNNSKAPSSVEIP_URL` og altså tilhører
    # rollen `disponit_kunnskapssveip`. Et navn avledet av filnavnet
    # ville meldt den som manglende — en port som finner på sin egen
    # fasit finner også sine egne feil.
    mangler = []
    for navn, _doer in SVEIPENE:
        kjorer = DRIFT / f"kjor_{navn}.py"
        assert kjorer.exists(), f"{navn} har ingen kjører"
        url = re.findall(r"DISPONIT_[A-Z0-9_]+_URL",
                         kjorer.read_text(encoding="utf-8"))
        assert url, f"{kjorer.name} navngir ingen DSN-variabel"
        # `DISPONIT_X_URL` i drift ↔ `DISPONIT_TEST_X_DSN` i CI.
        ventet = url[0].replace("DISPONIT_", "DISPONIT_TEST_", 1)
        ventet = ventet[:-len("_URL")] + "_DSN"
        if ventet not in ci:
            mangler.append(f"{navn} → {ventet}")
    assert mangler == [], (
        "sveipene har ingen egen DSN i ci.yml og vil kjøre som"
        f" migrator: {mangler}")


@pytest.mark.parametrize("navn,doer", SVEIPENE)
def test_flere_rader_er_en_brutt_kontrakt_og_rulles_tilbake(navn, doer):
    """TO RADER ER EN DØR SOM IKKE OPPFØRTE SEG SOM KONTRAKTEN.

    `fetchone()` tidde om dette: den tok den første raden og gikk videre
    som om alt var i orden. Nå er det en feilet kjøring — OG
    TRANSAKSJONEN RULLES TILBAKE, ikke committes.

    MUTASJONEN SOM DREPER DENNE: bytt `fetchall()` tilbake til
    `fetchone()`, eller flytt `conn.commit()` foran valideringen.
    """
    m = _modul(navn)
    conn = FalskTilkobling([EN_RAD, EN_RAD])
    res = m.kjor(conn, tidligere_feil=1)
    assert res.feilet is True, f"{navn}: to rader ble godtatt"
    assert res.hoppet_over is False
    assert res.alarm_utlost is True
    assert "rollback" in conn.spor, f"{navn}: rullet ikke tilbake"
    # …OG INGEN COMMIT FØR ROLLBACKEN. Det er hele rettingen: en
    # transaksjon som ble stående mens kjøringen rapporterte feilet.
    assert "commit" not in conn.spor[:conn.spor.index("rollback")], \
        f"{navn}: committet før kontrakten var validert ({conn.spor})"


@pytest.mark.parametrize("navn,doer", SVEIPENE)
def test_ingen_rad_er_en_brutt_kontrakt_og_rulles_tilbake(navn, doer):
    """INGEN RAD ER IKKE «NULL FUNN».

    En jobb som ikke kunne måle rapporterer FUNN, aldri null — og den
    lar ikke transaksjonen stå.
    """
    m = _modul(navn)
    conn = FalskTilkobling([])
    res = m.kjor(conn, tidligere_feil=0)
    assert res.feilet is True, f"{navn}: ingen rad ble godtatt"
    assert res.alarm_utlost is False, f"{navn}: alarm på FØRSTE feil"
    assert "rollback" in conn.spor, f"{navn}: rullet ikke tilbake"
    assert "commit" not in conn.spor[:conn.spor.index("rollback")], \
        f"{navn}: committet før kontrakten var validert ({conn.spor})"


@pytest.mark.parametrize("navn,doer", SVEIPENE)
def test_en_rad_committes_og_telles(navn, doer):
    """…og den GYLDIGE veien committer fortsatt.

    Porten over ville vært grønn på en sveip som aldri committet noe som
    helst. Denne binder den andre halvdelen: én rad, ingen rollback, og
    tallene fra raden i resultatet.
    """
    m = _modul(navn)
    conn = FalskTilkobling([EN_RAD])
    res = m.kjor(conn, tidligere_feil=1)
    assert res.feilet is False, f"{navn}: en gyldig rad ble avvist"
    assert res.alarm_utlost is False
    assert res.hoppet_over is False
    assert "rollback" not in conn.spor, f"{navn}: rullet tilbake ({conn.spor})"
    assert conn.spor.index("commit") > conn.spor.index("sveip"), conn.spor
    assert (res.tenanter, res.nye, res.oppdaterte, res.lukkede) == (1, 2, 3, 4)


@pytest.mark.parametrize("navn,doer", SVEIPENE)
def test_raden_valideres_FOR_commit(navn, doer):
    """EN RAD SOM IKKE ER KONTRAKTEN SKAL RULLE TILBAKE.

    De to portene over måler at FEIL ANTALL RADER ruller tilbake. Denne
    måler radens FORM: felt som ikke lar seg lese er like mye en dør som
    ikke oppførte seg som kontrakten.

    FORSKJELLEN ER IKKE TEORETISK. Lå konverteringen etter `commit()`,
    var «kjøringen feilet» og «transaksjonen står» sanne samtidig — og
    kalleren rapporterte feilet mens funnene var skrevet. Ni sveip
    hadde den formen til 3/9.

    Porten sier ikke hvor mange felt hver sveip leser — den sier at en
    rad de ikke kan lese, ikke skal committes. Det er den doktrinen som
    er felles; antallet er modulens eget.

    MUTASJONEN SOM DREPER DENNE: flytt heltallskonverteringen tilbake
    til etter `conn.commit()`.
    """
    m = _modul(navn)
    for rad in ((), (1,), (1, 2, None), ("a", "b", "c", "d", "e")):
        conn = FalskTilkobling([rad])
        res = m.kjor(conn, tidligere_feil=1)
        assert res.feilet is True, f"{navn}: {rad!r} ble godtatt"
        assert "rollback" in conn.spor, f"{navn}: rullet ikke tilbake"
        assert "commit" not in conn.spor[:conn.spor.index("rollback")], \
            f"{navn}: committet før formen var validert ({conn.spor})"


@pytest.mark.parametrize("navn,doer", SVEIPENE)
def test_opptatt_las_er_hverken_suksess_eller_feil(navn, doer):
    """En kjøring som fant nøkkelen opptatt har ikke sveipet noe og har
    heller ikke feilet. `hoppet_over` står PÅ resultatet så kalleren vet
    at feiltelleren skal stå urørt — et rent standardresultat ville sett
    ut som en kjøring som fant null funn, og hver overlappende aktivering
    ville slettet en alt opptelt feil.
    """
    m = _modul(navn)
    conn = FalskTilkobling([EN_RAD], fikk_las=False)
    res = m.kjor(conn, tidligere_feil=1)
    assert res.hoppet_over is True
    assert res.feilet is False and res.alarm_utlost is False
    # …og den rørte ingenting: bare låseforsøket står i sporet.
    assert conn.spor == ["las"], conn.spor
