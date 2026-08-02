"""PR-004-tester: PostgreSQL-tilstandslag (ADR-001) + attestasjonssignatur.

DB-testene krever DISPONIT_TEST_DSN og markeres pg — de kjører på staging
og lokalt med PostgreSQL, og hoppes over ellers. Signaturtestene kjører
alltid.
"""
import os
import threading
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from .conftest import POLICIES
from policy_validator import attestering
from policy_validator.engine import STOPP, TILLAT, UNNTAK, EvaluationContext, Grunn

DSN = os.environ.get("DISPONIT_TEST_DSN")
# Migrator er en ANNEN rolle enn runtime (Codex P1): runtime eier ingenting
# og kan derfor ikke skru av sine egne append-only-triggere. Faller tilbake
# til runtime-DSN kun for eldre lokale oppsett med én rolle.
MIGRATOR_DSN = os.environ.get("DISPONIT_TEST_MIGRATOR_DSN") or DSN
pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
NAA = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
CTX = EvaluationContext("t-pg", "agent", True, "api_token")
NOKLER = {"v_fordring": {"k1": "x" * 40}, "v_regnskap": {"k1": "y" * 40}}


# ---------------- Attestering (kjører alltid) ----------------------------

def att_usignert(verifikator="v_fordring", ressurs="fak-1", **felt):
    a = {"verifikator": verifikator, "ressurs_id": ressurs,
         "utloper": (NAA + timedelta(hours=1)).isoformat(), "resultat": True}
    a.update(felt)
    return a


def test_signer_og_verifiser_rundtur():
    a = attestering.signer(att_usignert(), "k1", NOKLER["v_fordring"]["k1"])
    assert attestering.verifiser(a, NOKLER) is True


def test_manipulert_innhold_avvises():
    a = attestering.signer(att_usignert(), "k1", NOKLER["v_fordring"]["k1"])
    a["resultat"] = False  # tukling etter signering
    assert attestering.verifiser(a, NOKLER) is False


def test_feil_nokkel_og_ukjent_verifikator_avvises():
    a = attestering.signer(att_usignert(), "k1", "feil-hemmelighet-" + "z" * 20)
    assert attestering.verifiser(a, NOKLER) is False
    b = attestering.signer(att_usignert(verifikator="v_ukjent"), "k1", "w" * 40)
    assert attestering.verifiser(b, NOKLER) is False


def test_kontroller_hendelse_krever_signatur_pa_alle():
    god = attestering.signer(att_usignert(), "k1", NOKLER["v_fordring"]["k1"])
    ok = attestering.kontroller_hendelse({"attestasjoner": {"a": god}}, NOKLER)
    assert ok is None
    brudd = attestering.kontroller_hendelse(
        {"attestasjoner": {"a": god, "b": att_usignert()}}, NOKLER)
    assert isinstance(brudd, Grunn)
    assert brudd.kode == "attestasjon_uten_signatur"


def test_nokkelregister_avviser_svake_nokler():
    with pytest.raises(ValueError):
        attestering._valider_register({"v_x": {"k1": "kort"}})


@pytest.mark.skipif(os.name == "nt",
                    reason="POSIX-modusbiter finnes ikke paa Windows; vakten i koden gjelder fortsatt, men kan ikke testes likt der")
def test_nokkelfil_med_apne_rettigheter_avvises(tmp_path):
    fil = tmp_path / "nokler.json"
    fil.write_text('{"v_x": {"k1": "' + "a" * 40 + '"}}', encoding="utf-8")
    fil.chmod(0o644)
    with pytest.raises(PermissionError):
        attestering.last_nokler(str(fil))
    fil.chmod(0o600)
    assert attestering.last_nokler(str(fil))["v_x"]["k1"] == "a" * 40


# ---------------- PostgreSQL (ADR-001) -----------------------------------

@pytest.fixture()
def migrator():
    """Skjemaeier — migrasjoner og opprydding. Aldri runtime-veien."""
    from db.pg import koble
    c = koble(MIGRATOR_DSN)
    yield c
    c.close()


@pytest.fixture()
def conn(migrator):
    """RUNTIME-tilkoblingen: kun SELECT og INSERT, eier ingenting."""
    from db.pg import koble, migrer
    migrer(migrator)
    migrator.execute("TRUNCATE frekvens_hendelser")   # RLS gjelder ikke TRUNCATE
    migrator.commit()
    c = koble(DSN)
    yield c
    c.close()


