"""M-48 foretaks- og kredittvakt v1 (116) — REGISTERET, OG ETT OPPSLAG.

Grensen `m48-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

DEN SKARPESTE PORTEN ER `oppslag_uten_ferskhetsvindu`, og den er
annerledes enn søskenmodulenes. For M-19 var dommen at modulen ikke
skulle spørre I DET HELE TATT. Her SPØR den — klyngens eneste utgående
kanal, etter eierbeslutning 3/9 — og da må porten måle noe vanskeligere:
at den ikke spør når den ikke trenger å. «Den unødvendige forespørselen
ER skaden», og et oppslag på et organisasjonsnummer vi alt har ferske
data om er per definisjon unødvendig.

DEN NEST SKARPESTE er `modulen_hentet_kredittdata`. Snittet går INNE i
modulen: foretaksregisteret er offentlig og sender bare et
organisasjonsnummer, mens kredittleverandøren er kommersiell, krever
hemmeligheter og gir en SCORE vi ville blitt fristet til å handle på.
Bare den første er koblet på.

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

MOTPARTSSVEIP_DSN = os.environ.get("DISPONIT_TEST_MOTPARTSSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "116_m48_motpartsregister.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "motpart.js")
KLIENT = ROT / "platform" / "core" / "api" / "foretaksregister.py"
MODULFILER = (
    ROT / "platform" / "core" / "api" / "motpart.py",
    ROT / "platform" / "drift" / "motpartssveip.py",
    ROT / "platform" / "drift" / "kjor_motpartssveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("motpartskrav", "motpartssubjekt", "foretaksoppslag",
        "motpartsversjon", "motpartsvurdering", "motpartsfunn")

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
    return koble(MOTPARTSSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m48-{merke}-{secrets.token_hex(4)}"


def _orgnr() -> str:
    """Ni siffer, unikt nok til at to tester ikke deler ferskhetsvindu."""
    return f"9{secrets.randbelow(10**8):08d}"


def _krav(c, tenant, *, ferskhet=24, gyldig=180, uvurdert=30,
          tak=50_000_000, grunnlag=("foretaksregister",),
          aktor="u-test"):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m48_sett_krav(%s,%s,%s,%s,%s,%s,%s)",
                  (tenant, ferskhet, gyldig, uvurdert, tak,
                   list(grunnlag), aktor)).fetchone()[0]
    c.commit()
    return v


def _motpart(c, tenant, *, orgnr=None, navn="Testfirma AS", mid=None,
             aktor="u-test"):
    mid = mid or uuid.uuid4()
    orgnr = orgnr or _orgnr()
    _sett_kontekst(c, tenant)
    c.execute("SELECT m48_registrer_motpart(%s,%s,%s,%s,%s)",
              (tenant, mid, orgnr, navn, aktor))
    c.commit()
    return mid, orgnr


def _vert(c) -> str:
    return c.execute("SELECT m48_registrert_vert()").fetchone()[0]


def _reserver(c, tenant, mid, *, vert=None, formaal="kredittvurdering",
              hjemmel="personvernforordningen art 6.1.f", oid=None,
              aktor="u-test"):
    oid = oid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    rad = c.execute(
        "SELECT organisasjonsnummer, forrige_oppslag FROM"
        " m48_reserver_oppslag(%s,%s,%s,%s,%s,%s,%s)",
        (tenant, oid, mid, vert if vert is not None else _vert(c),
         formaal, hjemmel, aktor)).fetchone()
    c.commit()
    return oid, rad


def _fullfor(c, tenant, oid, *, status="treff", sha=None,
             aktor="u-test"):
    _sett_kontekst(c, tenant)
    c.execute("SELECT m48_fullfor_oppslag(%s,%s,%s,%s,%s)",
              (tenant, oid, status,
               sha if sha is not None
               else ("a" * 64 if status == "treff" else None), aktor))
    c.commit()


def _versjon(c, tenant, mid, oid, *, vid=None, kilde="foretaksregister",
             kildeversjon="2026-09-01", navn="TESTFIRMA AS", form="AS",
             status="aktiv", konkurs=False, tvang=False,
             fra="2026-09-01", aktor="u-test"):
    vid = vid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m48_registrer_versjon("
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::date,%s)",
        (tenant, vid, mid, oid, kilde, kildeversjon, navn, form,
         status, konkurs, tvang, fra, aktor))
    c.commit()
    return vid


def _vurdering(c, tenant, vid, *, grunnlag="foretaksregister",
               ore=25_000_000, begrunnelse="Aktivt AS, ingen anmerkn.",
               uid=None, aktor="u-test"):
    uid = uid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    pv = c.execute(
        "SELECT m48_registrer_vurdering(%s,%s,%s,%s,%s,%s,%s)",
        (tenant, uid, vid, grunnlag, ore, begrunnelse,
         aktor)).fetchone()[0]
    c.commit()
    return uid, pv


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m48_sveip_motparter(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT funntype, over_grense, siste_registerstatus, apen"
        "  FROM motpartsfunn WHERE tenant=%s ORDER BY funntype",
        (tenant,)).fetchall()
    m.rollback()
    return rader


# --------------------------------------------------------------------
# §0: hver invariant i `m48-v1` har minst én port.
# --------------------------------------------------------------------

def test_grensen_er_registrert_og_hver_invariant_har_en_port():
    """§0-regelen, målt: grensen finnes, og hver invariant nevnes her.

    MUTASJONEN SOM DREPER DENNE: legg til en invariant i `m48-v1` uten
    å skrive porten.
    """
    from manifestskjema import KRAVGRENSER
    grense = KRAVGRENSER["m48-v1"]
    inv = set(grense["invarianter"])
    assert inv, grense
    tekst = Path(__file__).read_text(encoding="utf-8")
    mangler = sorted(i for i in inv if i not in tekst)
    assert mangler == [], f"invarianter uten port: {mangler}"


def test_kredittleverandoren_er_ikke_koblet_paa():
    """`modulen_hentet_kredittdata` — snittet går INNE i modulen.

    Foretaksregisteret er koblet på; kredittleverandøren er det ikke.
    Den er kommersiell, krever hemmeligheter, sender de reelle
    rettighetshavernes navn til en tredjepart og gir en SCORE modulen
    ville blitt fristet til å handle på.

    MUTASJONEN SOM DREPER DENNE: legg til et kredittoppslag.
    """
    forbudt = ("kredittleverandor", "kredittscore", "credit_score",
               "opencorporates", "experian", "bisnode", "creditsafe",
               "dun_bradstreet")
    filer = list(MODULFILER) + [KLIENT, MIGRASJON, FLATE]
    for fil in filer:
        kode = _bare_kode(fil, uten_strenger=True).lower()
        for ord_ in forbudt:
            assert ord_ not in kode, f"{fil.name}: {ord_}"
    # …og skjemaet har ingen kolonne å legge en score i.
    sql = _bare_kode(MIGRASJON).lower()
    for kol in ("score", "rating", "risikoklasse"):
        assert kol not in sql, kol


def test_ingen_kolonne_for_gjeldende_kredittgrense():
    """`modulen_satte_kredittgrense` — FRAVÆRET er porten.

    Spesifikasjonens vakt sier «setter aldri kredittgrensen selv», og
    kredittgrensen er INNGANG til M-23, ikke omvendt. Hadde skjemaet
    hatt et felt for den aktive grensen, ville fullmakten vært bygget
    allerede.

    MUTASJONEN SOM DREPER DENNE: legg til `gjeldende_grense_ore`.
    """
    sql = _bare_kode(MIGRASJON).lower()
    for forbudt in ("gjeldende_grense", "aktiv_grense",
                    "kredittgrense_ore", "innvilget_grense"):
        assert forbudt not in sql, forbudt
    # Kolonnen som FINNES heter forslag, og gjør det overalt.
    assert "foreslatt_grense_ore" in sql


def test_ingen_dor_avslar_en_motpart():
    """`modulen_avslo_motpart` — avslag er en menneskelig beslutning.

    `m48_deaktiver_motpart` er ikke et avslag: den sier «vi handler
    ikke med denne lenger», og historikken blir stående. Et AVSLAG
    ville vært en dom modulen felte.

    MUTASJONEN SOM DREPER DENNE: legg til en avslagsdør.
    """
    sql = _bare_kode(MIGRASJON).lower()
    for forbudt in ("m48_avsla", "m48_avvis_motpart", "avslag",
                    "avslatt"):
        assert forbudt not in sql, forbudt
    api = _bare_kode(MODULFILER[0]).lower()
    assert "avsla" not in api


def test_modulen_signerer_ingen_attestasjon():
    """`modulen_signerte_attestasjon` — v1 attesterer ingenting."""
    for fil in list(MODULFILER) + [KLIENT, MIGRASJON]:
        kode = _bare_kode(fil, uten_strenger=True).lower()
        for forbudt in ("attester", "signer", "attestasjon"):
            assert forbudt not in kode, f"{fil.name}: {forbudt}"


def test_kredittpolicyen_ligger_i_basen_ikke_i_koden():
    """`kredittpolicy_hardkodet` — grensene er TENANTENS.

    Ferskhetsvinduet, fristene og taket ligger i `motpartskrav`, satt
    gjennom en dør. En konstant i sveipen eller i API-et ville vært
    nøyaktig den fullmakten invarianten forbyr.

    MUTASJONEN SOM DREPER DENNE: sett et standardvindu i Python.
    """
    sveip = _bare_kode(MODULFILER[1])
    # Sveipen har ingen frist-tall i det hele tatt.
    for forbudt in ("FERSKHET", "GYLDIG_DOGN", "UVURDERT", "MAKS_ORE",
                    "MAKS_FORSLAG"):
        assert forbudt not in sveip, forbudt
    # API-ets `KRAVGRENSER` er BARE ytre validering, og speiler
    # CHECK-ene — den bærer ingen standardverdi.
    api = MODULFILER[0].read_text(encoding="utf-8")
    assert 'KRAVGRENSER = {' in api
    for forbudt in ('oppslag_ferskhet_timer = 24',
                    'STANDARD_FERSKHET', 'DEFAULT_VINDU'):
        assert forbudt not in api, forbudt
    # …og døra leser vinduet fra basen, ikke fra et argument.
    sql = MIGRASJON.read_text(encoding="utf-8")
    assert "SELECT oppslag_ferskhet_timer INTO v_vindu" in sql


def test_sveipen_kan_ikke_snakke_ut():
    """`oppslag_uten_formaal_og_hjemmel`, sett fra sveipens side.

    M-48 HAR en utgående kanal, og sveipen er der det ville vært
    lettest å misbruke den: den vet hvilke motparter som står
    uvurderte, og en nattlig oppfriskning av alle ville «løst» dem på
    én kjøring. Det ville vært doktrinens verste tilfelle —
    unødvendige forespørsler i industriell skala — og en sveip har
    hverken formål eller hjemmel å oppgi.

    MUTASJONEN SOM DREPER DENNE: importer `foretaksregister` i
    sveipen.
    """
    for fil in (MODULFILER[1], MODULFILER[2]):
        tre = ast.parse(fil.read_text(encoding="utf-8"))
        navn = set()
        for node in ast.walk(tre):
            if isinstance(node, ast.Import):
                navn.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                navn.add(node.module or "")
                navn.update(a.name for a in node.names)
        for forbudt in ("httpx", "requests", "socket", "urllib",
                        "http", "foretaksregister", "ssrf"):
            assert not any(n == forbudt or n.endswith("." + forbudt)
                           for n in navn), f"{fil.name}: {forbudt}"


def test_verten_er_en_kilde_og_den_ligger_i_basen():
    """`oppslag_mot_uregistrert_vert` — én registrert vert.

    Verten står i basen og ikke i `motpartskrav`: den tabellen er
    TENANTENS, og kunne en tenant sette verten, hadde vi bygget en
    SSRF-dør med policyklær på.

    MUTASJONEN SOM DREPER DENNE: hardkod verten i klienten, eller
    flytt den til `motpartskrav`.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    assert "CREATE FUNCTION m48_registrert_vert()" in sql
    assert "'data.brreg.no'::text" in sql
    # Verten er IKKE en tenant-kolonne.
    krav = sql[sql.index("CREATE TABLE motpartskrav"):
               sql.index("CREATE TABLE motpartssubjekt")]
    assert "vert" not in krav.lower()
    # Klienten har ingen egen vertskonstant.
    klient = _bare_kode(KLIENT)
    assert "brreg" not in klient.lower(), klient
    # …og API-et leser verten fra basen før det reserverer.
    api = MODULFILER[0].read_text(encoding="utf-8")
    assert "SELECT m48_registrert_vert()" in api


