"""M-35 v1 (migrasjon 089) — planens §7-porter.

  1. Tidslinjen er APPEND-ONLY: direkte UPDATE og DELETE på
     `kontinuitetshendelse_post` avvises for ENHVER rolle, også eieren
     (011/031-doktrinen — en krisehåndtering som kan redigeres i
     etterkant er ingen evidens).
  2. Lukking uten etteranalyse: døren avviser, OG den halve lukkingen
     er urepresenterbar ved direkte DML (CHECK-en `(lukket_ts IS NULL)
     = (lukket_av IS NULL)`). To porter, samme sannhet.
  3. SP-1: kryss-tenant. Radvakten OG RLS står begge i veien — et kall
     med feil tenantkontekst når ikke frem, og dørens
     `krev_tenantkontekst` slipper det ikke inn i det hele tatt.
  4. SP-2: gjenspill med identisk innhold er et STILLE JA (samme id
     tilbake, ingen ny rad); samme id med annet innhold er en materiell
     konflikt.
  5. Statusfilen (dom 4): fraværende, uparsbar og foreldet gir alle
     RØDT funn og `restore_verifisert: false` — målt gjennom den
     INJISERTE stien, uten rot-rettigheter og uten backupkatalog.
     At filen bare SKRIVES ved suksess måles i
     `test_backupskriptet.py::
     test_statusfilen_skrives_kun_ved_suksess_og_atomisk`.
  6. Et artefakt uten den målte restore-tiden er SKJEMAAVVIST, og
     `m35-v1`-grensen feller null/0 — «aldri grønt uten evidens» er en
     port, ikke en formulering.
  7. Migrasjonen er grønn og byte-bundet i denne basen, og er REN DDL
     bortsett fra `rolle_scope`-seedet (044/088-formen) — så «begge
     kjøringer» (tom + bebodd) hviler ikke på et SP-10-seed den ikke
     har.

Axe-porten for flaten bor i `platform/core/ui/test/kontinuitet.test.js`
(jsdom + axe-core); den kjøres av `npm test`, ikke herfra.

Alle DB-tester konstruerer egen tilstand; ingen delt fixture.
"""
from __future__ import annotations

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
MODULROT = ROT / "platform" / "modules" / "m35_kontinuitet"
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "089_m35_kontinuitet.sql")

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")


# ---------------------------------------------------------------------------
# Riggen
# ---------------------------------------------------------------------------

def _bruker(m, tenant=TENANT):
    _sett_kontekst(m, tenant)
    return m.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://m35.test', %s) RETURNING bruker_id",
        ("s35-" + secrets.token_hex(6),)).fetchone()[0]


def _hendelse(m, tenant=TENANT, *, nokkel="drift.strombrudd",
              alvor="kritisk", hid=None):
    """Én åpen hendelse gjennom døren. Døren skriver 'opprettet'-posten
    i samme transaksjon — en hendelse uten fødselspost finnes ikke."""
    _sett_kontekst(m, tenant)
    m.execute("SET ROLE disponit_m37_claimer")
    ut = m.execute(
        "SELECT m35_opprett_hendelse(%s,%s,%s::jsonb,%s,%s,%s)",
        (tenant, nokkel, "{}", alvor, "test", hid)).fetchone()[0]
    m.execute("RESET ROLE")
    m.commit()
    return ut


def _post(m, hid, posttype, tekst, tenant=TENANT, pid=None):
    _sett_kontekst(m, tenant)
    m.execute("SET ROLE disponit_m37_claimer")
    ut = m.execute("SELECT m35_legg_post(%s,%s,%s,%s,%s,%s)",
                   (tenant, hid, posttype, tekst, "test", pid)).fetchone()[0]
    m.execute("RESET ROLE")
    m.commit()
    return ut


def _tjeneste(m, tenant=TENANT, *, referent_id=None, kritikalitet="kritisk",
              kontaktrolle="driftsvakt", tid=None):
    _sett_kontekst(m, tenant)
    m.execute("SET ROLE disponit_m37_claimer")
    ut = m.execute(
        "SELECT m35_opprett_tjeneste(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant, "modul", referent_id or f"m{secrets.token_hex(4)}",
         kritikalitet, 3600, 86400, "gjenopprett@" + "a" * 64,
         kontaktrolle, "test", tid)).fetchone()[0]
    m.execute("RESET ROLE")
    m.commit()
    return ut


# ---------------------------------------------------------------------------
# Port 1: tidslinjen er append-only for ENHVER rolle
# ---------------------------------------------------------------------------

@pg
def test_port1_tidslinjen_er_append_only_ogsaa_for_eieren(migrator):
    """Direkte UPDATE og DELETE på en tidslinjepost avvises — som
    MIGRATOR, altså eieren av tabellen. Det er hele poenget: en vakt som
    bare gjelder de rettighetsløse er ingen vakt (011/053/056).

    MUTASJONEN SOM DREPER DENNE: gjør `m35_post_vakt` til en
    INSERT-only-trigger, eller la den slippe UPDATE gjennom for eieren.
    """
    hid = _hendelse(migrator)
    pid = _post(migrator, hid, "observasjon", "Strømmen er borte")

    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "UPDATE kontinuitetshendelse_post SET tekst='noe annet'"
            " WHERE tenant=%s AND post_id=%s", (TENANT, pid))
    migrator.rollback()

    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "DELETE FROM kontinuitetshendelse_post"
            " WHERE tenant=%s AND post_id=%s", (TENANT, pid))
    migrator.rollback()

    # …og posten står der uendret etter begge forsøkene.
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT tekst FROM kontinuitetshendelse_post WHERE tenant=%s"
        " AND post_id=%s", (TENANT, pid)).fetchone()
    migrator.rollback()
    assert rad[0] == "Strømmen er borte"


@pg
def test_port1b_fodselsposten_skrives_i_samme_transaksjon(migrator):
    """En hendelse uten fødselspost i tidslinjen finnes ikke — døren
    skriver 'opprettet' selv, og et menneske kan ikke skrive den."""
    hid = _hendelse(migrator)
    _sett_kontekst(migrator, TENANT)
    typer = [r[0] for r in migrator.execute(
        "SELECT posttype FROM kontinuitetshendelse_post WHERE tenant=%s"
        " AND hendelse_id=%s", (TENANT, hid)).fetchall()]
    migrator.rollback()
    assert typer == ["opprettet"]

    # 'opprettet' og 'lukket' er DØRENES egne posttyper.
    for forbudt in ("opprettet", "lukket"):
        _sett_kontekst(migrator, TENANT)
        migrator.execute("SET ROLE disponit_m37_claimer")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            migrator.execute("SELECT m35_legg_post(%s,%s,%s,%s,%s,%s)",
                             (TENANT, hid, forbudt, "juks", "test", None))
        migrator.rollback()