@pytest.fixture(scope="module")
def tjeneste():
    return yaml.safe_load((POLICIES / "bransjemal-tjenestebedrift.yaml").read_text(encoding="utf-8"))


def purrehendelse(fak="fak-pg-1", signert=True):
    a1 = att_usignert("v_fordring", fak, verdi=20); a1.pop("resultat")
    a2 = att_usignert("v_fordring", fak)
    if signert:
        a1 = attestering.signer(a1, "k1", NOKLER["v_fordring"]["k1"])
        a2 = attestering.signer(a2, "k1", NOKLER["v_fordring"]["k1"])
    return {"handling": "purring.send", "ressurs_id": fak, "faktura_id": fak,
            "dataklasser": ["finansiell"], "dataklasser_kilde": "connector",
            "attestasjoner": {"forfall_passert_dager": a1,
                              "ingen_aktiv_tvist": a2}}


@pg
def test_migrasjon_er_idempotent(migrator):
    from db.pg import migrer
    assert migrer(migrator) == [1, 2]  # andre kjøring — ingen feil


@pg
def test_revisjonslogg_er_append_only_i_databasen(conn):
    import psycopg
    from db.pg import sett_tenant
    sett_tenant(conn, "t-pg")
    conn.execute("INSERT INTO revisjonslogg (tenant, input_hash, policy_id,"
                 " beslutning, begrunnelse) VALUES ('t-pg','h','p','STOPP','[]')")
    conn.commit()
    for sql in ("UPDATE revisjonslogg SET beslutning='TILLAT'",
                "DELETE FROM revisjonslogg",
                "TRUNCATE revisjonslogg"):
        with pytest.raises(psycopg.Error):
            conn.execute(sql)
        conn.rollback()


@pg
def test_atomisk_reservasjon_under_ekte_kappløp(conn):
    """20 tråder, egne tilkoblinger, maks=3 — nøyaktig 3 skal vinne."""
    from db.pg import PgTellerLager, koble
    nokkel = ("t-pg", "purring.send", "faktura_id", "kapplop")
    resultater = []

    def prov():
        c = koble(DSN)
        try:
            resultater.append(PgTellerLager(c).reserver(
                nokkel, NAA - timedelta(days=14), 3, NAA))
        finally:
            c.close()

    traader = [threading.Thread(target=prov) for _ in range(20)]
    for t in traader: t.start()
    for t in traader: t.join()
    assert resultater.count(True) == 3 and resultater.count(False) == 17


@pg
def test_reservasjon_og_logg_i_samme_transaksjon(conn, tjeneste):
    """ADR-001 krav 2: TILLAT gir nøyaktig én reservasjon OG én loggpost;
    kappløpstaper gir loggpost med blokkert utfall og INGEN reservasjon."""
    from db.pg import sikker_beslutning_pg
    d1 = sikker_beslutning_pg(tjeneste, CTX, purrehendelse(), conn,
                              naa=NAA, nokler=NOKLER)
    assert d1.beslutning == TILLAT
    d2 = sikker_beslutning_pg(tjeneste, CTX, purrehendelse(), conn,
                              naa=NAA + timedelta(days=3), nokler=NOKLER)
    assert d2.beslutning == UNNTAK  # frekvensgrense (maks 1 per 14 dager)
    from db.pg import sett_tenant
    sett_tenant(conn, "t-pg")
    ant = conn.execute("SELECT count(*) FROM frekvens_hendelser").fetchone()[0]
    logg = conn.execute("SELECT beslutning FROM revisjonslogg"
                        " WHERE tenant='t-pg' ORDER BY id").fetchall()
    conn.rollback()
    assert ant == 1                       # taperen reserverte ingenting
    assert [r[0] for r in logg][-2:] == ["TILLAT", "UNNTAK"]  # begge logget


