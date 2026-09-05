"""M-15 likviditets- og kostnadsagent v1 (128) — KLYNGE 8s FØRSTE.

Grensen `m15-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

KLYNGENS DELTE DOM, OG DEN FORMER HVER PORT:

  EN GAL PROGNOSE SER NØYAKTIG UT SOM EN RIKTIG PROGNOSE — HELT TIL
  HORISONTEN ER PASSERT, OG DA HAR ALLE SLUTTET Å SE.

Klynge 7s feilform var «en foreldet regel ser ut som en riktig regel»,
og den kunne SLÅS OPP. En prognose har ingenting å slå opp mot før
tiden har gått, og da er den uinteressant.

Derfor måler portene her ikke bare at prognosen LAGES riktig, men at
den ikke kan lages på en måte som gjør den uetterprøvbar: uten
horisont, uten modellversjon, uten intervall, på tomt grunnlag — og at
`prognose_uten_maaling` er et funn INGEN kan klikke bort.

DEN ANDRE GRUPPEN PORTER MÅLER ET FRAVÆR: modulen utfører ingenting.
Ingen kolonne betyr «sagt opp» eller «betalt», statussettet har ingen
`iverksatt`, og ingen driftfil importerer noe som kan snakke ut.

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

LIKVIDITETSSVEIP_DSN = os.environ.get(
    "DISPONIT_TEST_LIKVIDITETSSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "128_m15_likviditet.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "likviditet.js")
FUNDAMENT = ROT / "docs" / "KLYNGE8-FUNDAMENT.md"
MODULFILER = (
    ROT / "platform" / "core" / "api" / "likviditet.py",
    ROT / "platform" / "drift" / "likviditetssveip.py",
    ROT / "platform" / "drift" / "kjor_likviditetssveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("likviditetskrav", "likviditetsmodell", "likviditetspost",
        "likviditetsprognose", "prognosebane", "prognosemaaling",
        "kostnadstiltak", "likviditetsfunn")

_STRENG = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")


def _bare_kode(fil: Path, *, uten_strenger: bool = False) -> str:
    """Filens innhold uten kommentarer OG uten docstrings (m24-formen).

    PORTEN SKAL MÅLE HANDLINGEN, IKKE BEGRUNNELSEN. En port som leter
    i rå filtekst treffer kommentaren som forklarer HVORFOR et mønster
    er unngått — og her er kommentarene fulle av ordene «betaling» og
    «oppsigelse», nettopp fordi modulen ikke gjør noen av delene.
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
    return koble(LIKVIDITETSSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m15-{merke}-{secrets.token_hex(4)}"


I_DAG = datetime.date.today()


def _dag(n: int) -> datetime.date:
    return I_DAG + datetime.timedelta(days=n)


METODE = ("Startsaldo fra bankposter, fordringer inn paa"
          " forfallsuken, registrerte forpliktelser ut paa sin.")


def _krav(c, tenant, *, horisont=13, grunnlag=7, maalefrist=14,
          modellvarsel=30, aktor="u-test", nokkel=None):
    _sett_kontekst(c, tenant)
    v = c.execute(
        "SELECT versjon FROM m15_sett_krav(%s,%s,%s,%s,%s,%s,%s)",
        (tenant, horisont, grunnlag, maalefrist, modellvarsel, aktor,
         nokkel or secrets.token_hex(8))).fetchone()[0]
    c.commit()
    return v


def _modell(c, tenant, *, navn="Kumulativ kontantbane",
            versjon="2026-01", metode=METODE,
            baselinje="samme som forrige uke", fra=None, til=None,
            aktor="u-test", modell_id=None):
    _sett_kontekst(c, tenant)
    mid = modell_id or uuid.uuid4()
    c.execute(
        "SELECT m15_registrer_modell(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant, mid, navn, versjon, metode, baselinje,
         fra or _dag(-10), til, aktor))
    c.commit()
    return mid


def _post(c, tenant, *, posttype="husleie",
          beskrivelse="Husleie kontorlokaler", belop=-8500000,
          forfall=None, gjentakelse="maanedlig", aktor="u-test"):
    _sett_kontekst(c, tenant)
    pid = uuid.uuid4()
    c.execute(
        "SELECT m15_registrer_post(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant, pid, posttype, beskrivelse, belop,
         forfall or _dag(5), gjentakelse, None, aktor))
    c.commit()
    return pid


def _prognose(c, tenant, modell_id, *, usikkerhet=1500,
              aktor="u-test", prognose_id=None):
    _sett_kontekst(c, tenant)
    pid = prognose_id or uuid.uuid4()
    rad = c.execute(
        "SELECT * FROM m15_lag_prognose(%s,%s,%s,%s,%s)",
        (tenant, pid, modell_id, usikkerhet, aktor)).fetchone()
    c.commit()
    return pid, rad


# ---------------------------------------------------------------------
# §0: hver invariant i `m15-v1` har minst én port.
# ---------------------------------------------------------------------

def test_grensen_er_registrert_og_hver_invariant_har_en_port():
    """§0-regelen, målt."""
    from manifestskjema import KRAVGRENSER
    inv = set(KRAVGRENSER["m15-v1"]["invarianter"])
    assert inv
    tekst = Path(__file__).read_text(encoding="utf-8")
    mangler = sorted(i for i in inv if i not in tekst)
    assert mangler == [], f"invarianter uten port: {mangler}"


# =====================================================================
# V1-DOMMEN: modulen utfører ingenting.
# =====================================================================

