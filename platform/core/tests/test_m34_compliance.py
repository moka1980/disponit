"""M-34 compliance- og sertifiseringsagent v1 (migrasjon 100) — grensens
seks invarianter, målt.

Grensen `m34-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR koden
(§0-regelen). Hver invariant har en port her, og hver port måler BÅDE at
den lovlige veien virker og at bruddet avvises — en port som bare måler
det som skal gå igjennom har ikke målt en invariant.

  * `modulen_sendte_inn_evidens` — V1-DOMMEN. Statisk (AST over
    modulens tre Python-filer: ingen HTTP-klient, ingen egress-import,
    ingen utsendingsvei) OG funksjonelt: hele registeret har ingen
    tabell, ingen kolonne og ingen funksjon som bærer en mottaker, og
    en full runde gjennom dørene og sveipen skriver ingenting utenfor
    modulens egne fire tabeller pluss evidenskjeden.
  * `kontroll_uten_eier` — direkte DML uten eier avvises (NOT NULL), med
    ukjent eier av fremmednøkkelen, og døren avviser en «eier» som ikke
    er aktivt medlem. Tre lag, samme sannhet.
  * `kontroll_oppfylt_uten_evidens` — DEN BÆRENDE. Status `oppfylt` uten
    evidenshenvisning ELLER uten dato avvises i TRE lag: CHECK-en, vakten
    (henvisningen må svare til en faktisk etterprøvingsrad) og dørens
    egen RAISE. Porten er skrevet så den ville vært RØD med en naiv
    implementasjon som bare hadde en CHECK.
  * `forbigatt_etterproving_uten_funn` — en kontroll forbi
    `etterproving_dogn` gir funn; to sveip gir ETT funn.
  * `tenantlekkasje_i_kontrollregister` — tenant A ser aldri tenant Bs
    kontroller, verken ved direkte DML eller over API-et.
  * `ui_axe_alvorlige_brudd` — bor i
    `platform/core/ui/test/compliance.test.js` (jsdom + axe-core), som
    kjøres av `npm test`, ikke herfra.

I tillegg: etterprøvingshistorikken er append-only (UPDATE OG DELETE
avvist), `avvik` uten beskrivelse avvises, `ikke_relevant` uten
begrunnelse avvises, evidenskjeden får sin rad, avledningen
`sist_etterprovd` kan ikke drive fra historikken, og migrasjonen er ren
DDL (SP-10s premiss) og navngir aldri runtime-rollen.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import ast
import hashlib
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

#: Sveiperollen. `m34_sveip_etterprovinger` er BARE hennes (kryss-tenant,
#: 038-reaperens snitt), så en test som kjører sveipen må koble som henne
#: — migratoren arver ingenting (`WITH INHERIT FALSE`) og web-runtime er
#: eksplisitt REVOKEt. CI setter variabelen.
COMPLIANCESVEIP_DSN = os.environ.get("DISPONIT_TEST_COMPLIANCESVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "100_m34_kontrollregister.sql")
#: Modulens EGNE Python-filer. Dette er hele modulen i kode: API-et,
#: sveipearbeideren og inngangspunktet dens. Fraværet av en utsendingsvei
#: skal kunne måles på nøyaktig disse tre.
MODULFILER = (
    ROT / "platform" / "core" / "api" / "compliance.py",
    ROT / "platform" / "drift" / "compliancesveip.py",
    ROT / "platform" / "drift" / "kjor_compliancesveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")


# ---------------------------------------------------------------------------
# Riggen
# ---------------------------------------------------------------------------

def _rt():
    """Runtime-rollen — den som HAR EXECUTE på de fire API-dørene og
    ingen tabellrettighet på registeret."""
    from db.pg import koble
    return koble(DSN)


def _sv():
    """Sveiperollen — den som har EXECUTE på sveipen og ingenting
    annet."""
    from db.pg import koble
    return koble(COMPLIANCESVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    """Egen tenant per test. Sveipen er kryss-tenant og ser HELE basen,
    så en delt tenant ville gjort testene rekkefølgeavhengige — og en
    test som består fordi naboen ryddet er ingen port."""
    return f"t-m34-{merke}-{secrets.token_hex(4)}"


def _bruker(m, tenant, *, navn=None, aktiv=True, roller=("admin",)):
    """En identitet med medlemskap i tenanten."""
    profil = {"visningsnavn": navn} if navn else {}
    _sett_kontekst(m, tenant)
    bid = m.execute(
        "INSERT INTO brukeridentitet (issuer, sub, profil)"
        " VALUES ('https://m34.test', %s, %s::jsonb) RETURNING bruker_id",
        ("s34-" + secrets.token_hex(6), json.dumps(profil))).fetchone()[0]
    m.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller, aktiv)"
        " VALUES (%s,%s,%s,%s)", (tenant, bid, list(roller), aktiv))
    m.commit()
    return bid


def _registrer(c, tenant, eier, *, krav="A.8.16",
               beskrivelse="Overvaking av aktiviteter i drift",
               rammeverk="ISO 27001", versjon="2022", dogn=90, kid=None,
               aktor="u-test"):
    """Én kontroll gjennom døren, som runtime."""
    kid = kid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute("SELECT m34_registrer_kontroll(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (tenant, kid, rammeverk, versjon, krav, beskrivelse, eier,
               dogn, aktor))
    c.commit()
    return kid


def _etterprov(c, tenant, kid, utfor, *, dager_siden=0, ref="SAK-2026-1",
               utfall="oppfylt", avvik=None, eid=None, aktor="u-test"):
    """Én etterprøving gjennom døren. `dager_siden` gjør det mulig å
    skrive «utført for 400 døgn siden» uten å regne på klokka."""
    eid = eid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m34_registrer_etterproving(%s,%s,%s,"
        "       current_date - %s::int,%s,%s,%s,%s,%s)",
        (tenant, eid, kid, dager_siden, utfor, ref, utfall, avvik, aktor))
    c.commit()
    return eid


def _sveip(v, grense=500):
    """Kjør sveipen én gang. -> (tenanter, nye, oppdaterte, lukkede,
    avkortet).

    TALLENE ER PLATTFORMVIDE, ikke tenantens: sveipen er kryss-tenant per
    konstruksjon og ser hver kontroll i basen, også dem andre tester har
    lagt igjen. Assertene under teller derfor tenantens EGNE funn
    (`_funn`), ikke returverdien — en test som stolte på totalen ville
    vært rekkefølgeavhengig.
    """
    rad = v.execute("SELECT * FROM m34_sveip_etterprovinger(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant, *, bare_apne=True):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT kontroll_id, funntype, dogn_over_frist, apen,"
        "       forst_sett, sist_sett_sveip FROM kontrollfunn"
        " WHERE tenant=%s AND (%s IS FALSE OR apen) ORDER BY funntype",
        (tenant, bare_apne)).fetchall()
    m.rollback()
    return rader


def _kontrollrad(m, tenant, kid):
    _sett_kontekst(m, tenant)
    rad = m.execute(
        "SELECT status, sist_etterprovd, evidens_ref,"
        "       ikke_relevant_begrunnelse FROM kontroll"
        " WHERE tenant=%s AND kontroll_id=%s", (tenant, kid)).fetchone()
    m.rollback()
    return rad


# ---------------------------------------------------------------------------
# INVARIANT 1: modulen_sendte_inn_evidens — V1-DOMMEN
# ---------------------------------------------------------------------------

def test_invariant_modulen_sendte_inn_evidens_statisk():
    """Katalogteksten lover automatisk innsamling av evidens OG
    innsending til sertifiseringsorgan. Begge forutsetter connectorer per
    rammeverk og et mandat per mottaker — ingen av delene finnes, og et
    compliance-verktøy som sender inn noe på egen hånd skaper EN PÅSTAND
    INGEN HAR LEST.

    Porten er en AST-analyse, ikke et delstrengsøk: en import inne i en
    funksjon (som resten av huset bruker for late importer) ville sluppet
    unna et `startswith("import ")`, og det er nøyaktig formen en
    innsendingsvei ville hatt her — modulens egne importer ER late.

    MUTASJONEN SOM DREPER DENNE: legg `import httpx` inne i en funksjon i
    `api/compliance.py`, eller la sveipearbeideren importere
    `api.ssrf`.
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
                # Relativ import (`from . import compliancesveip`) har
                # `module=None` og er per definisjon intern.
                navn = [node.module or ""] if node.level == 0 else []
            for n in navn:
                rot = n.split(".")[0]
                assert rot not in forbudt, \
                    f"{fil.name} importerer {n} — v1 sender ingenting inn"
                assert not n.endswith("ssrf"), \
                    f"{fil.name} importerer egressveien {n}"


