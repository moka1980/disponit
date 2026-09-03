"""M-41 betalings- og abonnementsstatus v1 (111) — HISTORIKKEN.

Grensen `m41-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR koden
(§0-regelen), og hver invariant der har minst én port her.

DEN SKARPESTE PORTEN ER `modulen_refunderte`. Netthandelsmalen har
`refusjon.utfor` stående som `modus: auto`, `reversering: irreversibel`,
opp til 5000 NOK — gatet på nettopp denne modulen. Å ta den fullmakten
før noen har målt hvor ofte statusen vår stemmer med
betalingsleverandørens, er å la modulen definere sin egen troverdighet
med kundens penger som innsats.

DEN NEST SKARPESTE er `betalingsstatus_uten_kilde`. En status uten
kilden sin er en PÅSTAND, og `betaling_autorisert` ville hvilt på
påstanden.

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

BETALINGSSVEIP_DSN = os.environ.get("DISPONIT_TEST_BETALINGSSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "111_m41_betalingsregister.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "betaling.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "betaling.py",
    ROT / "platform" / "drift" / "betalingssveip.py",
    ROT / "platform" / "drift" / "kjor_betalingssveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("betalingsterskel", "betalingssubjekt", "betalingshendelse",
        "abonnementsperiode", "betalingsfunn")

#: Oppdiktede betalingsmidler. At de IKKE står i basen er en av portene.
KORT_A = "4571 1234 5678 9010"
KORT_A_ANNEN_SKRIVEMATE = "4571-1234-5678-9010"
KORT_B = "5200 8765 4321 0987"

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
    return koble(BETALINGSSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m41-{merke}-{secrets.token_hex(4)}"


def _terskler(c, tenant, *, uavklart=3, avvik=0, reaut=7,
              aktor="u-test"):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m41_sett_terskler(%s,%s,%s,%s,%s)",
                  (tenant, uavklart, avvik, reaut, aktor)).fetchone()[0]
    c.commit()
    return v


def _subjekt(c, tenant, *, ref=None, navn="Kari Kunde", sid=None,
             aktor="u-test"):
    sid = sid or uuid.uuid4()
    ref = ref or ("ORD-" + secrets.token_hex(4))
    _sett_kontekst(c, tenant)
    c.execute("SELECT m41_registrer_subjekt(%s,%s,%s,%s,%s)",
              (tenant, sid, ref, navn, aktor))
    c.commit()
    return sid


def _status(c, tenant, sid, status, belop, *, forventet=None,
            middel=None, kilde="leverandor", kilde_ref=None,
            inntruffet="2026-08-20", notat="ført", aktor="u-test"):
    kilde_ref = kilde_ref or ("evt_" + secrets.token_hex(4))
    _sett_kontekst(c, tenant)
    ut = c.execute(
        "SELECT m41_registrer_status("
        "%s,%s,%s,%s,%s,%s,'NOK',%s,%s,%s,%s::date,%s,%s)",
        (tenant, uuid.uuid4(), sid, status, belop, forventet, middel,
         kilde, kilde_ref, inntruffet, notat, aktor)).fetchone()[0]
    c.commit()
    return ut


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m41_sveip_betalinger(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT funntype, over_grense, belop_ore, forventet_ore, apen"
        "  FROM betalingsfunn WHERE tenant=%s ORDER BY funntype",
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
# INVARIANT 1 og 2: modulen_refunderte / modulen_autoriserte_betaling
# ---------------------------------------------------------------------------

def test_invariant_modulen_refunderte_og_autoriserte_ingenting():
    """MODULENS SKARPESTE DOM, målt på IMPORTENE, KODEN og RUTENE.

    `refusjon.utfor` står i netthandelsmalen som `modus: auto`,
    `reversering: irreversibel`, opp til 5000 NOK — gatet på denne
    modulen. `refundert` KAN føres, fordi en refusjon kan ha skjedd; den
    kan ikke UTLØSES.

    MUTASJONEN SOM DREPER DENNE: legg til en `m41_utfor_refusjon`-dør.
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
                    "httpx", "requests", "aiohttp", "urllib", "http",
                    "socket", "smtplib", "decimal", "cryptography"}, \
                    f"{fil.name} importerer {n} — v1 har ingen utgående" \
                    " kanal mot en betalingsleverandør"
    for fil in (MIGRASJON, FLATE, *MODULFILER):
        uten = _bare_kode(fil, uten_strenger=True).lower()
        # `refunder(?!t)`: «refundert» er en STATUS man registrerer.
        # «Refunder» er handlingen v1 ikke gjør.
        for ord_ in (r"refunder(?!t)", r"utbetal", r"autoriser(?!t|ing)",
                     "attestasjon", "signatur", "kortnummer",
                     "urlopen", "aiohttp", r"requests\.", "m13_"):
            assert not re.search(ord_, uten), \
                f"{fil.name} bærer «{ord_}» — v1 refunderer ingenting" \
                " og autoriserer ingen betaling"

    # …OG DET FINNES INGEN DØR SOM GJØR DET.
    sql = MIGRASJON.read_text(encoding="utf-8")
    doerer = re.findall(r"CREATE FUNCTION (m41_\w+)", sql)
    assert len(doerer) >= 12, doerer
    for navn in doerer:
        for ord_ in ("refunder", "utbetal", "autoriser", "attester",
                     "signer", "sperr"):
            assert ord_ not in navn, navn

    from api.app import RUTESCOPE
    mine = sorted(sti for _m, sti in RUTESCOPE
                  if sti.startswith("/v1/betaling"))
    assert mine == [
        "/v1/betaling",
        "/v1/betaling/subjekt",
        "/v1/betaling/terskler",
        "/v1/betaling/{subjekt_id:uuid}/abonnement",
        "/v1/betaling/{subjekt_id:uuid}/aktiv",
        "/v1/betaling/{subjekt_id:uuid}/historikk",
        "/v1/betaling/{subjekt_id:uuid}/status",
    ], mine


