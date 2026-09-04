"""M-3 Datakvalitetsagent v1 (migrasjon 092) — de åtte invariantene.

`M3_INVARIANTER` i `manifestskjema.py` ble registrert FØR koden (§0), og
hver av dem har minst én port her. Porten måler både FORSØKET og
BRUDDET: en invariant som aldri ble prøvd har ikke bevist noe.

  1. `profil_leser_payloadkolonne` — profilerrollens kolonnegrants måles
     mot `information_schema.column_privileges`, ikke mot kildeteksten.
     En kryptert payloadkolonne i grantet feller porten.
  2. `maaler_har_skriverett_utenfor_egne_tabeller` — målerollen har null
     INSERT/UPDATE/DELETE/TRUNCATE i HELE basen, målt mot
     `information_schema.role_table_grants`.
  3. `umaalbar_tabell_talt_som_null` — modulens bærende regel, målt to
     veier: trukket kolonnegrant OG overskredet tidsbudsjett gir begge
     funnet `umaalbar` og INGEN profilrad med 0 avvik.
  4. `funntype_utenfor_lukket_sett` — CHECK-en avviser en ukjent
     funntype.
  5. `profil_endret_etter_innsetting` — append-only på profil og
     kjøring, for ENHVER rolle (også eieren).
  6. `tenantlekkasje_i_profil` — tenant A ser aldri tenant Bs tall,
     verken ved direkte DML eller over API-et.
  7. `bestilling_blokkert_av_kvalitetsmaaling` — v1-DOMMEN, håndhevet:
     statisk (ingen import fra bestillingsveien, ingen DML mot
     `bestilling*`/`oppdrag`/`policyer`) OG funksjonelt (en bestilling
     går uendret gjennom med et rødt kvalitetsfunn liggende i basen).
  8. `ui_axe_alvorlige_brudd` — flateporten bor i
     `platform/core/ui/test/datakvalitet.test.js` (jsdom + axe-core) og
     kjøres av `npm test`, ikke herfra. Rapportporten under krever
     likevel at tallet er MÅLT før grensen kan sies bestått.

I tillegg: registervakten (en regel mot en kolonne som ikke finnes
avvises), driftsformen (overlappende kjøring → `hoppet_over` med
feiltelleren urørt; to sammenhengende feil → alarm), idempotensen (to
kjøringer på uendret base gir to kjøringer og INGEN nye funnrader) og
SP-10 (migrasjonen er grønn fra tom base og mot seedet base).

Alle DB-tester konstruerer egen tilstand; ingen delt fixture.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import uuid
from pathlib import Path

import pytest

from .test_api import (DSN, MIGRATOR_DSN, ANNEN_TENANT,  # noqa: F401
                       TENANT, _lag_token, app, dekker, klient, migrator,
                       miljo)
from .test_m37 import _sett_kontekst

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "092_m3_datakvalitet.sql")
MODULROT = ROT / "platform" / "modules" / "m03_datakvalitet"
DRIFTROT = ROT / "platform" / "drift"

KVALITETSMAALER_DSN = os.environ.get("DISPONIT_TEST_KVALITETSMAALER_DSN")

#: Sentinel-tenanten registerfunnene bæres av (se migrasjonens §9).
PLATTFORM = "__plattform_kvalitet"

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")


# ---------------------------------------------------------------------------
# Riggen
# ---------------------------------------------------------------------------

def _m():
    from db.pg import koble
    return koble(MIGRATOR_DSN)


def _rt():
    from db.pg import koble
    return koble(DSN)


def _km():
    """Målerollens EGEN tilkobling. Faller tilbake til migrator kun for
    de testene som ikke måler rettighetsgrensen selv — grenseportene
    hopper eksplisitt av uten den ekte DSN-en."""
    from db.pg import koble
    return koble(KVALITETSMAALER_DSN or MIGRATOR_DSN)


def _nullstill(m):
    """Tømmer M-3s tre lagre. Krever eieren — se `_rydd_kvalitet`.

    Hjelperen eier ikke transaksjonen (den kalles midt i `_rydd`s), så
    committen hører kalleren til — og her MÅ den skje: profileringen
    kjører på en ANNEN tilkobling og ville ellers verken sett
    slettingen eller sluppet forbi låsene den holder.
    """
    from .test_api import _rydd_kvalitet
    _rydd_kvalitet(m)
    m.commit()


def _eiersporring(m, sql, args=()):
    """Leser M-3s lagre SOM EIEREN.

    Migrator har ingen rettighet på noen av de fire tabellene — heller
    ikke SELECT. At testene må ta rollen eksplisitt for å se en eneste
    rad er i seg selv en måling: rettighetsmodellen står, også for den
    som eier resten av basen.
    """
    m.execute("SET LOCAL ROLE disponit_kvalitet_eier")
    rader = m.execute(sql, args).fetchall()
    m.execute("RESET ROLE")
    m.rollback()
    return rader


def _fjern_provregler(m):
    """Fjerner regler testene selv la inn (prefiks `prove.`).

    Seedet fra migrasjonen står igjen: det ER registeret, og en test som
    tømte det ville målt en modul som ikke finnes.
    """
    m.execute("SET LOCAL ROLE disponit_kvalitet_eier")
    m.execute("DELETE FROM kvalitetsregel WHERE regel_id LIKE 'prove.%'")
    m.execute("RESET ROLE")
    m.commit()


def _regel(m, regel_id, relasjon, kolonne, regeltype, *, uttrykk=None,
           alvorlighet="lav", terskel=0, begrunnelse="port"):
    """Legger en regel i registeret SOM EIEREN. Registeret endres ellers
    kun i migrasjon; her er testen den migrasjonen."""
    m.execute("SET LOCAL ROLE disponit_kvalitet_eier")
    try:
        m.execute(
            "INSERT INTO kvalitetsregel (regel_id, relasjon, kolonne,"
            " regeltype, uttrykk, alvorlighet, terskel_andel, begrunnelse)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (regel_id, relasjon, kolonne, regeltype, uttrykk, alvorlighet,
             terskel, begrunnelse))
    finally:
        # RESET ROLE i en ABORTERT transaksjon kaster selv, og ville da
        # maskert nøyaktig den CheckViolation porten måler.
        try:
            m.execute("RESET ROLE")
        except Exception:
            pass
    m.commit()


def _bruker(m, tenant=TENANT):
    _sett_kontekst(m, tenant)
    ut = m.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://m3.test', %s) RETURNING bruker_id",
        ("s3-" + secrets.token_hex(6),)).fetchone()[0]
    m.commit()
    return ut


def _kontakt(m, tenant, *, rolle="driftsvakt", prioritet=1, bruker=None,
             bekreftet_av=None):
    """En beredskapskontakt. `bekreftet_av` har med vilje INGEN
    fremmednøkkel i 089 — det er nettopp derfor M-3 måler den."""
    # Brukeren opprettes FØRST og for seg: `_bruker` committer, og en
    # commit midt i argumentlisten ville kastet `SET LOCAL
    # disponit.tenant` — og dermed felt RLS-vakten på innsettingen under.
    bruker = bruker or _bruker(m, tenant)
    _sett_kontekst(m, tenant)
    m.execute(
        "INSERT INTO beredskapskontakt (tenant, kontakt_id, rolle,"
        " prioritet, bruker_id, bekreftet_ts, bekreftet_av)"
        " VALUES (%s,%s,%s,%s,%s,"
        "         CASE WHEN %s::text IS NULL THEN NULL ELSE now() END, %s)",
        (tenant, uuid.uuid4(), rolle, prioritet, bruker,
         bekreftet_av, bekreftet_av))
    m.commit()


def _profiler(conn, grense=50, tidsgrense_ms=5000):
    conn.execute("SELECT set_config('disponit.kvalitet_tidsgrense_ms',"
                 " %s, true)", (str(tidsgrense_ms),))
    rad = conn.execute("SELECT * FROM m3_profiler(%s)", (grense,)).fetchone()
    conn.commit()
    return {"kjoring_id": str(rad[0]), "antall_regler": rad[1],
            "antall_umaalbare": rad[2], "antall_funn": rad[3],
            "avbrutt": rad[4]}


def _funn(m, tenant=None):
    m.execute("SET LOCAL ROLE disponit_kvalitet_eier")
    m.execute("ALTER TABLE kvalitetsfunn NO FORCE ROW LEVEL SECURITY")
    if tenant is None:
        rader = m.execute(
            "SELECT tenant, regel_id, funntype, ganger_sett, detaljer"
            " FROM kvalitetsfunn ORDER BY tenant, regel_id").fetchall()
    else:
        rader = m.execute(
            "SELECT tenant, regel_id, funntype, ganger_sett, detaljer"
            " FROM kvalitetsfunn WHERE tenant=%s ORDER BY regel_id",
            (tenant,)).fetchall()
    m.execute("ALTER TABLE kvalitetsfunn FORCE ROW LEVEL SECURITY")
    m.execute("RESET ROLE")
    m.commit()
    return rader


def _profilrader(m, regel_id=None):
    m.execute("SET LOCAL ROLE disponit_kvalitet_eier")
    m.execute("ALTER TABLE kvalitetsprofil NO FORCE ROW LEVEL SECURITY")
    sql = ("SELECT tenant, regel_id, rader_vurdert, rader_avvik, andel_avvik"
           " FROM kvalitetsprofil")
    rader = (m.execute(sql + " WHERE regel_id=%s", (regel_id,)).fetchall()
             if regel_id else m.execute(sql).fetchall())
    m.execute("ALTER TABLE kvalitetsprofil FORCE ROW LEVEL SECURITY")
    m.execute("RESET ROLE")
    m.commit()
    return rader


@pytest.fixture()
def ren(migrator):
    """Tom M-3-tilstand før og etter. Registerseedet står."""
    _nullstill(migrator)
    _fjern_provregler(migrator)
    yield migrator
    _nullstill(migrator)
    _fjern_provregler(migrator)


# ---------------------------------------------------------------------------
# Invariant 1: profil_leser_payloadkolonne
# ---------------------------------------------------------------------------

#: Kolonner som ALDRI skal stå i profilerrollens grants. Ikke en
#: heuristikk over navn alene: listen er navngitt, OG mønsteret fanger
#: nye kolonner ingen har tenkt på ennå.
PAYLOADMONSTER = re.compile(
    r"(_kryptert$|^nonce$|^key_id$|^secret|^.*hash$|^payload$|"
    r"^profil$|^issuer$|^sub$|^postboks$|^parametre$|^tekst$|^innhold$)")


@pg
def test_inv1_profileren_har_ingen_payloadkolonne_i_grantene(migrator):
    """Målt mot BASEN, ikke mot kildeteksten: «den leser den ikke i dag»
    er ikke en egenskap ved en fil som endres.

    Porten måler FORSØKET også — at det finnes kolonnegrants i det hele
    tatt. En rolle uten ett eneste grant ville trivielt vært fri for
    payloadkolonner, og porten ville sagt grønt om en modul som ikke
    kan måle noe som helst.

    MUTASJONEN SOM DREPER DENNE: legg `kropp_kryptert` (eller enhver
    annen payloadkolonne) i en `GRANT SELECT (...)` i 092.
    """
    rader = migrator.execute(
        "SELECT table_name, column_name FROM information_schema"
        ".column_privileges WHERE grantee = 'disponit_kvalitet_eier'"
        " ORDER BY table_name, column_name").fetchall()
    migrator.rollback()
    assert rader, ("profilerrollen har ingen kolonnegrants — da måler"
                   " porten ingenting, og modulen kan ikke profilere noe")
    # Ingen TABELLgrant: en rad uten kolonnenavn ville betydd at hele
    # tabellen er gitt bort, og da er kolonnegrensen ingen grense.
    tabellgrants = migrator.execute(
        "SELECT table_name, privilege_type FROM information_schema"
        ".role_table_grants WHERE grantee = 'disponit_kvalitet_eier'"
        " AND table_schema = 'public'").fetchall()
    migrator.rollback()
    egne = {"kvalitetsregel", "kvalitetskjoring", "kvalitetsprofil",
            "kvalitetsfunn"}
    fremmede = [(t, p) for t, p in tabellgrants if t not in egne]
    assert not fremmede, (
        f"profilerrollen har TABELLgrant på fremmede tabeller: {fremmede}"
        " — kolonnegrensen er da ingen grense")
    forbudte = [(t, k) for t, k in rader
                if t not in egne and PAYLOADMONSTER.search(k)]
    assert not forbudte, f"payloadkolonner i profilerens grants: {forbudte}"


@pg
def test_inv1b_porten_ville_fanget_en_payloadkolonne(migrator):
    """MÅLINGEN ER RØD UTEN FIKSEN: gis profileren en kryptert
    payloadkolonne, feller porten. Uten denne kontrollen kunne mønsteret
    over vært skrevet feil og alltid sagt grønt."""
    migrator.execute(
        "GRANT SELECT (kropp_kryptert) ON epost_melding"
        " TO disponit_kvalitet_eier")
    try:
        rader = migrator.execute(
            "SELECT table_name, column_name FROM information_schema"
            ".column_privileges WHERE grantee = 'disponit_kvalitet_eier'"
            " AND table_name = 'epost_melding'").fetchall()
        assert any(PAYLOADMONSTER.search(k) for _, k in rader), \
            "mønsteret fanger ikke en kryptert payloadkolonne"
    finally:
        migrator.execute(
            "REVOKE SELECT (kropp_kryptert) ON epost_melding"
            " FROM disponit_kvalitet_eier")
        migrator.commit()


# ---------------------------------------------------------------------------
# Invariant 2: maaler_har_skriverett_utenfor_egne_tabeller
# ---------------------------------------------------------------------------

@pg
def test_inv2_maaleren_har_null_skriverett_i_hele_basen(migrator):
    """Målt mot `information_schema.role_table_grants`, ikke mot
    kildekoden. En kompromittert profileringsjobb kan telle, og
    ingenting annet — den har ikke engang SELECT på sine egne lagre.

    MUTASJONEN SOM DREPER DENNE: legg en `GRANT INSERT`/`SELECT` til
    `disponit_kvalitetsmaaler` i migrasjonen eller i migrer.py.
    """
    rader = migrator.execute(
        "SELECT table_name, privilege_type FROM information_schema"
        ".role_table_grants WHERE grantee = 'disponit_kvalitetsmaaler'"
    ).fetchall()
    migrator.rollback()
    assert rader == [], f"målerollen har tabellrettigheter: {rader}"

    # FORSØKET: at rollen finnes og HAR nøyaktig én EXECUTE. En rolle
    # som ikke eksisterer ville også hatt null grants.
    funksjoner = migrator.execute(
        "SELECT p.proname FROM pg_proc p, aclexplode(p.proacl) a"
        " WHERE a.grantee = (SELECT oid FROM pg_roles"
        "                     WHERE rolname='disponit_kvalitetsmaaler')"
        "   AND a.privilege_type = 'EXECUTE'").fetchall()
    migrator.rollback()
    assert [f[0] for f in funksjoner] == ["m3_profiler"], (
        "målerollen skal ha EXECUTE på nøyaktig én funksjon, fikk"
        f" {[f[0] for f in funksjoner]}")


@pg
@pytest.mark.skipif(not KVALITETSMAALER_DSN,
                    reason="DISPONIT_TEST_KVALITETSMAALER_DSN ikke satt")
def test_inv2b_maaleren_kan_ikke_skrive_selv_om_den_prover(ren):
    """Rettighetstabellen er én ting; et faktisk forsøk er en annen.
    Måleren prøver å skrive i sitt eget lager og blir avvist."""
    import psycopg
    km = _km()
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            km.execute("INSERT INTO kvalitetskjoring (kjoring_id,"
                       " startet_ts, antall_regler, antall_umaalbare,"
                       " antall_funn, avbrutt) VALUES"
                       " (gen_random_uuid(), now(), 0, 0, 0, false)")
        km.rollback()
        # ... og den kan ikke engang LESE profilene den selv skrev.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            km.execute("SELECT * FROM kvalitetsprofil")
        km.rollback()
    finally:
        km.close()


# ---------------------------------------------------------------------------
# Registervakten: en regel mot en kolonne som ikke finnes
# ---------------------------------------------------------------------------

@pg
def test_registervakten_avviser_regel_uten_kolonne(ren):
    """En regel som peker på en kolonne som ikke finnes er en løgn
    registeret ikke skal kunne bære. Kontrollen er tosidig: den SAMME
    innsettingen lykkes mot en kolonne som finnes.

    MUTASJONEN SOM DREPER DENNE: fjern `m3_regel_vakt`-triggeren.
    """
    import psycopg
    m = ren
    # Positivkontrollen først — ellers måler porten bare at INSERT feiler.
    _regel(m, "prove.finnes.ikke_tom", "beredskapskontakt", "rolle",
           "ikke_tom")
    for relasjon, kolonne, hva in (
            ("beredskapskontakt", "kolonne_som_ikke_finnes", "kolonnen"),
            ("tabell_som_ikke_finnes", "rolle", "relasjonen"),
            # Uten tenant-kolonne kan profilen aldri skrives — regelen
            # ville stått i registeret og aldri produsert et tall.
            ("brukeridentitet", "bruker_id", "tenant-kolonnen")):
        with pytest.raises(psycopg.errors.CheckViolation):
            _regel(m, f"prove.avvist.{secrets.token_hex(3)}", relasjon,
                   kolonne, "ikke_tom")
        m.rollback()
    # Fremmednøkkelmålet verifiseres i BEGGE ender.
    with pytest.raises(psycopg.errors.CheckViolation):
        _regel(m, "prove.doedt.maal", "beredskapskontakt", "bekreftet_av",
               "fremmednokkel_lever", uttrykk="brukeridentitet.finnes_ikke")
    m.rollback()
    # Et ugyldig regex avvises der feilen ble gjort, ikke ved hver kjøring.
    with pytest.raises(psycopg.errors.CheckViolation):
        _regel(m, "prove.raatt.regex", "beredskapskontakt", "rolle",
               "format", uttrykk="[ulukket")
    m.rollback()


@pg
def test_seedet_register_er_helt_og_dekker_alle_regeltypene(ren):
    """Registeret er seedet i migrasjonen og skal dekke hele det lukkede
    regeltypesettet — ellers står en gren av profileren uten en eneste
    regel som kan utløse den."""
    m = ren
    rader = _eiersporring(
        m, "SELECT regel_id, regeltype, begrunnelse FROM kvalitetsregel"
           " ORDER BY regel_id")
    assert len(rader) >= 7, f"for få seedede regler: {len(rader)}"
    assert {r[1] for r in rader} == {
        "ikke_tom", "format", "unik_innen_tenant", "fremmednokkel_lever"}
    for regel_id, _, begrunnelse in rader:
        assert begrunnelse and begrunnelse.strip(), \
            f"{regel_id} har tom begrunnelse — en regel uten hvorfor"


# ---------------------------------------------------------------------------
# Invariant 3: umaalbar_tabell_talt_som_null (modulens bærende regel)
# ---------------------------------------------------------------------------

@pg
def test_inv3_trukket_grant_gir_umaalbar_og_ingen_nullprofil(ren):
    """MODULENS BÆRENDE REGEL. Trekkes kolonnegrantet, skal regelen
    rapporteres som FUNN — aldri som en profilrad med 0 avvik.

    Porten måler begge sider: FØRST at regelen faktisk gir profilrader
    når grantet er der (forsøket), så at den gir `umaalbar` og INGEN
    profilrad når det ikke er det (bruddet).

    MUTASJONEN SOM DREPER DENNE: la unntaksveien i `m3_profiler` skrive
    en profilrad med rader_vurdert=0, rader_avvik=0.
    """
    m = ren
    _kontakt(m, TENANT, rolle="driftsvakt", prioritet=1)
    km = _km()
    try:
        # Forsøket: med grantet på plass MÅLES regelen.
        _profiler(km)
        for_rader = _profilrader(m, "beredskap.rolle.ikke_tom")
        assert for_rader, "regelen ga ingen profilrad med grantet på plass"

        _nullstill(m)
        m.execute("REVOKE SELECT (rolle) ON beredskapskontakt"
                  " FROM disponit_kvalitet_eier")
        m.commit()
        try:
            res = _profiler(km)
        finally:
            m.execute("GRANT SELECT (rolle) ON beredskapskontakt"
                      " TO disponit_kvalitet_eier")
            m.commit()
    finally:
        km.close()

    assert res["antall_umaalbare"] >= 1
    funn = _funn(m, PLATTFORM)
    umaalbare = [f for f in funn if f[2] == "umaalbar"]
    assert [f[1] for f in umaalbare] == ["beredskap.rolle.ikke_tom"], \
        f"forventet ett umaalbar-funn, fikk {funn}"
    assert _profilrader(m, "beredskap.rolle.ikke_tom") == [], (
        "en regel som ikke kunne måles fikk en profilrad — «0 avvik»"
        " fordi målingen ikke kjørte er ikke en grønn profil")
    # Kjøringen NAVNGIR den umålbare regelen, ikke bare teller den.
    navn = _eiersporring(
        m, "SELECT umaalbare_regler FROM kvalitetskjoring"
           " ORDER BY startet_ts DESC LIMIT 1")[0][0]
    assert list(navn) == ["beredskap.rolle.ikke_tom"]


@pg
def test_inv3b_tidsbudsjett_under_maalekostnaden_gir_samme_utfall(ren):
    """SAMME UTFALL som et trukket grant: en regel som koster mer enn
    tidsbudsjettet forkastes og rapporteres `umaalbar`, aldri som 0.

    Kostnaden er DETERMINISTISK: regelen peker på en relasjon som sover
    et halvt sekund per rad. Budsjettet settes til 50 ms.

    FUNNET UNDERVEIS, og grunnen til at porten er skrevet slik:
    `statement_timeout` armes én gang per TOPPNIVÅSETNING, så en
    `SET LOCAL statement_timeout` inne i `m3_profiler` ville ikke gjeldt
    funksjonens egne setninger. Budsjettet måles derfor på klokka og
    håndheves ved å FORKASTE målingen. Denne testen er beviset på at
    forkastingen faktisk skjer.
    """
    m = ren
    # DDL kan ikke parameteriseres; `TENANT` er en konstant i denne
    # filen, ikke inndata.
    # Verdien er ALLTID NULL: regelen gir dermed 1 av 1 avvik og
    # utløser funnet `terskel_overskredet` — som er nettopp det som SKAL
    # rulles tilbake sammen med profilraden når budsjettet sprekker.
    m.execute("CREATE OR REPLACE VIEW prove_treg_relasjon AS"
              f" SELECT '{TENANT}'::text AS tenant,"
              "        CASE WHEN pg_sleep(0.5) IS NULL THEN 'x'"
              "             ELSE NULL END::text AS verdi")
    m.execute("GRANT SELECT (tenant, verdi) ON prove_treg_relasjon"
              " TO disponit_kvalitet_eier")
    m.commit()
    km = _km()
    try:
        _regel(m, "prove.treg.ikke_tom", "prove_treg_relasjon", "verdi",
               "ikke_tom")
        # Forsøket: med ROMSLIG budsjett måles den, gir en profilrad OG
        # et rødt funn — begge deler må finnes for at bruddet under skal
        # bety noe.
        romslig = _profiler(km, tidsgrense_ms=60_000)
        assert _profilrader(m, "prove.treg.ikke_tom"), \
            "den trege regelen ble ikke målt selv med romslig budsjett"
        assert any(f[1] == "prove.treg.ikke_tom"
                   and f[2] == "terskel_overskredet" for f in _funn(m))
        assert romslig["antall_funn"] >= 1

        _nullstill(m)
        res = _profiler(km, tidsgrense_ms=50)
    finally:
        km.close()
        # Prøveregelen fjernes IKKE her: funnene under peker på den, og
        # `ren`-fixturen rydder i riktig rekkefølge (funn og profil
        # først, så registeret).
        m.execute("DROP VIEW IF EXISTS prove_treg_relasjon")
        m.commit()

    assert res["antall_umaalbare"] >= 1
    umaalbare = [f for f in _funn(m, PLATTFORM)
                 if f[2] == "umaalbar" and f[1] == "prove.treg.ikke_tom"]
    assert len(umaalbare) == 1, "den trege regelen ga ikke funnet umaalbar"
    assert umaalbare[0][4]["grense_ms"] == 50
    assert umaalbare[0][4]["brukt_ms"] > 50
    assert _profilrader(m, "prove.treg.ikke_tom") == [], (
        "en forkastet måling etterlot en profilrad")
    # ... OG funnet den rakk å reise er rullet tilbake med den, både i
    # basen og i kjøringens eget tall. En teller som overlevde en
    # rollback ville rapportert funn som ikke finnes.
    assert not [f for f in _funn(m, TENANT)
                if f[1] == "prove.treg.ikke_tom"], \
        "et funn fra en forkastet måling ble stående"
    # Telleren måles mot VIRKELIGHETEN: lagrene ble tømt før kjøringen,
    # så antall NYE funn er nøyaktig antall rader som står igjen.
    assert res["antall_funn"] == len(_funn(m)), (
        "kjøringen teller funn fra en måling som ble rullet tilbake —"
        f" antall_funn={res['antall_funn']}, rader={len(_funn(m))}")


@pg
def test_inv3c_droppet_kolonne_gir_regel_uten_kolonne_ikke_null(ren):
    """Registeret kan bli usant etter at vakten holdt: kolonnen kan
    droppes. Da er regelen et FUNN — ikke en stille null."""
    m = ren
    m.execute("DROP TABLE IF EXISTS prove_flyktig")
    m.execute("CREATE TABLE prove_flyktig (tenant TEXT NOT NULL,"
              " verdi TEXT, ekstra TEXT)")
    m.execute("GRANT SELECT (tenant, verdi, ekstra) ON prove_flyktig"
              " TO disponit_kvalitet_eier")
    m.commit()
    km = _km()
    try:
        _regel(m, "prove.flyktig.ikke_tom", "prove_flyktig", "ekstra",
               "ikke_tom")
        m.execute("ALTER TABLE prove_flyktig DROP COLUMN ekstra")
        m.commit()
        _profiler(km)
    finally:
        km.close()
        m.execute("DROP TABLE IF EXISTS prove_flyktig")
        m.commit()
    typer = {f[2] for f in _funn(m, PLATTFORM)
             if f[1] == "prove.flyktig.ikke_tom"}
    assert typer == {"regel_uten_kolonne"}, f"fikk {typer}"
    assert _profilrader(m, "prove.flyktig.ikke_tom") == []


# ---------------------------------------------------------------------------
# Invariant 4: funntype_utenfor_lukket_sett
# ---------------------------------------------------------------------------

@pg
def test_inv4_ukjent_funntype_avvises_av_check(ren):
    """Lukket sett (m6-formen): en ukjent funntype er en feil, aldri en
    ny kategori som stille oppstår. Kontrollen er tosidig — de fire
    lovlige går inn, en femte gjør det ikke."""
    import psycopg
    m = ren
    kjoring = uuid.uuid4()
    m.execute("SET LOCAL ROLE disponit_kvalitet_eier")
    m.execute("INSERT INTO kvalitetskjoring (kjoring_id, startet_ts,"
              " antall_regler, antall_umaalbare, antall_funn, avbrutt)"
              " VALUES (%s, now(), 0, 0, 0, false)", (kjoring,))
    m.execute("RESET ROLE")
    m.commit()
    regel = _eiersporring(m, "SELECT regel_id FROM kvalitetsregel"
                             " ORDER BY regel_id LIMIT 1")[0][0]

    for funntype in ("umaalbar", "terskel_overskredet",
                     "regel_uten_kolonne", "ukjent_tabell"):
        _sett_kontekst(m, PLATTFORM)
        m.execute("SET LOCAL ROLE disponit_kvalitet_eier")
        m.execute(
            "INSERT INTO kvalitetsfunn (tenant, regel_id, funntype,"
            " forst_sett_kjoring, sist_sett_kjoring) VALUES (%s,%s,%s,%s,%s)",
            (PLATTFORM, regel, funntype, kjoring, kjoring))
        m.execute("RESET ROLE")
        m.commit()

    _sett_kontekst(m, PLATTFORM)
    m.execute("SET LOCAL ROLE disponit_kvalitet_eier")
    with pytest.raises(psycopg.errors.CheckViolation):
        m.execute(
            "INSERT INTO kvalitetsfunn (tenant, regel_id, funntype,"
            " forst_sett_kjoring, sist_sett_kjoring) VALUES (%s,%s,%s,%s,%s)",
            (PLATTFORM, regel, "kritisk_nok_til_a_blokkere", kjoring, kjoring))
    m.rollback()


# ---------------------------------------------------------------------------
# Invariant 5: profil_endret_etter_innsetting
# ---------------------------------------------------------------------------

@pg
def test_inv5_profil_og_kjoring_er_append_only_ogsaa_for_eieren(ren):
    """Direkte UPDATE og DELETE avvises — SOM EIEREN av tabellene. Det
    er hele poenget: en vakt som bare gjelder de rettighetsløse er ingen
    vakt (011/053/056-doktrinen).

    MUTASJONEN SOM DREPER DENNE: gjør vaktene til INSERT-only-triggere,
    eller la dem slippe eieren gjennom.
    """
    import psycopg
    m = ren
    _kontakt(m, TENANT)
    km = _km()
    try:
        _profiler(km)
    finally:
        km.close()
    kjoring = _eiersporring(
        m, "SELECT kjoring_id FROM kvalitetskjoring LIMIT 1")[0][0]
    _sett_kontekst(m, TENANT)
    m.execute("SET LOCAL ROLE disponit_kvalitet_eier")
    for sql, args in (
            ("UPDATE kvalitetsprofil SET rader_avvik = 0", ()),
            ("DELETE FROM kvalitetsprofil", ()),
            ("UPDATE kvalitetskjoring SET avbrutt = false WHERE kjoring_id=%s",
             (kjoring,)),
            ("DELETE FROM kvalitetskjoring WHERE kjoring_id=%s", (kjoring,))):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            m.execute(sql, args)
        m.rollback()
        _sett_kontekst(m, TENANT)
        m.execute("SET LOCAL ROLE disponit_kvalitet_eier")
    m.execute("RESET ROLE")
    m.rollback()


@pg
def test_inv5b_funn_kan_oppdateres_men_aldri_forfalskes(ren):
    """Funn er LEVENDE, ikke evidens — `sist_sett_kjoring` oppdateres.
    Men identiteten er frosset, DELETE er avvist, og et funn kan ikke
    gjøres yngre eller sjeldnere enn det er."""
    import psycopg
    m = ren
    _bid = _bruker(m, TENANT)
    _kontakt(m, TENANT, rolle="a", prioritet=1, bruker=_bid)
    _kontakt(m, TENANT, rolle="b", prioritet=2, bruker=_bid)   # duplikat
    km = _km()
    try:
        _profiler(km)
    finally:
        km.close()
    funn = _funn(m, TENANT)
    assert funn, "duplikatregelen ga ikke funn"
    regel = funn[0][1]

    _sett_kontekst(m, TENANT)
    m.execute("SET LOCAL ROLE disponit_kvalitet_eier")
    # Den LOVLIGE oppdateringen (forsøket).
    m.execute("UPDATE kvalitetsfunn SET ganger_sett = ganger_sett + 1,"
              " sist_sett_ts = now() WHERE regel_id=%s", (regel,))
    m.commit()
    for sql in (
            "UPDATE kvalitetsfunn SET regel_id='beredskap.rolle.ikke_tom'",
            "UPDATE kvalitetsfunn SET forst_sett_ts = now()",
            "UPDATE kvalitetsfunn SET ganger_sett = 1",
            "DELETE FROM kvalitetsfunn"):
        _sett_kontekst(m, TENANT)
        m.execute("SET LOCAL ROLE disponit_kvalitet_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            m.execute(sql)
        m.rollback()


# ---------------------------------------------------------------------------
# Invariant 6: tenantlekkasje_i_profil
# ---------------------------------------------------------------------------

@pg
def test_inv6_tenant_a_ser_aldri_tenant_bs_profiltall(ren):
    """Direkte DML: RLS er vakten, ikke et WHERE noen må huske.

    MUTASJONEN SOM DREPER DENNE: fjern `tenant_isolasjon` fra
    `kvalitetsprofil`, eller la profileren skrive uten å binde
    konteksten til radens egen tenant.
    """
    m = ren
    a = _bruker(m, TENANT)
    _kontakt(m, TENANT, rolle="a", prioritet=1, bruker=a)
    _kontakt(m, TENANT, rolle="b", prioritet=2, bruker=a)
    _kontakt(m, ANNEN_TENANT, rolle="a", prioritet=1)
    km = _km()
    try:
        _profiler(km)
    finally:
        km.close()
    # Begge tenanter har tall (forsøket) ...
    alle = {r[0] for r in _profilrader(m)}
    assert {TENANT, ANNEN_TENANT} <= alle, f"fikk tenanter {alle}"

    # ... og ingen av dem ser den andres (bruddet).
    for jeg, ikke in ((TENANT, ANNEN_TENANT), (ANNEN_TENANT, TENANT)):
        _sett_kontekst(m, jeg)
        m.execute("SET LOCAL ROLE disponit_kvalitet_eier")
        synlige = {r[0] for r in m.execute(
            "SELECT DISTINCT tenant FROM kvalitetsprofil").fetchall()}
        m.execute("RESET ROLE")
        m.rollback()
        assert synlige <= {jeg}, \
            f"{jeg} så {synlige} — {ikke} lekket gjennom RLS"


@pg
def test_inv6b_apiet_gir_aldri_en_annen_tenants_tall(ren, klient):
    """Over API-et: `krev_tenantkontekst` binder tenanten til øktens
    kontekst, og RLS gir bare den ene tenantens rader. En kunde med
    `security:read` ser sine egne tall og INGEN funnliste på tvers."""
    m = ren
    a = _bruker(m, TENANT)
    _kontakt(m, TENANT, rolle="a", prioritet=1, bruker=a)
    _kontakt(m, TENANT, rolle="b", prioritet=2, bruker=a)
    _kontakt(m, ANNEN_TENANT, rolle="a", prioritet=1,
             bekreftet_av="bid_finnes_ikke_i_det_hele_tatt")
    km = _km()
    try:
        _profiler(km)
    finally:
        km.close()

    tok, _ = _lag_token(m, TENANT, "bruker", ["security:read"])
    r = klient.get("/v1/datakvalitet",
                   headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    d = r.json()
    tekst = json.dumps(d)
    assert ANNEN_TENANT not in tekst, \
        "en annen tenants navn lekket ut i svaret"
    assert d["plattformdrift"] is False
    assert "tverrgaaende_funn" not in d, (
        "funnlisten på tvers ble sendt til en økt uten platform:admin")
    assert d["regler"], "registeret manglet i svaret"
    assert d["kjoringer"], "kjøringen manglet i svaret"


@pg
def test_inv6c_platform_admin_ser_tverrgaaende_men_scopet_er_ikke_lesescope(
        ren, klient):
    """`platform:admin` UTVIDER svaret (utrullings-presedensen) — og
    scopet står IKKE i `LESESCOPES`, så en browsersesjon når det aldri.
    Begge halvdelene måles her: utvidelsen VIRKER, og porten står."""
    from api.app import LESESCOPES, RUTESCOPE
    assert "platform:admin" not in LESESCOPES, (
        "platform:admin er blitt et lesescope — da kan en browserøkt nå"
        " den tverrgående funnlisten")
    assert RUTESCOPE[("GET", "/v1/datakvalitet")] == "security:read"

    m = ren
    _kontakt(m, ANNEN_TENANT, rolle="a", prioritet=1,
             bekreftet_av="bid_finnes_ikke_i_det_hele_tatt")
    km = _km()
    try:
        _profiler(km)
    finally:
        km.close()
    assert _funn(m, ANNEN_TENANT), "riggen ga ingen funn å se på tvers"

    tok, _ = _lag_token(m, TENANT, "ops",
                        ["security:read", "platform:admin"])
    r = klient.get("/v1/datakvalitet",
                   headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["plattformdrift"] is True
    assert "tverrgaaende_funn" in d
    assert any(f["tenant"] == ANNEN_TENANT for f in d["tverrgaaende_funn"]), \
        "plattformdriften så ikke den andre tenantens funn"


# ---------------------------------------------------------------------------
# Invariant 7: bestilling_blokkert_av_kvalitetsmaaling — V1-DOMMEN
# ---------------------------------------------------------------------------

#: Modulens egne filer. Den statiske porten leser NØYAKTIG disse — en
#: liste som stille ble tom ville sagt grønt om ingenting.
M3_FILER = (
    ROT / "platform" / "core" / "db" / "migrations" / "092_m3_datakvalitet.sql",
    ROT / "platform" / "core" / "api" / "datakvalitet.py",
    ROT / "platform" / "drift" / "kvalitetsprofilering.py",
    ROT / "platform" / "drift" / "kjor_kvalitetsprofilering.py",
    ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
        / "datakvalitet.js",
)


def test_inv7_modulen_rorer_aldri_bestillingsveien_statisk():
    """V1-DOMMEN, HÅNDHEVET SOM PORT: M-3 skal ikke kunne blokkere en
    bestilling, og det skal ikke hvile på at ingen skriver koden.

    To målinger: modulen IMPORTERER ingenting fra bestillingsveien, og
    den har ingen INSERT/UPDATE/DELETE mot `bestilling*`, `oppdrag`,
    `policyer` eller `unntak`. Kommentarer teller ikke — de nevner
    nettopp disse ordene for å forklare hvorfor de ikke brukes.

    MUTASJONEN SOM DREPER DENNE: la profileren skrive en rad i `unntak`
    når en terskel overskrides. Det ville vært «bare et varsel» i
    kildeteksten og en ny endringsvei i basen.
    """
    assert all(f.exists() for f in M3_FILER), \
        f"modulfil mangler: {[str(f) for f in M3_FILER if not f.exists()]}"
    forbudte = ("bestilling", "oppdrag", "policyer", "unntak")
    verb = ("insert into", "update", "delete from")
    for fil in M3_FILER:
        for nr, raa in enumerate(fil.read_text(encoding="utf-8").splitlines(),
                                 1):
            linje = raa.strip()
            if linje.startswith("--") or linje.startswith("#") \
                    or linje.startswith("//") or linje.startswith("*"):
                continue
            lav = linje.lower()
            for tabell in forbudte:
                for v in verb:
                    assert f"{v} {tabell}" not in lav \
                        and f"{v} public.{tabell}" not in lav, \
                        f"{fil.name}:{nr} skriver mot {tabell}: {linje!r}"
            # Ingen import fra bestillingsveien i Python-filene.
            if fil.suffix == ".py":
                assert not re.match(
                    r"^(from|import)\s+.*\b(bestilling|policyregister|"
                    r"unntaksbehandling|plan)\b", linje), \
                    f"{fil.name}:{nr} importerer fra bestillingsveien"


def test_inv7b_maalerollen_har_ingen_bestillingsfunksjon_i_kjoreren():
    """Kjøreren er eneste rettighetskilde. Målerollen får NØYAKTIG én
    EXECUTE der, og ingen av bestillingsveiens dører."""
    kjorer = (ROT / "deploy" / "staging" / "migrer.py").read_text(
        encoding="utf-8")
    blokk = kjorer.split("KVALITETSMAALER_RETTIGHETER = \"\"\"")[1] \
                  .split('"""')[0]
    execs = re.findall(r"GRANT EXECUTE ON FUNCTION (\w+)", blokk)
    assert execs == ["m3_profiler"], \
        f"målerollen fikk mer enn profileringsdøren: {execs}"
    assert "GRANT SELECT" not in blokk and "GRANT INSERT" not in blokk


