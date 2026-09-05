"""M-45 bærekrafts- og ESG-agent v1 (136) — KLYNGE 9s FJERDE OG SISTE.

Grensen `m45-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

KLYNGENS DELTE DOM, OG DENNE MODULEN ER DENS SKARPESTE TILFELLE:

  EN YTRING AVGITT I HUSETS NAVN KAN IKKE TAS TILBAKE — OG DEN SOM
  LESER DEN VET IKKE AT EN MASKIN SKREV DEN.

En bærekraftsrapport leses av investorer, kunder og et tilsyn. Et
estimat lest som en måling er grønnvasking, uansett hva som var ment.

OG M-45 ER EN KLYNGE 7-MODUL I FORKLEDNING: den eneste av de fire som
rapporterer til en MYNDIGHET. `standardversjon_laast_per_periode` er
klynge 7s dom — EN FORELDET REGEL SER NØYAKTIG UT SOM EN RIKTIG REGEL
— anvendt på rapporteringsstandarden selv.

DEN TYNGSTE GRUPPEN PORTER MÅLER AT LÅSEN ER STRUKTURELL. En måling
som brukte en faktor fra en annen standardversjon enn perioden sin er
ikke validert bort — den er urepresenterbar, fordi begge
fremmednøklene går mot en sammensatt nøkkel som BÆRER versjonen.

FEM PORTER MÅLER ET FRAVÆR SOM ER ET BEVIS.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import ast
import contextlib
import datetime as dt
import os
import re
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN  # noqa: F401

ESGSVEIP_DSN = os.environ.get("DISPONIT_TEST_ESGSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "136_m45_esg.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "esg.js")
FUNDAMENT = ROT / "docs" / "KLYNGE9-FUNDAMENT.md"
MODULFILER = (
    ROT / "platform" / "core" / "api" / "esg.py",
    ROT / "platform" / "drift" / "esgsveip.py",
    ROT / "platform" / "drift" / "kjor_esgsveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

#: TABELLENE MODULEN EIER. `kildedokument` STÅR IKKE HER: den er
#: M-46s (118), og M-20 arvet den i 134. Tre registre for «hva hviler
#: dette på» ville gitt tre svar.
EGNE = ("esgkrav", "rapportperiode", "utslippsfaktor", "esgmaaling",
        "esgpaastand", "esgrapport", "esgfunn")

_STRENG = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")


def _bare_kode(fil: Path, *, uten_strenger: bool = False) -> str:
    """Filens innhold uten kommentarer OG uten docstrings (m24-formen)."""
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
    ut = "\n".join(x for x in linjer if not x.lstrip().startswith(merke))
    return _STRENG.sub("''", ut) if uten_strenger else ut


@contextlib.contextmanager
def _to():
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
    return koble(ESGSVEIP_DSN or MIGRATOR_DSN)


def _sett_kontekst(conn, tenant):
    conn.execute("SELECT set_config('disponit.tenant', %s, false)",
                 (tenant,))


def _tenantnavn(merke: str) -> str:
    return f"t-m45-{merke}-{secrets.token_hex(4)}"


I_DAG = dt.date.today()


def _nektes(mg, t, sql, args, *, teller_sql, teller_args):
    """134s hjelper: en vakt som ikke får noe å bite i, biter ikke."""
    _sett_kontekst(mg, t)
    synlig = mg.execute(teller_sql, teller_args).fetchone()[0]
    assert synlig == 1, f"porten ville vaert tom: {synlig} rader synlige"
    with pytest.raises(psycopg.errors.RaiseException):
        mg.execute(sql, args)
    mg.rollback()


# =====================================================================
# BYGGEKLOSSER.
# =====================================================================

def _krav(rt, t, *, terskel=2000, estimatfrist=400, kilde_dogn=3650):
    _sett_kontekst(rt, t)
    v = rt.execute("SELECT m45_sett_krav(%s,%s,%s,%s,%s)",
                   (t, terskel, estimatfrist, kilde_dogn, "u-test")
                   ).fetchone()[0]
    rt.commit()
    return v


def _kilde(rt, t, *, tittel=None, gyldig_til=None):
    _sett_kontekst(rt, t)
    kid = uuid.uuid4()
    kid = rt.execute("SELECT m45_registrer_kilde(%s,%s,%s,%s,%s,%s,%s)",
                     (t, kid, tittel or f"Kilde {secrets.token_hex(3)}",
                      "maaling", gyldig_til, secrets.token_hex(32),
                      "u-test")).fetchone()[0]
    rt.commit()
    return kid


def _periode(rt, t, *, merke="2026", versjon="2026.1", standard="ESRS",
             fra=None, til=None):
    _sett_kontekst(rt, t)
    pid = uuid.uuid4()
    rt.execute("SELECT m45_apne_periode(%s,%s,%s,%s,%s,%s,%s,%s)",
               (t, pid, merke, fra or dt.date(2026, 1, 1),
                til or dt.date(2026, 12, 31), standard, versjon,
                "u-test"))
    rt.commit()
    return pid


def _faktor(rt, t, kilde, *, kategori="elektrisitet_no", enhet="kWh",
            verdi="0.01700000", versjon="2026.1", standard="ESRS",
            fra=None):
    _sett_kontekst(rt, t)
    fid = uuid.uuid4()
    rt.execute("SELECT m45_registrer_faktor(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
               (t, fid, kategori, enhet, verdi, standard, versjon,
                kilde, fra or dt.date(2026, 1, 1), "u-test"))
    rt.commit()
    return fid


def _maaling(rt, t, pid, faktor, kilde, *, kategori="elektrisitet_no",
             mengde="120000", enhet="kWh", estimat=False, grunnlag=None,
             erstatter=None):
    _sett_kontekst(rt, t)
    mid = uuid.uuid4()
    rad = rt.execute(
        "SELECT * FROM m45_registrer_maaling"
        "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (t, mid, pid, kategori, mengde, enhet, faktor, estimat,
         grunnlag, erstatter, kilde, "u-test")).fetchone()
    rt.commit()
    return mid, rad


def _eldre_maaling(mg, t, mid, *, dogn):
    """ESTIMATET BLIR GAMMELT, OG DET KAN BARE TIDEN GJØRE.

    Raden er frosset av `m45_maalingsvakt`, og det er riktig — derfor
    må vakten kobles ut for å fabrikkere tilstanden. Migrator eier
    tabellen og kan det; INGEN ANNEN KAN.
    """
    _sett_kontekst(mg, t)
    mg.execute("ALTER TABLE esgmaaling DISABLE TRIGGER m45_maalingsvakt")
    mg.execute("UPDATE esgmaaling SET registrert = now()"
               " - make_interval(days => %s)"
               " WHERE tenant=%s AND maaling_id=%s", (dogn, t, mid))
    mg.execute("ALTER TABLE esgmaaling ENABLE TRIGGER m45_maalingsvakt")
    mg.commit()


# =====================================================================
# §0: GRENSEN.
# =====================================================================

def test_grensen_er_registrert_og_hver_invariant_har_en_port():
    from manifestskjema import KRAVGRENSER
    grense = KRAVGRENSER["m45-v1"]
    assert grense["maks_brudd"] == 0
    tekst = Path(__file__).read_text(encoding="utf-8")
    uten = [i for i in grense["invarianter"] if i not in tekst]
    assert uten == [], f"invarianter uten port: {uten}"


def test_ui_axe_alvorlige_brudd_dekkes_av_flatens_egen_suite():
    js = ROT / "platform" / "core" / "ui" / "test" / "esg.test.js"
    assert js.exists(), "flatens egen suite mangler"
    tekst = js.read_text(encoding="utf-8")
    assert "axe" in tekst.lower() or "aria" in tekst.lower()


# =====================================================================
# `standardversjon_laast_per_periode` — MODULENS TYNGSTE INVARIANT.
# =====================================================================

@pg
def test_laasen_er_strukturell_ikke_en_sjekk():
    """`standardversjon_laast_per_periode`.

    KLYNGE 7s DOM ANVENDT PÅ STANDARDEN SELV: en foreldet regel ser
    nøyaktig ut som en riktig regel. Et tall regnet med fjorårets
    faktor og lest som årets er feil på nøyaktig den måten CSRD skal
    hindre.

    LÅSEN ER TO SAMMENSATTE FREMMEDNØKLER. Døra sier fra med en
    setning, men selv med døra fjernet er tilstanden urepresenterbar.

    MUTASJONEN SOM DREPER DENNE: bytt de sammensatte fremmednøklene mot
    enkle nøkler på `periode_id` og `faktor_id`.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("laast")
        _krav(rt, t)
        kid = _kilde(rt, t)
        pid = _periode(rt, t, versjon="2026.1")
        gammel = _faktor(rt, t, kid, versjon="2026.1")
        ny = _faktor(rt, t, kid, versjon="2027.1",
                     fra=dt.date(2026, 6, 1))
        # DØRA SIER FRA MED EN SETNING.
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute(
                "SELECT * FROM m45_registrer_maaling"
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), pid, "elektrisitet_no", "1", "kWh",
                 ny, False, None, None, kid, "u-test"))
        assert "laast til 2026.1" in str(e.value)
        rt.rollback()
        # …OG BASEN NEKTER FORBI DØRA. Å oppgi periodens versjon med
        # en faktor fra en annen er en fremmednøkkelfeil.
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            mg.execute(
                "INSERT INTO esgmaaling (tenant, maaling_id, periode_id,"
                " standardversjon, kategori, mengde, enhet, faktor_id,"
                " utslipp_kg, er_estimat, kilde_id, kilde_sha256,"
                " registrert_av)"
                " VALUES (%s,%s,%s,'2026.1','elektrisitet_no',1,'kWh',"
                " %s,1,false,%s,%s,'u')",
                (t, uuid.uuid4(), pid, ny, kid, "0" * 64))
        mg.rollback()
        # …OG Å OPPGI FAKTORENS VERSJON MED PERIODENS ID LIKESÅ.
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            mg.execute(
                "INSERT INTO esgmaaling (tenant, maaling_id, periode_id,"
                " standardversjon, kategori, mengde, enhet, faktor_id,"
                " utslipp_kg, er_estimat, kilde_id, kilde_sha256,"
                " registrert_av)"
                " VALUES (%s,%s,%s,'2027.1','elektrisitet_no',1,'kWh',"
                " %s,1,false,%s,%s,'u')",
                (t, uuid.uuid4(), pid, ny, kid, "0" * 64))
        mg.rollback()
        del gammel


