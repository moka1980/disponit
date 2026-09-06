"""M-7 møteoperasjonsagent v1 (133) — KLYNGE 9s FØRSTE.

Grensen `m7-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR koden
(§0-regelen), og hver invariant der har minst én port her.

KLYNGENS DELTE DOM, OG DEN FORMER HVER PORT:

  EN YTRING AVGITT I HUSETS NAVN KAN IKKE TAS TILBAKE — OG DEN SOM
  LESER DEN VET IKKE AT EN MASKIN SKREV DEN.

DEN TYNGSTE GRUPPEN PORTER MÅLER REKKEFØLGE, IKKE TILSTAND.

Vaktsetningen sier «opptak starter kun med gyldig policy/varsling», og
rekkefølgen i den setningen ER regelen: ET NEKT SOM KOMMER ETTER
MIKROFONEN ER IKKE ET NEKT. Et opptak tatt uten grunnlag er ulovlig i
det øyeblikket det starter, og å oppdage det i en nattlig sveip er å
oppdage en skade — ikke å hindre den.

Derfor porter denne suiten fire nekt på ÉN dør, og en port på at
sveipen ALDRI kan reise `opptak_uten_hjemmel`. At funnet ikke kan
oppstå er beviset på at vernet ligger i datamodellen.

DEN ANDRE GRUPPEN MÅLER ET FRAVÆR: modulen fatter ingen beslutning.
`besluttet_av` er NOT NULL, døra nekter på tomt, og ingen kodevei
skriver en beslutning uten et navn.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import ast
import contextlib
import datetime
import os
import re
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN  # noqa: F401
from .test_m37 import _sett_kontekst

MOTESVEIP_DSN = os.environ.get("DISPONIT_TEST_MOTESVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "133_m7_moteoperasjon.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "mote.js")
FUNDAMENT = ROT / "docs" / "KLYNGE9-FUNDAMENT.md"
MODULFILER = (
    ROT / "platform" / "core" / "api" / "moteoperasjon.py",
    ROT / "platform" / "drift" / "motesveip.py",
    ROT / "platform" / "drift" / "kjor_motesveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("motekrav", "opptakshjemmel", "mote", "moteopptak",
        "referatpunkt", "motebeslutning", "moteaksjon", "motefunn")

_STRENG = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")


def _bare_kode(fil: Path, *, uten_strenger: bool = False) -> str:
    """Filens innhold uten kommentarer OG uten docstrings (m24-formen).

    PORTEN SKAL MÅLE HANDLINGEN, IKKE BEGRUNNELSEN. En port som leter
    i rå filtekst treffer kommentaren som forklarer HVORFOR et mønster
    er unngått — og her er kommentarene fulle av ordene «opptak» og
    «beslutning», nettopp fordi modulen ikke gjør noen av
    delene.
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
    ut = "\n".join(x for x in linjer
                   if not x.lstrip().startswith(merke))
    return _STRENG.sub("''", ut) if uten_strenger else ut


@contextlib.contextmanager
def _to():
    """RUNTIME for dørene, MIGRATOR for tabellene.

    SP-7 er grunnen til at det må være to: runtime har EXECUTE på
    dørene og INGEN tabellrettigheter, og migrator eier tabellene men
    slipper ikke inn dørene. En test med ÉN tilkobling ville målt en
    base der skillet ikke fantes.
    """
    from db.pg import koble
    rt = koble(DSN)
    mg = koble(MIGRATOR_DSN)
    try:
        yield rt, mg
    finally:
        for c in (rt, mg):
            try:
                c.rollback()
                c.close()
            except Exception:
                pass


def _mig():
    from db.pg import koble
    return koble(MIGRATOR_DSN)


def _sv():
    from db.pg import koble
    return koble(MOTESVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m7-{merke}-{secrets.token_hex(4)}"


I_DAG = datetime.date.today()


def _dag(n: int) -> datetime.date:
    return I_DAG + datetime.timedelta(days=n)




HJEMMELTEKST = ("Referatfoering av styremoeter etter styrevedtak"
                " 12/24, med varsling i innkallingen.")


def _krav(c, tenant, *, referatfrist=3, aksjonsfrist=7, terskel=7000,
          aktor="u-test", nokkel=None):
    _sett_kontekst(c, tenant)
    v = c.execute(
        "SELECT versjon FROM m7_sett_krav(%s,%s,%s,%s,%s,%s)",
        (tenant, referatfrist, aksjonsfrist, terskel, aktor,
         nokkel or secrets.token_hex(8))).fetchone()[0]
    c.commit()
    return v


def _hjemmel(c, tenant, *, grunnlagstype="berettiget_interesse",
             beskrivelse=HJEMMELTEKST, formal="referatfoering",
             fra=None, til=None, aktor="u-test", hjemmel_id=None):
    _sett_kontekst(c, tenant)
    hid = hjemmel_id or uuid.uuid4()
    c.execute(
        "SELECT m7_registrer_hjemmel(%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant, hid, grunnlagstype, beskrivelse, formal,
         fra or _dag(-30), til, aktor))
    c.commit()
    return hid


def _mote(c, tenant, *, tittel="Styremoete", start_dogn=-2,
          varighet_min=60, innkalt_av="u-kari",
          deltakere=("ext:1", "ext:2"), agenda="Kvartalstall",
          aktor="u-test", mote_id=None):
    import datetime
    _sett_kontekst(c, tenant)
    mid = mote_id or uuid.uuid4()
    start = (datetime.datetime.now(datetime.timezone.utc)
             + datetime.timedelta(days=start_dogn))
    c.execute(
        "SELECT m7_registrer_mote(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant, mid, tittel, start,
         start + datetime.timedelta(minutes=varighet_min),
         innkalt_av, list(deltakere), agenda, aktor))
    c.commit()
    return mid