@pg
def test_inv7c_bestilling_gaar_uendret_gjennom_med_rodt_kvalitetsfunn(ren):
    """FUNKSJONELL REGRESJON, ikke bare en statisk lesning: med et rødt
    kvalitetsfunn liggende i basen går bestillingsveien nøyaktig som
    før. Dette er v1-dommen målt der den betyr noe.

    MUTASJONEN SOM DREPER DENNE: enhver kobling fra kvalitetsfunn inn i
    beslutnings- eller bestillingsveien.
    """
    m = ren
    a = _bruker(m, TENANT)
    _kontakt(m, TENANT, rolle="a", prioritet=1, bruker=a)
    _kontakt(m, TENANT, rolle="b", prioritet=2, bruker=a)   # duplikat → funn
    km = _km()
    try:
        _profiler(km)
    finally:
        km.close()
    funn = _funn(m, TENANT)
    assert any(f[2] == "terskel_overskredet" for f in funn), \
        "riggen ga ikke et rødt funn å måle bestillingen mot"

    # BESTILLINGSVEIENS EGNE TABELLER ER URØRT. Tallene tas FØR og
    # ETTER en ny profilering: en profiler som la igjen så mye som én rad
    # i `oppdrag`, `unntak`, `revisjonslogg` eller `policyer` ville felle
    # porten uten at noen måtte gjette hvilken kolonne den skrev i.
    _sett_kontekst(m, TENANT)
    tabeller = ("oppdrag", "unntak", "revisjonslogg", "policyer",
                "bestilling_idempotens")
    for_ = {t: m.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in tabeller}
    m.rollback()
    km = _km()
    try:
        _profiler(km)
    finally:
        km.close()
    _sett_kontekst(m, TENANT)
    etter = {t: m.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
             for t in tabeller}
    m.rollback()
    assert etter == for_, (
        f"profileringen endret bestillingsveien: {for_} -> {etter}")

    # Og veien som BESTILLER kan ikke engang SE kvalitetsfunnet:
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        # Runtime har ingen SELECT på M-3s lagre i det hele tatt (SP-7),
        # så kvalitetsfunnet er ikke engang SYNLIG for veien som
        # bestiller — den kan ikke blokkere på noe den ikke ser.
        import psycopg
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT * FROM kvalitetsfunn")
        rt.rollback()
    finally:
        rt.close()


