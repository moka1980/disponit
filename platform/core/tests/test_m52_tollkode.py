"""M-52 toll- og HS-kodeagent v1 (122) — FORSLAGET, IKKE
DEKLARASJONEN.

Grensen `m52-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

DEN SKARPESTE PORTEN ER `forslag_uten_grunnlag`, og den måler noe
sterkere enn en tabellform: `m52_avgi_forslag` skriver forslaget OG
grunnene i SAMME setning. Hadde grunnene vært et eget kall etterpå,
ville et forslag uten grunnlag EKSISTERT i vinduet mellom de to — og
en flate som leste i det vinduet ville vist en kode ingen kunne
etterprøve.

Hvorfor det er den skarpeste: EN HS-KODE ER EN RETTSLIG PÅSTAND OM HVA
EN VARE ER. Feil kode gir bot, ikke bare forsinkelse — og boten treffer
KUNDEN. Et forslag uten grunnlag produserer FALSK TRYGGHET: en kode som
står der ser like ferdig ut som en noen har tenkt på. Et tomt felt
SPØR; en kode uten grunnlag SVARER.

DEN NEST SKARPESTE ER `sikkerhetsterskel_hardkodet`. Hvor sikker en
klassifisering må være før den vises som et forslag er en
RISIKOVURDERING: en importør med tusen kolliposter i uka og en med tre
har ikke samme toleranse for å ta feil. En konstant ville vært en
fullmakt modulen ga seg selv over kundens bøter.

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

TOLLKODESVEIP_DSN = os.environ.get("DISPONIT_TEST_TOLLKODESVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "122_m52_tollkode.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "tollkode.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "tollkode.py",
    ROT / "platform" / "drift" / "tollkodesveip.py",
    ROT / "platform" / "drift" / "kjor_tollkodesveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("tollkrav", "nomenklatur", "varenummer", "tollvare",
        "tollforslag", "forslagsgrunn", "tollfunn")

_STRENG = re.compile(
    r"'''.*?'''" r'|""".*?"""'
    r"|`(?:[^`\\]|\\.)*`"
    r"|'(?:[^'\\]|\\.|'')*'"
    r'|"(?:[^"\\]|\\.)*"', re.S)


def _bare_kode(fil: Path, *, uten_strenger: bool = False) -> str:
    """Filens innhold uten kommentarer OG uten docstrings (m24-formen).

    PORTEN SKAL MÅLE HANDLINGEN, IKKE BEGRUNNELSEN. Klynge 6 lærte det
    tre ganger: en port som leter i rå filtekst treffer kommentaren som
    forklarer HVORFOR et mønster er unngått.
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
    return koble(TOLLKODESVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m52-{merke}-{secrets.token_hex(4)}"


def _krav(c, tenant, *, utlop=30, avvik=7, aktor="u-test",
          nokkel=None):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m54_sett_krav(%s,%s,%s,%s,%s)",
                  (tenant, utlop, avvik, aktor,
                   nokkel or secrets.token_hex(8))).fetchone()[0]
    c.commit()
    return v


def _regelsett(c, tenant, *, standard="ehf", versjon="3.0",
               fra="2024-01-01", til=None, sid=None, aktor="u-test"):
    sid = sid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m54_registrer_regelsett("
        "%s,%s,%s,%s,%s::date,%s::date,%s,%s,%s)",
        (tenant, sid, standard, versjon, fra, til,
         secrets.token_hex(32), None, aktor))
    c.commit()
    return sid


def _regel(c, tenant, sid, *, kode=None, sti="Invoice/ID",
           krav="finnes", kodeverdi=(), sum_sti=None,
           alvorlighet="feil", gid=None, aktor="u-test"):
    gid = gid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m54_registrer_regel(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant, gid, sid, kode or f"R-{secrets.token_hex(3)}", sti,
         krav, list(kodeverdi), sum_sti, alvorlighet,
         f"regel {sti}", aktor))
    c.commit()
    return gid


def _dokument(c, tenant, *, retning="utgaaende", ref=None,
              motpart="Kunde AS", dato="2026-09-01", did=None,
              aktor="u-test"):
    did = did or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m54_registrer_dokument("
        "%s,%s,%s,%s,%s,%s::date,%s,%s,%s,%s)",
        (tenant, did, retning, ref or f"F-{secrets.token_hex(3)}",
         motpart, dato, secrets.token_hex(32), 8192,
         f"artefakt/{secrets.token_hex(3)}", aktor))
    c.commit()
    return did


def _felter(c, tenant, did, rader, *, aktor="u-test"):
    """`rader` er (sti, forekomst, verdi, ore)-firlinger."""
    _sett_kontekst(c, tenant)
    n = c.execute(
        "SELECT m54_registrer_felter(%s,%s,%s,%s,%s,%s,%s)",
        (tenant, did, [r[0] for r in rader], [r[1] for r in rader],
         [r[2] for r in rader], [r[3] for r in rader], aktor)
    ).fetchone()[0]
    c.commit()
    return n


