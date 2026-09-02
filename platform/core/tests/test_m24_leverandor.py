"""M-24 leverandør- og innkjøpsagent v1 (migrasjon 105) — REGISTERET.

Grensen `m24-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR koden
(§0-regelen), og hver invariant der har minst én port her.

DEN SKARPESTE PORTEN ER `modulen_utforte_betaling`. En utgående betaling
er den ene handlingen i hele katalogen som er umulig å angre: pengene er
borte, og de er borte hos noen andre.

DEN NEST SKARPESTE er RETNINGEN PÅ ET SLA. Et brudd regnet med feil
fortegn er STILLE — det ser ut som at alt er i orden, og en leverandør
som leverer for dårlig går uoppdaget. `m24_bryter_sla` måles derfor på
alle fire typene, i begge retninger, og på at en UKJENT type er en
EXCEPTION og ikke `false`.

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

LEVERANDORSVEIP_DSN = os.environ.get("DISPONIT_TEST_LEVERANDORSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "105_m24_leverandorregister.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "leverandor.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "leverandor.py",
    ROT / "platform" / "drift" / "leverandorsveip.py",
    ROT / "platform" / "drift" / "kjor_leverandorsveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

# Modulens EGNE fem tabeller. Alt annet i `public` er «utenfor
# registeret», og to invarianter måles på at radantallet der står stille.
EGNE = ("leverandorterskel", "leverandorpart", "leveranseavtale",
        "leveranse", "leverandorfunn")


# ---------------------------------------------------------------------------
# Riggen
# ---------------------------------------------------------------------------

def _rt():
    from db.pg import koble
    return koble(DSN)


def _sv():
    from db.pg import koble
    return koble(LEVERANDORSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m24-{merke}-{secrets.token_hex(4)}"


def _terskler(c, tenant, *, pris=100, brudd=1, varsel=30, stillhet=90,
              aktor="u-test"):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m24_sett_terskler(%s,%s,%s,%s,%s,%s)",
                  (tenant, pris, brudd, varsel, stillhet,
                   aktor)).fetchone()[0]
    c.commit()
    return v


def _part(c, tenant, navn="Nordisk Drift AS", lid=None, aktor="u-test"):
    lid = lid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute("SELECT m24_registrer_leverandor(%s,%s,%s,%s,%s)",
              (tenant, lid, navn, None, aktor))
    c.commit()
    return lid


def _avtale(c, tenant, lid, *, ytelse="Drift av server",
            sla="oppetid_promille", verdi=995, pris=250000,
            fra_siden=100, til_om=200, aid=None, aktor="u-test"):
    aid = aid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m24_registrer_avtale(%s,%s,%s,%s,%s,%s,%s,"
        "       current_date - %s::int, current_date + %s::int, %s)",
        (tenant, aid, lid, ytelse, sla, verdi, pris, fra_siden, til_om,
         aktor))
    c.commit()
    return aid


def _maling(c, tenant, aid, *, siden=10, verdi=999, pris=250000,
            vid=None, aktor="u-test"):
    vid = vid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    ut = c.execute(
        "SELECT m24_registrer_leveranse(%s,%s,%s,current_date - %s::int,"
        "                               %s,%s,NULL,%s)",
        (tenant, vid, aid, siden, verdi, pris, aktor)).fetchone()[0]
    c.commit()
    return ut


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m24_sveip_leverandorer(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant, *, bare_apne=True):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT funntype, avtale_id, antall, over_grense, terskelversjon,"
        "       apen FROM leverandorfunn"
        " WHERE tenant=%s AND (%s IS FALSE OR apen) ORDER BY funntype",
        (tenant, bare_apne)).fetchall()
    m.rollback()
    return rader


def _tell_utenfor(m):
    """Radantall i hver tabell UTENFOR modulens fem, som migrator.

    Tabeller migrator ikke får lese hoppes over — de kan ikke telles, og
    en port som lot som den talte dem ville løyet. Antallet som FAKTISK
    telles sjekkes mot en nedre grense, ellers ville en tom liste vært en
    grønn test som målte ingenting.
    """
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


def _bare_kode(fil: Path) -> str:
    """Filens innhold uten kommentarer OG uten docstrings.

    Porten under måler KODE, ikke prosa: modulens egen docstring FORTELLER
    at det ikke finnes noen returverdi som er et prisforslag, og et rått
    delstrengsøk ville falt på nettopp den setningen. En port som tvang
    dokumentasjonen til å tie om dommen ville gjort dommen usynlig.
    """
    tekst = fil.read_text(encoding="utf-8")
    linjer = tekst.splitlines()
    if fil.suffix == ".py":
        tre = ast.parse(tekst)
        for node in ast.walk(tre):
            if not isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                continue
            krop = getattr(node, "body", None)
            if not krop:
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


# ---------------------------------------------------------------------------
# INVARIANT 1: modulen_utforte_betaling — V1-DOMMEN
# ---------------------------------------------------------------------------

def test_invariant_modulen_utforte_betaling_statisk():
    """Katalogteksten lover leverandørbetaling innen policygrenser. v1
    betaler ingenting — og dette er den handlingen i katalogen som er
    umulig å angre: pengene er borte, og de er borte hos noen andre.

    Porten er en AST-analyse, ikke et delstrengsøk: en import inne i en
    funksjon ville sluppet unna et `startswith("import ")`.

    MUTASJONEN SOM DREPER DENNE: legg `import httpx` inne i en funksjon
    i `api/leverandor.py`.
    """
    forbudt = {"smtplib", "email", "http", "httpx", "requests", "urllib",
               "aiohttp", "socket", "ftplib", "telnetlib", "webbrowser",
               "ssl", "asyncio", "stripe", "nets", "bankid"}
    for fil in MODULFILER:
        tre = ast.parse(fil.read_text(encoding="utf-8"))
        for node in ast.walk(tre):
            navn = []
            if isinstance(node, ast.Import):
                navn = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                navn = [node.module or ""] if node.level == 0 else []
            for n in navn:
                assert n.split(".")[0] not in forbudt, \
                    f"{fil.name} importerer {n} — v1 betaler ingenting"


def test_invariant_modulen_utforte_betaling_har_ingen_betalingstilstand():
    """ANDRE HALVDEL, målt på DATAMODELLEN og på rutene.

    En betalingsvei kan ikke finnes uten en tilstand som sier at noe ble
    betalt. 105 har ingen `betalt`-status, ingen kontonummer og ingen kø;
    `app.py` registrerer nøyaktig sju leverandørruter, og ingen av dem er
    en betaling.

    MUTASJONEN SOM DREPER DENNE: legg `betalt` i status-CHECKen, eller en
    åttende rute som heter `.../betal`.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    kode = "\n".join(l for l in sql.splitlines()
                     if not l.lstrip().startswith("--"))
    for ord_ in ("'betalt'", "'utbetalt'", "kontonummer", "iban", "bic",
                 "betalingsdato", "utbetaling", "hovedbok", "kontoplan"):
        assert ord_ not in kode.lower(), \
            f"105 bærer «{ord_}» — v1 betaler og posterer ingenting"

    from api.app import RUTESCOPE
    mine = sorted(sti for _m, sti in RUTESCOPE
                  if sti.startswith("/v1/leverandor"))
    assert mine == [
        "/v1/leverandor",
        "/v1/leverandor/avtale",
        "/v1/leverandor/part",
        "/v1/leverandor/terskler",
        "/v1/leverandor/{avtale_id:uuid}/avslutt",
        "/v1/leverandor/{avtale_id:uuid}/leveranse",
        "/v1/leverandor/{avtale_id:uuid}/leveranser",
    ], mine