# ---------------------------------------------------------------------------
# Driftsformen: hoppet_over, alarm, idempotens
# ---------------------------------------------------------------------------

@pg
def test_drift_overlappende_kjoring_hopper_over_med_feiltelleren_urort(ren):
    """En kjøring som fant arbeidernøkkelen opptatt har verken lyktes
    eller feilet. Skillet må stå PÅ resultatet, ellers sletter en
    overlappende aktivering en alt opptelt feil og rapporterer suksess
    uten å ha målt noe."""
    from drift import kvalitetsprofilering as kp
    holder = _km()
    annen = _km()
    try:
        holder.execute("SELECT pg_advisory_lock(%s)", (kp.ARBEIDERNOKKEL,))
        holder.commit()
        res = kp.kjor(annen, tidligere_feil=1)
        assert res.hoppet_over is True
        assert res.feilet is False
        assert res.alarm_utlost is False
        assert res.kjoring_id is None
    finally:
        try:
            holder.execute("SELECT pg_advisory_unlock(%s)",
                           (kp.ARBEIDERNOKKEL,))
            holder.commit()
        except Exception:
            pass
        holder.close()
        annen.close()
    # Ingen kjøring ble registrert — den gjorde ingenting.
    assert _eiersporring(
        ren, "SELECT count(*) FROM kvalitetskjoring")[0][0] == 0


