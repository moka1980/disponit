"""M-46 anbuds- og konkurransevakt v1 (118) — TREFFENE OG UTKASTET,
IKKE INNSENDINGEN.

Grensen `m46-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

DEN SKARPESTE PORTEN ER `utkastpunkt_uten_kilde`, og den måler et
FRAVÆR i skjemaet, ikke en sjekk i koden: `utkastpunkt` har ingen
fritekstkolonne som kan bære en påstand. Hvert punkt peker på et
`kildedokument` gjennom en NOT NULL fremmednøkkel, og teksten som står
der er et SITAT med sidereferanse.

DEN NEST SKARPESTE er `udekket_krav_uten_funn`, håndhevet av
`m46_merk_klart`: et utkast kan ikke merkes klart så lenge et ABSOLUTT
krav står udekket. Et absolutt krav uten dokumentasjon fører til
avvisning av tilbudet.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import ast
import datetime
import json
import os
import re
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo)
from .test_m37 import _sett_kontekst

ANBUDSSVEIP_DSN = os.environ.get("DISPONIT_TEST_ANBUDSSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "118_m46_anbudsregister.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "anbud.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "anbud.py",
    ROT / "platform" / "drift" / "anbudssveip.py",
    ROT / "platform" / "drift" / "kjor_anbudssveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("anbudsprofil", "anbud", "kvalifikasjonskrav",
        "kildedokument", "anbudsutkast", "utkastpunkt", "anbudsfunn")

_STRENG = re.compile(
    r"'''.*?'''" r'|""".*?"""'
    r"|`(?:[^`\\]|\\.)*`"
    r"|'(?:[^'\\]|\\.|'')*'"
    r'|"(?:[^"\\]|\\.)*"', re.S)


def _bare_kode(fil: Path, *, uten_strenger: bool = False) -> str:
    """Filens innhold uten kommentarer OG uten docstrings (m24-formen)."""
    tekst = fil.read_text(encoding="utf-8")
    linjer = tekst.splitlines()
    if fil.suffix == ".py":
        for node in ast.walk(ast.parse(tekst)):
            krop = getattr(node, "body", None)
            if not isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef,
                                     ast.AsyncFunctionDef)) or not krop:
                continue
            forst = krop[0]
            if (isinstance(forst, ast.Expr)
                    and isinstance(forst.value, ast.Constant)
                    and isinstance(forst.value.value, str)):
                for i in range(forst.lineno - 1, forst.end_lineno):
                    linjer[i] = ""
        merke = "#"
    else:
        merke = "--" if fil.suffix == ".sql" else "//"
    ut = "\n".join(l for l in linjer
                   if not l.lstrip().startswith(merke))
    return _STRENG.sub("''", ut) if uten_strenger else ut


def _rt():
    from db.pg import koble
    return koble(DSN)


def _sv():
    from db.pg import koble
    return koble(ANBUDSSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m46-{merke}-{secrets.token_hex(4)}"


def _profil(c, tenant, *, nace=("62.010",), geografi=("Oslo",),
            min_ore=0, maks_ore=100_000_000_000, frist_dogn=14,
            kilde_dogn=365, aktor="u-test"):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m46_sett_profil(%s,%s,%s,%s,%s,%s,%s,%s)",
                  (tenant, list(nace), list(geografi), min_ore,
                   maks_ore, frist_dogn, kilde_dogn,
                   aktor)).fetchone()[0]
    c.commit()
    return v


def _anbud(c, tenant, *, ref=None, kilde="doffin", tittel="Drift",
           giver="Oslo kommune", nace="62.010", geografi="Oslo",
           verdi=500_000_000, dager_til_frist=20, aid=None,
           aktor="u-test"):
    aid = aid or uuid.uuid4()
    ref = ref or ("DOF-" + secrets.token_hex(4))
    frist = (datetime.datetime.now(datetime.timezone.utc)
             + datetime.timedelta(days=dager_til_frist))
    _sett_kontekst(c, tenant)
    c.execute("SELECT m46_registrer_anbud("
              "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (tenant, aid, ref, kilde, tittel, giver, nace, geografi,
               verdi, frist, aktor))
    c.commit()
    return aid


def _krav(c, tenant, aid, *, nummer=None, tekst="ISO 9001",
          kravtype="sertifisering", absolutt=True, kid=None,
          aktor="u-test"):
    kid = kid or uuid.uuid4()
    nummer = nummer or ("K" + secrets.token_hex(2))
    _sett_kontekst(c, tenant)
    c.execute("SELECT m46_registrer_krav(%s,%s,%s,%s,%s,%s,%s,%s)",
              (tenant, kid, aid, nummer, tekst, kravtype, absolutt,
               aktor))
    c.commit()
    return kid, nummer


def _kilde(c, tenant, *, tittel="ISO-sertifikat",
           dokumenttype="sertifikat", gyldig_til="2030-01-01",
           did=None, aktor="u-test"):
    did = did or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute("SELECT m46_registrer_kilde(%s,%s,%s,%s,%s::date,%s,%s)",
              (tenant, did, tittel, dokumenttype, gyldig_til,
               secrets.token_hex(32), aktor))
    c.commit()
    return did


def _utkast(c, tenant, aid, *, uid=None, aktor="u-test"):
    uid = uid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m46_opprett_utkast(%s,%s,%s,%s)",
                  (tenant, uid, aid, aktor)).fetchone()[0]
    c.commit()
    return uid, v


def _punkt(c, tenant, uid, kid, did, *, sitat="Sertifikatet gjelder",
           side="s. 1", pid=None, aktor="u-test"):
    pid = pid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute("SELECT m46_registrer_punkt(%s,%s,%s,%s,%s,%s,%s,%s)",
              (tenant, pid, uid, kid, did, sitat, side, aktor))
    c.commit()
    return pid


def _merk_klart(c, tenant, uid, *, aktor="u-test"):
    _sett_kontekst(c, tenant)
    n = c.execute("SELECT m46_merk_klart(%s,%s,%s)",
                  (tenant, uid, aktor)).fetchone()[0]
    c.commit()
    return n


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m46_sveip_anbud(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT funntype, over_grense, detalj, apen"
        "  FROM anbudsfunn WHERE tenant=%s ORDER BY funntype",
        (tenant,)).fetchall()
    m.rollback()
    return rader


# --------------------------------------------------------------------
# §0: hver invariant i `m46-v1` har minst én port.
# --------------------------------------------------------------------

def test_grensen_er_registrert_og_hver_invariant_har_en_port():
    """§0-regelen, målt."""
    from manifestskjema import KRAVGRENSER
    inv = set(KRAVGRENSER["m46-v1"]["invarianter"])
    assert inv
    tekst = Path(__file__).read_text(encoding="utf-8")
    mangler = sorted(i for i in inv if i not in tekst)
    assert mangler == [], f"invarianter uten port: {mangler}"


def test_utkastpunkt_har_ingen_fritekstkolonne():
    """`utkastpunkt_uten_kilde` — DEN SKARPESTE PORTEN.

    Den måler et FRAVÆR i skjemaet, ikke en sjekk i koden. Hadde
    tabellen hatt en `pastand TEXT`-kolonne ved siden av `kilde_id`,
    ville invarianten vært en regel noen måtte huske å håndheve ved
    hver ny skrivevei. Nå er den formen på tabellen: et punkt uten
    kilde kan ikke uttrykkes.

    MUTASJONEN SOM DREPER DENNE: gjør `kilde_id` nullbar, eller legg
    til en påstandskolonne.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    blokk = sql[sql.index("CREATE TABLE utkastpunkt"):
                sql.index("CREATE INDEX utkastpunkt_utkast")]
    assert "kilde_id UUID NOT NULL" in blokk
    assert "utkastpunkt_kilde_fk" in blokk
    assert "REFERENCES kildedokument" in blokk
    # INGEN kolonne som kan bære en kildeløs påstand.
    for forbudt in ("pastand", "fritekst", "egen_tekst", "notat"):
        assert forbudt not in blokk.lower(), forbudt
    # Teksten som FINNES er et sitat med sidereferanse.
    assert "sitat TEXT NOT NULL" in blokk
    assert "sidereferanse TEXT NOT NULL" in blokk