@pg
def test_invariant_modulen_utforte_betaling_funksjonelt(migrator):
    """TREDJE HALVDEL, målt på VIRKELIGHETEN: en full sveip endrer ikke
    ett eneste radantall utenfor modulens egne fem tabeller — OG DEN
    AVSLUTTER INGEN AVTALE.

    Det siste er dommens kjerne: sveipen VET hvilke avtaler som er
    utløpt. En jobb som avsluttet dem om natten — eller betalte dem — er
    nøyaktig den fullmakten v1 ikke gir seg selv.

    MUTASJONEN SOM DREPER DENNE: la `m24_sveip_leverandorer` sette
    `status = 'avsluttet'` på utløpte avtaler.
    """
    tenant = _tenantnavn("betal")
    c = _rt()
    try:
        _terskler(c, tenant)
        lid = _part(c, tenant)
        # Avtalen er UTLØPT: sveipen vet det, og rører den likevel ikke.
        aid = _avtale(c, tenant, lid, fra_siden=400, til_om=-30)
    finally:
        c.close()
    for_ = _tell_utenfor(migrator)
    with _sv() as v:
        _sveip(v)
    etter = _tell_utenfor(migrator)
    assert for_ == etter, \
        ("sveipen endret radantall utenfor registeret: "
         + str({k: (for_[k], etter[k]) for k in for_
                if for_[k] != etter[k]}))
    _sett_kontekst(migrator, tenant)
    rad = migrator.execute(
        "SELECT status FROM leveranseavtale WHERE tenant=%s"
        " AND avtale_id=%s", (tenant, aid)).fetchone()
    migrator.rollback()
    assert rad[0] == "aktiv", "sveipen avsluttet en avtale"
    # …men den SA FRA: den utløpte avtalen er et funn.
    assert "avtale_utlopt" in {r[0] for r in _funn(migrator, tenant)}


@pg
def test_dorene_skriver_bare_i_registeret_og_i_evidenskjeden(migrator):
    """DØRENE, som er halvdelen som faktisk skriver: en full syklus —
    terskler, leverandør, avtale, måling, avslutning — endrer ikke ett
    radantall utenfor registeret, bortsett fra evidenskjeden.

    FEM DØRER, FEM EVIDENSRADER. Verken flere (en dør som logget to
    ganger) eller færre (en dør som ikke logget i det hele tatt).

    BELØP STÅR ALDRI I EVIDENSKJEDEN. Den skal gjenfinne HANDLINGEN, ikke
    arkivere pengestrømmen på nytt et sted til (101s dom).
    """
    tenant = _tenantnavn("evidens")
    for_ = _tell_utenfor(migrator)
    c = _rt()
    try:
        _terskler(c, tenant)
        lid = _part(c, tenant)
        aid = _avtale(c, tenant, lid, pris=100000)
        _maling(c, tenant, aid, pris=400000)
        _sett_kontekst(c, tenant)
        c.execute("SELECT m24_avslutt_avtale(%s,%s,'byttet','u')",
                  (tenant, aid))
        c.commit()
    finally:
        c.close()
    etter = _tell_utenfor(migrator)
    # EVIDENSKJEDEN ER DET ENE UNNTAKET, og det er ved design. Den tas ut
    # av likheten HER og måles for seg like under — å utelate den uten å
    # telle den ville vært et hull i porten. (Tellingen over ser den
    # dessuten som tom uansett: `revisjonslogg` har RLS FORCE, og
    # migrator uten tenantkontekst ser null rader.)
    assert "revisjonslogg" in for_, \
        "porten kjenner ikke evidenskjeden — da måler unntaket ingenting"
    for_.pop("revisjonslogg")
    etter.pop("revisjonslogg")
    assert for_ == etter, \
        ("dørene skrev utenfor registeret: "
         + str({k: (for_[k], etter[k]) for k in for_
                if for_[k] != etter[k]}))
    _sett_kontekst(migrator, tenant)
    rader = migrator.execute(
        "SELECT handling::text, begrunnelse::text, kilde FROM revisjonslogg"
        " WHERE tenant=%s ORDER BY handling", (tenant,)).fetchall()
    migrator.rollback()
    assert sorted(r[0] for r in rader) == [
        "avtale.avsluttet", "avtale.registrert", "leverandor.registrert",
        "leveranse.registrert", "terskler.satt"], \
        sorted(r[0] for r in rader)
    assert all(r[2] == "m24_leverandor" for r in rader)
    for a, b, _k in rader:
        for tall in ("100000", "400000", "1000,00", "4000,00"):
            assert tall not in a and tall not in b, \
                f"evidenskjeden bærer beløpet {tall}"


# ---------------------------------------------------------------------------
# INVARIANT 2: modulen_beregnet_ny_pris — GRENSEN MOT M-26
# ---------------------------------------------------------------------------