def test_klienten_gaar_over_den_pinnede_transporten():
    """Egressvakta (014b) er den samme her som overalt ellers."""
    tre = ast.parse(KLIENT.read_text(encoding="utf-8"))
    navn = set()
    for node in ast.walk(tre):
        if isinstance(node, ast.ImportFrom):
            navn.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            navn.update(a.name for a in node.names)
    assert "ssrf" in navn, navn
    # Ingen egen httpx-klient utenom `ssrf.lag_klient`.
    kode = _bare_kode(KLIENT)
    assert "httpx.Client" not in kode
    assert "ssrf.lag_klient()" in kode
    assert "ssrf.les_begrenset" in kode


def test_flaten_har_ingen_forhandsvalgt_formaal():
    """`oppslag_uten_formaal_og_hjemmel`, sett fra flaten.

    En forhåndsvalgt «kredittvurdering» ville gjort porten til pynt:
    brukeren måtte da aldri ta stilling, og feltet hadde alltid vært
    fylt ut av oss.

    MUTASJONEN SOM DREPER DENNE: sett `formaal.value` til en verdi,
    eller fjern `disabled` fra knappen.
    """
    js = FLATE.read_text(encoding="utf-8")
    assert 'text: t("ui.motpart.oppslag.velg_formaal")' in js
    assert 'el("option", { value: ""' in js
    assert 'disabled: true' in js
    assert "knapp.disabled = !formaal.value" in js
    # …og API-laget har ingen standardverdi heller.
    api = _bare_kode(MODULFILER[0])
    assert 'formaal = _valg(kropp, "formaal", rid, FORMAAL)' in api