@pg
def test_invariant_modulen_signerte_attestasjon(migrator):
    """KLYNGENS FELLESDOM: modulen tar ikke attestasjonsfullmakten.

    `betaling_autorisert` og `samme_betalingsmiddel` er vilkår
    netthandelsmalen lar en `auto`-handling hvile på. v1 REGISTRERER
    grunnlaget for dem og attesterer ingenting — det finnes ingen
    attestasjonstabell, ingen signatur og ingen dør som skriver en.

    MUTASJONEN SOM DREPER DENNE: legg en attestasjonskolonne på
    `betalingshendelse`.
    """
    kolonner = migrator.execute(
        "SELECT table_name, column_name"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND (column_name LIKE '%%attest%%'"
        "        OR column_name LIKE '%%signat%%')",
        (list(EGNE),)).fetchall()
    migrator.rollback()
    assert kolonner == [], kolonner
    sql = _bare_kode(MIGRASJON, uten_strenger=True).lower()
    for ord_ in ("attestasjon", "attester", "signatur", "signer"):
        assert ord_ not in sql, ord_
    # …og modulen skriver i revisjonsloggen som EVIDENS, ikke som en
    # attestasjon: beslutningen er alltid `TILLAT` på en registrering
    # modulen selv gjorde, aldri en dom om et vilkår.
    i = sql.index("create function m41_evidens(")
    kropp = sql[i:sql.index("end $$;", i)]
    assert "revisjonslogg" in kropp


@pg
def test_refundert_kan_registreres_men_utloses_ikke(migrator):
    """FORSKJELLEN SOM ER HELE MODULEN.

    En refusjon KAN ha skjedd, og da skal den kunne skrives ned — ellers
    ville historikken vært usann. Men den skrives ned som noe en KILDE
    meldte, ikke som noe modulen gjorde.
    """
    tenant = _tenantnavn("refusjon")
    c = _rt()
    try:
        _terskler(c, tenant)
        sid = _subjekt(c, tenant)
        _status(c, tenant, sid, "gjennomfort", 149900,
                forventet=149900, middel=KORT_A)
        _status(c, tenant, sid, "refundert", 149900, forventet=149900,
                kilde="leverandor", inntruffet="2026-08-22",
                notat="kunden angret")
        _sett_kontekst(c, tenant)
        rad = c.execute(
            "SELECT status, kilde, kilde_ref FROM"
            " m41_gjeldende_status(%s,%s,current_date)",
            (tenant, sid)).fetchone()
        c.rollback()
    finally:
        c.close()
    assert rad[0] == "refundert"
    # KILDEN STÅR PÅ RADEN. Det er forskjellen mellom «vi refunderte» og
    # «leverandøren meldte at det ble refundert».
    assert rad[1] == "leverandor"
    assert rad[2]


# ---------------------------------------------------------------------------
# INVARIANT 3: betalingsstatus_uten_kilde
# ---------------------------------------------------------------------------

@pg
def test_invariant_betalingsstatus_uten_kilde(migrator):
    """HVER STATUS HAR EN KILDE, og `kilde_ref` med den.

    MUTASJONEN SOM DREPER DENNE: gjør `kilde_ref` NULLbar.
    """
    kolonner = {r[0]: r[1] for r in migrator.execute(
        "SELECT column_name, is_nullable"
        "  FROM information_schema.columns"
        " WHERE table_schema='public'"
        "   AND table_name='betalingshendelse'").fetchall()}
    migrator.rollback()
    for felt in ("kilde", "kilde_ref", "status", "belop_ore",
                 "inntruffet", "notat"):
        assert kolonner.get(felt) == "NO", felt

    tenant = _tenantnavn("kilde")
    c = _rt()
    try:
        _terskler(c, tenant)
        sid = _subjekt(c, tenant)
        # KILDEN ER ET LUKKET SETT.
        for kilde in ("gjetning", "", None):
            _sett_kontekst(c, tenant)
            with pytest.raises(psycopg.Error):
                c.execute(
                    "SELECT m41_registrer_status(%s,%s,%s,'autorisert',"
                    "1,NULL,'NOK',NULL,%s,'ref','2026-08-20','x','u')",
                    (tenant, uuid.uuid4(), sid, kilde))
            c.rollback()
        # …og `kilde_ref` er obligatorisk.
        for ref in ("", "   ", None):
            _sett_kontekst(c, tenant)
            with pytest.raises(psycopg.Error):
                c.execute(
                    "SELECT m41_registrer_status(%s,%s,%s,'autorisert',"
                    "1,NULL,'NOK',NULL,'leverandor',%s,'2026-08-20',"
                    "'x','u')", (tenant, uuid.uuid4(), sid, ref))
            c.rollback()
        # SAMME KILDEHENDELSE REGISTRERES ÉN GANG. En webhook som kommer
        # to ganger er ikke to statusskift.
        _status(c, tenant, sid, "autorisert", 100, kilde_ref="evt_1")
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m41_registrer_status(%s,%s,%s,'autorisert',"
                "100,NULL,'NOK',NULL,'leverandor','evt_1',"
                "'2026-08-21','dublett','u')",
                (tenant, uuid.uuid4(), sid))
        assert "kilde_unik" in str(ei.value)
        c.rollback()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# INVARIANT 4: betalingshistorikk_overskrevet
# ---------------------------------------------------------------------------

@pg
def test_invariant_betalingshistorikk_overskrevet(migrator):
    """DEN GJELDENDE STATUSEN ER DEN SISTE HENDELSEN.

    Det finnes ingen kolonne som holder den, og hver hendelse er frosset.
    To gjerder: eieren har ikke rettigheten, og VAKTEN stanser den som
    likevel har den.
    """
    rader = migrator.execute(
        "SELECT table_name, column_name"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND column_name IN ('gjeldende_status','naavaerende_status',"
        "                       'siste_status')"
        " ORDER BY 1,2", (list(EGNE),)).fetchall()
    migrator.rollback()
    assert rader == [], rader

    tenant = _tenantnavn("historikk")
    c = _rt()
    try:
        _terskler(c, tenant)
        sid = _subjekt(c, tenant)
        _status(c, tenant, sid, "autorisert", 149900, forventet=149900,
                middel=KORT_A, inntruffet="2026-08-20")
        _status(c, tenant, sid, "gjennomfort", 149900,
                forventet=149900, middel=KORT_A,
                inntruffet="2026-08-21")
        _sett_kontekst(c, tenant)
        rader = c.execute(
            "SELECT status, endret, middel_endret FROM"
            " m41_statushistorikken(%s,%s,200)", (tenant, sid)).fetchall()
        c.rollback()
    finally:
        c.close()
    # NYESTE ØVERST, og skiftet er merket. Betalingsmiddelet er det
    # samme, så `middel_endret` er usant — det er grunnlaget
    # `samme_betalingsmiddel` en dag skal hvile på.
    assert rader == [("gjennomfort", True, False),
                     ("autorisert", False, False)], rader

    rettigheter = migrator.execute(
        "SELECT DISTINCT privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_betaling_eier'"
        "   AND table_name='betalingshendelse' ORDER BY 1").fetchall()
    migrator.rollback()
    assert [r[0] for r in rettigheter] == ["INSERT", "SELECT"]
    for sql, ord_ in (("UPDATE betalingshendelse SET status='feilet'",
                       "FROSSET"),
                      ("DELETE FROM betalingshendelse",
                       "DELETE avvist")):
        _sett_kontekst(migrator, tenant)
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(sql + " WHERE tenant=%s", (tenant,))
        assert ord_ in str(ei.value), sql
        migrator.rollback()
    # …og SALTET ER FROSSET.
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "UPDATE betalingssubjekt SET hash_salt='a'||hash_salt"
            " WHERE tenant=%s", (tenant,))
    assert "frosset" in str(ei.value)
    migrator.rollback()


@pg
def test_en_framtidig_dato_kan_ikke_skjule_et_statusskifte(migrator):
    """Sveipen måler «siste hendelse med dato <= i dag». En
    framtidsdatert rad ville vært den siste for DØREN, men usynlig for
    SVEIPEN (110s lærdom).

    MUTASJONEN SOM DREPER DENNE: fjern datosjekken fra
    `m41_registrer_status`.
    """
    tenant = _tenantnavn("framtid")
    c = _rt()
    try:
        _terskler(c, tenant)
        sid = _subjekt(c, tenant)
        _status(c, tenant, sid, "autorisert", 100)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m41_registrer_status(%s,%s,%s,'gjennomfort',"
                "100,NULL,'NOK',NULL,'leverandor','evt_m',"
                "current_date + 1,'i morgen','u')",
                (tenant, uuid.uuid4(), sid))
        assert "framtida" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    n = migrator.execute(
        "SELECT count(*) FROM betalingshendelse WHERE tenant=%s",
        (tenant,)).fetchone()[0]
    migrator.rollback()
    assert n == 1


# ---------------------------------------------------------------------------
# INVARIANT 5: betalingsmiddel_lagret_i_klartekst
# ---------------------------------------------------------------------------

@pg
def test_invariant_betalingsmiddel_lagret_i_klartekst(migrator):
    """KORTNUMMERET LAGRES ALDRI — bare masken og en SALTET hash.

    Og saltet er SUBJEKTETS EGET: to kunder med samme kort får
    forskjellig hash, så en angriper med ett kjent kort ikke kan
    kartlegge hvem andre som bruker det.

    MUTASJONEN SOM DREPER DENNE: la hashen regnes uten saltet.
    """
    tenant = _tenantnavn("hash")
    c = _rt()
    try:
        _terskler(c, tenant)
        a = _subjekt(c, tenant, ref="A")
        b = _subjekt(c, tenant, ref="B")
        assert _status(c, tenant, a, "autorisert", 100,
                       middel=KORT_A) == "************9010"
        _status(c, tenant, b, "autorisert", 100, middel=KORT_A)
        # SAMME KORT, ANNEN SKRIVEMÅTE: samme hash for samme subjekt.
        _status(c, tenant, a, "gjennomfort", 100,
                middel=KORT_A_ANNEN_SKRIVEMATE, inntruffet="2026-08-21")
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    rader = migrator.execute(
        "SELECT s.ekstern_ref, h.betalingsmiddel_maske,"
        " h.betalingsmiddel_hash FROM betalingshendelse h"
        " JOIN betalingssubjekt s ON s.tenant=h.tenant"
        "  AND s.subjekt_id=h.subjekt_id"
        " WHERE h.tenant=%s ORDER BY s.ekstern_ref, h.inntruffet",
        (tenant,)).fetchall()
    migrator.rollback()
    assert len(rader) == 3
    a1, a2 = [r for r in rader if r[0] == "A"]
    b1, = [r for r in rader if r[0] == "B"]
    # SAMME SUBJEKT, SAMME KORT, TO SKRIVEMÅTER → samme hash.
    assert a1[2] == a2[2]
    # TO SUBJEKTER, SAMME KORT → FORSKJELLIG hash.
    assert a1[2] != b1[2]
    for _ref, maske, hash_ in rader:
        assert re.fullmatch(r"\*+[0-9A-Za-z]{4}", maske)
        assert re.fullmatch(r"[0-9a-f]{64}", hash_)
    # …og NUMMERET STÅR INGEN STEDER, heller ikke i revisjonsloggen.
    _sett_kontekst(migrator, tenant)
    for kilde, uttrykk in (
            ("betalingshendelse",
             "coalesce(betalingsmiddel_maske,'')"
             " || coalesce(betalingsmiddel_hash,'') || notat"
             " || kilde_ref"),
            ("revisjonslogg", "coalesce(handling,'')"
                              " || coalesce(begrunnelse::text,'')")):
        treff = migrator.execute(
            f"SELECT count(*) FROM public.{kilde}"
            f" WHERE tenant=%s AND ({uttrykk}) LIKE %s",
            (tenant, "%5678%")).fetchone()[0]
        assert treff == 0, kilde
    migrator.rollback()


@pg
def test_et_middelbytte_merkes_i_historikken(migrator):
    """`samme_betalingsmiddel` skal en dag kunne besvares — og det kan
    den bare hvis BYTTET er synlig."""
    tenant = _tenantnavn("middelbytte")
    c = _rt()
    try:
        _terskler(c, tenant)
        sid = _subjekt(c, tenant)
        _status(c, tenant, sid, "autorisert", 100, middel=KORT_A,
                inntruffet="2026-08-20")
        _status(c, tenant, sid, "gjennomfort", 100, middel=KORT_B,
                inntruffet="2026-08-21")
        _sett_kontekst(c, tenant)
        rader = c.execute(
            "SELECT betalingsmiddel_maske, middel_endret FROM"
            " m41_statushistorikken(%s,%s,200)", (tenant, sid)).fetchall()
        c.rollback()
    finally:
        c.close()
    assert rader[0] == ("************0987", True)
    assert rader[1] == ("************9010", False)


# ---------------------------------------------------------------------------
# INVARIANT 6: belop_i_flyttall
# ---------------------------------------------------------------------------

@pg
def test_invariant_belop_i_flyttall_i_katalogen(migrator):
    rader = migrator.execute(
        "SELECT table_name, column_name, data_type"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND column_name LIKE '%%ore%%' ORDER BY 1,2",
        (list(EGNE),)).fetchall()
    migrator.rollback()
    assert rader, "fant ingen beløpskolonner — porten måler ingenting"
    for tab, kol, typ in rader:
        assert typ == "bigint", f"{tab}.{kol} er {typ}, ikke bigint"
    flyt = migrator.execute(
        "SELECT table_name, column_name, data_type"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND data_type IN ('numeric','real','double precision')",
        (list(EGNE),)).fetchall()
    migrator.rollback()
    assert flyt == [], flyt


def test_invariant_belop_i_flyttall_over_api():
    from api.betaling import (MAKS_ORE, _bool, _heltall, _ore,
                              _ore_valgfritt, _valg)
    from api.policyadmin_http import _Avbrudd
    for verdi in (2.5, 100.0, True, False, "100", None, -1, MAKS_ORE):
        with pytest.raises(_Avbrudd):
            _ore({"b": verdi}, "b", "r")
    assert _ore({"b": 0}, "b", "r") == 0
    # DET FORVENTEDE BELØPET ER VALGFRITT — men aldri et flyttall.
    assert _ore_valgfritt({}, "f", "r") is None
    assert _ore_valgfritt({"f": None}, "f", "r") is None
    assert _ore_valgfritt({"f": 0}, "f", "r") == 0
    for verdi in (1.5, True, "100", -1):
        with pytest.raises(_Avbrudd):
            _ore_valgfritt({"f": verdi}, "f", "r")
    for verdi in (1.5, True, False, "3", None, -1, 1001):
        with pytest.raises(_Avbrudd):
            _heltall({"n": verdi}, "n", "r", 0, 1000)
    for verdi in (1, 0, "ja", None):
        with pytest.raises(_Avbrudd):
            _bool({"b": verdi}, "b", "r")
    # …og de lukkede settene er lukket.
    with pytest.raises(_Avbrudd):
        _valg({"s": "gjetning"}, "s", "r", ("a", "b"))
    assert _valg({"s": "a"}, "s", "r", ("a", "b")) == "a"


@pg
def test_avviket_regnes_i_heltall(migrator):
    """AVVIKET ER EN DIFFERANSE I ØRE, ikke en prosent. GRENSETILFELLET
    ER PORTEN: nøyaktig på grensen er ikke et funn, én øre over er det.

    MUTASJONEN SOM DREPER DENNE: bytt `>` mot `>=` i funndøren.
    """
    tenant = _tenantnavn("avvik")
    c = _rt()
    try:
        _terskler(c, tenant, avvik=200, uavklart=3650, reaut=3650)
        paa = _subjekt(c, tenant, ref="PAA")
        over = _subjekt(c, tenant, ref="OVER")
        stemmer = _subjekt(c, tenant, ref="STEMMER")
        _status(c, tenant, paa, "gjennomfort", 149700, forventet=149900)
        _status(c, tenant, over, "gjennomfort", 149699, forventet=149900)
        _status(c, tenant, stemmer, "gjennomfort", 149900,
                forventet=149900)
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_betaling_eier")
    funn = {}
    for rad in migrator.execute(
            "SELECT s.ekstern_ref, k.funntype, k.over_grense"
            "  FROM m41_funnkandidater(%s, current_date) k"
            "  JOIN betalingssubjekt s ON s.tenant=%s"
            "   AND s.subjekt_id=k.subjekt_id ORDER BY 1",
            (tenant, tenant)).fetchall():
        funn.setdefault(rad[0], []).append((rad[1], rad[2]))
    migrator.rollback()
    # NØYAKTIG PÅ GRENSEN (200 øre) er IKKE et funn.
    assert funn.get("PAA") is None, funn
    # ÉN ØRE OVER er det, og `over_grense` er 1.
    assert funn.get("OVER") == [("belopsavvik", 1)], funn
    assert funn.get("STEMMER") is None, funn


# ---------------------------------------------------------------------------
# INVARIANT 7: belopsgrense_hardkodet
# ---------------------------------------------------------------------------

def test_invariant_belopsgrense_hardkodet():
    for fil in MODULFILER:
        for m in re.finditer(
                r"^([A-Z_]*(?:GRENSE|TERSKEL|DOGN|AVVIK|BELOP)"
                r"[A-Z_]*)\s*=\s*(\d+)", _bare_kode(fil), re.M):
            assert m.group(1) in ("GRENSE", "MAKS_ORE",
                                  "MAKS_AVVIK_ORE"), \
                f"{fil.name} har terskelkonstanten {m.group(1)}"
    from drift import betalingssveip
    assert betalingssveip.GRENSE == 500
    kode = _bare_kode(MIGRASJON)
    assert "m41_sveip_betalinger(p_grense INT DEFAULT 500)" in kode
    assert "FROM public.betalingsterskel" in kode
    from api.betaling import terskler_endepunkt
    doc = terskler_endepunkt.__doc__ or ""
    assert "M-1" in doc and "gap" in doc.lower()


@pg
def test_tersklene_versjoneres_og_kan_ikke_slettes(migrator):
    tenant = _tenantnavn("terskelversjon")
    c = _rt()
    try:
        assert _terskler(c, tenant, uavklart=3) == 1
        assert _terskler(c, tenant, uavklart=5) == 2
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("DELETE FROM betalingsterskel WHERE tenant=%s",
                         (tenant,))
    assert "DELETE avvist" in str(ei.value)
    migrator.rollback()
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "UPDATE betalingsterskel SET uavklart_dogn=1"
            " WHERE tenant=%s", (tenant,))
    assert "versjonen må øke" in str(ei.value)
    migrator.rollback()