@pg
def test_port1c_lukket_hendelse_tar_ikke_flere_poster(migrator):
    """Tidslinjen er lukket når hendelsen er det — både gjennom døren
    og gjennom en direkte INSERT (vakten backstopper)."""
    hid = _hendelse(migrator)
    _post(migrator, hid, "etteranalyse", "Aggregatet var ikke testet")
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    migrator.execute("SELECT m35_lukk_hendelse(%s,%s,%s,%s)",
                     (TENANT, hid, "test", "Lukket etter gjennomgang"))
    migrator.execute("RESET ROLE")
    migrator.commit()

    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    with pytest.raises(psycopg.errors.NoDataFound):
        migrator.execute("SELECT m35_legg_post(%s,%s,%s,%s,%s,%s)",
                         (TENANT, hid, "tiltak", "for sent", "test", None))
    migrator.rollback()

    # Direkte INSERT som eieren: vakten avviser den samme veien.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "INSERT INTO kontinuitetshendelse_post (tenant, hendelse_id,"
            " post_id, posttype, aktor, tekst) VALUES"
            " (%s,%s,%s,'tiltak','test','snik')",
            (TENANT, hid, uuid.uuid4()))
    migrator.rollback()


# ---------------------------------------------------------------------------
# Port 2: lukking uten etteranalyse — døren OG CHECK-en
# ---------------------------------------------------------------------------

@pg
def test_port2_lukking_uten_etteranalyse_avvises_av_doren(migrator):
    """En krise uten etterlæring lukkes ikke. Døren feller dommen, og
    hendelsen står ÅPEN etterpå — en halvveis lukking er ikke en
    tilstand som kan oppstå.

    MUTASJONEN SOM DREPER DENNE: fjern EXISTS-sjekken i
    `m35_lukk_hendelse`, eller la den godta en hvilken som helst
    posttype.
    """
    hid = _hendelse(migrator)
    # Observasjoner og tiltak er IKKE etteranalyse — nettopp forskjellen
    # porten måler: det holder ikke å ha skrevet noe.
    _post(migrator, hid, "observasjon", "Strømmen er borte")
    _post(migrator, hid, "tiltak", "Startet aggregat")

    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        migrator.execute("SELECT m35_lukk_hendelse(%s,%s,%s,%s)",
                         (TENANT, hid, "test", "prøver å lukke"))
    migrator.rollback()

    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT lukket_ts, lukket_av FROM kontinuitetshendelse"
        " WHERE tenant=%s AND hendelse_id=%s", (TENANT, hid)).fetchone()
    migrator.rollback()
    assert rad == (None, None), "hendelsen ble delvis lukket av et avvist kall"

    # Med etteranalysen på plass GÅR den — og lukkeposten skrives i
    # SAMME transaksjon som flippet.
    _post(migrator, hid, "etteranalyse", "Aggregatet var ikke testet")
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    migrator.execute("SELECT m35_lukk_hendelse(%s,%s,%s,%s)",
                     (TENANT, hid, "test", "Lukket etter gjennomgang"))
    migrator.execute("RESET ROLE")
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    lukket = migrator.execute(
        "SELECT h.lukket_ts IS NOT NULL, h.lukket_av,"
        " (SELECT count(*) FROM kontinuitetshendelse_post p"
        "   WHERE p.tenant=h.tenant AND p.hendelse_id=h.hendelse_id"
        "     AND p.posttype='lukket')"
        " FROM kontinuitetshendelse h WHERE h.tenant=%s"
        " AND h.hendelse_id=%s", (TENANT, hid)).fetchone()
    migrator.rollback()
    assert lukket == (True, "test", 1)


@pg
def test_port2b_halv_lukking_er_urepresenterbar_ved_direkte_dml(migrator):
    """CHECK-en `(lukket_ts IS NULL) = (lukket_av IS NULL)`: en
    lukketid uten lukker (eller omvendt) er en påstand uten avsender, og
    basen nekter å bære den — også når skriveren er eieren og går
    UTENOM døren.

    Merk hvilken feil som kommer: radvakten fyrer FØR CHECK-en på en
    UPDATE som ikke er en lovlig lukkeovergang, så begge portene måles —
    CHECK-en direkte på en INSERT, vakten på UPDATE-veien.
    """
    hid = _hendelse(migrator)
    # INSERT med bare lukket_ts: CHECK-en feller den (vakten slipper
    # INSERT gjennom først når lukkefeltene er tomme, så her er det
    # nettopp CHECK-en som må stå).
    _sett_kontekst(migrator, TENANT)
    with pytest.raises((psycopg.errors.CheckViolation,
                        psycopg.errors.InsufficientPrivilege)):
        migrator.execute(
            "INSERT INTO kontinuitetshendelse (tenant, hendelse_id,"
            " tekstnokkel, alvor, apnet_av, lukket_ts) VALUES"
            " (%s,%s,'x.y','kritisk','test', now())",
            (TENANT, uuid.uuid4()))
    migrator.rollback()

    # UPDATE med bare lukket_av: vakten avviser (og hadde den ikke gjort
    # det, ville CHECK-en tatt den).
    _sett_kontekst(migrator, TENANT)
    with pytest.raises((psycopg.errors.CheckViolation,
                        psycopg.errors.InsufficientPrivilege)):
        migrator.execute(
            "UPDATE kontinuitetshendelse SET lukket_av='test'"
            " WHERE tenant=%s AND hendelse_id=%s", (TENANT, hid))
    migrator.rollback()

    # CHECK-en finnes som NAVNGITT constraint — porten måler formen, så
    # den ikke kan forsvinne i en refaktorering uten at noe sier fra.
    _sett_kontekst(migrator, TENANT)
    def_ = migrator.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conname='hendelse_lukking_komplett'").fetchone()
    migrator.rollback()
    assert def_ is not None, "CHECK-en er borte"
    assert "lukket_ts" in def_[0] and "lukket_av" in def_[0]


# ---------------------------------------------------------------------------
# Port 3: SP-1 — kryss-tenant
# ---------------------------------------------------------------------------

@pg
def test_port3_sp1_kryss_tenant_naar_ikke_frem(migrator):
    """Dørene står bak `krev_tenantkontekst`: et kall med EN tenant i
    konteksten og en ANNEN i parameteren avvises før noe skrives. Og
    lesing på tvers stoppes av RLS — ikke av at API-et lot være å spørre.

    MUTASJONEN SOM DREPER DENNE: dropp `krev_tenantkontekst` fra en dør,
    eller la RLS-policyen stå uten FORCE.
    """
    hid = _hendelse(migrator, TENANT)

    # Kontekst = ANNEN_TENANT, parameter = TENANT → døren nekter.
    _sett_kontekst(migrator, ANNEN_TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    with pytest.raises(psycopg.Error):
        migrator.execute("SELECT m35_legg_post(%s,%s,%s,%s,%s,%s)",
                         (TENANT, hid, "tiltak", "fra naboen", "x", None))
    migrator.rollback()

    # …og naboen ser den ikke engang. RLS er FORCE, så heller ikke
    # eieren av tabellen ser på tvers.
    _sett_kontekst(migrator, ANNEN_TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM kontinuitetshendelse WHERE hendelse_id=%s",
        (hid,)).fetchone()[0]
    migrator.rollback()
    assert n == 0, "en annen tenants hendelse er synlig — RLS holder ikke"


@pg
def test_port3b_alle_fire_tabellene_har_rls_force_og_isolasjon(migrator):
    """Formporten (057/082-formen): RLS ENABLE **og** FORCE på alle
    fire, med en `tenant_isolasjon`-policy som gjelder både USING og
    WITH CHECK. Uten FORCE ville eieren lest på tvers, og uten
    WITH CHECK kunne en rad SKREVET seg inn i en annen tenant."""
    for tabell in ("kontinuitet_tjeneste", "beredskapskontakt",
                   "kontinuitetshendelse", "kontinuitetshendelse_post"):
        rad = migrator.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
            " WHERE relname=%s AND relnamespace='public'::regnamespace",
            (tabell,)).fetchone()
        assert rad == (True, True), f"{tabell} mangler RLS ENABLE+FORCE"
        pol = migrator.execute(
            "SELECT qual, with_check FROM pg_policies WHERE tablename=%s"
            " AND policyname='tenant_isolasjon'", (tabell,)).fetchone()
        assert pol is not None, f"{tabell} mangler tenant_isolasjon"
        assert pol[0] and pol[1], f"{tabell}: policyen mangler en av retningene"
    migrator.rollback()