@pg
@pytest.mark.skipif(not KVALITETSMAALER_DSN,
                    reason="DISPONIT_TEST_KVALITETSMAALER_DSN ikke satt")
def test_drift_kjor_gjor_en_ekte_runde_med_sine_egne_rammer(ren):
    """Jobbens EGEN kodevei, kjørt mot basen — ikke bare hjelperen
    testene ellers bruker. Uten denne porten var `kjor()` bare målt på
    `hoppet_over`-grenen, og en syntaksfeil i rammesettingen (`SET LOCAL
    ... = %s` tar ikke parametre i den utvidede protokollen) ville ikke
    dukket opp før første ekte kjøring på verten.
    """
    from drift import kvalitetsprofilering as kp
    m = ren
    _kontakt(m, TENANT)
    km = _km()
    try:
        res = kp.kjor(km, tidligere_feil=0)
    finally:
        km.close()
    assert res.feilet is False and res.hoppet_over is False
    assert res.kjoring_id is not None
    assert res.antall_regler >= 7
    assert res.avbrutt is False
    assert _eiersporring(
        m, "SELECT count(*) FROM kvalitetskjoring")[0][0] == 1


@pg
@pytest.mark.skipif(not KVALITETSMAALER_DSN,
                    reason="DISPONIT_TEST_KVALITETSMAALER_DSN ikke satt")
