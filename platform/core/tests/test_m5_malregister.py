"""M-5 v1 (migrasjon 094) — malregisterets porter.

`M5_INVARIANTER` i `manifestskjema.py` er kravlisten, og hver invariant
måles her som FORSØK og BRUDD (m57/m6-parformen: null brudd uten et
forsøk beviser ingenting).

  felt_uten_kilde_diktet
      Den bærende. Et påkrevd felt uten dekning i inndataene rapporteres
      som manglende, og det som kommer tilbake bærer verken tom streng,
      feltnøkkelen eller en plassholder på plassen. Porten er skrevet så
      den er RØD med den naive implementasjonen (`coalesce(verdi, '')`,
      `coalesce(verdi, feltnokkel)`, `'{{feltnokkel}}'`) — se
      `test_port1*`.
  laast_klausul_endret
      En låst klausul har ingen feltnøkkel og kan derfor ikke nås av
      utfyllingen i det hele tatt; og direkte DML mot en låst komponent
      i en publisert versjon avvises av vakten, også for eieren.
  malversjon_endret_etter_publisering
      079-formen generalisert: innholdet er frosset, og de to
      livssyklusovergangene er de eneste lovlige UPDATE-ene. Begge veier
      måles — at tilbaketrekkingen FORTSATT går er like viktig som at
      redigeringen ikke gjør det.
  tenantlekkasje_i_malregister
      Direkte DML og over API.
  utfylling_skrev_dokument
      Statisk (ingen INSERT av utfylt tekst i modulen), funksjonelt
      (radantallet er uendret etter en utfylling) — og strukturelt:
      `m5_fyll_mal` er STABLE, og PostgreSQL avviser enhver skriving i
      en ikke-volatil funksjon. Den tredje er den sterkeste: den gjelder
      også koden ingen har skrevet ennå.
  ui_axe_alvorlige_brudd
      Bor i `platform/core/ui/test/dokumentmal.test.js` (jsdom +
      axe-core); den kjøres av `npm test`, ikke herfra.

Ja-punktet `ddl_begge_kjoringer_gronne` måles av `test_port9*`:
migrasjonen er byte-bundet i denne basen (den TOMME kjøringen), den er
REN DDL uten et eneste toppnivå-DML — så «mot seedet base» hviler ikke
på et SP-10-seed den ikke har — og fasiten pinner de samme bytene.

Alle DB-tester konstruerer egen tilstand; ingen delt fixture.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, ANNEN_TENANT,  # noqa: F401
                       TENANT, app, dekker, klient, migrator, miljo)
from .test_m37 import _sett_kontekst

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "094_m5_malregister.sql")
MODUL = ROT / "platform" / "core" / "api" / "dokumentmal.py"
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "dokumentmal.js")

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

TABELLER = ("malfamilie", "malversjon", "malkomponent", "malfelt")


# ---------------------------------------------------------------------------
# Riggen — alt går gjennom dørene, som i drift
# ---------------------------------------------------------------------------

def _som_eier(m, tenant, sql, args):
    """Ett dørkall som eieren. INGEN `finally: RESET ROLE`: en feilende
    dør etterlater transaksjonen avbrutt, og en RESET der ville reist en
    `InFailedSqlTransaction` som SKJULTE dommen porten måler. `SET ROLE`
    er transaksjonell og rulles uansett tilbake med kallerens rollback."""
    _sett_kontekst(m, tenant)
    m.execute("SET ROLE disponit_mal_eier")
    rad = m.execute(sql, args).fetchone()
    m.execute("RESET ROLE")
    return rad


def _familie(m, tenant=TENANT, navn=None):
    rad = _som_eier(m, tenant,
                    "SELECT m5_opprett_malfamilie(%s,%s,%s,%s,%s)",
                    (tenant, navn or ("Avtale-" + secrets.token_hex(4)),
                     None, "test", None))
    m.commit()
    return rad[0]


#: Malen porten 1 måler på: to felt, én låst klausul, fast tekst rundt.
KOMPONENTER = [
    {"komponenttype": "tekst", "innhold": "Arbeidsavtale mellom "},
    {"komponenttype": "felt", "feltnokkel": "arbeidsgiver"},
    {"komponenttype": "tekst", "innhold": " og "},
    {"komponenttype": "felt", "feltnokkel": "arbeidstaker"},
    {"komponenttype": "klausul", "innhold": "Oppsigelsestid er tre maaneder.",
     "laast": True},
]
FELT = [
    {"feltnokkel": "arbeidsgiver", "paakrevd": True, "felttype": "tekst",
     "beskrivelse": "Arbeidsgiverens navn"},
    {"feltnokkel": "arbeidstaker", "paakrevd": True, "felttype": "tekst",
     "beskrivelse": "Arbeidstakerens navn"},
]


def _versjon(m, familie_id, tenant=TENANT, komponenter=None, felt=None,
             vid=None):
    rad = _som_eier(
        m, tenant,
        "SELECT ut_versjon_id, ut_versjonsnr FROM"
        " m5_opprett_malversjon(%s,%s,%s::jsonb,%s::jsonb,%s,%s)",
        (tenant, familie_id,
         json.dumps(KOMPONENTER if komponenter is None else komponenter),
         json.dumps(FELT if felt is None else felt), "test", vid))
    m.commit()
    return rad[0]


def _publiser(m, versjon_id, tenant=TENANT):
    rad = _som_eier(m, tenant, "SELECT m5_publiser_malversjon(%s,%s,%s)",
                    (tenant, versjon_id, "test"))
    m.commit()
    return rad[0]


def _publisert_mal(m, tenant=TENANT, komponenter=None, felt=None):
    fid = _familie(m, tenant)
    vid = _versjon(m, fid, tenant, komponenter, felt)
    _publiser(m, vid, tenant)
    return fid, vid


def _fyll(m, versjon_id, verdier, tenant=TENANT):
    _sett_kontekst(m, tenant)
    m.execute("SET ROLE disponit_mal_eier")
    rader = m.execute(
        "SELECT rekkefolge, komponenttype, feltnokkel, laast, paakrevd,"
        "       dekket, tekst FROM m5_fyll_mal(%s,%s,%s::jsonb)"
        " ORDER BY rekkefolge",
        (tenant, versjon_id, json.dumps(verdier))).fetchall()
    m.rollback()
    return rader


def _radantall(m, tenant=TENANT):
    _sett_kontekst(m, tenant)
    tall = {t: m.execute(f"SELECT count(*) FROM {t} WHERE tenant=%s",
                         (tenant,)).fetchone()[0] for t in TABELLER}
    m.rollback()
    return tall


# ---------------------------------------------------------------------------
# Port 0: forutsetningen fundamentet ikke leverte
# ---------------------------------------------------------------------------

@pg
def test_port0_migrator_kan_sette_rolle_til_maleier(migrator):
    """094 lager sine dører i en `SET LOCAL ROLE disponit_mal_eier`-blokk,
    og det krever at migrator er MEDLEM av eierrollen.

    Klyngefundamentet (PR #326) OPPRETTER `disponit_mal_eier` i både
    `oppsett-postgresql.sh` og `ci.yml`, men glemte de to linjene som gjør
    rollen brukbar for migrator:

        GRANT disponit_mal_eier TO disponit_migrator WITH INHERIT FALSE
        GRANT USAGE, CREATE ON SCHEMA public TO disponit_mal_eier

    Uten dem svarer migrasjonen «permission denied to set role» — og det
    gjelder ALLE FEM klyngeeierne, ikke bare denne. Linjene hører
    fundamentet til (fem spor som hver legger sin linje i `ci.yml` er
    nøyaktig kollisjonen fundamentet finnes for å unngå), så porten står
    her og MÅLER mangelen i stedet for at den oppdages når deployen alt
    har stoppet tjenestene.

    MUTASJONEN SOM DREPER DENNE: fjern medlemskapet igjen.
    """
    # 'SET', ikke 'USAGE': medlemskapet er `WITH INHERIT FALSE`, altså
    # SET ROLE og ingen arvede rettigheter — nøyaktig det migrasjonen
    # trenger, og nøyaktig det `USAGE` ville svart nei på.
    medlem = migrator.execute(
        "SELECT pg_has_role(current_user, 'disponit_mal_eier', 'SET')"
    ).fetchone()[0]
    migrator.rollback()
    assert medlem, (
        "migrator er ikke medlem av disponit_mal_eier — legg"
        " `GRANT disponit_mal_eier TO disponit_migrator WITH INHERIT FALSE`"
        " og `GRANT USAGE, CREATE ON SCHEMA public TO disponit_mal_eier`"
        " i oppsett-postgresql.sh og ci.yml (gjelder alle fem"
        " klyngeeierne)")


# ---------------------------------------------------------------------------
# Port 1: felt_uten_kilde_diktet — DEN BÆRENDE
# ---------------------------------------------------------------------------

@pg
@dekker("dokumentmal_ulovlig_tilstand")
def test_port1_felt_uten_kilde_diktes_aldri(migrator):
    """Et påkrevd felt uten dekning i `p_verdier` rapporteres som
    manglende — og det som kommer tilbake bærer INGENTING på plassen.

    Tre ting måles, og hver av dem feller en egen naiv implementasjon:

      1. `tekst IS NULL` — ikke tom streng (`coalesce(v, '')`);
      2. feltnøkkelen står ikke i noen returnert tekst
         (`coalesce(v, feltnokkel)`);
      3. ingen returnert tekst inneholder en plassholderform
         (`{{...}}`, `<...>`, `___`, `[...]`) — den «pene» varianten som
         ser ut som innhold i et ferdig dokument.

    OG: det dekkede feltet står der med sin EKTE verdi. En port som bare
    målte fraværet ville også vært grønn for en funksjon som aldri fylte
    noe som helst.

    MUTASJONEN SOM DREPER DENNE: la `m5_fyll_mal` sette `tekst := ''`
    eller `tekst := k.feltnokkel` når verdien mangler.
    """
    _fid, vid = _publisert_mal(migrator)

    rader = _fyll(migrator, vid, {"arbeidsgiver": "Acme AS"})
    assert len(rader) == 5

    etter_nokkel = {r[2]: r for r in rader if r[1] == "felt"}
    dekket = etter_nokkel["arbeidsgiver"]
    manglende = etter_nokkel["arbeidstaker"]

    assert dekket[5] is True and dekket[6] == "Acme AS", \
        "et felt MED kilde ble ikke fylt ut — porten måler da ingenting"
    assert manglende[4] is True, "feltet er ikke rapportert som påkrevd"
    assert manglende[5] is False, "feltet er ikke rapportert som manglende"
    assert manglende[6] is None, (
        "et felt uten kilde fikk en verdi: "
        f"{manglende[6]!r} — tom streng og plassholder er begge dikting")

    tekster = [r[6] for r in rader if r[6] is not None]
    assert "" not in tekster, "en tom streng ble returnert som innhold"
    for t in tekster:
        assert "arbeidstaker" not in t, \
            f"feltnøkkelen lekket inn i dokumentteksten: {t!r}"
        for form in ("{{", "}}", "___", "<felt", "[felt"):
            assert form not in t, f"plassholderform i teksten: {t!r}"

    # …og den låste klausulen står urørt, med sin egen tekst.
    klausul = [r for r in rader if r[1] == "klausul"][0]
    assert klausul[3] is True and klausul[6] == KOMPONENTER[4]["innhold"]


@pg
def test_port1b_tom_streng_er_fravaer_ikke_en_verdi(migrator):
    """Den skarpeste varianten: kalleren SENDER nøkkelen, men med tom
    streng (eller bare mellomrom, eller JSON `null`). Alle tre er
    FRAVÆR, ikke en utfylling.

    Uten dette ville et skjema med et tomt tekstfelt produsert et
    dokument der feltet ser besvart ut — nøyaktig den feilen invarianten
    finnes for, bare flyttet ett hakk ut til klienten.

    MUTASJONEN SOM DREPER DENNE: fjern `btrim(v_raa) = ''`-armen.
    """
    _fid, vid = _publisert_mal(migrator)
    for verdi in ("", "   ", None):
        rader = _fyll(migrator, vid,
                      {"arbeidsgiver": "Acme AS", "arbeidstaker": verdi})
        rad = [r for r in rader if r[2] == "arbeidstaker"][0]
        assert rad[5] is False and rad[6] is None, \
            f"{verdi!r} ble behandlet som en verdi"


@pg
def test_port1c_ukjent_feltnokkel_er_en_feil_ikke_et_stille_hull(migrator):
    """En skrivefeil i en nøkkel må SI fra. Ellers blir to feil til én
    stille: verdien når aldri malen, og rapporten sier bare «feltet
    mangler» — som om brukeren ikke hadde skrevet noe.

    Og ikke-skalare verdier avvises: en `{}` på et tekstfelt er en
    kallerfeil, ikke noe som skal bli til en streng i et dokument.
    """
    _fid, vid = _publisert_mal(migrator)
    for verdier in ({"arbeidsgvier": "Acme AS"},
                    {"arbeidsgiver": {"navn": "Acme"}},
                    {"arbeidsgiver": True}):
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _fyll(migrator, vid, verdier)
        migrator.rollback()


@pg
def test_port1d_utfylling_krever_en_versjon_i_kraft(migrator):
    """Et utkast er ikke i kraft, og en tilbaketrukket versjon er det
    ikke lenger. Begge avvises — ellers ville tilbaketrekkingen ikke
    betydd noe."""
    fid = _familie(migrator)
    vid = _versjon(migrator, fid)
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        _fyll(migrator, vid, {})
    migrator.rollback()

    _publiser(migrator, vid)
    assert _fyll(migrator, vid, {})          # publisert: går
    _som_eier(migrator, TENANT,
              "SELECT m5_trekk_tilbake_malversjon(%s,%s,%s)",
              (TENANT, vid, "test"))
    migrator.commit()
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        _fyll(migrator, vid, {})
    migrator.rollback()


# ---------------------------------------------------------------------------
# Port 2: laast_klausul_endret
# ---------------------------------------------------------------------------

@pg
def test_port2_laast_klausul_kan_ikke_overstyres_av_utfyllingen(migrator):
    """Utfyllingen har ingen inngang til en låst klausul, og forsøket er
    derfor URERPRESENTERBART framfor usannsynlig.

    Forsøket som MÅLES: kalleren prøver å sende en verdi som skal
    erstatte klausulen — både på et navn som ligner et felt og på selve
    klausulens innhold. Begge avvises av døren (ukjent feltnøkkel), og
    klausulteksten står uendret i det som kommer tilbake.

    MUTASJONEN SOM DREPER DENNE: la CHECK-en tillate `feltnokkel` på en
    klausul, eller la `m5_fyll_mal` slå opp klausuler i `p_verdier`.
    """
    _fid, vid = _publisert_mal(migrator)
    original = KOMPONENTER[4]["innhold"]

    for forsok in ({"oppsigelsestid": "ingen"},
                   {"klausul": "Oppsigelsestid er null."},
                   {original: "erstattet"}):
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _fyll(migrator, vid, forsok)
        migrator.rollback()

    rader = _fyll(migrator, vid, {"arbeidsgiver": "A", "arbeidstaker": "B"})
    klausul = [r for r in rader if r[1] == "klausul"][0]
    assert klausul[6] == original, "klausulen ble endret av en utfylling"


@pg
def test_port2b_direkte_dml_mot_laast_komponent_avvises(migrator):
    """…og veien utenom utfyllingen er også stengt: UPDATE og DELETE på
    en låst komponent i en PUBLISERT versjon avvises — som MIGRATOR,
    altså eieren av tabellen. En vakt som bare gjelder de rettighetsløse
    er ingen vakt (011/053/056).
    """
    _fid, vid = _publisert_mal(migrator)
    _sett_kontekst(migrator, TENANT)
    kid = migrator.execute(
        "SELECT komponent_id FROM malkomponent WHERE tenant=%s"
        " AND versjon_id=%s AND laast", (TENANT, vid)).fetchone()[0]
    migrator.rollback()

    for sql in ("UPDATE malkomponent SET innhold='noe annet'"
                " WHERE tenant=%s AND komponent_id=%s",
                "DELETE FROM malkomponent WHERE tenant=%s"
                " AND komponent_id=%s"):
        _sett_kontekst(migrator, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(sql, (TENANT, kid))
        migrator.rollback()

    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT innhold, laast FROM malkomponent WHERE tenant=%s"
        " AND komponent_id=%s", (TENANT, kid)).fetchone()
    migrator.rollback()
    assert rad == (KOMPONENTER[4]["innhold"], True)


# ---------------------------------------------------------------------------
# Port 3: malversjon_endret_etter_publisering
# ---------------------------------------------------------------------------

@pg
def test_port3_publisert_versjon_er_frosset_men_kan_trekkes_tilbake(migrator):
    """BEGGE VEIER, som invarianten krever.

    Frosset: identitet, versjonsnummer og innholdshash kan ikke endres,
    og INGEN ny komponent kan legges til en publisert versjon.
    Tillatt: den ENE statusovergangen `publisert → tilbaketrukket`
    (079s enveis skjuling, i statusform) — og den er terminal.

    MUTASJONEN SOM DREPER DENNE: la `m5_versjon_vakt` slippe gjennom en
    UPDATE der bare `innhold_hash` endres, eller gjør
    `tilbaketrukket → publisert` mulig.
    """
    _fid, vid = _publisert_mal(migrator)

    for sql, args in (
            ("UPDATE malversjon SET innhold_hash='juks' WHERE tenant=%s"
             " AND versjon_id=%s", (TENANT, vid)),
            ("UPDATE malversjon SET versjonsnr=99 WHERE tenant=%s"
             " AND versjon_id=%s", (TENANT, vid)),
            ("DELETE FROM malversjon WHERE tenant=%s AND versjon_id=%s",
             (TENANT, vid))):
        _sett_kontekst(migrator, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(sql, args)
        migrator.rollback()

    # Nytt innhold i en publisert versjon: vakten avviser INSERT-en.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "INSERT INTO malkomponent (tenant, komponent_id, versjon_id,"
            " rekkefolge, komponenttype, innhold) VALUES"
            " (%s,%s,%s,99,'tekst','snik')", (TENANT, uuid.uuid4(), vid))
    migrator.rollback()

    # Tilbaketrekkingen GÅR — og er enveis.
    assert _som_eier(migrator, TENANT,
                     "SELECT m5_trekk_tilbake_malversjon(%s,%s,%s)",
                     (TENANT, vid, "test"))[0] == 1
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT status, tilbaketrukket_av FROM malversjon WHERE tenant=%s"
        " AND versjon_id=%s", (TENANT, vid)).fetchone()
    migrator.rollback()
    assert rad == ("tilbaketrukket", "test")

    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "UPDATE malversjon SET status='publisert',"
            " tilbaketrukket_ts=NULL, tilbaketrukket_av=NULL"
            " WHERE tenant=%s AND versjon_id=%s", (TENANT, vid))
    migrator.rollback()


@pg
def test_port3b_redigering_er_en_ny_versjon(migrator):
    """079s egen setning, målt: den lovlige veien videre er et NYTT
    versjonsnummer i samme familie — og det er en ny rad, ikke en
    endring."""
    fid, vid = _publisert_mal(migrator)
    vid2 = _versjon(migrator, fid)
    _sett_kontekst(migrator, TENANT)
    numre = [r[0] for r in migrator.execute(
        "SELECT versjonsnr FROM malversjon WHERE tenant=%s AND familie_id=%s"
        " ORDER BY versjonsnr", (TENANT, fid)).fetchall()]
    migrator.rollback()
    assert numre == [1, 2] and vid != vid2


# ---------------------------------------------------------------------------
# Port 4: tenantlekkasje_i_malregister
# ---------------------------------------------------------------------------

@pg
def test_port4_tenant_a_ser_aldri_tenant_bs_maler(migrator):
    """Direkte DML: RLS gjør B-radene usynlige under A-kontekst, og
    dørene slipper ikke engang kallet inn (`krev_tenantkontekst` binder
    tenanten til KONTEKSTEN, aldri til parameteret alene)."""
    _fid_b, vid_b = _publisert_mal(migrator, ANNEN_TENANT)

    _sett_kontekst(migrator, TENANT)
    for tabell, kolonne in (("malversjon", "versjon_id"),
                            ("malkomponent", "versjon_id"),
                            ("malfelt", "versjon_id")):
        n = migrator.execute(
            f"SELECT count(*) FROM {tabell} WHERE {kolonne}=%s",
            (vid_b,)).fetchone()[0]
        assert n == 0, f"{tabell} lekket {ANNEN_TENANT}s rader til {TENANT}"
    migrator.rollback()

    # Dørene: A-kontekst + B-parameter er avvist av kontekstporten, og
    # A-kontekst + B-id finner ingenting (RLS), aldri B-innholdet.
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_mal_eier")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute("SELECT * FROM m5_fyll_mal(%s,%s,'{}'::jsonb)",
                         (ANNEN_TENANT, vid_b))
    migrator.rollback()

    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_mal_eier")
    with pytest.raises(psycopg.errors.NoDataFound):
        migrator.execute("SELECT * FROM m5_fyll_mal(%s,%s,'{}'::jsonb)",
                         (TENANT, vid_b))
    migrator.rollback()


@pg
def test_port4b_api_leseveien_viser_bare_egen_tenant(migrator, klient):
    """…og over API-et. Økten er bundet til TENANT; B-familien finnes,
    men svaret nevner den ikke."""
    from api import sesjon as sesjonmodul
    _publisert_mal(migrator, ANNEN_TENANT)
    fid_a = _familie(migrator, TENANT, navn="Bare-min-" + secrets.token_hex(3))
    cookie, _csrf = _browserokt(migrator, ["admin"])

    r = klient.get("/v1/dokumentmal",
                   cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 200, r.text
    ider = {f["familie_id"] for f in r.json()["familier"]}
    assert str(fid_a) in ider
    _sett_kontekst(migrator, ANNEN_TENANT)
    b_ider = {str(x[0]) for x in migrator.execute(
        "SELECT familie_id FROM malfamilie WHERE tenant=%s",
        (ANNEN_TENANT,)).fetchall()}
    migrator.rollback()
    assert not (ider & b_ider), "leseveien lekket en annen tenants familier"


# ---------------------------------------------------------------------------
# Port 5: utfylling_skrev_dokument — v1-dommen
# ---------------------------------------------------------------------------

@pg
def test_port5_utfyllingen_skriver_ingenting(migrator):
    """FUNKSJONELT: radantallet i alle fire tabellene er uendret etter en
    utfylling — også en KOMPLETT en, som er tilfellet der en «lagre
    dokumentet»-linje ville vært mest fristende å legge inn.
    """
    _fid, vid = _publisert_mal(migrator)
    for _ in range(2):
        _familie(migrator)                    # støy, så tallene er ekte
    for_tall = _radantall(migrator)
    _fyll(migrator, vid, {"arbeidsgiver": "Acme AS",
                          "arbeidstaker": "Ola Nordmann"})
    _fyll(migrator, vid, {"arbeidsgiver": "Acme AS"})
    assert _radantall(migrator) == for_tall, \
        "utfyllingen endret radantallet — den lagret noe"


@pg
def test_port5b_utfyllingen_er_stable_og_kan_derfor_ikke_skrive(migrator):
    """STRUKTURELT, og dette er den sterkeste formen invarianten kan ha
    uten en egen rettighetsløs rolle: `m5_fyll_mal` er STABLE, og
    PostgreSQL avviser INSERT/UPDATE/DELETE i en ikke-volatil funksjon.
    Dommen gjelder altså også koden ingen har skrevet ennå.

    Beviset for at STABLE faktisk betyr det, måles i samme test — mot en
    kastbar funksjon, så porten ikke hviler på en antakelse om
    PostgreSQL-versjonen basen tilfeldigvis kjører.

    MUTASJONEN SOM DREPER DENNE: gjør `m5_fyll_mal` VOLATILE.
    """
    vol = migrator.execute(
        "SELECT provolatile FROM pg_proc WHERE proname='m5_fyll_mal'"
    ).fetchone()
    migrator.rollback()
    assert vol is not None and vol[0] == "s", \
        "m5_fyll_mal er ikke STABLE — da kan den skrive"

    migrator.execute(
        "CREATE FUNCTION _m5_provolatile_bevis() RETURNS INT"
        " LANGUAGE plpgsql STABLE AS $$ BEGIN"
        " INSERT INTO malfamilie (tenant, familie_id, navn, opprettet_av,"
        " innhold_hash) VALUES ('x', gen_random_uuid(), 'x', 'x', 'x');"
        " RETURN 1; END $$")
    with pytest.raises(psycopg.errors.FeatureNotSupported):
        migrator.execute("SELECT _m5_provolatile_bevis()")
    migrator.rollback()


def test_port5c_modulen_har_ingen_insert_av_utfylt_tekst():
    """STATISK: hverken API-modulen eller flaten har en skrivevei for
    utfylt tekst. Ingen INSERT/UPDATE/DELETE mot en maltabell, ingen
    kall til en dør som skriver, og ingen `commit()` i utfyllingsveien.

    Målt på KILDETEKSTEN og ikke på oppførselen, fordi «den gjør det
    ikke i dag» ikke er en egenskap ved en fil som endres.

    MUTASJONEN SOM DREPER DENNE: legg en `conn.commit()` eller en
    INSERT i `utfylling_endepunkt`.
    """
    raa = MODUL.read_text(encoding="utf-8")
    # NORMALISERT KILDETEKST, OG MED MELLOMROMMET MELLOM VERB OG TABELL
    # (CodeRabbit felte den forrige versjonen her): `f"{verb}{tabell}"`
    # ga «INSERT INTOmalfamilie», som aldri kan forekomme — porten
    # matchet altså ingenting, og en INSERT lagt inn i modulen ville
    # passert grønt. SQL-en er dessuten brutt over flere strengliteraler,
    # så sammenføyningen må vekk før teksten kan leses som SQL.
    kilde = " ".join(raa.split())
    for skjot in ('" "', '" + "', '""', '"'):
        kilde = kilde.replace(skjot, "")
    kilde = " ".join(kilde.split())
    for verb in ("INSERT INTO", "UPDATE", "DELETE FROM"):
        for tabell in TABELLER:
            assert f"{verb} {tabell}" not in kilde, \
                f"modulen skriver til {tabell}"
    # Utfyllingsveien isolert: fra `def utfylling_endepunkt` og ut.
    del_ = kilde.split("def utfylling_endepunkt", 1)[1]
    assert "commit()" not in del_, \
        "utfyllingsveien committer — da kan den lagre et dokument"
    for dor in ("m5_opprett_malversjon", "m5_publiser_malversjon",
                "m5_trekk_tilbake_malversjon", "m5_opprett_malfamilie"):
        assert dor not in del_, f"utfyllingsveien kaller {dor}"
    # innerHTML-forbudet (V6) måles på KODEN, ikke på kommentarene — en
    # kommentar som sier at forbudet gjelder er ikke et brudd på det.
    kode = [linje for linje in FLATE.read_text(encoding="utf-8").splitlines()
            if not linje.lstrip().startswith("//")]
    assert "innerHTML" not in "\n".join(kode), "flaten bruker innerHTML (V6)"


# ---------------------------------------------------------------------------
# Port 6: publisering av en mal som refererer et udeklarert felt
# ---------------------------------------------------------------------------

@pg
def test_port6_udeklarert_felt_kan_ikke_publiseres(migrator):
    """En mal som refererer et felt ingen har deklarert, kan ikke
    publiseres — utfyllingen ville da hatt et hull den aldri fikk vite
    at den skulle rapportere.

    Og MOTSATT VEI: et deklarert felt ingen komponent bruker avvises
    også. Et `paakrevd`-felt uten plass i teksten ville blitt rapportert
    manglende for alltid, uten at noe kunne fylle det — en mal ingen kan
    gjøre ferdig.

    MUTASJONEN SOM DREPER DENNE: fjern en av de to EXISTS-sjekkene i
    `m5_publiser_malversjon`.
    """
    fid = _familie(migrator)

    udeklarert = _versjon(migrator, fid, komponenter=[
        {"komponenttype": "tekst", "innhold": "Hei "},
        {"komponenttype": "felt", "feltnokkel": "mottaker"}], felt=[])
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        _publiser(migrator, udeklarert)
    migrator.rollback()

    ubrukt = _versjon(migrator, fid, komponenter=[
        {"komponenttype": "tekst", "innhold": "Hei"}], felt=[
        {"feltnokkel": "mottaker", "paakrevd": True, "felttype": "tekst",
         "beskrivelse": "Mottakerens navn"}])
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        _publiser(migrator, ubrukt)
    migrator.rollback()

    # …og begge blir publiserbare i det settene stemmer.
    ok = _versjon(migrator, fid, komponenter=[
        {"komponenttype": "tekst", "innhold": "Hei "},
        {"komponenttype": "felt", "feltnokkel": "mottaker"}], felt=[
        {"feltnokkel": "mottaker", "paakrevd": True, "felttype": "tekst",
         "beskrivelse": "Mottakerens navn"}])
    assert _publiser(migrator, ok) >= 1

    # Publisering er en ENGANGSovergang.
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        _publiser(migrator, ok)
    migrator.rollback()


# ---------------------------------------------------------------------------
# Port 7: CHECK-totaliteten
# ---------------------------------------------------------------------------

@pg
def test_port7_komponentformen_er_total(migrator):
    """De tre ulovlige formene, hver som sin egen `check_violation` —
    og de faller på CHECK-en, ikke på en IF-stige i døren: en totalitet
    som bare finnes i en funksjonskropp, er en totalitet neste
    skrivevei ikke arver.

    MUTASJONEN SOM DREPER DENNE: løs opp
    `malkomponent_form_total` i tre uavhengige CHECK-er.
    """
    fid = _familie(migrator)
    ulovlige = (
        # 'felt' MED innhold
        [{"komponenttype": "felt", "feltnokkel": "a", "innhold": "juks"}],
        # 'tekst' MED feltnøkkel
        [{"komponenttype": "tekst", "innhold": "hei", "feltnokkel": "a"}],
        # 'tekst' MED laast=true — bare klausuler kan låses
        [{"komponenttype": "tekst", "innhold": "hei", "laast": True}],
        # 'felt' MED laast=true — samme regel
        [{"komponenttype": "felt", "feltnokkel": "a", "laast": True}],
        # 'klausul' UTEN innhold
        [{"komponenttype": "klausul", "feltnokkel": "a"}],
        # ukjent komponenttype
        [{"komponenttype": "overskrift", "innhold": "hei"}],
    )
    for komponenter in ulovlige:
        with pytest.raises(psycopg.errors.CheckViolation):
            _versjon(migrator, fid, komponenter=komponenter, felt=[])
        migrator.rollback()

    # EN `laast` SOM IKKE ER EN BOOLEAN AVVISES — den blir ikke stille
    # false (CodeRabbits funn). En `"laast": "ja"` som ble tolket som
    # «ikke låst» ville låst OPP en klausul forfatteren mente å binde, og
    # det er nøyaktig invarianten `laast_klausul_endret` snudd på hodet.
    for verdi in ("ja", 1, None):
        with pytest.raises(psycopg.errors.NotNullViolation):
            _versjon(migrator, fid, komponenter=[
                {"komponenttype": "klausul", "innhold": "Bundet.",
                 "laast": verdi}], felt=[])
        migrator.rollback()

    # Et felt uten `paakrevd` er en NOT NULL-feil, ikke stilltiende
    # valgfritt: den halvferdige deklarasjonen er den som gjør at et hull
    # aldri blir rapportert.
    with pytest.raises(psycopg.errors.NotNullViolation):
        _versjon(migrator, fid,
                 komponenter=[{"komponenttype": "felt", "feltnokkel": "a"}],
                 felt=[{"feltnokkel": "a", "felttype": "tekst",
                        "beskrivelse": "A"}])
    migrator.rollback()

    # …og en tom komponentliste er ikke en mal.
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _versjon(migrator, fid, komponenter=[], felt=[])
    migrator.rollback()


@pg
def test_port7b_statusformen_er_total(migrator):
    """En «publisert» rad uten publiseringstidspunkt, og en
    «tilbaketrukket» som aldri var publisert, er urepresenterbare — også
    ved direkte INSERT som eieren."""
    fid = _familie(migrator)
    _sett_kontekst(migrator, TENANT)
    for status, ts in (("publisert", None), ("tilbaketrukket", None)):
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(
                "INSERT INTO malversjon (tenant, versjon_id, familie_id,"
                " versjonsnr, status, opprettet_av, innhold_hash,"
                " publisert_ts) VALUES (%s,%s,%s,%s,%s,'test','h',%s)",
                (TENANT, uuid.uuid4(), fid, 90, status, ts))
        migrator.rollback()
        _sett_kontekst(migrator, TENANT)
    migrator.rollback()


# ---------------------------------------------------------------------------
# Port 8: SP-1 og SP-2
# ---------------------------------------------------------------------------

@pg
def test_port8_hver_dor_kaller_kontekstporten_forst(migrator):
    """SP-1, målt på KROPPEN til hver av de fem dørene: `PERFORM
    public.krev_tenantkontekst(...)` er den FØRSTE setningen etter
    BEGIN. En dør som gjør noe som helst før kontekstporten, har gjort
    det uten å vite hvem som spør."""
    for navn in ("m5_opprett_malfamilie", "m5_opprett_malversjon",
                 "m5_publiser_malversjon", "m5_trekk_tilbake_malversjon",
                 "m5_fyll_mal"):
        kropp = migrator.execute(
            "SELECT prosrc FROM pg_proc WHERE proname=%s", (navn,)
        ).fetchone()[0]
        migrator.rollback()
        etter = kropp.split("BEGIN", 1)[1].strip()
        assert etter.startswith("PERFORM public.krev_tenantkontekst("), \
            f"{navn} gjør noe før kontekstporten: {etter[:80]!r}"


@pg
def test_port8b_gjenspill_er_stille_ja_annet_innhold_er_konflikt(migrator):
    """SP-2 (056-materialitetsformen): samme id + samme innhold gir samme
    svar og INGEN ny rad; samme id + annet innhold er en materiell
    konflikt. Uten det ville et tapt HTTP-svar + nytt klikk født en ny
    versjon av en mal som alt fantes."""
    fid = _familie(migrator)
    vid = uuid.uuid4()
    forste = _versjon(migrator, fid, vid=vid)
    igjen = _versjon(migrator, fid, vid=vid)
    assert forste == igjen == vid

    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM malversjon WHERE tenant=%s AND familie_id=%s",
        (TENANT, fid)).fetchone()[0]
    migrator.rollback()
    assert n == 1, "gjenspillet fødte en ny versjon"

    with pytest.raises(psycopg.errors.UniqueViolation):
        _versjon(migrator, fid, vid=vid, komponenter=[
            {"komponenttype": "tekst", "innhold": "noe annet"}], felt=[])
    migrator.rollback()


# ---------------------------------------------------------------------------
# Port 9: migrasjonen — ddl_begge_kjoringer_gronne
# ---------------------------------------------------------------------------

@pg
def test_port8c_manglende_grant_er_driftsfeil_ikke_tilstandsdom(migrator):
    """CodeRabbits FØRSTE funn, som port. Vaktens nei og en manglende
    GRANT er BEGGE SQLSTATE 42501 — og forskjellen er hele forskjellen:

      * vakten avviste  → 409 «malens tilstand tillater ikke dette»;
      * rollen mangler EXECUTE → DRIFTSFEIL (`db_utilgjengelig`), fordi
        sannheten er at DENNE INSTALLASJONEN ikke har fått
        `migrer.py`-grantene sine.

    Uten skillet ville en halvferdig deploy svart «tilstanden sier nei»
    på hver eneste skrivevei, og den som feilsøkte hadde lett i
    malregisteret i stedet for i rettighetsmodellen.

    Begge unntakene er EKTE — hentet fra basen, ikke konstruert — slik
    at porten måler diskriminatoren og ikke min gjetning om hva psycopg
    fyller `diag` med.

    MUTASJONEN SOM DREPER DENNE: sett `InsufficientPrivilege` tilbake i
    den generiske 409-armen.
    """
    from api.dokumentmal import _doerfeil

    _fid, vid = _publisert_mal(migrator)

    # 1. VAKTENS NEI — en RAISE inne i en PL/pgSQL-trigger.
    _sett_kontekst(migrator, TENANT)
    try:
        migrator.execute("UPDATE malversjon SET innhold_hash='juks'"
                         " WHERE tenant=%s AND versjon_id=%s", (TENANT, vid))
        raise AssertionError("vakten avviste ikke")
    except psycopg.errors.InsufficientPrivilege as e:
        vaktfeil = e
    migrator.rollback()
    avbrudd = _doerfeil(vaktfeil, "r")
    assert avbrudd is not None, "vaktens dom ble ikke oversatt"
    assert avbrudd.respons.status_code == 409

    # 2. MANGLENDE EXECUTE — planleggerens nei, uten PL/pgSQL-kontekst.
    #    `disponit_m37_claimer` har ingen rettighet på malregisteret i
    #    det hele tatt; det er nøyaktig tilstanden en installasjon uten
    #    `migrer.py`-blokken er i. (Rollen er valgt fordi migrator kan
    #    SET ROLE til den — medlemskapet finnes fra 005 — ikke fordi den
    #    har noe med M-5 å gjøre. Nettopp derfor er den riktig her.)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    try:
        migrator.execute("SELECT * FROM m5_fyll_mal(%s,%s,'{}'::jsonb)",
                         (TENANT, vid))
        raise AssertionError("rollen hadde EXECUTE likevel")
    except psycopg.errors.InsufficientPrivilege as e:
        rettighetsfeil = e
    migrator.rollback()
    assert _doerfeil(rettighetsfeil, "r") is None, (
        "en manglende GRANT ble oversatt til en tilstandsdom — da"
        " feilsøker driften i malregisteret i stedet for i"
        " rettighetsmodellen")


@pg
def test_port9_migrasjonen_er_kjort_og_bytebundet(migrator):
    """Den TOMME kjøringen er målt direkte: 094 står i `migrasjoner` med
    checksum lik sha256 av filbytene i treet — samme byte-binding
    fasiten pinner mot main."""
    cs = migrator.execute(
        "SELECT checksum FROM migrasjoner WHERE versjon=94").fetchone()
    migrator.rollback()
    assert cs is not None, "094 er ikke kjørt i testbasen"
    fil_sha = hashlib.sha256(MIGRASJON.read_bytes()).hexdigest()
    assert cs[0] == fil_sha, \
        "094 i treet er ikke bytene basen kjørte — historikk er immutable"
    fasit = json.loads(
        (ROT / "platform" / "core" / "db" / "migrasjons-fasit.json")
        .read_text(encoding="utf-8"))
    assert fasit.get("094_m5_malregister.sql") == fil_sha, \
        "fasiten pinner andre bytes enn treet bærer"


def test_port9b_migrasjonen_er_ren_ddl():
    """047-klassen: masse-DML i en migrasjon kan køe utsatte
    triggerhendelser som ALTER-setninger nekter å passere. 094 har INGEN
    seed i det hele tatt — ikke engang `rolle_scope` — så «grønn mot
    seedet base» hviler ikke på noe den ikke har.

    MUTASJONEN SOM DREPER DENNE: legg en INSERT på toppnivå i 094.
    """
    import pglast
    dml = [type(raa.stmt).__name__ for raa in
           pglast.parse_sql(MIGRASJON.read_text(encoding="utf-8"))
           if type(raa.stmt).__name__ in ("InsertStmt", "UpdateStmt",
                                          "DeleteStmt")]
    assert not dml, (
        f"094 bærer toppnivå-DML {dml} — da er den en backfill og skal"
        " registrere seed+måling i SP-10 (sp10-provekjoring.py)")


def test_port9c_094_navngir_aldri_runtime_rollen():
    """056/057-formen: `disponit` er lokalnavnet, og `migrer.py` er
    eneste rettighetskilde. En GRANT til runtime i migrasjonen ville
    lagt rettighetsmodellen to steder."""
    for linje in MIGRASJON.read_text(encoding="utf-8").splitlines():
        if linje.lstrip().startswith("--"):
            continue
        assert "TO disponit;" not in linje, \
            f"094 grantar direkte til runtime-rollen: {linje!r}"


def test_port9d_kjoreren_speiler_094_rettighetene():
    """Tabellspeilet i `migrer.py` (057-portformen): runtime får KUN
    SELECT på de fire tabellene, og EXECUTE på alle fem dørene — gitt
    SOM EIEREN, ellers blir grantene en stille WARNING. Ingen
    INSERT/UPDATE/DELETE noe sted: ALL skriving går gjennom dørene."""
    kjorer = (ROT / "deploy" / "staging" / "migrer.py").read_text(
        encoding="utf-8")
    assert ("GRANT SELECT ON malfamilie, malversjon, malkomponent,"
            " malfelt TO {rolle};") in kjorer
    for tabell in TABELLER:
        for verb in ("INSERT ON", "UPDATE ON", "DELETE ON"):
            assert f"{verb} {tabell}" not in kjorer, \
                f"runtime har fått {verb} {tabell} utenom dørene"
    eierblokk = kjorer.split("SET LOCAL ROLE disponit_mal_eier;", 1)
    assert len(eierblokk) == 2, "dørgrantene gis ikke som eieren"
    blokk = eierblokk[1].split("RESET ROLE;", 1)[0]
    for dor in ("m5_opprett_malfamilie", "m5_opprett_malversjon",
                "m5_publiser_malversjon", "m5_trekk_tilbake_malversjon",
                "m5_fyll_mal"):
        assert dor in blokk, f"{dor} mangler EXECUTE til runtime"


@pg
def test_port9f_eieren_har_kolonnegrant_ikke_tabellgrant(migrator):
    """M-3-regelen brukt på skrivesiden, målt mot
    `information_schema.column_privileges` og ikke mot kildeteksten:
    «den rører den ikke i dag» er ikke en egenskap ved en fil som endres.

    Eieren av dørene kan UPDATE-e LIVSSYKLUSKOLONNENE på `malversjon` og
    ingenting annet. `versjonsnr`, `familie_id` og `innhold_hash` er
    utenfor GRANTET, ikke bare utenfor vakten — så
    `malversjon_endret_etter_publisering` håndheves av to uavhengige
    mekanismer, og en dag noen svekker vakten står rettigheten fortsatt
    i veien.

    Og `malkomponent`/`malfelt`/`malfamilie` har INGEN UPDATE og INGEN
    DELETE i det hele tatt: append-only er en rettighetsegenskap her,
    ikke bare en trigger.

    MUTASJONEN SOM DREPER DENNE: bytt kolonnegrantet mot
    `GRANT UPDATE ON malversjon`.
    """
    kolonner = {r[0] for r in migrator.execute(
        "SELECT column_name FROM information_schema.column_privileges"
        " WHERE table_name='malversjon' AND grantee='disponit_mal_eier'"
        "   AND privilege_type='UPDATE'").fetchall()}
    migrator.rollback()
    assert kolonner == {"status", "publisert_ts", "publisert_av",
                        "tilbaketrukket_ts", "tilbaketrukket_av"}, kolonner

    for tabell in ("malkomponent", "malfelt", "malfamilie"):
        n = migrator.execute(
            "SELECT count(*) FROM information_schema.column_privileges"
            " WHERE table_name=%s AND grantee='disponit_mal_eier'"
            "   AND privilege_type IN ('UPDATE','DELETE')",
            (tabell,)).fetchone()[0]
        migrator.rollback()
        assert n == 0, f"{tabell}: eieren har fått UPDATE/DELETE"
    for tabell in TABELLER:
        n = migrator.execute(
            "SELECT count(*) FROM information_schema.table_privileges"
            " WHERE table_name=%s AND grantee='disponit_mal_eier'"
            "   AND privilege_type IN ('DELETE','TRUNCATE')",
            (tabell,)).fetchone()[0]
        migrator.rollback()
        assert n == 0, f"{tabell}: eieren har fått DELETE/TRUNCATE"


def test_port9e_rls_force_paa_hver_tabell():
    """Hver av de fire tabellene har ENABLE + FORCE + `tenant_isolasjon`.
    FORCE er det som gjør at eieren heller ikke ser forbi policyen."""
    sql = MIGRASJON.read_text(encoding="utf-8")
    for tabell in TABELLER:
        for setning in (
                f"ALTER TABLE {tabell} ENABLE ROW LEVEL SECURITY;",
                f"ALTER TABLE {tabell} FORCE ROW LEVEL SECURITY;",
                f"CREATE POLICY tenant_isolasjon ON {tabell}"):
            assert setning in sql, f"mangler: {setning}"
    assert "BYPASSRLS" not in sql


# ---------------------------------------------------------------------------
# Port 10: registrering — grensen, ruteregisteret, flatekartet
# ---------------------------------------------------------------------------

def test_port10_grensen_er_parformen_og_uendret():
    """Grensen ble registrert FØR byggingen (§0), og bygget legger
    verken til eller fjerner en invariant."""
    from manifestskjema import KRAVGRENSER, M5_INVARIANTER
    g = KRAVGRENSER["m5-v1"]
    assert g["invarianter"] == M5_INVARIANTER
    assert set(M5_INVARIANTER) == {
        "felt_uten_kilde_diktet", "laast_klausul_endret",
        "malversjon_endret_etter_publisering", "tenantlekkasje_i_malregister",
        "utfylling_skrev_dokument", "ui_axe_alvorlige_brudd"}
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["krav_ja"] == ("ddl_begge_kjoringer_gronne",)
    assert g["punktbinding"] == {}


def test_port10b_rutene_og_scopene_er_bundet():
    """RUTESCOPE og `Route()` i samme commit (PR-008), og scopevalget
    er 079s: `decisions:read` for lesing, `bestilling:opprett` for det
    som endrer registeret — INGEN nye scopes.

    UTFYLLINGEN bærer LESEscopet, og det er porten som holder den der:
    en dag noen gir den `bestilling:opprett`, faller denne testen og
    spørsmålet «hvorfor trenger en utfylling skrivemyndighet?» må
    besvares i PR-en i stedet for i produksjon.
    """
    from api.app import LESESCOPES, RUTESCOPE
    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert RUTESCOPE[("GET", "/v1/dokumentmal")] == "decisions:read"
    assert RUTESCOPE[
        ("POST", "/v1/dokumentmal/versjon/{versjon_id:str}/utfylling")
    ] == "decisions:read", "utfyllingen har fått et mutasjonsscope"
    for sti in ("/v1/dokumentmal/familier", "/v1/dokumentmal/versjoner",
                "/v1/dokumentmal/versjon/{versjon_id:str}/publiser",
                "/v1/dokumentmal/versjon/{versjon_id:str}/trekk-tilbake"):
        assert RUTESCOPE[("POST", sti)] == "bestilling:opprett"
    # Ingen NYE scopes: begge er alt i autorisasjonslaget.
    alle = {s for ss in ROLLE_TIL_SCOPES.values() for s in ss}
    assert {"decisions:read", "bestilling:opprett"} <= alle
    assert "decisions:read" in LESESCOPES


def test_port10c_flaten_er_registrert_som_modulflate():
    """Ruten i `sitekart.js` og flaten i `app.js` — og `modulflate: 5`,
    fordi M-5 er en modul kunden kjøper og ikke plattformens eget
    innsyn i seg selv (eiervedtaket 24/8)."""
    ui = ROT / "platform" / "core" / "ui" / "static" / "js"
    sitekart = (ui / "sitekart.js").read_text(encoding="utf-8")
    assert ('{ nokkel: "dokumentmal", scope: "decisions:read",'
            ' modulflate: 5 }') in sitekart
    appjs = (ui / "app.js").read_text(encoding="utf-8")
    assert "dokumentmal: visDokumentmal," in appjs
    assert 'from "./flater/dokumentmal.js"' in appjs


def test_port10d_locale_paritet_for_dokumentmal():
    """Hver `ui.dokumentmal.*`-nøkkel finnes i BÅDE nb og en. `t()`
    faller tilbake til nøkkelen, ikke til nb — en manglende engelsk
    nøkkel ville vist «ui.dokumentmal.utfylling.mangler» der ordet
    «Missing» skulle stått, midt i det ene ordet flaten finnes for."""
    nb = json.loads((ROT / "locales" / "nb.json").read_text(encoding="utf-8"))
    en = json.loads((ROT / "locales" / "en.json").read_text(encoding="utf-8"))
    mine = [k for k in nb if k.startswith("ui.dokumentmal.")
            or k == "ui.nav.dokumentmal"]
    assert len(mine) >= 40, f"for få nøkler i porten: {len(mine)}"
    for k in mine:
        assert isinstance(en.get(k), str) and en[k].strip(), \
            f"en.json mangler {k}"


# ---------------------------------------------------------------------------
# HTTP-veien ende til ende
# ---------------------------------------------------------------------------

def _browserokt(migrator, roller):
    """Minirigg: en innlogget browserøkt med gitte roller i TENANT.
    -> (sesjonscookie, csrf-token)."""
    from api import sesjon as sesjonmodul
    _sett_kontekst(migrator, TENANT)
    bid = migrator.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://m5.test', %s) RETURNING bruker_id",
        ("s5h-" + secrets.token_hex(6),)).fetchone()[0]
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


@pg
def test_http_utfyllingen_returnerer_hullene_og_lagrer_ingenting(migrator,
                                                                klient):
    """Hele veien: opprett → publiser → fyll ut med ETT felt dekket.

    Svaret navngir det manglende feltet i `mangler`, komponenten bærer
    `tekst: null`, og radantallet er uendret etterpå. Det er den samme
    dommen som port 1 og 5, målt gjennom HTTP — fordi det er DER en
    «hjelpsom» normalisering (`?? ""`) pleier å snike seg inn.
    """
    from .test_rekruttering_http import _post
    cookie, csrf = _browserokt(migrator, ["admin"])

    r = _post(klient, cookie, csrf, "/v1/dokumentmal/familier",
              {"navn": "Arbeidsavtale", "beskrivelse": "Standard"})
    assert r.status_code == 200, r.text
    fid = r.json()["familie_id"]

    r = _post(klient, cookie, csrf, "/v1/dokumentmal/versjoner",
              {"familie_id": fid, "komponenter": KOMPONENTER, "felt": FELT})
    assert r.status_code == 200, r.text
    vid = r.json()["versjon_id"]

    r = _post(klient, cookie, csrf,
              f"/v1/dokumentmal/versjon/{vid}/publiser", {})
    assert r.status_code == 200, r.text

    for_tall = _radantall(migrator)
    r = _post(klient, cookie, csrf,
              f"/v1/dokumentmal/versjon/{vid}/utfylling",
              {"verdier": {"arbeidsgiver": "Acme AS"}})
    assert r.status_code == 200, r.text
    kropp = r.json()
    assert kropp["mangler"] == ["arbeidstaker"]
    assert kropp["fullstendig"] is False
    felt = {k["feltnokkel"]: k for k in kropp["komponenter"]
            if k["komponenttype"] == "felt"}
    assert felt["arbeidsgiver"]["tekst"] == "Acme AS"
    assert felt["arbeidstaker"]["tekst"] is None, \
        "HTTP-laget normaliserte et manglende felt til noe som ser utfylt ut"
    assert felt["arbeidstaker"]["dekket"] is False
    tekster = [k["tekst"] for k in kropp["komponenter"]
               if k["tekst"] is not None]
    assert "" not in tekster and not any("arbeidstaker" in t for t in tekster)
    assert _radantall(migrator) == for_tall, "utfyllingen skrev noe"


@pg
@dekker("dokumentmal_ulovlig_tilstand")
def test_http_udeklarert_felt_er_409_ikke_400_og_ikke_500(migrator, klient):
    """FEILVEIEN ende til ende. Kroppen ER velformet; det er TILSTANDEN
    som sier nei, og forskjellen er hele forklaringen mennesket trenger
    («deklarer feltet», ikke «noe gikk galt»).

    Merk hvem som feller dommen: API-et teller ikke feltnøkler. Det
    kaller døren og oversetter dørens ERRCODE. En flate eller et API som
    sjekket selv ville vært en ANDRE sannhet å komme i utakt med.
    """
    from .test_rekruttering_http import _post
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _post(klient, cookie, csrf, "/v1/dokumentmal/familier",
              {"navn": "Tilbudsbrev"})
    fid = r.json()["familie_id"]
    r = _post(klient, cookie, csrf, "/v1/dokumentmal/versjoner",
              {"familie_id": fid,
               "komponenter": [{"komponenttype": "felt",
                                "feltnokkel": "mottaker"}],
               "felt": []})
    assert r.status_code == 200, r.text
    vid = r.json()["versjon_id"]

    r = _post(klient, cookie, csrf,
              f"/v1/dokumentmal/versjon/{vid}/publiser", {})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "dokumentmal_ulovlig_tilstand"


@pg
def test_http_utfyllingen_krever_lesescopet_og_csrf(migrator, klient):
    """En økt UTEN `decisions:read` kommer ikke til utfyllingen, og en
    POST uten CSRF-token avvises — selv om ruten bærer et lesescope.

    Det siste er poenget: at kallet ikke skriver gjør det ikke
    ubeskyttet. `_browserkontekst` gir samme dobbel-innsending som
    resten av browserveiene.
    """
    from api import sesjon as sesjonmodul
    from .test_rekruttering_http import _post
    _fid, vid = _publisert_mal(migrator)

    cookie, csrf = _browserokt(migrator, ["domeneadjudikator"])
    # `domeneadjudikator` har `decisions:read` — CSRF-porten måles her.
    r = klient.post(f"/v1/dokumentmal/versjon/{vid}/utfylling",
                    json={"verdier": {}},
                    cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code in (403, 409), r.text
    assert r.json()["feil"] == "csrf_ugyldig"

    # …men uten SKRIVEscopet stopper opprettelsen.
    r = _post(klient, cookie, csrf, "/v1/dokumentmal/familier",
              {"navn": "Nei"})
    assert r.status_code == 403, r.text
    assert r.json()["feil"] == "scope_mangler"


@pg
def test_http_feilformet_komponentliste_er_400_ikke_409(migrator, klient):
    """CodeRabbits andre funn, som port: `_liste` validerer BEVISST ikke
    komponentformen selv — CHECK-ene i 094 ER forespørselsvalideringen
    (én kilde til formen, ikke to som kan drifte fra hverandre). Da må
    en `check_violation` derfra komme ut som 400, ikke som 409.

    Forskjellen er ikke akademisk: et 409 ville sagt «malens tilstand
    tillater ikke dette» om en komponentliste som aldri var velformet, og
    etterlatt forfatteren uten å vite at det var HENNES kropp som var
    gal. 409 er reservert for tilstanden — se
    `test_http_udeklarert_felt_er_409_ikke_400_og_ikke_500`.
    """
    from .test_rekruttering_http import _post
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _post(klient, cookie, csrf, "/v1/dokumentmal/familier",
              {"navn": "Feilformet"})
    fid = r.json()["familie_id"]

    for komponenter in (
            # 'tekst' MED laast=true — bare klausuler kan låses
            [{"komponenttype": "tekst", "innhold": "hei", "laast": True}],
            # 'felt' MED innhold
            [{"komponenttype": "felt", "feltnokkel": "a", "innhold": "juks"}],
            # `laast` som ikke er en boolean → NOT NULL, ikke stille false
            [{"komponenttype": "klausul", "innhold": "Bundet.",
              "laast": "ja"}]):
        r = _post(klient, cookie, csrf, "/v1/dokumentmal/versjoner",
                  {"familie_id": fid, "komponenter": komponenter,
                   "felt": []})
        assert r.status_code == 400, (komponenter, r.status_code, r.text)
        assert r.json()["feil"] == "request_feilformet"
