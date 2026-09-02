"""M-26 prisbok- og tilbudsagent v1 (migrasjon 108) — PRISBOKA.

Grensen `m26-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR koden
(§0-regelen), og hver invariant der har minst én port her.

DEN SKARPESTE PORTEN ER `pris_uten_versjon`. `priser_fra_prisbok` er en
attestasjon om at et tilbud siterte boka, og den er verdiløs hvis ingen
kan svare på HVA SOM STO DER DA. En pris som kunne skrives om i ettertid
gjør hvert tilbud som siterte den til en gjetning.

DEN NEST SKARPESTE er at TO VERSJONER ALDRI OVERLAPPER. Da ville
«hvilken pris gjaldt den dagen» hatt to svar.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import ast
import hashlib
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

PRISBOKSVEIP_DSN = os.environ.get("DISPONIT_TEST_PRISBOKSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "108_m26_prisbok.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "prisbok.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "prisbok.py",
    ROT / "platform" / "drift" / "prisboksveip.py",
    ROT / "platform" / "drift" / "kjor_prisboksveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("prisbokterskel", "produkt", "pris", "klausul", "prisbokfunn")


def _rt():
    from db.pg import koble
    return koble(DSN)


def _sv():
    from db.pg import koble
    return koble(PRISBOKSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m26-{merke}-{secrets.token_hex(4)}"


def _terskler(c, tenant, *, rabatt=100, varsel=30, utenpris=7,
              aktor="u-test"):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m26_sett_terskler(%s,%s,%s,%s,%s)",
                  (tenant, rabatt, varsel, utenpris, aktor)).fetchone()[0]
    c.commit()
    return v


def _produkt(c, tenant, *, kode=None, navn="Konsulenttime", enhet="time",
             pid=None, aktor="u-test"):
    pid = pid or uuid.uuid4()
    kode = kode or ("K-" + secrets.token_hex(4))
    _sett_kontekst(c, tenant)
    c.execute("SELECT m26_registrer_produkt(%s,%s,%s,%s,%s,%s)",
              (tenant, pid, kode, navn, enhet, aktor))
    c.commit()
    return pid


def _pris(c, tenant, pid, ore, fra, begrunnelse="satt", aktor="u-test"):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m26_sett_pris(%s,%s,%s,'NOK',%s::date,%s,%s)",
                  (tenant, pid, ore, fra, begrunnelse,
                   aktor)).fetchone()[0]
    c.commit()
    return v


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m26_sveip_prisbok(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant, *, bare_apne=True):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT funntype, produkt_id, over_grense, prisversjon, apen"
        "  FROM prisbokfunn"
        " WHERE tenant=%s AND (%s IS FALSE OR apen) ORDER BY funntype",
        (tenant, bare_apne)).fetchall()
    m.rollback()
    return rader


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
# INVARIANT 1: pris_uten_versjon — MODULENS SKARPESTE
# ---------------------------------------------------------------------------

@pg
def test_invariant_pris_uten_versjon(migrator):
    """EN PRIS ENDRES ALDRI — DEN ERSTATTES.

    `priser_fra_prisbok` er en attestasjon om at et tilbud siterte boka,
    og den er verdiløs hvis ingen kan svare på HVA SOM STO DER DA. Denne
    porten måler nøyaktig det: to versjoner, og oppslag på fire datoer.

    MUTASJONEN SOM DREPER DENNE: la vakten slippe en `UPDATE` av
    `listepris_ore`.
    """
    tenant = _tenantnavn("versjon")
    c = _rt()
    try:
        _terskler(c, tenant)
        pid = _produkt(c, tenant)
        assert _pris(c, tenant, pid, 150000, "2026-01-01",
                     "startpris") == 1
        assert _pris(c, tenant, pid, 165000, "2026-07-01",
                     "indeksregulering") == 2
        _sett_kontekst(c, tenant)
        for dato, fasit in (("2025-12-31", None), ("2026-06-30", 150000),
                            ("2026-07-01", 165000), ("2030-01-01", 165000)):
            rad = c.execute(
                "SELECT listepris_ore FROM m26_pris_paa_dato(%s,%s,%s::date)",
                (tenant, pid, dato)).fetchone()
            assert (rad[0] if rad else None) == fasit, (dato, rad)
        c.rollback()
        # BEGRUNNELSEN ER OBLIGATORISK.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m26_sett_pris(%s,%s,1,'NOK','2027-01-01',"
                      "NULL,'u')", (tenant, pid))
        assert "begrunnelse" in str(ei.value)
        c.rollback()
        # EN PRIS SKRIVES IKKE BAKOVER.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m26_sett_pris(%s,%s,1,'NOK','2026-03-01',"
                      "'x','u')", (tenant, pid))
        assert "skrives ikke bakover" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    # DIREKTE DML: PRISEN ER FROSSET.
    for kolonne, verdi in (("listepris_ore", "1"),
                           ("begrunnelse", "'noe annet'"),
                           # FAST DATO, ikke `current_date`: den 1.
                           # juli 2026 ville `current_date` vært lik
                           # radens egen gyldig_fra, `IS DISTINCT FROM`
                           # usann, og porten grønn uten å måle noe.
                           ("gyldig_fra", "'2099-01-01'::date"),
                           ("opprettet_av", "'noen andre'")):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_prisbok_eier")
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(
                f"UPDATE pris SET {kolonne} = {verdi} WHERE tenant=%s"
                " AND versjon=2", (tenant,))
        assert "FROSSET" in str(ei.value), kolonne
        migrator.rollback()
    # …OG DEN KAN IKKE SLETTES. To gjerder, målt hver for seg: eieren har
    # ikke DELETE i det hele tatt, og VAKTEN stanser den som har det.
    rettigheter = migrator.execute(
        "SELECT privilege_type FROM information_schema.table_privileges"
        " WHERE grantee='disponit_prisbok_eier' AND table_name='pris'"
        " ORDER BY 1").fetchall()
    migrator.rollback()
    assert [r[0] for r in rettigheter] == ["INSERT", "SELECT", "UPDATE"]
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("DELETE FROM pris WHERE tenant=%s", (tenant,))
    assert "DELETE avvist" in str(ei.value)
    migrator.rollback()


@pg
def test_to_versjoner_overlapper_aldri(migrator):
    """DEN NEST SKARPESTE. Da ville «hvilken pris gjaldt den dagen» hatt
    to svar, og et tilbud gitt i går kunne blitt gjenfunnet mot to
    forskjellige tall.

    MÅLT PÅ DIREKTE DML, fordi det er der en regel som bare fantes i
    døren ville vært borte.

    MUTASJONEN SOM DREPER DENNE: fjern overlappsjekken fra
    `m26_pris_vakt`.
    """
    tenant = _tenantnavn("overlapp")
    c = _rt()
    try:
        _terskler(c, tenant)
        pid = _produkt(c, tenant)
        _pris(c, tenant, pid, 100, "2026-01-01")
        _pris(c, tenant, pid, 200, "2026-07-01")
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_prisbok_eier")
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "INSERT INTO pris (tenant, produkt_id, versjon,"
            " listepris_ore, gyldig_fra, gyldig_til, begrunnelse,"
            " opprettet_av) VALUES (%s,%s,99,1,'2026-05-01',"
            "'2026-08-01','x','u')", (tenant, pid))
    assert "overlapper" in str(ei.value)
    migrator.rollback()
    # …og ÉN ÅPEN PRIS PER PRODUKT, håndhevet av en delvis unik indeks.
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_prisbok_eier")
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "INSERT INTO pris (tenant, produkt_id, versjon,"
            " listepris_ore, gyldig_fra, begrunnelse, opprettet_av)"
            " VALUES (%s,%s,98,1,'2099-01-01','x','u')", (tenant, pid))
    assert ("pris_en_apen" in str(ei.value)
            or "overlapper" in str(ei.value)), ei.value
    migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 2: klausul_endret_i_stillhet
# ---------------------------------------------------------------------------

@pg
def test_invariant_klausul_endret_i_stillhet(migrator):
    """HASHEN REGNES AV TEKSTEN SELV, i basen.

    En hash kalleren oppga ville vært en PÅSTAND om innholdet, ikke en
    MÅLING av det — og `laste_klausuler_uendret` ville da vært en
    attestasjon om påstanden, ikke om teksten.

    MUTASJONEN SOM DREPER DENNE: fjern hashsjekken fra
    `m26_klausul_vakt`, eller la API-et sende hashen.
    """
    tenant = _tenantnavn("klausul")
    tekst = "Vårt ansvar er begrenset til kontraktssummen."
    c = _rt()
    try:
        _sett_kontekst(c, tenant)
        assert c.execute(
            "SELECT m26_sett_klausul(%s,'ansvar','Ansvarsbegrensning',"
            "%s,true,'2026-01-01'::date,'u')",
            (tenant, tekst)).fetchone()[0] == 1
        c.commit()
        _sett_kontekst(c, tenant)
        rad = c.execute("SELECT tekst_hash, standard FROM m26_klausulene(%s)",
                        (tenant,)).fetchone()
        c.rollback()
    finally:
        c.close()
    assert rad[0] == hashlib.sha256(tekst.encode("utf-8")).hexdigest()
    assert rad[1] is True
    # EN HASH SOM IKKE STEMMER MED TEKSTEN AVVISES, også ved direkte DML.
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_prisbok_eier")
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "INSERT INTO klausul (tenant, kode, versjon, tittel, tekst,"
            " tekst_hash, gyldig_fra, opprettet_av)"
            " VALUES (%s,'x',1,'T','tekst',%s,'2026-01-01','u')",
            (tenant, "0" * 64))
    assert "stemmer ikke med teksten" in str(ei.value)
    migrator.rollback()
    # …OG TEKSTEN ER FROSSET: en endret klausul er en NY versjon.
    # `standard` HØRER MED i den frysningen: `standard_forbehold
    # _inkludert` hviler på nettopp det flagget, og et forbehold som
    # stille sluttet å være standard ville gjort attestasjonen sann om
    # noe annet enn det den ble gitt for.
    for kolonne, verdi in (("tekst", "'noe helt annet'"),
                           ("standard", "false"),
                           ("tittel", "'en annen tittel'"),
                           ("opprettet_av", "'noen andre'")):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_prisbok_eier")
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(
                f"UPDATE klausul SET {kolonne} = {verdi} WHERE tenant=%s",
                (tenant,))
        assert "FROSSET" in str(ei.value), kolonne
        migrator.rollback()
    # …og INGEN SKRIVEVEI TAR IMOT EN HASH. Verken døren eller API-et har
    # den som parameter: hashen er en MÅLING av teksten, ikke et felt en
    # kaller fyller ut. (Lesingen viser den — det er hele poenget.)
    import inspect

    from api.prisbok import sett_klausul_endepunkt
    kilde = inspect.getsource(sett_klausul_endepunkt)
    # Docstringen FORKLARER fraværet og skal ikke telle med.
    i = kilde.index('"""')
    j = kilde.index('"""', i + 3) + 3
    assert "hash" in kilde[i:j], "forklaringen forsvant fra døren"
    assert "hash" not in kilde[:i] + kilde[j:]
    sql = MIGRASJON.read_text(encoding="utf-8")
    i = sql.index("CREATE FUNCTION m26_sett_klausul(")
    assert "hash" not in sql[i:sql.index("RETURNS", i)]