def _punkt(c, tenant, mote_id, *, rekkefolge=1, tekst="Noe ble sagt",
           kilde="agenda", kilde_ref="punkt-1", sikkerhet=9000,
           retter=None, aktor="u-test", punkt_id=None):
    _sett_kontekst(c, tenant)
    pid = punkt_id or uuid.uuid4()
    rad = c.execute(
        "SELECT * FROM m7_registrer_referatpunkt"
        "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant, pid, mote_id, rekkefolge, tekst, kilde, kilde_ref,
         sikkerhet, retter, aktor)).fetchone()
    c.commit()
    return pid, rad


def _naa(dogn=0, timer=0):
    import datetime
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=dogn, hours=timer))


def _aldre_mote(mg, tenant, mote_id, dogn):
    """Fabrikerer alderen med append-only-vakten AVSLÅTT.

    At denne hjelpefunksjonen er nødvendig, er selv et bevis: det
    finnes ingen lovlig vei til å flytte et møte.
    """
    mg.execute("ALTER TABLE mote DISABLE TRIGGER m7_motevakt")
    _sett_kontekst(mg, tenant)
    mg.execute(
        "UPDATE mote SET start_ts = start_ts - make_interval(days => %s),"
        "               slutt_ts = slutt_ts - make_interval(days => %s)"
        " WHERE tenant=%s AND mote_id=%s",
        (dogn, dogn, tenant, mote_id))
    mg.execute("ALTER TABLE mote ENABLE TRIGGER m7_motevakt")
    mg.commit()


# ---------------------------------------------------------------------
# §0: hver invariant i `m7-v1` har minst én port.
# ---------------------------------------------------------------------

def test_grensen_er_registrert_og_hver_invariant_har_en_port():
    """§0-regelen, målt."""
    from manifestskjema import KRAVGRENSER
    inv = set(KRAVGRENSER["m7-v1"]["invarianter"])
    assert inv
    tekst = Path(__file__).read_text(encoding="utf-8")
    mangler = sorted(i for i in inv if i not in tekst)
    assert mangler == [], f"invarianter uten port: {mangler}"


# =====================================================================
# `opptak_uten_hjemmel` OG `opptak_uten_varsling` — REKKEFØLGEN ER
# REGELEN.
# =====================================================================

@pg
def test_opptak_uten_hjemmel_nektes_for_raden_finnes():
    """ET NEKT SOM KOMMER ETTER MIKROFONEN ER IKKE ET NEKT.

    Et opptak tatt uten grunnlag er ulovlig i det øyeblikket det
    starter. Å oppdage det i en nattlig sveip er å oppdage en skade,
    ikke å hindre den — derfor nekter DØRA, og kolonnen er NOT NULL
    med fremmednøkkel så raden ikke kan finnes uten.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("utenhjemmel")
        _krav(rt, t)
        mid = _mote(rt, t)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.NoDataFound):
            rt.execute(
                "SELECT m7_start_opptak(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), mid, uuid.uuid4(), _naa(timer=-3),
                 "u-kari", ["ext:1"], _naa(timer=-2), "u-test"))
        rt.rollback()
        # …OG INGEN RAD BLE SKREVET.
        _sett_kontekst(mg, t)
        n = mg.execute("SELECT count(*) FROM moteopptak WHERE tenant=%s",
                       (t,)).fetchone()[0]
        mg.rollback()
    assert n == 0, "et opptak ble registrert uten hjemmel"


@pg
def test_opptak_paa_utlopt_hjemmel_nektes():
    """EN UTLØPT HJEMMEL SER NØYAKTIG UT SOM EN GYLDIG.

    Klynge 7s dom, og den gjelder her: hjemmelen står der, den ser
    riktig ut, og den er ikke gyldig lenger.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("utlopt")
        _krav(rt, t)
        hid = _hjemmel(rt, t, fra=_dag(-100), til=_dag(-50))
        mid = _mote(rt, t)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute(
                "SELECT m7_start_opptak(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), mid, hid, _naa(timer=-3), "u-kari",
                 ["ext:1"], _naa(timer=-2), "u-test"))
        rt.rollback()
        del mg


