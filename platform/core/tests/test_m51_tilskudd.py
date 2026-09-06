"""M-51 tilskudds- og støtteordningsvakt v1 (119) — ESTIMATET, IKKE
SØKNADEN.

Grensen `m51-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

DEN SKARPESTE PORTEN ER `estimat_uten_forutsetninger`, og den måler en
NEKT i døra, ikke en sjekk i flaten: et estimat kan ikke ferdigstilles
uten minst én forutsetning. Et estimat uten forutsetninger ER en
lovnad — ingenting sier hva tallet hviler på.

DEN NEST SKARPESTE er `belop_uten_kildepost`, og den måler et FRAVÆR:
`tilskuddsestimat` har ingen beløpskolonne. Summen er summen av
`estimatpost`-rader som hver peker på en `kildepost` gjennom en NOT
NULL fremmednøkkel.

HVORFOR SKILLET BETYR NOE: et tilskuddsestimat er et TALL EN BEDRIFT
PLANLEGGER ETTER. Sier vi «dere kan få 400 000», og bedriften ansetter
på det grunnlaget, er avstanden mellom estimat og lovnad ikke
akademisk — den er lønnsutbetalinger.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import ast
import datetime
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

TILSKUDDSSVEIP_DSN = os.environ.get("DISPONIT_TEST_TILSKUDDSSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "119_m51_tilskuddsregister.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "tilskudd.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "tilskudd.py",
    ROT / "platform" / "drift" / "tilskuddssveip.py",
    ROT / "platform" / "drift" / "kjor_tilskuddssveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("tilskuddskrav", "stotteordning", "kildepost",
        "tilskuddsestimat", "estimatpost", "estimatforutsetning",
        "tilskuddsfunn")

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
    return koble(TILSKUDDSSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m51-{merke}-{secrets.token_hex(4)}"


def _krav(c, tenant, *, frist=21, kilde=400, usikkerhet=20,
          aktor="u-test", nokkel=None):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m51_sett_krav(%s,%s,%s,%s,%s,%s)",
                  (tenant, frist, kilde, usikkerhet, aktor,
                   nokkel or secrets.token_hex(8))).fetchone()[0]
    c.commit()
    return v


def _ordning(c, tenant, *, kode=None, navn="Skattefunn",
             forvalter="Forskningsradet", versjon=None,
             maks=None, sats=None, dager_til_frist=30, oid=None,
             aktor="u-test"):
    oid = oid or uuid.uuid4()
    kode = kode or ("ORD-" + secrets.token_hex(3))
    versjon = versjon or ("v" + secrets.token_hex(3))
    frist = (datetime.datetime.now(datetime.timezone.utc)
             + datetime.timedelta(days=dager_til_frist))
    _sett_kontekst(c, tenant)
    c.execute("SELECT m51_registrer_ordning("
              "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (tenant, oid, kode, navn, forvalter, versjon,
               secrets.token_hex(32), maks, sats, frist, aktor))
    c.commit()
    return oid


def _kildepost(c, tenant, *, system="lonn", ref=None,
               beskrivelse="Utvikler, 1200 timer", belop=90_000_000,
               fra="2026-01-01", til="2026-06-30", kid=None,
               aktor="u-test"):
    kid = kid or uuid.uuid4()
    ref = ref or ("KP-" + secrets.token_hex(4))
    _sett_kontekst(c, tenant)
    c.execute("SELECT m51_registrer_kildepost("
              "%s,%s,%s,%s,%s,%s,%s::date,%s::date,%s)",
              (tenant, kid, system, ref, beskrivelse, belop, fra, til,
               aktor))
    c.commit()
    return kid


def _estimat(c, tenant, oid, *, fra="2026-01-01", til="2026-06-30",
             eid=None, aktor="u-test"):
    eid = eid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m51_opprett_estimat("
                  "%s,%s,%s,%s::date,%s::date,%s)",
                  (tenant, eid, oid, fra, til, aktor)).fetchone()[0]
    c.commit()
    return eid, v


def _post(c, tenant, eid, kid, *, andel=36_000_000,
          begrunnelse="19 % av lonnskostnaden", pid=None,
          aktor="u-test"):
    pid = pid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute("SELECT m51_legg_til_post(%s,%s,%s,%s,%s,%s,%s)",
              (tenant, pid, eid, kid, andel, begrunnelse, aktor))
    c.commit()
    return pid


def _forutsetning(c, tenant, eid, *, art="regnskapstall",
                  tekst="Timetallet er ikke revidert",
                  konsekvens="Reduseres proporsjonalt", fid=None,
                  aktor="u-test"):
    fid = fid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute("SELECT m51_legg_til_forutsetning("
              "%s,%s,%s,%s,%s,%s,%s)",
              (tenant, fid, eid, art, tekst, konsekvens, aktor))
    c.commit()
    return fid


def _ferdigstill(c, tenant, eid, *, aktor="u-test"):
    _sett_kontekst(c, tenant)
    rad = c.execute(
        "SELECT * FROM m51_ferdigstill_estimat(%s,%s,%s)",
        (tenant, eid, aktor)).fetchone()
    c.commit()
    return rad


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m51_sveip_tilskudd(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT funntype, over_grense, detalj, sum_ore, apen"
        "  FROM tilskuddsfunn WHERE tenant=%s ORDER BY funntype",
        (tenant,)).fetchall()
    m.rollback()
    return rader


# --------------------------------------------------------------------
# §0: hver invariant i `m51-v1` har minst én port.
# --------------------------------------------------------------------

def test_grensen_er_registrert_og_hver_invariant_har_en_port():
    """§0-regelen, målt."""
    from manifestskjema import KRAVGRENSER
    inv = set(KRAVGRENSER["m51-v1"]["invarianter"])
    assert inv
    tekst = Path(__file__).read_text(encoding="utf-8")
    mangler = sorted(i for i in inv if i not in tekst)
    assert mangler == [], f"invarianter uten port: {mangler}"


def test_estimatet_har_ingen_belopskolonne():
    """`belop_uten_kildepost` — DEN MÅLER ET FRAVÆR.

    Hadde `tilskuddsestimat` båret et beløp alene, ville invarianten
    vært en regel noen måtte huske ved hver ny skrivevei — og et
    estimat med et fritt tall ser like ferdig ut som ett bygget av
    kilder.

    MUTASJONEN SOM DREPER DENNE: legg til `belop_ore` på estimatet,
    eller gjør `kildepost_id` nullbar.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    est = sql[sql.index("CREATE TABLE tilskuddsestimat"):
              sql.index("CREATE INDEX tilskuddsestimat_ordning")]
    for forbudt in ("belop_ore", "sum_ore", "totalt"):
        assert forbudt not in est, forbudt
    post = sql[sql.index("CREATE TABLE estimatpost"):
               sql.index("CREATE INDEX estimatpost_estimat")]
    assert "kildepost_id UUID NOT NULL" in post
    assert "estimatpost_kilde_fk" in post
    assert "REFERENCES kildepost" in post


