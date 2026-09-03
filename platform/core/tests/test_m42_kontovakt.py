"""M-42 kontoverifikasjon og transaksjonsvakt v1 (110) — HISTORIKKEN.

Grensen `m42-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR koden
(§0-regelen), og hver invariant der har minst én port her.

DEN SKARPESTE PORTEN ER `kontohistorikk_overskrevet`. Det finnes ingen
kolonne noe sted som holder «gjeldende kontonummer»; den gjeldende
kontoen ER den siste oppgaven, og hver oppgave er frosset. Svindelen
avsløres av HISTORIKKEN — en tabell som ble oppdatert på stedet ville
slettet beviset i samme øyeblikk som det oppsto.

DEN NEST SKARPESTE er at DEN SOM OPPGA KONTOEN IKKE KAN VERIFISERE DEN.
Er de samme, er ingenting verifisert, og `konto_verifisert_uavhengig` er
nøyaktig navnet på det vilkåret.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import ast
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

KONTOVAKTSVEIP_DSN = os.environ.get("DISPONIT_TEST_KONTOVAKTSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "110_m42_kontoregister.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "kontovakt.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "kontovakt.py",
    ROT / "platform" / "drift" / "kontovaktsveip.py",
    ROT / "platform" / "drift" / "kjor_kontovaktsveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("kontoterskel", "betalingsmottaker", "kontooppgave",
        "kontoverifikasjon", "kontofunn")

#: Kontonumre testene bruker. De er OPPDIKTET og skal aldri stå i basen —
#: at de IKKE gjør det er en av portene.
KONTO_A = "1234.56.78903"
KONTO_A_ANNEN_SKRIVEMATE = "12345678903"
KONTO_B = "9999.11.22233"

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
    return koble(KONTOVAKTSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m42-{merke}-{secrets.token_hex(4)}"


def _terskler(c, tenant, *, rever=365, uver=7, aktor="u-test"):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m42_sett_terskler(%s,%s,%s,%s)",
                  (tenant, rever, uver, aktor)).fetchone()[0]
    c.commit()
    return v


def _mottaker(c, tenant, *, ref=None, navn="Byggmester AS", mid=None,
              aktor="u-test"):
    mid = mid or uuid.uuid4()
    ref = ref or ("LEV-" + secrets.token_hex(4))
    _sett_kontekst(c, tenant)
    c.execute("SELECT m42_registrer_mottaker(%s,%s,%s,%s,%s)",
              (tenant, mid, ref, navn, aktor))
    c.commit()
    return mid


def _konto(c, tenant, mid, nummer, *, av="Kari hos motparten",
           kanal="faktura", dato="2026-01-10", notat="fra faktura",
           aktor="u-test"):
    _sett_kontekst(c, tenant)
    maske = c.execute(
        "SELECT m42_oppgi_konto(%s,%s,%s,%s,%s,%s,%s::date,%s,%s)",
        (tenant, uuid.uuid4(), mid, nummer, av, kanal, dato, notat,
         aktor)).fetchone()[0]
    c.commit()
    return maske


def _siste_oppgave(c, tenant, mid):
    _sett_kontekst(c, tenant)
    rad = c.execute("SELECT oppgave_id FROM m42_gjeldende_konto(%s,%s)",
                    (tenant, mid)).fetchone()
    c.rollback()
    return rad[0]


def _verifiser(c, tenant, oid, *, metode="ringte_kjent_nummer",
               av="Ola hos oss", notat="ringte kjent nummer",
               dato="2026-01-11", aktor="u-test"):
    _sett_kontekst(c, tenant)
    ut = c.execute(
        "SELECT m42_verifiser_konto(%s,%s,%s,%s,%s,%s,%s::date,%s)",
        (tenant, uuid.uuid4(), oid, metode, av, notat, dato,
         aktor)).fetchone()[0]
    c.commit()
    return ut


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m42_sveip_konto(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT funntype, over_grense, fra_maske, til_maske, apen"
        "  FROM kontofunn WHERE tenant=%s ORDER BY funntype",
        (tenant,)).fetchall()
    m.rollback()
    return rader


def _tell_utenfor(m):
    tabeller = [r[0] for r in m.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
        " ORDER BY tablename").fetchall()]
    m.rollback()
    ut = {}
    for tab in tabeller:
        if tab in EGNE:
            continue
        try:
            ut[tab] = m.execute(
                f'SELECT count(*) FROM public."{tab}"').fetchone()[0]
        except psycopg.errors.InsufficientPrivilege:
            m.rollback()
    m.rollback()
    assert len(ut) > 20, \
        f"porten teller bare {len(ut)} tabeller — den måler ingenting"
    return ut


# ---------------------------------------------------------------------------
# INVARIANT 1: kontohistorikk_overskrevet — MODULENS SKARPESTE
# ---------------------------------------------------------------------------

@pg
def test_invariant_kontohistorikk_overskrevet(migrator):
    """HISTORIKKEN OVERSKRIVES ALDRI.

    Det finnes ingen kolonne noe sted som holder «gjeldende
    kontonummer»; den gjeldende kontoen ER den siste oppgaven. Svindelen
    avsløres av historikken — en tabell som ble oppdatert på stedet ville
    slettet beviset i samme øyeblikk som det oppsto.

    MUTASJONEN SOM DREPER DENNE: legg en `gjeldende_konto`-kolonne på
    `betalingsmottaker`, eller fjern UPDATE-armen fra `m42_oppgave_vakt`.
    """
    # 1. SKJEMAET har ingen «gjeldende konto».
    rader = migrator.execute(
        "SELECT table_name, column_name"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND column_name IN ('gjeldende_konto','kontonummer',"
        "                       'konto','bankkonto','iban')"
        " ORDER BY 1,2", (list(EGNE),)).fetchall()
    migrator.rollback()
    assert rader == [], rader

    tenant = _tenantnavn("historikk")
    c = _rt()
    try:
        _terskler(c, tenant)
        mid = _mottaker(c, tenant)
        assert _konto(c, tenant, mid, KONTO_A) == "*******8903"
        _konto(c, tenant, mid, KONTO_B, dato="2026-03-01",
               kanal="epost", notat="ny konto i e-post")
        _sett_kontekst(c, tenant)
        rader = c.execute(
            "SELECT kontonummer_maske, oppgitt_kanal, endret"
            "  FROM m42_kontohistorikken(%s,%s,200)",
            (tenant, mid)).fetchall()
        c.rollback()
    finally:
        c.close()
    # BEGGE LINJENE STÅR, nyeste først, og byttet er merket.
    assert rader == [("*******2233", "epost", True),
                     ("*******8903", "faktura", False)], rader

    # 2. TO GJERDER MOT SKRIVING: eieren har ikke rettigheten…
    rettigheter = migrator.execute(
        "SELECT DISTINCT privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_kontovakt_eier'"
        "   AND table_name='kontooppgave' ORDER BY 1").fetchall()
    migrator.rollback()
    assert [r[0] for r in rettigheter] == ["INSERT", "SELECT"]
    # …og VAKTEN stanser den som har den.
    for sql, ord_ in (("UPDATE kontooppgave SET oppgitt_av='noen'",
                       "FROSSET"),
                      ("DELETE FROM kontooppgave", "DELETE avvist")):
        _sett_kontekst(migrator, tenant)
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(sql + " WHERE tenant=%s", (tenant,))
        assert ord_ in str(ei.value), sql
        migrator.rollback()
    # …og SALTET ER FROSSET: et nytt salt ville gjort hver eldre hash
    # usammenlignbar og skjult nettopp den endringen modulen finnes for.
    for kolonne, verdi in (("hash_salt", "'a' || hash_salt"),
                           ("ekstern_ref", "'en annen ref'")):
        _sett_kontekst(migrator, tenant)
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(
                f"UPDATE betalingsmottaker SET {kolonne} = {verdi}"
                " WHERE tenant=%s", (tenant,))
        assert "frosset" in str(ei.value), kolonne
        migrator.rollback()


@pg
def test_kontonummeret_lagres_aldri(migrator):
    """KONTONUMMERET LAGRES ALDRI — bare masken og en SALTET hash.

    Og saltet er MOTTAKERENS EGET: to mottakere med samme kontonummer får
    forskjellig hash, så en angriper med én kjent konto ikke kan kartlegge
    hvem andre som bruker den.

    MUTASJONEN SOM DREPER DENNE: la hashen regnes uten saltet.
    """
    tenant = _tenantnavn("hash")
    c = _rt()
    try:
        _terskler(c, tenant)
        a = _mottaker(c, tenant, ref="A")
        b = _mottaker(c, tenant, ref="B")
        _konto(c, tenant, a, KONTO_A)
        _konto(c, tenant, b, KONTO_A)
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    rader = migrator.execute(
        "SELECT kontonummer_maske, kontonummer_hash FROM kontooppgave"
        " WHERE tenant=%s", (tenant,)).fetchall()
    migrator.rollback()
    assert len(rader) == 2
    # SAMME NUMMER, TO MOTTAKERE, TO FORSKJELLIGE HASHER.
    assert rader[0][1] != rader[1][1]
    for maske, hash_ in rader:
        assert maske == "*******8903"
        assert re.fullmatch(r"[0-9a-f]{64}", hash_)
    # …og NUMMERET STÅR INGEN STEDER, heller ikke i revisjonsloggen.
    _sett_kontekst(migrator, tenant)
    for kilde, uttrykk in (
            ("kontooppgave", "kontonummer_maske || kontonummer_hash"
                             " || oppgitt_av || notat"),
            ("revisjonslogg", "coalesce(handling,'') ||"
                              " coalesce(begrunnelse::text,'')")):
        treff = migrator.execute(
            f"SELECT count(*) FROM public.{kilde}"
            f" WHERE tenant=%s AND ({uttrykk}) LIKE %s",
            (tenant, "%78903%")).fetchone()[0]
        assert treff == 0, kilde
    migrator.rollback()
    # MASKEN SLIPPER NØYAKTIG FIRE SIFRE. Alt annet er stjerner.
    assert re.fullmatch(r"\*+[0-9]{4}", rader[0][0])


# ---------------------------------------------------------------------------
# INVARIANT 2: kontoendring_uten_funn
# ---------------------------------------------------------------------------

@pg
def test_invariant_kontoendring_uten_funn(migrator):
    """EN KONTOENDRING BLIR ET FUNN I SAMME TRANSAKSJON.

    Den venter ikke på nattens sveip: en endret utbetalingskonto er det
    høyeste svindelsignalet vi har, og et døgns forsinkelse er et døgn
    der pengene kan gå.

    OG SAMME KONTO I EN ANNEN SKRIVEMÅTE ER INGEN ENDRING. Mellomrom og
    punktum er skrivemåter, ikke forskjellige kontonumre.

    MUTASJONEN SOM DREPER DENNE: flytt funnskrivingen ut av
    `m42_oppgi_konto` og over i sveipen.
    """
    tenant = _tenantnavn("endring")
    c = _rt()
    try:
        _terskler(c, tenant, uver=3650)
        mid = _mottaker(c, tenant)
        _konto(c, tenant, mid, KONTO_A)
        # SAMME KONTO, ANNEN SKRIVEMÅTE → ingen endring.
        _konto(c, tenant, mid, KONTO_A_ANNEN_SKRIVEMATE,
               dato="2026-02-01", kanal="portal", notat="samme konto")
    finally:
        c.close()
    assert _funn(migrator, tenant) == [], \
        "en annen skrivemåte ble tolket som en kontoendring"

    c = _rt()
    try:
        _konto(c, tenant, mid, KONTO_B, dato="2026-03-01",
               kanal="epost", notat="ny konto i e-post")
    finally:
        c.close()
    # FUNNET STÅR ALT — INGEN SVEIP HAR KJØRT.
    funn = _funn(migrator, tenant)
    assert funn == [("kontoendring", None, "*******8903", "*******2233",
                     True)], funn

    # EN VERIFIKASJON AV DEN SISTE OPPGAVEN LUKKER FUNNET.
    c = _rt()
    try:
        oid = _siste_oppgave(c, tenant, mid)
        assert _verifiser(c, tenant, oid, dato="2026-03-02") == mid
    finally:
        c.close()
    assert [f[0] for f in _funn(migrator, tenant) if f[4]] == []
    # …og RADEN BLIR STÅENDE, lukket.
    assert [(f[0], f[4]) for f in _funn(migrator, tenant)] \
        == [("kontoendring", False)]

    # EN NY ENDRING ÅPNER DEN IGJEN — samme rad, ny tilstand.
    c = _rt()
    try:
        _konto(c, tenant, mid, "5555.66.77788", dato="2026-04-01",
               kanal="telefon", notat="enda en konto")
    finally:
        c.close()
    funn = _funn(migrator, tenant)
    assert funn == [("kontoendring", None, "*******2233", "*******7788",
                     True)], funn


@pg
def test_en_framtidig_dato_kan_ikke_skjule_en_kontoendring(migrator):
    """EN KONTO KAN IKKE OPPGIS I FRAMTIDA — og det er ingen formalitet.

    Sveipen måler «siste oppgave med dato <= i dag». En framtidsdatert
    linje ville derfor vært den siste for DØREN, men usynlig for
    SVEIPEN — som så ville LUKKET kontoendringsfunnet fordi den eldre
    linjen fortsatt så uendret ut. Altså en måte å skjule en
    kontoendring på ved å sette feil dato (CodeRabbit, 110).

    MUTASJONEN SOM DREPER DENNE: fjern datosjekken fra
    `m42_oppgi_konto`.
    """
    tenant = _tenantnavn("framtid")
    c = _rt()
    try:
        _terskler(c, tenant, uver=3650)
        mid = _mottaker(c, tenant)
        _konto(c, tenant, mid, KONTO_A)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m42_oppgi_konto(%s,%s,%s,%s,'Kari','epost',"
                "current_date + 1,'i morgen','u')",
                (tenant, uuid.uuid4(), mid, KONTO_B))
        assert "framtida" in str(ei.value)
        c.rollback()
        # …og det samme for verifikasjonen.
        oid = _siste_oppgave(c, tenant, mid)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m42_verifiser_konto(%s,%s,%s,"
                "'ringte_kjent_nummer','Ola','x',current_date + 1,'u')",
                (tenant, uuid.uuid4(), oid))
        assert "framtida" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    # HISTORIKKEN STÅR SOM FØR: den avviste linjen ble aldri skrevet.
    _sett_kontekst(migrator, tenant)
    n = migrator.execute(
        "SELECT count(*) FROM kontooppgave WHERE tenant=%s",
        (tenant,)).fetchone()[0]
    migrator.rollback()
    assert n == 1


@pg
def test_en_iban_som_ender_paa_bokstaver_kan_ogsa_foeres(migrator):
    """MASKEN TAR FIRE TEGN, ikke fire siffer.

    En IBAN kan ende på bokstaver, og et register som avviste den ville
    nektet å skrive ned nettopp den kontoen noen betalte til
    (CodeRabbit, 110).
    """
    tenant = _tenantnavn("iban")
    c = _rt()
    try:
        _terskler(c, tenant)
        mid = _mottaker(c, tenant)
        maske = _konto(c, tenant, mid,
                      "GB33 BUKB 2020 1555 55AB CD")
        assert maske == "******************ABCD", maske
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    maske = migrator.execute(
        "SELECT kontonummer_maske FROM kontooppgave WHERE tenant=%s",
        (tenant,)).fetchone()[0]
    migrator.rollback()
    assert re.fullmatch(r"\*+[0-9A-Za-z]{4}", maske)
    # …og NUMMERET STÅR FORTSATT IKKE NOE STED.
    assert "2020" not in maske


@pg
def test_en_verifikasjon_av_en_GAMMEL_oppgave_lukker_ingenting(migrator):
    """Å verifisere en gammel oppgave sier ingenting om kontoen som står
    der NÅ — og et funn som ble lukket av det ville vært et funn lukket
    med feil bevis."""
    tenant = _tenantnavn("gammel")
    c = _rt()
    try:
        _terskler(c, tenant, uver=3650)
        mid = _mottaker(c, tenant)
        _konto(c, tenant, mid, KONTO_A)
        gammel = _siste_oppgave(c, tenant, mid)
        _konto(c, tenant, mid, KONTO_B, dato="2026-03-01")
        assert _funn(migrator, tenant)[0][4] is True
        _verifiser(c, tenant, gammel, dato="2026-03-02")
    finally:
        c.close()
    funn = _funn(migrator, tenant)
    assert funn[0][0] == "kontoendring"
    assert funn[0][4] is True, \
        "funnet ble lukket av en verifikasjon av en GAMMEL oppgave"


@pg
def test_funnene_over_tid_og_sveipens_idempotens(migrator):
    """`uverifisert_konto` og `verifikasjon_utlopt`."""
    tenant = _tenantnavn("tid")
    c = _rt()
    try:
        _terskler(c, tenant, rever=365, uver=7)
        uver = _mottaker(c, tenant, ref="UVERIFISERT")
        utlopt = _mottaker(c, tenant, ref="UTLOPT")
        _konto(c, tenant, uver, KONTO_A, dato="2026-01-10")
        _konto(c, tenant, utlopt, KONTO_B, dato="2020-01-10")
        oid = _siste_oppgave(c, tenant, utlopt)
        _verifiser(c, tenant, oid, dato="2020-01-11")
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_kontovakt_eier")
    kand = {r[0]: r[1] for r in migrator.execute(
        "SELECT funntype, over_grense FROM m42_funnkandidater("
        "%s,'2026-02-01'::date) ORDER BY 1", (tenant,)).fetchall()}
    migrator.rollback()
    # 2026-02-01 minus 2026-01-10 er 22 døgn, minus grensen på 7.
    assert kand["uverifisert_konto"] == 15, kand
    assert kand["verifikasjon_utlopt"] > 1800, kand

    with _sv() as v:
        _sveip(v)
    typer = sorted(f[0] for f in _funn(migrator, tenant) if f[4])
    assert typer == ["uverifisert_konto", "verifikasjon_utlopt"], typer
    with _sv() as v:
        rad = _sveip(v)
    assert rad[1] == 0, "sveip nummer to skrev nye rader"


@pg
def test_en_tenant_uten_grenser_er_et_funn(migrator):
    tenant = _tenantnavn("utenterskel")
    c = _rt()
    try:
        _mottaker(c, tenant)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert [f[0] for f in _funn(migrator, tenant)] == ["ingen_terskel"]


# ---------------------------------------------------------------------------
# INVARIANT 3: verifikasjon_uten_menneske_og_metode
# ---------------------------------------------------------------------------

@pg
def test_invariant_verifikasjon_uten_menneske_og_metode(migrator):
    """EN VERIFIKASJON HAR ET MENNESKE, EN METODE OG ET NOTAT.

    «Verifisert» uten hvem og hvordan er ikke en måling — det er en
    påstand, og `konto_verifisert` ville hvilt på den.
    """
    kolonner = {r[0]: r[1] for r in migrator.execute(
        "SELECT column_name, is_nullable"
        "  FROM information_schema.columns"
        " WHERE table_schema='public'"
        "   AND table_name='kontoverifikasjon'").fetchall()}
    migrator.rollback()
    for felt in ("metode", "verifisert_av", "notat", "verifisert_dato"):
        assert kolonner.get(felt) == "NO", felt

    tenant = _tenantnavn("metode")
    c = _rt()
    try:
        _terskler(c, tenant)
        mid = _mottaker(c, tenant)
        _konto(c, tenant, mid, KONTO_A)
        oid = _siste_oppgave(c, tenant, mid)
        # METODEN ER ET LUKKET SETT.
        for metode in ("gjettet", "", None):
            _sett_kontekst(c, tenant)
            with pytest.raises(psycopg.Error):
                c.execute(
                    "SELECT m42_verifiser_konto(%s,%s,%s,%s,'Ola',"
                    "'x','2026-01-11'::date,'u')",
                    (tenant, uuid.uuid4(), oid, metode))
            c.rollback()
        # …og NOTATET er obligatorisk.
        for notat in ("", "   ", None):
            _sett_kontekst(c, tenant)
            with pytest.raises(psycopg.Error):
                c.execute(
                    "SELECT m42_verifiser_konto(%s,%s,%s,"
                    "'ringte_kjent_nummer','Ola',%s,"
                    "'2026-01-11'::date,'u')",
                    (tenant, uuid.uuid4(), oid, notat))
            c.rollback()
    finally:
        c.close()


@pg
def test_den_som_oppga_kontoen_kan_ikke_verifisere_den(migrator):
    """MODULENS NEST SKARPESTE PORT.

    Er de samme, er ingenting verifisert — og
    `konto_verifisert_uavhengig` er nøyaktig navnet på det vilkåret.
    Regelen står i VAKTEN, ikke bare i døren, og den er ufølsom for
    store bokstaver og mellomrom: en kontroll som kunne omgås med en
    ekstra blank var ingen kontroll.

    MUTASJONEN SOM DREPER DENNE: fjern sammenligningen fra
    `m42_verifikasjon_vakt`.
    """
    tenant = _tenantnavn("uavhengig")
    c = _rt()
    try:
        _terskler(c, tenant)
        mid = _mottaker(c, tenant)
        _konto(c, tenant, mid, KONTO_A, av="Kari Nordmann")
        oid = _siste_oppgave(c, tenant, mid)
        for av in ("Kari Nordmann", "  kari nordmann  ",
                   "KARI NORDMANN"):
            _sett_kontekst(c, tenant)
            with pytest.raises(psycopg.Error) as ei:
                c.execute(
                    "SELECT m42_verifiser_konto(%s,%s,%s,"
                    "'ringte_kjent_nummer',%s,'x','2026-01-11'::date,"
                    "'u')", (tenant, uuid.uuid4(), oid, av))
            assert "kan ikke verifisere den" in str(ei.value), av
            c.rollback()
        # …og et ANNET menneske slipper gjennom.
        assert _verifiser(c, tenant, oid, av="Ola Hansen") == mid
    finally:
        c.close()
    # VAKTEN, VED DIREKTE DML.
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_kontovakt_eier")
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "INSERT INTO kontoverifikasjon (tenant, verifikasjon_id,"
            " oppgave_id, metode, verifisert_av, notat,"
            " verifisert_dato, registrert_av)"
            " VALUES (%s,%s,%s,'annet','kari nordmann','x',"
            "'2026-01-11','u')", (tenant, uuid.uuid4(), oid))
    assert "kan ikke verifisere den" in str(ei.value)
    migrator.rollback()
    # …og VERIFIKASJONEN ER FROSSET.
    for sql, ord_ in (("UPDATE kontoverifikasjon SET metode='annet'",
                       "FROSSET"),
                      ("DELETE FROM kontoverifikasjon", "DELETE avvist")):
        _sett_kontekst(migrator, tenant)
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(sql + " WHERE tenant=%s", (tenant,))
        assert ord_ in str(ei.value), sql
        migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 4: modulen_verifiserte_mot_ekstern_kanal /
#              modulen_stoppet_betaling / modulen_signerte_attestasjon
# ---------------------------------------------------------------------------

def test_invariant_modulen_verifiserte_ingenting_og_stoppet_ingenting():
    """Målt på IMPORTENE (AST), på KODEN og på RUTENE.

    INGEN UTGÅENDE KANAL: modulen slår ikke opp i en bank, ringer ingen,
    og sender ingenting. En verifikasjon er en NEDSKRIVING av at et
    menneske gjorde det.
    """
    for fil in MODULFILER:
        for node in ast.walk(ast.parse(fil.read_text(encoding="utf-8"))):
            navn = []
            if isinstance(node, ast.Import):
                navn = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                navn = [node.module or ""] if node.level == 0 else []
            for n in navn:
                assert "attestering" not in n, f"{fil.name}: {n}"
                assert n.split(".")[0] not in {
                    "httpx", "requests", "aiohttp", "urllib",
                    "http", "socket", "smtplib", "ftplib",
                    "cryptography", "hmac"}, \
                    f"{fil.name} importerer {n} — v1 har ingen utgående" \
                    " kanal"
    for fil in (MIGRASJON, FLATE, *MODULFILER):
        uten = _bare_kode(fil, uten_strenger=True).lower()
        for ord_ in ("attestasjon", "signatur", "sperr", "blokker",
                     "stopp_betaling", "bankapi", "kontoregisteret_api",
                     "urlopen", "aiohttp", "requests\\.", "m24_"):
            assert not re.search(ord_, uten), \
                f"{fil.name} bærer «{ord_}» — v1 stopper ingen betaling" \
                " og verifiserer ingenting mot en ekstern kanal"

    from api.app import RUTESCOPE
    mine = sorted(sti for _m, sti in RUTESCOPE
                  if sti.startswith("/v1/kontovakt"))
    assert mine == [
        "/v1/kontovakt",
        "/v1/kontovakt/mottaker",
        "/v1/kontovakt/oppgave/{oppgave_id:uuid}/verifikasjon",
        "/v1/kontovakt/terskler",
        "/v1/kontovakt/{mottaker_id:uuid}/aktiv",
        "/v1/kontovakt/{mottaker_id:uuid}/historikk",
        "/v1/kontovakt/{mottaker_id:uuid}/konto",
    ], mine
    # …OG DET FINNES INGEN DØR SOM SPERRER NOE.
    sql = MIGRASJON.read_text(encoding="utf-8")
    for navn in re.findall(r"CREATE FUNCTION (m42_\w+)", sql):
        for ord_ in ("sperr", "blokker", "stopp", "avvis_betaling"):
            assert ord_ not in navn, navn


@pg
def test_sveipen_stopper_ingenting_og_rorer_ingen_oppgave(migrator):
    """Målt på VIRKELIGHETEN: en full sveip endrer ikke ett radantall
    utenfor registeret — OG DEN RØRER INGEN LINJE I HISTORIKKEN, selv om
    den vet nøyaktig hvem som byttet konto i går.
    """
    tenant = _tenantnavn("sveip")
    c = _rt()
    try:
        _terskler(c, tenant, uver=3650)
        mid = _mottaker(c, tenant)
        _konto(c, tenant, mid, KONTO_A)
        _konto(c, tenant, mid, KONTO_B, dato="2026-03-01")
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    for_bok = migrator.execute(
        "SELECT oppgave_id, kontonummer_hash FROM kontooppgave"
        " WHERE tenant=%s ORDER BY oppgave_id", (tenant,)).fetchall()
    migrator.rollback()
    for_ = _tell_utenfor(migrator)
    with _sv() as v:
        _sveip(v)
    etter = _tell_utenfor(migrator)
    assert "revisjonslogg" in for_
    for_.pop("revisjonslogg")
    etter.pop("revisjonslogg")
    assert for_ == etter, \
        ("sveipen endret radantall utenfor registeret: "
         + str({k: (for_[k], etter[k]) for k in for_
                if for_[k] != etter[k]}))
    _sett_kontekst(migrator, tenant)
    etter_bok = migrator.execute(
        "SELECT oppgave_id, kontonummer_hash FROM kontooppgave"
        " WHERE tenant=%s ORDER BY oppgave_id", (tenant,)).fetchall()
    migrator.rollback()
    assert for_bok == etter_bok, "sveipen rørte historikken"


# ---------------------------------------------------------------------------
# INVARIANT 5: verifikasjonskrav_hardkodet
# ---------------------------------------------------------------------------

def test_invariant_verifikasjonskrav_hardkodet():
    for fil in MODULFILER:
        for m in re.finditer(
                r"^([A-Z_]*(?:GRENSE|TERSKEL|DOGN|VERIFIKASJON|FRIST)"
                r"[A-Z_]*)\s*=\s*(\d+)", _bare_kode(fil), re.M):
            assert m.group(1) in ("GRENSE",), \
                f"{fil.name} har terskelkonstanten {m.group(1)}"
    from drift import kontovaktsveip
    assert kontovaktsveip.GRENSE == 500
    kode = _bare_kode(MIGRASJON)
    assert "m42_sveip_konto(p_grense INT DEFAULT 500)" in kode
    assert "FROM public.kontoterskel" in kode
    from api.kontovakt import terskler_endepunkt
    doc = terskler_endepunkt.__doc__ or ""
    assert "M-1" in doc and "gap" in doc.lower()


@pg
def test_tersklene_versjoneres_og_kan_ikke_slettes(migrator):
    tenant = _tenantnavn("terskelversjon")
    c = _rt()
    try:
        assert _terskler(c, tenant, rever=365) == 1
        assert _terskler(c, tenant, rever=180) == 2
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("DELETE FROM kontoterskel WHERE tenant=%s",
                         (tenant,))
    assert "DELETE avvist" in str(ei.value)
    migrator.rollback()
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "UPDATE kontoterskel SET uverifisert_dogn=1 WHERE tenant=%s",
            (tenant,))
    assert "versjonen må øke" in str(ei.value)
    migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 6: tenantlekkasje_i_kontoregister
# ---------------------------------------------------------------------------

@pg
def test_invariant_tenantlekkasje_direkte(migrator):
    a = _tenantnavn("lekk-a")
    b = _tenantnavn("lekk-b")
    c = _rt()
    try:
        _terskler(c, a)
        _mottaker(c, a)
        _terskler(c, b)
        _sett_kontekst(c, b)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT * FROM m42_kontostatus(%s)", (a,))
        assert "tenantkontekst" in str(ei.value)
        c.rollback()
        _sett_kontekst(c, a)
        assert c.execute("SELECT * FROM m42_kontostatus(%s)",
                         (a,)).fetchone()[0] == 1
        c.rollback()
    finally:
        c.close()
    for tab in EGNE:
        rad = migrator.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
            " WHERE oid = %s::regclass", (f"public.{tab}",)).fetchone()
        assert rad == (True, True), f"{tab}: RLS ikke ENABLE+FORCE"
    migrator.rollback()


@pg
def test_invariant_tenantlekkasje_over_api(migrator, klient):
    fremmed = _tenantnavn("fremmed")
    c = _rt()
    try:
        _terskler(c, TENANT)
        _mottaker(c, TENANT, ref="EGEN-REF")
        _terskler(c, fremmed)
        _mottaker(c, fremmed, ref="FREMMED-REF")
    finally:
        c.close()
    cookie, _csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/kontovakt", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    kropp = json.dumps(r.json())
    assert "EGEN-REF" in kropp
    assert "FREMMED-REF" not in kropp


@pg
def test_ingen_av_de_fem_tabellene_kan_tommes(migrator):
    tenant = _tenantnavn("truncate")
    c = _rt()
    try:
        _terskler(c, tenant, uver=3650)
        mid = _mottaker(c, tenant)
        _konto(c, tenant, mid, KONTO_A)
        oid = _siste_oppgave(c, tenant, mid)
        _verifiser(c, tenant, oid)
        _konto(c, tenant, mid, KONTO_B, dato="2026-03-01")
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    for tab in EGNE:
        _sett_kontekst(migrator, tenant)
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(f"TRUNCATE public.{tab}")
        assert ("TRUNCATE avvist" in str(ei.value)
                or "foreign key" in str(ei.value)), f"{tab}: {ei.value}"
        migrator.rollback()
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("TRUNCATE public.betalingsmottaker CASCADE")
    assert "TRUNCATE avvist" in str(ei.value), ei.value
    migrator.rollback()


def test_skrivedorene_som_leser_en_tilstand_laser_raden():
    sql = MIGRASJON.read_text(encoding="utf-8")
    for doer in ("m42_oppgi_konto", "m42_verifiser_konto",
                 "m42_sett_mottakeraktiv"):
        i = sql.index(f"CREATE FUNCTION {doer}(")
        assert "FOR UPDATE" in sql[i:sql.index("END $$;", i)], doer
    # …OG LÅSEN LIGGER PÅ MOTTAKEREN, ikke på den frosne oppgaven:
    # `FOR UPDATE` krever UPDATE-rettigheten, og `kontooppgave` har den
    # ikke — append-only nekter nettopp den.
    i = sql.index("CREATE FUNCTION m42_verifiser_konto(")
    kropp = sql[i:sql.index("END $$;", i)]
    assert "FROM public.betalingsmottaker m" in kropp
    assert "FROM public.kontooppgave o\n     WHERE" in kropp


# ---------------------------------------------------------------------------
# Sveipearbeideren
# ---------------------------------------------------------------------------

@pg
def test_sveipen_nekter_kontekst(migrator):
    with _sv() as v:
        v.execute("SELECT set_config('disponit.tenant','t',false)")
        with pytest.raises(psycopg.Error) as ei:
            v.execute("SELECT * FROM m42_sveip_konto(500)")
        assert "KRYSS-TENANT" in str(ei.value)
        v.rollback()


@pg
def test_sveiperollen_har_ingen_tabellrettigheter(migrator):
    if not KONTOVAKTSVEIP_DSN:
        pytest.skip("DISPONIT_TEST_KONTOVAKTSVEIP_DSN ikke satt")
    rader = migrator.execute(
        "SELECT table_name, privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_kontovaktsveip'").fetchall()
    migrator.rollback()
    assert rader == [], rader


def test_sveipen_nekter_aa_starte_uten_egen_dsn(tmp_path, monkeypatch):
    from drift import kjor_kontovaktsveip
    monkeypatch.delenv("DISPONIT_KONTOVAKTSVEIP_URL", raising=False)
    monkeypatch.setenv("DISPONIT_KONTOVAKTSVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.setattr("db.hemmeligheter.last_credentials", lambda: None)
    assert kjor_kontovaktsveip.main() == 2


def test_arbeidernokkelen_er_modulens_egen():
    """To sveip som låser på samme nøkkel ville blokkert hverandre."""
    from drift import (avstemmingssveip, fordringssveip, kontovaktsveip,
                       lagersveip, leverandorsveip, prisboksveip,
                       prosjektsveip)
    nokler = [m.ARBEIDERNOKKEL for m in
              (avstemmingssveip, fordringssveip, lagersveip,
               leverandorsveip, prisboksveip, prosjektsveip)]
    assert kontovaktsveip.ARBEIDERNOKKEL not in nokler


# ---------------------------------------------------------------------------
# INVARIANT 7: ui_axe_alvorlige_brudd
# ---------------------------------------------------------------------------

def test_invariant_ui_axe_bor_i_js_suiten():
    fil = (ROT / "platform" / "core" / "ui" / "test"
           / "kontovakt.test.js")
    assert fil.exists(), "kontovakt.test.js mangler"
    assert "axe" in fil.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# HTTP-riggen
# ---------------------------------------------------------------------------

def _C_SESJON():
    from api import sesjon as sesjonmodul
    return sesjonmodul.C_SESJON


def _browserokt(migrator, roller):
    from api import sesjon as sesjonmodul
    _sett_kontekst(migrator, TENANT)
    bid = migrator.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://m42.test', %s) RETURNING bruker_id",
        ("s42-" + secrets.token_hex(6),)).fetchone()[0]
    migrator.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller, aktiv)"
        " VALUES (%s,%s,%s,true)", (TENANT, bid, list(roller)))
    cookie, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    ver = migrator.execute(
        "SELECT authz_version FROM brukermedlemskap WHERE tenant=%s"
        " AND bruker_id=%s", (TENANT, bid)).fetchone()[0]
    migrator.execute(
        "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
        " authz_snapshot, csrf_hash, opprettet, siste_bruk, utloper,"
        " tilbakekalt) VALUES (%s,%s,%s,%s,%s, now(), now(),"
        " now()+interval '1 hour', false)",
        (sesjonmodul._hash(cookie), TENANT, bid, ver,
         sesjonmodul._hash(csrf)))
    migrator.commit()
    return cookie, csrf


def _hpost(klient, cookie, csrf, sti, kropp, idem=None):
    from api import sesjon as sesjonmodul
    return klient.post(sti, json=kropp,
                       cookies={sesjonmodul.C_SESJON: cookie},
                       headers={"X-Disponit-CSRF": csrf,
                                "Idempotency-Key":
                                    idem or secrets.token_urlsafe(24)})


@pg
@dekker("kontovakt_ulovlig_tilstand")
def test_http_selvverifikasjon_er_409(migrator, klient):
    """FEILVEIEN `kontovakt_ulovlig_tilstand`, ende til ende.

    Kroppen ER velformet: metoden er fra det lukkede settet, datoen er
    lesbar, notatet står der. Det er BASEN som sier at den som oppga
    kontoen ikke kan verifisere den — er de samme, er ingenting
    verifisert.

    Dette er også testen `test_api_porter.test_hver_feilvei_har_en_test`
    krever for den nye feilveien.
    """
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _hpost(klient, cookie, csrf, "/v1/kontovakt/terskler",
               {"reverifikasjon_dogn": 365, "uverifisert_dogn": 7})
    assert r.status_code in (200, 201), r.text
    ref = "H-" + secrets.token_hex(4)
    r = _hpost(klient, cookie, csrf, "/v1/kontovakt/mottaker",
               {"ekstern_ref": ref, "navn": "HTTP Bygg AS"})
    assert r.status_code in (200, 201), r.text
    mid = r.json()["mottaker_id"]

    idem = secrets.token_urlsafe(24)
    kropp = {"kontonummer": KONTO_A, "oppgitt_av": "Kari hos motparten",
             "oppgitt_kanal": "faktura", "oppgitt_dato": "2026-01-10",
             "notat": "fra faktura"}
    r = _hpost(klient, cookie, csrf, f"/v1/kontovakt/{mid}/konto",
               kropp, idem=idem)
    assert r.status_code in (200, 201), r.text
    # SVARET ER MASKEN — aldri nummeret.
    assert r.json()["kontonummer_maske"] == "*******8903"
    assert "78903" not in json.dumps(r.json()).replace("*******8903", "")
    oid = r.json()["oppgave_id"]
    # SP-2: SAMME NØKKEL GIR IKKE TO LINJER I HISTORIKKEN.
    r2 = _hpost(klient, cookie, csrf, f"/v1/kontovakt/{mid}/konto",
                kropp, idem=idem)
    assert r2.status_code == 409, r2.text

    # SELVVERIFIKASJON: TILSTANDEN sier nei.
    r = _hpost(klient, cookie, csrf,
               f"/v1/kontovakt/oppgave/{oid}/verifikasjon",
               {"metode": "ringte_kjent_nummer",
                "verifisert_av": "kari hos motparten",
                "notat": "ringte", "verifisert_dato": "2026-01-11"})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "kontovakt_ulovlig_tilstand"
    # SAMME REFERANSE TO GANGER: også en tilstand.
    r = _hpost(klient, cookie, csrf, "/v1/kontovakt/mottaker",
               {"ekstern_ref": ref, "navn": "Dublett"})
    assert r.status_code == 409, r.text
    # …og en ukjent kanal eller metode er 400: KROPPEN er feil.
    for felt, verdi in (("oppgitt_kanal", "brevdue"),
                        ("oppgitt_kanal", None)):
        r = _hpost(klient, cookie, csrf, f"/v1/kontovakt/{mid}/konto",
                   {**kropp, felt: verdi})
        assert r.status_code == 400, (felt, verdi, r.text)
    r = _hpost(klient, cookie, csrf,
               f"/v1/kontovakt/oppgave/{oid}/verifikasjon",
               {"metode": "gjettet", "verifisert_av": "Ola",
                "notat": "x", "verifisert_dato": "2026-01-11"})
    assert r.status_code == 400, r.text
    # `aktiv` ER PÅKREVD: en utelatelse skal ikke deaktivere mottakeren.
    r = _hpost(klient, cookie, csrf, f"/v1/kontovakt/{mid}/aktiv", {})
    assert r.status_code == 400, r.text

    # ET ANNET MENNESKE SLIPPER GJENNOM.
    r = _hpost(klient, cookie, csrf,
               f"/v1/kontovakt/oppgave/{oid}/verifikasjon",
               {"metode": "ringte_kjent_nummer",
                "verifisert_av": "Ola Hansen",
                "notat": "ringte kjent nummer",
                "verifisert_dato": "2026-01-11"})
    assert r.status_code in (200, 201), r.text

    # HISTORIKKEN ER BEVISET, og den bærer hvem som verifiserte hvordan.
    r = klient.get(f"/v1/kontovakt/{mid}/historikk",
                   cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    linjer = r.json()["oppgaver"]
    assert len(linjer) == 1
    assert linjer[0]["kontonummer_maske"] == "*******8903"
    assert linjer[0]["verifisert_av"] == "Ola Hansen"
    assert linjer[0]["metode"] == "ringte_kjent_nummer"
    assert linjer[0]["endret"] is False
    # …og NUMMERET FINNES INGEN STEDER I SVARET.
    assert "78903" not in json.dumps(linjer).replace("*******8903", "")


@pg
def test_http_lesingen_bruker_okonomi_read(migrator, klient):
    from api.app import RUTESCOPE
    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert RUTESCOPE[("GET", "/v1/kontovakt")] == "okonomi:read"
    assert "okonomi:read" in ROLLE_TIL_SCOPES["admin"]
    for rolle in ("leser", "sikkerhet", "godkjenner"):
        assert "okonomi:read" not in ROLLE_TIL_SCOPES[rolle], rolle
    cookie, _c = _browserokt(migrator, ["leser"])
    r = klient.get("/v1/kontovakt", cookies={_C_SESJON(): cookie})
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# §0
# ---------------------------------------------------------------------------

def test_grensen_ble_registrert_for_koden():
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m42-v1"]
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["punktbinding"] == {}
    egen = Path(__file__).read_text(encoding="utf-8")
    for inv in g["invarianter"]:
        assert inv in egen, f"invarianten {inv} har ingen port"