def test_invariant_modulen_sendte_inn_evidens_har_ingen_mottaker():
    """ANDRE HALVDEL av samme dom, målt på DATAMODELLEN og på flaten.

    En innsendingsvei kan ikke finnes uten et sted å sende TIL. 100 har
    ingen mottakertabell, ingen adressekolonne og ingen kø; `app.py`
    registrerer nøyaktig fire compliance-ruter, og ingen av dem er en
    innsending.

    Dette er den halvdelen som ville overlevd at noen skrev sin egen
    socket-kode uten å importere noe: uten en mottaker å skrive ned,
    finnes det ingenting å sende til, og fraværet er strukturelt i stedet
    for konvensjonelt.

    MUTASJONEN SOM DREPER DENNE: legg til en `sertifiseringsorgan`-tabell
    med en `endepunkt`-kolonne, eller en femte rute som heter
    `.../send-inn`.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    kode = "\n".join(l for l in sql.splitlines()
                     if not l.lstrip().startswith("--"))
    for ord_ in ("endepunkt", "mottaker", "url", "epost_til", "webhook",
                 "utsending", "innsend"):
        assert ord_ not in kode.lower(), \
            f"100 bærer «{ord_}» — v1 har ingen mottaker å sende til"

    from api.app import RUTESCOPE
    mine = sorted(sti for _m, sti in RUTESCOPE
                  if sti.startswith("/v1/compliance"))
    assert mine == [
        "/v1/compliance",
        "/v1/compliance/kontroll",
        "/v1/compliance/kontroll/{kontroll_id:uuid}/etterproving",
        "/v1/compliance/kontroll/{kontroll_id:uuid}/ikke-relevant",
    ], mine


@pg
def test_invariant_modulen_sendte_inn_evidens_funksjonelt(migrator):
    """TREDJE HALVDEL: en FULL runde — registrering, etterprøving,
    ikke-relevant og sveip — rører ingen tabell utenfor modulens egne
    fire pluss evidenskjeden.

    Målt som en faktisk telling FØR og ETTER, ikke som en påstand: det
    finnes ingen `varsel`-rad, ingen `oppdrag`-rad, ingen
    `utsendingsliste` — ingen av de tre veiene ut av plattformen. En
    modul som «sender inn» ville måttet ta én av dem.

    MUTASJONEN SOM DREPER DENNE: la sveipen køe et varsel i stedet for å
    skrive et funn, eller la etterprøvingsdøren opprette et
    utsendingsoppdrag.
    """
    ten = _tenantnavn("ingen-utsending")
    eier = _bruker(migrator, ten)

    def _tell(tabell):
        _sett_kontekst(migrator, ten)
        n = migrator.execute(
            f"SELECT count(*) FROM {tabell} WHERE tenant=%s",
            (ten,)).fetchone()[0]
        migrator.rollback()
        return n

    utveier = ("varsel", "oppdrag", "utsendingsliste", "unntak")
    for tabell in utveier:
        assert _tell(tabell) == 0, f"{tabell} var ikke tom ved start"

    c, v = _rt(), _sv()
    try:
        kid = _registrer(c, ten, eier)
        _etterprov(c, ten, kid, eier, ref="SAK-2026-77")
        kid2 = _registrer(c, ten, eier, krav="A.7.4",
                          beskrivelse="Fysisk overvaking")
        _sett_kontekst(c, ten)
        c.execute("SELECT m34_marker_ikke_relevant(%s,%s,%s,%s)",
                  (ten, kid2, "Vi har ingen egne lokaler.", "u-test"))
        c.commit()
        _sveip(v)
    finally:
        c.close()
        v.close()

    for tabell in utveier:
        assert _tell(tabell) == 0, \
            f"modulen skrev til {tabell} — det er en vei UT av plattformen"
    # …og evidenskjeden HAR fått sine rader: fraværet av en utsendingsvei
    # er ikke fravær av spor. Manifestet fører `m02_revisjonslogg` som
    # REELL avhengighet, og det er den som gjør en etterprøving
    # gjenfinnbar.
    _sett_kontekst(migrator, ten)
    handlinger = [r[0] for r in migrator.execute(
        "SELECT handling FROM revisjonslogg WHERE tenant=%s"
        "   AND kilde='m34_compliance' ORDER BY handling", (ten,)).fetchall()]
    migrator.rollback()
    assert handlinger == ["kontroll.etterprovd", "kontroll.ikke_relevant",
                          "kontroll.registrert", "kontroll.registrert"], \
        handlinger


# ---------------------------------------------------------------------------
# INVARIANT 2: kontroll_uten_eier
# ---------------------------------------------------------------------------

@pg
def test_invariant_kontroll_uten_eier(migrator):
    """«Kontroller uten eier» er katalogens egen KPI, og i v1 er den en
    NOT NULL — ikke en rapport. Tre lag måles her, fordi to ville vært
    for lite:

      1. DIREKTE DML, som dørenes eier: en INSERT uten `eier_bruker_id`
         avvises av NOT NULL, og en med ukjent bruker-id av
         fremmednøkkelen. Det er den bindende porten — den gjelder enhver
         skrivevei, også en fremtidig som glemmer døren.
      2. DØREN: en «eier» som ikke er AKTIVT MEDLEM av tenanten avvises.
         FK-en alene sier bare at id-en finnes ET STED i plattformen.
      3. SVEIPEN: eieren som SLUTTET etterpå. FK-en peker på identiteten,
         ikke på medlemskapet, så raden har fortsatt en eier å navngi
         lenge etter at mennesket er borte — og det er nøyaktig gapet
         KPI-en handler om. Uten dette tredje laget ville invarianten
         vært sann i skjemaet og usann i virkeligheten.

    MUTASJONEN SOM DREPER DENNE: fjern NOT NULL på `eier_bruker_id`,
    fjern medlemskapssjekken i `m34_registrer_kontroll`, eller fjern
    `kontroll_uten_eier` fra `m34_funnkandidater`.
    """
    ten = _tenantnavn("eier")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _sv()
    try:
        kid = _registrer(c, ten, eier)
        _sett_kontekst(migrator, ten)
        rv = migrator.execute(
            "SELECT rammeverk_id FROM rammeverk WHERE tenant=%s",
            (ten,)).fetchone()[0]
        migrator.rollback()

        # 1a. Ingen eier i det hele tatt.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_compliance_eier")
        with pytest.raises(psycopg.errors.NotNullViolation):
            migrator.execute(
                "INSERT INTO kontroll (tenant, kontroll_id, rammeverk_id,"
                "  krav_ref, beskrivelse, etterproving_dogn, opprettet_av)"
                " VALUES (%s,%s,%s,'A.0.0','uten eier',30,'test')",
                (ten, uuid.uuid4(), rv))
        migrator.rollback()

        # 1b. En eier som ikke finnes som identitet.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_compliance_eier")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            migrator.execute(
                "INSERT INTO kontroll (tenant, kontroll_id, rammeverk_id,"
                "  krav_ref, beskrivelse, eier_bruker_id,"
                "  etterproving_dogn, opprettet_av)"
                " VALUES (%s,%s,%s,'A.0.1','fantom','bid_finnes_ikke',"
                "         30,'test')", (ten, uuid.uuid4(), rv))
        migrator.rollback()

        # 2. Døren: en bruker fra en ANNEN tenant er ikke en eier her.
        annen = _tenantnavn("eier-annen")
        fremmed = _bruker(migrator, annen)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _registrer(c, ten, fremmed, krav="A.0.2")
        c.rollback()
        # …og et INAKTIVT medlem av EGEN tenant er heller ikke en eier.
        sovende = _bruker(migrator, ten, aktiv=False)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _registrer(c, ten, sovende, krav="A.0.3")
        c.rollback()

        # 3. Eieren som SLUTTET etter at kontrollen ble registrert.
        assert not [f for f in _funn(migrator, ten)
                    if f[1] == "kontroll_uten_eier"]
        _sett_kontekst(migrator, ten)
        migrator.execute(
            "UPDATE brukermedlemskap SET aktiv=false WHERE tenant=%s"
            "   AND bruker_id=%s", (ten, eier))
        migrator.commit()
        _sveip(v)
        uten_eier = [f for f in _funn(migrator, ten)
                     if f[1] == "kontroll_uten_eier"]
        assert [f[0] for f in uten_eier] == [kid], uten_eier
    finally:
        c.close()
        v.close()


# ---------------------------------------------------------------------------
# INVARIANT 3: kontroll_oppfylt_uten_evidens — DEN BÆRENDE
# ---------------------------------------------------------------------------

@pg
def test_invariant_kontroll_oppfylt_uten_evidens(migrator):
    """DOMMEN v1 HVILER PÅ: en kontroll er «oppfylt» BARE med skrevet
    evidenshenvisning og dato.

    TRE LAG, og porten er skrevet så den ville vært RØD med en naiv
    implementasjon som bare hadde CHECK-en:

      1. CHECK-en `kontroll_oppfylt_krever_evidens`. Avviser `oppfylt`
         uten dato, og `oppfylt` uten henvisning. En naiv implementasjon
         har denne — og BARE denne.
      2. VAKTEN. Dette er laget den naive mangler: med bare en CHECK
         kunne hvem som helst skrive `sist_etterprovd = current_date,
         evidens_ref = 'noe jeg fant på'` og få en fullt lovlig
         «oppfylt»-rad. Da er «oppfylt med evidens» bare «oppfylt med et
         tekstfelt til». Vakten krever at henvisningen svarer til en
         FAKTISK rad i `etterproving` — og at det ikke finnes en nyere,
         så den materialiserte avledningen heller ikke kan drive.
      3. DØRENS RAISE. En tom henvisning og en manglende dato avvises FØR
         noe skrives, med en feilmelding som sier hvorfor.

    MUTASJONEN SOM DREPER DENNE: fjern EXISTS-leddet i
    `m34_kontroll_vakt`. CHECK-en står, en tom base er fortsatt grønn, og
    hele registeret er igjen en avkryssingsliste.
    """
    ten = _tenantnavn("evidens")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        kid = _registrer(c, ten, eier)
        # Ved fødselen: ikke oppfylt, ingen evidens. Ærlig fra dag én.
        assert _kontrollrad(migrator, ten, kid)[0] == "ikke_oppfylt"

        # LAG 1a: `oppfylt` uten dato og uten henvisning. Vakten rører
        # ikke denne (den ser bare på rader som HAR en dato), så det er
        # CHECK-en alene som feller den.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_compliance_eier")
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(
                "UPDATE kontroll SET status='oppfylt' WHERE tenant=%s"
                "   AND kontroll_id=%s", (ten, kid))
        migrator.rollback()

        # LAG 1b: dato uten henvisning — halv evidens er ingen evidens.
        #
        # MÅLT MED VAKTEN AVSKRUDD, og det er ikke en snarvei: en BEFORE
        # ROW-trigger fyrer FØR CHECK-ene i PostgreSQL, så med vakten på
        # ville denne raden aldri nådd CHECK-en, og porten ville ikke
        # visst om CHECK-en fantes. Å skru vakten av her er den ENESTE
        # måten å måle at de to lagene er UAVHENGIGE — at CHECK-en holder
        # av seg selv, den dagen noen dropper triggeren.
        _sett_kontekst(migrator, ten)
        migrator.execute(
            "ALTER TABLE kontroll DISABLE TRIGGER m34_kontroll_vakt")
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(
                "UPDATE kontroll SET status='oppfylt',"
                "       sist_etterprovd=current_date WHERE tenant=%s"
                "   AND kontroll_id=%s", (ten, kid))
        migrator.rollback()
        # …og henvisning uten dato, samme lag, samme grunn.
        _sett_kontekst(migrator, ten)
        migrator.execute(
            "ALTER TABLE kontroll DISABLE TRIGGER m34_kontroll_vakt")
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(
                "UPDATE kontroll SET status='oppfylt',"
                "       evidens_ref='SAK-1' WHERE tenant=%s"
                "   AND kontroll_id=%s", (ten, kid))
        migrator.rollback()

        # LAG 2: DEN SKARPE. Dato OG henvisning er satt, CHECK-en er
        # oppfylt — men henvisningen svarer ikke til noen etterprøving.
        # En naiv implementasjon slipper dette gjennom.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_compliance_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "UPDATE kontroll SET status='oppfylt',"
                "       sist_etterprovd=current_date,"
                "       evidens_ref='noe jeg fant paa' WHERE tenant=%s"
                "   AND kontroll_id=%s", (ten, kid))
        migrator.rollback()

        # LAG 3: dørens RAISE, på tom henvisning og på manglende dato.
        for tom in (None, "", "   "):
            _sett_kontekst(c, ten)
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                c.execute(
                    "SELECT m34_registrer_etterproving(%s,%s,%s,"
                    "       current_date,%s,%s,'oppfylt',NULL,%s)",
                    (ten, uuid.uuid4(), kid, eier, tom, "u-test"))
            c.rollback()
        _sett_kontekst(c, ten)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute(
                "SELECT m34_registrer_etterproving(%s,%s,%s,NULL,%s,%s,"
                "       'oppfylt',NULL,%s)",
                (ten, uuid.uuid4(), kid, eier, "SAK-1", "u-test"))
        c.rollback()

        # DEN LOVLIGE VEIEN: historikken først, avledningen etter.
        _etterprov(c, ten, kid, eier, ref="SAK-2026-118")
        status, dato, ref, _ = _kontrollrad(migrator, ten, kid)
        assert status == "oppfylt" and ref == "SAK-2026-118"
        assert dato is not None

        # …og avledningen kan ikke settes bakover eller løsrives fra
        # historikken etterpå. Dette er samme vakt, målt på UPDATE av en
        # rad som ALT har evidens: en henvisning som byttes til en annen
        # streng er ikke lenger en henvisning.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_compliance_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "UPDATE kontroll SET evidens_ref='SAK-2026-999'"
                " WHERE tenant=%s AND kontroll_id=%s", (ten, kid))
        migrator.rollback()
    finally:
        c.close()


@pg
def test_avledningen_folger_historikkens_siste_rad(migrator):
    """`sist_etterprovd` ER «siste rad i `etterproving`», og det måles
    her — ikke antas.

    En etterprøving som registreres I ETTERKANT (utført tidligere enn en
    som alt står der) skal få sin rad i HISTORIKKEN, men skal IKKE flytte
    tilstanden bakover. Det er hele grunnen til at avledningen leses ut av
    tabellen i stedet for å skrives fra parameteren.

    MUTASJONEN SOM DREPER DENNE: la `m34_registrer_etterproving` skrive
    `p_utfort` rett inn i `kontroll.sist_etterprovd`.
    """
    ten = _tenantnavn("avledning")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        kid = _registrer(c, ten, eier)
        _etterprov(c, ten, kid, eier, dager_siden=10, ref="SAK-NY")
        _, ny_dato, ny_ref, _ = _kontrollrad(migrator, ten, kid)
        # Den ETTERSLEPTE registreringen: utført 40 døgn siden, skrevet
        # inn nå.
        _etterprov(c, ten, kid, eier, dager_siden=40, ref="SAK-GAMMEL")
        _, dato, ref, _ = _kontrollrad(migrator, ten, kid)
        assert (dato, ref) == (ny_dato, ny_ref), \
            "en etterslept etterprøving flyttet tilstanden bakover"
        # …men historikken har BEGGE. Det er den revisor ber om.
        _sett_kontekst(migrator, ten)
        refs = sorted(r[0] for r in migrator.execute(
            "SELECT evidens_ref FROM etterproving WHERE tenant=%s"
            "   AND kontroll_id=%s", (ten, kid)).fetchall())
        migrator.rollback()
        assert refs == ["SAK-GAMMEL", "SAK-NY"]
    finally:
        c.close()


# ---------------------------------------------------------------------------
# INVARIANT 4: forbigatt_etterproving_uten_funn
# ---------------------------------------------------------------------------

@pg
def test_invariant_forbigatt_etterproving_uten_funn(migrator):
    """En kontroll forbi sin etterprøvingsfrist er et FUNN — ikke en rad
    som stille blir gammel. Og ETT funn, ikke ett per kjøring: en daglig
    sveip over en kontroll som har vært forbigått i et år skal gi ETT
    funn, ikke 365.

    Porten måler tre ting i rekkefølge:
      * en kontroll INNENFOR fristen gir INGEN funn (ellers måler porten
        bare at sveipen skriver rader);
      * en kontroll FORBI fristen gir ett, med antall døgn;
      * en sveip nummer to gir det SAMME funnet, med et nyere
        `sist_sett_sveip` og en urørt `forst_sett`.
      * …og et funn som ikke lenger gjelder LUKKES, det slettes ikke: at
        noe VAR forbigått er også historikk.

    MUTASJONEN SOM DREPER DENNE: bytt `ON CONFLICT DO NOTHING`/
    NOT EXISTS-leddet i sveipen mot en ren INSERT. Funnlisten vokser da
    med kadensen, og folk lærer seg å overse den.
    """
    ten = _tenantnavn("forbigatt")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _sv()
    try:
        kid = _registrer(c, ten, eier, dogn=90)
        _etterprov(c, ten, kid, eier, dager_siden=10, ref="SAK-FERSK")
        _sveip(v)
        assert not [f for f in _funn(migrator, ten)
                    if f[1] == "etterproving_forbigatt"], \
            "en kontroll innenfor fristen ble et funn"

        # Forbi fristen: siste etterprøving for 131 døgn siden, intervall
        # 90 → 41 døgn over.
        gammel = _registrer(c, ten, eier, krav="A.5.1",
                            beskrivelse="Policy for infosikkerhet", dogn=90)
        _etterprov(c, ten, gammel, eier, dager_siden=131, ref="SAK-GAMMEL")
        _sveip(v)
        funn = [f for f in _funn(migrator, ten)
                if f[1] == "etterproving_forbigatt"]
        assert len(funn) == 1 and funn[0][0] == gammel, funn
        assert funn[0][2] == 41, f"døgnene over fristen er {funn[0][2]}"
        forst, sist = funn[0][4], funn[0][5]

        # SVEIP NUMMER TO: samme funn, ferskere observasjon.
        _sveip(v)
        funn2 = [f for f in _funn(migrator, ten)
                 if f[1] == "etterproving_forbigatt"]
        assert len(funn2) == 1, "to sveip ga to funn"
        assert funn2[0][4] == forst, "førstegangsobservasjonen ble flyttet"
        assert funn2[0][5] >= sist

        # LUKKINGEN: kontrollen etterprøves, funnet lukkes — raden består.
        _etterprov(c, ten, gammel, eier, ref="SAK-NY")
        _sveip(v)
        assert not [f for f in _funn(migrator, ten)
                    if f[1] == "etterproving_forbigatt"]
        alle = [f for f in _funn(migrator, ten, bare_apne=False)
                if f[1] == "etterproving_forbigatt"]
        assert len(alle) == 1 and alle[0][3] is False, \
            "funnet ble slettet i stedet for lukket"
    finally:
        c.close()
        v.close()


@pg
def test_ikke_relevant_lukker_funnet_og_koster_en_begrunnelse(migrator):
    """`ikke_relevant` er en BESLUTNING, ikke et fravær.

    To ting måles: at den koster en skreven begrunnelse (i CHECK-en OG i
    dørens RAISE), og at den lønner seg — en kontroll man har skrevet ned
    hvorfor man har valgt bort, skal ikke fortsette å produsere funn.
    Uten det siste ville begrunnelsen bare vært et skjema å fylle ut.
    """
    ten = _tenantnavn("ikke-relevant")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _sv()
    try:
        kid = _registrer(c, ten, eier, dogn=1)
        _etterprov(c, ten, kid, eier, dager_siden=30, ref="SAK-X")
        _sveip(v)
        assert [f for f in _funn(migrator, ten)
                if f[1] == "etterproving_forbigatt"]

        # CHECK-en ved direkte DML.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_compliance_eier")
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(
                "UPDATE kontroll SET status='ikke_relevant',"
                "       ikke_relevant_ts=now(), ikke_relevant_av='test'"
                " WHERE tenant=%s AND kontroll_id=%s", (ten, kid))
        migrator.rollback()

        # Dørens RAISE.
        for tom in (None, "", "   "):
            _sett_kontekst(c, ten)
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                c.execute("SELECT m34_marker_ikke_relevant(%s,%s,%s,%s)",
                          (ten, kid, tom, "u-test"))
            c.rollback()

        _sett_kontekst(c, ten)
        c.execute("SELECT m34_marker_ikke_relevant(%s,%s,%s,%s)",
                  (ten, kid, "Vi har ingen slike systemer.", "u-test"))
        c.commit()
        status, _, _, begrunnelse = _kontrollrad(migrator, ten, kid)
        assert status == "ikke_relevant"
        assert begrunnelse.startswith("Vi har ingen slike")

        _sveip(v)
        assert not [f for f in _funn(migrator, ten)
                    if f[1] == "etterproving_forbigatt"], \
            "en skreven beslutning fortsatte å produsere funn"
    finally:
        c.close()
        v.close()


# ---------------------------------------------------------------------------
# INVARIANT 5: tenantlekkasje_i_kontrollregister
# ---------------------------------------------------------------------------

@pg
def test_invariant_tenantlekkasje_direkte(migrator):
    """RLS er den bindende porten, ikke et predikat i en dør.

    Tre ting måles: at et direkte SELECT som dørenes eier bare ser egen
    tenant, at sveipens kryss-tenant-vindu FINNES (ellers ville den aldri
    sett en tenant, og hele sveipen vært død), og at vinduet er STENGT så
    snart en tenantkontekst er satt — SP-1 er ikke kallerens frie valg.
    """
    a, b = _tenantnavn("lekk-a"), _tenantnavn("lekk-b")
    eier_a, eier_b = _bruker(migrator, a), _bruker(migrator, b)
    c = _rt()
    try:
        kid_a = _registrer(c, a, eier_a, krav="A-KRAV",
                           beskrivelse="A sin kontroll")
        kid_b = _registrer(c, b, eier_b, krav="B-KRAV",
                           beskrivelse="B sin kontroll")

        # 1. Direkte DML, som dørenes eier, MED kontekst på A.
        _sett_kontekst(migrator, a)
        migrator.execute("SET LOCAL ROLE disponit_compliance_eier")
        sett = {r[0] for r in migrator.execute(
            "SELECT kontroll_id FROM kontroll").fetchall()}
        migrator.rollback()
        assert kid_a in sett and kid_b not in sett

        # 2. Sveipens vindu finnes — UTEN kontekst ser eieren begge.
        migrator.execute("SELECT set_config('disponit.tenant','',true)")
        migrator.execute("SET LOCAL ROLE disponit_compliance_eier")
        uten = {r[0] for r in migrator.execute(
            "SELECT kontroll_id FROM kontroll").fetchall()}
        migrator.rollback()
        assert {kid_a, kid_b} <= uten, \
            "sveipens vindu finnes ikke — den ville aldri sett en tenant"

        # 3. SP-1: parameteret er ikke kallerens frie valg.
        _sett_kontekst(c, a)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM m34_kontrollbilde(%s,%s)",
                      (b, 50)).fetchall()
        c.rollback()

        # …og lesedøren i EGEN kontekst gir bare egne rader.
        _sett_kontekst(c, a)
        rader = c.execute(
            "SELECT kontroll_id, beskrivelse FROM m34_kontrollbilde(%s,%s)",
            (a, 50)).fetchall()
        c.rollback()
        assert [r[1] for r in rader] == ["A sin kontroll"]
    finally:
        c.close()


@pg
def test_invariant_tenantlekkasje_over_api(migrator, klient):
    """Samme invariant, over HTTP: økten hos A får aldri se Bs
    kontroller. Tenanten kommer fra ØKTEN, aldri fra kroppen eller en
    parameter."""
    b = _tenantnavn("api-b")
    eier_a = _bruker(migrator, TENANT)
    eier_b = _bruker(migrator, b)
    c = _rt()
    try:
        _registrer(c, TENANT, eier_a, krav="A-OVER-API",
                   beskrivelse="A sin kontroll over API")
        _registrer(c, b, eier_b, krav="B-OVER-API",
                   beskrivelse="B sin kontroll over API")
    finally:
        c.close()
    cookie, _csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/compliance", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    krav = [k["krav_ref"] for k in r.json()["kontroller"]]
    assert "A-OVER-API" in krav
    assert "B-OVER-API" not in krav


# ---------------------------------------------------------------------------
# INVARIANT 6: ui_axe_alvorlige_brudd
# ---------------------------------------------------------------------------

def test_invariant_ui_axe_bor_i_js_suiten():
    """Flateporten kjøres av `npm test`, ikke herfra — men den SKAL
    finnes, og en modul som slettet flatetesten sin skulle ikke kunne bli
    grønn her. Porten binder derfor filen og de tre skjermene den måler.
    """
    fil = (ROT / "platform" / "core" / "ui" / "test" / "compliance.test.js")
    kilde = fil.read_text(encoding="utf-8")
    assert kilde.count("alvorligeBrudd(") >= 4, \
        "flateporten måler ikke axe på hver skjerm flaten kan stå i"
    assert "visCompliance" in kilde


# ---------------------------------------------------------------------------
# Append-only, avvik og evidenskjeden
# ---------------------------------------------------------------------------

@pg
def test_etterprovingshistorikken_er_append_only(migrator):
    """Historikken er evidensen. En etterprøving som kan endres i
    etterkant er ingen etterprøving, og en som kan slettes er en revisjon
    uten spor.

    BEGGE veier måles — UPDATE og DELETE — og som DØRENES EIER, ikke som
    en tilfeldig rolle: en sperre som bare gjelder den svakeste rollen er
    ingen sperre.
    """
    ten = _tenantnavn("append")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        kid = _registrer(c, ten, eier)
        _etterprov(c, ten, kid, eier, ref="SAK-A")
        for setning in (
                "UPDATE etterproving SET evidens_ref='SAK-B'"
                " WHERE tenant=%s",
                "UPDATE etterproving SET utfall='avvik' WHERE tenant=%s",
                "DELETE FROM etterproving WHERE tenant=%s"):
            _sett_kontekst(migrator, ten)
            migrator.execute("SET LOCAL ROLE disponit_compliance_eier")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                migrator.execute(setning, (ten,))
            migrator.rollback()
        # …og TRUNCATE, som er den veien som ellers ville tatt alt på én
        # gang.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_compliance_eier")
        with pytest.raises(psycopg.Error):
            migrator.execute("TRUNCATE etterproving")
        migrator.rollback()
        # Raden står.
        _sett_kontekst(migrator, ten)
        assert migrator.execute(
            "SELECT evidens_ref FROM etterproving WHERE tenant=%s",
            (ten,)).fetchone()[0] == "SAK-A"
        migrator.rollback()
    finally:
        c.close()


@pg
def test_avvik_uten_beskrivelse_avvises(migrator):
    """Et avvik uten beskrivelse er et avvik ingen kan lukke.

    To lag: CHECK-en ved direkte DML, og dørens egen RAISE. Og den
    lovlige veien måles med: et registrert avvik setter kontrollen til
    `ikke_oppfylt` — et avvik er ikke «litt oppfylt».
    """
    ten = _tenantnavn("avvik")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        kid = _registrer(c, ten, eier)
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_compliance_eier")
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(
                "INSERT INTO etterproving (tenant, etterproving_id,"
                "  kontroll_id, utfort, utfort_av_bruker_id, evidens_ref,"
                "  utfall, opprettet_av)"
                " VALUES (%s,%s,%s,current_date,%s,'SAK-1','avvik','t')",
                (ten, uuid.uuid4(), kid, eier))
        migrator.rollback()

        for tom in (None, "", "   "):
            _sett_kontekst(c, ten)
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                c.execute(
                    "SELECT m34_registrer_etterproving(%s,%s,%s,"
                    "       current_date,%s,'SAK-1','avvik',%s,%s)",
                    (ten, uuid.uuid4(), kid, eier, tom, "u-test"))
            c.rollback()

        _etterprov(c, ten, kid, eier, ref="SAK-2", utfall="avvik",
                   avvik="Loggen manglet for to av fem systemer.")
        status, dato, ref, _ = _kontrollrad(migrator, ten, kid)
        assert status == "ikke_oppfylt", "et avvik ble lest som oppfylt"
        # …men evidensen står likevel: vi VET hva vi så, og når.
        assert (dato is not None) and ref == "SAK-2"
    finally:
        c.close()


# ---------------------------------------------------------------------------
# HTTP-feilveien
# ---------------------------------------------------------------------------

@pg
@dekker("kontroll_ulovlig_tilstand")
def test_http_etterproving_uten_evidens_er_409(migrator, klient):
    """FEILVEIEN, ende til ende.

    En evidenshenvisning som er tom svarer 400 (kroppen er feilformet —
    feltet mangler innhold), mens en kontroll som ALT står som ikke
    relevant svarer 409 `kontroll_ulovlig_tilstand`: kroppen ER velformet,
    det er TILSTANDEN som sier nei. Forskjellen er hele forklaringen
    mennesket i flaten trenger, og den skal ikke være 500.

    Merk hvem som feller dommen: API-et sjekker ikke tilstanden. Det
    kaller døren og oversetter dørens ERRCODE.

    MUTASJONEN SOM DREPER DENNE: la `_doerfeil` mappe
    `invalid_parameter_value` til 500, eller la endepunktet
    forhåndssjekke tilstanden og svare 400.
    """
    eier = _bruker(migrator, TENANT)
    cookie, csrf = _browserokt(migrator, ["admin"])

    r = _post(klient, cookie, csrf, "/v1/compliance/kontroll",
              {"rammeverk": "ISO 27001", "rammeverk_versjon": "2022",
               "krav_ref": "A.9.9", "beskrivelse": "Tilgangsgjennomgang",
               "eier_bruker_id": eier, "etterproving_dogn": 90})
    assert r.status_code in (200, 201), r.text
    kid = r.json()["kontroll_id"]
    assert r.json()["ny"] is True

    # Tom henvisning: kroppen er feilformet, ikke tilstanden.
    r = _post(klient, cookie, csrf,
              f"/v1/compliance/kontroll/{kid}/etterproving",
              {"utfort": "2026-09-01", "utfort_av_bruker_id": eier,
               "evidens_ref": "   ", "utfall": "oppfylt"})
    assert r.status_code == 400, r.text
    assert r.json()["feil"] == "request_feilformet"

    # En utfører som ikke er medlem: velformet kropp, TILSTANDEN sier nei.
    r = _post(klient, cookie, csrf,
              f"/v1/compliance/kontroll/{kid}/etterproving",
              {"utfort": "2026-09-01", "utfort_av_bruker_id": "bid_fremmed",
               "evidens_ref": "SAK-1", "utfall": "oppfylt"})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "kontroll_ulovlig_tilstand"

    # Den lovlige veien.
    r = _post(klient, cookie, csrf,
              f"/v1/compliance/kontroll/{kid}/etterproving",
              {"utfort": "2026-09-01", "utfort_av_bruker_id": eier,
               "evidens_ref": "SAK-2026-931", "utfall": "oppfylt"})
    assert r.status_code in (200, 201), r.text

    # `ikke_relevant` to ganger: nå er det TILSTANDEN som sier nei.
    r = _post(klient, cookie, csrf,
              f"/v1/compliance/kontroll/{kid}/ikke-relevant",
              {"begrunnelse": "Vi har ingen slike systemer."})
    assert r.status_code in (200, 201), r.text
    r = _post(klient, cookie, csrf,
              f"/v1/compliance/kontroll/{kid}/ikke-relevant",
              {"begrunnelse": "Vi har fortsatt ingen slike systemer."})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "kontroll_ulovlig_tilstand"

    # Den FØRSTE begrunnelsen står — det avviste kallet skrev ingenting.
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT ikke_relevant_begrunnelse FROM kontroll"
        " WHERE tenant=%s AND kontroll_id=%s",
        (TENANT, kid)).fetchone()[0] == "Vi har ingen slike systemer."
    migrator.rollback()


@pg
def test_http_registrering_og_etterproving_er_idempotente(migrator, klient):
    """SP-2 (m35/096-formen) på BEGGE skriveveiene som føder en rad.

    En dobbelt bokført etterprøving ville vært et revisjonsspor som lyver
    om hvor mange ganger noe faktisk ble kontrollert — det er en verre
    feil enn en dobbelt registrert kontroll, og derfor måles begge.
    """
    eier = _bruker(migrator, TENANT)
    cookie, csrf = _browserokt(migrator, ["admin"])
    nokkel = secrets.token_urlsafe(24)
    kropp = {"rammeverk": "NIS2", "krav_ref": "par 21 nr. 2 f",
             "beskrivelse": "Test av beredskapsplan",
             "eier_bruker_id": eier, "etterproving_dogn": 180}

    r1 = _post(klient, cookie, csrf, "/v1/compliance/kontroll", kropp,
               idem=nokkel)
    assert r1.status_code in (200, 201), r1.text
    assert r1.json()["ny"] is True
    r2 = _post(klient, cookie, csrf, "/v1/compliance/kontroll", kropp,
               idem=nokkel)
    assert r2.json()["kontroll_id"] == r1.json()["kontroll_id"]
    assert r2.json()["ny"] is False, "gjenspillet fødte en ny kontroll"

    endret = dict(kropp, etterproving_dogn=365)
    r3 = _post(klient, cookie, csrf, "/v1/compliance/kontroll", endret,
               idem=nokkel)
    assert r3.status_code == 409, r3.text
    assert r3.json()["feil"] == "idempotenskonflikt"

    kid = r1.json()["kontroll_id"]
    ep_nokkel = secrets.token_urlsafe(24)
    ep = {"utfort": "2026-08-14", "utfort_av_bruker_id": eier,
          "evidens_ref": "OEV-2026-2", "utfall": "oppfylt"}
    e1 = _post(klient, cookie, csrf,
               f"/v1/compliance/kontroll/{kid}/etterproving", ep,
               idem=ep_nokkel)
    assert e1.status_code in (200, 201), e1.text
    assert e1.json()["ny"] is True
    e2 = _post(klient, cookie, csrf,
               f"/v1/compliance/kontroll/{kid}/etterproving", ep,
               idem=ep_nokkel)
    assert e2.json()["ny"] is False, \
        "gjenspillet bokførte etterprøvingen en gang til"
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT count(*) FROM etterproving WHERE tenant=%s"
        "   AND kontroll_id=%s", (TENANT, kid)).fetchone()[0] == 1
    migrator.rollback()


@pg
def test_uendelig_og_fremtidig_dato_avvises(migrator):
    """🔴 CodeRabbit, kritisk: `date 'infinity'` er en LOVLIG DATE.

    Sluppet gjennom ville den vært giftig to steder på én gang:
    `sist_etterprovd + etterproving_dogn` blir `infinity`, og
    `current_date - infinity` sprenger `int` i BÅDE `m34_kontrollbilde`
    og `m34_funnkandidater`. Den siste er den alvorlige: sveipen er
    kryss-tenant og kjører alle tenanter i én transaksjon, så ÉN slik rad
    hos ÉN kunde ville stanset etterprøvingssveipen for HELE plattformen
    — stille, hver natt.

    En fremtidig dato er den mildere varianten av samme feil: kontrollen
    forsvinner fra funnene for alltid, uten at noen har skrevet en
    begrunnelse.

    To lag, som resten av modulen: CHECK-en stenger uendeligheten for
    enhver skrivevei (den kan ikke lese klokka — `current_date` er ikke
    IMMUTABLE), og dørens RAISE stenger fremtiden.
    """
    ten = _tenantnavn("infinity")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        kid = _registrer(c, ten, eier)
        for uttrykk in ("'infinity'::date", "current_date + 1"):
            _sett_kontekst(c, ten)
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                c.execute(
                    "SELECT m34_registrer_etterproving(%s,%s,%s," + uttrykk
                    + ",%s,'SAK-1','oppfylt',NULL,%s)",
                    (ten, uuid.uuid4(), kid, eier, "u-test"))
            c.rollback()
        # CHECK-en, ved direkte DML som dørenes eier: uendeligheten er
        # urepresenterbar i BASEN, ikke bare avvist i døren.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_compliance_eier")
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(
                "INSERT INTO etterproving (tenant, etterproving_id,"
                " kontroll_id, utfort, utfort_av_bruker_id, evidens_ref,"
                " utfall, opprettet_av)"
                " VALUES (%s,%s,%s,'infinity'::date,%s,'SAK-1',"
                "         'oppfylt','t')",
                (ten, uuid.uuid4(), kid, eier))
        migrator.rollback()
        # …og på avledningen, med vakten avskrudd så det er CHECK-en og
        # ikke evidenskoblingen som feller den.
        _sett_kontekst(migrator, ten)
        migrator.execute(
            "ALTER TABLE kontroll DISABLE TRIGGER m34_kontroll_vakt")
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(
                "UPDATE kontroll SET sist_etterprovd='infinity'::date,"
                "       evidens_ref='SAK-1' WHERE tenant=%s"
                "   AND kontroll_id=%s", (ten, kid))
        migrator.rollback()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Sveipearbeideren — artefaktryddingens form, ORDRETT
# ---------------------------------------------------------------------------

sveiperolle = pytest.mark.skipif(
    not COMPLIANCESVEIP_DSN,
    reason="DISPONIT_TEST_COMPLIANCESVEIP_DSN ikke satt")


@pg
@sveiperolle
def test_overlappende_sveip_hopper_over_og_rorer_ikke_feiltelleren(
        migrator, tmp_path, monkeypatch):
    """`artefaktrydding`-formen, ordrett: en kjøring som fant
    arbeidernøkkelen opptatt har verken lyktes eller feilet.

    Skrev den 0 her, ville en overlappende kjøring (manuell drift, flere
    verter, en henger som holder låsen) slettet en alt opptelt feil, og
    alarmen etter to sammenhengende feil ville aldri nådd frem.
    """
    from drift import compliancesveip
    from drift import kjor_compliancesveip as kjorer

    holder = psycopg.connect(MIGRATOR_DSN, autocommit=True)
    v = _sv()
    try:
        holder.execute("SELECT pg_advisory_lock(%s)",
                       (compliancesveip.ARBEIDERNOKKEL,))
        r = compliancesveip.kjor(v, tidligere_feil=1)
        assert r.hoppet_over is True
        assert r.feilet is False and r.alarm_utlost is False
        assert (r.tenanter, r.nye, r.oppdaterte, r.lukkede,
                r.avkortet) == (0, 0, 0, 0, 0)

        # …og `main()` lar telleren stå NØYAKTIG som den sto.
        tilstand = tmp_path / "compliancesveip.json"
        tilstand.write_text(json.dumps({"feil": 1}), encoding="utf-8")
        monkeypatch.setenv("DISPONIT_COMPLIANCESVEIPTILSTAND",
                           str(tilstand))
        monkeypatch.setenv("DISPONIT_COMPLIANCESVEIP_URL",
                           COMPLIANCESVEIP_DSN)
        assert kjorer.main() == 0
        assert json.loads(
            tilstand.read_text(encoding="utf-8"))["feil"] == 1, \
            "den hoppet over kjøringen slettet en alt opptelt feil"
    finally:
        holder.execute("SELECT pg_advisory_unlock(%s)",
                       (compliancesveip.ARBEIDERNOKKEL,))
        holder.close()
        v.close()


def test_alarm_etter_to_sammenhengende_feilede_kjoringer(tmp_path,
                                                         monkeypatch):
    """En stille etterprøvingssveip er et kontrollregister som eldes uten
    at noen ser det — altså nøyaktig tilstanden modulen finnes for å gjøre
    synlig. Et register der ingenting er forbigått fordi ingen målte, er
    ikke et grønt register.

    Første feil teller opp uten alarm; den ANDRE alarmerer — og
    JSON-linja bærer begge tallene, så journalen kan svare på spørsmålet
    uten å måtte lese tilstandsfilen.
    """
    from drift import kjor_compliancesveip as kjorer

    tilstand = tmp_path / "compliancesveip.json"
    monkeypatch.setenv("DISPONIT_COMPLIANCESVEIPTILSTAND", str(tilstand))
    monkeypatch.setenv("DISPONIT_COMPLIANCESVEIP_URL",
                       "postgresql://finnes-ikke@127.0.0.1:1/nei")
    monkeypatch.setattr(
        kjorer, "_koble",
        lambda dsn: (_ for _ in ()).throw(RuntimeError("nede")))

    linjer = []
    monkeypatch.setattr("builtins.print",
                        lambda *a, **k: linjer.append(a[0]) if not k.get(
                            "file") else None)
    assert kjorer.main() == 1
    forste = json.loads(linjer[-1])
    assert forste["feilet"] == 1 and forste["sammenhengende_feil"] == 1
    assert forste["alarm"] == 0, "alarm etter ÉN feil er en falsk alarm"

    assert kjorer.main() == 1
    andre = json.loads(linjer[-1])
    assert andre["sammenhengende_feil"] == 2
    assert andre["alarm"] == 1, \
        "to sammenhengende feilede kjøringer alarmerte ikke"
    assert andre["tilstand_lagret"] == 1
    assert json.loads(tilstand.read_text(encoding="utf-8"))["feil"] == 2


def test_sveipen_nekter_aa_starte_uten_egen_dsn(tmp_path, monkeypatch):
    """INGEN fallback til `DATABASE_URL`. Runtime-rollen har med vilje
    ikke EXECUTE på sveipen (100 REVOKEr den), så en fallback ville bare
    byttet en tydelig oppstartsnekt mot «permission denied» i journalen
    hver natt — og en jobb som feiler likt hver natt er en jobb ingen
    leser."""
    from drift import kjor_compliancesveip as kjorer
    monkeypatch.setenv("DISPONIT_COMPLIANCESVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.delenv("DISPONIT_COMPLIANCESVEIP_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://skal-ikke-brukes/x")
    assert kjorer.main() == 2
    kilde = (ROT / "platform" / "drift"
             / "kjor_compliancesveip.py").read_text(encoding="utf-8")
    kode = "\n".join(l for l in kilde.splitlines()
                     if not l.lstrip().startswith("#"))
    assert "DATABASE_URL" not in kode, \
        "kjøreren har fått en fallback til runtime-DSN-en"


@pg
@sveiperolle
def test_sveipekjoringen_gir_en_json_linje_med_tallene(migrator, tmp_path,
                                                      monkeypatch):
    """Én JSON-linje per kjøring, med tallene jobben faktisk målte — en
    jobb som ikke kunne måle rapporterer FUNN, aldri null.

    `avkortet` STÅR I LINJA. Traff sveipen taket sitt, er kjøringen ikke
    feilet (funnene er idempotente, neste kjøring tar igjen resten) — men
    den er heller ikke ferdig, og den forskjellen skal være lesbar i
    journalen uten å måtte telles i basen etterpå.
    """
    from drift import kjor_compliancesveip as kjorer
    ten = _tenantnavn("json")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        kid = _registrer(c, ten, eier, dogn=30)
        _etterprov(c, ten, kid, eier, dager_siden=90, ref="SAK-Q")
    finally:
        c.close()
    monkeypatch.setenv("DISPONIT_COMPLIANCESVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.setenv("DISPONIT_COMPLIANCESVEIP_URL", COMPLIANCESVEIP_DSN)
    linjer = []
    monkeypatch.setattr("builtins.print",
                        lambda *a, **k: linjer.append(a[0]) if not k.get(
                            "file") else None)
    assert kjorer.main() == 0
    linje = json.loads(linjer[-1])
    assert linje["hendelse"] == "compliancesveip"
    assert linje["feilet"] == 0 and linje["hoppet_over"] == 0
    assert linje["nye_funn"] >= 1 and linje["tenanter"] >= 1
    assert set(linje) == {"hendelse", "tenanter", "nye_funn",
                          "oppdaterte_funn", "lukkede_funn", "avkortet",
                          "feilet", "hoppet_over", "sammenhengende_feil",
                          "alarm", "tilstand_lagret"}


@pg
@sveiperolle
def test_sveiperollen_har_noyaktig_en_rettighet(migrator):
    """Rollen ER jobben (095-formen): ÉN EXECUTE, INGEN
    tabellrettigheter.

    Delte den DSN med runtime, ville sveipen måttet grantes til
    web-API-rollen; sveipen er kryss-tenant og setter selv
    RLS-konteksten, så det ville gitt hele forespørselsveien nøyaktig det
    vinduet denne rollen finnes for å nekte den.
    """
    v = _sv()
    try:
        # Den ene tingen den KAN.
        v.execute("SELECT * FROM m34_sveip_etterprovinger(1)").fetchone()
        v.commit()
        # …og alt den ikke kan: ingen tabell, ingen lesedør.
        for setning, param in (
                ("SELECT count(*) FROM kontroll", None),
                ("SELECT count(*) FROM etterproving", None),
                ("SELECT count(*) FROM kontrollfunn", None),
                ("SELECT count(*) FROM rammeverk", None),
                ("SELECT * FROM m34_kontrollbilde(%s,%s)", ("t", 1)),
                ("SELECT * FROM m34_funnkandidater(%s, current_date)",
                 ("t",))):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                v.execute(setning, param)
            v.rollback()
    finally:
        v.close()
    # …og runtime kan IKKE kjøre sveipen: en rettighet som bare slutter å
    # bli gitt er ikke trukket tilbake (035/091/095).
    c = _rt()
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM m34_sveip_etterprovinger(1)")
        c.rollback()
    finally:
        c.close()


@pg
@sveiperolle
def test_sveipen_nekter_aa_kjore_med_en_tenantkontekst(migrator):
    """Sveipen er KRYSS-TENANT og kjøres uten kontekst. En kaller som
    har satt en kontekst ber om noe annet enn det funksjonen gjør — og da
    er «nei» riktigere enn et halvt svar over én tenant.

    Uten dette leddet ville en kontekst i sesjonen slått av
    kryss-tenant-policyen (`m34_sveip_tenantliste` krever at konteksten
    er tom), tenantlisten blitt tom, og sveipen rapportert null funn i
    stedet for å si fra. Nøyaktig den stille nullen «en jobb som ikke
    kunne måle rapporterer FUNN, aldri null» handler om.
    """
    v = _sv()
    try:
        v.execute("SELECT set_config('disponit.tenant','t-noe',true)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            v.execute("SELECT * FROM m34_sveip_etterprovinger(1)")
        v.rollback()
    finally:
        v.close()


# ---------------------------------------------------------------------------
# Migrasjonens form: SP-10-premisset og rettighetsspeilet
# ---------------------------------------------------------------------------

@pg
def test_migrasjonen_er_kjort_og_bytebundet(migrator):
    """100 står i `migrasjoner` med checksum lik sha256 av filbytene i
    treet — den TOMME kjøringen målt direkte, og samme byte-binding
    fasiten pinner mot main."""
    cs = migrator.execute(
        "SELECT checksum FROM migrasjoner WHERE versjon=100").fetchone()
    migrator.rollback()
    assert cs is not None, "100 er ikke kjørt i testbasen"
    fil_sha = hashlib.sha256(MIGRASJON.read_bytes()).hexdigest()
    assert cs[0] == fil_sha, \
        "100 i treet er ikke bytene basen kjørte — historikk er immutable"
    fasit = json.loads(
        (ROT / "platform" / "core" / "db" / "migrasjons-fasit.json")
        .read_text(encoding="utf-8"))
    assert fasit.get("100_m34_kontrollregister.sql") == fil_sha, \
        "fasiten pinner andre bytes enn treet bærer"


def test_migrasjonen_er_ren_ddl():
    """SP-10s premiss (047-klassen): masse-DML i en migrasjon kan køe
    utsatte triggerhendelser som ALTER-setninger nekter å passere. 100 har
    ingen slik seed — den er ren DDL — og DA er «grønn fra tom base» og
    «grønn mot seedet base» det samme utsagnet.

    Og det er ikke bare et premiss her: 100 rører HELLER INGEN eksisterende
    tabell med en ALTER. Den legger fire NYE tabeller ved siden av det som
    finnes, og de eneste setningene som treffer eksisterende objekter er
    GRANT-er. En bebodd base og en tom base er dermed samme migrasjon.
    """
    import pglast
    tekst = MIGRASJON.read_text(encoding="utf-8")
    dml = [type(raa.stmt).__name__
           for raa in pglast.parse_sql(tekst)
           if type(raa.stmt).__name__ in ("InsertStmt", "UpdateStmt",
                                          "DeleteStmt")]
    assert not dml, (
        f"100 bærer toppnivå-DML {dml} — da er den en backfill og skal"
        " registrere seed+måling i sp10-provekjoring.py")
    # `ALTER TABLE` finnes bare på modulens EGNE, nyopprettede tabeller
    # (RLS-bryterne). Ingen fremmed tabell endres.
    egne = {"rammeverk", "kontroll", "etterproving", "kontrollfunn"}
    for raa in pglast.parse_sql(tekst):
        if type(raa.stmt).__name__ == "AlterTableStmt":
            navn = raa.stmt.relation.relname
            assert navn in egne, \
                f"100 endrer den eksisterende tabellen {navn}"


def test_migrasjonen_navngir_aldri_runtime_rollen():
    """056/057/089/096-formen: `disponit` er bare LOKALNAVNET på
    web-API-rollen, og `migrer.py` er eneste rettighetskilde for den
    konfigurerte rollen. En GRANT til runtime i migrasjonen ville lagt
    rettighetsmodellen to steder — og det ene stedet ville vært usant på
    enhver installasjon som kaller rollen noe annet. REVOKE-en er lovlig
    og nødvendig (091/095-formen): en rettighet som bare slutter å bli
    gitt er ikke trukket tilbake."""
    for linje in MIGRASJON.read_text(encoding="utf-8").splitlines():
        if linje.lstrip().startswith("--"):
            continue
        assert "TO disponit;" not in linje, \
            f"100 grantar direkte til runtime-rollen: {linje!r}"


def test_kjoreren_speiler_100_rettighetene():
    """Rettighetsspeilet i `migrer.py` (057/096-portformen), og den
    SKARPESTE delen av det: registeret har INGEN tabellrettigheter for
    noen rolle utenom dørenes egen eier.

      * runtime får EXECUTE på lesedøren og de tre skrivedørene — og
        ALDRI på sveipen (kryss-tenant, 038-reaperens snitt);
      * sveiperollen får EXECUTE på sveipen og ingenting annet — heller
        ikke på lesedøren eller det interne kandidatleddet;
      * ingen SELECT/INSERT/UPDATE/DELETE på `rammeverk`, `kontroll`,
        `etterproving` eller `kontrollfunn` noe sted i kjøreren.
    """
    kjorer = (ROT / "deploy" / "staging" / "migrer.py").read_text(
        encoding="utf-8")
    for dor in ("m34_kontrollbilde(TEXT, INT)",
                "m34_registrer_kontroll(TEXT, UUID, TEXT, TEXT, TEXT,"
                " TEXT, TEXT, INT, TEXT)",
                "m34_registrer_etterproving(TEXT, UUID, UUID, DATE, TEXT,"
                " TEXT, TEXT, TEXT, TEXT)",
                "m34_marker_ikke_relevant(TEXT, UUID, TEXT, TEXT)",
                "m34_sveip_etterprovinger(INT)"):
        assert f"GRANT EXECUTE ON FUNCTION {dor} TO {{rolle}};" in kjorer, dor
    assert ("REVOKE ALL ON FUNCTION m34_sveip_etterprovinger(INT)"
            " FROM {rolle};") in kjorer, \
        "runtime får beholde kryss-tenant-sveipen"
    assert ("REVOKE ALL ON FUNCTION m34_kontrollbilde(TEXT, INT)"
            " FROM {rolle};") in kjorer, \
        "sveiperollen får beholde en lesevei den ikke trenger"
    for tabell in ("rammeverk", "kontroll", "etterproving", "kontrollfunn"):
        for verb in ("SELECT ON", "INSERT ON", "UPDATE ON", "DELETE ON"):
            assert f"{verb} {tabell}" not in kjorer, \
                f"en rolle har fått {verb} {tabell} utenom dørene"


def test_enheten_og_rollen_er_registrert_i_deployet():
    """Sveipen har sin EGEN LOGIN-rolle og sin egen timer (095-formen).
    Porten binder de fire stedene enheten må stå for at den skal kjøre —
    og for at en mislykket deploy skal kunne reversere den."""
    opp = (ROT / "deploy" / "staging" / "opp.sh").read_text(encoding="utf-8")
    for bit in ("disponit-compliancesveip.service"
                " disponit-compliancesveip.timer",
                "DISPONIT_COMPLIANCESVEIP_URL mangler",
                "skriv_cred compliancesveip DISPONIT_COMPLIANCESVEIP_URL",
                "systemctl enable --now disponit-compliancesveip.timer"):
        assert bit in opp, bit
    # SELVREVERS: en timer som ikke står her blir ikke gjenopprettet når
    # deployen ruller tilbake, og sveipen ville vært stille i stedet for
    # å komme tilbake.
    selvrevers = opp[opp.index("SELVREVERS_ENHETER="):]
    selvrevers = selvrevers[:selvrevers.index('"\n', 20) + 1]
    assert "disponit-compliancesveip.timer" in selvrevers

    enhet = (ROT / "deploy" / "staging"
             / "disponit-compliancesveip.service").read_text(encoding="utf-8")
    assert "LoadCredential=DISPONIT_COMPLIANCESVEIP_URL:" in enhet
    assert "drift.kjor_compliancesveip" in enhet
    assert "StateDirectory=disponit" in enhet


def test_grensen_dekker_manifestets_seks_invarianter():
    """Grensen `m34-v1` ble registrert FØR koden (§0-regelen). Porten
    pinner den mot planen, ikke mot listen selv: seks invarianter, null
    tillatte brudd, og `ddl_begge_kjoringer_gronne` som eneste
    ja-punkt."""
    from manifestskjema import KRAVGRENSER, M34_INVARIANTER
    g = KRAVGRENSER["m34-v1"]
    assert len(M34_INVARIANTER) == len(set(M34_INVARIANTER)) == 6
    assert g["invarianter"] is M34_INVARIANTER
    assert g["krav_ja"] == ("ddl_begge_kjoringer_gronne",)
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    # Punktbindingen er TOM MED VILJE — uflippbar til målingene finnes.
    assert g["punktbinding"] == {}


def test_rutene_og_flaten_er_registrert():
    """`Route()` og `RUTESCOPE` bindes toveis av `test_pr008`; her måles
    SCOPEVALGET, som er en dom og ikke en detalj.

    LESINGEN bærer `security:read` og ikke `decisions:read`: PR-008 §1
    beskriver `security:read` som ops/compliance-scopen på en
    TENANTBUNDET brukersesjon, og `autorisasjon.py` kaller rollen
    `sikkerhet` for «Compliance/ops» med rene ord. Kretsen er dessuten
    snevrere MED VILJE — avviksbeskrivelser og evidenshenvisninger er
    revisjonsmateriale, ikke allmenn tilstandsinnsikt, og verken
    `godkjenner` eller `policyforvalter` har noe der å gjøre.

    SKRIVINGEN GJENBRUKER `bestilling:opprett` — et nytt scope skal ikke
    oppstå av vane.
    """
    from api.app import BROWSER_MUTASJONSSCOPES, LESESCOPES, RUTESCOPE
    assert RUTESCOPE[("GET", "/v1/compliance")] == "security:read"
    assert "security:read" in LESESCOPES
    for sti in ("/v1/compliance/kontroll",
                "/v1/compliance/kontroll/{kontroll_id:uuid}/etterproving",
                "/v1/compliance/kontroll/{kontroll_id:uuid}/ikke-relevant"):
        assert RUTESCOPE[("POST", sti)] == "bestilling:opprett"
    assert "bestilling:opprett" in BROWSER_MUTASJONSSCOPES

    from api.autorisasjon import ROLLE_TIL_SCOPES
    for rolle in ("admin", "sikkerhet"):
        assert "security:read" in ROLLE_TIL_SCOPES[rolle]
    assert "bestilling:opprett" in ROLLE_TIL_SCOPES["admin"]
    # …og den snevrere kretsen er FAKTISK snevrere: rollene som bare
    # leser beslutninger når ikke registeret.
    for rolle in ("godkjenner", "policyforvalter", "leser"):
        assert "security:read" not in ROLLE_TIL_SCOPES[rolle], rolle

    sitekart = (ROT / "platform" / "core" / "ui" / "static" / "js"
                / "sitekart.js").read_text(encoding="utf-8")
    assert ('{ nokkel: "compliance", scope: "security:read",'
            ' modulflate: 34 }') in sitekart


# ---------------------------------------------------------------------------
# Små hjelpere for HTTP-veien (m35/096-formen)
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
        " ('https://m34.test', %s) RETURNING bruker_id",
        ("s34h-" + secrets.token_hex(6),)).fetchone()[0]
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


def _post(klient, cookie, csrf, sti, kropp, idem=None):
    from api import sesjon as sesjonmodul
    return klient.post(sti, json=kropp,
                       cookies={sesjonmodul.C_SESJON: cookie},
                       headers={"X-Disponit-CSRF": csrf,
                                "Idempotency-Key":
                                    idem or secrets.token_urlsafe(24)})