def test_modulen_sa_opp_abonnement_og_modulen_utforte_betaling():
    """FRAVÆRET ER PORTEN.

    Katalogens vaktsetning: «oppsigelser og betalinger utføres bare
    via egne policykontrollerte moduler». Modulen holder den i
    datamodellen — statussettet har ingen `iverksatt`, og ingen
    kolonne betyr «sagt opp» eller «betalt».

    MUTASJONEN SOM DREPER DENNE: legg `iverksatt` i statussettet.
    """
    sql = _bare_kode(MIGRASJON, uten_strenger=True)
    for ord_ in ("sagt_opp", "oppsagt", "betalt_ts", "iverksatt",
                 "utfort_betaling"):
        assert not re.search(rf"\b{ord_}\w*\s+(TEXT|TIMESTAMPTZ|UUID|"
                             rf"BOOLEAN|DATE|BIGINT)", sql, re.I), ord_
    # …og statussettet selv, med strenger.
    rå = MIGRASJON.read_text(encoding="utf-8")
    status = re.search(
        r"status TEXT NOT NULL DEFAULT 'foreslatt'\s*\n\s*CHECK "
        r"\(status IN \(([^)]*)\)\)", rå)
    assert status, "statussettet finnes ikke"
    assert "iverksatt" not in status.group(1)
    assert set(re.findall(r"'([a-z_]+)'", status.group(1))) == {
        "foreslatt", "vurdert", "avvist"}

    for fil in MODULFILER:
        kode = _bare_kode(fil, uten_strenger=True)
        for modul in ("httpx", "requests", "urllib", "socket",
                      "smtplib", "aiohttp"):
            assert not re.search(rf"^\s*(import|from)\s+{modul}\b",
                                 kode, re.M), (
                f"{fil.name} importerer {modul} — modulen skal ikke"
                " kunne betale eller si opp noe")


@pg
def test_vurderingsdoera_nekter_alt_annet_enn_vurdert_og_avvist():
    """DEN ENESTE VEIEN UT AV `foreslatt`."""
    t = _tenantnavn("vurder")
    with _to() as (rt, _mg):
        _sett_kontekst(rt, t)
        tid = uuid.uuid4()
        rt.execute(
            "SELECT m15_foresla_tiltak(%s,%s,%s,%s,%s,%s,%s)",
            (t, tid, "Si opp ubrukte lisenser i designverktoeyet",
             4500000, "reversibel",
             "Fjorten lisenser uten paalogging siste nitti dager",
             "u-kari"))
        rt.commit()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT m15_vurder_tiltak(%s,%s,%s,%s,%s)",
                       (t, tid, "iverksatt", "gjort", "u-kari"))


# =====================================================================
# KLYNGENS FIRE DOMMER, HÅNDHEVET I DATAMODELLEN.
# =====================================================================

def test_prognose_uten_horisont_og_prognose_uten_modellversjon():
    """NOT NULL, IKKE ET SVEIPEFUNN.

    En prognose uten et tidspunkt den kan etterprøves mot er ikke en
    prognose, det er en mening med tall i. Samme form som M-50s
    `journalperson.slettefrist` (124) og M-53s
    `hmsavvik.oppbevaring_til` (127).

    MUTASJONEN SOM DREPER DENNE: gjør `gjelder_til` nullbar.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    kropp = re.search(r"CREATE TABLE likviditetsprognose \((.*?)\n\);",
                      sql, re.S).group(1)
    linjer = [x for x in kropp.splitlines()
              if not x.lstrip().startswith("--")]
    for felt in ("horisont_uker", "gjelder_til", "modell_id",
                 "modellversjon", "baselinje", "startsaldo_ore",
                 "kravversjon"):
        rad = [x for x in linjer if re.match(rf"\s*{felt}\s", x)]
        assert rad, f"{felt} finnes ikke"
        assert "NOT NULL" in rad[0].upper(), (
            f"{felt} er nullbar — prognosen kan ikke etterprøves")
    # …OG HORISONTEN ER REGNET, IKKE MOTTATT.
    assert "gjelder_til = laget_dato + (horisont_uker * 7)" in sql


def test_prognose_uten_intervall():
    """INTERVALL, ALDRI BARE PUNKT.

    Et punktestimat uten usikkerhet er ikke en presis prognose — det
    er en upresis prognose som har mistet informasjonen om hvor
    upresis den er.

    OG CHECKen ER IKKE PYNT: et «intervall» der punktet ligger utenfor
    er ikke et intervall, det er tre tall.

    MUTASJONEN SOM DREPER DENNE: gjør `nedre_ore` nullbar.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    kropp = re.search(r"CREATE TABLE prognosebane \((.*?)\n\);",
                      sql, re.S).group(1)
    linjer = [x for x in kropp.splitlines()
              if not x.lstrip().startswith("--")]
    for felt in ("punkt_ore", "nedre_ore", "ovre_ore"):
        rad = [x for x in linjer if re.match(rf"\s*{felt}\s", x)]
        assert rad and "NOT NULL" in rad[0].upper(), felt
    assert "nedre_ore <= punkt_ore AND punkt_ore <= ovre_ore" in sql


@pg
def test_banen_baerer_baandet_og_punktet_ligger_inni():
    """MÅLT MOT BASEN, ikke bare mot filteksten."""
    t = _tenantnavn("baand")
    with _to() as (rt, mg):
        _krav(rt, t)
        mid = _modell(rt, t)
        _post(rt, t)
        pid, _ = _prognose(rt, t, mid, usikkerhet=1500)
        _sett_kontekst(rt, t)
        rader = rt.execute(
            "SELECT uke_nr, punkt_ore, nedre_ore, ovre_ore"
            "  FROM m15_banen(%s,%s)", (t, pid)).fetchall()
    assert len(rader) == 13, "horisonten ga ikke tretten uker"
    for uke, punkt, nedre, ovre in rader:
        assert nedre <= punkt <= ovre, uke
        # BÅNDET ER FAKTISK BREDT. Et bånd der nedre == ovre ville
        # passert CHECKen og vært verdiløst.
        if punkt != 0:
            assert nedre < ovre, f"uke {uke} har et bånd uten bredde"


@pg
def test_doera_nekter_uten_grenser_uten_modell_og_paa_tomt_grunnlag():
    """TRE NEKT, hvert fordi det motsatte ville sett riktig ut."""
    t = _tenantnavn("nekt")
    with _to() as (rt, _mg):
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT * FROM m15_lag_prognose(%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), uuid.uuid4(), 1500, "u-kari"))
        rt.rollback()
        _krav(rt, t)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT * FROM m15_lag_prognose(%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), uuid.uuid4(), 1500, "u-kari"))
        rt.rollback()
        mid = _modell(rt, t)
        # MODELLEN FINNES, MEN GRUNNLAGET ER TOMT.
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            rt.execute("SELECT * FROM m15_lag_prognose(%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), mid, 1500, "u-kari"))
        assert "grunnlag" in str(e.value).lower()


@pg
def test_prognose_mot_avviklet_modell_nektes():
    """ARKIVET TAR IMOT DEN AVVIKLEDE VERSJONEN; BRUKEN ER STENGT."""
    t = _tenantnavn("avviklet")
    with _to() as (rt, _mg):
        _krav(rt, t)
        mid = _modell(rt, t, fra=_dag(-100), til=_dag(-1))
        _post(rt, t)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT * FROM m15_lag_prognose(%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), mid, 1500, "u-kari"))