def _valider(c, tenant, did, sid, *, vid=None, aktor="u-test"):
    vid = vid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    rad = c.execute("SELECT * FROM m54_valider_dokument(%s,%s,%s,%s,%s)",
                    (tenant, did, sid, vid, aktor)).fetchone()
    c.commit()
    return vid, rad


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m54_sveip_ehf(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(c, tenant, *, bare_apne=True):
    _sett_kontekst(c, tenant)
    rader = c.execute(
        "SELECT funntype, over_grense, detalj, apen"
        "  FROM m54_funnene(%s,%s) ORDER BY funntype",
        (tenant, bare_apne)).fetchall()
    c.rollback()
    return rader




def _rt():
    from db.pg import koble
    return koble(DSN)


def _sv():
    from db.pg import koble
    return koble(TOLLKODESVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m52-{merke}-{secrets.token_hex(4)}"


def _krav(c, tenant, *, terskel=70, utlop=60, frist=14,
          aktor="u-test", nokkel=None):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m52_sett_krav(%s,%s,%s,%s,%s,%s)",
                  (tenant, terskel, utlop, frist, aktor,
                   nokkel or secrets.token_hex(8))).fetchone()[0]
    c.commit()
    return v


def _nomenklatur(c, tenant, *, system="hs", versjon="HS 2022",
                 fra="2022-01-01", til=None, nid=None,
                 aktor="u-test"):
    nid = nid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m52_registrer_nomenklatur("
        "%s,%s,%s,%s,%s::date,%s::date,%s,%s,%s)",
        (tenant, nid, system, versjon, fra, til,
         secrets.token_hex(32), None, aktor))
    c.commit()
    return nid


def _varenummer(c, tenant, nid, *, kode="7318.15",
                tekst="Skruer og bolter av jern eller staal",
                sats=4500, vid=None, aktor="u-test"):
    vid = vid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m52_registrer_varenummer(%s,%s,%s,%s,%s,%s,%s)",
        (tenant, vid, nid, kode, tekst, sats, aktor))
    c.commit()
    return vid


def _vare(c, tenant, *, ref=None, beskrivelse="Sekskantskrue M8x40",
          materiale="staal", bruk="festemiddel", land="DE",
          vid=None, aktor="u-test"):
    vid = vid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m52_registrer_vare(%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant, vid, ref or f"ART-{secrets.token_hex(3)}",
         beskrivelse, materiale, bruk, land, aktor))
    c.commit()
    return vid


def _grunn(art="nomenklaturtekst", henvisning="7318.15",
           utdrag="Skruer og bolter av jern eller staal", dato=None):
    return (art, henvisning, utdrag, dato)