# ---------------------------------------------------------------------------
# Port 4: SP-2 — gjenspill og materiell konflikt
# ---------------------------------------------------------------------------

@pg
def test_port4_sp2_gjenspill_er_stille_ja_konflikt_er_hoyt_nei(migrator):
    """056-materialitetsformen på alle de opprettende dørene: samme id
    med IDENTISK innhold gir samme id tilbake og INGEN ny rad; samme id
    med ANNET innhold er en materiell konflikt.

    MUTASJONEN SOM DREPER DENNE: bytt innholdssammenlikningen mot et
    rent `ON CONFLICT DO NOTHING` — da blir en endret payload et stille
    ja, og to ulike hendelser deler én rad.
    """
    hid = uuid.uuid4()
    forste = _hendelse(migrator, hid=hid, nokkel="drift.strombrudd")
    igjen = _hendelse(migrator, hid=hid, nokkel="drift.strombrudd")
    assert forste == igjen == hid, "gjenspill ga ikke samme id"

    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM kontinuitetshendelse WHERE tenant=%s"
        " AND hendelse_id=%s", (TENANT, hid)).fetchone()[0]
    poster = migrator.execute(
        "SELECT count(*) FROM kontinuitetshendelse_post WHERE tenant=%s"
        " AND hendelse_id=%s", (TENANT, hid)).fetchone()[0]
    migrator.rollback()
    assert n == 1, "gjenspillet fødte en ny hendelse"
    assert poster == 1, "gjenspillet fødte en ny fødselspost i tidslinjen"

    # Samme id, ANNET innhold → materiell konflikt.
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    with pytest.raises(psycopg.errors.UniqueViolation):
        migrator.execute(
            "SELECT m35_opprett_hendelse(%s,%s,%s::jsonb,%s,%s,%s)",
            (TENANT, "drift.noe.annet", "{}", "kritisk", "test", hid))
    migrator.rollback()

    # Samme regel på tidslinjeposten og på kartinnslaget.
    pid = uuid.uuid4()
    assert _post(migrator, forste, "tiltak", "Startet aggregat", pid=pid) \
        == _post(migrator, forste, "tiltak", "Startet aggregat", pid=pid)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    with pytest.raises(psycopg.errors.UniqueViolation):
        migrator.execute("SELECT m35_legg_post(%s,%s,%s,%s,%s,%s)",
                         (TENANT, forste, "tiltak", "noe helt annet",
                          "test", pid))
    migrator.rollback()

    tid = uuid.uuid4()
    assert _tjeneste(migrator, referent_id="m35_kontinuitet", tid=tid) \
        == _tjeneste(migrator, referent_id="m35_kontinuitet", tid=tid)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    with pytest.raises(psycopg.errors.UniqueViolation):
        migrator.execute(
            "SELECT m35_opprett_tjeneste(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (TENANT, "modul", "m35_kontinuitet", "normal", 3600, 86400,
             "gjenopprett@" + "a" * 64, "driftsvakt", "test", tid))
    migrator.rollback()


@pg
def test_port4b_kontaktens_identitet_er_frosset(migrator):
    """En annen person i rollen er en NY kontakt, aldri en redigering —
    ellers arver den nye personens rad den gamles bekreftelse, og
    dekningsmålingen blir grønn for en person ingen har snakket med.
    Bekreftelsen kan derimot FORNYES; det er hele poenget med den."""
    bid = _bruker(migrator)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    kid = migrator.execute(
        "SELECT m35_opprett_kontakt(%s,%s,%s,%s,%s,%s)",
        (TENANT, "driftsvakt", 1, bid, "test", None)).fetchone()[0]
    migrator.execute("RESET ROLE")
    migrator.commit()

    # Fødes UBEKREFTET: dekningen krever ferskhet, ikke eksistens.
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT bekreftet_ts, bekreftet_av FROM beredskapskontakt"
        " WHERE tenant=%s AND kontakt_id=%s",
        (TENANT, kid)).fetchone() == (None, None)
    migrator.rollback()

    # Identiteten er frosset — også for eieren, også utenom døren.
    annen = _bruker(migrator)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "UPDATE beredskapskontakt SET bruker_id=%s WHERE tenant=%s"
            " AND kontakt_id=%s", (annen, TENANT, kid))
    migrator.rollback()

    # Re-bekreftelse er LOVLIG og er veien til grønt.
    for _ in range(2):
        _sett_kontekst(migrator, TENANT)
        migrator.execute("SET ROLE disponit_m37_claimer")
        migrator.execute("SELECT m35_bekreft_kontakt(%s,%s,%s)",
                         (TENANT, kid, "test"))
        migrator.execute("RESET ROLE")
        migrator.commit()
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT bekreftet_ts IS NOT NULL, bekreftet_av FROM"
        " beredskapskontakt WHERE tenant=%s AND kontakt_id=%s",
        (TENANT, kid)).fetchone()
    migrator.rollback()
    assert rad == (True, "test")

    # …men en FREMTIDSbekreftelse ville holdt dekningen kunstig grønn.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "UPDATE beredskapskontakt SET bekreftet_ts = now()"
            " + interval '1 day' WHERE tenant=%s AND kontakt_id=%s",
            (TENANT, kid))
    migrator.rollback()


# ---------------------------------------------------------------------------
# Port 5: statusfilen (dom 4) — målt gjennom den INJISERTE stien
# ---------------------------------------------------------------------------

def _statusfil(tmp_path, **felter) -> Path:
    data = {"ts": 1_000_000.0, "backup_ts": 1_000_000.0,
            "restore_varighet_s": 12.5, "tabeller": 40,
            "storrelse_b": 12345}
    data.update(felter)
    sti = tmp_path / "siste-verifisering.json"
    sti.write_text(json.dumps(data), encoding="utf-8")
    return sti


def test_port5_statusfil_fravaerende_er_rodt(tmp_path):
    """Ingen fil ⇒ ingen evidens ⇒ RØDT. Ikke «ukjent», ikke «antatt
    grønt»: dom 4 sier at fraværet av evidens er et funn.

    MUTASJONEN SOM DREPER DENNE: la `vurder_statusfil` returnere
    `restore_verifisert: True` når filen mangler, eller la den svelge
    OSError uten å legge igjen et funn.
    """
    from modules.m35_kontinuitet import ovelse
    d = ovelse.vurder_statusfil(tmp_path / "finnes-ikke.json", 1_000_000.0)
    assert d["restore_verifisert"] is False
    assert d["maalt_restoretid_s"] is None
    assert d["maalt_backupalder_s"] is None
    assert [f["tekstnokkel"] for f in d["funn"]] == \
        ["kontinuitet.funn.statusfil_mangler"]
    assert all(f["alvor"] == "rodt" for f in d["funn"])