@pg
def test_periodens_standardversjon_er_frossen():
    """En periode som kunne bytte versjon i ettertid ville gjort låsen
    til en anbefaling."""
    with _to() as (rt, mg):
        t = _tenantnavn("frossen")
        _krav(rt, t)
        pid = _periode(rt, t)
        for felt, verdi in (("standardversjon", "'2027.1'"),
                            ("standard", "'GRI'"),
                            ("fra", "'2020-01-01'"),
                            ("merke", "'noe annet'")):
            _nektes(mg, t,
                    f"UPDATE rapportperiode SET {felt}={verdi}"
                    " WHERE tenant=%s AND periode_id=%s", (t, pid),
                    teller_sql="SELECT count(*) FROM rapportperiode"
                               " WHERE tenant=%s AND periode_id=%s",
                    teller_args=(t, pid))
        _nektes(mg, t,
                "DELETE FROM rapportperiode"
                " WHERE tenant=%s AND periode_id=%s", (t, pid),
                teller_sql="SELECT count(*) FROM rapportperiode"
                           " WHERE tenant=%s AND periode_id=%s",
                teller_args=(t, pid))


@pg
def test_faktorens_verdi_er_frossen_en_rettelse_er_en_ny_faktor():
    """En faktor som kunne korrigeres i ettertid ville endret HVERT
    TALL som noen gang ble regnet med den.

    Og de gamle tallene ville endret seg uten at noen rørte dem — i en
    rapport et tilsyn har lest.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("faktor")
        _krav(rt, t)
        kid = _kilde(rt, t)
        fid = _faktor(rt, t, kid, verdi="0.01700000")
        for felt, verdi in (("verdi", "0.99"),
                            ("standardversjon", "'2027.1'"),
                            ("kategori", "'noe_annet'")):
            _nektes(mg, t,
                    f"UPDATE utslippsfaktor SET {felt}={verdi}"
                    " WHERE tenant=%s AND faktor_id=%s", (t, fid),
                    teller_sql="SELECT count(*) FROM utslippsfaktor"
                               " WHERE tenant=%s AND faktor_id=%s",
                    teller_args=(t, fid))


@pg
def test_utslippet_regnes_ved_skriving_og_fryses():
    """Regnet på LESETIDSPUNKT ville tallet endret seg når faktoren ble
    korrigert — og en rapport som endrer seg etter at den er lest, er
    ikke en rapport."""
    with _to() as (rt, mg):
        t = _tenantnavn("regnet")
        _krav(rt, t)
        kid = _kilde(rt, t)
        pid = _periode(rt, t)
        fid = _faktor(rt, t, kid, verdi="0.01700000")
        mid, rad = _maaling(rt, t, pid, fid, kid, mengde="120000")
        # 120000 kWh * 0,017 kg/kWh = 2040 kg.
        assert str(rad[1]) == "2040.000000", rad
        _nektes(mg, t,
                "UPDATE esgmaaling SET utslipp_kg=1"
                " WHERE tenant=%s AND maaling_id=%s", (t, mid),
                teller_sql="SELECT count(*) FROM esgmaaling"
                           " WHERE tenant=%s AND maaling_id=%s",
                teller_args=(t, mid))


# =====================================================================
# DE FEM SOM ALDRI KAN REISES.
# =====================================================================

@pg
def test_tall_uten_kilde_er_urepresenterbart():
    """`tall_uten_kilde` — OG DEN KAN ALDRI REISES.

    `kilde_id` er NOT NULL med fremmednøkkel til HUSETS kilderegister
    (118). Et tall uten kilde finnes ikke.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("utenkilde")
        _krav(rt, t)
        kid = _kilde(rt, t)
        pid = _periode(rt, t)
        fid = _faktor(rt, t, kid)
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.NotNullViolation):
            mg.execute(
                "INSERT INTO esgmaaling (tenant, maaling_id, periode_id,"
                " standardversjon, kategori, mengde, enhet, faktor_id,"
                " utslipp_kg, er_estimat, kilde_id, kilde_sha256,"
                " registrert_av)"
                " VALUES (%s,%s,%s,'2026.1','el',1,'kWh',%s,1,false,"
                " NULL,%s,'u')", (t, uuid.uuid4(), pid, fid, "0" * 64))
        mg.rollback()
        # …OG FAKTOREN HVILER OGSÅ PÅ ET DOKUMENT. En faktor uten
        # kilde er et tall noen husket, og hele rapporten hviler på det.
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.NotNullViolation):
            mg.execute(
                "INSERT INTO utslippsfaktor (tenant, faktor_id,"
                " kategori, enhet, verdi, standard, standardversjon,"
                " kilde_id, gyldig_fra, registrert_av)"
                " VALUES (%s,%s,'el','kWh',1,'ESRS','2026.1',NULL,"
                " '2026-01-01','u')", (t, uuid.uuid4()))
        mg.rollback()