@pg
def test_varsling_etter_opptaksstart_nektes_i_to_lag():
    """REKKEFØLGEN ER HELE REGELEN.

    En varsling registrert i etterkant er ikke en varsling — det er en
    unnskyldning. Døra nekter, OG CHECKen i tabellen nekter: to lag,
    fordi et opptak er den ene handlingen i modulen som ikke kan
    gjøres ugjort.

    MUTASJONEN SOM DREPER DENNE: fjern `IF p_varslet_ts > p_startet`
    i `m7_start_opptak`. CHECKen tar den fortsatt — og det er poenget
    med to lag.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("varsletetter")
        _krav(rt, t)
        hid = _hjemmel(rt, t)
        mid = _mote(rt, t)
        # LAG 1: døra.
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute(
                "SELECT m7_start_opptak(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), mid, hid, _naa(timer=-1), "u-kari",
                 ["ext:1"], _naa(timer=-2), "u-test"))
        rt.rollback()
        # LAG 2: CHECKen, som holder selv om noen skriver utenom døra.
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.CheckViolation):
            mg.execute(
                "INSERT INTO moteopptak (tenant, opptak_id, mote_id,"
                " hjemmel_id, varslet_ts, varslet_av, varslede,"
                " startet_ts, registrert_av)"
                " VALUES (%s,%s,%s,%s,%s,'u',ARRAY['ext:1'],%s,'u')",
                (t, uuid.uuid4(), mid, hid, _naa(timer=-1),
                 _naa(timer=-2)))
        mg.rollback()


@pg
def test_opptak_uten_varslede_nektes():
    """«Alle ble varslet» er ikke en liste."""
    with _to() as (rt, mg):
        t = _tenantnavn("ingenvarslet")
        _krav(rt, t)
        hid = _hjemmel(rt, t)
        mid = _mote(rt, t)
        _sett_kontekst(rt, t)
        for varslede in ([], [""], ["   "]):
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                rt.execute(
                    "SELECT m7_start_opptak"
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (t, uuid.uuid4(), mid, hid, _naa(timer=-3),
                     "u-kari", varslede, _naa(timer=-2), "u-test"))
            rt.rollback()
            _sett_kontekst(rt, t)
        rt.rollback()
        del mg


@pg
def test_opptak_datert_fram_i_tid_nektes():
    """Uten dette kunne varslingskravet oppfylles ved å datere
    opptaket fram i tid — og da hadde regelen vært triviell."""
    with _to() as (rt, mg):
        t = _tenantnavn("framtid")
        _krav(rt, t)
        hid = _hjemmel(rt, t)
        mid = _mote(rt, t)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute(
                "SELECT m7_start_opptak(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), mid, hid, _naa(), "u-kari",
                 ["ext:1"], _naa(timer=1), "u-test"))
        rt.rollback()
        del mg


@pg
def test_et_lovlig_opptak_gaar_gjennom_og_baerer_grunnlaget():
    """Den som starter et opptak skal se hva det hviler på i SAMME
    svar — ikke måtte slå det opp etterpå."""
    with _to() as (rt, mg):
        t = _tenantnavn("lovlig")
        _krav(rt, t)
        hid = _hjemmel(rt, t, grunnlagstype="avtale")
        mid = _mote(rt, t)
        _sett_kontekst(rt, t)
        rad = rt.execute(
            "SELECT * FROM m7_start_opptak(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (t, uuid.uuid4(), mid, hid, _naa(timer=-3), "u-kari",
             ["ext:1", "ext:2"], _naa(timer=-2), "u-test")).fetchone()
        rt.commit()
    assert rad[1] == "avtale" and rad[2] is True


@pg
def test_hjemmelen_er_ikke_bare_samtykke():
    """SAMTYKKE ER ETT AV FIRE, OG OFTE DET SVAKESTE.

    I en arbeidsrelasjon er samtykke ofte ikke gyldig — maktubalansen
    gjør det. En modell som bare kjente samtykke ville tvunget fram et
    ugyldig grunnlag for å komme videre.

    `samtykkehendelse` (M-44, 114) er heller ikke denne hjemmelen: den
    er nøklet på `mottaker_id`, `kanal` og `formal`, og svarer på «har
    vi lov til å sende dette».
    """
    with _mig() as mg:
        sjekk = mg.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid='opptakshjemmel'::regclass"
            "   AND conname='opptakshjemmel_grunnlagstype_lukket'"
        ).fetchone()[0]
        # M-44s register er noe annet, og kolonnene sier det.
        kolonner = mg.execute(
            "SELECT string_agg(attname, ',' ORDER BY attnum)"
            "  FROM pg_attribute"
            " WHERE attrelid='samtykkehendelse'::regclass"
            "   AND attnum>0 AND NOT attisdropped").fetchone()[0]
    for verdi in ("samtykke", "avtale", "berettiget_interesse",
                  "rettslig_forpliktelse"):
        assert verdi in sjekk, verdi
    assert "mottaker_id" in kolonner and "kanal" in kolonner, (
        "M-44s samtykkeregister har endret form — grensen mot M-7 må"
        f" vurderes på nytt: {kolonner}")


@pg
def test_sveipen_reiser_aldri_opptak_uten_hjemmel():
    """AT FUNNET IKKE KAN OPPSTÅ ER BEVISET.

    `moteopptak.hjemmel_id` er NOT NULL med fremmednøkkel, og døra
    nekter før raden finnes. Funntypen står i det lukkede settet fordi
    invarianten heter det — og denne porten viser at vernet ligger i
    datamodellen og ikke i en nattlig sjekk.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("aldri")
        _krav(rt, t)
        hid = _hjemmel(rt, t)
        mid = _mote(rt, t)
        _sett_kontekst(rt, t)
        rt.execute(
            "SELECT m7_start_opptak(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (t, uuid.uuid4(), mid, hid, _naa(timer=-3), "u-kari",
             ["ext:1"], _naa(timer=-2), "u-test"))
        rt.commit()
        with _sv() as sv:
            sv.execute("SELECT * FROM m7_sveip_moter(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        n = mg.execute(
            "SELECT count(*) FROM motefunn"
            " WHERE funntype='opptak_uten_hjemmel'").fetchone()[0]
        mg.rollback()
        kode = _bare_kode(MIGRASJON)
        reisinger = kode.count("'opptak_uten_hjemmel'")
    assert n == 0, "funnet ble reist — NOT NULL holder ikke"
    assert reisinger == 1, (
        "funntypen nevnes i mer enn det lukkede settet — noe kan reise"
        f" den ({reisinger} treff)")


# =====================================================================
# `modulen_fattet_beslutning` — V1-DOMMEN.
# =====================================================================