def test_ingen_kolonne_og_ingen_dor_sender_soknad():
    """`modulen_sendte_soknad` — FRAVÆRET er porten."""
    sql = _bare_kode(MIGRASJON).lower()
    for forbudt in ("sendt", "m51_send", "innsend", "levert"):
        assert forbudt not in sql, forbudt
    api = _bare_kode(MODULFILER[0]).lower()
    for forbudt in ("send_soknad", "innsend", "m51_send"):
        assert forbudt not in api, forbudt
    js = _bare_kode(FLATE, uten_strenger=True).lower()
    for forbudt in ("sendsoknad", "send_soknad", "innsend"):
        assert forbudt not in js, forbudt
    assert "klar_til_gjennomgang" in _bare_kode(MIGRASJON)


def test_modulen_henter_ingen_ordning():
    """`modulen_hentet_eksternt` — M-48 fikk klyngens ene unntak.

    Et regelverk som endres gjør gårsdagens estimat feil uten at noe i
    systemet vet det.
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
    for forbudt in ("XMLHttpRequest", "WebSocket", "sendBeacon",
                    "EventSource"):
        assert forbudt not in js, forbudt


def test_modulen_signerer_ingen_attestasjon():
    """`modulen_signerte_attestasjon` — v1 attesterer ingenting."""
    for fil in list(MODULFILER) + [MIGRASJON]:
        kode = _bare_kode(fil, uten_strenger=True).lower()
        for forbudt in ("attester", "signer", "attestasjon"):
            assert forbudt not in kode, f"{fil.name}: {forbudt}"


def test_belop_er_heltall_ore_overalt():
    """`belop_i_flyttall` — den mest banale invarianten, og den
    dyreste å bryte.

    Et estimat regnet i flyttall gir en bedrift et tall som ikke
    stemmer med regnskapet de søker på grunnlag av, og avviket dukker
    opp først når forvalteren kontrollregner.

    MUTASJONEN SOM DREPER DENNE: bytt en BIGINT til NUMERIC, eller
    regn spennet med `::float`.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    for kolonne in ("belop_ore BIGINT", "andel_ore BIGINT",
                    "maks_belop_ore BIGINT", "sum_ore BIGINT"):
        assert kolonne in sql, kolonne
    # KODEN, IKKE KOMMENTARENE. Ordet «NUMERIC» står i kommentaren som
    # forklarer HVORFOR `sum()` må castes — `sum()` på BIGINT gir
    # numeric i PostgreSQL, og uten `::bigint` bryter funksjonen sin
    # egen RETURNS TABLE-kontrakt. En port som lette i kommentarer
    # ville straffet begrunnelsen og ikke handlingen.
    kode = _bare_kode(MIGRASJON).upper()
    for forbudt in ("NUMERIC", "DOUBLE PRECISION", "::FLOAT",
                    "::REAL"):
        assert forbudt not in kode, forbudt
    # …og hver `sum()` på et beløp er castet tilbake til bigint.
    import re as _re
    ucastet = _re.findall(r"sum\(\w+\.andel_ore\), 0\)(?!::bigint)",
                          kode.lower())
    assert not ucastet, ucastet
    # SPENNET REGNES I HELTALL: `(sum * prosent) / 100` på BIGINT.
    assert "(v_sum * v_usikkerhet) / 100" in sql
    # …og API-et slipper ingen flyttall inn.
    api = MODULFILER[0].read_text(encoding="utf-8")
    assert "isinstance(verdi, bool)" in api
    assert "isinstance(verdi, int)" in api
    # Flaten regner med BigInt og strenger, ikke divisjon.
    #
    # KODEN, IKKE KOMMENTARENE: `Math.floor(ore / 100)` står i
    # kommentaren som FORKLARER hvorfor mønsteret er unngått (118s
    # rundturtap). En port som lette i kommentarer ville straffet
    # begrunnelsen og ikke handlingen.
    js = FLATE.read_text(encoding="utf-8")
    assert "BigInt(" in js
    kode = _bare_kode(FLATE, uten_strenger=True)
    assert "Math.floor(ore / 100)" not in kode
    assert "Math.round(" not in kode


