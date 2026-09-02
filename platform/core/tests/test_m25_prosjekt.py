"""M-25 prosjekt- og kontraktagent v1 (migrasjon 107) — REGISTERET.

Grensen `m25-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR koden
(§0-regelen), og hver invariant der har minst én port her.

DEN SKARPESTE PORTEN ER `milepael_uten_dokumentasjon`. En automatisk
faktura på en milepæl ingen har dokumentert er penger krevd for arbeid
som kanskje ikke er gjort — og et krav har forlatt systemet i det
øyeblikket det ble sendt.

DEN NEST SKARPESTE er at FORBRUK OG BETALINGSPLAN ALDRI BLANDES. Et
register som la dem i samme kolonne ville gjort «går prosjektet i pluss»
til et spørsmål ingen kunne svare på.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import ast
import json
import os
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo)
from .test_m37 import _sett_kontekst

PROSJEKTSVEIP_DSN = os.environ.get("DISPONIT_TEST_PROSJEKTSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "107_m25_prosjektregister.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "prosjekt.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "prosjekt.py",
    ROT / "platform" / "drift" / "prosjektsveip.py",
    ROT / "platform" / "drift" / "kjor_prosjektsveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("prosjektterskel", "prosjekt", "milepael", "prosjektarbeid",
        "prosjektfunn")


def _rt():
    from db.pg import koble
    return koble(DSN)


def _sv():
    from db.pg import koble
    return koble(PROSJEKTSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m25-{merke}-{secrets.token_hex(4)}"


def _terskler(c, tenant, *, budsjett=0, frist=7, stillhet=30,
              aktor="u-test"):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m25_sett_terskler(%s,%s,%s,%s,%s)",
                  (tenant, budsjett, frist, stillhet,
                   aktor)).fetchone()[0]
    c.commit()
    return v


def _prosjekt(c, tenant, *, kunde="Kunde AS", navn=None, budsjett=1000000,
              start_siden=60, slutt_om=60, pid=None, aktor="u-test"):
    pid = pid or uuid.uuid4()
    navn = navn or ("P-" + secrets.token_hex(4))
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m25_registrer_prosjekt(%s,%s,%s,%s,'K-1',%s,"
        "       current_date - %s::int, current_date + %s::int, %s)",
        (tenant, pid, kunde, navn, budsjett, start_siden, slutt_om,
         aktor))
    c.commit()
    return pid


def _plan(c, tenant, pid, milepaeler=None, aktor="u-test"):
    milepaeler = milepaeler or [
        {"navn": "Oppstart", "planlagt_dato": "2026-07-01",
         "belop_ore": 300000},
        {"navn": "Overtakelse", "planlagt_dato": "2026-12-01",
         "belop_ore": 700000}]
    _sett_kontekst(c, tenant)
    ut = c.execute("SELECT m25_sett_betalingsplan(%s,%s,%s::jsonb,%s)",
                   (tenant, pid, json.dumps(milepaeler),
                    aktor)).fetchone()[0]
    c.commit()
    return ut


def _arbeid(c, tenant, pid, *, siden=1, minutter=480, kostnad=100000,
            aid=None, aktor="u-test"):
    aid = aid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    ut = c.execute(
        "SELECT m25_registrer_arbeid(%s,%s,%s,current_date - %s::int,"
        "       %s,%s,'grunnarbeid',%s)",
        (tenant, aid, pid, siden, minutter, kostnad, aktor)).fetchone()[0]
    c.commit()
    return ut


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m25_sveip_prosjekter(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant, *, bare_apne=True):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT funntype, prosjekt_id, over_grense, milepael_nr, apen"
        "  FROM prosjektfunn"
        " WHERE tenant=%s AND (%s IS FALSE OR apen) ORDER BY funntype",
        (tenant, bare_apne)).fetchall()
    m.rollback()
    return rader


def _bare_kode(fil: Path) -> str:
    """Filens innhold uten kommentarer OG uten docstrings (m24-formen).

    Portene måler KODE, ikke prosa: modulens egen docstring FORTELLER at
    den ikke fakturerer, og et rått delstrengsøk ville falt på nettopp
    den setningen.
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
    return "\n".join(l for l in linjer
                     if not l.lstrip().startswith(merke))


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
# INVARIANT 1: milepael_uten_dokumentasjon — MODULENS SKARPESTE
# ---------------------------------------------------------------------------