@pg
def test_en_beslutning_uten_et_navn_er_urepresenterbar():
    """En beslutning uten et menneske bak er ikke en beslutning modulen
    SKREV NED — det er en beslutning modulen FATTET.

    To lag: døra nekter på tomt, og kolonnen er NOT NULL.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("beslutning")
        _krav(rt, t)
        mid = _mote(rt, t)
        _sett_kontekst(rt, t)
        for navn in (None, "", "   "):
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                rt.execute(
                    "SELECT m7_registrer_beslutning"
                    "(%s,%s,%s,%s,%s,%s,%s,%s)",
                    (t, uuid.uuid4(), mid, "Vi utsetter innkjoepet",
                     navn, _naa(), None, "u-test"))
            rt.rollback()
            _sett_kontekst(rt, t)
        rt.rollback()
        # LAG 2: kolonnen.
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.NotNullViolation):
            mg.execute(
                "INSERT INTO motebeslutning (tenant, beslutning_id,"
                " mote_id, tekst, besluttet_av, besluttet_ts,"
                " registrert_av)"
                " VALUES (%s,%s,%s,'noe som ble vedtatt',NULL,now(),'u')",
                (t, uuid.uuid4(), mid))
        mg.rollback()


def test_ingen_kodevei_fatter_en_beslutning():
    """Porten leser koden UTEN kommentarer og strenger.

    128s lærdom: en tidligere port traff LOCALE-NØKKELEN som SIER at
    modulen ikke fatter beslutninger — altså forklaringen, ikke
    mønsteret.
    """
    for fil in (MIGRASJON, *MODULFILER, FLATE):
        kode = _bare_kode(fil, uten_strenger=True).lower()
        for ord_ in ("fatt_beslutning", "fattbeslutning",
                     "auto_beslutning", "beslutt_selv"):
            assert ord_ not in kode, f"{fil.name} inneholder «{ord_}»"


def test_ingen_driftsfil_kan_snakke_ut():
    """Gjerdet står i koden, ikke i en kommentar. Et referat som kunne
    sendes ut av sveipen ville vært en ytring ingen godkjente."""
    for fil in MODULFILER:
        kode = _bare_kode(fil, uten_strenger=True)
        for modul in ("httpx", "requests", "socket", "urllib",
                      "smtplib", "http.client"):
            assert f"import {modul}" not in kode, f"{fil.name}: {modul}"


# =====================================================================
# `usikkerhet_skjult` OG `referat_uten_kilde`.
# =====================================================================

@pg
def test_flagget_kan_ikke_lyve_om_sitt_eget_tall():
    """Uten CHECKen kunne en rad si «bekreftet» på 20 % sikkerhet — og
    det er nøyaktig løgnen vaktsetningen forbyr."""
    with _to() as (rt, mg):
        t = _tenantnavn("lyver")
        _krav(rt, t, terskel=7000)
        mid = _mote(rt, t)
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.CheckViolation):
            mg.execute(
                "INSERT INTO referatpunkt (tenant, punkt_id, mote_id,"
                " rekkefolge, tekst, kilde, kilde_ref, sikkerhet_bp,"
                " terskel_bp, ubekreftet, registrert_av)"
                " VALUES (%s,%s,%s,1,'noe','agenda','p1',2000,7000,"
                "         false,'u')",
                (t, uuid.uuid4(), mid))
        mg.rollback()
        del rt


@pg
def test_terskelen_som_gjaldt_da_baeres_paa_raden():
    """Terskelen er tenantens og kan endres — men et punkt som ble
    merket ubekreftet DEN GANG skal fortsatt stå som ubekreftet når
    noen leser referatet et halvt år senere.

    Uten `terskel_bp` på raden kan «hvorfor er dette merket?» ikke
    besvares etter at grensen er justert. Samme form som `kravversjon`
    i klynge 7 og `modellversjon` i klynge 8.

    MUTASJONEN SOM DREPER DENNE: la døra lese terskelen på nytt ved
    lesing i stedet for å skrive den på raden.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("terskel")
        _krav(rt, t, terskel=7000)
        mid = _mote(rt, t)
        _punkt(rt, t, mid, sikkerhet=6000)
        # Tenanten senker kravet etterpå.
        _krav(rt, t, terskel=3000)
        _sett_kontekst(rt, t)
        rad = rt.execute("SELECT * FROM m7_referatet(%s,%s)",
                         (t, mid)).fetchone()
        rt.rollback()
        del mg
    assert rad[6] == 7000, f"terskelen fulgte kravet: {rad[6]}"
    assert rad[7] is True, "punktet ble ubekreftet av seg selv"


@pg
def test_et_manuelt_punkt_er_alltid_sikkert():
    """Et menneske som skriver selv er ikke 60 % sikker på hva det selv
    mente. Døra setter det, og CHECKen krever det."""
    with _to() as (rt, mg):
        t = _tenantnavn("manuell")
        _krav(rt, t)
        mid = _mote(rt, t)
        _, rad = _punkt(rt, t, mid, kilde="manuell",
                        kilde_ref="u-kari", sikkerhet=4000)
        assert rad[1] is False, "et manuelt punkt ble ubekreftet"
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.CheckViolation):
            mg.execute(
                "INSERT INTO referatpunkt (tenant, punkt_id, mote_id,"
                " rekkefolge, tekst, kilde, kilde_ref, sikkerhet_bp,"
                " terskel_bp, ubekreftet, registrert_av)"
                " VALUES (%s,%s,%s,9,'noe','manuell','u',4000,7000,"
                "         true,'u')",
                (t, uuid.uuid4(), mid))
        mg.rollback()


@pg
def test_et_opptakspunkt_maa_peke_paa_et_opptak_som_finnes():
    """Uten dette kunne `kilde_ref` vært hva som helst, og «hva hviler
    dette på?» ubesvarlig."""
    with _to() as (rt, mg):
        t = _tenantnavn("kilde")
        _krav(rt, t)
        mid = _mote(rt, t)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.NoDataFound):
            rt.execute(
                "SELECT m7_registrer_referatpunkt"
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), mid, 1, "noe", "opptak",
                 "finnes-ikke", 9000, None, "u-test"))
        rt.rollback()
        del mg