def test_port5b_statusfil_foreldet_er_rodt(tmp_path):
    """Eldre enn to døgn ⇒ minst én nattlig kjøring har feilet. Filen
    ER lesbar og verifiseringen VAR ekte — men `restore_verifisert` er
    en påstand om NÅ, ikke om en fortid, så den blir false likevel."""
    from modules.m35_kontinuitet import ovelse
    naa = 1_000_000.0
    # Rett innenfor: to døgn på prikken er fortsatt grønt.
    innenfor = ovelse.vurder_statusfil(
        _statusfil(tmp_path, backup_ts=naa - 172_800, ts=naa - 172_800), naa)
    assert innenfor["restore_verifisert"] is True
    assert innenfor["funn"] == []

    # Ett sekund over: rødt, med alderen i funnets parametre.
    utenfor = ovelse.vurder_statusfil(
        _statusfil(tmp_path, backup_ts=naa - 172_801, ts=naa - 172_801), naa)
    assert utenfor["restore_verifisert"] is False
    assert [f["tekstnokkel"] for f in utenfor["funn"]] == \
        ["kontinuitet.funn.statusfil_foreldet"]
    # Målingen står LIKEVEL i rapporten — den er ærlig, bare foreldet.
    assert utenfor["maalt_restoretid_s"] == 12.5
    assert utenfor["maalt_backupalder_s"] == 172_801


@pytest.mark.parametrize("felter", [
    {"restore_varighet_s": 0},          # «0 s» er «aldri målt»
    {"restore_varighet_s": -1},
    {"backup_ts": 2_000_000.0},         # backup fra fremtiden
    {"ts": 2_000_000.0},
    {"restore_varighet_s": True},       # bool er ikke et måletall
])
def test_port5c_statusfil_uleselig_eller_umulig_er_rodt(tmp_path, felter):
    """Fail-closed på hver vei som ikke er «fersk fil, gyldige tall».
    Særlig `0` og `True`: et nulltall ville lest som en måling, og
    `bool` er en subklasse av `int` som ville sluppet gjennom en naiv
    typesjekk."""
    from modules.m35_kontinuitet import ovelse
    d = ovelse.vurder_statusfil(_statusfil(tmp_path, **felter), 1_000_000.0)
    assert d["restore_verifisert"] is False
    assert d["maalt_restoretid_s"] is None
    assert d["funn"] and d["funn"][0]["alvor"] == "rodt"


def test_port5d_soppel_i_filen_er_rodt(tmp_path):
    from modules.m35_kontinuitet import ovelse
    sti = tmp_path / "siste-verifisering.json"
    # Fire ulike vranglåser: ikke JSON, gyldig JSON uten feltene, ikke
    # UTF-8 i det hele tatt, og gyldig JSON der tallet er en streng.
    for innhold in (b"ikke json", b"{}", b"\xff\xfe",
                    b'{"ts": "i fjor", "backup_ts": 1, '
                    b'"restore_varighet_s": 1}'):
        sti.write_bytes(innhold)
        d = ovelse.vurder_statusfil(sti, 1_000_000.0)
        assert d["restore_verifisert"] is False, innhold
        assert d["funn"], innhold


def test_port5e_kart_og_kontaktmaalingene_leser_samme_rad():
    """Ferskheten og dekningen dømmer på SAMME registerrad — to utvalg
    ville kunnet si «ingen kritiske tjenester» og «to udekkede kritiske
    roller» i samme rapport."""
    from modules.m35_kontinuitet import ovelse
    rader = [
        ("t1", "modul", "m01_policy", "kritisk", "driftsvakt"),
        ("t2", "ekstern", "strømleverandør", "kritisk", "kommunikasjon"),
        ("t3", "modul", "finnes_ikke", "kritisk", "driftsvakt"),
        ("t4", "modul", "m02_revisjonslogg", "normal", "ingen"),
    ]
    kart = ovelse.vurder_kart(
        rader, lambda typ, ident: None if typ == "ekstern"
        else ident.startswith("m0"))
    # To verifiserbare kritiske rader (t1, t3), én av dem brutt.
    assert (kart["forsok"], kart["brudd"]) == (2, 1)
    # Den eksterne er GULT, ikke rødt og ikke stille grønt.
    gule = [f for f in kart["funn"] if f["alvor"] == "gult"]
    assert [f["tekstnokkel"] for f in gule] == \
        ["kontinuitet.funn.referent_uverifiserbar"]

    naa = 1_000_000.0
    kontakter = ovelse.vurder_kontakter(
        rader, [("driftsvakt", naa - 10 * 86400),
                ("kommunikasjon", naa - 200 * 86400)], naa)
    # To KRITISKE roller (driftsvakt, kommunikasjon) — den normale
    # tjenestens rolle telles ikke. Kommunikasjon er foreldet.
    assert (kontakter["forsok"], kontakter["brudd"]) == (2, 1)
    assert [f["detalj"]["rolle"] for f in kontakter["funn"]] == \
        ["kommunikasjon"]


def test_port5f_en_udekket_rolle_telles_en_gang():
    """Rollene måles som MENGDE: fem kritiske tjenester som peker på
    samme udekkede rolle er ETT hull, ikke fem. Å telle det fem ganger
    gjør ikke hullet fem ganger større — det gjør bare rapporten
    uleselig."""
    from modules.m35_kontinuitet import ovelse
    rader = [(f"t{i}", "modul", f"m{i}", "kritisk", "driftsvakt")
             for i in range(5)]
    d = ovelse.vurder_kontakter(rader, [], 1_000_000.0)
    assert (d["forsok"], d["brudd"]) == (1, 1)
    assert len(d["funn"]) == 1


# ---------------------------------------------------------------------------
# Port 6: artefaktet og m35-v1-grensen
# ---------------------------------------------------------------------------

def _gront_artefakt() -> dict:
    from manifestskjema import M35_INVARIANTER
    maalt: dict = {}
    for navn in M35_INVARIANTER:
        maalt[f"{navn}_forsok"] = 3
        maalt[f"{navn}_brudd"] = 0
    maalt.update({"restore_verifisert": True,
                  "ddl_begge_kjoringer_gronne": True,
                  "maalt_restoretid_s": 12.5,
                  "maalt_backupalder_s": 3600,
                  "siste_gronne_alder_dogn": 30,
                  "live_helse_ok": True})
    return {"krav_id": "m35-v1", "ts": "2026-08-31T00:00:00+00:00",
            "bestatt": True,
            "oppsett": {"modul": "m35_kontinuitet", "commit": "0" * 40,
                        "vert": "lokal", "tenant": "t-test"},
            "maalt": maalt, "funn": []}