def _forslag(c, tenant, vare_id, varenummer_id, *, sikkerhet=90,
             grunner=None, fid=None, aktor="u-test"):
    fid = fid or uuid.uuid4()
    g = list(grunner or [_grunn()])
    _sett_kontekst(c, tenant)
    rad = c.execute(
        "SELECT * FROM m52_avgi_forslag("
        "%s,%s,%s,%s,%s,%s,%s,%s,%s::date[],%s)",
        (tenant, fid, vare_id, varenummer_id, sikkerhet,
         [x[0] for x in g], [x[1] for x in g], [x[2] for x in g],
         [x[3] for x in g], aktor)).fetchone()
    c.commit()
    return fid, rad


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m52_sveip_tollkode(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(c, tenant, *, bare_apne=True):
    _sett_kontekst(c, tenant)
    rader = c.execute(
        "SELECT funntype, over_grense, detalj, sikkerhet, apen"
        "  FROM m52_funnene(%s,%s) ORDER BY funntype",
        (tenant, bare_apne)).fetchall()
    c.rollback()
    return rader


# --------------------------------------------------------------------
# §0: hver invariant i `m52-v1` har minst én port.
# --------------------------------------------------------------------

def test_grensen_er_registrert_og_hver_invariant_har_en_port():
    """§0-regelen, målt."""
    from manifestskjema import KRAVGRENSER
    inv = set(KRAVGRENSER["m52-v1"]["invarianter"])
    assert inv
    tekst = Path(__file__).read_text(encoding="utf-8")
    mangler = sorted(i for i in inv if i not in tekst)
    assert mangler == [], f"invarianter uten port: {mangler}"


# --------------------------------------------------------------------
# FRAVÆRENE.
# --------------------------------------------------------------------

def test_ingen_kolonne_og_ingen_dor_deklarerer():
    """`modulen_deklarerte` — FRAVÆRET er porten.

    En deklarasjon er bindende: den kan rettes, men rettingen er en
    egen sak med sin egen historikk, og i noen tilfeller et avvik
    tollmyndigheten ser.
    """
    sql = _bare_kode(MIGRASJON, uten_strenger=True).lower()
    for forbudt in ("deklarert", "m52_deklarer", "mottaker",
                    "utboks", "outbox", "innsend"):
        assert forbudt not in sql, forbudt
    api = _bare_kode(MODULFILER[0], uten_strenger=True).lower()
    for forbudt in ("deklarer(", "m52_deklarer", "mottaker",
                    "outbox"):
        assert forbudt not in api, forbudt
    js = _bare_kode(FLATE, uten_strenger=True).lower()
    for forbudt in ("deklarer(", "senddeklarasjon", "mottaker"):
        assert forbudt not in js, forbudt
    # …og tilstanden HOS OSS finnes, med sitt eget navn.
    assert "klar_til_deklarering" in _bare_kode(MIGRASJON)


def test_modulen_signerer_ingenting():
    """`modulen_signerte_utsending` — signaturen hører til v2."""
    for fil in list(MODULFILER) + [MIGRASJON]:
        kode = _bare_kode(fil, uten_strenger=True).lower()
        for forbudt in ("signatur", "signer(", "attester",
                        "attestasjon"):
            assert forbudt not in kode, f"{fil.name}: {forbudt}"
    assert "klar_til_deklarering" in _bare_kode(MIGRASJON)


def test_forslaget_og_grunnene_skrives_i_samme_setning():
    """`forslag_uten_grunnlag` — STERKERE ENN EN TABELLFORM.

    Hadde grunnene vært et eget kall etterpå, ville et forslag uten
    grunnlag EKSISTERT i vinduet mellom de to — og en flate som leste
    i det vinduet ville vist en kode ingen kunne etterprøve.

    MUTASJONEN SOM DREPER DENNE: del `m52_avgi_forslag` i to dører.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    dor = sql[sql.index("CREATE FUNCTION m52_avgi_forslag"):
              sql.index("REVOKE ALL ON FUNCTION m52_avgi_forslag")]
    # ÉN setning: forslaget i en CTE, grunnene i den neste.
    assert "WITH f AS (" in dor
    assert "INSERT INTO public.tollforslag" in dor
    assert "INSERT INTO public.forslagsgrunn" in dor
    assert dor.count("INSERT INTO") == 2
    # …og døra nekter på tom liste, med begrunnelsen skrevet ut.
    assert "cardinality(p_grunn_arter) = 0" in dor
    assert "falsk trygghet" in dor
    # DET FINNES INGEN EGEN GRUNN-DØR.
    assert "CREATE FUNCTION m52_registrer_grunn" not in sql
    api = _bare_kode(MODULFILER[0]).lower()
    assert "m52_registrer_grunn" not in api


def test_sikkerhetsterskelen_er_ikke_hardkodet():
    """`sikkerhetsterskel_hardkodet` — TERSKELEN ER TENANTENS.

    En importør med tusen kolliposter i uka og en med tre har ikke
    samme toleranse for å ta feil. En konstant ville vært en fullmakt
    modulen ga seg selv over kundens bøter.
    """
    sql = _bare_kode(MIGRASJON)
    assert "sikkerhetsterskel INT NOT NULL DEFAULT 70" in sql
    dor = sql[sql.index("CREATE FUNCTION m52_avgi_forslag"):
              sql.index("REVOKE ALL ON FUNCTION m52_avgi_forslag")]
    assert "v_terskel IS NULL THEN" in dor
    assert "RAISE EXCEPTION" in dor
    assert "coalesce(v_terskel" not in dor
    for fil in MODULFILER:
        kode = _bare_kode(fil, uten_strenger=True)
        assert not re.search(r"terskel\w*\s*=\s*\d", kode), fil.name
    js = _bare_kode(FLATE, uten_strenger=True)
    assert not re.search(r"terskel\w*\s*[:=]\s*\d", js)
    # FLATEN LESER TERSKELEN FRA SVARET, og lar feltet stå tomt uten.
    assert "krav ? String(krav.sikkerhetsterskel)" in FLATE.read_text(
        encoding="utf-8")


def test_forslaget_kan_ikke_uttrykkes_uten_nomenklaturversjon():
    """`forslag_uten_nomenklaturversjon` — FORMEN PÅ TABELLEN.

    BÅDE fremmednøkkel OG snapshot: fremmednøkkelen binder til raden,
    snapshotet binder til TEKSTEN, og det er snapshotet som svarer på
    «hvilken versjon» år senere uten et oppslag.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    t = sql[sql.index("CREATE TABLE tollforslag"):
            sql.index("CREATE INDEX tollforslag_vare_idx")]
    assert "nomenklatur_id UUID NOT NULL" in t
    assert "tollforslag_nomenklatur_fk" in t
    assert "system_ved_forslag TEXT NOT NULL" in t
    assert "versjon_ved_forslag TEXT NOT NULL" in t
    assert "kode_ved_forslag TEXT NOT NULL" in t
    # …OG BESKRIVELSEN DEN BLE AVGITT MOT. Uten den kan ingen se HVA
    # som ble klassifisert — bare hva det ble klassifisert som.
    assert "beskrivelse_ved_forslag TEXT NOT NULL" in t
    # DOMMEN ER GENERERT, ikke skrevet.
    assert ("GENERATED ALWAYS AS (sikkerhet >= terskel_brukt) STORED"
            in t)


# --------------------------------------------------------------------
# DOMMENE, MÅLT MOT BASEN.
# --------------------------------------------------------------------

@pg
def test_forslag_uten_grunnlag_nektes(miljo):
    """MODULENS SKARPESTE NEKT.

    ET FORSLAG UTEN GRUNNLAG ER VERRE ENN INGEN FORSLAG: en kode som
    står der ser like ferdig ut som en noen har tenkt på, og den som
    stempler den har flyttet ansvaret uten å ha flyttet kontrollen.

    MUTASJONEN SOM DREPER DENNE: fjern kardinalitetssjekken.
    """
    tenant = _tenantnavn("utengrunn")
    with _rt() as c:
        _krav(c, tenant)
        nid = _nomenklatur(c, tenant)
        vnr = _varenummer(c, tenant, nid)
        vid = _vare(c, tenant)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute(
                "SELECT * FROM m52_avgi_forslag("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s::date[],%s)",
                (tenant, uuid.uuid4(), vid, vnr, 90, [], [], [], [],
                 "u-test"))
        assert "ingen grunn" in str(e.value)
        assert "falsk trygghet" in str(e.value)
        c.rollback()
        # …og ulik lengde på listene avvises, fordi et felt som
        # forsvant i kappingen ville gjort forslaget svakere enn det
        # ser ut.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute(
                "SELECT * FROM m52_avgi_forslag("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s::date[],%s)",
                (tenant, uuid.uuid4(), vid, vnr, 90,
                 ["nomenklaturtekst", "faglig_vurdering"], ["a"],
                 ["bbbb"], [None], "u-test"))
        assert "ulik" in str(e.value)
        c.rollback()