def test_invariant_modulen_beregnet_ny_pris():
    """Katalogen deler marginbeskyttelsen eksplisitt: M-24 OPPDAGER
    kostnadsøkningen, M-26 FORESLÅR ny pris. v1 holder seg på sin side av
    snittet og beregner ikke ny pris i det hele tatt.

    `prisavvik_promille` er AVVIKET mellom to MÅLTE tall — hva vi
    avtalte, og hva vi faktisk betalte. Det er oppdagelsen. Et forslag
    ville vært et tredje tall ingen har målt.

    MUTASJONEN SOM DREPER DENNE: legg `foreslatt_pris_ore` i
    `m24_avtalene`.
    """
    for fil in (MIGRASJON, FLATE, *MODULFILER):
        uten = _bare_kode(fil)
        for ord_ in ("foreslatt_pris", "ny_pris", "prisforslag",
                     "anbefalt_pris", "prisberegning", "m26_"):
            assert ord_ not in uten.lower(), \
                f"{fil.name} bærer «{ord_}» — M-26 foreslår, ikke M-24"
    # …og snittet står SKREVET, så neste utgave vet hvorfor.
    for fil in (MIGRASJON, MODULFILER[0]):
        assert "M-26" in fil.read_text(encoding="utf-8"), \
            f"{fil.name} nevner ikke grensen mot M-26"


# ---------------------------------------------------------------------------
# INVARIANT 3: belop_i_flyttall
# ---------------------------------------------------------------------------

@pg
def test_invariant_belop_i_flyttall_i_katalogen(migrator):
    """Måler KATALOGEN: hver beløpskolonne i modulens fem tabeller er
    `bigint`. Porten leser `information_schema` og ikke SQL-teksten, så
    et `ALTER TABLE ... TYPE double precision` i en SENERE migrasjon også
    faller på den.
    """
    rader = migrator.execute(
        "SELECT table_name, column_name, data_type"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND (column_name LIKE '%%ore%%' OR column_name LIKE '%%pris%%')"
        " ORDER BY table_name, column_name", (list(EGNE),)).fetchall()
    migrator.rollback()
    assert rader, "fant ingen beløpskolonner — porten måler ingenting"
    for tab, kol, typ in rader:
        if kol.endswith("_promille"):
            assert typ == "integer", f"{tab}.{kol} er {typ}"
            continue
        assert typ == "bigint", f"{tab}.{kol} er {typ}, ikke bigint"
    # …og INGEN kolonne i registeret er et flyttall, uansett navn.
    flyt = migrator.execute(
        "SELECT table_name, column_name, data_type"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND data_type IN ('numeric','real','double precision')",
        (list(EGNE),)).fetchall()
    migrator.rollback()
    assert flyt == [], flyt


def test_invariant_belop_i_flyttall_over_api():
    """…og API-et RUNDER ALDRI et flyttall, det avviser det.

    `True` avvises av samme grunn: i Python er `True` en `int`, og uten
    `isinstance(x, bool)` ville `{"sla_brudd_grense": true}` blitt
    grensen 1 — «ett brudd er nok», satt av en typefeil ingen ville sett.
    """
    from api.leverandor import MAKS_ORE, _heltall, _ore
    from api.policyadmin_http import _Avbrudd
    for verdi in (2.5, 100.0, True, False, "100", None, -1, MAKS_ORE):
        with pytest.raises(_Avbrudd):
            _ore({"p": verdi}, "p", "r")
    assert _ore({"p": 0}, "p", "r") == 0
    assert _ore({"p": 250000}, "p", "r") == 250000
    for verdi in (1.5, True, False, "3", None, 0, 1001):
        with pytest.raises(_Avbrudd):
            _heltall({"n": verdi}, "n", "r", 1, 1000)
    assert _heltall({"n": 3}, "n", "r", 1, 1000) == 3


# ---------------------------------------------------------------------------
# INVARIANT 4: maling_uten_avtalt_verdi
# ---------------------------------------------------------------------------

@pg
def test_invariant_maling_uten_avtalt_verdi(migrator):
    """EN MÅLING ER MOT EN AVTALT VERDI. En måling utenfor avtalens
    vindu er et tall uten dom — og et tall uten dom er verre enn intet
    tall, fordi noen handler på det.

    VAKTEN ER DEN BINDENDE, ikke døren: porten måler DIREKTE DML, så en
    regel som bare fantes i døren ville falt her.

    MUTASJONEN SOM DREPER DENNE: fjern vindussjekken fra
    `m24_leveranse_vakt`.
    """
    tenant = _tenantnavn("vindu")
    c = _rt()
    try:
        _terskler(c, tenant)
        lid = _part(c, tenant)
        aid = _avtale(c, tenant, lid, fra_siden=30, til_om=30)
        # Innenfor: går gjennom.
        assert _maling(c, tenant, aid, siden=10) is True
        # Etter vinduet.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m24_registrer_leveranse(%s,%s,%s,"
                "       current_date + 60, 999, 1, NULL, 'u')",
                (tenant, uuid.uuid4(), aid))
        assert "utenfor avtalens gyldighet" in str(ei.value)
        c.rollback()
        # FØR vinduet — den andre kanten, som en `>`-feil ville sluppet.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m24_registrer_leveranse(%s,%s,%s,"
                "       current_date - 60, 999, 1, NULL, 'u')",
                (tenant, uuid.uuid4(), aid))
        assert "utenfor avtalens gyldighet" in str(ei.value)
        c.rollback()
        # …og uten avtale i det hele tatt.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m24_registrer_leveranse(%s,%s,%s,current_date,"
                "       999,1,NULL,'u')",
                (tenant, uuid.uuid4(), uuid.uuid4()))
        assert "avtalen finnes ikke" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    # DIREKTE DML, forbi døren.
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_leverandor_eier")
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "INSERT INTO leveranse (tenant, leveranse_id, avtale_id,"
            " levert, faktisk_verdi, faktisk_pris_ore, registrert_av)"
            " VALUES (%s,%s,%s,current_date + 90,999,1,'u')",
            (tenant, uuid.uuid4(), aid))
    assert "utenfor avtalens gyldighet" in str(ei.value)
    migrator.rollback()


@pg
def test_en_maling_er_append_only(migrator):
    """En SLA-historikk som kunne redigeres ville vært en påstand, ikke
    en måling. Append-only helt ned til GRANTET, ikke bare i vakten."""
    tenant = _tenantnavn("append")
    c = _rt()
    try:
        _terskler(c, tenant)
        lid = _part(c, tenant)
        aid = _avtale(c, tenant, lid)
        _maling(c, tenant, aid)
    finally:
        c.close()
    for sql in ("UPDATE leveranse SET faktisk_verdi=1 WHERE tenant=%s",
                "DELETE FROM leveranse WHERE tenant=%s"):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_leverandor_eier")
        with pytest.raises(psycopg.Error), migrator.transaction():
            migrator.execute(sql, (tenant,))
        migrator.rollback()
    rettigheter = migrator.execute(
        "SELECT privilege_type FROM information_schema.table_privileges"
        " WHERE grantee='disponit_leverandor_eier'"
        "   AND table_name='leveranse' ORDER BY 1").fetchall()
    migrator.rollback()
    assert [r[0] for r in rettigheter] == ["INSERT", "SELECT"], rettigheter


