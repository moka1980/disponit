"""M-23 kundefordringsagent v1 (migrasjon 104) — FORDRINGSREGISTERET.

Grensen `m23-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR koden
(§0-regelen), og hver invariant der har minst én port her.

DEN SKARPESTE PORTEN ER `modulen_sendte_til_kunde`. Dette er den eneste
modulen i klyngen der den forbudte handlingen treffer en UTENFORSTÅENDE:
en purring sendt for tidlig, til feil kunde, eller på et krav som alt er
betalt, har forlatt systemet i det øyeblikket den ble sendt.

DEN NEST SKARPESTE er `purretrinn_hoppet_over`. For en kunde er
forskjellen mellom en påminnelse og et inkassovarsel hele saken, og et
hopp er en eskalering ingen besluttet.

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

FORDRINGSVEIP_DSN = os.environ.get("DISPONIT_TEST_FORDRINGSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "104_m23_fordringsregister.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "fordring.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "fordring.py",
    ROT / "platform" / "drift" / "fordringssveip.py",
    ROT / "platform" / "drift" / "kjor_fordringssveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

PLAN = [
    {"navn": "Påminnelse", "dogn_etter_forfall": 3,
     "handling": "paaminnelse", "gebyr_ore": 0},
    {"navn": "Purring", "dogn_etter_forfall": 14,
     "handling": "purring", "gebyr_ore": 7000},
    {"navn": "Inkassovarsel", "dogn_etter_forfall": 28,
     "handling": "inkassovarsel", "gebyr_ore": 35000},
]


# ---------------------------------------------------------------------------
# Riggen
# ---------------------------------------------------------------------------

def _rt():
    from db.pg import koble
    return koble(DSN)


def _sv():
    from db.pg import koble
    return koble(FORDRINGSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m23-{merke}-{secrets.token_hex(4)}"


def _plan(c, tenant, trinn=None, aktor="u-test"):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m23_sett_purreplan(%s,%s::jsonb,%s)",
                  (tenant, json.dumps(PLAN if trinn is None else trinn),
                   aktor)).fetchone()[0]
    c.commit()
    return v


def _fordring(c, tenant, *, belop=250000, forfall_siden=0, nummer=None,
              kunde="Nordvik AS", fid=None, aktor="u-test"):
    fid = fid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m23_registrer_fordring(%s,%s,%s,%s,%s,"
        "       current_date - %s::int - 5, current_date - %s::int,%s)",
        (tenant, fid, kunde, nummer or ("F-" + secrets.token_hex(4)),
         belop, forfall_siden, forfall_siden, aktor))
    c.commit()
    return fid


def _betal(c, tenant, fid, belop, aktor="u-test"):
    _sett_kontekst(c, tenant)
    ut = c.execute(
        "SELECT m23_registrer_betaling(%s,%s,%s,%s,current_date,%s)",
        (tenant, uuid.uuid4(), fid, belop, aktor)).fetchone()[0]
    c.commit()
    return ut


def _trinn(c, tenant, fid, aktor="u-test"):
    _sett_kontekst(c, tenant)
    ut = c.execute("SELECT m23_neste_trinn(%s,%s,%s,NULL,%s)",
                   (tenant, uuid.uuid4(), fid, aktor)).fetchone()[0]
    c.commit()
    return ut


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m23_sveip_fordringer(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant, *, bare_apne=True):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT funntype, fordring_id, moden_for_trinn,"
        "       dogn_over_grense, apen FROM fordringsfunn"
        " WHERE tenant=%s AND (%s IS FALSE OR apen) ORDER BY funntype",
        (tenant, bare_apne)).fetchall()
    m.rollback()
    return rader


# Modulens EGNE fem tabeller. Alt annet i `public` er «utenfor
# registeret», og to invarianter måles på at radantallet der står stille.
EGNE = ("purreplan", "purretrinn", "fordring", "fordringshendelse",
        "fordringsfunn")


def _tell_utenfor(m):
    """Radantall i hver tabell UTENFOR modulens fem, som migrator.

    Tabeller migrator ikke får lese hoppes over — de kan ikke telles,
    og en port som lot som den talte dem ville løyet. Antallet som
    FAKTISK telles sjekkes mot en nedre grense, ellers ville en tom
    liste vært en grønn test som målte ingenting.
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


# ---------------------------------------------------------------------------
# INVARIANT 1: modulen_sendte_til_kunde — V1-DOMMEN, OG DEN STRENGESTE
# ---------------------------------------------------------------------------

def test_invariant_modulen_sendte_til_kunde_statisk():
    """Katalogteksten lover et FORSLAG om nedbetalingsplan til kunden. v1
    sender ingenting — og dette er den eneste modulen i klyngen der den
    forbudte handlingen treffer en UTENFORSTÅENDE.

    Porten er en AST-analyse, ikke et delstrengsøk: en import inne i en
    funksjon ville sluppet unna et `startswith("import ")`.

    MUTASJONEN SOM DREPER DENNE: legg `import smtplib` inne i en funksjon
    i `api/fordring.py`.
    """
    forbudt = {"smtplib", "email", "http", "httpx", "requests", "urllib",
               "aiohttp", "socket", "ftplib", "telnetlib", "webbrowser",
               "ssl", "asyncio", "twilio"}
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
                    f"{fil.name} importerer {n} — v1 sender ingenting"
                assert not n.endswith("ssrf"), \
                    f"{fil.name} importerer egressveien {n}"


