"""M-50 postjournal- og innsynsvakt v1 (124) — OFFENTLIG ER IKKE FRITT.

Grensen `m50-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

DEN NÆRLIGGENDE BEGRUNNELSEN TREFFER IKKE, og portene måler derfor noe
annet enn i klynge 6: postjournaler ER offentlige. Innvendingen «vi har
ikke lov til å se på det» gjelder ikke.

DET SOM TREFFER ER AT JOURNALENE INNEHOLDER NAVNGITTE PRIVATPERSONER,
og at ti tusen oppslag sammenstilt i et register er en PROFIL — som er
VÅR, ikke kommunens. Forskjellen er ikke gradvis: ett oppslag er
innsyn, sammenstillingen er en behandling.

DEN SKARPESTE PORTEN ER `personopplysning_uten_sletteplan`, og den
måler noe sterkere enn et sveipefunn: `journalperson.slettefrist` er
`NOT NULL`. En personopplysning uten sletteplan skal ikke kunne
OPPSTÅ.

Grunnen er at oppdagelsen kommer for sent. Et forslag uten grunnlag kan
trekkes tilbake (M-52); en personopplysning som har ligget i registeret
i et halvår uten plan HAR ligget der, og det kan ingen sveip gjøre
ugjort.

MODULENS EGET FUNN INGEN KAN LUKKE er derfor et annet:
`slettefrist_passert`. Vi oppbevarer en navngitt privatperson lenger
enn vi SELV har bestemt. Det lukkes av at raden ANONYMISERES — ikke
slettes: at vi HAR oppbevart noen skal fortsatt kunne leses, uten
navnet. Sletting ville fjernet beviset på at vi hadde den.

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

POSTJOURNALSVEIP_DSN = os.environ.get(
    "DISPONIT_TEST_POSTJOURNALSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "124_m50_postjournal.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "journal.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "postjournal.py",
    ROT / "platform" / "drift" / "postjournalsveip.py",
    ROT / "platform" / "drift" / "kjor_postjournalsveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("journalkrav", "journalkilde", "journalsak", "journalpost",
        "journalperson", "journalfunn")

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
    return koble(POSTJOURNALSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m50-{merke}-{secrets.token_hex(4)}"


#: Dagens dato LESES NÅR DEN BRUKES, ikke ved import (CodeRabbit).
#: En suite som krysser midnatt ville ellers regnet mot gårsdagen mens
#: basens `current_date` sto på i dag — og en test som råtner med
#: klokka måler ikke det den sier.
def _idag() -> datetime.date:
    return datetime.date.today()


I_DAG = _idag()


def _dag(n: int) -> datetime.date:
    return _idag() + datetime.timedelta(days=n)


def _krav(c, tenant, *, maks=365, varsel=30, kilde=60,
          aktor="u-test", nokkel=None):
    _sett_kontekst(c, tenant)
    v = c.execute(
        "SELECT versjon FROM m50_sett_krav(%s,%s,%s,%s,%s,%s)",
        (tenant, maks, varsel, kilde, aktor,
         nokkel or secrets.token_hex(8))).fetchone()[0]
    c.commit()
    return v


def _kilde(c, tenant, *, organ="Oslo kommune", orgnr="958935420",
           fmt="noark5", versjon="2026.1", fra="2026-01-01", til=None,
           sha=None, aktor="u-test"):
    kid = uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute("SELECT * FROM m50_registrer_kilde("
              "%s,%s,%s,%s,%s,%s,%s::date,%s::date,%s,NULL,%s)",
              (tenant, kid, organ, orgnr, fmt, versjon, fra, til,
               sha or secrets.token_hex(32), aktor))
    c.commit()
    return kid


def _sak(c, tenant, *, tittel="Byggesaker",
         formaal="kartlegging av byggesaker i kommunen",
         grunnlag="berettiget_interesse", aktor="u-test"):
    sid = uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute("SELECT * FROM m50_opprett_sak(%s,%s,%s,%s,%s,%s)",
              (tenant, sid, tittel, formaal, grunnlag, aktor))
    c.commit()
    return sid


def _post(c, tenant, sid, kid, *, nr=None, dato=None, tittel="Sak",
          formaal="kartlegging av byggesaker i kommunen",
          hentet_av="Ola Nordmann", hentet=None, navn=None,
          roller=None, frister=None, aktor="u-test"):
    pid = uuid.uuid4()
    _sett_kontekst(c, tenant)
    rad = c.execute(
        "SELECT * FROM m50_registrer_post("
        "%s,%s,%s,%s,%s,%s::date,%s,%s,%s,%s::date,%s,%s,"
        "%s::date[],%s)",
        (tenant, pid, sid, kid, nr or secrets.token_hex(4),
         dato or I_DAG, tittel, formaal, hentet_av, hentet or I_DAG,
         navn if navn is not None else ["Kari Nordmann"],
         roller if roller is not None else ["part"],
         frister if frister is not None else [_dag(30)],
         aktor)).fetchone()
    c.commit()
    return pid, rad


def _personene(c, tenant, pid):
    _sett_kontekst(c, tenant)
    rader = c.execute("SELECT * FROM m50_personene(%s,%s)",
                      (tenant, pid)).fetchall()
    c.rollback()
    return rader


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m50_sveip_postjournal(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(c, tenant, *, bare_apne=True):
    _sett_kontekst(c, tenant)
    rader = c.execute(
        "SELECT funntype, over_grense, detalj, kan_lukkes, apen"
        "  FROM m50_funnene(%s,%s)",
        (tenant, bare_apne)).fetchall()
    c.rollback()
    return rader


# --------------------------------------------------------------------
# §0: hver invariant i `m50-v1` har minst én port.
# --------------------------------------------------------------------

def test_grensen_er_registrert_og_hver_invariant_har_en_port():
    """§0-regelen, målt."""
    from manifestskjema import KRAVGRENSER
    inv = set(KRAVGRENSER["m50-v1"]["invarianter"])
    assert inv
    tekst = Path(__file__).read_text(encoding="utf-8")
    mangler = sorted(i for i in inv if i not in tekst)
    assert mangler == [], f"invarianter uten port: {mangler}"


# --------------------------------------------------------------------
# FRAVÆRENE.
# --------------------------------------------------------------------

def test_ingen_kolonne_og_ingen_dor_henter():
    """`modulen_hentet_eksternt` — FRAVÆRET er porten.

    Postjournaler ER offentlige, så innvendingen «vi har ikke lov til å
    se på det» treffer ikke. Det som treffer er at ti tusen oppslag
    sammenstilt i et register er en PROFIL — og profilen er vår, ikke
    kommunens.

    MUTASJONEN SOM DREPER DENNE: legg til en `hentet_automatisk`.
    """
    sql = _bare_kode(MIGRASJON, uten_strenger=True).lower()
    for forbudt in ("hentet_automatisk", "m50_hent", "hoest",
                    "m50_sok"):
        assert forbudt not in sql, forbudt
    api = _bare_kode(MODULFILER[0], uten_strenger=True).lower()
    for forbudt in ("def hent_", "m50_hent", "hoest"):
        assert forbudt not in api, forbudt
    js = _bare_kode(FLATE, uten_strenger=True).lower()
    for forbudt in ("hentjournal(", "hoest", "sokjournal"):
        assert forbudt not in js, forbudt
    # …og MENNESKET som gjorde oppslaget står med navn, i en kolonne
    # som ikke kan forveksles.
    assert "hentet_av_person" in _bare_kode(MIGRASJON)


def test_modulen_sender_ingen_henvendelse():
    """`modulen_sendte_henvendelse`."""
    for fil in list(MODULFILER) + [MIGRASJON]:
        kode = _bare_kode(fil, uten_strenger=True).lower()
        for forbudt in ("innsynsbegjaering", "m50_send", "mottaker",
                        "utboks", "outbox"):
            assert forbudt not in kode, f"{fil.name}: {forbudt}"


def test_sveipen_importerer_ingenting_som_kan_snakke_ut():
    """En sveip som hentet selv ville vært høstemaskinen."""
    kilde = MODULFILER[1].read_text(encoding="utf-8")
    for forbudt in ("httpx", "requests", "urllib", "socket",
                    "aiohttp"):
        assert not re.search(rf"^\s*(import|from)\s+{forbudt}\b",
                             kilde, re.M), forbudt


def test_sveipen_anonymiserer_ikke():
    """Å slette en personopplysning automatisk ville sett riktig ut,
    og vært galt: sletting er en handling med en ansvarlig."""
    sql = _bare_kode(MIGRASJON)
    kropp = sql[sql.index("CREATE FUNCTION m50_sveip_postjournal"):]
    kropp = kropp[:kropp.index("REVOKE ALL ON FUNCTION"
                               " m50_sveip_postjournal")]
    assert "UPDATE public.journalperson" not in kropp
    assert "m50_anonymiser" not in kropp
    for tabell in EGNE:
        if tabell == "journalfunn":
            continue
        assert f"INSERT INTO public.{tabell}" not in kropp, tabell


# --------------------------------------------------------------------
# DET FARLIGE GJORT UMULIG, IKKE OPPDAGET.
# --------------------------------------------------------------------

def test_slettefristen_er_not_null_i_skjemaet():
    """`personopplysning_uten_sletteplan` — DEN SKARPESTE PORTEN.

    Dette er ikke et sveipefunn. En personopplysning uten sletteplan
    skal ikke kunne OPPSTÅ — samme form som M-52s forslag uten
    grunnlag: det farlige gjøres UMULIG, ikke oppdaget i etterkant.

    Grunnen er at oppdagelsen kommer for sent. Et forslag kan trekkes
    tilbake; en personopplysning som har ligget i registeret i et
    halvår HAR ligget der, og det kan ingen sveip gjøre ugjort.

    MUTASJONEN SOM DREPER DENNE: gjør `slettefrist` nullbar.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    t = sql[sql.index("CREATE TABLE journalperson ("):
            sql.index("CREATE INDEX journalperson_frist_idx")]
    assert "slettefrist DATE NOT NULL" in t
    # …OG NAVNET ER NULLBART BARE FORDI ANONYMISERINGEN TØMMER DET.
    # CHECK-en binder de tre kolonnene sammen, så en rad ikke kan stå
    # uten navn OG uten anonymiseringsspor.
    assert "journalperson_anonymisering CHECK" in t
    assert "navn IS NOT NULL" in t
    assert "anonymisert_ts IS NOT NULL AND anonymisert_av IS NOT NULL" in t