@pg
def test_forslag_mot_avviklet_nomenklatur_nektes(miljo):
    """`forslag_mot_utlopt_nomenklatur`, halvdel én: DØRA NEKTER.

    En kode som var riktig i 2022 kan være avviklet i dag, og et
    forslag mot en avviklet versjon er ikke et gammelt forslag — det
    er et VELFORMET OG GALT svar.
    """
    tenant = _tenantnavn("avviklet")
    with _rt() as c:
        _krav(c, tenant)
        # ET ALT AVVIKLET SETT KAN REGISTRERES — det er ARKIVET (121s
        # lærdom): en klassifisering fra 2017 må kunne forstås mot
        # nomenklaturen som gjaldt DA.
        gammel = _nomenklatur(c, tenant, versjon="HS 2017",
                              fra="2017-01-01", til="2021-12-31")
        vnr = _varenummer(c, tenant, gammel)
        vid = _vare(c, tenant)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute(
                "SELECT * FROM m52_avgi_forslag("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s::date[],%s)",
                (tenant, uuid.uuid4(), vid, vnr, 90,
                 ["nomenklaturtekst"], ["7318.15"], ["Skruer"],
                 [None], "u-test"))
        assert "ikke gyldig i dag" in str(e.value)
        c.rollback()


@pg
def test_forslag_under_terskel_avgis_ikke(miljo):
    """Et forslag under terskelen ville sett like ferdig ut som ett
    over, og sikkerheten står bare på raden — ikke i øyet til den som
    leser den i en liste."""
    tenant = _tenantnavn("underterskel")
    with _rt() as c:
        _krav(c, tenant, terskel=70)
        nid = _nomenklatur(c, tenant)
        vnr = _varenummer(c, tenant, nid)
        vid = _vare(c, tenant)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute(
                "SELECT * FROM m52_avgi_forslag("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s::date[],%s)",
                (tenant, uuid.uuid4(), vid, vnr, 55,
                 ["nomenklaturtekst"], ["7318.15"], ["Skruer"],
                 [None], "u-test"))
        # BESKJEDEN BÆRER BEGGE TALLENE (119s lærdom).
        assert "55" in str(e.value) and "70" in str(e.value)
        c.rollback()


@pg
def test_forslaget_baerer_koden_terskelen_og_grunnene(miljo):
    """Den som klassifiserer skal se hva forslaget SIER."""
    tenant = _tenantnavn("gyldig")
    with _rt() as c:
        _krav(c, tenant, terskel=70)
        nid = _nomenklatur(c, tenant, versjon="HS 2022")
        vnr = _varenummer(c, tenant, nid, kode="7318.15")
        vid = _vare(c, tenant)
        _fid, rad = _forslag(c, tenant, vid, vnr, sikkerhet=90,
                             grunner=[
                                 _grunn("bindende_forhandsuttalelse",
                                        "BKU-2024-117",
                                        "Identisk skrue i 7318.15",
                                        "2024-03-01"),
                                 _grunn()])
        sikkerhet, terskel, over, antall, system, versjon, kode = rad
        assert (sikkerhet, terskel, over, antall) == (90, 70, True, 2)
        assert (system, versjon, kode) == ("hs", "HS 2022", "7318.15")
        # GRUNNENE I RETTSKILDENES REKKEFØLGE: en bindende
        # forhåndsuttalelse veier tyngre enn en tekstlikhet.
        _sett_kontekst(c, tenant)
        arter = [r[1] for r in c.execute(
            "SELECT * FROM m52_grunnene(%s,%s)",
            (tenant, _fid)).fetchall()]
        assert arter == ["bindende_forhandsuttalelse",
                         "nomenklaturtekst"]
        c.rollback()


@pg
def test_hevet_terskel_stopper_klarmerkingen(miljo):
    """Å merke klart ville vært å be et menneske deklarere på et
    grunnlag tenanten SELV har forkastet.

    LÅSEN LESES PÅ NYTT ETTER `FOR UPDATE` (klynge 6s lærdom).
    """
    tenant = _tenantnavn("hevet")
    with _rt() as c:
        _krav(c, tenant, terskel=70)
        nid = _nomenklatur(c, tenant)
        vnr = _varenummer(c, tenant, nid)
        vid = _vare(c, tenant)
        fid, _rad = _forslag(c, tenant, vid, vnr, sikkerhet=90)
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT m52_merk_klart(%s,%s,%s)",
                         (tenant, fid, "u-test")).fetchone()[0] == 90
        c.rollback()

        _krav(c, tenant, terskel=95)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT m52_merk_klart(%s,%s,%s)",
                      (tenant, fid, "u-test"))
        assert "90" in str(e.value) and "95" in str(e.value)
        c.rollback()

    sql = _bare_kode(MIGRASJON)
    kropp = sql[sql.index("CREATE FUNCTION m52_merk_klart"):
                sql.index("REVOKE ALL ON FUNCTION m52_merk_klart")]
    laas = kropp.index("FOR UPDATE")
    assert "klar_til_deklarering" in kropp[laas:]