@pg
def test_referatpunkt_uten_registrerte_grenser_nektes():
    """Uten en sikkerhetsterskel kan ingenting merkes ubekreftet, og da
    er merkingen en tilfeldighet."""
    with _to() as (rt, mg):
        t = _tenantnavn("utenkrav")
        # Møtet må registreres uten `_krav`, så vi går rett på døra.
        _sett_kontekst(rt, t)
        mid = uuid.uuid4()
        rt.execute(
            "SELECT m7_registrer_mote(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (t, mid, "Uten krav", _naa(dogn=-2),
             _naa(dogn=-2, timer=1), "u-kari", ["ext:1"], "agenda",
             "u-test"))
        rt.commit()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.NoDataFound):
            rt.execute(
                "SELECT m7_registrer_referatpunkt"
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), mid, 1, "noe", "agenda", "p1",
                 9000, None, "u-test"))
        rt.rollback()
        del mg


@pg
def test_lesedora_gir_aldri_et_punkt_uten_ubekreftet():
    """`usikkerhet_skjult` håndhevet der den faktisk kan brytes: i det
    som forlater basen."""
    with _to() as (rt, mg):
        t = _tenantnavn("lesedor")
        _krav(rt, t)
        mid = _mote(rt, t)
        _punkt(rt, t, mid, rekkefolge=1, sikkerhet=9000)
        _punkt(rt, t, mid, rekkefolge=2, sikkerhet=4000)
        _sett_kontekst(rt, t)
        rader = rt.execute("SELECT * FROM m7_referatet(%s,%s)",
                           (t, mid)).fetchall()
        rt.rollback()
        del mg
    assert len(rader) == 2
    assert [r[7] for r in rader] == [False, True]
    assert all(isinstance(r[6], int) for r in rader)


# =====================================================================
# `aksjon_uten_eier` OG `referat_overskrevet`.
# =====================================================================

@pg
def test_aksjon_uten_eier_er_urepresenterbar():
    """En aksjon uten eier er en aksjon ingen gjør. Sveipen kan ikke
    reise funnet, og AT DEN IKKE KAN er beviset."""
    with _mig() as mg:
        rad = mg.execute(
            "SELECT is_nullable, column_default FROM"
            " information_schema.columns"
            " WHERE table_name='moteaksjon' AND column_name='eier'"
        ).fetchone()
    assert rad == ("NO", None), f"eier: {rad}"