def test_invariant_modulen_sendte_til_kunde_har_ingen_sendetilstand():
    """ANDRE HALVDEL, målt på DATAMODELLEN og på rutene.

    En sendevei kan ikke finnes uten en tilstand som sier at noe ble
    sendt. 104 har ingen `sendt`-status, ingen mottakeradresse og ingen
    kø; `app.py` registrerer nøyaktig sju fordringsruter, og ingen av dem
    er en sending.

    OG DEN POSTERER IKKE: samme snitt som M-13 (101). Ingen hovedbok,
    ingen kontoplan.

    MUTASJONEN SOM DREPER DENNE: legg `sendt` i status-CHECKen, eller en
    åttende rute som heter `.../purr`.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    kode = "\n".join(l for l in sql.splitlines()
                     if not l.lstrip().startswith("--"))
    for ord_ in ("'sendt'", "'purret'", "smtp", "mottakeradresse",
                 "epost_til", "webhook", "hovedbok", "kontoplan"):
        assert ord_ not in kode.lower(), \
            f"104 bærer «{ord_}» — v1 sender og posterer ingenting"

    from api.app import RUTESCOPE
    mine = sorted(sti for _m, sti in RUTESCOPE
                  if sti.startswith("/v1/fordring"))
    assert mine == [
        "/v1/fordring",
        "/v1/fordring",
        "/v1/fordring/purreplan",
        "/v1/fordring/{fordring_id:uuid}/betaling",
        "/v1/fordring/{fordring_id:uuid}/ettergi",
        "/v1/fordring/{fordring_id:uuid}/hendelser",
        "/v1/fordring/{fordring_id:uuid}/neste-trinn",
    ], mine


@pg
def test_invariant_modulen_sendte_til_kunde_funksjonelt(migrator):
    """TREDJE HALVDEL, målt på VIRKELIGHETEN: en full sveip endrer ikke
    ett eneste radantall utenfor modulens egne fem tabeller — OG DEN
    FLYTTER INGEN TRINN.

    Det siste er dommens kjerne: sveipen VET hvilke fordringer som er
    modne. En jobb som eskalerte om natten er nøyaktig den fullmakten v1
    ikke gir seg selv.

    MUTASJONEN SOM DREPER DENNE: la `m23_sveip_fordringer` sette
    `trinn = moden_for_trinn`.
    """
    tenant = _tenantnavn("send")
    c = _rt()
    try:
        _plan(c, tenant)
        fid = _fordring(c, tenant, forfall_siden=40)
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
    # …OG TRINNET STÅR URØRT. Fordringen er 40 døgn forfalt og moden for
    # trinn 3, men sveipen har ikke flyttet den.
    _sett_kontekst(migrator, tenant)
    rad = migrator.execute(
        "SELECT trinn FROM fordring WHERE tenant=%s AND fordring_id=%s",
        (tenant, fid)).fetchone()
    migrator.rollback()
    assert rad[0] == 0, "sveipen eskalerte mot en kunde"


# ---------------------------------------------------------------------------
# INVARIANT 3: postering_utenfor_registeret — SAMME SNITT SOM M-13
# ---------------------------------------------------------------------------

@pg
def test_invariant_postering_utenfor_registeret(migrator):
    """En fordring er et KRAV, ikke en postering. Modulen skriver i sine
    fem tabeller og ingen andre steder — heller ikke i M-13s bilag, som
    er nabomodulen og den nærmeste fristelsen.

    SVEIPEN ER MÅLT FOR SEG (`..._sendte_til_kunde_funksjonelt`). Denne
    porten måler DØRENE, som er den halvdelen som faktisk skriver: en
    full syklus — registrer, delbetal, flytt trinn, ettergi — endrer
    ikke ett radantall utenfor registeret.

    MUTASJONEN SOM DREPER DENNE: la `m23_registrer_betaling` skrive en
    rad i `bilag`.
    """
    tenant = _tenantnavn("postering")
    for_ = _tell_utenfor(migrator)
    c = _rt()
    try:
        _plan(c, tenant)
        fid = _fordring(c, tenant, belop=100000, forfall_siden=40)
        assert _betal(c, tenant, fid, 40000) is True
        _trinn(c, tenant, fid)
        _sett_kontekst(c, tenant)
        c.execute("SELECT m23_ettergi(%s,%s,%s,'tapt sak','u')",
                  (tenant, uuid.uuid4(), fid))
        c.commit()
    finally:
        c.close()
    etter = _tell_utenfor(migrator)
    # EVIDENSKJEDEN ER DET ENE UNNTAKET, og det er ved design: hver dør
    # legger én rad i `revisjonslogg`. Den tas ut av likheten HER og
    # måles for seg like under — å utelate den uten å telle den ville
    # vært et hull i porten. (Tellingen over ser den dessuten som tom
    # uansett: `revisjonslogg` har RLS FORCE, og migrator uten
    # tenantkontekst ser null rader.)
    assert "revisjonslogg" in for_, \
        "porten kjenner ikke evidenskjeden — da måler unntaket ingenting"
    for_.pop("revisjonslogg")
    etter.pop("revisjonslogg")
    assert for_ == etter, \
        ("dørene skrev utenfor registeret: "
         + str({k: (for_[k], etter[k]) for k in for_
                if for_[k] != etter[k]}))
    # …OG DE TRE HENDELSENE STÅR I MODULENS EGET REGISTER, så porten
    # over ikke er grønn fordi ingenting skjedde.
    _sett_kontekst(migrator, tenant)
    assert migrator.execute(
        "SELECT count(*) FROM fordringshendelse WHERE tenant=%s",
        (tenant,)).fetchone()[0] == 3
    # FEM DØRER, FEM EVIDENSRADER: purreplan, fordring, betaling, trinn,
    # ettergivelse. Verken flere (en dør som logget to ganger) eller
    # færre (en dør som ikke logget i det hele tatt).
    rader = migrator.execute(
        "SELECT handling, beslutning, kilde FROM revisjonslogg"
        " WHERE tenant=%s ORDER BY handling", (tenant,)).fetchall()
    migrator.rollback()
    assert len(rader) == 5, f"{len(rader)} evidensrader, ikke 5"
    assert sorted(r[0] for r in rader) == [
        "fordring.betaling", "fordring.ettergitt", "fordring.registrert",
        "fordring.trinn", "purreplan.satt"], sorted(r[0] for r in rader)
    assert all(r[2] == "m23_fordring" for r in rader)
    # BELØP STÅR ALDRI I EVIDENSKJEDEN. Den skal gjenfinne HANDLINGEN,
    # ikke arkivere pengestrømmen på nytt et sted til (101s dom). Porten
    # leter etter DE FAKTISKE TALLENE fra syklusen over.
    logg = migrator.execute(
        "SELECT handling::text, begrunnelse::text FROM revisjonslogg"
        " WHERE tenant=%s", (tenant,)).fetchall()
    migrator.rollback()
    for a, b in logg:
        for tall in ("100000", "40000", "1000,00", "400,00"):
            assert tall not in a and tall not in b, \
                f"evidenskjeden bærer beløpet {tall}"


@pg
def test_fordringsrollen_naar_ikke_naboregisteret(migrator):
    """SP-7 på tvers av moduler: `disponit_fordring_eier` har ingen
    rettighet på M-13s tabeller, og `disponit_avstemming_eier` har ingen
    på M-23s. To registre i samme base er to registre bare så lenge
    grantene sier det.
    """
    rader = migrator.execute(
        "SELECT grantee, table_name, privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE (grantee='disponit_fordring_eier'"
        "        AND table_name IN ('bankkonto','bankpost','bilag',"
        "                           'avstemming','avstemmingsfunn'))"
        "    OR (grantee='disponit_avstemming_eier'"
        "        AND table_name IN ('purreplan','purretrinn','fordring',"
        "                           'fordringshendelse','fordringsfunn'))"
        ).fetchall()
    migrator.rollback()
    assert rader == [], rader
    # …og porten måler noe: rollen HAR rettigheter på sine egne.
    egne = migrator.execute(
        "SELECT count(*) FROM information_schema.table_privileges"
        " WHERE grantee='disponit_fordring_eier'"
        "   AND table_name='fordring'").fetchone()[0]
    migrator.rollback()
    assert egne > 0, "rollen har ingen rettigheter i det hele tatt"


def test_invariant_postering_utenfor_registeret_statisk():
    """…og KILDEN nevner ikke naboregisteret. En modul som importerte
    `bilag` ville hatt veien åpen selv om ingen gikk den i dag.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    kode = "\n".join(l for l in sql.splitlines()
                      if not l.lstrip().startswith("--"))
    for tab in ("bankkonto", "bankpost", "bilag", "avstemming",
                "avstemmingsfunn"):
        assert f"public.{tab}" not in kode, \
            f"104 rører M-13s {tab}"
    for fil in MODULFILER:
        tekst = fil.read_text(encoding="utf-8")
        uten = "\n".join(l for l in tekst.splitlines()
                          if not l.lstrip().startswith("#"))
        for ord_ in ("m13_", "avstemming", "bilag"):
            assert ord_ not in uten.lower(), \
                f"{fil.name} bærer «{ord_}»"