# =====================================================================
# MÅLINGEN — MODULENS EGENTLIGE PRODUKT.
# =====================================================================

def _aldre_prognose(mg, tenant, prognose_id, dogn):
    """Fabrikerer alderen med append-only-vakten AVSLÅTT.

    `laget_dato` settes av døra til `current_date`, og prognosen er
    append-only — også for migrator. Testen må derfor gå utenom, og
    gjør det SYNLIG i stedet for å finne en dør som ikke burde finnes.

    At denne hjelpefunksjonen er nødvendig, er selv et bevis: det
    finnes ingen lovlig vei til å endre en avgitt prognose.
    """
    mg.execute("ALTER TABLE likviditetsprognose DISABLE TRIGGER"
               " likviditetsprognose_append_only")
    mg.execute("ALTER TABLE prognosebane DISABLE TRIGGER"
               " prognosebane_append_only")
    _sett_kontekst(mg, tenant)
    mg.execute(
        "UPDATE likviditetsprognose"
        "   SET laget_dato = laget_dato - %s::int,"
        "       gjelder_til = gjelder_til - %s::int"
        " WHERE tenant=%s AND prognose_id=%s",
        (dogn, dogn, tenant, prognose_id))
    mg.execute(
        "UPDATE prognosebane SET ukeslutt = ukeslutt - %s::int"
        " WHERE tenant=%s AND prognose_id=%s",
        (dogn, tenant, prognose_id))
    mg.execute("ALTER TABLE likviditetsprognose ENABLE TRIGGER"
               " likviditetsprognose_append_only")
    mg.execute("ALTER TABLE prognosebane ENABLE TRIGGER"
               " prognosebane_append_only")
    mg.commit()


@pg
def test_maaling_av_en_uke_som_ikke_er_over_nektes():
    """EN MÅLING AV EN UKE SOM FORTSATT LØPER ER ET DELVIS TALL SOM
    SER UT SOM ET ENDELIG.

    Nekten er ikke pedanteri: en slik måling ville lukket
    `prognose_uten_maaling` uten at noen faktisk hadde sett hva som
    skjedde.
    """
    t = _tenantnavn("formaal")
    with _to() as (rt, _mg):
        _krav(rt, t)
        mid = _modell(rt, t)
        _post(rt, t)
        pid, _ = _prognose(rt, t, mid)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            rt.execute(
                "SELECT * FROM m15_registrer_maaling(%s,%s,%s,%s,%s,%s)",
                (t, pid, 1, -8000000, None, "u-kari"))
        assert "ikke over" in str(e.value)


@pg
def test_innenfor_intervall_regnes_av_baandet_paa_raden():
    """HADDE KALLEREN FÅTT SI «JA, DETTE VAR INNENFOR», VILLE MÅLINGEN
    VÆRT EN KARAKTER MODULEN GA SEG SELV.

    MUTASJONEN SOM DREPER DENNE: la døra ta `innenfor` som parameter.
    """
    t = _tenantnavn("innenfor")
    with _to() as (rt, mg):
        _krav(rt, t)
        mid = _modell(rt, t)
        _post(rt, t)
        pid, _ = _prognose(rt, t, mid, usikkerhet=1500)
        _aldre_prognose(mg, t, pid, 30)
        _sett_kontekst(rt, t)
        bane = rt.execute(
            "SELECT punkt_ore, nedre_ore, ovre_ore"
            "  FROM m15_banen(%s,%s) WHERE uke_nr = 1",
            (t, pid)).fetchone()
        punkt, nedre, ovre = bane
        # ETT TALL INNENFOR OG ETT UTENFOR, begge avgjort av raden.
        _sett_kontekst(rt, t)
        inni = rt.execute(
            "SELECT * FROM m15_registrer_maaling(%s,%s,%s,%s,%s,%s)",
            (t, pid, 1, punkt, None, "u-kari")).fetchone()
        rt.commit()
        assert inni[1] is True, "et treff midt i båndet ble meldt bom"
        assert inni[0] == 0, "avviket mot eget punkt er ikke null"
        _sett_kontekst(rt, t)
        ute = rt.execute(
            "SELECT * FROM m15_registrer_maaling(%s,%s,%s,%s,%s,%s)",
            (t, pid, 2, nedre - abs(ovre - nedre) - 1, None,
             "u-kari")).fetchone()
        rt.commit()
        assert ute[1] is False, "et tall langt utenfor ble meldt treff"


@pg
def test_prognose_uten_maaling_kan_ingen_lukke():
    """KLYNGENS FUNN INGEN KAN KLIKKE BORT.

    En prognosemodul som ikke måles blir gradvis dårligere uten at
    noen oppdager det, mens den beholder autoriteten sin. Knappen som
    fjernet dette funnet ville fjernet det eneste signalet.

    MUTASJONEN SOM DREPER DENNE: ta funntypen ut av
    `m15_funn_er_sveipens`.
    """
    t = _tenantnavn("umaalt")
    with _to() as (rt, mg):
        _krav(rt, t, maalefrist=14)
        mid = _modell(rt, t)
        _post(rt, t)
        pid, _ = _prognose(rt, t, mid)
        # Horisonten OG nådefristen passert.
        _aldre_prognose(mg, t, pid, 13 * 7 + 20)
        with _sv() as sv:
            sv.execute("SELECT * FROM m15_sveip_likviditet(200)")
            sv.commit()
        _sett_kontekst(mg, t)
        rad = mg.execute(
            "SELECT funn_id, over_grense FROM likviditetsfunn"
            " WHERE tenant=%s AND funntype='prognose_uten_maaling'",
            (t,)).fetchone()
        assert rad, "en umålt prognose ga intet funn"
        assert rad[1] > 0, "over_grense teller ikke døgn over fristen"
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT m15_lukk_funn(%s,%s,%s,%s)",
                       (t, rad[0], "jeg har sett den", "u-kari"))
        rt.rollback()
        _sett_kontekst(mg, t)
        mg.execute("DELETE FROM likviditetsfunn WHERE tenant=%s", (t,))
        mg.commit()


