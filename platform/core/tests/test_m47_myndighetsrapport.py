"""M-47 myndighetsrapporteringsagent v1 (123) — FRISTEN ER PRODUKTET.

Grensen `m47-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

DENNE MODULEN ER ANNERLEDES ENN KLYNGE 6, OG PORTENE MÅ VÆRE DET.

For de fem i klynge 6 var skaden å HANDLE: å sende inn et tilbud, å
sette en kredittgrense, å avgi en tollkode. Avholdenhet var hele
svaret, og portene der måler FRAVÆR — ingen mottaker, ingen utboks,
ingen signatur.

HER ER SKADEN OGSÅ Å LA VÆRE. En frist som går uten innsending er
nøyaktig det modulen ble bygget for å hindre. En modul som legger et
utkast klart og lar fristen passere i stillhet, har forårsaket skaden
den skulle avverge — og gjort det verre enn om den ikke fantes, fordi
noen stolte på at den så etter.

  EN STILLE M-47 ER VERRE ENN INGEN M-47.

Derfor måler portene her BÅDE fraværet (`modulen_sendte_innsending`,
`modulen_signerte_utsending`) OG NÆRVÆRET: at fristen faktisk gir et
funn (`frist_uten_varsel`), og at en feilet sveip lager støy
(`sveipefeil_uten_stoy`).

DEN SKARPESTE PORTEN ER `frist_passert_uten_bevis`. En frist som har
gått uten at noen sendte inn er ikke en mening man kan være uenig i.
Å lukke det funnet for hånd ville vært å skru av det ene varselet som
sier at noe faktisk har gått galt — og forsinkelsesgebyret kommer
uansett. Det lukkes bare av at et BEVIS registreres, altså av at noen
faktisk sendte inn.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import ast
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

MYNDIGHETSSVEIP_DSN = os.environ.get("DISPONIT_TEST_MYNDIGHETSSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "123_m47_myndighetsrapport.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "myndighet.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "myndighetsrapport.py",
    ROT / "platform" / "drift" / "myndighetssveip.py",
    ROT / "platform" / "drift" / "kjor_myndighetssveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("myndighetskrav", "regelverk", "rapportplikttype",
        "rapportplikt", "rapportbevis", "myndighetsfunn")

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


def _rt():
    from db.pg import koble
    return koble(DSN)


def _sv():
    from db.pg import koble
    return koble(MYNDIGHETSSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m47-{merke}-{secrets.token_hex(4)}"


I_DAG = datetime.date.today()


def _dag(n: int) -> datetime.date:
    return I_DAG + datetime.timedelta(days=n)


def _krav(c, tenant, *, varsel=14, eskalering=3, regelvarsel=60,
          aktor="u-test", nokkel=None):
    _sett_kontekst(c, tenant)
    v = c.execute(
        "SELECT versjon FROM m47_sett_krav(%s,%s,%s,%s,%s,%s)",
        (tenant, varsel, eskalering, regelvarsel, aktor,
         nokkel or secrets.token_hex(8))).fetchone()[0]
    c.commit()
    return v


def _regelverk(c, tenant, *, myndighet="skatteetaten",
               navn="MVA-melding", versjon="2026-01",
               hjemmel="skattebetalingsloven 8-1",
               fra="2026-01-01", til=None, sha=None, aktor="u-test"):
    rid = uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute("SELECT * FROM m47_registrer_regelverk("
              "%s,%s,%s,%s,%s,%s,%s::date,%s::date,%s,NULL,%s)",
              (tenant, rid, myndighet, navn, versjon, hjemmel, fra,
               til, sha or secrets.token_hex(32), aktor))
    c.commit()
    return rid


def _plikttype(c, tenant, *, nokkel="mva_melding", navn="MVA-melding",
               frekvens="to_maanedlig", aktor="u-test"):
    tid = uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute("SELECT * FROM m47_registrer_plikttype("
              "%s,%s,%s,%s,%s,NULL,%s)",
              (tenant, tid, nokkel, navn, frekvens, aktor))
    c.commit()
    return tid


def _plikt(c, tenant, tid, rid, *, fra=None, til=None, frist=None,
           aktor="u-test"):
    pid = uuid.uuid4()
    _sett_kontekst(c, tenant)
    rad = c.execute(
        "SELECT * FROM m47_registrer_plikt("
        "%s,%s,%s,%s,%s::date,%s::date,%s::date,%s)",
        (tenant, pid, tid, rid, fra or _dag(-60), til or _dag(-31),
         frist or _dag(7), aktor)).fetchone()
    c.commit()
    return pid, rad


def _bevis(c, tenant, pid, *, dato=None, kvittering="KV-1",
           person="Ola Nordmann", aktor="u-test"):
    bid = uuid.uuid4()
    _sett_kontekst(c, tenant)
    rad = c.execute(
        "SELECT * FROM m47_registrer_bevis("
        "%s,%s,%s,%s::date,%s,%s,NULL,%s)",
        (tenant, bid, pid, dato or I_DAG, kvittering, person,
         aktor)).fetchone()
    c.commit()
    return bid, rad


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m47_sveip_myndighetsplikt(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(c, tenant, *, bare_apne=True):
    _sett_kontekst(c, tenant)
    rader = c.execute(
        "SELECT funntype, over_grense, detalj, kan_lukkes, apen"
        "  FROM m47_funnene(%s,%s)",
        (tenant, bare_apne)).fetchall()
    c.rollback()
    return rader


# --------------------------------------------------------------------
# §0: hver invariant i `m47-v1` har minst én port.
# --------------------------------------------------------------------

def test_grensen_er_registrert_og_hver_invariant_har_en_port():
    """§0-regelen, målt."""
    from manifestskjema import KRAVGRENSER
    inv = set(KRAVGRENSER["m47-v1"]["invarianter"])
    assert inv
    tekst = Path(__file__).read_text(encoding="utf-8")
    mangler = sorted(i for i in inv if i not in tekst)
    assert mangler == [], f"invarianter uten port: {mangler}"


# --------------------------------------------------------------------
# FRAVÆRENE — men de er bare halve dommen her.
# --------------------------------------------------------------------

def test_ingen_kolonne_og_ingen_dor_sender_inn():
    """`modulen_sendte_innsending` — FRAVÆRET er porten.

    En innsending til en myndighet er BINDENDE og kan ikke kalles
    tilbake. Feil tall i en pålagt rapport er ikke en feil man retter —
    det er en korrigert innsending med sin egen historikk, og i noen
    tilfeller et avvik myndigheten ser.
    """
    sql = _bare_kode(MIGRASJON, uten_strenger=True).lower()
    for forbudt in ("m47_send", "mottaker", "utboks", "outbox",
                    "innsend_dor", "avsender"):
        assert forbudt not in sql, forbudt
    api = _bare_kode(MODULFILER[0], uten_strenger=True).lower()
    for forbudt in ("def send", "m47_send", "mottaker", "outbox"):
        assert forbudt not in api, forbudt
    js = _bare_kode(FLATE, uten_strenger=True).lower()
    for forbudt in ("sendinn(", "senddeklarasjon", "mottaker"):
        assert forbudt not in js, forbudt
    # …og TILSTANDEN HOS OSS finnes, med et navn som ikke kan
    # forveksles: raden heter `innsendt_av_person`, ikke `innsendt`.
    assert "innsendt_av_person" in _bare_kode(MIGRASJON)


def test_modulen_signerer_ingenting():
    """`modulen_signerte_utsending` — signaturen hører til v2."""
    for fil in list(MODULFILER) + [MIGRASJON]:
        kode = _bare_kode(fil, uten_strenger=True).lower()
        for forbudt in ("signatur", "signer(", "attester",
                        "attestasjon"):
            assert forbudt not in kode, f"{fil.name}: {forbudt}"


def test_sveipen_importerer_ingenting_som_kan_snakke_ut():
    """Fristene er myndighetens. En modul som hentet dem selv ville
    tatt ansvaret for at NØYAKTIG de er de gjeldende."""
    kilde = MODULFILER[1].read_text(encoding="utf-8")
    for forbudt in ("httpx", "requests", "urllib", "socket",
                    "aiohttp"):
        assert not re.search(rf"^\s*(import|from)\s+{forbudt}\b",
                             kilde, re.M), forbudt


# --------------------------------------------------------------------
# …OG NÆRVÆRET. Det er her M-47 skiller seg fra klynge 6.
# --------------------------------------------------------------------

def test_sveipen_lager_stoy_naar_den_feiler():
    """`sveipefeil_uten_stoy` — EN STILLE SVEIP LAR FRISTEN GÅ.

    Det er ikke en talemåte. Plattformens egen auto-utrulling til
    staging feilet hver eneste natt fra 4. september, i fem kjøringer,
    på samme manglende DSN. Den returnerte feilkode. Ingen så det.
    Serveren sto med kode fra flere moduler tilbake mens arbeidet gikk
    videre, og det ble oppdaget først da eier spurte om noe helt annet.

    MUTASJONEN SOM DREPER DENNE: la kjøreren returnere 0 ved feil.
    """
    from drift import myndighetssveip
    assert myndighetssveip.ALARM_ETTER_FEIL == 2
    kilde = MODULFILER[2].read_text(encoding="utf-8")
    # HVER VEI UT AV KJØREREN SOM ER EN FEIL, ØKER TELLEREN.
    for vakt in ("hemmeligheter_kunne_ikke_lastes",
                 "DISPONIT_MYNDIGHETSSVEIP_URL mangler",
                 "tilkobling_feilet"):
        assert vakt in kilde, vakt
    assert kilde.count("_les_feiltelling() + 1") >= 1
    assert kilde.count("tidligere + 1") >= 1
    assert '"alarm"' in kilde
    # …og en negativ eller uekte teller fra fila er en FEIL, ikke en
    # verdi: `int(True)` er 1, og en negativ teller ville slått alarmen
    # av permanent.
    assert "isinstance(raa, bool)" in kilde
    assert "negativ feiltelling" in kilde


def test_hoppet_over_er_verken_seier_eller_feil():
    """En kjøring som fant arbeidernøkkelen opptatt har ikke sveipet
    noe — og skal IKKE nullstille en alt opptelt feil."""
    from drift import myndighetssveip
    assert myndighetssveip.ARBEIDERNOKKEL == 471_930_662
    assert myndighetssveip.KONTRAKTFELT == 4
    kilde = _bare_kode(MODULFILER[1])
    assert "res.hoppet_over = True" in kilde
    kjorer = _bare_kode(MODULFILER[2])
    assert "hoppet_over" in kjorer


@pg
def test_plikt_uten_varselfrist_nektes(miljo):
    """`frist_uten_varsel` — PORTEN PÅ DEN ANDRE HALVDELEN.

    Uten tenantens varselfrist finnes det ingen frist å varsle på.
    Plikten ville ligget i registeret og SETT overvåket ut mens
    ingenting så etter den — og en frist ingen har sett er hele skaden.

    MUTASJONEN SOM DREPER DENNE: la døra sette en standardfrist selv.
    """
    tenant = _tenantnavn("utenkrav")
    with _rt() as c:
        rid = _regelverk(c, tenant)
        tid = _plikttype(c, tenant)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT * FROM m47_registrer_plikt("
                      "%s,%s,%s,%s,%s::date,%s::date,%s::date,%s)",
                      (tenant, uuid.uuid4(), tid, rid, _dag(-60),
                       _dag(-31), _dag(7), "u-test"))
        assert "varselfrist" in str(e.value)
        assert "hele skaden" in str(e.value)
        c.rollback()


@pg
def test_varselfristen_er_tenantens(miljo):
    """`varselfrist_hardkodet`.

    En bedrift med regnskapsfører og fjorten dagers internfrist trenger
    et annet varsel enn en som gjør det selv kvelden før. En konstant
    ville vært en fullmakt modulen ga seg selv over kundens
    forsinkelsesgebyr.
    """
    sql = _bare_kode(MIGRASJON)
    assert "varselfrist_dogn INT NOT NULL DEFAULT 14" in sql
    # PARENTESEN ER EN DEL AV NAVNET her: `m47_registrer_plikttype`
    # står FØR `m47_registrer_plikt` i fila, og et prefiksoppslag ville
    # gitt feil dør — porten hadde da målt noe helt annet enn den sier.
    dor = sql[sql.index("CREATE FUNCTION m47_registrer_plikt(\n"):
              sql.index("REVOKE ALL ON FUNCTION m47_registrer_plikt(\n")]
    assert "v_kravversjon IS NULL THEN" in dor
    assert "RAISE EXCEPTION" in dor
    assert "coalesce(v_kravversjon" not in dor
    for fil in MODULFILER:
        kode = _bare_kode(fil, uten_strenger=True)
        assert not re.search(r"varselfrist\w*\s*=\s*\d", kode), fil.name
    js = _bare_kode(FLATE, uten_strenger=True)
    assert not re.search(r"varselfrist\w*\s*[:=]\s*\d", js)
    # FLATEN LESER FRISTEN FRA SVARET, og lar feltet stå tomt uten.
    assert "krav ? String(krav.varselfrist_dogn)" in FLATE.read_text(
        encoding="utf-8")


@pg
def test_plikt_mot_avviklet_regelverk_nektes(miljo):
    """`plikt_mot_utlopt_regel`, halvdel én: DØRA NEKTER.

    Regelverket kan REGISTRERES avviklet — arkivet skal kunne svare på
    hva regelen sa den gangen (121s lærdom, som var min egen feil der).
    Men en NY plikt mot det ville hvilt på en hjemmel som ikke gjelder.
    """
    tenant = _tenantnavn("avviklet")
    with _rt() as c:
        _krav(c, tenant)
        tid = _plikttype(c, tenant)
        # ET ALT AVVIKLET REGELVERK KAN REGISTRERES.
        gammelt = _regelverk(c, tenant, navn="Gammelt skjema",
                             versjon="2019", fra="2019-01-01",
                             til="2020-12-31")
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT * FROM m47_registrer_plikt("
                      "%s,%s,%s,%s,%s::date,%s::date,%s::date,%s)",
                      (tenant, uuid.uuid4(), tid, gammelt, _dag(-60),
                       _dag(-31), _dag(7), "u-test"))
        assert "gjelder ikke i dag" in str(e.value)
        assert "Arkivet tar imot det" in str(e.value)
        c.rollback()


@pg
def test_plikten_baerer_hjemmelen_og_regelversjonen(miljo):
    """`plikt_uten_hjemmel` og `plikt_uten_regelversjon`.

    BÅDE fremmednøkkel OG snapshot: nøkkelen binder til raden,
    snapshotet binder til TEKSTEN — og det er snapshotet som svarer
    «hva sto det da» år senere uten et oppslag.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    t = sql[sql.index("CREATE TABLE rapportplikt ("):
            sql.index("CREATE INDEX rapportplikt_frist_idx")]
    assert "regelverk_id UUID NOT NULL" in t
    assert "rapportplikt_regelverk_fk" in t
    for kolonne in ("myndighet_ved_registrering",
                    "regelnavn_ved_registrering",
                    "regelversjon_ved_registrering",
                    "hjemmel_ved_registrering"):
        assert f"{kolonne} TEXT NOT NULL" in t, kolonne
    # HJEMMELEN KAN IKKE VÆRE TOM. En plikt uten hjemmel er en påstand
    # om at noen må gjøre noe, uten å si hvem som har bestemt det.
    assert "CHECK (hjemmel_ved_registrering ~ '[^[:space:]]')" in t

    tenant = _tenantnavn("hjemmel")
    with _rt() as c:
        _krav(c, tenant)
        rid = _regelverk(c, tenant, hjemmel="skattebetalingsloven 8-1")
        tid = _plikttype(c, tenant)
        _pid, rad = _plikt(c, tenant, tid, rid)
        # Svaret bærer hjemmelen, versjonen og døgnene til fristen.
        assert rad[3] == "skatteetaten"
        assert rad[5] == "2026-01"
        assert rad[6] == "skattebetalingsloven 8-1"
        assert rad[2] == 7