@pg
def test_referat_overskrevet_er_umulig_og_rettelser_er_nye_punkter():
    """Et referat er en gjengivelse avgitt på et tidspunkt.

    RETTELSER GJØRES SOM NYE PUNKTER som peker på det de retter — ikke
    ved å skrive om det som sto der, for da ville «hva sto i referatet
    da vi vedtok dette?» vært ubesvarlig.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("frosset")
        _krav(rt, t)
        hid = _hjemmel(rt, t)
        mid = _mote(rt, t)
        pid, _ = _punkt(rt, t, mid, sikkerhet=4000)
        # RADENE MÅ FINNES, ellers treffer UPDATE-ene null rader og
        # porten er grønn uten at én vakt ble prøvd. Det er nøyaktig
        # den feilformen denne suiten leter etter andre steder.
        _sett_kontekst(rt, t)
        rt.execute(
            "SELECT m7_start_opptak(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (t, uuid.uuid4(), mid, hid, _naa(timer=-3), "u-kari",
             ["ext:1"], _naa(timer=-2), "u-test"))
        rt.execute(
            "SELECT m7_registrer_beslutning(%s,%s,%s,%s,%s,%s,%s,%s)",
            (t, uuid.uuid4(), mid, "Vi utsetter innkjoepet", "u-kari",
             _naa(timer=-2), pid, "u-test"))
        rt.commit()
        _sett_kontekst(mg, t)
        for setning in ("UPDATE referatpunkt SET tekst='noe annet'",
                        "DELETE FROM referatpunkt",
                        "UPDATE motebeslutning SET tekst='x'",
                        "UPDATE moteopptak SET varslet_av='x'"):
            _sett_kontekst(mg, t)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                mg.execute(setning + " WHERE tenant=%s", (t,))
            mg.rollback()
        # …OG RETTELSEN ER ET NYTT PUNKT SOM PEKER TILBAKE.
        pid2, _ = _punkt(rt, t, mid, rekkefolge=1,
                         tekst="Rettet: det var noe annet",
                         kilde="manuell", kilde_ref="u-kari",
                         retter=pid)
        _sett_kontekst(rt, t)
        rader = rt.execute("SELECT * FROM m7_referatet(%s,%s)",
                           (t, mid)).fetchall()
        rt.rollback()
    assert len(rader) == 2, "rettelsen erstattet punktet"
    original = [r for r in rader if r[0] == pid][0]
    assert original[11] is True, "det rettede punktet er ikke merket"


@pg
def test_motet_kan_ikke_flyttes():
    """Et møte som kunne flyttes i ettertid ville gjort referatfristen
    til noe som alltid kan overholdes."""
    with _to() as (rt, mg):
        t = _tenantnavn("flytt")
        _krav(rt, t)
        mid = _mote(rt, t)
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            mg.execute("UPDATE mote SET start_ts = now()"
                       " WHERE tenant=%s AND mote_id=%s", (t, mid))
        mg.rollback()
        del rt


@pg
def test_hjemmelen_er_frosset_og_avslutning_er_enveis():
    """121s dom. En hjemmel som kunne redigeres ville gjort «hva hvilte
    opptaket på?» til et oppslag i noe som har endret seg siden."""
    with _to() as (rt, mg):
        t = _tenantnavn("hjemmelfrys")
        _krav(rt, t)
        hid = _hjemmel(rt, t)
        _sett_kontekst(mg, t)
        mg.execute("SET LOCAL ROLE disponit_mote_eier")
        for felt in ("grunnlagstype='samtykke'", "formal='noe annet'",
                     "gyldig_fra=current_date"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                mg.execute(f"UPDATE opptakshjemmel SET {felt}"
                           " WHERE tenant=%s AND hjemmel_id=%s",
                           (t, hid))
            mg.rollback()
            mg.execute("SET LOCAL ROLE disponit_mote_eier")
            _sett_kontekst(mg, t)
        mg.rollback()
        _sett_kontekst(rt, t)
        rt.execute("SELECT m7_avslutt_hjemmel(%s,%s,%s,%s)",
                   (t, hid, _dag(0), "u-test"))
        rt.commit()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT m7_avslutt_hjemmel(%s,%s,%s,%s)",
                       (t, hid, _dag(5), "u-test"))
        rt.rollback()


# =====================================================================
# `tenantlekkasje_i_moteregister`.
# =====================================================================

@pg
def test_tenantlekkasje_i_moteregister_er_umulig():
    """FORCE ROW LEVEL SECURITY på alle åtte."""
    with _mig() as mg:
        rader = mg.execute(
            "SELECT relname, relrowsecurity, relforcerowsecurity"
            "  FROM pg_class WHERE relname = ANY(%s)",
            (list(EGNE),)).fetchall()
    assert len(rader) == len(EGNE)
    for navn, pa, tvang in rader:
        assert pa and tvang, f"{navn}: rls={pa} force={tvang}"


@pg
def test_en_tenant_ser_ikke_en_annens_referat():
    """Radvakten, målt og ikke bare erklært."""
    with _to() as (rt, mg):
        a, b = _tenantnavn("a"), _tenantnavn("b")
        _krav(rt, a)
        mid = _mote(rt, a)
        _punkt(rt, a, mid)
        _sett_kontekst(rt, b)
        n = rt.execute("SELECT count(*) FROM m7_moteregister(%s,%s)",
                       (b, 100)).fetchone()[0]
        p = rt.execute("SELECT count(*) FROM m7_referatet(%s,%s)",
                       (b, mid)).fetchone()[0]
        rt.rollback()
        del mg
    assert (n, p) == (0, 0)


# =====================================================================
# SVEIPEN, DRIFTA OG FLATEN.
# =====================================================================

@pg
def test_mote_uten_referat_reises_og_lukkes_av_referatet():
    """Klyngens funn: et referat som ikke finnes er en gjengivelse
    ingen kan etterprøve."""
    with _to() as (rt, mg):
        t = _tenantnavn("utenreferat")
        _krav(rt, t, referatfrist=3)
        mid = _mote(rt, t, start_dogn=-2)
        _aldre_mote(mg, t, mid, 30)
        with _sv() as sv:
            sv.execute("SELECT * FROM m7_sveip_moter(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        apne = mg.execute(
            "SELECT count(*) FROM motefunn WHERE tenant=%s"
            "   AND funntype='mote_uten_referat' AND apen",
            (t,)).fetchone()[0]
        mg.rollback()
        assert apne == 1, f"møtet er over uten referat, {apne} funn"

        _punkt(rt, t, mid, kilde="manuell", kilde_ref="u-kari")
        with _sv() as sv:
            sv.execute("SELECT * FROM m7_sveip_moter(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        etter = mg.execute(
            "SELECT count(*) FILTER (WHERE apen),"
            "       count(*) FILTER (WHERE NOT apen AND"
            "                        lukket_av='m7_sveip')"
            "  FROM motefunn WHERE tenant=%s"
            "   AND funntype='mote_uten_referat'", (t,)).fetchone()
        mg.rollback()
    assert etter == (0, 1), f"lukkingen fulgte ikke referatet: {etter}"


@pg
def test_et_menneske_kan_ikke_lukke_sveipens_funn():
    """`mote_uten_referat` og `aksjon_over_frist` lukkes av at
    TILSTANDEN opphører, ikke av at noen huker av."""
    with _to() as (rt, mg):
        t = _tenantnavn("nekt")
        _krav(rt, t)
        mid = _mote(rt, t)
        _aldre_mote(mg, t, mid, 30)
        with _sv() as sv:
            sv.execute("SELECT * FROM m7_sveip_moter(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        fid = mg.execute(
            "SELECT funn_id FROM motefunn WHERE tenant=%s"
            "   AND funntype='mote_uten_referat' LIMIT 1",
            (t,)).fetchone()[0]
        mg.rollback()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT m7_lukk_funn(%s,%s,%s,%s)",
                       (t, fid, "vi tar det senere", "u-test"))
        rt.rollback()


@pg
def test_ubekreftet_punkt_kan_lukkes_av_et_menneske_og_forblir_lukket():
    """«Vi har lest det, det stemmer» er en legitim avklaring med et
    navn på — og 131s lærdom gjelder: sveipen skal ikke gjenåpne den
    natten etter.

    MUTASJONEN SOM DREPER DENNE: fjern `NOT EXISTS`-leddet på lukkede
    funn i sveipens tredje blokk.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("avklart")
        _krav(rt, t)
        mid = _mote(rt, t)
        _punkt(rt, t, mid, sikkerhet=4000)
        with _sv() as sv:
            sv.execute("SELECT * FROM m7_sveip_moter(500)")
            sv.commit()
        _sett_kontekst(rt, t)
        funn = [f for f in rt.execute(
            "SELECT * FROM m7_motefunn(%s,%s)", (t, 100)).fetchall()
            if f[1] == "ubekreftet_punkt_uavklart"]
        rt.rollback()
        assert funn, "det ubekreftede punktet ble ikke sett"
        assert funn[0][9] is True, "et menneske kan ikke lukke det"
        _sett_kontekst(rt, t)
        rt.execute("SELECT m7_lukk_funn(%s,%s,%s,%s)",
                   (t, funn[0][0], "vi har lest det, det stemmer",
                    "u-test"))
        rt.commit()
        # NATTEN ETTER.
        with _sv() as sv:
            sv.execute("SELECT * FROM m7_sveip_moter(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        apne = mg.execute(
            "SELECT count(*) FROM motefunn WHERE tenant=%s"
            "   AND funntype='ubekreftet_punkt_uavklart' AND apen",
            (t,)).fetchone()[0]
        mg.rollback()
    assert apne == 0, "sveipen gjenåpnet en avklaring"