@pg
def test_det_avtalte_er_frosset(migrator):
    """En avtale som kunne få ny `avtalt_verdi` i ettertid ville
    omskrevet hvert SLA-brudd som alt var målt mot den — historien ville
    rettet seg selv."""
    tenant = _tenantnavn("frosset")
    c = _rt()
    try:
        _terskler(c, tenant)
        lid = _part(c, tenant)
        aid = _avtale(c, tenant, lid)
    finally:
        c.close()
    for kolonne, verdi in (("avtalt_verdi", "900"),
                           ("avtalt_pris_ore", "1"),
                           ("sla_type", "'leveringstid_dogn'"),
                           ("gyldig_til", "current_date + 999")):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_leverandor_eier")
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(
                f"UPDATE leveranseavtale SET {kolonne} = {verdi}"
                " WHERE tenant=%s AND avtale_id=%s", (tenant, aid))
        assert "frosset" in str(ei.value), kolonne
        migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 5: terskel_hardkodet
# ---------------------------------------------------------------------------

def test_invariant_terskel_hardkodet():
    """Tersklene er TENANTENS, ikke modulens. «Ti prosent prisøkning er
    for mye» er en forretningsbeslutning, og en terskel kodet inn ville
    vært en fullmakt modulen ga seg selv — samme dom som M-23s
    purretrinn.

    PORTEN MÅLER FRAVÆRET AV EN TERSKELKONSTANT i modulens kode. Sveipen
    tar ingen terskelparameter i det hele tatt — grensene leses fra
    `leverandorterskel`.

    ÆRLIG OM HVA DETTE IKKE ER: tersklene går ikke gjennom M-1s
    policymotor (dokumentbasert, ingen tenant-innstilling). Invarianten
    er oppfylt i den forstand som betyr noe — tenanten eier og fører
    verdiene — men koblingen til M-1 er et NAVNGITT gap, skrevet i 105s
    hode og i `api/leverandor.py`.

    MUTASJONEN SOM DREPER DENNE: legg `PRISGRENSE_PROMILLE = 100` i
    `drift/leverandorsveip.py`.
    """
    import re
    for fil in MODULFILER:
        tekst = fil.read_text(encoding="utf-8")
        uten = "\n".join(l for l in tekst.splitlines()
                         if not l.lstrip().startswith("#"))
        # En modulkonstant som ser ut som en terskel. `MAKS_*` er tak på
        # KROPPEN (validering), ikke terskler — de er unntatt ved navn,
        # ikke ved mønster.
        for m in re.finditer(
                r"^([A-Z_]*(?:PROMILLE|TERSKEL|GRENSE|DOGN)[A-Z_]*)"
                r"\s*=\s*(\d+)", uten, re.M):
            assert m.group(1) in ("GRENSE",), \
                f"{fil.name} har terskelkonstanten {m.group(1)}={m.group(2)}"
    # `GRENSE` er sveipens TAK PÅ TRANSAKSJONEN (maks nye funn per
    # tenant per kjøring), ikke en terskel noe måles mot. Porten binder
    # det ved navn så unntaket ikke kan vokse i stillhet.
    from drift import leverandorsveip
    assert leverandorsveip.GRENSE == 500

    sql = MIGRASJON.read_text(encoding="utf-8")
    kode = "\n".join(l for l in sql.splitlines()
                     if not l.lstrip().startswith("--"))
    # Sveipen tar INGEN terskelparameter — grensene kommer fra tabellen.
    assert "m24_sveip_leverandorer(p_grense INT DEFAULT 500)" in kode
    assert "FROM public.leverandorterskel" in kode

    from api.leverandor import terskler_endepunkt
    doc = terskler_endepunkt.__doc__ or ""
    assert "M-1" in doc and "gap" in doc.lower(), \
        "gapet mot M-1 skal stå skrevet i dørens docstring"


@pg
def test_tersklene_versjoneres_og_kan_ikke_slettes(migrator):
    """Et funn bærer versjonen det ble vurdert mot, så en endret terskel
    ikke omskriver historien. Og en tenant kan ikke SLETTE seg til en
    tilstand uten terskler — det er en tilstand sveipen skal SI FRA om.
    """
    tenant = _tenantnavn("versjon")
    c = _rt()
    try:
        assert _terskler(c, tenant, pris=100) == 1
        assert _terskler(c, tenant, pris=250) == 2
        _sett_kontekst(c, tenant)
        rad = c.execute("SELECT * FROM m24_tersklene(%s)",
                        (tenant,)).fetchone()
        c.rollback()
        assert rad[0] == 250 and rad[4] == 2
    finally:
        c.close()
    # TO GJERDER, MÅLT HVER FOR SEG. Dørenes eier har ikke DELETE i det
    # hele tatt (SP-7-formen: bare det som trengs), og VAKTEN stanser den
    # som har det — her migrator, som eier tabellen. Et grant som ble
    # utvidet i en senere migrasjon ville ellers gått rett gjennom.
    rettigheter = migrator.execute(
        "SELECT privilege_type FROM information_schema.table_privileges"
        " WHERE grantee='disponit_leverandor_eier'"
        "   AND table_name='leverandorterskel' ORDER BY 1").fetchall()
    migrator.rollback()
    assert [r[0] for r in rettigheter] == ["INSERT", "SELECT", "UPDATE"], \
        rettigheter
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("DELETE FROM leverandorterskel WHERE tenant=%s",
                         (tenant,))
    assert "DELETE avvist" in str(ei.value)
    migrator.rollback()
    # …og versjonen kan ikke stå stille ved en endring.
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "UPDATE leverandorterskel SET prisstigning_promille=1"
            " WHERE tenant=%s", (tenant,))
    assert "versjonen må øke" in str(ei.value)
    migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 6: sla_brudd_uten_funn — OG RETNINGEN
# ---------------------------------------------------------------------------

