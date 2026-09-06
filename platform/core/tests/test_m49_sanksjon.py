"""M-49 sanksjonskontroll v1 (117) — KONTROLLEN, IKKE BLOKKERINGEN.

Grensen `m49-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

DE TO SKARPESTE PORTENE ER `modulen_blokkerte_motpart` og
`modulen_avfeide_navnelikhet`, og de måler HVER SIN RETNING av samme
dom.

Spesifikasjonen vil at modulen skal blokkere fail-closed ved treff, og
samtidig at navnelikhet aldri avfeies automatisk. De to sammen betyr at
treffene blir mange og at ingen kan lukkes maskinelt. v1 blokkerer
derfor ikke — den tyngste grunnen er at det ikke finnes noe å blokkere
MED — og v1 avfeier heller ingenting: et treff lukkes bare av et
menneske med en begrunnelse.

Beslutningen, motargumentet og utløseren står i toppen av migrasjon
117. DATAMODELLEN ER FORMET ETTER UTLØSEREN: `matchtype` skiller
`eksakt_identifikator` fra `eksakt_navn` og `navnelikhet`, fordi det er
der grensen kommer til å gå den dagen blokkering skrus på.

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

SANKSJONSSVEIP_DSN = os.environ.get("DISPONIT_TEST_SANKSJONSSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "117_m49_sanksjonskontroll.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "sanksjon.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "sanksjon.py",
    ROT / "platform" / "drift" / "sanksjonssveip.py",
    ROT / "platform" / "drift" / "kjor_sanksjonssveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("sanksjonskrav", "sanksjonsliste", "sanksjonssubjekt",
        "sanksjonskontroll", "sanksjonstreff", "sanksjonsavklaring",
        "sanksjonsfunn")

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
    return koble(SANKSJONSSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m49-{merke}-{secrets.token_hex(4)}"


def _krav(c, tenant, *, terskel=85, gyldig=90, uavklart=3,
          ukontrollert=30, aktor="u-test"):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m49_sett_krav(%s,%s,%s,%s,%s,%s)",
                  (tenant, terskel, gyldig, uavklart, ukontrollert,
                   aktor)).fetchone()[0]
    c.commit()
    return v


def _liste(c, tenant, *, kilde="ofac", versjon=None, fra="2026-09-01",
           lid=None, antall=12000, aktor="u-test"):
    lid = lid or uuid.uuid4()
    versjon = versjon or ("v" + secrets.token_hex(4))
    _sett_kontekst(c, tenant)
    c.execute("SELECT m49_registrer_liste("
              "%s,%s,%s,%s,%s::date,%s,%s,%s)",
              (tenant, lid, kilde, versjon, fra,
               secrets.token_hex(32), antall, aktor))
    c.commit()
    return lid


def _subjekt(c, tenant, *, ref=None, navn="Mohammed Ali",
             subjekttype="person", land="NO", fodt="1970-01-01",
             ident=None, sid=None, aktor="u-test"):
    sid = sid or uuid.uuid4()
    ref = ref or ("K-" + secrets.token_hex(4))
    _sett_kontekst(c, tenant)
    c.execute("SELECT m49_registrer_subjekt("
              "%s,%s,%s,%s,%s,%s,%s::date,%s,%s)",
              (tenant, sid, ref, navn, subjekttype, land, fodt,
               ident, aktor))
    c.commit()
    return sid


def _treffrad(*, matchtype="navnelikhet", matchfelt=("navn",),
              likhet=92, listenavn="ALI, Mohamed",
              referanse="SDN-1", program=None, tid=None):
    return {"treff_id": str(tid or uuid.uuid4()),
            "matchtype": matchtype, "matchfelt": list(matchfelt),
            "likhet": likhet, "listenavn": listenavn,
            "liste_referanse": referanse, "liste_program": program}


def _kontroll(c, tenant, sid, lid, *, treff=(),
              felt=("navn", "land"), kid=None, aktor="u-test"):
    kid = kid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    n = c.execute("SELECT m49_registrer_kontroll("
                  "%s,%s,%s,%s,%s,%s::jsonb,%s)",
                  (tenant, kid, sid, lid, list(felt),
                   json.dumps(list(treff)), aktor)).fetchone()[0]
    c.commit()
    return kid, n


def _avklar(c, tenant, tid, *, konklusjon="ikke_samme_part",
            begrunnelse="Fodselsdato avviker med tolv aar",
            aid=None, aktor="u-test"):
    aid = aid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute("SELECT m49_avklar_treff(%s,%s,%s,%s,%s,%s)",
              (tenant, aid, tid, konklusjon, begrunnelse, aktor))
    c.commit()
    return aid


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m49_sveip_sanksjoner(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT funntype, over_grense, siste_matchtype, apen"
        "  FROM sanksjonsfunn WHERE tenant=%s ORDER BY funntype",
        (tenant,)).fetchall()
    m.rollback()
    return rader


# --------------------------------------------------------------------
# §0: hver invariant i `m49-v1` har minst én port.
# --------------------------------------------------------------------

def test_grensen_er_registrert_og_hver_invariant_har_en_port():
    """§0-regelen, målt.

    MUTASJONEN SOM DREPER DENNE: legg til en invariant i `m49-v1` uten
    å skrive porten.
    """
    from manifestskjema import KRAVGRENSER
    inv = set(KRAVGRENSER["m49-v1"]["invarianter"])
    assert inv
    tekst = Path(__file__).read_text(encoding="utf-8")
    mangler = sorted(i for i in inv if i not in tekst)
    assert mangler == [], f"invarianter uten port: {mangler}"


def test_ingen_kolonne_og_ingen_dor_blokkerer(miljo=None):
    """`modulen_blokkerte_motpart` — FRAVÆRET er porten.

    v1 blokkerer ikke, og den tyngste grunnen er at det ikke finnes noe
    å blokkere MED: et register stanser ingen handel. Hadde skjemaet
    hatt et flagg for det, ville fullmakten vært bygget allerede — og
    et flagg ingen leser er `alarm`-feltet fra 115 om igjen.

    MUTASJONEN SOM DREPER DENNE: legg til `blokkert` eller en
    `m49_blokker`-dør.
    """
    sql = _bare_kode(MIGRASJON).lower()
    for forbudt in ("blokkert", "m49_blokker", "sperret", "stanset",
                    "handelsstopp"):
        assert forbudt not in sql, forbudt
    api = _bare_kode(MODULFILER[0]).lower()
    for forbudt in ("blokker", "sperr"):
        assert forbudt not in api, forbudt
    js = _bare_kode(FLATE, uten_strenger=True).lower()
    assert "blokker" not in js


def test_ingen_maskinell_avfeiing_av_navnelikhet():
    """`modulen_avfeide_navnelikhet` — den andre retningen av dommen.

    Et treff lukkes BARE av `m49_avklar_treff`, med en aktør og en
    begrunnelse. Det finnes ingen batchdør, ingen «lukk alle under
    90 %», og sveipen har ingen avklaringsvei.

    MUTASJONEN SOM DREPER DENNE: legg til en masseavklaring, eller la
    sveipen skrive i `sanksjonsavklaring`.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    # ÉN dør skriver avklaringer, og den heter `m49_avklar_treff`.
    inserts = re.findall(r"INSERT INTO public\.sanksjonsavklaring",
                         sql)
    assert len(inserts) == 1, inserts
    sveip = sql[sql.index("CREATE FUNCTION m49_sveip_sanksjoner("):]
    assert "sanksjonsavklaring" in sveip      # den LESER dem
    assert "INSERT INTO public.sanksjonsavklaring" not in sveip
    assert "UPDATE public.sanksjonsavklaring" not in sveip
    # …og ingen batchrute i API-et eller flaten.
    api = _bare_kode(MODULFILER[0]).lower()
    for forbudt in ("avfei", "masseavklar", "lukk_alle", "bulk"):
        assert forbudt not in api, forbudt
    js = _bare_kode(FLATE, uten_strenger=True).lower()
    for forbudt in ("avfei", "masseavklar", "lukkalle"):
        assert forbudt not in js, forbudt


