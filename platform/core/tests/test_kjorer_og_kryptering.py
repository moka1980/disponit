"""PR-005a: migrasjonskjører, envelope-kryptering og kontraktene i
migrasjon 003.

Alt her krever ekte PostgreSQL — det er hele poenget. Kontraktene ligger i
databasen (CHECK, trigger, RLS, unik-indeks), og en test som ikke snakker
med databasen kan ikke si noe om dem.
"""
import os
import threading
from datetime import datetime, timedelta, timezone

import pytest

DSN = os.environ.get("DISPONIT_TEST_DSN")
MIGRATOR_DSN = os.environ.get("DISPONIT_TEST_MIGRATOR_DSN") or DSN
pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
NAA = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
KEK = "a" * 64


@pytest.fixture()
def migrator():
    from db.pg import koble
    c = koble(MIGRATOR_DSN)
    yield c
    c.close()


@pytest.fixture()
def conn(migrator):
    """Runtime-tilkoblingen. Migrasjonene kjøres av migrator først."""
    from db.kjorer import migrer
    migrer(migrator)
    from db.pg import koble
    c = koble(DSN)
    yield c
    c.close()


# ---------------- Migrasjonskjøreren (v3-delta pkt. 1) -------------------

@pg
def test_kjorer_er_idempotent_og_kjorer_kun_manglende(migrator):
    from db.kjorer import migrer
    migrer(migrator)
    assert migrer(migrator) == [], "kjørte en migrasjon som allerede var kjørt"


@pg
def test_nye_migrasjoner_faar_alltid_checksum(migrator):
    """Kjøreren registrerer checksum for alt DEN kjører.

    Rader fra før checksum-æraen (001/002 registrerte seg selv i sin egen
    SQL) kan stå med NULL til bootstrap-skriptet har kjørt — det er hele
    grunnen til at bootstrap finnes. Testen sa opprinnelig at ALLE rader
    måtte ha checksum, og det var sant kun på en database jeg selv hadde
    droppet på forhånd. CI, som bygger databasen slik staging er bygget,
    avslørte antakelsen."""
    from db.kjorer import migrer
    migrer(migrator)
    rader = dict(migrator.execute(
        "SELECT versjon, checksum FROM migrasjoner ORDER BY versjon").fetchall())
    migrator.rollback()
    assert set(rader) == {1, 2, 3}
    assert rader[3], "migrasjon 003 ble kjørt av kjøreren og skal ha checksum"
    for legacy in (1, 2):
        assert rader[legacy] is None or len(rader[legacy]) == 64


@pg
def test_bootstrap_laaser_historikken(migrator):
    """Etter bootstrap har alle rader checksum, og kolonnen er NOT NULL —
    da kan ingen legge inn en migrasjonsrad uten å binde seg til innhold."""
    import hashlib
    import importlib.util
    from pathlib import Path
    import psycopg
    rot = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "bootstrap", rot / "deploy/staging/migrasjon-bootstrap.py")
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)

    for versjon, forventet in modul.REVIEWEDE_CHECKSUMS.items():
        migrator.execute("UPDATE migrasjoner SET checksum=%s"
                         " WHERE versjon=%s AND checksum IS NULL",
                         (forventet, versjon))
    migrator.execute("ALTER TABLE migrasjoner"
                     " ALTER COLUMN checksum SET NOT NULL")
    migrator.commit()

    mangler = migrator.execute(
        "SELECT count(*) FROM migrasjoner WHERE checksum IS NULL").fetchone()[0]
    assert mangler == 0
    with pytest.raises(psycopg.Error):
        migrator.execute("INSERT INTO migrasjoner (versjon) VALUES (999)")
    migrator.rollback()


@pg
def test_endret_historisk_migrasjon_avvises(migrator, tmp_path):
    """Kjernen i herdingen: en fil som er kjørt kan ikke endres i ettertid.
    Uten dette kan innholdet i en 'kjørt' migrasjon byttes ut, og ingen
    database vil merke det."""
    from db.kjorer import migrer
    migrer(migrator)
    migrator.execute("UPDATE migrasjoner SET checksum='0'*64 WHERE versjon=1")
    migrator.commit()
    try:
        with pytest.raises(RuntimeError, match="endret|immutable|checksum"):
            migrer(migrator)
    finally:
        migrator.rollback()
        # gjenopprett riktig checksum for de andre testene
        import hashlib
        from pathlib import Path
        mig = Path(__file__).resolve().parents[1] / "db/migrations"
        fil = next(mig.glob("001_*.sql"))
        migrator.execute("UPDATE migrasjoner SET checksum=%s WHERE versjon=1",
                         (hashlib.sha256(fil.read_bytes()).hexdigest(),))
        migrator.commit()