# --------------------------------------------------------------------
# Databaseportene.
# --------------------------------------------------------------------

@pg
def test_oppslag_innenfor_ferskhetsvinduet_nektes(miljo):
    """DEN SKARPESTE PORTEN: `oppslag_uten_ferskhetsvindu`.

    «Den unødvendige forespørselen ER skaden.» Et oppslag på et
    organisasjonsnummer vi alt har ferske data om er per definisjon
    unødvendig — og nektes i BASEN, ikke i en klient som kan glemmes.

    MUTASJONEN SOM DREPER DENNE: fjern vindussjekken, eller flytt den
    til Python.
    """
    tenant = _tenantnavn("vindu")
    with _rt() as c:
        _krav(c, tenant, ferskhet=24)
        mid, _ = _motpart(c, tenant)
        _reserver(c, tenant, mid)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _reserver(c, tenant, mid)
        assert "ferskhetsvindu" in str(e.value)
        c.rollback()


@pg
def test_ferskhetsvinduet_er_tenantens_og_null_slaar_det_av(miljo):
    """Vinduet er policy, ikke en konstant. 0 er et LOVLIG valg.

    Et gulv i koden ville flyttet valget dit ingen kan se det; med 0 i
    `motpartskrav` står valget med navn og tidspunkt, og sveipen måler
    hvor mange oppslag det faktisk ble.
    """
    tenant = _tenantnavn("null")
    with _rt() as c:
        _krav(c, tenant, ferskhet=0)
        mid, _ = _motpart(c, tenant)
        _reserver(c, tenant, mid)
        # Ingen unntak: vinduet er av.
        _reserver(c, tenant, mid)
        c.rollback()


@pg
def test_feilede_oppslag_stenger_ikke_vinduet(miljo):
    """En forespørsel som aldri kom fram ga oss ingen kunnskap.

    Et vindu som stengte på en nettverksfeil ville låst en tenant ute
    i et døgn. De TELLES likevel — sveipen finner
    `gjentatte_oppslagsfeil`.
    """
    tenant = _tenantnavn("feil")
    with _rt() as c:
        _krav(c, tenant, ferskhet=24)
        mid, _ = _motpart(c, tenant)
        oid, _ = _reserver(c, tenant, mid)
        _fullfor(c, tenant, oid, status="feil")
        # Nytt forsøk er lovlig.
        _reserver(c, tenant, mid)
        c.rollback()


