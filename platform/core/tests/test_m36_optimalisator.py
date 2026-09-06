"""M-36 bedriftsoptimalisator v1 (132) — KLYNGE 8s SISTE.

Grensen `m36-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

VAKTSETNINGEN ER EN ADVARSEL, OG DEN FORMER HALVE SUITEN:

  EN OPTIMALISATOR SOM FINNER AT DEN BESTE FORBEDRINGEN ER «GI M-36
  LOV TIL Å GJØRE X», ER IKKE ØDELAGT. DEN GJØR NØYAKTIG DET DEN BLE
  BEDT OM.

Derfor måler portene her ikke at modulen LAR VÆRE å utvide sin
fullmakt — de måler at den IKKE KAN. Fraværet av en dør og fraværet av
en rettighet er to halve sperrer; porten leser begge.

DEN ANDRE HALVDELEN MÅLER AT MODULEN SER HELE HUSET. 33 registre står
i `m36_funnregister`, 32 av dem leses, og tre koder «åpen» annerledes
enn de andre. En optimalisator som antok én form ville lest 30 av 32 og
meldt rent for de to siste — samme feilform som den blinde sveipen i
130. Registeret er derfor eksplisitt,
og en port faller når en ny `*funn`-tabell dukker opp utenfor det.

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

OPTIMALISATORSVEIP_DSN = os.environ.get(
    "DISPONIT_TEST_OPTIMALISATORSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "132_m36_optimalisator.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "optimalisator.js")
FUNDAMENT = ROT / "docs" / "KLYNGE8-FUNDAMENT.md"
MODULFILER = (
    ROT / "platform" / "core" / "api" / "optimalisator.py",
    ROT / "platform" / "drift" / "optimalisatorsveip.py",
    ROT / "platform" / "drift" / "kjor_optimalisatorsveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("optimaliseringskrav", "optimaliseringsmodell",
        "tiltaksforslag", "portefoljestopp", "rangering",
        "rangeringspost", "effektmaaling", "optimaliseringsfunn")

#: Tabellene modulen IKKE skal kunne skrive i, uansett hvordan
#: rangeringen faller ut.
POLICYTABELLER = ("policyer", "policyutkast", "policyaktivering")

_STRENG = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")


def _bare_kode(fil: Path, *, uten_strenger: bool = False) -> str:
    """Filens innhold uten kommentarer OG uten docstrings (m24-formen).

    PORTEN SKAL MÅLE HANDLINGEN, IKKE BEGRUNNELSEN. En port som leter
    i rå filtekst treffer kommentaren som forklarer HVORFOR et mønster
    er unngått — og her er kommentarene fulle av ordene «iverksett» og
    «fullmakt», nettopp fordi modulen ikke gjør noen av
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
    return koble(OPTIMALISATORSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m36-{merke}-{secrets.token_hex(4)}"


I_DAG = datetime.date.today()


def _dag(n: int) -> datetime.date:
    return I_DAG + datetime.timedelta(days=n)




METODE = ("Anslag sortert synkende; reversibelt foran irreversibelt"
          " ved likt anslag; deretter eldste forslag foerst.")


def _krav(c, tenant, *, horisont=12, maalefrist=14, maks=10,
          aktor="u-test", nokkel=None):
    _sett_kontekst(c, tenant)
    v = c.execute(
        "SELECT versjon FROM m36_sett_krav(%s,%s,%s,%s,%s,%s)",
        (tenant, horisont, maalefrist, maks, aktor,
         nokkel or secrets.token_hex(8))).fetchone()[0]
    c.commit()
    return v


def _modell(c, tenant, *, navn="Effektrangering", versjon="2026-01",
            metode=METODE, baselinje="ingen rangering",
            usikkerhet=2000, fra=None, til=None, aktor="u-test",
            modell_id=None):
    _sett_kontekst(c, tenant)
    mid = modell_id or uuid.uuid4()
    c.execute(
        "SELECT m36_registrer_modell(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant, mid, navn, versjon, metode, baselinje, usikkerhet,
         fra or _dag(-30), til, aktor))
    c.commit()
    return mid


def _tiltak(c, tenant, *, beskrivelse=None, grunnlagstype="regel",
            grunnlag="Ingen paalogging paa nitti dager",
            reversibilitet="reversibel", modul="m12_tilgang",
            funntype="ubrukt_tilgang", anslag=4500000,
            aktor="u-test", tiltak_id=None):
    _sett_kontekst(c, tenant)
    tid = tiltak_id or uuid.uuid4()
    c.execute(
        "SELECT m36_foresla_tiltak(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant, tid,
         beskrivelse or f"Tiltak {secrets.token_hex(4)} med nok tekst",
         grunnlagstype, grunnlag, reversibilitet, modul, funntype,
         anslag, aktor))
    c.commit()
    return tid


def _rangering(c, tenant, modell_id, *, aktor="u-test",
               rangering_id=None):
    _sett_kontekst(c, tenant)
    rid = rangering_id or uuid.uuid4()
    rad = c.execute("SELECT * FROM m36_rangere(%s,%s,%s,%s)",
                    (tenant, rid, modell_id, aktor)).fetchone()
    c.commit()
    return rid, rad


def _aldre_rangering(mg, tenant, rangering_id, dogn):
    """Fabrikerer alderen med append-only-vakten AVSLÅTT.

    At denne hjelpefunksjonen er nødvendig, er selv et bevis: det
    finnes ingen lovlig vei til å endre en avgitt rangering.
    """
    mg.execute("ALTER TABLE rangering DISABLE TRIGGER m36_evidensvakt")
    mg.execute("ALTER TABLE rangeringspost DISABLE TRIGGER"
               " m36_evidensvakt")
    _sett_kontekst(mg, tenant)
    mg.execute(
        "UPDATE rangering SET laget_dato = laget_dato - %s::int,"
        "       gjelder_til = gjelder_til - %s::int"
        " WHERE tenant=%s AND rangering_id=%s",
        (dogn, dogn, tenant, rangering_id))
    mg.execute(
        "UPDATE rangeringspost SET ukeslutt = ukeslutt - %s::int"
        " WHERE tenant=%s AND rangering_id=%s",
        (dogn, tenant, rangering_id))
    mg.execute("ALTER TABLE rangering ENABLE TRIGGER m36_evidensvakt")
    mg.execute("ALTER TABLE rangeringspost ENABLE TRIGGER"
               " m36_evidensvakt")
    mg.commit()


# ---------------------------------------------------------------------
# §0: hver invariant i `m36-v1` har minst én port.
# ---------------------------------------------------------------------