def test_formaalet_kan_ikke_vaere_tomt_eller_kort():
    """`treff_uten_formaal`.

    Uten formålet er sammenstillingen en behandling ingen kan gjøre
    rede for. «Vi fant det på nett» er ikke et rettslig grunnlag — og
    et formål på tre tegn er ikke et formål.
    """
    sql = _bare_kode(MIGRASJON)
    assert "formaal TEXT NOT NULL CHECK (length(btrim(formaal)) >= 16)" \
        in sql.replace("\n", " ").replace("    ", " ") \
        or "length(btrim(formaal)) >= 16" in sql
    # BÅDE PÅ SAKEN OG PÅ POSTEN. En post kan hentes inn i en sak av en
    # annen grunn enn saken ble opprettet for.
    assert sql.count("length(btrim(formaal)) >= 16") == 2
    # …OG BEHANDLINGSGRUNNLAGET ER EN LUKKET LISTE. «Vi fant det på
    # nett» står ikke i den, og det er hele poenget.
    assert "grunnlag TEXT NOT NULL CHECK (grunnlag IN (" in sql
    from api.postjournal import GRUNNLAG, MIN_FORMAAL
    assert MIN_FORMAAL == 16
    assert "berettiget_interesse" in GRUNNLAG


def test_posten_baerer_kildeversjonen_snapshotet():
    """`treff_uten_kildeversjon`.

    BÅDE fremmednøkkel OG snapshot: nøkkelen binder til raden,
    snapshotet til TEKSTEN — og det er snapshotet som svarer «hvilket
    format leste vi dette i» når kommunen har lagt om.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    t = sql[sql.index("CREATE TABLE journalpost ("):
            sql.index("CREATE INDEX journalpost_sak_idx")]
    assert "kilde_id UUID NOT NULL" in t
    assert "journalpost_kilde_fk" in t
    for kolonne in ("organ_ved_registrering",
                    "format_ved_registrering",
                    "kildeversjon_ved_registrering"):
        assert f"{kolonne} TEXT NOT NULL" in t, kolonne


# --------------------------------------------------------------------
# DOMMENE, MÅLT MOT BASEN.
# --------------------------------------------------------------------

@pg
def test_post_uten_oppbevaringsgrenser_nektes(miljo):
    """Uten tenantens grenser finnes det ingen maksimal
    oppbevaringstid å måle slettefristen mot."""
    tenant = _tenantnavn("utenkrav")
    with _rt() as c:
        kid = _kilde(c, tenant)
        sid = _sak(c, tenant)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT * FROM m50_registrer_post("
                      "%s,%s,%s,%s,%s,%s::date,%s,%s,%s,%s::date,"
                      "%s,%s,%s::date[],%s)",
                      (tenant, uuid.uuid4(), sid, kid, "24/1", I_DAG,
                       "Sak", "kartlegging av byggesaker i kommunen",
                       "Ola", I_DAG, ["Kari"], ["part"], [_dag(30)],
                       "u-test"))
        assert "oppbevaringsgrenser" in str(e.value)
        c.rollback()


@pg
def test_post_mot_avviklet_kildeversjon_nektes(miljo):
    """`treff_uten_kildeversjon`, sett fra tiden.

    Kildeversjonen kan REGISTRERES avviklet — arkivet skal kunne svare
    på hvilket format vi leste noe i den gangen. En NY post lest i et
    format som er lagt om ville vært en registrering der feltene kan
    bety noe annet enn de gjorde.
    """
    tenant = _tenantnavn("avviklet")
    with _rt() as c:
        _krav(c, tenant)
        sid = _sak(c, tenant)
        gammel = _kilde(c, tenant, fmt="kommunal_web", versjon="2019",
                        fra="2019-01-01", til="2020-12-31")
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT * FROM m50_registrer_post("
                      "%s,%s,%s,%s,%s,%s::date,%s,%s,%s,%s::date,"
                      "%s,%s,%s::date[],%s)",
                      (tenant, uuid.uuid4(), sid, gammel, "19/9",
                       I_DAG, "Gammel",
                       "kartlegging av byggesaker i kommunen", "Ola",
                       I_DAG, ["Kari"], ["part"], [_dag(30)],
                       "u-test"))
        assert "gjelder ikke i dag" in str(e.value)
        assert "Arkivet tar imot den" in str(e.value)
        c.rollback()


@pg
def test_slettefrist_utover_tenantens_tak_nektes(miljo):
    """En frist på ti år i et register med ett års tak er ikke en plan
    — det er en omgåelse av planen."""
    tenant = _tenantnavn("tak")
    with _rt() as c:
        _krav(c, tenant, maks=365)
        kid = _kilde(c, tenant)
        sid = _sak(c, tenant)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT * FROM m50_registrer_post("
                      "%s,%s,%s,%s,%s,%s::date,%s,%s,%s,%s::date,"
                      "%s,%s,%s::date[],%s)",
                      (tenant, uuid.uuid4(), sid, kid, "24/2", I_DAG,
                       "Sak", "kartlegging av byggesaker i kommunen",
                       "Ola", I_DAG, ["Kari"], ["part"], [_dag(4000)],
                       "u-test"))
        assert "omgåelse av planen" in str(e.value)
        c.rollback()


@pg
def test_personlistene_kan_ikke_vaere_null_eller_ulike(miljo):
    """122s CODERABBIT-FUNN, ANVENDT FØR DET BLE FUNNET IGJEN.

    `cardinality(NULL)` ER NULL, så en sammenligning mot en NULL-liste
    er NULL — altså ikke SANN — og vakten slår ikke til. Da ville
    `unnest` gitt NULL RADER, og posten stått der med navn som aldri
    ble registrert med en frist.
    """
    tenant = _tenantnavn("nullliste")
    with _rt() as c:
        _krav(c, tenant)
        kid = _kilde(c, tenant)
        sid = _sak(c, tenant)
        for navn, roller, frister, ord in (
                (None, ["part"], [_dag(30)], "NULL"),
                (["Kari"], None, [_dag(30)], "NULL"),
                (["Kari"], ["part"], None, "NULL"),
                (["Kari", "Per"], ["part"], [_dag(30)], "ulik")):
            _sett_kontekst(c, tenant)
            with pytest.raises(
                    psycopg.errors.InvalidParameterValue) as e:
                c.execute("SELECT * FROM m50_registrer_post("
                          "%s,%s,%s,%s,%s,%s::date,%s,%s,%s,"
                          "%s::date,%s,%s,%s::date[],%s)",
                          (tenant, uuid.uuid4(), sid, kid,
                           secrets.token_hex(3), I_DAG, "Sak",
                           "kartlegging av byggesaker i kommunen",
                           "Ola", I_DAG, navn, roller, frister,
                           "u-test"))
            assert ord in str(e.value)
            c.rollback()


@pg
def test_posten_og_personene_skrives_i_samme_setning(miljo):
    """STERKERE ENN EN TABELLFORM.

    Hadde personene vært et eget kall etterpå, ville en journalpost med
    navngitte privatpersoner EKSISTERT i vinduet mellom de to — uten
    slettefrister. En sveip som kjørte i det vinduet ville sett en post
    uten personer, altså ingenting å rydde, mens navnene lå der.

    MUTASJONEN SOM DREPER DENNE: del `m50_registrer_post` i to dører.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    dor = sql[sql.index("CREATE FUNCTION m50_registrer_post("):
              sql.index("REVOKE ALL ON FUNCTION m50_registrer_post(")]
    assert "WITH p AS (" in dor
    assert "INSERT INTO public.journalpost" in dor
    assert "INSERT INTO public.journalperson" in dor
    assert dor.count("INSERT INTO") == 2
    # DET FINNES INGEN EGEN PERSONDØR.
    assert "CREATE FUNCTION m50_registrer_person" not in sql
    api = _bare_kode(MODULFILER[0])
    assert "m50_registrer_person" not in api

    tenant = _tenantnavn("samme")
    with _rt() as c:
        _krav(c, tenant)
        kid = _kilde(c, tenant)
        sid = _sak(c, tenant)
        pid, rad = _post(c, tenant, sid, kid,
                         navn=["Kari Nordmann", "Per Hansen"],
                         roller=["part", "avsender"],
                         frister=[_dag(30), _dag(60)])
        assert rad[1] == 2, "antallet personer ble ikke målt"
        assert rad[2] == "Oslo kommune"
        assert rad[4] == "2026.1"
        rader = _personene(c, tenant, pid)
        assert [r[1] for r in rader] == ["Kari Nordmann",
                                         "Per Hansen"]
        # HVER AV DEM HAR EN SLETTEFRIST.
        assert all(r[3] is not None for r in rader)