@pg
def test_en_ny_klausulversjon_lukker_den_forrige(migrator):
    tenant = _tenantnavn("klausul2")
    c = _rt()
    try:
        _sett_kontekst(c, tenant)
        c.execute("SELECT m26_sett_klausul(%s,'ansvar','T','A',false,"
                  "'2026-01-01'::date,'u')", (tenant,))
        c.commit()
        _sett_kontekst(c, tenant)
        assert c.execute(
            "SELECT m26_sett_klausul(%s,'ansvar','T','B',false,"
            "'2026-07-01'::date,'u')", (tenant,)).fetchone()[0] == 2
        c.commit()
        _sett_kontekst(c, tenant)
        rader = c.execute(
            "SELECT versjon, tekst, gyldig_til FROM m26_klausulene(%s)",
            (tenant,)).fetchall()
        c.rollback()
    finally:
        c.close()
    assert [(r[0], r[1]) for r in rader] == [(2, "B"), (1, "A")]
    assert rader[0][2] is None, "den nye skal være åpen"
    assert rader[1][2] is not None, "den gamle skal være lukket"


# ---------------------------------------------------------------------------
# INVARIANT 3: modulen_satte_pris / modulen_genererte_tilbud /
#              modulen_signerte_attestasjon
# ---------------------------------------------------------------------------

