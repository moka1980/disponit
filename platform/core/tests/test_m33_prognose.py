"""M-33 prediksjons- og scenarioagent v1 (130) — KLYNGE 8s ANDRE.

Grensen `m33-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

KLYNGENS DELTE DOM, OG DEN FORMER HVER PORT:

  EN GAL PROGNOSE SER NØYAKTIG UT SOM EN RIKTIG PROGNOSE — HELT TIL
  HORISONTEN ER PASSERT, OG DA HAR ALLE SLUTTET Å SE.

M-33s EGEN DOM, OG DEN ER DEN VANSKELIGSTE Å MÅLE:

  EN MODELL SOM IKKE KAN TAPE, HAR IKKE VUNNET.

`slaar_ikke_naiv_baseline` er bare et ekte funn hvis modellen faktisk
KAN tape for basislinjen. En «prognose» som kopierte forrige uke ville
hatt null avvik mot basislinjen for alltid, invarianten ville vært
grønn i all evighet, og den ville ikke målt noe. Derfor er det ikke
nok å porte at funnet kan REISES — porten må vise at det også kan
LUKKES, altså at modellen kan vinne. En invariant som bare kan gå én
vei er en invariant uten innhold.

DEN TREDJE GRUPPEN PORTER MÅLER ET FRAVÆR: modulen tar ingen
personalavgjørelse. Ingen kolonne betyr «ansatt» eller «sagt opp»,
ingen status kan bli `iverksatt`, og ingen driftsfil importerer noe
som kan snakke ut.

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

PROGNOSESVEIP_DSN = os.environ.get("DISPONIT_TEST_PROGNOSESVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "130_m33_prognose.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "prognose.js")
FUNDAMENT = ROT / "docs" / "KLYNGE8-FUNDAMENT.md"
MODULFILER = (
    ROT / "platform" / "core" / "api" / "prognose.py",
    ROT / "platform" / "drift" / "prognosesveip.py",
    ROT / "platform" / "drift" / "kjor_prognosesveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("prognosekrav", "prognosemodell", "bemanningsprognose",
        "bemanningsbane", "bemanningsmaaling", "prognosefunn")

_STRENG = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")


def _bare_kode(fil: Path, *, uten_strenger: bool = False) -> str:
    """Filens innhold uten kommentarer OG uten docstrings (m24-formen).

    PORTEN SKAL MÅLE HANDLINGEN, IKKE BEGRUNNELSEN. En port som leter
    i rå filtekst treffer kommentaren som forklarer HVORFOR et mønster
    er unngått — og her er kommentarene fulle av ordene «ansett» og
    «personalavgjørelse», nettopp fordi modulen ikke gjør noen av
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
    return koble(PROGNOSESVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m33-{merke}-{secrets.token_hex(4)}"


I_DAG = datetime.date.today()


def _dag(n: int) -> datetime.date:
    return I_DAG + datetime.timedelta(days=n)


METODE = ("Glidende snitt over de siste hele ukene med foert tid;"
          " intervallet fra spredningen i de samme ukene.")


def _krav(c, tenant, *, horisont=4, grunnlag=8, maalefrist=14,
          domsgrunnlag=4, aktor="u-test", nokkel=None):
    _sett_kontekst(c, tenant)
    v = c.execute(
        "SELECT versjon FROM m33_sett_krav(%s,%s,%s,%s,%s,%s,%s)",
        (tenant, horisont, grunnlag, maalefrist, domsgrunnlag, aktor,
         nokkel or secrets.token_hex(8))).fetchone()[0]
    c.commit()
    return v


def _modell(c, tenant, *, navn="Glidende snitt", versjon="2026-01",
            metode=METODE, baselinje="samme som forrige uke",
            fra=None, til=None, aktor="u-test", modell_id=None):
    _sett_kontekst(c, tenant)
    mid = modell_id or uuid.uuid4()
    c.execute(
        "SELECT m33_registrer_modell(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant, mid, navn, versjon, metode, baselinje,
         fra or _dag(-30), til, aktor))
    c.commit()
    return mid


def _historikk(mg, tenant, *, uker=8, minutter=None):
    """Fører tid i `timeregistrering` — M-39s tabell, M-33s grunnlag.

    STIGENDE SOM STANDARD (100, 200, … per uke bakover snus til
    ferskest sist). Det er ikke pynt: med en stigende serie er snittet
    LAVERE enn forrige uke, og da er modellen og basislinjen
    forskjellige tall. En flat serie ville gjort dem like — og
    `slaar_ikke_naiv_baseline` til en invariant uten innhold.
    """
    _sett_kontekst(mg, tenant)
    tid = uuid.uuid4()
    mg.execute(
        "INSERT INTO lonnstaker (tenant, taker_id, ekstern_ref, navn,"
        " aktiv, opprettet_av) VALUES (%s,%s,%s,%s,true,%s)",
        (tenant, tid, f"E-{secrets.token_hex(3)}", "Testperson",
         "u-test"))
    for k in range(1, uker + 1):
        m = minutter[k - 1] if minutter else (uker + 1 - k) * 100
        # EN UKE DER INGEN JOBBET ER EN EKTE OBSERVASJON, og raden må
        # finnes: uten den ser døra ingen historikk i den uken, og
        # dekningsvinduet blir kortere enn det faktisk er. `None`
        # betyr «ingen registrering», 0 betyr «null minutter».
        if m is None:
            continue
        mg.execute(
            "INSERT INTO timeregistrering (tenant, time_id, taker_id,"
            " dato, minutter, prosjektkode, kilde, kilde_ref, notat,"
            " registrert_av)"
            " VALUES (%s,%s,%s,%s,%s,'P1','import',%s,'n',%s)",
            (tenant, uuid.uuid4(), tid, _dag(-(k * 7) + 1), m,
             f"r{k}-{secrets.token_hex(3)}", "u-test"))
    mg.commit()
    return tid


def _prognose(c, tenant, modell_id, *, aktor="u-test",
              prognose_id=None):
    _sett_kontekst(c, tenant)
    pid = prognose_id or uuid.uuid4()
    rad = c.execute(
        "SELECT * FROM m33_lag_prognose(%s,%s,%s,%s)",
        (tenant, pid, modell_id, aktor)).fetchone()
    c.commit()
    return pid, rad


def _aldre_prognose(mg, tenant, prognose_id, dogn):
    """Fabrikerer alderen med append-only-vakten AVSLÅTT.

    `laget_dato` settes av døra til `current_date`, og prognosen er
    append-only — også for migrator. Testen må derfor gå utenom, og
    gjør det SYNLIG i stedet for å finne en dør som ikke burde finnes.

    At denne hjelpefunksjonen er nødvendig, er selv et bevis: det
    finnes ingen lovlig vei til å endre en avgitt prognose.
    """
    mg.execute("ALTER TABLE bemanningsprognose DISABLE TRIGGER"
               " m33_evidensvakt")
    mg.execute("ALTER TABLE bemanningsbane DISABLE TRIGGER"
               " m33_evidensvakt")
    _sett_kontekst(mg, tenant)
    mg.execute(
        "UPDATE bemanningsprognose"
        "   SET laget_dato = laget_dato - %s::int,"
        "       gjelder_til = gjelder_til - %s::int"
        " WHERE tenant=%s AND prognose_id=%s",
        (dogn, dogn, tenant, prognose_id))
    mg.execute(
        "UPDATE bemanningsbane SET ukeslutt = ukeslutt - %s::int"
        " WHERE tenant=%s AND prognose_id=%s",
        (dogn, tenant, prognose_id))
    mg.execute("ALTER TABLE bemanningsprognose ENABLE TRIGGER"
               " m33_evidensvakt")
    mg.execute("ALTER TABLE bemanningsbane ENABLE TRIGGER"
               " m33_evidensvakt")
    mg.commit()


# ---------------------------------------------------------------------
# §0: hver invariant i `m33-v1` har minst én port.
# ---------------------------------------------------------------------

def test_grensen_er_registrert_og_hver_invariant_har_en_port():
    """§0-regelen, målt."""
    from manifestskjema import KRAVGRENSER
    inv = set(KRAVGRENSER["m33-v1"]["invarianter"])
    assert inv
    tekst = Path(__file__).read_text(encoding="utf-8")
    mangler = sorted(i for i in inv if i not in tekst)
    assert mangler == [], f"invarianter uten port: {mangler}"


# =====================================================================
# V1-DOMMEN: `modulen_utforte_handling`.
# =====================================================================

def test_modulen_ansatte_ingen_og_sa_ingen_opp():
    """Vaktsetningens fravær, målt der det kan brytes.

    «Ingen personalavgjørelse eller automatisk handling uten separat
    policy.» Det håndheves ikke av en sperre — det håndheves av at
    veien ikke finnes. Porten leser koden UTEN kommentarer og
    strenger: kommentarene her er fulle av ordet «personalavgjørelse»
    nettopp fordi modulen ikke tar noen.
    """
    forbudt = ("ansett", "si_opp", "oppsigelse", "avskjed",
               "permitter", "flytt_vakt", "iverksett")
    for fil in (MIGRASJON, *MODULFILER):
        kode = _bare_kode(fil, uten_strenger=True).lower()
        for ord_ in forbudt:
            assert ord_ not in kode, f"{fil.name} inneholder «{ord_}»"