@pg
@pytest.mark.parametrize("sla,avtalt,faktisk,brudd", [
    ("leveringstid_dogn", 3, 5, True),
    ("leveringstid_dogn", 3, 3, False),
    ("leveringstid_dogn", 3, 2, False),
    ("responstid_timer", 4, 5, True),
    ("responstid_timer", 4, 4, False),
    ("feilrate_promille", 10, 11, True),
    ("feilrate_promille", 10, 10, False),
    ("oppetid_promille", 995, 990, True),
    ("oppetid_promille", 995, 995, False),
    ("oppetid_promille", 995, 999, False),
])
def test_retningen_paa_et_sla_er_en_lukket_tabell(sla, avtalt, faktisk,
                                                  brudd):
    """DEN NEST SKARPESTE PORTEN. «Oppetid 995 promille» er brutt når den
    faktiske er LAVERE; «leveringstid 3 døgn» når den er HØYERE.

    Et brudd regnet med feil fortegn er STILLE: det ser ut som at alt er
    i orden, og en leverandør som leverer for dårlig går uoppdaget. Alle
    fire typene måles, i begge retninger, OG PÅ LIKHET — grensetilfellet
    er der en `>=` i stedet for `>` gjør hver avtale til et brudd.

    MUTASJONEN SOM DREPER DENNE: bytt `<` mot `>` i oppetidsarmen.
    """
    c = _rt()
    try:
        _sett_kontekst(c, TENANT)
        ut = c.execute("SELECT m24_bryter_sla(%s,%s,%s)",
                       (sla, avtalt, faktisk)).fetchone()[0]
        c.rollback()
    finally:
        c.close()
    assert ut is brudd


@pg
def test_en_ukjent_sla_type_er_en_exception_ikke_false(migrator):
    """Et `ELSE RETURN false` ville gjort hver framtidig SLA-type usynlig
    fra dagen den ble lagt til i CHECKen og glemt her — altså et stille
    «alt er i orden» om noe ingen har vurdert.

    OG DØREN SLÅR OPP RETNINGEN FØR RADEN FINNES: en avtale med en type
    ingen kan vurdere ville stått i registeret og aldri gitt et funn.
    """
    d = _rt()
    try:
        _sett_kontekst(d, TENANT)
        with pytest.raises(psycopg.Error) as ei:
            d.execute("SELECT m24_bryter_sla('kaffekvalitet',1,2)")
        assert "ukjent sla_type" in str(ei.value)
        d.rollback()
        # NULL ER INGEN MÅLING. `NULL > NULL` er NULL, og en NULL som
        # ble lest som «ikke brudd» er nøyaktig den stille feilen.
        for a, f in ((None, 2), (2, None), (None, None)):
            _sett_kontekst(d, TENANT)
            with pytest.raises(psycopg.Error) as ei:
                d.execute("SELECT m24_bryter_sla('oppetid_promille',%s,%s)",
                          (a, f))
            assert "ingen måling" in str(ei.value)
            d.rollback()
    finally:
        d.close()

    tenant = _tenantnavn("ukjent")
    c = _rt()
    try:
        lid = _part(c, tenant)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m24_registrer_avtale(%s,%s,%s,'X','kaffekvalitet',"
                "1,1,current_date,current_date+1,'u')",
                (tenant, uuid.uuid4(), lid))
        assert "ukjent sla_type" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    assert migrator.execute(
        "SELECT count(*) FROM leveranseavtale WHERE tenant=%s",
        (tenant,)).fetchone()[0] == 0
    migrator.rollback()


@pg
def test_invariant_sla_brudd_uten_funn(migrator):
    """Et SLA-brudd er et FUNN, ikke en stille rad i en målingstabell.
    IDEMPOTENSEN måles i samme test.

    GRENSEN ER TENANTENS: med `sla_brudd_grense = 2` er ETT brudd ikke et
    funn — én forsinket leveranse er livet.

    MUTASJONEN SOM DREPER DENNE: bytt `>=` mot `>` i kandidatens
    bruddgrense (da må det tre til der tenanten sa to).
    """
    tenant = _tenantnavn("brudd")
    c = _rt()
    try:
        _terskler(c, tenant, brudd=2, pris=100000)
        lid = _part(c, tenant)
        aid = _avtale(c, tenant, lid, verdi=995)
        _maling(c, tenant, aid, siden=30, verdi=990)   # brudd 1
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert "sla_brudd" not in {r[0] for r in _funn(migrator, tenant)}, \
        "ett brudd ble et funn der tenanten sa to"

    c = _rt()
    try:
        _maling(c, tenant, aid, siden=20, verdi=985)   # brudd 2
        _maling(c, tenant, aid, siden=10, verdi=999)   # innenfor
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    funn = {r[0]: r for r in _funn(migrator, tenant)}
    assert "sla_brudd" in funn
    assert funn["sla_brudd"][2] == 2, "antallet brudd står ikke på funnet"
    assert funn["sla_brudd"][3] == 0, "over_grense er ikke antall - grense"
    assert funn["sla_brudd"][4] == 1, "terskelversjonen står ikke på funnet"

    forst = {(r[0], r[1]) for r in _funn(migrator, tenant)}
    with _sv() as v:
        rad = _sveip(v)
    assert rad[1] == 0, "sveip nummer to skrev nye rader"
    assert {(r[0], r[1]) for r in _funn(migrator, tenant)} == forst


@pg
def test_prisen_over_terskel_regnes_i_heltall(migrator):
    """HELTALLSARITMETIKK OG INGEN DIVISJON i sammenligningen: `faktisk *
    1000 > avtalt * (1000 + promille)`. En terskel som «nesten» er
    passert er ingen terskel — og en avrunding ville avgjort hvilken.

    GRENSETILFELLET ER PORTEN: med terskel 100 promille og avtalt 100 000
    øre er 110 000 IKKE over (likhet), 110 001 er.
    """
    for pris, ventet in ((110000, False), (110001, True)):
        tenant = _tenantnavn(f"pris{pris}")
        c = _rt()
        try:
            _terskler(c, tenant, pris=100, brudd=99)
            lid = _part(c, tenant)
            aid = _avtale(c, tenant, lid, pris=100000)
            _maling(c, tenant, aid, siden=5, pris=pris)
        finally:
            c.close()
        with _sv() as v:
            _sveip(v)
        typer = {r[0] for r in _funn(migrator, tenant)}
        assert ("pris_over_terskel" in typer) is ventet, \
            f"{pris} øre mot 100000 med 100 promille: {typer}"