@pg
def test_plikten_beviset_og_regelverksidentiteten_er_frosset(miljo):
    """`plikt_overskrevet`.

    En plikt som kunne endres i ettertid ville gjort «hva var vi
    pålagt, med hvilken frist, etter hvilken regel» til et spørsmål
    uten svar den dagen noen spør.

    REGELVERKETS IDENTITET er frosset av en KOLONNEGRANT (121s dom) —
    bare `gyldig_til` kan settes, fordi en myndighet som kunngjør at et
    skjema avvikles er nettopp den endringen modulen skal følge med på.
    """
    tenant = _tenantnavn("frosset")
    with _rt() as c:
        _krav(c, tenant)
        rid = _regelverk(c, tenant)
        tid = _plikttype(c, tenant)
        pid, _rad = _plikt(c, tenant, tid, rid)
        _bevis(c, tenant, pid)
        c.rollback()

    from db.pg import koble
    # HISTORIKKTABELLENE FÅR IKKE UPDATE I DET HELE TATT. Det er ikke
    # en radvakt som kan gjøre en feil — det er en rettighet som ikke
    # finnes.
    for tabell, kolonne, ny in (
            ("rapportplikt", "frist", "current_date"),
            ("rapportbevis", "kvittering_ref", "'KV-9'"),
            ("rapportplikttype", "navn", "'Noe annet'")):
        with koble(MIGRATOR_DSN) as m:
            _sett_kontekst(m, tenant)
            m.execute("SET LOCAL ROLE disponit_myndighet_eier")
            with pytest.raises(
                    psycopg.errors.InsufficientPrivilege) as e:
                m.execute(f"UPDATE {tabell} SET {kolonne} = {ny}")
            assert "permission denied" in str(e.value).lower()
            m.rollback()

    # REGELVERKETS IDENTITET: kolonnegrant OG radvakt.
    for kolonne, ny in (("versjon", "'2099'"), ("navn", "'Annet'"),
                        ("hjemmel", "'noe annet'"),
                        ("gyldig_fra", "'2020-01-01'")):
        with koble(MIGRATOR_DSN) as m:
            _sett_kontekst(m, tenant)
            m.execute("SET LOCAL ROLE disponit_myndighet_eier")
            with pytest.raises(
                    psycopg.errors.InsufficientPrivilege):
                m.execute(f"UPDATE regelverk SET {kolonne} = {ny}")
            m.rollback()

    # …MEN AVVIKLINGSDATOEN KAN SETTES.
    with _rt() as c:
        _sett_kontekst(c, tenant)
        c.execute("SELECT * FROM m47_sett_gyldig_til(%s,%s,%s::date,%s)",
                  (tenant, rid, _dag(400), "u-test"))
        c.commit()
        _sett_kontekst(c, tenant)
        rad = c.execute(
            "SELECT versjon, gyldig_til FROM m47_regelverkene(%s,500)",
            (tenant,)).fetchone()
        assert rad[0] == "2026-01", "identiteten flyttet seg"
        assert rad[1] == _dag(400)
        c.rollback()

    # …OG SLETTING ER ALDRI LOVLIG.
    for tabell in EGNE:
        with koble(MIGRATOR_DSN) as m:
            _sett_kontekst(m, tenant)
            m.execute("SET LOCAL ROLE disponit_myndighet_eier")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                m.execute(f"DELETE FROM {tabell}")
            m.rollback()