# ---------------------------------------------------------------------------
# Abonnementsperioden og funnene over tid
# ---------------------------------------------------------------------------

@pg
def test_abonnementsperioden_erstattes_og_overlapper_aldri(migrator):
    tenant = _tenantnavn("abonnement")
    c = _rt()
    try:
        _terskler(c, tenant)
        sid = _subjekt(c, tenant)
        _sett_kontekst(c, tenant)
        assert c.execute(
            "SELECT m41_sett_abonnementsstatus(%s,%s,'aktivt',"
            "'2026-01-01'::date,'startet','u')",
            (tenant, sid)).fetchone()[0] == 1
        c.commit()
        _sett_kontekst(c, tenant)
        assert c.execute(
            "SELECT m41_sett_abonnementsstatus(%s,%s,'i_restanse',"
            "'2026-07-01'::date,'ubetalt faktura','u')",
            (tenant, sid)).fetchone()[0] == 2
        c.commit()
        _sett_kontekst(c, tenant)
        for dato, fasit in (("2025-12-31", None), ("2026-06-30", "aktivt"),
                            ("2026-07-01", "i_restanse")):
            rad = c.execute(
                "SELECT status FROM m41_abonnement_paa_dato(%s,%s,"
                "%s::date)", (tenant, sid, dato)).fetchone()
            assert (rad[0] if rad else None) == fasit, (dato, rad)
        c.rollback()
        # EN PERIODE SKRIVES IKKE BAKOVER.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m41_sett_abonnementsstatus(%s,%s,'aktivt',"
                "'2026-03-01'::date,'x','u')", (tenant, sid))
        assert "skrives ikke bakover" in str(ei.value)
        c.rollback()
        # …og en periode uten begrunnelse er ingen beslutning.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m41_sett_abonnementsstatus(%s,%s,'aktivt',"
                "'2027-01-01'::date,NULL,'u')", (tenant, sid))
        assert "begrunnelse" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_betaling_eier")
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "INSERT INTO abonnementsperiode (tenant, subjekt_id,"
            " versjon, status, gyldig_fra, gyldig_til, begrunnelse,"
            " opprettet_av) VALUES (%s,%s,99,'aktivt','2026-05-01',"
            "'2026-08-01','x','u')", (tenant, sid))
    assert "overlapper" in str(ei.value)
    migrator.rollback()


