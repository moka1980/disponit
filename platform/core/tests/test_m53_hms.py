"""M-53 HMS- og avviksmottak v1 (127) — ET FELT SOM KAN FYLLES BLIR FYLT.

Grensen `m53-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

DENNE MODULEN ER ANNERLEDES ENN DE FIRE ANDRE I KLYNGE 7, og portene
må være det. `docs/KLYNGE7-FUNDAMENT.md` skrev det ned før noen av dem
var bygget: de fire andre PRODUSERER noe som skal ut. Et avviksmottak
TAR IMOT. Og risikoen ligger et helt annet sted — dette er den eneste
modulen i katalogen som mottar data OM en ansatt FRA en ansatt.

DEN SKARPESTE GRUPPEN PORTER HER MÅLER ET FRAVÆR SOM MÅ VÆRE TOTALT.

Et anonymt avvik skal ikke bære aktøren i `opprettet_av`, ikke
tidspunktet i en `TIMESTAMPTZ DEFAULT now()`, ikke navnet i en
melderrad, og ikke aktøren i evidenskjeden. Fire kolonner, fire steder
det kunne lekket — og `revisjonslogg` er append-only siden 001, så et
navn som lekker DIT kan aldri fjernes igjen.

  HUSETS EGEN STANDARDKOLONNE ER LEKKASJEN.

Derfor måler portene ikke bare at API-et lar være å sende navnet, men
at DATABASEN nekter det, at kolonnen ikke har en `DEFAULT` å gli på, og
at melderraden er UMULIG å skrive mot et anonymt avvik.

M-30-GRENSEN ER AVKLART FØR KODEN, i `docs/M53-M30-GRENSESNITTET.md`,
og porten `test_m30_grensen_er_dokumentert_for_koden` krever at
dokumentet finnes og navngir konflikten. M-30 SLETTER ingenting; den
registrerer at noen har bedt om det. Utførelsen skjer her.

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

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo)
from .test_m37 import _sett_kontekst

HMSSVEIP_DSN = os.environ.get("DISPONIT_TEST_HMSSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "127_m53_hms_avvik.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "hms.js")
GRENSEDOK = ROT / "docs" / "M53-M30-GRENSESNITTET.md"
MODULFILER = (
    ROT / "platform" / "core" / "api" / "hms.py",
    ROT / "platform" / "drift" / "hmssveip.py",
    ROT / "platform" / "drift" / "kjor_hmssveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("hmskrav", "hmsregelverk", "hmsavvik", "hmsmelder",
        "hmstiltak", "hmsfunn")

_STRENG = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")


def _bare_kode(fil: Path, *, uten_strenger: bool = False) -> str:
    """Filens innhold uten kommentarer OG uten docstrings (m24-formen).

    PORTEN SKAL MÅLE HANDLINGEN, IKKE BEGRUNNELSEN. Klynge 6 lærte det
    tre ganger, og oppstartsvakten en fjerde: en port som leter i rå
    filtekst treffer kommentaren som forklarer HVORFOR et mønster er
    unngått.
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


def _mig():
    """MIGRATOR — for direkte DML og katalogoppslag.

    Dørene nås IKKE herfra: `REVOKE ALL ... FROM PUBLIC` og et
    EXECUTE-grant til `disponit` alene er hele SP-7-formen. At
    migrator ikke slipper inn er en egenskap, ikke et hinder.
    """
    from db.pg import koble
    return koble(MIGRATOR_DSN)


def _rt():
    """RUNTIME — rollen dørene faktisk er gitt til."""
    from db.pg import koble
    return koble(DSN)


def _sv():
    """SVEIPEROLLEN — ÉN EXECUTE, ingen tabellrettigheter."""
    from db.pg import koble
    return koble(HMSSVEIP_DSN or MIGRATOR_DSN)


