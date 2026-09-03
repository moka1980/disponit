"""M-27 lager- og logistikkagent v1 (migrasjon 109) — BEHOLDNINGEN.

Grensen `m27-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR koden
(§0-regelen), og hver invariant der har minst én port her.

DEN SKARPESTE PORTEN ER `beholdning_uten_bevegelse`. Det finnes ingen
kolonne noen kan sette; beholdningen ER summen av bevegelsene. En
lagerstatus som kunne skrives direkte ville gjort «hvorfor står det 7
her» til et spørsmål uten svar — og `lager_reservert` til en attestasjon
om et tall ingen kan spore.

DEN NEST SKARPESTE er `negativ_beholdning`. En negativ beholdning er
ikke en tilstand i verden; den er en måling som er feil, og et register
som tillot den ville rapportert usannhet med fullt alvor.

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

LAGERSVEIP_DSN = os.environ.get("DISPONIT_TEST_LAGERSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "109_m27_lagerregister.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "lager.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "lager.py",
    ROT / "platform" / "drift" / "lagersveip.py",
    ROT / "platform" / "drift" / "kjor_lagersveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("lagerterskel", "vare", "bestillingspunkt", "lagerbevegelse",
        "lagerfunn")

_STRENG = re.compile(
    r"'''.*?'''" r'|""".*?"""'
    r"|`(?:[^`\\]|\\.)*`"
    r"|'(?:[^'\\]|\\.|'')*'"
    r'|"(?:[^"\\]|\\.)*"', re.S)


def _bare_kode(fil: Path, *, uten_strenger: bool = False) -> str:
    """Filens innhold uten kommentarer OG uten docstrings (m24-formen).

    `uten_strenger` fjerner i tillegg hver strengliteral. Et ord som bare
    står i en feilmelding er PROSA; porten skal måle KODEN.
    """
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
    return koble(LAGERSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m27-{merke}-{secrets.token_hex(4)}"


def _terskler(c, tenant, *, stille=180, punkt=30, telle=365,
              aktor="u-test"):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m27_sett_terskler(%s,%s,%s,%s,%s)",
                  (tenant, stille, punkt, telle, aktor)).fetchone()[0]
    c.commit()
    return v


def _vare(c, tenant, *, kode=None, navn="Skrue", enhet="stk", vid=None,
          aktor="u-test"):
    vid = vid or uuid.uuid4()
    kode = kode or ("V-" + secrets.token_hex(4))
    _sett_kontekst(c, tenant)
    c.execute("SELECT m27_registrer_vare(%s,%s,%s,%s,%s,%s)",
              (tenant, vid, kode, navn, enhet, aktor))
    c.commit()
    return vid


def _punkt(c, tenant, vid, antall, fra, begrunnelse="satt",
           aktor="u-test"):
    _sett_kontekst(c, tenant)
    v = c.execute(
        "SELECT m27_sett_bestillingspunkt(%s,%s,%s,%s::date,%s,%s)",
        (tenant, vid, antall, fra, begrunnelse, aktor)).fetchone()[0]
    c.commit()
    return v


def _bevegelse(c, tenant, vid, type_, antall, *, kost=None,
               utfort="current_date", notat="x", aktor="u-test"):
    _sett_kontekst(c, tenant)
    ut = c.execute(
        f"SELECT m27_registrer_bevegelse(%s,%s,%s,%s,%s,%s,{utfort},"
        "%s,%s)",
        (tenant, uuid.uuid4(), vid, type_, antall, kost, notat,
         aktor)).fetchone()[0]
    c.commit()
    return ut


def _telling(c, tenant, vid, talt, *, utfort="current_date",
             notat="telt", aktor="u-test"):
    _sett_kontekst(c, tenant)
    ut = c.execute(
        f"SELECT m27_registrer_telling(%s,%s,%s,%s,{utfort},%s,%s)",
        (tenant, uuid.uuid4(), vid, talt, notat, aktor)).fetchone()[0]
    c.commit()
    return ut


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m27_sveip_lager(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT funntype, over_grense, beholdning, punktversjon, apen"
        "  FROM lagerfunn WHERE tenant=%s AND apen ORDER BY funntype",
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
# INVARIANT 1: beholdning_uten_bevegelse — MODULENS SKARPESTE
# ---------------------------------------------------------------------------

@pg
def test_invariant_beholdning_uten_bevegelse(migrator):
    """BEHOLDNINGEN ER IKKE ET FELT. Den er summen av bevegelsene.

    Målt tre veier: (1) skjemaet har ingen beholdningskolonne noen kan
    sette, (2) døren gir nøyaktig summen av hovedboken, og (3) det finnes
    ingen dør som SETTER et tall — heller ikke tellingen.

    MUTASJONEN SOM DREPER DENNE: legg en `beholdning`-kolonne på `vare`
    og la døren lese den.
    """
    # 1. SKJEMAET. `lagerfunn.beholdning` er UNNTATT og måles for seg:
    #    den er en ØYEBLIKKSKOPI sveipen skriver PÅ FUNNET, ikke en
    #    kilde noen leser beholdningen fra.
    rader = migrator.execute(
        "SELECT table_name, column_name"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND column_name IN ('beholdning','saldo','lagerbeholdning',"
        "                       'antall','pa_lager')"
        " ORDER BY 1,2", (list(EGNE),)).fetchall()
    migrator.rollback()
    assert rader == [("lagerfunn", "beholdning")], rader
    # …og INGEN LESEDØR HENTER BEHOLDNINGEN FRA `lagerfunn`.
    sql = MIGRASJON.read_text(encoding="utf-8")
    i = sql.index("CREATE FUNCTION m27_beholdning(")
    j = sql.index("END $$;", i)
    assert "lagerfunn" not in sql[i:j]
    assert "public.lagerbevegelse" in sql[i:j]

    tenant = _tenantnavn("sum")
    c = _rt()
    try:
        _terskler(c, tenant)
        vid = _vare(c, tenant)
        assert _bevegelse(c, tenant, vid, "mottak", 100, kost=1500) == 100
        assert _bevegelse(c, tenant, vid, "uttak", 60) == 40
        assert _bevegelse(c, tenant, vid, "retur", 5) == 45
        assert _bevegelse(c, tenant, vid, "svinn", 2) == 43
        # 2. DØREN ER SUMMEN, regnet på nytt fra hovedboken.
        _sett_kontekst(c, tenant)
        doer = c.execute("SELECT m27_beholdning(%s,%s)",
                         (tenant, vid)).fetchone()[0]
        c.rollback()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    sum_ = migrator.execute(
        "SELECT sum(endring)::bigint FROM lagerbevegelse"
        " WHERE tenant=%s AND vare_id=%s", (tenant, vid)).fetchone()[0]
    migrator.rollback()
    assert doer == sum_ == 43

    # 3. EN TELLING SETTER INGEN BEHOLDNING — den skriver DIFFERANSEN.
    c = _rt()
    try:
        assert _telling(c, tenant, vid, 40) == -3
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT m27_beholdning(%s,%s)",
                         (tenant, vid)).fetchone()[0] == 40
        c.rollback()
        # …og en telling som BEKREFTET tallet gir en linje med 0.
        assert _telling(c, tenant, vid, 40) == 0
        _sett_kontekst(c, tenant)
        n = c.execute(
            "SELECT count(*) FROM m27_bevegelsene(%s,%s,500)"
            " WHERE bevegelsestype='telling'", (tenant, vid)).fetchone()[0]
        c.rollback()
    finally:
        c.close()
    assert n == 2, "tellingen som ikke endret noe forsvant fra hovedboken"

    # …OG DET FINNES INGEN DØR SOM SETTER ET TALL.
    doerer = [r[0] for r in migrator.execute(
        "SELECT proname FROM pg_proc WHERE proname LIKE 'm27\\_%%'"
        " ORDER BY 1").fetchall()]
    migrator.rollback()
    assert doerer, "porten fant ingen dører — den måler ingenting"
    for navn in doerer:
        assert "sett_beholdning" not in navn, navn
        assert "juster_lager" not in navn, navn


@pg
def test_hovedboken_er_frosset(migrator):
    """EN BEVEGELSE ER FROSSET. Ingen UPDATE, ingen DELETE.

    To gjerder, målt hver for seg: eieren har ikke rettigheten i det hele
    tatt, og VAKTEN stanser den som likevel har den.

    MUTASJONEN SOM DREPER DENNE: gi `disponit_beholdning_eier` UPDATE på
    `lagerbevegelse`, eller fjern UPDATE-armen fra vakten.
    """
    tenant = _tenantnavn("frossen")
    c = _rt()
    try:
        _terskler(c, tenant)
        vid = _vare(c, tenant)
        _bevegelse(c, tenant, vid, "mottak", 10)
    finally:
        c.close()
    rettigheter = migrator.execute(
        "SELECT DISTINCT privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_beholdning_eier'"
        "   AND table_name='lagerbevegelse' ORDER BY 1").fetchall()
    migrator.rollback()
    assert [r[0] for r in rettigheter] == ["INSERT", "SELECT"]
    # VAKTEN, målt som eier av tabellen (migrator) — den som har
    # rettigheten møter likevel dommen.
    for sql, ord_ in (("UPDATE lagerbevegelse SET endring=1", "FROSSET"),
                      ("DELETE FROM lagerbevegelse", "DELETE avvist")):
        _sett_kontekst(migrator, tenant)
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(sql + " WHERE tenant=%s", (tenant,))
        assert ord_ in str(ei.value), sql
        migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 2: negativ_beholdning
# ---------------------------------------------------------------------------

@pg
def test_invariant_negativ_beholdning(migrator):
    """GRENSETILFELLET ER PORTEN: å ta ut ALT er lov, én til er det ikke.

    Og regelen står i VAKTEN, ikke bare i døren — en regel som bare
    fantes i døren ville vært borte i det øyeblikket noen skrev en
    INSERT for hånd, og beholdningen er summen av nettopp de radene.

    MUTASJONEN SOM DREPER DENNE: fjern summeringen fra
    `m27_bevegelse_vakt`.
    """
    tenant = _tenantnavn("negativ")
    c = _rt()
    try:
        _terskler(c, tenant)
        vid = _vare(c, tenant)
        _bevegelse(c, tenant, vid, "mottak", 40)
        # NØYAKTIG ALT: lovlig, og resultatet er null.
        assert _bevegelse(c, tenant, vid, "uttak", 40) == 0
        # ÉN TIL: ikke lovlig.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m27_registrer_bevegelse(%s,%s,%s,'uttak',"
                      "1,NULL,current_date,'for mye','u')",
                      (tenant, uuid.uuid4(), vid))
        assert "negativ" in str(ei.value)
        c.rollback()
        # …og en telling kan ikke telle negativt.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m27_registrer_telling(%s,%s,%s,-1,"
                      "current_date,'x','u')",
                      (tenant, uuid.uuid4(), vid))
        assert "negativ" in str(ei.value)
        c.rollback()
        # …og antallet er en STØRRELSE: et negativt antall er ingen
        # måling, og et fortegn kalleren snudde ville vært et uttak
        # forkledd som et mottak.
        for antall in (0, -5):
            _sett_kontekst(c, tenant)
            with pytest.raises(psycopg.Error) as ei:
                c.execute("SELECT m27_registrer_bevegelse(%s,%s,%s,"
                          "'mottak',%s,NULL,current_date,'x','u')",
                          (tenant, uuid.uuid4(), vid, antall))
            assert "STØRRELSE" in str(ei.value), antall
            c.rollback()
    finally:
        c.close()
    # VAKTEN, VED DIREKTE DML.
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_beholdning_eier")
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "INSERT INTO lagerbevegelse (tenant, bevegelse_id, vare_id,"
            " bevegelsestype, endring, utfort, notat, registrert_av)"
            " VALUES (%s,%s,%s,'uttak',-1,current_date,'x','u')",
            (tenant, uuid.uuid4(), vid))
    assert "negativ" in str(ei.value)
    migrator.rollback()
    # …og FORTEGNET FØLGER TYPEN, håndhevet av en CHECK. Målt med et
    # UTTAK SOM ØKER beholdningen: da rekker ikke negativvakten å svare
    # først, og det er CHECK-en alene som står igjen.
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_beholdning_eier")
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "INSERT INTO lagerbevegelse (tenant, bevegelse_id, vare_id,"
            " bevegelsestype, endring, utfort, notat, registrert_av)"
            " VALUES (%s,%s,%s,'uttak',5,current_date,'x','u')",
            (tenant, uuid.uuid4(), vid))
    assert "lagerbevegelse_fortegn" in str(ei.value)
    migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 3: under_bestillingspunkt_uten_funn
# ---------------------------------------------------------------------------

@pg
def test_invariant_under_bestillingspunkt_uten_funn(migrator):
    """PÅ PUNKTET ER ET FUNN. ÉN OVER ER DET IKKE.

    Et bestillingspunkt er punktet der noen SKAL bestille, ikke punktet
    der det er for sent — og grensetilfellet er hele forskjellen.

    MUTASJONEN SOM DREPER DENNE: bytt `<=` mot `<` i
    `m27_under_bestillingspunkt` eller i funndøren.
    """
    tenant = _tenantnavn("punkt")
    c = _rt()
    try:
        _terskler(c, tenant, punkt=3650, stille=3650, telle=3650)
        paa = _vare(c, tenant, kode="PAA")
        over = _vare(c, tenant, kode="OVER")
        under = _vare(c, tenant, kode="UNDER")
        for vid, beholdning in ((paa, 50), (over, 51), (under, 10)):
            _punkt(c, tenant, vid, 50, "2026-01-01")
            _bevegelse(c, tenant, vid, "mottak", beholdning)
        _sett_kontekst(c, tenant)
        for vid, fasit in ((paa, True), (over, False), (under, True)):
            ut = c.execute(
                "SELECT m27_under_bestillingspunkt(%s,%s,current_date)",
                (tenant, vid)).fetchone()[0]
            assert ut is fasit, (vid, ut)
        c.rollback()
        # UTEN PUNKT GIR `NULL`, ikke `false`: «vi er over punktet» om en
        # vare som ikke HAR et punkt ville vært en dom uten grunnlag.
        upunkt = _vare(c, tenant, kode="INTET")
        _sett_kontekst(c, tenant)
        assert c.execute(
            "SELECT m27_under_bestillingspunkt(%s,%s,current_date)",
            (tenant, upunkt)).fetchone()[0] is None
        c.rollback()
        assert upunkt
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    funn = {}
    _sett_kontekst(migrator, tenant)
    for rad in migrator.execute(
            "SELECT v.kode, f.funntype, f.over_grense, f.beholdning"
            "  FROM lagerfunn f JOIN vare v ON v.tenant=f.tenant"
            "   AND v.vare_id=f.vare_id"
            " WHERE f.tenant=%s AND f.apen ORDER BY 1,2",
            (tenant,)).fetchall():
        funn.setdefault(rad[0], []).append(rad[1:])
    migrator.rollback()
    assert ("under_bestillingspunkt", 0, 50) in funn.get("PAA", []), funn
    assert ("under_bestillingspunkt", 40, 10) in funn.get("UNDER", []), funn
    assert not [f for f in funn.get("OVER", [])
                if f[0] == "under_bestillingspunkt"], funn
    # Varen UTEN punkt får IKKE `under_bestillingspunkt` — den mangler
    # grunnlaget for dommen. Den får sitt eget funn, og det måles i
    # `test_en_framtidig_punktversjon_...` under, der måledagen er langt
    # nok fram til at tenantens grense er passert.
    assert not [f for f in funn.get("INTET", [])
                if f[0] == "under_bestillingspunkt"], funn


@pg
def test_en_framtidig_punktversjon_skjuler_ikke_at_varen_mangler_punkt(
        migrator):
    """En vare med BARE et framtidig punkt har ikke noe punkt NÅ.

    Formen `coalesce(gyldig_til, p_dag)` over ALLE punktradene ville gitt
    «hadde punkt i dag» for et punkt som begynner neste år, og funnet
    ville vært stille så lenge noen hadde ført et framtidig punkt — altså
    det motsatte av det porten lover. Det er lærdommen fra 108
    (CodeRabbit), og `FILTER (WHERE gyldig_fra <= p_dag)` er svaret.

    MÅLT PÅ FUNNDØREN MED EN FRAMTIDIG MÅLEDAG, fordi det er den eneste
    måten å komme forbi at varen er opprettet i dag.
    """
    tenant = _tenantnavn("framtid")
    c = _rt()
    try:
        _terskler(c, tenant, punkt=30, stille=3650, telle=3650)
        vid = _vare(c, tenant)
        _sett_kontekst(c, tenant)
        # ENESTE PUNKT, og det begynner om 100 døgn.
        c.execute("SELECT m27_sett_bestillingspunkt(%s,%s,50,"
                  "current_date + 100,'framtidig','u')", (tenant, vid))
        c.commit()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_beholdning_eier")
    rader = migrator.execute(
        "SELECT funntype, over_grense"
        "  FROM m27_funnkandidater(%s, current_date + 60)"
        " ORDER BY funntype", (tenant,)).fetchall()
    migrator.rollback()
    assert [r[0] for r in rader] == ["uten_bestillingspunkt"], rader
    # 60 døgn siden opprettelsen, minus tenantens grense på 30.
    assert rader[0][1] == 30, rader


@pg
def test_funnene_lukkes_og_sveipen_er_idempotent(migrator):
    tenant = _tenantnavn("lukking")
    c = _rt()
    try:
        _terskler(c, tenant, punkt=3650, stille=3650, telle=3650)
        vid = _vare(c, tenant)
        _punkt(c, tenant, vid, 50, "2026-01-01")
        _bevegelse(c, tenant, vid, "mottak", 10)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert [r[0] for r in _funn(migrator, tenant)] \
        == ["under_bestillingspunkt"]
    with _sv() as v:
        rad = _sveip(v)
    assert rad[1] == 0, "sveip nummer to skrev nye rader"
    # PÅFYLL LUKKER FUNNET — men raden blir stående.
    c = _rt()
    try:
        _bevegelse(c, tenant, vid, "mottak", 100)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert _funn(migrator, tenant) == []
    _sett_kontekst(migrator, tenant)
    n = migrator.execute(
        "SELECT count(*) FROM lagerfunn WHERE tenant=%s AND NOT apen"
        " AND lukket_ts IS NOT NULL", (tenant,)).fetchone()[0]
    migrator.rollback()
    assert n == 1, "funnet ble slettet i stedet for lukket"


@pg
def test_en_tenant_uten_grenser_er_et_funn(migrator):
    tenant = _tenantnavn("utenterskel")
    c = _rt()
    try:
        _vare(c, tenant)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert [r[0] for r in _funn(migrator, tenant)] == ["ingen_terskel"]


@pg
def test_et_deaktivert_vare_lukker_funnene_og_beholder_hovedboken(
        migrator):
    tenant = _tenantnavn("deaktiver")
    c = _rt()
    try:
        _terskler(c, tenant, punkt=3650, stille=3650, telle=3650)
        vid = _vare(c, tenant)
        _punkt(c, tenant, vid, 50, "2026-01-01")
        _bevegelse(c, tenant, vid, "mottak", 10)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert _funn(migrator, tenant)
    c = _rt()
    try:
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT m27_sett_vareaktiv(%s,%s,false,'u')",
                         (tenant, vid)).fetchone()[0] is True
        c.commit()
        # …og en gang til er et STILLE JA.
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT m27_sett_vareaktiv(%s,%s,false,'u')",
                         (tenant, vid)).fetchone()[0] is False
        c.commit()
        # EN DEAKTIVERT VARE TAR IKKE IMOT BEVEGELSER.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m27_registrer_bevegelse(%s,%s,%s,'mottak',"
                      "1,NULL,current_date,'x','u')",
                      (tenant, uuid.uuid4(), vid))
        assert "deaktivert" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    assert _funn(migrator, tenant) == []
    # HOVEDBOKEN BESTÅR.
    _sett_kontekst(migrator, tenant)
    n = migrator.execute(
        "SELECT count(*) FROM lagerbevegelse WHERE tenant=%s",
        (tenant,)).fetchone()[0]
    migrator.rollback()
    assert n == 1
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("DELETE FROM vare WHERE tenant=%s", (tenant,))
    assert "DELETE avvist" in str(ei.value)
    migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 4: bestillingspunkt_hardkodet
# ---------------------------------------------------------------------------

def test_invariant_bestillingspunkt_hardkodet():
    for fil in MODULFILER:
        for m in re.finditer(
                r"^([A-Z_]*(?:GRENSE|TERSKEL|PUNKT|STILLE|DOGN|INTERVALL)"
                r"[A-Z_]*)\s*=\s*(\d+)", _bare_kode(fil), re.M):
            assert m.group(1) in ("GRENSE",), \
                f"{fil.name} har terskelkonstanten {m.group(1)}"
    from drift import lagersveip
    assert lagersveip.GRENSE == 500
    kode = _bare_kode(MIGRASJON)
    assert "m27_sveip_lager(p_grense INT DEFAULT 500)" in kode
    assert "FROM public.lagerterskel" in kode
    from api.lager import terskler_endepunkt
    doc = terskler_endepunkt.__doc__ or ""
    assert "M-1" in doc and "gap" in doc.lower()


@pg
def test_punktet_er_versjonert_og_frosset(migrator):
    """«Hva var punktet den dagen» er det som gjør et eldre funn
    etterprøvbart."""
    tenant = _tenantnavn("punktversjon")
    c = _rt()
    try:
        _terskler(c, tenant)
        vid = _vare(c, tenant)
        assert _punkt(c, tenant, vid, 50, "2026-01-01") == 1
        assert _punkt(c, tenant, vid, 80, "2026-07-01") == 2
        _sett_kontekst(c, tenant)
        for dato, fasit in (("2025-12-31", None), ("2026-06-30", 50),
                            ("2026-07-01", 80), ("2030-01-01", 80)):
            rad = c.execute(
                "SELECT punkt_antall FROM m27_punkt_paa_dato(%s,%s,"
                "%s::date)", (tenant, vid, dato)).fetchone()
            assert (rad[0] if rad else None) == fasit, (dato, rad)
        c.rollback()
        # ET PUNKT SKRIVES IKKE BAKOVER.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m27_sett_bestillingspunkt(%s,%s,1,"
                      "'2026-03-01'::date,'x','u')", (tenant, vid))
        assert "skrives ikke bakover" in str(ei.value)
        c.rollback()
        # …og et punkt uten begrunnelse er ingen beslutning.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m27_sett_bestillingspunkt(%s,%s,1,"
                      "'2027-01-01'::date,NULL,'u')", (tenant, vid))
        assert "begrunnelse" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    # DIREKTE DML: PUNKTET ER FROSSET.
    for kolonne, verdi in (("punkt_antall", "1"),
                           ("begrunnelse", "'noe annet'"),
                           ("gyldig_fra", "'2099-01-01'::date"),
                           ("opprettet_av", "'noen andre'")):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_beholdning_eier")
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(
                f"UPDATE bestillingspunkt SET {kolonne} = {verdi}"
                " WHERE tenant=%s AND versjon=2", (tenant,))
        assert "FROSSET" in str(ei.value), kolonne
        migrator.rollback()
    # …OG TO VERSJONER OVERLAPPER ALDRI.
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_beholdning_eier")
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "INSERT INTO bestillingspunkt (tenant, vare_id, versjon,"
            " punkt_antall, gyldig_fra, gyldig_til, begrunnelse,"
            " opprettet_av) VALUES (%s,%s,99,1,'2026-05-01',"
            "'2026-08-01','x','u')", (tenant, vid))
    assert "overlapper" in str(ei.value)
    migrator.rollback()


@pg
def test_tersklene_versjoneres_og_kan_ikke_slettes(migrator):
    tenant = _tenantnavn("terskelversjon")
    c = _rt()
    try:
        assert _terskler(c, tenant, stille=180) == 1
        assert _terskler(c, tenant, stille=90) == 2
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("DELETE FROM lagerterskel WHERE tenant=%s",
                         (tenant,))
    assert "DELETE avvist" in str(ei.value)
    migrator.rollback()
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "UPDATE lagerterskel SET stille_dogn=1 WHERE tenant=%s",
            (tenant,))
    assert "versjonen må øke" in str(ei.value)
    migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 5: modulen_bestilte / modulen_beregnet_prognose /
#              modulen_signerte_attestasjon
# ---------------------------------------------------------------------------

def test_invariant_modulen_bestilte_ingenting_og_beregnet_ingen_prognose():
    """Målt på IMPORTENE (AST), på KODEN og på RUTENE.

    `bestill(?!ingspunkt)`: «bestillingspunkt» er GRENSEN som utløser
    funnet — «bestill» alene ville vært handlingen v1 ikke gjør.
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
                    "decimal", "statistics", "math", "hmac",
                    "cryptography", "smtplib", "httpx"}, \
                    f"{fil.name} importerer {n} — v1 regner ingen prognose"
    for fil in (MIGRASJON, FLATE, *MODULFILER):
        uten = _bare_kode(fil, uten_strenger=True).lower()
        for ord_ in ("attestasjon", "signatur", r"bestill(?!ingspunkt)",
                     "prognose", "forbruksrate", "glidende",
                     "ekstrapol", "stddev", "regr_", "m24_",
                     "innkjopsordre"):
            assert not re.search(ord_, uten), \
                f"{fil.name} bærer «{ord_}» — v1 bestiller ingenting og" \
                " beregner ingen prognose"

    from api.app import RUTESCOPE
    mine = sorted(sti for _m, sti in RUTESCOPE
                  if sti.startswith("/v1/lager"))
    assert mine == [
        "/v1/lager",
        "/v1/lager/terskler",
        "/v1/lager/vare",
        "/v1/lager/{vare_id:uuid}/aktiv",
        "/v1/lager/{vare_id:uuid}/bevegelse",
        "/v1/lager/{vare_id:uuid}/bevegelser",
        "/v1/lager/{vare_id:uuid}/paa-dato",
        "/v1/lager/{vare_id:uuid}/punkt",
        "/v1/lager/{vare_id:uuid}/telling",
    ], mine