@pg
def test_funnene_over_tid_og_sveipens_idempotens(migrator):
    """`uavklart_betaling` og `autorisasjon_utlopt`."""
    tenant = _tenantnavn("tid")
    c = _rt()
    try:
        _terskler(c, tenant, uavklart=3, reaut=7, avvik=0)
        sid = _subjekt(c, tenant)
        _status(c, tenant, sid, "autorisert", 149900,
                inntruffet="2026-08-20")
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_betaling_eier")
    kand = {r[0]: r[1] for r in migrator.execute(
        "SELECT funntype, over_grense FROM m41_funnkandidater("
        "%s,'2026-09-01'::date) ORDER BY 1", (tenant,)).fetchall()}
    migrator.rollback()
    # 2026-09-01 minus 2026-08-20 er 12 døgn, minus grensene 3 og 7.
    assert kand["uavklart_betaling"] == 9, kand
    assert kand["autorisasjon_utlopt"] == 5, kand

    with _sv() as v:
        _sveip(v)
    apne = sorted(f[0] for f in _funn(migrator, tenant) if f[4])
    assert apne == ["autorisasjon_utlopt", "uavklart_betaling"], apne
    with _sv() as v:
        rad = _sveip(v)
    assert rad[1] == 0, "sveip nummer to skrev nye rader"

    # EN GJENNOMFØRT BETALING LUKKER BEGGE — men radene blir stående.
    c = _rt()
    try:
        _status(c, tenant, sid, "gjennomfort", 149900,
                inntruffet="2026-08-25")
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert [f[0] for f in _funn(migrator, tenant) if f[4]] == []
    assert len(_funn(migrator, tenant)) == 2