@pg
def test_maalingen_lukker_funnet_ingen_andre_kan_lukke():
    """DEN ENESTE VEIEN. Porten kjører sveipen, måler, og sveiper
    igjen — formen porten på 116–124 manglet.
    """
    t = _tenantnavn("lukker")
    with _to() as (rt, mg):
        _krav(rt, t, maalefrist=14)
        mid = _modell(rt, t)
        _post(rt, t)
        pid, _ = _prognose(rt, t, mid)
        _aldre_prognose(mg, t, pid, 13 * 7 + 20)
        with _sv() as sv:
            sv.execute("SELECT * FROM m15_sveip_likviditet(200)")
            sv.commit()
        _sett_kontekst(rt, t)
        rt.execute(
            "SELECT * FROM m15_registrer_maaling(%s,%s,%s,%s,%s,%s)",
            (t, pid, 1, -8500000, None, "u-kari"))
        rt.commit()
        with _sv() as sv:
            sv.execute("SELECT * FROM m15_sveip_likviditet(200)")
            sv.commit()
        _sett_kontekst(mg, t)
        rad = mg.execute(
            "SELECT apen, lukket_av FROM likviditetsfunn"
            " WHERE tenant=%s AND funntype='prognose_uten_maaling'",
            (t,)).fetchone()
        mg.execute("DELETE FROM likviditetsfunn WHERE tenant=%s", (t,))
        mg.commit()
    assert rad and rad[0] is False, (
        "målingen lukket ikke funnet den er den eneste veien til")
    assert rad[1] == "m15_sveip"


@pg
def test_en_maaling_rettes_ikke():
    """EN MÅLING SOM LOT SEG JUSTERE ER EN MÅLING SOM ALLTID
    BEKREFTER."""
    t = _tenantnavn("rettes")
    with _to() as (rt, mg):
        _krav(rt, t)
        mid = _modell(rt, t)
        _post(rt, t)
        pid, _ = _prognose(rt, t, mid)
        _aldre_prognose(mg, t, pid, 30)
        _sett_kontekst(rt, t)
        rt.execute(
            "SELECT * FROM m15_registrer_maaling(%s,%s,%s,%s,%s,%s)",
            (t, pid, 1, -8500000, None, "u-kari"))
        rt.commit()
        # SAMME TALL: stille ja.
        _sett_kontekst(rt, t)
        igjen = rt.execute(
            "SELECT * FROM m15_registrer_maaling(%s,%s,%s,%s,%s,%s)",
            (t, pid, 1, -8500000, None, "u-kari")).fetchone()
        rt.commit()
        assert igjen[3] is False
        # ANNET TALL: konflikt.
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute(
                "SELECT * FROM m15_registrer_maaling(%s,%s,%s,%s,%s,%s)",
                (t, pid, 1, -9000000, None, "u-kari"))


# =====================================================================
# HISTORIKK, RADVAKTER OG TENANTISOLASJON.
# =====================================================================

@pg
def test_prognose_overskrevet():
    """APPEND-ONLY, HÅNDHEVET FOR ALLE — også migrator.

    En prognose er en PÅSTAND AVGITT PÅ ET TIDSPUNKT. Kunne den
    redigeres, ville enhver måling vært en sammenligning mot noe som
    er endret etterpå — altså ingen måling. EN PROGNOSE SOM KAN
    JUSTERES I ETTERKANT ER EN PROGNOSE SOM ALLTID STEMMER.

    MUTASJONEN SOM DREPER DENNE: fjern
    `likviditetsprognose_append_only`.
    """
    t = _tenantnavn("frosset")
    with _to() as (rt, mg):
        _krav(rt, t)
        mid = _modell(rt, t)
        _post(rt, t)
        pid, _ = _prognose(rt, t, mid)
        for tabell, setning in (
                ("likviditetsprognose",
                 "UPDATE likviditetsprognose SET startsaldo_ore = 1"),
                ("likviditetsprognose",
                 "DELETE FROM likviditetsprognose"),
                ("prognosebane",
                 "UPDATE prognosebane SET punkt_ore = 1"),
                ("prognosebane", "DELETE FROM prognosebane")):
            _sett_kontekst(mg, t)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                mg.execute(setning + " WHERE tenant=%s AND"
                           " prognose_id=%s", (t, pid))
            mg.rollback()


@pg
def test_modellen_og_posten_er_frosset():
    """MODELLENS IDENTITET, OG BELØPET ET MENNESKE SATTE.

    En modell som kunne endres i ettertid ville gjort hvert snapshot
    til en påstand om noe som ikke lenger står noe sted — og det er
    SKARPERE her enn i klynge 7, fordi en modell endres av OSS.

    En ny sum er en NY post, så begge står i historikken med hvert
    sitt navn.
    """
    t = _tenantnavn("frost2")
    with _to() as (rt, mg):
        _krav(rt, t)
        mid = _modell(rt, t)
        pid = _post(rt, t)
        for felt, verdi in (("versjon", "'2099'"),
                            ("metode", "'noe annet'"),
                            ("baselinje", "'ingenting'"),
                            ("gyldig_fra", "current_date")):
            _sett_kontekst(mg, t)
            mg.execute("SET LOCAL ROLE disponit_likviditet_eier")
            with pytest.raises(psycopg.errors.Error):
                mg.execute(f"UPDATE likviditetsmodell SET {felt}"
                           f" = {verdi} WHERE tenant=%s AND"
                           " modell_id=%s", (t, mid))
            mg.rollback()
        for felt, verdi in (("belop_ore", "1"),
                            ("beskrivelse", "'noe annet'"),
                            ("registrert_av", "'u-noen'")):
            _sett_kontekst(mg, t)
            mg.execute("SET LOCAL ROLE disponit_likviditet_eier")
            with pytest.raises(psycopg.errors.Error):
                mg.execute(f"UPDATE likviditetspost SET {felt}"
                           f" = {verdi} WHERE tenant=%s AND post_id=%s",
                           (t, pid))
            mg.rollback()


