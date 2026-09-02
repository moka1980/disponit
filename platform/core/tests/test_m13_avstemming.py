"""M-13 bankavstemmingsagent v1 (migrasjon 101) — AVSTEMMINGSREGISTERET.

Grensen `m13-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR koden
(§0-regelen), og hver invariant der har minst én port her. Rekkefølgen i
fila følger grensen, så det er lett å se at ingen står uten måling.

DEN SKARPESTE PORTEN ER DEN FØRSTE: `postering_utenfor_registeret`.
Katalogteksten lover automatisk bokføring ved full match; v1 bokfører
ingenting. Det er ikke forsiktighet — en automatisk bokføring er en
skriving i regnskapet, og et regnskap som endres av noe ingen leste er
ikke et regnskap. Fraværet måles statisk (AST + datamodellen + rutene) og
funksjonelt (radantallet utenfor modulens egne tabeller er uendret etter
en sveip).

DEN NEST SKARPESTE er `belop_i_flyttall`. Et flyttall i en avstemming
viser seg først når summene ikke går opp, og da er det ikke lenger til å
finne ut av. Porten måler katalogen, ikke koden: hver beløpskolonne i
101 er `bigint`, ingen unntak.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import ast
import json
import os
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo)
from .test_m37 import _sett_kontekst

#: Sveiperollen. `m13_sveip_avstemming` er BARE hennes (kryss-tenant,
#: 038-reaperens snitt), så en test som kjører sveipen må koble som henne
#: — migratoren arver ingenting (`WITH INHERIT FALSE`) og web-runtime er
#: eksplisitt REVOKEt. CI setter variabelen.
AVSTEMMINGSVEIP_DSN = os.environ.get("DISPONIT_TEST_AVSTEMMINGSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "101_m13_avstemmingsregister.sql")
#: Modulens EGNE Python-filer. Dette er hele modulen i kode: API-et,
#: sveipearbeideren og inngangspunktet dens. Fraværet av en bokføringsvei
#: skal kunne måles på nøyaktig disse tre.
MODULFILER = (
    ROT / "platform" / "core" / "api" / "avstemming.py",
    ROT / "platform" / "drift" / "avstemmingssveip.py",
    ROT / "platform" / "drift" / "kjor_avstemmingssveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")


# ---------------------------------------------------------------------------
# Riggen
# ---------------------------------------------------------------------------

def _rt():
    """Runtime-rollen — den som HAR EXECUTE på dørene og ingen
    tabellrettighet på registeret."""
    from db.pg import koble
    return koble(DSN)


def _sv():
    """Sveiperollen — den som har EXECUTE på sveipen og ingenting
    annet."""
    from db.pg import koble
    return koble(AVSTEMMINGSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    """Egen tenant per test. Sveipen er kryss-tenant og ser HELE basen,
    så en delt tenant ville gjort testene rekkefølgeavhengige — og en
    test som består fordi naboen ryddet er ingen port."""
    return f"t-m13-{merke}-{secrets.token_hex(4)}"


def _konto(c, tenant, *, navn="Driftskonto", nummer=None, valuta="NOK",
           kid=None, aktor="u-test"):
    kid = kid or uuid.uuid4()
    nummer = nummer or ("1234" + secrets.token_hex(4).translate(
        str.maketrans("abcdef", "012345")))
    _sett_kontekst(c, tenant)
    c.execute("SELECT m13_registrer_konto(%s,%s,%s,%s,%s,%s)",
              (tenant, kid, navn, nummer, valuta, aktor))
    c.commit()
    return kid


def _post(c, tenant, konto, belop, *, ref=None, dager_siden=0,
          tekst="Innbetaling", motpart="Kunde AS", pid=None,
          aktor="u-test"):
    """Én bankpost gjennom døren. `dager_siden` gjør det mulig å skrive
    «bokført for 400 døgn siden» uten å regne på klokka."""
    pid = pid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    rad = c.execute(
        "SELECT * FROM m13_registrer_post(%s,%s,%s,%s,"
        "                                 current_date - %s::int,"
        "                                 %s,%s,%s,%s)",
        (tenant, pid, konto, ref or ("BANK-" + secrets.token_hex(4)),
         dager_siden, belop, tekst, motpart, aktor)).fetchone()
    c.commit()
    # DØREN RETURNERER (ny, post_id), og hjelperen gir tilbake id-en til
    # raden som FAKTISK står der — ikke den vi utledet.
    return rad[1]


def _bilag(c, tenant, belop, *, retning="inn", nummer=None,
           forfall_om=None, motpart="Kunde AS", bid=None, aktor="u-test"):
    """Ett bilag gjennom døren. `forfall_om` er ANTALL DØGN FRA I DAG —
    negativt for forfalt. `None` gir et bilag uten forfallsdato."""
    bid = bid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    if forfall_om is None:
        c.execute(
            "SELECT m13_registrer_bilag(%s,%s,%s,%s,%s,%s,"
            "                           current_date - 30,NULL,%s)",
            (tenant, bid, nummer or ("F-" + secrets.token_hex(4)), retning,
             belop, motpart, aktor))
    else:
        c.execute(
            "SELECT m13_registrer_bilag(%s,%s,%s,%s,%s,%s,"
            "                           current_date - 60,"
            "                           current_date + %s::int,%s)",
            (tenant, bid, nummer or ("F-" + secrets.token_hex(4)), retning,
             belop, motpart, forfall_om, aktor))
    c.commit()
    return bid


def _avstem(c, tenant, post, bilag, *, metode="manuell",
            begrunnelse="matchet manuelt", aid=None, aktor="u-test"):
    aid = aid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute("SELECT m13_avstem(%s,%s,%s,%s,%s,%s,%s)",
              (tenant, aid, post, bilag, metode, begrunnelse, aktor))
    c.commit()
    return aid


def _sveip(v, grense=500, dogn=30):
    """Kjør sveipen én gang. -> (tenanter, nye, oppdaterte, lukkede,
    avkortet).

    TALLENE ER PLATTFORMVIDE, ikke tenantens: sveipen er kryss-tenant per
    konstruksjon og ser hver bankpost i basen, også dem andre tester har
    lagt igjen. Assertene under teller derfor tenantens EGNE funn
    (`_funn`), ikke returverdien — en test som stolte på totalen ville
    vært rekkefølgeavhengig.
    """
    rad = v.execute("SELECT * FROM m13_sveip_avstemming(%s,%s)",
                    (grense, dogn)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant, *, bare_apne=True):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT objekttype, objekt_id, funntype, dogn_over_grense,"
        "       rest_ore, apen FROM avstemmingsfunn"
        " WHERE tenant=%s AND (%s IS FALSE OR apen)"
        " ORDER BY funntype, objekt_id", (tenant, bare_apne)).fetchall()
    m.rollback()
    return rader


def _status(c, tenant):
    _sett_kontekst(c, tenant)
    rad = c.execute("SELECT * FROM m13_avstemmingsstatus(%s)",
                    (tenant,)).fetchone()
    c.rollback()
    return rad


# ---------------------------------------------------------------------------
# INVARIANT 1: postering_utenfor_registeret — V1-DOMMEN
# ---------------------------------------------------------------------------

def test_invariant_postering_utenfor_registeret_statisk():
    """Katalogteksten lover automatisk bokføring ved full match. v1
    bokfører ingenting, og et regnskap som endres av noe ingen leste er
    ikke et regnskap.

    Porten er en AST-analyse, ikke et delstrengsøk: en import inne i en
    funksjon (som resten av huset bruker for late importer) ville sluppet
    unna et `startswith("import ")`, og det er nøyaktig formen en
    bokføringsvei ville hatt her — modulens egne importer ER late.

    MUTASJONEN SOM DREPER DENNE: legg `import httpx` inne i en funksjon i
    `api/avstemming.py`, eller la sveipearbeideren importere `api.ssrf`.
    """
    forbudt = {"http", "httpx", "requests", "urllib", "aiohttp", "socket",
               "smtplib", "email", "ftplib", "telnetlib", "webbrowser",
               "ssl", "asyncio"}
    for fil in MODULFILER:
        tre = ast.parse(fil.read_text(encoding="utf-8"))
        for node in ast.walk(tre):
            navn = []
            if isinstance(node, ast.Import):
                navn = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # Relativ import (`from . import avstemmingssveip`) har
                # `module=None` og er per definisjon intern.
                navn = [node.module or ""] if node.level == 0 else []
            for n in navn:
                rot = n.split(".")[0]
                assert rot not in forbudt, \
                    f"{fil.name} importerer {n} — v1 bokfører ingenting"
                assert not n.endswith("ssrf"), \
                    f"{fil.name} importerer egressveien {n}"


def test_invariant_postering_utenfor_registeret_har_ingen_hovedbok():
    """ANDRE HALVDEL av samme dom, målt på DATAMODELLEN og på rutene.

    En bokføringsvei kan ikke finnes uten et sted å bokføre TIL. 101 har
    ingen hovedbokstabell, ingen kontoplan og ingen posteringskø;
    `app.py` registrerer nøyaktig seks avstemmingsruter, og ingen av dem
    er en bokføring.

    Dette er den halvdelen som ville overlevd at noen skrev sin egen
    socket-kode uten å importere noe: uten en hovedbok å skrive i, finnes
    det ingenting å bokføre til, og fraværet er strukturelt i stedet for
    konvensjonelt.

    MUTASJONEN SOM DREPER DENNE: legg til en `hovedbok`-tabell eller en
    sjuende rute som heter `.../bokfor`.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    kode = "\n".join(l for l in sql.splitlines()
                     if not l.lstrip().startswith("--"))
    # ORDGRENSER, ikke delstrenger: «posteringsteksten» er navnet på
    # bankens egen beskrivelse av en bevegelse og har ingenting med
    # bokføring å gjøre. En port som felte den ville tvunget fram et
    # dårligere kolonnenavn for å bestå seg selv.
    import re as _re
    for ord_ in ("hovedbok", "kontoplan", "bokfoer", "bokfor_",
                 "endepunkt", "webhook", "mottaker", "postering_"):
        assert not _re.search(rf"\b{ord_}", kode.lower()), \
            f"101 bærer «{ord_}» — v1 har ingen hovedbok å skrive i"

    from api.app import RUTESCOPE
    mine = sorted(sti for _m, sti in RUTESCOPE
                  if sti.startswith("/v1/avstemming"))
    assert mine == [
        "/v1/avstemming",
        "/v1/avstemming/bankpost",
        "/v1/avstemming/bilag",
        "/v1/avstemming/konto",
        "/v1/avstemming/match",
        "/v1/avstemming/match/{avstemming_id:uuid}/opphev",
    ], mine