def test_grensen_er_registrert_og_hver_invariant_har_en_port():
    """§0-regelen, målt."""
    from manifestskjema import KRAVGRENSER
    inv = set(KRAVGRENSER["m36-v1"]["invarianter"])
    assert inv
    tekst = Path(__file__).read_text(encoding="utf-8")
    mangler = sorted(i for i in inv if i not in tekst)
    assert mangler == [], f"invarianter uten port: {mangler}"


# =====================================================================
# `modulen_utvidet_egen_fullmakt` — DEN VIKTIGSTE, OG DEN MÅLES SOM
# TO FRAVÆR.
# =====================================================================

@pg
def test_modulen_har_ingen_rettighet_paa_policytabellene():
    """FRAVÆR NUMMER ÉN: rettigheten finnes ikke.

    En optimalisator som fant at den beste forbedringen var «gi M-36
    lov til X», er ikke ødelagt — den gjør nøyaktig det den ble bedt
    om. Derfor skal den ikke kunne SKRIVE det noe sted.
    """
    with _mig() as mg:
        rader = mg.execute(
            "SELECT table_name, privilege_type FROM"
            " information_schema.table_privileges"
            " WHERE grantee='disponit_optimalisator_eier'"
            "   AND table_name = ANY(%s)",
            (list(POLICYTABELLER),)).fetchall()
    assert rader == [], f"modulrollen kan røre policytabellene: {rader}"


def test_ingen_dor_eller_flate_skriver_i_en_policy():
    """FRAVÆR NUMMER TO: døra finnes ikke.

    En rettighet uten en dør og en dør uten en rettighet er hver for
    seg en HALV sperre. Porten leser begge, og denne leser koden —
    uten kommentarer og strenger, så den måler mønsteret og ikke
    forklaringen på hvorfor mønsteret er unngått.
    """
    for fil in (MIGRASJON, *MODULFILER, FLATE):
        kode = _bare_kode(fil, uten_strenger=True).lower()
        for tabell in POLICYTABELLER:
            for verb in ("insert into " + tabell, "update " + tabell,
                         "delete from " + tabell):
                assert verb not in kode, f"{fil.name}: {verb}"


@pg
def test_modulen_leser_andre_registre_og_kan_ikke_skrive_i_dem():
    """`modulen_overstyrte_en_annen_moduls_grense`, målt.

    En optimalisator som kunne skrive i en annen moduls funnregister
    ville kunnet «lukke» funnene som talte mot dens egen rangering —
    og det er den ene feilen ingen ville oppdaget, fordi rangeringen
    da alltid ville sett velbegrunnet ut.
    """
    with _mig() as mg:
        registre = [r[0] for r in mg.execute(
            "SELECT relasjon FROM m36_funnregister").fetchall()]
        rader = mg.execute(
            "SELECT DISTINCT privilege_type FROM"
            " information_schema.table_privileges"
            " WHERE grantee='disponit_optimalisator_eier'"
            "   AND table_name = ANY(%s)", (registre,)).fetchall()
    assert registre, "funnregisteret er tomt"
    assert [r[0] for r in rader] == ["SELECT"], (
        f"modulrollen har mer enn SELECT på registrene: {rader}")


def test_ingen_driftsfil_kan_snakke_ut():
    """Gjerdet står i koden, ikke i en kommentar."""
    for fil in MODULFILER:
        kode = _bare_kode(fil, uten_strenger=True)
        for modul in ("httpx", "requests", "socket", "urllib",
                      "smtplib", "http.client"):
            assert f"import {modul}" not in kode, f"{fil.name}: {modul}"


# =====================================================================
# `modulen_iverksatte_tiltak`.
# =====================================================================

@pg
def test_statussettet_har_ingen_iverksatt():
    """V1-DOMMEN, gjort urepresenterbar.

    Et tiltak kan bli vurdert eller avvist. Utførelsen går gjennom
    modulen som EIER handlingen, av et menneske, på M-41s
    policykontrollerte vei — og den veien vet ikke at denne tabellen
    finnes.
    """
    with _mig() as mg:
        sjekk = mg.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid='tiltaksforslag'::regclass"
            "   AND conname='tiltaksforslag_status_lukket'"
        ).fetchone()[0]
    assert "iverksatt" not in sjekk, sjekk
    for verdi in ("foreslatt", "vurdert", "avvist"):
        assert verdi in sjekk


@pg
def test_dora_nekter_en_status_utenfor_vurderingene():
    """Døra nekter FØR CHECKen, slik at kalleren får en melding den
    kan handle på i stedet for en `CheckViolation`."""
    with _to() as (rt, mg):
        t = _tenantnavn("iverksett")
        _krav(rt, t)
        tid = _tiltak(rt, t)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT m36_vurder_tiltak(%s,%s,%s,%s,%s)",
                       (t, tid, "iverksatt", "vi gjorde det", "u-test"))
        rt.rollback()
        del mg


def test_koden_har_ingen_iverksettelsesvei():
    """Porten leser koden UTEN kommentarer og strenger.

    128s lærdom: en tidligere port traff LOCALE-NØKKELEN som SIER at
    modulen ikke iverksetter — altså forklaringen, ikke mønsteret.
    """
    for fil in (MIGRASJON, *MODULFILER, FLATE):
        kode = _bare_kode(fil, uten_strenger=True).lower()
        for ord_ in ("iverksett", "utfor_tiltak", "gjennomfor_tiltak"):
            assert ord_ not in kode, f"{fil.name} inneholder «{ord_}»"


# =====================================================================
# `korrelasjon_presentert_som_aarsak` OG `tiltak_uten_grunnlagstype`.
# =====================================================================

@pg
def test_tiltak_uten_grunnlagstype_er_urepresenterbart():
    """Kolonnen er NOT NULL med et lukket sett og INGEN standardverdi.

    En standardverdi ville gjort «vi vet ikke» til «regel» i
    stillhet — og det er nøyaktig påstanden vaktsetningen forbyr.
    """
    with _mig() as mg:
        rad = mg.execute(
            "SELECT is_nullable, column_default FROM"
            " information_schema.columns"
            " WHERE table_name='tiltaksforslag'"
            "   AND column_name='grunnlagstype'").fetchone()
        sjekk = mg.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid='tiltaksforslag'::regclass"
            "   AND conname='tiltaksforslag_grunnlagstype_lukket'"
        ).fetchone()[0]
    assert rad == ("NO", None), f"grunnlagstype: {rad}"
    for verdi in ("korrelasjon", "eksperiment", "regel"):
        assert verdi in sjekk