@pg
def test_tiltak_uten_reversibilitet():
    """`reversibilitet` ER NOT NULL OG ET LUKKET SETT.

    Et tiltak ingen har vurdert reversibiliteten av er et tiltak ingen
    kan angre — og det er nettopp de tiltakene som foreslås først,
    fordi de ser billigst ut.
    """
    t = _tenantnavn("rev")
    with _to() as (rt, mg):
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            rt.execute(
                "SELECT m15_foresla_tiltak(%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), "Si opp ubrukte lisenser i verktoeyet",
                 4500000, None, "Fjorten lisenser uten paalogging",
                 "u-kari"))
        assert "reversibilitet" in str(e.value).lower()
        rt.rollback()
        # …og direkte DML kommer heller ikke forbi.
        _sett_kontekst(mg, t)
        mg.execute("SET LOCAL ROLE disponit_likviditet_eier")
        with pytest.raises(psycopg.errors.Error):
            mg.execute(
                "INSERT INTO kostnadstiltak (tenant, tiltak_id,"
                " beskrivelse, forventet_effekt_ore, reversibilitet,"
                " grunnlag, opprettet_av)"
                " VALUES (%s,%s,'Si opp ubrukte lisenser i verktoeyet',"
                " 1, NULL, 'Fjorten lisenser uten paalogging', 'u')",
                (t, uuid.uuid4()))


@pg
def test_tenantlekkasje_i_likviditetsregister():
    """RLS ENABLE + FORCE PÅ ALLE ÅTTE."""
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
def test_prognose_fra_en_tenant_er_usynlig_for_en_annen():
    t1, t2 = _tenantnavn("a"), _tenantnavn("b")
    with _to() as (rt, mg):
        _krav(rt, t1)
        mid = _modell(rt, t1)
        _post(rt, t1)
        _prognose(rt, t1, mid)
        _sett_kontekst(mg, t2)
        n = mg.execute(
            "SELECT count(*) FROM likviditetsprognose").fetchone()[0]
    assert n == 0, "en tenant så en annens prognose"


@pg
def test_modulen_leser_bankpostene_men_kan_ikke_skrive_dem():
    """EN LIKVIDITETSMODUL SOM KUNNE SKRIVE I BANKREGISTERET VILLE
    KUNNET «RETTE» VIRKELIGHETEN TIL Å PASSE PROGNOSEN.

    Det er den ene feilen ingen ville oppdaget: tallene stemmer, fordi
    vi endret fasiten.
    """
    with _mig() as c:
        rader = c.execute(
            "SELECT table_name, privilege_type"
            "  FROM information_schema.table_privileges"
            " WHERE grantee = 'disponit_likviditet_eier'"
            "   AND table_name IN ('bankpost','bankkonto','fordring')"
        ).fetchall()
        c.rollback()
    # ALLE TRE, OG BARE SELECT. En port som bare sjekket rettigheten
    # på de radene som FANTES, ville vært grønn om et grant forsvant —
    # og da hadde prognosen stille regnet uten fordringene.
    assert {r[0] for r in rader} == {"bankpost", "bankkonto",
                                     "fordring"}, rader
    for tabell, rett in rader:
        assert rett == "SELECT", f"{tabell}: {rett} er for mye"


# =====================================================================
# SVEIPEN OG FUNNENE.
# =====================================================================

@pg
def test_prognose_mot_utdatert_grunnlag():
    """ALDEREN PÅ INNGANGSDATAENE ER EN DEL AV PROGNOSEN.

    Banksaldoen er fra i går, prognosen fra i dag — og forskjellen er
    ikke null. Uten bankposter i det hele tatt er grunnlaget så
    utdatert det kan bli, og funnet skal reises.
    """
    t = _tenantnavn("utdatert")
    with _to() as (rt, mg):
        _krav(rt, t, grunnlag=7)
        mid = _modell(rt, t)
        _post(rt, t)
        _prognose(rt, t, mid)
        with _sv() as sv:
            sv.execute("SELECT * FROM m15_sveip_likviditet(200)")
            sv.commit()
        _sett_kontekst(mg, t)
        typer = {r[0] for r in mg.execute(
            "SELECT funntype FROM likviditetsfunn"
            " WHERE tenant=%s AND apen", (t,)).fetchall()}
        mg.execute("DELETE FROM likviditetsfunn WHERE tenant=%s", (t,))
        mg.commit()
    assert "prognose_mot_utdatert_grunnlag" in typer, typer


@pg
def test_bane_under_null_kan_lukkes_og_staar_natten_over():
    """125/126s VAKT GJELDER OGSÅ HER.

    «Kassekreditt er avtalt» er en legitim beslutning om noe som ennå
    ikke er brutt. Porten lukker, kjører DERETTER sveipen, og leser
    raden på nytt.
    """
    t = _tenantnavn("undernull")
    with _to() as (rt, mg):
        _krav(rt, t)
        mid = _modell(rt, t)
        _post(rt, t, belop=-8500000)
        _prognose(rt, t, mid)
        with _sv() as sv:
            sv.execute("SELECT * FROM m15_sveip_likviditet(200)")
            sv.commit()
        _sett_kontekst(mg, t)
        rad = mg.execute(
            "SELECT funn_id, over_grense, detalj FROM likviditetsfunn"
            " WHERE tenant=%s AND funntype='bane_under_null'",
            (t,)).fetchone()
        assert rad, "en bane under null ga intet funn"
        assert rad[1] > 0, "dybden er ikke et positivt tall"
        # TO SANNE TALL, HVERT MED SITT NAVN: `over_grense` er dybden,
        # `detalj` sier NÅR det begynner.
        assert "under null fra uke" in rad[2], rad[2]
        mg.commit()
        _sett_kontekst(rt, t)
        rt.execute("SELECT m15_lukk_funn(%s,%s,%s,%s)",
                   (t, rad[0], "kassekreditt er avtalt", "u-kari"))
        rt.commit()
        with _sv() as sv:
            sv.execute("SELECT * FROM m15_sveip_likviditet(200)")
            sv.commit()
        _sett_kontekst(mg, t)
        etter = mg.execute(
            "SELECT apen, lukket_av, lukkenotat FROM likviditetsfunn"
            " WHERE tenant=%s AND funn_id=%s", (t, rad[0])).fetchone()
        mg.execute("DELETE FROM likviditetsfunn WHERE tenant=%s", (t,))
        mg.commit()
    assert etter[0] is False, "sveipen gjenåpnet et menneskes lukking"
    assert etter[1] == "u-kari"
    assert etter[2] == "kassekreditt er avtalt"