@pg
def test_bevis_i_framtiden_nektes(miljo):
    """Et bevis datert i morgen er ikke et bevis — det er en plan, og
    en plan lukker ikke et fristfunn."""
    tenant = _tenantnavn("framtid")
    with _rt() as c:
        _krav(c, tenant)
        rid = _regelverk(c, tenant)
        tid = _plikttype(c, tenant)
        pid, _ = _plikt(c, tenant, tid, rid)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT * FROM m47_registrer_bevis("
                      "%s,%s,%s,%s::date,%s,%s,NULL,%s)",
                      (tenant, uuid.uuid4(), pid, _dag(1), "KV",
                       "Ola", "u-test"))
        assert "framtiden" in str(e.value)
        assert "en plan" in str(e.value)
        c.rollback()


@pg
def test_forsinkelsen_staar_paa_beviset(miljo):
    """Et bevis registrert etter fristen er FORTSATT et bevis — men at
    det kom for sent er en opplysning noen skal kunne finne igjen."""
    tenant = _tenantnavn("forsinket")
    with _rt() as c:
        _krav(c, tenant)
        rid = _regelverk(c, tenant)
        tid = _plikttype(c, tenant)
        pid, _ = _plikt(c, tenant, tid, rid, frist=_dag(-10))
        _bid, rad = _bevis(c, tenant, pid, dato=_dag(-2))
        assert rad[4] == 8, "forsinkelsen ble ikke regnet"
        # …OG ÉN PLIKT HAR ETT BEVIS. En korrigert innsending er en NY
        # plikt med sin egen frist, ikke et nytt bevis på den gamle.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute("SELECT * FROM m47_registrer_bevis("
                      "%s,%s,%s,%s::date,%s,%s,NULL,%s)",
                      (tenant, uuid.uuid4(), pid, I_DAG, "KV-2",
                       "Kari", "u-test"))
        c.rollback()