def test_ordningens_krav_ligger_paa_ordningsraden():
    """`ordningskrav_hardkodet` — frister, satser og tak er ikke
    modulens.

    MUTASJONEN SOM DREPER DENNE: sett en standardsats i Python.
    """
    sveip = _bare_kode(MODULFILER[1])
    for forbudt in ("MAKS_BELOP", "SATS", "FRIST_VARSEL",
                    "USIKKERHET"):
        assert forbudt not in sveip, forbudt
    api = MODULFILER[0].read_text(encoding="utf-8")
    for forbudt in ("sats_prosent = 19", "STANDARD_SATS",
                    "DEFAULT_MAKS"):
        assert forbudt not in api, forbudt
    sql = MIGRASJON.read_text(encoding="utf-8")
    ordning = sql[sql.index("CREATE TABLE stotteordning"):
                  sql.index("CREATE INDEX stotteordning_aktive_frist")]
    for kolonne in ("maks_belop_ore", "sats_prosent", "soknadsfrist",
                    "regelverksversjon"):
        assert kolonne in ordning, kolonne


# --------------------------------------------------------------------
# Databaseportene.
# --------------------------------------------------------------------

@pg
def test_estimat_uten_forutsetninger_kan_ikke_ferdigstilles(miljo):
    """`estimat_uten_forutsetninger` — DEN SKARPESTE PORTEN.

    «Estimat presenteres som estimat MED FORUTSETNINGER, aldri som
    lovnad.» Et estimat uten forutsetninger ER en lovnad: ingenting
    sier hva tallet hviler på, og den som planlegger etter det kan
    ikke se når grunnlaget svikter.

    MUTASJONEN SOM DREPER DENNE: fjern sjekken i
    `m51_ferdigstill_estimat`.
    """
    tenant = _tenantnavn("forutsetning")
    with _rt() as c:
        _krav(c, tenant)
        oid = _ordning(c, tenant)
        kid = _kildepost(c, tenant)
        eid, _v = _estimat(c, tenant, oid)
        _post(c, tenant, eid, kid)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _ferdigstill(c, tenant, eid)
        assert "ingen forutsetninger" in str(e.value)
        assert "lovnad" in str(e.value)
        c.rollback()
        # …og med én forutsetning går det.
        _forutsetning(c, tenant, eid)
        rad = _ferdigstill(c, tenant, eid)
        assert rad[0] == 36_000_000
        c.rollback()


@pg
def test_estimat_uten_poster_kan_ikke_ferdigstilles(miljo):
    """Et tall uten noe bak ville vært null, og null ser ut som et
    svar."""
    tenant = _tenantnavn("poster")
    with _rt() as c:
        _krav(c, tenant)
        oid = _ordning(c, tenant)
        eid, _v = _estimat(c, tenant, oid)
        _forutsetning(c, tenant, eid)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _ferdigstill(c, tenant, eid)
        assert "ingen poster" in str(e.value)
        c.rollback()