def test_port6_grensen_dekker_planens_punkter():
    """Planen §6 teller 5 invarianter, 2 ja-punkter og 3 målinger.
    Pinnet MOT PLANEN, ikke mot listen selv (m57-grensens form)."""
    from manifestskjema import KRAVGRENSER, M35_INVARIANTER
    g = KRAVGRENSER["m35-v1"]
    assert len(M35_INVARIANTER) == len(set(M35_INVARIANTER)) == 5
    assert g["invarianter"] is M35_INVARIANTER
    assert g["krav_ja"] == ("restore_verifisert", "ddl_begge_kjoringer_gronne")
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    # Tallene fra dommene: 2 døgn backupalder, 92 døgn øvelsesrytme.
    assert g["rpo_maks_backupalder_s"] == 172800
    assert g["ovelse_maks_gronn_alder_dogn"] == 92
    assert g["rto_min_restoretid_s"] == 0
    # Punktbindingen er TOM MED VILJE — uflippbar til målingene finnes.
    assert g["punktbinding"] == {}


def test_port6b_grensen_maaler_parene_ja_punktene_og_maalingene():
    from manifestskjema import M35_INVARIANTER, _sjekk_grenser
    assert _sjekk_grenser("m35-v1", _gront_artefakt()) == []
    for navn in M35_INVARIANTER:
        art = _gront_artefakt()
        art["maalt"][f"{navn}_brudd"] = 1
        assert any(f"{navn}_brudd=1" in f
                   for f in _sjekk_grenser("m35-v1", art)), navn
        art = _gront_artefakt()
        art["maalt"][f"{navn}_forsok"] = 0
        assert any(f"{navn}_forsok=0" in f
                   for f in _sjekk_grenser("m35-v1", art)), navn
    for punkt in ("restore_verifisert", "ddl_begge_kjoringer_gronne"):
        for verdi in (False, None, 1, "ja"):
            art = _gront_artefakt()
            art["maalt"][punkt] = verdi
            assert any(punkt in f for f in _sjekk_grenser("m35-v1", art)), \
                (punkt, verdi)


def test_port6c_aldri_gront_uten_maalt_restoretid():
    """DEN BÆRENDE PORTEN (dom 4/5): en rapport uten et POSITIVT,
    målt restore-tall kan ikke bli grønn. `null` er «ingen evidens»
    (statusfilen manglet eller var foreldet) og `0` er «aldri målt» —
    begge felles.

    MUTASJONEN SOM DREPER DENNE: gjør `rto_min_restoretid_s` til en
    inklusiv grense, eller la `_grenser_m35` hoppe over feltet når det
    er `None`.
    """
    from manifestskjema import _sjekk_grenser
    for verdi in (None, 0, -1):
        art = _gront_artefakt()
        art["maalt"]["maalt_restoretid_s"] = verdi
        feil = _sjekk_grenser("m35-v1", art)
        assert any("maalt_restoretid_s" in f for f in feil), verdi
    # Backupalderen: eldre enn to døgn er rødt, og en alder fra
    # fremtiden er det også.
    for verdi in (172_801, -1, None):
        art = _gront_artefakt()
        art["maalt"]["maalt_backupalder_s"] = verdi
        assert any("maalt_backupalder_s" in f
                   for f in _sjekk_grenser("m35-v1", art)), verdi
    # Rytmen (dom 2): eldre enn kvartalsgulvet er rødt, og «ingen
    # tidligere grønn øvelse» (null) er aldri grønt.
    for verdi in (93, None, -1):
        art = _gront_artefakt()
        art["maalt"]["siste_gronne_alder_dogn"] = verdi
        assert any("siste_gronne_alder_dogn" in f
                   for f in _sjekk_grenser("m35-v1", art)), verdi


def test_port6d_rapport_uten_maalt_restorefelt_er_skjemaavvist():
    """FØR grensen i det hele tatt får se artefaktet: skjemaet krever
    feltet. En rapport som bare UTELATER målingen skal ikke kunne
    smyge seg forbi som «ingen data, ingen innvending»."""
    import jsonschema

    from manifestskjema import ARTEFAKTSKJEMAER
    skjema = json.loads(
        (ROT / "platform" / "core" / ARTEFAKTSKJEMAER["m35-v1"])
        .read_text(encoding="utf-8"))
    jsonschema.validate(_gront_artefakt(), skjema)      # grønn validerer
    for felt in ("maalt_restoretid_s", "maalt_backupalder_s",
                 "siste_gronne_alder_dogn", "restore_verifisert",
                 "ddl_begge_kjoringer_gronne", "live_helse_ok"):
        art = _gront_artefakt()
        del art["maalt"][felt]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(art, skjema)


def test_port6e_bygg_rapport_regner_bestatt_aldri_paastar_den():
    """`bestatt` er en UTREGNING av funnene og bruddene — ikke et felt
    kalleren kan sette. Et rødt funn alene er nok til å felle den."""
    from modules.m35_kontinuitet import ovelse
    felles = dict(tenant="t", commit="0" * 40, vert="v",
                  ts_iso="2026-08-31T00:00:00+00:00",
                  kart={"forsok": 1, "brudd": 0, "funn": []},
                  kontakter={"forsok": 1, "brudd": 0, "funn": []},
                  live_ok=True, tidslinje_forsok=1, tidslinje_brudd=0,
                  lukking_forsok=1, lukking_brudd=0,
                  siste_gronne_alder_dogn=5, ddl_begge_gronne=True,
                  axe_forsok=1, axe_brudd=0)
    gronn = ovelse.bygg_rapport(
        statusfil={"restore_verifisert": True, "maalt_restoretid_s": 12.5,
                   "maalt_backupalder_s": 3600.0, "funn": []}, **felles)
    assert gronn["bestatt"] is True

    # Uverifisert restore feller den, selv uten et eneste rødt funn.
    uten = ovelse.bygg_rapport(
        statusfil={"restore_verifisert": False, "maalt_restoretid_s": None,
                   "maalt_backupalder_s": None, "funn": []}, **felles)
    assert uten["bestatt"] is False

    # /live nede feller den, og legger igjen sitt eget funn.
    nede = ovelse.bygg_rapport(
        statusfil={"restore_verifisert": True, "maalt_restoretid_s": 12.5,
                   "maalt_backupalder_s": 3600.0, "funn": []},
        **{**felles, "live_ok": False})
    assert nede["bestatt"] is False
    assert any(f["tekstnokkel"] == "kontinuitet.funn.live_helse_feilet"
               for f in nede["funn"])

    # Et GULT funn farger derimot ikke dommen — det står der for
    # mennesket som leser rapporten.
    gult = ovelse.bygg_rapport(
        statusfil={"restore_verifisert": True, "maalt_restoretid_s": 12.5,
                   "maalt_backupalder_s": 3600.0, "funn": []},
        **{**felles, "ekstra_funn": [
            {"alvor": "gult", "tekstnokkel": "kontinuitet.funn.x"}]})
    assert gult["bestatt"] is True


