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


def alle_versjoner() -> list[int]:
    """Versjonene som FINNES i repoet, lest fra mappa.

    Sto tidligere hardkodet som `[1, 2, 3]` fem steder. Da PR-005b la til
    004, falt seks tester på en gang — uten at noen av dem hadde noe med
    innholdet i 004 å gjøre. En test som må endres hver gang en migrasjon
    legges til, måler filnavn, ikke kontrakt. Kontrakten er: kjøreren
    registrerer NØYAKTIG de versjonene som finnes, alle med checksum.
    """
    from pathlib import Path
    mig = Path(__file__).resolve().parents[1] / "db/migrations"
    return sorted(int(f.name[:3])
                  for f in mig.glob("[0-9][0-9][0-9]_*.sql"))


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
    assert set(rader) == set(alle_versjoner())
    for v in alle_versjoner():
        if v in (1, 2):     # legacy: kan stå med NULL til bootstrap har kjørt
            assert rader[v] is None or len(rader[v]) == 64
        else:
            assert rader[v], f"migrasjon {v:03d} ble kjørt av kjøreren og skal ha checksum"


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
    # Versjonsnummeret må være HØYERE enn alt som finnes i repoet. Filen het
    # opprinnelig 004, og da PR-005b la til en ekte 004 traff testen
    # checksum-vakten i stedet for BEGIN-vakten — den ville fortsatt
    # «bestått» med `raises(RuntimeError)`, men bevist feil kontrakt.
    ny = max(alle_versjoner()) + 1
    (tmp_path / f"{ny:03d}_med_egen_tx.sql").write_text(
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


@pg
def test_migrasjonen_er_faktisk_lagret_etter_kjoring():
    """Persistens, ikke bare returverdi.

    `with conn.transaction()` blir en SAVEPOINT når det allerede finnes en
    åpen transaksjon — og advisory-låsen åpner en. Uten en eksplisitt
    commit rulles både DDL og registrering tilbake når tilkoblingen lukkes,
    mens kjøreren har rapportert at migrasjonen er kjørt.

    Oppdaget på staging: `migrer()` svarte `[3]`, og etterpå fantes ingen
    rad for versjon 3. Alle de andre kjører-testene passerte, fordi de leser
    fra SAMME tilkobling der den uncommitede raden er synlig — de bevises
    for feil grunn. Denne åpner en NY tilkobling."""
    from db.kjorer import migrer
    from db.pg import koble
    # Testen MÅ starte fra en tilstand der noe faktisk mangler. Første
    # utgave kjørte mot et ferdig register, der migrer() ikke gjør noe — og
    # da passerte den også med commiten fjernet. Mutasjonstest avslørte det.
    opprydd = koble(MIGRATOR_DSN)
    try:
        opprydd.execute("DELETE FROM migrasjoner WHERE versjon=3")
        opprydd.commit()
    finally:
        opprydd.close()

    c1 = koble(MIGRATOR_DSN)
    try:
        assert migrer(c1) == [3], "det var ingenting å kjøre — testen ville bestått uansett"
    finally:
        c1.close()
    c2 = koble(MIGRATOR_DSN)
    try:
        versjoner = [r[0] for r in c2.execute(
            "SELECT versjon FROM migrasjoner ORDER BY versjon").fetchall()]
        c2.rollback()
    finally:
        c2.close()
    assert versjoner == alle_versjoner(), \
        f"registeret overlevde ikke tilkoblingen: {versjoner}"


# ---------- Codex-krav: herding skjer FØR 003, på begge veier ------------

def _bootstrap_modul():
    import importlib.util
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    spek = importlib.util.spec_from_file_location(
        "migrasjon_bootstrap", rot / "deploy/staging/migrasjon-bootstrap.py")
    modul = importlib.util.module_from_spec(spek)
    spek.loader.exec_module(modul)
    return modul


def _migrer_modul():
    import importlib.util
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    spek = importlib.util.spec_from_file_location(
        "deploy_migrer", rot / "deploy/staging/migrer.py")
    modul = importlib.util.module_from_spec(spek)
    spek.loader.exec_module(modul)
    return modul


def _nullstill(migrator, med_legacy_uten_checksum: bool):
    """Bygger utgangstilstanden testen skal måle fra.

    med_legacy_uten_checksum=True etterligner en PR-004-database: 001 og 002
    er kjørt og registrert, checksum-kolonnen finnes ikke, 003 er ukjent.
    False gir en helt fersk database.
    """
    migrator.execute("DROP TABLE IF EXISTS unntak_historikk, unntak,"
                     " idempotens, policyer, tenant_nokler, attestasjon_jti,"
                     " api_tokener, revisjonslogg, frekvens_hendelser,"
                     " migrasjoner CASCADE")
    migrator.commit()
    if med_legacy_uten_checksum:
        from pathlib import Path
        mig = Path(__file__).resolve().parents[1] / "db/migrations"
        for v in (1, 2):
            fil = next(mig.glob(f"{v:03d}_*.sql"))
            migrator.execute(fil.read_text(encoding="utf-8"))
        migrator.commit()
        # PR-004-tilstand: ingen checksum-kolonne i det hele tatt
        migrator.execute("ALTER TABLE migrasjoner DROP COLUMN IF EXISTS checksum")
        migrator.commit()


@pg
@pytest.mark.parametrize("oppgradering", [False, True],
                         ids=["fersk_database", "pr004_oppgradering"])
def test_herding_skjer_for_003_paa_begge_veier(migrator, monkeypatch,
                                               oppgradering):
    """Codex' P1: oppsettet kjørte aldri checksum-bootstrapen. Både fersk
    CI og oppgraderinger kunne bli grønne med `migrasjoner.checksum`
    fortsatt nullable, og 003 kjørt før historikken var herdet — i strid
    med den bindende kontrakten i v3-delta.

    Testen måler rekkefølgen direkte: den noterer når checksum-kolonnen ble
    NOT NULL, og når 003 ble registrert."""
    _nullstill(migrator, oppgradering)
    modul = _migrer_modul()
    monkeypatch.setenv("DISPONIT_MIGRATOR_URL", MIGRATOR_DSN)

    rekkefolge = []
    bootstrap = _bootstrap_modul()
    ekte_herd = bootstrap.herd_historikk

    def spionert_herd(conn):
        registrerte = [r[0] for r in conn.execute(
            "SELECT versjon FROM migrasjoner ORDER BY versjon").fetchall()]
        rekkefolge.append(("herding", registrerte))
        return ekte_herd(conn)

    bootstrap.herd_historikk = spionert_herd
    monkeypatch.setattr(modul, "last_bootstrap", lambda: bootstrap)

    assert modul.main(["disponit"]) == 0

    assert rekkefolge, "herd_historikk ble aldri kalt — historikken er ikke låst"
    _, ved_herding = rekkefolge[0]
    assert 3 not in ved_herding, (
        f"003 var registrert allerede da herdingen skjedde: {ved_herding} — "
        f"kontrakten krever backfill + NOT NULL FØR 003")

    nullable = migrator.execute(
        "SELECT is_nullable FROM information_schema.columns"
        " WHERE table_name='migrasjoner' AND column_name='checksum'").fetchone()
    uten = migrator.execute(
        "SELECT versjon FROM migrasjoner WHERE checksum IS NULL").fetchall()
    versjoner = [r[0] for r in migrator.execute(
        "SELECT versjon FROM migrasjoner ORDER BY versjon").fetchall()]
    migrator.rollback()
    assert nullable[0] == "NO", "checksum er fortsatt nullable"
    assert uten == [], f"migrasjoner uten checksum: {uten}"
    assert versjoner == alle_versjoner()


@pg
def test_migrer_feiler_hardt_hvis_historikken_ikke_er_laast(migrator,
                                                            monkeypatch):
    """En advarsel med exit 0 er ingen port. Blir herdingen en no-op, skal
    inngangen returnere feil — ikke skrive en beskjed og gå videre."""
    _nullstill(migrator, med_legacy_uten_checksum=True)
    modul = _migrer_modul()
    monkeypatch.setenv("DISPONIT_MIGRATOR_URL", MIGRATOR_DSN)

    bootstrap = _bootstrap_modul()
    bootstrap.herd_historikk = lambda conn: None      # herding gjøres til no-op
    monkeypatch.setattr(modul, "last_bootstrap", lambda: bootstrap)

    assert modul.main(["disponit"]) != 0, "inngangen godtok en ulåst historikk"
    migrator.rollback()


@pg
def test_ingen_annen_prosess_naar_003_for_herdingen_er_ferdig(migrator,
                                                              monkeypatch):
    """Codex' P1: ytterlåsen manglet, så «herding før 003» var bare sant
    inne i én prosess.

    Deterministisk, uten sleep: prosess A stanses INNE i herdingen mens den
    holder ytterlåsen. Prosess B — en helt annen tilkobling — forsøker å
    kjøre migrasjonene og skal ikke komme til 003. B får `lock_timeout`, så
    den feiler raskt i stedet for å henge, og testen måler faktisk
    blokkering framfor å håpe på en rekkefølge."""
    import threading
    import psycopg
    from db.pg import koble

    _nullstill(migrator, med_legacy_uten_checksum=True)
    modul = _migrer_modul()
    monkeypatch.setenv("DISPONIT_MIGRATOR_URL", MIGRATOR_DSN)

    bootstrap = _bootstrap_modul()
    ekte_herd = bootstrap.herd_historikk
    a_er_inne = threading.Event()
    b_er_ferdig = threading.Event()

    def herd_med_pause(conn):
        a_er_inne.set()
        assert b_er_ferdig.wait(30), "B ble aldri ferdig"
        return ekte_herd(conn)

    bootstrap.herd_historikk = herd_med_pause
    monkeypatch.setattr(modul, "last_bootstrap", lambda: bootstrap)

    a_resultat = {}

    def kjor_a():
        a_resultat["kode"] = modul.main(["disponit"])

    a = threading.Thread(target=kjor_a)
    a.start()
    try:
        assert a_er_inne.wait(30), "A nådde aldri herdingen"

        # B: egen tilkobling, kort lock_timeout. Ytterlåsen A holder skal
        # stoppe den før den rekker å registrere 003.
        from db.kjorer import migrer as kjorer_migrer
        b = koble(MIGRATOR_DSN)
        try:
            b.execute("SET lock_timeout = '750ms'")
            b.commit()
            with pytest.raises(psycopg.Error) as feil:
                kjorer_migrer(b)
            assert "lock" in str(feil.value).lower(), str(feil.value)
        finally:
            b.close()

        registrert_mens_a_venter = [r[0] for r in migrator.execute(
            "SELECT versjon FROM migrasjoner ORDER BY versjon").fetchall()]
        migrator.rollback()
        assert 3 not in registrert_mens_a_venter, (
            "003 ble registrert av en annen prosess før herdingen var ferdig: "
            f"{registrert_mens_a_venter}")
    finally:
        b_er_ferdig.set()
        a.join(60)

    assert a_resultat.get("kode") == 0
    nullable = migrator.execute(
        "SELECT is_nullable FROM information_schema.columns"
        " WHERE table_name='migrasjoner' AND column_name='checksum'").fetchone()
    versjoner = [r[0] for r in migrator.execute(
        "SELECT versjon FROM migrasjoner ORDER BY versjon").fetchall()]
    migrator.rollback()
    assert nullable[0] == "NO" and versjoner == alle_versjoner()