@pg
def test_runtime_har_ingen_tabellrettigheter():
    """SP-7: kjøretiden når dørene og ingenting annet."""
    with _mig() as mg:
        rader = mg.execute(
            "SELECT table_name, privilege_type FROM"
            " information_schema.table_privileges"
            " WHERE grantee='disponit' AND table_name = ANY(%s)",
            (list(EGNE),)).fetchall()
    assert rader == [], f"runtime har tabellrettigheter: {rader}"


@pg
def test_sveiperollen_naar_en_funksjon_og_bare_den():
    """111s form."""
    with _mig() as mg:
        rader = mg.execute(
            "SELECT p.proname FROM pg_proc p"
            " WHERE p.proname LIKE 'm7\\_%'"
            "   AND has_function_privilege('disponit_motesveip',"
            "                              p.oid, 'EXECUTE')"
        ).fetchall()
    assert sorted(r[0] for r in rader) == ["m7_sveip_moter"]


@pg
def test_sveipen_ser_ingenting_uten_kryss_tenant_policyen():
    """130s LÆRDOM: en sveip uten `disponit.tenant` ville sett NULL
    RADER og rapportert null funn — med grønn exit-kode."""
    with _mig() as mg:
        rad = mg.execute(
            "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy"
            " WHERE polrelid='motekrav'::regclass"
            "   AND polname='m7_sveip_tenantliste'").fetchone()
    assert rad, "kryss-tenant-policyen mangler — sveipen ville vært blind"
    assert "IS NULL" in rad[0], f"policyen er ikke snever nok: {rad[0]}"


@pg
def test_sveipen_teller_tenanter_og_gir_fire_felt():
    """Kontrakten driftsfila leser."""
    with _to() as (rt, mg):
        t = _tenantnavn("kontrakt")
        _krav(rt, t)
        with _sv() as sv:
            rader = sv.execute(
                "SELECT * FROM m7_sveip_moter(500)").fetchall()
            sv.commit()
        del mg
    assert len(rader) == 1 and len(rader[0]) == 4
    assert rader[0][0] >= 1


@pg
def test_ingen_m7_funksjon_er_immutable_naar_den_leser_naa():
    """125s LÆRDOM, målt over hele katalogen."""
    with _mig() as mg:
        rader = mg.execute(
            "SELECT p.proname FROM pg_proc p"
            " WHERE p.provolatile='i'"
            "   AND (pg_get_functiondef(p.oid) ILIKE '%current_date%'"
            "     OR pg_get_functiondef(p.oid) ILIKE '%now()%')"
            "   AND p.pronamespace='public'::regnamespace"
        ).fetchall()
    assert rader == [], f"IMMUTABLE funksjoner som leser nå: {rader}"


def test_sveipens_arbeidernokkel_er_modulens_egen():
    """To sveip som delte nøkkel ville blokkert hverandre i stillhet."""
    nokler = {}
    for fil in sorted((ROT / "platform" / "drift").glob("*sveip.py")):
        m = re.search(r"ARBEIDERNOKKEL = ([\d_]+)",
                      fil.read_text(encoding="utf-8"))
        if m:
            nokler.setdefault(m.group(1), []).append(fil.name)
    delte = {k: v for k, v in nokler.items() if len(v) > 1}
    assert delte == {}, f"delte arbeidernøkler: {delte}"


def test_driftsfila_navngir_sin_egen_jobb():
    """Arvefeilen fra 116-118, og fra kjørerne i 130/132."""
    sti = ROT / "deploy" / "staging" / "disponit-motesveip.service"
    tj = sti.read_text(encoding="utf-8")
    beskrivelse = tj.split("Description=")[1][:110]
    assert "møtesveip" in beskrivelse
    for arvet in ("likviditet", "bemanning", "rangering", "EHF",
                  "HMS", "kontantbane"):
        assert arvet not in beskrivelse, f"arvet ord: {arvet}"
    assert ("LoadCredential=DISPONIT_MOTESVEIP_URL:"
            "/etc/disponit/motesveip/DISPONIT_MOTESVEIP_URL" in tj)
    kjorer = (ROT / "platform" / "drift"
              / "kjor_motesveip.py").read_text(encoding="utf-8")
    assert "m7_sveip_moter()" in kjorer
    for arvet in ("m33_sveip_prognose", "m36_sveip_optimalisering",
                  "m50_sveip_postjournal", "(130 REVOKEr",
                  "(124 REVOKEr"):
        assert arvet not in kjorer, f"arvet referanse: {arvet}"