@pg
def test_grunnlagstypen_kopieres_inn_i_rangeringen_og_fryses():
    """`korrelasjon_presentert_som_aarsak` handler om hva vi PÅSTO.

    Et join ville gitt dagens verdi, ikke den som gjaldt da
    rangeringen ble avgitt. Og forslagets innhold er FROSSET, så
    `grunnlagstype` kan ikke skrives om fra `korrelasjon` til `regel`
    i ettertid.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("frosset")
        _krav(rt, t)
        mid = _modell(rt, t)
        tid = _tiltak(rt, t, grunnlagstype="korrelasjon")
        rid, _ = _rangering(rt, t, mid)
        _sett_kontekst(rt, t)
        post = rt.execute("SELECT * FROM m36_rangeringen(%s,%s)",
                          (t, rid)).fetchone()
        assert post[6] == "korrelasjon", post[6]
        rt.rollback()
        # …og forslaget kan ikke skrives om.
        _sett_kontekst(mg, t)
        mg.execute("SET LOCAL ROLE disponit_optimalisator_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            mg.execute("UPDATE tiltaksforslag SET grunnlagstype='regel'"
                       " WHERE tenant=%s AND tiltak_id=%s", (t, tid))
        mg.rollback()


@pg
def test_lesedora_gir_aldri_et_tiltak_uten_sin_grunnlagstype():
    """Vaktsetningen håndhevet der den faktisk kan brytes: i det som
    forlater basen. En flate kan velge å ikke VISE grunnlagstypen, men
    den kan ikke få et svar der den mangler."""
    with _to() as (rt, mg):
        t = _tenantnavn("lesedor")
        _krav(rt, t)
        mid = _modell(rt, t)
        for gt in ("korrelasjon", "eksperiment", "regel"):
            _tiltak(rt, t, grunnlagstype=gt)
        rid, _ = _rangering(rt, t, mid)
        _sett_kontekst(rt, t)
        poster = rt.execute("SELECT * FROM m36_rangeringen(%s,%s)",
                            (t, rid)).fetchall()
        rt.rollback()
        del mg
    assert len(poster) == 3
    assert all(p[6] in ("korrelasjon", "eksperiment", "regel")
               for p in poster), poster


@pg
def test_sveipen_ser_korrelasjon_alene_paa_topp():
    """Modellen FÅR rangere på korrelasjon — men at det ØVERSTE
    forslaget gjør det, skal noen se.

    Funnet lukkes av at toppen endrer seg, ikke av et klikk.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("topp")
        _krav(rt, t)
        mid = _modell(rt, t)
        _tiltak(rt, t, grunnlagstype="korrelasjon", anslag=9000000)
        _tiltak(rt, t, grunnlagstype="regel", anslag=1000000)
        rid, _ = _rangering(rt, t, mid)
        with _sv() as sv:
            sv.execute("SELECT * FROM m36_sveip_optimalisering(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        funn = mg.execute(
            "SELECT referanse FROM optimaliseringsfunn WHERE tenant=%s"
            "   AND funntype='korrelasjon_alene_paa_topp' AND apen",
            (t,)).fetchall()
        assert funn == [(str(rid),)], f"toppen ble ikke sett: {funn}"
        mg.rollback()

        # EN NY RANGERING DER TOPPEN ER ET EKSPERIMENT LUKKER FUNNET.
        _tiltak(rt, t, grunnlagstype="eksperiment", anslag=99000000)
        rid2, _ = _rangering(rt, t, mid)
        with _sv() as sv:
            sv.execute("SELECT * FROM m36_sveip_optimalisering(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        apne = mg.execute(
            "SELECT count(*) FROM optimaliseringsfunn WHERE tenant=%s"
            "   AND funntype='korrelasjon_alene_paa_topp' AND apen",
            (t,)).fetchone()[0]
        mg.rollback()
        del rid2
    assert apne == 0, "funnet ble stående etter at toppen endret seg"


# =====================================================================
# `tiltak_uten_reversibilitet` OG `portefoljestopp_uten_virkning`.
# =====================================================================

@pg
def test_tiltak_uten_reversibilitet_er_urepresenterbart():
    """Gjort UMULIG, ikke oppdaget.

    Funntypen `tiltak_uten_reversibilitet` står i det lukkede settet
    fordi invarianten heter det — men sveipen reiser den ALDRI, og at
    den aldri reises ER beviset på at vernet ligger i datamodellen og
    ikke i en nattlig sjekk. Porten under måler nettopp det.
    """
    with _mig() as mg:
        rad = mg.execute(
            "SELECT is_nullable, column_default FROM"
            " information_schema.columns"
            " WHERE table_name='tiltaksforslag'"
            "   AND column_name='reversibilitet'").fetchone()
    assert rad == ("NO", None), f"reversibilitet: {rad}"


@pg
def test_sveipen_reiser_aldri_tiltak_uten_reversibilitet():
    """Funntypen finnes i settet, og skal aldri kunne oppstå.

    En sveip som KUNNE reise den, ville betydd at det finnes en vei
    inn i tabellen uten reversibilitet — altså at `NOT NULL` ikke
    holder.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("aldri")
        _krav(rt, t)
        mid = _modell(rt, t)
        _tiltak(rt, t)
        _rangering(rt, t, mid)
        with _sv() as sv:
            sv.execute("SELECT * FROM m36_sveip_optimalisering(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        n = mg.execute(
            "SELECT count(*) FROM optimaliseringsfunn"
            " WHERE funntype='tiltak_uten_reversibilitet'"
        ).fetchone()[0]
        mg.rollback()
        # …OG DET FINNES INGEN KODE SOM KAN SKRIVE DEN.
        kode = _bare_kode(MIGRASJON)
        reisinger = kode.count("'tiltak_uten_reversibilitet'")
        del rt
    assert n == 0, "funnet ble reist — NOT NULL holder ikke"
    assert reisinger == 1, (
        "funntypen nevnes i mer enn det lukkede settet — noe kan reise"
        f" den ({reisinger} treff)")


@pg
def test_portefoljestoppen_hindrer_en_ny_rangering():
    """`portefoljestopp_uten_virkning`, målt.

    Stoppen stanser M-36, ikke porteføljen — å stanse en annen modul
    ville vært `modulen_overstyrte_en_annen_moduls_grense`. Men
    virkningen er ekte: med aktiv stopp blir ingen ny rangering til.

    MUTASJONEN SOM DREPER DENNE: fjern `IF m36_stopp_aktiv(...)`-
    grenen i `m36_rangere`.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("stopp")
        _krav(rt, t)
        mid = _modell(rt, t)
        _tiltak(rt, t)
        _sett_kontekst(rt, t)
        sid = uuid.uuid4()
        aktiv = rt.execute(
            "SELECT aktiv FROM m36_sett_stopp(%s,%s,%s,%s)",
            (t, sid, "Vi omorganiserer og vil ikke ha nye forslag",
             "u-test")).fetchone()[0]
        rt.commit()
        assert aktiv is True

        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT m36_rangere(%s,%s,%s,%s)",
                       (t, uuid.uuid4(), mid, "u-test"))
        rt.rollback()

        # …OG ETTER OPPHEVING GÅR DEN IGJEN.
        _sett_kontekst(rt, t)
        rt.execute("SELECT m36_opphev_stopp(%s,%s,%s,%s)",
                   (t, sid, "Omorganiseringen er ferdig naa", "u-test"))
        rt.commit()
        _, rad = _rangering(rt, t, mid)
        del mg
    assert rad[1] == 1, f"rangeringen ble ikke laget: {rad}"