def test_drift_main_skriver_en_json_linje_og_nullstiller_feiltelleren(
        ren, tmp_path, monkeypatch):
    """SYSTEMD SITT INNGANGSPUNKT, kjørt mot basen. Alarmporten under
    måler feilveien; denne måler at den GRØNNE veien finnes: én
    JSON-linje med rundens tall, exit 0, og en feilteller som settes til
    null etter en vellykket kjøring.

    `avbrutt` står i linjen fordi den er rundens egen dom over seg selv
    — en runde som ikke rakk gjennom registeret er ikke en grønn runde
    med få regler, og driftsloggen skal kunne si forskjellen.
    """
    from drift import kjor_kvalitetsprofilering as kk
    tilstand = tmp_path / "kvalitet.json"
    tilstand.write_text(json.dumps({"feil": 1}), encoding="utf-8")
    monkeypatch.setenv("DISPONIT_KVALITETSTILSTAND", str(tilstand))
    monkeypatch.setenv("DISPONIT_KVALITETSMAALER_URL", KVALITETSMAALER_DSN)
    linjer = []
    monkeypatch.setattr("builtins.print",
                        lambda *a, **k: linjer.append(a[0])
                        if k.get("file") is None else None)
    assert kk.main() == 0
    d = json.loads(linjer[-1])
    assert d["hendelse"] == "kvalitetsprofilering"
    assert d["feilet"] == 0 and d["hoppet_over"] == 0
    assert d["antall_regler"] >= 7
    assert d["avbrutt"] == 0
    assert d["kjoring_id"]
    # En vellykket kjøring NULLSTILLER telleren — ellers ville to feil
    # med en suksess imellom utløst alarmen «to sammenhengende».
    assert d["sammenhengende_feil"] == 0 and d["alarm"] == 0
    assert json.loads(tilstand.read_text(encoding="utf-8"))["feil"] == 0