def test_port6f_gronn_rapport_validerer_mot_skjemaet():
    """Det `bygg_rapport` produserer er nøyaktig det skjemaet krever —
    ellers ville CLI-et skrevet artefakter ingen port kan lese."""
    import jsonschema

    from manifestskjema import ARTEFAKTSKJEMAER
    from modules.m35_kontinuitet import ovelse
    skjema = json.loads(
        (ROT / "platform" / "core" / ARTEFAKTSKJEMAER["m35-v1"])
        .read_text(encoding="utf-8"))
    rapport = ovelse.bygg_rapport(
        tenant="t", commit="0" * 40, vert="v",
        ts_iso="2026-08-31T00:00:00+00:00",
        statusfil={"restore_verifisert": True, "maalt_restoretid_s": 12.5,
                   "maalt_backupalder_s": 3600.0, "funn": []},
        kart={"forsok": 1, "brudd": 0, "funn": []},
        kontakter={"forsok": 1, "brudd": 0, "funn": []},
        live_ok=True, tidslinje_forsok=1, tidslinje_brudd=0,
        lukking_forsok=1, lukking_brudd=0, siste_gronne_alder_dogn=5,
        ddl_begge_gronne=True, axe_forsok=1, axe_brudd=0)
    jsonschema.validate(rapport, skjema)
    from manifestskjema import _sjekk_grenser
    assert _sjekk_grenser("m35-v1", rapport) == []


# ---------------------------------------------------------------------------
# Port 7: migrasjonen — grønn, byte-bundet, ren DDL
# ---------------------------------------------------------------------------

@pg
def test_port7_migrasjonen_er_kjort_og_bytebundet(migrator):
    """Den tomme kjøringen er målt direkte: 089 står i `migrasjoner`
    med checksum lik sha256 av filbytene i treet — samme byte-binding
    fasiten pinner mot main. AT den kunne kjøres to ganger (bebodd
    kjøring) måles av `test_port7b`s premiss + kjøringen i CI."""
    import hashlib
    cs = migrator.execute(
        "SELECT checksum FROM migrasjoner WHERE versjon=89").fetchone()
    migrator.rollback()
    assert cs is not None, "089 er ikke kjørt i testbasen"
    fil_sha = hashlib.sha256(MIGRASJON.read_bytes()).hexdigest()
    assert cs[0] == fil_sha, \
        "089 i treet er ikke bytene basen kjørte — historikk er immutable"
    fasit = json.loads(
        (ROT / "platform" / "core" / "db" / "migrasjons-fasit.json")
        .read_text(encoding="utf-8"))
    assert fasit.get("089_m35_kontinuitet.sql") == fil_sha, \
        "fasiten pinner andre bytes enn treet bærer"


def test_port7b_migrasjonen_er_ren_ddl():
    """047-klassen: masse-DML i en migrasjon kan køe utsatte
    triggerhendelser som ALTER-setninger nekter å passere. 089 har ingen
    slik seed, og porten måler premisset — med ETT navngitt unntak:
    `rolle_scope`-seedet (043 §6b, 044/088-formen ordrett)."""
    import pglast
    sql = MIGRASJON.read_text(encoding="utf-8")
    dml = []
    for raa in pglast.parse_sql(sql):
        navn = type(raa.stmt).__name__
        if navn == "InsertStmt" and raa.stmt.relation.relname == \
                "rolle_scope":
            continue
        if navn in ("InsertStmt", "UpdateStmt", "DeleteStmt"):
            dml.append(navn)
    assert not dml, (
        f"089 bærer toppnivå-DML {dml} — da er den en backfill og skal"
        " registrere seed+måling i SP-10 (sp10-provekjoring.py)")


def test_port7c_089_navngir_aldri_runtime_rollen():
    """056/057-formen: `disponit` er lokalnavnet, og `migrer.py` er
    eneste rettighetskilde. En GRANT til runtime i migrasjonen ville
    lagt rettighetsmodellen to steder."""
    sql = MIGRASJON.read_text(encoding="utf-8")
    for linje in sql.splitlines():
        if linje.lstrip().startswith("--"):
            continue
        assert "TO disponit;" not in linje, \
            f"089 grantar direkte til runtime-rollen: {linje!r}"


def test_port7d_kjoreren_speiler_089_rettighetene():
    """Tabellspeilet i `migrer.py` (057-portformen): runtime får KUN
    SELECT på de fire tabellene, og EXECUTE på alle sju dørene. Ingen
    INSERT/UPDATE noe sted — ALL skriving går gjennom dørene."""
    kjorer = (ROT / "deploy" / "staging" / "migrer.py").read_text(
        encoding="utf-8")
    assert ("GRANT SELECT ON kontinuitet_tjeneste, beredskapskontakt,"
            "\n    kontinuitetshendelse, kontinuitetshendelse_post"
            " TO {rolle};") in kjorer
    for tabell in ("kontinuitet_tjeneste", "beredskapskontakt",
                   "kontinuitetshendelse", "kontinuitetshendelse_post"):
        for verb in ("INSERT ON", "UPDATE ON", "DELETE ON"):
            assert f"{verb} {tabell}" not in kjorer, \
                f"runtime har fått {verb} {tabell} utenom dørene"
    for dor in ("m35_opprett_tjeneste", "m35_oppdater_tjeneste",
                "m35_opprett_kontakt", "m35_bekreft_kontakt",
                "m35_opprett_hendelse", "m35_legg_post",
                "m35_lukk_hendelse"):
        assert f"GRANT EXECUTE ON FUNCTION {dor}(" in kjorer, \
            f"{dor} mangler runtime-EXECUTE i kjøreren"


@pg
def test_port7e_alle_syv_dorene_eies_av_claimeren(migrator):
    """SECURITY DEFINER-dører som IKKE eies av claimeren ville kjørt som
    migrator — altså med eierens rettigheter, forbi hele modellen.
    Eierskapet står også i `eierskap-reparasjon.sql`; her måles basen."""
    rader = dict(migrator.execute(
        "SELECT p.proname, r.rolname FROM pg_proc p"
        " JOIN pg_roles r ON r.oid = p.proowner"
        " WHERE p.proname LIKE 'm35\\_%' AND p.prosecdef").fetchall())
    migrator.rollback()
    assert set(rader) == {
        "m35_opprett_tjeneste", "m35_oppdater_tjeneste",
        "m35_opprett_kontakt", "m35_bekreft_kontakt",
        "m35_opprett_hendelse", "m35_legg_post", "m35_lukk_hendelse"}
    assert set(rader.values()) == {"disponit_m37_claimer"}

    eierskap = (ROT / "deploy" / "staging" / "eierskap-reparasjon.sql") \
        .read_text(encoding="utf-8")
    for dor in rader:
        assert f"'{dor}(" in eierskap, \
            f"{dor} mangler i eierskap-reparasjon.sql"


# ---------------------------------------------------------------------------
# Registrering: manifest, scopes, bestillings-/oppdragstype
# ---------------------------------------------------------------------------

def test_manifestet_er_gyldig_og_aerlig():
    """Manifestet validerer, sier under_utvikling/ikke_i_drift, bærer de
    REELLE avhengighetene, og INGEN sjekklistepunkter er flippet uten
    måling."""
    import yaml

    from manifestskjema import valider_manifest
    m = yaml.safe_load((MODULROT / "manifest.yaml")
                       .read_text(encoding="utf-8"))
    assert valider_manifest(m) == []
    assert m["id"] == "m35_kontinuitet" == MODULROT.name
    assert m["status"] == "under_utvikling"
    assert m["driftstilstand"] == "ikke_i_drift"
    assert m["avhengigheter"] == ["m01_policy", "m02_revisjonslogg"]
    assert m["i18n_prefiks"] == "kontinuitet"
    for punkt, innhold in m["staging_sjekkliste"].items():
        assert innhold["status"] == "nei", \
            f"{punkt} er flippet uten at noen måling finnes"