@pg
def test_invariant_milepael_uten_dokumentasjon(migrator):
    """EN MILEPÆL KAN IKKE MERKES NÅDD UTEN EN HENVISNING TIL HVA SOM
    DOKUMENTERER DEN.

    `milepael_dokumentert` i policyen kan aldri bli sant om noe som ikke
    har en dokumentasjon å peke på — og en automatisk faktura på en
    udokumentert milepæl er penger krevd for arbeid som kanskje ikke er
    gjort.

    MÅLT PÅ DØREN OG PÅ CHECK-EN. `NULL ~ '...'` er NULL, og en CHECK
    som evaluerer til NULL PASSERER (hullet fra 101, lukket i 102) — så
    DIREKTE DML med bare `naadd_ts` satt er den porten som betyr noe.

    MUTASJONEN SOM DREPER DENNE: gjør `dokumentasjon_ref` valgfri i
    `milepael_naadd_helhet`.
    """
    tenant = _tenantnavn("dok")
    c = _rt()
    try:
        _terskler(c, tenant)
        pid = _prosjekt(c, tenant)
        _plan(c, tenant, pid)
        for ref in (None, "", "   "):
            _sett_kontekst(c, tenant)
            with pytest.raises(psycopg.Error) as ei:
                c.execute("SELECT m25_naa_milepael(%s,%s,1,%s,'u')",
                          (tenant, pid, ref))
            assert "påstand" in str(ei.value), ref
            c.rollback()
        _sett_kontekst(c, tenant)
        assert c.execute(
            "SELECT m25_naa_milepael(%s,%s,1,'befaring, bilde 41','u')",
            (tenant, pid)).fetchone()[0] is True
        c.commit()
        # …og en gang til er et STILLE JA.
        _sett_kontekst(c, tenant)
        assert c.execute(
            "SELECT m25_naa_milepael(%s,%s,1,'igjen','u')",
            (tenant, pid)).fetchone()[0] is False
        c.commit()
    finally:
        c.close()
    # DIREKTE DML, forbi døren: HALVVEIS NÅDD FINNES IKKE.
    for kolonner, verdier in (
            ("naadd_ts", "now()"),
            ("naadd_ts, naadd_av", "now(), 'u'"),
            ("naadd_ts, dokumentasjon_ref", "now(), 'noe'")):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_prosjekt_eier")
        with pytest.raises(psycopg.Error), migrator.transaction():
            migrator.execute(
                f"UPDATE milepael SET ({kolonner}) = ROW({verdier})"
                " WHERE tenant=%s AND milepael_nr=2", (tenant,))
        migrator.rollback()


@pg
def test_en_naadd_milepael_er_frosset(migrator):
    """Et beløp som kunne endres i ettertid ville omskrevet grunnlaget
    for et krav som alt var stilt — og en dokumentasjonsreferanse som
    kunne byttes ut ville gjort «dokumentert» til et ord."""
    tenant = _tenantnavn("frosset")
    c = _rt()
    try:
        _terskler(c, tenant)
        pid = _prosjekt(c, tenant)
        _plan(c, tenant, pid)
        _sett_kontekst(c, tenant)
        c.execute("SELECT m25_naa_milepael(%s,%s,1,'rapport 7','u')",
                  (tenant, pid))
        c.commit()
    finally:
        c.close()
    for kolonne, verdi in (("belop_ore", "1"),
                           ("dokumentasjon_ref", "'noe annet'"),
                           ("naadd_av", "'noen andre'"),
                           ("navn", "'X'")):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_prosjekt_eier")
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(
                f"UPDATE milepael SET {kolonne} = {verdi}"
                " WHERE tenant=%s AND milepael_nr=1", (tenant,))
        assert "frosset" in str(ei.value), kolonne
        migrator.rollback()
    # …OG DEN KAN IKKE SLETTES. En UNÅDD kan: betalingsplanen REDIGERES
    # til den er avtalt, og en plan man ikke kunne rette ville tvunget
    # fram et nytt prosjekt for hver skrivefeil.
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_prosjekt_eier")
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("DELETE FROM milepael WHERE tenant=%s"
                         " AND milepael_nr=1", (tenant,))
    assert "DELETE avvist" in str(ei.value)
    migrator.rollback()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_prosjekt_eier")
    with migrator.transaction():
        migrator.execute("DELETE FROM milepael WHERE tenant=%s"
                         " AND milepael_nr=2", (tenant,))
    migrator.rollback()