@pg
def test_ferdigstilling_gir_summen_OG_spennet(miljo):
    """ETT TALL ER EN LOVNAD, ET INTERVALL ER ET ESTIMAT.

    Spennet regnes i HELTALL: `(sum * prosent) / 100` på BIGINT.

    MUTASJONEN SOM DREPER DENNE: returner bare summen, eller regn
    spennet i flyttall.
    """
    tenant = _tenantnavn("spenn")
    with _rt() as c:
        _krav(c, tenant, usikkerhet=20)
        oid = _ordning(c, tenant)
        kid = _kildepost(c, tenant, belop=90_000_000)
        eid, _v = _estimat(c, tenant, oid)
        _post(c, tenant, eid, kid, andel=36_000_000)
        _forutsetning(c, tenant, eid)
        sum_, nedre, ovre, poster, forutsetninger = _ferdigstill(
            c, tenant, eid)
        assert sum_ == 36_000_000
        assert nedre == 28_800_000     # 36 000 000 - 20 %
        assert ovre == 43_200_000      # 36 000 000 + 20 %
        assert (poster, forutsetninger) == (1, 1)
        # ALLE ER HELTALL — ingen flyttall har vært innom.
        for v in (sum_, nedre, ovre):
            assert isinstance(v, int), type(v)
        c.rollback()


@pg
def test_terskeldoera_er_idempotent_paa_nokkelen(miljo):
    """EN GJENSPILT POST SKAL IKKE BUMPE VERSJONEN.

    De andre skrivedørene utleder rad-id-en fra idempotensnøkkelen og
    er idempotente fordi en gjentatt id ikke kan settes inn to ganger.
    `tilskuddskrav` er en SINGLETON per tenant og har ingen slik id —
    så uten nøkkelen inne i døra ville en klient som gjentar etter en
    tidsavbrutt forbindelse økt `versjon` en gang til uten at noe var
    endret. Og versjonen er ikke pynt: hvert funn bærer
    `kravversjon`, så et fantomtall gjør «hvilke terskler gjaldt da»
    til et spørsmål ingen kan svare på.

    SAMME NØKKEL MED ANDRE VERDIER ER NOE ANNET, og må si fra:
    nøkkelen er klientens løfte om at dette er den samme
    operasjonen. Er verdiene andre, er løftet brutt, og å velge én av
    dem i stillhet er verre enn en konflikt.

    MUTASJONEN SOM DREPER DENNE: la døra ignorere `p_nokkel`.
    """
    tenant = _tenantnavn("idem")
    nokkel = secrets.token_hex(8)
    with _rt() as c:
        v1 = _krav(c, tenant, usikkerhet=20, nokkel=nokkel)
        # GJENSPILL: samme nøkkel, samme verdier.
        v2 = _krav(c, tenant, usikkerhet=20, nokkel=nokkel)
        assert v1 == v2, (v1, v2)

        # SAMME NØKKEL, ANDRE VERDIER → konflikt, ikke stille valg.
        with pytest.raises(psycopg.errors.UniqueViolation):
            _krav(c, tenant, usikkerhet=35, nokkel=nokkel)
        c.rollback()

        # NY NØKKEL BUMPER, som den skal.
        v3 = _krav(c, tenant, usikkerhet=35)
        assert v3 == v1 + 1, (v1, v3)
        # …og verdien FULGTE MED. Lest gjennom lesedøra: kjøretids-
        # rollen har ingen tabellrettigheter, og skal ikke ha dem.
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT * FROM m51_kravene(%s)",
                         (tenant,)).fetchone()[2] == 35
        c.rollback()


@pg
def test_sammendraget_teller_hver_ordning_en_gang(miljo):
    """SUMMEN I SAMMENDRAGET ER TALLET EN BEDRIFT PLANLEGGER ETTER.

    Estimatene er versjonerte og append-only, og et nytt estimat kan
    lages MENS det forrige står som klart. En ordning kan derfor ha
    både v1 og v2 med `klar_til_gjennomgang`, og en sum som la sammen
    begge ville vist penger ordningen ikke kan gi — dobbelt så mye,
    på det ene tallet som må stemme.

    `m51_ordningene` viser NYESTE versjon per ordning. Sammendraget
    må bruke samme utvalg, ellers sier de to tallene på samme skjerm
    forskjellige ting.

    MUTASJONEN SOM DREPER DENNE: summér alle klare estimater i stedet
    for det nyeste per ordning.
    """
    tenant = _tenantnavn("dobbelt")
    with _rt() as c:
        _krav(c, tenant, usikkerhet=20)
        oid = _ordning(c, tenant)
        kid = _kildepost(c, tenant, belop=90_000_000)
        # v1: ferdigstilt paa 36 000 000.
        e1, v1 = _estimat(c, tenant, oid)
        _post(c, tenant, e1, kid, andel=36_000_000)
        _forutsetning(c, tenant, e1)
        _ferdigstill(c, tenant, e1)
        # v2: ny versjon paa samme ordning, ogsaa ferdigstilt.
        e2, v2 = _estimat(c, tenant, oid)
        assert v2 > v1
        _post(c, tenant, e2, kid, andel=30_000_000)
        _forutsetning(c, tenant, e2)
        _ferdigstill(c, tenant, e2)

        _sett_kontekst(c, tenant)
        rad = c.execute("SELECT * FROM m51_tilskuddsstatus(%s)",
                        (tenant,)).fetchone()
        klare, sum_klare = rad[3], rad[4]
        # ÉN ordning, ETT klart estimat i sammendraget — det nyeste.
        assert klare == 1, klare
        assert sum_klare == 30_000_000, sum_klare

        # …og listen sier det samme, som den maa.
        assert c.execute(
            "SELECT sum_ore, klar FROM m51_ordningene(%s, 500)",
            (tenant,)).fetchall() == [(30_000_000, True)]
        c.rollback()