@pg
def test_forslaget_og_nomenklaturidentiteten_er_frosset(miljo):
    """`forslag_overskrevet`.

    Et forslag som kunne endres i ettertid ville gjort «hva foreslo
    vi, og hvorfor» til et spørsmål uten svar den dagen
    tollmyndigheten spør.

    NOMENKLATURENS IDENTITET er frosset av en KOLONNEGRANT (121s
    dom) — bare `gyldig_til` kan settes, fordi et tollvesen som
    kunngjør en avviklingsdato er nettopp den endringen modulen skal
    følge med på.
    """
    tenant = _tenantnavn("frosset")
    with _rt() as c:
        _krav(c, tenant)
        nid = _nomenklatur(c, tenant)
        vnr = _varenummer(c, tenant, nid)
        vid = _vare(c, tenant)
        fid, _rad = _forslag(c, tenant, vid, vnr)
        c.rollback()

    from db.pg import koble
    with koble(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        m.execute("SET LOCAL ROLE disponit_tollkode_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as e:
            m.execute("UPDATE tollforslag SET sikkerhet = 99"
                      " WHERE forslag_id = %s", (fid,))
        assert "frosset" in str(e.value)
        m.rollback()

    for kolonne, ny in (("versjon", "'HS 2099'"),
                        ("system", "'kn'"),
                        ("gyldig_fra", "'2020-01-01'")):
        with koble(MIGRATOR_DSN) as m:
            _sett_kontekst(m, tenant)
            m.execute("SET LOCAL ROLE disponit_tollkode_eier")
            with pytest.raises(
                    psycopg.errors.InsufficientPrivilege) as e:
                m.execute(f"UPDATE nomenklatur SET {kolonne} = {ny}"
                          " WHERE nomenklatur_id = %s", (nid,))
            assert "permission denied" in str(e.value).lower()
            m.rollback()

    # …MEN AVVIKLINGSDATOEN KAN SETTES.
    with _rt() as c:
        _sett_kontekst(c, tenant)
        c.execute("SELECT m52_sett_gyldig_til(%s,%s,%s::date,%s)",
                  (tenant, nid, "2027-12-31", "u-test"))
        c.commit()
        _sett_kontekst(c, tenant)
        rad = c.execute(
            "SELECT versjon, gyldig_til FROM m52_nomenklaturene(%s,500)",
            (tenant,)).fetchone()
        assert rad[0] == "HS 2022", "identiteten flyttet seg"
        assert rad[1] == datetime.date(2027, 12, 31)
        c.rollback()

    # …OG SLETTING ER ALDRI LOVLIG.
    for tabell in EGNE:
        with koble(MIGRATOR_DSN) as m:
            _sett_kontekst(m, tenant)
            m.execute("SET LOCAL ROLE disponit_tollkode_eier")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                m.execute(f"DELETE FROM {tabell}")
            m.rollback()


@pg
def test_en_ny_nomenklatur_gir_et_nytt_forslag(miljo):
    """SAMME VARE MOT SAMME REGELVERK ER ÉTT FORSLAG; mot et NYTT er
    det en ny rad ved siden av den gamle."""
    tenant = _tenantnavn("nyrad")
    with _rt() as c:
        _krav(c, tenant)
        n1 = _nomenklatur(c, tenant, versjon="HS 2022")
        v1 = _varenummer(c, tenant, n1)
        vid = _vare(c, tenant)
        _forslag(c, tenant, vid, v1)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(
                "SELECT * FROM m52_avgi_forslag("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s::date[],%s)",
                (tenant, uuid.uuid4(), vid, v1, 90,
                 ["nomenklaturtekst"], ["x"], ["yyyy"], [None],
                 "u-test"))
        c.rollback()
        n2 = _nomenklatur(c, tenant, versjon="HS 2027",
                          fra="2027-01-01")
        c.rollback()
    # HS 2027 gjelder ikke i dag, så forslaget mot den nektes — og
    # DET er riktig: man klassifiserer mot regelverket som gjelder.
    with _rt() as c:
        v2 = _varenummer(c, tenant, n2, kode="7318.16")
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute(
                "SELECT * FROM m52_avgi_forslag("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s::date[],%s)",
                (tenant, uuid.uuid4(), vid, v2, 90,
                 ["nomenklaturtekst"], ["x"], ["yyyy"], [None],
                 "u-test"))
        c.rollback()


@pg
def test_tenantisolasjon(miljo):
    """`tenantlekkasje_i_tollregister`."""
    a, b = _tenantnavn("iso-a"), _tenantnavn("iso-b")
    with _rt() as c:
        for t in (a, b):
            _krav(c, t)
            _nomenklatur(c, t)
            _vare(c, t, ref=f"ART-{t[-4:]}")
        _sett_kontekst(c, a)
        refs = [r[1] for r in c.execute(
            "SELECT * FROM m52_varene(%s,500)", (a,)).fetchall()]
        assert len(refs) == 1 and refs[0].endswith(a[-4:])
        with pytest.raises(psycopg.Error):
            c.execute("SELECT * FROM m52_varene(%s,500)", (b,))
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
            c.execute("SELECT * FROM m52_sveip_tollkode(10)")
        c.rollback()


# --------------------------------------------------------------------
# SVEIPEN: ETT FUNN INGEN KAN LUKKE.
# --------------------------------------------------------------------