@pg
def test_invariant_postering_utenfor_registeret_funksjonelt(migrator):
    """TREDJE HALVDEL, målt på VIRKELIGHETEN: en full sveip endrer ikke
    ett eneste radantall utenfor modulens egne fem tabeller.

    De to portene over måler kode og form. Denne måler utfallet, og den
    er den eneste som ville fanget en bokføringsvei skrevet i ren SQL
    inne i en definer — der finnes verken en import eller et tabellnavn
    porten over leter etter.

    MUTASJONEN SOM DREPER DENNE: la `m13_sveip_avstemming` skrive én rad
    i en hvilken som helst annen tabell.
    """
    tenant = _tenantnavn("bokfor")
    with _rt() as c:
        k = _konto(c, tenant)
        b = _bilag(c, tenant, 100000, forfall_om=-40)
        _post(c, tenant, k, 60000, dager_siden=200)
    egne = {"bankkonto", "bankpost", "bilag", "avstemming",
            "avstemmingsfunn"}
    tabeller = [r[0] for r in migrator.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
        " ORDER BY tablename").fetchall()]
    migrator.rollback()

    def tell():
        ut = {}
        for tab in tabeller:
            if tab in egne:
                continue
            try:
                ut[tab] = migrator.execute(
                    f'SELECT count(*) FROM public."{tab}"').fetchone()[0]
            except psycopg.errors.InsufficientPrivilege:
                # Migrator eier ikke ALT (038-reaperens og m37-claimerens
                # tabeller er andres). Porten måler det den kan se, og
                # krever under at det er nok til å bety noe.
                migrator.rollback()
        migrator.rollback()
        return ut

    # `revisjonslogg` er UNNTATT med vilje og teller IKKE som en
    # postering: evidenskjeden skrives av registreringsdørene (ikke av
    # sveipen), og en modul som ikke bokførte i evidenskjeden ville brutt
    # husets egen regel. Sveipen skriver ingen evidensrad — den skriver
    # funn — så tellingen tas rundt SVEIPEN, der revisjonsloggen skal
    # stå helt stille.
    for _ in (b,):
        pass
    for_ = tell()
    assert len(for_) > 20, \
        f"porten teller bare {len(for_)} tabeller — den måler ingenting"
    with _sv() as v:
        _sveip(v)
    etter = tell()
    assert for_ == etter, \
        ("sveipen endret radantall utenfor registeret: "
         + str({k2: (for_[k2], etter[k2]) for k2 in for_
                if for_[k2] != etter[k2]}))