@pg
def test_stoppen_kan_ikke_slettes_og_bare_oppheves_en_gang():
    """En stopp som kunne slettes er en stopp ingen kan bevise sto —
    og det er nettopp da spørsmålet stilles."""
    with _to() as (rt, mg):
        t = _tenantnavn("stoppvakt")
        _krav(rt, t)
        _sett_kontekst(rt, t)
        sid = uuid.uuid4()
        rt.execute("SELECT m36_sett_stopp(%s,%s,%s,%s)",
                   (t, sid, "En god nok begrunnelse her", "u-test"))
        rt.commit()
        _sett_kontekst(mg, t)
        mg.execute("SET LOCAL ROLE disponit_optimalisator_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            mg.execute("DELETE FROM portefoljestopp WHERE tenant=%s",
                       (t,))
        mg.rollback()
        _sett_kontekst(rt, t)
        rt.execute("SELECT m36_opphev_stopp(%s,%s,%s,%s)",
                   (t, sid, "Vi tar den av igjen naa", "u-test"))
        rt.commit()
        # EN ANDRE OPPHEVING ER ET GJENSPILL, IKKE EN NY HANDLING.
        _sett_kontekst(rt, t)
        rad = rt.execute(
            "SELECT * FROM m36_opphev_stopp(%s,%s,%s,%s)",
            (t, sid, "Vi tar den av igjen naa", "u-test")).fetchone()
        rt.commit()
    assert rad[1] is False and rad[2] is False


@pg
def test_bare_en_aktiv_stopp_om_gangen():
    """To ville gjort «er porteføljen stoppet?» til et spørsmål med to
    svar."""
    with _to() as (rt, mg):
        t = _tenantnavn("tostopp")
        _krav(rt, t)
        _sett_kontekst(rt, t)
        rt.execute("SELECT m36_sett_stopp(%s,%s,%s,%s)",
                   (t, uuid.uuid4(), "Foerste stopp med begrunnelse",
                    "u-test"))
        rt.commit()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.UniqueViolation):
            rt.execute("SELECT m36_sett_stopp(%s,%s,%s,%s)",
                       (t, uuid.uuid4(), "Andre stopp med begrunnelse",
                        "u-test"))
        rt.rollback()
        del mg