# ---------------------------------------------------------------------------
# INVARIANT 2: belop_i_flyttall
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
        " WHERE table_schema='public'"
        "   AND table_name IN ('fordring','fordringshendelse',"
        "                      'purretrinn')"
        "   AND (column_name LIKE '%ore%' OR column_name LIKE '%belop%')"
        " ORDER BY table_name, column_name").fetchall()
    migrator.rollback()
    assert rader, "fant ingen beløpskolonner — porten måler ingenting"
    for tab, kol, typ in rader:
        assert typ == "bigint", f"{tab}.{kol} er {typ}, ikke bigint"


def test_invariant_belop_i_flyttall_over_api():
    """…og API-et RUNDER ALDRI et flyttall, det avviser det.

    `True` avvises av samme grunn: i Python er `True` en `int`, og uten
    `isinstance(x, bool)` ville `{"belop_ore": true}` blitt 1 øre.
    """
    from api.fordring import MAKS_ORE, _ore
    from api.policyadmin_http import _Avbrudd
    for verdi in (2.5, 100.0, True, False, "100", None, 0, -1, MAKS_ORE):
        with pytest.raises(_Avbrudd):
            _ore({"belop_ore": verdi}, "belop_ore", "r")
    assert _ore({"belop_ore": 5000}, "belop_ore", "r") == 5000
    # `minst=0` brukes for gebyr: null er lovlig, negativt er det ikke.
    assert _ore({"g": 0}, "g", "r", minst=0) == 0
    with pytest.raises(_Avbrudd):
        _ore({"g": -1}, "g", "r", minst=0)