@pg
def test_sveipen_bestiller_ingenting_og_rorer_ingen_bevegelse(migrator):
    """Målt på VIRKELIGHETEN: en full sveip endrer ikke ett radantall
    utenfor registeret — OG DEN RØRER INGEN HOVEDBOKSLINJE, selv om den
    vet nøyaktig hvor mye som mangler.

    MUTASJONEN SOM DREPER DENNE: la sveipen føre et mottak på varene
    under punktet.
    """
    tenant = _tenantnavn("sveip")
    c = _rt()
    try:
        _terskler(c, tenant, punkt=3650, stille=3650, telle=3650)
        vid = _vare(c, tenant)
        _punkt(c, tenant, vid, 50, "2026-01-01")
        _bevegelse(c, tenant, vid, "mottak", 10)
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    for_bok = migrator.execute(
        "SELECT bevegelse_id, bevegelsestype, endring FROM lagerbevegelse"
        " WHERE tenant=%s ORDER BY bevegelse_id", (tenant,)).fetchall()
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
        "SELECT bevegelse_id, bevegelsestype, endring FROM lagerbevegelse"
        " WHERE tenant=%s ORDER BY bevegelse_id", (tenant,)).fetchall()
    migrator.rollback()
    assert for_bok == etter_bok, "sveipen rørte hovedboken"