@pg
def test_sveipen_finner_forslag_mot_utlopt_nomenklatur(miljo):
    """`forslag_mot_utlopt_nomenklatur`, halvdel to: TIDEN.

    Døra nekter et forslag mot en avviklet nomenklatur. Men det
    farlige forslaget er det som var RIKTIG da det ble avgitt, og som
    ligger og venter på et menneske mens tollmyndigheten skifter
    versjon under det.

    EN FORELDET REGEL SER NØYAKTIG UT SOM EN RIKTIG REGEL. Derfor er
    dette funnet det ingen kan lukke for hånd.
    """
    tenant = _tenantnavn("utlopt")
    with _rt() as c:
        _krav(c, tenant)
        nid = _nomenklatur(c, tenant, versjon="HS 2022")
        vnr = _varenummer(c, tenant, nid)
        vid = _vare(c, tenant)
        fid, _rad = _forslag(c, tenant, vid, vnr)
        _sett_kontekst(c, tenant)
        c.execute("SELECT m52_merk_klart(%s,%s,%s)",
                  (tenant, fid, "u-test"))
        c.commit()

    with _sv() as v:
        rad = _sveip(v)
    assert rad[0] >= 1
    with _rt() as c:
        funn = {r[0]: r for r in _funn(c, tenant)}
    assert "forslag_mot_utlopt_nomenklatur" not in funn

    # NOMENKLATUREN AVVIKLES — og forslaget står urørt.
    with _rt() as c:
        _sett_kontekst(c, tenant)
        # …til en dato ETTER settets startdato, men i fortiden:
        # nomenklaturen VAR gyldig da forslaget ble avgitt.
        c.execute("SELECT m52_sett_gyldig_til(%s,%s,%s::date,%s)",
                  (tenant, nid, "2023-06-30", "u-test"))
        c.commit()

    with _sv() as v:
        _sveipen = _sveip(v)
    with _rt() as c:
        funn = {r[0]: r for r in _funn(c, tenant)}
    rad = funn["forslag_mot_utlopt_nomenklatur"]
    # `over_grense` er DØGN siden settet gikk ut — ikke et ja/nei.
    # Tallet er selve poenget: et forslag som ble foreldet i går og
    # ett som har ligget i tre år krever ikke samme hastverk.
    assert isinstance(rad[1], int) and rad[1] > 0
    assert "HS 2022" in rad[2]
    assert rad[4] is True

    # IDEMPOTENT: en ny sveip gir samme ene funn, ikke to.
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        assert len([r for r in _funn(c, tenant)
                    if r[0] == "forslag_mot_utlopt_nomenklatur"]) == 1

    # …OG ET MENNESKE FÅR IKKE LUKKE DET.
    with _rt() as c:
        _sett_kontekst(c, tenant)
        fnid = c.execute(
            "SELECT funn_id FROM m52_funnene(%s,true)"
            " WHERE funntype = %s",
            (tenant, "forslag_mot_utlopt_nomenklatur")).fetchone()[0]
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT m52_lukk_funn(%s,%s,%s,%s)",
                      (tenant, fnid, "sett paa", "u-test"))
        assert "kan ikke lukkes" in str(e.value)
        assert "en handling, ikke en mening" in str(e.value)
        c.rollback()

    # DEN LUKKES NÅR TILSTANDEN ER BORTE — her: et gyldig etterfølgende
    # sett finnes, og forslaget er avgitt på nytt mot det.
    with _rt() as c:
        n2 = _nomenklatur(c, tenant, versjon="HS 2027",
                          fra="2019-01-01")
        v2 = _varenummer(c, tenant, n2, kode="7318.16")
        _forslag(c, tenant, vid, v2)
        c.commit()
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        rad = [r for r in _funn(c, tenant, bare_apne=False)
               if r[0] == "forslag_mot_utlopt_nomenklatur"][0]
    assert rad[4] is False, "funnet ble ikke lukket av sveipen"


@pg
def test_sveipen_maaler_uten_aa_deklarere(miljo):
    """Sveipen skriver funn og ingenting annet."""
    sql = _bare_kode(MIGRASJON)
    kropp = sql[sql.index("CREATE FUNCTION m52_sveip_tollkode"):]
    kropp = kropp[:kropp.index("REVOKE ALL ON FUNCTION"
                               " m52_sveip_tollkode")]
    for tabell in EGNE:
        if tabell == "tollfunn":
            continue
        assert f"INSERT INTO public.{tabell}" not in kropp, tabell
        assert f"UPDATE public.{tabell}" not in kropp, tabell
    # TENANTLISTA MATERIALISERES FØR `set_config` (klynge 6s lærdom om
    # den late markøren).
    assert "ARRAY(" in kropp or "array_agg" in kropp
    assert "v_tenanter" in kropp
    # TELLERNE AKKUMULERES, de settes ikke — `INTO` SETTER en variabel
    # (klynge 6s lærdom), så hver runde må legges til eksplisitt.
    for teller in ("v_nye", "v_oppdaterte", "v_lukket"):
        assert f"{teller} := {teller} +" in kropp, teller


# --------------------------------------------------------------------
# DRIFTEN.
# --------------------------------------------------------------------

def test_sveipefilen_har_kontrakten():
    from drift import tollkodesveip
    assert tollkodesveip.ARBEIDERNOKKEL == 638_204_915
    assert tollkodesveip.KONTRAKTFELT == 4


def test_sveipen_staar_i_flaaterosteret():
    from drift.sveipestatus import FLAATEN
    assert FLAATEN.get("tollkodesveip") == 30, FLAATEN


def test_kjoreren_alarmerer_paa_manglende_dsn():
    """En sveip som ikke kan koble seg til skal TELLE SOM FEIL.

    Ellers er en tom kjøring og en vellykket kjøring like stille — og
    modulen som skal måle at forslag foreldes, foreldes selv i taushet.
    """
    kilde = (ROT / "platform" / "drift"
             / "kjor_tollkodesveip.py").read_text(encoding="utf-8")
    mangler = kilde[kilde.index("DISPONIT_TOLLKODESVEIP_URL"):]
    mangler = mangler[:mangler.index("return 2")]
    assert "_skriv_feiltelling(" in mangler
    assert "_les_feiltelling() + 1" in mangler
    assert '"alarm"' in mangler
    assert '"feilet": 1' in mangler
    # …og en tellerverdi fra fila som ikke er et ekte, ikke-negativt
    # heltall er en feil, ikke en verdi: `int(True)` er 1, og en
    # negativ teller ville slått alarmen av permanent.
    assert "isinstance(raa, bool)" in kilde
    assert "negativ feiltelling" in kilde