@pg
def test_tall_uten_faktorversjon_er_urepresenterbart():
    """`tall_uten_faktorversjon`.

    `faktor_id` OG `standardversjon` er begge NOT NULL, og sammen er de
    en fremmednøkkel. Et tall uten faktorversjon kan ikke skrives — og
    et tall MED feil versjon heller ikke.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("utenversjon")
        _krav(rt, t)
        kid = _kilde(rt, t)
        pid = _periode(rt, t)
        fid = _faktor(rt, t, kid)
        for kolonne in ("faktor_id", "standardversjon"):
            _sett_kontekst(mg, t)
            verdier = {"faktor_id": str(fid), "standardversjon": "2026.1"}
            verdier[kolonne] = None
            with pytest.raises(psycopg.errors.NotNullViolation):
                mg.execute(
                    "INSERT INTO esgmaaling (tenant, maaling_id,"
                    " periode_id, standardversjon, kategori, mengde,"
                    " enhet, faktor_id, utslipp_kg, er_estimat,"
                    " kilde_id, kilde_sha256, registrert_av)"
                    " VALUES (%s,%s,%s,%s,'el',1,'kWh',%s,1,false,%s,"
                    " %s,'u')",
                    (t, uuid.uuid4(), pid, verdier["standardversjon"],
                     verdier["faktor_id"], kid, "0" * 64))
            mg.rollback()


@pg
def test_estimat_ikke_merket_er_urepresenterbart():
    """`estimat_ikke_merket`.

    `er_estimat` er NOT NULL **UTEN DEFAULT**, og bundet til
    `estimatgrunnlag` av en CHECK. En default ville stille merket alt
    som MÅLT, og en glemt kolonne ville blitt en FALSK PÅSTAND i
    stedet for en feil.

    MUTASJONEN SOM DREPER DENNE: gi `er_estimat` en default på false.
    """
    with _mig() as mg:
        rad = mg.execute(
            "SELECT column_default, is_nullable"
            "  FROM information_schema.columns"
            " WHERE table_name='esgmaaling' AND column_name='er_estimat'"
        ).fetchone()
    assert rad == (None, "NO"), (
        "en default paa er_estimat ville merket alt som maalt")
    with _to() as (rt, mg):
        t = _tenantnavn("umerket")
        _krav(rt, t)
        kid = _kilde(rt, t)
        pid = _periode(rt, t)
        fid = _faktor(rt, t, kid)
        # ET ESTIMAT MÅ SI HVA DET HVILER PÅ.
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute(
                "SELECT * FROM m45_registrer_maaling"
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), pid, "elektrisitet_no", "1", "kWh",
                 fid, True, None, None, kid, "u-test"))
        assert "maa si hva det hviler paa" in str(e.value)
        rt.rollback()
        # …OG EN MÅLING HAR IKKE ET ESTIMATGRUNNLAG.
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute(
                "SELECT * FROM m45_registrer_maaling"
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), pid, "elektrisitet_no", "1", "kWh",
                 fid, False, "vi gjettet litt her", None, kid,
                 "u-test"))
        rt.rollback()
        # …OG BASEN NEKTER FORBI DØRA: flagget er bundet til innholdet.
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.CheckViolation):
            mg.execute(
                "INSERT INTO esgmaaling (tenant, maaling_id, periode_id,"
                " standardversjon, kategori, mengde, enhet, faktor_id,"
                " utslipp_kg, er_estimat, estimatgrunnlag, kilde_id,"
                " kilde_sha256, registrert_av)"
                " VALUES (%s,%s,%s,'2026.1','el',1,'kWh',%s,1,true,"
                " NULL,%s,%s,'u')",
                (t, uuid.uuid4(), pid, fid, kid, "0" * 64))
        mg.rollback()


@pg
def test_paastand_uten_kilde_er_urepresenterbar():
    """`paastand_uten_kilde` — 134s form, arvet.

    «Ingen påstand uten datagrunnlag (anti-grønnvasking).»
    """
    with _to() as (rt, mg):
        t = _tenantnavn("paastand")
        _krav(rt, t)
        kid = _kilde(rt, t)
        pid = _periode(rt, t)
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.NotNullViolation):
            mg.execute(
                "INSERT INTO esgpaastand (tenant, paastand_id,"
                " periode_id, rekkefolge, tekst, kilde_id,"
                " kilde_sha256, registrert_av)"
                " VALUES (%s,%s,%s,1,'Vi kuttet 40 prosent',NULL,%s,'u')",
                (t, uuid.uuid4(), pid, "0" * 64))
        mg.rollback()
        del kid


@pg
def test_modulen_sendte_rapport_er_urepresenterbart():
    """`modulen_sendte_rapport` — OG FRAVÆRET ER PORTEN.

    Det finnes ingen kolonne for «sendt» i hele modulen, og ingen dør
    som setter en. Innsendingen til et tilsyn er et menneskes, og den
    hører hjemme i M-47 — en kolonne her ville gjort «sendte vi?» til
    et spørsmål med to svar.

    MUTASJONEN SOM DREPER DENNE: legg til `sendt_ts` på `esgrapport`.
    """
    with _mig() as mg:
        kolonner = [r[0] for r in mg.execute(
            "SELECT c.table_name || '.' || c.column_name"
            "  FROM information_schema.columns c"
            " WHERE c.table_name = ANY(%s)"
            "   AND (c.column_name ~ 'sendt|innsend|levert|"
            "innrapportert|oversendt')", (list(EGNE),)).fetchall()]
        doerer = [r[0] for r in mg.execute(
            "SELECT p.proname FROM pg_proc p"
            " WHERE p.proname LIKE 'm45\\_%'"
            "   AND (p.proname ~ 'send|lever|innrapport')").fetchall()]
    assert kolonner == [], f"modulen har en sendt-kolonne: {kolonner}"
    assert doerer == [], f"modulen har en sendedoer: {doerer}"


def test_ingen_kodevei_sender_rapporten():
    """MÅLT I KODEN, IKKE BARE I SKJEMAET.

    KOMMENTARER OG STRENGER FJERNES FØRST (128s lærdom): filhodene her
    er fulle av ordet «sender» nettopp fordi modulen ikke gjør det.
    """
    for fil in MODULFILER:
        if not fil.exists():
            continue
        kode = _bare_kode(fil, uten_strenger=True).lower()
        for forbudt in ("send_rapport", "sendrapport", "innsend",
                        "lever_rapport", "til_tilsyn"):
            assert forbudt not in kode, f"{fil.name}: {forbudt}"


@pg
def test_de_fem_umulige_staar_likevel_i_funntypesettet():
    """AT DE STÅR OG ER UMULIGE ER HELE BEVISET."""
    with _mig() as mg:
        uttrykk = mg.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'esgfunn_type_lukket'").fetchone()[0]
    for umulig in ("tall_uten_kilde", "tall_uten_faktorversjon",
                   "estimat_ikke_merket", "paastand_uten_kilde",
                   "modulen_sendte_rapport"):
        assert umulig in uttrykk, umulig


# =====================================================================
# `rapport_overskrevet` OG `tenantlekkasje_i_esgregister`.
# =====================================================================

@pg
def test_rapport_overskrevet_er_umulig_en_ny_er_en_ny_versjon():
    """`rapport_overskrevet`.

    «Hva sto i rapporten da noen leste den» må kunne besvares etterpå.
    Rapporten BÆRER TALLENE slik de sto — en rapport som pekte på
    tabellene ville endret seg når en måling ble rettet.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("rapport")
        _krav(rt, t)
        kid = _kilde(rt, t)
        pid = _periode(rt, t)
        fid = _faktor(rt, t, kid)
        _maaling(rt, t, pid, fid, kid, mengde="1000")
        _sett_kontekst(rt, t)
        rid = uuid.uuid4()
        r1 = rt.execute("SELECT * FROM m45_sammenstill(%s,%s,%s,%s)",
                        (t, rid, pid, "u-kari")).fetchone()
        rt.commit()
        assert r1[1] == 1 and str(r1[2]) == "17.000000", r1
        for felt, verdi in (("sum_utslipp_kg", "999"),
                            ("estimatandel_bp", "0"),
                            ("innholds_hash", "'" + "a" * 64 + "'")):
            _nektes(mg, t,
                    f"UPDATE esgrapport SET {felt}={verdi}"
                    " WHERE tenant=%s AND rapport_id=%s", (t, rid),
                    teller_sql="SELECT count(*) FROM esgrapport"
                               " WHERE tenant=%s AND rapport_id=%s",
                    teller_args=(t, rid))
        # EN NY SAMMENSTILLING ER EN NY RAD MED EN NY VERSJON, og den
        # gamle står med sine tall.
        _maaling(rt, t, pid, fid, kid, mengde="1000",
                 kategori="transport_diesel")
        _sett_kontekst(rt, t)
        r2 = rt.execute("SELECT * FROM m45_sammenstill(%s,%s,%s,%s)",
                        (t, uuid.uuid4(), pid, "u-kari")).fetchone()
        rt.commit()
        assert r2[1] == 2 and str(r2[2]) == "34.000000"
        _sett_kontekst(mg, t)
        gamle = mg.execute("SELECT sum_utslipp_kg FROM esgrapport"
                           " WHERE tenant=%s AND rapport_id=%s",
                           (t, rid)).fetchone()[0]
        mg.rollback()
    assert str(gamle) == "17.000000", "den gamle rapporten endret seg"