@pg
def test_tenantisolasjon(miljo):
    """`tenantlekkasje_i_pliktregister`."""
    a, b = _tenantnavn("iso-a"), _tenantnavn("iso-b")
    with _rt() as c:
        for tn in (a, b):
            _krav(c, tn)
            _regelverk(c, tn, navn=f"Skjema-{tn[-4:]}")
        _sett_kontekst(c, a)
        navn = [r[2] for r in c.execute(
            "SELECT * FROM m47_regelverkene(%s,500)", (a,)).fetchall()]
        assert len(navn) == 1 and navn[0].endswith(a[-4:])
        with pytest.raises(psycopg.Error):
            c.execute("SELECT * FROM m47_regelverkene(%s,500)", (b,))
        c.rollback()
    sql = _bare_kode(MIGRASJON)
    for tabell in EGNE:
        assert f"'{tabell}'" in sql, tabell
    assert "FORCE ROW LEVEL" in sql


@pg
def test_kjoretidsrollen_har_ingen_tabellrettigheter(miljo):
    """SP-7."""
    with _rt() as c:
        _sett_kontekst(c, TENANT)
        for tabell in EGNE:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(f"SELECT 1 FROM {tabell} LIMIT 1")
            c.rollback()
            _sett_kontekst(c, TENANT)
        c.rollback()