@pg
def test_andel_kan_ikke_overstige_kildeposten(miljo):
    """Å telle med mer enn det som står i regnskapet er ikke et
    estimat — det er feilen som gjør en tilskuddssak til en
    tilbakebetalingssak."""
    tenant = _tenantnavn("andel")
    with _rt() as c:
        _krav(c, tenant)
        oid = _ordning(c, tenant)
        kid = _kildepost(c, tenant, belop=90_000_000)
        eid, _v = _estimat(c, tenant, oid)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _post(c, tenant, eid, kid, andel=95_000_000)
        assert "større enn kildeposten" in str(e.value)
        c.rollback()


@pg
def test_kildeposten_maa_overlappe_estimatets_periode(miljo):
    """Et beløp fra en annen periode kan telles i to søknader."""
    tenant = _tenantnavn("periode")
    with _rt() as c:
        _krav(c, tenant)
        oid = _ordning(c, tenant)
        kid = _kildepost(c, tenant, fra="2025-01-01",
                         til="2025-06-30")
        eid, _v = _estimat(c, tenant, oid, fra="2026-01-01",
                           til="2026-06-30")
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _post(c, tenant, eid, kid)
        assert "utenfor estimatets periode" in str(e.value)
        c.rollback()


@pg
def test_en_kildepost_kan_ikke_telles_to_ganger(miljo):
    """Dobbelttelling er feilen som gjør en tilskuddssak til en
    tilbakebetalingssak."""
    tenant = _tenantnavn("dobbel")
    with _rt() as c:
        _krav(c, tenant)
        oid = _ordning(c, tenant)
        kid = _kildepost(c, tenant)
        eid, _v = _estimat(c, tenant, oid)
        _post(c, tenant, eid, kid, andel=10_000_000)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _post(c, tenant, eid, kid, andel=5_000_000)
        c.rollback()