# ---------------------------------------------------------------------------
# INVARIANT 3: purretrinn_hardkodet
# ---------------------------------------------------------------------------

def test_invariant_purretrinn_hardkodet():
    """Purretrinnene er TENANTENS, ikke modulens. «Etter 14 døgn purrer
    vi» er en forretningsbeslutning, og et trinn kodet inn ville vært en
    fullmakt modulen ga seg selv.

    PORTEN MÅLER FRAVÆRET AV EN DØGNKONSTANT i modulens kode og i
    sveipens SQL. Sveipefunksjonen tar ingen døgnparameter i det hele
    tatt — grensene leses fra `purretrinn`.

    ÆRLIG OM HVA DETTE IKKE ER: planen går ikke gjennom M-1s policymotor
    (dokumentbasert, ingen tenant-innstilling). Invarianten er oppfylt i
    den forstand som betyr noe — tenanten eier og fører verdiene — men
    koblingen til M-1 er et NAVNGITT gap, skrevet i 104s hode og i
    `api/fordring.py`.

    MUTASJONEN SOM DREPER DENNE: legg `DOGN_PURRING = 14` i
    `drift/fordringssveip.py`.
    """
    import re
    for fil in MODULFILER:
        tekst = fil.read_text(encoding="utf-8")
        uten = "\n".join(l for l in tekst.splitlines()
                         if not l.lstrip().startswith("#"))
        # En modulkonstant som ser ut som en døgngrense. `MAKS_DOGN` og
        # `MAKS_TRINN` er tak på KROPPEN (validering), ikke terskler —
        # de er unntatt ved navn, ikke ved mønster.
        for m in re.finditer(r"^([A-Z_]*DOGN[A-Z_]*)\s*=\s*(\d+)",
                             uten, re.M):
            assert m.group(1) in ("MAKS_DOGN",), \
                f"{fil.name} har døgnkonstanten {m.group(1)}={m.group(2)}"
    sql = MIGRASJON.read_text(encoding="utf-8")
    kode = "\n".join(l for l in sql.splitlines()
                     if not l.lstrip().startswith("--"))
    # Sveipen tar INGEN døgnparameter — grensene kommer fra tabellen.
    assert "m23_sveip_fordringer(p_grense INT DEFAULT 500)" in kode
    assert "dogn_etter_forfall" in kode
    # …og trinnene leses fra `purretrinn` i kandidatene.
    assert "FROM public.purretrinn" in kode

    from api.fordring import purreplan_endepunkt
    doc = purreplan_endepunkt.__doc__ or ""
    assert "M-1" in doc and "gap" in doc.lower(), \
        "gapet mot M-1 skal stå skrevet i dørens docstring"


@pg
def test_purreplanen_kan_ikke_gaa_bakover(migrator):
    """Trinnene må STIGE i tid. En plan der trinn 2 kommer før trinn 1 er
    en eskalering som går bakover, og da betyr «trinn» ingenting.

    MUTASJONEN SOM DREPER DENNE: fjern `m23_purretrinn_vakt`.
    """
    tenant = _tenantnavn("bakover")
    c = _rt()
    try:
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m23_sett_purreplan(%s,%s::jsonb,'u')",
                      (tenant, json.dumps([
                          {"navn": "A", "dogn_etter_forfall": 20,
                           "handling": "purring"},
                          {"navn": "B", "dogn_etter_forfall": 5,
                           "handling": "inkassovarsel"}])))
        assert "bakover" in str(ei.value)
        c.rollback()
        # …og en plan uten trinn er ingen plan.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m23_sett_purreplan(%s,'[]'::jsonb,'u')",
                      (tenant,))
        assert "uten trinn" in str(ei.value)
        c.rollback()
        assert _plan(c, tenant) >= 1
    finally:
        c.close()


# ---------------------------------------------------------------------------
# INVARIANT 4: purretrinn_hoppet_over
# ---------------------------------------------------------------------------

@pg
def test_invariant_purretrinn_hoppet_over(migrator):
    """ETT HAKK, FRAMOVER. For kunden er forskjellen mellom en
    påminnelse og et inkassovarsel hele saken, og et hopp er en
    eskalering ingen besluttet.

    DØREN HAR INGEN TRINNPARAMETER — den flytter til NESTE. Vakten er
    likevel den bindende, og porten måler den på DIREKTE DML.

    MUTASJONEN SOM DREPER DENNE: fjern hakksjekken fra
    `m23_fordring_vakt`.
    """
    tenant = _tenantnavn("hopp")
    c = _rt()
    try:
        _plan(c, tenant)
        fid = _fordring(c, tenant, forfall_siden=40)
        assert _trinn(c, tenant, fid) == 1
        assert _trinn(c, tenant, fid) == 2
        assert _trinn(c, tenant, fid) == 3
        # Planen har tre trinn; det fjerde finnes ikke.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m23_neste_trinn(%s,%s,%s,NULL,'u')",
                      (tenant, uuid.uuid4(), fid))
        assert "siste trinn" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    # DIREKTE DML: hoppet fra 3 til 5, og skrittet bakover fra 3 til 2.
    for nytt in (5, 2, 0):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_fordring_eier")
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(
                "UPDATE fordring SET trinn=%s WHERE tenant=%s"
                " AND fordring_id=%s", (nytt, tenant, fid))
        assert "ETT hakk" in str(ei.value), nytt
        migrator.rollback()