def test_drift_to_sammenhengende_feil_utloser_alarm(tmp_path, monkeypatch):
    """Alarmen etter to feilede kjøringer, målt gjennom `main()` og
    tilstandsfilen — ikke gjennom en etterligning av den. Formen er
    artefaktryddingens, og porten er den samme."""
    from drift import kjor_kvalitetsprofilering as kk

    tilstand = tmp_path / "kvalitet.json"
    monkeypatch.setenv("DISPONIT_KVALITETSTILSTAND", str(tilstand))
    monkeypatch.setenv("DISPONIT_KVALITETSMAALER_URL", "postgresql://ugyldig")
    monkeypatch.setattr(kk, "_koble",
                        lambda dsn: (_ for _ in ()).throw(RuntimeError("nede")))

    linjer = []
    monkeypatch.setattr("builtins.print",
                        lambda *a, **k: linjer.append(a[0])
                        if k.get("file") is None else None)
    assert kk.main() == 1
    d1 = json.loads(linjer[-1])
    assert d1["feilet"] == 1 and d1["sammenhengende_feil"] == 1
    assert d1["alarm"] == 0, "alarm etter ÉN feil er en falsk alarm"

    assert kk.main() == 1
    d2 = json.loads(linjer[-1])
    assert d2["sammenhengende_feil"] == 2
    assert d2["alarm"] == 1, "to sammenhengende feil utløste ikke alarm"
    assert json.loads(tilstand.read_text(encoding="utf-8"))["feil"] == 2