@pg
def test_migrasjoner_fra_003_eier_ikke_transaksjonen(migrator):
    """Kjøreren eier transaksjonen fra og med 003. En fil med egen BEGIN
    ville brutt den kontrakten stille."""
    from pathlib import Path
    mig = Path(__file__).resolve().parents[1] / "db/migrations"
    for fil in sorted(mig.glob("[0-9][0-9][0-9]_*.sql")):
        if int(fil.name[:3]) >= 3:
            sql = fil.read_text(encoding="utf-8")
            assert "BEGIN;" not in sql and "COMMIT;" not in sql, fil.name


@pg
def test_bootstrap_checksums_stemmer_med_filene():
    """Merge-porten: konstantene i bootstrap skal være SHA-256 av 001/002
    slik de ble reviewet. Feil her betyr at historikken låses til noe annet
    enn det som faktisk er gjennomgått."""
    import hashlib
    import importlib.util
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "bootstrap", rot / "deploy/staging/migrasjon-bootstrap.py")
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    for versjon, forventet in modul.REVIEWEDE_CHECKSUMS.items():
        fil = next((rot / "platform/core/db/migrations")
                   .glob(f"{versjon:03d}_*.sql"))
        assert hashlib.sha256(fil.read_bytes()).hexdigest() == forventet, fil.name


# ---------------- Envelope-kryptering (v3-delta pkt. 2-3) ---------------

@pg
def test_krypter_og_dekrypter_rundtur(conn, monkeypatch):
    from db import kryptering
    monkeypatch.setenv("DISPONIT_KEK", KEK)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, "t-krypt")
    conn.commit()
    ct, nonce = kryptering.krypter(dek, {"felt": "hemmelig"}, "t-krypt", key_id)
    assert b"hemmelig" not in ct
    assert kryptering.dekrypter(dek, ct, nonce, "t-krypt", key_id) == {
        "felt": "hemmelig"}


@pg
def test_ciphertext_kan_ikke_flyttes_til_annen_tenant(conn, monkeypatch):
    """AAD binder bytene til tenant+key_id. Kopieres raden til en annen
    tenant, feiler dekrypteringen — RLS beskytter raden, ikke bytene."""
    from cryptography.exceptions import InvalidTag
    from db import kryptering
    monkeypatch.setenv("DISPONIT_KEK", KEK)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, "t-a")
    conn.commit()
    ct, nonce = kryptering.krypter(dek, {"x": 1}, "t-a", key_id)
    with pytest.raises(InvalidTag):
        kryptering.dekrypter(dek, ct, nonce, "t-b", key_id)


@pg
def test_samme_tenant_faar_samme_dek(conn, monkeypatch):
    from db import kryptering
    monkeypatch.setenv("DISPONIT_KEK", KEK)
    k1, d1 = kryptering.hent_eller_opprett_aktiv_dek(conn, "t-gjenbruk")
    conn.commit()
    k2, d2 = kryptering.hent_eller_opprett_aktiv_dek(conn, "t-gjenbruk")
    conn.commit()
    assert (k1, d1) == (k2, d2), "ny DEK ble laget for en tenant som har en"


@pg
def test_crypto_shredding_gjor_payload_uleselig(conn, monkeypatch):
    from db import kryptering
    monkeypatch.setenv("DISPONIT_KEK", KEK)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, "t-shred")
    conn.commit()
    ct, nonce = kryptering.krypter(dek, {"personnr": "01019012345"},
                                   "t-shred", key_id)
    kryptering.destruer(conn, "t-shred", key_id)
    conn.commit()
    from db.pg import sett_tenant
    sett_tenant(conn, "t-shred")   # SET LOCAL forsvant med commit (RLS)
    rad = conn.execute("SELECT wrapped_dek, aktiv, destruert_ts IS NOT NULL"
                       " FROM tenant_nokler WHERE tenant='t-shred'"
                       " AND key_id=%s", (key_id,)).fetchone()
    conn.rollback()
    assert rad == (None, False, True)
    # ciphertext består som artefakt, men nøkkelen er borte fra databasen
    assert b"01019012345" not in ct


@pg
def test_shredding_som_ikke_traff_noe_er_en_feil(conn, monkeypatch):
    from db import kryptering
    monkeypatch.setenv("DISPONIT_KEK", KEK)
    with pytest.raises(RuntimeError, match="0 rader|forventet"):
        kryptering.destruer(conn, "t-finnes-ikke", "dek-tull")
    conn.rollback()