@pg
def test_en_avsluttet_fordring_eskalerer_ikke(migrator):
    """En betalt eller ettergitt fordring tar ikke imot flere trinn — og
    trinnet kan ikke flyttes på den i det hele tatt.

    Uten regelen ville en fordring som ble gjort opp i går kunne fått et
    inkassovarsel i dag, og det er nøyaktig skaden som ikke kan trekkes
    tilbake.
    """
    tenant = _tenantnavn("avsluttet")
    c = _rt()
    try:
        _plan(c, tenant)
        fid = _fordring(c, tenant, belop=100000, forfall_siden=40)
        _trinn(c, tenant, fid)
        # Dørens svar er SP-2s «ny», ikke «kravet ble lukket» — og
        # lukkingen måles på det som faktisk betyr noe: neste eskalering
        # avvises fordi kravet er gjort opp i samme transaksjon.
        assert _betal(c, tenant, fid, 100000) is True
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m23_neste_trinn(%s,%s,%s,NULL,'u')",
                      (tenant, uuid.uuid4(), fid))
        assert "avsluttet fordring eskalerer ikke" in str(ei.value)
        c.rollback()
        # …og en innbetaling på et oppgjort krav er en tilgodehavende.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m23_registrer_betaling(%s,%s,%s,100,current_date,"
                "                              'u')",
                (tenant, uuid.uuid4(), fid))
        assert "tilgodehavende" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_fordring_eier")
    with pytest.raises(psycopg.Error) as ei:
        migrator.execute(
            "UPDATE fordring SET trinn=2 WHERE tenant=%s AND fordring_id=%s",
            (tenant, fid))
    assert "gjort opp" in str(ei.value)
    migrator.rollback()


@pg
def test_overbetaling_avvises_og_saldoen_er_summen_av_hendelsene(migrator):
    """Overbetaling er en TILGODEHAVENDE, ikke en fordring — og
    `betalt_ore` er summen av hendelsene, ikke et fritt tall. En
    vedlikeholdt avledning ingen kontrollerer er en denormalisering som
    driver (100s `sist_etterprovd`-form).

    MUTASJONEN SOM DREPER DENNE: fjern summeringssjekken fra
    `m23_fordring_vakt`.
    """
    tenant = _tenantnavn("saldo")
    c = _rt()
    try:
        _plan(c, tenant)
        fid = _fordring(c, tenant, belop=250000)
        _betal(c, tenant, fid, 100000)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m23_registrer_betaling(%s,%s,%s,200000,"
                "                              current_date,'u')",
                (tenant, uuid.uuid4(), fid))
        assert "verbetaling" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_fordring_eier")
    with pytest.raises(psycopg.Error) as ei:
        migrator.execute(
            "UPDATE fordring SET betalt_ore=200000 WHERE tenant=%s"
            " AND fordring_id=%s", (tenant, fid))
    assert "summen av" in str(ei.value)
    migrator.rollback()
    # …og hendelsene er append-only helt ned til grantet.
    for sql in (
        "UPDATE fordringshendelse SET belop_ore=1 WHERE tenant=%s",
        "DELETE FROM fordringshendelse WHERE tenant=%s",
    ):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_fordring_eier")
        with pytest.raises(psycopg.Error), migrator.transaction():
            migrator.execute(sql, (tenant,))
        migrator.rollback()


@pg
def test_ettergivelse_koster_en_begrunnelse(migrator):
    """Å avskrive et krav uten å si hvorfor er den ene handlingen ingen
    kan etterprøve senere."""
    tenant = _tenantnavn("ettergi")
    c = _rt()
    try:
        _plan(c, tenant)
        fid = _fordring(c, tenant)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m23_ettergi(%s,%s,%s,NULL,'u')",
                      (tenant, uuid.uuid4(), fid))
        assert "begrunnelse" in str(ei.value)
        c.rollback()
        _sett_kontekst(c, tenant)
        assert c.execute(
            "SELECT m23_ettergi(%s,%s,%s,'konkurs hos kunden','u')",
            (tenant, uuid.uuid4(), fid)).fetchone()[0] is True
        c.commit()
        # …og en gang til er et STILLE JA.
        _sett_kontekst(c, tenant)
        assert c.execute(
            "SELECT m23_ettergi(%s,%s,%s,'igjen','u')",
            (tenant, uuid.uuid4(), fid)).fetchone()[0] is False
        c.commit()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# INVARIANT 5: forfalt_fordring_uten_funn
# ---------------------------------------------------------------------------