def test_drift_manglende_dsn_nekter_oppstart_og_faller_aldri_til_runtime(
        monkeypatch):
    """Ingen fallback til runtime-DSN-en: `migrer.py` REVOKEr
    `m3_profiler` fra runtime, så en fallback ville startet jobben rett i
    «permission denied» — og en jobb som feiler på feil grunn er verre
    enn en som ikke starter."""
    from drift import kjor_kvalitetsprofilering as kk
    monkeypatch.delenv("DISPONIT_KVALITETSMAALER_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://runtime")
    monkeypatch.setenv("DISPONIT_DATABASE_URL", "postgresql://runtime")
    assert kk.main() == 2
    kilde = (DRIFTROT / "kjor_kvalitetsprofilering.py").read_text(
        encoding="utf-8")
    assert "DATABASE_URL" not in kilde.replace(
        "DISPONIT_KVALITETSMAALER_URL", ""), \
        "jobben har fått en fallback-DSN"


@pg
def test_drift_idempotens_to_kjoringer_gir_ingen_nye_funnrader(ren):
    """To kjøringer på uendret base gir TO kjøringer og INGEN nye
    funnrader: funnet oppdateres med `sist_sett_kjoring`, og funnlisten
    vokser ikke med kadensen."""
    m = ren
    a = _bruker(m, TENANT)
    _kontakt(m, TENANT, rolle="a", prioritet=1, bruker=a)
    _kontakt(m, TENANT, rolle="b", prioritet=2, bruker=a)
    km = _km()
    try:
        f1 = _profiler(km)
        funn1 = _funn(m)
        f2 = _profiler(km)
        funn2 = _funn(m)
    finally:
        km.close()

    assert f1["kjoring_id"] != f2["kjoring_id"]
    assert _eiersporring(
        m, "SELECT count(*) FROM kvalitetskjoring")[0][0] == 2
    assert f1["antall_funn"] >= 1, "riggen ga ingen funn å måle på"
    assert f2["antall_funn"] == 0, (
        "andre kjøring rapporterte NYE funn på en uendret base")
    assert len(funn2) == len(funn1), "funnlisten vokste med kadensen"
    ganger = {(f[0], f[1], f[2]): f[3] for f in funn2}
    for f in funn1:
        assert ganger[(f[0], f[1], f[2])] == f[3] + 1, \
            "funnet ble ikke oppdatert med et nytt gjensyn"


@pg
def test_drift_batchgrense_gjor_runden_avbrutt_ikke_stille_forkortet(ren):
    """En runde som ikke rakk gjennom registeret er AVBRUTT — ikke en
    grønn runde med få regler. Uten `avbrutt` ville en for lav grense
    sett ut som en base uten problemer."""
    km = _km()
    try:
        hel = _profiler(km)
        assert hel["avbrutt"] is False
        del1 = _profiler(km, grense=1)
    finally:
        km.close()
    assert del1["antall_regler"] == 1
    assert del1["avbrutt"] is True, \
        "en runde som stoppet på batchgrensen sa ikke fra"


# ---------------------------------------------------------------------------
# SP-10 og fasit-pinningen
# ---------------------------------------------------------------------------

@pg
def test_sp10_migrasjonen_er_kjort_og_bytebundet(migrator):
    """Den tomme kjøringen er målt direkte: 092 står i `migrasjoner` med
    checksum lik sha256 av filbytene i treet — samme byte-binding
    fasiten pinner mot main."""
    import hashlib
    cs = migrator.execute(
        "SELECT checksum FROM migrasjoner WHERE versjon=92").fetchone()
    migrator.rollback()
    assert cs is not None, "092 er ikke kjørt i testbasen"
    fil_sha = hashlib.sha256(MIGRASJON.read_bytes()).hexdigest()
    assert cs[0] == fil_sha, \
        "092 i treet er ikke bytene basen kjørte — historikk er immutable"
    fasit = json.loads(
        (ROT / "platform" / "core" / "db" / "migrasjons-fasit.json")
        .read_text(encoding="utf-8"))
    assert fasit.get("092_m3_datakvalitet.sql") == fil_sha, \
        "fasiten pinner andre bytes enn treet bærer"


def test_sp10b_migrasjonen_er_ren_ddl_bortsett_fra_sitt_eget_register():
    """047-klassen: masse-DML i en migrasjon kan køe utsatte
    triggerhendelser som ALTER-setninger nekter å passere. 092 har ETT
    navngitt unntak — seedet av sitt EGET, nyopprettede register — og
    porten måler premisset.

    Det er også grunnen til at «begge kjøringer» (tom base / bebodd
    base) er den SAMME kjøringen for 092: den rører ikke en eneste
    eksisterende rad.
    """
    import pglast
    sql = MIGRASJON.read_text(encoding="utf-8")
    dml = []
    for raa in pglast.parse_sql(sql):
        navn = type(raa.stmt).__name__
        if navn == "InsertStmt" and raa.stmt.relation.relname == \
                "kvalitetsregel":
            continue
        if navn in ("InsertStmt", "UpdateStmt", "DeleteStmt"):
            dml.append(navn)
    assert not dml, (
        f"092 bærer toppnivå-DML {dml} — da er den en backfill og skal"
        " registrere seed+måling i SP-10 (sp10-provekjoring.py)")


def test_sp10c_092_navngir_aldri_runtime_rollen():
    """056/057-formen: `disponit` er lokalnavnet, og `migrer.py` er
    eneste rettighetskilde. En GRANT til runtime i migrasjonen ville
    lagt rettighetsmodellen to steder."""
    for linje in MIGRASJON.read_text(encoding="utf-8").splitlines():
        if linje.lstrip().startswith("--"):
            continue
        assert "TO disponit;" not in linje, \
            f"092 grantar direkte til runtime-rollen: {linje!r}"


@pg
def test_alle_dorene_eies_av_kvalitetseieren_og_star_i_reparasjonen(migrator):
    """SECURITY DEFINER-dører som IKKE eies av `disponit_kvalitet_eier`
    ville kjørt som migrator — altså med eierens rettigheter på ALT,
    forbi hele kolonnegrant-modellen. Eierskapet står også i
    `eierskap-reparasjon.sql`; her måles basen."""
    rader = dict(migrator.execute(
        "SELECT p.proname, r.rolname FROM pg_proc p"
        " JOIN pg_roles r ON r.oid = p.proowner"
        " WHERE p.proname LIKE 'm3\\_%' AND p.prosecdef").fetchall())
    migrator.rollback()
    assert set(rader) == {
        "m3_profiler", "m3_reis_funn", "m3_regelregister",
        "m3_kvalitetsprofil", "m3_kvalitetsfunn",
        "m3_kvalitetsfunn_tverrgaaende"}
    assert set(rader.values()) == {"disponit_kvalitet_eier"}

    tabeller = dict(migrator.execute(
        "SELECT c.relname, pg_get_userbyid(c.relowner) FROM pg_class c"
        " JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname='public' AND c.relname LIKE 'kvalitets%'"
        " AND c.relkind='r'").fetchall())
    migrator.rollback()
    assert set(tabeller) == {"kvalitetsregel", "kvalitetskjoring",
                             "kvalitetsprofil", "kvalitetsfunn"}
    assert set(tabeller.values()) == {"disponit_kvalitet_eier"}

    eierskap = (ROT / "deploy" / "staging" / "eierskap-reparasjon.sql") \
        .read_text(encoding="utf-8")
    for dor in rader:
        assert f"'{dor}(" in eierskap, \
            f"{dor} mangler i eierskap-reparasjon.sql"
    for tabell in tabeller:
        assert f"'{tabell}'" in eierskap, \
            f"{tabell} mangler i eierskap-reparasjon.sql"