@pg
def test_en_omskrevet_plan_rorer_ikke_de_naadde(migrator):
    """Betalingsplanen skrives i én omgang — men en omskriving skal ikke
    kunne slette grunnlaget for et krav som alt er stilt."""
    tenant = _tenantnavn("omskriv")
    c = _rt()
    try:
        _terskler(c, tenant)
        pid = _prosjekt(c, tenant)
        _plan(c, tenant, pid)
        _sett_kontekst(c, tenant)
        c.execute("SELECT m25_naa_milepael(%s,%s,1,'rapport 7','u')",
                  (tenant, pid))
        c.commit()
        # En ny plan med ETT trinn: den nådde milepæl 1 består.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error):
            # Nummer 1 finnes fortsatt (nådd), så den nye plan-raden med
            # samme nummer kolliderer på primærnøkkelen. Registeret sier
            # heller nei enn å skrive over et krav.
            c.execute("SELECT m25_sett_betalingsplan(%s,%s,%s::jsonb,'u')",
                      (tenant, pid, json.dumps(
                          [{"navn": "Ny", "planlagt_dato": "2027-01-01",
                            "belop_ore": 1}])))
        c.rollback()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    rader = migrator.execute(
        "SELECT milepael_nr, dokumentasjon_ref FROM milepael"
        " WHERE tenant=%s ORDER BY 1", (tenant,)).fetchall()
    migrator.rollback()
    assert rader == [(1, "rapport 7"), (2, None)], rader


@pg
def test_en_plan_uten_milepaeler_er_ingen_plan(migrator):
    """`kontraktsfestet_betalingsplan` kan da aldri bli sant, og ingen
    vet hva vi har lov å kreve når."""
    tenant = _tenantnavn("tomplan")
    c = _rt()
    try:
        _terskler(c, tenant)
        pid = _prosjekt(c, tenant)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m25_sett_betalingsplan(%s,%s,'[]'::jsonb,"
                      "'u')", (tenant, pid))
        assert "ingen plan" in str(ei.value)
        c.rollback()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# INVARIANT 2: modulen_fakturerte / modulen_signerte_attestasjon
# ---------------------------------------------------------------------------