@pg
def test_invariant_forfalt_fordring_uten_funn(migrator):
    """En fordring som passerer sitt purretrinn er et FUNN, ikke en
    stille gammel rad. IDEMPOTENSEN måles i samme test.

    MUTASJONEN SOM DREPER DENNE: fjern `m.moden > f.trinn` fra
    kandidatene (da blir hver forfalt fordring et funn, også de som er
    fulgt opp).
    """
    tenant = _tenantnavn("funn")
    c = _rt()
    try:
        _plan(c, tenant)
        moden = _fordring(c, tenant, forfall_siden=40, kunde="Moden AS")
        fulgt = _fordring(c, tenant, forfall_siden=40, kunde="Fulgt AS")
        # `fulgt` føres helt fram til trinn 3 — den er IKKE et funn.
        for _ in range(3):
            _trinn(c, tenant, fulgt)
        _fordring(c, tenant, forfall_siden=-5, kunde="Fersk AS")
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    funn = {r[1]: r for r in _funn(migrator, tenant)}
    assert moden in funn, "en moden fordring ble ikke et funn"
    assert funn[moden][0] == "trinn_forfalt"
    assert funn[moden][2] == 3, "moden_for_trinn står ikke på funnet"
    assert fulgt not in funn, "en fulgt opp fordring ble et funn"
    assert len(funn) == 1, funn

    forst = {(r[0], r[1]) for r in _funn(migrator, tenant)}
    with _sv() as v:
        rad = _sveip(v)
    assert rad[1] == 0, "sveip nummer to skrev nye rader"
    assert {(r[0], r[1]) for r in _funn(migrator, tenant)} == forst


@pg
def test_manglende_purreplan_og_urort_krav_blir_funn(migrator):
    """De to andre funntypene. `ingen_purreplan` er den som fanger en
    tenant som aldri kom i gang: forfalte krav og ingen plan å måle dem
    mot — og da vet ingen når noe eskalerer.
    """
    uten = _tenantnavn("utenplan")
    c = _rt()
    try:
        _fordring(c, uten, forfall_siden=20)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert {r[0] for r in _funn(migrator, uten)} == {"ingen_purreplan"}

    med = _tenantnavn("urort")
    c = _rt()
    try:
        _plan(c, med)
        _fordring(c, med, forfall_siden=120)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    typer = {r[0] for r in _funn(migrator, med)}
    assert "forfalt_uten_trinn" in typer
    assert "trinn_forfalt" in typer
    assert "ingen_purreplan" not in typer


@pg
def test_funnet_lukkes_naar_kravet_gjores_opp(migrator):
    """Et funn som ikke lenger gjelder lukkes — og RADEN BESTÅR."""
    tenant = _tenantnavn("lukkfunn")
    c = _rt()
    try:
        _plan(c, tenant)
        fid = _fordring(c, tenant, belop=50000, forfall_siden=40)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert len(_funn(migrator, tenant)) == 1
    c = _rt()
    try:
        _betal(c, tenant, fid, 50000)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert _funn(migrator, tenant) == []
    lukkede = _funn(migrator, tenant, bare_apne=False)
    assert len(lukkede) == 1 and lukkede[0][4] is False


@pg
def test_aldersfordelingen_er_riktig_paa_kantene(migrator):
    """BØTTEKANTENE. En aldersfordeling som er feil på kanten er feil
    overalt der det betyr noe — og det er nettopp kantene manifestets
    datasettkrav peker på.

    Bøttene er HALVÅPNE: 30 døgn hører til `1_30`, 31 til `31_60`.

    ALLE BØTTENE STÅR I SVARET, også de tomme. En fordeling som endret
    form fra dag til dag kan ingen sammenligne over tid.
    """
    tenant = _tenantnavn("alder")
    c = _rt()
    try:
        _plan(c, tenant)
        for dager, belop in ((-1, 1000), (30, 2000), (31, 4000),
                             (60, 8000), (61, 16000), (90, 32000),
                             (91, 64000)):
            _fordring(c, tenant, belop=belop, forfall_siden=dager)
        _sett_kontekst(c, tenant)
        rader = {r[0]: (r[1], r[2]) for r in c.execute(
            "SELECT * FROM m23_aldersfordeling(%s)", (tenant,)).fetchall()}
        c.rollback()
    finally:
        c.close()
    assert list(rader) == ["ikke_forfalt", "1_30", "31_60", "61_90",
                           "over_90"], list(rader)
    assert rader["ikke_forfalt"] == (1, 1000)
    assert rader["1_30"] == (1, 2000)
    assert rader["31_60"] == (2, 12000)      # 31 og 60
    assert rader["61_90"] == (2, 48000)      # 61 og 90
    assert rader["over_90"] == (1, 64000)    # 91


@pg
def test_ingen_av_de_fem_tabellene_kan_tommes(migrator):
    """TRUNCATE AVVISES PÅ ALLE FEM. Porten finnes fordi den ble brutt:
    `m23_funn_ingen_truncate` gjenbrukte radvakten, og TG_OP='TRUNCATE'
    falt glatt gjennom til `RETURN NEW` — triggeren het `ingen_truncate`
    og slapp TRUNCATE igjennom. En vakt som ikke vakter er verre enn
    ingen, fordi den leses som beskyttelse.

    `purreplan` har ingen egen vakt og trenger ingen: `purretrinn`
    refererer den, så TRUNCATE uten CASCADE feiler på fremmednøkkelen og
    med CASCADE treffer den purretrinnvakten. Porten krever at den
    AVVISES — ikke hvilken av de to veiene som stanser den.

    MUTASJONEN SOM DREPER DENNE: fjern TRUNCATE-armen fra én av vaktene.
    """
    tenant = _tenantnavn("truncate")
    c = _rt()
    try:
        _plan(c, tenant)
        fid = _fordring(c, tenant, forfall_siden=40)
        _trinn(c, tenant, fid)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    for tab in ("fordringsfunn", "fordringshendelse", "fordring",
                "purretrinn", "purreplan"):
        _sett_kontekst(migrator, tenant)
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(f"TRUNCATE public.{tab}")
        assert "TRUNCATE" in str(ei.value) or "foreign key" in str(ei.value), \
            f"{tab}: {ei.value}"
        migrator.rollback()
    # …og radene står der fortsatt.
    _sett_kontekst(migrator, tenant)
    assert migrator.execute(
        "SELECT count(*) FROM fordringsfunn WHERE tenant=%s",
        (tenant,)).fetchone()[0] >= 1
    migrator.rollback()