@pg
def test_sveipekontrakten_er_fire_felt():
    """FIRE FELT, som resten av flåten."""
    with _sv() as c:
        rad = c.execute(
            "SELECT * FROM m15_sveip_likviditet(1)").fetchone()
        c.rollback()
    assert len(rad) == 4
    from drift import likviditetssveip
    assert likviditetssveip.KONTRAKTFELT == 4


# =====================================================================
# API OG FLATE.
# =====================================================================

def test_api_regner_i_ore_og_aldri_i_flyttall():
    """`0.1 + 0.2` ER IKKE `0.3`.

    En likviditetsprognose som samler tolv ukers avrundingsfeil ville
    bommet med et beløp ingen kan forklare — og bommen ville sett ut
    som modellens feil, ikke som datatypens.

    MUTASJONEN SOM DREPER DENNE: ta imot `float` i `_ore`.
    """
    api = ROT / "platform" / "core" / "api" / "likviditet.py"
    kode = _bare_kode(api)
    blokk = re.search(r"def _ore\(.*?(?=\ndef )", kode, re.S)
    assert blokk, "_ore finnes ikke"
    kropp = blokk.group(0)
    assert "isinstance(verdi, int)" in kropp
    assert "float" not in kropp, "_ore slipper inn flyttall"


def test_api_har_ingen_iverksettelsesvei():
    """INGEN RUTE SIER OPP NOE OG INGEN BETALER."""
    api = ROT / "platform" / "core" / "api" / "likviditet.py"
    kode = _bare_kode(api, uten_strenger=True).lower()
    for forbudt in ("def iverksett", "def si_opp", "def betal",
                    "m15_iverksett", "outbox", "mottaker"):
        assert forbudt not in kode, forbudt
    rå = api.read_text(encoding="utf-8")
    m = re.search(r"VURDERINGER = \(([^)]*)\)", rå)
    assert m and set(re.findall(r'"([a-z]+)"', m.group(1))) == {
        "vurdert", "avvist"}


def test_flaten_viser_baandet_i_samme_rad_som_punktet():
    """EN BANE VIST SOM ÉN LINJE VILLE SETT UT SOM EN PRESIS PROGNOSE.

    `nedre` og `ovre` skal stå i tabellen, ikke bak et klikk.

    MUTASJONEN SOM DREPER DENNE: fjern nedre/ovre fra `banetabell`.
    """
    kode = _bare_kode(FLATE)
    blokk = re.search(r"export function banetabell\(.*?\n\}", kode,
                      re.S)
    assert blokk, "banetabell finnes ikke"
    kropp = blokk.group(0)
    for felt in ("nedre_ore", "ovre_ore", "punkt_ore"):
        assert felt in kropp, felt
    assert "ui.likviditet.nedre" in kropp
    assert "ui.likviditet.ovre" in kropp


def test_flaten_leser_kan_maales_fra_basen():
    """FLATEN REGNER IKKE UT SELV om en uke er over.

    Regelen bor ETT sted, og en kopi her ville råtnet den dagen
    nådefristen endret seg (124s `kan_lukkes`-form).
    """
    kode = _bare_kode(FLATE)
    blokk = re.search(r"export function banetabell\(.*?\n\}", kode,
                      re.S).group(0)
    assert "u.kan_maales" in blokk
    # …og ingen egen datoregning i tabellen.
    assert "new Date(" not in blokk, (
        "flaten regner selv om uken er over")


def test_flaten_har_ingen_iverksett_knapp():
    """DET KAN IKKE FINNES EN. Et kostnadstiltak kan bli vurdert eller
    avvist; oppsigelsen går gjennom betalingsmodulens vei."""
    kode = _bare_kode(FLATE, uten_strenger=True).lower()
    for forbudt in ("iverksett", "si_opp", "siopp", "utfor_tiltak"):
        assert forbudt not in kode, forbudt
    rå = FLATE.read_text(encoding="utf-8")
    m = re.search(r'velger\("likv-vurdering", \[([^\]]*)\]', rå)
    assert m and set(re.findall(r'"([a-z]+)"', m.group(1))) == {
        "vurdert", "avvist"}


def test_ui_axe_alvorlige_brudd():
    """INGEN `role="alert"` PÅ EN `<li>` (124s funn: rollen overstyrer
    `listitem`, og axe felte hele lista)."""
    kode = _bare_kode(FLATE)
    assert not re.search(r'el\("li"[^)]*role:\s*"alert"', kode)


def test_kroner_deler_bare_i_visningen():
    """ØRE HELE VEIEN INN, KRONER BARE UT.

    Delingen på 100 skal skje ÉTT sted — i visningen. Skjer den i en
    beregning, samler feilen seg.
    """
    kode = _bare_kode(FLATE)
    delinger = re.findall(r"/\s*100\b", kode)
    fn = re.search(r"export function kroner\(ore\).*?\n\}", kode, re.S)
    assert fn, "kroner finnes ikke"
    assert len(delinger) == len(re.findall(r"/\s*100\b", fn.group(0))), (
        "det deles på 100 utenfor visningsfunksjonen")


def test_fundamentets_lonnsantakelse_er_rettet():
    """FUNDAMENTET TOK FEIL, OG DET SKAL STÅ SKREVET.

    `docs/KLYNGE8-FUNDAMENT.md` listet lønnsgrunnlaget (M-39) blant
    M-15s inngangsdata. M-39 MÅLER TIMER, IKKE KRONER — det finnes
    ingen sats noe sted i huset, verifisert mot katalogen.

    Dette er samme feilform som fundamentet selv fanget for M-36
    («leser en KPI-katalog som ikke finnes»), og andre gang i klyngen
    at en antakelse ikke overlevde møtet med skjemaet.

    ET FUNDAMENT KAN TILDELE NUMRE OG ROLLER UTEN Å LESE KODEN. DET
    KAN IKKE TILDELE DATA.
    """
    tekst = FUNDAMENT.read_text(encoding="utf-8")
    # DEN SPESIFIKKE SETNINGEN, ikke bare ordene «måler timer». En
    # løsere port ville vært grønn på en tilfeldig omtale av timer et
    # helt annet sted i dokumentet.
    assert "M-39 måler timer, ikke kroner" in tekst, (
        "fundamentet er ikke rettet — det påstår fortsatt at"
        " lønnsgrunnlaget er en inngangskilde for M-15")
    # …og manifestet erklærer ikke en avhengighet modulen ikke har.
    import yaml
    man = yaml.safe_load(
        (ROT / "platform" / "modules" / "m15_likviditet"
         / "manifest.yaml").read_text(encoding="utf-8"))
    assert "m39_lonnsgrunnlag" not in man["avhengigheter"], (
        "manifestet erklærer M-39, men modulen kan ikke lese en pris"
        " derfra")