def test_invariant_modulen_fakturerte_og_attesterte_ikke():
    """POLICYEN NAVNGIR MODULEN SOM `v_prosjekt` og bruker
    `milepael_dokumentert` til å la `ordre.bekreft_og_fakturer` gå
    automatisk. v1 TAR IKKE DEN FULLMAKTEN.

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
                assert "attestering" not in n, \
                    f"{fil.name} importerer {n}"
                assert n.split(".")[0] not in {
                    "hmac", "hashlib", "cryptography", "smtplib",
                    "httpx", "requests"}, f"{fil.name} importerer {n}"
    for fil in (MIGRASJON, FLATE, *MODULFILER):
        uten = _bare_kode(fil).lower()
        for ord_ in ("attestasjon", "signatur", "signer(", "'fakturert'",
                     "m23_registrer_fordring", "fordring"):
            assert ord_ not in uten, \
                f"{fil.name} bærer «{ord_}» — v1 fakturerer ingenting"

    from api.app import RUTESCOPE
    mine = sorted(sti for _m, sti in RUTESCOPE
                  if sti.startswith("/v1/prosjekt"))
    assert mine == [
        "/v1/prosjekt",
        "/v1/prosjekt",
        "/v1/prosjekt/terskler",
        "/v1/prosjekt/{prosjekt_id:uuid}/arbeid",
        "/v1/prosjekt/{prosjekt_id:uuid}/arbeidsliste",
        "/v1/prosjekt/{prosjekt_id:uuid}/avslutt",
        "/v1/prosjekt/{prosjekt_id:uuid}/betalingsplan",
        "/v1/prosjekt/{prosjekt_id:uuid}/milepael",
        "/v1/prosjekt/{prosjekt_id:uuid}/milepaeler",
    ], mine


@pg
def test_sveipen_fakturerer_ingen_og_naar_ingen_milepael(migrator):
    """Målt på VIRKELIGHETEN: en full sveip endrer ikke ett radantall
    utenfor registeret — OG DEN MERKER INGEN MILEPÆL NÅDD, selv om den
    vet hvilke som har passert sin dato.

    MUTASJONEN SOM DREPER DENNE: la sveipen sette `naadd_ts` på
    milepæler over frist.
    """
    tenant = _tenantnavn("sveip")
    c = _rt()
    try:
        _terskler(c, tenant, frist=1)
        pid = _prosjekt(c, tenant)
        _plan(c, tenant, pid, [
            {"navn": "Gammel", "planlagt_dato": "2026-01-01",
             "belop_ore": 100}])
    finally:
        c.close()
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
    rad = migrator.execute(
        "SELECT naadd_ts FROM milepael WHERE tenant=%s AND milepael_nr=1",
        (tenant,)).fetchone()
    migrator.rollback()
    assert rad[0] is None, "sveipen merket en milepæl nådd"
    assert "milepael_over_frist" in {r[0] for r in _funn(migrator, tenant)}


# ---------------------------------------------------------------------------
# INVARIANT 3: belop_i_flyttall — OG SKILLET MELLOM DE TO TALLENE
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
    # …OG TIMENE ER HELE MINUTTER, ikke desimaltimer.
    rad = migrator.execute(
        "SELECT data_type FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name='prosjektarbeid'"
        "   AND column_name='minutter'").fetchone()
    migrator.rollback()
    assert rad[0] == "integer", rad


@pg
def test_forbruk_og_betalingsplan_er_to_stoerrelser(migrator):
    """DEN NEST SKARPESTE PORTEN. `budsjett_ore` er hva prosjektet får
    KOSTE; milepælenes `belop_ore` er hva kontrakten lar oss KREVE. Et
    register som la dem i samme kolonne ville gjort «går prosjektet i
    pluss» til et spørsmål ingen kunne svare på.

    MUTASJONEN SOM DREPER DENNE: la `forbruk_ore` telle milepælbeløp,
    eller `klar_ore` telle kostnader.
    """
    tenant = _tenantnavn("skille")
    c = _rt()
    try:
        _terskler(c, tenant)
        pid = _prosjekt(c, tenant, budsjett=1000000)
        _plan(c, tenant, pid, [
            {"navn": "A", "planlagt_dato": "2026-07-01",
             "belop_ore": 300000},
            {"navn": "B", "planlagt_dato": "2026-12-01",
             "belop_ore": 700000}])
        _arbeid(c, tenant, pid, kostnad=400000, minutter=480)
        _arbeid(c, tenant, pid, kostnad=700000, minutter=480, siden=2)
        _sett_kontekst(c, tenant)
        c.execute("SELECT m25_naa_milepael(%s,%s,1,'rapport','u')",
                  (tenant, pid))
        c.commit()
        _sett_kontekst(c, tenant)
        rad = c.execute("SELECT * FROM m25_prosjektene(%s,100)",
                        (tenant,)).fetchone()
        s = c.execute("SELECT * FROM m25_prosjektstatus(%s)",
                      (tenant,)).fetchone()
        c.rollback()
    finally:
        c.close()
    # budsjett | forbruk | minutter | … | milepæler | nådde | klar | plan
    assert rad[4] == 1000000, "budsjettet"
    assert rad[5] == 1100000, "forbruket er summen av arbeidets kostnad"
    assert rad[6] == 960, "minuttene er summen, i HELE minutter"
    assert rad[11] == 2 and rad[12] == 1
    assert rad[13] == 300000, "klar er summen av de NÅDDE milepælene"
    assert rad[14] == 1000000, "plan er summen av ALLE milepælene"
    # …og sammendraget holder de samme fire tallene fra hverandre.
    assert (s[2], s[3], s[4]) == (1000000, 1100000, 300000), s


def test_invariant_belop_i_flyttall_over_api():
    from api.prosjekt import MAKS_ORE, _heltall, _ore
    from api.policyadmin_http import _Avbrudd
    for verdi in (2.5, 100.0, True, False, "100", None, -1, MAKS_ORE):
        with pytest.raises(_Avbrudd):
            _ore({"p": verdi}, "p", "r")
    assert _ore({"p": 0}, "p", "r") == 0
    for verdi in (1.5, True, False, "3", None, 0, 1441):
        with pytest.raises(_Avbrudd):
            _heltall({"n": verdi}, "n", "r", 1, 1440)
    assert _heltall({"n": 90}, "n", "r", 1, 1440) == 90


# ---------------------------------------------------------------------------
# INVARIANT 4: budsjettvarsel_hardkodet
# ---------------------------------------------------------------------------

def test_invariant_budsjettvarsel_hardkodet():
    """Grensene er TENANTENS. Hvor mye et prosjekt kan gå over budsjett
    før noen skal se på det, er en forretningsbeslutning.

    ÆRLIG OM HVA DETTE IKKE ER: grensene går ikke gjennom M-1s
    policymotor (dokumentbasert, ingen tenant-innstilling). Invarianten
    er oppfylt i den forstand som betyr noe, men koblingen til M-1 er et
    NAVNGITT gap.
    """
    import re
    for fil in MODULFILER:
        for m in re.finditer(
                r"^([A-Z_]*(?:GRENSE|TERSKEL|PROMILLE|DOGN|BUDSJETT)"
                r"[A-Z_]*)\s*=\s*(\d+)", _bare_kode(fil), re.M):
            assert m.group(1) in ("GRENSE",), \
                f"{fil.name} har terskelkonstanten {m.group(1)}"
    from drift import prosjektsveip
    assert prosjektsveip.GRENSE == 500
    kode = _bare_kode(MIGRASJON)
    assert "m25_sveip_prosjekter(p_grense INT DEFAULT 500)" in kode
    assert "FROM public.prosjektterskel" in kode
    from api.prosjekt import terskler_endepunkt
    doc = terskler_endepunkt.__doc__ or ""
    assert "M-1" in doc and "gap" in doc.lower()


@pg
def test_budsjettgrensen_regnes_i_heltall(migrator):
    """HELTALLSARITMETIKK OG INGEN DIVISJON: `forbruk * 1000 > budsjett *
    (1000 + promille)`. GRENSETILFELLET ER PORTEN: med promille 100 og
    budsjett 100 000 er 110 000 IKKE over, 110 001 er.

    MUTASJONEN SOM DREPER DENNE: bytt `>` mot `>=`.
    """
    for forbruk, ventet in ((110000, False), (110001, True)):
        tenant = _tenantnavn(f"b{forbruk}")
        c = _rt()
        try:
            _terskler(c, tenant, budsjett=100, stillhet=3650)
            pid = _prosjekt(c, tenant, budsjett=100000)
            _plan(c, tenant, pid, [
                {"navn": "A", "planlagt_dato": "2099-01-01",
                 "belop_ore": 1}])
            _arbeid(c, tenant, pid, kostnad=forbruk)
        finally:
            c.close()
        with _sv() as v:
            _sveip(v)
        typer = {r[0] for r in _funn(migrator, tenant)}
        assert ("budsjett_overskredet" in typer) is ventet, \
            f"{forbruk} mot 100000 med 100 promille: {typer}"


@pg
def test_tersklene_versjoneres_og_kan_ikke_slettes(migrator):
    tenant = _tenantnavn("versjon")
    c = _rt()
    try:
        assert _terskler(c, tenant, budsjett=0) == 1
        assert _terskler(c, tenant, budsjett=250) == 2
    finally:
        c.close()
    rettigheter = migrator.execute(
        "SELECT privilege_type FROM information_schema.table_privileges"
        " WHERE grantee='disponit_prosjekt_eier'"
        "   AND table_name='prosjektterskel' ORDER BY 1").fetchall()
    migrator.rollback()
    assert [r[0] for r in rettigheter] == ["INSERT", "SELECT", "UPDATE"]
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("DELETE FROM prosjektterskel WHERE tenant=%s",
                         (tenant,))
    assert "DELETE avvist" in str(ei.value)
    migrator.rollback()
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "UPDATE prosjektterskel SET stillhet_dogn=1 WHERE tenant=%s",
            (tenant,))
    assert "versjonen må øke" in str(ei.value)
    migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 5: budsjett_overskredet_uten_funn
# ---------------------------------------------------------------------------

@pg
def test_invariant_budsjett_overskredet_uten_funn(migrator):
    """Et sprukket budsjett er et FUNN, ikke en stille rad.
    IDEMPOTENSEN måles i samme test."""
    tenant = _tenantnavn("budsjett")
    c = _rt()
    try:
        _terskler(c, tenant, budsjett=0, frist=3650, stillhet=3650)
        pid = _prosjekt(c, tenant, budsjett=100000)
        _plan(c, tenant, pid, [
            {"navn": "A", "planlagt_dato": "2099-01-01",
             "belop_ore": 1}])
        _arbeid(c, tenant, pid, kostnad=150000)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    funn = {r[0]: r[2] for r in _funn(migrator, tenant)}
    assert funn.get("budsjett_overskredet") == 50000, funn
    forst = {(r[0], r[1]) for r in _funn(migrator, tenant)}
    with _sv() as v:
        rad = _sveip(v)
    assert rad[1] == 0, "sveip nummer to skrev nye rader"
    assert {(r[0], r[1]) for r in _funn(migrator, tenant)} == forst


@pg
def test_de_ovrige_funntypene(migrator):
    """`betalingsplan_mangler`, `ingen_arbeid_registrert` og
    `ingen_terskel`.

    `ingen_terskel` er den som fanger en tenant som aldri kom i gang, og
    de andre reises IKKE samtidig — de ville vært gjetninger.
    """
    uten = _tenantnavn("utenterskel")
    c = _rt()
    try:
        _prosjekt(c, uten)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert {r[0] for r in _funn(migrator, uten)} == {"ingen_terskel"}

    stille = _tenantnavn("stille")
    c = _rt()
    try:
        _terskler(c, stille, stillhet=10, frist=3650)
        _prosjekt(c, stille, start_siden=100)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    typer = {r[0] for r in _funn(migrator, stille)}
    assert "ingen_arbeid_registrert" in typer
    assert "betalingsplan_mangler" in typer
    assert "ingen_terskel" not in typer


@pg
def test_milepaelnummeret_star_paa_funnet(migrator):
    """Uten det måtte et menneske lete etter hvilken milepæl det
    gjelder."""
    tenant = _tenantnavn("nr")
    c = _rt()
    try:
        _terskler(c, tenant, frist=0, stillhet=3650)
        pid = _prosjekt(c, tenant)
        _plan(c, tenant, pid, [
            {"navn": "A", "planlagt_dato": "2099-01-01",
             "belop_ore": 1},
            {"navn": "B", "planlagt_dato": "2026-01-01",
             "belop_ore": 1}])
        _arbeid(c, tenant, pid)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    rad = [r for r in _funn(migrator, tenant)
           if r[0] == "milepael_over_frist"]
    assert rad and rad[0][3] == 2, rad


@pg
def test_avslutningen_lukker_funnene_og_stenger_arbeid(migrator):
    tenant = _tenantnavn("avslutt")
    c = _rt()
    try:
        _terskler(c, tenant, stillhet=1, frist=3650)
        pid = _prosjekt(c, tenant, start_siden=100)
        _plan(c, tenant, pid)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert _funn(migrator, tenant)
    c = _rt()
    try:
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m25_avslutt_prosjekt(%s,%s,NULL,'u')",
                      (tenant, pid))
        assert "begrunnelse" in str(ei.value)
        c.rollback()
        _sett_kontekst(c, tenant)
        assert c.execute(
            "SELECT m25_avslutt_prosjekt(%s,%s,'overlevert','u')",
            (tenant, pid)).fetchone()[0] is True
        c.commit()
        _sett_kontekst(c, tenant)
        assert c.execute(
            "SELECT m25_avslutt_prosjekt(%s,%s,'igjen','u')",
            (tenant, pid)).fetchone()[0] is False
        c.commit()
        # ARBEID PÅ ET AVSLUTTET PROSJEKT ville endret et forbruk noen
        # alt har konkludert på.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m25_registrer_arbeid(%s,%s,%s,current_date,60,1,"
                "'x','u')", (tenant, uuid.uuid4(), pid))
        assert "avsluttet prosjekt" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    assert _funn(migrator, tenant) == []
    lukkede = _funn(migrator, tenant, bare_apne=False)
    assert lukkede and all(r[4] is False for r in lukkede)


@pg
def test_arbeidet_er_append_only_og_kontrakten_frosset(migrator):
    tenant = _tenantnavn("append")
    c = _rt()
    try:
        _terskler(c, tenant)
        pid = _prosjekt(c, tenant)
        _arbeid(c, tenant, pid)
    finally:
        c.close()
    for sql in ("UPDATE prosjektarbeid SET kostnad_ore=1 WHERE tenant=%s",
                "DELETE FROM prosjektarbeid WHERE tenant=%s"):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_prosjekt_eier")
        with pytest.raises(psycopg.Error), migrator.transaction():
            migrator.execute(sql, (tenant,))
        migrator.rollback()
    rettigheter = migrator.execute(
        "SELECT privilege_type FROM information_schema.table_privileges"
        " WHERE grantee='disponit_prosjekt_eier'"
        "   AND table_name='prosjektarbeid' ORDER BY 1").fetchall()
    migrator.rollback()
    assert [r[0] for r in rettigheter] == ["INSERT", "SELECT"]
    for kolonne, verdi in (("budsjett_ore", "1"),
                           ("kunde_ref", "'X'"),
                           ("start", "current_date")):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_prosjekt_eier")
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(
                f"UPDATE prosjekt SET {kolonne} = {verdi}"
                " WHERE tenant=%s AND prosjekt_id=%s", (tenant, pid))
        assert "frosset" in str(ei.value), kolonne
        migrator.rollback()


@pg
def test_ingen_av_de_fem_tabellene_kan_tommes(migrator):
    """TRUNCATE AVVISES PÅ ALLE FEM (104s lærdom)."""
    tenant = _tenantnavn("truncate")
    c = _rt()
    try:
        _terskler(c, tenant, stillhet=1)
        pid = _prosjekt(c, tenant, start_siden=100)
        _plan(c, tenant, pid)
        _arbeid(c, tenant, pid, siden=90)
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
        migrator.execute("TRUNCATE public.prosjekt CASCADE")
    assert "TRUNCATE avvist" in str(ei.value), ei.value
    migrator.rollback()


def test_skrivedorene_som_leser_en_tilstand_laser_raden():
    sql = MIGRASJON.read_text(encoding="utf-8")
    for doer in ("m25_sett_betalingsplan", "m25_naa_milepael",
                 "m25_registrer_arbeid", "m25_avslutt_prosjekt"):
        i = sql.index(f"CREATE FUNCTION {doer}(")
        assert "FOR UPDATE" in sql[i:sql.index("END $$;", i)], doer


# ---------------------------------------------------------------------------
# INVARIANT 6: tenantlekkasje_i_prosjektregister
# ---------------------------------------------------------------------------

@pg
def test_invariant_tenantlekkasje_direkte(migrator):
    a = _tenantnavn("lekk-a")
    b = _tenantnavn("lekk-b")
    c = _rt()
    try:
        _terskler(c, a)
        _prosjekt(c, a)
        _terskler(c, b)
        _sett_kontekst(c, b)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT * FROM m25_prosjektstatus(%s)", (a,))
        assert "tenantkontekst" in str(ei.value)
        c.rollback()
        _sett_kontekst(c, a)
        assert c.execute("SELECT * FROM m25_prosjektstatus(%s)",
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
        _prosjekt(c, TENANT, kunde="EGEN-KUNDE")
        _terskler(c, fremmed)
        _prosjekt(c, fremmed, kunde="FREMMED-KUNDE")
    finally:
        c.close()
    cookie, _csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/prosjekt", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    kropp = json.dumps(r.json())
    assert "EGEN-KUNDE" in kropp
    assert "FREMMED-KUNDE" not in kropp


# ---------------------------------------------------------------------------
# Sveipearbeideren
# ---------------------------------------------------------------------------

@pg
def test_sveipen_nekter_kontekst(migrator):
    with _sv() as v:
        v.execute("SELECT set_config('disponit.tenant','t',false)")
        with pytest.raises(psycopg.Error) as ei:
            v.execute("SELECT * FROM m25_sveip_prosjekter(500)")
        assert "KRYSS-TENANT" in str(ei.value)
        v.rollback()


@pg
def test_sveiperollen_har_ingen_tabellrettigheter(migrator):
    if not PROSJEKTSVEIP_DSN:
        pytest.skip("DISPONIT_TEST_PROSJEKTSVEIP_DSN ikke satt")
    rader = migrator.execute(
        "SELECT table_name, privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_prosjektsveip'").fetchall()
    migrator.rollback()
    assert rader == [], rader


def test_sveipen_nekter_aa_starte_uten_egen_dsn(tmp_path, monkeypatch):
    from drift import kjor_prosjektsveip
    monkeypatch.delenv("DISPONIT_PROSJEKTSVEIP_URL", raising=False)
    monkeypatch.setenv("DISPONIT_PROSJEKTSVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.setattr("db.hemmeligheter.last_credentials", lambda: None)
    assert kjor_prosjektsveip.main() == 2


# ---------------------------------------------------------------------------
# INVARIANT 7: ui_axe_alvorlige_brudd
# ---------------------------------------------------------------------------

def test_invariant_ui_axe_bor_i_js_suiten():
    fil = (ROT / "platform" / "core" / "ui" / "test" / "prosjekt.test.js")
    assert fil.exists(), "prosjekt.test.js mangler"
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
        " ('https://m25.test', %s) RETURNING bruker_id",
        ("s25-" + secrets.token_hex(6),)).fetchone()[0]
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
@dekker("prosjekt_ulovlig_tilstand")
def test_http_milepael_uten_dokumentasjon_er_409(migrator, klient):
    """FEILVEIEN `prosjekt_ulovlig_tilstand`, ende til ende.

    DOKUMENTASJONSREFERANSEN ER 400 BEGGE VEIER, og det er RIKTIG:
    API-et er STRENGERE enn døren. `_tekst` avviser både tom streng og
    bare mellomrom som en feilformet kropp, så basens egen RAISE nås
    aldri over HTTP. Porten står likevel her, fordi den binder at det
    IKKE blir 409 — en tom referanse er en bruker som ikke fylte ut
    feltet, ikke et register som sier nei.

    409 MÅLES DER TILSTANDEN FAKTISK SIER NEI: arbeid ført på et
    avsluttet prosjekt, og et prosjektnavn som alt finnes hos samme
    kunde. Kroppen er velformet i begge tilfeller.

    Dette er også testen `test_api_porter.test_hver_feilvei_har_en_test`
    krever for den nye feilveien.
    """
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _hpost(klient, cookie, csrf, "/v1/prosjekt/terskler",
               {"budsjettvarsel_promille": 0, "milepael_frist_dogn": 7,
                "stillhet_dogn": 30})
    assert r.status_code in (200, 201), r.text
    r = _hpost(klient, cookie, csrf, "/v1/prosjekt",
               {"kunde_ref": "HTTP AS",
                "navn": "P-" + secrets.token_hex(3),
                "budsjett_ore": 100000, "start": "2026-07-01",
                "planlagt_slutt": "2026-12-31"})
    assert r.status_code in (200, 201), r.text
    pid = r.json()["prosjekt_id"]
    r = _hpost(klient, cookie, csrf,
               f"/v1/prosjekt/{pid}/betalingsplan",
               {"milepaeler": [{"navn": "A", "planlagt_dato": "2026-08-01",
                                "belop_ore": 1000}]})
    assert r.status_code in (200, 201), r.text
    # TOM OG BARE-MELLOMROM ER BEGGE 400: API-et er strengere enn
    # døren, og en tom referanse er en bruker som ikke fylte ut feltet.
    for ref in ("", "   "):
        r = _hpost(klient, cookie, csrf, f"/v1/prosjekt/{pid}/milepael",
                   {"milepael_nr": 1, "dokumentasjon_ref": ref})
        assert r.status_code == 400, (ref, r.text)
        assert r.json()["feil"] == "request_feilformet"
    # MED dokumentasjon: 200.
    r = _hpost(klient, cookie, csrf, f"/v1/prosjekt/{pid}/milepael",
               {"milepael_nr": 1, "dokumentasjon_ref": "rapport 12"})
    assert r.status_code in (200, 201), r.text
    # …og en betalingsplan uten milepæler er 400 (kroppen).
    r = _hpost(klient, cookie, csrf,
               f"/v1/prosjekt/{pid}/betalingsplan", {"milepaeler": []})
    assert r.status_code == 400, r.text
    # …og desimaltimer likeså.
    r = _hpost(klient, cookie, csrf, f"/v1/prosjekt/{pid}/arbeid",
               {"utfort": "2026-08-01", "minutter": 90.5,
                "kostnad_ore": 100, "beskrivelse": "x"})
    assert r.status_code == 400, r.text

    # DER TILSTANDEN SIER NEI: et prosjektnavn som alt finnes hos samme
    # kunde. Kroppen er velformet — registeret har det fra før.
    navn = "Dublett " + secrets.token_hex(3)
    kropp = {"kunde_ref": "HTTP AS", "navn": navn,
             "budsjett_ore": 1, "start": "2026-07-01",
             "planlagt_slutt": "2026-12-31"}
    r = _hpost(klient, cookie, csrf, "/v1/prosjekt", kropp)
    assert r.status_code in (200, 201), r.text
    r = _hpost(klient, cookie, csrf, "/v1/prosjekt", kropp)
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "prosjekt_ulovlig_tilstand"

    # …og arbeid ført på et AVSLUTTET prosjekt. Det ville endret et
    # forbruk noen alt har konkludert på.
    r = _hpost(klient, cookie, csrf, f"/v1/prosjekt/{pid}/avslutt",
               {"begrunnelse": "overlevert"})
    assert r.status_code in (200, 201), r.text
    r = _hpost(klient, cookie, csrf, f"/v1/prosjekt/{pid}/arbeid",
               {"utfort": "2026-08-01", "minutter": 60,
                "kostnad_ore": 100, "beskrivelse": "etterpå"})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "prosjekt_ulovlig_tilstand"


@pg
def test_http_lesingen_bruker_okonomi_read(migrator, klient):
    from api.app import RUTESCOPE
    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert RUTESCOPE[("GET", "/v1/prosjekt")] == "okonomi:read"
    assert "okonomi:read" in ROLLE_TIL_SCOPES["admin"]
    for rolle in ("leser", "sikkerhet", "godkjenner"):
        assert "okonomi:read" not in ROLLE_TIL_SCOPES[rolle], rolle
    cookie, _c = _browserokt(migrator, ["leser"])
    r = klient.get("/v1/prosjekt", cookies={_C_SESJON(): cookie})
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# §0
# ---------------------------------------------------------------------------

def test_grensen_ble_registrert_for_koden():
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m25-v1"]
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["punktbinding"] == {}
    egen = Path(__file__).read_text(encoding="utf-8")
    for inv in g["invarianter"]:
        assert inv in egen, f"invarianten {inv} har ingen port"