# ---------------------------------------------------------------------------
# INVARIANT 6: belop_i_flyttall
# ---------------------------------------------------------------------------

@pg
def test_invariant_belop_i_flyttall_i_katalogen(migrator):
    rader = migrator.execute(
        "SELECT table_name, column_name, data_type"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND (column_name LIKE '%%ore%%' OR column_name LIKE"
        "        '%%antall%%' OR column_name = 'endring')"
        " ORDER BY 1,2", (list(EGNE),)).fetchall()
    migrator.rollback()
    assert rader, "fant ingen tallkolonner — porten måler ingenting"
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
    from api.lager import (MAKS_ANTALL, MAKS_ORE, _antall, _bool,
                           _heltall, _ore_valgfritt)
    from api.policyadmin_http import _Avbrudd
    for verdi in (2.5, 100.0, True, False, "100", None, 0, -1,
                  MAKS_ANTALL + 1):
        with pytest.raises(_Avbrudd):
            _antall({"n": verdi}, "n", "r")
    assert _antall({"n": 1}, "n", "r") == 1
    assert _antall({"n": 0}, "n", "r", minst=0) == 0
    # ENHETSKOSTEN ER VALGFRI — men aldri et flyttall.
    assert _ore_valgfritt({}, "k", "r") is None
    assert _ore_valgfritt({"k": None}, "k", "r") is None
    assert _ore_valgfritt({"k": 0}, "k", "r") == 0
    for verdi in (1.5, True, "100", -1, MAKS_ORE):
        with pytest.raises(_Avbrudd):
            _ore_valgfritt({"k": verdi}, "k", "r")
    for verdi in (1.5, True, False, "3", None, -1, 1001):
        with pytest.raises(_Avbrudd):
            _heltall({"n": verdi}, "n", "r", 0, 1000)
    # …og en boolsk verdi må VÆRE boolsk: `1` er ikke `true`.
    for verdi in (1, 0, "ja", None):
        with pytest.raises(_Avbrudd):
            _bool({"b": verdi}, "b", "r")
    assert _bool({"b": True}, "b", "r") is True
    assert _bool({}, "b", "r") is False


def test_teksten_lagres_trimmet():
    from api.lager import _tekst
    from api.policyadmin_http import _Avbrudd
    assert _tekst({"k": "  V-100  "}, "k", "r", 10) == "V-100"
    for verdi in ("", "   ", None, 5, True, ["V"]):
        with pytest.raises(_Avbrudd):
            _tekst({"k": verdi}, "k", "r", 10)
    # LENGDEN MÅLES PÅ DET SOM LAGRES.
    assert _tekst({"k": "  V-100  "}, "k", "r", 5) == "V-100"
    with pytest.raises(_Avbrudd):
        _tekst({"k": "V-1000"}, "k", "r", 5)


# ---------------------------------------------------------------------------
# INVARIANT 7: tenantlekkasje_i_lagerregister
# ---------------------------------------------------------------------------

@pg
def test_invariant_tenantlekkasje_direkte(migrator):
    a = _tenantnavn("lekk-a")
    b = _tenantnavn("lekk-b")
    c = _rt()
    try:
        _terskler(c, a)
        _vare(c, a)
        _terskler(c, b)
        _sett_kontekst(c, b)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT * FROM m27_lagerstatus(%s)", (a,))
        assert "tenantkontekst" in str(ei.value)
        c.rollback()
        _sett_kontekst(c, a)
        assert c.execute("SELECT * FROM m27_lagerstatus(%s)",
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
        _vare(c, TENANT, kode="EGEN-KODE")
        _terskler(c, fremmed)
        _vare(c, fremmed, kode="FREMMED-KODE")
    finally:
        c.close()
    cookie, _csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/lager", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    kropp = json.dumps(r.json())
    assert "EGEN-KODE" in kropp
    assert "FREMMED-KODE" not in kropp


@pg
def test_ingen_av_de_fem_tabellene_kan_tommes(migrator):
    tenant = _tenantnavn("truncate")
    c = _rt()
    try:
        _terskler(c, tenant, punkt=3650, stille=3650, telle=3650)
        vid = _vare(c, tenant)
        _punkt(c, tenant, vid, 50, "2026-01-01")
        _bevegelse(c, tenant, vid, "mottak", 10)
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
        migrator.execute("TRUNCATE public.vare CASCADE")
    assert "TRUNCATE avvist" in str(ei.value), ei.value
    migrator.rollback()


def test_skrivedorene_som_leser_en_tilstand_laser_raden():
    sql = MIGRASJON.read_text(encoding="utf-8")
    for doer in ("m27_sett_bestillingspunkt", "m27_registrer_bevegelse",
                 "m27_registrer_telling", "m27_sett_vareaktiv"):
        i = sql.index(f"CREATE FUNCTION {doer}(")
        assert "FOR UPDATE" in sql[i:sql.index("END $$;", i)], doer
    # …OG VAKTEN LÅSER OGSÅ: to samtidige uttak skal ikke kunne lese
    # samme beholdning og begge komme til at det er nok igjen.
    i = sql.index("CREATE FUNCTION m27_bevegelse_vakt(")
    assert "FOR UPDATE" in sql[i:sql.index("END $$;", i)]


# ---------------------------------------------------------------------------
# Sveipearbeideren
# ---------------------------------------------------------------------------

@pg
def test_sveipen_nekter_kontekst(migrator):
    with _sv() as v:
        v.execute("SELECT set_config('disponit.tenant','t',false)")
        with pytest.raises(psycopg.Error) as ei:
            v.execute("SELECT * FROM m27_sveip_lager(500)")
        assert "KRYSS-TENANT" in str(ei.value)
        v.rollback()


@pg
def test_sveiperollen_har_ingen_tabellrettigheter(migrator):
    if not LAGERSVEIP_DSN:
        pytest.skip("DISPONIT_TEST_LAGERSVEIP_DSN ikke satt")
    rader = migrator.execute(
        "SELECT table_name, privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_lagersveip'").fetchall()
    migrator.rollback()
    assert rader == [], rader


def test_sveipen_nekter_aa_starte_uten_egen_dsn(tmp_path, monkeypatch):
    from drift import kjor_lagersveip
    monkeypatch.delenv("DISPONIT_LAGERSVEIP_URL", raising=False)
    monkeypatch.setenv("DISPONIT_LAGERSVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.setattr("db.hemmeligheter.last_credentials", lambda: None)
    assert kjor_lagersveip.main() == 2


class _FalskConn:
    """En tilkobling som gir sveipen en rad som IKKE er kontrakten."""

    def __init__(self, rader):
        self.rader = rader
        # REKKEFØLGEN ER DET SOM MÅLES. `finally`-blokken låser opp og
        # committer den opplåsingen uansett, så «committet noe gang» er
        # ikke porten — «rullet tilbake FØR første commit» er det.
        self.logg = []

    def execute(self, sql, args=None):
        return self

    def fetchone(self):
        return (True,)          # advisory-låsen

    def fetchall(self):
        return self.rader

    def commit(self):
        self.logg.append("commit")

    def rollback(self):
        self.logg.append("rollback")


def test_en_rad_som_ikke_er_kontrakten_rulles_tilbake():
    """KONTRAKTEN VALIDERES HELT UT FØR COMMIT.

    Fem felt som lar seg lese som heltall — ikke fire, ikke en NULL. En
    rad som ikke er kontrakten skal rulle TILBAKE, ikke bli stående mens
    kjøringen rapporterer feilet (CodeRabbit, 109).

    MUTASJONEN SOM DREPER DENNE: flytt heltallskonverteringen tilbake til
    etter `conn.commit()`.
    """
    from drift import lagersveip
    for rader in ([(1, 2, 3, 4)], [(1, 2, 3, 4, None)],
                  [(1, 2, 3, 4, "fem")], [], [(1,) * 5, (1,) * 5]):
        # (En rad med SEKS felt er derimot gyldig — sveipen leser fem,
        #  og den delte kontraktporten mater alle sveipene et supersett.)
        conn = _FalskConn(rader)
        res = lagersveip.kjor(conn, tidligere_feil=1)
        assert res.feilet is True, rader
        assert res.alarm_utlost is True, rader
        assert conn.logg[0] == "rollback", (rader, conn.logg)
    # …og den gyldige raden går gjennom, uten rollback.
    conn = _FalskConn([(2, 3, 4, 5, 6)])
    res = lagersveip.kjor(conn)
    assert (res.feilet, res.tenanter, res.nye, res.oppdaterte,
            res.lukkede, res.avkortet) == (False, 2, 3, 4, 5, 6)
    assert "rollback" not in conn.logg, conn.logg
    assert conn.logg[0] == "commit"
    # …og et SUPERSETT er også gyldig: sveipen leser de fem første.
    conn = _FalskConn([(2, 3, 4, 5, 6, 7)])
    res = lagersveip.kjor(conn)
    assert res.feilet is False and res.avkortet == 6
    assert "rollback" not in conn.logg, conn.logg


def test_arbeidernokkelen_er_modulens_egen():
    """To sveip som låser på samme nøkkel ville blokkert hverandre."""
    from drift import (avstemmingssveip, fordringssveip, lagersveip,
                       leverandorsveip, prisboksveip, prosjektsveip)
    nokler = [m.ARBEIDERNOKKEL for m in
              (avstemmingssveip, fordringssveip, leverandorsveip,
               prisboksveip, prosjektsveip)]
    assert lagersveip.ARBEIDERNOKKEL not in nokler


# ---------------------------------------------------------------------------
# INVARIANT 8: ui_axe_alvorlige_brudd
# ---------------------------------------------------------------------------

def test_invariant_ui_axe_bor_i_js_suiten():
    fil = (ROT / "platform" / "core" / "ui" / "test" / "lager.test.js")
    assert fil.exists(), "lager.test.js mangler"
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
        " ('https://m27.test', %s) RETURNING bruker_id",
        ("s27-" + secrets.token_hex(6),)).fetchone()[0]
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
@dekker("lager_ulovlig_tilstand")
def test_http_beholdningen_kan_ikke_bli_negativ_er_409(migrator, klient):
    """FEILVEIEN `lager_ulovlig_tilstand`, ende til ende.

    Kroppen ER velformet: antallet er et heltall, datoen er lesbar,
    notatet står der. Det er BASEN som sier at beholdningen ikke kan bli
    negativ — en negativ beholdning er ikke en tilstand i verden, den er
    en måling som er feil.

    Dette er også testen `test_api_porter.test_hver_feilvei_har_en_test`
    krever for den nye feilveien.
    """
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _hpost(klient, cookie, csrf, "/v1/lager/terskler",
               {"stille_dogn": 180, "uten_punkt_dogn": 30,
                "telleintervall_dogn": 365})
    assert r.status_code in (200, 201), r.text
    kode = "H-" + secrets.token_hex(4)
    r = _hpost(klient, cookie, csrf, "/v1/lager/vare",
               {"kode": kode, "navn": "HTTP-skrue", "enhet": "stk"})
    assert r.status_code in (200, 201), r.text
    vid = r.json()["vare_id"]
    r = _hpost(klient, cookie, csrf, f"/v1/lager/{vid}/bevegelse",
               {"bevegelsestype": "mottak", "antall": 10,
                "enhetskost_ore": 1500, "utfort": "2026-08-01",
                "notat": "palle"})
    assert r.status_code in (200, 201), r.text
    assert r.json()["beholdning"] == 10
    # FOR STORT UTTAK: TILSTANDEN sier nei.
    r = _hpost(klient, cookie, csrf, f"/v1/lager/{vid}/bevegelse",
               {"bevegelsestype": "uttak", "antall": 11,
                "utfort": "2026-08-02", "notat": "for mye"})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "lager_ulovlig_tilstand"
    # SAMME KODE TO GANGER: også en tilstand.
    r = _hpost(klient, cookie, csrf, "/v1/lager/vare",
               {"kode": kode, "navn": "Dublett", "enhet": "stk"})
    assert r.status_code == 409, r.text
    # …og et flyttall er 400: KROPPEN er feil.
    r = _hpost(klient, cookie, csrf, f"/v1/lager/{vid}/bevegelse",
               {"bevegelsestype": "mottak", "antall": 1.5,
                "utfort": "2026-08-02", "notat": "x"})
    assert r.status_code == 400, r.text
    # …og en ukjent bevegelsestype likeså — settet er lukket, og
    # `telling` hører IKKE hjemme her.
    for type_ in ("telling", "justering", None):
        r = _hpost(klient, cookie, csrf, f"/v1/lager/{vid}/bevegelse",
                   {"bevegelsestype": type_, "antall": 1,
                    "utfort": "2026-08-02", "notat": "x"})
        assert r.status_code == 400, (type_, r.text)
    # `aktiv` ER PÅKREVD: en utelatelse skal ikke deaktivere varen.
    r = _hpost(klient, cookie, csrf, f"/v1/lager/{vid}/aktiv", {})
    assert r.status_code == 400, r.text

    # SP-2: SAMME NØKKEL GIR IKKE TO LINJER I HOVEDBOKEN.
    idem = secrets.token_urlsafe(24)
    kropp = {"bevegelsestype": "mottak", "antall": 5,
             "utfort": "2026-08-03", "notat": "gjentatt"}
    r1 = _hpost(klient, cookie, csrf, f"/v1/lager/{vid}/bevegelse",
                kropp, idem=idem)
    assert r1.status_code in (200, 201), r1.text
    r2 = _hpost(klient, cookie, csrf, f"/v1/lager/{vid}/bevegelse",
                kropp, idem=idem)
    assert r2.status_code == 409, r2.text
    r = klient.get(f"/v1/lager/{vid}/bevegelser",
                   cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    linjer = r.json()["bevegelser"]
    assert len([b for b in linjer if b["notat"] == "gjentatt"]) == 1
    # HOVEDBOKEN BÆRER DEN LØPENDE BEHOLDNINGEN — svaret på «hvorfor
    # står det 15 her».
    assert linjer[0]["beholdning_etter"] == 15

    # OPPSLAGET SOM BETYR NOE: hva sto på lager den dagen.
    r = klient.get(f"/v1/lager/{vid}/paa-dato?dato=2026-08-01",
                   cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    assert r.json()["beholdning"] == 10
    # …og `punkt` er `null` når varen ikke hadde et punkt da.
    assert r.json()["punkt"] is None


@pg
def test_http_lesingen_bruker_okonomi_read(migrator, klient):
    from api.app import RUTESCOPE
    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert RUTESCOPE[("GET", "/v1/lager")] == "okonomi:read"
    assert "okonomi:read" in ROLLE_TIL_SCOPES["admin"]
    for rolle in ("leser", "sikkerhet", "godkjenner"):
        assert "okonomi:read" not in ROLLE_TIL_SCOPES[rolle], rolle
    cookie, _c = _browserokt(migrator, ["leser"])
    r = klient.get("/v1/lager", cookies={_C_SESJON(): cookie})
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# §0
# ---------------------------------------------------------------------------

def test_grensen_ble_registrert_for_koden():
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m27-v1"]
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["punktbinding"] == {}
    egen = Path(__file__).read_text(encoding="utf-8")
    for inv in g["invarianter"]:
        assert inv in egen, f"invarianten {inv} har ingen port"