@pg
def test_sveipen_er_ikke_kjoretidsrollens(miljo):
    with _rt() as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM m47_sveip_myndighetsplikt(10)")
        c.rollback()


def test_ui_axe_dekning():
    """`ui_axe_alvorlige_brudd` — flaten er registrert der axe kjører."""
    app_js = (ROT / "platform" / "core" / "ui" / "static" / "js"
              / "app.js").read_text(encoding="utf-8")
    assert "myndighet: visMyndighet," in app_js
    sitekart = (ROT / "platform" / "core" / "ui" / "static" / "js"
                / "sitekart.js").read_text(encoding="utf-8")
    assert ('{ nokkel: "myndighet", scope: "okonomi:read",'
            ' modulflate: 47 }') in sitekart


# --------------------------------------------------------------------
# SVEIPEN — MODULENS PRODUKT, IKKE ET ANDRE GJERDE.
# --------------------------------------------------------------------

@pg
def test_fristen_gir_et_funn_for_den_gaar(miljo):
    """`frist_uten_varsel` — NÆRVÆRET, ikke fraværet.

    En plikt som ligger i registeret uten at noen ser på den er ikke
    overvåket; den er arkivert. Det er sveipen som gjør den til en
    frist noen VET om.

    MUTASJONEN SOM DREPER DENNE: fjern `frist_naermer_seg`-grenen.
    """
    tenant = _tenantnavn("varsel")
    with _rt() as c:
        _krav(c, tenant, varsel=14)
        rid = _regelverk(c, tenant)
        tid = _plikttype(c, tenant)
        # INNENFOR varselvinduet.
        _plikt(c, tenant, tid, rid, frist=_dag(7))
        # UTENFOR — den skal IKKE gi funn ennå.
        _plikt(c, tenant, tid, rid, fra=_dag(-200), til=_dag(-170),
               frist=_dag(60))
        c.commit()

    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        typer = [r[0] for r in _funn(c, tenant)]
    assert typer.count("frist_naermer_seg") == 1, typer
    assert "frist_passert_uten_bevis" not in typer