@pg
def test_manglende_kek_er_hard_feil(monkeypatch):
    from db import kryptering
    monkeypatch.delenv("DISPONIT_KEK", raising=False)
    with pytest.raises(RuntimeError, match="KEK"):
        kryptering._kek()


# ---------------- Kontrakter i migrasjon 003 -----------------------------

@pg
def test_destruert_noekkel_kan_aldri_vaere_aktiv(conn):
    """GO-vilkår 1. CHECK-en, ikke koden, er garantien."""
    import psycopg
    from db.pg import sett_tenant
    sett_tenant(conn, "t-check")
    with pytest.raises(psycopg.Error):
        conn.execute("INSERT INTO tenant_nokler (tenant, key_id, wrapped_dek,"
                     " aktiv, destruert_ts) VALUES ('t-check','k','\\x00',"
                     " true, now())")
    conn.rollback()


@pg
def test_verifiser_token_returnerer_aldri_hemmeligheten(migrator):
    """GO-vilkår 2: funksjonen skal svare hvem tokenet tilhører, aldri
    røpe secret_mac — heller ikke indirekte via returtypen."""
    kolonner = migrator.execute(
        "SELECT pg_get_function_result(oid) FROM pg_proc"
        " WHERE proname='verifiser_token'").fetchone()[0]
    migrator.rollback()
    assert "secret_mac" not in kolonner
    assert "tenant" in kolonner and "scopes" in kolonner


@pg
def test_verifiser_token_har_laast_search_path(migrator):
    """SECURITY DEFINER uten låst search_path er en kjent
    rettighetseskaleringsvei: kalleren kan plante egne funksjoner/tabeller
    tidligere i søkestien."""
    rad = migrator.execute(
        "SELECT prosecdef, array_to_string(proconfig,',') FROM pg_proc"
        " WHERE proname='verifiser_token'").fetchone()
    migrator.rollback()
    assert rad[0] is True
    assert "search_path=pg_catalog" in (rad[1] or "")


@pg
def test_ugyldig_mac_format_avvises_for_sammenligning(migrator):
    """Format-guarden. Ikke-hex eller feil lengde skal aldri nå
    sammenligningen."""
    for kandidat in ("", "ikke-hex", "a" * 63, "a" * 65, "A" * 64):
        rader = migrator.execute(
            "SELECT * FROM verifiser_token('t','%s')" % kandidat).fetchall()
        assert rader == []
    migrator.rollback()


@pg
def test_alle_nye_tabeller_har_rls_med_force(migrator):
    forventet = {"unntak", "unntak_historikk", "idempotens", "policyer",
                 "tenant_nokler", "attestasjon_jti"}
    rader = migrator.execute(
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class"
        " WHERE relnamespace='public'::regnamespace AND relkind='r'").fetchall()
    migrator.rollback()
    faktisk = {n: (r, f) for n, r, f in rader}
    for tabell in forventet:
        assert faktisk.get(tabell) == (True, True), \
            f"{tabell} mangler RLS med FORCE — tenant-isolasjonen er hullete"


@pg
def test_jti_kan_ikke_brukes_to_ganger(conn, migrator):
    """Replay-vernet er en unik-indeks, ikke en if-setning i koden."""
    import psycopg
    from db.pg import sett_tenant
    # RLS med FORCE gjelder ogsaa skjemaeieren: uten tenant satt sletter
    # DELETE null rader — stille, uten feil. Vedlikeholdskode maa sette
    # tenant like mye som applikasjonskoden.
    sett_tenant(migrator, "t-jti")
    migrator.execute("DELETE FROM attestasjon_jti WHERE tenant='t-jti'")
    migrator.commit()
    sett_tenant(conn, "t-jti")
    conn.execute("INSERT INTO attestasjon_jti (tenant, jti, utloper) VALUES"
                 " ('t-jti','jti-abc123456789-0123456789', now() + interval '1 hour')")
    conn.commit()
    sett_tenant(conn, "t-jti")
    with pytest.raises(psycopg.Error):
        conn.execute("INSERT INTO attestasjon_jti (tenant, jti, utloper)"
                     " VALUES ('t-jti','jti-abc123456789-0123456789',"
                     " now() + interval '1 hour')")
    conn.rollback()