# ---------------------------------------------------------------------------
# INVARIANT 2: belop_i_flyttall
# ---------------------------------------------------------------------------

@pg
def test_invariant_belop_i_flyttall_i_katalogen(migrator):
    """Måler KATALOGEN, ikke kildekoden: hver beløpskolonne i modulens
    fem tabeller er `bigint`. En `numeric` ville vært eksakt og dermed
    forsvarlig, men den blander seg med flyttall i enhver aritmetikk som
    ikke passer på — og `real`/`double precision` er det den regelen
    finnes for å hindre.

    Porten leser `information_schema` og ikke SQL-teksten, så et
    `ALTER TABLE ... TYPE double precision` i en SENERE migrasjon også
    faller på den.

    MUTASJONEN SOM DREPER DENNE: bytt `belop_ore BIGINT` til
    `belop NUMERIC(12,2)` i 101.
    """
    rader = migrator.execute(
        "SELECT table_name, column_name, data_type"
        "  FROM information_schema.columns"
        " WHERE table_schema='public'"
        "   AND table_name IN ('bankpost','bilag','avstemming',"
        "                      'avstemmingsfunn')"
        "   AND (column_name LIKE '%ore%' OR column_name LIKE '%belop%')"
        " ORDER BY table_name, column_name").fetchall()
    migrator.rollback()
    assert rader, "fant ingen beløpskolonner — porten måler ingenting"
    for tab, kol, typ in rader:
        assert typ == "bigint", f"{tab}.{kol} er {typ}, ikke bigint"


@pg
def test_invariant_belop_i_flyttall_over_api(migrator, klient):
    """…og API-et RUNDER ALDRI et flyttall, det avviser det.

    `2.5` øre er ikke et beløp, det er en enhet noen har misforstått. En
    flate som rundet ville gjort misforståelsen til et tall i et
    regnskap, og feilen ville vært usynlig fra det øyeblikket.

    `True` avvises av samme grunn: i Python er `True` en `int`, og uten
    `isinstance(x, bool)`-sjekken ville `{"belop_ore": true}` blitt
    beløpet 1 øre.

    MUTASJONEN SOM DREPER DENNE: bytt `_ore` til `int(round(verdi))`.
    """
    from api.avstemming import MAKS_ORE, _ore
    from api.policyadmin_http import _Avbrudd
    for verdi in (2.5, 100.0, True, False, "100", None, MAKS_ORE,
                  -MAKS_ORE, 0):
        with pytest.raises(_Avbrudd):
            _ore({"belop_ore": verdi}, "belop_ore", "r",
                 tillat_negativ=True)
    # …og de lovlige slipper gjennom, ellers måler porten bare at alt
    # avvises.
    assert _ore({"belop_ore": -5000}, "belop_ore", "r",
                tillat_negativ=True) == -5000
    assert _ore({"belop_ore": 5000}, "belop_ore", "r",
                tillat_negativ=False) == 5000
    with pytest.raises(_Avbrudd):
        _ore({"belop_ore": -1}, "belop_ore", "r", tillat_negativ=False)


# ---------------------------------------------------------------------------
# INVARIANT 3: match_uten_begge_sider
# ---------------------------------------------------------------------------

@pg
def test_invariant_match_uten_begge_sider(migrator):
    """En avstemming er et FORHOLD mellom to identifiserte sider. Målt på
    DIREKTE DML som tabellens eier — altså den veien som ville omgått
    enhver kontroll i døren.

    De sammensatte fremmednøklene gjør en match mot en annen tenants post
    urepresenterbar; vakten måler at BEGGE sidene finnes i det hele tatt
    og at forholdet er meningsfullt.

    MUTASJONEN SOM DREPER DENNE: fjern `m13_avstemming_vakt`-triggeren.
    """
    tenant = _tenantnavn("sider")
    with _rt() as c:
        k = _konto(c, tenant)
        p = _post(c, tenant, k, 50000)
        b = _bilag(c, tenant, 50000)
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_avstemming_eier")
    # Ukjent bilag.
    with pytest.raises(psycopg.Error):
        migrator.execute(
            "INSERT INTO avstemming (tenant, avstemming_id, post_id,"
            " bilag_id, metode, avvik_ore, begrunnelse, opprettet_av)"
            " VALUES (%s,%s,%s,%s,'manuell',0,'x','u')",
            (tenant, uuid.uuid4(), p, uuid.uuid4()))
    migrator.rollback()
    # Ukjent post.
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_avstemming_eier")
    with pytest.raises(psycopg.Error):
        migrator.execute(
            "INSERT INTO avstemming (tenant, avstemming_id, post_id,"
            " bilag_id, metode, avvik_ore, begrunnelse, opprettet_av)"
            " VALUES (%s,%s,%s,%s,'manuell',0,'x','u')",
            (tenant, uuid.uuid4(), uuid.uuid4(), b))
    migrator.rollback()


@pg
def test_fortegnet_ma_svare_til_bilagets_retning(migrator):
    """En UTBETALING dekker ikke en kundefaktura. Uten regelen ville
    summene gått opp i et regnskap som var galt — og det er nøyaktig den
    feilen registeret finnes for å hindre.

    Målt BÅDE gjennom døren (som gir en lesbar setning) og på direkte DML
    (som er det som gjør regelen sann for enhver skrivevei).

    MUTASJONEN SOM DREPER DENNE: fjern fortegnssjekken fra vakten.
    """
    tenant = _tenantnavn("fortegn")
    with _rt() as c:
        k = _konto(c, tenant)
        ut = _post(c, tenant, k, -50000, tekst="Utbetaling")
        inn_bilag = _bilag(c, tenant, 50000, retning="inn")
        with pytest.raises(psycopg.Error) as ei:
            _avstem(c, tenant, ut, inn_bilag)
        assert "retning" in str(ei.value)
        c.rollback()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_avstemming_eier")
    with pytest.raises(psycopg.Error):
        migrator.execute(
            "INSERT INTO avstemming (tenant, avstemming_id, post_id,"
            " bilag_id, metode, avvik_ore, begrunnelse, opprettet_av)"
            " VALUES (%s,%s,%s,%s,'manuell',0,'x','u')",
            (tenant, uuid.uuid4(), ut, inn_bilag))
    migrator.rollback()