@pg
def test_gjentakelsen_utvides_over_horisonten():
    """EN KOLONNE SOM LAGRES OG ALDRI BRUKES.

    Første utgave talte hver post ÉN gang — i uken `forste_forfall`
    falt. En månedlig husleie dukket da opp én gang på tretten uker i
    stedet for tre (CodeRabbit).

    OG FEILEN GIKK I DEN FARLIGE RETNINGEN: banen undertalte det som
    skal UT, så kontantbeholdningen så bedre ut enn den er — og
    `bane_under_null`, funnet modulen finnes for, uteble nettopp når
    den trengtes.

    MUTASJONEN SOM DREPER DENNE: filtrer på `forste_forfall` igjen i
    stedet for på forekomstdatoen.
    """
    t = _tenantnavn("gjentak")
    with _to() as (rt, _mg):
        _krav(rt, t, horisont=13)
        mid = _modell(rt, t)
        # MÅNEDLIG husleie fra og med om to dager: innenfor tretten
        # uker skal den forfalle tre eller fire ganger.
        _post(rt, t, belop=-8500000, forfall=_dag(2),
              gjentakelse="maanedlig")
        pid, _ = _prognose(rt, t, mid)
        _sett_kontekst(rt, t)
        rader = rt.execute(
            "SELECT uke_nr, ut_ore FROM m15_banen(%s,%s)"
            " WHERE ut_ore <> 0 ORDER BY uke_nr", (t, pid)).fetchall()
        _sett_kontekst(rt, t)
        siste = rt.execute(
            "SELECT punkt_ore FROM m15_banen(%s,%s)"
            " WHERE uke_nr = 13", (t, pid)).fetchone()[0]
    assert len(rader) >= 3, (
        f"en månedlig post traff bare {len(rader)} uker av tretten")
    for _uke, ut in rader:
        assert ut == -8500000
    # …OG SUMMEN SLÅR UT PÅ BANEN, ikke bare i en kolonne.
    assert siste <= -8500000 * len(rader) + 1, siste


@pg
def test_engang_forfaller_bare_en_gang():
    """«Engang» får et intervall på tusen år, og `generate_series` gir
    da nøyaktig én rad. Samme kodevei som de fire andre, og en gren
    mindre å ta feil i — men det må måles at den faktisk gir ÉN."""
    t = _tenantnavn("engang")
    with _to() as (rt, _mg):
        _krav(rt, t, horisont=13)
        mid = _modell(rt, t)
        _post(rt, t, belop=-5000000, forfall=_dag(2),
              gjentakelse="engang")
        pid, _ = _prognose(rt, t, mid)
        _sett_kontekst(rt, t)
        rader = rt.execute(
            "SELECT uke_nr FROM m15_banen(%s,%s) WHERE ut_ore <> 0",
            (t, pid)).fetchall()
    assert len(rader) == 1, f"en engangspost traff {len(rader)} uker"


@pg
def test_en_post_som_utloper_slutter_aa_forfalle():
    """`gjelder_til` ER EN SLUTT, ikke en pynt: en oppsagt avtale skal
    ikke fortsette å belaste banen etter at den er ute."""
    t = _tenantnavn("utlop")
    with _to() as (rt, mg):
        _krav(rt, t, horisont=13)
        mid = _modell(rt, t)
        _sett_kontekst(rt, t)
        rt.execute(
            "SELECT m15_registrer_post(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (t, uuid.uuid4(), "abonnement", "Verktoey som sies opp",
             -1000000, _dag(2), "maanedlig", _dag(40), "u-kari"))
        rt.commit()
        pid, _ = _prognose(rt, t, mid)
        _sett_kontekst(rt, t)
        rader = rt.execute(
            "SELECT uke_nr FROM m15_banen(%s,%s) WHERE ut_ore <> 0"
            " ORDER BY uke_nr", (t, pid)).fetchall()
    assert rader, "posten forfalt aldri"
    assert max(r[0] for r in rader) <= 7, (
        "en post som utløper etter 40 døgn belastet banen senere")


@pg
def test_forfall_i_dag_faller_ikke_mellom_to_uker():
    """PENGER MED FORFALL I DAG FALT INGEN STEDER (CodeRabbit, 129).

    Uke 1 hadde `fra = current_date`, og hvert ukepredikat var
    eksklusivt på nedre grense. En forpliktelse med forfall NØYAKTIG I
    DAG traff derfor ingen uke — det finnes ingen tidligere uke å falle
    i — og beløpet var ikke dekket noe annet sted heller:
    `startsaldo_ore` kommer fra `bankpost`, som bare holder BOKFØRTE
    bevegelser.

    SAMME FARLIGE RETNING SOM GJENTAKELSESFEILEN: banen undertalte det
    som skal ut, så kontantlinjen så bedre ut enn den er — på den ene
    dagen den betyr mest.

    MUTASJONEN SOM DREPER DENNE: sett `>` tilbake på nedre grense.
    """
    t = _tenantnavn("idag")
    with _to() as (rt, _mg):
        _krav(rt, t, horisont=13)
        mid = _modell(rt, t)
        _post(rt, t, belop=-7000000, forfall=I_DAG,
              gjentakelse="engang")
        pid, rad = _prognose(rt, t, mid)
        _sett_kontekst(rt, t)
        uke1 = rt.execute(
            "SELECT ut_ore, punkt_ore FROM m15_banen(%s,%s)"
            " WHERE uke_nr = 1", (t, pid)).fetchone()
    assert uke1[0] == -7000000, (
        "en regning som forfaller i dag traff ingen uke")
    assert uke1[1] == -7000000
    # …og døra rapporterte det laveste punktet riktig.
    assert rad[4] == -7000000