@contextlib.contextmanager
def _to():
    """RUNTIME for dørene, MIGRATOR for tabellene.

    SP-7 ER GRUNNEN TIL AT DET MÅ VÆRE TO: runtime har EXECUTE på
    dørene og INGEN tabellrettigheter i det hele tatt, og migrator
    eier tabellene men slipper ikke inn dørene (`REVOKE ALL … FROM
    PUBLIC`). En test som klarte seg med ÉN tilkobling ville målt en
    base der skillet ikke fantes — og det skillet er halve modulen.
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


def _tenantnavn(merke: str) -> str:
    return f"t-m53-{merke}-{secrets.token_hex(4)}"


I_DAG = datetime.date.today()


def _dag(n: int) -> datetime.date:
    return I_DAG + datetime.timedelta(days=n)


def _krav(c, tenant, *, maks=3650, varsel=60, tiltak=14, regel=60,
          aktor="u-test", nokkel=None):
    _sett_kontekst(c, tenant)
    v = c.execute(
        "SELECT versjon FROM m53_sett_krav(%s,%s,%s,%s,%s,%s,%s)",
        (tenant, maks, varsel, tiltak, regel, aktor,
         nokkel or secrets.token_hex(8))).fetchone()[0]
    c.commit()
    return v


def _regel(c, tenant, *, avvikstype="personskade", versjon="2026-01",
           hjemmel="arbeidsmiljoloven 5-1", dogn=1825, helse=True,
           fra=None, til=None, aktor="u-test", regel_id=None):
    _sett_kontekst(c, tenant)
    rid = regel_id or uuid.uuid4()
    c.execute(
        "SELECT m53_registrer_regel(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant, rid, avvikstype, versjon, hjemmel, dogn, helse,
         fra or _dag(-30), til, aktor))
    c.commit()
    return rid


def _avvik(c, tenant, *, avvikstype="personskade", melderform="anonym",
           beskrivelse="Fall fra stillas i tredje etasje",
           sted="Byggeplass A", hendelsesdato=None, navn=None,
           rolle=None, aktor=None, avvik_id=None):
    _sett_kontekst(c, tenant)
    aid = avvik_id or uuid.uuid4()
    rad = c.execute(
        "SELECT * FROM m53_meld_avvik(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant, aid, avvikstype, melderform, beskrivelse, sted,
         hendelsesdato or _dag(-1), navn, rolle, aktor)).fetchone()
    c.commit()
    return aid, rad


# ---------------------------------------------------------------------
# §0: hver invariant i `m53-v1` har minst én port.
#
# `m53-v1` var den SISTE oppføringen i `UBYGDE_GRENSER`. Med denne
# modulen bygget er lista tom, og klynge 7 er ferdig.
# ---------------------------------------------------------------------

def test_grensen_er_registrert_og_hver_invariant_har_en_port():
    """§0-regelen, målt."""
    from manifestskjema import KRAVGRENSER
    inv = set(KRAVGRENSER["m53-v1"]["invarianter"])
    assert inv
    tekst = Path(__file__).read_text(encoding="utf-8")
    mangler = sorted(i for i in inv if i not in tekst)
    assert mangler == [], f"invarianter uten port: {mangler}"


# =====================================================================
# V1-DOMMEN: modulen varsler ingen myndighet og lukker ingen avvik.
# =====================================================================

def test_modulen_varslet_myndighet():
    """INGEN UTBOKS, INGEN MOTTAKER, INGEN SIGNATUR.

    Arbeidstilsynet får ingenting fra oss i v1. En kolonne som betydde
    «sendt» ville vært en påstand om at noen andre har fått noe — og
    den påstanden kan bare den som faktisk sendte, gjøre.

    MUTASJONEN SOM DREPER DENNE: legg til `sendt_ts` på `hmsavvik`.
    """
    sql = _bare_kode(MIGRASJON, uten_strenger=True)
    for ord_ in ("sendt", "innsending", "mottaker", "utboks",
                 "signatur", "arbeidstilsynet_ref"):
        assert not re.search(rf"\b{ord_}\w*\s+(TEXT|TIMESTAMPTZ|UUID|"
                             rf"BOOLEAN|DATE)", sql, re.I), (
            f"en kolonne som betyr «{ord_}» finnes i 127")
    for fil in MODULFILER:
        kode = _bare_kode(fil, uten_strenger=True)
        for modul in ("httpx", "requests", "urllib", "socket",
                      "smtplib", "aiohttp"):
            assert not re.search(rf"^\s*(import|from)\s+{modul}\b",
                                 kode, re.M), (
                f"{fil.name} importerer {modul} — modulen skal ikke"
                " kunne snakke ut")


def test_modulen_lukket_avvik_selv():
    """SVEIPEN HAR INGEN VEI TIL `behandlet`.

    Et avvik lukkes av at et NAVNGITT menneske registrerer et tiltak.
    En sveip som lukket selv, ville vært en HMS-avdeling uten
    mennesker i.

    MUTASJONEN SOM DREPER DENNE: la sveipen sette status = 'behandlet'.
    """
    sql = _bare_kode(MIGRASJON)
    sveip = re.search(r"CREATE FUNCTION m53_sveip_hms\(.*?\nEND \$\$;",
                      sql, re.S)
    assert sveip, "sveipen finnes ikke"
    kropp = sveip.group(0)
    assert "hmsavvik" not in kropp.replace("public.hmsavvik a", "") \
        or "UPDATE public.hmsavvik" not in kropp, (
        "sveipen skriver i hmsavvik")
    assert "'behandlet'" not in kropp, (
        "sveipen setter status behandlet")
    # …og den ENE veien dit krever et menneske.
    doer = re.search(
        r"CREATE FUNCTION m53_registrer_tiltak\(.*?\nEND \$\$;",
        sql, re.S).group(0)
    assert "btrim(p_aktor) = ''" in doer, (
        "tiltaksdøra godtar en tom aktør")


# =====================================================================
# VARSLERVERNET. Den skarpeste gruppen.
# =====================================================================

def test_meldt_ts_har_ingen_default():
    """TIDSSTEMPLET ER OGSÅ IDENTITET.

    `now()` på mikrosekundet, i en bedrift med tolv ansatte og en
    vaktliste, peker på én person. En `DEFAULT now()` ville fylt seg
    selv i det stille og gjort vernet til pynt — nøyaktig som
    `opprettet_av NOT NULL` ville gjort det.

    MUTASJONEN SOM DREPER DENNE: gi `meldt_ts` en DEFAULT.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    tabell = re.search(r"CREATE TABLE hmsavvik \((.*?)\n\);", sql, re.S)
    assert tabell, "hmsavvik finnes ikke"
    kropp = "\n".join(l for l in tabell.group(1).splitlines()
                      if not l.lstrip().startswith("--"))
    linje = [l for l in kropp.splitlines()
             if re.match(r"\s*meldt_ts\s", l)]
    assert linje, "meldt_ts finnes ikke"
    assert "DEFAULT" not in linje[0].upper(), (
        "meldt_ts har en DEFAULT — den ville fylt seg selv")
    assert "NOT NULL" not in linje[0].upper(), (
        "meldt_ts er NOT NULL — da kan et anonymt avvik ikke oppstå")
    for felt in ("meldt_av",):
        rad = [l for l in kropp.splitlines()
               if re.match(rf"\s*{felt}\s", l)]
        assert rad and "NOT NULL" not in rad[0].upper(), (
            f"{felt} er NOT NULL — husets standardkolonne er lekkasjen")


@pg
def test_anonymt_avvik_baerer_verken_aktoer_eller_tidspunkt():
    """DEN BÆRENDE PORTEN — `anonymt_avvik_kan_spores`.

    MUTASJONEN SOM DREPER DENNE: la døra skrive `p_aktor` uansett
    melderform.
    """
    t = _tenantnavn("anon")
    with _to() as (rt, mg):
        _krav(rt, t)
        _regel(rt, t)
        aid, rad = _avvik(rt, t)
        _sett_kontekst(mg, t)
        r = mg.execute(
            "SELECT melderform, meldt_av, meldt_ts, meldt_dato,"
            " (SELECT count(*) FROM hmsmelder m"
            "   WHERE m.tenant=a.tenant AND m.avvik_id=a.avvik_id)"
            " FROM hmsavvik a WHERE a.tenant=%s AND a.avvik_id=%s",
            (t, aid)).fetchone()
    assert r[0] == "anonym"
    assert r[1] is None, "aktøren ble skrevet på et anonymt avvik"
    assert r[2] is None, "tidspunktet ble skrevet på et anonymt avvik"
    assert r[3] == I_DAG, "datoen skal stå — den peker ikke ut noen"
    assert r[4] == 0, "et anonymt avvik fikk en melderrad"
    assert rad[5] is False, "døra påstår at en melder ble lagret"


@pg
def test_anonymt_avvik_med_navn_eller_aktoer_nektes():
    """`varsler_identitet_lekket`: EN DØR SOM STILLE KASTET NAVNET
    VILLE SETT RIKTIG UT I HVER TEST.

    Nektet er poenget: kalleren skal få vite at den gjorde noe galt,
    ikke oppdage et halvt år senere at feltet aldri kom fram.
    """
    t = _tenantnavn("nekt")
    with _to() as (rt, _mg):
        _krav(rt, t)
        _regel(rt, t)
        for navn, aktor in (("Kari", None), (None, "u-kari")):
            _sett_kontekst(rt, t)
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                rt.execute(
                    "SELECT * FROM m53_meld_avvik(%s,%s,%s,%s,%s,%s,"
                    "%s,%s,%s,%s)",
                    (t, uuid.uuid4(), "personskade", "anonym",
                     "Fall fra stillas i tredje etasje", "Bygg A",
                     _dag(-1), navn, None, aktor))
            rt.rollback()