@pg
def test_passert_frist_uten_bevis_er_funnet_ingen_kan_lukke(miljo):
    """MODULENS SKARPESTE FUNN.

    En frist som HAR gått uten at noen sendte inn er ikke en mening man
    kan være uenig i. Å lukke det for hånd ville vært å skru av det ene
    varselet som sier at noe faktisk har gått galt — og
    forsinkelsesgebyret kommer uansett.

    DET LUKKES AV EN HANDLING: at et bevis registreres, altså at noen
    faktisk sendte inn.
    """
    tenant = _tenantnavn("passert")
    with _rt() as c:
        _krav(c, tenant)
        rid = _regelverk(c, tenant)
        tid = _plikttype(c, tenant)
        pid, _ = _plikt(c, tenant, tid, rid, frist=_dag(-10))
        c.commit()

    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        rader = {r[0]: r for r in _funn(c, tenant)}
    rad = rader["frist_passert_uten_bevis"]
    assert rad[1] == 10, "døgnene siden fristen manglet"
    assert rad[3] is False, "funnet ble meldt som lukkbart"
    assert rad[4] is True

    # IDEMPOTENT: en ny sveip gir samme ene funn, ikke to.
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        assert len([r for r in _funn(c, tenant)
                    if r[0] == "frist_passert_uten_bevis"]) == 1

    # …OG ET MENNESKE FÅR IKKE LUKKE DET.
    with _rt() as c:
        _sett_kontekst(c, tenant)
        fid = c.execute(
            "SELECT funn_id FROM m47_funnene(%s,true)"
            " WHERE funntype = %s",
            (tenant, "frist_passert_uten_bevis")).fetchone()[0]
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT * FROM m47_lukk_funn(%s,%s,%s,%s)",
                      (tenant, fid, "sett paa", "u-test"))
        assert "kan ikke lukkes for hånd" in str(e.value)
        assert "en handling, ikke en mening" in str(e.value)
        c.rollback()

    # DEN LUKKES AV HANDLINGEN.
    with _rt() as c:
        _bevis(c, tenant, pid, dato=_dag(-1))
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        aapne = {r[0] for r in _funn(c, tenant)}
    assert "frist_passert_uten_bevis" not in aapne


@pg
def test_paaminnelsen_kan_lukkes_men_avviket_kan_ikke(miljo):
    """SKILLET MELLOM EN PÅMINNELSE OG ET AVVIK.

    «Jeg har sett den, jeg gjør den på fredag» er en legitim
    menneskelig beslutning om noe som ennå ikke har gått galt. Det
    samme utsagnet om en frist som ALT er gått, er ikke det.

    Regelen bor ÉTT sted (`m47_funn_er_sveipens`), og både døra og
    lesedøra leser den. En kopi i klienten ville vært en andre regel.
    """
    tenant = _tenantnavn("skillet")
    with _rt() as c:
        _krav(c, tenant, varsel=14)
        rid = _regelverk(c, tenant)
        tid = _plikttype(c, tenant)
        _plikt(c, tenant, tid, rid, frist=_dag(5))
        c.commit()
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        rader = {r[0]: r for r in _funn(c, tenant)}
        assert rader["frist_naermer_seg"][3] is True
        _sett_kontekst(c, tenant)
        fid = c.execute(
            "SELECT funn_id FROM m47_funnene(%s,true)"
            " WHERE funntype = %s",
            (tenant, "frist_naermer_seg")).fetchone()[0]
        _sett_kontekst(c, tenant)
        ut = c.execute("SELECT * FROM m47_lukk_funn(%s,%s,%s,%s)",
                       (tenant, fid, "gjor den fredag",
                        "u-test")).fetchone()
        assert ut[1] is False, "påminnelsen lot seg ikke lukke"
        c.commit()
    # …OG REGELEN ER ÉN, ikke to.
    sql = _bare_kode(MIGRASJON)
    assert sql.count("CREATE FUNCTION m47_funn_er_sveipens") == 1
    assert "m47_funn_er_sveipens(v_type)" in sql
    assert "NOT public.m47_funn_er_sveipens(f.funntype)" in sql
    # FLATEN NAVNGIR funntypene (den må, for å oversette dem), men den
    # AVGJØR ingenting: filteret leser `kan_lukkes` fra svaret. En
    # sammenligning mot funntypen her ville vært en andre regel som
    # kunne komme i utakt med basens.
    js = _bare_kode(FLATE)
    assert "f.kan_lukkes" in js
    assert not re.search(
        r"funntype\s*===\s*[\"']", js), "flaten avgjør på funntype"