def test_ingen_driftsfil_kan_snakke_ut():
    """Gjerdet står i koden, ikke i en kommentar.

    En sveip som kunne nå nettet, kunne meldt et bemanningsbehov
    videre til et lønns- eller vaktsystem — og da hadde modulen tatt
    en personalavgjørelse uten at noen valgte det.
    """
    for fil in MODULFILER:
        kode = _bare_kode(fil, uten_strenger=True)
        for modul in ("httpx", "requests", "socket", "urllib",
                      "smtplib", "http.client"):
            assert f"import {modul}" not in kode, f"{fil.name}: {modul}"


@pg
def test_ingen_dor_skriver_i_m39s_tabeller():
    """Modulen LESER timelistene og kan ikke røre dem.

    En prognosemodul som kunne skrive i `timeregistrering` ville
    kunnet «rette» virkeligheten til å passe prognosen — og det er den
    ene feilen ingen ville oppdaget, fordi treffraten da alltid ville
    vært perfekt.
    """
    with _mig() as mg:
        rader = mg.execute(
            "SELECT privilege_type FROM"
            " information_schema.table_privileges"
            " WHERE grantee='disponit_prognose_eier'"
            "   AND table_name IN ('timeregistrering','kvalitetsfunn',"
            "                      'kvalitetsprofil')").fetchall()
    typer = {r[0] for r in rader}
    assert typer == {"SELECT"}, f"modulrollen har mer enn SELECT: {typer}"


# =====================================================================
# `prognose_uten_horisont`, `_modellversjon`, `_intervall`.
# =====================================================================

@pg
def test_prognose_uten_horisont_er_urepresenterbar():
    """`horisont_uker` og `gjelder_til` er NOT NULL, og de henger sammen.

    En prognose uten et tidspunkt den kan etterprøves mot er ikke en
    prognose — det er en mening med tall i. GJORT UMULIG, IKKE
    OPPDAGET.
    """
    with _mig() as mg:
        rad = mg.execute(
            "SELECT count(*) FROM information_schema.columns"
            " WHERE table_name='bemanningsprognose'"
            "   AND column_name IN ('horisont_uker','gjelder_til')"
            "   AND is_nullable='NO'").fetchone()
        assert rad[0] == 2
        # …OG DE MÅ STEMME OVERENS. En horisont på 8 uker med en
        # sluttdato om 3 uker ville vært to påstander om det samme.
        sjekk = mg.execute(
            "SELECT count(*) FROM pg_constraint"
            " WHERE conrelid='bemanningsprognose'::regclass"
            "   AND conname='bemanningsprognose_horisont_stemmer'"
        ).fetchone()[0]
        assert sjekk == 1


@pg
def test_prognose_uten_modellversjon_er_urepresenterbar():
    """Snapshotet, ikke en fremmednøkkel til noe som kan endres.

    En modell KAN avvikles etter at prognosen er laget. Sto bare
    `modell_id` på raden, ville «hvilken versjon laget denne?» vært et
    oppslag i noe som har endret seg siden.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("modellversjon")
        _krav(rt, t)
        _historikk(mg, t)
        mid = _modell(rt, t, versjon="v-frosset")
        pid, _ = _prognose(rt, t, mid)
        _sett_kontekst(rt, t)
        # Avvikler modellen ETTERPÅ.
        rt.execute("SELECT m33_avvikle_modell(%s,%s,%s,%s)",
                   (t, mid, _dag(0), "u-test"))
        rt.commit()
        _sett_kontekst(mg, t)
        v = mg.execute(
            "SELECT modellversjon FROM bemanningsprognose"
            " WHERE tenant=%s AND prognose_id=%s",
            (t, pid)).fetchone()[0]
        mg.rollback()
    assert v == "v-frosset", "versjonen fulgte modellen i stedet for raden"


@pg
def test_prognose_uten_intervall_er_urepresenterbar():
    """`nedre` og `ovre` er NOT NULL — OG ALDRI LIKE NÅR PUNKTET ER > 0.

    `NOT NULL` alene er ikke nok: null er en gyldig verdi, og et bånd
    med bredde null er et PUNKT som later som det er et intervall.
    Det er nøyaktig løgnen `prognose_presentert_som_faktum` finnes for
    å hindre.

    Porten bruker en HELT FLAT historikk, der spredningen er null — og
    krever at intervallet likevel har bredde. Uten minstebredden i
    130 ville denne falt.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("intervall")
        _krav(rt, t)
        _historikk(mg, t, minutter=[600] * 8)
        mid = _modell(rt, t)
        pid, _ = _prognose(rt, t, mid)
        _sett_kontekst(rt, t)
        bane = rt.execute("SELECT * FROM m33_banen(%s,%s)",
                          (t, pid)).fetchall()
        rt.rollback()
    assert bane, "ingen bane"
    for u in bane:
        uke, _slutt, punkt, ned, opp = u[0], u[1], u[2], u[3], u[4]
        assert punkt == 600, f"uke {uke}: flat historikk ga {punkt}"
        assert ned < punkt < opp, (
            f"uke {uke}: baandet har bredde null ({ned}-{opp})")


# =====================================================================
# `prognose_uten_datakvalitetsflagg` — og skillet som er hele poenget.
# =====================================================================

@pg
def test_ren_og_ukjent_datakvalitet_er_ikke_samme_tilstand():
    """«Ingen funn» og «ingen har sett etter» er to ulike svar.

    En boolsk `data_ok` ville gjort det andre til det første, og
    prognosen ville båret et kvalitetsstempel ingen hadde utstedt.
    Samme feilform som `lukket_av`-dommen i 125: den stille
    standardverdien er den farlige.

    MUTASJONEN SOM DREPER DENNE: la `m33_datakvalitet` returnere
    `ren` når `kvalitetsprofil` er tom.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("ukjent")
        _krav(rt, t)
        _historikk(mg, t)
        mid = _modell(rt, t)
        _, rad = _prognose(rt, t, mid)
        assert rad[3] == "ukjent", (
            "en tenant M-3 aldri har profilert fikk et"
            f" kvalitetsstempel: {rad[3]}")

        # …og med en profil, men uten funn, er den REN.
        mg.execute("SET LOCAL ROLE disponit_kvalitet_eier")
        _sett_kontekst(mg, t)
        kid = uuid.uuid4()
        rid = f"m33.{secrets.token_hex(4)}"
        mg.execute(
            # REGELEN PEKER PÅ EN TABELL `disponit_kvalitet_eier`
            # SELV SER. `m3_regel_vakt` slår opp i
            # `information_schema.columns`, som er RETTIGHETSFILTRERT:
            # en kolonne rollen ikke har rett på, finnes ikke for den.
            # `timeregistrering` er M-39s, og kvalitetsrollen har
            # ingenting der — så en regel mot den ville blitt avvist
            # med «kolonnen finnes ikke». Det er ikke en feil i 092:
            # en tabell M-3 ikke kan lese, kan den heller ikke
            # profilere.
            "INSERT INTO kvalitetsregel (regel_id, relasjon, kolonne,"
            " regeltype, alvorlighet, terskel_andel, begrunnelse)"
            " VALUES (%s,'kvalitetsfunn','regel_id','ikke_tom',"
            "         'lav',0.0,'port for m33')"
            " ON CONFLICT DO NOTHING", (rid,))
        mg.execute(
            "INSERT INTO kvalitetskjoring (kjoring_id, startet_ts,"
            " fullfort_ts, antall_regler, antall_umaalbare,"
            " antall_funn, umaalbare_regler, avbrutt)"
            " VALUES (%s, now(), now(), 1, 0, 0, '{}', false)",
            (kid,))
        mg.execute(
            "INSERT INTO kvalitetsprofil (tenant, kjoring_id,"
            " regel_id, rader_vurdert, rader_avvik)"
            " VALUES (%s,%s,%s,10,0)", (t, kid, rid))
        mg.commit()

        _, rad2 = _prognose(rt, t, mid)
    assert rad2[3] == "ren", (
        f"en profilert tenant uten funn ble ikke ren: {rad2[3]}")


@pg
def test_datakvalitetsflagget_kan_ikke_lyve_om_sin_egen_teller():
    """`flagget` betyr at det FINNES funn — håndhevet av en CHECK.

    Uten den kunne en rad si «ren» og telle 12, eller «flagget» og
    telle 0. Et flagg som ikke stemmer med sin egen teller er verre
    enn intet flagg.
    """
    with _mig() as mg:
        with pytest.raises(psycopg.errors.CheckViolation):
            _sett_kontekst(mg, "t-m33-check")
            mg.execute(
                "INSERT INTO bemanningsprognose (tenant, prognose_id,"
                " laget_dato, horisont_uker, modell_id, modellversjon,"
                " baselinje, grunnlag_uker, grunnlag_siste_dato,"
                " grunnlag_antall_uker, datakvalitet,"
                " datakvalitet_antall, gjelder_til, laget_av)"
                " VALUES ('t-m33-check',%s,current_date,4,%s,'v','b',"
                "         8,current_date,8,'ren',12,"
                "         current_date+28,'u')",
                (uuid.uuid4(), uuid.uuid4()))
        mg.rollback()


# =====================================================================
# `slaar_ikke_naiv_baseline` OG `backtest_uten_baseline`.
# =====================================================================

@pg
def test_baneuke_uten_baselinje_er_urepresenterbar():
    """`backtest_uten_baseline`, gjort umulig.

    En baneuke uten basislinje kan aldri inngå i en backtest, og en
    modell som er DELVIS umålbar er en modell som kan gjemme sine
    dårligste uker.
    """
    with _mig() as mg:
        n = mg.execute(
            "SELECT count(*) FROM information_schema.columns"
            " WHERE table_name='bemanningsbane'"
            "   AND column_name='baseline_minutter'"
            "   AND is_nullable='NO'").fetchone()[0]
    assert n == 1


@pg
def test_modellen_og_basislinjen_er_forskjellige_tall():
    """EN MODELL SOM IKKE KAN TAPE, HAR IKKE VUNNET.

    Dette er porten hele modulen hviler på. Med stigende historikk
    (100 … 800 minutter per uke bakover) er snittet 450 og
    basislinjen 800 — altså to ULIKE tall. Var de like, ville
    modellavviket og basislinjeavviket alltid vært identiske,
    `slaar_ikke_naiv_baseline` ville aldri kunnet reises, og
    invarianten ville vært grønn uten å måle noe.

    MUTASJONEN SOM DREPER DENNE: la `m33_lag_prognose` sette
    `forventet_minutter = v_baseline`.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("ulike")
        _krav(rt, t)
        _historikk(mg, t)
        mid = _modell(rt, t)
        pid, rad = _prognose(rt, t, mid)
        _sett_kontekst(rt, t)
        bane = rt.execute("SELECT * FROM m33_banen(%s,%s)",
                          (t, pid)).fetchall()
        rt.rollback()
    punkt, baseline = bane[0][2], bane[0][5]
    assert baseline == 800, f"basislinjen er ikke forrige uke: {baseline}"
    assert punkt == 450, f"snittet er ikke snittet: {punkt}"
    assert punkt != baseline, (
        "modellen er sin egen basislinje — funnet kan aldri reises")
    assert rad[4] == baseline