@pg
def test_melderrad_mot_anonymt_avvik_er_umulig():
    """ANONYMITET ER FRAVÆRET AV EN RAD, IKKE ET TOMT FELT.

    Uten denne vakten ville anonymiteten hvilt på at hver skrivevei
    husket det. Med den finnes det ingen kolonne å fylle.

    MUTASJONEN SOM DREPER DENNE: fjern triggeren
    `hmsmelder_krever_navngitt`.
    """
    t = _tenantnavn("melder")
    with _to() as (rt, mg):
        _krav(rt, t)
        _regel(rt, t)
        aid, _ = _avvik(rt, t)
        _sett_kontekst(mg, t)
        mg.execute("SET LOCAL ROLE disponit_hms_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            mg.execute(
                "INSERT INTO hmsmelder (tenant, avvik_id, navn,"
                " slettefrist) VALUES (%s,%s,'Kari',%s)",
                (t, aid, _dag(100)))


@pg
def test_evidenskjeden_baerer_verken_varsler_eller_beskrivelse():
    """`evidenskjede_baerer_varsler`. `revisjonslogg` ER APPEND-ONLY
    SIDEN 001.

    Et navn som lekker inn her kan ALDRI fjernes igjen — den samme
    garantien som gjør beviskjeden troverdig, gjør lekkasjen permanent.

    OG BESKRIVELSEN GÅR ALDRI INN I HASHEN. En hash er enveis, men den
    lar hvem som helst BEKREFTE en gjetning, og «var det Kari som
    meldte om formannen?» er nøyaktig den gjetningen vernet skal gjøre
    ubesvarbar.
    """
    t = _tenantnavn("evidens")
    hemmelig = f"stillasfall-{secrets.token_hex(6)}"
    with _to() as (rt, mg):
        _krav(rt, t)
        _regel(rt, t)
        _avvik(rt, t, beskrivelse=f"{hemmelig} i tredje etasje")
        _sett_kontekst(mg, t)
        rader = mg.execute(
            "SELECT handling, aktor, begrunnelse::text"
            "  FROM revisjonslogg"
            " WHERE tenant=%s AND kilde='m53_hms'", (t,)).fetchall()
    assert rader, "evidenskjeden skrev ingenting"
    meldinger = [r for r in rader if r[0] == "avvik_meldt"]
    assert meldinger, "meldingen etterlot ingen evidensrad"
    # AKTØREN NEKTES BARE PÅ AVVIKSRADEN, og skillet er meningen:
    # `sett_krav` og `registrer_regel` er ADMINISTRATIVE handlinger med
    # en ansvarlig, og en evidenskjede uten navn på dem ville vært et
    # tap. Det er MELDINGEN som må være navnløs.
    for _handling, aktor, _b in meldinger:
        assert aktor is None, (
            "evidenskjeden bærer aktøren for et anonymt avvik")
    # …OG BESKRIVELSEN STÅR IKKE I NOEN AV DEM.
    for _handling, _aktor, begrunnelse in rader:
        assert hemmelig not in begrunnelse, (
            "beskrivelsen står i evidensraden")


# =====================================================================
# OPPBEVARINGSPLIKT MOT SLETTEPLIKT.
# =====================================================================

def test_m30_grensen_er_dokumentert_for_koden():
    """FUNDAMENTET KREVDE DET: «grensesnittet mot M-30 avklares FØR
    koden» (docs/KLYNGE7-FUNDAMENT.md).

    Dokumentet skal navngi konflikten, ikke bare nevne M-30. Porten
    krever de setningene avklaringen hviler på — og at 099s faktiske
    form (M-30 sletter ingenting) står der, siden det er punktet der
    klyngefundamentet tok feil.
    """
    assert GRENSEDOK.exists(), "M-30-avklaringen mangler"
    tekst = GRENSEDOK.read_text(encoding="utf-8")
    for krav in ("art. 17", "retensjonslager", "099",
                 "personvernsak", "oppbevaring_hjemmel"):
        assert krav in tekst, f"avklaringen nevner ikke {krav}"