def test_scopene_er_registrert_begge_veier():
    """`kontinuitet:read` er et LESEscope (og bare det); write er
    admin-myndighet og står i browser-mutasjonssettet."""
    from api.app import BROWSER_MUTASJONSSCOPES, LESESCOPES
    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert "kontinuitet:read" in LESESCOPES
    assert "kontinuitet:write" not in LESESCOPES
    assert "kontinuitet:write" in BROWSER_MUTASJONSSCOPES
    for rolle in ("leser", "sikkerhet", "admin"):
        assert "kontinuitet:read" in ROLLE_TIL_SCOPES[rolle]
    assert "kontinuitet:write" in ROLLE_TIL_SCOPES["admin"]
    for rolle in ("leser", "sikkerhet"):
        assert "kontinuitet:write" not in ROLLE_TIL_SCOPES[rolle], \
            f"{rolle} kan skrive i kriseloggen"


@pg
def test_rolle_scope_speilet_i_basen_matcher_app_laget(migrator):
    """043 §6b / 044 §6: basen skal se det SAMME rollemønsteret som
    app-laget. To kilder som kan komme i utakt er én for mange."""
    from api.autorisasjon import ROLLE_TIL_SCOPES
    rader = {(r[0], r[1]) for r in migrator.execute(
        "SELECT rolle, scope FROM rolle_scope WHERE scope LIKE"
        " 'kontinuitet:%'").fetchall()}
    migrator.rollback()
    forventet = {(rolle, scope)
                 for rolle, scopes in ROLLE_TIL_SCOPES.items()
                 for scope in scopes if scope.startswith("kontinuitet:")}
    assert rader == forventet, \
        "rolle_scope i basen og ROLLE_TIL_SCOPES er ute av takt"


def test_bestillingstypen_er_deklarert_og_lukket():
    """Kroppen er den minste i registeret: ett valg. HVA som måles er
    øvelseslogikkens lukkede kontrakt, aldri bestillerens valg — og
    fristen har ÉN kilde (`UTFORELSESFRIST_VALG`)."""
    from api.bestilling import BESTILLINGSTYPER, Bestillingsfeil, normaliser
    from oppdragskontrakt import OPPDRAGSTYPER, UTFORELSESFRIST_VALG
    bt = BESTILLINGSTYPER["kontinuitet.ovelse"]
    assert bt.eiermodul == "m35_kontinuitet"
    assert bt.omfang == ("full",)
    assert bt.skjemafelt == frozenset({"bestillingstype", "omfang"})

    ot = OPPDRAGSTYPER["kontinuitet.ovelse"]
    assert ot.eiermodul == "m35_kontinuitet"
    assert ot.paakrevde == frozenset({"omfang"})
    assert ot.produserer_artefakt is True
    assert ot.rapport_artefakttype == "kontinuitet.ovelse.rapport"
    # Handlingsprefikset UTEN punktum til slutt (m57s Codex P1): et
    # prefiks med punktum treffer aldri den nøyaktige handlingen, og
    # eiermodulen ville blitt `ukjent`.
    assert ot.handlingsprefikser == ("kontinuitet.ovelse",)

    assert UTFORELSESFRIST_VALG["kontinuitet.ovelse"] == \
        ("omfang", {"full": 30 * 60})

    ok = normaliser("t", {"bestillingstype": "kontinuitet.ovelse",
                          "omfang": "full"})
    assert ok == {"tenant": "t", "bestillingstype": "kontinuitet.ovelse",
                  "omfang": "full"}
    for kropp in ({"bestillingstype": "kontinuitet.ovelse"},
                  {"bestillingstype": "kontinuitet.ovelse",
                   "omfang": "delvis"},
                  {"bestillingstype": "kontinuitet.ovelse", "omfang": None}):
        with pytest.raises(Bestillingsfeil):
            normaliser("t", kropp)


def test_rutene_og_scopene_henger_sammen():
    """Menyen lover aldri en flate serveren svarer 403 på: basisruten og
    RUTESCOPE må bære SAMME scope."""
    from api.app import RUTESCOPE
    assert RUTESCOPE[("GET", "/v1/kontinuitet")] == "kontinuitet:read"
    for sti in ("/v1/kontinuitet/hendelser",
                "/v1/kontinuitet/hendelse/{hendelse_id:str}/post",
                "/v1/kontinuitet/hendelse/{hendelse_id:str}/lukk"):
        assert RUTESCOPE[("POST", sti)] == "kontinuitet:write"
    sitekart = (ROT / "platform" / "core" / "ui" / "static" / "js"
                / "sitekart.js").read_text(encoding="utf-8")
    assert '{ nokkel: "kontinuitet", scope: "kontinuitet:read" }' in sitekart


# ---------------------------------------------------------------------------
# HTTP-veien: feilveien `kontinuitet_ulovlig_tilstand` ende til ende
# ---------------------------------------------------------------------------

def _browserokt(migrator, roller):
    """Minirigg: en innlogget browserøkt med gitte roller i TENANT.
    -> (sesjonscookie, csrf-token)."""
    from api import sesjon as sesjonmodul
    _sett_kontekst(migrator, TENANT)
    bid = migrator.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://m35.test', %s) RETURNING bruker_id",
        ("s35h-" + secrets.token_hex(6),)).fetchone()[0]
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
@dekker("kontinuitet_ulovlig_tilstand")
def test_http_lukking_uten_etteranalyse_er_409(migrator, klient):
    """FEILVEIEN, ende til ende: en lukking uten etteranalyse svarer
    409 `kontinuitet_ulovlig_tilstand` — ikke 400 (kroppen ER velformet)
    og ikke 500 (ingenting er galt med serveren). Det er TILSTANDEN som
    sier nei, og forskjellen er hele forklaringen mennesket trenger.

    Merk hvem som feller dommen: API-et teller ikke etteranalyse-poster.
    Det kaller døren og oversetter dørens ERRCODE. En flate eller et
    API som sjekket selv, ville vært en ANDRE sannhet å komme i utakt
    med — og den utakten er nøyaktig det en krise avdekker.

    MUTASJONEN SOM DREPER DENNE: la `_doerfeil` mappe
    `integrity_constraint_violation` til 500, eller la endepunktet
    forhåndssjekke og svare 400.
    """
    from .test_rekruttering_http import _post
    cookie, csrf = _browserokt(migrator, ["admin"])

    r = _post(klient, cookie, csrf, "/v1/kontinuitet/hendelser",
              {"tekstnokkel": "drift.strombrudd", "alvor": "kritisk"})
    assert r.status_code in (200, 201), r.text
    hid = r.json()["hendelse_id"]

    # Observasjon og tiltak holder ikke — det er etteranalysen som kreves.
    for posttype in ("observasjon", "tiltak"):
        rp = _post(klient, cookie, csrf,
                   f"/v1/kontinuitet/hendelse/{hid}/post",
                   {"posttype": posttype, "tekst": "noe skjedde"})
        assert rp.status_code in (200, 201), rp.text

    r = _post(klient, cookie, csrf, f"/v1/kontinuitet/hendelse/{hid}/lukk",
              {"tekst": "prøver å lukke"})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "kontinuitet_ulovlig_tilstand"

    # Hendelsen står fortsatt ÅPEN — det avviste kallet skrev ingenting.
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT lukket_ts FROM kontinuitetshendelse WHERE tenant=%s"
        " AND hendelse_id=%s", (TENANT, hid)).fetchone()[0] is None
    migrator.rollback()

    # Med etteranalysen på plass går lukkingen gjennom.
    rp = _post(klient, cookie, csrf, f"/v1/kontinuitet/hendelse/{hid}/post",
               {"posttype": "etteranalyse", "tekst": "Aggregatet var utestet"})
    assert rp.status_code in (200, 201), rp.text
    r = _post(klient, cookie, csrf, f"/v1/kontinuitet/hendelse/{hid}/lukk",
              {"tekst": "Lukket etter gjennomgang"})
    assert r.status_code in (200, 201), r.text

    # …og en post PÅ den lukkede hendelsen er samme tilstandsnei.
    rp = _post(klient, cookie, csrf, f"/v1/kontinuitet/hendelse/{hid}/post",
               {"posttype": "tiltak", "tekst": "for sent"})
    assert rp.status_code == 404, rp.text