@pg
def test_sveipen_feller_dom_naar_modellen_taper_og_lukker_naar_den_vinner():
    """Funnet må kunne gå BEGGE veier, ellers måler det ingenting.

    Første halvdel: fire målte uker der det faktiske (900) ligger
    nærmere basislinjen (800) enn modellen (450) → funnet reises.

    Andre halvdel — OG DEN ER LIKE VIKTIG: en ny modellversjon der
    målingene ligger nærmere modellen → funnet finnes ikke for den
    versjonen. En invariant som bare kan gå én vei er en invariant
    uten innhold.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("dom")
        _krav(rt, t, domsgrunnlag=4)
        _historikk(mg, t)
        mid = _modell(rt, t, versjon="taper")
        pid, _ = _prognose(rt, t, mid)
        _aldre_prognose(mg, t, pid, 60)
        _sett_kontekst(rt, t)
        for uke in range(1, 5):
            rt.execute(
                "SELECT m33_registrer_maaling(%s,%s,%s,%s,%s)",
                (t, pid, uke, 900, "u-test"))
        rt.commit()
        with _sv() as sv:
            sv.execute("SELECT * FROM m33_sveip_prognose(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        funn = mg.execute(
            "SELECT referanse, over_grense FROM prognosefunn"
            " WHERE tenant=%s AND funntype='slaar_ikke_naiv_baseline'"
            "   AND apen", (t,)).fetchall()
        assert funn == [("taper", 1400)], (
            f"modellen tapte, men dommen uteble: {funn}")

        # ANDRE HALVDEL: en versjon der modellen VINNER.
        mid2 = _modell(rt, t, versjon="vinner",
                       modell_id=uuid.uuid4())
        pid2, _ = _prognose(rt, t, mid2)
        _aldre_prognose(mg, t, pid2, 60)
        _sett_kontekst(rt, t)
        for uke in range(1, 5):
            # 460 ligger 10 fra modellen (450) og 340 fra
            # basislinjen (800).
            rt.execute(
                "SELECT m33_registrer_maaling(%s,%s,%s,%s,%s)",
                (t, pid2, uke, 460, "u-test"))
        rt.commit()
        with _sv() as sv:
            sv.execute("SELECT * FROM m33_sveip_prognose(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        vant = mg.execute(
            "SELECT count(*) FROM prognosefunn"
            " WHERE tenant=%s AND funntype='slaar_ikke_naiv_baseline'"
            "   AND referanse='vinner' AND apen", (t,)).fetchone()[0]
        mg.rollback()
    assert vant == 0, "en modell som slår basislinjen fikk dom likevel"


@pg
def test_dommen_felles_ikke_for_domsgrunnlaget_er_naadd():
    """Å kalle en modell dårlig etter én uke er å forveksle støy med
    kunnskap.

    `domsgrunnlag_uker` er tenantens, og porten setter den til 4 og
    måler bare 3 uker. Ingen dom skal falle.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("forfort")
        _krav(rt, t, domsgrunnlag=4)
        _historikk(mg, t)
        mid = _modell(rt, t, versjon="for-tidlig")
        pid, _ = _prognose(rt, t, mid)
        _aldre_prognose(mg, t, pid, 60)
        _sett_kontekst(rt, t)
        for uke in range(1, 4):
            rt.execute(
                "SELECT m33_registrer_maaling(%s,%s,%s,%s,%s)",
                (t, pid, uke, 900, "u-test"))
        rt.commit()
        with _sv() as sv:
            sv.execute("SELECT * FROM m33_sveip_prognose(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        n = mg.execute(
            "SELECT count(*) FROM prognosefunn WHERE tenant=%s"
            "   AND funntype='slaar_ikke_naiv_baseline'",
            (t,)).fetchone()[0]
        mg.rollback()
    assert n == 0, "dommen falt på 3 uker der grunnlaget krever 4"


@pg
def test_likhet_teller_som_tap():
    """En modell som er NØYAKTIG like god som basislinjen har tapt.

    Den har ikke tilført noe, og den koster tillit — fordi den ser ut
    som analyse. `>=`, ikke `>`.

    Målingen 625 ligger 175 fra både modellen (450) og basislinjen
    (800). Avvikene er identiske.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("likhet")
        _krav(rt, t, domsgrunnlag=4)
        _historikk(mg, t)
        mid = _modell(rt, t, versjon="uavgjort")
        pid, _ = _prognose(rt, t, mid)
        _aldre_prognose(mg, t, pid, 60)
        _sett_kontekst(rt, t)
        for uke in range(1, 5):
            rt.execute(
                "SELECT m33_registrer_maaling(%s,%s,%s,%s,%s)",
                (t, pid, uke, 625, "u-test"))
        rt.commit()
        _sett_kontekst(mg, t)
        avvik = mg.execute(
            "SELECT sum(avvik_minutter), sum(baseline_avvik_minutter)"
            "  FROM bemanningsmaaling WHERE tenant=%s", (t,)).fetchone()
        assert avvik[0] == avvik[1], f"ikke uavgjort: {avvik}"
        with _sv() as sv:
            sv.execute("SELECT * FROM m33_sveip_prognose(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        n = mg.execute(
            "SELECT count(*) FROM prognosefunn WHERE tenant=%s"
            "   AND funntype='slaar_ikke_naiv_baseline' AND apen",
            (t,)).fetchone()[0]
        mg.rollback()
    assert n == 1, "uavgjort ble regnet som seier"


# =====================================================================
# `prognose_uten_maaling` — funnet ingen kan klikke bort.
# =====================================================================

@pg
def test_umaalt_uke_reises_og_lukkes_bare_av_maalingen():
    """Klyngens funn, og den eneste veien ut av det.

    Sveipen reiser funnet når uken er over med målefristen, og lukker
    det når — og bare når — målingen faktisk kommer.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("umaalt")
        _krav(rt, t, horisont=4, maalefrist=14)
        _historikk(mg, t)
        mid = _modell(rt, t)
        pid, _ = _prognose(rt, t, mid)
        _aldre_prognose(mg, t, pid, 60)
        with _sv() as sv:
            sv.execute("SELECT * FROM m33_sveip_prognose(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        apne = mg.execute(
            "SELECT count(*) FROM prognosefunn WHERE tenant=%s"
            "   AND funntype='prognose_uten_maaling' AND apen",
            (t,)).fetchone()[0]
        assert apne == 4, f"fire uker er over, {apne} funn"

        _sett_kontekst(rt, t)
        rt.execute("SELECT m33_registrer_maaling(%s,%s,%s,%s,%s)",
                   (t, pid, 1, 700, "u-test"))
        rt.commit()
        with _sv() as sv:
            sv.execute("SELECT * FROM m33_sveip_prognose(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        etter = mg.execute(
            "SELECT count(*) FILTER (WHERE apen),"
            "       count(*) FILTER (WHERE NOT apen AND"
            "                        lukket_av='m33_sveip')"
            "  FROM prognosefunn WHERE tenant=%s"
            "   AND funntype='prognose_uten_maaling'",
            (t,)).fetchone()
        mg.rollback()
    assert etter == (3, 1), f"lukkingen fulgte ikke målingen: {etter}"


