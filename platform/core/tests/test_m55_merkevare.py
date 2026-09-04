"""M-55 merkevare- og IP-overvåker v1 (120) — BEVISET, IKKE KRAVET.

Grensen `m55-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

DEN SKARPESTE PORTEN ER `funn_uten_bevaringskopi`, og den måler et
FRAVÆR i datamodellen: `merkevarefunn.kopi_id` er NOT NULL med
fremmednøkkel. Et funn uten bevaringskopi kan ikke uttrykkes.

Hvorfor det er den skarpeste: et merkevarefunn er en påstand om at NOEN
ANDRE bruker noe som ligner vårt. En nettside som er endret eller borte
den dagen saken tas opp, er ingen sak — og et funn som ikke kan bevises
er VERRE enn ingen funn, fordi noen handler på det.

DEN NEST SKARPESTE ER `modulen_sendte_krav`, og den måler også et
fravær: 120 har ingen mottaker, ingen kravtekst og ingen utboks. Et
krav sendt på et automatisk funn er en ANKLAGE MOT EN NAVNGITT PART, og
en feilaktig anklage er ikke reversibel ved å trekke den.

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

MERKEVARESVEIP_DSN = os.environ.get("DISPONIT_TEST_MERKEVARESVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "120_m55_merkevarefunn.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "merkevare.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "merkevare.py",
    ROT / "platform" / "drift" / "merkevaresveip.py",
    ROT / "platform" / "drift" / "kjor_merkevaresveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("merkevarekrav", "merkevare", "bevaringskopi",
        "merkevarefunn", "forvekslingsvurdering", "merkevarevarsel")

_STRENG = re.compile(
    r"'''.*?'''" r'|""".*?"""'
    r"|`(?:[^`\\]|\\.)*`"
    r"|'(?:[^'\\]|\\.|'')*'"
    r'|"(?:[^"\\]|\\.)*"', re.S)


def _bare_kode(fil: Path, *, uten_strenger: bool = False) -> str:
    """Filens innhold uten kommentarer OG uten docstrings (m24-formen).

    PORTEN SKAL MÅLE HANDLINGEN, IKKE BEGRUNNELSEN. Klynge 6 lærte det
    tre ganger: en port som leter i rå filtekst treffer kommentaren som
    forklarer HVORFOR et mønster er unngått, og straffer da nettopp den
    setningen som gjør koden forståelig.
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
    return koble(MERKEVARESVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m55-{merke}-{secrets.token_hex(4)}"


def _krav(c, tenant, *, terskel=80, funnfrist=14, henvfrist=3,
          aktor="u-test", nokkel=None):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m55_sett_krav(%s,%s,%s,%s,%s,%s)",
                  (tenant, terskel, funnfrist, henvfrist, aktor,
                   nokkel or secrets.token_hex(8))).fetchone()[0]
    c.commit()
    return v


def _merke(c, tenant, *, navn="Disponit", art="varemerke",
           nummer="301234", foerer="Patentstyret",
           klasser=("9", "42"), fra="2024-01-01", mid=None,
           aktor="u-test"):
    mid = mid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute("SELECT m55_registrer_merkevare("
              "%s,%s,%s,%s,%s,%s,%s,%s::date,%s)",
              (tenant, mid, navn, art, nummer, foerer, list(klasser),
               fra, aktor))
    c.commit()
    return mid


def _kopi(c, tenant, *, url=None, alder_dogn=2, sha=None,
          bytes_=40960, medietype="text/html", kid=None,
          aktor="u-test"):
    kid = kid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m55_registrer_bevaringskopi("
        "%s,%s,%s,now() - make_interval(days => %s),%s,%s,%s,%s,%s)",
        (tenant, kid,
         url or f"https://eksempel.no/{secrets.token_hex(4)}",
         alder_dogn, sha or secrets.token_hex(32), bytes_, medietype,
         f"artefakt/{secrets.token_hex(4)}", aktor))
    c.commit()
    return kid


def _funn(c, tenant, mid, kid, *, observert="Dispunit",
          bruksform="annonsetekst", kontekst="Annonse paa sok",
          motpart="Ukjent AS", fid=None, aktor="u-test"):
    fid = fid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute("SELECT m55_registrer_funn(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (tenant, fid, mid, kid, observert, bruksform, kontekst,
               motpart, aktor))
    c.commit()
    return fid