def test_ingen_kolonne_og_ingen_dor_sender_tilbud():
    """`modulen_sendte_tilbud` — FRAVÆRET er porten.

    Et innsendt tilbud er BINDENDE, og fristen gjør det irreversibelt:
    man kan ikke trekke det og sende et bedre etterpå.

    MUTASJONEN SOM DREPER DENNE: legg til en `sendt`-kolonne eller en
    innsendingsdør.
    """
    sql = _bare_kode(MIGRASJON).lower()
    for forbudt in ("sendt", "m46_send", "innsend", "levert_portal"):
        assert forbudt not in sql, forbudt
    api = _bare_kode(MODULFILER[0]).lower()
    for forbudt in ("send_tilbud", "innsend", "m46_send"):
        assert forbudt not in api, forbudt
    js = _bare_kode(FLATE, uten_strenger=True).lower()
    for forbudt in ("sendinn", "send_inn", "innsend"):
        assert forbudt not in js, forbudt
    # Tilstanden som FINNES heter «klar til gjennomgang».
    assert "klar_til_gjennomgang" in _bare_kode(MIGRASJON)


def test_modulen_henter_ingenting_fra_doffin_eller_ted():
    """`modulen_hentet_eksternt` — M-48 fikk klyngens ene unntak.

    Anbudsportalene er ikke ETT oppslag, de er et ABONNEMENT: en
    søkeprofil som kjører kontinuerlig og henter alt som matcher.
    """
    for fil in MODULFILER:
        tre = ast.parse(fil.read_text(encoding="utf-8"))
        navn = set()
        for node in ast.walk(tre):
            if isinstance(node, ast.Import):
                navn.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                navn.add(node.module or "")
                navn.update(a.name for a in node.names)
        for forbudt in ("httpx", "requests", "socket", "urllib",
                        "foretaksregister", "ssrf"):
            assert not any(n == forbudt or n.endswith("." + forbudt)
                           for n in navn), f"{fil.name}: {forbudt}"
    js = _bare_kode(FLATE, uten_strenger=True)
    for forbudt in ("XMLHttpRequest", "WebSocket", "sendBeacon",
                    "EventSource"):
        assert forbudt not in js, forbudt