@pg
def test_et_menneske_kan_ikke_lukke_sveipens_funn():
    """`prognose_uten_maaling` og `slaar_ikke_naiv_baseline` lukkes av
    at TILSTANDEN opphører, ikke av at noen huker av.

    Kunne et menneske lukket dem, ville klyngens dom vært en
    anbefaling.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("nekt")
        _krav(rt, t, horisont=4)
        _historikk(mg, t)
        mid = _modell(rt, t)
        pid, _ = _prognose(rt, t, mid)
        _aldre_prognose(mg, t, pid, 60)
        with _sv() as sv:
            sv.execute("SELECT * FROM m33_sveip_prognose(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        fid = mg.execute(
            "SELECT funn_id FROM prognosefunn WHERE tenant=%s"
            "   AND funntype='prognose_uten_maaling' LIMIT 1",
            (t,)).fetchone()[0]
        mg.rollback()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT m33_lukk_funn(%s,%s,%s,%s)",
                       (t, fid, "vi tar det senere", "u-test"))
        rt.rollback()


@pg
def test_ukjent_datakvalitet_KAN_lukkes_av_et_menneske():
    """Og det er riktig.

    «Vi vet at M-3 aldri har sett på dette, vi planlegger likevel» er
    en legitim beslutning — med et navn på. Funnet lukkes derfor av et
    menneske og aldri av sveipen: `datakvalitet` står på en
    append-only rad og kan ikke endre seg, så «tilstanden opphørte»
    kan ikke inntreffe.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("ukjentlukk")
        _krav(rt, t)
        _historikk(mg, t)
        mid = _modell(rt, t)
        _prognose(rt, t, mid)
        with _sv() as sv:
            sv.execute("SELECT * FROM m33_sveip_prognose(500)")
            sv.commit()
        # LEST GJENNOM DØRA, ikke gjennom hjelpefunksjonen.
        # `kan_lukkes` er det FLATEN faktisk ser, og en port som
        # kalte `m33_funn_er_sveipens` direkte ville målt et internt
        # ledd i stedet for kontrakten.
        _sett_kontekst(rt, t)
        rader = [r for r in rt.execute(
            "SELECT * FROM m33_prognosefunn(%s,%s)",
            (t, 100)).fetchall()
            if r[1] == "prognose_paa_ukjent_datakvalitet"]
        rt.rollback()
        assert rader, "funnet ble ikke reist"
        fid, kan = rader[0][0], rader[0][9]
        assert kan is True
        del mg
        _sett_kontekst(rt, t)
        apen = rt.execute(
            "SELECT apen FROM m33_lukk_funn(%s,%s,%s,%s)",
            (t, fid, "vi vet, vi planlegger likevel",
             "u-test")).fetchone()[0]
        rt.commit()
    assert apen is False


@pg
def test_en_lukking_uten_navn_er_urepresenterbar():
    """125s lærdom, innebygd fra fødselen.

    En tom aktør ville gitt `false OR NULL` = NULL i CHECKen, og NULL
    i en `NOT NULL`-kolonne dreper HELE transaksjonen — i sveipen
    betyr det at ett navnløst kall river med seg alle lukkingene i
    samme kjøring. To lag: døra nekter, og CHECKen gjør raden umulig.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("navnlos")
        _krav(rt, t)
        _historikk(mg, t)
        mid = _modell(rt, t)
        _prognose(rt, t, mid)
        with _sv() as sv:
            sv.execute("SELECT * FROM m33_sveip_prognose(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        fid = mg.execute(
            "SELECT funn_id FROM prognosefunn WHERE tenant=%s"
            "   AND funntype='prognose_paa_ukjent_datakvalitet'"
            " LIMIT 1", (t,)).fetchone()[0]
        # LAG 1: døra.
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT m33_lukk_funn(%s,%s,%s,%s)",
                       (t, fid, "en grunn", "   "))
        rt.rollback()
        # LAG 2: CHECKen, som holder selv om noen skriver utenom døra.
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.CheckViolation):
            mg.execute(
                "UPDATE prognosefunn SET apen=false, lukket_ts=now()"
                " WHERE tenant=%s AND funn_id=%s", (t, fid))
        mg.rollback()


# =====================================================================
# MÅLINGEN: ukorrigerbar, og ikke før uken er over.
# =====================================================================

@pg
def test_en_uke_som_ikke_er_over_kan_ikke_males():
    """129s dom, innebygd fra fødselen.

    `ukeslutt` er ukens SISTE dag, så uken er ferdig først når
    `ukeslutt < current_date`. Å måle en uke som ennå løper er å
    registrere et delresultat som et sluttresultat — og siden
    målingen er ukorrigerbar, ville det tallet stått for evig.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("forfrist")
        _krav(rt, t)
        _historikk(mg, t)
        mid = _modell(rt, t)
        pid, _ = _prognose(rt, t, mid)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT m33_registrer_maaling(%s,%s,%s,%s,%s)",
                       (t, pid, 1, 700, "u-test"))
        rt.rollback()


@pg
def test_ukeslutt_er_ukens_siste_dag_ikke_neste_ukes_forste():
    """129s LÆRDOM, TATT MED FRA FØDSELEN.

    M-15 skrev først `til` i et halvåpent vindu `[fra, til)` inn i en
    kolonne som HETER «slutt», og det kostet en egen migrasjon (129)
    pluss en port (#393). Her er det riktig fra første linje — og
    porten står slik at det ikke kan gli.

    MUTASJONEN SOM DREPER DENNE: `(v_dato + (u.n * 7))` uten `- 1`.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("ukeslutt")
        _krav(rt, t, horisont=4)
        _historikk(mg, t)
        mid = _modell(rt, t)
        pid, _ = _prognose(rt, t, mid)
        _sett_kontekst(mg, t)
        rader = mg.execute(
            "SELECT b.uke_nr, b.ukeslutt, p.laget_dato"
            "  FROM bemanningsbane b JOIN bemanningsprognose p"
            "    ON p.tenant=b.tenant AND p.prognose_id=b.prognose_id"
            " WHERE b.tenant=%s AND b.prognose_id=%s"
            "   AND b.ukeslutt <> p.laget_dato + (b.uke_nr*7) - 1",
            (t, pid)).fetchall()
        mg.rollback()
    assert rader == [], f"ukeslutt er ikke ukens siste dag: {rader}"


@pg
def test_en_maaling_kan_ikke_rettes():
    """En måling som lot seg justere er en måling som alltid bekrefter.

    To lag igjen: døra nekter en ny måling på samme uke, og
    append-only-vakten nekter en UPDATE — også for migrator.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("ukorrigerbar")
        _krav(rt, t)
        _historikk(mg, t)
        mid = _modell(rt, t)
        pid, _ = _prognose(rt, t, mid)
        _aldre_prognose(mg, t, pid, 60)
        _sett_kontekst(rt, t)
        rt.execute("SELECT m33_registrer_maaling(%s,%s,%s,%s,%s)",
                   (t, pid, 1, 700, "u-test"))
        rt.commit()
        # `SET LOCAL` forsvinner med transaksjonen. Konteksten settes
        # på nytt fordi den ellers ville gitt et NEKT som ser ut som
        # dommen porten leter etter — og porten hadde vært grønn av
        # feil grunn.
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.UniqueViolation):
            rt.execute("SELECT m33_registrer_maaling(%s,%s,%s,%s,%s)",
                       (t, pid, 1, 999, "u-test"))
        rt.rollback()
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            mg.execute(
                "UPDATE bemanningsmaaling SET faktisk_minutter=1"
                " WHERE tenant=%s AND prognose_id=%s", (t, pid))
        mg.rollback()