@pg
def test_signaturport_stopper_usignert_hendelse(conn, tjeneste):
    from db.pg import sikker_beslutning_pg
    d = sikker_beslutning_pg(tjeneste, CTX, purrehendelse(signert=False),
                             conn, naa=NAA, nokler=NOKLER)
    assert d.beslutning == STOPP
    assert d.begrunnelse[-1].kode == "attestasjon_uten_signatur"
    from db.pg import sett_tenant
    sett_tenant(conn, "t-pg")
    siste = conn.execute("SELECT beslutning FROM revisjonslogg"
                         " ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.rollback()
    assert siste == STOPP  # også signaturbrudd revisjonslogges


@pg
def test_db_nede_gir_stopp_aldri_sideeffekt(tjeneste):
    from db.pg import koble, sikker_beslutning_pg
    c = koble(DSN)
    c.close()  # simuler DB borte
    d = sikker_beslutning_pg(tjeneste, CTX, purrehendelse(), c,
                             naa=NAA, nokler=NOKLER)
    assert d.beslutning == STOPP


# ---------- Codex-review PR-004: rolleskille og tenant-isolasjon ---------

@pg
def test_runtime_kan_ikke_skru_av_append_only(conn):
    """P1: eide runtime-rollen tabellene, kunne den slette eller deaktivere
    sine egne append-only-triggere. En vakt du kan fjerne er ingen vakt."""
    import psycopg
    for sql in ("ALTER TABLE revisjonslogg DISABLE TRIGGER revisjonslogg_ingen_endring",
                "ALTER TABLE revisjonslogg DISABLE TRIGGER ALL",
                "DROP TRIGGER revisjonslogg_ingen_endring ON revisjonslogg",
                "ALTER TABLE revisjonslogg DROP CONSTRAINT revisjonslogg_tenant_ikke_tom",
                "DROP TABLE revisjonslogg",
                "ALTER TABLE revisjonslogg DISABLE ROW LEVEL SECURITY",
                "DROP POLICY tenant_isolasjon ON revisjonslogg"):
        with pytest.raises(psycopg.Error):
            conn.execute(sql)
        conn.rollback()


@pg
def test_runtime_eier_ingenting(conn):
    """Eierskap er selve forskjellen: eier man tabellen, kan man endre den."""
    rad = conn.execute(
        "SELECT count(*) FROM pg_tables WHERE schemaname='public'"
        " AND tableowner = current_user").fetchone()[0]
    conn.rollback()
    assert rad == 0, "runtime-rollen eier tabeller — da kan den fjerne vaktene"


@pg
def test_tenant_isolasjon_leser_ikke_paa_tvers(conn):
    """P1: en indeks er ikke isolasjon. Databasegrensen skal nekte lesing av
    en annen tenants rader, ikke bare gjøre den rask."""
    from db.pg import sett_tenant
    sett_tenant(conn, "tenant-a")
    conn.execute("INSERT INTO revisjonslogg (tenant, input_hash, policy_id,"
                 " beslutning, begrunnelse)"
                 " VALUES ('tenant-a','h-a','p','TILLAT','[]')")
    conn.commit()
    sett_tenant(conn, "tenant-b")
    conn.execute("INSERT INTO revisjonslogg (tenant, input_hash, policy_id,"
                 " beslutning, begrunnelse)"
                 " VALUES ('tenant-b','h-b','p','TILLAT','[]')")
    conn.commit()

    sett_tenant(conn, "tenant-a")
    synlige = conn.execute("SELECT DISTINCT tenant FROM revisjonslogg").fetchall()
    conn.rollback()
    assert [r[0] for r in synlige] == ["tenant-a"], \
        f"tenant-a ser andre tenanters rader: {synlige}"


@pg
def test_tenant_isolasjon_skriver_ikke_paa_tvers(conn):
    """Å skrive en rad merket med en ANNEN tenant enn sesjonens skal avvises
    av WITH CHECK — ellers kan en tenant plante rader hos en annen."""
    import psycopg
    from db.pg import sett_tenant
    sett_tenant(conn, "tenant-a")
    with pytest.raises(psycopg.Error):
        conn.execute("INSERT INTO revisjonslogg (tenant, input_hash, policy_id,"
                     " beslutning, begrunnelse)"
                     " VALUES ('tenant-b','h-x','p','TILLAT','[]')")
    conn.rollback()


@pg
def test_uten_tenant_er_alt_stengt(conn):
    """Fail-closed: glemmer koden å sette tenant, skal databasen vise null
    rader og nekte skriving — ikke vise alle tenanters data."""
    import psycopg
    conn.execute("SELECT set_config('disponit.tenant', '', true)")
    conn.execute("RESET disponit.tenant")
    antall = conn.execute("SELECT count(*) FROM revisjonslogg").fetchone()[0]
    assert antall == 0, "uten tenant er hele loggen synlig — isolasjonen er av"
    with pytest.raises(psycopg.Error):
        conn.execute("INSERT INTO revisjonslogg (tenant, input_hash, policy_id,"
                     " beslutning, begrunnelse)"
                     " VALUES ('tenant-a','h-y','p','TILLAT','[]')")
    conn.rollback()


@pg
def test_tenant_kan_ikke_vaere_null_eller_tom(migrator):
    """P1: kolonnen tillot NULL. En loggpost uten tenant kan ikke isoleres,
    og da er isolasjonen hullete uansett hvor god policyen er."""
    import psycopg
    from db.pg import sett_tenant
    sett_tenant(migrator, "tenant-a")
    for tenant in (None, "", "   "):
        with pytest.raises(psycopg.Error):
            migrator.execute(
                "INSERT INTO revisjonslogg (tenant, input_hash, policy_id,"
                " beslutning, begrunnelse) VALUES (%s,'h','p','TILLAT','[]')",
                (tenant,))
        migrator.rollback()


@pg
def test_uautentisert_forsok_logges_paa_reservert_tenant(conn, tjeneste):
    """Uten kontekst finnes ingen tenant — men forsøket skal likevel stå i
    revisjonsloggen, på den reserverte verdien, aldri hos en ekte kunde."""
    from db.pg import UKJENT_TENANT, sett_tenant, sikker_beslutning_pg
    d = sikker_beslutning_pg(tjeneste, None, purrehendelse(), conn, naa=NAA)
    assert d.beslutning == STOPP
    sett_tenant(conn, UKJENT_TENANT)
    siste = conn.execute("SELECT tenant, beslutning FROM revisjonslogg"
                         " ORDER BY id DESC LIMIT 1").fetchone()
    conn.rollback()
    assert siste == (UKJENT_TENANT, "STOPP")


@pg
def test_ogsaa_skjemaeieren_er_underlagt_tenant_isolasjonen(migrator):
    """FORCE ROW LEVEL SECURITY. Uten FORCE er tabelleieren unntatt policyen,
    og da forsvinner isolasjonen i det migrator-rollen — eller en
    feilkonfigurert runtime som eier tabellene — kobler seg på."""
    from db.pg import sett_tenant
    sett_tenant(migrator, "tenant-a")
    migrator.execute("INSERT INTO revisjonslogg (tenant, input_hash, policy_id,"
                     " beslutning, begrunnelse)"
                     " VALUES ('tenant-a','h-eier','p','TILLAT','[]')")
    migrator.commit()
    sett_tenant(migrator, "tenant-b")
    synlige = migrator.execute(
        "SELECT DISTINCT tenant FROM revisjonslogg").fetchall()
    migrator.rollback()
    assert [r[0] for r in synlige] in ([], ["tenant-b"]), \
        f"skjemaeieren omgaar tenant-isolasjonen: {synlige}"


@pg
def test_append_only_triggeren_star_paa_etter_migrasjon(migrator):
    """Migrasjon 002 slår triggeren AV for å bakfylle tenant, og PÅ igjen.
    Glemmes det siste, er revisjonsloggen ikke lenger append-only — og den
    vanlige append-only-testen ville ikke merket det, fordi den kjører som
    runtime, som mangler UPDATE-rettighet uansett. Den ville altså bestått
    av feil grunn. Denne kjører som eier, der bare triggeren stopper oss."""
    import psycopg
    from db.pg import migrer, sett_tenant
    migrer(migrator)
    sett_tenant(migrator, "tenant-a")
    migrator.execute("INSERT INTO revisjonslogg (tenant, input_hash, policy_id,"
                     " beslutning, begrunnelse)"
                     " VALUES ('tenant-a','h-trig','p','TILLAT','[]')")
    migrator.commit()
    sett_tenant(migrator, "tenant-a")
    with pytest.raises(psycopg.Error):
        migrator.execute("UPDATE revisjonslogg SET beslutning='STOPP'"
                         " WHERE input_hash='h-trig'")
    migrator.rollback()