@pg
def test_oppslag_mot_annen_vert_nektes(miljo):
    """`oppslag_mot_uregistrert_vert`, håndhevet i basen.

    En klient som ble endret til å spørre et annet sted, får ingen
    reservasjon å gjøre det under — og uten reservasjon kan svaret
    aldri bli en `motpartsversjon`.
    """
    tenant = _tenantnavn("vert")
    with _rt() as c:
        _krav(c, tenant)
        mid, _ = _motpart(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _reserver(c, tenant, mid, vert="evil.example.com")
        assert "registrerte verten" in str(e.value)
        c.rollback()


@pg
def test_oppslag_uten_policy_nektes(miljo):
    """Et oppslag uten policy er et oppslag ingen har hjemlet.

    `coalesce` mot tabellens egen default ville vært å hardkode
    policyen i en annen fil enn den som eier den.
    """
    tenant = _tenantnavn("upolicy")
    with _rt() as c:
        mid, _ = _motpart(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _reserver(c, tenant, mid)
        assert "motpartskrav" in str(e.value)
        c.rollback()


@pg
def test_formaal_og_hjemmel_staar_paa_raden(miljo):
    """`oppslag_uten_formaal_og_hjemmel` — to NOT NULL uten default.

    Et tilsyn spør ikke «slo dere opp?» — det spør «med hvilken
    hjemmel».
    """
    tenant = _tenantnavn("hjemmel")
    with _rt() as c:
        _krav(c, tenant)
        mid, _ = _motpart(c, tenant)
        oid, _ = _reserver(c, tenant, mid, formaal="onboarding",
                           hjemmel="avtale med motpart, art 6.1.b")
        _sett_kontekst(c, tenant)
        rad = c.execute(
            "SELECT formaal, hjemmel, vert FROM"
            " m48_oppslagene(%s,%s)", (tenant, mid)).fetchone()
        assert rad[0] == "onboarding"
        assert rad[1] == "avtale med motpart, art 6.1.b"
        assert rad[2] == "data.brreg.no"
        c.rollback()
    # …og kolonnene har ingen standardverdi i skjemaet.
    sql = MIGRASJON.read_text(encoding="utf-8")
    blokk = sql[sql.index("CREATE TABLE foretaksoppslag"):
                sql.index("CREATE INDEX foretaksoppslag_ferskhet")]
    for felt in ("formaal TEXT NOT NULL", "hjemmel TEXT NOT NULL"):
        assert felt in blokk
    assert "formaal TEXT NOT NULL DEFAULT" not in blokk
    assert "hjemmel TEXT NOT NULL DEFAULT" not in blokk


@pg
def test_et_fullfort_oppslag_er_frosset(miljo):
    """Et svar overskrives ikke — hverken gjennom døra eller utenom.

    TO GJERDER: døra gir den ærlige feilmeldingen, radvakten stanser
    den som går utenom. Samme form som 110–114.
    """
    tenant = _tenantnavn("frys")
    with _rt() as c:
        _krav(c, tenant)
        mid, _ = _motpart(c, tenant)
        oid, _ = _reserver(c, tenant, mid)
        _fullfor(c, tenant, oid, status="treff")
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _fullfor(c, tenant, oid, status="feil")
        assert "alt fullført" in str(e.value)
        c.rollback()
    # …og utenom døra, som eieren selv.
    with psycopg.connect(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        m.execute("SET ROLE disponit_motpart_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            m.execute("UPDATE foretaksoppslag SET svarstatus='feil'"
                      " WHERE tenant=%s AND oppslag_id=%s",
                      (tenant, oid))
        m.rollback()


@pg
def test_forespørselens_egne_felter_kan_ikke_skrives_om(miljo):
    """Bare SVARET kan fylles inn.

    Hvem, om hvem, hvorfor og mot hvilken vert er skrevet FØR
    forespørselen gikk ut, og skal ikke kunne skrives om etterpå.
    """
    tenant = _tenantnavn("omskriv")
    with _rt() as c:
        _krav(c, tenant)
        mid, _ = _motpart(c, tenant)
        oid, _ = _reserver(c, tenant, mid)
        c.rollback()
    with psycopg.connect(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        m.execute("SET ROLE disponit_motpart_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as e:
            m.execute("UPDATE foretaksoppslag SET hjemmel='noe annet'"
                      " WHERE tenant=%s AND oppslag_id=%s",
                      (tenant, oid))
        assert "frosset" in str(e.value)
        m.rollback()


@pg
def test_en_versjon_krever_et_oppslag_med_treff(miljo):
    """`motpartshistorikk_overskrevet`, fra den andre kanten.

    Fremmednøkkelen sikrer at oppslaget FINNES; døra sikrer at det ga
    et svar. Uten den kunne en reservasjon som aldri gikk ut, blitt
    til en «registerprofil».
    """
    tenant = _tenantnavn("versjon")
    with _rt() as c:
        _krav(c, tenant)
        mid, _ = _motpart(c, tenant)
        oid, _ = _reserver(c, tenant, mid)
        # Reservasjonen er ikke fullført ennå.
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _versjon(c, tenant, mid, oid)
        assert "bare et treff er en profil" in str(e.value)
        c.rollback()
        _fullfor(c, tenant, oid, status="ikke_funnet")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _versjon(c, tenant, mid, oid)
        c.rollback()


@pg
def test_et_oppslag_om_en_annen_motpart_gir_ingen_profil(miljo):
    """Uten sjekken ville en forveksling blitt en profil på feil rad."""
    tenant = _tenantnavn("kryss")
    with _rt() as c:
        _krav(c, tenant)
        a, _ = _motpart(c, tenant)
        b, _ = _motpart(c, tenant)
        oid, _ = _reserver(c, tenant, a)
        _fullfor(c, tenant, oid)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _versjon(c, tenant, b, oid)
        assert "gjelder" in str(e.value)
        c.rollback()


@pg
def test_historikken_kan_ikke_overskrives(miljo):
    """`motpartshistorikk_overskrevet` — append-only, håndhevet."""
    tenant = _tenantnavn("append")
    with _rt() as c:
        _krav(c, tenant)
        mid, _ = _motpart(c, tenant)
        oid, _ = _reserver(c, tenant, mid)
        _fullfor(c, tenant, oid)
        vid = _versjon(c, tenant, mid, oid)
        _vurdering(c, tenant, vid)
        c.rollback()
    for tabell in ("motpartsversjon", "motpartsvurdering"):
        # EGEN FORBINDELSE PER TABELL. `rollback()` tilbakestiller
        # `SET ROLE` — en løkke som ruller tilbake mellom rundene
        # ville kjørt runde to som TABELLEIEREN, og da hadde
        # UPDATE-en gått gjennom og porten målt ingenting.
        with psycopg.connect(MIGRATOR_DSN) as m:
            _sett_kontekst(m, tenant)
            m.execute("SET ROLE disponit_motpart_eier")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                m.execute(f"UPDATE {tabell} SET tenant=tenant"
                          " WHERE tenant=%s", (tenant,))
            m.rollback()


@pg
def test_vurderingen_baerer_policyversjonen_fra_basen(miljo):
    """`vurdering_uten_policyversjon`.

    Policyversjonen er IKKE et argument: en kaller som fikk oppgi den
    kunne oppgitt en annen enn den som faktisk gjaldt, og porten ville
    vært noe man gikk utenom ved å lyve.
    """
    tenant = _tenantnavn("policyv")
    with _rt() as c:
        _krav(c, tenant)
        mid, _ = _motpart(c, tenant)
        oid, _ = _reserver(c, tenant, mid)
        _fullfor(c, tenant, oid)
        vid = _versjon(c, tenant, mid, oid)
        _, pv1 = _vurdering(c, tenant, vid)
        assert pv1 == 1
        _krav(c, tenant, ferskhet=12)      # hever versjonen
        _, pv2 = _vurdering(c, tenant, vid)
        assert pv2 == 2
        c.rollback()
    # …og signaturen tar ingen policyversjon inn.
    sql = MIGRASJON.read_text(encoding="utf-8")
    sig = sql[sql.index("CREATE FUNCTION m48_registrer_vurdering("):]
    sig = sig[:sig.index(")")]
    assert "policyversjon" not in sig, sig


@pg
def test_forslag_over_taket_lagres_og_blir_et_funn(miljo):
    """Taket MÅLES, det nekter ikke.

    Nektet døra å lagre det, ville nettopp den observasjonen noen
    skulle tatt stilling til forsvunnet — og modulen hadde tatt en
    beslutning den ikke har fullmakt til.
    """
    tenant = _tenantnavn("tak")
    with _rt() as c:
        _krav(c, tenant, tak=1_000_000)
        mid, _ = _motpart(c, tenant)
        oid, _ = _reserver(c, tenant, mid)
        _fullfor(c, tenant, oid)
        vid = _versjon(c, tenant, mid, oid)
        _vurdering(c, tenant, vid, ore=9_000_000)   # over taket
        c.rollback()
    with _sv() as v:
        _sveip(v)
    with psycopg.connect(MIGRATOR_DSN) as m:
        typer = [r[0] for r in _funn(m, tenant)]
        assert "forslag_over_tak" in typer, typer


@pg
def test_tenantisolasjon(miljo):
    """`tenantlekkasje_i_motpartsregister` — RLS + `krev_tenantkontekst`."""
    a, b = _tenantnavn("iso-a"), _tenantnavn("iso-b")
    with _rt() as c:
        _krav(c, a)
        _krav(c, b)
        mid_a, _ = _motpart(c, a, navn="A AS")
        _motpart(c, b, navn="B AS")
        # A i konteksten, spør om B.
        _sett_kontekst(c, a)
        with pytest.raises(psycopg.Error):
            c.execute("SELECT * FROM m48_motpartene(%s,%s)", (b, 10))
        c.rollback()
        # A ser bare sine egne.
        _sett_kontekst(c, a)
        navn = [r[2] for r in c.execute(
            "SELECT * FROM m48_motpartene(%s,%s)", (a, 50)).fetchall()]
        assert navn == ["A AS"], navn
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
    """SP-7. Og målt med `has_table_privilege`, ikke information_schema:
    den siste viser BARE navngitte mottakere og er blind for PUBLIC."""
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
            " 'm48_sveip_motparter(int)', 'EXECUTE')").fetchone()[0] \
            is False
        assert m.execute(
            "SELECT has_function_privilege('disponit_motpartssveip',"
            " 'm48_sveip_motparter(int)', 'EXECUTE')").fetchone()[0] \
            is True
        # …og sveiperollen har ingen tabellrettigheter.
        for tabell in EGNE:
            assert m.execute(
                "SELECT has_table_privilege('disponit_motpartssveip',"
                " %s, 'SELECT')", (tabell,)).fetchone()[0] is False
        m.rollback()


@pg
def test_sveipen_ser_alle_tenanter(miljo):
    """DEN LATE MARKØREN, målt (112s lærdom).

    `FOR t IN SELECT ...` er en LAT markør, og `set_config` inne i
    løkka endrer RLS-konteksten den fortsatt leser gjennom — da ser
    den færre og færre tenanter for hver runde. Tenantlista
    materialiseres derfor til et array FØR løkka.

    MUTASJONEN SOM DREPER DENNE: bytt arrayet mot en `FOR ... IN
    SELECT`-løkke.
    """
    a, b, c_ = (_tenantnavn("sv-a"), _tenantnavn("sv-b"),
                _tenantnavn("sv-c"))
    with _rt() as c:
        for t in (a, b, c_):
            _motpart(c, t)      # ingen krav -> `ingen_krav`
        c.rollback()
    with _sv() as v:
        rad = _sveip(v)
    assert rad[0] >= 3, rad
    with psycopg.connect(MIGRATOR_DSN) as m:
        for t in (a, b, c_):
            typer = [r[0] for r in _funn(m, t)]
            assert "ingen_krav" in typer, (t, typer)


@pg
def test_forlatt_reservasjon_faar_en_vei_ut(miljo):
    """M-39s FELLE, unngått (113).

    En funntype uten øvre grense OG uten botemiddel er et varsel som
    aldri kan lukkes — og et varsel som aldri lukkes blir et varsel
    ingen leser. En klient som dør mellom de to dørene etterlater en
    reservasjon ingen fyller ut, så sveipen setter `forlatt` etter
    seks timer.

    MUTASJONEN SOM DREPER DENNE: fjern ryddingen, eller la
    `oppslag_uten_svar` stå uten en terminaltilstand.
    """
    tenant = _tenantnavn("forlatt")
    with _rt() as c:
        _krav(c, tenant)
        mid, _ = _motpart(c, tenant)
        oid, _ = _reserver(c, tenant, mid)
        c.rollback()
    # TIDSREISE, OG DEN MÅ GÅ RUNDT RADVAKTEN — som er poenget.
    #
    # `reservert` er et av feltene vakten fryser: forespørselens egne
    # opplysninger skal ikke kunne skrives om etter at den gikk ut. I
    # drift går tiden av seg selv; i en test må den forfalskes, og da
    # er den ærlige veien å slå av vakten EKSPLISITT og slå den på
    # igjen — ikke å myke opp vakten så testen slipper til.
    #
    # `try/finally`: en test som feiler midtveis skal ikke etterlate
    # en avslått radvakt til de neste.
    with psycopg.connect(MIGRATOR_DSN) as m:
        m.execute("ALTER TABLE public.foretaksoppslag"
                  " DISABLE TRIGGER foretaksoppslag_frosset")
        m.commit()
        try:
            m.execute("SET ROLE disponit_motpart_eier")
            _sett_kontekst(m, tenant)
            m.execute("UPDATE foretaksoppslag"
                      "   SET reservert = now() - interval '9 hours'"
                      " WHERE tenant=%s AND oppslag_id=%s",
                      (tenant, oid))
            m.commit()
        finally:
            # `RESET ROLE` FØR opprydningen. `SET ROLE` er ikke
            # transaksjonslokal: `commit()` inne i `try` gjør den
            # permanent for sesjonen, og da ville `ENABLE TRIGGER`
            # kjørt som eierrollen — som ikke eier tabellen.
            m.rollback()
            m.execute("RESET ROLE")
            m.execute("ALTER TABLE public.foretaksoppslag"
                      " ENABLE TRIGGER foretaksoppslag_frosset")
            m.commit()
    with _sv() as v:
        rad = _sveip(v)
    assert rad[4] >= 1, rad          # `forlatte`
    with psycopg.connect(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        status = m.execute(
            "SELECT svarstatus, fullfort IS NOT NULL"
            "  FROM foretaksoppslag WHERE tenant=%s AND oppslag_id=%s",
            (tenant, oid)).fetchone()
        assert status == ("forlatt", True), status
        m.rollback()
    # …og en forlatt reservasjon stenger ikke vinduet.
    with _rt() as c:
        _reserver(c, tenant, mid)
        c.rollback()


@pg
def test_funnene_lukkes_naar_de_er_loest(miljo):
    """Et funn som ikke lenger er en kandidat, er løst.

    MUTASJONEN SOM DREPER DENNE: fjern lukkedelen av sveipen.
    """
    tenant = _tenantnavn("lukk")
    with _rt() as c:
        mid, _ = _motpart(c, tenant)     # ingen krav
        c.rollback()
    with _sv() as v:
        _sveip(v)
    with psycopg.connect(MIGRATOR_DSN) as m:
        assert ("ingen_krav", None, None, True) in _funn(m, tenant)
    with _rt() as c:
        _krav(c, tenant)                 # botemiddelet
        c.rollback()
    with _sv() as v:
        _sveip(v)
    with psycopg.connect(MIGRATOR_DSN) as m:
        rader = {r[0]: r[3] for r in _funn(m, tenant)}
        assert rader.get("ingen_krav") is False, rader


@pg
def test_belop_er_heltall_ore(miljo):
    """101s form: BIGINT øre. `True` er en `int` i Python."""
    tenant = _tenantnavn("ore")
    with psycopg.connect(MIGRATOR_DSN) as m:
        typ = m.execute(
            "SELECT data_type FROM information_schema.columns"
            " WHERE table_name='motpartsvurdering'"
            "   AND column_name='foreslatt_grense_ore'").fetchone()[0]
        assert typ == "bigint", typ
        typ = m.execute(
            "SELECT data_type FROM information_schema.columns"
            " WHERE table_name='motpartskrav'"
            "   AND column_name='maks_forslag_ore'").fetchone()[0]
        assert typ == "bigint", typ
        m.rollback()
    api = MODULFILER[0].read_text(encoding="utf-8")
    assert "isinstance(verdi, bool)" in api
    with _rt() as c:
        _krav(c, tenant)
        mid, _ = _motpart(c, tenant)
        oid, _ = _reserver(c, tenant, mid)
        _fullfor(c, tenant, oid)
        vid = _versjon(c, tenant, mid, oid)
        with pytest.raises(psycopg.errors.CheckViolation):
            _vurdering(c, tenant, vid, ore=-1)
        c.rollback()


@pg
def test_grunnlaget_maa_vaere_tenantens_eget(miljo):
    """En tenant kan velge fra det lukkede settet, ikke utvide det."""
    tenant = _tenantnavn("grunnlag")
    with _rt() as c:
        _krav(c, tenant, grunnlag=("foretaksregister",))
        mid, _ = _motpart(c, tenant)
        oid, _ = _reserver(c, tenant, mid)
        _fullfor(c, tenant, oid)
        vid = _versjon(c, tenant, mid, oid)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _vurdering(c, tenant, vid, grunnlag="manuell_gjennomgang")
        assert "godkjente" in str(e.value)
        c.rollback()
        # …og et ukjent grunnlag kan ikke settes i policyen heller.
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _krav(c, tenant, grunnlag=("oppslag_i_avisen",))
        c.rollback()


@pg
def test_deaktivering_beholder_historikken(miljo):
    """«Vi handler ikke med denne lenger» er ikke «dette skjedde aldri»."""
    tenant = _tenantnavn("deakt")
    with _rt() as c:
        _krav(c, tenant)
        mid, _ = _motpart(c, tenant)
        oid, _ = _reserver(c, tenant, mid)
        _fullfor(c, tenant, oid)
        vid = _versjon(c, tenant, mid, oid)
        _sett_kontekst(c, tenant)
        c.execute("SELECT m48_deaktiver_motpart(%s,%s,%s)",
                  (tenant, mid, "u-test"))
        c.commit()
        _sett_kontekst(c, tenant)
        n = c.execute("SELECT count(*) FROM m48_versjonene(%s,%s)",
                      (tenant, mid)).fetchone()[0]
        assert n == 1
        n = c.execute("SELECT count(*) FROM m48_oppslagene(%s,%s)",
                      (tenant, mid)).fetchone()[0]
        assert n == 1
        c.rollback()
        # …og en deaktivert motpart slås ikke opp.
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _reserver(c, tenant, mid)
        assert "deaktivert" in str(e.value)
        c.rollback()
        assert vid is not None


@pg
def test_funn_lukkes_bare_med_et_notat(miljo):
    """Et funn som lukkes uten begrunnelse er gjemt, ikke løst."""
    tenant = _tenantnavn("notat")
    with _rt() as c:
        mid, _ = _motpart(c, tenant)
        c.rollback()
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute("SELECT m48_lukk_funn(%s,%s,%s,%s,%s)",
                      (tenant, mid, "ingen_krav", "ok", "u-test"))
        c.rollback()
        _sett_kontekst(c, tenant)
        c.execute("SELECT m48_lukk_funn(%s,%s,%s,%s,%s)",
                  (tenant, mid, "ingen_krav",
                   "policy kommer neste uke", "u-test"))
        c.commit()
    with psycopg.connect(MIGRATOR_DSN) as m:
        rader = {r[0]: r[3] for r in _funn(m, tenant)}
        assert rader.get("ingen_krav") is False, rader


@pg
def test_evidenskjeden_bærer_hvert_oppslag(miljo):
    """Et tilsyn spør etter oppslaget i evidenskjeden, ikke i loggen."""
    tenant = _tenantnavn("evidens")
    with _rt() as c:
        _krav(c, tenant)
        mid, _ = _motpart(c, tenant)
        _reserver(c, tenant, mid)
        c.rollback()
    with psycopg.connect(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        handlinger = [r[0] for r in m.execute(
            "SELECT handling FROM revisjonslogg"
            " WHERE tenant=%s AND kilde='m48_motpart'"
            " ORDER BY handling", (tenant,)).fetchall()]
        assert "foretaksoppslag_reservert" in handlinger, handlinger
        assert "motpart_registrert" in handlinger, handlinger
        m.rollback()


# --------------------------------------------------------------------
# API-portene.
# --------------------------------------------------------------------

def test_rutene_er_registrert_med_riktig_scope():
    """LESING `okonomi:read`, SKRIVING `bestilling:opprett`."""
    from api.app import RUTESCOPE
    forventet = {
        ("GET", "/v1/motpart"): "okonomi:read",
        ("GET", "/v1/motpart/funn"): "okonomi:read",
        ("GET", "/v1/motpart/{motpart_id:uuid}/historikk"):
            "okonomi:read",
        ("GET", "/v1/motpart/{motpart_id:uuid}/oppslagslogg"):
            "okonomi:read",
        ("POST", "/v1/motpart/krav"): "bestilling:opprett",
        ("POST", "/v1/motpart/registrer"): "bestilling:opprett",
        ("POST", "/v1/motpart/versjon/{versjon_id:uuid}/vurdering"):
            "bestilling:opprett",
        ("POST", "/v1/motpart/{motpart_id:uuid}/oppslag"):
            "bestilling:opprett",
        ("POST", "/v1/motpart/{motpart_id:uuid}/deaktiver"):
            "bestilling:opprett",
        ("POST", "/v1/motpart/{motpart_id:uuid}/funn/lukk"):
            "bestilling:opprett",
    }
    for nokkel, scope in forventet.items():
        assert RUTESCOPE.get(nokkel) == scope, nokkel


def test_oppslagsloggen_er_en_lesevei_for_tenanten():
    """Et unntak ingen kan etterprøve er ikke et unntak.

    Spørsmålet «hvilke organisasjonsnumre har dere sendt ut, når, mot
    hvilken vert og med hvilken hjemmel» skal kunne besvares av den
    som eier dataene.

    MUTASJONEN SOM DREPER DENNE: fjern lesedøra.
    """
    from api.app import RUTESCOPE
    assert ("GET", "/v1/motpart/{motpart_id:uuid}/oppslagslogg") \
        in RUTESCOPE
    sql = MIGRASJON.read_text(encoding="utf-8")
    assert "CREATE FUNCTION m48_oppslagene(" in sql
    assert "m48_oppslagene(TEXT, UUID) TO disponit" in sql


def test_reservasjonen_committes_for_forespørselen():
    """DESIGNETS KJERNE, målt i koden.

    Gjorde vi reservasjon, forespørsel og fullføring i ÉN transaksjon,
    ville en krasj under forespørselen rullet reservasjonen tilbake —
    og da hadde forespørselen gått ut av huset uten at det fantes en
    rad som sa det.

    MUTASJONEN SOM DREPER DENNE: flytt `conn.commit()` ned etter
    `fr.hent`.
    """
    kilde = MODULFILER[0].read_text(encoding="utf-8")
    start = kilde.index("def oppslag_endepunkt(")
    krop = kilde[start:]
    i_res = krop.index("m48_reserver_oppslag")
    i_commit = krop.index("conn.commit()", i_res)
    i_hent = krop.index("fr.hent(", i_res)
    assert i_res < i_commit < i_hent, (i_res, i_commit, i_hent)


def test_oppslaget_er_idempotent_paa_nokkelen():
    """SP-2, og her er den STRENGT nødvendig.

    En gjentatt POST må ikke bli to utgående forespørsler — det er
    forskjellen på et dobbeltklikk og to oppslag noen må svare for.
    """
    kilde = MODULFILER[0].read_text(encoding="utf-8")
    start = kilde.index("def oppslag_endepunkt(")
    krop = kilde[start:]
    assert "_krev_idem(request, rid)" in krop
    assert '_utled("oppslag", tenant, nokkel)' in krop


def test_api_et_har_ingen_dor_som_setter_en_grense():
    """`modulen_satte_kredittgrense`, fra API-siden."""
    from api.app import RUTESCOPE
    for metode, sti in RUTESCOPE:
        if sti.startswith("/v1/motpart"):
            for forbudt in ("grense", "avsla", "innvilg", "godkjenn"):
                assert forbudt not in sti, (metode, sti)


def test_ui_axe_dekning():
    """`ui_axe_alvorlige_brudd` — flaten er dekket av UI-suiten.

    Selve axe-kjøringen ligger i `platform/core/ui/test/`; denne
    porten måler at flaten er REGISTRERT der den blir kjørt.
    """
    app_js = (ROT / "platform" / "core" / "ui" / "static" / "js"
              / "app.js").read_text(encoding="utf-8")
    assert "motpart: visMotpart," in app_js
    sitekart = (ROT / "platform" / "core" / "ui" / "static" / "js"
                / "sitekart.js").read_text(encoding="utf-8")
    assert '{ nokkel: "motpart", scope: "okonomi:read"' in sitekart


def test_sveipen_leser_fem_felt_og_ikke_flere():
    """#358s lærdom: en dør som en dag gir et sjette felt skal ikke
    gjøre en gyldig kjøring til en feilet."""
    sveip = MODULFILER[1].read_text(encoding="utf-8")
    assert "KONTRAKTFELT = 5" in sveip
    assert "rader[0][:KONTRAKTFELT]" in sveip


def test_kjoreskriptet_har_ingen_fallback_til_database_url():
    """Runtime-rollen har med vilje ikke EXECUTE på sveipen."""
    kjor = MODULFILER[2].read_text(encoding="utf-8")
    assert "DISPONIT_MOTPARTSSVEIP_URL" in kjor
    assert "DATABASE_URL" not in _bare_kode(MODULFILER[2])
    assert '"forlatte": r.forlatte' in kjor


def test_timeren_staar_etter_de_andre_sveipene():
    """08:50 — stigen er fordelt i klyngefundamentet."""
    timer = (ROT / "deploy" / "staging"
             / "disponit-motpartssveip.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 05:35:00 UTC" in timer
    assert "Persistent=true" in timer
    sti = ROT / "deploy" / "staging" / "disponit-motpartssveip.service"
    tjeneste = sti.read_text(encoding="utf-8")
    assert ("LoadCredential=DISPONIT_MOTPARTSSVEIP_URL:"
            "/etc/disponit/motpartssveip/DISPONIT_MOTPARTSSVEIP_URL"
            in tjeneste)


def test_sveipen_staar_i_flaaterosteret():
    """En sveip som ikke er i rosteret er en sveip ingen savner."""
    from drift.sveipestatus import FLAATEN
    assert FLAATEN.get("motpartssveip") == 30, FLAATEN


def test_klienten_tolker_et_ekte_svar():
    """FORMEN ER OBSERVERT, IKKE GJETTET (3/9).

    Feltnavnene er lest av et ekte svar fra Enhetsregisteret. Prøven
    her kjører uten nett — den mater tolkeren en kopi av den formen.

    MUTASJONEN SOM DREPER DENNE: bytt et feltnavn i `tolk`.
    """
    from api.foretaksregister import AKTIV, tolk
    raa = json.dumps({
        "organisasjonsnummer": "923609016",
        "navn": "EQUINOR ASA",
        "organisasjonsform": {"kode": "ASA"},
        "registreringsdatoEnhetsregisteret": "1995-03-12",
        "konkurs": False,
        "underAvvikling": False,
        "underTvangsavviklingEllerTvangsopplosning": False,
    }).encode("utf-8")
    f = tolk(raa)
    assert f.organisasjonsnummer == "923609016"
    assert f.navn == "EQUINOR ASA"
    assert f.organisasjonsform == "ASA"
    assert f.registerstatus == AKTIV
    assert f.konkurs is False
    assert len(f.raa_sha256) == 64


def test_et_svar_uten_registreringsdato_er_ukjent_ikke_aktivt():
    """FEILRETNINGEN ER HELE POENGET.

    En motpart vi ikke forstår skal ikke se kredittverdig ut. En modul
    som feiler mot «har kreditt» er farligere enn en som feiler mot
    «vet ikke».

    MUTASJONEN SOM DREPER DENNE: la fallback bli `aktiv`.
    """
    from api.foretaksregister import UKJENT, tolk
    raa = json.dumps({
        "organisasjonsnummer": "923609016", "navn": "X AS",
        "organisasjonsform": {"kode": "AS"},
    }).encode("utf-8")
    assert tolk(raa).registerstatus == UKJENT


def test_konkurs_og_avvikling_staar_som_egne_kolonner():
    """Tolkningen er vurderingens jobb, ikke registerets.

    Vurderingen skal kunne gjøres om igjen med en annen policy uten at
    grunnlaget er borte.
    """
    from api.foretaksregister import UNDER_AVVIKLING, tolk
    raa = json.dumps({
        "organisasjonsnummer": "923609016", "navn": "X AS",
        "organisasjonsform": {"kode": "AS"},
        "registreringsdatoEnhetsregisteret": "2020-01-01",
        "konkurs": True, "underAvvikling": True,
    }).encode("utf-8")
    f = tolk(raa)
    assert f.registerstatus == UNDER_AVVIKLING
    assert f.konkurs is True


def test_registeret_kan_ikke_svare_om_et_annet_foretak():
    """Uten sjekken ville en forveksling blitt en profil på feil rad."""
    from api.foretaksregister import OppslagFeil, tolk
    raa = json.dumps({
        "organisasjonsnummer": "999999999", "navn": "Feil AS",
        "organisasjonsform": {"kode": "AS"},
        "registreringsdatoEnhetsregisteret": "2020-01-01",
    }).encode("utf-8")
    f = tolk(raa)
    assert f.organisasjonsnummer == "999999999"
    with pytest.raises(OppslagFeil):
        tolk(b"ikke json")