@pg
def test_sveipen_ser_en_stopp_som_blir_staaende():
    """En stopp som blir stående uten at noen tar stilling, er en
    modul som er slått av i stillhet.

    Dette er det ENESTE av M-36s tre funn et menneske kan lukke, og
    det er riktig: «vi vet, den skal stå» er en legitim beslutning med
    et navn på.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("staaende")
        _krav(rt, t, maalefrist=14)
        _sett_kontekst(rt, t)
        sid = uuid.uuid4()
        rt.execute("SELECT m36_sett_stopp(%s,%s,%s,%s)",
                   (t, sid, "Vi venter paa styrebehandling", "u-test"))
        rt.commit()
        # Eldre stoppen forbi målefristen.
        # MIGRATOR EIER TABELLEN, ikke modulrollen: `ALTER TABLE ...
        # DISABLE TRIGGER` krever eierskap, og en `SET LOCAL ROLE` til
        # modulrollen ville derfor NEKTET. At den nekter er selv et
        # bevis: modulrollen kan ikke ta av sin egen vakt.
        _sett_kontekst(mg, t)
        mg.execute("ALTER TABLE portefoljestopp DISABLE TRIGGER"
                   " m36_stoppvakt")
        mg.execute("UPDATE portefoljestopp"
                   "   SET satt_ts = satt_ts - interval '40 days'"
                   " WHERE tenant=%s", (t,))
        mg.execute("ALTER TABLE portefoljestopp ENABLE TRIGGER"
                   " m36_stoppvakt")
        mg.commit()
        with _sv() as sv:
            sv.execute("SELECT * FROM m36_sveip_optimalisering(500)")
            sv.commit()
        _sett_kontekst(rt, t)
        funn = [f for f in rt.execute(
            "SELECT * FROM m36_optimaliseringsfunn(%s,%s)",
            (t, 100)).fetchall()
            if f[1] == "stopp_staar_uten_oppheving"]
        rt.rollback()
        assert funn, "en stopp som blir stående ble ikke sett"
        assert funn[0][9] is True, "et menneske kan ikke lukke den"
        _sett_kontekst(rt, t)
        apen = rt.execute(
            "SELECT apen FROM m36_lukk_funn(%s,%s,%s,%s)",
            (t, funn[0][0], "vi vet, den skal staa",
             "u-test")).fetchone()[0]
        rt.commit()
    assert apen is False


# =====================================================================
# PROGNOSEFORMEN, ARVET FRA M-33: horisont, modellversjon, intervall,
# måling. Fire av M-36s invarianter er de samme fire.
# =====================================================================

@pg
def test_prognose_uten_horisont_er_urepresenterbar():
    """`horisont_uker` og `gjelder_til` er NOT NULL, og de henger
    sammen. Et tiltak uten en dato det kan etterprøves mot er et
    forslag ingen kan si var feil."""
    with _mig() as mg:
        n = mg.execute(
            "SELECT count(*) FROM information_schema.columns"
            " WHERE table_name='rangering'"
            "   AND column_name IN ('horisont_uker','gjelder_til')"
            "   AND is_nullable='NO'").fetchone()[0]
        sjekk = mg.execute(
            "SELECT count(*) FROM pg_constraint"
            " WHERE conrelid='rangering'::regclass"
            "   AND conname='rangering_horisont_stemmer'").fetchone()[0]
    assert n == 2 and sjekk == 1


@pg
def test_prognose_uten_modellversjon_er_urepresenterbar():
    """Snapshotet, ikke en fremmednøkkel til noe som kan endres."""
    with _to() as (rt, mg):
        t = _tenantnavn("modellversjon")
        _krav(rt, t)
        mid = _modell(rt, t, versjon="v-frosset")
        _tiltak(rt, t)
        rid, _ = _rangering(rt, t, mid)
        _sett_kontekst(rt, t)
        rt.execute("SELECT m36_avvikle_modell(%s,%s,%s,%s)",
                   (t, mid, _dag(0), "u-test"))
        rt.commit()
        _sett_kontekst(mg, t)
        v = mg.execute(
            "SELECT modellversjon FROM rangering"
            " WHERE tenant=%s AND rangering_id=%s",
            (t, rid)).fetchone()[0]
        mg.rollback()
    assert v == "v-frosset", "versjonen fulgte modellen i stedet for raden"


@pg
def test_prognose_uten_intervall_er_urepresenterbar():
    """`nedre` og `ovre` er NOT NULL — OG ALDRI LIKE.

    131s lærdom, tatt med fra fødselen: `NOT NULL` alene er ikke nok,
    fordi et bånd med bredde null er et PUNKT som later som det er et
    intervall. CHECKen krever `nedre < ovre`.

    Porten bruker et anslag på ÉN øre, der 20 % usikkerhet runder til
    0 — nøyaktig tilfellet som ville gitt et degenerert bånd.
    """
    with _mig() as mg:
        n = mg.execute(
            "SELECT count(*) FROM pg_constraint"
            " WHERE conrelid='rangeringspost'::regclass"
            "   AND conname='rangeringspost_intervall_har_bredde'"
        ).fetchone()[0]
    assert n == 1, "CHECKen som forbyr bredde null mangler"
    with _to() as (rt, mg):
        t = _tenantnavn("intervall")
        _krav(rt, t)
        mid = _modell(rt, t, usikkerhet=2000)
        _tiltak(rt, t, anslag=1)
        rid, _ = _rangering(rt, t, mid)
        _sett_kontekst(rt, t)
        post = rt.execute("SELECT * FROM m36_rangeringen(%s,%s)",
                          (t, rid)).fetchone()
        rt.rollback()
        del mg
    assert post[4] < post[3] < post[5], (
        f"baandet har bredde null: {post[4]}-{post[5]}")


@pg
def test_prognose_uten_maaling_reises_og_lukkes_bare_av_maalingen():
    """Klyngens funn, og den eneste veien ut av det."""
    with _to() as (rt, mg):
        t = _tenantnavn("umaalt")
        _krav(rt, t, horisont=1, maalefrist=14)
        mid = _modell(rt, t)
        _tiltak(rt, t)
        rid, _ = _rangering(rt, t, mid)
        _aldre_rangering(mg, t, rid, 60)
        with _sv() as sv:
            sv.execute("SELECT * FROM m36_sveip_optimalisering(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        apne = mg.execute(
            "SELECT count(*) FROM optimaliseringsfunn WHERE tenant=%s"
            "   AND funntype='rangering_uten_maaling' AND apen",
            (t,)).fetchone()[0]
        mg.rollback()
        assert apne == 1, f"horisonten er passert, {apne} funn"

        _sett_kontekst(rt, t)
        rt.execute("SELECT m36_registrer_effekt(%s,%s,%s,%s,%s)",
                   (t, rid, 1, 3000000, "u-test"))
        rt.commit()
        with _sv() as sv:
            sv.execute("SELECT * FROM m36_sveip_optimalisering(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        etter = mg.execute(
            "SELECT count(*) FILTER (WHERE apen),"
            "       count(*) FILTER (WHERE NOT apen AND"
            "                        lukket_av='m36_sveip')"
            "  FROM optimaliseringsfunn WHERE tenant=%s"
            "   AND funntype='rangering_uten_maaling'",
            (t,)).fetchone()
        mg.rollback()
    assert etter == (0, 1), f"lukkingen fulgte ikke målingen: {etter}"


@pg
def test_en_horisont_som_ikke_er_passert_kan_ikke_males():
    """Målingen er ukorrigerbar, så et delresultat registrert som
    endelig ville stått for alltid (130s dom)."""
    with _to() as (rt, mg):
        t = _tenantnavn("forfrist")
        _krav(rt, t)
        mid = _modell(rt, t)
        _tiltak(rt, t)
        rid, _ = _rangering(rt, t, mid)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT m36_registrer_effekt(%s,%s,%s,%s,%s)",
                       (t, rid, 1, 1000, "u-test"))
        rt.rollback()
        del mg


@pg
def test_en_gjentatt_effektmaaling_svarer_med_raden():
    """131s lærdom, innebygd fra fødselen.

    En klient som mistet svaret og prøver igjen får den lagrede raden,
    ikke 400 på en skriving som lyktes. Et ANNET tall er fortsatt en
    feil.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("effektgjenspill")
        _krav(rt, t, horisont=1)
        mid = _modell(rt, t)
        _tiltak(rt, t)
        rid, _ = _rangering(rt, t, mid)
        _aldre_rangering(mg, t, rid, 60)
        _sett_kontekst(rt, t)
        a = rt.execute(
            "SELECT * FROM m36_registrer_effekt(%s,%s,%s,%s,%s)",
            (t, rid, 1, 3000000, "u-test")).fetchone()
        rt.commit()
        _sett_kontekst(rt, t)
        b = rt.execute(
            "SELECT * FROM m36_registrer_effekt(%s,%s,%s,%s,%s)",
            (t, rid, 1, 3000000, "u-test")).fetchone()
        rt.commit()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.UniqueViolation):
            rt.execute("SELECT m36_registrer_effekt(%s,%s,%s,%s,%s)",
                       (t, rid, 1, 9, "u-test"))
        rt.rollback()
        del mg
    assert a[3] is True and b[3] is False
    assert a[:3] == b[:3]