@pg
def test_en_erstattet_maaling_staar_men_teller_ikke():
    """HISTORIKKEN OVERSKRIVES ALDRI, men rapporten bærer det siste
    tallet.

    Et estimat som ble erstattet av en måling skal fortsatt kunne ses —
    «hva trodde vi da» er et spørsmål et tilsyn stiller.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("erstattet")
        _krav(rt, t)
        kid = _kilde(rt, t)
        pid = _periode(rt, t)
        fid = _faktor(rt, t, kid)
        est, _ = _maaling(rt, t, pid, fid, kid, mengde="1000",
                          estimat=True,
                          grunnlag="anslag basert paa fjoraaret")
        _maaling(rt, t, pid, fid, kid, mengde="1200", erstatter=est)
        _sett_kontekst(rt, t)
        rad = rt.execute("SELECT * FROM m45_sammenstill(%s,%s,%s,%s)",
                         (t, uuid.uuid4(), pid, "u-kari")).fetchone()
        rt.commit()
        # BARE DEN LEVENDE TELLER: 1200 * 0,017 = 20,4.
        assert str(rad[2]) == "20.400000", rad
        assert rad[3] == 0, "et erstattet estimat teller fortsatt"
        _sett_kontekst(rt, t)
        rader = rt.execute("SELECT * FROM m45_maalingene(%s,%s)",
                           (t, pid)).fetchall()
        rt.rollback()
        # …OG BEGGE STÅR, med den gamle merket som erstattet.
        assert len(rader) == 2
        assert [r[12] for r in rader].count(True) == 1
        del mg


@pg
def test_en_lukket_periode_tar_ikke_imot_flere_tall():
    """Et tall lagt til etter at rapporten ble sammenstilt ville endret
    et tall noen alt har lest."""
    with _to() as (rt, mg):
        t = _tenantnavn("lukket")
        _krav(rt, t)
        kid = _kilde(rt, t)
        pid = _periode(rt, t)
        fid = _faktor(rt, t, kid)
        _sett_kontekst(rt, t)
        rt.execute("SELECT m45_lukk_periode(%s,%s,%s)", (t, pid, "u-kari"))
        rt.commit()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute(
                "SELECT * FROM m45_registrer_maaling"
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), pid, "elektrisitet_no", "1", "kWh",
                 fid, False, None, None, kid, "u-test"))
        assert "er lukket" in str(e.value)
        rt.rollback()
        # …OG EN LUKKET PERIODE KAN IKKE ÅPNES IGJEN.
        _nektes(mg, t,
                "UPDATE rapportperiode SET status='apen', lukket_ts=NULL,"
                " lukket_av=NULL WHERE tenant=%s AND periode_id=%s",
                (t, pid),
                teller_sql="SELECT count(*) FROM rapportperiode"
                           " WHERE tenant=%s AND periode_id=%s",
                teller_args=(t, pid))


@pg
def test_tenantlekkasje_i_esgregister_er_umulig():
    """FORCE RLS PÅ ALLE SJU, målt fra to kanter."""
    with _to() as (rt, mg):
        t1 = _tenantnavn("egen")
        t2 = _tenantnavn("annen")
        for t in (t1, t2):
            _krav(rt, t)
        _periode(rt, t1)
        _sett_kontekst(rt, t2)
        sett = rt.execute("SELECT count(*) FROM m45_perioderegister(%s,%s)",
                          (t1, 50)).fetchone()[0]
        rt.rollback()
        assert sett == 0, "en annen tenants perioder var synlige"
        _sett_kontekst(rt, t2)
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as e:
            rt.execute("SELECT m45_sett_krav(%s,%s,%s,%s,%s)",
                       (t1, 1, 1, 1, "u-tyv"))
        assert "kallerens tenantkontekst" in str(e.value)
        rt.rollback()
        with _mig() as mg2:
            mangler = [r[0] for r in mg2.execute(
                "SELECT c.relname FROM pg_class c"
                " WHERE c.relname = ANY(%s)"
                "   AND NOT (c.relrowsecurity AND c.relforcerowsecurity)",
                (list(EGNE),)).fetchall()]
        assert mangler == [], f"uten FORCE ser eieren forbi policyen: {mangler}"
        del mg


@pg
def test_runtime_har_ingen_tabellrettigheter():
    """SP-7."""
    with _mig() as mg:
        rader = mg.execute(
            "SELECT table_name, privilege_type FROM"
            " information_schema.table_privileges"
            " WHERE grantee='disponit' AND table_name = ANY(%s)",
            (list(EGNE) + ["kildedokument"],)).fetchall()
    assert rader == [], f"runtime har tabellrettigheter: {rader}"


@pg
def test_modulen_arver_husets_kilderegister_for_tredje_gang():
    """M-46 bygde det i 118, M-20 arvet det i 134, M-45 arver det her.

    Tre registre for «hva hviler dette på» ville gitt tre svar.
    """
    with _mig() as mg:
        egne = sorted(r[0] for r in mg.execute(
            "SELECT c.relname FROM pg_class c"
            " WHERE c.relnamespace='public'::regnamespace"
            "   AND c.relkind='r'"
            "   AND (c.relname LIKE 'esg%' OR c.relname='rapportperiode'"
            "     OR c.relname='utslippsfaktor')").fetchall())
        fk = sorted(r[0] for r in mg.execute(
            "SELECT conname FROM pg_constraint"
            " WHERE confrelid='kildedokument'::regclass"
            "   AND conname LIKE '%kilde_fk'").fetchall())
    assert egne == sorted(EGNE), egne
    for ventet in ("esgmaaling_kilde_fk", "esgpaastand_kilde_fk",
                   "utslippsfaktor_kilde_fk"):
        assert ventet in fk, f"{ventet} peker ikke paa husets register"


# =====================================================================
# SVEIPEN.
# =====================================================================

@pg
def test_et_estimat_som_aldri_ble_erstattet_reises_og_lukkes_av_maalingen():
    """ET ESTIMAT ER LOV — DET ER MIDLERTIDIGHETEN SOM GJØR DET LOVLIG.

    Et estimat som har stått i to år er ikke et estimat lenger; det er
    et tall huset har bestemt seg for. Fristen er TENANTENS.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("gammelt")
        _krav(rt, t, estimatfrist=30)
        kid = _kilde(rt, t)
        pid = _periode(rt, t)
        fid = _faktor(rt, t, kid)
        est, _ = _maaling(rt, t, pid, fid, kid, mengde="1000",
                          estimat=True,
                          grunnlag="anslag basert paa fjoraaret")
        with _sv() as sv:
            sv.execute("SELECT * FROM m45_sveip_esg(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        assert mg.execute(
            "SELECT count(*) FROM esgfunn WHERE tenant=%s AND apen"
            "   AND funntype='estimat_ikke_erstattet_over_frist'",
            (t,)).fetchone()[0] == 0
        mg.rollback()

        _eldre_maaling(mg, t, est, dogn=45)
        with _sv() as sv:
            sv.execute("SELECT * FROM m45_sveip_esg(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        funn = mg.execute(
            "SELECT funntype, referanse, over_grense FROM esgfunn"
            " WHERE tenant=%s AND apen"
            "   AND funntype='estimat_ikke_erstattet_over_frist'",
            (t,)).fetchall()
        mg.rollback()
        assert funn == [("estimat_ikke_erstattet_over_frist", est, 45)], funn

        _maaling(rt, t, pid, fid, kid, mengde="1100", erstatter=est)
        with _sv() as sv:
            sv.execute("SELECT * FROM m45_sveip_esg(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        etter = mg.execute(
            "SELECT apen, lukket_av FROM esgfunn WHERE tenant=%s"
            "   AND funntype='estimat_ikke_erstattet_over_frist'",
            (t,)).fetchone()
        mg.rollback()
    assert etter == (False, "m45_sveip")