@pg
def test_de_ovrige_funntypene(migrator):
    """`avtale_uten_maling` og `ingen_terskel`.

    `ingen_terskel` er den som fanger en tenant som aldri kom i gang:
    aktive avtaler og ingen grenser å måle dem mot — og da ville hvert av
    de andre funnene vært en gjetning. De andre typene skal derfor IKKE
    reises samtidig.
    """
    uten = _tenantnavn("utenterskel")
    c = _rt()
    try:
        lid = _part(c, uten)
        _avtale(c, uten, lid, fra_siden=400, til_om=-30)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert {r[0] for r in _funn(migrator, uten)} == {"ingen_terskel"}, \
        "en tenant uten terskler fikk funn som forutsetter terskler"

    stille = _tenantnavn("stille")
    c = _rt()
    try:
        _terskler(c, stille, stillhet=30)
        lid = _part(c, stille)
        _avtale(c, stille, lid, fra_siden=200, til_om=200)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    typer = {r[0] for r in _funn(migrator, stille)}
    assert "avtale_uten_maling" in typer
    assert "ingen_terskel" not in typer


@pg
def test_funnene_lukkes_naar_avtalen_avsluttes(migrator):
    """Et åpent funn om en avtale som ikke lenger finnes er et varsel
    ingen kan gjøre noe med — og RADEN BESTÅR."""
    tenant = _tenantnavn("lukk")
    c = _rt()
    try:
        _terskler(c, tenant)
        lid = _part(c, tenant)
        aid = _avtale(c, tenant, lid, verdi=995)
        _maling(c, tenant, aid, verdi=900)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert len(_funn(migrator, tenant)) >= 1
    c = _rt()
    try:
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT m24_avslutt_avtale(%s,%s,'slutt','u')",
                         (tenant, aid)).fetchone()[0] is True
        c.commit()
        # …og en gang til er et STILLE JA.
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT m24_avslutt_avtale(%s,%s,'igjen','u')",
                         (tenant, aid)).fetchone()[0] is False
        c.commit()
    finally:
        c.close()
    assert _funn(migrator, tenant) == []
    lukkede = _funn(migrator, tenant, bare_apne=False)
    assert lukkede and all(r[5] is False for r in lukkede)
    # …og en avsluttet avtale gjenåpnes ikke.
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_leverandor_eier")
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "UPDATE leveranseavtale SET status='aktiv' WHERE tenant=%s"
            " AND avtale_id=%s", (tenant, aid))
    assert "gjenåpnes ikke" in str(ei.value)
    migrator.rollback()


@pg
def test_sla_oversikten_teller_alle_fire_typene(migrator):
    """ALLE FIRE TYPENE STÅR I SVARET, også de tenanten ikke bruker. En
    oversikt som endret form fra dag til dag kan ingen sammenligne over
    tid (104s aldersfordeling, samme dom)."""
    tenant = _tenantnavn("oversikt")
    c = _rt()
    try:
        _terskler(c, tenant)
        lid = _part(c, tenant)
        a1 = _avtale(c, tenant, lid, ytelse="Drift",
                     sla="oppetid_promille", verdi=995)
        a2 = _avtale(c, tenant, lid, ytelse="Support",
                     sla="responstid_timer", verdi=4)
        _maling(c, tenant, a1, siden=5, verdi=990)     # brudd
        _maling(c, tenant, a1, siden=4, verdi=999)     # innenfor
        _maling(c, tenant, a2, siden=3, verdi=8)       # brudd
        _sett_kontekst(c, tenant)
        rader = {r[0]: (r[1], r[2], r[3]) for r in c.execute(
            "SELECT * FROM m24_slaoversikt(%s)", (tenant,)).fetchall()}
        c.rollback()
    finally:
        c.close()
    assert list(rader) == ["leveringstid_dogn", "responstid_timer",
                           "feilrate_promille", "oppetid_promille"], \
        list(rader)
    assert rader["oppetid_promille"] == (1, 2, 1)
    assert rader["responstid_timer"] == (1, 1, 1)
    assert rader["leveringstid_dogn"] == (0, 0, 0)
    assert rader["feilrate_promille"] == (0, 0, 0)

    # ALLE TRE KOLONNENE MÅLER SAMME UTVALG: aktive avtaler. Første
    # utgave telte avtalene som var aktive NÅ, men målingene og bruddene
    # fra ALLE avtaler — og da kunne en rad stått med «0 avtaler, 1
    # måling, 1 brudd». Et tall om ett utvalg ved siden av to tall om et
    # annet er nøyaktig den slags rad ingen kan handle på. (CodeRabbit.)
    #
    # MUTASJONEN SOM DREPER DENNE: fjern `a.status = 'aktiv'` fra
    # målings- eller bruddkolonnen.
    c = _rt()
    try:
        _sett_kontekst(c, tenant)
        c.execute("SELECT m24_avslutt_avtale(%s,%s,'reforhandlet','u')",
                  (tenant, a2))
        c.commit()
        _sett_kontekst(c, tenant)
        etter = {r[0]: (r[1], r[2], r[3]) for r in c.execute(
            "SELECT * FROM m24_slaoversikt(%s)", (tenant,)).fetchall()}
        # …og HISTORIKKEN FORSVINNER IKKE: avtalen står fortsatt i
        # listen, med sine målinger og sitt bruddtall.
        avtaler = {r[0]: r for r in c.execute(
            "SELECT * FROM m24_avtalene(%s,100)", (tenant,)).fetchall()}
        c.rollback()
    finally:
        c.close()
    assert etter["responstid_timer"] == (0, 0, 0), etter
    assert etter["oppetid_promille"] == (1, 2, 1), etter
    assert avtaler[a2][10] == "avsluttet"
    assert (avtaler[a2][11], avtaler[a2][12]) == (1, 1)


@pg
def test_to_aktive_avtaler_paa_samme_ytelse_finnes_ikke(migrator):
    """To samtidige ville gjort «hva er avtalt» til et spørsmål med to
    svar — og et SLA-brudd til noe som avhenger av hvilken rad man leste.

    …men NÅR DEN FØRSTE ER AVSLUTTET, går den neste gjennom. Det er hele
    poenget med en delvis indeks framfor en total.
    """
    tenant = _tenantnavn("dublett")
    c = _rt()
    try:
        _terskler(c, tenant)
        lid = _part(c, tenant)
        aid = _avtale(c, tenant, lid, ytelse="Drift")
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(
                "SELECT m24_registrer_avtale(%s,%s,%s,'Drift',"
                "'oppetid_promille',990,1,current_date,current_date+1,'u')",
                (tenant, uuid.uuid4(), lid))
        c.rollback()
        _sett_kontekst(c, tenant)
        c.execute("SELECT m24_avslutt_avtale(%s,%s,'reforhandlet','u')",
                  (tenant, aid))
        c.commit()
        assert _avtale(c, tenant, lid, ytelse="Drift", verdi=990)
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Vaktene som ikke hører til én invariant
# ---------------------------------------------------------------------------