def test_timeren_staar_utenfor_de_andres_vindu():
    # ÉN SETNING PER `read_text`: utf8-porten balanserer paranteser og
    # gir opp før den ser `encoding=` når kallet brytes over linjer.
    kat = ROT / "deploy" / "staging"
    enhet = (kat / "disponit-tollkodesveip.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 06:05:00 UTC" in enhet
    assert "Persistent=true" in enhet
    sti = kat / "disponit-tollkodesveip.service"
    tjeneste = sti.read_text(encoding="utf-8")
    assert "User=disponit" in tjeneste
    assert "kjor_tollkodesveip" in tjeneste


# --------------------------------------------------------------------
# API-FLATEN.
# --------------------------------------------------------------------

def test_rutene_er_registrert_med_riktig_scope():
    """LESING `okonomi:read`, SKRIVING `bestilling:opprett`."""
    from api.app import RUTESCOPE
    forventet = {
        ("GET", "/v1/toll"): "okonomi:read",
        ("GET", "/v1/toll/funn"): "okonomi:read",
        ("GET",
         "/v1/toll/nomenklatur/{nomenklatur_id:uuid}/varenummer"):
            "okonomi:read",
        ("GET", "/v1/toll/forslag/{forslag_id:uuid}/grunner"):
            "okonomi:read",
        ("GET", "/v1/toll/vare/{vare_id:uuid}/forslag"):
            "okonomi:read",
        ("POST", "/v1/toll/krav"): "bestilling:opprett",
        ("POST", "/v1/toll/nomenklatur"): "bestilling:opprett",
        ("POST", "/v1/toll/varenummer"): "bestilling:opprett",
        ("POST", "/v1/toll/vare"): "bestilling:opprett",
        ("POST",
         "/v1/toll/nomenklatur/{nomenklatur_id:uuid}/gyldig-til"):
            "bestilling:opprett",
        ("POST", "/v1/toll/vare/{vare_id:uuid}/forslag"):
            "bestilling:opprett",
        ("POST", "/v1/toll/forslag/{forslag_id:uuid}/klart"):
            "bestilling:opprett",
        ("POST", "/v1/toll/funn/{funn_id:uuid}/lukk"):
            "bestilling:opprett",
    }
    for nokkel, scope in forventet.items():
        assert RUTESCOPE.get(nokkel) == scope, nokkel
    # INGEN RUTE DEKLARERER — sett fra rutetabellen.
    for metode, sti in RUTESCOPE:
        if sti.startswith("/v1/toll"):
            assert "deklar" not in sti, sti
            assert "send" not in sti, sti


def test_api_kaller_bare_doerene():
    """Ingen rå SQL mot tabellene fra APIet."""
    api = _bare_kode(MODULFILER[0])
    for tabell in EGNE:
        assert not re.search(
            rf"\b(INSERT INTO|UPDATE|DELETE FROM)\s+{tabell}\b",
            api), tabell
    assert "m52_avgi_forslag" in api
    assert "m52_merk_klart" in api


def test_forslagsruten_krever_grunnene_i_samme_kall():
    """Et API som tok imot grunnene etterpå ville gjenåpnet vinduet
    døra lukket."""
    api = MODULFILER[0].read_text(encoding="utf-8")
    rute = api[api.index("def avgi_forslag_endepunkt"):]
    rute = rute[:rute.index("\ndef ")]
    assert "_grunner(kropp, rid)" in rute
    assert "m52_avgi_forslag" in rute
    for annen in ("def registrer_grunn_endepunkt",
                  "m52_registrer_grunn"):
        assert annen not in api, annen
    # …og `_grunner` kapper ikke: ulik lengde er en feil, ikke en
    # stille avkorting som ville gjort forslaget svakere enn det ser ut.
    hjelper = api[api.index("def _grunner("):]
    hjelper = hjelper[:hjelper.index("\ndef ")]
    assert "MAKS_GRUNNER" in hjelper
    assert "len(" in hjelper


def test_flaten_viser_grunnene_og_terskelen():
    js = FLATE.read_text(encoding="utf-8")
    assert "m52_grunnene" in js, "rekkefølgen er ikke forankret"
    assert "over_terskel" in js
    # …OG DEN LAR IKKE ET FORSLAG AVGIS UTEN GRUNNLAG.
    assert "grunner.length === 0" in js
    # …OG UNDER TERSKELEN SIER DEN DET, i stedet for å vise et tall.
    assert 't("ui.tollkode.under_terskel")' in js


def test_ui_axe_dekning():
    """`ui_axe_alvorlige_brudd` — flaten er registrert der axe kjører."""
    app_js = (ROT / "platform" / "core" / "ui" / "static" / "js"
              / "app.js").read_text(encoding="utf-8")
    assert "tollkode: visTollkode," in app_js
    sitekart = (ROT / "platform" / "core" / "ui" / "static" / "js"
                / "sitekart.js").read_text(encoding="utf-8")
    assert ('{ nokkel: "tollkode", scope: "okonomi:read",'
            ' modulflate: 52 }') in sitekart


@pg
def test_sveipen_ser_en_tenant_som_bare_har_varer(miljo):
    """CodeRabbits funn: TENANTLISTA VAR BARE NOMENKLATURENE.

    En tenant som har registrert varer, men ennå ingen nomenklatur, er
    NØYAKTIG den som trenger `vare_uten_forslag` og `ingen_krav` mest —
    han har varer på vei ut og ingenting å klassifisere dem mot. Med
    nomenklaturtabellen alene hoppet sveipen rett over ham, hver natt,
    uten et ord.

    MUTASJONEN SOM DREPER DENNE: fjern `UNION`-en over `tollvare`.
    """
    tenant = _tenantnavn("barevarer")
    with _rt() as c:
        _vare(c, tenant, ref="ART-ALENE")
        c.commit()
        _sett_kontekst(c, tenant)
        assert c.execute(
            "SELECT count(*) FROM m52_nomenklaturene(%s,500)",
            (tenant,)).fetchone()[0] == 0, "forutsetningen holdt ikke"
        c.rollback()

    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        rader = {r[0]: r for r in _funn(c, tenant)}
    assert rader, "sveipen hoppet over en tenant som bare har varer"
    # …OG FUNNET ER DET ÆRLIGE: han har ingenting å måle mot ennå.
    # `vare_uten_forslag` krever en frist, og en innebygd frist ville
    # vært en fullmakt modulen ga seg selv.
    assert "ingen_krav" in rader, sorted(rader)
    assert "nomenklatur" in rader["ingen_krav"][2]
    assert "vare_uten_forslag" not in rader

    # IDEMPOTENT: ett funn, ikke ett per natt.
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        assert len([r for r in _funn(c, tenant)
                    if r[0] == "ingen_krav"]) == 1

    # …OG DET LUKKES AV EN HANDLING: så snart tenanten registrerer et
    # krav, er tilstanden borte.
    with _rt() as c:
        _krav(c, tenant)
        c.commit()
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        aapne = {r[0] for r in _funn(c, tenant)}
    assert "ingen_krav" not in aapne, "funnet ble stående etter kravet"


@pg
def test_tenantlista_leses_bare_uten_kontekst(miljo):
    """Kryss-tenant-autoriteten er snever på BEGGE registrene."""
    sql = _bare_kode(MIGRASJON)
    for tabell in ("nomenklatur", "tollvare"):
        vakt = sql[sql.index(f"CREATE POLICY m52_sveip_tenantliste"
                             f"{'_vare' if tabell == 'tollvare' else ''}"
                             f" ON {tabell}"):]
        vakt = vakt[:vakt.index(";")]
        assert "FOR SELECT TO disponit_tollkode_eier" in vakt, tabell
        assert "IS NULL" in vakt, tabell
    # …og ingen andre tabell har en slik vakt.
    assert sql.count("CREATE POLICY m52_sveip_tenantliste") == 2


def test_en_hemmelighet_som_ikke_lastes_teller_som_feil():
    """CodeRabbits andre funn: `last_credentials()` sto UTENFOR vakten.

    Slapp unntaket ut derfra, økte telleren aldri: et permanent ødelagt
    LoadCredential-oppsett ville avsluttet med en stakksporing i
    journalen hver natt, uten at alarmen noen gang bygget seg opp. Det
    er samme hull som den manglende DSN-en, ett steg tidligere.
    """
    kilde = (ROT / "platform" / "drift"
             / "kjor_tollkodesveip.py").read_text(encoding="utf-8")
    vakt = kilde[kilde.index("last_credentials()"):]
    vakt = vakt[:vakt.index("DISPONIT_TOLLKODESVEIP_URL")]
    assert "except Exception:" in vakt
    assert "_les_feiltelling() + 1" in vakt
    assert "_skriv_feiltelling(" in vakt
    assert '"alarm"' in vakt
    assert "hemmeligheter_kunne_ikke_lastes" in vakt


@pg
def test_en_null_grunnliste_gir_ikke_et_forslag_uten_grunnlag(miljo):
    """CodeRabbits KRITISKE funn: `cardinality(NULL)` ER NULL.

    Lengdesjekken sammenlignet fire kardinaliteter. Var én av listene
    NULL, ble sammenligningen NULL — altså ikke SANN — og `IF`-en slo
    ikke til. Da var `p_grunn_arter` fylt, vakten passert, og
    `unnest`-en over en NULL-liste ga NULL RADER: et forslag som
    beskriver seg selv som begrunnet, uten en eneste grunn i basen.

    DET ER PRESIS DEN FALSKE TRYGGHETEN MODULEN FINNES FOR Å HINDRE, og
    den kom inn gjennom modulens egen vakt.
    """
    tenant = _tenantnavn("nullgrunn")
    with _rt() as c:
        _krav(c, tenant)
        nid = _nomenklatur(c, tenant)
        vnr = _varenummer(c, tenant, nid)
        vid = _vare(c, tenant)
        for henv, utdrag, datoer in (
                (None, ["Skruer av staal"], [None]),
                (["HS 73.18"], None, [None]),
                (["HS 73.18"], ["Skruer av staal"], None)):
            _sett_kontekst(c, tenant)
            with pytest.raises(
                    psycopg.errors.InvalidParameterValue) as e:
                c.execute(
                    "SELECT * FROM m52_avgi_forslag("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s::date[],%s)",
                    (tenant, uuid.uuid4(), vid, vnr, 90,
                     ["nomenklaturtekst"], henv, utdrag, datoer,
                     "u-test"))
            assert "NULL" in str(e.value)
            c.rollback()