@pg
def test_avviket_kan_ikke_skrives_fritt(migrator):
    """`avvik_ore` er en OBSERVASJON, og vakten regner den samme
    differansen. Et avvik man kan skrive fritt måler ingenting — og da
    ville kolonnen vært et tekstfelt med et talltegn foran.

    MUTASJONEN SOM DREPER DENNE: fjern avvikssjekken fra vakten.
    """
    tenant = _tenantnavn("avvik")
    with _rt() as c:
        k = _konto(c, tenant)
        p = _post(c, tenant, k, 50000)
        b = _bilag(c, tenant, 50000)
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_avstemming_eier")
    with pytest.raises(psycopg.Error) as ei:
        migrator.execute(
            "INSERT INTO avstemming (tenant, avstemming_id, post_id,"
            " bilag_id, metode, avvik_ore, begrunnelse, opprettet_av)"
            " VALUES (%s,%s,%s,%s,'manuell',999,'x','u')",
            (tenant, uuid.uuid4(), p, b))
    assert "avvik_ore" in str(ei.value)
    migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 4: post_matchet_flere_ganger
# ---------------------------------------------------------------------------

@pg
def test_invariant_post_matchet_flere_ganger(migrator):
    """Dobbeltmatch er feilen som får et regnskap til å stemme på papiret
    og ikke i virkeligheten, og den er stille.

    Målt i tre lag: døren sier fra med en setning, den PARTIELLE unike
    indeksen gjør det sant også for direkte DML, og en OPPHEVET match
    slipper posten fri igjen — ellers ville en feilretting vært umulig.

    MUTASJONEN SOM DREPER DENNE: fjern `WHERE opphevet_ts IS NULL` fra
    indeksen (da blir opphevingen umulig), eller fjern indeksen (da blir
    dobbeltmatchen mulig). Begge feller porten.
    """
    tenant = _tenantnavn("dobbelt")
    with _rt() as c:
        k = _konto(c, tenant)
        p = _post(c, tenant, k, 50000)
        b1 = _bilag(c, tenant, 50000)
        b2 = _bilag(c, tenant, 50000)
        a1 = _avstem(c, tenant, p, b1)
        with pytest.raises(psycopg.Error) as ei:
            _avstem(c, tenant, p, b2)
        assert "alt avstemt" in str(ei.value)
        c.rollback()

    # Direkte DML, forbi døren.
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_avstemming_eier")
    with pytest.raises(psycopg.errors.UniqueViolation):
        migrator.execute(
            "INSERT INTO avstemming (tenant, avstemming_id, post_id,"
            " bilag_id, metode, avvik_ore, begrunnelse, opprettet_av)"
            " VALUES (%s,%s,%s,%s,'manuell',0,'x','u')",
            (tenant, uuid.uuid4(), p, b2))
    migrator.rollback()

    # …og etter oppheving er posten fri.
    with _rt() as c:
        _sett_kontekst(c, tenant)
        c.execute("SELECT m13_opphev_avstemming(%s,%s,%s,%s)",
                  (tenant, a1, "feil bilag", "u-test"))
        c.commit()
        _avstem(c, tenant, p, b2)
    # TELLINGEN GÅR SOM MIGRATOR, ikke som runtime: runtime har med vilje
    # INGEN tabellrettighet på registeret (SP-7), og en test som leste
    # tabellen som runtime ville målt at snittet var borte.
    _sett_kontekst(migrator, tenant)
    rader = migrator.execute(
        "SELECT count(*) FROM avstemming WHERE tenant=%s AND post_id=%s",
        (tenant, p)).fetchone()[0]
    migrator.rollback()
    # BEGGE radene består. At noe VAR avstemt er også historikk — en
    # slettet rad ville skjult at noen en gang mente noe annet.
    assert rader == 2


@pg
def test_overdekning_avvises(migrator):
    """DELBETALING ER TILLATT, OVERDEKNING ER DET IKKE. To innbetalinger
    på 1000 mot én faktura på 1000 er ikke en delbetaling — det er en
    dobbeltmatch med et annet ansikt.

    Regelen bor i døren og ikke i en CHECK, fordi den gjelder et AGGREGAT
    over flere rader, og en CHECK ser bare sin egen.

    MUTASJONEN SOM DREPER DENNE: fjern overdekningssjekken fra
    `m13_avstem`.
    """
    tenant = _tenantnavn("overdekn")
    with _rt() as c:
        k = _konto(c, tenant)
        b = _bilag(c, tenant, 100000)
        p1 = _post(c, tenant, k, 60000)
        p2 = _post(c, tenant, k, 40000)
        p3 = _post(c, tenant, k, 1000)
        _avstem(c, tenant, p1, b)
        # Delbetaling nummer to går GJENNOM: 60 000 + 40 000 = 100 000.
        _avstem(c, tenant, p2, b)
        with pytest.raises(psycopg.Error) as ei:
            _avstem(c, tenant, p3, b)
        assert "verdekning" in str(ei.value)
        c.rollback()
        s = _status(c, tenant)
    # Bilaget er dekket: null åpne bilag, null rest.
    assert (s[3], s[4]) == (0, 0)


# ---------------------------------------------------------------------------
# INVARIANT 5: registerrad_endret_etter_innsetting
# ---------------------------------------------------------------------------