@pg
def test_ingen_av_de_fem_tabellene_kan_tommes(migrator):
    """TRUNCATE AVVISES PÅ ALLE FEM. Porten finnes fordi den ble brutt i
    104: en vakt som gjenbrukte radlogikken lot TG_OP='TRUNCATE' falle
    glatt gjennom til `RETURN NEW` — triggeren het `ingen_truncate` og
    slapp TRUNCATE igjennom. En vakt som ikke vakter er verre enn ingen,
    fordi den leses som beskyttelse.

    MUTASJONEN SOM DREPER DENNE: fjern TRUNCATE-armen fra én av vaktene.
    """
    tenant = _tenantnavn("truncate")
    c = _rt()
    try:
        _terskler(c, tenant)
        lid = _part(c, tenant)
        aid = _avtale(c, tenant, lid, verdi=995)
        _maling(c, tenant, aid, verdi=900)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    for tab in EGNE:
        _sett_kontekst(migrator, tenant)
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(f"TRUNCATE public.{tab}")
        # `leveranseavtale` og `leverandorpart` er REFERERT, så
        # fremmednøkkelen stanser dem før triggeren rekker å si noe. Den
        # veien er like lukket — men porten skal ikke late som den målte
        # vakten når det var fremmednøkkelen som stanset.
        assert ("TRUNCATE avvist" in str(ei.value)
                or "foreign key" in str(ei.value)), f"{tab}: {ei.value}"
        migrator.rollback()
    # …OG CASCADE, som er veien FORBI fremmednøkkelen. Uten denne linjen
    # ville porten vært grønn på en base der `leveranseavtale` kunne
    # tømmes med ett ord til.
    for tab in ("leveranseavtale", "leverandorpart"):
        _sett_kontekst(migrator, tenant)
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(f"TRUNCATE public.{tab} CASCADE")
        assert "TRUNCATE avvist" in str(ei.value), f"{tab}: {ei.value}"
        migrator.rollback()
    _sett_kontekst(migrator, tenant)
    assert migrator.execute(
        "SELECT count(*) FROM leverandorfunn WHERE tenant=%s",
        (tenant,)).fetchone()[0] >= 1
    migrator.rollback()