@pg
def test_en_tenant_uten_grenser_er_et_funn(migrator):
    tenant = _tenantnavn("utenterskel")
    c = _rt()
    try:
        _subjekt(c, tenant)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert [f[0] for f in _funn(migrator, tenant)] == ["ingen_terskel"]


@pg
def test_et_deaktivert_subjekt_lukker_funnene_og_beholder_historikken(
        migrator):
    tenant = _tenantnavn("deaktiver")
    c = _rt()
    try:
        _terskler(c, tenant, uavklart=3)
        sid = _subjekt(c, tenant)
        _status(c, tenant, sid, "autorisert", 100,
                inntruffet="2026-08-01")
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert _funn(migrator, tenant)
    c = _rt()
    try:
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT m41_sett_subjektaktiv(%s,%s,false,'u')",
                         (tenant, sid)).fetchone()[0] is True
        c.commit()
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT m41_sett_subjektaktiv(%s,%s,false,'u')",
                         (tenant, sid)).fetchone()[0] is False
        c.commit()
        # ET DEAKTIVERT SUBJEKT TAR IKKE IMOT NYE STATUSER.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m41_registrer_status(%s,%s,%s,'gjennomfort',"
                "100,NULL,'NOK',NULL,'leverandor','evt_z',"
                "'2026-08-22','x','u')", (tenant, uuid.uuid4(), sid))
        assert "deaktivert" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    assert [f[0] for f in _funn(migrator, tenant) if f[4]] == []
    _sett_kontekst(migrator, tenant)
    n = migrator.execute(
        "SELECT count(*) FROM betalingshendelse WHERE tenant=%s",
        (tenant,)).fetchone()[0]
    migrator.rollback()
    assert n == 1
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("DELETE FROM betalingssubjekt WHERE tenant=%s",
                         (tenant,))
    assert "DELETE avvist" in str(ei.value)
    migrator.rollback()