@pg
def test_invariant_registerrad_endret_etter_innsetting(migrator):
    """Bankposten er en OBSERVASJON: det som skjedde på konto endrer seg
    ikke fordi noen redigerer en rad. Bilagets BELØP er frosset av samme
    grunn — restbeløpet regnes mot det, og et beløp som kunne endres
    etter en match ville gjort hver eldre avstemming til en påstand om et
    tall som ikke lenger finnes.

    Målt på DIREKTE DML som tabellens eier: dette er veien som ville
    omgått dørene.

    MUTASJONEN SOM DREPER DENNE: fjern `m13_bankpost_vakt` eller
    beløpslinjen i `m13_bilag_vakt`.
    """
    tenant = _tenantnavn("frosset")
    with _rt() as c:
        k = _konto(c, tenant)
        p = _post(c, tenant, k, 50000)
        b = _bilag(c, tenant, 50000)
    for sql, args in (
        ("UPDATE bankpost SET belop_ore=1 WHERE tenant=%s AND post_id=%s",
         (tenant, p)),
        ("UPDATE bankpost SET tekst='endret' WHERE tenant=%s"
         " AND post_id=%s", (tenant, p)),
        ("DELETE FROM bankpost WHERE tenant=%s AND post_id=%s",
         (tenant, p)),
        ("UPDATE bilag SET belop_ore=1 WHERE tenant=%s AND bilag_id=%s",
         (tenant, b)),
        ("UPDATE bilag SET retning='ut' WHERE tenant=%s AND bilag_id=%s",
         (tenant, b)),
        ("DELETE FROM bilag WHERE tenant=%s AND bilag_id=%s", (tenant, b)),
    ):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_avstemming_eier")
        with pytest.raises(psycopg.Error), migrator.transaction():
            migrator.execute(sql, args)
        migrator.rollback()


@pg
def test_en_opphevet_match_gjenapnes_ikke(migrator):
    """OPPHEVING GÅR ÉN VEI. En rad som kunne gjenåpnes ville gjort den
    partielle unike indeksen til en regel med hull: posten kunne fått en
    ny match mens den gamle lå og ventet på å bli levende igjen.

    MUTASJONEN SOM DREPER DENNE: fjern gjenåpningssjekken fra vakten.
    """
    tenant = _tenantnavn("gjenapne")
    with _rt() as c:
        k = _konto(c, tenant)
        p = _post(c, tenant, k, 50000)
        b = _bilag(c, tenant, 50000)
        a = _avstem(c, tenant, p, b)
        _sett_kontekst(c, tenant)
        c.execute("SELECT m13_opphev_avstemming(%s,%s,%s,%s)",
                  (tenant, a, "feil", "u-test"))
        c.commit()
        # …og en gang til er et STILLE JA, ikke en feil: to klikk på den
        # samme knappen er en bruker som ville ha nøyaktig den tilstanden
        # som alt gjelder.
        _sett_kontekst(c, tenant)
        igjen = c.execute("SELECT m13_opphev_avstemming(%s,%s,%s,%s)",
                          (tenant, a, "feil", "u-test")).fetchone()[0]
        c.commit()
    assert igjen is False
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_avstemming_eier")
    with pytest.raises(psycopg.Error) as ei:
        migrator.execute(
            "UPDATE avstemming SET opphevet_ts=NULL, opphevet_av=NULL,"
            " opphevet_begrunnelse=NULL WHERE tenant=%s"
            " AND avstemming_id=%s", (tenant, a))
    assert "gjenåpnes" in str(ei.value)
    migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 6: uavstemt_over_grense_uten_funn + funntype_utenfor_lukket_sett
# ---------------------------------------------------------------------------

@pg
def test_invariant_uavstemt_over_grense_uten_funn(migrator):
    """En uavstemt post som passerer aldersgrensen er et FUNN, ikke en
    rad som stille blir gammel — og et forfalt bilag likeså.

    TO FUNNTYPER PÅ BILAG, ikke én: helt udekket kan være en faktura
    ingen har betalt, delvis dekket er nesten alltid en avstemming som
    mangler sin siste post. De fører til to forskjellige handlinger.

    IDEMPOTENSEN måles i samme test: en sveip nummer to gir NULL nye
    rader og flytter bare `sist_sett_sveip`. En funnliste som vokser med
    kadensen er en funnliste folk lærer seg å overse.

    MUTASJONEN SOM DREPER DENNE: fjern `NOT EXISTS`-leddet fra
    innsettingen i sveipen (da vokser listen), eller fjern
    `> v_grense`-filteret fra kandidatene (da blir alt et funn).
    """
    tenant = _tenantnavn("funn")
    with _rt() as c:
        k = _konto(c, tenant)
        gammel = _post(c, tenant, k, 12345, dager_siden=200)
        _post(c, tenant, k, 500, dager_siden=3)      # under grensen
        udekket = _bilag(c, tenant, 50000, retning="ut", forfall_om=-40)
        delvis = _bilag(c, tenant, 100000, forfall_om=-10)
        p = _post(c, tenant, k, 60000, dager_siden=5)
        _avstem(c, tenant, p, delvis)
    with _sv() as v:
        _sveip(v)
    funn = {(r[0], r[2]): r for r in _funn(migrator, tenant)}
    assert set(funn) == {
        ("post", "uavstemt_post_over_grense"),
        ("bilag", "forfalt_bilag_uten_dekning"),
        ("bilag", "delvis_dekket_bilag"),
    }, sorted(funn)
    assert funn[("post", "uavstemt_post_over_grense")][1] == gammel
    assert funn[("bilag", "forfalt_bilag_uten_dekning")][1] == udekket
    assert funn[("bilag", "delvis_dekket_bilag")][1] == delvis
    # Restbeløpet står PÅ funnet: 100 000 − 60 000.
    assert funn[("bilag", "delvis_dekket_bilag")][4] == 40000

    forst = {(r[0], r[1], r[2]) for r in _funn(migrator, tenant)}
    with _sv() as v:
        _sveip(v)
    igjen = {(r[0], r[1], r[2]) for r in _funn(migrator, tenant)}
    assert forst == igjen, "sveip nummer to endret funnsettet"
    assert len(_funn(migrator, tenant, bare_apne=False)) == 3


@pg
def test_sveipen_ser_en_tenant_som_bare_har_bilag(migrator):
    """Tenantlisten er UNIONEN av begge sidene. Hentet den bare fra
    `bankpost`, ville en tenant som har registrert bilag men ennå ingen
    kontoutskrift aldri blitt sveipet — og det er NØYAKTIG den tenanten
    `forfalt_bilag_uten_dekning` finnes for: ingen har betalt, og ingen
    har importert noe som kunne vist det.

    MUTASJONEN SOM DREPER DENNE: hent tenantlisten fra `bankpost` alene.
    """
    tenant = _tenantnavn("barebilag")
    with _rt() as c:
        b = _bilag(c, tenant, 50000, forfall_om=-30)
    with _sv() as v:
        _sveip(v)
    funn = _funn(migrator, tenant)
    assert [(r[0], r[2]) for r in funn] == [
        ("bilag", "forfalt_bilag_uten_dekning")], funn
    assert funn[0][1] == b