def test_skrivedorene_som_leser_en_tilstand_laser_raden():
    """De to dørene som leser en tilstand og handler på den, låser raden.

    Uten låsen kunne to samtidige avslutninger begge lese 'aktiv' og
    begge skrive en evidensrad, mens bare den ene UPDATE-en traff en rad.
    Det var CodeRabbits funn på 104s ettergivelsesdør; her står porten
    FØR feilen, ikke etter.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    for doer in ("m24_registrer_leveranse", "m24_avslutt_avtale"):
        i = sql.index(f"CREATE FUNCTION {doer}(")
        kropp = sql[i:sql.index("END $$;", i)]
        assert "FOR UPDATE" in kropp, f"{doer} låser ikke raden"


@pg
def test_avslutningen_tar_radlasen(migrator):
    """…og målt der låsen finnes: økt B blir STÅENDE så lenge økt A
    holder raden, og faller på sin egen `statement_timeout`."""
    tenant = _tenantnavn("las")
    a, b = _rt(), _rt()
    try:
        _terskler(a, tenant)
        lid = _part(a, tenant)
        aid = _avtale(a, tenant, lid)
        _sett_kontekst(a, tenant)
        a.execute("SELECT m24_avslutt_avtale(%s,%s,'A','u')", (tenant, aid))
        _sett_kontekst(b, tenant)
        b.execute("SET LOCAL statement_timeout = '1500ms'")
        with pytest.raises(psycopg.errors.QueryCanceled):
            b.execute("SELECT m24_avslutt_avtale(%s,%s,'B','u')",
                      (tenant, aid))
        b.rollback()
        a.commit()
    finally:
        a.close()
        b.close()
    _sett_kontekst(migrator, tenant)
    rad = migrator.execute(
        "SELECT status, avsluttet_begrunnelse FROM leveranseavtale"
        " WHERE tenant=%s AND avtale_id=%s", (tenant, aid)).fetchone()
    migrator.rollback()
    assert rad == ("avsluttet", "A")


# ---------------------------------------------------------------------------
# INVARIANT 7: tenantlekkasje_i_leverandorregister
# ---------------------------------------------------------------------------

@pg
def test_invariant_tenantlekkasje_direkte(migrator):
    a = _tenantnavn("lekk-a")
    b = _tenantnavn("lekk-b")
    c = _rt()
    try:
        _terskler(c, a)
        _part(c, a)
        _terskler(c, b)
        _part(c, b)
        _sett_kontekst(c, b)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT * FROM m24_leverandorstatus(%s)", (a,))
        assert "tenantkontekst" in str(ei.value)
        c.rollback()
        _sett_kontekst(c, a)
        assert c.execute("SELECT * FROM m24_leverandorstatus(%s)",
                         (a,)).fetchone()[1] == 1
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
        lid = _part(c, TENANT, navn="EGEN-LEVERANDOR")
        _avtale(c, TENANT, lid, ytelse="EGEN-YTELSE")
        _terskler(c, fremmed)
        flid = _part(c, fremmed, navn="FREMMED-LEVERANDOR")
        _avtale(c, fremmed, flid, ytelse="FREMMED-YTELSE")
    finally:
        c.close()
    cookie, _csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/leverandor", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    kropp = json.dumps(r.json())
    assert "EGEN-LEVERANDOR" in kropp
    assert "FREMMED-LEVERANDOR" not in kropp
    assert "FREMMED-YTELSE" not in kropp


# ---------------------------------------------------------------------------
# Sveipearbeideren
# ---------------------------------------------------------------------------

@pg
def test_sveipen_nekter_kontekst(migrator):
    with _sv() as v:
        v.execute("SELECT set_config('disponit.tenant','t',false)")
        with pytest.raises(psycopg.Error) as ei:
            v.execute("SELECT * FROM m24_sveip_leverandorer(500)")
        assert "KRYSS-TENANT" in str(ei.value)
        v.rollback()


@pg
def test_sveiperollen_har_ingen_tabellrettigheter(migrator):
    if not LEVERANDORSVEIP_DSN:
        pytest.skip("DISPONIT_TEST_LEVERANDORSVEIP_DSN ikke satt")
    rader = migrator.execute(
        "SELECT table_name, privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_leverandorsveip'").fetchall()
    migrator.rollback()
    assert rader == [], rader


def test_overlappende_sveip_hopper_over_og_rorer_ikke_feiltelleren():
    from drift import leverandorsveip

    class Falsk:
        def execute(self, sql, *a):
            class R:
                @staticmethod
                def fetchone():
                    return (False,)
            return R()

        def commit(self):
            pass

    r = leverandorsveip.kjor(Falsk(), tidligere_feil=1)
    assert r.hoppet_over is True
    assert r.feilet is False and r.alarm_utlost is False


def test_sveipen_nekter_aa_starte_uten_egen_dsn(tmp_path, monkeypatch):
    from drift import kjor_leverandorsveip
    monkeypatch.delenv("DISPONIT_LEVERANDORSVEIP_URL", raising=False)
    monkeypatch.setenv("DISPONIT_LEVERANDORSVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.setattr("db.hemmeligheter.last_credentials", lambda: None)
    assert kjor_leverandorsveip.main() == 2


def test_de_to_sveipene_deler_ikke_advisory_nokkel():
    """To jobber som låser på samme nøkkel ville blokkert hverandre uten
    grunn — og verre: den ene ville rapportert `hoppet_over` fordi den
    andre kjørte."""
    from drift import (avstemmingssveip, fordringssveip, henvendelsessveip,
                       leverandorsveip, onboardingsveip)
    nokler = [m.ARBEIDERNOKKEL for m in (
        avstemmingssveip, fordringssveip, henvendelsessveip,
        leverandorsveip, onboardingsveip)]
    assert len(set(nokler)) == len(nokler), nokler


# ---------------------------------------------------------------------------
# INVARIANT 8: ui_axe_alvorlige_brudd
# ---------------------------------------------------------------------------

def test_invariant_ui_axe_bor_i_js_suiten():
    fil = (ROT / "platform" / "core" / "ui" / "test" / "leverandor.test.js")
    assert fil.exists(), "leverandor.test.js mangler"
    assert "axe" in fil.read_text(encoding="utf-8"), \
        "UI-suiten kjører ingen axe-port for flaten"


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
        " ('https://m24.test', %s) RETURNING bruker_id",
        ("s24-" + secrets.token_hex(6),)).fetchone()[0]
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
@dekker("leverandor_ulovlig_tilstand")
def test_http_malingen_utenfor_vinduet_er_409_og_ikke_400(migrator, klient):
    """FEILVEIEN `leverandor_ulovlig_tilstand`, ende til ende.

    Datoen ER lesbar og avtalen finnes — og likevel er målingen et tall
    uten dom. Et 400 her ville sagt at brukeren skrev feil, når sannheten
    er at avtalen ikke gjaldt den dagen.

    Dette er også testen `test_api_porter.test_hver_feilvei_har_en_test`
    krever for den nye feilveien.
    """
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _hpost(klient, cookie, csrf, "/v1/leverandor/terskler",
               {"prisstigning_promille": 100, "sla_brudd_grense": 1,
                "avtale_varsel_dogn": 30, "maling_stillhet_dogn": 90})
    assert r.status_code in (200, 201), r.text
    r = _hpost(klient, cookie, csrf, "/v1/leverandor/part",
               {"navn": "HTTP AS " + secrets.token_hex(3)})
    assert r.status_code in (200, 201), r.text
    lid = r.json()["leverandor_id"]
    r = _hpost(klient, cookie, csrf, "/v1/leverandor/avtale",
               {"leverandor_id": lid,
                "ytelse": "Drift " + secrets.token_hex(3),
                "sla_type": "oppetid_promille", "avtalt_verdi": 995,
                "avtalt_pris_ore": 250000, "gyldig_fra": "2026-07-01",
                "gyldig_til": "2026-12-31"})
    assert r.status_code in (200, 201), r.text
    aid = r.json()["avtale_id"]
    # INNENFOR: 200.
    r = _hpost(klient, cookie, csrf,
               f"/v1/leverandor/{aid}/leveranse",
               {"levert": "2026-08-01", "faktisk_verdi": 999,
                "faktisk_pris_ore": 250000})
    assert r.status_code in (200, 201), r.text
    # UTENFOR: TILSTANDEN sier nei.
    r = _hpost(klient, cookie, csrf,
               f"/v1/leverandor/{aid}/leveranse",
               {"levert": "2027-08-01", "faktisk_verdi": 999,
                "faktisk_pris_ore": 250000})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "leverandor_ulovlig_tilstand"
    # …og en ukjent sla_type er 400: KROPPEN er feil.
    r = _hpost(klient, cookie, csrf, "/v1/leverandor/avtale",
               {"leverandor_id": lid, "ytelse": "Annet",
                "sla_type": "kaffekvalitet", "avtalt_verdi": 1,
                "avtalt_pris_ore": 1, "gyldig_fra": "2026-07-01",
                "gyldig_til": "2026-12-31"})
    assert r.status_code == 400, r.text
    assert r.json()["feil"] == "request_feilformet"
    # …og et flyttall likeså.
    r = _hpost(klient, cookie, csrf,
               f"/v1/leverandor/{aid}/leveranse",
               {"levert": "2026-08-02", "faktisk_verdi": 999,
                "faktisk_pris_ore": 2.5})
    assert r.status_code == 400, r.text


@pg
def test_http_lesingen_bruker_okonomi_read(migrator, klient):
    """Leverandørregisteret gjenbruker `okonomi:read` fra M-13 (101) og
    M-23 (104) — ikke et nytt scope. Hva vi har avtalt å betale, og hva
    vi faktisk betaler, er virksomhetens pengestrøm.
    """
    from api.app import RUTESCOPE
    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert RUTESCOPE[("GET", "/v1/leverandor")] == "okonomi:read"
    assert "okonomi:read" in ROLLE_TIL_SCOPES["admin"]
    for rolle in ("leser", "sikkerhet", "godkjenner"):
        assert "okonomi:read" not in ROLLE_TIL_SCOPES[rolle], rolle
    cookie, _c = _browserokt(migrator, ["leser"])
    r = klient.get("/v1/leverandor", cookies={_C_SESJON(): cookie})
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# §0
# ---------------------------------------------------------------------------

def test_grensen_ble_registrert_for_koden():
    """Grensen `m24-v1` sto i `KRAVGRENSER` fra klynge 3-fundamentet, før
    en eneste linje av modulen fantes (§0-regelen)."""
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m24-v1"]
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["punktbinding"] == {}
    egen = Path(__file__).read_text(encoding="utf-8")
    for inv in g["invarianter"]:
        assert inv in egen, f"invarianten {inv} har ingen port"