def test_modulen_henter_ingen_liste_selv():
    """`modulen_hentet_eksternt` — M-48 fikk klyngens ene unntak.

    En sanksjonsliste er noe helt annet enn et organisasjonsnummer:
    fila er stor, den oppdateres uforutsigbart, og en modul som hentet
    den automatisk ville tatt ansvaret for at NØYAKTIG den versjonen
    er den gjeldende.
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
                        "foretaksregister", "ssrf"):
            assert not any(n == forbudt or n.endswith("." + forbudt)
                           for n in navn), f"{fil.name}: {forbudt}"
    js = _bare_kode(FLATE, uten_strenger=True)
    for forbudt in ("fetch(", "XMLHttpRequest", "WebSocket",
                    "sendBeacon", "EventSource"):
        assert forbudt not in js, forbudt


def test_modulen_signerer_ingen_attestasjon():
    """`modulen_signerte_attestasjon` — v1 attesterer ingenting."""
    for fil in list(MODULFILER) + [MIGRASJON]:
        kode = _bare_kode(fil, uten_strenger=True).lower()
        for forbudt in ("attester", "signer", "attestasjon"):
            assert forbudt not in kode, f"{fil.name}: {forbudt}"


def test_matchterskelen_ligger_i_basen_ikke_i_koden():
    """`matchterskel_hardkodet` — terskelen er TENANTENS.

    Hvor lik en streng må være for å bli et treff er en
    risikoavveining: en bank vil ha lavere terskel enn en nettbutikk,
    og begge skal kunne begrunne sitt valg overfor et tilsyn.

    MUTASJONEN SOM DREPER DENNE: sett en standardterskel i Python.
    """
    sveip = _bare_kode(MODULFILER[1])
    for forbudt in ("TERSKEL", "MATCHTERSKEL", "GYLDIG_DOGN",
                    "UAVKLART"):
        assert forbudt not in sveip, forbudt
    api = MODULFILER[0].read_text(encoding="utf-8")
    for forbudt in ("matchterskel = 85", "STANDARD_TERSKEL",
                    "DEFAULT_TERSKEL"):
        assert forbudt not in api, forbudt
    # …og døra leser terskelen fra basen, ikke fra et argument.
    sql = MIGRASJON.read_text(encoding="utf-8")
    assert "SELECT k.matchterskel, k.versjon INTO v_terskel" in sql
    sig = sql[sql.index("CREATE FUNCTION m49_registrer_kontroll("):]
    sig = sig[:sig.index(")")]
    assert "terskel" not in sig, sig


# --------------------------------------------------------------------
# Databaseportene.
# --------------------------------------------------------------------

@pg
def test_eksakt_identifikator_krever_en_identifikator(miljo):
    """DEN SKARPESTE DATABASEPORTEN.

    `eksakt_identifikator` er den ENE klassen som en dag skal kunne
    blokkere handel maskinelt. Uten denne vakten kunne en klientfeil
    gjort en navnelikhet til et blokkeringsgrunnlag.

    MUTASJONEN SOM DREPER DENNE: fjern sjekken i døra.
    """
    tenant = _tenantnavn("eksakt")
    with _rt() as c:
        _krav(c, tenant)
        lid = _liste(c, tenant)
        uten = _subjekt(c, tenant, ident=None)
        med = _subjekt(c, tenant, navn="Testfirma AS",
                       subjekttype="foretak", fodt=None,
                       ident="923609016")
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _kontroll(c, tenant, uten, lid, treff=[_treffrad(
                matchtype="eksakt_identifikator",
                matchfelt=("identifikator",), likhet=100)])
        assert "ingen identifikator" in str(e.value)
        c.rollback()
        # …og MED identifikator går det.
        _kontroll(c, tenant, med, lid, treff=[_treffrad(
            matchtype="eksakt_identifikator",
            matchfelt=("identifikator",), likhet=100,
            listenavn="TESTFIRMA AS")])
        c.rollback()


@pg
def test_matchtypen_og_likheten_kan_ikke_si_hver_sin_ting(miljo):
    """Skillet mellom eksakt og navnelikhet kan ikke viskes ut.

    Var en `navnelikhet` 100 %, ville den vært `eksakt_navn` — og den
    dagen blokkering skrus på for eksakte treff, ville skillet vært
    borte.

    MUTASJONEN SOM DREPER DENNE: fjern en av de to CHECK-ene.
    """
    tenant = _tenantnavn("likhet")
    with _rt() as c:
        _krav(c, tenant)
        lid = _liste(c, tenant)
        sid = _subjekt(c, tenant, ident="123456789")
        # navnelikhet må være UNDER 100
        with pytest.raises(psycopg.errors.CheckViolation):
            _kontroll(c, tenant, sid, lid,
                      treff=[_treffrad(matchtype="navnelikhet",
                                       likhet=100)])
        c.rollback()
        # eksakt navn må være PRESIS 100
        with pytest.raises(psycopg.errors.CheckViolation):
            _kontroll(c, tenant, sid, lid,
                      treff=[_treffrad(matchtype="eksakt_navn",
                                       likhet=99)])
        c.rollback()
        # identifikatortreff må ha identifikatoren i matchfelt
        with pytest.raises(psycopg.errors.CheckViolation):
            _kontroll(c, tenant, sid, lid, treff=[_treffrad(
                matchtype="eksakt_identifikator",
                matchfelt=("navn",), likhet=100)])
        c.rollback()


@pg
def test_kontrollen_og_treffene_kan_ikke_si_hver_sin_ting(miljo):
    """`antall_treff` REGNES av døra, den sendes ikke inn.

    Tok kalleren begge deler hver for seg, kunne en kontroll påstå
    «ingen treff» mens treffradene sto der.
    """
    tenant = _tenantnavn("telling")
    with _rt() as c:
        _krav(c, tenant)
        lid = _liste(c, tenant)
        sid = _subjekt(c, tenant, ident="123456789")
        _kid, n = _kontroll(c, tenant, sid, lid,
                            treff=[_treffrad(), _treffrad(likhet=88)])
        assert n == 2
        _sett_kontekst(c, tenant)
        rad = c.execute(
            "SELECT utfall, antall_treff FROM m49_kontrollene(%s,%s)"
            " LIMIT 1", (tenant, sid)).fetchone()
        assert rad == ("treff", 2), rad
        c.rollback()
        # …og null treff gir `ingen_treff`, ikke `treff` med 0.
        _kid2, n2 = _kontroll(c, tenant, sid, lid, treff=[])
        assert n2 == 0
        _sett_kontekst(c, tenant)
        utfall = [r[0] for r in c.execute(
            "SELECT utfall FROM m49_kontrollene(%s,%s)",
            (tenant, sid)).fetchall()]
        assert "ingen_treff" in utfall, utfall
        c.rollback()


@pg
def test_kontroll_uten_matchgrunnlag_nektes(miljo):
    """`kontroll_uten_matchgrunnlag`.

    Uten terskelen OG hvilke felter som ble sammenlignet, kan ingen
    etterpå skille «lista var slik» fra «vi lette feil sted».
    """
    tenant = _tenantnavn("grunnlag")
    with _rt() as c:
        _krav(c, tenant)
        lid = _liste(c, tenant)
        sid = _subjekt(c, tenant, ident="123456789")
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _kontroll(c, tenant, sid, lid, felt=())
        assert "HVILKE felter" in str(e.value)
        c.rollback()
    # …og kolonnene er NOT NULL uten default.
    sql = MIGRASJON.read_text(encoding="utf-8")
    blokk = sql[sql.index("CREATE TABLE sanksjonskontroll"):
                sql.index("CREATE INDEX sanksjonskontroll_oppslag")]
    assert "matchterskel INT NOT NULL" in blokk
    assert "sammenlignede_felt TEXT[] NOT NULL" in blokk
    assert "cardinality(sammenlignede_felt) > 0" in blokk


@pg
def test_treff_uten_listeversjon_er_umulig(miljo):
    """`treff_uten_listeversjon` — «sto de på lista DEN DAGEN».

    Hver kontroll peker på NØYAKTIG én frosset listeversjon, og en
    liste som oppdateres på stedet ville slettet svaret.
    """
    tenant = _tenantnavn("listev")
    with _rt() as c:
        _krav(c, tenant)
        sid = _subjekt(c, tenant, ident="123456789")
        with pytest.raises(psycopg.errors.NoDataFound):
            _kontroll(c, tenant, sid, uuid.uuid4())
        c.rollback()
    # …og listetabellen er frosset for eieren.
    with psycopg.connect(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        m.execute("SET ROLE disponit_sanksjon_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            m.execute("UPDATE sanksjonsliste SET tenant=tenant"
                      " WHERE tenant=%s", (tenant,))
        m.rollback()


@pg
def test_en_avklaring_kan_ikke_skrives_om(miljo):
    """`sanksjonshistorikk_overskrevet` — MODULENS SKARPESTE GJERDE.

    «Hvem sa hva, når» er nøyaktig spørsmålet et tilsyn stiller etter
    et sanksjonsbrudd. En ny vurdering er en ny KONTROLL, ikke en
    retting av gårsdagens dom.

    TO GJERDER: døra gir den ærlige feilmeldingen, REVOKE stanser den
    som går utenom.
    """
    tenant = _tenantnavn("avklaring")
    with _rt() as c:
        _krav(c, tenant)
        lid = _liste(c, tenant)
        sid = _subjekt(c, tenant, ident="123456789")
        tid = uuid.uuid4()
        _kontroll(c, tenant, sid, lid, treff=[_treffrad(tid=tid)])
        _avklar(c, tenant, tid)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _avklar(c, tenant, tid, konklusjon="bekreftet_treff",
                    begrunnelse="Ombestemte meg helt og holdent")
        assert "alt avklart" in str(e.value)
        c.rollback()
    for tabell in ("sanksjonsavklaring", "sanksjonstreff",
                   "sanksjonskontroll"):
        # EGEN FORBINDELSE PER TABELL: `rollback()` tilbakestiller
        # `SET ROLE`, og runde to ville ellers kjørt som tabelleieren
        # (116s lærdom).
        with psycopg.connect(MIGRATOR_DSN) as m:
            _sett_kontekst(m, tenant)
            m.execute("SET ROLE disponit_sanksjon_eier")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                m.execute(f"UPDATE {tabell} SET tenant=tenant"
                          " WHERE tenant=%s", (tenant,))
            m.rollback()


@pg
def test_avklaring_krever_en_ekte_begrunnelse(miljo):
    """«ok» er ikke en begrunnelse for å slippe en part gjennom."""
    tenant = _tenantnavn("begrunn")
    with _rt() as c:
        _krav(c, tenant)
        lid = _liste(c, tenant)
        sid = _subjekt(c, tenant, ident="123456789")
        tid = uuid.uuid4()
        _kontroll(c, tenant, sid, lid, treff=[_treffrad(tid=tid)])
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _avklar(c, tenant, tid, begrunnelse="ok")
        assert "begrunnelse" in str(e.value)
        c.rollback()


@pg
def test_uavklart_eskalert_er_et_lovlig_svar(miljo):
    """DEN ÆRLIGE TREDJE KONKLUSJONEN.

    En saksbehandler som IKKE klarer å avgjøre skal kunne si det. En
    modul som bare tilbød ja og nei ville presset fram gjetninger og
    kalt dem avklaringer.

    MUTASJONEN SOM DREPER DENNE: fjern `uavklart_eskalert` fra settet.
    """
    tenant = _tenantnavn("eskalert")
    with _rt() as c:
        _krav(c, tenant)
        lid = _liste(c, tenant)
        sid = _subjekt(c, tenant, ident="123456789")
        tid = uuid.uuid4()
        _kontroll(c, tenant, sid, lid, treff=[_treffrad(tid=tid)])
        _avklar(c, tenant, tid, konklusjon="uavklart_eskalert",
                begrunnelse="Klarer ikke avgjore; sendt til jurist")
        _sett_kontekst(c, tenant)
        rad = c.execute(
            "SELECT konklusjon FROM m49_treffene(%s,%s)",
            (tenant, sid)).fetchone()
        assert rad[0] == "uavklart_eskalert"
        c.rollback()


@pg
def test_et_bekreftet_treff_kan_ikke_lukkes_bort(miljo):
    """MODULENS SKARPESTE NEKT.

    `bekreftet_treff` betyr at et menneske har sagt at parten ER
    sanksjonert. Kunne det funnet lukkes med et notat, ville modulen
    tilbudt en knapp for å gjøre den observasjonen borte — og den
    knappen er farligere enn manglende blokkering, fordi den SER UT
    som saksbehandling.

    MUTASJONEN SOM DREPER DENNE: fjern nektet i `m49_lukk_funn`.
    """
    tenant = _tenantnavn("bekreftet")
    with _rt() as c:
        _krav(c, tenant)
        lid = _liste(c, tenant)
        sid = _subjekt(c, tenant, ident="123456789")
        tid = uuid.uuid4()
        _kontroll(c, tenant, sid, lid, treff=[_treffrad(tid=tid)])
        _avklar(c, tenant, tid, konklusjon="bekreftet_treff",
                begrunnelse="Samme orgnr og samme adresse som lista")
        c.rollback()
    with _sv() as v:
        _sveip(v)
    with psycopg.connect(MIGRATOR_DSN) as m:
        typer = [r[0] for r in _funn(m, tenant)]
        assert "bekreftet_treff" in typer, typer
    with _rt() as c:
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as e:
            c.execute("SELECT m49_lukk_funn(%s,%s,%s,%s,%s)",
                      (tenant, sid, "bekreftet_treff",
                       "vil gjerne bli ferdig", "u-test"))
        assert "kan ikke lukkes bort" in str(e.value)
        c.rollback()


@pg
def test_bekreftet_funn_lukkes_naar_en_ny_kontroll_er_ren(miljo):
    """M-39s FELLE, unngått (113).

    Et bekreftet treff fra i fjor, der en ny kontroll mot en ny
    listeversjon ikke lenger gir treffet, er LØST — og funnet skal
    lukkes. Uten bindingen til siste kontroll ville funnet stått for
    alltid, og et varsel som aldri lukkes blir et varsel ingen leser.

    MUTASJONEN SOM DREPER DENNE: fjern `JOIN siste` fra
    `bekreftet`-CTE-en i sveipen.
    """
    tenant = _tenantnavn("renkontroll")
    with _rt() as c:
        _krav(c, tenant)
        lid = _liste(c, tenant, versjon="gammel")
        sid = _subjekt(c, tenant, ident="123456789")
        tid = uuid.uuid4()
        _kontroll(c, tenant, sid, lid, treff=[_treffrad(tid=tid)])
        _avklar(c, tenant, tid, konklusjon="bekreftet_treff",
                begrunnelse="Samme orgnr og samme adresse som lista")
        c.rollback()
    with _sv() as v:
        _sveip(v)
    with psycopg.connect(MIGRATOR_DSN) as m:
        assert "bekreftet_treff" in [r[0] for r in _funn(m, tenant)]
    # NY listeversjon, ny kontroll UTEN treff.
    with _rt() as c:
        ny = _liste(c, tenant, versjon="ny", fra="2026-09-02")
        _kontroll(c, tenant, sid, ny, treff=[])
        c.rollback()
    with _sv() as v:
        _sveip(v)
    with psycopg.connect(MIGRATOR_DSN) as m:
        rader = {r[0]: r[3] for r in _funn(m, tenant)}
        assert rader.get("bekreftet_treff") is False, rader


@pg
def test_tenantisolasjon(miljo):
    """`tenantlekkasje_i_sanksjonsregister` — RLS + krev_tenantkontekst."""
    a, b = _tenantnavn("iso-a"), _tenantnavn("iso-b")
    with _rt() as c:
        _krav(c, a)
        _krav(c, b)
        _subjekt(c, a, navn="A Person", ident="111111111")
        _subjekt(c, b, navn="B Person", ident="222222222")
        _sett_kontekst(c, a)
        with pytest.raises(psycopg.Error):
            c.execute("SELECT * FROM m49_subjektene(%s,%s)", (b, 10))
        c.rollback()
        _sett_kontekst(c, a)
        navn = [r[2] for r in c.execute(
            "SELECT * FROM m49_subjektene(%s,%s)", (a, 50)).fetchall()]
        assert navn == ["A Person"], navn
        c.rollback()
    with psycopg.connect(MIGRATOR_DSN) as m:
        for tabell in EGNE:
            rad = m.execute(
                "SELECT relrowsecurity, relforcerowsecurity"
                "  FROM pg_class WHERE relname=%s", (tabell,)).fetchone()
            assert rad == (True, True), (tabell, rad)
        m.rollback()


@pg
def test_kjoretidsrollen_har_ingen_tabellrettigheter(miljo):
    """SP-7, målt med `has_table_privilege` (blind for PUBLIC ellers)."""
    with psycopg.connect(MIGRATOR_DSN) as m:
        for tabell in EGNE:
            for rett in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                har = m.execute(
                    "SELECT has_table_privilege('disponit', %s, %s)",
                    (tabell, rett)).fetchone()[0]
                assert har is False, (tabell, rett)
        m.rollback()


@pg
def test_sveipen_er_ikke_kjoretidsrollens(miljo):
    """Sveipen har sin EGEN rolle med nøyaktig én EXECUTE."""
    with psycopg.connect(MIGRATOR_DSN) as m:
        assert m.execute(
            "SELECT has_function_privilege('disponit',"
            " 'm49_sveip_sanksjoner(int)', 'EXECUTE')").fetchone()[0] \
            is False
        assert m.execute(
            "SELECT has_function_privilege('disponit_sanksjonssveip',"
            " 'm49_sveip_sanksjoner(int)', 'EXECUTE')").fetchone()[0] \
            is True
        for tabell in EGNE:
            assert m.execute(
                "SELECT has_table_privilege('disponit_sanksjonssveip',"
                " %s, 'SELECT')", (tabell,)).fetchone()[0] is False
        m.rollback()


@pg
def test_sveipen_ser_alle_tenanter(miljo):
    """DEN LATE MARKØREN, målt (112s lærdom, gjentatt i 116).

    MUTASJONEN SOM DREPER DENNE: bytt arrayet mot en `FOR ... IN
    SELECT`-løkke.
    """
    a, b, c_ = (_tenantnavn("sv-a"), _tenantnavn("sv-b"),
                _tenantnavn("sv-c"))
    with _rt() as c:
        for t in (a, b, c_):
            _subjekt(c, t, ident="999999999")   # ingen krav
        c.rollback()
    with _sv() as v:
        rad = _sveip(v)
    assert rad[0] >= 3, rad
    with psycopg.connect(MIGRATOR_DSN) as m:
        for t in (a, b, c_):
            typer = [r[0] for r in _funn(m, t)]
            assert "ingen_krav" in typer, (t, typer)


@pg
def test_ingen_liste_er_et_eget_funn(miljo):
    """Et register som ser rolig ut fordi ingen har lastet lista, er
    farligere enn et som viser funn."""
    tenant = _tenantnavn("uliste")
    with _rt() as c:
        _krav(c, tenant)
        _subjekt(c, tenant, ident="999999999")
        c.rollback()
    with _sv() as v:
        _sveip(v)
    with psycopg.connect(MIGRATOR_DSN) as m:
        typer = [r[0] for r in _funn(m, tenant)]
        assert "ingen_liste" in typer, typer


@pg
def test_kontroll_mot_gammel_liste_blir_et_funn(miljo):
    """En fersk kontroll mot fjorårets liste er ikke en fersk kontroll."""
    tenant = _tenantnavn("gammel")
    with _rt() as c:
        _krav(c, tenant)
        gammel = _liste(c, tenant, versjon="g1", fra="2026-08-01")
        sid = _subjekt(c, tenant, ident="123456789")
        _kontroll(c, tenant, sid, gammel, treff=[])
        _liste(c, tenant, versjon="n1", fra="2026-09-02")
        c.rollback()
    with _sv() as v:
        _sveip(v)
    with psycopg.connect(MIGRATOR_DSN) as m:
        typer = [r[0] for r in _funn(m, tenant)]
        assert "kontroll_mot_gammel_liste" in typer, typer


@pg
def test_evidenskjeden_baerer_hver_kontroll_og_avklaring(miljo):
    """Et tilsyn spør etter kontrollen i evidenskjeden."""
    tenant = _tenantnavn("evidens")
    with _rt() as c:
        _krav(c, tenant)
        lid = _liste(c, tenant)
        sid = _subjekt(c, tenant, ident="123456789")
        tid = uuid.uuid4()
        _kontroll(c, tenant, sid, lid, treff=[_treffrad(tid=tid)])
        _avklar(c, tenant, tid)
        c.rollback()
    with psycopg.connect(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        handlinger = {r[0] for r in m.execute(
            "SELECT handling FROM revisjonslogg"
            " WHERE tenant=%s AND kilde='m49_sanksjon'",
            (tenant,)).fetchall()}
        for h in ("sanksjonsliste_lastet", "sanksjonssubjekt_registrert",
                  "sanksjonskontroll_utfort", "treff_avklart"):
            assert h in handlinger, (h, handlinger)
        m.rollback()


@pg
def test_normaliseringen_gjetter_ikke(miljo):
    """En normalisering som GJETTER er en match i forkledning.

    Den slår sammen mellomrom og senker bokstavstørrelse — og gjør
    ingenting annet. Ingen translitterering, ingen navnebytte.

    MUTASJONEN SOM DREPER DENNE: legg til `unaccent` eller en
    navnetabell i `m49_normaliser`.
    """
    with psycopg.connect(MIGRATOR_DSN) as m:
        assert m.execute(
            "SELECT m49_normaliser('  Mohammed   ALI ')"
        ).fetchone()[0] == "mohammed ali"
        # «Mohamed» blir IKKE «Mohammed».
        assert m.execute(
            "SELECT m49_normaliser('Mohamed Ali')"
        ).fetchone()[0] == "mohamed ali"
        # Diakritikk står som den står.
        assert m.execute(
            "SELECT m49_normaliser('Øystein Ås')"
        ).fetchone()[0] == "øystein ås"
        m.rollback()
    sql = MIGRASJON.read_text(encoding="utf-8")
    krop = sql[sql.index("CREATE FUNCTION m49_normaliser("):]
    krop = krop[:krop.index("$$;")]
    for forbudt in ("unaccent", "translate", "soundex", "metaphone",
                    "similarity"):
        assert forbudt not in krop, forbudt


# --------------------------------------------------------------------
# API- og flateportene.
# --------------------------------------------------------------------

def test_rutene_er_registrert_med_riktig_scope():
    """LESING `okonomi:read`, SKRIVING `bestilling:opprett`."""
    from api.app import RUTESCOPE
    forventet = {
        ("GET", "/v1/sanksjon"): "okonomi:read",
        ("GET", "/v1/sanksjon/funn"): "okonomi:read",
        ("GET", "/v1/sanksjon/lister"): "okonomi:read",
        ("GET", "/v1/sanksjon/{subjekt_id:uuid}/kontroller"):
            "okonomi:read",
        ("GET", "/v1/sanksjon/{subjekt_id:uuid}/treff"): "okonomi:read",
        ("POST", "/v1/sanksjon/krav"): "bestilling:opprett",
        ("POST", "/v1/sanksjon/liste"): "bestilling:opprett",
        ("POST", "/v1/sanksjon/subjekt"): "bestilling:opprett",
        ("POST", "/v1/sanksjon/treff/{treff_id:uuid}/avklaring"):
            "bestilling:opprett",
        ("POST", "/v1/sanksjon/{subjekt_id:uuid}/kontroll"):
            "bestilling:opprett",
        ("POST", "/v1/sanksjon/{subjekt_id:uuid}/aktiv"):
            "bestilling:opprett",
        ("POST", "/v1/sanksjon/{subjekt_id:uuid}/funn/lukk"):
            "bestilling:opprett",
    }
    for nokkel, scope in forventet.items():
        assert RUTESCOPE.get(nokkel) == scope, nokkel


def test_ingen_rute_blokkerer_eller_avfeier():
    """De to fraværene, sett fra rutetabellen."""
    from api.app import RUTESCOPE
    for metode, sti in RUTESCOPE:
        if sti.startswith("/v1/sanksjon"):
            for forbudt in ("blokker", "sperr", "avfei", "bulk"):
                assert forbudt not in sti, (metode, sti)


def test_treff_id_utledes_og_sendes_ikke_inn():
    """SP-2, og her er den mer enn idempotens.

    En klient som fikk oppgi treff-id-ene kunne sendt samme id to
    ganger, eller gjenbrukt en fra en tidligere kontroll — og et treff
    er en observasjon knyttet til NØYAKTIG én kontroll mot NØYAKTIG én
    listeversjon.
    """
    kilde = MODULFILER[0].read_text(encoding="utf-8")
    start = kilde.index("def _treffliste(")
    krop = kilde[start:kilde.index("\ndef ", start + 10)]
    assert '_utled(f"treff:{i}", tenant, nokkel)' in krop
    # …og id-en leses ALDRI fra kroppen.
    assert 'rad.get("treff_id")' not in krop
    assert '"treff_id", rid' not in krop


def test_api_et_validerer_matchtypen_mot_likheten():
    """To sjekker som svarer på hver sin ting.

    API-et gir et ærlig 400 til den som skrev feil; CHECK-en i basen
    gjør det umulig å komme utenom.
    """
    kilde = MODULFILER[0].read_text(encoding="utf-8")
    start = kilde.index("def _treffliste(")
    krop = kilde[start:kilde.index("\ndef ", start + 10)]
    assert 'if matchtype == "navnelikhet":' in krop
    assert "if likhet >= 100:" in krop
    assert 'matchtype == "eksakt_identifikator"' in krop
    assert '"identifikator" not in matchfelt' in krop


def test_flaten_har_ingen_forhandsvalgt_konklusjon():
    """`modulen_avfeide_navnelikhet`, sett fra flaten.

    Hadde vi forhåndsvalgt «ikke samme part» — den vanligste
    konklusjonen — ville porten vært pynt: brukeren måtte da aldri ta
    stilling.

    MUTASJONEN SOM DREPER DENNE: sett `konklusjon.value`, eller fjern
    `disabled` fra knappen.
    """
    js = FLATE.read_text(encoding="utf-8")
    assert 'text: t("ui.sanksjon.avklaring.velg")' in js
    assert 'el("option", { value: ""' in js
    assert "disabled: true" in js
    assert "knapp.disabled = !konklusjon.value" in js
    # …og ingen avkrysningsbokser med en samlet knapp under.
    assert 'type: "checkbox"' not in js


def test_flaten_sier_hva_modulen_ikke_gjor():
    """En bruker som TROR handelen blir stanset er farligere stilt enn
    en som vet at den ikke blir det."""
    js = FLATE.read_text(encoding="utf-8")
    assert 't("ui.sanksjon.oversikt.hvorfor")' in js
    for sprak in ("nb", "en"):
        d = json.loads((ROT / "locales" / f"{sprak}.json").read_text(
            encoding="utf-8"))
        tekst = d["ui.sanksjon.oversikt.hvorfor"].lower()
        assert ("stanser ingen" in tekst or "stops no" in tekst), tekst


def test_sveipen_leser_fire_felt_og_ikke_flere():
    """#358s lærdom, og M-49 har fire — ikke fem som M-48."""
    sveip = MODULFILER[1].read_text(encoding="utf-8")
    assert "KONTRAKTFELT = 4" in sveip
    assert "rader[0][:KONTRAKTFELT]" in sveip
    assert "forlatte" not in _bare_kode(MODULFILER[1])