@pg
def test_funnet_lukkes_naar_posten_avstemmes(migrator):
    """Et funn som ikke lenger gjelder lukkes — og RADEN BESTÅR. At noe
    VAR et funn er også historikk, og en revisor spør nettopp etter det.

    MUTASJONEN SOM DREPER DENNE: bytt lukkingen i sveipen til en DELETE
    (radvakten nekter den, så mutasjonen dør i basen — som er poenget).
    """
    tenant = _tenantnavn("lukk")
    with _rt() as c:
        k = _konto(c, tenant)
        p = _post(c, tenant, k, 50000, dager_siden=200)
        b = _bilag(c, tenant, 50000)
    with _sv() as v:
        _sveip(v)
    assert len(_funn(migrator, tenant)) == 1
    with _rt() as c:
        _avstem(c, tenant, p, b)
    with _sv() as v:
        _sveip(v)
    assert _funn(migrator, tenant) == []
    lukket = _funn(migrator, tenant, bare_apne=False)
    assert len(lukket) == 1 and lukket[0][5] is False


@pg
def test_invariant_funntype_utenfor_lukket_sett(migrator):
    """Funntypene er et LUKKET SETT i basen, og FLATEN kjenner nøyaktig
    de samme tre. En funntype flaten ikke kan tegne ville vært et funn
    ingen ser.

    MUTASJONEN SOM DREPER DENNE: legg en fjerde verdi i CHECK-en uten å
    lære flaten den.
    """
    tenant = _tenantnavn("lukket")
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_avstemming_eier")
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "INSERT INTO avstemmingsfunn (tenant, objekttype, objekt_id,"
            " funntype) VALUES (%s,'post',%s,'oppdiktet')",
            (tenant, uuid.uuid4()))
    migrator.rollback()

    sql = MIGRASJON.read_text(encoding="utf-8")
    flate = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
             / "avstemming.js").read_text(encoding="utf-8")
    for funntype in ("uavstemt_post_over_grense",
                     "forfalt_bilag_uten_dekning", "delvis_dekket_bilag"):
        assert funntype in sql, funntype
        assert funntype in flate, \
            f"flaten kjenner ikke funntypen {funntype}"


# ---------------------------------------------------------------------------
# INVARIANT 7: tenantlekkasje_i_avstemmingsregister
# ---------------------------------------------------------------------------

@pg
def test_invariant_tenantlekkasje_direkte(migrator):
    """RLS ENABLE+FORCE + `tenant_isolasjon` på hver av de fem tabellene,
    og dørene binder tenanten til KONTEKSTEN (SP-1) — aldri til
    parameteret alene.

    MUTASJONEN SOM DREPER DENNE: fjern `krev_tenantkontekst` fra en dør,
    eller `FORCE` fra en tabell.
    """
    a = _tenantnavn("lekk-a")
    b = _tenantnavn("lekk-b")
    with _rt() as c:
        ka = _konto(c, a)
        _post(c, a, ka, 50000)
        kb = _konto(c, b)
        _post(c, b, kb, 90000)
        # Døren kalt MED b i konteksten og a som parameter → nei.
        _sett_kontekst(c, b)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT * FROM m13_avstemmingsstatus(%s)", (a,))
        assert "tenantkontekst" in str(ei.value)
        c.rollback()
        # …og med riktig kontekst ser hver bare sine egne.
        assert _status(c, a)[0] == 1
        assert _status(c, b)[0] == 1

    for tab in ("bankkonto", "bankpost", "bilag", "avstemming",
                "avstemmingsfunn"):
        rad = migrator.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
            " WHERE oid = %s::regclass", (f"public.{tab}",)).fetchone()
        assert rad == (True, True), f"{tab}: RLS ikke ENABLE+FORCE"
    migrator.rollback()


@pg
def test_invariant_tenantlekkasje_over_api(migrator, klient):
    """…og over HTTP. Leseflaten viser tenantens egne rader og ingen
    andres — målt på det klienten faktisk får, ikke på et predikat.
    """
    fremmed = _tenantnavn("fremmed")
    c = _rt()
    try:
        ka = _konto(c, TENANT, navn="Egen konto")
        _post(c, TENANT, ka, 111111, tekst="Egen bevegelse")
        kf = _konto(c, fremmed, navn="Fremmed konto")
        _post(c, fremmed, kf, 777777, tekst="Fremmed bevegelse")
    finally:
        c.close()
    cookie, _csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/avstemming", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    kropp = json.dumps(r.json())
    assert "Egen bevegelse" in kropp
    assert "Fremmed bevegelse" not in kropp
    assert "Fremmed konto" not in kropp


# ---------------------------------------------------------------------------
# INVARIANT 8: ui_axe_alvorlige_brudd
# ---------------------------------------------------------------------------

def test_invariant_ui_axe_bor_i_js_suiten():
    """Axe-porten kjøres i `platform/core/ui` (node --test), ikke her.
    Denne testen finnes for at grensen ikke skal ha en invariant uten et
    spor — den peker på hvor målingen bor.
    """
    fil = (ROT / "platform" / "core" / "ui" / "test"
           / "avstemming.test.js")
    assert fil.exists(), "avstemming.test.js mangler"
    tekst = fil.read_text(encoding="utf-8")
    assert "axe" in tekst, "UI-suiten kjører ingen axe-port for flaten"


# ---------------------------------------------------------------------------
# Kontonummeret
# ---------------------------------------------------------------------------