@pg
def test_ettergivelsen_tar_radlasen(migrator):
    """ALLE TRE SKRIVEDØRENE LÅSER RADEN. `m23_ettergi` gjorde det ikke:
    den leste `status` uten `FOR UPDATE`, så to samtidige ettergivelser
    kunne begge lese 'apen', begge legge inn en 'ettergitt'-hendelse og
    begge skrive en evidensrad — mens den andre UPDATE-en traff null
    rader og likevel returnerte true.

    Porten måler låsen der den finnes: økt B blir STÅENDE så lenge økt A
    holder raden, og faller på sin egen `statement_timeout`. Uten låsen
    ville B gått rett igjennom.
    """
    tenant = _tenantnavn("las")
    a, b = _rt(), _rt()
    try:
        _plan(a, tenant)
        fid = _fordring(a, tenant)
        # A holder raden — transaksjonen står åpen.
        _sett_kontekst(a, tenant)
        a.execute("SELECT m23_ettergi(%s,%s,%s,'A','u')",
                  (tenant, uuid.uuid4(), fid))
        _sett_kontekst(b, tenant)
        b.execute("SET LOCAL statement_timeout = '1500ms'")
        with pytest.raises(psycopg.errors.QueryCanceled):
            b.execute("SELECT m23_ettergi(%s,%s,%s,'B','u')",
                      (tenant, uuid.uuid4(), fid))
        b.rollback()
        a.commit()
        # …og etter at A er ferdig er kravet ettergitt ÉN gang. Tellingen
        # går via migrator: kjøretidsrollen har ingen tabellrettigheter
        # (SP-7), den når registeret bare gjennom dørene.
        _sett_kontekst(migrator, tenant)
        assert migrator.execute(
            "SELECT count(*) FROM fordringshendelse WHERE tenant=%s"
            " AND fordring_id=%s AND art='ettergitt'",
            (tenant, fid)).fetchone()[0] == 1
        migrator.rollback()
        # …og Bs andre forsøk er nå et STILLE JA, ikke en ny hendelse.
        _sett_kontekst(b, tenant)
        assert b.execute("SELECT m23_ettergi(%s,%s,%s,'B','u')",
                         (tenant, uuid.uuid4(), fid)).fetchone()[0] is False
        b.rollback()
    finally:
        a.close()
        b.close()


def test_alle_skrivedorene_laser_raden_de_endrer():
    """…og statisk, så en fjerde dør ikke kan legges til uten låsen."""
    sql = MIGRASJON.read_text(encoding="utf-8")
    for doer in ("m23_registrer_betaling", "m23_neste_trinn",
                 "m23_ettergi"):
        i = sql.index(f"CREATE FUNCTION {doer}(")
        kropp = sql[i:sql.index("END $$;", i)]
        assert "FOR UPDATE" in kropp, f"{doer} låser ikke raden"


# ---------------------------------------------------------------------------
# INVARIANT 6: tenantlekkasje_i_fordringsregister
# ---------------------------------------------------------------------------

@pg
def test_invariant_tenantlekkasje_direkte(migrator):
    a = _tenantnavn("lekk-a")
    b = _tenantnavn("lekk-b")
    c = _rt()
    try:
        _plan(c, a)
        _fordring(c, a)
        _plan(c, b)
        _fordring(c, b)
        _sett_kontekst(c, b)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT * FROM m23_fordringsstatus(%s)", (a,))
        assert "tenantkontekst" in str(ei.value)
        c.rollback()
        _sett_kontekst(c, a)
        assert c.execute("SELECT * FROM m23_fordringsstatus(%s)",
                         (a,)).fetchone()[0] == 1
        c.rollback()
    finally:
        c.close()
    for tab in ("purreplan", "purretrinn", "fordring",
                "fordringshendelse", "fordringsfunn"):
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
        _plan(c, TENANT)
        _fordring(c, TENANT, kunde="EGEN-KUNDE")
        _plan(c, fremmed)
        _fordring(c, fremmed, kunde="FREMMED-KUNDE")
    finally:
        c.close()
    cookie, _csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/fordring", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    kropp = json.dumps(r.json())
    assert "EGEN-KUNDE" in kropp
    assert "FREMMED-KUNDE" not in kropp


# ---------------------------------------------------------------------------
# INVARIANT 7: ui_axe_alvorlige_brudd
# ---------------------------------------------------------------------------

def test_invariant_ui_axe_bor_i_js_suiten():
    fil = (ROT / "platform" / "core" / "ui" / "test" / "fordring.test.js")
    assert fil.exists(), "fordring.test.js mangler"
    assert "axe" in fil.read_text(encoding="utf-8"), \
        "UI-suiten kjører ingen axe-port for flaten"