@pg
def test_posten_saken_og_kildeidentiteten_er_frosset(miljo):
    """`treff_overskrevet`.

    Et formål som lot seg redigere i ettertid er ikke et formål — det
    er en forklaring man finner på når noen spør.

    KILDENS IDENTITET er frosset av en KOLONNEGRANT (121s dom), fordi
    en kommune som legger om journalformatet er nettopp den endringen
    modulen skal følge med på.
    """
    tenant = _tenantnavn("frosset")
    with _rt() as c:
        _krav(c, tenant)
        kid = _kilde(c, tenant)
        sid = _sak(c, tenant)
        _post(c, tenant, sid, kid)
        c.rollback()

    from db.pg import koble
    # HISTORIKKTABELLENE FÅR IKKE UPDATE I DET HELE TATT.
    for tabell, kolonne, ny in (
            ("journalsak", "formaal", "'noe annet'"),
            ("journalpost", "formaal", "'noe annet'")):
        with koble(MIGRATOR_DSN) as m:
            _sett_kontekst(m, tenant)
            m.execute("SET LOCAL ROLE disponit_postjournal_eier")
            with pytest.raises(
                    psycopg.errors.InsufficientPrivilege) as e:
                m.execute(f"UPDATE {tabell} SET {kolonne} = {ny}")
            assert "permission denied" in str(e.value).lower()
            m.rollback()

    # KILDENS IDENTITET: kolonnegrant OG radvakt.
    for kolonne, ny in (("versjon", "'2099'"), ("organ", "'Annet'"),
                        ("format", "'annet'"),
                        ("gyldig_fra", "'2020-01-01'")):
        with koble(MIGRATOR_DSN) as m:
            _sett_kontekst(m, tenant)
            m.execute("SET LOCAL ROLE disponit_postjournal_eier")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                m.execute(f"UPDATE journalkilde SET {kolonne} = {ny}")
            m.rollback()

    # SLETTEFRISTEN ER FROSSET MED VILJE: kunne den flyttes, ville
    # «oppbevart etter egen frist» vært et funn man kunne fjerne ved å
    # utsette fristen — altså et gjerde som forsvant når man dyttet.
    with koble(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        m.execute("SET LOCAL ROLE disponit_postjournal_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            m.execute("UPDATE journalperson SET slettefrist ="
                      " current_date + 9999")
        m.rollback()

    # …MEN AVVIKLINGSDATOEN PÅ KILDEN KAN SETTES.
    with _rt() as c:
        _sett_kontekst(c, tenant)
        c.execute("SELECT * FROM m50_sett_gyldig_til("
                  "%s,%s,%s::date,%s)",
                  (tenant, kid, _dag(400), "u-test"))
        c.commit()
        _sett_kontekst(c, tenant)
        rad = c.execute(
            "SELECT versjon, gyldig_til FROM m50_kildene(%s,500)",
            (tenant,)).fetchone()
        assert rad[0] == "2026.1", "identiteten flyttet seg"
        assert rad[1] == _dag(400)
        c.rollback()

    # …OG SLETTING ER ALDRI LOVLIG. For `journalperson` er det en DOM:
    # at vi HAR oppbevart noen skal fortsatt kunne leses.
    for tabell in EGNE:
        with koble(MIGRATOR_DSN) as m:
            _sett_kontekst(m, tenant)
            m.execute("SET LOCAL ROLE disponit_postjournal_eier")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                m.execute(f"DELETE FROM {tabell}")
            m.rollback()


@pg
def test_anonymisering_gaar_bare_en_vei(miljo):
    """En rad som kunne gå tilbake fra anonymisert til navngitt ville
    betydd at vi hadde navnet et sted likevel — og da var
    anonymiseringen aldri ekte."""
    tenant = _tenantnavn("envei")
    with _rt() as c:
        _krav(c, tenant)
        kid = _kilde(c, tenant)
        sid = _sak(c, tenant)
        pid, _ = _post(c, tenant, sid, kid)
        person = _personene(c, tenant, pid)[0][0]
        _sett_kontekst(c, tenant)
        ut = c.execute("SELECT * FROM m50_anonymiser(%s,%s,%s)",
                       (tenant, person, "u-test")).fetchone()
        assert ut[1] is True and ut[2] is False
        c.commit()
        # IDEMPOTENT: et menneske som trykker to ganger skal ikke få en
        # feilmelding om noe som er i orden.
        _sett_kontekst(c, tenant)
        ut2 = c.execute("SELECT * FROM m50_anonymiser(%s,%s,%s)",
                        (tenant, person, "u-test")).fetchone()
        assert ut2[2] is True, "gjentaket ble ikke meldt som gjentak"
        c.commit()
        # NAVNET ER BORTE, SPORET STÅR.
        rad = _personene(c, tenant, pid)[0]
        assert rad[1] is None
        assert rad[5] is not None and rad[6] == "u-test"

    from db.pg import koble
    with koble(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        m.execute("SET LOCAL ROLE disponit_postjournal_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as e:
            m.execute("UPDATE journalperson SET navn = 'Kari'"
                      " WHERE person_id = %s", (person,))
        assert "kan ikke settes" in str(e.value) \
            or "gjøres om igjen" in str(e.value)
        m.rollback()


@pg
def test_navnet_staar_ikke_i_evidensen_ved_anonymisering(miljo):
    """Å skrive navnet ned i revisjonsloggen i det øyeblikket vi
    sletter det ville vært å FLYTTE opplysningen, ikke å fjerne den."""
    sql = _bare_kode(MIGRASJON)
    dor = sql[sql.index("CREATE FUNCTION m50_anonymiser"):
              sql.index("REVOKE ALL ON FUNCTION m50_anonymiser")]
    assert "m50_evidens" in dor
    # EVIDENSEN BÆRER ID-ENE, ALDRI NAVNET.
    evidens = dor[dor.index("m50_evidens"):]
    assert "navn" not in evidens
    assert "person_id" in evidens


@pg
def test_tenantisolasjon(miljo):
    """`tenantlekkasje_i_journalregister`."""
    a, b = _tenantnavn("iso-a"), _tenantnavn("iso-b")
    with _rt() as c:
        for tn in (a, b):
            _krav(c, tn)
            _kilde(c, tn, organ=f"Kommune-{tn[-4:]}")
        _sett_kontekst(c, a)
        organ = [r[1] for r in c.execute(
            "SELECT * FROM m50_kildene(%s,500)", (a,)).fetchall()]
        assert len(organ) == 1 and organ[0].endswith(a[-4:])
        with pytest.raises(psycopg.Error):
            c.execute("SELECT * FROM m50_kildene(%s,500)", (b,))
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
            c.execute("SELECT * FROM m50_sveip_postjournal(10)")
        c.rollback()


def test_ui_axe_dekning():
    """`ui_axe_alvorlige_brudd` — flaten er registrert der axe kjører."""
    app_js = (ROT / "platform" / "core" / "ui" / "static" / "js"
              / "app.js").read_text(encoding="utf-8")
    assert "journal: visJournal," in app_js
    sitekart = (ROT / "platform" / "core" / "ui" / "static" / "js"
                / "sitekart.js").read_text(encoding="utf-8")
    assert ('{ nokkel: "journal", scope: "okonomi:read",'
            ' modulflate: 50 }') in sitekart


# --------------------------------------------------------------------
# SVEIPEN OG FUNNET INGEN KAN LUKKE.
# --------------------------------------------------------------------

@pg
def test_passert_slettefrist_er_funnet_ingen_kan_lukke(miljo):
    """MODULENS EGET FUNN, OG DET TYNGSTE.

    Vi oppbevarer en navngitt privatperson lenger enn vi SELV har
    bestemt. Det er ikke en mening man kan være uenig i, og et menneske
    som klikket det bort ville skrudd av det ene varselet som sier at
    vi bryter vår egen sletteplan.

    DET LUKKES AV AT RADEN ANONYMISERES — ikke slettes. At vi HAR
    oppbevart noen skal fortsatt kunne leses, uten navnet.
    """
    tenant = _tenantnavn("passert")
    with _rt() as c:
        _krav(c, tenant)
        kid = _kilde(c, tenant)
        sid = _sak(c, tenant)
        pid, _ = _post(c, tenant, sid, kid, frister=[_dag(-10)])
        c.commit()

    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        rader = {r[0]: r for r in _funn(c, tenant)}
    rad = rader["slettefrist_passert"]
    assert rad[1] == 10, "døgnene over fristen manglet"
    assert rad[3] is False, "funnet ble meldt som lukkbart"
    assert rad[4] is True

    # IDEMPOTENT.
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        assert len([r for r in _funn(c, tenant)
                    if r[0] == "slettefrist_passert"]) == 1

    # ET MENNESKE FÅR IKKE LUKKE DET.
    with _rt() as c:
        _sett_kontekst(c, tenant)
        fid = c.execute(
            "SELECT funn_id FROM m50_funnene(%s,true)"
            " WHERE funntype = %s",
            (tenant, "slettefrist_passert")).fetchone()[0]
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT * FROM m50_lukk_funn(%s,%s,%s,%s)",
                      (tenant, fid, "sett paa", "u-test"))
        assert "kan ikke lukkes for hånd" in str(e.value)
        assert "en handling, ikke en mening" in str(e.value)
        c.rollback()

    # SVEIPENS EGEN LUKKING GJENÅPNES DERIMOT. Den betyr «tilstanden
    # var borte»; er tilstanden tilbake, er funnet tilbake. Skillet
    # står på `lukket_av`, og uten det ville et funn sveipen lukket i
    # går vært usynlig for alltid.
    #
    # DEN LUKKES AV HANDLINGEN.
    with _rt() as c:
        person = _personene(c, tenant, pid)[0][0]
        _sett_kontekst(c, tenant)
        c.execute("SELECT * FROM m50_anonymiser(%s,%s,%s)",
                  (tenant, person, "u-test"))
        c.commit()
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        aapne = {r[0] for r in _funn(c, tenant)}
    assert "slettefrist_passert" not in aapne


@pg
def test_varselet_kan_lukkes_men_bruddet_kan_ikke(miljo):
    """SKILLET MELLOM ET VARSEL OG ET BRUDD.

    «Jeg har sett den, den skal forlenges» er en legitim beslutning om
    noe som ennå ikke er brutt. Det samme utsagnet om en frist som ALT
    er gått, er ikke det.

    Regelen bor ÉTT sted (`m50_funn_er_sveipens`), og både døra og
    lesedøra leser den.
    """
    tenant = _tenantnavn("skillet")
    with _rt() as c:
        _krav(c, tenant, varsel=30)
        kid = _kilde(c, tenant)
        sid = _sak(c, tenant)
        _post(c, tenant, sid, kid, frister=[_dag(5)])
        c.commit()
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        rader = {r[0]: r for r in _funn(c, tenant)}
        assert rader["slettefrist_naermer_seg"][3] is True
        _sett_kontekst(c, tenant)
        fid = c.execute(
            "SELECT funn_id FROM m50_funnene(%s,true)"
            " WHERE funntype = %s",
            (tenant, "slettefrist_naermer_seg")).fetchone()[0]
        _sett_kontekst(c, tenant)
        ut = c.execute("SELECT * FROM m50_lukk_funn(%s,%s,%s,%s)",
                       (tenant, fid, "skal forlenges",
                        "u-test")).fetchone()
        assert ut[1] is False, "varselet lot seg ikke lukke"
        c.commit()

    # …OG DET BLIR LUKKET. DETTE ER PORTEN SOM MANGLET (CodeRabbit).
    #
    # Min forrige port målte bare at lukkingen SVARTE `apen = false`.
    # Sveipen gjenåpnet den samme raden neste natt, fordi `DO UPDATE`
    # satte `apen = true` ubetinget — og porten så det ikke, fordi den
    # aldri kjørte sveipen etterpå. Lukkeknappen var pynt, og porten
    # bekreftet pynten.
    #
    # MUTASJONEN SOM DREPER DENNE: sett `apen = true` ubetinget igjen.
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        aapne = {r[0] for r in _funn(c, tenant)}
        assert "slettefrist_naermer_seg" not in aapne, \
            "sveipen gjenaapnet et funn et menneske hadde lukket"
        # …og raden STÅR der, lukket av mennesket — ikke slettet.
        lukket = [r for r in _funn(c, tenant, bare_apne=False)
                  if r[0] == "slettefrist_naermer_seg"]
        assert len(lukket) == 1 and lukket[0][4] is False
    sql = _bare_kode(MIGRASJON)
    assert sql.count("CREATE FUNCTION m50_funn_er_sveipens") == 1
    assert "m50_funn_er_sveipens(v_type)" in sql
    assert "NOT public.m50_funn_er_sveipens(f.funntype)" in sql
    # FLATEN NAVNGIR funntypene (den må, for å oversette dem), men den
    # AVGJØR ingenting: filteret leser `kan_lukkes` fra svaret.
    js = _bare_kode(FLATE)
    assert "f.kan_lukkes" in js
    assert not re.search(r"funntype\s*===", js), \
        "flaten avgjor pa funntype i stedet for pa kan_lukkes"


@pg
def test_post_mot_utlopt_kilde_er_klyngens_funn(miljo):
    """Posten ble lest i et format som siden er lagt om. Den ser
    velformet ut, og feltene kan bety noe annet enn de gjorde.

    INGEN ETTERFØLGER-UNNTAK PÅ POSTNIVÅET (123s lærdom, funnet av min
    egen port der): med unntaket ville funnet forsvunnet i det
    øyeblikket noen registrerte en NY kildeversjon — mens den gamle
    posten fortsatt var lest i det gamle formatet.
    """
    tenant = _tenantnavn("utlopt")
    with _rt() as c:
        _krav(c, tenant)
        # RELATIV STARTDATO (CodeRabbit): en fast `2026-01-01` ville
        # ligget ETTER `_dag(-1)` om suiten kjørte før den datoen, og
        # da hadde `gyldig_til < gyldig_fra` avvist oppsettet i stedet
        # for å måle dommen. En test som råtner med kalenderen måler
        # ikke det den sier.
        kid = _kilde(c, tenant, fra=str(_dag(-200)))
        sid = _sak(c, tenant)
        _post(c, tenant, sid, kid, frister=[_dag(200)])
        c.commit()
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        assert "post_mot_utlopt_kilde" not in {
            r[0] for r in _funn(c, tenant)}

    # KILDEVERSJONEN AVVIKLES — og posten står urørt.
    with _rt() as c:
        _sett_kontekst(c, tenant)
        c.execute("SELECT * FROM m50_sett_gyldig_til("
                  "%s,%s,%s::date,%s)",
                  (tenant, kid, _dag(-1), "u-test"))
        c.commit()
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        rader = {r[0]: r for r in _funn(c, tenant)}
    assert "post_mot_utlopt_kilde" in rader
    assert rader["post_mot_utlopt_kilde"][3] is False

    # EN NY KILDEVERSJON LUKKER DET IKKE. Den gamle posten er fortsatt
    # lest i det gamle formatet.
    with _rt() as c:
        _kilde(c, tenant, versjon="2027.1", fra=str(_dag(-100)))
        c.commit()
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        assert "post_mot_utlopt_kilde" in {
            r[0] for r in _funn(c, tenant)}, \
            "funnet forsvant av at problemet ble stoerre"


@pg
def test_sveipen_ser_en_tenant_som_bare_har_poster(miljo):
    """122s LÆRDOM: tenantlista er BEGGE registrene."""
    sql = _bare_kode(MIGRASJON)
    kropp = sql[sql.index("CREATE FUNCTION m50_sveip_postjournal"):]
    kropp = kropp[:kropp.index("REVOKE ALL ON FUNCTION"
                               " m50_sveip_postjournal")]
    assert "FROM public.journalkilde k" in kropp
    assert "SELECT p.tenant FROM public.journalpost p" in kropp
    assert "UNION" in kropp
    # TELLERNE AKKUMULERES, de settes ikke — `INTO` SETTER en variabel.
    for teller in ("v_nye", "v_oppdaterte", "v_lukket"):
        assert f"{teller} := {teller} +" in kropp, teller
    # …OG TENANTLISTA MATERIALISERES FØR LØKKA.
    assert "INTO v_tenanter" in kropp


def test_organnummeret_valideres_som_basen_gjor():
    """CodeRabbit: `_tekst_valgfri(..., 9)` måler LENGDE, ikke siffer.

    «abcdefghi» passerte API-et og traff først databasens sifferkrav —
    altså en 500 der brukeren skulle fått en 400 med en forklaring.
    Samme klasse som `str.isalpha()` i M-47 (123).
    """
    from api.postjournal import _ORGNR_RE
    sql = _bare_kode(MIGRASJON)
    assert "organnummer ~ '^[0-9]{9}$'" in sql
    for ugyldig in ("abcdefghi", "12345678", "1234567890", "12345678a",
                    ""):
        assert not _ORGNR_RE.match(ugyldig), ugyldig
    assert _ORGNR_RE.match("958935420")


@pg
def test_tomt_personnavn_nektes(miljo):
    """CodeRabbit: `btrim` på et blankt navn gir en tom streng.

    Raden ville stått der som en person uten navn — altså SETT
    anonymisert ut uten å være det, med et anonymiseringsspor som
    mangler. Og da ville `journalperson_anonymisering`-CHECK-en, som
    skal binde de tre kolonnene sammen, vært omgått med en tom streng i
    stedet for NULL.
    """
    tenant = _tenantnavn("tomtnavn")
    with _rt() as c:
        _krav(c, tenant)
        kid = _kilde(c, tenant)
        sid = _sak(c, tenant)
        for navn in ("", "   "):
            _sett_kontekst(c, tenant)
            with pytest.raises(
                    psycopg.errors.InvalidParameterValue) as e:
                c.execute("SELECT * FROM m50_registrer_post("
                          "%s,%s,%s,%s,%s,%s::date,%s,%s,%s,"
                          "%s::date,%s,%s,%s::date[],%s)",
                          (tenant, uuid.uuid4(), sid, kid,
                           secrets.token_hex(3), _idag(), "Sak",
                           "kartlegging av byggesaker i kommunen",
                           "Ola", _idag(), [navn], ["part"],
                           [_dag(30)], "u-test"))
            assert "tomt" in str(e.value)
            c.rollback()