@pg
def test_plikt_mot_utlopt_regelverk_er_klyngens_funn(miljo):
    """`plikt_mot_utlopt_regel`, halvdel to: TIDEN.

    Døra nekter en NY plikt mot et avviklet regelverk. Men den farlige
    plikten er den som var RIKTIG da den ble registrert, og som ligger
    og venter mens myndigheten skifter regelverk under den.

    EN FORELDET REGEL SER NØYAKTIG UT SOM EN RIKTIG REGEL.
    """
    tenant = _tenantnavn("utlopt")
    with _rt() as c:
        _krav(c, tenant)
        rid = _regelverk(c, tenant, fra="2026-01-01")
        tid = _plikttype(c, tenant)
        _plikt(c, tenant, tid, rid, frist=_dag(30))
        c.commit()

    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        assert "plikt_mot_utlopt_regelverk" not in {
            r[0] for r in _funn(c, tenant)}

    # REGELVERKET AVVIKLES — og plikten står urørt.
    with _rt() as c:
        _sett_kontekst(c, tenant)
        c.execute("SELECT * FROM m47_sett_gyldig_til("
                  "%s,%s,%s::date,%s)",
                  (tenant, rid, _dag(-1), "u-test"))
        c.commit()
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        rader = {r[0]: r for r in _funn(c, tenant)}
    assert "plikt_mot_utlopt_regelverk" in rader
    assert rader["plikt_mot_utlopt_regelverk"][3] is False
    assert "MVA-melding" in rader["plikt_mot_utlopt_regelverk"][2]

    # DEN LUKKES AV AT PLIKTEN REGISTRERES PÅ NYTT MOT ET GYLDIG SETT.
    with _rt() as c:
        nytt = _regelverk(c, tenant, versjon="2027",
                          fra="2026-01-01")
        tid2 = _plikttype(c, tenant, nokkel="mva_melding_ny",
                          navn="MVA-melding (ny)")
        _plikt(c, tenant, tid2, nytt, frist=_dag(30))
        c.commit()
    # Den GAMLE plikten står fortsatt mot det avviklede settet, så
    # funnet blir stående — og det er riktig. Den forsvinner først når
    # den plikten selv er ute av bildet, altså bevist.
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        assert "plikt_mot_utlopt_regelverk" in {
            r[0] for r in _funn(c, tenant)}


@pg
def test_sveipen_maaler_uten_aa_sende(miljo):
    """Sveipen skriver funn og ingenting annet."""
    sql = _bare_kode(MIGRASJON)
    kropp = sql[sql.index("CREATE FUNCTION m47_sveip_myndighetsplikt"):]
    kropp = kropp[:kropp.index("REVOKE ALL ON FUNCTION"
                               " m47_sveip_myndighetsplikt")]
    for tabell in EGNE:
        if tabell == "myndighetsfunn":
            continue
        assert f"INSERT INTO public.{tabell}" not in kropp, tabell
        assert f"UPDATE public.{tabell}" not in kropp, tabell
    # TENANTLISTA ER BEGGE REGISTRENE (122s lærdom, anvendt her uten å
    # måtte finnes på nytt): en tenant med plikter men ingen regelverk
    # skal ikke hoppes over.
    assert "FROM public.regelverk r" in kropp
    assert "SELECT p.tenant FROM public.rapportplikt p" in kropp
    assert "UNION" in kropp
    # TELLERNE AKKUMULERES, de settes ikke — `INTO` SETTER en variabel.
    for teller in ("v_nye", "v_oppdaterte", "v_lukket"):
        assert f"{teller} := {teller} +" in kropp, teller


