"""M-21 avtale- og fristagent v1 (migrasjon 096) — grensens seks
invarianter, målt.

Grensen `m21-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR koden
(§0-regelen). Hver invariant har en port her, og hver port måler BÅDE at
den lovlige veien virker og at bruddet avvises — en port som bare måler
det som skal gå igjennom har ikke målt en invariant.

  * `plikt_uten_eier` — direkte DML uten eier avvises (NOT NULL), og
    døren avviser en «eier» som ikke er medlem av tenanten. To lag,
    samme sannhet.
  * `frist_lukket_uten_kvittering` — CHECK-en avviser en lukket plikt
    uten kvittering ved DIREKTE DML, vakten avviser en statusovergang
    uten navngitt aktør, og TIDEN LUKKER INGENTING: en frist får passere,
    sveipen kjøres, og plikten står fortsatt `apen`.
  * `varsel_duplisert_per_varslingspunkt` — tre sveip på samme tilstand
    gir ETT varsel per punkt. Og porten er ikke «aldri varsle igjen»:
    en gjentakende plikt som kvitteres ut ruller til neste forekomst og
    FÅR sine varsler der.
  * `forpass_stanset_ordinaer_sending` — DEN SKARPE. En injisert feil i
    forpasset stanser ikke den ordinære sendingen, og feilen telles
    separat. Porten er skrevet så den ville vært RØD med en naiv
    `forpass(); ordinaer()` i samme transaksjon.
  * `tenantlekkasje_i_pliktregister` — tenant A ser aldri tenant Bs
    plikter, verken ved direkte DML eller over API-et.
  * `ui_axe_alvorlige_brudd` — bor i
    `platform/core/ui/test/avtalefrist.test.js` (jsdom + axe-core), som
    kjøres av `npm test`, ikke herfra.

I tillegg: varsel og anker skrives i SAMME transaksjon (rull tilbake
midtveis — ingen av delene består), `bortfalt` uten begrunnelse avvises,
evidenskjeden får sin rad, og migrasjonen er ren DDL (SP-10s premiss)
og navngir aldri runtime-rollen.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT, VARSEL_DSN,  # noqa: F401
                       app, dekker, klient, migrator, miljo)
from .test_m37 import _sett_kontekst

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "096_m21_pliktregister.sql")

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


def _vs():
    """Varselsenderens rolle — den som har EXECUTE på sveipen og
    ingenting annet."""
    from db.pg import koble
    return koble(VARSEL_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    """Egen tenant per test. Sveipen er kryss-tenant og ser HELE basen,
    så en delt tenant ville gjort testene rekkefølgeavhengige — og en
    test som består fordi naboen ryddet er ingen port."""
    return f"t-m21-{merke}-{secrets.token_hex(4)}"


def _bruker(m, tenant, *, epost=None, aktiv=True, roller=("admin",)):
    """En identitet med medlemskap i tenanten. `epost` gjør den til en
    gyldig e-postmottaker for varselsenderen."""
    profil = {}
    if epost:
        profil = {"epost": epost, "epost_verifisert": True}
    _sett_kontekst(m, tenant)
    bid = m.execute(
        "INSERT INTO brukeridentitet (issuer, sub, profil)"
        " VALUES ('https://m21.test', %s, %s::jsonb) RETURNING bruker_id",
        ("s21-" + secrets.token_hex(6), json.dumps(profil))).fetchone()[0]
    m.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller, aktiv)"
        " VALUES (%s,%s,%s,%s)", (tenant, bid, list(roller), aktiv))
    m.commit()
    return bid


def _registrer(c, tenant, eier, *, tittel="MVA-melding",
               kilde="sktl. par 8-3", frist="now() + interval '5 days'",
               gjentakelse="engang", punkter=None, pid=None,
               aktor="u-test"):
    """Én plikt gjennom døren, som runtime. `frist` er et SQL-uttrykk, så
    testene kan skrive «om fem døgn» uten å regne på klokka."""
    pid = pid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m21_registrer_plikt(%s,%s,%s,%s,%s," + frist + ",%s,%s,%s)",
        (tenant, pid, tittel, eier, kilde, gjentakelse, punkter, aktor))
    c.commit()
    return pid


def _sveip(v, grense=100):
    """Kjør sveipen én gang. -> totalt antall køede varsler.

    TALLET ER PLATTFORMVIDT, ikke tenantens: sveipen er kryss-tenant per
    konstruksjon og ser hver plikt i basen, også dem andre tester har lagt
    igjen. Assertene under teller derfor tenantens EGNE ankre
    (`_sveip_her`), ikke returverdien — en test som stolte på totalen ville
    vært rekkefølgeavhengig, og en test som består fordi naboen ryddet er
    ingen port.
    """
    n = v.execute("SELECT m21_koe_fristvarsler(%s)", (grense,)).fetchone()[0]
    v.commit()
    return n


def _sveip_her(v, m, tenant, grense=100):
    """Sveipen, målt som «hvor mange NYE ankre fikk NETTOPP denne
    tenanten» — den eneste tellingen som er sann uansett hva som ellers
    ligger i basen."""
    forut = len(_ankre(m, tenant))
    _sveip(v, grense)
    return len(_ankre(m, tenant)) - forut


def _varsler(m, tenant):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT ressurs_id, hendelse FROM varsel WHERE tenant=%s"
        "   AND art='pliktfrist' ORDER BY hendelse", (tenant,)).fetchall()
    m.rollback()
    return rader


def _ankre(m, tenant):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT plikt_id, dogn_for, frist_ts, varsel_ref"
        "  FROM pliktvarsel_sendt WHERE tenant=%s"
        " ORDER BY frist_ts, dogn_for", (tenant,)).fetchall()
    m.rollback()
    return rader


# ---------------------------------------------------------------------------
# INVARIANT 1: plikt_uten_eier
# ---------------------------------------------------------------------------

@pg
def test_invariant_plikt_uten_eier(migrator):
    """«Plikter uten eier» er katalogens egen KPI, og i v1 er den en NOT
    NULL — ikke en rapport. To lag måles her, fordi ett ville vært for
    lite:

      1. DIREKTE DML, som eieren av tabellen: en INSERT uten
         `eier_bruker_id` avvises av NOT NULL, og en med en ukjent
         bruker-id av fremmednøkkelen. Det er den bindende porten —
         den gjelder enhver skrivevei, også en fremtidig som glemmer
         døren.
      2. DØREN: en «eier» som ikke er AKTIVT MEDLEM av tenanten avvises.
         FK-en alene sier bare at id-en finnes ET STED i plattformen, og
         en plikt eid av en fremmed tenants bruker er nøyaktig like lite
         gjort som en uten eier.

    MUTASJONEN SOM DREPER DENNE: fjern NOT NULL på `eier_bruker_id`, eller
    fjern medlemskapssjekken i `m21_registrer_plikt`.
    """
    ten = _tenantnavn("eier")
    _bruker(migrator, ten)
    # 1a. Ingen eier i det hele tatt.
    _sett_kontekst(migrator, ten)
    migrator.execute("SET LOCAL ROLE disponit_plikt_eier")
    with pytest.raises(psycopg.errors.NotNullViolation):
        migrator.execute(
            "INSERT INTO plikt (tenant, plikt_id, tittel, kilde, frist_ts,"
            "                   opprettet_av)"
            " VALUES (%s,%s,'uten eier','ingen', now(), 'test')",
            (ten, uuid.uuid4()))
    migrator.rollback()

    # 1b. En eier som ikke finnes som identitet.
    _sett_kontekst(migrator, ten)
    migrator.execute("SET LOCAL ROLE disponit_plikt_eier")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        migrator.execute(
            "INSERT INTO plikt (tenant, plikt_id, tittel, eier_bruker_id,"
            "                   kilde, frist_ts, opprettet_av)"
            " VALUES (%s,%s,'fantom','bid_finnes_ikke','ingen', now(),"
            "         'test')", (ten, uuid.uuid4()))
    migrator.rollback()

    # 2. Døren: en bruker fra en ANNEN tenant er ikke en eier her.
    annen = _tenantnavn("eier-annen")
    fremmed = _bruker(migrator, annen)
    c = _rt()
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _registrer(c, ten, fremmed)
        c.rollback()
        # …og et INAKTIVT medlem av EGEN tenant er heller ikke en eier:
        # en plikt hos noen som har sluttet er en plikt ingen gjør.
        sovende = _bruker(migrator, ten, aktiv=False)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _registrer(c, ten, sovende)
        c.rollback()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# INVARIANT 2: frist_lukket_uten_kvittering
# ---------------------------------------------------------------------------

@pg
def test_invariant_frist_lukket_uten_kvittering(migrator):
    """AKSEPTKRAVET som invariant, i tre lag.

      1. CHECK-en: en direkte UPDATE til `status='lukket'` uten
         kvittering avvises — også som tabellens egen eier.
      2. VAKTEN: en overgang som HAR kvitteringen, men ingen navngitt
         aktør i sesjonen, avvises også. En statusovergang er FORFATTET,
         aldri avledet — og en jobb som skulle lukke fordi tiden gikk har
         ingen aktør å skrive.
      3. DØREN: en tom kvitteringsreferanse avvises med en melding som
         sier hvorfor.

    MUTASJONEN SOM DREPER DENNE: dropp
    `plikt_lukket_krever_kvittering`, eller la vakten slippe en
    statusovergang uten aktør igjennom.
    """
    ten = _tenantnavn("kvitt")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        pid = _registrer(c, ten, eier)

        # 1. CHECK-en, direkte DML som eieren. Målt på en INSERT og ikke
        #    på en UPDATE, og det er ikke et smutthull: radvakten er en
        #    BEFORE-trigger og fyrer FØR constraint-sjekken, så en UPDATE
        #    ville målt vakten (steg 2) og ikke CHECK-en. INSERT-veien har
        #    ingen vakt foran seg — den treffer CHECK-en rent, og det er
        #    nettopp den formen som gjelder enhver fremtidig skrivevei
        #    noen måtte finne på å lage.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_plikt_eier")
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(
                "INSERT INTO plikt (tenant, plikt_id, tittel,"
                " eier_bruker_id, kilde, frist_ts, status, opprettet_av)"
                " VALUES (%s,%s,'lukket uten kvittering',%s,'ingen', now(),"
                "         'lukket','test')", (ten, uuid.uuid4(), eier))
        migrator.rollback()

        # 2. Vakten: full kvittering, men ingen aktør i sesjonen.
        migrator.execute(
            "SELECT set_config('disponit.tenant',%s,true),"
            "       set_config('disponit.aktor','',true)", (ten,))
        migrator.execute("SET LOCAL ROLE disponit_plikt_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "UPDATE plikt SET status='lukket', kvittering_ref='ARK-1',"
                "       lukket_ts=now(), lukket_av='noen'"
                " WHERE tenant=%s AND plikt_id=%s", (ten, pid))
        migrator.rollback()

        # 3. Døren: tom referanse.
        _sett_kontekst(c, ten)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute("SELECT m21_lukk_plikt(%s,%s,%s,%s)",
                      (ten, pid, "   ", "u-test"))
        c.rollback()

        # …og den lovlige veien går igjennom.
        _sett_kontekst(c, ten)
        neste = c.execute("SELECT m21_lukk_plikt(%s,%s,%s,%s)",
                          (ten, pid, "ARK-2026-4711", "u-test")).fetchone()[0]
        c.commit()
        assert neste is None, "en engangsplikt har ingen neste forekomst"
        _sett_kontekst(migrator, ten)
        rad = migrator.execute(
            "SELECT status, kvittering_ref, lukket_av FROM plikt"
            " WHERE tenant=%s AND plikt_id=%s", (ten, pid)).fetchone()
        migrator.rollback()
        assert rad == ("lukket", "ARK-2026-4711", "u-test")
    finally:
        c.close()


@pg
def test_tiden_lukker_ingenting(migrator):
    """En frist får PASSERE, sveipen kjøres, og plikten står fortsatt
    `apen`.

    Dette er den andre halvdelen av dommen «en frist lukkes aldri av at
    tiden går»: det finnes ingen jobb i 096 som setter `status`. Sveipen
    KØER VARSEL — og at den gjør nettopp det, og ikke noe mer, er hva
    porten måler. Den forfalte plikten får varselet sitt (den skal ikke
    ties i hjel), og status er urørt.

    MUTASJONEN SOM DREPER DENNE: la sveipen sette `status='lukket'` på
    plikter der fristen er passert.
    """
    ten = _tenantnavn("tid")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _vs()
    try:
        pid = _registrer(c, ten, eier,
                         frist="now() - interval '3 days'")
        _sveip_her(v, migrator, ten)
        _sett_kontekst(migrator, ten)
        status = migrator.execute(
            "SELECT status FROM plikt WHERE tenant=%s AND plikt_id=%s",
            (ten, pid)).fetchone()[0]
        migrator.rollback()
        assert status == "apen", \
            "sveipen lukket en frist — tiden skal ikke kunne lukke noe"
        # …og den forfalte plikten ble faktisk VARSLET: alle tre punktene
        # er passert, så alle tre fyrer. En port der ingenting skjedde
        # ville bestått uten å ha målt noe.
        assert len(_varsler(migrator, ten)) == 3
    finally:
        c.close()
        v.close()


# ---------------------------------------------------------------------------
# INVARIANT 3: varsel_duplisert_per_varslingspunkt
# ---------------------------------------------------------------------------

@pg
def test_invariant_varsel_duplisert_per_varslingspunkt(migrator):
    """TRE SVEIP PÅ SAMME TILSTAND → ETT VARSEL PER PUNKT.

    En frist nærmer seg over mange sveip — timeren går hvert femte minutt
    — og et varsel per kjøring ville gjort varselet til støy folk lærer
    seg å overse. Da forsvinner de viktige med dem.

    Fristen ligger fem døgn fram, så punktene 30 og 7 er nådd og 1 er det
    ikke. Det er med vilje: en port der ALLE punktene fyrer kan ikke
    skille «ett per punkt» fra «ett per plikt».

    MUTASJONEN SOM DREPER DENNE: fjern `NOT EXISTS`-leddet mot
    `pliktvarsel_sendt` i sveipen, eller `dogn_for` fra ankerets PK.
    """
    ten = _tenantnavn("idem")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _vs()
    try:
        pid = _registrer(c, ten, eier, frist="now() + interval '5 days'")
        assert _sveip_her(v, migrator, ten) == 2, \
            "punktene 30 og 7 er nådd, 1 er det ikke"
        assert _sveip_her(v, migrator, ten) == 0
        assert _sveip_her(v, migrator, ten) == 0
        varsler = _varsler(migrator, ten)
        assert len(varsler) == 2, varsler
        assert {r[0] for r in varsler} == {str(pid)}
        # Ett anker per punkt, og hvert anker peker på SITT varsel.
        ankre = _ankre(migrator, ten)
        assert sorted(a[1] for a in ankre) == [7, 30]
        assert len({a[3] for a in ankre}) == 2, \
            "to ankre peker på samme varsel"
    finally:
        c.close()
        v.close()


@pg
def test_neste_forekomst_far_sitt_eget_varsel(migrator):
    """PORTEN ER IKKE «ALDRI VARSLE IGJEN».

    En gjentakende plikt som kvitteres ut ruller til neste forekomst, og
    den forekomsten får sine egne varsler. Det er nøyaktig derfor
    `frist_ts` er med i ankerets primærnøkkel: uten leddet ville en
    kvartalsvis plikt fått varsel om FØRSTE forekomst og aldri om de
    neste — og idempotensen ville blitt taushet.

    Riggen er den ekte situasjonen: en månedlig plikt som er TO DØGN
    FOR SENT. Alle tre punktene er passert, så første sveip køer tre
    varsler. Så kvitteres forekomsten ut, og fristen ruller til 28 døgn
    fram — der 30-punktet er nådd med det samme, mens 7 og 1 ikke er
    det. Neste sveip køer derfor NØYAKTIG ett nytt varsel: samme plikt,
    samme punkt, en annen frist i nøkkelen.

    MUTASJONEN SOM DREPER DENNE: ta `frist_ts` ut av
    `pliktvarsel_sendt`s PK og ut av sveipens anti-join — da ville 30-
    punktet stått som «alt varslet» for all framtid.
    """
    ten = _tenantnavn("gjentak")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _vs()
    try:
        pid = _registrer(c, ten, eier, gjentakelse="manedlig",
                         frist="now() - interval '2 days'")
        assert _sveip_her(v, migrator, ten) == 3
        assert _sveip_her(v, migrator, ten) == 0

        _sett_kontekst(c, ten)
        neste = c.execute("SELECT m21_lukk_plikt(%s,%s,%s,%s)",
                          (ten, pid, "ARK-M-1", "u-test")).fetchone()[0]
        c.commit()
        assert neste is not None, "en månedlig plikt har en neste forekomst"

        # PLIKTEN STÅR FORTSATT ÅPEN — det er FOREKOMSTEN som ble
        # kvittert ut, ikke forpliktelsen. Den opphører gjennom bortfall,
        # som koster en skreven begrunnelse.
        _sett_kontekst(migrator, ten)
        rad = migrator.execute(
            "SELECT status, kvittering_ref, frist_ts FROM plikt"
            " WHERE tenant=%s AND plikt_id=%s", (ten, pid)).fetchone()
        migrator.rollback()
        assert rad[0] == "apen" and rad[1] == "ARK-M-1"
        assert rad[2] == neste

        assert _sveip_her(v, migrator, ten) == 1, \
            "neste forekomst fikk ikke sitt varsel"
        assert _sveip_her(v, migrator, ten) == 0, \
            "…og den er idempotent på sitt eget punkt"
        ankre = _ankre(migrator, ten)
        assert len(ankre) == 4
        # Fire ankre, TO frister: den nye forekomsten deler `dogn_for=30`
        # med den gamle og skilles BARE av fristen. Det er hele
        # begrunnelsen for at `frist_ts` står i primærnøkkelen.
        assert len({a[2] for a in ankre}) == 2
        assert sorted(a[1] for a in ankre if a[2] == neste) == [30]
    finally:
        c.close()
        v.close()


@pg
def test_gjenspill_av_kvitteringen_ruller_ikke_fristen_igjen(migrator):
    """CodeRabbit P1: en tapt respons + nytt klikk skal ikke hoppe over en
    hel forekomst i STILLHET.

    For en ENGANGS-plikt fanges en retry av statussjekken: raden står
    `lukket` og kallet avvises. For en GJENTAKENDE plikt gjør den ikke
    det — plikten er `apen` igjen etter rullingen — og uten en egen
    gjenspillgren ville det andre kallet rullet fristen EN GANG TIL.
    Resultatet er den verste feilklassen registeret har: ingen
    feilmelding, ingen dublett, bare en frist som stille ble borte, og
    varslingspunktene for den forekomsten som aldri fyrte.

    Identiteten er KVITTERINGEN: et arkivnummer er beviset for ÉN
    levering, og den samme referansen kan ikke kvittere ut to
    forekomster. Porten måler begge halvdelene — at gjenspillet er et
    stille ja med SAMME frist, og at en NY kvittering ruller videre som
    den skal.

    MUTASJONEN SOM DREPER DENNE: fjern gjenspillgrenen fra
    `m21_lukk_plikt`.
    """
    ten = _tenantnavn("gjenspill")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _vs()
    try:
        pid = _registrer(c, ten, eier, gjentakelse="manedlig",
                         frist="now() - interval '2 days'")
        _sett_kontekst(c, ten)
        forste = c.execute("SELECT m21_lukk_plikt(%s,%s,%s,%s)",
                           (ten, pid, "ARK-G-1", "u-test")).fetchone()[0]
        c.commit()
        _sett_kontekst(c, ten)
        gjenspill = c.execute("SELECT m21_lukk_plikt(%s,%s,%s,%s)",
                              (ten, pid, "ARK-G-1", "u-test")).fetchone()[0]
        c.commit()
        assert gjenspill == forste, \
            "gjenspillet rullet fristen en gang til — en forekomst ble" \
            " stille borte"

        # …og gjenspillet skrev heller ingen NY evidensrad: en handling
        # som ikke skjedde skal ikke stå i evidenskjeden som om den
        # gjorde det.
        _sett_kontekst(migrator, ten)
        assert migrator.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
            "   AND handling='plikt.kvittert'", (ten,)).fetchone()[0] == 1
        migrator.rollback()

        # En NY kvittering er en ny forekomst, og den ruller videre.
        _sett_kontekst(c, ten)
        andre = c.execute("SELECT m21_lukk_plikt(%s,%s,%s,%s)",
                          (ten, pid, "ARK-G-2", "u-test")).fetchone()[0]
        c.commit()
        assert andre > forste
    finally:
        c.close()
        v.close()


@pg
def test_sp2_materialiteten_dekker_gjentakelse_og_varslingspunkter(migrator):
    """CodeRabbit: SP-2s materialitetssjekk dekket bare hodet.

    `gjentakelse` avgjør om fristen ruller, og varslingspunktene avgjør
    NÅR noen får vite om den. Et gjenspill som endret ett av dem ville
    fått et stille ja på en plikt som varsler noe helt annet enn den
    kalleren tror den registrerte — og SP-2s hele poeng er at et
    gjenspill ikke skal kunne endre noe i det stille.
    """
    ten = _tenantnavn("sp2")
    eier = _bruker(migrator, ten)
    pid = uuid.uuid4()
    # FAST frist, ikke `now() + …`: gjenspillet skal skille seg fra det
    # første kallet på NØYAKTIG det testen måler, og en frist regnet på
    # nytt noen millisekunder senere ville felt hvert eneste gjenspill på
    # en helt annen grunn.
    frist = "2027-05-31T00:00:00+00:00"
    c = _rt()

    def _forsok(gjentakelse, punkter):
        _sett_kontekst(c, ten)
        return c.execute(
            "SELECT m21_registrer_plikt(%s,%s,%s,%s,%s,%s::timestamptz,"
            "                           %s,%s,%s)",
            (ten, pid, "MVA-melding", eier, "sktl. par 8-3", frist,
             gjentakelse, punkter, "u-test")).fetchone()[0]

    try:
        assert _forsok("aarlig", [30, 7, 1]) is True
        c.commit()
        # Identisk innhold: stille ja.
        assert _forsok("aarlig", [30, 7, 1]) is False
        c.rollback()
        # …og de to som FØR slapp igjennom som «likt innhold».
        for gjentakelse, punkter in (("manedlig", [30, 7, 1]),
                                     ("aarlig", [90, 30])):
            with pytest.raises(psycopg.errors.UniqueViolation):
                _forsok(gjentakelse, punkter)
            c.rollback()
    finally:
        c.close()


@pg
def test_varsel_og_anker_er_samme_transaksjon(migrator):
    """Et varsel køet uten anker, eller et anker uten varsel, skal være
    URESPRESENTERBART.

    Målt ved å rulle tilbake midt i: sveipen kalles, den rapporterer
    arbeidet sitt — og så ROLLBACK. Ingen av delene består. Hadde de to
    innsettingene ligget i hver sin transaksjon, ville den ene overlevd,
    og da ville idempotensen vært brutt i den ene retningen og varselet
    tapt i den andre.

    MUTASJONEN SOM DREPER DENNE: legg en COMMIT mellom varsel- og
    ankerinnsettingen (eller flytt den ene til en egen forbindelse).
    """
    ten = _tenantnavn("atomisk")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _vs()
    try:
        _registrer(c, ten, eier, frist="now() + interval '5 days'")
        n = v.execute("SELECT m21_koe_fristvarsler(100)").fetchone()[0]
        assert n >= 2, "sveipen rapporterte ikke arbeidet den gjorde"
        v.rollback()
        assert _varsler(migrator, ten) == [], \
            "et varsel overlevde en rullet sveip — uten sitt anker"
        assert _ankre(migrator, ten) == [], \
            "et anker overlevde en rullet sveip — uten sitt varsel"
        # …og etter rollbacken er tilstanden uendret, så en ny sveip gjør
        # nøyaktig det samme arbeidet om igjen.
        assert _sveip_her(v, migrator, ten) == 2
        assert len(_varsler(migrator, ten)) == 2
        assert len(_ankre(migrator, ten)) == 2
    finally:
        c.close()
        v.close()


# ---------------------------------------------------------------------------
# INVARIANT 4: forpass_stanset_ordinaer_sending — DEN SKARPE
# ---------------------------------------------------------------------------

@pg
def test_invariant_forpass_stanset_ordinaer_sending(migrator, monkeypatch):
    """DEN SKARPESTE PORTEN I M-21.

    Fristsveipen er lagt som FORPASS i varselsenderen fordi senderens
    rytme, backoff og idempotens er vunne argumenter et forpass arver
    gratis. PRISEN er denne invarianten: en feil i forpasset skal under
    INGEN omstendighet stanse den ordinære varselsendingen.

    Feilen INJISERES i `m21_koe_fristvarsler` — funksjonen erstattes med
    en som kaster, og legges tilbake til slutt. Deretter kjøres
    varselsenderen med et ordinært varsel i køen, og porten krever tre
    ting:

      * varselet gikk UT (`sendt == 1`), altså at forpassets fall ikke
        rev med seg noe;
      * feilen ble TALT SEPARAT (`forpass_feil == 1`) — ikke slått
        sammen med `feilet`, som er e-poster køen selv retter opp med
        backoff. En sveip som ikke kjørte er noe helt annet: varsler som
        ALDRI BLE KØET, og som ingen backoff henter inn;
      * kontrollkjøringen UTEN injeksjon gir `forpass_feil == 0`, så
        telleren ikke bare alltid er 1.

    PORTEN ER RØD MED EN NAIV `forpass(); ordinaer()` I SAMME
    TRANSAKSJON: da ville den kastende sveipen etterlatt en abortert
    transaksjon, og hver eneste påfølgende setning — rekøingen, klaimet,
    statusskrivingen — feilet med «current transaction is aborted».
    `sendt` ville vært 0. Det er `conn.rollback()` i forpassets egen
    except-gren som gjør forskjellen, og det er den denne testen fester.
    """
    from drift import varselsender

    ten = _tenantnavn("forpass")
    eier = _bruker(migrator, ten, epost="frist@m21.test")
    # Ett ORDINÆRT varsel i køen — ikke et pliktvarsel. Det er nettopp
    # poenget: sendingen av det som ALT ligger i køen skal være upåvirket
    # av at et forpass faller.
    _sett_kontekst(migrator, ten)
    vid = migrator.execute(
        "INSERT INTO varsel (tenant, bruker_id, art, ressurs_type,"
        " ressurs_id, hendelse, tekstnokkel, parametre, epost_status)"
        " VALUES (%s,%s,'attestering_venter','policyutkast',%s,'r1',"
        "         'varsel.attestering_venter','{}'::jsonb,'koet')"
        " RETURNING id",
        (ten, eier, "u-" + secrets.token_hex(4))).fetchone()[0]
    migrator.commit()

    monkeypatch.setenv("DISPONIT_PLATTFORMTENANT", ten)
    sendt_til: list[tuple] = []

    def _fanget(til, emne, tekst):
        sendt_til.append((til, emne, tekst))

    # KONTROLLKJØRINGEN først, uten injeksjon: telleren skal være 0.
    # Uten den ville `forpass_feil == 1` under bestått av en teller som
    # alltid er 1.
    v = _vs()
    try:
        kontroll = varselsender.kjor(v, send=_fanget)
    finally:
        v.close()
    assert kontroll["forpass_feil"] == 0, kontroll
    assert kontroll["sendt"] >= 1, kontroll
    assert any(t[0] == "frist@m21.test" for t in sendt_til)

    # Nytt ordinært varsel, og så INJEKSJONEN.
    _sett_kontekst(migrator, ten)
    vid2 = migrator.execute(
        "INSERT INTO varsel (tenant, bruker_id, art, ressurs_type,"
        " ressurs_id, hendelse, tekstnokkel, parametre, epost_status)"
        " VALUES (%s,%s,'attestering_venter','policyutkast',%s,'r2',"
        "         'varsel.attestering_venter','{}'::jsonb,'koet')"
        " RETURNING id",
        (ten, eier, "u-" + secrets.token_hex(4))).fetchone()[0]
    migrator.commit()

    original = migrator.execute(
        "SELECT pg_get_functiondef('m21_koe_fristvarsler(int)'::regprocedure)"
    ).fetchone()[0]
    migrator.rollback()
    sendt_til.clear()
    try:
        migrator.execute("SET LOCAL ROLE disponit_plikt_eier")
        migrator.execute(
            "CREATE OR REPLACE FUNCTION m21_koe_fristvarsler(p_grense INT"
            " DEFAULT 100) RETURNS INT LANGUAGE plpgsql AS $$ BEGIN"
            " RAISE EXCEPTION 'injisert feil i forpasset'; END $$")
        migrator.commit()

        v = _vs()
        try:
            res = varselsender.kjor(v, send=_fanget)
        finally:
            v.close()
    finally:
        # Legg funksjonen tilbake uansett utfall — en injeksjon som blir
        # stående ville forgiftet hver senere test i suiten.
        migrator.execute("SET LOCAL ROLE disponit_plikt_eier")
        migrator.execute(original)
        migrator.commit()

    # DEN ORDINÆRE SENDINGEN GIKK, som om forpasset ikke fantes.
    assert res["sendt"] == 1, res
    assert any(t[0] == "frist@m21.test" for t in sendt_til)
    # FEILEN BLE TALT SEPARAT — og ikke som en feilet e-post.
    assert res["forpass_feil"] == 1, res
    assert res["feilet"] == 0, res
    # …og raden er faktisk merket sendt i basen, ikke bare i tellingen.
    _sett_kontekst(migrator, ten)
    assert migrator.execute(
        "SELECT epost_status FROM varsel WHERE tenant=%s AND id=%s",
        (ten, vid2)).fetchone()[0] == "sendt"
    assert migrator.execute(
        "SELECT epost_status FROM varsel WHERE tenant=%s AND id=%s",
        (ten, vid)).fetchone()[0] == "sendt"
    migrator.rollback()


def test_forpasset_har_sin_egen_transaksjon_og_egen_telling():
    """STATISK PORT på senderen: forpassets rollback og separate telling
    står i koden, ikke bare i en testkjøring.

    Grunnen til at dette måles på kildeteksten i tillegg til i basen: en
    refaktorering som slår de fire sveipene sammen med den ordinære
    sendingen i én transaksjon ville bestått enhver test der forpasset
    tilfeldigvis ikke feiler. Formen er den bindende.
    """
    kilde = (ROT / "platform" / "drift"
             / "varselsender.py").read_text(encoding="utf-8")
    assert "m21_koe_fristvarsler" in kilde, \
        "fristsveipen er ikke koblet inn i senderens pre-pass"
    # Forpasset står FØR den ordinære sendingen (det er hele poenget med
    # et forpass), og etter det egne `conn.commit()`.
    i_forpass = kilde.index("m21_koe_fristvarsler")
    i_rekoe = kilde.index("varsel_rekoe")
    assert i_forpass < i_rekoe, "forpasset kjører ikke før sendingen"
    blokk = kilde[i_forpass:i_rekoe]
    assert "conn.rollback()" in blokk, \
        "forpasset rydder ikke sin egen aborterte transaksjon — den" \
        " ordinære sendingen ville falt med den"
    assert "forpass_feil += 1" in blokk
    assert '"forpass_feil": forpass_feil' in kilde


# ---------------------------------------------------------------------------
# INVARIANT 5: tenantlekkasje_i_pliktregister
# ---------------------------------------------------------------------------

@pg
def test_invariant_tenantlekkasje_i_pliktregister(migrator):
    """Tenant A ser aldri tenant Bs plikter — verken ved direkte DML
    eller gjennom dørene.

    Tre lag måles:
      1. RLS: med A-kontekst er B-radene ikke der, heller ikke for
         tabellens eier (FORCE ROW LEVEL SECURITY).
      2. SP-1: lesedøren kalt med B som parameter, men A i konteksten,
         avvises av `krev_tenantkontekst` — parameteret er aldri
         kallerens frie valg.
      3. Kryss-tenant-policyen er SNEVER: så snart en tenantkontekst
         står, ser eieren bare den ene tenanten. Sveipens vindu finnes
         nøyaktig når det ikke er noen kontekst å bryte.

    MUTASJONEN SOM DREPER DENNE: gjør `m21_sveip_tenantliste` betingelses-
    løs (`USING (true)`), eller fjern `krev_tenantkontekst` fra
    `m21_plikter`.
    """
    a, b = _tenantnavn("lek-a"), _tenantnavn("lek-b")
    eier_a, eier_b = _bruker(migrator, a), _bruker(migrator, b)
    c = _rt()
    try:
        pid_a = _registrer(c, a, eier_a, tittel="A sin plikt")
        pid_b = _registrer(c, b, eier_b, tittel="B sin plikt")

        # 1. RLS, direkte DML som eieren.
        _sett_kontekst(migrator, a)
        migrator.execute("SET LOCAL ROLE disponit_plikt_eier")
        synlige = migrator.execute(
            "SELECT plikt_id FROM plikt ORDER BY plikt_id").fetchall()
        migrator.rollback()
        assert [r[0] for r in synlige] == [pid_a], synlige

        # 3. Kryss-tenant-policyen slår seg AV så snart konteksten står.
        #    Uten kontekst ser eieren begge (det er sveipens vindu).
        migrator.execute("SELECT set_config('disponit.tenant','',true)")
        migrator.execute("SET LOCAL ROLE disponit_plikt_eier")
        uten = {r[0] for r in migrator.execute(
            "SELECT plikt_id FROM plikt").fetchall()}
        migrator.rollback()
        assert {pid_a, pid_b} <= uten, \
            "sveipens vindu finnes ikke — den ville aldri sett en tenant"

        # 2. SP-1: parameteret er ikke kallerens frie valg.
        _sett_kontekst(c, a)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM m21_plikter(%s,%s)", (b, 50)).fetchall()
        c.rollback()

        # …og lesedøren i EGEN kontekst gir bare egne rader.
        _sett_kontekst(c, a)
        rader = c.execute("SELECT plikt_id, tittel FROM m21_plikter(%s,%s)",
                          (a, 50)).fetchall()
        c.rollback()
        assert [r[1] for r in rader] == ["A sin plikt"]
    finally:
        c.close()


@pg
def test_tenantlekkasje_over_api(migrator, klient):
    """Samme invariant, over HTTP: økten hos A får aldri se Bs plikter.

    Tenanten kommer fra ØKTEN, aldri fra kroppen eller en parameter —
    her måles at det faktisk er slik hele veien ut til svaret.
    """
    b = _tenantnavn("api-b")
    eier_a = _bruker(migrator, TENANT)
    eier_b = _bruker(migrator, b)
    c = _rt()
    try:
        _registrer(c, TENANT, eier_a, tittel="A-plikt over API")
        _registrer(c, b, eier_b, tittel="B-plikt over API")
    finally:
        c.close()
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/plikt", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    titler = [p["tittel"] for p in r.json()["plikter"]]
    assert "A-plikt over API" in titler
    assert "B-plikt over API" not in titler


# ---------------------------------------------------------------------------
# Bortfall, evidenskjede og HTTP-feilveien
# ---------------------------------------------------------------------------

@pg
def test_bortfall_uten_begrunnelse_avvises(migrator):
    """`bortfalt` er den EKSPLISITTE skrevne statusen akseptkravet åpner
    for, og den koster en begrunnelse. Uten den ville «bortfalt» vært en
    gratis vei ut av enhver frist, og registeret en liste over ting man
    kan klikke bort.

    To lag igjen: CHECK-en ved direkte DML, og dørens egen RAISE.
    """
    ten = _tenantnavn("bortfall")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        pid = _registrer(c, ten, eier)
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_plikt_eier")
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(
                "UPDATE plikt SET status='bortfalt', bortfalt_ts=now(),"
                "       bortfalt_av='test' WHERE tenant=%s AND plikt_id=%s",
                (ten, pid))
        migrator.rollback()

        for tom in (None, "", "   "):
            # Konteksten settes PER FORSØK: `set_config(..., true)` er
            # transaksjonslokal, og rollbacken under tar den med seg.
            _sett_kontekst(c, ten)
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                c.execute("SELECT m21_marker_bortfalt(%s,%s,%s,%s)",
                          (ten, pid, tom, "u-test"))
            c.rollback()

        _sett_kontekst(c, ten)
        c.execute("SELECT m21_marker_bortfalt(%s,%s,%s,%s)",
                  (ten, pid, "Avtalen er sagt opp av motparten.", "u-test"))
        c.commit()
        _sett_kontekst(migrator, ten)
        rad = migrator.execute(
            "SELECT status, bortfall_begrunnelse, bortfalt_av FROM plikt"
            " WHERE tenant=%s AND plikt_id=%s", (ten, pid)).fetchone()
        migrator.rollback()
        assert rad[0] == "bortfalt" and rad[2] == "u-test"
        assert rad[1].startswith("Avtalen er sagt opp")
    finally:
        c.close()


@pg
def test_evidenskjeden_far_hver_handling(migrator):
    """Manifestet fører `m02_revisjonslogg` som REELL avhengighet: et
    fristvarsel skal kunne GJENFINNES i evidenskjeden, ikke bare i en
    varseltabell. Porten måler at det faktisk skjer — for
    registreringen, for kvitteringen, for bortfallet OG for hvert køet
    varsel.

    Formen er den ordinære (`payload_type='kryptert'`,
    `referansepayload IS NULL`): `revisjonslogg` har ingen
    ciphertext-kolonner (041 §4 dokumenterer det mot levende base), så
    det er formen HVER eksisterende skriver bruker. `referanse`-formen er
    lukket til domeneovertakelses-familien av
    `er_gyldig_referansepayload`, og å utvide DEN for en fristhendelse
    ville vært å låne en tolkning M-21 ikke er blitt gitt.

    INNHOLDET ER IKKE ARKIVERT PÅ NYTT: tittelen og kilden er kundens
    tekst, og de skal ikke stå i evidenskjeden. Porten måler også det.
    """
    ten = _tenantnavn("evidens")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _vs()
    try:
        hemmelig_tittel = "Hemmelig avtaletittel " + secrets.token_hex(4)
        pid = _registrer(c, ten, eier, tittel=hemmelig_tittel,
                         frist="now() + interval '5 days'")
        _sveip(v)
        _sett_kontekst(c, ten)
        c.execute("SELECT m21_lukk_plikt(%s,%s,%s,%s)",
                  (ten, pid, "ARK-9", "u-test"))
        c.commit()

        _sett_kontekst(migrator, ten)
        rader = migrator.execute(
            "SELECT handling, beslutning, kilde, aktor, payload_type,"
            "       referansepayload, input_hash, begrunnelse::text"
            "  FROM revisjonslogg WHERE tenant=%s ORDER BY id",
            (ten,)).fetchall()
        migrator.rollback()
        handlinger = [r[0] for r in rader]
        assert handlinger.count("plikt.registrert") == 1
        assert handlinger.count("plikt.fristvarsel_koet") == 2
        assert handlinger.count("plikt.kvittert") == 1
        for r in rader:
            assert r[1] == "TILLAT"
            assert r[2] == "m21_avtalefrist"
            assert r[4] == "kryptert" and r[5] is None
            assert len(r[6]) == 64, "input_hash er ikke en sha256"
            assert hemmelig_tittel not in r[7]
        assert {r[3] for r in rader} == {"u-test", "fristsveip"}
    finally:
        c.close()
        v.close()


@pg
@dekker("plikt_ulovlig_tilstand")
def test_http_lukking_uten_kvittering_er_409(migrator, klient):
    """FEILVEIEN, ende til ende.

    En kvittering som er tom svarer 400 (kroppen er feilformet — feltet
    mangler innhold), mens en plikt som ALT er kvittert ut svarer 409
    `plikt_ulovlig_tilstand`: kroppen ER velformet, det er TILSTANDEN som
    sier nei. Forskjellen er hele forklaringen mennesket i flaten
    trenger, og den skal ikke være 500.

    Merk hvem som feller dommen: API-et sjekker ikke tilstanden. Det
    kaller døren og oversetter dørens ERRCODE. En flate eller et API som
    sjekket selv ville vært en ANDRE sannhet å komme i utakt med.

    MUTASJONEN SOM DREPER DENNE: la `_doerfeil` mappe
    `invalid_parameter_value` til 500, eller la endepunktet
    forhåndssjekke tilstanden og svare 400.
    """
    eier = _bruker(migrator, TENANT)
    cookie, csrf = _browserokt(migrator, ["admin"])

    r = _post(klient, cookie, csrf, "/v1/plikt",
              {"tittel": "Skattemelding", "eier_bruker_id": eier,
               "kilde": "sktfvl. par 8-2",
               "frist": "2027-05-31T00:00:00Z", "gjentakelse": "engang"})
    assert r.status_code in (200, 201), r.text
    pid = r.json()["plikt_id"]
    assert r.json()["ny"] is True

    # Tom kvittering: kroppen er feilformet, ikke tilstanden.
    r = _post(klient, cookie, csrf, f"/v1/plikt/{pid}/lukk",
              {"kvittering_ref": "   "})
    assert r.status_code == 400, r.text
    assert r.json()["feil"] == "request_feilformet"

    # Den lovlige veien.
    r = _post(klient, cookie, csrf, f"/v1/plikt/{pid}/lukk",
              {"kvittering_ref": "ARK-2027-1"})
    assert r.status_code in (200, 201), r.text
    assert r.json()["neste_frist"] is None

    # …og en gang til: nå er det TILSTANDEN som sier nei.
    r = _post(klient, cookie, csrf, f"/v1/plikt/{pid}/lukk",
              {"kvittering_ref": "ARK-2027-2"})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "plikt_ulovlig_tilstand"

    # Kvitteringen er den FØRSTE — det avviste kallet skrev ingenting.
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT kvittering_ref FROM plikt WHERE tenant=%s AND plikt_id=%s",
        (TENANT, pid)).fetchone()[0] == "ARK-2027-1"
    migrator.rollback()


@pg
def test_http_registrering_er_idempotent_paa_nokkelen(migrator, klient):
    """SP-2 (m35-formen): samme Idempotency-Key + samme innhold gir SAMME
    plikt og et STILLE JA — en tapt respons + nytt klikk skal aldri føde
    plikten en gang til. Samme nøkkel med ANNET innhold er en materiell
    konflikt kalleren skal se."""
    eier = _bruker(migrator, TENANT)
    cookie, csrf = _browserokt(migrator, ["admin"])
    nokkel = secrets.token_urlsafe(24)
    kropp = {"tittel": "Årsregnskap", "eier_bruker_id": eier,
             "kilde": "regnskapsloven par 3-1",
             "frist": "2027-06-30T00:00:00Z", "gjentakelse": "aarlig"}

    r1 = _post(klient, cookie, csrf, "/v1/plikt", kropp, idem=nokkel)
    assert r1.status_code in (200, 201), r1.text
    assert r1.json()["ny"] is True
    r2 = _post(klient, cookie, csrf, "/v1/plikt", kropp, idem=nokkel)
    assert r2.status_code in (200, 201), r2.text
    assert r2.json()["plikt_id"] == r1.json()["plikt_id"]
    assert r2.json()["ny"] is False, "gjenspillet fødte en ny plikt"

    endret = dict(kropp, tittel="Årsregnskap (revidert)")
    r3 = _post(klient, cookie, csrf, "/v1/plikt", endret, idem=nokkel)
    assert r3.status_code == 409, r3.text
    assert r3.json()["feil"] == "idempotenskonflikt"


# ---------------------------------------------------------------------------
# Migrasjonens form: SP-10-premisset og rettighetsspeilet
# ---------------------------------------------------------------------------

@pg
def test_migrasjonen_er_kjort_og_bytebundet(migrator):
    """096 står i `migrasjoner` med checksum lik sha256 av filbytene i
    treet — den TOMME kjøringen målt direkte, og samme byte-binding
    fasiten pinner mot main."""
    cs = migrator.execute(
        "SELECT checksum FROM migrasjoner WHERE versjon=96").fetchone()
    migrator.rollback()
    assert cs is not None, "096 er ikke kjørt i testbasen"
    fil_sha = hashlib.sha256(MIGRASJON.read_bytes()).hexdigest()
    assert cs[0] == fil_sha, \
        "096 i treet er ikke bytene basen kjørte — historikk er immutable"
    fasit = json.loads(
        (ROT / "platform" / "core" / "db" / "migrasjons-fasit.json")
        .read_text(encoding="utf-8"))
    assert fasit.get("096_m21_pliktregister.sql") == fil_sha, \
        "fasiten pinner andre bytes enn treet bærer"


def test_migrasjonen_er_ren_ddl():
    """SP-10s premiss (047-klassen): masse-DML i en migrasjon kan køe
    utsatte triggerhendelser som ALTER-setninger nekter å passere. 096 har
    ingen slik seed — den er ren DDL — og DA er «grønn fra tom base» og
    «grønn mot seedet base» det samme utsagnet, målt av den tomme
    kjøringen over pluss CI-kjøringen mot en bebodd base."""
    import pglast
    dml = [type(raa.stmt).__name__
           for raa in pglast.parse_sql(
               MIGRASJON.read_text(encoding="utf-8"))
           if type(raa.stmt).__name__ in ("InsertStmt", "UpdateStmt",
                                          "DeleteStmt")]
    assert not dml, (
        f"096 bærer toppnivå-DML {dml} — da er den en backfill og skal"
        " registrere seed+måling i sp10-provekjoring.py")


@pg
def test_enumsplicen_er_gronn_mot_BEBODD_varseltabell(migrator):
    """SP-10s ANDRE halvdel, målt og ikke antatt.

    096 er ren DDL (porten over), og for de tre EGNE tabellene er «tom
    base» og «bebodd base» derfor det samme utsagnet — de finnes ikke før
    migrasjonen lager dem. Men §6 rører en tabell som ALT ER BEBODD:
    `varsel`, gjennom `ALTER TABLE ... DROP/ADD CONSTRAINT` på art- og
    ressurstype-CHECKen. Det er nøyaktig 047-klassen — en ALTER over rader
    som alt står der — og det er den ene setningen i 096 der «bebodd»
    kunne betydd noe annet enn «tom».

    Porten kjører derfor §6-blokken ORDRETT fra filen, en gang til, mot
    en `varsel`-tabell som har rader av BÅDE en gammel art og den nye.
    Blokken skal være grønn, og CHECK-ene skal bære BEGGE verdiene
    etterpå.

    MUTASJONEN SOM DREPER DENNE: gjør splicen ERSTATTENDE i stedet for
    additiv (bygg et nytt sett med bare M-21s verdier). Da er SQL-en
    fortsatt syntaktisk gyldig og en TOM base fortsatt grønn — men
    `ADD CONSTRAINT` valideres mot hver eksisterende rad, og den ene
    `attestering_venter`-raden feller hele migrasjonen. Det er nøyaktig
    forskjellen på «grønn fra tom base» og «grønn mot bebodd base», og
    den finnes ikke å måle noe annet sted i 096.
    """
    ten = _tenantnavn("bebodd")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _vs()
    try:
        _registrer(c, ten, eier, frist="now() - interval '1 day'")
        _sveip_her(v, migrator, ten)
    finally:
        c.close()
        v.close()
    # …og en rad av en GAMMEL art ved siden av, så tabellen er bebodd med
    # mer enn M-21s egne rader.
    _sett_kontekst(migrator, ten)
    migrator.execute(
        "INSERT INTO varsel (tenant, bruker_id, art, ressurs_type,"
        " ressurs_id, hendelse, tekstnokkel) VALUES"
        " (%s,%s,'attestering_venter','policyutkast',%s,'r','x')",
        (ten, eier, "u-" + secrets.token_hex(4)))
    migrator.commit()
    assert len(_varsler(migrator, ten)) == 3, "tabellen er ikke bebodd"

    sql = MIGRASJON.read_text(encoding="utf-8")
    blokk = sql[sql.index("-- 6. Varselenumene"):]
    blokk = blokk[blokk.index("DO $$"):]
    migrator.execute(blokk)
    migrator.commit()

    definisjoner = dict(migrator.execute(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conrelid='varsel'::regclass AND conname IN"
        "       ('varsel_art_chk','varsel_ressurs_type_chk')").fetchall())
    migrator.rollback()
    assert "'pliktfrist'" in definisjoner["varsel_art_chk"]
    # …og den GAMLE arten står der fortsatt: splicen er ADDITIV, ikke en
    # omskriving. Et sett som mistet en verdi ville gjort hver eksisterende
    # rad ulovlig i det samme ALTER-et.
    assert "'attestering_venter'" in definisjoner["varsel_art_chk"]
    assert "'plikt'" in definisjoner["varsel_ressurs_type_chk"]
    assert "'policyutkast'" in definisjoner["varsel_ressurs_type_chk"]


def test_migrasjonen_navngir_aldri_runtime_rollen():
    """056/057/089-formen: `disponit` er bare LOKALNAVNET på web-API-
    rollen, og `migrer.py` er eneste rettighetskilde for den konfigurerte
    rollen. En GRANT til runtime i migrasjonen ville lagt
    rettighetsmodellen to steder — og det ene stedet ville vært usant på
    enhver installasjon som kaller rollen noe annet. REVOKE-en er lovlig
    og nødvendig (091-formen): en rettighet som bare slutter å bli gitt er
    ikke trukket tilbake."""
    for linje in MIGRASJON.read_text(encoding="utf-8").splitlines():
        if linje.lstrip().startswith("--"):
            continue
        assert "TO disponit;" not in linje, \
            f"096 grantar direkte til runtime-rollen: {linje!r}"


def test_kjoreren_speiler_096_rettighetene():
    """Rettighetsspeilet i `migrer.py` (057-portformen), og den
    SKARPESTE delen av det: registeret har INGEN tabellrettigheter for
    noen rolle utenom dørenes egen eier.

      * runtime får EXECUTE på lesedøren og de tre skrivedørene — og
        ALDRI på sveipen (kryss-tenant, 038-reaperens snitt);
      * senderrollen får EXECUTE på sveipen og ingenting annet;
      * ingen SELECT/INSERT/UPDATE/DELETE på `plikt`, `pliktvarsling`
        eller `pliktvarsel_sendt` noe sted i kjøreren.
    """
    kjorer = (ROT / "deploy" / "staging" / "migrer.py").read_text(
        encoding="utf-8")
    for dor in ("m21_plikter(TEXT, INT)",
                "m21_registrer_plikt(TEXT, UUID, TEXT, TEXT, TEXT,"
                " TIMESTAMPTZ, TEXT, INT[], TEXT)",
                "m21_lukk_plikt(TEXT, UUID, TEXT, TEXT)",
                "m21_marker_bortfalt(TEXT, UUID, TEXT, TEXT)",
                "m21_koe_fristvarsler(INT)"):
        assert f"GRANT EXECUTE ON FUNCTION {dor} TO {{rolle}};" in kjorer, dor
    assert "REVOKE ALL ON FUNCTION m21_koe_fristvarsler(INT) FROM {rolle};" \
        in kjorer, "runtime får beholde kryss-tenant-sveipen"
    for tabell in ("plikt", "pliktvarsling", "pliktvarsel_sendt"):
        for verb in ("SELECT ON", "INSERT ON", "UPDATE ON", "DELETE ON"):
            assert f"{verb} {tabell}" not in kjorer, \
                f"en rolle har fått {verb} {tabell} utenom dørene"


def test_grensen_dekker_manifestets_seks_invarianter():
    """Grensen `m21-v1` ble registrert FØR koden (§0-regelen). Porten
    pinner den mot planen, ikke mot listen selv: seks invarianter, null
    tillatte brudd, og `ddl_begge_kjoringer_gronne` som eneste
    ja-punkt."""
    from manifestskjema import KRAVGRENSER, M21_INVARIANTER
    g = KRAVGRENSER["m21-v1"]
    assert len(M21_INVARIANTER) == len(set(M21_INVARIANTER)) == 6
    assert g["invarianter"] is M21_INVARIANTER
    assert g["krav_ja"] == ("ddl_begge_kjoringer_gronne",)
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    # Punktbindingen er TOM MED VILJE — uflippbar til målingene finnes.
    assert g["punktbinding"] == {}


def test_rutene_og_flaten_er_registrert():
    """`Route()` og `RUTESCOPE` bindes toveis av `test_pr008`; her måles
    SCOPEVALGET, som er en dom og ikke en detalj: registrering,
    kvittering og bortfall GJENBRUKER `bestilling:opprett` (et nytt scope
    skal ikke oppstå av vane), og lesingen bærer `decisions:read` — det
    scopet ALLE kunderollene har, og det eneste `LESESCOPES`-porten
    godtar for en `/v1/`-GET."""
    from api.app import LESESCOPES, RUTESCOPE
    assert RUTESCOPE[("GET", "/v1/plikt")] == "decisions:read"
    assert "decisions:read" in LESESCOPES
    for sti in ("/v1/plikt", "/v1/plikt/{plikt_id:uuid}/lukk",
                "/v1/plikt/{plikt_id:uuid}/bortfall"):
        assert RUTESCOPE[("POST", sti)] == "bestilling:opprett"
    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert "bestilling:opprett" in ROLLE_TIL_SCOPES["admin"]
    assert "decisions:read" in ROLLE_TIL_SCOPES["leser"]
    sitekart = (ROT / "platform" / "core" / "ui" / "static" / "js"
                / "sitekart.js").read_text(encoding="utf-8")
    # Eiervedtak 1/9: ruten flyttet fra toppnavigasjonen til
    # venstremenyen (modul 21). Bindingen står, men mot den nye formen —
    # `modulflate` er en del av ruteoppføringen, ikke en tilleggslinje.
    assert ('{ nokkel: "avtalefrist", scope: "decisions:read",'
            ' modulflate: 21 }') in sitekart


# ---------------------------------------------------------------------------
# Små hjelpere for HTTP-veien (m35-formen)
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
        " ('https://m21.test', %s) RETURNING bruker_id",
        ("s21h-" + secrets.token_hex(6),)).fetchone()[0]
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