@pg
def test_kontonummeret_lagres_aldri_helt(migrator):
    """Registeret trenger å kunne SI hvilken konto en post hører til og å
    kjenne igjen den samme kontoen på nytt. Ingen av delene krever hele
    nummeret — og å lagre det man ikke trenger er hvordan et register
    blir et brudd.

    Normaliseringen måles i samme test: «1234.56.78903» og
    «1234 5678903» er den SAMME kontoen. Uten den ville et mellomrom
    skapt en ny konto, og posten havnet under feil hode.

    MUTASJONEN SOM DREPER DENNE: lagre `p_kontonummer` i en kolonne.
    """
    tenant = _tenantnavn("konto")
    with _rt() as c:
        k1 = _konto(c, tenant, nummer="1234.56.78903")
        # Samme nummer, annen skrivemåte, ANNEN id → den unike indeksen
        # på hashen sier nei.
        with pytest.raises(psycopg.errors.UniqueViolation):
            _konto(c, tenant, nummer="1234 5678903")
        c.rollback()
    _sett_kontekst(migrator, tenant)
    rad = migrator.execute(
        "SELECT kontonummer_hale, kontonummer_hash FROM bankkonto"
        " WHERE tenant=%s AND konto_id=%s", (tenant, k1)).fetchone()
    kolonner = [r[0] for r in migrator.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name='bankkonto'"
    ).fetchall()]
    migrator.rollback()
    assert rad[0] == "8903"
    assert len(rad[1]) == 64
    # Ingen kolonne som kunne holdt hele nummeret.
    assert "kontonummer" not in kolonner, kolonner


@pg
def test_for_kort_kontonummer_avvises(migrator):
    """Fire siffer ville gjort halen lik hele nummeret, og da lagres
    nettopp det man skulle latt være."""
    tenant = _tenantnavn("kort")
    with _rt() as c:
        with pytest.raises(psycopg.Error) as ei:
            _konto(c, tenant, nummer="1234")
        assert "åtte siffer" in str(ei.value)
        c.rollback()


# ---------------------------------------------------------------------------
# Importidempotensen
# ---------------------------------------------------------------------------

@pg
def test_samme_kontoutskrift_to_ganger_gir_de_samme_radene(migrator):
    """DEN VIRKELIGE IDEMPOTENSEN er `ekstern_ref`, ikke
    Idempotency-Key-en: nøkkelen beskytter mot dobbeltklikk i flaten,
    bankens egen referanse beskytter mot den samme kontoutskriften lastet
    inn to ganger — og det siste er det som faktisk skjer.

    OG EN KILDE SOM MOTSIER SEG SELV FÅR NEI. Dukker referansen opp med
    et annet beløp, velger registeret ikke.

    MUTASJONEN SOM DREPER DENNE: fjern den unike indeksen på
    (tenant, konto_id, ekstern_ref).
    """
    tenant = _tenantnavn("import")
    with _rt() as c:
        k = _konto(c, tenant)
        _post(c, tenant, k, 50000, ref="BANK-X")
        _sett_kontekst(c, tenant)
        annen_nokkel = uuid.uuid4()
        rad = c.execute(
            "SELECT * FROM m13_registrer_post(%s,%s,%s,'BANK-X',"
            "                                 current_date,50000,"
            "                                 'Innbetaling',NULL,'u')",
            (tenant, annen_nokkel, k)).fetchone()
        c.commit()
        assert rad[0] is False
        # …OG ID-EN ER RADENS, IKKE DEN VI UTLEDET. Denne døren har TO
        # idempotenser — kallerens nøkkel og bankens referanse — og en
        # dør som bare svarte «nei, ikke ny» ville latt flaten sitte
        # igjen med en id ingen rad har. Neste kall som brukte den (en
        # match) ville fått «finnes ikke» om noe som beviselig finnes.
        assert rad[1] != annen_nokkel, \
            "døren ga tilbake kallerens utledede id, ikke radens"
        assert _status(c, tenant)[0] == 1
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT * FROM m13_registrer_post(%s,%s,%s,'BANK-X',"
                "                                 current_date,99999,"
                "                                 'Innbetaling',NULL,'u')",
                (tenant, uuid.uuid4(), k))
        assert "motsier seg selv" in str(ei.value)
        c.rollback()
    # …og id-en døren ga tilbake peker på en rad som finnes.
    _sett_kontekst(migrator, tenant)
    assert migrator.execute(
        "SELECT count(*) FROM bankpost WHERE tenant=%s AND post_id=%s",
        (tenant, rad[1])).fetchone()[0] == 1
    migrator.rollback()


# ---------------------------------------------------------------------------
# HTTP-riggen. Browsersesjon, ikke bærertoken: skriveveiene er
# `bestilling:opprett` + CSRF, altså menneskelige handlinger i flaten, og
# en test som gikk utenom sesjonen ville målt en vei som ikke finnes.
# ---------------------------------------------------------------------------

def _C_SESJON():
    from api import sesjon as sesjonmodul
    return sesjonmodul.C_SESJON


def _browserokt(migrator, roller):
    """Minirigg: en innlogget browserøkt med gitte roller i TENANT.
    -> (sesjonscookie, csrf-token)."""
    from api import sesjon as sesjonmodul
    _sett_kontekst(migrator, TENANT)
    bid = migrator.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://m13.test', %s) RETURNING bruker_id",
        ("s13h-" + secrets.token_hex(6),)).fetchone()[0]
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
        " now()+interval \'1 hour\', false)",
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
def test_http_lesing_krever_okonomi_read(migrator, klient):
    """Flaten står bak `okonomi:read`, og det er det ene NYE scopet i
    klynge 3. En sesjon uten det får 403 — menyen lover aldri en flate
    serveren svarer 403 på, og porten måler at serveren faktisk holder
    det.

    KRETSEN ER `admin` ALENE: verken `leser` eller `sikkerhet` har
    scopet, og porten måler nettopp det mot `ROLLE_TIL_SCOPES`.
    """
    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert "okonomi:read" in ROLLE_TIL_SCOPES["admin"]
    for rolle in ("leser", "sikkerhet", "godkjenner", "policyforvalter"):
        assert "okonomi:read" not in ROLLE_TIL_SCOPES[rolle], rolle
    cookie, _csrf = _browserokt(migrator, ["leser"])
    r = klient.get("/v1/avstemming", cookies={_C_SESJON(): cookie})
    assert r.status_code == 403, r.text
    cookie, _csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/avstemming", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text