@pg
def test_en_foreldet_standardversjon_i_en_apen_periode_reises():
    """KLYNGE 7s DOM, ANVENDT PÅ STANDARDEN SELV.

    EN FORELDET REGEL SER NØYAKTIG UT SOM EN RIKTIG REGEL. Finnes det
    faktorer i en nyere versjon av samme standard, har standarden
    flyttet seg mens perioden sto åpen.

    SVEIPEN LÅSER IKKE OM. Låsen er dommen — den skal ikke kunne endres
    av en nattjobb. Sveipen sier fra; et menneske avgjør.

    MUTASJONEN SOM DREPER DENNE: la sveipen oppdatere
    `rapportperiode.standardversjon`.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("foreldet")
        _krav(rt, t)
        kid = _kilde(rt, t)
        pid = _periode(rt, t, versjon="2026.1")
        _faktor(rt, t, kid, versjon="2026.1", fra=dt.date(2026, 1, 1))
        with _sv() as sv:
            sv.execute("SELECT * FROM m45_sveip_esg(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        assert mg.execute(
            "SELECT count(*) FROM esgfunn WHERE tenant=%s AND apen",
            (t,)).fetchone()[0] == 0
        mg.rollback()

        # STANDARDEN FLYTTER SEG.
        _faktor(rt, t, kid, versjon="2027.1", fra=dt.date(2027, 1, 1),
                kategori="transport_diesel")
        with _sv() as sv:
            sv.execute("SELECT * FROM m45_sveip_esg(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        funn = mg.execute(
            "SELECT funntype, referanse FROM esgfunn"
            " WHERE tenant=%s AND apen", (t,)).fetchall()
        mg.rollback()
        assert funn == [("standardversjon_foreldet_i_apen_periode", pid)]

        # …OG SVEIPEN HAR IKKE RØRT LÅSEN.
        _sett_kontekst(mg, t)
        versjon = mg.execute(
            "SELECT standardversjon FROM rapportperiode"
            " WHERE tenant=%s AND periode_id=%s", (t, pid)).fetchone()[0]
        mg.rollback()
        assert versjon == "2026.1", "sveipen laaste om perioden"

        # PERIODEN LUKKES → funnet lukkes.
        _sett_kontekst(rt, t)
        rt.execute("SELECT m45_lukk_periode(%s,%s,%s)", (t, pid, "u-kari"))
        rt.commit()
        with _sv() as sv:
            sv.execute("SELECT * FROM m45_sveip_esg(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        etter = mg.execute(
            "SELECT apen, lukket_av FROM esgfunn WHERE tenant=%s"
            "   AND funntype='standardversjon_foreldet_i_apen_periode'",
            (t,)).fetchone()
        mg.rollback()
    assert etter == (False, "m45_sveip")


@pg
def test_et_menneske_kan_ikke_lukke_sveipens_funn():
    """Et estimat som har stått for lenge slutter ikke å ha stått for
    lenge fordi noen leste varselet."""
    with _to() as (rt, mg):
        t = _tenantnavn("nekt")
        _krav(rt, t, estimatfrist=10)
        kid = _kilde(rt, t)
        pid = _periode(rt, t)
        fid = _faktor(rt, t, kid)
        est, _ = _maaling(rt, t, pid, fid, kid, estimat=True,
                          grunnlag="anslag basert paa fjoraaret")
        _eldre_maaling(mg, t, est, dogn=30)
        with _sv() as sv:
            sv.execute("SELECT * FROM m45_sveip_esg(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        fid2 = mg.execute(
            "SELECT funn_id FROM esgfunn WHERE tenant=%s"
            "   AND funntype='estimat_ikke_erstattet_over_frist'",
            (t,)).fetchone()[0]
        mg.rollback()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute("SELECT m45_lukk_funn(%s,%s,%s,%s)",
                       (t, fid2, "vi tar det neste aar", "u-test"))
        assert "lukkes av at tilstanden opphoerer" in str(e.value)
        rt.rollback()


@pg
def test_estimatandel_over_terskel_kan_avklares_og_forblir_lukket():
    """«VI VET, OG DET STÅR I RAPPORTEN» er en avklaring med et navn på
    — og 131s lærdom gjelder.

    ANDELEN REGNES AV UTSLIPPET, IKKE AV ANTALLET: ti små estimater og
    én stor måling er ikke «91 % gjettet».

    MUTASJONEN SOM DREPER DENNE: fjern `NOT EXISTS`-leddet på lukkede
    funn i sveipens tredje blokk.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("andel")
        _krav(rt, t, terskel=2000)
        kid = _kilde(rt, t)
        pid = _periode(rt, t)
        fid = _faktor(rt, t, kid)
        # 1000 målt, 1000 estimert → 5000 bp, over terskelen på 2000.
        _maaling(rt, t, pid, fid, kid, mengde="1000")
        _maaling(rt, t, pid, fid, kid, mengde="1000",
                 kategori="transport_diesel", estimat=True,
                 grunnlag="anslag basert paa fjoraaret")
        with _sv() as sv:
            sv.execute("SELECT * FROM m45_sveip_esg(500)")
            sv.commit()
        _sett_kontekst(rt, t)
        funn = [f for f in rt.execute("SELECT * FROM m45_esgfunn(%s,%s)",
                                      (t, 50)).fetchall()
                if f[1] == "estimatandel_over_terskel_uavklart"]
        rt.rollback()
        assert funn, "halve utslippet er gjettet"
        assert funn[0][4] == 5000, funn[0]
        assert funn[0][9] is True, "et menneske skal kunne avklare denne"
        _sett_kontekst(rt, t)
        rt.execute("SELECT m45_lukk_funn(%s,%s,%s,%s)",
                   (t, funn[0][0], "vi vet, og det staar i rapporten",
                    "u-kari"))
        rt.commit()
        # NATTEN ETTER.
        with _sv() as sv:
            sv.execute("SELECT * FROM m45_sveip_esg(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        apne = mg.execute(
            "SELECT count(*) FROM esgfunn WHERE tenant=%s"
            "   AND funntype='estimatandel_over_terskel_uavklart'"
            "   AND apen", (t,)).fetchone()[0]
        mg.rollback()
    assert apne == 0, "sveipen gjenaapnet en avklaring"


