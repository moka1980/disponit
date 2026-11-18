"""M-39 lønnsgrunnlag v1 (113) — GRUNNLAGET, IKKE LØNNSKJØRINGEN.

Grensen `m39-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

DEN SKARPESTE PORTEN ER `modulen_produserte_lonnsfil`, ikke
`modulen_utbetalte`.

At modulen ikke utbetaler er samme dom som M-41s (111), og den trenger
ingen ny begrunnelse. Det særegne her er FILA: en lønnsfil er ikke en
betaling — den ser harmløs ut, den kan «bare genereres», og den er
nettopp derfor farligere enn en enkelt utbetaling. Den rammer ALLE på
én gang, og den rammer noen som har regnet med beløpet. En feil i en
faktura oppdages av en kunde som klager. En feil i en lønnsfil oppdages
av noen som ikke fikk husleia.

DEN NEST SKARPESTE er `overtid_uten_flagg`. Det finnes ingen
`overtid`-kolonne noen kan sette og gå videre fra; overtid UTLEDES og
blir et funn. Et flagg modulen satte selv ville vært nøyaktig den
attestasjonen `overtid_flagget` skal hvile på.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import ast
import json
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

LONNSSVEIP_DSN = os.environ.get("DISPONIT_TEST_LONNSSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "113_m39_lonnsgrunnlag.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "lonn.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "lonn.py",
    ROT / "platform" / "drift" / "lonnssveip.py",
    ROT / "platform" / "drift" / "kjor_lonnssveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("lonnsterskel", "lonnstaker", "arbeidsplan",
        "timeregistrering", "lonnsfunn")

_STRENG = re.compile(
    r"'''.*?'''" r'|""".*?"""'
    r"|`(?:[^`\\]|\\.)*`"
    r"|'(?:[^'\\]|\\.|'')*'"
    r'|"(?:[^"\\]|\\.)*"', re.S)


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
    ut = "\n".join(l for l in linjer
                   if not l.lstrip().startswith(merke))
    return _STRENG.sub("''", ut) if uten_strenger else ut


def _rt():
    from db.pg import koble
    return koble(DSN)


def _sv():
    from db.pg import koble
    return koble(LONNSSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m39-{merke}-{secrets.token_hex(4)}"


def _terskler(c, tenant, *, dag=450, uke=2250, avvik=0, uten_plan=7,
              vindu=3650, aktor="u-test"):
    """VINDUET ER VIDT I TESTENE, med vilje.

    Portene her måler REGELEN, ikke vinduet; et snevert
    standardvindu ville gjort hver fikstur avhengig av dagens dato.
    Vinduet har sin egen port under.
    """
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m39_sett_terskler(%s,%s,%s,%s,%s,%s,%s)",
                  (tenant, dag, uke, avvik, uten_plan, vindu,
                   aktor)).fetchone()[0]
    c.commit()
    return v


def _taker(c, tenant, *, ref=None, navn="Kari Ansatt", tid=None,
           aktor="u-test"):
    tid = tid or uuid.uuid4()
    ref = ref or ("A-" + secrets.token_hex(4))
    _sett_kontekst(c, tenant)
    c.execute("SELECT m39_registrer_taker(%s,%s,%s,%s,%s)",
              (tenant, tid, ref, navn, aktor))
    c.commit()
    return tid


def _plan(c, tenant, tid, *, minutter=450, kode="P-1",
          fra="2026-01-01", grunn="avtalt", aktor="u-test"):
    _sett_kontekst(c, tenant)
    v = c.execute(
        "SELECT m39_sett_arbeidsplan(%s,%s,%s,%s,%s,%s::date,%s,%s)",
        (tenant, uuid.uuid4(), tid, minutter, kode, fra, grunn,
         aktor)).fetchone()[0]
    c.commit()
    return v


def _timer(c, tenant, tid, dato, minutter, *, kode="P-1",
           kilde="fort_av_ansatt", kilde_ref=None, notat="ok",
           aktor="u-test"):
    kilde_ref = kilde_ref or ("r_" + secrets.token_hex(4))
    _sett_kontekst(c, tenant)
    ut = c.execute(
        "SELECT m39_registrer_timer(%s,%s,%s,%s::date,%s,%s,%s,%s,%s,"
        "%s)",
        (tenant, uuid.uuid4(), tid, dato, minutter, kode, kilde,
         kilde_ref, notat, aktor)).fetchone()[0]
    c.commit()
    return ut


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m39_sveip_lonnsgrunnlag(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT funntype, over_grense, antall_dager, siste_dato, apen"
        "  FROM lonnsfunn WHERE tenant=%s ORDER BY funntype",
        (tenant,)).fetchall()
    m.rollback()
    return rader


def _tell_utenfor(m):
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
# INVARIANT 1 og 2: modulen_utbetalte / modulen_produserte_lonnsfil
# ---------------------------------------------------------------------------

def test_invariant_modulen_utbetalte_og_produserte_lonnsfil():
    """MODULENS SKARPESTE DOM, målt på IMPORTENE, KODEN og RUTENE.

    ANDRE HALVDEL ER DEN SÆREGNE. At modulen ikke utbetaler er M-41s
    dom. Det nye her er FILA: en lønnsfil ser harmløs ut, den kan «bare
    genereres», og den rammer alle på én gang. Derfor er
    filskrivingsmodulene ikke bare ubrukte — de er UIMPORTERTE.

    MUTASJONEN SOM DREPER DENNE: `import csv` i `lonnssveip.py`.
    """
    for fil in MODULFILER:
        for node in ast.walk(ast.parse(fil.read_text(encoding="utf-8"))):
            navn = []
            if isinstance(node, ast.Import):
                navn = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                navn = [node.module or ""] if node.level == 0 else []
            for n in navn:
                rot = n.split(".")[0]
                assert rot not in {
                    "httpx", "requests", "aiohttp", "urllib", "http",
                    "socket", "smtplib", "decimal"}, \
                    f"{fil.name} importerer {n} — v1 har ingen utgående" \
                    " kanal"
                # FILSKRIVING: `kjor_*sveip.py` fører sin egen
                # tilstandsfil og har `os`/`json` av den grunn; de to
                # andre skal ikke ha noe filverktøy i det hele tatt.
                if fil.name != "kjor_lonnssveip.py":
                    assert rot not in {"csv", "pathlib", "shutil",
                                       "tempfile", "openpyxl", "os",
                                       "io"}, \
                        f"{fil.name} importerer {n} — v1 produserer" \
                        " ingen lønnsfil"
    for fil in (MIGRASJON, FLATE, MODULFILER[0], MODULFILER[1]):
        uten = _bare_kode(fil, uten_strenger=True).lower()
        for ord_ in (r"utbetal", r"lonnsfil", r"lønnsfil", r"eksporter",
                     r"\bcsv\b", r"\bsftp\b", "nets", "bankfil",
                     "urlopen", r"requests\.", r"open\("):
            assert not re.search(ord_, uten), \
                f"{fil.name} bærer «{ord_}» — v1 utbetaler ingenting" \
                " og produserer ingen lønnsfil"

    # …OG DET FINNES INGEN DØR SOM GJØR DET.
    sql = MIGRASJON.read_text(encoding="utf-8")
    doerer = re.findall(r"CREATE FUNCTION (m39_\w+)", sql)
    assert len(doerer) >= 12, doerer
    for navn in doerer:
        for ord_ in ("utbetal", "eksport", "lonnsfil", "generer",
                     "attester", "signer", "godkjenn"):
            assert ord_ not in navn, navn

    from api.app import RUTESCOPE
    mine = sorted(sti for _m, sti in RUTESCOPE
                  if sti.startswith("/v1/lonn"))
    assert mine == [
        "/v1/lonn",
        "/v1/lonn/taker",
        "/v1/lonn/terskler",
        "/v1/lonn/{taker_id:uuid}/aktiv",
        "/v1/lonn/{taker_id:uuid}/dager",
        "/v1/lonn/{taker_id:uuid}/historikk",
        "/v1/lonn/{taker_id:uuid}/plan",
        "/v1/lonn/{taker_id:uuid}/planer",
        "/v1/lonn/{taker_id:uuid}/timer",
    ], mine


def test_flaten_genererer_ingen_fil():
    uten = _bare_kode(FLATE, uten_strenger=True)
    for ord_ in ("download", "Blob", "createObjectURL", "fetch(",
                 "XMLHttpRequest", "toCSV", "eksport"):
        assert ord_ not in uten, f"flaten bærer «{ord_}»"
    api = (ROT / "platform" / "core" / "ui" / "static" / "js"
           / "api.js").read_text(encoding="utf-8")
    assert not re.search(
        r"export const (genererLonnsfil|utbetalLonn|eksporterLonn)", api)
    # ALLE FEM SKRIVEVEIENE sender en Idempotency-Key.
    for n in ("settLonnsterskler", "registrerLonnstaker",
              "settArbeidsplan", "registrerTimer",
              "settLonnstakerAktiv"):
        i = api.index(f"export const {n} =")
        j = api.find("\n\n", i)
        kropp = api[i:j if j != -1 else len(api)]
        assert "idem || nyIdempotensnokkel()" in kropp, n


# ---------------------------------------------------------------------------
# INVARIANT 3: modulen_signerte_attestasjon
# ---------------------------------------------------------------------------

@pg
def test_invariant_modulen_signerte_attestasjon(migrator):
    """KLYNGENS FELLESDOM: modulen tar ikke attestasjonsfullmakten.

    Malen betror `v_lonn` TRE vilkår og bruker alle tre. v1 gir dem
    grunnlaget og attesterer ingen av dem.

    MUTASJONEN SOM DREPER DENNE: en `godkjent`-kolonne på
    `timeregistrering`.
    """
    kolonner = migrator.execute(
        "SELECT table_name, column_name"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND (column_name LIKE '%%attest%%'"
        "        OR column_name LIKE '%%signat%%'"
        "        OR column_name LIKE '%%godkjen%%'"
        "        OR column_name LIKE '%%validert%%')",
        (list(EGNE),)).fetchall()
    migrator.rollback()
    assert kolonner == [], kolonner
    sql = _bare_kode(MIGRASJON, uten_strenger=True).lower()
    for ord_ in ("attestasjon", "attester", "signatur", "signer"):
        assert ord_ not in sql, ord_
    i = sql.index("create function m39_evidens(")
    assert "revisjonslogg" in sql[i:sql.index("end $$;", i)]


# ---------------------------------------------------------------------------
# INVARIANT 4: timer_i_flyttall
# ---------------------------------------------------------------------------

@pg
def test_invariant_timer_i_flyttall_i_katalogen(migrator):
    flyt = migrator.execute(
        "SELECT table_name, column_name, data_type"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND data_type IN ('numeric','real','double precision')",
        (list(EGNE),)).fetchall()
    migrator.rollback()
    assert flyt == [], flyt
    rader = migrator.execute(
        "SELECT table_name, column_name, data_type"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND column_name LIKE '%%minutter%%' ORDER BY 1,2",
        (list(EGNE),)).fetchall()
    migrator.rollback()
    assert rader, "fant ingen minuttkolonner — porten måler ingenting"
    for tab, kol, typ in rader:
        assert typ == "integer", f"{tab}.{kol} er {typ}"
    # …OG DET FINNES INGEN «TIMER»-KOLONNE. Timen er en enhet klienten
    # regner i; basen kjenner bare minutter.
    timer = migrator.execute(
        "SELECT table_name, column_name FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND column_name ~ '(^|_)timer($|_)'",
        (list(EGNE),)).fetchall()
    migrator.rollback()
    assert timer == [], timer


def test_invariant_timer_i_flyttall_over_api():
    from api.adresse import _valg as _valg_adresse  # noqa: F401
    from api.lonn import MINUTTER_DOGN, _bool, _heltall, _minutter, _valg
    from api.policyadmin_http import _Avbrudd
    # 7,5 TIME ER 450 MINUTTER — og `7.5` kommer aldri inn.
    for verdi in (7.5, 450.0, True, False, "450", None, -1,
                  MINUTTER_DOGN + 1):
        with pytest.raises(_Avbrudd):
            _minutter({"m": verdi}, "m", "r")
    assert _minutter({"m": 0}, "m", "r") == 0
    assert _minutter({"m": 450}, "m", "r") == 450
    assert _minutter({"m": MINUTTER_DOGN}, "m", "r") == MINUTTER_DOGN
    for verdi in (1.5, True, False, "3", None, -1, 3651):
        with pytest.raises(_Avbrudd):
            _heltall({"n": verdi}, "n", "r", 0, 3650)
    for verdi in (1, 0, "ja", None):
        with pytest.raises(_Avbrudd):
            _bool({"b": verdi}, "b", "r")
    with pytest.raises(_Avbrudd):
        _valg({"s": "gjetning"}, "s", "r", ("a", "b"))


@pg
def test_avviket_regnes_i_heltall(migrator):
    """AVVIKET ER EN DIFFERANSE I MINUTTER, ikke en prosent.
    GRENSETILFELLET ER PORTEN: nøyaktig på grensen er ikke et funn, ett
    minutt over er det.

    MUTASJONEN SOM DREPER DENNE: bytt `>` mot `>=` i funndøren.
    """
    tenant = _tenantnavn("avvik")
    c = _rt()
    try:
        # Avviksgrense 15 min. Normaltid høy nok til at overtid ikke
        # forstyrrer målingen.
        _terskler(c, tenant, dag=1440, uke=10080, avvik=15,
                  uten_plan=3650)
        paa = _taker(c, tenant, ref="PAA")
        _plan(c, tenant, paa, minutter=450)
        _timer(c, tenant, paa, "2026-08-03", 465)
        over = _taker(c, tenant, ref="OVER")
        _plan(c, tenant, over, minutter=450)
        _timer(c, tenant, over, "2026-08-03", 466)
        under = _taker(c, tenant, ref="UNDER")
        _plan(c, tenant, under, minutter=450)
        _timer(c, tenant, under, "2026-08-03", 434)
        stemmer = _taker(c, tenant, ref="STEMMER")
        _plan(c, tenant, stemmer, minutter=450)
        _timer(c, tenant, stemmer, "2026-08-03", 450)
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_lonn_eier")
    funn = {}
    for ref, ft, og in migrator.execute(
            "SELECT s.ekstern_ref, k.funntype, k.over_grense"
            "  FROM m39_funnkandidater(%s, '2026-09-03'::date) k"
            "  JOIN lonnstaker s ON s.tenant=%s"
            "   AND s.taker_id=k.taker_id ORDER BY 1", (tenant, tenant)
            ).fetchall():
        funn.setdefault(ref, []).append((ft, og))
    migrator.rollback()
    # NØYAKTIG PÅ GRENSEN (15 min) er IKKE et funn.
    assert funn.get("PAA") is None, funn
    # ETT MINUTT OVER er det, og `over_grense` er 1.
    assert funn.get("OVER") == [("avvik_mot_plan", 1)], funn
    # …OG BEGGE RETNINGER MÅLES. Mindre enn planlagt er like mye et
    # spørsmål som mer — det ene er fravær, det andre overtid.
    assert funn.get("UNDER") == [("avvik_mot_plan", 1)], funn
    assert funn.get("STEMMER") is None, funn


# ---------------------------------------------------------------------------
# INVARIANT 5: time_uten_arbeidsplan
# ---------------------------------------------------------------------------

@pg
def test_invariant_time_uten_arbeidsplan(migrator):
    """EN TIME UTEN EN PLAN Å MÅLES MOT ER IKKE MÅLT.

    `timer_mot_arbeidsplan` er et vilkår om en SAMMENLIGNING. Uten en
    plan finnes det ingen sammenligning, og et «ja» ville vært en
    attestasjon om noe ingen gjorde.

    DØREN SIER DET MED ÉN GANG. Den som fører en time skal få vite at
    den ikke måles mot noe — ikke først når sveipen har gått en uke
    senere.
    """
    tenant = _tenantnavn("utenplan")
    c = _rt()
    try:
        _terskler(c, tenant, uten_plan=7)
        uten = _taker(c, tenant, ref="UTEN")
        # SVARET ER USANT: ingen plan å måle mot.
        assert _timer(c, tenant, uten, "2026-08-01", 450) is False
        med = _taker(c, tenant, ref="MED")
        _plan(c, tenant, med, fra="2026-01-01")
        assert _timer(c, tenant, med, "2026-08-01", 450) is True
        # …og en time FØR planen begynte er fortsatt umålt.
        assert _timer(c, tenant, med, "2025-12-01", 450,
                      kilde_ref="for") is False
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_lonn_eier")
    funn = {}
    for ref, ft, og, ant in migrator.execute(
            "SELECT s.ekstern_ref, k.funntype, k.over_grense,"
            " k.antall_dager FROM m39_funnkandidater(%s,"
            " '2026-09-03'::date) k JOIN lonnstaker s ON s.tenant=%s"
            " AND s.taker_id=k.taker_id ORDER BY 1,2",
            (tenant, tenant)).fetchall():
        funn.setdefault(ref, []).append((ft, og, ant))
    migrator.rollback()
    # 2026-09-03 minus 2026-08-01 er 33 døgn, minus fristen 7.
    assert ("time_uten_arbeidsplan", 26, 1) in funn["UTEN"], funn
    # MED har ÉN umålt dag — den før planen begynte.
    umaalte = [f for f in funn.get("MED", [])
               if f[0] == "time_uten_arbeidsplan"]
    assert len(umaalte) == 1 and umaalte[0][2] == 1, funn


@pg
def test_planen_har_noyaktig_ett_svar_per_dag(migrator):
    """UTEN DEN EGENSKAPEN er `timer_mot_arbeidsplan` ikke et spørsmål
    man kan svare på i det hele tatt."""
    tenant = _tenantnavn("planperiode")
    c = _rt()
    try:
        _terskler(c, tenant)
        tid = _taker(c, tenant)
        assert _plan(c, tenant, tid, minutter=450, kode="P-1",
                     fra="2026-01-01") == 1
        assert _plan(c, tenant, tid, minutter=420, kode="P-2",
                     fra="2026-07-01") == 2
        _sett_kontekst(c, tenant)
        for dato, fasit in (("2025-12-31", None), ("2026-06-30", 450),
                            ("2026-07-01", 420)):
            rad = c.execute(
                "SELECT planlagt_minutter_dag FROM"
                " m39_plan_paa_dato(%s,%s,%s::date)",
                (tenant, tid, dato)).fetchone()
            assert (rad[0] if rad else None) == fasit, (dato, rad)
        c.rollback()
        # EN PLAN SKRIVES IKKE BAKOVER.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m39_sett_arbeidsplan(%s,%s,%s,450,'P-3',"
                      "'2026-03-01'::date,'x','u')",
                      (tenant, uuid.uuid4(), tid))
        assert "skrives ikke bakover" in str(ei.value)
        c.rollback()
        # …og en plan uten begrunnelse er ingen beslutning.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m39_sett_arbeidsplan(%s,%s,%s,450,'P-4',"
                      "'2027-01-01'::date,NULL,'u')",
                      (tenant, uuid.uuid4(), tid))
        assert "begrunnelse" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_lonn_eier")
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "INSERT INTO arbeidsplan (tenant, plan_id, taker_id,"
            " versjon, planlagt_minutter_dag, prosjektkode, gyldig_fra,"
            " gyldig_til, begrunnelse, opprettet_av) VALUES"
            " (%s,%s,%s,99,450,'P-9','2026-03-01','2026-05-01','x','u')",
            (tenant, uuid.uuid4(), tid))
    assert "overlapper" in str(ei.value)
    migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 6: overtid_uten_flagg
# ---------------------------------------------------------------------------

@pg
def test_invariant_overtid_uten_flagg(migrator):
    """OVERTID ER ET FUNN, IKKE ET FLAGG.

    Det finnes ingen kolonne noen kan sette og gå videre fra, og ingen
    parameter noen kan sende inn. Overtid UTLEDES av timene mot
    tenantens egen normaltid.

    MUTASJONEN SOM DREPER DENNE: `overtid BOOLEAN` på
    `timeregistrering`.
    """
    kolonner = migrator.execute(
        "SELECT table_name, column_name"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND column_name LIKE '%%overtid%%'",
        (list(EGNE),)).fetchall()
    migrator.rollback()
    # `lonnsfunn.funntype` bærer ordet som en VERDI, ikke som en kolonne.
    assert kolonner == [], kolonner

    from api.lonn import registrer_timer_endepunkt
    kilde = _bare_kode(MODULFILER[0])
    i = kilde.index("def registrer_timer_endepunkt")
    kropp = kilde[i:kilde.index("\ndef ", i + 1)]
    assert "overtid" not in kropp, "API-et tar imot et overtidsflagg"
    doc = registrer_timer_endepunkt.__doc__ or ""
    assert "overtid" in doc.lower()

    # FLATEN: målt på det den SENDER og det den TILBYR, ikke på at
    # ordet forekommer — `MERKE` slår opp funntypen `overtid`, og det
    # er nettopp riktig bruk.
    kilde_js = FLATE.read_text(encoding="utf-8")
    i = kilde_js.index("registrerTimer(gjeldende.taker_id, {")
    nyttelast = kilde_js[i:kilde_js.index("}, idem)", i)]
    assert "overtid" not in nyttelast, \
        "flaten sender et overtidsflagg"
    # …og det finnes ingen kontroll å sette det med.
    assert not re.search(r'id: "[a-z-]*overtid', kilde_js), \
        "flaten tilbyr en overtidskontroll"


@pg
def test_overtid_maales_bade_per_dag_og_per_uke(migrator):
    """UKESOVERTIDEN ER DEN SOM ER LETT Å GLEMME.

    En modul som bare så på dagen ville sluppet gjennom seks
    normallange dager på rad — og det er nettopp den formen for overtid
    som ikke oppdages av noen.

    MUTASJONEN SOM DREPER DENNE: fjern ukesarmen fra
    `m39_funnkandidater`.
    """
    tenant = _tenantnavn("overtid")
    c = _rt()
    try:
        _terskler(c, tenant, dag=450, uke=2250, avvik=1440,
                  uten_plan=3650)
        dag = _taker(c, tenant, ref="DAG")
        _plan(c, tenant, dag, minutter=450)
        _timer(c, tenant, dag, "2026-08-03", 500)
        uke = _taker(c, tenant, ref="UKE")
        _plan(c, tenant, uke, minutter=440)
        # SEKS DAGER À 440 MINUTTER: ingen dag er over 450, men uka er
        # 2640 — 390 minutter over normaluka.
        for i, d in enumerate(["2026-08-03", "2026-08-04", "2026-08-05",
                               "2026-08-06", "2026-08-07",
                               "2026-08-08"]):
            _timer(c, tenant, uke, d, 440, kilde_ref=f"u{i}")
        rolig = _taker(c, tenant, ref="ROLIG")
        _plan(c, tenant, rolig, minutter=440)
        for i, d in enumerate(["2026-08-03", "2026-08-04",
                               "2026-08-05"]):
            _timer(c, tenant, rolig, d, 440, kilde_ref=f"r{i}")
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_lonn_eier")
    funn = {}
    for ref, ft, og in migrator.execute(
            "SELECT s.ekstern_ref, k.funntype, k.over_grense"
            "  FROM m39_funnkandidater(%s, '2026-09-03'::date) k"
            "  JOIN lonnstaker s ON s.tenant=%s"
            "   AND s.taker_id=k.taker_id ORDER BY 1,2",
            (tenant, tenant)).fetchall():
        funn.setdefault(ref, []).append((ft, og))
    migrator.rollback()
    assert funn.get("DAG") == [("overtid", 50)], funn
    # 6 × 440 = 2640, minus normaluka 2250.
    assert funn.get("UKE") == [("overtid", 390)], funn
    # TRE NORMALDAGER er verken dags- eller ukesovertid.
    assert funn.get("ROLIG") is None, funn


@pg
def test_dags_og_ukesovertid_blir_EN_funnrad(migrator):
    """BEGGE ARMENE HAR SAMME FUNNTYPE, og funnraden er nøklet på
    (tenant, taker_id, funntype).

    To rader fra kandidatdøren ville kollidert i sveipens `ON CONFLICT`
    («cannot affect row a second time») og tatt HELE NATTA med seg —
    ikke bare denne takeren.

    MUTASJONEN SOM DREPER DENNE: fjern `GROUP BY` til slutt i
    `m39_funnkandidater`.
    """
    tenant = _tenantnavn("begge")
    c = _rt()
    try:
        _terskler(c, tenant, dag=450, uke=2250, avvik=1440,
                  uten_plan=3650)
        tid = _taker(c, tenant, ref="BEGGE")
        _plan(c, tenant, tid, minutter=450)
        # FEM DAGER À 600: hver dag er over 450 OG uka er over 2250.
        for i, d in enumerate(["2026-08-03", "2026-08-04", "2026-08-05",
                               "2026-08-06", "2026-08-07"]):
            _timer(c, tenant, tid, d, 600, kilde_ref=f"b{i}")
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_lonn_eier")
    rader = migrator.execute(
        "SELECT funntype, over_grense, antall_dager FROM"
        " m39_funnkandidater(%s,'2026-09-03'::date) WHERE"
        " funntype='overtid'", (tenant,)).fetchall()
    migrator.rollback()
    assert len(rader) == 1, f"to overtidsrader: {rader}"
    # DEN STØRSTE OVERSKRIDELSEN VINNER: uka er 3000 − 2250 = 750,
    # dagen er 600 − 450 = 150.
    assert rader[0][1] == 750, rader
    # …og alle dagene telles med, både de fem dagene og den ene uka.
    assert rader[0][2] == 6, rader

    # …OG SVEIPEN OVERLEVER DET. Dette er den egentlige porten: uten
    # sammenslåingen feiler HELE kjøringen, ikke bare denne takeren.
    with _sv() as v:
        rad = _sveip(v)
    assert rad[0] >= 1, rad
    assert [f[0] for f in _funn(migrator, tenant) if f[4]] == ["overtid"]


# ---------------------------------------------------------------------------
# INVARIANT 7: lonnsgrunnlag_overskrevet
# ---------------------------------------------------------------------------

@pg
def test_invariant_lonnsgrunnlag_overskrevet(migrator):
    """EN FEILFØRT TIME RETTES MED EN NY RAD, aldri ved å endre den
    gamle — og det er nettopp det sporet en lønnstvist står på.

    To gjerder: eieren har ikke rettigheten, og VAKTEN stanser den som
    likevel har den.
    """
    tenant = _tenantnavn("frosset")
    c = _rt()
    try:
        _terskler(c, tenant)
        tid = _taker(c, tenant)
        _plan(c, tenant, tid)
        _timer(c, tenant, tid, "2026-08-03", 450, kilde_ref="a",
               notat="ført av ansatt")
        # RETTINGEN ER EN NY RAD, med sin egen kilde.
        _timer(c, tenant, tid, "2026-08-03", 60, kilde="korreksjon",
               kilde_ref="a-rettet", notat="glemte en time")
        _sett_kontekst(c, tenant)
        rader = c.execute(
            "SELECT minutter, kilde, notat FROM"
            " m39_timehistorikken(%s,%s,200)", (tenant, tid)).fetchall()
        c.rollback()
    finally:
        c.close()
    # BEGGE STÅR. Historikken viser at noe ble rettet, og hva.
    assert len(rader) == 2
    assert {r[1] for r in rader} == {"fort_av_ansatt", "korreksjon"}

    rettigheter = migrator.execute(
        "SELECT DISTINCT privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_lonn_eier'"
        "   AND table_name='timeregistrering' ORDER BY 1").fetchall()
    migrator.rollback()
    assert [r[0] for r in rettigheter] == ["INSERT", "SELECT"]
    for sql, ord_ in (("UPDATE timeregistrering SET minutter=1",
                       "FROSSET"),
                      ("DELETE FROM timeregistrering",
                       "DELETE avvist")):
        _sett_kontekst(migrator, tenant)
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(sql + " WHERE tenant=%s", (tenant,))
        assert ord_ in str(ei.value), sql
        migrator.rollback()


@pg
def test_en_framtidig_dato_kan_ikke_skjule_en_arbeidsdag(migrator):
    """Sveipen måler «timer med dato <= i dag». En framtidsdatert rad
    ville vært usynlig for den (110-112s lærdom), og timene ville stått
    uten funn til datoen passerte.

    MUTASJONEN SOM DREPER DENNE: fjern datosjekken fra
    `m39_registrer_timer`.
    """
    tenant = _tenantnavn("framtid")
    c = _rt()
    try:
        _terskler(c, tenant)
        tid = _taker(c, tenant)
        _plan(c, tenant, tid)
        _timer(c, tenant, tid, "2026-08-03", 450)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m39_registrer_timer(%s,%s,%s,current_date + 1,"
                "450,'P-1','fort_av_ansatt','f','i morgen','u')",
                (tenant, uuid.uuid4(), tid))
        assert "framtida" in str(ei.value)
        c.rollback()
        # SAMME KILDEHENDELSE TO GANGER er heller ikke to arbeidsdager.
        _timer(c, tenant, tid, "2026-08-04", 450, kilde_ref="dup")
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m39_registrer_timer(%s,%s,%s,'2026-08-05',450,"
                "'P-1','fort_av_ansatt','dup','dublett','u')",
                (tenant, uuid.uuid4(), tid))
        assert "kilde_unik" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    n = migrator.execute(
        "SELECT count(*) FROM timeregistrering WHERE tenant=%s",
        (tenant,)).fetchone()[0]
    migrator.rollback()
    assert n == 2


# ---------------------------------------------------------------------------
# INVARIANT 8: timegrense_hardkodet
# ---------------------------------------------------------------------------

def test_invariant_timegrense_hardkodet():
    for fil in MODULFILER:
        for m in re.finditer(
                r"^([A-Z_]*(?:GRENSE|TERSKEL|NORMALTID|DOGN|OVERTID)"
                r"[A-Z_]*)\s*=\s*(\d+)", _bare_kode(fil), re.M):
            # `MINUTTER_DOGN`/`MINUTTER_UKE` er FYSISKE enheter —
            # antall minutter i et døgn og i en uke — ikke
            # policygrenser noen har valgt. De reelle grensene ligger i
            # `lonnsterskel`.
            assert m.group(1) in ("GRENSE", "TERSKELGRENSER",
                                  "MINUTTER_DOGN", "MINUTTER_UKE"), \
                f"{fil.name} har grensekonstanten {m.group(1)}"
    from drift import lonnssveip
    assert lonnssveip.GRENSE == 500
    kode = _bare_kode(MIGRASJON)
    assert "m39_sveip_lonnsgrunnlag(p_grense INT DEFAULT 500)" in kode
    assert "FROM public.lonnsterskel" in kode
    from api.lonn import terskler_endepunkt
    doc = terskler_endepunkt.__doc__ or ""
    assert "M-1" in doc and "gap" in doc.lower()


@pg
def test_grensene_versjoneres_og_kan_ikke_slettes(migrator):
    tenant = _tenantnavn("terskelversjon")
    c = _rt()
    try:
        assert _terskler(c, tenant, dag=450) == 1
        assert _terskler(c, tenant, dag=420) == 2
        # EN UKE KAN IKKE VÆRE KORTERE ENN EN DAG. Uten den sjekken
        # ville hver eneste arbeidsdag blitt ukesovertid, og funnlista
        # vært ubrukelig fra første natt.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m39_sett_terskler(%s,450,300,0,7,60,'u')",
                      (tenant,))
        assert "kortere enn per dag" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("DELETE FROM lonnsterskel WHERE tenant=%s",
                         (tenant,))
    assert "DELETE avvist" in str(ei.value)
    migrator.rollback()
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "UPDATE lonnsterskel SET normaltid_minutter_dag=1"
            " WHERE tenant=%s", (tenant,))
    assert "versjonen må øke" in str(ei.value)
    migrator.rollback()


# ---------------------------------------------------------------------------
# Funnene over tid, og sveipen
# ---------------------------------------------------------------------------

@pg
def test_ukjent_prosjektkode_maales_mot_var_egen_plan(migrator):
    """`prosjektkode_gyldig` måles mot ARBEIDSPLANEN VÅR EGEN, ikke mot
    M-25s prosjektregister.

    Det er en ÆRLIGERE måling, ikke en svakere: den svarer på «jobbet
    hen på noe hen var satt opp på», som er det spørsmålet en timeliste
    faktisk reiser.
    """
    tenant = _tenantnavn("kode")
    c = _rt()
    try:
        _terskler(c, tenant, dag=1440, uke=10080, avvik=1440,
                  uten_plan=3650)
        rett = _taker(c, tenant, ref="RETT")
        _plan(c, tenant, rett, kode="P-1")
        _timer(c, tenant, rett, "2026-08-03", 450, kode="P-1")
        feil = _taker(c, tenant, ref="FEIL")
        _plan(c, tenant, feil, kode="P-1")
        _timer(c, tenant, feil, "2026-08-03", 450, kode="P-9")
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_lonn_eier")
    funn = dict(migrator.execute(
        "SELECT s.ekstern_ref, k.funntype FROM"
        " m39_funnkandidater(%s,'2026-09-03'::date) k JOIN lonnstaker s"
        " ON s.tenant=%s AND s.taker_id=k.taker_id", (tenant, tenant)
        ).fetchall())
    migrator.rollback()
    assert "RETT" not in funn, funn
    assert funn.get("FEIL") == "ukjent_prosjektkode", funn


@pg
def test_de_tre_uopprettelige_funnene_kan_lukkes(migrator):
    """CodeRabbit, alvorlig og REELT — og skarpere enn det ser ut.

    Skillet er hvorvidt funnet har et BOTEMIDDEL.
    `time_uten_arbeidsplan` KAN rettes: noen fører en plan, og dagen
    blir målt. `overtid`, `avvik_mot_plan` og `ukjent_prosjektkode` kan
    IKKE rettes — timeregistreringene er FROSSET, og overtid som har
    skjedd, har skjedd.

    Uten et vindu ville de tre derfor aldri kunne lukkes. Innen et år
    ville hver aktiv ansatt hatt alle tre permanent åpne, og
    funnlisten vært ren støy. ET FUNNREGISTER SOM ALLTID SIER JA SIER
    INGENTING.

    MUTASJONEN SOM DREPER DENNE: fjern
    `(p_dag - m.dato) <= t.vurderingsvindu_dogn` fra én av armene.
    """
    tenant = _tenantnavn("vindu")
    c = _rt()
    try:
        # Vindu på 30 døgn. `uten_plan` står NORMALT (7 døgn), for
        # det er nettopp den armen som skal vise seg å IKKE følge
        # vinduet.
        _terskler(c, tenant, dag=450, uke=2250, avvik=0, uten_plan=7,
                  vindu=30)
        gammel = _taker(c, tenant, ref="GAMMEL")
        _plan(c, tenant, gammel, minutter=450, kode="P-1")
        # En dag med overtid, avvik OG feil kode — men for lenge siden.
        _timer(c, tenant, gammel, "2026-01-05", 600, kode="P-9")
        fersk = _taker(c, tenant, ref="FERSK")
        _plan(c, tenant, fersk, minutter=450, kode="P-1")
        _timer(c, tenant, fersk, "2026-08-25", 600, kode="P-9")
        # …og en umålt time som er ELDRE ENN VINDUET: den skal STÅ,
        # fordi den KAN rettes.
        umaalt = _taker(c, tenant, ref="UMAALT")
        _timer(c, tenant, umaalt, "2026-01-05", 450)
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_lonn_eier")
    funn = {}
    for ref, ft in migrator.execute(
            "SELECT s.ekstern_ref, k.funntype FROM"
            " m39_funnkandidater(%s,'2026-09-03'::date) k JOIN"
            " lonnstaker s ON s.tenant=%s AND s.taker_id=k.taker_id"
            " ORDER BY 1,2", (tenant, tenant)).fetchall():
        funn.setdefault(ref, []).append(ft)
    migrator.rollback()
    # UTENFOR VINDUET: ingen av de tre står igjen.
    assert "GAMMEL" not in funn, funn
    # INNENFOR: alle tre.
    assert funn.get("FERSK") == ["avvik_mot_plan", "overtid",
                                 "ukjent_prosjektkode"], funn
    # …OG DEN SOM KAN RETTES STÅR UANSETT ALDER.
    assert funn.get("UMAALT") == ["time_uten_arbeidsplan"], funn

    # SVEIPEN LUKKER DEM. Det er den egentlige porten: uten vinduet
    # ville radene stått åpne for alltid.
    with _sv() as v:
        _sveip(v)
    apne = sorted({f[0] for f in _funn(migrator, tenant) if f[4]})
    assert apne == ["avvik_mot_plan", "overtid",
                    "time_uten_arbeidsplan", "ukjent_prosjektkode"], apne
    c = _rt()
    try:
        # SNEVRER VINDUET slik at heller ikke FERSK er innenfor.
        _terskler(c, tenant, dag=450, uke=2250, avvik=0,
                  uten_plan=7, vindu=1)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    apne = sorted({f[0] for f in _funn(migrator, tenant) if f[4]})
    # DE TRE ER LUKKET. Den fjerde står, fordi den kan rettes.
    assert apne == ["time_uten_arbeidsplan"], apne
    # …og radene BLIR STÅENDE: at et funn HAR stått er også en måling.
    assert len(_funn(migrator, tenant)) == 4


@pg
def test_en_tenant_uten_grenser_er_et_funn(migrator):
    tenant = _tenantnavn("utenterskel")
    c = _rt()
    try:
        _taker(c, tenant)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert [f[0] for f in _funn(migrator, tenant)] == ["ingen_terskel"]


@pg
def test_sveipen_er_idempotent_og_lukker_uten_aa_slette(migrator):
    tenant = _tenantnavn("idempotens")
    c = _rt()
    try:
        _terskler(c, tenant, uten_plan=7)
        tid = _taker(c, tenant)
        _timer(c, tenant, tid, "2026-01-05", 450)
    finally:
        c.close()
    with _sv() as v:
        rad1 = _sveip(v)
    assert rad1[1] >= 1, rad1
    with _sv() as v:
        rad2 = _sveip(v)
    assert rad2[1] == 0, "sveip nummer to skrev nye rader"
    assert rad2[2] >= 1, rad2
    c = _rt()
    try:
        _plan(c, tenant, tid, fra="2026-01-01")
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert [f[0] for f in _funn(migrator, tenant) if f[4]] == []
    assert len(_funn(migrator, tenant)) == 1


@pg
def test_oppdaterte_funn_akkumuleres_over_tenantene(migrator):
    """112s CodeRabbit-lærdom, portet FØR den kunne oppstå her:
    `INTO v_oppdaterte` ville satt summen på nytt for hver tenant."""
    a = _tenantnavn("akk-a")
    b = _tenantnavn("akk-b")
    for tenant, antall in ((a, 2), (b, 3)):
        c = _rt()
        try:
            _terskler(c, tenant, uten_plan=7)
            for i in range(antall):
                tid = _taker(c, tenant, ref=f"R{i}")
                _timer(c, tenant, tid, "2026-01-05", 450,
                       kilde_ref=f"k{i}")
        finally:
            c.close()
    with _sv() as v:
        forste = _sveip(v)
    assert forste[1] >= 5, forste
    with _sv() as v:
        andre = _sveip(v)
    assert andre[1] == 0, andre
    assert andre[2] >= 5, \
        f"oppdaterte ble overskrevet per tenant: {andre}"


@pg
def test_et_deaktivert_subjekt_lukker_funnene_og_beholder_historikken(
        migrator):
    tenant = _tenantnavn("deaktiver")
    c = _rt()
    try:
        _terskler(c, tenant, uten_plan=7)
        tid = _taker(c, tenant)
        _timer(c, tenant, tid, "2026-01-05", 450)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert _funn(migrator, tenant)
    c = _rt()
    try:
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT m39_sett_takeraktiv(%s,%s,false,'u')",
                         (tenant, tid)).fetchone()[0] is True
        c.commit()
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT m39_sett_takeraktiv(%s,%s,false,'u')",
                         (tenant, tid)).fetchone()[0] is False
        c.commit()
        # EN DEAKTIVERT TAKER TAR IKKE IMOT NYE TIMER…
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m39_registrer_timer(%s,%s,%s,'2026-02-01',450,"
                "'P-1','fort_av_ansatt','z','x','u')",
                (tenant, uuid.uuid4(), tid))
        assert "deaktivert" in str(ei.value)
        c.rollback()
        # …OG INGEN NY ARBEIDSPLAN.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m39_sett_arbeidsplan(%s,%s,%s,450,'P-1',"
                      "'2026-02-01'::date,'x','u')",
                      (tenant, uuid.uuid4(), tid))
        assert "deaktivert" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    assert [f[0] for f in _funn(migrator, tenant) if f[4]] == []
    _sett_kontekst(migrator, tenant)
    n = migrator.execute(
        "SELECT count(*) FROM timeregistrering WHERE tenant=%s",
        (tenant,)).fetchone()[0]
    migrator.rollback()
    assert n == 1
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("DELETE FROM lonnstaker WHERE tenant=%s",
                         (tenant,))
    assert "DELETE avvist" in str(ei.value)
    migrator.rollback()


@pg
def test_sveipen_utbetaler_ingenting_og_rorer_ingen_time(migrator):
    """Målt på VIRKELIGHETEN: en full sveip endrer ikke ett radantall
    utenfor registeret — OG DEN RØRER INGEN TIME."""
    tenant = _tenantnavn("sveip")
    c = _rt()
    try:
        _terskler(c, tenant, uten_plan=7)
        tid = _taker(c, tenant)
        _timer(c, tenant, tid, "2026-01-05", 450)
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    for_bok = migrator.execute(
        "SELECT time_id, minutter, dato FROM timeregistrering"
        " WHERE tenant=%s ORDER BY time_id", (tenant,)).fetchall()
    migrator.rollback()
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
    etter_bok = migrator.execute(
        "SELECT time_id, minutter, dato FROM timeregistrering"
        " WHERE tenant=%s ORDER BY time_id", (tenant,)).fetchall()
    migrator.rollback()
    assert for_bok == etter_bok, "sveipen rørte timegrunnlaget"


# ---------------------------------------------------------------------------
# INVARIANT 9: tenantlekkasje_i_lonnsregister
# ---------------------------------------------------------------------------

@pg
def test_invariant_tenantlekkasje_direkte(migrator):
    a = _tenantnavn("lekk-a")
    b = _tenantnavn("lekk-b")
    c = _rt()
    try:
        _terskler(c, a)
        _taker(c, a)
        _terskler(c, b)
        _sett_kontekst(c, b)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT * FROM m39_lonnsstatus(%s)", (a,))
        assert "tenantkontekst" in str(ei.value)
        c.rollback()
        _sett_kontekst(c, a)
        assert c.execute("SELECT * FROM m39_lonnsstatus(%s)",
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
def test_kryss_tenant_policyen_er_snever(migrator):
    """SVEIPENS ENESTE KRYSS-TENANT-SVAR er «hvilke tenanter finnes»."""
    rader = migrator.execute(
        "SELECT tablename, cmd, roles::text, qual FROM pg_policies"
        " WHERE schemaname='public' AND policyname LIKE 'm39_%'"
        " ORDER BY 1").fetchall()
    migrator.rollback()
    assert len(rader) == 1, rader
    tabell, cmd, roller, qual = rader[0]
    assert tabell == "lonnstaker"
    assert cmd == "SELECT"
    assert roller == "{disponit_lonn_eier}"
    assert "disponit.tenant" in qual and "IS NULL" in qual.upper()


@pg
def test_invariant_tenantlekkasje_over_api(migrator, klient):
    fremmed = _tenantnavn("fremmed")
    egen = "EGEN-" + secrets.token_hex(4)
    fremmed_ref = "FREMMED-" + secrets.token_hex(4)
    c = _rt()
    try:
        _terskler(c, TENANT)
        _taker(c, TENANT, ref=egen)
        _terskler(c, fremmed)
        _taker(c, fremmed, ref=fremmed_ref)
    finally:
        c.close()
    cookie, _csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/lonn", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    kropp = json.dumps(r.json())
    assert egen in kropp
    assert fremmed_ref not in kropp


@pg
def test_ingen_av_de_fem_tabellene_kan_tommes(migrator):
    tenant = _tenantnavn("truncate")
    c = _rt()
    try:
        _terskler(c, tenant, uten_plan=7)
        tid = _taker(c, tenant)
        _plan(c, tenant, tid)
        _timer(c, tenant, tid, "2026-01-05", 450)
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
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("TRUNCATE public.lonnstaker CASCADE")
    assert "TRUNCATE avvist" in str(ei.value), ei.value
    migrator.rollback()


def test_skrivedorene_som_leser_en_tilstand_laser_raden():
    """…OG DE LÅSER EN RAD DE HAR UPDATE PÅ.

    `SELECT ... FOR UPDATE` krever UPDATE-retten, og `timeregistrering`
    har den ikke — den er frosset. Dørene låser derfor TAKEREN, som er
    den raden som faktisk kan endre seg under dem (M-42s lærdom, 110,
    og 112s gjentakelse).
    """
    sql = _bare_kode(MIGRASJON)
    for doer in ("m39_registrer_timer", "m39_sett_arbeidsplan",
                 "m39_sett_takeraktiv"):
        i = sql.index(f"CREATE FUNCTION {doer}(")
        kropp = sql[i:sql.index("END $$;", i)]
        assert "FOR UPDATE" in kropp, doer
        for m in re.finditer(r"FOR UPDATE", kropp):
            start = kropp.rfind("SELECT", 0, m.start())
            assert start != -1, doer
            assert "public.lonnstaker" in kropp[start:m.end()], doer


# ---------------------------------------------------------------------------
# Sveipearbeideren
# ---------------------------------------------------------------------------

@pg
def test_sveipen_nekter_kontekst(migrator):
    with _sv() as v:
        v.execute("SELECT set_config('disponit.tenant','t',false)")
        with pytest.raises(psycopg.Error) as ei:
            v.execute("SELECT * FROM m39_sveip_lonnsgrunnlag(500)")
        assert "KRYSS-TENANT" in str(ei.value)
        v.rollback()


@pg
def test_sveiperollen_har_ingen_tabellrettigheter(migrator):
    if not LONNSSVEIP_DSN:
        pytest.skip("DISPONIT_TEST_LONNSSVEIP_DSN ikke satt")
    rader = migrator.execute(
        "SELECT table_name, privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_lonnssveip'").fetchall()
    migrator.rollback()
    assert rader == [], rader


def test_sveipen_nekter_aa_starte_uten_egen_dsn(tmp_path, monkeypatch):
    from drift import kjor_lonnssveip
    monkeypatch.delenv("DISPONIT_LONNSSVEIP_URL", raising=False)
    monkeypatch.setenv("DISPONIT_LONNSSVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.setattr("db.hemmeligheter.last_credentials", lambda: None)
    assert kjor_lonnssveip.main() == 2


def test_arbeidernokkelen_er_modulens_egen():
    from drift import (adressesveip, betalingssveip, kontovaktsveip,
                       lagersveip, lonnssveip, prisboksveip)
    nokler = [m.ARBEIDERNOKKEL for m in
              (adressesveip, betalingssveip, kontovaktsveip, lagersveip,
               prisboksveip)]
    assert lonnssveip.ARBEIDERNOKKEL not in nokler


# ---------------------------------------------------------------------------
# INVARIANT 10: ui_axe_alvorlige_brudd
# ---------------------------------------------------------------------------

def test_invariant_ui_axe_bor_i_js_suiten():
    fil = (ROT / "platform" / "core" / "ui" / "test" / "lonn.test.js")
    assert fil.exists(), "lonn.test.js mangler"
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
        " ('https://m39.test', %s) RETURNING bruker_id",
        ("s39-" + secrets.token_hex(6),)).fetchone()[0]
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
@dekker("lonn_ulovlig_tilstand")
def test_http_time_i_framtida_er_409(migrator, klient):
    """FEILVEIEN `lonn_ulovlig_tilstand`, ende til ende.

    Kroppen ER velformet: minuttene er et heltall, kilden er fra det
    lukkede settet, notatet står der. Det er BASEN som sier at en time
    ikke kan være arbeidet i framtida.

    Dette er også testen `test_api_porter.test_hver_feilvei_har_en_test`
    krever for den nye feilveien.
    """
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _hpost(klient, cookie, csrf, "/v1/lonn/terskler",
               {"normaltid_minutter_dag": 450,
                "normaltid_minutter_uke": 2250,
                "avvik_minutter": 0, "uten_plan_dogn": 7,
                "vurderingsvindu_dogn": 3650})
    assert r.status_code in (200, 201), r.text
    ref = "H-" + secrets.token_hex(4)
    r = _hpost(klient, cookie, csrf, "/v1/lonn/taker",
               {"ekstern_ref": ref, "navn": "HTTP Ansatt"})
    assert r.status_code in (200, 201), r.text
    tid = r.json()["taker_id"]
    # …og et felt som ALLTID er null står ikke i svaret.
    assert "ny" not in r.json()

    # EN TIME UTEN PLAN: svaret sier det MED ÉN GANG.
    #
    # DATOEN LIGGER FØR PLANEN under. Planen er DATERT, så en plan fra
    # 2026-01-01 dekker også dager ført før den ble registrert — og det
    # er riktig: planen sier hva som gjaldt DEN DAGEN, ikke hva vi
    # visste da vi skrev den.
    r = _hpost(klient, cookie, csrf, f"/v1/lonn/{tid}/timer",
               {"dato": "2025-12-01", "minutter": 450,
                "prosjektkode": "P-1", "kilde": "fort_av_ansatt",
                "kilde_ref": "evt_h0", "notat": "uten plan"})
    assert r.status_code in (200, 201), r.text
    assert r.json()["har_plan"] is False

    r = _hpost(klient, cookie, csrf, f"/v1/lonn/{tid}/plan",
               {"planlagt_minutter_dag": 450, "prosjektkode": "P-1",
                "gyldig_fra": "2026-01-01", "begrunnelse": "avtalt"})
    assert r.status_code in (200, 201), r.text

    idem = secrets.token_urlsafe(24)
    kropp = {"dato": "2026-08-04", "minutter": 450,
             "prosjektkode": "P-1", "kilde": "fort_av_ansatt",
             "kilde_ref": "evt_h1", "notat": "ført"}
    r = _hpost(klient, cookie, csrf, f"/v1/lonn/{tid}/timer", kropp,
               idem=idem)
    assert r.status_code in (200, 201), r.text
    assert r.json()["har_plan"] is True
    # SP-2: SAMME NØKKEL GIR IKKE TO ARBEIDSDAGER.
    r2 = _hpost(klient, cookie, csrf, f"/v1/lonn/{tid}/timer", kropp,
                idem=idem)
    assert r2.status_code == 409, r2.text

    # FRAMTIDIG DATO: TILSTANDEN sier nei.
    from datetime import date, timedelta
    i_morgen = (date.today() + timedelta(days=1)).isoformat()
    r = _hpost(klient, cookie, csrf, f"/v1/lonn/{tid}/timer",
               {**kropp, "kilde_ref": "evt_h2", "dato": i_morgen})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "lonn_ulovlig_tilstand"
    # EN PLAN SKREVET BAKOVER: også en tilstand.
    r = _hpost(klient, cookie, csrf, f"/v1/lonn/{tid}/plan",
               {"planlagt_minutter_dag": 420, "prosjektkode": "P-2",
                "gyldig_fra": "2025-06-01", "begrunnelse": "bakover"})
    assert r.status_code == 409, r.text
    # SAMME REFERANSE TO GANGER likeså.
    r = _hpost(klient, cookie, csrf, "/v1/lonn/taker",
               {"ekstern_ref": ref, "navn": "Dublett"})
    assert r.status_code == 409, r.text

    # …og 7,5 TIME SOM FLYTTALL er 400: KROPPEN er feil.
    for felt, verdi in (("minutter", 7.5), ("minutter", 450.0),
                        ("minutter", "450"), ("kilde", "gjetning"),
                        ("kilde", None), ("prosjektkode", "")):
        r = _hpost(klient, cookie, csrf, f"/v1/lonn/{tid}/timer",
                   {**kropp, "kilde_ref": "evt_x", felt: verdi})
        assert r.status_code == 400, (felt, verdi, r.text)
    # DET FINNES INGEN OVERTIDSPARAMETER — et forsøk blir stille
    # ignorert, ikke tatt imot, og dagen blir et FUNN som før.
    r = _hpost(klient, cookie, csrf, f"/v1/lonn/{tid}/timer",
               {**kropp, "kilde_ref": "evt_ot", "dato": "2026-08-05",
                "minutter": 600, "overtid": True})
    assert r.status_code in (200, 201), r.text
    # `aktiv` ER PÅKREVD.
    r = _hpost(klient, cookie, csrf, f"/v1/lonn/{tid}/aktiv", {})
    assert r.status_code == 400, r.text

    # DAGENE ER BEVISET, med begge tallene på samme linje.
    r = klient.get(f"/v1/lonn/{tid}/dager",
                   cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    dager = {d["dato"]: d for d in r.json()["dager"]}
    assert dager["2026-08-04"]["minutter"] == 450
    assert dager["2026-08-04"]["planlagt_minutter"] == 450
    assert dager["2026-08-04"]["avvik_minutter"] == 0
    # DAGEN FØR PLANEN: `planlagt_minutter` er NULL, ikke 0. «Ingen
    # plan» og «planlagt fri» er to helt forskjellige svar.
    assert dager["2025-12-01"]["planlagt_minutter"] is None
    assert dager["2025-12-01"]["avvik_minutter"] is None
    assert dager["2026-08-05"]["avvik_minutter"] == 150

    r = klient.get(f"/v1/lonn/{tid}/historikk",
                   cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    assert len(r.json()["timer"]) == 3
    r = klient.get(f"/v1/lonn/{tid}/planer",
                   cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    assert len(r.json()["planer"]) == 1


@pg
def test_http_lesingen_bruker_okonomi_read(migrator, klient):
    from api.app import RUTESCOPE
    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert RUTESCOPE[("GET", "/v1/lonn")] == "okonomi:read"
    assert "okonomi:read" in ROLLE_TIL_SCOPES["admin"]
    for rolle in ("leser", "sikkerhet", "godkjenner"):
        assert "okonomi:read" not in ROLLE_TIL_SCOPES[rolle], rolle
    cookie, _c = _browserokt(migrator, ["leser"])
    r = klient.get("/v1/lonn", cookies={_C_SESJON(): cookie})
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# §0
# ---------------------------------------------------------------------------

def test_grensen_ble_registrert_for_koden():
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m39-v1"]
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["punktbinding"] == {}
    egen = Path(__file__).read_text(encoding="utf-8")
    for inv in g["invarianter"]:
        assert inv in egen, f"invarianten {inv} har ingen port"
