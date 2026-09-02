"""M-14 fakturakontrollagent v1 (migrasjon 106) — FAKTURAREGISTERET.

Grensen `m14-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR koden
(§0-regelen), og hver invariant der har minst én port her.

DEN SKARPESTE PORTEN ER `modulen_signerte_attestasjon`, og den er
klyngens nye: `bransjemal-tjenestebedrift.yaml` navngir modulen som
verifikatoren `v_regnskap`, betrodd for `faktura_godkjent` — og bruker
den attestasjonen til å slippe `faktura.bokfor` gjennom som
`modus: auto`. Klynge 1–3 holdt igjen på å UTFØRE en handling; her
holder vi igjen på å AUTORISERE en.

DEN NEST SKARPESTE er MVA-AVRUNDINGEN. En mva-kontroll blir stille gal i
avrundingen: `netto * 0.25` i flyttall gir 2499.9999999999995 øre på
99,99 kroner netto, og en kontroll som sammenlignet det med
leverandørens 2500 ville reist et funn på hver eneste faktura.

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

FAKTURASVEIP_DSN = os.environ.get("DISPONIT_TEST_FAKTURASVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "106_m14_fakturakontroll.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "faktura.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "faktura.py",
    ROT / "platform" / "drift" / "fakturasveip.py",
    ROT / "platform" / "drift" / "kjor_fakturasveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("mvasats", "fakturaterskel", "inngaaende_faktura",
        "fakturakontroll", "fakturafunn")


# ---------------------------------------------------------------------------
# Riggen
# ---------------------------------------------------------------------------

def _rt():
    from db.pg import koble
    return koble(DSN)


def _sv():
    from db.pg import koble
    return koble(FAKTURASVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m14-{merke}-{secrets.token_hex(4)}"


def _terskler(c, tenant, *, slingring=1, grense=2500000, frist=7,
              vindu=3, aktor="u-test"):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m14_sett_terskler(%s,%s,%s,%s,%s,%s)",
                  (tenant, slingring, grense, frist, vindu,
                   aktor)).fetchone()[0]
    c.commit()
    return v


def _sats(c, tenant, kode="hoy", promille=250, fra="2020-01-01",
          til=None, aktor="u-test"):
    _sett_kontekst(c, tenant)
    ut = c.execute("SELECT m14_sett_mvasats(%s,%s,%s,%s::date,%s::date,%s)",
                   (tenant, kode, promille, fra, til, aktor)).fetchone()[0]
    c.commit()
    return ut


def _faktura(c, tenant, *, ref="Nordisk Drift AS", nummer=None,
             netto=10000, mva=2500, brutto=None, kode="hoy",
             utstedt="2026-08-01", mottatt_siden=1, fid=None,
             aktor="u-test"):
    fid = fid or uuid.uuid4()
    nummer = nummer or ("F-" + secrets.token_hex(4))
    brutto = netto + mva if brutto is None else brutto
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m14_registrer_faktura(%s,%s,%s,%s,%s,%s,%s,%s,'NOK',"
        "       %s::date, %s::date + 30, current_date - %s::int, %s)",
        (tenant, fid, ref, nummer, netto, mva, brutto, kode, utstedt,
         utstedt, mottatt_siden, aktor))
    c.commit()
    return fid


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m14_sveip_fakturaer(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant, *, bare_apne=True):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT funntype, faktura_id, over_grense, motpart_faktura_id,"
        "       apen FROM fakturafunn"
        " WHERE tenant=%s AND (%s IS FALSE OR apen) ORDER BY funntype",
        (tenant, bare_apne)).fetchall()
    m.rollback()
    return rader


def _bare_kode(fil: Path) -> str:
    """Filens innhold uten kommentarer OG uten docstrings.

    Portene under måler KODE, ikke prosa: modulens egen docstring
    FORTELLER at den ikke bokfører og ikke attesterer, og et rått
    delstrengsøk ville falt på nettopp den setningen. En port som tvang
    dokumentasjonen til å tie om dommen ville gjort dommen usynlig.
    (Formen er `test_m24_leverandor.py` sin.)
    """
    tekst = fil.read_text(encoding="utf-8")
    linjer = tekst.splitlines()
    if fil.suffix == ".py":
        tre = ast.parse(tekst)
        for node in ast.walk(tre):
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
    """Radantall i hver tabell UTENFOR modulens fem, som migrator."""
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
# INVARIANT 1: modulen_signerte_attestasjon — KLYNGENS NYE DOM
# ---------------------------------------------------------------------------

def test_invariant_modulen_signerte_attestasjon():
    """POLICYEN NAVNGIR MODULEN SOM `v_regnskap`, betrodd for
    `dublettsjekk`, `mva_validert` og `faktura_godkjent` — og bruker de
    tre til å slippe `faktura.bokfor` gjennom som `modus: auto`.

    v1 TAR IKKE DEN FULLMAKTEN. En attestasjon er nettopp det som
    slipper en automatisk bokføring med penger i andre enden gjennom, og
    å ta den før treffraten under den er målt, er å la modulen definere
    sin egen troverdighet.

    MÅLT PÅ IMPORTENE (AST, så en import inne i en funksjon ikke slipper
    unna), PÅ KODEN og PÅ DATAMODELLEN.

    MUTASJONEN SOM DREPER DENNE: `from policy_validator import
    attestering` i `api/faktura.py`.
    """
    for fil in MODULFILER:
        tre = ast.parse(fil.read_text(encoding="utf-8"))
        for node in ast.walk(tre):
            navn = []
            if isinstance(node, ast.Import):
                navn = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                navn = [node.module or ""] if node.level == 0 else []
            for n in navn:
                assert "attestering" not in n, \
                    f"{fil.name} importerer {n} — v1 attesterer ingenting"
                assert n.split(".")[0] not in {
                    "hmac", "hashlib", "cryptography", "nacl", "jwt"}, \
                    f"{fil.name} importerer signeringsverktøyet {n}"
    for fil in (MIGRASJON, FLATE, *MODULFILER):
        uten = _bare_kode(fil).lower()
        for ord_ in ("attestasjon", "signatur", "nokkel_id", "signer(",
                     "krav_sett_hash"):
            assert ord_ not in uten, \
                f"{fil.name} bærer «{ord_}» — v1 attesterer ingenting"


def test_invariant_modulen_bokforte():
    """…og den BOKFØRER INGENTING. Samme snitt som M-13 (101).

    Målt på DATAMODELLEN og på RUTENE: 106 har ingen hovedbok, ingen
    kontoplan og ingen status som heter `bokfort`, og `app.py`
    registrerer nøyaktig sju fakturaruter — ingen av dem er en bokføring.
    """
    kode = _bare_kode(MIGRASJON).lower()
    for ord_ in ("'bokfort'", "hovedbok", "kontoplan", "kontonummer",
                 "bilagsnummer", "posteringslinje", "debet", "kredit"):
        assert ord_ not in kode, \
            f"106 bærer «{ord_}» — v1 bokfører ingenting"
    # …og ORDET FINNES IKKE I DET LUKKEDE UTFALLSSETTET i API-et.
    from api.faktura import AVGJORELSER
    assert AVGJORELSER == ("kontrollert", "avvist"), AVGJORELSER

    from api.app import RUTESCOPE
    mine = sorted(sti for _m, sti in RUTESCOPE
                  if sti.startswith("/v1/faktura"))
    assert mine == [
        "/v1/faktura",
        "/v1/faktura",
        "/v1/faktura/mvasats",
        "/v1/faktura/terskler",
        "/v1/faktura/{faktura_id:uuid}/avgjor",
        "/v1/faktura/{faktura_id:uuid}/kontroll",
        "/v1/faktura/{faktura_id:uuid}/kontroller",
    ], mine


@pg
def test_sveipen_bokforer_ingenting_og_avgjor_ingen_faktura(migrator):
    """Målt på VIRKELIGHETEN: en full sveip endrer ikke ett radantall
    utenfor registeret — OG DEN AVGJØR INGEN FAKTURA, selv om den vet
    hvilke som er kontrollert uten avvik.

    MUTASJONEN SOM DREPER DENNE: la sveipen sette `status =
    'kontrollert'` på fakturaer uten avvik.
    """
    tenant = _tenantnavn("sveip")
    c = _rt()
    try:
        _terskler(c, tenant)
        _sats(c, tenant)
        fid = _faktura(c, tenant, mottatt_siden=40)
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
        "SELECT status FROM inngaaende_faktura WHERE tenant=%s"
        " AND faktura_id=%s", (tenant, fid)).fetchone()
    migrator.rollback()
    assert rad[0] == "mottatt", "sveipen avgjorde en faktura"
    # …men den SA FRA: fakturaen har stått lenger enn fristen.
    assert "ukontrollert" in {r[0] for r in _funn(migrator, tenant)}


# ---------------------------------------------------------------------------
# INVARIANT 2: belop_i_flyttall — OG MVA-AVRUNDINGEN
# ---------------------------------------------------------------------------

@pg
@pytest.mark.parametrize("netto,promille,fasit", [
    (0, 250, 0),
    (1, 250, 0),        # 0,25 → 0
    (2, 250, 1),        # 0,50 → 1 (halv-opp, ikke halv-til-partall)
    (6, 250, 2),        # 1,50 → 2
    (9999, 250, 2500),  # 2499,75 → 2500
    (10000, 250, 2500),
    (12345, 150, 1852),  # 1851,75 → 1852
    (100, 0, 0),
    (10 ** 12, 250, 250 * 10 ** 9),
])
def test_mva_regnes_i_heltall_med_halv_opp(migrator, netto, promille,
                                           fasit):
    """DEN NEST SKARPESTE PORTEN. Regelen er
    `(netto * promille + 500) / 1000`, halv-opp, og den bor i BASEN.

    `netto * 0.25` i flyttall gir 2499.9999999999995 øre på 99,99 kroner
    netto — og en kontroll som sammenlignet det med leverandørens 2500
    ville reist et funn på hver eneste faktura. En mva-kontroll blir
    stille gal i avrundingen, ikke i den store regningen.

    GRENSETILFELLENE ER PORTEN: 0,50 øre skal bli 1 (halv-opp), ikke 0
    (trunkering) og ikke 0 (halv-til-partall).

    MUTASJONEN SOM DREPER DENNE: fjern `+ 500`.
    """
    c = _rt()
    try:
        _sett_kontekst(c, TENANT)
        ut = c.execute("SELECT m14_forventet_mva(%s,%s)",
                       (netto, promille)).fetchone()[0]
        c.rollback()
    finally:
        c.close()
    assert ut == fasit


@pg
def test_mva_uten_grunnlag_eller_sats_er_ingen_kontroll(migrator):
    """`NULL` som ble lest som «stemmer» er nøyaktig den stille feilen."""
    c = _rt()
    try:
        for netto, prom in ((None, 250), (100, None), (None, None)):
            _sett_kontekst(c, TENANT)
            with pytest.raises(psycopg.Error) as ei:
                c.execute("SELECT m14_forventet_mva(%s,%s)", (netto, prom))
            assert "ingen kontroll" in str(ei.value)
            c.rollback()
        _sett_kontekst(c, TENANT)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m14_forventet_mva(-1,250)")
        assert "negativt" in str(ei.value)
        c.rollback()
    finally:
        c.close()


@pg
def test_invariant_belop_i_flyttall_i_katalogen(migrator):
    """Måler KATALOGEN: hver beløpskolonne er `bigint`, og INGEN kolonne
    i registeret er et flyttall — uansett navn."""
    rader = migrator.execute(
        "SELECT table_name, column_name, data_type"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND column_name LIKE '%%ore%%'"
        " ORDER BY 1,2", (list(EGNE),)).fetchall()
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
    """…og API-et RUNDER ALDRI et flyttall, det avviser det."""
    from api.faktura import MAKS_ORE, _heltall, _ore
    from api.policyadmin_http import _Avbrudd
    for verdi in (2.5, 100.0, True, False, "100", None, -1, MAKS_ORE):
        with pytest.raises(_Avbrudd):
            _ore({"p": verdi}, "p", "r")
    assert _ore({"p": 0}, "p", "r") == 0
    for verdi in (1.5, True, False, "3", None, -1, 1001):
        with pytest.raises(_Avbrudd):
            _heltall({"n": verdi}, "n", "r", 0, 1000)
    assert _heltall({"n": 250}, "n", "r", 0, 1000) == 250


# ---------------------------------------------------------------------------
# INVARIANT 3: mvasats_hardkodet
# ---------------------------------------------------------------------------

def test_invariant_mvasats_hardkodet():
    """SATSENE ER TENANTENS, ikke modulens. En sats kodet inn ville vært
    en fullmakt modulen ga seg selv over et tall STATEN setter — og den
    ville vært udatert, så en satsendring hadde gjort hver gammel
    faktura gal med tilbakevirkende kraft.

    PORTEN MÅLER FRAVÆRET AV EN SATSKONSTANT i modulens kode, og at
    sveipen tar ingen satsparameter.

    ÆRLIG OM HVA DETTE IKKE ER: satsene går ikke gjennom M-1s
    policymotor (dokumentbasert, ingen tenant-innstilling). Invarianten
    er oppfylt i den forstand som betyr noe — tenanten eier og fører
    verdiene — men koblingen til M-1 er et NAVNGITT gap.
    """
    import re
    for fil in MODULFILER:
        uten = _bare_kode(fil)
        for m in re.finditer(
                r"^([A-Z_]*(?:SATS|MVA|PROMILLE|GRENSE)[A-Z_]*)\s*=\s*(\d+)",
                uten, re.M):
            # `MAKS_*` er tak på KROPPEN (validering) og `GRENSE` er
            # sveipens tak på TRANSAKSJONEN — ingen av dem er en sats
            # eller en terskel noe måles mot. Unntatt VED NAVN, ikke ved
            # mønster, så listen ikke kan vokse i stillhet.
            assert m.group(1) in ("GRENSE", "MAKS_ORE",
                                  "MAKS_SATSKODE"), \
                f"{fil.name} har satskonstanten {m.group(1)}={m.group(2)}"
    from drift import fakturasveip
    assert fakturasveip.GRENSE == 500

    kode = _bare_kode(MIGRASJON)
    assert "m14_sveip_fakturaer(p_grense INT DEFAULT 500)" in kode
    assert "FROM public.mvasats" in kode
    # …og de norske satsene står IKKE i migrasjonen som frødata.
    #
    # Første utgave av denne løkken brukte ikke `tall` i påstanden i det
    # hele tatt — den gjentok den samme kontrollen tre ganger og målte
    # ingen av satsene. En port som ikke bruker løkkevariabelen sin er
    # ingen løkke. (CodeRabbit.)
    for tall in ("250", "150", "120"):
        assert f"VALUES ('{tall}'" not in kode
        assert f", {tall}," not in kode.replace("\n", " "), \
            f"106 ser ut til å frø satsen {tall} — satsene er tenantens"
    from api.faktura import terskler_endepunkt
    doc = terskler_endepunkt.__doc__ or ""
    assert "M-1" in doc and "gap" in doc.lower()


@pg
def test_satsen_leses_paa_fakturaens_dato(migrator):
    """EN SATSENDRING GJØR IKKE GAMLE FAKTURAER GALE.

    Satsen leses etter FAKTURAENS dato, ikke dagens. Uten det ville en
    satsendring omskrevet hver mva-kontroll som alt var kjørt — historien
    ville rettet seg selv.

    MUTASJONEN SOM DREPER DENNE: bruk `current_date` i
    `m14_sats_paa_dato`.
    """
    tenant = _tenantnavn("dato")
    c = _rt()
    try:
        _sats(c, tenant, "lav", 150, "2020-01-01", "2026-06-30")
        _sats(c, tenant, "lav", 120, "2026-07-01", None)
        _sett_kontekst(c, tenant)
        for dato, fasit in (("2026-06-30", 150), ("2026-07-01", 120),
                            ("2019-12-31", None)):
            ut = c.execute("SELECT m14_sats_paa_dato(%s,'lav',%s::date)",
                           (tenant, dato)).fetchone()[0]
            assert ut == fasit, (dato, ut)
        c.rollback()
        # OVERLAPPENDE PERIODER FINNES IKKE: da ville «hvilken sats
        # gjaldt denne dagen» hatt to svar, og kontrollen ville avhengt
        # av hvilken rad planleggeren leste først.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m14_sett_mvasats(%s,'lav',999,"
                      "'2026-01-01'::date,'2026-12-31'::date,'u')",
                      (tenant,))
        assert "overlapper" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    # …og SATSEN ER FROSSET: en endret promille ville omskrevet hver
    # kontroll som alt var kjørt mot den.
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_faktura_eier")
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("UPDATE mvasats SET promille=1 WHERE tenant=%s",
                         (tenant,))
    assert "frosset" in str(ei.value)
    migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 4: dublett_uten_funn
# ---------------------------------------------------------------------------

@pg
def test_invariant_dublett_den_eksakte_avvises_den_naere_er_et_funn(
        migrator):
    """TO HALVDELER, OG DE ER FORSKJELLIGE.

    DEN EKSAKTE dubletten — samme leverandør og samme fakturanummer — kan
    ikke registreres: den skal ikke kunne betales to ganger fordi noen
    importerte den fra to kanaler. En UNIQUE feller den.

    DEN NÆRE — samme leverandør, samme beløp, samme periode, ULIKT
    nummer — er et FUNN. Det er mønsteret i en dobbeltfakturering, og det
    er en menneskelig vurdering, ikke en regel basen kan felle.

    MUTASJONEN SOM DREPER DENNE: fjern `faktura_en_per_nummer`, eller
    la den nære dubletten også bli en nektelse.
    """
    tenant = _tenantnavn("dublett")
    c = _rt()
    try:
        _terskler(c, tenant, vindu=3)
        _sats(c, tenant)
        _faktura(c, tenant, nummer="F-100", utstedt="2026-08-01")
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(
                "SELECT m14_registrer_faktura(%s,%s,'Nordisk Drift AS',"
                "'F-100',10000,2500,12500,'hoy','NOK','2026-08-01',"
                "'2026-08-31',current_date,'u')", (tenant, uuid.uuid4()))
        c.rollback()
        # DEN NÆRE går gjennom — og blir en kontroll med avvik.
        n2 = _faktura(c, tenant, nummer="F-101", utstedt="2026-08-03")
        # …og en som ligger UTENFOR vinduet er ingen dublett.
        n3 = _faktura(c, tenant, nummer="F-102", utstedt="2026-09-01")
        _sett_kontekst(c, tenant)
        utfall = {r[1]: r[2] for r in c.execute(
            "SELECT kontrolltype, faktura_id, utfall FROM fakturakontroll"
            " WHERE tenant=%s AND kontrolltype='dublett'",
            (tenant,)).fetchall()} if False else None
        c.rollback()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    rader = {r[0]: r[1] for r in migrator.execute(
        "SELECT faktura_id, utfall FROM fakturakontroll"
        " WHERE tenant=%s AND kontrolltype='dublett'",
        (tenant,)).fetchall()}
    migrator.rollback()
    assert rader[n2] == "avvik", "den nære dubletten ble ikke sett"
    assert rader[n3] == "ok", "en faktura utenfor vinduet ble en dublett"

    with _sv() as v:
        _sveip(v)
    funn = {(r[0], r[1]) for r in _funn(migrator, tenant)}
    assert ("naer_dublett", n2) in funn
    assert ("naer_dublett", n3) not in funn
    # MOTPARTEN STÅR PÅ FUNNET — uten den måtte et menneske lete etter
    # hvilken faktura det gjelder.
    motpart = [r[3] for r in _funn(migrator, tenant)
               if r[0] == "naer_dublett" and r[1] == n2]
    assert motpart and motpart[0] is not None


@pg
def test_mva_avviket_maales_mot_tenantens_slingring(migrator):
    """Avrundingen er halv-opp, så et øre fra eller til er avrunding og
    ikke feil. GRENSETILFELLET ER PORTEN: med slingring 1 er et avvik på
    1 øre `ok`, og 2 øre er `avvik`.

    MUTASJONEN SOM DREPER DENNE: bytt `<=` mot `<` i slingringssjekken.
    """
    for avvik, ventet in ((0, "ok"), (1, "ok"), (2, "avvik")):
        tenant = _tenantnavn(f"sling{avvik}")
        c = _rt()
        try:
            _terskler(c, tenant, slingring=1)
            _sats(c, tenant)
            fid = _faktura(c, tenant, netto=10000, mva=2500 + avvik,
                           brutto=12500 + avvik)
        finally:
            c.close()
        _sett_kontekst(migrator, tenant)
        rad = migrator.execute(
            "SELECT utfall, avvik_ore FROM fakturakontroll"
            " WHERE tenant=%s AND faktura_id=%s AND kontrolltype='mva'",
            (tenant, fid)).fetchone()
        migrator.rollback()
        assert rad == (ventet, avvik), (avvik, rad)


@pg
def test_ingen_mvasats_er_en_egen_funntype(migrator):
    """«Vi har ikke ført satsen» og «leverandøren regnet feil» er to
    forskjellige problemer med to forskjellige løsninger. Et avvik på
    null ville skjult det første i det andre."""
    tenant = _tenantnavn("utensats")
    c = _rt()
    try:
        _terskler(c, tenant)
        # INGEN sats ført.
        fid = _faktura(c, tenant)
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    rad = migrator.execute(
        "SELECT utfall, avvik_ore, notat FROM fakturakontroll"
        " WHERE tenant=%s AND faktura_id=%s AND kontrolltype='mva'",
        (tenant, fid)).fetchone()
    migrator.rollback()
    assert rad[0] == "avvik" and rad[1] is None
    assert "ingen mvasats" in rad[2]
    with _sv() as v:
        _sveip(v)
    typer = {r[0] for r in _funn(migrator, tenant)}
    assert "ingen_mvasats" in typer
    assert "mva_avvik" not in typer


# ---------------------------------------------------------------------------
# INVARIANT 5: faktura_uten_kontroll
# ---------------------------------------------------------------------------

@pg
def test_invariant_faktura_uten_kontroll(migrator):
    """En faktura som forfaller mens den venter er den dyreste raden i
    registeret. IDEMPOTENSEN måles i samme test.

    MUTASJONEN SOM DREPER DENNE: fjern `ukontrollert` fra kandidatene.
    """
    tenant = _tenantnavn("frist")
    c = _rt()
    try:
        _terskler(c, tenant, frist=7)
        _sats(c, tenant)
        gammel = _faktura(c, tenant, nummer="G-1", mottatt_siden=30)
        _faktura(c, tenant, nummer="N-1", mottatt_siden=2)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    ukontrollerte = {r[1]: r[2] for r in _funn(migrator, tenant)
                     if r[0] == "ukontrollert"}
    assert list(ukontrollerte) == [gammel], ukontrollerte
    assert ukontrollerte[gammel] == 23, "over_grense er ikke døgn over"

    forst = {(r[0], r[1]) for r in _funn(migrator, tenant)}
    with _sv() as v:
        rad = _sveip(v)
    assert rad[1] == 0, "sveip nummer to skrev nye rader"
    assert {(r[0], r[1]) for r in _funn(migrator, tenant)} == forst


@pg
def test_over_belopsgrensen_lukkes_av_en_manuell_kontroll(migrator):
    """Over grensen skal et menneske ha sett på fakturaen — uansett hvor
    pen den ser ut maskinelt. Funnet lukkes av en MANUELL kontroll, ikke
    av at de tre maskinelle gikk bra."""
    tenant = _tenantnavn("stor")
    c = _rt()
    try:
        _terskler(c, tenant, grense=100000)
        _sats(c, tenant)
        fid = _faktura(c, tenant, netto=200000, mva=50000)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    funn = {r[0]: r[2] for r in _funn(migrator, tenant)}
    assert "over_belopsgrense" in funn
    assert funn["over_belopsgrense"] == 150000
    c = _rt()
    try:
        _sett_kontekst(c, tenant)
        c.execute("SELECT m14_registrer_kontroll(%s,%s,%s,'ok',"
                  "'sett og sammenholdt med bestillingen','u')",
                  (tenant, uuid.uuid4(), fid))
        c.commit()
        # …og en manuell kontroll UTEN notat er ingen kontroll.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m14_registrer_kontroll(%s,%s,%s,'ok',"
                      "NULL,'u')", (tenant, uuid.uuid4(), fid))
        assert "etterprøve" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert "over_belopsgrense" not in {r[0] for r in _funn(migrator, tenant)}


@pg
def test_kontrollene_er_append_only(migrator):
    """En kontroll som kunne skrives om ville gjort «denne fakturaen er
    kontrollert» til en påstand. Append-only helt ned til GRANTET."""
    tenant = _tenantnavn("append")
    c = _rt()
    try:
        _terskler(c, tenant)
        _sats(c, tenant)
        _faktura(c, tenant)
    finally:
        c.close()
    for sql in ("UPDATE fakturakontroll SET utfall='ok' WHERE tenant=%s",
                "DELETE FROM fakturakontroll WHERE tenant=%s"):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_faktura_eier")
        with pytest.raises(psycopg.Error), migrator.transaction():
            migrator.execute(sql, (tenant,))
        migrator.rollback()
    rettigheter = migrator.execute(
        "SELECT privilege_type FROM information_schema.table_privileges"
        " WHERE grantee='disponit_faktura_eier'"
        "   AND table_name='fakturakontroll' ORDER BY 1").fetchall()
    migrator.rollback()
    assert [r[0] for r in rettigheter] == ["INSERT", "SELECT"], rettigheter


@pg
def test_fakturaens_innhold_er_frosset(migrator):
    """Beløpene og datoene er det leverandøren KREVER. Et register som
    lot dem endres ville gjort kontrollen til en kontroll av noe annet
    enn det som kom."""
    tenant = _tenantnavn("frosset")
    c = _rt()
    try:
        _terskler(c, tenant)
        _sats(c, tenant)
        fid = _faktura(c, tenant)
    finally:
        c.close()
    for kolonne, verdi in (("netto_ore", "1"), ("mva_ore", "1"),
                           ("fakturanummer", "'X'"), ("utstedt",
                                                      "current_date")):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_faktura_eier")
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(
                f"UPDATE inngaaende_faktura SET {kolonne} = {verdi}"
                " WHERE tenant=%s AND faktura_id=%s", (tenant, fid))
        assert "frosset" in str(ei.value), kolonne
        migrator.rollback()


@pg
def test_avgjorelsen_lukker_funnene_og_gjenaapnes_ikke(migrator):
    tenant = _tenantnavn("avgjor")
    c = _rt()
    try:
        _terskler(c, tenant, frist=1)
        _sats(c, tenant)
        fid = _faktura(c, tenant, mottatt_siden=30)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert _funn(migrator, tenant)
    c = _rt()
    try:
        _sett_kontekst(c, tenant)
        assert c.execute(
            "SELECT m14_avgjor_faktura(%s,%s,'kontrollert','sett','u')",
            (tenant, fid)).fetchone()[0] is True
        c.commit()
        # …og en gang til er et STILLE JA.
        _sett_kontekst(c, tenant)
        assert c.execute(
            "SELECT m14_avgjor_faktura(%s,%s,'avvist','igjen','u')",
            (tenant, fid)).fetchone()[0] is False
        c.commit()
        # …og «bokfort» finnes ikke som utfall.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m14_avgjor_faktura(%s,%s,'bokfort','x','u')",
                      (tenant, fid))
        assert "ingen av delene er en bokføring" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    assert _funn(migrator, tenant) == []
    lukkede = _funn(migrator, tenant, bare_apne=False)
    assert lukkede and all(r[4] is False for r in lukkede)
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_faktura_eier")
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "UPDATE inngaaende_faktura SET status='mottatt'"
            " WHERE tenant=%s AND faktura_id=%s", (tenant, fid))
    assert "alt kontrollert" in str(ei.value) or "status" in str(ei.value)
    migrator.rollback()


@pg
def test_treffraten_teller_alle_fem_typene(migrator):
    """TREFFRATEN ER MODULENS EGENTLIGE LEVERANSE i v1: «en dublettsjekk
    ingen har målt er ikke en kontroll, det er en påstand».

    ALLE FEM TYPENE STÅR I SVARET, også de tenanten ikke har kjørt.
    """
    tenant = _tenantnavn("treff")
    c = _rt()
    try:
        _terskler(c, tenant)
        _sats(c, tenant)
        _faktura(c, tenant, nummer="T-1")
        _faktura(c, tenant, nummer="T-2", netto=10000, mva=2600,
                 brutto=12600)
        _sett_kontekst(c, tenant)
        rader = {r[0]: (r[1], r[2]) for r in c.execute(
            "SELECT * FROM m14_treffrate(%s)", (tenant,)).fetchall()}
        c.rollback()
    finally:
        c.close()
    assert list(rader) == ["dublett", "mva", "leverandor",
                           "belopsgrense", "manuell"], list(rader)
    assert rader["mva"] == (2, 1)
    assert rader["leverandor"] == (2, 2)
    assert rader["manuell"] == (0, 0)


# ---------------------------------------------------------------------------
# Vaktene og rettighetene
# ---------------------------------------------------------------------------

@pg
def test_ingen_av_de_fem_tabellene_kan_tommes(migrator):
    """TRUNCATE AVVISES PÅ ALLE FEM. Porten finnes fordi den ble brutt i
    104: en vakt som gjenbrukte radlogikken lot TG_OP='TRUNCATE' falle
    glatt gjennom til `RETURN NEW`."""
    tenant = _tenantnavn("truncate")
    c = _rt()
    try:
        _terskler(c, tenant)
        _sats(c, tenant)
        _faktura(c, tenant, mottatt_siden=30)
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
    # …OG CASCADE, som er veien FORBI fremmednøkkelen.
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("TRUNCATE public.inngaaende_faktura CASCADE")
    assert "TRUNCATE avvist" in str(ei.value), ei.value
    migrator.rollback()


@pg
def test_fakturaeieren_ser_bare_ett_objekt_i_en_annen_modul(migrator):
    """SP-7 PÅ TVERS AV MODULER, med ETT navngitt unntak.

    `disponit_faktura_eier` har SELECT på `leverandorpart` — og
    ingenting annet av M-24s. Grantet står fordi leverandørkontrollen må
    svare for ÉN referanse om gangen og ikke kan avkortes: `m24_leverandorene`
    returnerer en side (LIMIT 500), og en kontroll bygget på den ville
    svart «ukjent leverandør» for tenantens leverandør nummer 501 — et
    STILLE galt svar.

    MUTASJONEN SOM DREPER DENNE: grant M-14s eier noe mer av M-24.
    """
    rader = migrator.execute(
        "SELECT table_name, privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_faktura_eier'"
        "   AND table_name IN ('leverandorpart','leveranseavtale',"
        "                      'leveranse','leverandorfunn',"
        "                      'leverandorterskel')"
        " ORDER BY 1,2").fetchall()
    migrator.rollback()
    assert rader == [("leverandorpart", "SELECT")], rader


@pg
def test_leverandorkontrollen_finner_en_registrert_leverandor(migrator):
    """…og den VIRKER: en leverandør som står i M-24 gir `ok`.

    Uten denne halvdelen ville porten over vært grønn på en kontroll som
    alltid svarte «ukjent».
    """
    tenant = _tenantnavn("kjent")
    c = _rt()
    try:
        _terskler(c, tenant)
        _sats(c, tenant)
        _sett_kontekst(c, tenant)
        c.execute("SELECT m24_registrer_leverandor(%s,%s,%s,NULL,'u')",
                  (tenant, uuid.uuid4(), "Kjent Leverandør AS"))
        c.commit()
        kjent = _faktura(c, tenant, ref="Kjent Leverandør AS",
                         nummer="K-1")
        ukjent = _faktura(c, tenant, ref="Aldri Sett AS", nummer="U-1")
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    rader = {r[0]: r[1] for r in migrator.execute(
        "SELECT faktura_id, utfall FROM fakturakontroll"
        " WHERE tenant=%s AND kontrolltype='leverandor'",
        (tenant,)).fetchall()}
    migrator.rollback()
    assert rader[kjent] == "ok", "en registrert leverandør ble ukjent"
    assert rader[ukjent] == "avvik"


def test_skrivedoren_som_leser_en_tilstand_laser_raden():
    """Uten låsen kunne to samtidige avgjørelser begge lese 'mottatt' og
    begge skrive en evidensrad, mens bare den ene traff en rad.
    (CodeRabbits funn på 104s ettergivelsesdør; her står porten FØR
    feilen.)"""
    sql = MIGRASJON.read_text(encoding="utf-8")
    i = sql.index("CREATE FUNCTION m14_avgjor_faktura(")
    assert "FOR UPDATE" in sql[i:sql.index("END $$;", i)]


# ---------------------------------------------------------------------------
# INVARIANT 6: tenantlekkasje_i_fakturaregister
# ---------------------------------------------------------------------------

@pg
def test_invariant_tenantlekkasje_direkte(migrator):
    a = _tenantnavn("lekk-a")
    b = _tenantnavn("lekk-b")
    c = _rt()
    try:
        _terskler(c, a)
        _sats(c, a)
        _faktura(c, a)
        _terskler(c, b)
        _sett_kontekst(c, b)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT * FROM m14_fakturastatus(%s)", (a,))
        assert "tenantkontekst" in str(ei.value)
        c.rollback()
        _sett_kontekst(c, a)
        assert c.execute("SELECT * FROM m14_fakturastatus(%s)",
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
        _sats(c, TENANT)
        _faktura(c, TENANT, ref="EGEN-LEVERANDOR", nummer="E-1")
        _terskler(c, fremmed)
        _sats(c, fremmed)
        _faktura(c, fremmed, ref="FREMMED-LEVERANDOR", nummer="X-1")
    finally:
        c.close()
    cookie, _csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/faktura", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    kropp = json.dumps(r.json())
    assert "EGEN-LEVERANDOR" in kropp
    assert "FREMMED-LEVERANDOR" not in kropp


# ---------------------------------------------------------------------------
# Sveipearbeideren
# ---------------------------------------------------------------------------

@pg
def test_sveipen_nekter_kontekst(migrator):
    with _sv() as v:
        v.execute("SELECT set_config('disponit.tenant','t',false)")
        with pytest.raises(psycopg.Error) as ei:
            v.execute("SELECT * FROM m14_sveip_fakturaer(500)")
        assert "KRYSS-TENANT" in str(ei.value)
        v.rollback()


@pg
def test_sveiperollen_har_ingen_tabellrettigheter(migrator):
    if not FAKTURASVEIP_DSN:
        pytest.skip("DISPONIT_TEST_FAKTURASVEIP_DSN ikke satt")
    rader = migrator.execute(
        "SELECT table_name, privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_fakturasveip'").fetchall()
    migrator.rollback()
    assert rader == [], rader


def test_sveipen_nekter_aa_starte_uten_egen_dsn(tmp_path, monkeypatch):
    from drift import kjor_fakturasveip
    monkeypatch.delenv("DISPONIT_FAKTURASVEIP_URL", raising=False)
    monkeypatch.setenv("DISPONIT_FAKTURASVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.setattr("db.hemmeligheter.last_credentials", lambda: None)
    assert kjor_fakturasveip.main() == 2


# ---------------------------------------------------------------------------
# INVARIANT 7: ui_axe_alvorlige_brudd
# ---------------------------------------------------------------------------

def test_invariant_ui_axe_bor_i_js_suiten():
    fil = (ROT / "platform" / "core" / "ui" / "test" / "faktura.test.js")
    assert fil.exists(), "faktura.test.js mangler"
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
        " ('https://m14.test', %s) RETURNING bruker_id",
        ("s14-" + secrets.token_hex(6),)).fetchone()[0]
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
@dekker("faktura_ulovlig_tilstand")
def test_http_den_eksakte_dubletten_er_409_og_ikke_400(migrator, klient):
    """FEILVEIEN `faktura_ulovlig_tilstand`, ende til ende.

    Kroppen ER velformet: alle feltene er der, beløpene går opp, datoene
    er lesbare. Det er BASEN som sier at denne fakturaen har vi alt — og
    det er nettopp kontrollen modulen er navngitt for i policyen.

    Dette er også testen `test_api_porter.test_hver_feilvei_har_en_test`
    krever for den nye feilveien.
    """
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _hpost(klient, cookie, csrf, "/v1/faktura/terskler",
               {"mva_slingring_ore": 1, "belopsgrense_ore": 2500000,
                "kontrollfrist_dogn": 7, "dublettvindu_dogn": 3})
    assert r.status_code in (200, 201), r.text
    r = _hpost(klient, cookie, csrf, "/v1/faktura/mvasats",
               {"sats_kode": "hoy", "promille": 250,
                "gyldig_fra": "2020-01-01", "gyldig_til": None})
    assert r.status_code in (200, 201), r.text
    nummer = "H-" + secrets.token_hex(4)
    kropp = {"leverandor_ref": "HTTP AS", "fakturanummer": nummer,
             "netto_ore": 10000, "mva_ore": 2500, "brutto_ore": 12500,
             "sats_kode": "hoy", "valuta": "NOK",
             "utstedt": "2026-08-01", "forfall": "2026-08-31",
             "mottatt": "2026-08-02"}
    r = _hpost(klient, cookie, csrf, "/v1/faktura", kropp)
    assert r.status_code in (200, 201), r.text
    fid = r.json()["faktura_id"]
    # SAMME LEVERANDØR OG SAMME NUMMER: TILSTANDEN sier nei.
    r = _hpost(klient, cookie, csrf, "/v1/faktura", kropp)
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "faktura_ulovlig_tilstand"
    # …og `netto + mva <> brutto` likeså — basen, ikke kroppen.
    r = _hpost(klient, cookie, csrf, "/v1/faktura",
               {**kropp, "fakturanummer": "H2-" + secrets.token_hex(4),
                "brutto_ore": 99999})
    assert r.status_code == 409, r.text
    # …mens `bokfort` er 400: KROPPEN er feil, ordet finnes ikke.
    r = _hpost(klient, cookie, csrf, f"/v1/faktura/{fid}/avgjor",
               {"status": "bokfort", "begrunnelse": "x"})
    assert r.status_code == 400, r.text
    assert r.json()["feil"] == "request_feilformet"
    # …og et flyttall likeså.
    r = _hpost(klient, cookie, csrf, "/v1/faktura",
               {**kropp, "fakturanummer": "H3-" + secrets.token_hex(4),
                "mva_ore": 2500.5})
    assert r.status_code == 400, r.text


@pg
def test_http_lesingen_bruker_okonomi_read(migrator, klient):
    from api.app import RUTESCOPE
    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert RUTESCOPE[("GET", "/v1/faktura")] == "okonomi:read"
    assert "okonomi:read" in ROLLE_TIL_SCOPES["admin"]
    for rolle in ("leser", "sikkerhet", "godkjenner"):
        assert "okonomi:read" not in ROLLE_TIL_SCOPES[rolle], rolle
    cookie, _c = _browserokt(migrator, ["leser"])
    r = klient.get("/v1/faktura", cookies={_C_SESJON(): cookie})
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# §0
# ---------------------------------------------------------------------------

def test_grensen_ble_registrert_for_koden():
    """Grensen `m14-v1` sto i `KRAVGRENSER` fra klynge 4-fundamentet, før
    en eneste linje av modulen fantes (§0-regelen)."""
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m14-v1"]
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["punktbinding"] == {}
    egen = Path(__file__).read_text(encoding="utf-8")
    for inv in g["invarianter"]:
        assert inv in egen, f"invarianten {inv} har ingen port"