def test_avvik_uten_oppbevaringshjemmel():
    """NOT NULL, IKKE ET SVEIPEFUNN.

    Et avvik uten oppbevaringsgrunnlag skal ikke kunne OPPSTÅ. Samme
    form som M-50s `journalperson.slettefrist` (124): oppdagelsen
    kommer for sent.

    MUTASJONEN SOM DREPER DENNE: gjør `oppbevaring_til` nullbar.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    kropp = re.search(r"CREATE TABLE hmsavvik \((.*?)\n\);",
                      sql, re.S).group(1)
    linjer = [x for x in kropp.splitlines()
              if not x.lstrip().startswith("--")]
    for felt in ("oppbevaring_hjemmel", "oppbevaring_til",
                 "oppbevaring_dogn", "regelversjon"):
        rad = [x for x in linjer if re.match(rf"\s*{felt}\s", x)]
        assert rad, f"{felt} finnes ikke"
        assert "NOT NULL" in rad[0].upper(), (
            f"{felt} er nullbar — et avvik uten hjemmel kan oppstå")
    m = re.search(r"CREATE TABLE hmsmelder \((.*?)\n\);", sql, re.S)
    rad = [x for x in m.group(1).splitlines()
           if re.match(r"\s*slettefrist\s", x)]
    assert rad and "NOT NULL" in rad[0].upper()


@pg
def test_doera_nekter_uten_grenser_og_uten_gjeldende_regel():
    """TO NEKT, BEGGE FORDI DET MOTSATTE VILLE SETT RIKTIG UT."""
    t = _tenantnavn("nektdoer")
    kall = ("SELECT * FROM m53_meld_avvik(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
    args = lambda: (t, uuid.uuid4(), "personskade", "anonym",
                    "Fall fra stillas i tredje etasje", "Bygg A",
                    _dag(-1), None, None, None)
    with _to() as (rt, _mg):
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute(kall, args())
        rt.rollback()
        _krav(rt, t)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute(kall, args())
        rt.rollback()


@pg
def test_regel_over_tenantens_tak_nektes():
    """EN HJEMMEL PÅ TI ÅR I ET REGISTER MED ETT ÅRS TAK ER IKKE EN
    PLAN — DET ER EN OMGÅELSE AV PLANEN (124s tredje nekt, ordrett)."""
    t = _tenantnavn("tak")
    with _to() as (rt, _mg):
        _krav(rt, t, maks=365)
        _regel(rt, t, dogn=1825)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute(
                "SELECT * FROM m53_meld_avvik(%s,%s,%s,%s,%s,%s,%s,"
                "%s,%s,%s)",
                (t, uuid.uuid4(), "personskade", "anonym",
                 "Fall fra stillas i tredje etasje", "Bygg A",
                 _dag(-1), None, None, None))


@pg
def test_avvik_mot_avviklet_regel_nektes():
    """ARKIVET TAR IMOT DEN AVVIKLEDE VERSJONEN; BRUKEN ER STENGT.

    En avviklet regel KAN registreres — arkivet skal kunne svare på
    hvilken regel som gjaldt den gangen. Skillet går ved AVVIKET.
    """
    t = _tenantnavn("avviklet")
    with _to() as (rt, _mg):
        _krav(rt, t)
        _regel(rt, t, fra=_dag(-100), til=_dag(-1))
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute(
                "SELECT * FROM m53_meld_avvik(%s,%s,%s,%s,%s,%s,%s,"
                "%s,%s,%s)",
                (t, uuid.uuid4(), "personskade", "anonym",
                 "Fall fra stillas i tredje etasje", "Bygg A",
                 _dag(-1), None, None, None))


@pg
def test_navngitt_avvik_kan_faktisk_anonymiseres():
    """PORTEN SOM FANGET EN REELL SELVMOTSIGELSE.

    Første utgave hadde to CHECKer som motsa hverandre:
    `hmsavvik_navngitt_er_navngitt` krevde at et navngitt avvik HAR en
    aktør, og `hmsavvik_anonymisert_er_sporlost` at et anonymisert
    IKKE har en. Et navngitt avvik kunne dermed ALDRI anonymiseres.

    Det var ikke en skjønnhetsfeil: `oppbevaring_utlopt` er funnet
    ingen kan lukke, og det lukkes av ÉN handling — anonymisering. Med
    den blokkert ville funnet stått åpent for alltid på nøyaktig de
    radene som betyr mest.

    Fanget av en riggkjøring, ikke av lesing.
    """
    t = _tenantnavn("anonymiser")
    with _to() as (rt, mg):
        _krav(rt, t)
        _regel(rt, t)
        aid, _ = _avvik(rt, t, melderform="navngitt", navn="Kari",
                        rolle="operator", aktor="u-kari")
        _sett_kontekst(rt, t)
        rad = rt.execute(
            "SELECT * FROM m53_anonymiser(%s,%s,%s,%s)",
            (t, aid, "SAK-2026-119", "u-kari")).fetchone()
        rt.commit()
        assert rad[0] is True and rad[1] is True
        _sett_kontekst(mg, t)
        etter = mg.execute(
            "SELECT a.meldt_av, a.meldt_ts, a.m30_sak_ref, m.navn,"
            " m.rolle, m.anonymisert_ts IS NOT NULL"
            " FROM hmsavvik a JOIN hmsmelder m"
            "   ON m.tenant=a.tenant AND m.avvik_id=a.avvik_id"
            " WHERE a.tenant=%s AND a.avvik_id=%s",
            (t, aid)).fetchone()
    assert etter[0] is None and etter[1] is None
    assert etter[2] == "SAK-2026-119"
    # NAVNET ER `None`, IKKE «(anonymisert)». 124s CodeRabbit-funn:
    # en plassholderstreng gir en rad som SER anonymisert ut uten å
    # være det, og som et navnesøk fortsatt treffer.
    assert etter[3] is None and etter[4] is None
    assert etter[5] is True


@pg
def test_tidlig_anonymisering_krever_en_m30_sak():
    """`sletting_uten_m30_avklaring`, MÅLT.

    Arbeidstilsynet krever at avviket bevares. En tidlig sletting uten
    hjemmel er ikke en ryddig sletting — den er et bortkommet bevis.
    """
    t = _tenantnavn("tidlig")
    with _to() as (rt, _mg):
        _krav(rt, t)
        _regel(rt, t)
        aid, _ = _avvik(rt, t)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT * FROM m53_anonymiser(%s,%s,%s,%s)",
                       (t, aid, None, "u-kari"))


# =====================================================================
# HISTORIKK, RADVAKTER OG TENANTISOLASJON.
# =====================================================================

@pg
def test_avvik_overskrevet():
    """APPEND-ONLY PÅ INNHOLDET. M-42s dom (110), gjentatt i 112–126.

    Særlig `oppbevaring_til`: kunne den flyttes, ville «oppbevart etter
    egen frist» vært et funn man kunne fjerne ved å utsette fristen —
    et gjerde som forsvant når man dyttet på det.

    Skrives som MODULROLLEN, ikke som migrator: det er DENS grenser
    som gjelder i drift (123s form).
    """
    t = _tenantnavn("frosset")
    with _to() as (rt, mg):
        _krav(rt, t)
        _regel(rt, t)
        aid, _ = _avvik(rt, t)
        for felt, verdi in (("beskrivelse", "'noe helt annet'"),
                            ("oppbevaring_til", "current_date"),
                            ("regelversjon", "'2099-01'"),
                            ("meldt_dato", "current_date - 5")):
            _sett_kontekst(mg, t)
            mg.execute("SET LOCAL ROLE disponit_hms_eier")
            with pytest.raises(psycopg.errors.Error) as e:
                mg.execute(f"UPDATE hmsavvik SET {felt} = {verdi}"
                           " WHERE tenant=%s AND avvik_id=%s", (t, aid))
            assert isinstance(e.value, (
                psycopg.errors.InsufficientPrivilege,
                psycopg.errors.CheckViolation)), felt
            mg.rollback()


@pg
def test_tiltak_er_append_only():
    """HVA SOM FAKTISK BLE GJORT ER DET ET TILSYN ETTERPRØVER.

    Et tiltak som lot seg redigere i ettertid er ikke et tiltak — det
    er en forklaring man finner på når noen spør (124s formulering om
    formålet, som gjelder ordrett her).
    """
    t = _tenantnavn("tiltak")
    with _to() as (rt, mg):
        _krav(rt, t)
        _regel(rt, t)
        aid, _ = _avvik(rt, t)
        _sett_kontekst(rt, t)
        tid = uuid.uuid4()
        rt.execute(
            "SELECT * FROM m53_registrer_tiltak(%s,%s,%s,%s,%s,%s,%s)",
            (t, aid, tid, "Stillaset er sikret og kontrollert", False,
             I_DAG, "u-kari"))
        rt.commit()
        for setning in (
                "UPDATE hmstiltak SET beskrivelse='noe annet'",
                "DELETE FROM hmstiltak"):
            _sett_kontekst(mg, t)
            mg.execute("SET LOCAL ROLE disponit_hms_eier")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                mg.execute(setning + " WHERE tenant=%s AND tiltak_id=%s",
                           (t, tid))
            mg.rollback()


@pg
def test_behandlet_avvik_kan_ikke_aapnes_igjen():
    """STATUSEN ER HISTORIKK. Et nytt tiltak på en gammel sak er et
    NYTT avvik, ikke en omgjøring."""
    t = _tenantnavn("gjenaapne")
    with _to() as (rt, mg):
        _krav(rt, t)
        _regel(rt, t)
        aid, _ = _avvik(rt, t)
        _sett_kontekst(rt, t)
        rt.execute(
            "SELECT * FROM m53_registrer_tiltak(%s,%s,%s,%s,%s,%s,%s)",
            (t, aid, uuid.uuid4(), "Stillaset er sikret", True, I_DAG,
             "u-kari"))
        rt.commit()
        _sett_kontekst(mg, t)
        mg.execute("SET LOCAL ROLE disponit_hms_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            mg.execute(
                "UPDATE hmsavvik SET status='apen', behandlet_ts=NULL,"
                " behandlet_av=NULL WHERE tenant=%s AND avvik_id=%s",
                (t, aid))


@pg
def test_tenantlekkasje_i_avviksregister():
    """RLS ENABLE + FORCE PÅ ALLE SEKS."""
    with _mig() as c:
        rader = dict(c.execute(
            "SELECT relname, relrowsecurity AND relforcerowsecurity"
            "  FROM pg_class WHERE relname = ANY(%s)",
            (list(EGNE),)).fetchall())
        c.rollback()
    for tabell in EGNE:
        assert rader.get(tabell) is True, (
            f"{tabell} mangler RLS ENABLE+FORCE")


@pg
def test_avvik_fra_en_tenant_er_usynlig_for_en_annen():
    t1, t2 = _tenantnavn("a"), _tenantnavn("b")
    with _to() as (rt, mg):
        _krav(rt, t1)
        _regel(rt, t1)
        _avvik(rt, t1)
        _sett_kontekst(mg, t2)
        n = mg.execute("SELECT count(*) FROM hmsavvik").fetchone()[0]
    assert n == 0, "en tenant så en annens avvik"


# =====================================================================
# SVEIPEN OG FUNNENE.
# =====================================================================

def _aldre(mg, tenant, avvik_id, dogn):
    """Fabrikerer alderen med radvakten AVSLÅTT.

    `meldt_dato` er frosset for døra, og det er nettopp poenget: den
    kan ikke settes bakover av noen skrivevei modulen tilbyr. Testen
    må derfor gå utenom, og gjør det synlig i stedet for å finne en
    dør som ikke burde finnes.
    """
    mg.execute("ALTER TABLE hmsavvik DISABLE TRIGGER hmsavvik_frosset")
    _sett_kontekst(mg, tenant)
    # HENDELSESDATOEN FLYTTES MED. `meldt_dato >= hendelsesdato` er
    # en CHECK, ikke en radvakt, og den gjelder også når vakten er av:
    # et avvik kan ikke være meldt før det skjedde, uansett hvem som
    # skriver.
    mg.execute(
        "UPDATE hmsavvik SET meldt_dato=%s, hendelsesdato=%s,"
        " oppbevaring_til=%s + oppbevaring_dogn"
        " WHERE tenant=%s AND avvik_id=%s",
        (_dag(-dogn), _dag(-dogn), _dag(-dogn), tenant, avvik_id))
    mg.execute("ALTER TABLE hmsavvik ENABLE TRIGGER hmsavvik_frosset")
    mg.commit()


@pg
def test_ubehandlet_avvik_gir_funn():
    """MODULENS EGEN GRUNN TIL Å FINNES.

    Skaden er også Å LA VÆRE (M-47s dom, 123). Et HMS-mottak som tok
    imot og ikke sa fra, ville vært en postkasse.
    """
    t = _tenantnavn("ubehandlet")
    with _to() as (rt, mg):
        _krav(rt, t, tiltak=14)
        _regel(rt, t)
        aid, _ = _avvik(rt, t)
        _aldre(mg, t, aid, 40)
        with _sv() as sv:
            sv.execute("SELECT * FROM m53_sveip_hms(200)")
            sv.commit()
        _sett_kontekst(mg, t)
        rad = mg.execute(
            "SELECT funntype, over_grense, apen FROM hmsfunn"
            " WHERE tenant=%s AND funntype='avvik_ubehandlet'",
            (t,)).fetchone()
        mg.execute("DELETE FROM hmsfunn WHERE tenant=%s", (t,))
        mg.commit()
    assert rad, "et 40 døgn gammelt ubehandlet avvik ga intet funn"
    assert rad[1] == 26, "over_grense teller ikke døgn over fristen"
    assert rad[2] is True


@pg
def test_for_tidlig_anonymisert_kan_ingen_lukke():
    """FUNNET INGEN KAN LUKKE, RETNING TO.

    Som regel er dette den LOVLIGE veien: noen krevde sletting, og
    art. 17 ga dem rett. DET ER FORTSATT ET HULL. Arbeidstilsynet spør
    ikke hvorfor beviset er borte; det spør om det er der.

    En knapp som fjernet funnet ville fjernet det eneste sporet av at
    avviket noen gang fantes.
    """
    t = _tenantnavn("hull")
    with _to() as (rt, mg):
        _krav(rt, t)
        _regel(rt, t)
        aid, _ = _avvik(rt, t)
        _sett_kontekst(rt, t)
        rt.execute("SELECT * FROM m53_anonymiser(%s,%s,%s,%s)",
                   (t, aid, "SAK-2026-500", "u-kari"))
        rt.commit()
        with _sv() as sv:
            sv.execute("SELECT * FROM m53_sveip_hms(200)")
            sv.commit()
        _sett_kontekst(mg, t)
        fid, detalj = mg.execute(
            "SELECT funn_id, detalj FROM hmsfunn WHERE tenant=%s"
            " AND funntype='for_tidlig_anonymisert'", (t,)).fetchone()
        assert "SAK-2026-500" in detalj, (
            "M-30-henvisningen står ikke i funnet")
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT m53_lukk_funn(%s,%s,%s,%s)",
                       (t, fid, "jeg har sett den", "u-kari"))
        rt.rollback()
        _sett_kontekst(mg, t)
        mg.execute("DELETE FROM hmsfunn WHERE tenant=%s", (t,))
        mg.commit()


@pg
def test_et_menneskes_lukking_staar_natten_over():
    """125/126s VAKT GJELDER OGSÅ HER.

    Nummer ti kopierte sveipen fra nummer ni; det er nøyaktig det
    vakten ble skrevet for. Porten lukker som menneske, kjører DERETTER
    sveipen, og leser raden på nytt — formen porten på 116–124 manglet.
    """
    t = _tenantnavn("lukkevern")
    with _to() as (rt, mg):
        _krav(rt, t, tiltak=14)
        _regel(rt, t)
        aid, _ = _avvik(rt, t)
        _aldre(mg, t, aid, 40)
        with _sv() as sv:
            sv.execute("SELECT * FROM m53_sveip_hms(200)")
            sv.commit()
        _sett_kontekst(mg, t)
        fid = mg.execute(
            "SELECT funn_id FROM hmsfunn WHERE tenant=%s"
            " AND funntype='avvik_ubehandlet'", (t,)).fetchone()[0]
        mg.commit()
        _sett_kontekst(rt, t)
        rt.execute("SELECT m53_lukk_funn(%s,%s,%s,%s)",
                   (t, fid, "sett, tiltak kommer fredag", "u-kari"))
        rt.commit()
        # OG SÅ SVEIPER VI IGJEN. Dette er linjen som manglet.
        with _sv() as sv:
            sv.execute("SELECT * FROM m53_sveip_hms(200)")
            sv.commit()
        _sett_kontekst(mg, t)
        rad = mg.execute(
            "SELECT apen, lukket_av, lukkenotat FROM hmsfunn"
            " WHERE tenant=%s AND funn_id=%s", (t, fid)).fetchone()
        mg.execute("DELETE FROM hmsfunn WHERE tenant=%s", (t,))
        mg.commit()
    assert rad[0] is False, "sveipen gjenåpnet et menneskes lukking"
    assert rad[1] == "u-kari"
    assert rad[2] == "sett, tiltak kommer fredag"


@pg
def test_lukkedoera_nekter_en_tom_aktoer():
    """125s ANDRE LÆRDOM, INNEBYGD FRA FØDSELEN."""
    t = _tenantnavn("tomaktor")
    with _to() as (rt, _mg):
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT m53_lukk_funn(%s,%s,%s,%s)",
                       (t, uuid.uuid4(), "et notat her", None))


# =====================================================================
# API OG FLATE.
# =====================================================================

def test_api_sender_aldri_aktoeren_for_et_anonymt_avvik():
    """DEN VIKTIGSTE LINJEN I HELE MODULEN, MÅLT I KODEN.

        aktor = None if anonym else bid

    `_browserkontekst` gir bruker-id-en. For et anonymt avvik sendes
    den ALDRI videre — ikke maskert, ikke tømt, ikke sendt.

    MUTASJONEN SOM DREPER DENNE: send `bid` uansett melderform.
    """
    api = ROT / "platform" / "core" / "api" / "hms.py"
    kode = _bare_kode(api)
    blokk = re.search(
        r"def meld_avvik_endepunkt\(.*?(?=\ndef )", kode, re.S)
    assert blokk, "meldeendepunktet finnes ikke"
    kropp = blokk.group(0)
    assert re.search(r"aktor\s*=\s*None if anonym else bid", kropp), (
        "meldeendepunktet skiller ikke aktøren på melderformen")
    # …OG NAVNET NULLES FØR DET NÅR DØRA. Døra nekter uansett; her
    # sendes det ikke engang, slik at et anonymt avvik aldri får et
    # navn gjennom laget.
    assert re.search(r"if anonym:\s*\n\s*navn = None\s*\n\s*"
                     r"rolle = None", kropp), (
        "melderavnet sendes videre uavhengig av melderformen")


def test_flaten_fjerner_navnefeltet_den_skjuler_det_ikke():
    """ET FELT SOM KAN FYLLES BLIR FYLT.

    Ikke `hidden`, ikke `disabled`, ikke tømt ved innsending —
    FJERNET FRA DOM-EN. Et skjult felt sendes fortsatt med skjemaet,
    og et deaktivert kan slås på av hva som helst som rører DOM-en.

    MUTASJONEN SOM DREPER DENNE: bytt `sett(plass, …)` mot
    `navnfelt.hidden = anonym`.
    """
    kode = _bare_kode(FLATE)
    blokk = re.search(r"function tegnMelderdel\(\).*?\n  \}", kode,
                      re.S)
    assert blokk, "meldeskjemaet tegner ikke melderdelen betinget"
    kropp = blokk.group(0)
    assert "sett(plass" in kropp, (
        "melderdelen byttes ikke ut — den skjules")
    assert ".hidden" not in kropp and ".disabled" not in kropp, (
        "navnefeltet skjules eller deaktiveres i stedet for å fjernes")


def test_flaten_advarer_om_fritekst_for_feltet():
    """DEN ÆRLIGE GRENSEN FOR HVA EN DATABASE KAN LOVE.

    «Jeg sa fra til formannen på tirsdag» identifiserer melderen
    uansett hva skjemaet gjør. Advarselen skal stå FØR feltet — den
    skal leses av den som skriver, ikke av den som har skrevet ferdig.
    """
    kode = _bare_kode(FLATE)
    # INNE I MELDESKJEMAET, ikke i hele filen: `ui.hms.beskrivelse`
    # brukes også som kolonneoverskrift lenger oppe, og en port som
    # målte den første forekomsten ville målt tabellen i stedet for
    # skjemaet — og vært grønn uansett hvor advarselen sto.
    blokk = re.search(r"export function meldeskjema\(.*?\n\}", kode,
                      re.S)
    assert blokk, "meldeskjemaet finnes ikke"
    kropp = blokk.group(0)
    advarsel = kropp.find("ui.hms.fritekst_advarsel")
    felt = kropp.find('"hms-beskrivelse", "ui.hms.beskrivelse"')
    assert advarsel > 0, "advarselen om fritekst finnes ikke"
    assert felt > 0, "beskrivelsesfeltet finnes ikke i skjemaet"
    assert advarsel < felt, "advarselen står etter feltet den advarer om"


def test_flaten_skiller_aldri_skrevet_fra_slettet():
    """`melder_navn: null` BETYR TO HELT ULIKE TING.

    En flate som slo dem sammen ville fortalt en varsler at systemet
    «har slettet» noe det aldri hadde.
    """
    kode = _bare_kode(FLATE)
    fn = re.search(r"export function meldertilstand\(a\).*?\n\}",
                   kode, re.S)
    assert fn, "meldertilstand finnes ikke"
    kropp = fn.group(0)
    assert "ui.hms.melder_anonym" in kropp
    assert "ui.hms.melder_anonymisert" in kropp
    # …og tabellen bruker funksjonen, ikke feltet direkte.
    tab = re.search(r"export function avvikstabell\(.*?\n\}", kode,
                    re.S).group(0)
    assert "meldertilstand(a)" in tab, (
        "avvikstabellen leser melder_navn direkte")


def test_ui_axe_alvorlige_brudd():
    """INGEN `role=\"alert\"` PÅ EN `<li>`.

    124 lærte det: rollen overstyrer `listitem`, og axe felte hele
    lista. Den samme feilen ville stått her, i en flate med enda flere
    varsellinjer.
    """
    kode = _bare_kode(FLATE)
    for m in re.finditer(r'el\("li"[^)]*role:\s*"alert"', kode):
        pytest.fail("role=\"alert\" på en <li> bryter listerollen")


@pg
def test_sveipekontrakten_er_fire_felt():
    """FIRE FELT, som M-46, M-49, M-51, M-55, M-54, M-52, M-47 og
    M-50. Modulen har ingen rad å rydde tilsvarende M-48s forlatte
    reservasjoner, og et femte felt med verdien 0 ville vært en linje
    som lot som den målte noe."""
    with _sv() as c:
        rad = c.execute("SELECT * FROM m53_sveip_hms(1)").fetchone()
        c.rollback()
    assert len(rad) == 4
    from drift import hmssveip
    assert hmssveip.KONTRAKTFELT == 4


@pg
def test_gjenspill_av_samme_melding_er_et_stille_ja():
    """SP-2-MATERIALITET (CodeRabbit).

    Kalleren utleder `avvik_id` av sin Idempotency-Key. Uten
    gjenspillhåndteringen ville en nettverksfeil, et dobbelttrykk
    eller en retry i et mellomledd truffet primærnøkkelen og gitt en
    feilmelding til et menneske som nettopp meldte en personskade.

    ET SKJEMA SOM FEILER PÅ ANDRE FORSØK ER ET SKJEMA FOLK SLUTTER Å
    BRUKE, og en HMS-melding som ikke ble sendt er hele skaden.

    MUTASJONEN SOM DREPER DENNE: fjern `IF FOUND THEN`-grenen.
    """
    t = _tenantnavn("gjenspill")
    aid = uuid.uuid4()
    with _to() as (rt, mg):
        _krav(rt, t)
        _regel(rt, t)
        _sett_kontekst(rt, t)
        kall = ("SELECT * FROM m53_meld_avvik(%s,%s,%s,%s,%s,%s,%s,"
                "%s,%s,%s)")
        args = (t, aid, "personskade", "anonym",
                "Fall fra stillas i tredje etasje", "Bygg A",
                _dag(-1), None, None, None)
        forst = rt.execute(kall, args).fetchone()
        rt.commit()
        _sett_kontekst(rt, t)
        igjen = rt.execute(kall, args).fetchone()
        rt.commit()
        assert forst == igjen, "gjenspillet ga et annet svar"
        _sett_kontekst(mg, t)
        n = mg.execute("SELECT count(*) FROM hmsavvik WHERE tenant=%s",
                       (t,)).fetchone()[0]
        assert n == 1, "gjenspillet lagde en rad til"
        # …OG SAMME ID MED ANNET INNHOLD ER EN KONFLIKT.
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute(kall, (t, aid, "materiell", "anonym",
                              "En helt annen hendelse i verkstedet",
                              "Bygg A", _dag(-1), None, None, None))


@pg
def test_bildet_baerer_hele_flaten_i_ett_kall():
    """FLATEN TEGNER ALT I SAMME RUNDE (CodeRabbit fant at den ikke
    kunne).

    `/v1/hms` ga bare sammendraget, mens flaten leste `d.avvik`,
    `d.regelverk` og `d.funn` fra det samme svaret. Listene ville vært
    tomme for alltid — og en tom avviksliste ser nøyaktig ut som et
    register uten avvik.

    MUTASJONEN SOM DREPER DENNE: la `svar_for` returnere bare
    sammendraget igjen.
    """
    import importlib
    hms = importlib.import_module("api.hms")
    t = _tenantnavn("bilde")
    with _to() as (rt, _mg):
        _krav(rt, t)
        _regel(rt, t)
        _avvik(rt, t)
        _sett_kontekst(rt, t)
        svar = hms.svar_for(rt, t)
    for nokkel in ("sammendrag", "avvik", "regelverk", "funn"):
        assert nokkel in svar, f"bildet mangler {nokkel}"
    assert len(svar["avvik"]) == 1
    assert len(svar["regelverk"]) == 1
    # …OG SAMMENDRAGET BÆRER ALLE FIRE GRENSENE (123s lærdom).
    for g in ("oppbevaring_maks_dogn", "oppbevaringsvarsel_dogn",
              "tiltaksfrist_dogn", "regelvarsel_dogn"):
        assert svar["sammendrag"][g] is not None, g


@pg
def test_et_utdatert_varsel_lukkes_av_sveipen():
    """EN FUNNLISTE SOM VOKSER ER EN FUNNLISTE INGEN LESER.

    `oppbevaring_naermer_seg` skal lukkes i det øyeblikket fristen
    PASSERER, for da er det `oppbevaring_utlopt` som gjelder. Første
    utgave lukket bare to av funntypene, fordi lukkingen gjentok
    predikatene i et eget CTE i stedet for å lese det samme
    kandidatsettet (CodeRabbit).

    MUTASJONEN SOM DREPER DENNE: ta `oppbevaring_naermer_seg` ut av
    `funntype IN (...)` i lukkegrenen igjen.
    """
    t = _tenantnavn("utdatert")
    with _to() as (rt, mg):
        _krav(rt, t, varsel=60)
        # En regel som gir 30 døgns oppbevaring: fristen ligger da
        # INNENFOR varselvinduet med én gang.
        _regel(rt, t, dogn=30)
        aid, _ = _avvik(rt, t)
        with _sv() as sv:
            sv.execute("SELECT * FROM m53_sveip_hms(200)")
            sv.commit()
        _sett_kontekst(mg, t)
        typer = {r[0] for r in mg.execute(
            "SELECT funntype FROM hmsfunn WHERE tenant=%s AND apen",
            (t,)).fetchall()}
        assert "oppbevaring_naermer_seg" in typer, typer
        # …OG SÅ PASSERER FRISTEN.
        _aldre(mg, t, aid, 40)
        with _sv() as sv:
            sv.execute("SELECT * FROM m53_sveip_hms(200)")
            sv.commit()
        _sett_kontekst(mg, t)
        rader = dict(mg.execute(
            "SELECT funntype, apen FROM hmsfunn WHERE tenant=%s",
            (t,)).fetchall())
        mg.execute("DELETE FROM hmsfunn WHERE tenant=%s", (t,))
        mg.commit()
    assert rader.get("oppbevaring_naermer_seg") is False, (
        "det utdaterte varselet ble stående åpent ved siden av det"
        " passerte")
    assert rader.get("oppbevaring_utlopt") is True


@pg
def test_gjenspill_av_et_tiltak_med_annet_innhold_nektes():
    """`ON CONFLICT DO NOTHING` SVARTE OK PÅ EN SKRIVING SOM ALDRI
    SKJEDDE (CodeRabbit).

    Den som registrerte «stillaset er sikret» ville fått bekreftelse
    på et tiltak som aldri ble skrevet — og `hmstiltak` er
    append-only, så det finnes ingen vei til å rette det etterpå.
    """
    t = _tenantnavn("tiltakgjen")
    tid = uuid.uuid4()
    with _to() as (rt, mg):
        _krav(rt, t)
        _regel(rt, t)
        aid, _ = _avvik(rt, t)
        _sett_kontekst(rt, t)
        kall = ("SELECT * FROM m53_registrer_tiltak(%s,%s,%s,%s,%s,"
                "%s,%s)")
        rt.execute(kall, (t, aid, tid, "Stillaset er sikret og maalt",
                          False, I_DAG, "u-kari"))
        rt.commit()
        # IDENTISK GJENSPILL: stille ja.
        _sett_kontekst(rt, t)
        rt.execute(kall, (t, aid, tid, "Stillaset er sikret og maalt",
                          False, I_DAG, "u-kari"))
        rt.commit()
        _sett_kontekst(mg, t)
        n = mg.execute("SELECT count(*) FROM hmstiltak WHERE tenant=%s",
                       (t,)).fetchone()[0]
        assert n == 1
        # ANNET INNHOLD: konflikt, ikke et stille OK.
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute(kall, (t, aid, tid, "Noe helt annet ble gjort",
                              True, I_DAG, "u-kari"))


@pg
def test_anonymisering_svarer_sant_ogsaa_andre_gang():
    """SVARET GJELDER TILSTANDEN, IKKE HANDLINGEN (CodeRabbit).

    Første utgave svarte `anonymisert: false` på et andre kall mot en
    alt anonymisert rad — det motsatte av sannheten, til den som
    nettopp ba om det. `false` ville betydd «ny handling utført», og
    det er et ANNET spørsmål enn det kalleren stiller.

    MUTASJONEN SOM DREPER DENNE: sett `false` tilbake i den
    idempotente grenen.
    """
    t = _tenantnavn("idem-anon")
    with _to() as (rt, _mg):
        _krav(rt, t)
        _regel(rt, t)
        aid, _ = _avvik(rt, t)
        _sett_kontekst(rt, t)
        rt.execute("SELECT * FROM m53_anonymiser(%s,%s,%s,%s)",
                   (t, aid, "SAK-1", "u-kari"))
        rt.commit()
        _sett_kontekst(rt, t)
        rad = rt.execute("SELECT * FROM m53_anonymiser(%s,%s,%s,%s)",
                         (t, aid, "SAK-1", "u-kari")).fetchone()
    assert rad[0] is True, (
        "andre kall svarte at raden IKKE er anonymisert")


def test_gjenspillkappløpet_har_en_gren_og_deler_sammenligningen():
    """`FOR UPDATE` PÅ EN RAD SOM IKKE FINNES TAR INGEN LÅS.

    To samtidige kall med samme Idempotency-Key ser derfor begge
    `NOT FOUND`, én INSERT vinner primærnøkkelen, og den andre ville
    fått en `unique_violation` — nøyaktig den klientfeilen
    gjenspillgrenen finnes for å hindre, i det ene tilfellet der den
    er mest sannsynlig: dobbelttrykket (CodeRabbit).

    PORTEN ER STRUKTURELL MED VILJE. Et ekte kappløp krever to tråder
    som blokkerer på hverandre, og en port som VANLIGVIS treffer
    vinduet er en port som av og til er rød uten grunn — og en flaky
    port er verre enn ingen, fordi den lærer folk å kjøre om igjen.

    Den måler derfor de to tingene som kan RÅTNE: at grenen finnes, og
    at begge veier bruker SAMME materialitetssjekk. To kopier ville
    før eller siden gått fra hverandre, og da ville den ene veien
    godtatt noe den andre nektet.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    doer = re.search(r"CREATE FUNCTION m53_meld_avvik\(.*?\nEND \$\$;",
                     sql, re.S)
    assert doer, "mottaksdøra finnes ikke"
    kropp = doer.group(0)
    assert "EXCEPTION WHEN unique_violation THEN" in kropp, (
        "kappløpsgrenen mangler — taperen får en klientfeil")
    assert kropp.count("m53_krev_samme_avvik(") == 2, (
        "de to gjenspillveiene deler ikke materialitetssjekken")
    # …OG SJEKKEN SELV FINNES BARE ÉN GANG.
    assert sql.count("CREATE FUNCTION m53_krev_samme_avvik(") == 1