@pg
def test_http_leseflaten_krever_lesescopet(migrator, klient):
    """GET /v1/kontinuitet bak `kontinuitet:read`, og skriveveiene bak
    write: en ren leser får 200 på lesing og et nei på skriving."""
    from .test_rekruttering_http import _post
    from api import sesjon as sesjonmodul
    cookie, csrf = _browserokt(migrator, ["leser"])
    r = klient.get("/v1/kontinuitet",
                   cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 200, r.text
    for nokkel in ("siste_ovelse", "tjenester", "kontakter", "hendelser"):
        assert nokkel in r.json(), nokkel
    # Ingen øvelse er promotert ennå (PR-B) — `null`, aldri et nullobjekt.
    assert r.json()["siste_ovelse"] is None

    r = _post(klient, cookie, csrf, "/v1/kontinuitet/hendelser",
              {"tekstnokkel": "drift.forsok", "alvor": "kritisk"})
    assert r.status_code in (401, 403), r.text


@pg
def test_http_bestilling_paa_uregistrert_type_er_aerlig_nei_ikke_500(
        migrator, klient):
    """CodeRabbit på 089: forgreningen i `utfor_bestilling` sto som
    «alt som ikke er WCAG-formen» og leste `inndata_id` rett ut av den
    normaliserte kroppen — FØR `bestillingstype_utilgjengelig`-porten.
    En bestilling på `kontinuitet.ovelse` (som ikke har feltet) ville
    blitt en KeyError og en 500, i stedet for det ærlige «typen er ikke
    claimbar ennå».

    Porten måler at svaret er den KODEDE avvisningen, ikke en
    serverfeil. Den er også fremtidssikringen: registrerer noen
    oppdragstypen uten å gi den sin egen navngitte gren, går denne rød —
    og den samme klassen gjelder `epost.behandling` (088), som arvet
    nøyaktig samme latente feil.

    MUTASJONEN SOM DREPER DENNE: gjør `elif bt.oppdragstype ==
    "rekruttering.evaluering"` til et `else` igjen.
    """
    from .test_rekruttering_http import _post
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _post(klient, cookie, csrf, "/v1/bestilling",
              {"bestillingstype": "kontinuitet.ovelse", "omfang": "full"})
    # 503 er `bestillingstype_utilgjengelig` sin egen HTTP-klasse
    # (driftsvei: typen finnes, men er ikke claimbar ennå) — det er en
    # KODET avvisning, i motsetning til den 500-en en KeyError ville
    # gitt. Porten måler nettopp forskjellen: et svar bestilleren kan
    # handle på, ikke en uventet krasj.
    assert r.status_code != 500, \
        f"bestillingsveien velter på den nye typen: {r.text}"
    assert r.json().get("feil") == "bestillingstype_utilgjengelig", r.text


@pg
def test_tidslinjetaket_gjelder_per_hendelse_ikke_paa_tvers(migrator):
    """CodeRabbit på 089: et flatt tak på tvers lot ÉN stor hendelse
    spise hele budsjettet, så hendelsene etter den kom tilbake med TOM
    tidslinje — og en tom tidslinje leses som «ingenting skjedde», ikke
    som «vi viste deg ikke alt». Vinduet gir hver hendelse sine egne.

    Målt med et lavt tak (monkeypatchet), så porten er billig og likevel
    måler nøyaktig regelen.
    """
    from api import kontinuitet as km
    stor = _hendelse(migrator, nokkel="drift.stor")
    liten = _hendelse(migrator, nokkel="drift.liten")
    for i in range(6):
        _post(migrator, stor, "observasjon", f"post {i}")
    _post(migrator, liten, "observasjon", "den eneste")

    tidligere = km.MAKS_POSTER
    try:
        km.MAKS_POSTER = 3
        _sett_kontekst(migrator, TENANT)
        svar = km.svar_for(migrator, TENANT)
        migrator.rollback()
    finally:
        km.MAKS_POSTER = tidligere

    per = {h["hendelse_id"]: h["tidslinje"] for h in svar["hendelser"]}
    assert len(per[str(stor)]) == 3, "taket gjelder ikke per hendelse"
    # To: dørens egen 'opprettet'-post pluss den ene observasjonen.
    assert len(per[str(liten)]) == 2, \
        "den lille hendelsen ble sultet av naboen sin tidslinje"
    # …og postene som ER med, er de ELDSTE — starten av en krise er det
    # som forklarer resten.
    assert [p["tekst"] for p in per[str(stor)]] == \
        ["drift.stor", "post 0", "post 1"]


@pg
def test_driftsfeil_blir_aldri_en_tilstandsdom(migrator, klient):
    """CodeRabbit på 089: `_doerfeil` svelget ENHVER psycopg-feil som
    409. En tapt forbindelse ville da fortalt et menneske i en krise at
    hendelsen er i feil tilstand, mens sannheten er at basen er nede.
    Nå oversettes bare dørenes egne dommer; resten kastes videre.

    MUTASJONEN SOM DREPER DENNE: la `_doerfeil` returnere et `_Avbrudd`
    for alt.
    """
    from api.kontinuitet import _doerfeil
    # Dørenes egne dommer oversettes…
    for feil, ventet in (
            (psycopg.errors.UniqueViolation("x"), "idempotenskonflikt"),
            (psycopg.errors.NoDataFound("x"), "ikke_funnet"),
            (psycopg.errors.IntegrityConstraintViolation("x"),
             "kontinuitet_ulovlig_tilstand"),
            (psycopg.errors.InsufficientPrivilege("x"),
             "kontinuitet_ulovlig_tilstand")):
        avbrudd = _doerfeil(feil, "r")
        assert avbrudd is not None, feil
        assert ventet in avbrudd.respons.body.decode("utf-8"), \
            (feil, ventet)
    # …og alt annet er en DRIFTSFEIL som skal kastes videre.
    for feil in (psycopg.errors.OperationalError("nede"),
                 psycopg.errors.SyntaxError("tull"),
                 psycopg.errors.SerializationFailure("kappløp")):
        assert _doerfeil(feil, "r") is None, feil