@pg
def test_treffet_regnes_av_bandet_ikke_av_kalleren():
    """Hadde kalleren fått si «ja, dette var innenfor», ville målingen
    vært en karakter modulen ga seg selv."""
    with _to() as (rt, mg):
        t = _tenantnavn("treff")
        _krav(rt, t)
        _historikk(mg, t)
        mid = _modell(rt, t)
        pid, _ = _prognose(rt, t, mid)
        _aldre_prognose(mg, t, pid, 60)
        _sett_kontekst(rt, t)
        bane = rt.execute("SELECT * FROM m33_banen(%s,%s)",
                          (t, pid)).fetchone()
        ned, opp = bane[3], bane[4]
        inni = rt.execute(
            "SELECT innenfor_intervall FROM"
            " m33_registrer_maaling(%s,%s,%s,%s,%s)",
            (t, pid, 1, (ned + opp) // 2, "u-test")).fetchone()[0]
        utenfor = rt.execute(
            "SELECT innenfor_intervall FROM"
            " m33_registrer_maaling(%s,%s,%s,%s,%s)",
            (t, pid, 2, opp + 1, "u-test")).fetchone()[0]
        rt.commit()
    assert inni is True and utenfor is False


# =====================================================================
# `prognose_overskrevet` OG `tenantlekkasje_i_prognoseregister`.
# =====================================================================

@pg
def test_en_avgitt_prognose_kan_ikke_endres():
    """`prognose_overskrevet`, gjort umulig — også for migrator.

    En prognose som kan justeres i etterkant er en prognose som alltid
    stemmer.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("frosset")
        _krav(rt, t)
        _historikk(mg, t)
        mid = _modell(rt, t)
        pid, _ = _prognose(rt, t, mid)
        _sett_kontekst(mg, t)
        for setning in (
                "UPDATE bemanningsprognose SET horisont_uker=99",
                "DELETE FROM bemanningsprognose",
                "UPDATE bemanningsbane SET forventet_minutter=0",
                "DELETE FROM bemanningsbane"):
            # KONTEKSTEN SETTES PÅ NYTT FOR HVER RUNDE. `SET LOCAL`
            # dør med transaksjonen, og uten konteksten ville radvakten
            # skjult raden — da hadde setningen truffet null rader og
            # porten vært grønn uten at vakten ble prøvd.
            _sett_kontekst(mg, t)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                mg.execute(setning + " WHERE tenant=%s AND"
                           " prognose_id=%s", (t, pid))
            mg.rollback()


@pg
def test_modellens_identitet_er_frosset_og_avvikling_er_enveis():
    """121s dom. Bare `gyldig_til` kan settes, og bare én gang.

    En modell som kunne gjenoppvekkes ville gjort «hvilken modell
    gjaldt da?» ubesvarlig.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("modellfrys")
        _krav(rt, t)
        mid = _modell(rt, t)
        _sett_kontekst(mg, t)
        mg.execute("SET LOCAL ROLE disponit_prognose_eier")
        for felt in ("navn='nytt'", "versjon='v2'",
                     "baselinje='noe annet'", "gyldig_fra=current_date"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                mg.execute(f"UPDATE prognosemodell SET {felt}"
                           " WHERE tenant=%s AND modell_id=%s",
                           (t, mid))
            mg.rollback()
            mg.execute("SET LOCAL ROLE disponit_prognose_eier")
            _sett_kontekst(mg, t)
        mg.rollback()
        _sett_kontekst(rt, t)
        rt.execute("SELECT m33_avvikle_modell(%s,%s,%s,%s)",
                   (t, mid, _dag(0), "u-test"))
        rt.commit()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT m33_avvikle_modell(%s,%s,%s,%s)",
                       (t, mid, _dag(5), "u-test"))
        rt.rollback()


@pg
def test_tenantlekkasje_i_prognoseregister_er_umulig():
    """FORCE ROW LEVEL SECURITY på alle seks.

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
def test_en_tenant_ser_ikke_en_annens_prognose():
    """Radvakten, målt og ikke bare erklært."""
    with _to() as (rt, mg):
        a, b = _tenantnavn("a"), _tenantnavn("b")
        _krav(rt, a)
        _historikk(mg, a)
        mid = _modell(rt, a)
        pid, _ = _prognose(rt, a, mid)
        _sett_kontekst(rt, b)
        n = rt.execute(
            "SELECT count(*) FROM m33_prognoseregister(%s,%s)",
            (b, 100)).fetchone()[0]
        bane = rt.execute("SELECT count(*) FROM m33_banen(%s,%s)",
                          (b, pid)).fetchone()[0]
        rt.rollback()
    assert n == 0 and bane == 0


# =====================================================================
# DØRENES NEKT.
# =====================================================================

@pg
def test_prognose_uten_historikk_nektes():
    """NULL ARBEID ER IKKE DET SAMME SOM INGEN DATA.

    En tenant uten en eneste timeregistrering ville fått
    `forventet_minutter = 0` av et snitt over ingenting — og «null
    timer neste uke» fordi ingen har ført timer, er den reneste
    formen for `prognose_presentert_som_faktum`: modellen påstår noe
    om virkeligheten når den bare har målt sin egen tomhet.

    Samme feilform som `Number("")` → 0 i M-15s flate, og som `ren`
    versus `ukjent`: TOMHET SOM BLIR TIL ET TALL.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("tomhet")
        _krav(rt, t)
        mid = _modell(rt, t)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.NoDataFound):
            rt.execute("SELECT m33_lag_prognose(%s,%s,%s,%s)",
                       (t, uuid.uuid4(), mid, "u-test"))
        rt.rollback()
        del mg


@pg
def test_prognose_uten_krav_nektes():
    """Uten horisonten finnes det ingen dato å måle mot, og da er
    `prognose_uten_maaling` et funn som aldri kan reises."""
    with _to() as (rt, mg):
        t = _tenantnavn("utenkrav")
        _historikk(mg, t)
        _sett_kontekst(rt, t)
        mid = uuid.uuid4()
        rt.execute(
            "SELECT m33_registrer_modell(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (t, mid, "M", "v1", METODE, "samme som forrige uke",
             _dag(-10), None, "u-test"))
        rt.commit()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.NoDataFound):
            rt.execute("SELECT m33_lag_prognose(%s,%s,%s,%s)",
                       (t, uuid.uuid4(), mid, "u-test"))
        rt.rollback()


@pg
def test_prognose_mot_avviklet_modell_nektes_men_arkivet_tar_imot_den():
    """Skillet går ved BRUKEN, ikke ved registreringen.

    Arkivet skal kunne svare på hvilken modell som gjaldt den gangen;
    det er prognosen som ikke får hvile på en avviklet versjon.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("avviklet")
        _krav(rt, t)
        _historikk(mg, t)
        # REGISTRERING av en alt avviklet versjon: tillatt.
        gammel = _modell(rt, t, versjon="v-gammel", fra=_dag(-100),
                         til=_dag(-50))
        _sett_kontekst(rt, t)
        n = rt.execute("SELECT count(*) FROM m33_modellregister(%s)",
                       (t,)).fetchone()[0]
        assert n == 1
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT m33_lag_prognose(%s,%s,%s,%s)",
                       (t, uuid.uuid4(), gammel, "u-test"))
        rt.rollback()


@pg
def test_kravdora_er_idempotent_og_versjonen_oker_bare_ved_endring():
    """M-51s lærdom (119), gjentatt gjennom seks moduler.

    En versjon som økte for hvert gjenspill ville gjort
    funnhistorikken uleselig.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("idem")
        n = secrets.token_hex(8)
        v1 = _krav(rt, t, horisont=4, nokkel=n)
        v2 = _krav(rt, t, horisont=4, nokkel=n)
        v3 = _krav(rt, t, horisont=4, nokkel=secrets.token_hex(8))
        v4 = _krav(rt, t, horisont=6, nokkel=secrets.token_hex(8))
        del mg
    assert (v1, v2, v3, v4) == (1, 1, 1, 2)


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
    """111s form. En sveiperolle med flere dører ville båret en
    fullmakt ingen hadde bedt om."""
    with _mig() as mg:
        rader = mg.execute(
            "SELECT p.proname FROM pg_proc p"
            " WHERE p.proname LIKE 'm33\\_%'"
            "   AND has_function_privilege('disponit_prognosesveip',"
            "                              p.oid, 'EXECUTE')"
        ).fetchall()
    assert sorted(r[0] for r in rader) == ["m33_sveip_prognose"]


@pg
def test_ingen_m33_funksjon_er_immutable_naar_den_leser_naa():
    """125s LÆRDOM, og porten leser HELE migrasjonskatalogen.

    Planleggeren har LOV til å folde en IMMUTABLE funksjon til en
    konstant og gjenbruke den i en bufret plan. En `IMMUTABLE`
    funksjon som leser `current_date` gir derfor gårsdagens svar i
    morgen — og det er en gyldighetsregel som slutter å virke uten å
    feile.
    """
    with _mig() as mg:
        rader = mg.execute(
            "SELECT p.proname FROM pg_proc p"
            " WHERE p.provolatile='i'"
            "   AND (pg_get_functiondef(p.oid) ILIKE '%current_date%'"
            "     OR pg_get_functiondef(p.oid) ILIKE '%now()%')"
            "   AND p.pronamespace='public'::regnamespace"
        ).fetchall()
    assert rader == [], f"IMMUTABLE funksjoner som leser nå: {rader}"


@pg
def test_sveipen_ser_ingenting_uten_kryss_tenant_policyen():
    """DEN FARLIGSTE FEILFORMEN EN SVEIP KAN HA.

    Tabellene har `FORCE ROW LEVEL SECURITY`. En sveip som spurte på
    tvers uten `disponit.tenant` ville sett NULL RADER og rapportert
    null funn — med grønn exit-kode. Første utkast av 130 gjorde
    nettopp det.

    Porten måler at policyen finnes OG at den bare gjelder når
    konteksten er TOM: en policy som slapp gjennom uansett ville vært
    et hull, ikke et gjerde.
    """
    with _mig() as mg:
        rad = mg.execute(
            "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy"
            " WHERE polrelid='prognosekrav'::regclass"
            "   AND polname='m33_sveip_tenantliste'").fetchone()
    assert rad, "kryss-tenant-policyen mangler — sveipen ville vært blind"
    assert "IS NULL" in rad[0], f"policyen er ikke snever nok: {rad[0]}"