@pg
def test_ukevinduet_er_husets_egen_halvaapne_definisjon():
    """`[fra, til)` — SAMME SOM M-16 HAR HATT SIDEN FASE 2.

    En ny vindusaritmetikk i hver modul er selve feilen. M-16s §3 sier
    om sine egne kortspørringer at «ingen kortspørring har egen
    vindusaritmetikk»; denne modulen har det nå heller ikke.

    Porten måler at ukene verken overlapper eller etterlater hull: en
    dato tilhører NØYAKTIG én uke.
    """
    t = _tenantnavn("vindu")
    with _to() as (rt, _mg):
        _krav(rt, t, horisont=13)
        mid = _modell(rt, t)
        # Én post per dag de første femten dagene.
        for n in range(15):
            _post(rt, t, belop=-100, forfall=_dag(n),
                  gjentakelse="engang")
        pid, _ = _prognose(rt, t, mid)
        _sett_kontekst(rt, t)
        rader = rt.execute(
            "SELECT uke_nr, ut_ore, ukeslutt FROM m15_banen(%s,%s)"
            " ORDER BY uke_nr", (t, pid)).fetchall()
    # FEMTEN DAGER, SYV PER UKE: 7 + 7 + 1.
    assert rader[0][1] == -700, rader[0]
    assert rader[1][1] == -700, rader[1]
    assert rader[2][1] == -100, rader[2]
    assert sum(r[1] for r in rader) == -1500, "en dag falt bort"
    # `ukeslutt` ER SISTE FAKTISKE DAG, ikke den første i neste uke.
    assert rader[0][2] == _dag(6), rader[0][2]
    assert rader[1][2] == _dag(13), rader[1][2]


@pg
def test_en_uke_kan_ikke_maales_paa_sin_egen_siste_dag():
    """UKEN ER IKKE OVER FØR DAGEN ETTER SISTE DAG.

    Med `ukeslutt` som siste faktiske dag måtte begge lesningene
    flyttes: døra nekter nå på `>=`, og `kan_maales` krever `<`. En
    måling avgitt på ukens siste dag ville vært et delvis tall som ser
    ut som et endelig.
    """
    t = _tenantnavn("sistedag")
    with _to() as (rt, mg):
        _krav(rt, t)
        mid = _modell(rt, t)
        _post(rt, t)
        pid, _ = _prognose(rt, t, mid)
        # Flytt uke 1 slik at siste dag ER i dag.
        _aldre_prognose(mg, t, pid, 6)
        _sett_kontekst(rt, t)
        kan = rt.execute(
            "SELECT ukeslutt, kan_maales FROM m15_banen(%s,%s)"
            " WHERE uke_nr = 1", (t, pid)).fetchone()
        assert kan[0] == I_DAG, kan
        assert kan[1] is False, "ukens siste dag ble meldt målbar"
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute(
                "SELECT * FROM m15_registrer_maaling(%s,%s,%s,%s,%s,%s)",
                (t, pid, 1, -1, None, "u-kari"))


@pg
def test_hver_baneuke_slutter_paa_sin_egen_siste_dag():
    """129 ENDRET BETYDNINGEN AV `ukeslutt` UTEN Å ETTERFYLLE
    (CodeRabbit, MINOR pa #392).

    128 skrev `ukeslutt` som FORSTE dag i neste uke; 129 skriver den
    som SISTE dag i uken, og flippet begge leserne med. En rad skrevet
    av 128 ville derfor blitt malbar en dag for sent.

    JEG ETTERFYLTE IKKE, og grunnen skal sta:

      * `prognosebane` er append-only, handhevet for alle inkludert
        migrator. En korrigerende UPDATE matte slatt av vakten - og en
        migrasjon som slar av append-only-vakten for a rette et tall
        er noyaktig den dora tabellen finnes for a stenge.
      * Staging sto pa migrasjon 127 da 129 ble skrevet: tabellen
        eksisterte ikke der. En base som migrerer fra bunnen kjorer
        128 og 129 i samme pass, uten at noen rekker a lage en
        prognose mellom dem.

    HVA DENNE PORTEN KAN MALE, OG HVA DEN IKKE KAN: `prognosebane` har
    FORCE ROW LEVEL SECURITY, sa selv migrator ser bare sin egen
    tenant. Porten kan derfor IKKE revidere en etterlatt 128-rad hos
    en fremmed tenant - det sporsmalet er besvart av staging-beviset
    over, ikke av en test. Det den maler er SKRIVEREN: at
    `m15_lag_prognose` legger ukeslutt pa ukens siste dag, hver uke,
    slik at 128-konvensjonen ikke kan snike seg inn igjen.

    MUTASJONEN SOM DREPER DENNE: skriv `k.til` igjen i stedet for
    `k.til - 1` i `m15_lag_prognose`.
    """
    t = _tenantnavn("ukeslutt")
    with _to() as (rt, mg):
        _krav(rt, t, horisont=13)
        mid = _modell(rt, t)
        _post(rt, t)
        pid, _ = _prognose(rt, t, mid)
        _sett_kontekst(mg, t)
        rader = mg.execute(
            "SELECT count(*) FROM prognosebane WHERE tenant=%s"
            "   AND prognose_id=%s", (t, pid)).fetchone()[0]
        gale = mg.execute(
            "SELECT b.uke_nr, b.ukeslutt, p.laget_dato"
            "  FROM prognosebane b"
            "  JOIN likviditetsprognose p"
            "    ON p.tenant = b.tenant"
            "   AND p.prognose_id = b.prognose_id"
            " WHERE b.tenant=%s AND b.prognose_id=%s"
            "   AND b.ukeslutt"
            "       <> p.laget_dato + (b.uke_nr * 7) - 1",
            (t, pid)).fetchall()
        mg.rollback()
    # En tom bane ville gjort porten gronn uten a male noe.
    assert rader == 13, f"forventet 13 baneuker, fikk {rader}"
    assert gale == [], (
        "baneuker der `ukeslutt` ikke er ukens siste dag - enten er"
        f" 128-konvensjonen i live, eller 129 er reversert: {gale}")