def test_modulen_signerer_ingen_attestasjon():
    """`modulen_signerte_attestasjon` — v1 attesterer ingenting."""
    for fil in list(MODULFILER) + [MIGRASJON]:
        kode = _bare_kode(fil, uten_strenger=True).lower()
        for forbudt in ("attester", "signer", "attestasjon"):
            assert forbudt not in kode, f"{fil.name}: {forbudt}"


def test_sokeprofilen_ligger_i_basen_ikke_i_koden():
    """`sokeprofil_hardkodet` — NACE, geografi og verdi er TENANTENS.

    Hvilke konkurranser man i det hele tatt vil se er ikke noe en
    modul kan bestemme.
    """
    sveip = _bare_kode(MODULFILER[1])
    for forbudt in ("NACE", "GEOGRAFI", "MIN_VERDI", "MAKS_VERDI",
                    "FRIST_VARSEL"):
        assert forbudt not in sveip, forbudt
    api = MODULFILER[0].read_text(encoding="utf-8")
    for forbudt in ("nace_koder = [", "STANDARD_NACE",
                    "DEFAULT_PROFIL"):
        assert forbudt not in api, forbudt
    # …og sveipen leser profilen fra basen.
    sql = MIGRASJON.read_text(encoding="utf-8")
    assert "FROM public.anbudsprofil p WHERE p.tenant = v_t" in sql


# --------------------------------------------------------------------
# Databaseportene.
# --------------------------------------------------------------------

@pg
def test_utkast_kan_ikke_merkes_klart_med_udekket_absolutt_krav(miljo):
    """`udekket_krav_uten_funn` — DEN NEST SKARPESTE PORTEN.

    Et absolutt krav uten dokumentasjon fører til AVVISNING av
    tilbudet. Uten denne vakten kunne noen merket et utkast klart med
    hull i, og hullet ville bare vært et funn i en liste ingen leser
    før fristen.

    MUTASJONEN SOM DREPER DENNE: fjern sjekken i `m46_merk_klart`.
    """
    tenant = _tenantnavn("klart")
    with _rt() as c:
        _profil(c, tenant)
        aid = _anbud(c, tenant)
        kid, nummer = _krav(c, tenant, aid, absolutt=True)
        did = _kilde(c, tenant)
        uid, _v = _utkast(c, tenant, aid)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _merk_klart(c, tenant, uid)
        assert "absolutte krav står udekket" in str(e.value)
        assert nummer in str(e.value)
        c.rollback()
        # …og med kravet dekket går det.
        _punkt(c, tenant, uid, kid, did)
        assert _merk_klart(c, tenant, uid) == 0
        c.rollback()