@pg
def test_en_kildepost_fra_framtida_avvises(miljo):
    """Et tall fra en periode som ikke er over er et anslag, ikke en
    kilde."""
    tenant = _tenantnavn("framtid")
    i_morgen = (datetime.date.today()
                + datetime.timedelta(days=1)).isoformat()
    with _rt() as c:
        _krav(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _kildepost(c, tenant, fra="2026-01-01", til=i_morgen)
        assert "ikke er over" in str(e.value)
        c.rollback()


@pg
def test_et_ferdigstilt_estimat_er_frosset(miljo):
    """En ny post hører til et NYTT estimat.

    TO GJERDER: døra gir den ærlige feilmeldingen, radvakten stanser
    den som går utenom.
    """
    tenant = _tenantnavn("frys")
    with _rt() as c:
        _krav(c, tenant)
        oid = _ordning(c, tenant)
        k1 = _kildepost(c, tenant)
        k2 = _kildepost(c, tenant, belop=10_000_000)
        eid, _v = _estimat(c, tenant, oid)
        _post(c, tenant, eid, k1)
        _forutsetning(c, tenant, eid)
        _ferdigstill(c, tenant, eid)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _post(c, tenant, eid, k2, andel=1_000_000)
        assert "merket klart" in str(e.value)
        c.rollback()
    with psycopg.connect(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        m.execute("SET ROLE disponit_tilskudd_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as e:
            m.execute("UPDATE tilskuddsestimat SET klar_av='noen'"
                      " WHERE tenant=%s", (tenant,))
        assert "frosset" in str(e.value)
        m.rollback()


@pg
def test_historikken_kan_ikke_overskrives(miljo):
    """`tilskuddshistorikk_overskrevet` — append-only, håndhevet."""
    tenant = _tenantnavn("append")
    with _rt() as c:
        _krav(c, tenant)
        oid = _ordning(c, tenant)
        kid = _kildepost(c, tenant)
        eid, _v = _estimat(c, tenant, oid)
        _post(c, tenant, eid, kid)
        _forutsetning(c, tenant, eid)
        c.rollback()
    for tabell in ("kildepost", "estimatpost",
                   "estimatforutsetning"):
        # EGEN FORBINDELSE PER TABELL: `rollback()` tilbakestiller
        # `SET ROLE` (116s lærdom).
        with psycopg.connect(MIGRATOR_DSN) as m:
            _sett_kontekst(m, tenant)
            m.execute("SET ROLE disponit_tilskudd_eier")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                m.execute(f"UPDATE {tabell} SET tenant=tenant"
                          " WHERE tenant=%s", (tenant,))
            m.rollback()


@pg
def test_ordningens_regelverk_er_frosset_men_aktiv_kan_settes(miljo):
    """EN SNEVER GRANT PÅ KOLONNEN, ikke en radvakt som gjetter.

    Eieren kan endre aktivflagget og ingenting annet, så
    regelverksversjonen og fristen er frosset uten at en trigger må
    liste opp hvilke felter som er lov.
    """
    tenant = _tenantnavn("kolonne")
    with _rt() as c:
        _krav(c, tenant)
        oid = _ordning(c, tenant)
        c.rollback()
    with psycopg.connect(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        m.execute("SET ROLE disponit_tilskudd_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            m.execute("UPDATE stotteordning SET regelverksversjon='x'"
                      " WHERE tenant=%s", (tenant,))
        m.rollback()
    # …men døra kan sette `aktiv`.
    with _rt() as c:
        _sett_kontekst(c, tenant)
        c.execute("SELECT m51_sett_ordningaktiv(%s,%s,%s,%s)",
                  (tenant, oid, False, "u-test"))
        c.commit()
        _sett_kontekst(c, tenant)
        aktiv = c.execute(
            "SELECT aktiv FROM m51_ordningene(%s,%s) LIMIT 1",
            (tenant, 10)).fetchone()[0]
        assert aktiv is False
        c.rollback()


@pg
def test_tenantisolasjon(miljo):
    """`tenantlekkasje_i_tilskuddsregister` — RLS + krev_tenantkontekst."""
    a, b = _tenantnavn("iso-a"), _tenantnavn("iso-b")
    with _rt() as c:
        _krav(c, a)
        _krav(c, b)
        _ordning(c, a, navn="A-ordning")
        _ordning(c, b, navn="B-ordning")
        _sett_kontekst(c, a)
        with pytest.raises(psycopg.Error):
            c.execute("SELECT * FROM m51_ordningene(%s,%s)", (b, 10))
        c.rollback()
        _sett_kontekst(c, a)
        navn = [r[2] for r in c.execute(
            "SELECT * FROM m51_ordningene(%s,%s)", (a, 50)).fetchall()]
        assert navn == ["A-ordning"], navn
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
    """SP-7, målt med `has_table_privilege`."""
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
            " 'm51_sveip_tilskudd(int)', 'EXECUTE')").fetchone()[0] \
            is False
        assert m.execute(
            "SELECT has_function_privilege('disponit_tilskuddssveip',"
            " 'm51_sveip_tilskudd(int)', 'EXECUTE')").fetchone()[0] \
            is True
        for tabell in EGNE:
            assert m.execute(
                "SELECT has_table_privilege("
                "'disponit_tilskuddssveip', %s, 'SELECT')",
                (tabell,)).fetchone()[0] is False
        m.rollback()


@pg
def test_sveipen_ser_alle_tenanter(miljo):
    """DEN LATE MARKØREN, målt (112s lærdom, gjentatt i 116–118)."""
    a, b, c_ = (_tenantnavn("sv-a"), _tenantnavn("sv-b"),
                _tenantnavn("sv-c"))
    with _rt() as c:
        for t in (a, b, c_):
            _ordning(c, t)      # ingen krav -> `ingen_krav`
        c.rollback()
    with _sv() as v:
        rad = _sveip(v)
    assert rad[0] >= 3, rad
    with psycopg.connect(MIGRATOR_DSN) as m:
        for t in (a, b, c_):
            typer = [r[0] for r in _funn(m, t)]
            assert "ingen_krav" in typer, (t, typer)


@pg
def test_over_ordningens_tak_baerer_summen_og_kan_ikke_lukkes(miljo):
    """«OVER TAKET» UTEN Å SI HVOR MYE ER EN BESKJED MAN IKKE KAN
    HANDLE PÅ.

    Og funnet kan ikke lukkes bort — samme dom som M-46s udekkede
    absolutte krav (118) og M-49s bekreftede treff (117).
    """
    tenant = _tenantnavn("tak")
    with _rt() as c:
        _krav(c, tenant)
        oid = _ordning(c, tenant, maks=10_000_000)
        kid = _kildepost(c, tenant, belop=50_000_000)
        eid, _v = _estimat(c, tenant, oid)
        _post(c, tenant, eid, kid, andel=30_000_000)
        c.rollback()
    with _sv() as v:
        _sveip(v)
    with psycopg.connect(MIGRATOR_DSN) as m:
        rader = {r[0]: r for r in _funn(m, tenant)}
        assert "estimat_over_ordningstak" in rader, rader
        # SUMMEN STÅR PÅ FUNNET.
        assert rader["estimat_over_ordningstak"][3] == 30_000_000
    with _rt() as c:
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as e:
            c.execute("SELECT m51_lukk_funn(%s,%s,%s,%s,%s)",
                      (tenant, oid, "estimat_over_ordningstak",
                       "vil bli ferdig", "u-test"))
        assert "kan ikke lukkes bort" in str(e.value)
        c.rollback()


@pg
def test_evidenskjeden_baerer_hvert_steg(miljo):
    """En tilskuddssak kontrolleres i ettertid."""
    tenant = _tenantnavn("evidens")
    with _rt() as c:
        _krav(c, tenant)
        oid = _ordning(c, tenant)
        kid = _kildepost(c, tenant)
        eid, _v = _estimat(c, tenant, oid)
        _post(c, tenant, eid, kid)
        _forutsetning(c, tenant, eid)
        _ferdigstill(c, tenant, eid)
        c.rollback()
    with psycopg.connect(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        handlinger = {r[0] for r in m.execute(
            "SELECT handling FROM revisjonslogg"
            " WHERE tenant=%s AND kilde='m51_tilskudd'",
            (tenant,)).fetchall()}
        for h in ("stotteordning_registrert", "kildepost_registrert",
                  "tilskuddsestimat_opprettet", "estimatpost_lagt_til",
                  "estimatforutsetning_lagt_til",
                  "estimat_ferdigstilt"):
            assert h in handlinger, (h, handlinger)
        m.rollback()


# --------------------------------------------------------------------
# API- og flateportene.
# --------------------------------------------------------------------

def test_rutene_er_registrert_med_riktig_scope():
    """LESING `okonomi:read`, SKRIVING `bestilling:opprett`."""
    from api.app import RUTESCOPE
    forventet = {
        ("GET", "/v1/tilskudd"): "okonomi:read",
        ("GET", "/v1/tilskudd/funn"): "okonomi:read",
        ("GET", "/v1/tilskudd/kildeposter"): "okonomi:read",
        ("GET", "/v1/tilskudd/estimat/{estimat_id:uuid}/poster"):
            "okonomi:read",
        ("GET",
         "/v1/tilskudd/estimat/{estimat_id:uuid}/forutsetninger"):
            "okonomi:read",
        ("GET", "/v1/tilskudd/{ordning_id:uuid}/estimater"):
            "okonomi:read",
        ("POST", "/v1/tilskudd/krav"): "bestilling:opprett",
        ("POST", "/v1/tilskudd/ordning"): "bestilling:opprett",
        ("POST", "/v1/tilskudd/kildepost"): "bestilling:opprett",
        ("POST", "/v1/tilskudd/estimat/{estimat_id:uuid}/post"):
            "bestilling:opprett",
        ("POST",
         "/v1/tilskudd/estimat/{estimat_id:uuid}/forutsetning"):
            "bestilling:opprett",
        ("POST",
         "/v1/tilskudd/estimat/{estimat_id:uuid}/ferdigstill"):
            "bestilling:opprett",
        ("POST", "/v1/tilskudd/{ordning_id:uuid}/estimat"):
            "bestilling:opprett",
        ("POST", "/v1/tilskudd/{ordning_id:uuid}/aktiv"):
            "bestilling:opprett",
        ("POST", "/v1/tilskudd/{ordning_id:uuid}/funn/lukk"):
            "bestilling:opprett",
    }
    for nokkel, scope in forventet.items():
        assert RUTESCOPE.get(nokkel) == scope, nokkel


def test_ingen_rute_sender_en_soknad():
    """`modulen_sendte_soknad`, sett fra rutetabellen."""
    from api.app import RUTESCOPE
    for metode, sti in RUTESCOPE:
        if sti.startswith("/v1/tilskudd"):
            for forbudt in ("send", "innsend", "lever", "soknad"):
                assert forbudt not in sti, (metode, sti)
    assert ("POST",
            "/v1/tilskudd/estimat/{estimat_id:uuid}/ferdigstill") \
        in RUTESCOPE


def test_postruten_krever_alltid_en_kildepost():
    """API-et har ingen vei til et beløp uten kilde — fordi kolonnen
    ikke finnes."""
    kilde = MODULFILER[0].read_text(encoding="utf-8")
    start = kilde.index("def legg_til_post_endepunkt(")
    krop = kilde[start:kilde.index("\ndef ", start + 10)]
    assert '_kropp_uuid(kropp, "kildepost_id", rid)' in krop
    assert '_ore(kropp, "andel_ore", rid)' in krop
    for forbudt in ("kildepost_id or None", "belop_ore"):
        assert forbudt not in krop, forbudt


def test_ferdigstillruten_returnerer_spennet():
    """Den som ferdigstiller skal se hva estimatet faktisk sier."""
    kilde = MODULFILER[0].read_text(encoding="utf-8")
    start = kilde.index("def ferdigstill_endepunkt(")
    krop = kilde[start:kilde.index("\ndef ", start + 10)]
    for felt in ('"sum_ore"', '"nedre_ore"', '"ovre_ore"',
                 '"antall_forutsetninger"'):
        assert felt in krop, felt


def test_flaten_viser_aldri_summen_alene():
    """ETT TALL ER EN LOVNAD, ET INTERVALL ER ET ESTIMAT.

    MUTASJONEN SOM DREPER DENNE: la `estimatTekst` returnere bare
    summen.
    """
    js = FLATE.read_text(encoding="utf-8")
    assert "export function estimatTekst(rad)" in js
    assert 't("ui.tilskudd.sum_med_spenn")' in js
    for sprak in ("nb", "en"):
        d = json.loads((ROT / "locales" / f"{sprak}.json").read_text(
            encoding="utf-8"))
        mal = d["ui.tilskudd.sum_med_spenn"]
        for plass in ("{sum}", "{nedre}", "{ovre}"):
            assert plass in mal, (sprak, mal)


def test_flaten_sier_hva_modulen_ikke_gjor():
    """En bruker som TROR tallet er en lovnad er farligere stilt enn
    en som vet at det er et estimat."""
    js = FLATE.read_text(encoding="utf-8")
    assert 't("ui.tilskudd.oversikt.hvorfor")' in js
    assert 't("ui.tilskudd.ferdigstill_hjelp")' in js
    for sprak in ("nb", "en"):
        d = json.loads((ROT / "locales" / f"{sprak}.json").read_text(
            encoding="utf-8"))
        h = d["ui.tilskudd.oversikt.hvorfor"].lower()
        assert ("sender ingen søknad" in h
                or "submits no application" in h), h
        f = d["ui.tilskudd.ferdigstill_hjelp"].lower()
        assert ("sender ingen søknad" in f
                or "submits no application" in f), f


def test_flaten_tilbyr_bare_ferske_kildeposter():
    """En knapp som alltid feiler er verre enn en valgmulighet som
    ikke finnes."""
    js = FLATE.read_text(encoding="utf-8")
    assert "kildeposter.filter((k) => k.fersk === true)" in js
    assert 't("ui.tilskudd.post.ingen_ferske_kilder")' in js


def test_flaten_sier_fra_naar_forutsetninger_mangler():
    """Fraværet er grunnen til at estimatet ikke kan ferdigstilles,
    ikke en tom tabell."""
    js = FLATE.read_text(encoding="utf-8")
    assert 't("ui.tilskudd.uten_forutsetninger_varsel")' in js
    assert 'role: "alert"' in js


def test_sveipen_leser_fire_felt_og_ikke_flere():
    """#358s lærdom."""
    sveip = MODULFILER[1].read_text(encoding="utf-8")
    assert "KONTRAKTFELT = 4" in sveip
    assert "rader[0][:KONTRAKTFELT]" in sveip


def test_kjoreskriptet_har_ingen_fallback_til_database_url():
    """Runtime-rollen har med vilje ikke EXECUTE på sveipen."""
    kjor = MODULFILER[2].read_text(encoding="utf-8")
    assert "DISPONIT_TILSKUDDSSVEIP_URL" in kjor
    assert "DATABASE_URL" not in _bare_kode(MODULFILER[2])


def test_timeren_staar_i_klyngestigen():
    """09:35 — stigen er fordelt i klyngefundamentet."""
    sti_t = (ROT / "deploy" / "staging"
             / "disponit-tilskuddssveip.timer")
    timer = sti_t.read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 05:50:00 UTC" in timer
    assert "Persistent=true" in timer
    sti_s = (ROT / "deploy" / "staging"
             / "disponit-tilskuddssveip.service")
    tjeneste = sti_s.read_text(encoding="utf-8")
    assert ("LoadCredential=DISPONIT_TILSKUDDSSVEIP_URL:"
            "/etc/disponit/tilskuddssveip/DISPONIT_TILSKUDDSSVEIP_URL"
            in tjeneste)
    # …og beskrivelsen navngir SIN EGEN jobb (arvefeilen fra 116–118).
    assert "tilskuddssveip" in tjeneste.split("Description=")[1][:70]
    for arvet in ("adresser", "uavklarte treff", "udekkede krav"):
        assert arvet not in tjeneste, arvet


def test_sveipen_staar_i_flaaterosteret():
    """En sveip som ikke er i rosteret er en sveip ingen savner."""
    from drift.sveipestatus import FLAATEN
    assert FLAATEN.get("tilskuddssveip") == 30, FLAATEN


def test_ui_axe_dekning():
    """`ui_axe_alvorlige_brudd` — flaten er registrert der axe kjører."""
    app_js = (ROT / "platform" / "core" / "ui" / "static" / "js"
              / "app.js").read_text(encoding="utf-8")
    assert "tilskudd: visTilskudd," in app_js
    sitekart = (ROT / "platform" / "core" / "ui" / "static" / "js"
                / "sitekart.js").read_text(encoding="utf-8")
    assert '{ nokkel: "tilskudd", scope: "okonomi:read"' in sitekart