# ---------------------------------------------------------------------------
# Sveipearbeideren
# ---------------------------------------------------------------------------

@pg
def test_sveipen_nekter_kontekst(migrator):
    with _sv() as v:
        v.execute("SELECT set_config('disponit.tenant','t',false)")
        with pytest.raises(psycopg.Error) as ei:
            v.execute("SELECT * FROM m23_sveip_fordringer(500)")
        assert "KRYSS-TENANT" in str(ei.value)
        v.rollback()


@pg
def test_sveiperollen_har_ingen_tabellrettigheter(migrator):
    if not FORDRINGSVEIP_DSN:
        pytest.skip("DISPONIT_TEST_FORDRINGSVEIP_DSN ikke satt")
    rader = migrator.execute(
        "SELECT table_name, privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_fordringssveip'").fetchall()
    migrator.rollback()
    assert rader == [], rader


def test_overlappende_sveip_hopper_over_og_rorer_ikke_feiltelleren():
    from drift import fordringssveip

    class Falsk:
        def execute(self, sql, *a):
            class R:
                @staticmethod
                def fetchone():
                    return (False,)
            return R()

        def commit(self):
            pass

    r = fordringssveip.kjor(Falsk(), tidligere_feil=1)
    assert r.hoppet_over is True
    assert r.feilet is False and r.alarm_utlost is False


def test_sveipen_nekter_aa_starte_uten_egen_dsn(tmp_path, monkeypatch):
    from drift import kjor_fordringssveip
    monkeypatch.delenv("DISPONIT_FORDRINGSVEIP_URL", raising=False)
    monkeypatch.setenv("DISPONIT_FORDRINGSVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.setattr("db.hemmeligheter.last_credentials", lambda: None)
    assert kjor_fordringssveip.main() == 2


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
        " ('https://m23.test', %s) RETURNING bruker_id",
        ("s23-" + secrets.token_hex(6),)).fetchone()[0]
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
@dekker("fordring_ulovlig_tilstand")
def test_http_trinnhoppet_er_409_og_ikke_400(migrator, klient):
    """FEILVEIEN `fordring_ulovlig_tilstand`, ende til ende.

    Endepunktet har INGEN trinnparameter, så et hopp kan ikke bes om —
    men et trinn utover planens siste kan, og det er en TILSTAND som sier
    nei. Et 400 her ville sagt at brukeren skrev feil, når sannheten er
    at planen er tom for trinn.

    Dette er også testen `test_api_porter.test_hver_feilvei_har_en_test`
    krever for den nye feilveien.
    """
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _hpost(klient, cookie, csrf, "/v1/fordring/purreplan",
               {"trinn": PLAN})
    assert r.status_code in (200, 201), r.text
    r = _hpost(klient, cookie, csrf, "/v1/fordring",
               {"kunde_ref": "HTTP AS",
                "fakturanummer": "H-" + secrets.token_hex(3),
                "belop_ore": 100000, "utstedt": "2026-07-01",
                "forfall": "2026-07-15"})
    assert r.status_code in (200, 201), r.text
    fid = r.json()["fordring_id"]
    for _ in range(3):
        r = _hpost(klient, cookie, csrf,
                   f"/v1/fordring/{fid}/neste-trinn", {})
        assert r.status_code in (200, 201), r.text
    # Fjerde trinn finnes ikke: TILSTANDEN sier nei.
    r = _hpost(klient, cookie, csrf, f"/v1/fordring/{fid}/neste-trinn", {})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "fordring_ulovlig_tilstand"
    # …og et flyttall er 400: KROPPEN er feil.
    r = _hpost(klient, cookie, csrf, f"/v1/fordring/{fid}/betaling",
               {"belop_ore": 2.5, "inntruffet": "2026-09-01"})
    assert r.status_code == 400, r.text
    assert r.json()["feil"] == "request_feilformet"


@pg
def test_http_lesingen_bruker_okonomi_read(migrator, klient):
    """Fordringsregisteret gjenbruker `okonomi:read` fra M-13 (101) —
    ikke et nytt scope. Dette ER kretsen det ble laget for: hvem som
    skylder oss hva er virksomhetens pengestrøm.
    """
    from api.app import RUTESCOPE
    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert RUTESCOPE[("GET", "/v1/fordring")] == "okonomi:read"
    assert "okonomi:read" in ROLLE_TIL_SCOPES["admin"]
    for rolle in ("leser", "sikkerhet", "godkjenner"):
        assert "okonomi:read" not in ROLLE_TIL_SCOPES[rolle], rolle
    cookie, _c = _browserokt(migrator, ["leser"])
    r = klient.get("/v1/fordring", cookies={_C_SESJON(): cookie})
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# §0
# ---------------------------------------------------------------------------

def test_grensen_ble_registrert_for_koden():
    """Grensen `m23-v1` sto i `KRAVGRENSER` fra klynge 3-fundamentet, før
    en eneste linje av modulen fantes (§0-regelen)."""
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m23-v1"]
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["punktbinding"] == {}
    egen = Path(__file__).read_text(encoding="utf-8")
    for inv in g["invarianter"]:
        assert inv in egen, f"invarianten {inv} har ingen port"