@pg
def test_merk_klart_sier_hvor_mange_vektede_krav_som_mangler(miljo):
    """SVARET SIER HVA UTKASTET IKKE DEKKER.

    Den som merker klart skal vite hva de sender uten, i stedet for å
    tro at alt er dekket. Et vektet krav STOPPER ikke — det gir trekk,
    og forskjellen er hele grunnen til at `absolutt` står på raden.

    MUTASJONEN SOM DREPER DENNE: returner alltid 0, eller la vektede
    krav blokkere.
    """
    tenant = _tenantnavn("vektet")
    with _rt() as c:
        _profil(c, tenant)
        aid = _anbud(c, tenant)
        abs_id, _ = _krav(c, tenant, aid, absolutt=True)
        _krav(c, tenant, aid, tekst="Tre referanser",
              kravtype="erfaring", absolutt=False)
        _krav(c, tenant, aid, tekst="Fem aars drift",
              kravtype="erfaring", absolutt=False)
        did = _kilde(c, tenant)
        uid, _v = _utkast(c, tenant, aid)
        _punkt(c, tenant, uid, abs_id, did)
        assert _merk_klart(c, tenant, uid) == 2
        c.rollback()


@pg
def test_punkt_krever_en_gyldig_kilde(miljo):
    """Et utløpt sertifikat er ikke dokumentasjon.

    Kilden var kanskje gyldig da den ble registrert; går den ut,
    påstår et punkt som peker på den noe kilden ikke lenger bærer.
    """
    tenant = _tenantnavn("utlopt")
    with _rt() as c:
        _profil(c, tenant)
        aid = _anbud(c, tenant)
        kid, _ = _krav(c, tenant, aid)
        utlopt = _kilde(c, tenant, tittel="Gammel attest",
                        dokumenttype="attest", gyldig_til="2020-01-01")
        uid, _v = _utkast(c, tenant, aid)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _punkt(c, tenant, uid, kid, utlopt)
        assert "ikke gyldig lenger" in str(e.value)
        c.rollback()


@pg
def test_punkt_kan_ikke_dekke_et_krav_fra_et_annet_anbud(miljo):
    """Uten denne sjekken ville tellingen av udekkede krav sett riktig
    ut mens utkastet var tomt der det gjaldt."""
    tenant = _tenantnavn("kryss")
    with _rt() as c:
        _profil(c, tenant)
        a1 = _anbud(c, tenant)
        a2 = _anbud(c, tenant)
        k1, _ = _krav(c, tenant, a1)
        did = _kilde(c, tenant)
        u2, _v = _utkast(c, tenant, a2)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _punkt(c, tenant, u2, k1, did)
        assert "hører til anbud" in str(e.value)
        c.rollback()