def test_invariant_modulen_satte_ingen_pris_og_genererte_inget_tilbud():
    """HVER PRIS ER SKREVET AV ET MENNESKE. Modulen ganger ikke,
    indekserer ikke og runder ikke — og den lager inget tilbud.

    Målt på IMPORTENE (AST), på KODEN og på RUTENE.
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
                    f"{fil.name} importerer {n} — v1 regner ingen pris"
    for fil in (MIGRASJON, FLATE, *MODULFILER):
        uten = _bare_kode(fil, uten_strenger=True).lower()
        # `tilbud(?!t)`: «p_tilbudt_ore» er BELØPET NOEN HAR TILBUDT en
        # kunde — det er målingen `m26_innenfor_rabatt` gjør. Et
        # «tilbud» som substantiv er dokumentet v1 ikke lager.
        for ord_ in ("attestasjon", "signatur", r"tilbud(?!t)",
                     "prisforslag", "foreslatt", "indeksregul", "m05_",
                     "dokumentmal"):
            assert not re.search(ord_, uten), (
                f"{fil.name} bærer «{ord_}» — v1 setter ingen"
                " pris og genererer inget tilbud")

    from api.app import RUTESCOPE
    mine = sorted(sti for _m, sti in RUTESCOPE
                  if sti.startswith("/v1/prisbok"))
    assert mine == [
        "/v1/prisbok",
        "/v1/prisbok/klausul",
        "/v1/prisbok/produkt",
        "/v1/prisbok/terskler",
        "/v1/prisbok/{produkt_id:uuid}/aktiv",
        "/v1/prisbok/{produkt_id:uuid}/historikk",
        "/v1/prisbok/{produkt_id:uuid}/paa-dato",
        "/v1/prisbok/{produkt_id:uuid}/pris",
    ], mine


@pg
def test_sveipen_setter_ingen_pris_og_forlenger_ingen_gyldighet(migrator):
    """Målt på VIRKELIGHETEN: en full sveip endrer ikke ett radantall
    utenfor registeret — OG DEN RØRER INGEN PRISRAD, selv om den vet
    hvilke som er i ferd med å gå ut.

    MUTASJONEN SOM DREPER DENNE: la sveipen sette `gyldig_til = NULL` på
    priser som utløper.
    """
    tenant = _tenantnavn("sveip")
    c = _rt()
    try:
        _terskler(c, tenant, varsel=3650)
        pid = _produkt(c, tenant)
        _pris(c, tenant, pid, 1000, "2026-01-01")
        _pris(c, tenant, pid, 2000, "2030-01-01")
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    for_pris = migrator.execute(
        "SELECT versjon, listepris_ore, gyldig_fra, gyldig_til FROM pris"
        " WHERE tenant=%s ORDER BY versjon", (tenant,)).fetchall()
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
    etter_pris = migrator.execute(
        "SELECT versjon, listepris_ore, gyldig_fra, gyldig_til FROM pris"
        " WHERE tenant=%s ORDER BY versjon", (tenant,)).fetchall()
    migrator.rollback()
    assert for_pris == etter_pris, "sveipen rørte en prisrad"


# ---------------------------------------------------------------------------
# INVARIANT 4: belop_i_flyttall
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


def test_teksten_lagres_trimmet():
    """API-et og basen skal være ENIGE om hva som ble skrevet.

    Dørene i 108 `btrim`-er selv, så en utrimmet verdi herfra ville
    blitt lagret trimmet likevel. Og koden er det et tilbud siterer:
    « K-100 » og «K-100» skal ikke kunne bli to rader.
    """
    from api.prisbok import _tekst
    from api.policyadmin_http import _Avbrudd
    assert _tekst({"k": "  K-100  "}, "k", "r", 10) == "K-100"
    assert _tekst({"k": "K-100"}, "k", "r", 5) == "K-100"
    for verdi in ("", "   ", None, 5, True, ["K"]):
        with pytest.raises(_Avbrudd):
            _tekst({"k": verdi}, "k", "r", 10)
    # LENGDEN MÅLES PÅ DET SOM LAGRES: seks tegn med mellomrom rundt er
    # fem tegn i basen.
    assert _tekst({"k": "  K-100  "}, "k", "r", 5) == "K-100"
    with pytest.raises(_Avbrudd):
        _tekst({"k": "K-1000"}, "k", "r", 5)


def test_invariant_belop_i_flyttall_over_api():
    from api.prisbok import MAKS_ORE, _bool, _heltall, _ore
    from api.policyadmin_http import _Avbrudd
    for verdi in (2.5, 100.0, True, False, "100", None, -1, MAKS_ORE):
        with pytest.raises(_Avbrudd):
            _ore({"p": verdi}, "p", "r")
    assert _ore({"p": 0}, "p", "r") == 0
    for verdi in (1.5, True, False, "3", None, -1, 1001):
        with pytest.raises(_Avbrudd):
            _heltall({"n": verdi}, "n", "r", 0, 1000)
    # …og en boolsk verdi må VÆRE boolsk: `1` er ikke `true`.
    for verdi in (1, 0, "ja", None):
        with pytest.raises(_Avbrudd):
            _bool({"b": verdi}, "b", "r")
    assert _bool({"b": True}, "b", "r") is True
    assert _bool({}, "b", "r") is False


@pg
def test_rabattgrensen_regnes_i_heltall(migrator):
    """HELTALLSARITMETIKK OG INGEN DIVISJON: `tilbudt * 1000 >=
    listepris * (1000 - promille)`. GRENSETILFELLET ER PORTEN.

    OG «INGEN PRIS DEN DAGEN» GIR `NULL`, ikke `false`: «innenfor rabatt»
    om noe som ikke hadde en pris ville vært en dom uten grunnlag.
    """
    tenant = _tenantnavn("rabatt")
    c = _rt()
    try:
        _terskler(c, tenant, rabatt=100)
        pid = _produkt(c, tenant)
        _pris(c, tenant, pid, 165000, "2026-01-01")
        _sett_kontekst(c, tenant)
        for tilbudt, fasit in ((165000, True), (148500, True),
                               (148499, False), (0, False)):
            ut = c.execute(
                "SELECT m26_innenfor_rabatt(%s,%s,current_date,%s)",
                (tenant, pid, tilbudt)).fetchone()[0]
            assert ut is fasit, (tilbudt, ut)
        # INGEN PRIS DEN DAGEN → NULL.
        ut = c.execute(
            "SELECT m26_innenfor_rabatt(%s,%s,'2020-01-01'::date,1)",
            (tenant, pid)).fetchone()[0]
        assert ut is None
        c.rollback()
        # …og et negativt tilbudt beløp er ingen måling.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m26_innenfor_rabatt(%s,%s,current_date,-1)",
                      (tenant, pid))
        assert "negativt" in str(ei.value)
        c.rollback()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# INVARIANT 5: rabattgrense_hardkodet
# ---------------------------------------------------------------------------

def test_invariant_rabattgrense_hardkodet():
    import re
    for fil in MODULFILER:
        for m in re.finditer(
                r"^([A-Z_]*(?:GRENSE|TERSKEL|PROMILLE|RABATT|DOGN)"
                r"[A-Z_]*)\s*=\s*(\d+)", _bare_kode(fil), re.M):
            assert m.group(1) in ("GRENSE",), \
                f"{fil.name} har terskelkonstanten {m.group(1)}"
    from drift import prisboksveip
    assert prisboksveip.GRENSE == 500
    kode = _bare_kode(MIGRASJON)
    assert "m26_sveip_prisbok(p_grense INT DEFAULT 500)" in kode
    assert "FROM public.prisbokterskel" in kode
    from api.prisbok import terskler_endepunkt
    doc = terskler_endepunkt.__doc__ or ""
    assert "M-1" in doc and "gap" in doc.lower()


@pg
def test_tersklene_versjoneres_og_kan_ikke_slettes(migrator):
    tenant = _tenantnavn("terskelversjon")
    c = _rt()
    try:
        assert _terskler(c, tenant, rabatt=100) == 1
        assert _terskler(c, tenant, rabatt=250) == 2
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("DELETE FROM prisbokterskel WHERE tenant=%s",
                         (tenant,))
    assert "DELETE avvist" in str(ei.value)
    migrator.rollback()
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "UPDATE prisbokterskel SET uten_pris_dogn=1 WHERE tenant=%s",
            (tenant,))
    assert "versjonen må øke" in str(ei.value)
    migrator.rollback()


# ---------------------------------------------------------------------------
# Funnene
# ---------------------------------------------------------------------------

@pg
def test_funnene_og_idempotensen(migrator):
    """`pris_utloper_snart`, `uten_gyldig_pris` og `ingen_terskel`."""
    tenant = _tenantnavn("funn")
    c = _rt()
    try:
        _terskler(c, tenant, varsel=30, utenpris=7)
        pid = _produkt(c, tenant)
        _pris(c, tenant, pid, 1000, "2026-01-01")
        # En ny versjon fra om fem døgn lukker den gjeldende om fire.
        _sett_kontekst(c, tenant)
        c.execute("SELECT m26_sett_pris(%s,%s,2000,'NOK',"
                  "current_date + 5,'utloper snart','u')", (tenant, pid))
        c.commit()
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    funn = {r[0]: r for r in _funn(migrator, tenant)}
    assert "pris_utloper_snart" in funn
    assert funn["pris_utloper_snart"][2] == 4, funn
    forst = {(r[0], r[1]) for r in _funn(migrator, tenant)}
    with _sv() as v:
        rad = _sveip(v)
    assert rad[1] == 0, "sveip nummer to skrev nye rader"
    assert {(r[0], r[1]) for r in _funn(migrator, tenant)} == forst

    uten = _tenantnavn("utenterskel")
    c = _rt()
    try:
        _produkt(c, uten)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert {r[0] for r in _funn(migrator, uten)} == {"ingen_terskel"}


@pg
def test_en_framtidig_pris_skjuler_ikke_at_produktet_star_uten_pris(
        migrator):
    """Et produkt med BARE en framtidig pris har ingen pris NÅ.

    Den forrige formen regnet `coalesce(gyldig_til, p_dag)` over ALLE
    prisradene, også de som ikke hadde begynt. En pris som gjelder fra
    neste år ga da «sist hadde pris i dag», og funnet ble stille så lenge
    noen hadde ført en framtidig pris — altså det motsatte av det porten
    lover (CodeRabbit, 108).

    MÅLT PÅ FUNNDØREN MED EN FRAMTIDIG MÅLEDAG, fordi det er den eneste
    måten å komme forbi at produktet er opprettet i dag.
    """
    tenant = _tenantnavn("framtid")
    c = _rt()
    try:
        _terskler(c, tenant, utenpris=7)
        pid = _produkt(c, tenant)
        _sett_kontekst(c, tenant)
        # ENESTE PRIS, og den begynner om 100 døgn.
        c.execute("SELECT m26_sett_pris(%s,%s,1000,'NOK',"
                  "current_date + 100,'framtidig','u')", (tenant, pid))
        c.commit()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_prisbok_eier")
    rader = migrator.execute(
        "SELECT funntype, over_grense"
        "  FROM m26_funnkandidater(%s, current_date + 60)"
        " ORDER BY funntype", (tenant,)).fetchall()
    migrator.rollback()
    assert [r[0] for r in rader] == ["uten_gyldig_pris"], rader
    # 60 døgn siden opprettelsen, minus tenantens grense på 7.
    assert rader[0][1] == 53, rader


@pg
def test_et_deaktivert_produkt_lukker_funnene_og_beholder_historikken(
        migrator):
    """Et slettet produkt ville tatt prishistorikken med seg, og den er
    svaret på hva et gammelt tilbud siterte."""
    tenant = _tenantnavn("deaktiver")
    c = _rt()
    try:
        _terskler(c, tenant, varsel=3650)
        pid = _produkt(c, tenant)
        _pris(c, tenant, pid, 1000, "2026-01-01")
        _sett_kontekst(c, tenant)
        c.execute("SELECT m26_sett_pris(%s,%s,2000,'NOK',current_date + 5,"
                  "'x','u')", (tenant, pid))
        c.commit()
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert _funn(migrator, tenant)
    c = _rt()
    try:
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT m26_sett_produktaktiv(%s,%s,false,'u')",
                         (tenant, pid)).fetchone()[0] is True
        c.commit()
        # …og en gang til er et STILLE JA.
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT m26_sett_produktaktiv(%s,%s,false,'u')",
                         (tenant, pid)).fetchone()[0] is False
        c.commit()
    finally:
        c.close()
    assert _funn(migrator, tenant) == []
    # HISTORIKKEN BESTÅR.
    _sett_kontekst(migrator, tenant)
    n = migrator.execute("SELECT count(*) FROM pris WHERE tenant=%s",
                         (tenant,)).fetchone()[0]
    migrator.rollback()
    assert n == 2
    # …og PRODUKTET kan ikke slettes.
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("DELETE FROM produkt WHERE tenant=%s", (tenant,))
    assert "DELETE avvist" in str(ei.value)
    migrator.rollback()


@pg
def test_ingen_av_de_fem_tabellene_kan_tommes(migrator):
    tenant = _tenantnavn("truncate")
    c = _rt()
    try:
        _terskler(c, tenant, varsel=3650)
        pid = _produkt(c, tenant)
        _pris(c, tenant, pid, 1000, "2026-01-01")
        _sett_kontekst(c, tenant)
        c.execute("SELECT m26_sett_klausul(%s,'k','T','A',true,"
                  "'2026-01-01'::date,'u')", (tenant,))
        c.execute("SELECT m26_sett_pris(%s,%s,2,'NOK',current_date + 2,"
                  "'x','u')", (tenant, pid))
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
        migrator.execute("TRUNCATE public.produkt CASCADE")
    assert "TRUNCATE avvist" in str(ei.value), ei.value
    migrator.rollback()


def test_skrivedorene_som_leser_en_tilstand_laser_raden():
    sql = MIGRASJON.read_text(encoding="utf-8")
    for doer in ("m26_sett_pris", "m26_sett_klausul"):
        i = sql.index(f"CREATE FUNCTION {doer}(")
        assert "FOR UPDATE" in sql[i:sql.index("END $$;", i)], doer


# ---------------------------------------------------------------------------
# INVARIANT 6: tenantlekkasje_i_prisbok
# ---------------------------------------------------------------------------

@pg
def test_invariant_tenantlekkasje_direkte(migrator):
    a = _tenantnavn("lekk-a")
    b = _tenantnavn("lekk-b")
    c = _rt()
    try:
        _terskler(c, a)
        _produkt(c, a)
        _terskler(c, b)
        _sett_kontekst(c, b)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT * FROM m26_prisbokstatus(%s)", (a,))
        assert "tenantkontekst" in str(ei.value)
        c.rollback()
        _sett_kontekst(c, a)
        assert c.execute("SELECT * FROM m26_prisbokstatus(%s)",
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
        _produkt(c, TENANT, kode="EGEN-KODE")
        _terskler(c, fremmed)
        _produkt(c, fremmed, kode="FREMMED-KODE")
    finally:
        c.close()
    cookie, _csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/prisbok", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    kropp = json.dumps(r.json())
    assert "EGEN-KODE" in kropp
    assert "FREMMED-KODE" not in kropp


# ---------------------------------------------------------------------------
# Sveipearbeideren
# ---------------------------------------------------------------------------

@pg
def test_sveipen_nekter_kontekst(migrator):
    with _sv() as v:
        v.execute("SELECT set_config('disponit.tenant','t',false)")
        with pytest.raises(psycopg.Error) as ei:
            v.execute("SELECT * FROM m26_sveip_prisbok(500)")
        assert "KRYSS-TENANT" in str(ei.value)
        v.rollback()


@pg
def test_sveiperollen_har_ingen_tabellrettigheter(migrator):
    if not PRISBOKSVEIP_DSN:
        pytest.skip("DISPONIT_TEST_PRISBOKSVEIP_DSN ikke satt")
    rader = migrator.execute(
        "SELECT table_name, privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_prisboksveip'").fetchall()
    migrator.rollback()
    assert rader == [], rader


def test_sveipen_nekter_aa_starte_uten_egen_dsn(tmp_path, monkeypatch):
    from drift import kjor_prisboksveip
    monkeypatch.delenv("DISPONIT_PRISBOKSVEIP_URL", raising=False)
    monkeypatch.setenv("DISPONIT_PRISBOKSVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.setattr("db.hemmeligheter.last_credentials", lambda: None)
    assert kjor_prisboksveip.main() == 2


# ---------------------------------------------------------------------------
# INVARIANT 7: ui_axe_alvorlige_brudd
# ---------------------------------------------------------------------------

def test_invariant_ui_axe_bor_i_js_suiten():
    fil = (ROT / "platform" / "core" / "ui" / "test" / "prisbok.test.js")
    assert fil.exists(), "prisbok.test.js mangler"
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
        " ('https://m26.test', %s) RETURNING bruker_id",
        ("s26-" + secrets.token_hex(6),)).fetchone()[0]
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
@dekker("prisbok_ulovlig_tilstand")
def test_http_prisen_skrives_ikke_bakover_er_409(migrator, klient):
    """FEILVEIEN `prisbok_ulovlig_tilstand`, ende til ende.

    Kroppen ER velformet: beløpet er et heltall, datoen er lesbar,
    begrunnelsen står der. Det er BASEN som sier at boka ikke skrives
    bakover — «hva sto her da» er hele spørsmålet den finnes for å svare
    på.

    Dette er også testen `test_api_porter.test_hver_feilvei_har_en_test`
    krever for den nye feilveien.
    """
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _hpost(klient, cookie, csrf, "/v1/prisbok/terskler",
               {"rabattgrense_promille": 100, "utlop_varsel_dogn": 30,
                "uten_pris_dogn": 7})
    assert r.status_code in (200, 201), r.text
    kode = "H-" + secrets.token_hex(4)
    r = _hpost(klient, cookie, csrf, "/v1/prisbok/produkt",
               {"kode": kode, "navn": "HTTP-time", "enhet": "time"})
    assert r.status_code in (200, 201), r.text
    pid = r.json()["produkt_id"]
    r = _hpost(klient, cookie, csrf, f"/v1/prisbok/{pid}/pris",
               {"listepris_ore": 150000, "valuta": "NOK",
                "gyldig_fra": "2026-07-01", "begrunnelse": "startpris"})
    assert r.status_code in (200, 201), r.text
    # BAKOVER: TILSTANDEN sier nei.
    r = _hpost(klient, cookie, csrf, f"/v1/prisbok/{pid}/pris",
               {"listepris_ore": 1, "valuta": "NOK",
                "gyldig_fra": "2026-01-01", "begrunnelse": "for tidlig"})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "prisbok_ulovlig_tilstand"
    # SAMME KODE TO GANGER: også en tilstand.
    r = _hpost(klient, cookie, csrf, "/v1/prisbok/produkt",
               {"kode": kode, "navn": "Dublett", "enhet": "stk"})
    assert r.status_code == 409, r.text
    # …og et flyttall er 400: KROPPEN er feil.
    r = _hpost(klient, cookie, csrf, f"/v1/prisbok/{pid}/pris",
               {"listepris_ore": 150000.5, "valuta": "NOK",
                "gyldig_fra": "2027-01-01", "begrunnelse": "x"})
    assert r.status_code == 400, r.text
    # …og en pris uten begrunnelse likeså (API-et er strengere enn døren).
    r = _hpost(klient, cookie, csrf, f"/v1/prisbok/{pid}/pris",
               {"listepris_ore": 1, "valuta": "NOK",
                "gyldig_fra": "2027-01-01"})
    assert r.status_code == 400, r.text

    # `aktiv` ER PÅKREVD: en kropp uten feltet ville ellers DEAKTIVERT
    # produktet — en utelatelse som utfører en handling (CodeRabbit).
    r = _hpost(klient, cookie, csrf, f"/v1/prisbok/{pid}/aktiv", {})
    assert r.status_code == 400, r.text
    r = _hpost(klient, cookie, csrf, f"/v1/prisbok/{pid}/aktiv",
               {"aktiv": 0})
    assert r.status_code == 400, r.text
    r = _hpost(klient, cookie, csrf, f"/v1/prisbok/{pid}/aktiv",
               {"aktiv": False})
    assert r.status_code in (200, 201), r.text

    # OPPSLAGET SOM BETYR NOE: hva sto i boka den dagen.
    r = klient.get(f"/v1/prisbok/{pid}/paa-dato?dato=2026-08-01",
                   cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    assert r.json()["pris"]["listepris_ore"] == 150000
    # …og `null` når ingen pris gjaldt — ikke null kroner.
    r = klient.get(f"/v1/prisbok/{pid}/paa-dato?dato=2020-01-01",
                   cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    assert r.json()["pris"] is None


@pg
def test_http_lesingen_bruker_okonomi_read(migrator, klient):
    from api.app import RUTESCOPE
    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert RUTESCOPE[("GET", "/v1/prisbok")] == "okonomi:read"
    assert "okonomi:read" in ROLLE_TIL_SCOPES["admin"]
    for rolle in ("leser", "sikkerhet", "godkjenner"):
        assert "okonomi:read" not in ROLLE_TIL_SCOPES[rolle], rolle
    cookie, _c = _browserokt(migrator, ["leser"])
    r = klient.get("/v1/prisbok", cookies={_C_SESJON(): cookie})
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# §0
# ---------------------------------------------------------------------------

def test_grensen_ble_registrert_for_koden():
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m26-v1"]
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["punktbinding"] == {}
    egen = Path(__file__).read_text(encoding="utf-8")
    for inv in g["invarianter"]:
        assert inv in egen, f"invarianten {inv} har ingen port"