@pg
def test_sveipen_teller_tenanter_og_gir_fire_felt():
    """Kontrakten driftsfila leser."""
    with _to() as (rt, mg):
        t = _tenantnavn("kontrakt")
        _krav(rt, t)
        _historikk(mg, t)
        mid = _modell(rt, t)
        _prognose(rt, t, mid)
        with _sv() as sv:
            rader = sv.execute(
                "SELECT * FROM m33_sveip_prognose(500)").fetchall()
            sv.commit()
    assert len(rader) == 1, f"sveipen ga {len(rader)} rader, ikke én"
    assert len(rader[0]) == 4, f"kontrakten ga {len(rader[0])} felt"
    assert rader[0][0] >= 1


def test_sveipens_arbeidernokkel_er_modulens_egen():
    """To sveip som delte nøkkel ville blokkert hverandre i stillhet."""
    import importlib
    nokler = {}
    katalog = ROT / "platform" / "drift"
    for fil in sorted(katalog.glob("*sveip.py")):
        kode = fil.read_text(encoding="utf-8")
        m = re.search(r"ARBEIDERNOKKEL = ([\d_]+)", kode)
        if m:
            nokler.setdefault(m.group(1), []).append(fil.name)
    delte = {k: v for k, v in nokler.items() if len(v) > 1}
    assert delte == {}, f"delte arbeidernøkler: {delte}"
    del importlib


def test_driftsfila_navngir_sin_egen_jobb():
    """Arvefeilen fra 116-118: en beskrivelse som er kopiert fra
    naboen forteller journalen hva NABOEN gjør."""
    tj = (ROT / "deploy" / "staging"
          / "disponit-prognosesveip.service").read_text(encoding="utf-8")
    beskrivelse = tj.split("Description=")[1][:90]
    assert "prognosesveip" in beskrivelse
    for arvet in ("likviditet", "kontantbane", "adresser", "EHF",
                  "uavklarte treff", "HMS"):
        assert arvet not in beskrivelse, f"arvet ord: {arvet}"
    assert ("LoadCredential=DISPONIT_PROGNOSESVEIP_URL:"
            "/etc/disponit/prognosesveip/DISPONIT_PROGNOSESVEIP_URL"
            in tj)


def test_timeren_gaar_etter_hele_stigen():
    """SVEIPESTATUS SKAL LESE FLÅTEN ETTER AT FLÅTEN HAR KJØRT.

    M-33 er den FØRSTE timeren bak 11:20, og derfor flytter denne
    PR-en `disponit-sveipestatus.timer` til 12:05 — akkurat som
    klynge 8-fundamentet sa at den skulle, og i den PR-en som faktisk
    gjør flyttingen nødvendig.

    Porten leser KLOKKESLETTENE og sammenligner. Et literal ville
    måttet rettes hver gang stigen vokser, og da måler det ikke
    rekkefølgen — det måler at noen husket å redigere testen.
    """
    katalog = ROT / "deploy" / "staging"
    status = (katalog / "disponit-sveipestatus.timer").read_text(
        encoding="utf-8")
    bak = re.search(r"OnCalendar=\*-\*-\* (\d\d:\d\d):00 UTC", status)
    assert bak, "sveipestatus har intet klokkeslett"
    for fil in sorted(katalog.glob("disponit-*sveip.timer")):
        tekst = fil.read_text(encoding="utf-8")
        m = re.search(r"OnCalendar=\*-\*-\* (\d\d:\d\d):00 UTC", tekst)
        if not m:
            continue
        assert m.group(1) < bak.group(1), (
            f"{fil.name} ({m.group(1)}) kjører etter sveipestatus"
            f" ({bak.group(1)}) — statusen ville meldt den uteblitt")
    egen = (katalog / "disponit-prognosesveip.timer").read_text(
        encoding="utf-8")
    assert "OnCalendar=*-*-* 11:35:00 UTC" in egen
    assert "Persistent=true" in egen


def test_sveipens_dsn_star_i_ci():
    """127s LÆRDOM, som kostet en rød CI.

    En sveiperolle uten DSN i `ci.yml` faller tilbake til migrator, og
    da måler portene en base der SP-7-skillet ikke finnes: fem porter
    var grønne lokalt og røde i CI. Navnet hentes fra KJØRERen, ikke
    fra filnavnet.
    """
    kjorer = ROT / "platform" / "drift" / "kjor_prognosesveip.py"
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
# FLATEN OG API-ET.
# =====================================================================

def test_flaten_har_ingen_knapp_som_tar_en_personalavgjorelse():
    """Porten leser flaten UTEN strenger.

    128s lærdom: en tidligere port traff LOCALE-NØKKELEN som SIER at
    modulen ikke utfører noe — altså målte den forklaringen, ikke
    oppførselen.
    """
    kode = _bare_kode(FLATE, uten_strenger=True).lower()
    for ord_ in ("ansett", "siopp", "si_opp", "iverksett",
                 "flyttvakt", "permitter"):
        assert ord_ not in kode, f"flaten har «{ord_}»"


def test_flaten_tegner_alltid_bandet_ved_siden_av_punktet():
    """`prognose_presentert_som_faktum`, målt i flaten.

    Et punktestimat uten usikkerhet er ikke en presis prognose — det
    er en upresis prognose som har mistet informasjonen om hvor
    upresis den er.
    """
    kode = FLATE.read_text(encoding="utf-8")
    assert "ui.prognose.baand_verdi" in kode
    # BÅNDET STÅR I SAMME `banetabell`-rad som punktet, ikke i et
    # panel man må åpne.
    tabell = kode.split("export function banetabell")[1].split(
        "export function")[0]
    for felt in ("forventet_minutter", "nedre_minutter",
                 "ovre_minutter", "baseline_minutter"):
        assert felt in tabell, f"{felt} mangler i banetabellen"


def test_flaten_leser_kan_maales_fra_basen():
    """124s `kan_lukkes`-form. Regnet flaten det ut selv, ville
    knappen blitt aktiv en dag før døra sier ja."""
    kode = _bare_kode(FLATE, uten_strenger=True)
    assert "u.kan_maales" in kode
    assert "Date" not in kode.split("export function banetabell")[1].split(
        "export function")[0]


def test_apiet_gir_hele_bildet_i_ett_kall():
    """127s CodeRabbit-funn, ikke gjentatt: flaten leser fire lister,
    og fire runder ville gitt fire mulige halvtegnede skjermer."""
    from api import prognose as modul
    kilde = _bare_kode(Path(modul.__file__))
    for del_ in ("sammendrag", "prognoser", "modeller", "funn"):
        assert f'"{del_}"' in kilde, f"svar_for mangler {del_}"


def test_apiet_avviser_flyttall_og_tomme_minutter():
    """`Number("")` er `0`, og en måling RETTES IKKE.

    128s CodeRabbit-funn: et tomt felt sendt inn ville registrert null
    minutter som ukens faktiske arbeid — permanent.
    """
    from api import prognose as modul
    kilde = _bare_kode(Path(modul.__file__))
    assert "isinstance(verdi, int)" in kilde
    assert "isinstance(verdi, bool)" in kilde
    flate = FLATE.read_text(encoding="utf-8")
    assert "required: true" in flate
    assert 'faktisk.value.trim() === ""' in flate


def test_ui_axe_alvorlige_brudd_dekkes_av_flatens_egen_suite():
    """`ui_axe_alvorlige_brudd` måles i `platform/core/ui/test`.

    Porten her er en PEKER, ikke en kopi: to steder som måler det
    samme ville kunnet gi to svar.
    """
    js = (ROT / "platform" / "core" / "ui" / "test"
          / "prognose.test.js")
    assert js.exists(), "flatens egen suite mangler"
    tekst = js.read_text(encoding="utf-8")
    assert "axe" in tekst.lower() or "aria" in tekst.lower()


def test_fundamentet_navngir_modulen_og_migrasjonen():
    """Fundamentet tildelte nummeret; koden skal svare til det."""
    tekst = FUNDAMENT.read_text(encoding="utf-8")
    assert "130" in tekst and "M-33" in tekst
    assert MIGRASJON.exists()


# =====================================================================
# SP-2: GJENSPILL. Begge fant CodeRabbit på #394 — de manglet.
# =====================================================================