@pg
def test_et_klart_utkast_er_frosset(miljo):
    """Et nytt punkt hører til et NYTT utkast.

    TO GJERDER: døra gir den ærlige feilmeldingen, radvakten stanser
    den som går utenom.
    """
    tenant = _tenantnavn("frys")
    with _rt() as c:
        _profil(c, tenant)
        aid = _anbud(c, tenant)
        kid, _ = _krav(c, tenant, aid)
        vektet, _ = _krav(c, tenant, aid, tekst="Vektet",
                          kravtype="erfaring", absolutt=False)
        did = _kilde(c, tenant)
        uid, _v = _utkast(c, tenant, aid)
        _punkt(c, tenant, uid, kid, did)
        _merk_klart(c, tenant, uid)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _punkt(c, tenant, uid, vektet, did)
        assert "merket klart" in str(e.value)
        c.rollback()
    with psycopg.connect(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        m.execute("SET ROLE disponit_anbud_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as e:
            m.execute("UPDATE anbudsutkast SET klar_av='noen'"
                      " WHERE tenant=%s", (tenant,))
        assert "frosset" in str(e.value)
        m.rollback()


@pg
def test_et_nytt_utkast_far_neste_versjon(miljo):
    """Versjonen REGNES, den sendes ikke inn.

    En kaller som fikk oppgi den kunne gjenbrukt et nummer og skrevet
    over historikken — «hva sto i utkast 2 da noen godkjente det» må
    kunne besvares.
    """
    tenant = _tenantnavn("versjon")
    with _rt() as c:
        _profil(c, tenant)
        aid = _anbud(c, tenant)
        _u1, v1 = _utkast(c, tenant, aid)
        _u2, v2 = _utkast(c, tenant, aid)
        assert (v1, v2) == (1, 2)
        c.rollback()
    sql = MIGRASJON.read_text(encoding="utf-8")
    sig = sql[sql.index("CREATE FUNCTION m46_opprett_utkast("):]
    sig = sig[:sig.index(")")]
    assert "versjon" not in sig, sig


@pg
def test_historikken_kan_ikke_overskrives(miljo):
    """`anbudshistorikk_overskrevet` — append-only, håndhevet."""
    tenant = _tenantnavn("append")
    with _rt() as c:
        _profil(c, tenant)
        aid = _anbud(c, tenant)
        kid, _ = _krav(c, tenant, aid)
        did = _kilde(c, tenant)
        uid, _v = _utkast(c, tenant, aid)
        _punkt(c, tenant, uid, kid, did)
        c.rollback()
    for tabell in ("kvalifikasjonskrav", "kildedokument",
                   "utkastpunkt"):
        # EGEN FORBINDELSE PER TABELL: `rollback()` tilbakestiller
        # `SET ROLE` (116s lærdom).
        with psycopg.connect(MIGRATOR_DSN) as m:
            _sett_kontekst(m, tenant)
            m.execute("SET ROLE disponit_anbud_eier")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                m.execute(f"UPDATE {tabell} SET tenant=tenant"
                          " WHERE tenant=%s", (tenant,))
            m.rollback()


@pg
def test_to_punkter_paa_samme_krav_nektes(miljo):
    """To svar på samme krav er ikke dobbelt så godt dekket; det er to
    svar der leseren må gjette hvilket som gjelder."""
    tenant = _tenantnavn("dobbel")
    with _rt() as c:
        _profil(c, tenant)
        aid = _anbud(c, tenant)
        kid, _ = _krav(c, tenant, aid)
        did = _kilde(c, tenant)
        uid, _v = _utkast(c, tenant, aid)
        _punkt(c, tenant, uid, kid, did)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _punkt(c, tenant, uid, kid, did, sitat="et annet sitat")
        c.rollback()


@pg
def test_tenantisolasjon(miljo):
    """`tenantlekkasje_i_anbudsregister` — RLS + krev_tenantkontekst."""
    a, b = _tenantnavn("iso-a"), _tenantnavn("iso-b")
    with _rt() as c:
        _profil(c, a)
        _profil(c, b)
        _anbud(c, a, tittel="A-anbud")
        _anbud(c, b, tittel="B-anbud")
        _sett_kontekst(c, a)
        with pytest.raises(psycopg.Error):
            c.execute("SELECT * FROM m46_anbudene(%s,%s)", (b, 10))
        c.rollback()
        _sett_kontekst(c, a)
        titler = [r[3] for r in c.execute(
            "SELECT * FROM m46_anbudene(%s,%s)", (a, 50)).fetchall()]
        assert titler == ["A-anbud"], titler
        c.rollback()
    with psycopg.connect(MIGRATOR_DSN) as m:
        for tabell in EGNE:
            rad = m.execute(
                "SELECT relrowsecurity, relforcerowsecurity"
                "  FROM pg_class WHERE relname=%s", (tabell,)).fetchone()
            assert rad == (True, True), (tabell, rad)
        m.rollback()


@pg
def test_kjoretidsrollen_har_ingen_tabellrettigheter(miljo):
    """SP-7, målt med `has_table_privilege`."""
    with psycopg.connect(MIGRATOR_DSN) as m:
        for tabell in EGNE:
            for rett in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                har = m.execute(
                    "SELECT has_table_privilege('disponit', %s, %s)",
                    (tabell, rett)).fetchone()[0]
                assert har is False, (tabell, rett)
        m.rollback()


@pg
def test_sveipen_er_ikke_kjoretidsrollens(miljo):
    """Sveipen har sin EGEN rolle med nøyaktig én EXECUTE."""
    with psycopg.connect(MIGRATOR_DSN) as m:
        assert m.execute(
            "SELECT has_function_privilege('disponit',"
            " 'm46_sveip_anbud(int)', 'EXECUTE')").fetchone()[0] is False
        assert m.execute(
            "SELECT has_function_privilege('disponit_anbudssveip',"
            " 'm46_sveip_anbud(int)', 'EXECUTE')").fetchone()[0] is True
        for tabell in EGNE:
            assert m.execute(
                "SELECT has_table_privilege('disponit_anbudssveip',"
                " %s, 'SELECT')", (tabell,)).fetchone()[0] is False
        m.rollback()


@pg
def test_sveipen_ser_alle_tenanter(miljo):
    """DEN LATE MARKØREN, målt (112s lærdom, gjentatt i 116/117)."""
    a, b, c_ = (_tenantnavn("sv-a"), _tenantnavn("sv-b"),
                _tenantnavn("sv-c"))
    with _rt() as c:
        for t in (a, b, c_):
            _anbud(c, t)      # ingen profil -> `ingen_profil`
        c.rollback()
    with _sv() as v:
        rad = _sveip(v)
    assert rad[0] >= 3, rad
    with psycopg.connect(MIGRATOR_DSN) as m:
        for t in (a, b, c_):
            typer = [r[0] for r in _funn(m, t)]
            assert "ingen_profil" in typer, (t, typer)


@pg
def test_udekket_absolutt_krav_blir_et_funn_og_kan_ikke_lukkes(miljo):
    """Et absolutt krav uten dokumentasjon fører til avvisning.

    En knapp som gjorde den observasjonen borte ville sett ut som
    saksbehandling — samme dom som M-49s bekreftede treff (117).
    """
    tenant = _tenantnavn("udekket")
    with _rt() as c:
        _profil(c, tenant)
        aid = _anbud(c, tenant)
        _krav(c, tenant, aid, absolutt=True)
        _utkast(c, tenant, aid)
        c.rollback()
    with _sv() as v:
        _sveip(v)
    with psycopg.connect(MIGRATOR_DSN) as m:
        typer = [r[0] for r in _funn(m, tenant)]
        assert "udekket_absolutt_krav" in typer, typer
    with _rt() as c:
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as e:
            c.execute("SELECT m46_lukk_funn(%s,%s,%s,%s,%s)",
                      (tenant, aid, "udekket_absolutt_krav",
                       "vil bli ferdig", "u-test"))
        assert "kan ikke lukkes bort" in str(e.value)
        c.rollback()


@pg
def test_funnet_lukkes_naar_kravet_dekkes(miljo):
    """M-39s felle unngått: funnet har et botemiddel."""
    tenant = _tenantnavn("lukk")
    with _rt() as c:
        _profil(c, tenant)
        aid = _anbud(c, tenant)
        kid, _ = _krav(c, tenant, aid, absolutt=True)
        did = _kilde(c, tenant)
        uid, _v = _utkast(c, tenant, aid)
        c.rollback()
    with _sv() as v:
        _sveip(v)
    with psycopg.connect(MIGRATOR_DSN) as m:
        assert "udekket_absolutt_krav" in [r[0] for r in _funn(m, tenant)]
    with _rt() as c:
        _punkt(c, tenant, uid, kid, did)     # botemiddelet
        c.rollback()
    with _sv() as v:
        _sveip(v)
    with psycopg.connect(MIGRATOR_DSN) as m:
        rader = {r[0]: r[3] for r in _funn(m, tenant)}
        assert rader.get("udekket_absolutt_krav") is False, rader


@pg
def test_fristen_blir_et_funn_naar_den_naermer_seg(miljo):
    """Modulens mest betente funn: en frist som passerer er den ene
    feilen som ikke kan rettes dagen etter."""
    tenant = _tenantnavn("frist")
    with _rt() as c:
        _profil(c, tenant, frist_dogn=14)
        _anbud(c, tenant, dager_til_frist=5)      # innenfor varselet
        _anbud(c, tenant, dager_til_frist=-3)     # passert
        c.rollback()
    with _sv() as v:
        _sveip(v)
    with psycopg.connect(MIGRATOR_DSN) as m:
        typer = [r[0] for r in _funn(m, tenant)]
        assert "frist_naermer_seg" in typer, typer
        assert "frist_passert" in typer, typer


@pg
def test_evidenskjeden_baerer_hvert_steg(miljo):
    """Et tilsyn — eller en klage på tildelingen — spør etter dette."""
    tenant = _tenantnavn("evidens")
    with _rt() as c:
        _profil(c, tenant)
        aid = _anbud(c, tenant)
        kid, _ = _krav(c, tenant, aid)
        did = _kilde(c, tenant)
        uid, _v = _utkast(c, tenant, aid)
        _punkt(c, tenant, uid, kid, did)
        _merk_klart(c, tenant, uid)
        c.rollback()
    with psycopg.connect(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        handlinger = {r[0] for r in m.execute(
            "SELECT handling FROM revisjonslogg"
            " WHERE tenant=%s AND kilde='m46_anbud'",
            (tenant,)).fetchall()}
        for h in ("anbud_registrert", "kvalifikasjonskrav_registrert",
                  "kildedokument_registrert", "anbudsutkast_opprettet",
                  "utkastpunkt_registrert", "utkast_merket_klart"):
            assert h in handlinger, (h, handlinger)
        m.rollback()


# --------------------------------------------------------------------
# API- og flateportene.
# --------------------------------------------------------------------

def test_rutene_er_registrert_med_riktig_scope():
    """LESING `okonomi:read`, SKRIVING `bestilling:opprett`."""
    from api.app import RUTESCOPE
    forventet = {
        ("GET", "/v1/anbud"): "okonomi:read",
        ("GET", "/v1/anbud/funn"): "okonomi:read",
        ("GET", "/v1/anbud/kilder"): "okonomi:read",
        ("GET", "/v1/anbud/{anbud_id:uuid}/krav"): "okonomi:read",
        ("GET", "/v1/anbud/{anbud_id:uuid}/utkast"): "okonomi:read",
        ("POST", "/v1/anbud/profil"): "bestilling:opprett",
        ("POST", "/v1/anbud/registrer"): "bestilling:opprett",
        ("POST", "/v1/anbud/kilde"): "bestilling:opprett",
        ("POST", "/v1/anbud/utkast/{utkast_id:uuid}/punkt"):
            "bestilling:opprett",
        ("POST", "/v1/anbud/utkast/{utkast_id:uuid}/klart"):
            "bestilling:opprett",
        ("POST", "/v1/anbud/{anbud_id:uuid}/krav/ny"):
            "bestilling:opprett",
        ("POST", "/v1/anbud/{anbud_id:uuid}/utkast/ny"):
            "bestilling:opprett",
        ("POST", "/v1/anbud/{anbud_id:uuid}/aktiv"):
            "bestilling:opprett",
        ("POST", "/v1/anbud/{anbud_id:uuid}/funn/lukk"):
            "bestilling:opprett",
    }
    for nokkel, scope in forventet.items():
        assert RUTESCOPE.get(nokkel) == scope, nokkel


def test_ingen_rute_sender_et_tilbud():
    """`modulen_sendte_tilbud`, sett fra rutetabellen.

    `/klart` er IKKE en innsendingsrute: den setter en tilstand hos
    oss, og nekter så lenge et absolutt krav står udekket.
    """
    from api.app import RUTESCOPE
    for metode, sti in RUTESCOPE:
        if sti.startswith("/v1/anbud"):
            for forbudt in ("send", "innsend", "lever", "publiser"):
                assert forbudt not in sti, (metode, sti)
    assert ("POST", "/v1/anbud/utkast/{utkast_id:uuid}/klart") \
        in RUTESCOPE


def test_punktruten_krever_alltid_en_kilde():
    """API-et har ingen vei til en kildeløs påstand — fordi kolonnen
    ikke finnes."""
    kilde = MODULFILER[0].read_text(encoding="utf-8")
    start = kilde.index("def registrer_punkt_endepunkt(")
    krop = kilde[start:kilde.index("\ndef ", start + 10)]
    assert '_kropp_uuid(kropp, "kilde_id", rid)' in krop
    assert '_tekst(kropp, "sitat", rid' in krop
    assert '_tekst(kropp, "sidereferanse", rid' in krop
    # INGEN valgfri kilde, ingen fritekstpåstand.
    for forbudt in ("kilde_id or None", "pastand", "fritekst"):
        assert forbudt not in krop, forbudt


def test_flaten_har_ingen_send_knapp():
    """`modulen_sendte_tilbud`, sett fra flaten.

    MUTASJONEN SOM DREPER DENNE: legg til en «Send inn»-knapp.
    """
    # KODEN, IKKE KOMMENTARENE. Ordet «innsendt» står i kommentaren
    # som FORKLARER hvorfor knappen ikke finnes — en port som lette i
    # kommentarer ville straffet begrunnelsen og ikke handlingen.
    kode = _bare_kode(FLATE, uten_strenger=True)
    for forbudt in ("sendInn", "sendAnbud", "innsend", "leverTilbud"):
        assert forbudt not in kode, forbudt
    js = FLATE.read_text(encoding="utf-8")
    # «Klar til gjennomgang» sier hva den IKKE gjør.
    assert 't("ui.anbud.klart_hjelp")' in js
    for sprak in ("nb", "en"):
        d = json.loads((ROT / "locales" / f"{sprak}.json").read_text(
            encoding="utf-8"))
        tekst = d["ui.anbud.klart_hjelp"].lower()
        assert ("sender ingenting" in tekst
                or "submits nothing" in tekst), tekst


def test_flaten_tilbyr_bare_gyldige_kilder():
    """En knapp som alltid feiler er verre enn en valgmulighet som
    ikke finnes."""
    js = FLATE.read_text(encoding="utf-8")
    assert "kilder.filter((k) => k.gyldig_naa === true)" in js
    assert 't("ui.anbud.punkt.ingen_gyldige_kilder")' in js
    # …og knappen er død til kilde, sitat OG side er fylt ut.
    assert "knapp.disabled = !kilde.value" in js


def test_flaten_viser_udekkede_krav():
    """En flate som filtrerte bort de udekkede ville skjult nettopp
    det som må gjøres."""
    js = FLATE.read_text(encoding="utf-8")
    assert 't("ui.anbud.udekket")' in js
    # Kravtabellen tar HELE lista, ikke en filtrert.
    assert "export function kravTabell(krav, dekk)" in js
    assert "for (const k of krav)" in js


def test_sveipen_leser_fire_felt_og_ikke_flere():
    """#358s lærdom."""
    sveip = MODULFILER[1].read_text(encoding="utf-8")
    assert "KONTRAKTFELT = 4" in sveip
    assert "rader[0][:KONTRAKTFELT]" in sveip


def test_kjoreskriptet_har_ingen_fallback_til_database_url():
    """Runtime-rollen har med vilje ikke EXECUTE på sveipen."""
    kjor = MODULFILER[2].read_text(encoding="utf-8")
    assert "DISPONIT_ANBUDSSVEIP_URL" in kjor
    assert "DATABASE_URL" not in _bare_kode(MODULFILER[2])


def test_timeren_staar_i_klyngestigen():
    """09:20 — stigen er fordelt i klyngefundamentet."""
    sti_t = ROT / "deploy" / "staging" / "disponit-anbudssveip.timer"
    timer = sti_t.read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 09:20:00 UTC" in timer
    assert "Persistent=true" in timer
    sti_s = ROT / "deploy" / "staging" / "disponit-anbudssveip.service"
    tjeneste = sti_s.read_text(encoding="utf-8")
    assert ("LoadCredential=DISPONIT_ANBUDSSVEIP_URL:"
            "/etc/disponit/anbudssveip/DISPONIT_ANBUDSSVEIP_URL"
            in tjeneste)
    # …og beskrivelsen navngir SIN EGEN jobb (arvefeilen fra 116/117).
    assert "anbudssveip" in tjeneste.split("Description=")[1][:60]
    for arvet in ("adresser", "uavklarte treff"):
        assert arvet not in tjeneste, arvet


def test_sveipen_staar_i_flaaterosteret():
    """En sveip som ikke er i rosteret er en sveip ingen savner."""
    from drift.sveipestatus import FLAATEN
    assert FLAATEN.get("anbudssveip") == 30, FLAATEN


def test_ui_axe_dekning():
    """`ui_axe_alvorlige_brudd` — flaten er registrert der axe kjører."""
    app_js = (ROT / "platform" / "core" / "ui" / "static" / "js"
              / "app.js").read_text(encoding="utf-8")
    assert "anbud: visAnbud," in app_js
    sitekart = (ROT / "platform" / "core" / "ui" / "static" / "js"
                / "sitekart.js").read_text(encoding="utf-8")
    assert '{ nokkel: "anbud", scope: "okonomi:read"' in sitekart