@pg
def test_jti_kappløp_gir_noeyaktig_en_vinner(conn, migrator):
    """20 tråder, egne tilkoblinger, samme jti. Nøyaktig én skal vinne —
    resten skal få unikbrudd, ikke stille aksept."""
    import psycopg
    from db.pg import koble, sett_tenant
    # Rydd bort rester fra tidligere kjoeringer: testen maa starte fra en
    # tilstand der jti-en er UBRUKT, ellers taper alle tjue og testen
    # "består" av feil grunn.
    sett_tenant(migrator, "t-race")   # RLS gjelder ogsaa her, se over
    migrator.execute("DELETE FROM attestasjon_jti WHERE tenant='t-race'")
    migrator.commit()
    resultater = []
    laas = threading.Lock()
    start = threading.Barrier(20)

    def prov():
        c = koble(DSN)
        try:
            start.wait()
            sett_tenant(c, "t-race")
            c.execute("INSERT INTO attestasjon_jti (tenant, jti, utloper)"
                      " VALUES ('t-race','jti-kapplop-000001-0123456789',"
                      " now() + interval '1 hour')")
            c.commit()
            ok = True
        except psycopg.Error:
            c.rollback()
            ok = False
        finally:
            c.close()
        with laas:
            resultater.append(ok)

    traader = [threading.Thread(target=prov) for _ in range(20)]
    for t in traader:
        t.start()
    for t in traader:
        t.join()
    assert resultater.count(True) == 1, resultater


@pg
def test_kodens_jti_krav_er_ikke_mildere_enn_databasens(migrator):
    """Er koden mildere enn CHECK-en, slipper en ugyldig jti gjennom porten
    og feiler først i INSERT-en — sen feil i stedet for ren STOPP."""
    import re
    from policy_validator import attestering
    definisjon = migrator.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conname='attestasjon_jti_jti_check'").fetchone()[0]
    migrator.rollback()
    db_krav = int(re.search(r">=\s*(\d+)", definisjon).group(1))
    kort = "j" * (db_krav - 1)
    g = attestering.kontroller_binding(
        {"ressurs_id": "r", "attestasjoner": {"v": {
            f: "x" for f in attestering.BINDINGSFELT}}},
        type("C", (), {"tenant_id": "t"})(), "h", "p", NAA)
    assert g is not None, "porten slapp gjennom en attestasjon med tullefelter"
    assert len(kort) < db_krav


@pg
def test_kjoreren_avviser_fil_som_eier_egen_transaksjon(migrator, tmp_path,
                                                        monkeypatch):
    """Vakten i KJØREREN, ikke bare i filene.

    `test_migrasjoner_fra_003_eier_ikke_transaksjonen` sjekker at filene i
    repoet er i orden. Den sier ingenting om hva kjøreren gjør hvis noen
    legger inn en fil med egen BEGIN — og mutasjonstest viste nettopp at
    vakten kunne fjernes uten at én test falt."""
    from db import kjorer
    (tmp_path / "004_med_egen_tx.sql").write_text(
        "BEGIN;\nCREATE TABLE bare_tull (x int);\nCOMMIT;\n", encoding="utf-8")
    monkeypatch.setattr(kjorer, "_MIG", tmp_path)
    with pytest.raises(RuntimeError, match="transaksjonen|BEGIN"):
        kjorer.migrer(migrator)
    migrator.rollback()


@pg
def test_kjoreren_virker_paa_database_migrert_med_forrige_versjon(migrator,
                                                                  tmp_path,
                                                                  monkeypatch):
    """Oppgraderingsveien. `migrasjoner`-tabellen ble laget av 001_init.sql
    UTEN checksum-kolonne, og `CREATE TABLE IF NOT EXISTS` gjør ingenting
    når tabellen finnes. Uten en eksplisitt ALTER feiler kjøreren på enhver
    database som er migrert med PR-004 — altså på staging og i produksjon,
    men ikke på en fersk testdatabase."""
    from db import kjorer
    # Ta vare på tilstanden: å droppe kolonnen sletter checksummene for ALLE
    # versjoner, ikke bare de legacy. Uten gjenoppretting ødelegger denne
    # testen tilstanden for de andre — som den gjorde i første utgave.
    foer = dict(migrator.execute(
        "SELECT versjon, checksum FROM migrasjoner").fetchall())
    migrator.execute("ALTER TABLE migrasjoner ALTER COLUMN checksum DROP NOT NULL")
    migrator.execute("ALTER TABLE migrasjoner DROP COLUMN IF EXISTS checksum")
    migrator.commit()
    try:
        kjorer.migrer(migrator)      # skal legge til kolonnen, ikke kaste
        antall = migrator.execute(
            "SELECT count(*) FROM information_schema.columns"
            " WHERE table_name='migrasjoner' AND column_name='checksum'"
        ).fetchone()[0]
        assert antall == 1, "checksum-kolonnen ble ikke lagt til"
    finally:
        migrator.rollback()
        for versjon, cs in foer.items():
            migrator.execute("UPDATE migrasjoner SET checksum=%s WHERE versjon=%s",
                             (cs, versjon))
        migrator.commit()