@pg
@dekker("avstemming_ulovlig_tilstand")
def test_http_ulovlig_tilstand_er_409_og_ikke_400(migrator, klient):
    """FEILVEIEN `avstemming_ulovlig_tilstand`. Kroppen ER velformet —
    det er BASEN som sier nei — og forskjellen er selve svaret: «disse to
    hører ikke sammen» er en annen setning enn «noe gikk galt».

    Dette er også testen `test_api_porter.test_hver_feilvei_har_en_test`
    krever for den nye feilveien.
    """
    c = _rt()
    try:
        k = _konto(c, TENANT)
        ut = _post(c, TENANT, k, -50000, tekst="Utbetaling")
        inn = _bilag(c, TENANT, 50000, retning="inn")
    finally:
        c.close()
    cookie, csrf = _browserokt(migrator, ["admin"])
    # Velformet kropp, men de to hører ikke sammen: TILSTANDEN sier nei.
    r = _hpost(klient, cookie, csrf, "/v1/avstemming/match",
               {"post_id": str(ut), "bilag_id": str(inn),
                "metode": "manuell", "begrunnelse": "feil fortegn"})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "avstemming_ulovlig_tilstand"
    # …og et flyttall er 400: KROPPEN er feil, ikke tilstanden.
    r = _hpost(klient, cookie, csrf, "/v1/avstemming/bilag",
               {"bilagsnummer": "F-FLYT", "retning": "inn",
                "belop_ore": 2.5, "motpart": "Kunde AS",
                "utstedt": "2026-07-01"})
    assert r.status_code == 400, r.text
    assert r.json()["feil"] == "request_feilformet"


@pg
def test_http_registrering_er_idempotent(migrator, klient):
    """SP-2 på alle fire skriveveiene som føder en rad: samme
    Idempotency-Key + samme innhold gir SAMME id og skriver ingenting to
    ganger. En dobbelt registrert innbetaling er nøyaktig den feilen som
    får et regnskap til å stemme på papiret og ikke i virkeligheten.
    """
    cookie, csrf = _browserokt(migrator, ["admin"])
    nokkel = secrets.token_urlsafe(24)
    kropp = {"navn": "Idempotenskonto",
             "kontonummer": "9999" + secrets.token_hex(4).translate(
                 str.maketrans("abcdef", "012345")),
             "valuta": "NOK"}
    a = _hpost(klient, cookie, csrf, "/v1/avstemming/konto", kropp,
               idem=nokkel)
    b = _hpost(klient, cookie, csrf, "/v1/avstemming/konto", kropp,
               idem=nokkel)
    assert a.status_code in (200, 201), a.text
    assert b.status_code in (200, 201), b.text
    assert a.json()["konto_id"] == b.json()["konto_id"]
    assert a.json()["ny"] is True and b.json()["ny"] is False


# ---------------------------------------------------------------------------
# Sveipearbeideren
# ---------------------------------------------------------------------------

@pg
def test_sveipen_nekter_kontekst(migrator):
    """Sveipen er KRYSS-TENANT og kjøres uten tenantkontekst. En kaller
    som har satt en kontekst ber om noe annet enn det funksjonen gjør, og
    da er nei riktigere enn et halvt svar.
    """
    with _sv() as v:
        v.execute("SELECT set_config('disponit.tenant','t',false)")
        with pytest.raises(psycopg.Error) as ei:
            v.execute("SELECT * FROM m13_sveip_avstemming(500,30)")
        assert "KRYSS-TENANT" in str(ei.value)
        v.rollback()


@pg
def test_sveiperollen_har_ingen_tabellrettigheter(migrator):
    """`disponit_avstemmingssveip` har nøyaktig ÉN rettighet i basen:
    EXECUTE på sveipen. INGEN tabellrettigheter, ingen lesedør, ingen
    skrivedør — funnene skrives av den eier-eide defineren.

    MUTASJONEN SOM DREPER DENNE: gi sveiperollen SELECT på `bankpost`.
    """
    if not AVSTEMMINGSVEIP_DSN:
        pytest.skip("DISPONIT_TEST_AVSTEMMINGSVEIP_DSN ikke satt")
    rader = migrator.execute(
        "SELECT table_name, privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_avstemmingssveip'").fetchall()
    migrator.rollback()
    assert rader == [], rader


def test_overlappende_sveip_hopper_over_og_rorer_ikke_feiltelleren():
    """En kjøring som fant arbeidernøkkelen opptatt har VERKEN lyktes
    ELLER feilet. Uten `hoppet_over` på resultatet ville kalleren
    persistert feiltellingen 0 — altså slettet en alt opptelt feil ved
    hver overlappende aktivering, og alarmen etter to sammenhengende feil
    ville aldri nådd frem.
    """
    from drift import avstemmingssveip

    class Falsk:
        def execute(self, sql, *a):
            class R:
                @staticmethod
                def fetchone():
                    return (False,)
            return R()

        def commit(self):
            pass

    r = avstemmingssveip.kjor(Falsk(), tidligere_feil=1)
    assert r.hoppet_over is True
    assert r.feilet is False and r.alarm_utlost is False


def test_sveipen_nekter_aa_starte_uten_egen_dsn(tmp_path, monkeypatch):
    """INGEN fallback til `DATABASE_URL`. Runtime-rollen har med vilje
    ikke EXECUTE på sveipen (101 REVOKEr den), så en fallback ville bare
    byttet en tydelig oppstartsnekt mot «permission denied» i journalen
    hver natt.
    """
    from drift import kjor_avstemmingssveip
    monkeypatch.delenv("DISPONIT_AVSTEMMINGSVEIP_URL", raising=False)
    monkeypatch.setenv("DISPONIT_AVSTEMMINGSVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.setattr("db.hemmeligheter.last_credentials", lambda: None)
    assert kjor_avstemmingssveip.main() == 2


# ---------------------------------------------------------------------------
# §0: grensen ble registrert FØR koden
# ---------------------------------------------------------------------------

def test_grensen_ble_registrert_for_koden():
    """Grensen `m13-v1` sto i `KRAVGRENSER` fra klynge 3-fundamentet, før
    en eneste linje av modulen fantes (§0-regelen). Porten måler at hver
    invariant der har en test HER — ikke at testen er god, men at ingen
    invariant står uten et spor.

    `punktbinding` er tom med vilje: ingen sjekklistepunkter er flippbare
    før målingene finnes.
    """
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m13-v1"]
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["punktbinding"] == {}
    egen = Path(__file__).read_text(encoding="utf-8")
    for inv in g["invarianter"]:
        assert inv in egen, f"invarianten {inv} har ingen port"