@pg
def test_sveipen_refunderer_ingenting_og_rorer_ingen_hendelse(migrator):
    """Målt på VIRKELIGHETEN: en full sveip endrer ikke ett radantall
    utenfor registeret — OG DEN RØRER INGEN HENDELSE."""
    tenant = _tenantnavn("sveip")
    c = _rt()
    try:
        _terskler(c, tenant, uavklart=3)
        sid = _subjekt(c, tenant)
        _status(c, tenant, sid, "autorisert", 100,
                inntruffet="2026-08-01")
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    for_bok = migrator.execute(
        "SELECT hendelse_id, status, belop_ore FROM betalingshendelse"
        " WHERE tenant=%s ORDER BY hendelse_id", (tenant,)).fetchall()
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
        "SELECT hendelse_id, status, belop_ore FROM betalingshendelse"
        " WHERE tenant=%s ORDER BY hendelse_id", (tenant,)).fetchall()
    migrator.rollback()
    assert for_bok == etter_bok, "sveipen rørte historikken"


# ---------------------------------------------------------------------------
# INVARIANT 8: tenantlekkasje_i_betalingsregister
# ---------------------------------------------------------------------------

@pg
def test_invariant_tenantlekkasje_direkte(migrator):
    a = _tenantnavn("lekk-a")
    b = _tenantnavn("lekk-b")
    c = _rt()
    try:
        _terskler(c, a)
        _subjekt(c, a)
        _terskler(c, b)
        _sett_kontekst(c, b)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT * FROM m41_betalingsstatus(%s)", (a,))
        assert "tenantkontekst" in str(ei.value)
        c.rollback()
        _sett_kontekst(c, a)
        assert c.execute("SELECT * FROM m41_betalingsstatus(%s)",
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
        _subjekt(c, TENANT, ref="EGEN-REF")
        _terskler(c, fremmed)
        _subjekt(c, fremmed, ref="FREMMED-REF")
    finally:
        c.close()
    cookie, _csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/betaling", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    kropp = json.dumps(r.json())
    assert "EGEN-REF" in kropp
    assert "FREMMED-REF" not in kropp


@pg
def test_ingen_av_de_fem_tabellene_kan_tommes(migrator):
    tenant = _tenantnavn("truncate")
    c = _rt()
    try:
        _terskler(c, tenant, uavklart=3)
        sid = _subjekt(c, tenant)
        _status(c, tenant, sid, "autorisert", 100,
                inntruffet="2026-08-01")
        _sett_kontekst(c, tenant)
        c.execute("SELECT m41_sett_abonnementsstatus(%s,%s,'aktivt',"
                  "'2026-01-01'::date,'x','u')", (tenant, sid))
        c.commit()
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
        migrator.execute("TRUNCATE public.betalingssubjekt CASCADE")
    assert "TRUNCATE avvist" in str(ei.value), ei.value
    migrator.rollback()


def test_skrivedorene_som_leser_en_tilstand_laser_raden():
    sql = MIGRASJON.read_text(encoding="utf-8")
    for doer in ("m41_registrer_status", "m41_sett_abonnementsstatus",
                 "m41_sett_subjektaktiv"):
        i = sql.index(f"CREATE FUNCTION {doer}(")
        assert "FOR UPDATE" in sql[i:sql.index("END $$;", i)], doer


# ---------------------------------------------------------------------------
# Sveipearbeideren
# ---------------------------------------------------------------------------

@pg
def test_sveipen_nekter_kontekst(migrator):
    with _sv() as v:
        v.execute("SELECT set_config('disponit.tenant','t',false)")
        with pytest.raises(psycopg.Error) as ei:
            v.execute("SELECT * FROM m41_sveip_betalinger(500)")
        assert "KRYSS-TENANT" in str(ei.value)
        v.rollback()


@pg
def test_sveiperollen_har_ingen_tabellrettigheter(migrator):
    if not BETALINGSSVEIP_DSN:
        pytest.skip("DISPONIT_TEST_BETALINGSSVEIP_DSN ikke satt")
    rader = migrator.execute(
        "SELECT table_name, privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_betalingssveip'").fetchall()
    migrator.rollback()
    assert rader == [], rader


def test_sveipen_nekter_aa_starte_uten_egen_dsn(tmp_path, monkeypatch):
    from drift import kjor_betalingssveip
    monkeypatch.delenv("DISPONIT_BETALINGSSVEIP_URL", raising=False)
    monkeypatch.setenv("DISPONIT_BETALINGSSVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.setattr("db.hemmeligheter.last_credentials", lambda: None)
    assert kjor_betalingssveip.main() == 2


def test_arbeidernokkelen_er_modulens_egen():
    from drift import (betalingssveip, kontovaktsveip, lagersveip,
                       prisboksveip, prosjektsveip)
    nokler = [m.ARBEIDERNOKKEL for m in
              (kontovaktsveip, lagersveip, prisboksveip, prosjektsveip)]
    assert betalingssveip.ARBEIDERNOKKEL not in nokler


# ---------------------------------------------------------------------------
# INVARIANT 9: ui_axe_alvorlige_brudd
# ---------------------------------------------------------------------------

def test_invariant_ui_axe_bor_i_js_suiten():
    fil = (ROT / "platform" / "core" / "ui" / "test" / "betaling.test.js")
    assert fil.exists(), "betaling.test.js mangler"
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
        " ('https://m41.test', %s) RETURNING bruker_id",
        ("s41-" + secrets.token_hex(6),)).fetchone()[0]
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
@dekker("betaling_ulovlig_tilstand")
def test_http_status_i_framtida_er_409(migrator, klient):
    """FEILVEIEN `betaling_ulovlig_tilstand`, ende til ende.

    Kroppen ER velformet: beløpet er et heltall, kilden er fra det
    lukkede settet, notatet står der. Det er BASEN som sier at en
    betaling ikke kan inntreffe i framtida — sveipen måler mot i dag, og
    en framtidsdatert rad ville skjult statusskiftet.

    Dette er også testen `test_api_porter.test_hver_feilvei_har_en_test`
    krever for den nye feilveien.
    """
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _hpost(klient, cookie, csrf, "/v1/betaling/terskler",
               {"uavklart_dogn": 3, "belopsavvik_ore": 0,
                "reautorisasjon_dogn": 7})
    assert r.status_code in (200, 201), r.text
    ref = "H-" + secrets.token_hex(4)
    r = _hpost(klient, cookie, csrf, "/v1/betaling/subjekt",
               {"ekstern_ref": ref, "navn": "HTTP Kunde"})
    assert r.status_code in (200, 201), r.text
    sid = r.json()["subjekt_id"]
    # …og et felt som ALLTID er null står ikke i svaret.
    assert "ny" not in r.json()

    idem = secrets.token_urlsafe(24)
    kropp = {"status": "autorisert", "belop_ore": 149900,
             "forventet_ore": 149900, "valuta": "NOK",
             "betalingsmiddel": KORT_A, "kilde": "leverandor",
             "kilde_ref": "evt_h1", "inntruffet": "2026-08-20",
             "notat": "autorisert"}
    r = _hpost(klient, cookie, csrf, f"/v1/betaling/{sid}/status",
               kropp, idem=idem)
    assert r.status_code in (200, 201), r.text
    # SVARET ER MASKEN — aldri nummeret.
    assert r.json()["betalingsmiddel_maske"] == "************9010"
    assert "5678" not in json.dumps(r.json())
    # SP-2: SAMME NØKKEL GIR IKKE TO STATUSSKIFT.
    r2 = _hpost(klient, cookie, csrf, f"/v1/betaling/{sid}/status",
                kropp, idem=idem)
    assert r2.status_code == 409, r2.text

    # FRAMTIDIG DATO: TILSTANDEN sier nei.
    from datetime import date, timedelta
    i_morgen = (date.today() + timedelta(days=1)).isoformat()
    r = _hpost(klient, cookie, csrf, f"/v1/betaling/{sid}/status",
               {**kropp, "kilde_ref": "evt_h2",
                "inntruffet": i_morgen})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "betaling_ulovlig_tilstand"
    # SAMME REFERANSE TO GANGER: også en tilstand.
    r = _hpost(klient, cookie, csrf, "/v1/betaling/subjekt",
               {"ekstern_ref": ref, "navn": "Dublett"})
    assert r.status_code == 409, r.text
    # …og en ukjent status eller kilde er 400: KROPPEN er feil.
    for felt, verdi in (("status", "gjettet"), ("status", None),
                        ("kilde", "brevdue"), ("belop_ore", 1.5)):
        r = _hpost(klient, cookie, csrf, f"/v1/betaling/{sid}/status",
                   {**kropp, "kilde_ref": "evt_x", felt: verdi})
        assert r.status_code == 400, (felt, verdi, r.text)
    # `aktiv` ER PÅKREVD.
    r = _hpost(klient, cookie, csrf, f"/v1/betaling/{sid}/aktiv", {})
    assert r.status_code == 400, r.text

    # HISTORIKKEN ER BEVISET, og den bærer kilden.
    r = klient.get(f"/v1/betaling/{sid}/historikk",
                   cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    linjer = r.json()["hendelser"]
    assert len(linjer) == 1
    assert linjer[0]["kilde"] == "leverandor"
    assert linjer[0]["kilde_ref"] == "evt_h1"
    assert linjer[0]["betalingsmiddel_maske"] == "************9010"
    assert "5678" not in json.dumps(linjer)


@pg
def test_http_lesingen_bruker_okonomi_read(migrator, klient):
    from api.app import RUTESCOPE
    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert RUTESCOPE[("GET", "/v1/betaling")] == "okonomi:read"
    assert "okonomi:read" in ROLLE_TIL_SCOPES["admin"]
    for rolle in ("leser", "sikkerhet", "godkjenner"):
        assert "okonomi:read" not in ROLLE_TIL_SCOPES[rolle], rolle
    cookie, _c = _browserokt(migrator, ["leser"])
    r = klient.get("/v1/betaling", cookies={_C_SESJON(): cookie})
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# §0
# ---------------------------------------------------------------------------

def test_grensen_ble_registrert_for_koden():
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m41-v1"]
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["punktbinding"] == {}
    egen = Path(__file__).read_text(encoding="utf-8")
    for inv in g["invarianter"]:
        assert inv in egen, f"invarianten {inv} har ingen port"