def _vurder(c, tenant, fid, *, vid=None, aktor="u-test"):
    vid = vid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    rad = c.execute("SELECT * FROM m55_vurder_funn(%s,%s,%s,%s)",
                    (tenant, fid, vid, aktor)).fetchone()
    c.commit()
    return rad


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m55_sveip_merkevare(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _varsler(c, tenant, *, bare_apne=True):
    _sett_kontekst(c, tenant)
    rader = c.execute(
        "SELECT varseltype, funn_id, likhet, terskel_brukt, apen"
        "  FROM m55_varslene(%s,%s) ORDER BY varseltype",
        (tenant, bare_apne)).fetchall()
    c.rollback()
    return rader


# --------------------------------------------------------------------
# §0: hver invariant i `m55-v1` har minst én port.
# --------------------------------------------------------------------

def test_grensen_er_registrert_og_hver_invariant_har_en_port():
    """§0-regelen, målt."""
    from manifestskjema import KRAVGRENSER
    inv = set(KRAVGRENSER["m55-v1"]["invarianter"])
    assert inv
    tekst = Path(__file__).read_text(encoding="utf-8")
    mangler = sorted(i for i in inv if i not in tekst)
    assert mangler == [], f"invarianter uten port: {mangler}"


# --------------------------------------------------------------------
# FRAVÆRENE. De måles i datamodellen og i importlistene, ikke i flyten.
# --------------------------------------------------------------------

def test_funnet_kan_ikke_uttrykkes_uten_bevaringskopi():
    """`funn_uten_bevaringskopi` — DEN MÅLER ET FRAVÆR.

    Hadde `kopi_id` vært nullbar, ville invarianten vært en regel noen
    måtte huske ved hver ny skrivevei — og et funn uten kopi ser like
    ferdig ut som ett med. En side som er endret eller borte den dagen
    saken tas opp, er ingen sak.

    MUTASJONEN SOM DREPER DENNE: gjør `kopi_id` nullbar, eller fjern
    fremmednøkkelen.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    funn = sql[sql.index("CREATE TABLE merkevarefunn"):
               sql.index("CREATE INDEX merkevarefunn_apne_idx")]
    assert "kopi_id UUID NOT NULL" in funn
    assert "merkevarefunn_kopi_fk" in funn
    assert "REFERENCES bevaringskopi" in funn
    # …og kopien bærer det som gjør den til et bevis.
    kopi = sql[sql.index("CREATE TABLE bevaringskopi"):
               sql.index("CREATE TABLE merkevarefunn")]
    for felt in ("kilde_url TEXT NOT NULL", "hentet_ts TIMESTAMPTZ",
                 "innhold_sha256 TEXT NOT NULL",
                 "innhold_bytes BIGINT NOT NULL",
                 "lagringsnokkel TEXT NOT NULL"):
        assert felt in kopi, felt


def test_ingen_kolonne_og_ingen_dor_sender_krav():
    """`modulen_sendte_krav` — FRAVÆRET er porten.

    Spesifikasjonen parkerte «automatisk varselbrev ved IP-brudd» med
    begrunnelsen at det er et juridisk krav mot en navngitt
    tredjepart. Modulen dokumenterer i stedet, og mennesket beslutter.
    """
    sql = _bare_kode(MIGRASJON, uten_strenger=True).lower()
    for forbudt in ("m55_send", "krav_sendt", "mottaker", "utboks",
                    "varselbrev", "klage"):
        assert forbudt not in sql, forbudt
    api = _bare_kode(MODULFILER[0], uten_strenger=True).lower()
    for forbudt in ("send_krav", "sendkrav", "m55_send", "mottaker",
                    "klage"):
        assert forbudt not in api, forbudt
    js = _bare_kode(FLATE, uten_strenger=True).lower()
    for forbudt in ("sendkrav", "send_krav", "sendklage", "mottaker"):
        assert forbudt not in js, forbudt
    # MODULENS ENESTE UTGANG STÅR DER, og den peker inn i M-37.
    assert "henvist_unntak_id" in _bare_kode(MIGRASJON)
    assert "m55_henvis_funn" in _bare_kode(MIGRASJON)


def test_modulen_henter_ingenting():
    """`modulen_hentet_eksternt` — M-48 fikk klyngens ene unntak.

    M-19s begrunnelse gjelder ikke her: modulen ville sendt VÅRE EGNE
    merkevarenavn, ikke kundedata. Grunnen er en annen — et
    overvåkingsoppslag mot tredjeparts annonseplattformer og
    domeneregistre hører hjemme i oppdragskontraktens `ekstern_lesing`
    med målautorisasjon, ikke i en modulfil.
    """
    for fil in MODULFILER:
        tre = ast.parse(fil.read_text(encoding="utf-8"))
        navn = set()
        for node in ast.walk(tre):
            if isinstance(node, ast.Import):
                navn.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                navn.add(node.module or "")
                navn.update(a.name for a in node.names)
        for forbudt in ("httpx", "requests", "socket", "urllib",
                        "ssrf", "foretaksregister"):
            assert not any(n == forbudt or n.endswith("." + forbudt)
                           for n in navn), f"{fil.name}: {forbudt}"
    js = _bare_kode(FLATE, uten_strenger=True)
    for forbudt in ("XMLHttpRequest", "WebSocket", "sendBeacon",
                    "EventSource"):
        assert forbudt not in js, forbudt


def test_modulen_signerer_ingen_attestasjon():
    """`modulen_signerte_attestasjon` — v1 attesterer ingenting."""
    for fil in list(MODULFILER) + [MIGRASJON]:
        kode = _bare_kode(fil, uten_strenger=True).lower()
        for forbudt in ("attester", "signer", "attestasjon"):
            assert forbudt not in kode, f"{fil.name}: {forbudt}"


def test_forvekslingsterskelen_er_ikke_hardkodet():
    """`forvekslingsterskel_hardkodet` — TERSKELEN ER TENANTENS.

    Hvor likt noe må være før det er forveksling er en forretnings- og
    juridisk vurdering: et varemerke i en nisje tåler langt mindre
    likhet enn et generisk ord gjør.

    DETTE ER PORTEN MOT DEN VENNLIGE FEILEN: et standardtall i koden
    ville vært behjelpelig og ville brutt invarianten. Verdien finnes
    ETT sted — `merkevarekrav.forvekslingsterskel` med sin DEFAULT —
    og verken API-et, sveipen eller flaten bærer et tall.
    """
    sql = _bare_kode(MIGRASJON)
    assert "forvekslingsterskel INT NOT NULL DEFAULT 80" in sql
    # …og DEFAULT-en står i TABELLEN, ikke i en dør: `m55_vurder_funn`
    # NEKTER uten en rad, den faller ikke tilbake på et tall.
    dor = sql[sql.index("CREATE FUNCTION m55_vurder_funn"):
              sql.index("REVOKE ALL ON FUNCTION m55_vurder_funn")]
    assert "v_terskel IS NULL THEN" in dor
    assert "RAISE EXCEPTION" in dor
    assert "coalesce(v_terskel" not in dor
    for fil in MODULFILER:
        kode = _bare_kode(fil, uten_strenger=True)
        assert not re.search(r"terskel\s*=\s*\d", kode), fil.name
    js = _bare_kode(FLATE, uten_strenger=True)
    assert not re.search(r"terskel\w*\s*[:=]\s*\d", js)
    # FLATEN LESER TERSKELEN FRA SVARET, og lar feltet stå tomt uten.
    assert "krav ? String(krav.forvekslingsterskel)" in FLATE.read_text(
        encoding="utf-8")


# --------------------------------------------------------------------
# DOMMENE, MÅLT MOT BASEN.
# --------------------------------------------------------------------

@pg
def test_vurdering_uten_terskel_nektes(miljo):
    """`forvekslingsterskel_hardkodet`, sett fra døra.

    Døra kunne vært behjelpelig og brukt 80. Da ville modulen tatt en
    juridisk vurdering på tenantens vegne, og ingen ville sett at den
    gjorde det.

    MUTASJONEN SOM DREPER DENNE: gi `v_terskel` en fallback.
    """
    tenant = _tenantnavn("uterskel")
    with _rt() as c:
        mid = _merke(c, tenant)
        kid = _kopi(c, tenant)
        fid = _funn(c, tenant, mid, kid)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT * FROM m55_vurder_funn(%s,%s,%s,%s)",
                      (tenant, fid, uuid.uuid4(), "u-test"))
        assert "forvekslingsterskel" in str(e.value)
        c.rollback()


@pg
def test_vurderingen_er_deterministisk_og_baerer_sitt_grunnlag(miljo):
    """`forvekslingsvurdering_uten_grunnlag`.

    ET TALL ALENE ER EN MENING. Raden bærer grunnlaget OG de to
    tekstene som ble sammenlignet, så hvem som helst kan regne etter og
    få nøyaktig samme svar.

    DETERMINISMEN MÅLES: `m55_likhet` kalt direkte gir samme tall som
    vurderingen lagret, og to kall gir samme svar.

    MUTASJONEN SOM DREPER DENNE: la `m55_grunnlag` returnere `{}`,
    eller slutt å snapshote inndataene.
    """
    tenant = _tenantnavn("determ")
    with _rt() as c:
        _krav(c, tenant, terskel=80)
        mid = _merke(c, tenant, navn="Disponit")
        kid = _kopi(c, tenant)
        fid = _funn(c, tenant, mid, kid, observert="Dispunit")
        likhet, terskel, over, grunnlag, kravv, alg = _vurder(
            c, tenant, fid)
        assert (likhet, terskel, over) == (87, 80, True)
        assert grunnlag == ["redigeringsavstand"]
        assert (kravv, alg) == (1, "lev-1")

        _sett_kontekst(c, tenant)
        # SAMME INN GIR SAMME UT — to ganger, og likt det som ble lagret.
        for _ in range(2):
            assert c.execute("SELECT m55_likhet('Disponit','Dispunit')"
                             ).fetchone()[0] == likhet
        rad = c.execute(
            "SELECT merkenavn_ved_vurdering, observert_ved_vurdering,"
            "       likhet, terskel_brukt, over_terskel"
            "  FROM m55_vurderingene(%s,%s)", (tenant, fid)).fetchone()
        assert rad[0] == "Disponit" and rad[1] == "Dispunit"
        # …og dommen er REGNET, ikke skrevet: kolonnen er generert.
        assert rad[4] == (rad[2] >= rad[3])
        c.rollback()


@pg
def test_grunnlaget_kan_ikke_vaere_tomt(miljo):
    """CHECK-en, målt direkte som eieren.

    En vurdering uten grunnlag er et tall ingen kan etterprøve. Døra
    fyller alltid inn `redigeringsavstand`; CHECK-en er gjerdet under
    den.
    """
    tenant = _tenantnavn("tomtgrunnlag")
    with _rt() as c:
        _krav(c, tenant)
        mid = _merke(c, tenant)
        kid = _kopi(c, tenant)
        fid = _funn(c, tenant, mid, kid)
        c.rollback()
    from db.pg import koble
    with koble(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        m.execute("SET LOCAL ROLE disponit_merkevare_eier")
        with pytest.raises(psycopg.errors.CheckViolation) as e:
            m.execute(
                "INSERT INTO forvekslingsvurdering (tenant,"
                " vurdering_id, funn_id, merkenavn_ved_vurdering,"
                " observert_ved_vurdering, likhet, terskel_brukt,"
                " kravversjon, grunnlag, algoritmeversjon,"
                " vurdert_av) VALUES"
                " (%s,%s,%s,'a','b',50,80,1,'{}','lev-1','u')",
                (tenant, uuid.uuid4(), fid))
        assert "grunnlag_finnes" in str(e.value)
        m.rollback()


@pg
def test_likheten_er_ren_avstand_uten_gjetning(miljo):
    """DETERMINISMEN, PRØVD PÅ KJENTE PAR.

    Normaliseringen senker bokstavstørrelse og slår sammen mellomrom.
    Den translittererer ikke og gjetter ikke på stavemåter — en
    normalisering som GJETTER er en match i forkledning, og her ville
    gjetningen stått mellom en navngitt part og en anklage.

    TO TOMME STRENGER ER IKKE 100 % LIKE. De er ingenting, og
    ingenting kan ikke forveksles med noe.
    """
    with _rt() as c:
        _sett_kontekst(c, TENANT)
        f = lambda a, b: c.execute(
            "SELECT m55_likhet(%s,%s)", (a, b)).fetchone()[0]
        assert f("Disponit", "Disponit") == 100
        assert f("Disponit", "  disponit  ") == 100      # normalisert
        assert f("Disponit", "Dispunit") == 87           # ett bytte
        assert f("Disponit", "Disponit AS") == 72        # suffiks
        assert f("Disponit", "Kaffekopp") == 0
        assert f("", "Disponit") == 0
        assert f("", "") == 0
        assert f(None, "Disponit") == 0
        c.rollback()


@pg
def test_lukking_over_terskel_krever_henvisning(miljo):
    """MODULEN HAR ÉN UTGANG, OG DEN KAN IKKE LUKKES FORBI.

    Samme figur som M-49s bekreftede treff (117), M-46s udekkede
    absolutte krav (118) og M-51s takfunn (119). Her er grunnen den
    skarpeste: kunne funnet lukkes uten henvisning, ville modulens
    eneste virkning vært viskbar.

    ET UVURDERT FUNN KAN LUKKES. «Vi så på det, det var ingenting» er
    et lovlig svar.

    MUTASJONEN SOM DREPER DENNE: fjern `v_over`-sjekken i
    `m55_lukk_funn`.
    """
    tenant = _tenantnavn("lukk")
    with _rt() as c:
        _krav(c, tenant, terskel=80)
        mid = _merke(c, tenant)
        kid = _kopi(c, tenant)
        fid = _funn(c, tenant, mid, kid, observert="Dispunit")
        _vurder(c, tenant, fid)

        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT m55_lukk_funn(%s,%s,%s,%s)",
                      (tenant, fid, "ikke noe farlig", "u-test"))
        # BESKJEDEN BÆRER BEGGE TALLENE: «over terskel» uten å si hvor
        # mye er en beskjed man ikke kan handle på (119s lærdom).
        assert "87" in str(e.value) and "80" in str(e.value)
        c.rollback()

        # ET UVURDERT FUNN KAN LUKKES.
        kid2 = _kopi(c, tenant)
        fid2 = _funn(c, tenant, mid, kid2, observert="Kaffe og krus")
        _sett_kontekst(c, tenant)
        c.execute("SELECT m55_lukk_funn(%s,%s,%s,%s)",
                  (tenant, fid2, "sett paa, ingenting", "u-test"))
        c.commit()

        # …OG ETTER HENVISNING KAN DET FØRSTE LUKKES.
        _sett_kontekst(c, tenant)
        c.execute("SELECT m55_henvis_funn(%s,%s,%s,%s)",
                  (tenant, fid, uuid.uuid4(), "u-test"))
        c.execute("SELECT m55_lukk_funn(%s,%s,%s,%s)",
                  (tenant, fid, "henvist, advokat vurderer", "u-test"))
        c.commit()
        _sett_kontekst(c, tenant)
        rader = c.execute(
            "SELECT lukket_ts IS NOT NULL, henvist_unntak_id IS NOT NULL"
            "  FROM m55_funnene(%s,NULL,500)", (tenant,)).fetchall()
        assert sorted(rader) == [(True, False), (True, True)]
        c.rollback()


@pg
def test_henvisningen_kan_settes_en_gang(miljo):
    """En ny henvisning ville SKJULT den første, og hvem som sendte hva
    til unntakskøen når er hele sporet ut av modulen."""
    tenant = _tenantnavn("henvis")
    with _rt() as c:
        _krav(c, tenant)
        mid = _merke(c, tenant)
        kid = _kopi(c, tenant)
        fid = _funn(c, tenant, mid, kid)
        _sett_kontekst(c, tenant)
        c.execute("SELECT m55_henvis_funn(%s,%s,%s,%s)",
                  (tenant, fid, uuid.uuid4(), "u-test"))
        c.commit()
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute("SELECT m55_henvis_funn(%s,%s,%s,%s)",
                      (tenant, fid, uuid.uuid4(), "u-test"))
        c.rollback()


@pg
def test_beviset_er_frosset(miljo):
    """`merkevarefunn_overskrevet` — OG DEN ER SKARPERE HER ENN I DE
    FIRE ANDRE MODULENE I KLYNGEN.

    Et endret bevis er ikke et svakere bevis: det er et bevis som ikke
    lenger beviser noe. `bevaringskopi` og `forvekslingsvurdering` har
    ikke UPDATE i det hele tatt — det er en RETTIGHET som ikke finnes,
    ikke en vakt som kan gjøre en feil. `merkevarefunn` beholder
    UPDATE fordi henvisning og lukking må kunne settes, og radvakten
    lukker åpningen fra den andre siden.

    MUTASJONEN SOM DREPER DENNE: gi eieren UPDATE på kopien, eller
    fjern en kolonne fra radvaktens liste.
    """
    tenant = _tenantnavn("frosset")
    with _rt() as c:
        _krav(c, tenant)
        mid = _merke(c, tenant)
        kid = _kopi(c, tenant)
        fid = _funn(c, tenant, mid, kid)
        _vurder(c, tenant, fid)
        c.rollback()

    from db.pg import koble
    # KOPIEN OG VURDERINGEN: ingen UPDATE i det hele tatt.
    for tabell, nokkel, verdi in (
            ("bevaringskopi", "kopi_id", kid),
            ("forvekslingsvurdering", "funn_id", fid)):
        # EGEN TILKOBLING PER TABELL: `rollback()` tilbakestiller
        # `SET ROLE`, så en løkke på én tilkobling ville kjørt neste
        # runde som eieren av tabellen (klynge 6s målte lærdom).
        with koble(MIGRATOR_DSN) as m:
            _sett_kontekst(m, tenant)
            m.execute("SET LOCAL ROLE disponit_merkevare_eier")
            with pytest.raises(
                    psycopg.errors.InsufficientPrivilege) as e:
                m.execute(f"UPDATE {tabell} SET tenant = tenant"
                          f" WHERE {nokkel} = %s", (verdi,))
            assert "permission denied" in str(e.value).lower()
            m.rollback()

    # FUNNET: bevisdelen er frosset av radvakten.
    with koble(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        m.execute("SET LOCAL ROLE disponit_merkevare_eier")
        for kolonne, ny in (("observert_navn", "'noe annet'"),
                            ("kopi_id", "gen_random_uuid()"),
                            ("kontekst", "'omskrevet'"),
                            ("registrert_av", "'noen andre'")):
            with pytest.raises(
                    psycopg.errors.InsufficientPrivilege) as e:
                m.execute(f"UPDATE merkevarefunn SET {kolonne} = {ny}"
                          " WHERE funn_id = %s", (fid,))
            assert "frosset" in str(e.value)
            m.execute("ROLLBACK; BEGIN")
            m.execute("SET LOCAL ROLE disponit_merkevare_eier")
            _sett_kontekst(m, tenant)
        m.rollback()

    # …OG SLETTING ER ALDRI LOVLIG.
    for tabell in EGNE:
        with koble(MIGRATOR_DSN) as m:
            _sett_kontekst(m, tenant)
            m.execute("SET LOCAL ROLE disponit_merkevare_eier")
            with pytest.raises(
                    psycopg.errors.InsufficientPrivilege):
                m.execute(f"DELETE FROM {tabell}")
            m.rollback()


@pg
def test_et_lukket_funn_kan_ikke_vurderes_paa_nytt(miljo):
    """En ny vurdering hører til et nytt funn, ikke til et som alt er
    gjennomgått. Låsen tas FØR tilstanden leses: et `m55_lukk_funn`
    som committer mens vi venter er ellers usynlig."""
    tenant = _tenantnavn("lukketvurder")
    with _rt() as c:
        _krav(c, tenant)
        mid = _merke(c, tenant)
        kid = _kopi(c, tenant)
        fid = _funn(c, tenant, mid, kid, observert="Kaffe og krus")
        _sett_kontekst(c, tenant)
        c.execute("SELECT m55_lukk_funn(%s,%s,%s,%s)",
                  (tenant, fid, "sett paa, ingenting", "u-test"))
        c.commit()
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT * FROM m55_vurder_funn(%s,%s,%s,%s)",
                      (tenant, fid, uuid.uuid4(), "u-test"))
        assert "lukket" in str(e.value)
        c.rollback()
    # LÅSEN LESES PÅ NYTT ETTER `FOR UPDATE` i alle tre dørene som
    # muterer et funn. Klynge 6 skrev denne feilen fem ganger.
    sql = _bare_kode(MIGRASJON)
    for dor in ("m55_vurder_funn", "m55_henvis_funn", "m55_lukk_funn"):
        kropp = sql[sql.index(f"CREATE FUNCTION {dor}"):
                    sql.index(f"REVOKE ALL ON FUNCTION {dor}")]
        laas = kropp.index("FOR UPDATE")
        assert "lukket_ts" in kropp[laas:], dor


@pg
def test_en_ny_vurdering_er_en_ny_rad(miljo):
    """DOM 4. Endres algoritmen eller terskelen, oppstår en ny
    vurdering VED SIDEN AV den gamle — aldri i stedet for.

    Samme regnestykke to ganger er derimot IKKE to vurderinger.
    """
    tenant = _tenantnavn("nyrad")
    with _rt() as c:
        _krav(c, tenant, terskel=80)
        mid = _merke(c, tenant)
        kid = _kopi(c, tenant)
        fid = _funn(c, tenant, mid, kid)
        _vurder(c, tenant, fid)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute("SELECT * FROM m55_vurder_funn(%s,%s,%s,%s)",
                      (tenant, fid, uuid.uuid4(), "u-test"))
        c.rollback()
        # NY TERSKEL → NY KRAVVERSJON → NY RAD, og den gamle består.
        _krav(c, tenant, terskel=95)
        likhet, terskel, over, _g, kravv, _a = _vurder(c, tenant, fid)
        assert (terskel, over, kravv) == (95, False, 2)
        _sett_kontekst(c, tenant)
        rader = c.execute(
            "SELECT terskel_brukt, over_terskel, kravversjon"
            "  FROM m55_vurderingene(%s,%s)", (tenant, fid)).fetchall()
        assert sorted(rader) == [(80, True, 1), (95, False, 2)]
        c.rollback()


@pg
def test_tenantisolasjon(miljo):
    """`tenantlekkasje_i_merkevareregister`.

    Hver tabell har RLS med FORCE, og lesedørene krever tenantkontekst.
    Et merkevarefunn navngir en tredjepart; en lekkasje her er ikke en
    tellefeil, det er en anklage sendt til feil sted.
    """
    a, b = _tenantnavn("iso-a"), _tenantnavn("iso-b")
    with _rt() as c:
        for t in (a, b):
            _krav(c, t)
            mid = _merke(c, t, navn=f"Merke-{t[-4:]}")
            kid = _kopi(c, t)
            _funn(c, t, mid, kid)
        _sett_kontekst(c, a)
        navn = [r[1] for r in c.execute(
            "SELECT * FROM m55_merkene(%s,500)", (a,)).fetchall()]
        assert len(navn) == 1 and navn[0].endswith(a[-4:])
        # …og døra nekter å svare på en ANNEN tenant enn konteksten.
        with pytest.raises(psycopg.Error):
            c.execute("SELECT * FROM m55_merkene(%s,500)", (b,))
        c.rollback()
    # RLS PÅ ALLE SEKS, MED FORCE.
    sql = _bare_kode(MIGRASJON)
    for tabell in EGNE:
        assert f"'{tabell}'" in sql, tabell
    assert "ENABLE ROW LEVEL' \n" not in sql
    assert "FORCE ROW LEVEL" in sql


@pg
def test_kjoretidsrollen_har_ingen_tabellrettigheter(miljo):
    """SP-7. Kjøretidsrollen har dørene, aldri tabellene."""
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
    """Sveipen er kryss-tenant. Runtime har ikke EXECUTE på den."""
    with _rt() as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM m55_sveip_merkevare(10)")
        c.rollback()


# --------------------------------------------------------------------
# SVEIPEN.
# --------------------------------------------------------------------

@pg
def test_sveipen_finner_det_ingen_har_sett_paa(miljo):
    """NATTENS ENESTE JOBB.

    Sveipen VURDERER IKKE og HENVISER IKKE. Den melder tilstander et
    menneske må ta stilling til — og `forveksling_ikke_henvist` er den
    viktigste av dem.
    """
    tenant = _tenantnavn("sveip")
    with _rt() as c:
        _krav(c, tenant, terskel=80)
        mid = _merke(c, tenant, navn="Disponit")
        _merke(c, tenant, navn="Kaffekopp", art="produktnavn",
               nummer=None, foerer=None, klasser=())
        kid = _kopi(c, tenant)
        fid = _funn(c, tenant, mid, kid, observert="Dispunit")
        _vurder(c, tenant, fid)
        kid2 = _kopi(c, tenant)
        _funn(c, tenant, mid, kid2, observert="Kaffe og krus")
        c.rollback()

    with _sv() as v:
        tenanter, nye, oppdaterte, lukkede = _sveip(v)
        assert tenanter >= 1 and nye >= 3
        # …OG DEN ER IDEMPOTENT: andre kjøring skriver ingen nye.
        t2, nye2, oppd2, _l2 = _sveip(v)
        assert nye2 == 0 and oppd2 >= 3

    with _rt() as c:
        typer = {r[0] for r in _varsler(c, tenant)}
        assert "forveksling_ikke_henvist" in typer
        assert "funn_uten_vurdering" in typer
        assert "merkevare_uten_funn" in typer
        # LIKHETEN STÅR PÅ VARSELET, med terskelen.
        rad = [r for r in _varsler(c, tenant)
               if r[0] == "forveksling_ikke_henvist"][0]
        assert (rad[2], rad[3]) == (87, 80)


@pg
def test_forveksling_ikke_henvist_kan_ikke_lukkes_av_et_menneske(miljo):
    """MODULENS EGET VARSEL ER IKKE VISKBART.

    En forveksling over tenantens EGEN terskel som ingen har sett på er
    nøyaktig det modulen finnes for å vise.

    MEN SVEIPEN LUKKER DET, og bare den: når funnet er henvist, er
    tilstanden borte. Forskjellen er hele poenget — sveipen lukker det
    som ER løst; et menneske kan ikke lukke det som ikke er det.

    MUTASJONEN SOM DREPER DENNE: ta `forveksling_ikke_henvist` ut av
    nekten i `m55_lukk_varsel`.
    """
    tenant = _tenantnavn("uviskbar")
    with _rt() as c:
        _krav(c, tenant, terskel=80)
        mid = _merke(c, tenant)
        kid = _kopi(c, tenant)
        fid = _funn(c, tenant, mid, kid, observert="Dispunit")
        _vurder(c, tenant, fid)
        c.rollback()
    with _sv() as v:
        _sveip(v)

    with _rt() as c:
        _sett_kontekst(c, tenant)
        wid = c.execute(
            "SELECT varsel_id FROM m55_varslene(%s,true)"
            " WHERE varseltype = 'forveksling_ikke_henvist'",
            (tenant,)).fetchone()[0]
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT m55_lukk_varsel(%s,%s,%s,%s)",
                      (tenant, wid, "ikke viktig", "u-test"))
        assert "forveksling_ikke_henvist" in str(e.value)
        c.rollback()

        # HENVIS — og sveipen lukker varselet selv.
        _sett_kontekst(c, tenant)
        c.execute("SELECT m55_henvis_funn(%s,%s,%s,%s)",
                  (tenant, fid, uuid.uuid4(), "u-test"))
        c.commit()
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        apne = {r[0] for r in _varsler(c, tenant)}
        assert "forveksling_ikke_henvist" not in apne
        alle = _varsler(c, tenant, bare_apne=False)
        lukket = [r for r in alle
                  if r[0] == "forveksling_ikke_henvist"]
        assert lukket and lukket[0][4] is False


@pg
def test_sveipen_ser_alle_tenanter(miljo):
    """Kryss-tenant, med tenantlista MATERIALISERT før løkka (112s
    lærdom): en lat markør ville lest gjennom en RLS-kontekst som
    endres inne i løkken."""
    a, b = _tenantnavn("flere-a"), _tenantnavn("flere-b")
    with _rt() as c:
        for t in (a, b):
            _krav(c, t)
            mid = _merke(c, t, navn=f"Merke-{t[-4:]}")
            kid = _kopi(c, t)
            _funn(c, t, mid, kid)
        c.rollback()
    with _sv() as v:
        tenanter, nye, _o, _l = _sveip(v)
        assert tenanter >= 2 and nye >= 2
    with _rt() as c:
        for t in (a, b):
            assert _varsler(c, t), t
    sveip = _bare_kode(MIGRASJON)
    assert "array_agg(DISTINCT m.tenant" in sveip
    assert "FOREACH v_t IN ARRAY v_tenanter" in sveip


@pg
def test_evidenskjeden_baerer_hvert_steg(miljo):
    """Hver skrivedør skriver en revisjonslogglinje."""
    tenant = _tenantnavn("evidens")
    with _rt() as c:
        _krav(c, tenant)
        mid = _merke(c, tenant)
        kid = _kopi(c, tenant)
        fid = _funn(c, tenant, mid, kid)
        _vurder(c, tenant, fid)
        _sett_kontekst(c, tenant)
        c.execute("SELECT m55_henvis_funn(%s,%s,%s,%s)",
                  (tenant, fid, uuid.uuid4(), "u-test"))
        c.commit()
    from db.pg import koble
    with koble(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        handlinger = {r[0] for r in m.execute(
            "SELECT handling FROM revisjonslogg"
            " WHERE tenant=%s AND kilde='m55_merkevare'",
            (tenant,)).fetchall()}
        m.rollback()
    assert handlinger >= {"merkevarekrav_satt", "merkevare_registrert",
                          "bevaringskopi_registrert",
                          "merkevarefunn_registrert",
                          "forvekslingsvurdering_gjort",
                          "merkevarefunn_henvist"}


@pg
def test_bevis_fra_framtida_og_ikke_web_avvises(miljo):
    """Et bevis med et hull i seg er ikke et bevis."""
    tenant = _tenantnavn("darligbevis")
    with _rt() as c:
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute(
                "SELECT m55_registrer_bevaringskopi(%s,%s,%s,"
                "now() + interval '2 days',%s,%s,%s,%s,%s)",
                (tenant, uuid.uuid4(), "https://eksempel.no/x",
                 "b" * 64, 100, "text/html", "artefakt/x", "u-test"))
        assert "framtida" in str(e.value)
        c.rollback()
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.CheckViolation) as e:
            c.execute(
                "SELECT m55_registrer_bevaringskopi(%s,%s,%s,now(),"
                "%s,%s,%s,%s,%s)",
                (tenant, uuid.uuid4(), "file:///etc/passwd",
                 "c" * 64, 100, "text/html", "artefakt/y", "u-test"))
        assert "url_er_web" in str(e.value)
        c.rollback()


@pg
def test_terskeldoera_er_idempotent_paa_nokkelen(miljo):
    """119s lærdom, tatt med fra første linje her.

    `merkevarekrav` er en SINGLETON per tenant og har ingen id å utlede
    fra idempotensnøkkelen. Uten nøkkelen inne i døra ville et
    gjenspill bumpet `versjon` — og hver vurdering bærer
    `kravversjon`, så et fantomtall gjør «hvilken terskel gjaldt da»
    til et spørsmål ingen kan svare på.
    """
    tenant = _tenantnavn("idem")
    nokkel = secrets.token_hex(8)
    with _rt() as c:
        v1 = _krav(c, tenant, terskel=80, nokkel=nokkel)
        assert _krav(c, tenant, terskel=80, nokkel=nokkel) == v1
        with pytest.raises(psycopg.errors.UniqueViolation):
            _krav(c, tenant, terskel=90, nokkel=nokkel)
        c.rollback()
        assert _krav(c, tenant, terskel=90) == v1 + 1
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT * FROM m55_kravene(%s)",
                         (tenant,)).fetchone()[0] == 90
        c.rollback()


# --------------------------------------------------------------------
# API, FLATE OG DRIFT.
# --------------------------------------------------------------------

def test_rutene_er_registrert_med_riktig_scope():
    """LESING `okonomi:read`, SKRIVING `bestilling:opprett`."""
    from api.app import RUTESCOPE
    forventet = {
        ("GET", "/v1/merkevare"): "okonomi:read",
        ("GET", "/v1/merkevare/funn"): "okonomi:read",
        ("GET", "/v1/merkevare/bevaringskopier"): "okonomi:read",
        ("GET", "/v1/merkevare/varsler"): "okonomi:read",
        ("GET", "/v1/merkevare/funn/{funn_id:uuid}/vurderinger"):
            "okonomi:read",
        ("GET", "/v1/merkevare/{merkevare_id:uuid}/funn"):
            "okonomi:read",
        ("POST", "/v1/merkevare/krav"): "bestilling:opprett",
        ("POST", "/v1/merkevare/merke"): "bestilling:opprett",
        ("POST", "/v1/merkevare/bevaringskopi"): "bestilling:opprett",
        ("POST", "/v1/merkevare/funn"): "bestilling:opprett",
        ("POST", "/v1/merkevare/funn/{funn_id:uuid}/vurder"):
            "bestilling:opprett",
        ("POST", "/v1/merkevare/funn/{funn_id:uuid}/henvis"):
            "bestilling:opprett",
        ("POST", "/v1/merkevare/funn/{funn_id:uuid}/lukk"):
            "bestilling:opprett",
        ("POST", "/v1/merkevare/varsel/{varsel_id:uuid}/lukk"):
            "bestilling:opprett",
        ("POST", "/v1/merkevare/{merkevare_id:uuid}/aktiv"):
            "bestilling:opprett",
    }
    for nokkel, scope in forventet.items():
        assert RUTESCOPE.get(nokkel) == scope, nokkel


def test_ingen_rute_sender_et_krav():
    """`modulen_sendte_krav`, sett fra rutetabellen.

    Modulens ENESTE utgang er `/henvis`, og den fester en peker til en
    sak i M-37s unntakskø.
    """
    from api.app import RUTESCOPE
    stier = [sti for (_m, sti) in RUTESCOPE
             if sti.startswith("/v1/merkevare")]
    assert stier
    for sti in stier:
        for forbudt in ("send", "krav/send", "klage", "brev",
                        "varsel/send"):
            assert forbudt not in sti, sti
    assert any(sti.endswith("/henvis") for sti in stier)


def test_funnruten_krever_alltid_en_bevaringskopi():
    """API-et bygger aldri et funn uten `kopi_id`."""
    api = MODULFILER[0].read_text(encoding="utf-8")
    bygg = api[api.index("def registrer_funn_endepunkt"):
               api.index("def vurder_endepunkt")]
    assert '_kropp_uuid(kropp, "kopi_id", rid)' in bygg
    assert "m55_registrer_funn(" in bygg


def test_vurderingsruten_returnerer_dommen_ikke_bare_ok():
    """Den som vurderer skal se hva vurderingen SIER."""
    api = MODULFILER[0].read_text(encoding="utf-8")
    rute = api[api.index("def vurder_endepunkt"):
               api.index("def henvis_endepunkt")]
    for felt in ('"likhet"', '"terskel_brukt"', '"over_terskel"',
                 '"grunnlag"', '"algoritmeversjon"'):
        assert felt in rute, felt


def test_flaten_viser_aldri_likheten_alene():
    """Et tall uten terskelen det ble målt mot er en mening i
    tallform, og terskelen er tenantens egen."""
    js = FLATE.read_text(encoding="utf-8")
    assert '"ui.merkevare.likhet_over"' in js
    assert '"ui.merkevare.likhet_under"' in js
    assert "{terskel}" in js
    assert "grunnlagTekst" in js
    # …og dommen utledes av tallene naar flagget ikke er med
    # (CodeRabbit): `m55_varslene` sender ikke `over_terskel`, og
    # `undefined` lest som usant ville snudd beskjeden om nettopp den
    # forvekslingen modulen finnes for aa vise.
    assert "rad.likhet >= rad.terskel_brukt" in js


def test_flaten_sier_hva_modulen_ikke_gjor():
    js = FLATE.read_text(encoding="utf-8")
    assert 't("ui.merkevare.oversikt.hvorfor")' in js
    from json import loads
    nb = loads((ROT / "locales" / "nb.json").read_text(
        encoding="utf-8"))
    assert "aldri krav eller klager" in nb[
        "ui.merkevare.oversikt.hvorfor"]


def test_flaten_lenker_ikke_til_den_pastatte_krenkeren():
    """URL-en vises som TEKST, aldri som lenke.

    En klikkbar lenke til den påståtte krenkerens side ville vært en
    utgående forespørsel flaten inviterer til, og modulen gjør ingen.
    """
    js = _bare_kode(FLATE, uten_strenger=True)
    assert 'el("a"' not in js
    assert "href" not in js


def test_sveipen_leser_fire_felt_og_ikke_flere():
    """#358s lærdom."""
    sveip = MODULFILER[1].read_text(encoding="utf-8")
    assert "KONTRAKTFELT = 4" in sveip
    assert "rader[0][:KONTRAKTFELT]" in sveip


def test_kjoreskriptet_har_ingen_fallback_til_database_url():
    """Runtime-rollen har med vilje ikke EXECUTE på sveipen."""
    kjor = MODULFILER[2].read_text(encoding="utf-8")
    assert "DISPONIT_MERKEVARESVEIP_URL" in kjor
    assert "DATABASE_URL" not in _bare_kode(MODULFILER[2])


def test_manglende_dsn_teller_som_en_feilet_kjoring():
    """CodeRabbits funn på 118, tatt med fra første linje her.

    Uten dette teller en permanent feilkonfigurert sveip aldri opp mot
    alarmen: den avslutter med 2 hver natt, stille.
    """
    kjor = MODULFILER[2].read_text(encoding="utf-8")
    gren = kjor[kjor.index("if not dsn:"):
                kjor.index("tidligere = _les_feiltelling()")]
    assert "_les_feiltelling() + 1" in gren
    assert "_skriv_feiltelling(n)" in gren
    assert '"alarm"' in gren
    # …og en negativ teller er ikke en teller.
    assert "negativ feiltelling" in kjor
    # …og `int()` skal ikke få lage en av noe annet (CodeRabbit):
    # `int(True)` er 1 og `int(2.9)` er 2, så en oedelagt fil ville
    # gitt en teller som ser gyldig ut.
    assert "isinstance(raa, int)" in kjor
    assert "isinstance(raa, bool)" in kjor


def test_timeren_staar_bakerst_i_klyngestigen():
    """09:50 — klyngefundamentets siste plass."""
    sti_t = (ROT / "deploy" / "staging"
             / "disponit-merkevaresveip.timer")
    timer = sti_t.read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 09:50:00 UTC" in timer
    assert "Persistent=true" in timer
    sti_s = (ROT / "deploy" / "staging"
             / "disponit-merkevaresveip.service")
    tjeneste = sti_s.read_text(encoding="utf-8")
    assert ("LoadCredential=DISPONIT_MERKEVARESVEIP_URL:"
            "/etc/disponit/merkevaresveip/DISPONIT_MERKEVARESVEIP_URL"
            in tjeneste)
    # …og beskrivelsen navngir SIN EGEN jobb (arvefeilen fra 116–118,
    # der `sed` dro med seg adressesveipens Description).
    assert "merkevaresveip" in tjeneste.split("Description=")[1][:70]
    for arvet in ("adresser", "uavklarte treff", "udekkede krav",
                  "estimater uten grunnlag"):
        assert arvet not in tjeneste, arvet


def test_sveipen_staar_i_flaaterosteret():
    """En sveip som ikke er i rosteret er en sveip ingen savner."""
    from drift.sveipestatus import FLAATEN
    assert FLAATEN.get("merkevaresveip") == 30, FLAATEN


def test_ui_axe_dekning():
    """`ui_axe_alvorlige_brudd` — flaten er registrert der axe kjører."""
    app_js = (ROT / "platform" / "core" / "ui" / "static" / "js"
              / "app.js").read_text(encoding="utf-8")
    assert "merkevare: visMerkevare," in app_js
    sitekart = (ROT / "platform" / "core" / "ui" / "static" / "js"
                / "sitekart.js").read_text(encoding="utf-8")
    assert '{ nokkel: "merkevare", scope: "okonomi:read"' in sitekart