@pg
def test_et_gjenspilt_prognosekall_svarer_med_raden():
    """API-et utleder `prognose_id` av Idempotency-Key-en.

    Uten en gjenspillgren traff et gjentatt kall primærnøkkelen og ga
    400 på noe helt lovlig — en nettverksretry ville sett ut som en
    feil forespørsel. M-51s lærdom (119), som M-15 har hatt siden 128
    og denne modulen manglet til CodeRabbit sa fra.

    MUTASJONEN SOM DREPER DENNE: fjern `IF FOUND THEN`-grenen i
    `m33_lag_prognose`.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("gjenspill")
        _krav(rt, t)
        _historikk(mg, t)
        mid = _modell(rt, t)
        pid = uuid.uuid4()
        _, forste = _prognose(rt, t, mid, prognose_id=pid)
        _sett_kontekst(rt, t)
        andre = rt.execute(
            "SELECT * FROM m33_lag_prognose(%s,%s,%s,%s)",
            (t, pid, mid, "u-test")).fetchone()
        rt.commit()
        _sett_kontekst(mg, t)
        n = mg.execute(
            "SELECT count(*) FROM bemanningsbane WHERE tenant=%s"
            "   AND prognose_id=%s", (t, pid)).fetchone()[0]
        mg.rollback()
    assert forste[5] is True and andre[5] is False, "`ny` skiller ikke"
    # SVARET ER DET SAMME, og banen er ikke skrevet to ganger.
    assert forste[:5] == andre[:5]
    assert n == 4, f"gjenspillet skrev banen på nytt: {n} uker"


@pg
def test_samme_nokkel_mot_en_annen_modell_er_ikke_et_gjenspill():
    """To ulike forespørsler som deler nøkkel er ikke én forespørsel.

    Å svare med den første ville skjult at den andre aldri ble utført.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("kollisjon")
        _krav(rt, t)
        _historikk(mg, t)
        m1 = _modell(rt, t, versjon="v1")
        m2 = _modell(rt, t, versjon="v2")
        pid = uuid.uuid4()
        _prognose(rt, t, m1, prognose_id=pid)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT m33_lag_prognose(%s,%s,%s,%s)",
                       (t, pid, m2, "u-test"))
        rt.rollback()


@pg
def test_et_gjenspilt_modellkall_svarer_med_raden():
    """Samme dom for modelldøra: identiteten er FROSSET, så et
    gjenspill kan ikke skrive på nytt — det må svare."""
    with _to() as (rt, mg):
        t = _tenantnavn("modellgjenspill")
        mid = uuid.uuid4()
        _sett_kontekst(rt, t)
        a = rt.execute(
            "SELECT * FROM"
            " m33_registrer_modell(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (t, mid, "M", "v1", METODE, "samme som forrige uke",
             _dag(-10), None, "u-test")).fetchone()
        rt.commit()
        _sett_kontekst(rt, t)
        b = rt.execute(
            "SELECT * FROM"
            " m33_registrer_modell(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (t, mid, "M", "v1", METODE, "samme som forrige uke",
             _dag(-10), None, "u-test")).fetchone()
        rt.commit()
        del mg
    assert a[2] is True and b[2] is False
    assert a[:2] == b[:2]


@pg
def test_sveipen_gjenaapner_ikke_et_funn_et_menneske_har_lukket():
    """125/126s FEILFORM, FANGET AV CODERABBIT FØR MERGE.

    `prognosefunn_ett_apent` er en DELINDEKS over de åpne radene. Når
    et menneske har lukket funnet, treffer `ON CONFLICT` ingenting —
    og INSERTen lager en NY åpen rad. Hver natt.

    For de to andre funntypene er gjenreising RIKTIG: der betyr den at
    tilstanden faktisk kom tilbake. Her kan den ikke det —
    `datakvalitet` står på en append-only rad og endrer seg aldri — så
    uten vernet ville «vi vet, vi planlegger likevel» vært en
    beslutning som ikke overlevde natten.

    MUTASJONEN SOM DREPER DENNE: fjern `NOT EXISTS`-leddet i sveipens
    tredje blokk.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("gjenaapning")
        _krav(rt, t)
        _historikk(mg, t)
        mid = _modell(rt, t)
        _prognose(rt, t, mid)
        with _sv() as sv:
            sv.execute("SELECT * FROM m33_sveip_prognose(500)")
            sv.commit()
        _sett_kontekst(rt, t)
        fid = [r for r in rt.execute(
            "SELECT * FROM m33_prognosefunn(%s,%s)",
            (t, 100)).fetchall()
            if r[1] == "prognose_paa_ukjent_datakvalitet"][0][0]
        rt.rollback()
        _sett_kontekst(rt, t)
        rt.execute("SELECT m33_lukk_funn(%s,%s,%s,%s)",
                   (t, fid, "vi vet, vi planlegger likevel", "u-test"))
        rt.commit()

        # NATTEN ETTER.
        with _sv() as sv:
            sv.execute("SELECT * FROM m33_sveip_prognose(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        apne = mg.execute(
            "SELECT count(*) FROM prognosefunn WHERE tenant=%s"
            "   AND funntype='prognose_paa_ukjent_datakvalitet'"
            "   AND apen", (t,)).fetchone()[0]
        mg.rollback()
    assert apne == 0, (
        "sveipen gjenåpnet et funn et menneske hadde lukket")


def test_en_tapt_tilstandsfil_er_ogsa_en_feilet_kjoring():
    """CodeRabbit på #394: telleren ER alarmen.

    Lar tilstandsfila seg ikke skrive, nullstilles telleren ved hver
    kjøring — og en sveip som feiler hver natt når da ALDRI terskelen.
    Feilen ville vært usynlig i nettopp den situasjonen alarmen finnes
    for.

    `feilet` står urørt i linjen: sveipen kan ha gjort jobben sin helt
    riktig. Det er ALARMVEIEN som er brutt.

    DEN SAMME MANGELEN FINNES I DE ANDRE ~30 `kjor_*sveip.py`, og er
    meldt som egen sak. Denne porten dekker M-33s egen.
    """
    import json
    from drift import kjor_prognosesveip as kjorer

    class _FalskResultat:
        tenanter, nye, oppdaterte, lukkede = 1, 0, 0, 0
        feilet = False
        alarm_utlost = False
        hoppet_over = False

    class _Tilkobling:
        def close(self):
            pass

    linjer: list[str] = []
    gamle = (kjorer._skriv_feiltelling, kjorer._les_feiltelling,
             kjorer._koble, kjorer.prognosesveip.kjor)
    try:
        kjorer._skriv_feiltelling = lambda _n: False   # tapt fil
        kjorer._les_feiltelling = lambda: 0
        kjorer._koble = lambda _dsn: _Tilkobling()
        kjorer.prognosesveip.kjor = (
            lambda _c, **_k: _FalskResultat())
        os.environ["DISPONIT_PROGNOSESVEIP_URL"] = "postgresql:///x"
        import builtins
        ekte_print = builtins.print
        builtins.print = lambda *a, **k: linjer.append(str(a[0]))
        try:
            kode = kjorer.main()
        finally:
            builtins.print = ekte_print
    finally:
        (kjorer._skriv_feiltelling, kjorer._les_feiltelling,
         kjorer._koble, kjorer.prognosesveip.kjor) = gamle
        os.environ.pop("DISPONIT_PROGNOSESVEIP_URL", None)

    assert kode != 0, "en tapt tilstandsfil ga grønn exit-kode"
    linje = json.loads(linjer[-1])
    assert linje["tilstand_lagret"] == 0
    # …OG `feilet` LYVER IKKE om sveipen.
    assert linje["feilet"] == 0


# =====================================================================
# 131: RETTELSENE FRA CODERABBITS GJENNOMGANG AV #394.
#
# Gjennomgangen ga ÅTTE funn. Seks er dekket av porter her — fire i
# dørene (migrasjon 131), én i timeren, og skillet mellom dem står i
# migrasjonens topptekst. De to siste er bagateller i andre modulers
# tekster og hører ikke hjemme i M-33s suite.
# =====================================================================

@pg
def test_snittet_deler_ikke_paa_uker_tenanten_ikke_har_levd():
    """DEN ALVORLIGSTE AV GJENNOMGANGENS ÅTTE FUNN, og den rammet
    modulens hovedinvariant.

    `avg(minutter)` gikk over ALLE `grunnlag_uker` blokker. En blokk
    uten rader bidro med 0 — også blokker som ligger FØR tenantens
    aller første timeregistrering.

    En tenant med tre ukers historikk og `grunnlag_uker = 8` fikk
    derfor et forventet nivå på under halvparten av det virkelige,
    mens `grunnlag_antall_uker` sa 3. RADEN OG DIVISOREN VAR IKKE
    ENIGE.

    OG KONSEKVENSEN: et snitt som ligger for lavt taper for
    basislinjen HVER målte uke, og sveipen reiser
    `slaar_ikke_naiv_baseline` mot en modell som aldri fikk sitt eget
    vindu. Funnet som skulle si «modellen er ikke god nok» ville sagt
    «tenanten er ny».

    Her: tre uker med 600, 500, 400 minutter (ferskest først). Snittet
    over de DEKKEDE blokkene er 500. Over alle åtte ville det vært
    (600+500+400)/8 = 188.

    MUTASJONEN SOM DREPER DENNE: fjern
    `WHERE v_dato - ((b.k - 1) * 7) > v_forste`.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("dekning")
        _krav(rt, t, grunnlag=8)
        _historikk(mg, t, uker=3, minutter=[400, 500, 600])
        mid = _modell(rt, t)
        pid, rad = _prognose(rt, t, mid)
        _sett_kontekst(rt, t)
        bane = rt.execute("SELECT * FROM m33_banen(%s,%s)",
                          (t, pid)).fetchone()
        rt.rollback()
    assert rad[2] == 3, f"dekningen ble ikke 3 uker: {rad[2]}"
    assert bane[2] == 500, (
        f"snittet delte på uker tenanten ikke har levd: {bane[2]}"
        " (skulle vært 500, ikke 188)")
    # BASISLINJEN ER UBERØRT: forrige uke er fortsatt forrige uke.
    assert bane[5] == 400