@pg
def test_rls_star_paa_alle_tenant_lagrene(migrator):
    """ENABLE + FORCE + `tenant_isolasjon` på hver tenant-tabell, og
    ingen `BYPASSRLS` noe sted i modulen. `kvalitetskjoring` og
    `kvalitetsregel` er PLATTFORMSKOP og står derfor med vilje uten —
    de har ingen tenant-kolonne å isolere på."""
    for tabell in ("kvalitetsprofil", "kvalitetsfunn"):
        rls, force = migrator.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
            " WHERE oid = %s::regclass", (tabell,)).fetchone()
        assert rls and force, f"{tabell} mangler ENABLE/FORCE RLS"
        polnavn = {r[0] for r in migrator.execute(
            "SELECT polname FROM pg_policy WHERE polrelid=%s::regclass",
            (tabell,)).fetchall()}
        assert "tenant_isolasjon" in polnavn, f"{tabell}: {polnavn}"
    migrator.rollback()
    for rolle in ("disponit_kvalitet_eier", "disponit_kvalitetsmaaler"):
        assert migrator.execute(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname=%s",
            (rolle,)).fetchone()[0] is False, f"{rolle} har BYPASSRLS"
    migrator.rollback()


@pg
def test_profilerens_krysstenantpolicy_er_kun_lesing(migrator):
    """Kryss-tenant er en EKSPLISITT policy per tabell (m6/m57-formen),
    og HER er den strengere enn forbildene: `FOR SELECT`. En profiler som
    kunne skrive i tabellene den måler ville vært den endringsveien v1
    er en dom mot."""
    rader = migrator.execute(
        "SELECT c.relname, p.polcmd, pg_get_expr(p.polqual, p.polrelid)"
        "  FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid"
        " WHERE p.polname = 'm3_profilering' ORDER BY c.relname").fetchall()
    migrator.rollback()
    assert rader, "profileren har ingen kryss-tenant-policy — den ville"\
                  " vært blind og rapportert null over null rader"
    for relname, polcmd, _ in rader:
        assert polcmd == "r", \
            f"{relname}: m3_profilering er ikke FOR SELECT (polcmd={polcmd})"


# ---------------------------------------------------------------------------
# Manifest, ruter og kravgrensen
# ---------------------------------------------------------------------------

def test_manifestet_er_gyldig_og_aerlig():
    """Manifestet validerer, sier `under_utvikling`/`ikke_i_drift`, og
    ingen sjekklistepunkter er flippet uten måling."""
    import yaml
    import sys
    sys.path.insert(0, str(ROT / "platform" / "core"))
    from manifestskjema import valider_manifest
    manifest = yaml.safe_load(
        (MODULROT / "manifest.yaml").read_text(encoding="utf-8"))
    assert valider_manifest(manifest) == []
    assert manifest["status"] == "under_utvikling"
    assert manifest["driftstilstand"] == "ikke_i_drift"
    # v1 bestiller ikke gjennom policyporten og skriver ingen beslutning
    # til evidenskjeden — en oppført m01/m02 ville vært LÅNT, ikke målt.
    assert manifest["avhengigheter"] == []
    for punkt, verdi in manifest["staging_sjekkliste"].items():
        assert verdi["status"] == "nei", (
            f"{punkt} er flippet uten en måling som bærer den —"
            " et manifest skal aldri lese sterkere enn porten måler")


def test_ruten_og_scopet_er_deklarert_sammen():
    """`test_pr008.py` binder RUTESCOPE toveis til `Route()` i kilden.
    Porten her er den samme sannheten sett fra modulens side: stien står
    i ÉN literal, og linjen har ingen kommentar etter seg."""
    kilde = (ROT / "platform" / "core" / "api" / "app.py").read_text(
        encoding="utf-8")
    assert 'Route("/v1/datakvalitet", datakvalitet, methods=["GET"])' in kilde
    linjer = [l for l in kilde.splitlines()
              if '("GET",  "/v1/datakvalitet")' in l]
    assert len(linjer) == 1, f"RUTESCOPE-linjen står {len(linjer)} ganger"
    assert linjer[0].rstrip().endswith('"security:read",'), \
        f"RUTESCOPE-linjen har en hale test_pr008 ikke kan parse: {linjer[0]!r}"


def test_kravgrensen_krever_at_hver_invariant_er_forsokt():
    """`m3-v1` måler hver invariant som (forsøk, brudd). Rapporten under
    er den denne suiten faktisk kan skrive; porten er at grensen godtar
    den, OG at den blir RØD hvis en eneste invariant mangler forsøk."""
    import sys
    sys.path.insert(0, str(ROT / "platform" / "core"))
    from manifestskjema import M3_INVARIANTER, _sjekk_grenser

    maalt = {"ddl_begge_kjoringer_gronne": True}
    for navn in M3_INVARIANTER:
        maalt[f"{navn}_forsok"] = 1
        maalt[f"{navn}_brudd"] = 0
    rapport = {"krav_id": "m3-v1", "bestatt": True, "maalt": maalt}
    assert _sjekk_grenser("m3-v1", rapport) == []

    # RØD UTEN FIKSEN, tre veier.
    for endre, forventet in (
            (lambda m: m.update(
                {f"{M3_INVARIANTER[0]}_forsok": 0}), "_forsok=0"),
            (lambda m: m.update(
                {f"{M3_INVARIANTER[0]}_brudd": 1}), "_brudd=1"),
            (lambda m: m.update(
                {"ddl_begge_kjoringer_gronne": False}), "ddl")):
        m2 = dict(maalt)
        endre(m2)
        feil = _sjekk_grenser("m3-v1", {"krav_id": "m3-v1", "bestatt": True,
                                        "maalt": m2})
        assert feil, f"grensen godtok en rapport den ikke skulle ({forventet})"


def test_axe_porten_finnes_og_kjores_av_npm_test():
    """`ui_axe_alvorlige_brudd` måles i jsdom + axe-core, ikke herfra.
    Porten her er at filen FINNES og faktisk kaller axe — en invariant
    uten en fil å kjøre er en invariant ingen måler."""
    fil = (ROT / "platform" / "core" / "ui" / "test" / "datakvalitet.test.js")
    assert fil.exists(), "axe-porten for flaten mangler"
    kilde = fil.read_text(encoding="utf-8")
    assert kilde.count("alvorligeBrudd") >= 4, \
        "axe kjøres ikke på flatens tilstander"
    assert "visDatakvalitet" in kilde


def test_grensen_dekkes_av_portene_i_denne_fila():
    """§0, MÅLT BEGGE VEIER — OG DET VAR HALVPARTEN SOM MANGLET.

    Grensen `m3-v1` har stått i `KRAVGRENSER` siden FØR koden ble
    skrevet: §0-regelen ble respektert. Portene under har ligget her
    siden. MEN INGENTING BANDT DE TO SAMMEN.

    Konsekvensen er stille: en invariant kunne fjernes fra grensen,
    eller en port slettes, og ingen test ville merket det. Grensen ville
    fremdeles vært «registrert», og suiten fremdeles grønn.

    `test_kravgrenser_unike.py` pinner at en grense ikke OVERSKRIVES.
    Denne pinner at den er DEKKET. De to er ulike hull, og bare det
    første var lukket.

    MUTASJONEN SOM DREPER DENNE: legg til en invariant i `m3-v1` som
    ingen test her nevner.
    """
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m3-v1"]
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    inv = set(g["invarianter"])
    assert inv
    egen = Path(__file__).read_text(encoding="utf-8")
    mangler = sorted(i for i in inv if i not in egen)
    assert mangler == [], f"invarianter uten port: {mangler}"