def test_kjoreskriptet_har_ingen_fallback_til_database_url():
    """Runtime-rollen har med vilje ikke EXECUTE på sveipen."""
    kjor = MODULFILER[2].read_text(encoding="utf-8")
    assert "DISPONIT_SANKSJONSSVEIP_URL" in kjor
    assert "DATABASE_URL" not in _bare_kode(MODULFILER[2])


def test_timeren_staar_i_klyngestigen():
    """09:05 — stigen er fordelt i klyngefundamentet."""
    sti_t = ROT / "deploy" / "staging" / "disponit-sanksjonssveip.timer"
    timer = sti_t.read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 05:40:00 UTC" in timer
    assert "Persistent=true" in timer
    sti = ROT / "deploy" / "staging" / "disponit-sanksjonssveip.service"
    tjeneste = sti.read_text(encoding="utf-8")
    assert ("LoadCredential=DISPONIT_SANKSJONSSVEIP_URL:"
            "/etc/disponit/sanksjonssveip/DISPONIT_SANKSJONSSVEIP_URL"
            in tjeneste)
    # …og beskrivelsen navngir SIN EGEN jobb (arvefeilen fra 116).
    assert "sanksjonssveip" in tjeneste.split("Description=")[1][:60]
    assert "adresser" not in tjeneste


def test_sveipen_staar_i_flaaterosteret():
    """En sveip som ikke er i rosteret er en sveip ingen savner."""
    from drift.sveipestatus import FLAATEN
    assert FLAATEN.get("sanksjonssveip") == 30, FLAATEN


def test_ui_axe_dekning():
    """`ui_axe_alvorlige_brudd` — flaten er registrert der axe kjører."""
    app_js = (ROT / "platform" / "core" / "ui" / "static" / "js"
              / "app.js").read_text(encoding="utf-8")
    assert "sanksjon: visSanksjon," in app_js
    sitekart = (ROT / "platform" / "core" / "ui" / "static" / "js"
                / "sitekart.js").read_text(encoding="utf-8")
    assert '{ nokkel: "sanksjon", scope: "okonomi:read"' in sitekart