@pg
def test_en_tenant_med_bare_en_hel_uke_faar_ingen_prognose():
    """Med én blokk ER snittet forrige uke.

    Da er modellen sin egen basislinje: den kan ikke tape, og
    `slaar_ikke_naiv_baseline` blir et funn ingen kan reise. Samme dom
    som `grunnlag_uker >= 2`, håndhevet på DATAENE i stedet for på
    grensen — en grense kan settes riktig mens dataene ikke rekker.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("enuke")
        _krav(rt, t, grunnlag=8)
        _historikk(mg, t, uker=1, minutter=[600])
        mid = _modell(rt, t)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.NoDataFound):
            rt.execute("SELECT m33_lag_prognose(%s,%s,%s,%s)",
                       (t, uuid.uuid4(), mid, "u-test"))
        rt.rollback()


@pg
def test_et_lite_snitt_gir_likevel_et_intervall_med_bredde():
    """ET PUNKT SOM PÅSTÅR Å VÆRE ET INTERVALL.

    `v_punkt` er det AVRUNDEDE snittet. Med små tall runder det til 0,
    og minstebredden — som var regnet av `v_punkt` — ble også 0. Raden
    fikk `nedre = forventet = ovre = 0`, og CHECKen slapp den gjennom
    fordi 0 <= 0 <= 0.

    PORTEN BRUKER FIRE UKER DER ALLE ER FØRT MED NULL MINUTTER.

    Første utkast brukte [1, 0, 0, 0], og den porten var GRØNN også
    med den gamle koden: spredningen i den serien er 0,43, og
    `ceil(0,43) = 1` reddet båndet uten at minstebredden ble prøvd.
    En port som ikke dør av mutasjonen måler ikke det den sier.

    Med fire like uker er BÅDE snittet og spredningen null, og da er
    det ubetingede `1` det eneste som står mellom raden og et
    intervall med bredde null. Serien er ikke oppkonstruert: en
    timeregistrering med 0 minutter er lovlig, og fire slike uker
    betyr «vi har ført, og ingen jobbet».

    MUTASJONEN SOM DREPER DENNE: sett siste ledd i `greatest(...)`
    tilbake til `CASE WHEN v_punkt > 0 THEN 1 ELSE 0 END`.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("smaatall")
        _krav(rt, t, grunnlag=4)
        _historikk(mg, t, uker=4, minutter=[0, 0, 0, 0])
        mid = _modell(rt, t)
        pid, _ = _prognose(rt, t, mid)
        _sett_kontekst(rt, t)
        bane = rt.execute("SELECT * FROM m33_banen(%s,%s)",
                          (t, pid)).fetchall()
        rt.rollback()
    assert bane
    for u in bane:
        punkt, ned, opp = u[2], u[3], u[4]
        assert ned < opp, (
            f"uke {u[0]}: baandet har bredde null ({ned}-{opp})"
            f" ved punkt {punkt}")


@pg
def test_datakvalitetsdora_binder_tenanten_til_konteksten():
    """DEN ENESTE M-33-DØRA UTEN `krev_tenantkontekst`.

    Ingen data lakk — radvakten på M-3s registre ser til det — men det
    er verre enn en lekkasje ville vært SYNLIG: med feil tenant ga
    funksjonen null rader og svarte `ukjent` med 0 funn. Altså
    nøyaktig den verdien modulen behandler som den farlige, avgitt av
    feil grunn.
    """
    with _to() as (rt, mg):
        a, b = _tenantnavn("kvala"), _tenantnavn("kvalb")
        _sett_kontekst(rt, a)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT * FROM m33_datakvalitet(%s)", (b,))
        rt.rollback()
        del mg


@pg
def test_en_gjentatt_maaling_svarer_med_raden_ikke_med_en_feil():
    """EN SKRIVING SOM LYKTES SKAL IKKE RAPPORTERES SOM FEILET.

    API-et krever `Idempotency-Key`. Uten gjenspillgrenen fanget døra
    `unique_violation` og reiste den på nytt, og API-et oversatte den
    til 400. En klient som mistet svaret og prøvde igjen fikk vite at
    det feilet — og siden `POST /maaling` er den ENESTE veien til å
    lukke `prognose_uten_maaling`, kunne den ikke engang se om funnet
    var lukket.

    MUTASJONEN SOM DREPER DENNE: fjern `IF FOUND THEN`-grenen i
    `m33_registrer_maaling`.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("maalgjenspill")
        _krav(rt, t)
        _historikk(mg, t)
        mid = _modell(rt, t)
        pid, _ = _prognose(rt, t, mid)
        _aldre_prognose(mg, t, pid, 60)
        _sett_kontekst(rt, t)
        a = rt.execute(
            "SELECT * FROM m33_registrer_maaling(%s,%s,%s,%s,%s)",
            (t, pid, 1, 700, "u-test")).fetchone()
        rt.commit()
        _sett_kontekst(rt, t)
        b = rt.execute(
            "SELECT * FROM m33_registrer_maaling(%s,%s,%s,%s,%s)",
            (t, pid, 1, 700, "u-test")).fetchone()
        rt.commit()
        # …MEN ET ANNET TALL ER FORTSATT EN FEIL. To ulike
        # forespørsler som deler nøkkel er ikke én forespørsel.
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.UniqueViolation):
            rt.execute("SELECT m33_registrer_maaling(%s,%s,%s,%s,%s)",
                       (t, pid, 1, 999, "u-test"))
        rt.rollback()
        _sett_kontekst(mg, t)
        n = mg.execute(
            "SELECT count(*) FROM bemanningsmaaling WHERE tenant=%s",
            (t,)).fetchone()[0]
        mg.rollback()
    assert a[4] is True and b[4] is False, "`ny` skiller ikke"
    assert a[:4] == b[:4], "gjenspillet ga et annet svar"
    assert n == 1, f"gjenspillet skrev en ny rad: {n}"


@pg
def test_en_gjentatt_avvikling_svarer_med_raden():
    """`m33_modellvakt` avviser en ny avvikling, så et gjenspill med
    IDENTISK dato ga 400 på noe som alt hadde lyktes.

    Avvikling er enveis — det gjør ikke et gjenspill til en feil, det
    gjør det til et spørsmål med et svar. En ANNEN dato er fortsatt
    en feil: den ville omskrevet «hvilken modell gjaldt da».
    """
    with _to() as (rt, mg):
        t = _tenantnavn("avviklgjenspill")
        mid = _modell(rt, t)
        _sett_kontekst(rt, t)
        a = rt.execute(
            "SELECT * FROM m33_avvikle_modell(%s,%s,%s,%s)",
            (t, mid, _dag(0), "u-test")).fetchone()
        rt.commit()
        _sett_kontekst(rt, t)
        b = rt.execute(
            "SELECT * FROM m33_avvikle_modell(%s,%s,%s,%s)",
            (t, mid, _dag(0), "u-test")).fetchone()
        rt.commit()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT m33_avvikle_modell(%s,%s,%s,%s)",
                       (t, mid, _dag(5), "u-test"))
        rt.rollback()
        del mg
    assert a[2] is True and b[2] is False
    assert a[:2] == b[:2]


def test_sveipen_rekker_aa_bli_ferdig_for_statussveipen_starter():
    """KLOKKESLETTET ALENE ER IKKE EN REKKEFØLGE.

    `RandomizedDelaySec` etablerer ingen ordning mellom timere — den
    finnes for å hindre at de fyrer samtidig. Med 30 minutters
    spredning kunne prognosesveipen starte 12:05 og kjøre til 12:15,
    mens statussveipen kan starte 12:05 med null spredning.
    Statussveipen leser tilstandsfila og ville meldt en FALSK
    «uteblitt».

    Porten regner VERST TENKELIG SLUTT for M-33 og krever at den
    ligger før statussveipens TIDLIGSTE start.

    DEN SAMME OVERLAPPEN FINNES I RESTEN AV FLÅTEN — stigens trinn er
    15 minutter mens hver sveip har inntil 30 minutters spredning. Det
    er en egen sak. Porten her dekker M-33s egen del, og gjør
    regnestykket synlig for den neste som legger en sveip i stigen.
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

    def _timeout(fil):
        tekst = (katalog / fil).read_text(encoding="utf-8")
        m = re.search(r"TimeoutStartSec=(\d+)min", tekst)
        return int(m.group(1)) if m else 0

    start, spredning = _tid("disponit-prognosesveip.timer")
    kjoretid = _timeout("disponit-prognosesveip.service")
    slutt = start + spredning + kjoretid
    status_start, _ = _tid("disponit-sveipestatus.timer")
    assert kjoretid > 0, "tjenesten har ingen TimeoutStartSec å regne med"
    assert slutt <= status_start, (
        f"prognosesveipen kan holde på til {slutt // 60}:"
        f"{slutt % 60:02d} mens statussveipen kan starte"
        f" {status_start // 60}:{status_start % 60:02d} — statusen"
        " ville lest forrige døgns tilstandsfil")