def test_lesescopet_er_security_read_og_ikke_okonomi():
    """HELSEOPPLYSNINGER ETTER GDPR ART. 9 BAK FINANSLESERENS SCOPE.

    Jeg skrev av M-50-raden uten å se at datasettet er et helt annet
    (CodeRabbit). `GET /v1/hms/avvik` returnerer `beskrivelse`,
    `helseopplysninger` og `melder_navn`; `okonomi:read` er
    finansleserens scope fra 101.

    SKRIVEVEIENE BEHOLDER `bestilling:opprett`, og porten krever det:
    skulle meldingen krevd `security:read`, måtte en anonym melding
    gått gjennom den HMS-ansvarlige — altså ikke vært anonym.

    MUTASJONEN SOM DREPER DENNE: sett `okonomi:read` tilbake på én GET.
    """
    import importlib
    app = importlib.import_module("api.app")
    hms = {(m, sti): sc for (m, sti), sc in app.RUTESCOPE.items()
           if sti.startswith("/v1/hms")}
    assert hms, "M-53s ruter står ikke i RUTESCOPE"
    for (metode, sti), scope in hms.items():
        if metode == "GET":
            assert scope == "security:read", f"{sti}: {scope}"
        else:
            assert scope == "bestilling:opprett", f"{sti}: {scope}"