@pg
def test_treffet_regnes_av_bandet_ikke_av_kalleren():
    """Hadde kalleren fått si «ja, dette traff», ville målingen vært
    en karakter modulen ga seg selv."""
    with _to() as (rt, mg):
        t = _tenantnavn("treff")
        _krav(rt, t, horisont=1, maks=2)
        mid = _modell(rt, t)
        _tiltak(rt, t, anslag=4500000)
        _tiltak(rt, t, anslag=1000000)
        rid, _ = _rangering(rt, t, mid)
        _aldre_rangering(mg, t, rid, 60)
        _sett_kontekst(rt, t)
        post = rt.execute("SELECT * FROM m36_rangeringen(%s,%s)",
                          (t, rid)).fetchone()
        ned, opp = post[4], post[5]
        inni = rt.execute(
            "SELECT innenfor_intervall FROM"
            " m36_registrer_effekt(%s,%s,%s,%s,%s)",
            (t, rid, 1, (ned + opp) // 2, "u-test")).fetchone()[0]
        utenfor = rt.execute(
            "SELECT innenfor_intervall FROM"
            " m36_registrer_effekt(%s,%s,%s,%s,%s)",
            (t, rid, 2, 99000000, "u-test")).fetchone()[0]
        rt.commit()
        del mg
    assert inni is True and utenfor is False


# =====================================================================
# `rangering_overskrevet` OG `tenantlekkasje_i_tiltaksregister`.
# =====================================================================

@pg
def test_rangering_overskrevet_er_umulig():
    """En rangering er en PÅSTAND AVGITT PÅ ET TIDSPUNKT.

    Kunne den redigeres, ville enhver effektmåling vært en
    sammenligning mot noe som er endret etterpå — og «tok vi feil?»
    ville alltid hatt svaret nei.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("frossen")
        _krav(rt, t)
        mid = _modell(rt, t)
        _tiltak(rt, t)
        rid, _ = _rangering(rt, t, mid)
        for setning in (
                "UPDATE rangering SET horisont_uker=99",
                "DELETE FROM rangering",
                "UPDATE rangeringspost SET forventet_effekt_ore=0",
                "DELETE FROM rangeringspost"):
            # Konteksten settes på nytt for hver runde: `SET LOCAL`
            # dør med transaksjonen, og uten den ville radvakten
            # skjult raden — da hadde setningen truffet null rader og
            # porten vært grønn uten at vakten ble prøvd.
            _sett_kontekst(mg, t)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                mg.execute(setning + " WHERE tenant=%s AND"
                           " rangering_id=%s", (t, rid))
            mg.rollback()


@pg
def test_tenantlekkasje_i_tiltaksregister_er_umulig():
    """FORCE ROW LEVEL SECURITY på alle åtte.

    `FORCE` og ikke bare `ENABLE`: uten den er EIEREN unntatt, og da
    er invarianten en invariant uten håndhevelse.
    """
    with _mig() as mg:
        rader = mg.execute(
            "SELECT relname, relrowsecurity, relforcerowsecurity"
            "  FROM pg_class WHERE relname = ANY(%s)",
            (list(EGNE),)).fetchall()
    assert len(rader) == len(EGNE)
    for navn, pa, tvang in rader:
        assert pa and tvang, f"{navn}: rls={pa} force={tvang}"


@pg
def test_en_tenant_ser_ikke_en_annens_rangering():
    """Radvakten, målt og ikke bare erklært."""
    with _to() as (rt, mg):
        a, b = _tenantnavn("a"), _tenantnavn("b")
        _krav(rt, a)
        mid = _modell(rt, a)
        _tiltak(rt, a)
        rid, _ = _rangering(rt, a, mid)
        _sett_kontekst(rt, b)
        n = rt.execute(
            "SELECT count(*) FROM m36_rangeringsregister(%s,%s)",
            (b, 100)).fetchone()[0]
        poster = rt.execute(
            "SELECT count(*) FROM m36_rangeringen(%s,%s)",
            (b, rid)).fetchone()[0]
        tiltak = rt.execute("SELECT count(*) FROM m36_tiltakene(%s,%s)",
                            (b, 100)).fetchone()[0]
        rt.rollback()
        del mg
    assert (n, poster, tiltak) == (0, 0, 0)


# =====================================================================
# FUNNREGISTERET — MODULENS VIKTIGSTE «FUNKSJON», OG DEN ER EN LISTE.
# =====================================================================

@pg
def test_ingen_funntabell_faller_utenfor_registeret():
    """DEN NESTE MODULENS FUNN KAN IKKE FALLE UT AV SYNET I STILLHET.

    33 registre står i `m36_funnregister`, og 32 av dem leses. En
    optimalisator som antok ÉN form ville lest 30 av 32 og meldt rent
    for de to siste — samme feilform som den blinde sveipen i 130: den
    ser ut som en vellykket kjøring.

    Denne porten faller når en ny modul legger til sitt funnregister
    uten å skrive det inn i `m36_funnregister`. Det er meningen: den
    som bygger modul 37 skal måtte ta stilling til om optimalisatoren
    skal se den.
    """
    with _mig() as mg:
        mg.execute("SET LOCAL ROLE disponit_optimalisator_eier")
        udekket = [r[0] for r in mg.execute(
            "SELECT * FROM m36_udekkede_registre()").fetchall()]
        mg.rollback()
    assert udekket == [], (
        "funnregistre M-36 ikke vet om — legg dem i m36_funnregister"
        f" eller si eksplisitt hvorfor de ikke leses: {udekket}")


@pg
def test_registeret_koder_de_tre_avvikende_formene():
    """TRE AV DE 33 KODER «ÅPEN» ANNERLEDES, og det ble målt mot basen
    før første linje kode.

    `kvalitetsfunn` (M-3) har ingen `apen` — hver rad ER et åpent
    funn. `retensjonsfunn` bruker `lukket_maaling IS NULL`. Og M-55
    skiller OBSERVASJON fra VARSEL, så `merkevarefunn` står i
    registeret for å være dekket, men leses ikke.
    """
    with _mig() as mg:
        rader = dict(mg.execute(
            "SELECT relasjon, apenform FROM m36_funnregister"
            " WHERE apenform <> 'apen_kolonne'").fetchall())
    assert rader.get("kvalitetsfunn") == "alle_rader_apne", rader
    assert rader.get("retensjonsfunn") == "lukket_maaling_null", rader
    assert rader.get("merkevarefunn") == "alle_rader_apne", rader


@pg
def test_rangeringen_nekter_naar_et_register_er_usynlig():
    """En rangering laget mens et register var usynlig ville hvilt på
    et grunnlag ingen visste var ufullstendig — og den ville sett like
    komplett ut som de riktige.

    MUTASJONEN SOM DREPER DENNE: fjern `v_udekket`-sjekken i
    `m36_rangere`.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("usynlig")
        _krav(rt, t)
        mid = _modell(rt, t)
        _tiltak(rt, t)
        # Fjern ett register fra listen og se at døra stopper.
        fjernet = mg.execute(
            "DELETE FROM m36_funnregister WHERE relasjon='tollfunn'"
            " RETURNING modul, typekolonne, apenform, begrunnelse"
        ).fetchone()
        mg.commit()
        try:
            _sett_kontekst(rt, t)
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                rt.execute("SELECT m36_rangere(%s,%s,%s,%s)",
                           (t, uuid.uuid4(), mid, "u-test"))
            rt.rollback()
        finally:
            mg.execute(
                "INSERT INTO m36_funnregister (relasjon, modul,"
                " typekolonne, apenform, begrunnelse)"
                " VALUES ('tollfunn',%s,%s,%s,%s)", fjernet)
            mg.commit()


@pg
def test_rangeringen_baerer_hvor_bredt_den_saa():
    """`grunnlag_registre` TELLER REGISTRE LEST, ikke registre med
    funn.

    Et register uten åpne funn er fortsatt et register vi har sett i.
    Å telle bort det ville gjort et rent hus til et smalt grunnlag —
    og «var grunnlaget komplett?» ville fått feil svar.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("bredde")
        _krav(rt, t)
        mid = _modell(rt, t)
        _tiltak(rt, t)
        rid, rad = _rangering(rt, t, mid)
        _sett_kontekst(mg, t)
        forventet = mg.execute(
            "SELECT count(*) FROM m36_funnregister fr"
            " WHERE fr.relasjon <> 'merkevarefunn'").fetchone()[0]
        mg.rollback()
        del rid
    assert rad[3] == forventet, (
        f"rangeringen sier {rad[3]} registre, huset har {forventet}")
    assert rad[2] == 0, "en tom tenant hadde åpne funn"


@pg
def test_et_tiltak_maa_peke_paa_et_register_modulen_leser():
    """Et forslag som pekte på et register modulen ikke leser, kunne
    ikke spores tilbake til en måling."""
    with _to() as (rt, mg):
        t = _tenantnavn("ukjentkilde")
        _krav(rt, t)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute(
                "SELECT m36_foresla_tiltak"
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), "Et tiltak med nok beskrivelse her",
                 "regel", "En god nok begrunnelse her", "reversibel",
                 "m99_finnes_ikke", "noe", 1000, "u-test"))
        rt.rollback()
        del mg


@pg
def test_apne_funn_leser_ogsaa_de_avvikende_formene():
    """DEN BLINDE OPTIMALISATOREN, MÅLT.

    Porten legger et funn i `kvalitetsfunn` — registeret UTEN
    `apen`-kolonne — og krever at M-36 ser det. En modul som antok
    husets standardform ville ikke gjort det, og ville meldt rent.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("avvikende")
        _krav(rt, t)
        rid = f"m36.{secrets.token_hex(4)}"
        mg.execute("SET LOCAL ROLE disponit_kvalitet_eier")
        _sett_kontekst(mg, t)
        mg.execute(
            "INSERT INTO kvalitetsregel (regel_id, relasjon, kolonne,"
            " regeltype, alvorlighet, terskel_andel, begrunnelse)"
            " VALUES (%s,'kvalitetsfunn','regel_id','ikke_tom','lav',"
            "         0.0,'port for m36') ON CONFLICT DO NOTHING",
            (rid,))
        mg.execute(
            "INSERT INTO kvalitetsfunn (tenant, regel_id, funntype,"
            " forst_sett_kjoring, sist_sett_kjoring, ganger_sett,"
            " detaljer)"
            " VALUES (%s,%s,'terskel_overskredet',%s,%s,1,'{}')",
            (t, rid, uuid.uuid4(), uuid.uuid4()))
        mg.commit()
        _sett_kontekst(rt, t)
        sett_ = rt.execute("SELECT * FROM m36_apne_funn(%s)",
                           (t,)).fetchall()
        rt.rollback()
    kvalitet = [r for r in sett_ if r[1] == "kvalitetsfunn"]
    assert kvalitet == [("m3_datakvalitet", "kvalitetsfunn",
                         "terskel_overskredet", 1)], (
        "M-36 så ikke funnet i registeret uten `apen`-kolonne:"
        f" {kvalitet}")


# =====================================================================
# SP-7, SVEIPEN OG DRIFTA.
# =====================================================================

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
            " WHERE p.proname LIKE 'm36\\_%'"
            "   AND has_function_privilege("
            "         'disponit_optimalisatorsveip', p.oid, 'EXECUTE')"
        ).fetchall()
    assert sorted(r[0] for r in rader) == ["m36_sveip_optimalisering"]


@pg
def test_sveipen_ser_ingenting_uten_kryss_tenant_policyen():
    """130s LÆRDOM. En sveip som spurte på tvers uten
    `disponit.tenant` ville sett NULL RADER og rapportert null funn —
    med grønn exit-kode."""
    with _mig() as mg:
        rad = mg.execute(
            "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy"
            " WHERE polrelid='optimaliseringskrav'::regclass"
            "   AND polname='m36_sveip_tenantliste'").fetchone()
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
                "SELECT * FROM m36_sveip_optimalisering(500)"
            ).fetchall()
            sv.commit()
        del mg
    assert len(rader) == 1 and len(rader[0]) == 4
    assert rader[0][0] >= 1