@pg
def test_andelen_regnes_av_utslippet_ikke_av_antallet():
    """TI SMÅ ESTIMATER OG ÉN STOR MÅLING ER IKKE «91 % GJETTET».

    Det er tallet som betyr noe i en rapport, ikke radene.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("vekt")
        _krav(rt, t, terskel=2000)
        kid = _kilde(rt, t)
        pid = _periode(rt, t)
        fid = _faktor(rt, t, kid)
        _maaling(rt, t, pid, fid, kid, mengde="100000")
        for i in range(10):
            _maaling(rt, t, pid, fid, kid, mengde="100",
                     kategori=f"smaatteri_{i}", estimat=True,
                     grunnlag="anslag basert paa fjoraaret")
        _sett_kontekst(rt, t)
        rad = rt.execute("SELECT * FROM m45_sammenstill(%s,%s,%s,%s)",
                         (t, uuid.uuid4(), pid, "u-kari")).fetchone()
        rt.rollback()
        # 1000 av 101000 = 99 bp, altså under én prosent.
        assert rad[3] == 99, rad
        del mg


@pg
def test_sveipen_ser_ingenting_uten_kryss_tenant_policyen():
    """130s LÆRDOM."""
    with _mig() as mg:
        rad = mg.execute(
            "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy"
            " WHERE polrelid='esgkrav'::regclass"
            "   AND polname='m45_sveip_tenantliste'").fetchone()
    assert rad, "kryss-tenant-policyen mangler — sveipen ville vaert blind"
    assert "IS NULL" in rad[0], f"policyen er ikke snever nok: {rad[0]}"


@pg
def test_sveipen_teller_tenanter_og_gir_fire_felt():
    with _to() as (rt, mg):
        t = _tenantnavn("kontrakt")
        _krav(rt, t)
        with _sv() as sv:
            rader = sv.execute("SELECT * FROM m45_sveip_esg(500)").fetchall()
            sv.commit()
        del mg, t
    assert len(rader) == 1 and len(rader[0]) == 4
    assert rader[0][0] >= 1


@pg
def test_sveipen_sender_ingen_rapport_og_lukker_ingen_periode():
    """Målt mot rettighetene OG mot funksjonskroppen."""
    with _mig() as mg:
        naar = [r[0] for r in mg.execute(
            "SELECT p.proname FROM pg_proc p"
            " WHERE p.proname LIKE 'm45\\_%'"
            "   AND has_function_privilege('disponit_esgsveip',"
            "                              p.oid, 'EXECUTE')").fetchall()]
        kropp = mg.execute(
            "SELECT pg_get_functiondef(p.oid) FROM pg_proc p"
            " WHERE p.proname = 'm45_sveip_esg'").fetchone()[0]
    assert sorted(naar) == ["m45_sveip_esg"]
    ren = _STRENG.sub("''", kropp)
    for tabell in ("rapportperiode", "esgmaaling", "esgpaastand",
                   "esgrapport", "utslippsfaktor"):
        for form in (f"public.{tabell}", tabell):
            assert f"INSERT INTO {form}" not in ren, (tabell, form)
            assert f"UPDATE {form}" not in ren, (tabell, form)


@pg
def test_funntabellen_staar_i_m36s_katalog_med_lesretten():
    with _mig() as mg:
        rad = mg.execute(
            "SELECT modul, typekolonne, apenform FROM m36_funnregister"
            " WHERE relasjon='esgfunn'").fetchone()
        les = mg.execute(
            "SELECT count(*) FROM information_schema.table_privileges"
            " WHERE table_name='esgfunn' AND privilege_type='SELECT'"
            "   AND grantee='disponit_optimalisator_eier'").fetchone()[0]
    assert rad == ("m45_esg", "funntype", "apen_kolonne"), rad
    assert les == 1, "registrert uten lesrett — det ser komplett ut"


def test_sveipens_arbeidernokkel_er_modulens_egen():
    nokler = {}
    for fil in sorted((ROT / "platform" / "drift").glob("*sveip.py")):
        m = re.search(r"ARBEIDERNOKKEL = ([\d_]+)",
                      fil.read_text(encoding="utf-8"))
        if m:
            nokler.setdefault(m.group(1), []).append(fil.name)
    delte = {k: v for k, v in nokler.items() if len(v) > 1}
    assert delte == {}, f"delte arbeidernoekler: {delte}"


def test_driftsfila_navngir_sin_egen_jobb():
    sti = ROT / "deploy" / "staging" / "disponit-esgsveip.service"
    tj = sti.read_text(encoding="utf-8")
    beskrivelse = tj.split("Description=")[1][:130]
    assert "esg" in beskrivelse.lower() or "estimat" in beskrivelse.lower()
    for arvet in ("likviditet", "rangering", "EHF", "HMS", "møte",
                  "innhold", "telefoni", "samtale"):
        assert arvet not in beskrivelse, f"arvet ord: {arvet}"
    assert ("LoadCredential=DISPONIT_ESGSVEIP_URL:"
            "/etc/disponit/esgsveip/DISPONIT_ESGSVEIP_URL" in tj)
    kjorer = (ROT / "platform" / "drift"
              / "kjor_esgsveip.py").read_text(encoding="utf-8")
    assert "m45_sveip_esg()" in kjorer
    for arvet in ("m33_sveip_prognose", "m36_sveip_optimalisering",
                  "m7_sveip_moter", "m20_sveip_innhold",
                  "m43_sveip_telefoni"):
        assert arvet not in kjorer, f"arvet referanse: {arvet}"


def test_sveipen_rekker_aa_bli_ferdig_for_statussveipen_starter():
    """ET KLOKKESLETT ER IKKE EN REKKEFØLGE (132s lærdom).

    M-45 ER DEN SISTE BAK STATUSSVEIPEN, og det er denne PR-en som
    måler at hele stigen — nå ni sveip — går opp.
    """
    katalog = ROT / "deploy" / "staging"

    def _tid(fil):
        tekst = (katalog / fil).read_text(encoding="utf-8")
        kl = re.search(r"OnCalendar=\*-\*-\* (\d\d):(\d\d):00 UTC", tekst)
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
            f"{fil.name} kan holde paa til {slutt // 60}:{slutt % 60:02d}"
            f" mens statussveipen kan starte"
            f" {status_start // 60}:{status_start % 60:02d}")
    assert _tid("disponit-esgsveip.timer") == (13 * 60 + 20, 5)


def test_sveipens_dsn_star_i_ci():
    kjorer = ROT / "platform" / "drift" / "kjor_esgsveip.py"
    url = re.findall(r"DISPONIT_[A-Z0-9_]+_URL",
                     kjorer.read_text(encoding="utf-8"))
    assert url, "kjoereren leser ingen DSN"
    ventet = url[0].replace("DISPONIT_", "DISPONIT_TEST_", 1)
    ventet = ventet[:-len("_URL")] + "_DSN"
    ci = (ROT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert f"{ventet}:" in ci, f"{ventet} mangler i ci.yml"
    opp = (ROT / "deploy" / "staging" / "opp.sh").read_text(encoding="utf-8")
    assert url[0] in opp, f"{url[0]} mangler i opp.sh"


@pg
def test_hver_skrivedoer_legger_igjen_et_spor():
    skrivende = ("m45_sett_krav", "m45_registrer_kilde",
                 "m45_apne_periode", "m45_lukk_periode",
                 "m45_registrer_faktor", "m45_avvikle_faktor",
                 "m45_registrer_maaling", "m45_registrer_paastand",
                 "m45_sammenstill", "m45_lukk_funn")
    with _mig() as mg:
        uten = []
        for navn in skrivende:
            kropp = mg.execute(
                "SELECT pg_get_functiondef(p.oid) FROM pg_proc p"
                " WHERE p.proname=%s", (navn,)).fetchone()[0]
            if "m45_evidens" not in kropp:
                uten.append(navn)
    assert uten == [], f"skrivedoerer uten evidensspor: {uten}"


def test_fundamentet_navngir_modulen_og_migrasjonen():
    tekst = FUNDAMENT.read_text(encoding="utf-8")
    assert "136" in tekst and "M-45" in tekst
    assert MIGRASJON.exists()


# =====================================================================
# TO FUNN FRA CODERABBIT, OG PORTENE SOM HOLDER DEM FANGET.
# =====================================================================

@pg
def test_alle_fire_stedene_regner_estimatandelen_likt():
    """TO TALL SOM BEGGE SER RIKTIGE UT ER DET VERSTE UTFALLET.

    Andelen regnes fire steder: i `m45_bildet`, i
    `m45_perioderegister`, i `m45_sammenstill` og i sveipen. Første
    utkast glemte å filtrere bort ERSTATTEDE tall i `m45_bildet`, og da
    ville sammendraget vist en høyere estimatandel enn rapporten for
    samme periode.

    CodeRabbit fant den 5/9. Porten måler at de fire er enige.

    MUTASJONEN SOM DREPER DENNE: fjern `NOT EXISTS`-leddet i ett av
    de fire.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("enige")
        _krav(rt, t, terskel=10000)
        kid = _kilde(rt, t)
        pid = _periode(rt, t)
        fid = _faktor(rt, t, kid)
        # 1000 målt, og et estimat på 1000 som ERSTATTES.
        _maaling(rt, t, pid, fid, kid, mengde="1000")
        est, _ = _maaling(rt, t, pid, fid, kid, mengde="1000",
                          kategori="transport_diesel", estimat=True,
                          grunnlag="anslag basert paa fjoraaret")
        _maaling(rt, t, pid, fid, kid, mengde="1000",
                 kategori="transport_diesel", erstatter=est)
        _sett_kontekst(rt, t)
        bilde = rt.execute("SELECT * FROM m45_bildet(%s)", (t,)).fetchone()
        register = rt.execute("SELECT * FROM m45_perioderegister(%s,%s)",
                              (t, 10)).fetchone()
        rapport = rt.execute("SELECT * FROM m45_sammenstill(%s,%s,%s,%s)",
                             (t, uuid.uuid4(), pid, "u-kari")).fetchone()
        rt.commit()
        with _sv() as sv:
            sv.execute("SELECT * FROM m45_sveip_esg(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        sveipefunn = mg.execute(
            "SELECT count(*) FROM esgfunn WHERE tenant=%s"
            "   AND funntype='estimatandel_over_terskel_uavklart'",
            (t,)).fetchone()[0]
        mg.rollback()
    # ESTIMATET ER ERSTATTET: andelen er null, i alle fire.
    assert bilde[11] == 0, f"m45_bildet: {bilde[11]}"
    assert register[11] == 0, f"m45_perioderegister: {register[11]}"
    assert rapport[3] == 0, f"m45_sammenstill: {rapport[3]}"
    assert sveipefunn == 0, "sveipen saa en andel de andre ikke saa"