def test_sveipen_rekker_aa_bli_ferdig_for_statussveipen_starter():
    """ET KLOKKESLETT ER IKKE EN REKKEFØLGE (132s lærdom).

    Det som må gå opp er START + SPREDNING + `TimeoutStartSec` for
    HVER overvåket sveip, målt mot statussveipens TIDLIGSTE start.
    """
    katalog = ROT / "deploy" / "staging"

    def _tid(fil):
        tekst = (katalog / fil).read_text(encoding="utf-8")
        kl = re.search(r"OnCalendar=\*-\*-\* (\d\d):(\d\d):00 UTC",
                       tekst)
        sp = re.search(r"RandomizedDelaySec=(\d+)min", tekst)
        assert kl, f"{fil} har intet klokkeslett"
        return (int(kl.group(1)) * 60 + int(kl.group(2)),
                int(sp.group(1)) if sp else 0)

    status_start, _ = _tid("disponit-sveipestatus.timer")
    for fil in sorted(katalog.glob("disponit-*sveip.timer")):
        tekst = fil.read_text(encoding="utf-8")
        if "OnCalendar" not in tekst:
            continue
        start, spredning = _tid(fil.name)
        tj = katalog / fil.name.replace(".timer", ".service")
        m = (re.search(r"TimeoutStartSec=(\d+)min",
                       tj.read_text(encoding="utf-8"))
             if tj.exists() else None)
        slutt = start + spredning + (int(m.group(1)) if m else 0)
        assert slutt <= status_start, (
            f"{fil.name} kan holde på til {slutt // 60}:"
            f"{slutt % 60:02d} mens statussveipen kan starte"
            f" {status_start // 60}:{status_start % 60:02d}")
    assert _tid("disponit-motesveip.timer") == (6 * 60 + 35, 4)


def test_sveipens_dsn_star_i_ci():
    """127s LÆRDOM. Navnet hentes fra KJØREREN, ikke fra filnavnet."""
    kjorer = ROT / "platform" / "drift" / "kjor_motesveip.py"
    url = re.findall(r"DISPONIT_[A-Z0-9_]+_URL",
                     kjorer.read_text(encoding="utf-8"))
    assert url, "kjøreren leser ingen DSN"
    ventet = url[0].replace("DISPONIT_", "DISPONIT_TEST_", 1)
    ventet = ventet[:-len("_URL")] + "_DSN"
    ci = (ROT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8")
    assert f"{ventet}:" in ci, f"{ventet} mangler i ci.yml"
    opp = (ROT / "deploy" / "staging" / "opp.sh").read_text(
        encoding="utf-8")
    assert url[0] in opp, f"{url[0]} mangler i opp.sh"


def test_flaten_viser_usikkerheten_i_samme_rad_som_teksten():
    """`usikkerhet_skjult`, målt i flaten.

    Et referat ser likt ut enten et menneske skrev det eller en
    transkripsjon gjettet. Derfor står merkingen i SAMME RAD.
    """
    kode = FLATE.read_text(encoding="utf-8")
    tabell = kode.split("export function referattabell")[1].split(
        "export function")[0]
    for felt in ("p.ubekreftet", "p.kilde", "p.sikkerhet_bp",
                 "p.terskel_bp"):
        assert felt in tabell, f"{felt} mangler i referattabellen"


def test_flaten_viser_opptakets_grunnlag_uten_et_klikk_til():
    """Et opptak uten synlig hjemmel ville sett ut som et opptak uten
    hjemmel, og forskjellen er hele modulen."""
    kode = FLATE.read_text(encoding="utf-8")
    tabell = kode.split("export function motetabell")[1].split(
        "export function")[0]
    assert "m.opptakshjemmel" in tabell
    assert "GRUNNLAGSTEKST" in tabell


def test_apiet_gir_hele_bildet_i_ett_kall():
    """127s CodeRabbit-funn, ikke gjentatt."""
    from api import moteoperasjon as modul
    kilde = _bare_kode(Path(modul.__file__))
    for del_ in ("sammendrag", "moter", "hjemler", "aksjoner", "funn"):
        assert f'"{del_}"' in kilde, f"svar_for mangler {del_}"


def test_ui_axe_alvorlige_brudd_dekkes_av_flatens_egen_suite():
    """`ui_axe_alvorlige_brudd` måles i `platform/core/ui/test`.

    Porten her er en PEKER, ikke en kopi: to steder som måler det
    samme ville kunnet gi to svar.
    """
    js = ROT / "platform" / "core" / "ui" / "test" / "mote.test.js"
    assert js.exists(), "flatens egen suite mangler"
    tekst = js.read_text(encoding="utf-8")
    assert "axe" in tekst.lower() or "aria" in tekst.lower()


def test_fundamentet_navngir_modulen_og_migrasjonen():
    """Fundamentet tildelte nummeret; koden skal svare til det."""
    tekst = FUNDAMENT.read_text(encoding="utf-8")
    assert "133" in tekst and "M-7" in tekst
    assert MIGRASJON.exists()


@pg
def test_funntabellen_staar_i_m36s_katalog():
    """M-36 (132) NEKTER Å RANGERE mens ett funnregister er ukjent.

    Denne modulen ble stoppet av nettopp det 5/9: `m36_rangere` reiste
    `funnregistre utenfor m36_funnregister (motefunn)`, og HELE M-36s
    suite ble rød av en tabell M-7 la til. Det er ikke en feil i 132 —
    det er 132 som gjør det den ble bygget for.

    PORTEN SPØR ETTER M-7s EGEN RAD OG IKKE ETTER AT KATALOGEN ER
    KOMPLETT. Kompletthet er M-36s invariant og måles av
    `test_ingen_funntabell_faller_utenfor_registeret` — to steder som
    målte det samme kunne gitt to svar. Her står bare det denne
    modulen selv svarer for: at raden finnes, og at «åpen» er kodet
    slik `motefunn` faktisk koder den.
    """
    with _mig() as mg:
        rad = mg.execute(
            "SELECT modul, typekolonne, apenform FROM m36_funnregister"
            " WHERE relasjon='motefunn'").fetchone()
        # …og `apen_kolonne` er sant fordi kolonnen finnes.
        kolonne = mg.execute(
            "SELECT count(*) FROM information_schema.columns"
            " WHERE table_name='motefunn' AND column_name='apen'"
            "   AND data_type='boolean'").fetchone()[0]
        mg.rollback()
    assert rad == ("m7_moteoperasjon", "funntype", "apen_kolonne"), rad
    assert kolonne == 1, "registeret lover en apen-kolonne som ikke finnes"