@pg
def test_ingen_m36_funksjon_er_immutable_naar_den_leser_naa():
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
    """Arvefeilen fra 116-118."""
    sti = (ROT / "deploy" / "staging"
           / "disponit-optimalisatorsveip.service")
    tj = sti.read_text(encoding="utf-8")
    beskrivelse = tj.split("Description=")[1][:110]
    assert "optimalisatorsveip" in beskrivelse
    for arvet in ("likviditet", "kontantbane", "bemanning",
                  "basislinjen", "EHF", "HMS"):
        assert arvet not in beskrivelse, f"arvet ord: {arvet}"
    assert ("LoadCredential=DISPONIT_OPTIMALISATORSVEIP_URL:"
            "/etc/disponit/optimalisatorsveip/"
            "DISPONIT_OPTIMALISATORSVEIP_URL" in tj)


def test_sveipen_rekker_aa_bli_ferdig_for_statussveipen_starter():
    """ET KLOKKESLETT ER IKKE EN REKKEFØLGE.

    `RandomizedDelaySec` etablerer ingen ordning mellom timere — den
    finnes for å hindre samtidighet. Det som må gå opp er START +
    SPREDNING + `TimeoutStartSec` for HVER overvåket sveip, og det
    regnestykket var ikke gjort da statussveipen ble flyttet til
    12:05: M-36 på 11:50 + 10 + 10 = 12:10 passerte den. Derfor
    flytter denne PR-en statussveipen til 12:20.

    DEN NESTE SOM LEGGER EN SVEIP I STIGEN MÅ GJØRE DET SAMME
    REGNESTYKKET, og porten faller hvis han lar være.
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
        tekst = (katalog / fil.name).read_text(encoding="utf-8")
        if "OnCalendar" not in tekst:
            continue
        start, spredning = _tid(fil.name)
        tj = katalog / fil.name.replace(".timer", ".service")
        m = (re.search(r"TimeoutStartSec=(\d+)min",
                       tj.read_text(encoding="utf-8"))
             if tj.exists() else None)
        kjoretid = int(m.group(1)) if m else 0
        slutt = start + spredning + kjoretid
        assert slutt <= status_start, (
            f"{fil.name} kan holde på til {slutt // 60}:"
            f"{slutt % 60:02d} mens statussveipen kan starte"
            f" {status_start // 60}:{status_start % 60:02d} —"
            " statusen ville lest forrige døgns tilstandsfil")
    egen, egen_sp = _tid("disponit-optimalisatorsveip.timer")
    assert (egen, egen_sp) == (6 * 60 + 30, 4)


def test_sveipens_dsn_star_i_ci():
    """127s LÆRDOM. Navnet hentes fra KJØREREN, ikke fra filnavnet."""
    kjorer = ROT / "platform" / "drift" / "kjor_optimalisatorsveip.py"
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


# =====================================================================
# FLATEN.
# =====================================================================

def test_flaten_har_ingen_iverksettknapp():
    """Porten leser flaten UTEN strenger (128s lærdom)."""
    kode = _bare_kode(FLATE, uten_strenger=True).lower()
    for ord_ in ("iverksett", "utfor", "policyutkast"):
        assert ord_ not in kode, f"flaten har «{ord_}»"


def test_flaten_viser_grunnlagstypen_i_samme_rad_som_tallet():
    """`korrelasjon_presentert_som_aarsak`, målt i flaten.

    Et forslag som er nummer én på grunn av en samvariasjon skal se
    annerledes ut enn ett som er det på grunn av et eksperiment — og
    forskjellen skal stå i SAMME RAD, ikke i en fotnote.
    """
    kode = FLATE.read_text(encoding="utf-8")
    tabell = kode.split("export function rangeringstabell")[1].split(
        "export function")[0]
    for felt in ("forventet_effekt_ore", "nedre_effekt_ore",
                 "ovre_effekt_ore", "grunnlagstype",
                 "reversibilitet"):
        assert felt in tabell, f"{felt} mangler i rangeringstabellen"
    assert 'p.grunnlagstype === "korrelasjon"' in tabell


def test_flaten_leser_kan_maales_fra_basen():
    """124s `kan_lukkes`-form."""
    kode = _bare_kode(FLATE, uten_strenger=True)
    assert "p.kan_maales" in kode
    tabell = kode.split("export function rangeringstabell")[1].split(
        "export function")[0]
    assert "Date" not in tabell


def test_apiet_gir_hele_bildet_i_ett_kall():
    """127s CodeRabbit-funn, ikke gjentatt."""
    from api import optimalisator as modul
    kilde = _bare_kode(Path(modul.__file__))
    for del_ in ("sammendrag", "rangeringer", "tiltak", "modeller",
                 "stopp", "funn"):
        assert f'"{del_}"' in kilde, f"svar_for mangler {del_}"


def test_ui_axe_alvorlige_brudd_dekkes_av_flatens_egen_suite():
    """`ui_axe_alvorlige_brudd` måles i `platform/core/ui/test`.

    Porten her er en PEKER, ikke en kopi: to steder som måler det
    samme ville kunnet gi to svar.
    """
    js = (ROT / "platform" / "core" / "ui" / "test"
          / "optimalisator.test.js")
    assert js.exists(), "flatens egen suite mangler"
    tekst = js.read_text(encoding="utf-8")
    assert "axe" in tekst.lower() or "aria" in tekst.lower()


def test_fundamentet_navngir_modulen_og_migrasjonen():
    """Fundamentet tildelte nummeret; koden skal svare til det."""
    tekst = FUNDAMENT.read_text(encoding="utf-8")
    assert "132" in tekst and "M-36" in tekst
    assert MIGRASJON.exists()


# =====================================================================
# CODERABBITS FUNN PÅ #396.
# =====================================================================

def test_taket_handheves_og_foreslaas_ikke():
    """`GRENSE` var bare en STANDARDVERDI.

    En kaller som ba om 100 000 tenanter fikk 100 000, og døra har
    `greatest(p_maks, 1)` uten øvre grense. Kjøreren gjør det ikke i
    dag — men et tak som bare gjelder når ingen ber om noe annet, er
    ikke et tak.

    MUTASJONEN SOM DREPER DENNE: fjern `grense = min(grense, GRENSE)`.
    """
    from drift import optimalisatorsveip as sveip

    class _Tilkobling:
        def __init__(self):
            self.bedt_om = None

        def execute(self, sql, params=None):
            if "advisory_lock" in sql:
                return _Rad([(True,)])
            if "m36_sveip_optimalisering" in sql:
                self.bedt_om = params[0]
                return _Rad([(1, 0, 0, 0)])
            return _Rad([])

        def commit(self):
            pass

        def rollback(self):
            pass

    class _Rad:
        def __init__(self, rader):
            self.rader = rader

        def fetchone(self):
            return self.rader[0]

        def fetchall(self):
            return self.rader

    c = _Tilkobling()
    sveip.kjor(c, grense=100000)
    assert c.bedt_om == sveip.GRENSE, (
        f"sveipen ba om {c.bedt_om} tenanter, taket er {sveip.GRENSE}")


def test_kjoreren_navngir_sin_egen_dor_i_kommentarene():
    """Kommentarene fulgte med da fila ble kopiert fra M-33s kjører,
    som selv arvet dem fra M-15s.

    En kommentar som navngir NABOENS migrasjon og NABOENS sveipedør
    forteller den neste leseren noe som ikke er sant om denne fila —
    samme arvefeil som beskrivelsene i 116-118.
    """
    kode = (ROT / "platform" / "drift"
            / "kjor_optimalisatorsveip.py").read_text(encoding="utf-8")
    assert "m36_sveip_optimalisering()" in kode
    for arvet in ("m50_sveip_postjournal", "m33_sveip_prognose",
                  "m15_sveip_likviditet", "(124 REVOKEr",
                  "(130 REVOKEr"):
        assert arvet not in kode, f"arvet referanse: {arvet}"


def test_flaten_lyver_ikke_om_hvem_som_eier_funnet():
    """«Lukkes av sveipen» sto på `lukk`-tilbakekallet.

    Det er `null` for en LESER uten skrivescope, så leseren fikk
    teksten på HVERT åpent funn — også de et menneske faktisk kan
    lukke. Det er en påstand om hvem som eier funnet, og den var feil.

    Samme feil sto i M-33s flate, skrevet samme kveld, og rettes i
    samme slengen.
    """
    for fil in ("optimalisator.js", "prognose.js"):
        kode = (ROT / "platform" / "core" / "ui" / "static" / "js"
                / "flater" / fil).read_text(encoding="utf-8")
        assert "f.apen && !f.kan_lukkes" in kode, fil