@pg
def test_periodens_status_leses_med_laas():
    """LÅS FØRST, LES ETTERPÅ (klynge 6s lærdom).

    Uten `FOR UPDATE` kan to transaksjoner begge lese `apen`, den ene
    lukke perioden og committe, og den andre skrive et tall inn i en
    lukket periode. Da ville et tall landet ETTER at rapporten var
    sammenstilt — og «hva sto i rapporten da noen leste den» hadde to
    svar.

    CodeRabbit fant den 5/9. Porten leser funksjonskroppen, fordi et
    ekte kappløp krever to samtidige transaksjoner og en tidsluke
    testen ikke kan garantere — men LÅSEN kan måles direkte.

    MUTASJONEN SOM DREPER DENNE: fjern `FOR UPDATE`.
    """
    with _mig() as mg:
        uten = []
        for navn in ("m45_registrer_maaling", "m45_registrer_paastand"):
            kropp = mg.execute(
                "SELECT pg_get_functiondef(p.oid) FROM pg_proc p"
                " WHERE p.proname=%s", (navn,)).fetchone()[0]
            i = kropp.find("FROM public.rapportperiode")
            assert i > 0, navn
            if "FOR UPDATE" not in kropp[i:i + 200]:
                uten.append(navn)
    assert uten == [], f"leser perioden uten laas: {uten}"


@pg
def test_en_lukket_periode_kan_ikke_lukkes_to_ganger():
    """Lukkingen er atomisk: `AND p.status = 'apen'` i UPDATE-en gjør
    at den andre kalleren får `false`, ikke en dobbel lukking med to
    tidspunkter."""
    with _to() as (rt, mg):
        t = _tenantnavn("dobbel")
        _krav(rt, t)
        pid = _periode(rt, t)
        _sett_kontekst(rt, t)
        assert rt.execute("SELECT m45_lukk_periode(%s,%s,%s)",
                          (t, pid, "u-kari")).fetchone()[0] is True
        rt.commit()
        _sett_kontekst(rt, t)
        assert rt.execute("SELECT m45_lukk_periode(%s,%s,%s)",
                          (t, pid, "u-per")).fetchone()[0] is False
        rt.commit()
        _sett_kontekst(mg, t)
        av = mg.execute("SELECT lukket_av FROM rapportperiode"
                        " WHERE tenant=%s AND periode_id=%s",
                        (t, pid)).fetchone()[0]
        mg.rollback()
    assert av == "u-kari", "den andre lukkingen overskrev den første"