@pg
def test_sveipen_ser_en_tenant_som_bare_har_plikter(miljo):
    """122s LÆRDOM, ANVENDT FØR CODERABBIT REKKER Å FINNE DEN.

    I 122 kom tenantlista fra ett register alene, og en tenant som
    hadde varer men ingen nomenklatur ble hoppet over hver natt. Her er
    begge registrene med fra første linje — og `ingen_krav` kan feste
    seg til en plikt når tenanten ikke har et regelverk å henge det på.
    """
    tenant = _tenantnavn("barepl")
    with _rt() as c:
        # Krav og regelverk FØRST, så plikten kan registreres…
        _krav(c, tenant)
        rid = _regelverk(c, tenant)
        tid = _plikttype(c, tenant)
        _plikt(c, tenant, tid, rid, frist=_dag(-5))
        c.commit()
    with _sv() as v:
        rad = _sveip(v)
    assert rad[0] >= 1
    with _rt() as c:
        typer = {r[0] for r in _funn(c, tenant)}
    assert "frist_passert_uten_bevis" in typer


@pg
def test_kravet_bumper_ikke_versjonen_paa_en_replay(miljo):
    """CodeRabbits alvorlige funn, og M-51s (119) feil gjentatt.

    Nøkkelen ble tatt imot og kastet, så en REPLAYET POST bumpet
    `versjon`. Her er det verre enn en teller som hopper: HVERT FUNN
    BÆRER `kravversjon`, og en versjon som økte uten at en terskel
    endret seg gjør funnhistorikken uleselig — «hvilken terskel gjaldt
    da dette funnet ble reist» får to svar.

    MUTASJONEN SOM DREPER DENNE: fjern nøkkelsammenligningen.
    """
    tenant = _tenantnavn("replay")
    n = secrets.token_hex(8)
    with _rt() as c:
        v1 = _krav(c, tenant, varsel=14, nokkel=n)
        assert v1 == 1
        # SAMME NØKKEL → SAMME RAD TILBAKE, urørt.
        v2 = _krav(c, tenant, varsel=14, nokkel=n)
        assert v2 == 1, "en replay bumpet versjonen"
        # …OG EN REPLAY ENDRER INGEN TERSKEL, heller ikke om kroppen
        # er en annen. En nøkkel som «virker» for to forskjellige
        # kropper ville vært en nøkkel som ikke betyr noe.
        v3 = _krav(c, tenant, varsel=30, nokkel=n)
        assert v3 == 1
        _sett_kontekst(c, tenant)
        rad = c.execute(
            "SELECT varselfrist_dogn FROM m47_bildet(%s,10)",
            (tenant,)).fetchone()[0]
        assert rad == 14, "replayen endret terskelen"
        c.rollback()
        # EN NY NØKKEL ER EN NY ENDRING.
        v4 = _krav(c, tenant, varsel=30, nokkel=secrets.token_hex(8))
        assert v4 == 2


@pg
def test_bildet_gir_alle_tre_tersklene(miljo):
    """CodeRabbits andre alvorlige funn.

    Skjemaet som setter tersklene forhåndsfyller seg fra dette svaret.
    Med bare varselfristen sto de to andre feltene TOMME, og en tenant
    som lagret skjemaet ville sendt 0 inn i felt med minimum 1.

    ET SKJEMA SOM VISER MINDRE ENN DET LAGRER ER EN FELLE.
    """
    tenant = _tenantnavn("terskler")
    with _rt() as c:
        _krav(c, tenant, varsel=21, eskalering=5, regelvarsel=90)
        _sett_kontekst(c, tenant)
        rad = c.execute(
            "SELECT varselfrist_dogn, eskaleringsfrist_dogn,"
            " regelvarsel_dogn, kravversjon FROM m47_bildet(%s,10)",
            (tenant,)).fetchone()
        assert rad[:3] == (21, 5, 90), rad
        assert rad[3] == 1
        c.rollback()
    # …OG FLATEN LESER ALLE TRE, ikke bare den ene.
    js = FLATE.read_text(encoding="utf-8")
    for f in ("varselfrist_dogn", "eskaleringsfrist_dogn",
              "regelvarsel_dogn"):
        assert f"krav.{f}" in js, f


def test_nokkelen_valideres_som_basen_gjor():
    """CodeRabbit: `str.isalpha()` er unicode-bevisst.

    «æbc» og «ßx1» passerte API-et og traff først databasens
    ASCII-CHECK — altså en 500 der brukeren skulle fått en 400 med en
    forklaring. Mønsteret står ett sted og speiler basens, tegn for
    tegn.
    """
    from api.myndighetsrapport import _NOKKEL_RE
    sql = _bare_kode(MIGRASJON)
    assert "nokkel TEXT NOT NULL CHECK (nokkel ~ '^[a-z][a-z0-9_]{2,63}$')" \
        in sql.replace("\n", " ").replace("    ", "") or \
        "'^[a-z][a-z0-9_]{2,63}$'" in sql
    for ugyldig in ("æbc", "ßx1", "Abc", "ab", "a-b", "1abc", ""):
        assert not _NOKKEL_RE.match(ugyldig), ugyldig
    for gyldig in ("abc", "mva_melding", "a" + "b" * 63):
        assert _NOKKEL_RE.match(gyldig), gyldig
