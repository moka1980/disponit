"""PR-014b CP1: domenekontroll · egress · artefakt — datamodell + integritet.

DB-en håndhever kontrakten: `domenekontroll`/`artefakt` er tenant-isolert
(RLS+FORCE) med statemaskiner; `domenekontroll_hendelse`/`artefakttype_register`
er append-only; ÉN verifisert eier per hostname og ÉN promotert artefakt per
(oppdrag, type); default-deny GRANT (runtime skriver aldri, ser aldri
`hostname_binding`; egress ser KUN visningen — aldri `challenge_token_hash`).
Hver invariant har en mutasjon som MÅ feile. (De herdede skrivefunksjonene
kommer i CP2; her er skjemaet + isolasjonen.)
"""
import secrets

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, TENANT, ANNEN_TENANT, migrator, miljo  # noqa: F401
from .test_m37 import _lag_sak, _lag_oppdrag, _sett_kontekst

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _host():
    return "d" + secrets.token_hex(6) + ".example"


def _dk(conn, tenant, hostname, *, status="ventende", gen=0, verifisert=False):
    _sett_kontekst(conn, tenant)
    conn.execute(
        "INSERT INTO domenekontroll (tenant, hostname, status,"
        " autorisasjonsgenerasjon, verifisert_ts, siste_vellykkede_revalidering,"
        " utloper) VALUES (%s,%s,%s,%s,"
        + ("now(), now(), now()+interval '90 days'" if verifisert
           else "NULL, NULL, NULL") + ")",
        (tenant, hostname, status, gen))
    conn.commit()


# ---------------- domenekontroll ----------------

@pg
def test_domenekontroll_rls_tenant_isolasjon(migrator):
    h = _host()
    _dk(migrator, TENANT, h)
    _sett_kontekst(migrator, ANNEN_TENANT)
    n = migrator.execute("SELECT count(*) FROM domenekontroll WHERE hostname=%s",
                         (h,)).fetchone()[0]
    migrator.rollback()
    assert n == 0, "en annen tenant så domenekontroll-raden (RLS-brudd)"


@pg
def test_domenekontroll_statemaskin_og_monoton_generasjon(migrator):
    h = _host()
    _dk(migrator, TENANT, h, status="verifisert", gen=2, verifisert=True)
    _sett_kontekst(migrator, TENANT)
    # verifisert → ventende er ulovlig.
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE domenekontroll SET status='ventende'"
                         " WHERE tenant=%s AND hostname=%s", (TENANT, h))
    migrator.rollback()
    # generasjonen kan ikke reduseres.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE domenekontroll SET autorisasjonsgenerasjon=1"
                         " WHERE tenant=%s AND hostname=%s", (TENANT, h))
    migrator.rollback()
    # (tenant, hostname) er frosset.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE domenekontroll SET hostname=%s"
                         " WHERE tenant=%s AND hostname=%s", (h + "x", TENANT, h))
    migrator.rollback()


@pg
def test_en_verifisert_per_hostname(migrator):
    h = _host()
    _dk(migrator, TENANT, h, status="verifisert", verifisert=True)
    # samme hostname, verifisert, annen tenant → delindeks avviser.
    with pytest.raises(psycopg.errors.UniqueViolation):
        _dk(migrator, ANNEN_TENANT, h, status="verifisert", verifisert=True)
    migrator.rollback()


@pg
def test_domenekontroll_hendelse_append_only(migrator):
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "INSERT INTO domenekontroll_hendelse (tenant, hostname, hendelse,"
        " aktor) VALUES (%s,%s,'utstedt','sys')", (TENANT, _host()))
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE domenekontroll_hendelse SET hendelse='x'"
                         " WHERE tenant=%s", (TENANT,))
    migrator.rollback()


@pg
def test_domenekontroll_og_hostname_binding_kan_ikke_slettes(migrator):
    h = _host()
    _dk(migrator, TENANT, h)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("DELETE FROM domenekontroll WHERE tenant=%s AND"
                         " hostname=%s", (TENANT, h))
    migrator.rollback()
    migrator.execute("INSERT INTO hostname_binding (hostname, tenant)"
                     " VALUES (%s,%s)", (h, TENANT))
    migrator.commit()
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("DELETE FROM hostname_binding WHERE hostname=%s", (h,))
    migrator.rollback()


# ---------------- artefakt ----------------

def _artefakttype(conn, modul, kh, at, ver=1):
    """kontrakt + artefakttype (begge direkte som migrator; immutabelt)."""
    conn.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,%s,%s,'p','k','krever_outbox','kompenserende')"
        " ON CONFLICT DO NOTHING", (modul, ver, kh))
    conn.execute(
        "INSERT INTO artefakttype_register (artefakttype, eiermodul,"
        " kontraktversjon, kontrakt_hash, skjema_hash) VALUES (%s,%s,%s,%s,%s)",
        (at, modul, ver, kh, "sh-" + secrets.token_hex(4)))
    conn.commit()


def _artefakt(conn, tenant, oppdrag_id, at, modul, kh, *, jti=None, ver=1):
    from db import kryptering
    _sett_kontekst(conn, tenant)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
    ct, _ = kryptering.krypter(dek, {"rapport": "x"}, tenant, key_id)
    jti = jti or ("jti-" + secrets.token_hex(8))
    aid = conn.execute(
        "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype, modul_id,"
        " release_id, kontraktversjon, kontrakt_hash, module_epoch, tilstand,"
        " storrelse_bytes, klartekst_sha256, ciphertext, dek_ref, kapabilitet_jti)"
        " VALUES (%s,%s,%s,%s,'r1',%s,%s,0,'staged',100,%s,%s,%s,%s)"
        " RETURNING artefakt_id",
        (tenant, oppdrag_id, at, modul, ver, kh,
         "h-" + secrets.token_hex(8), ct, key_id, jti)).fetchone()[0]
    conn.commit()
    return aid


@pg
def test_artefakt_statemaskin_og_frosset_binding(migrator):
    at = "at-" + secrets.token_hex(4); modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    aid = _artefakt(migrator, TENANT, opp, at, modul, kh)
    # identitet/hash frosset.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE artefakt SET klartekst_sha256='x'"
                         " WHERE artefakt_id=%s", (aid,))
    migrator.rollback()
    # staged → promotert er lovlig.
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE artefakt SET tilstand='promotert',"
                     " promotert_ts=now() WHERE artefakt_id=%s", (aid,))
    migrator.commit()
    # promotert er terminal.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE artefakt SET tilstand='forkastet'"
                         " WHERE artefakt_id=%s", (aid,))
    migrator.rollback()


@pg
def test_artefakt_ciphertext_kan_kun_nulles(migrator):
    at = "at-" + secrets.token_hex(4); modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    aid = _artefakt(migrator, TENANT, opp, at, modul, kh)
    # Codex: nulling UTEN forkastelsen er forbudt — ellers kunne en feilaktig
    # privilegert UPDATE tømme nyttelasten mens tilstand + hash fortsatt påsto
    # at evidensen fantes.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE artefakt SET ciphertext=NULL"
                         " WHERE artefakt_id=%s", (aid,))
    migrator.rollback()
    # ... og et retained artefakt kan heller ikke tømmes.
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE artefakt SET tilstand='karantene'"
                     " WHERE artefakt_id=%s", (aid,))
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE artefakt SET ciphertext=NULL"
                         " WHERE artefakt_id=%s", (aid,))
    migrator.rollback()
    # nulling I SAMME overgang staged → forkastet er lov.
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE artefakt SET tilstand='forkastet', ciphertext=NULL"
                     " WHERE artefakt_id=%s", (aid,))
    migrator.commit()
    # men å sette ciphertext til NYTT innhold er forbudt (frosset terminal + verd).
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE artefakt SET ciphertext=%s WHERE artefakt_id=%s",
                         (b"nytt", aid))
    migrator.rollback()


@pg
def test_ett_promotert_per_oppdrag(migrator):
    at = "at-" + secrets.token_hex(4); modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    a1 = _artefakt(migrator, TENANT, opp, at, modul, kh)
    a2 = _artefakt(migrator, TENANT, opp, at, modul, kh)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE artefakt SET tilstand='promotert' WHERE"
                     " artefakt_id=%s", (a1,)); migrator.commit()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.UniqueViolation):
        migrator.execute("UPDATE artefakt SET tilstand='promotert' WHERE"
                         " artefakt_id=%s", (a2,))
    migrator.rollback()


@pg
def test_artefakttype_immutable(migrator):
    at = "at-" + secrets.token_hex(4); modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE artefakttype_register SET skjema_hash='x'"
                         " WHERE artefakttype=%s", (at,))
    migrator.rollback()


# ---------------- GRANT-modell (port 17, 41) ----------------

@pg
def test_grant_runtime_default_deny_og_ingen_hostname_binding(migrator):
    # Port 41: runtime skriver ingen av de tre, og ser ikke hostname_binding.
    q = lambda sql, a: migrator.execute(sql, a).fetchone()[0]
    assert q("SELECT has_table_privilege('disponit','domenekontroll','INSERT')", ())         is False
    assert q("SELECT has_table_privilege('disponit','artefakt','UPDATE')", ())               is False
    assert q("SELECT has_table_privilege('disponit','artefakttype_register','INSERT')", ())  is False
    assert q("SELECT has_table_privilege('disponit','hostname_binding','SELECT')", ())       is False
    # men runtime LESER domenekontroll/artefakt/artefakttype_register.
    assert q("SELECT has_table_privilege('disponit','domenekontroll','SELECT')", ())         is True
    assert q("SELECT has_table_privilege('disponit','artefakt','SELECT')", ())               is True
    migrator.rollback()


@pg
def test_grant_egress_kun_visningen(migrator):
    # Port 17: egress ser visningen, ikke basistabellene, aldri hemmeligheten.
    q = lambda sql: migrator.execute(sql).fetchone()[0]
    assert q("SELECT has_table_privilege('disponit_egress','v_domeneautorisasjon','SELECT')") is True
    assert q("SELECT has_table_privilege('disponit_egress','hostname_binding','SELECT')")     is False
    assert q("SELECT has_table_privilege('disponit_egress','artefakt','SELECT')")             is False
    assert q("SELECT has_table_privilege('disponit_egress','artefakttype_register','SELECT')") is False
    # security_invoker krever kolonne-SELECT paa visningens kolonner — men ALDRI
    # paa hemmeligheten.
    assert q("SELECT has_column_privilege('disponit_egress','domenekontroll','hostname','SELECT')") is True
    assert q("SELECT has_column_privilege('disponit_egress','domenekontroll','challenge_token_hash','SELECT')") is False
    migrator.rollback()


# ================= CP2: herdede §2-funksjoner =================

def _admin():
    """migrator SET ROLE domains_admin (committed → overlever rollback)."""
    from db.pg import koble
    c = koble(MIGRATOR_DSN)
    c.execute("SET ROLE disponit_domains_admin")
    c.commit()
    return c


def _rt_call(sql, args):
    """Kall en artefaktfunksjon som RUNTIME (den har EXECUTE)."""
    from db.pg import koble
    c = koble(DSN)
    try:
        rad = c.execute(sql, args).fetchone()
        c.commit()
        return rad
    finally:
        c.close()


def _dkrow(conn, tenant, hostname):
    _sett_kontekst(conn, tenant)
    r = conn.execute("SELECT status, autorisasjonsgenerasjon,"
                     " challenge_token_hash FROM domenekontroll"
                     " WHERE tenant=%s AND hostname=%s",
                     (tenant, hostname)).fetchone()
    conn.rollback()
    return r


def _binding(conn, hostname):
    r = conn.execute("SELECT tenant FROM hostname_binding WHERE hostname=%s",
                     (hostname,)).fetchone()
    conn.rollback()
    return r[0] if r else None


@pg
def test_utsted_challenge_lagrer_hash_ikke_klartekst(migrator):
    # Port 7: kun hash lagres; reutstedelse virker.
    h = _host(); a = _admin()
    try:
        a.execute("SELECT utsted_challenge(%s,%s,false,'sha-1','sys')", (TENANT, h))
        a.commit()
        assert _dkrow(migrator, TENANT, h) == ("ventende", 0, "sha-1")
        a.execute("SELECT utsted_challenge(%s,%s,false,'sha-2','sys')", (TENANT, h))
        a.commit()
        assert _dkrow(migrator, TENANT, h)[2] == "sha-2", "reutstedelse virket ikke"
    finally:
        a.close()


@pg
def test_verifiser_setter_verifisert_og_binding(migrator):
    h = _host(); a = _admin()
    try:
        res = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                        (TENANT, h)).fetchone()[0]
        a.commit()
        assert res == "verifisert"
    finally:
        a.close()
    row = _dkrow(migrator, TENANT, h)
    assert row[0] == "verifisert" and row[1] == 1
    assert _binding(migrator, h) == TENANT


@pg
def test_b4_takeover_port10(migrator):
    # Port 10: aktiv A + B verifiser → A tilbakekalt, B avklaring_kreves,
    # 'konflikt:A', binding → B.
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        res = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                        (ANNEN_TENANT, h)).fetchone()[0]
        a.commit()
        assert res == "konflikt:" + TENANT
    finally:
        a.close()
    assert _dkrow(migrator, TENANT, h)[0] == "tilbakekalt"
    b = _dkrow(migrator, ANNEN_TENANT, h)
    assert b[0] == "avklaring_kreves"
    assert _binding(migrator, h) == ANNEN_TENANT


@pg
def test_b4_rad2_utlopt_a_gir_b_direkte_port13(migrator):
    # Port 13: A tilbakekalt → B verifiseres direkte (ingen avklaring).
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT tilbakekall_domenekontroll(%s,%s,'opphort','sys')",
                  (TENANT, h)); a.commit()
        res = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                        (ANNEN_TENANT, h)).fetchone()[0]
        a.commit()
        assert res == "verifisert"
    finally:
        a.close()
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "verifisert"
    assert _binding(migrator, h) == ANNEN_TENANT


@pg
def test_samme_tenant_reverifiser_ingen_avklaring_port14(migrator):
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        res = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                        (TENANT, h)).fetchone()[0]
        a.commit()
        assert res == "verifisert"
    finally:
        a.close()
    assert _dkrow(migrator, TENANT, h)[0] == "verifisert"


@pg
def test_avgjor_overtakelse_port12(migrator):
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (ANNEN_TENANT, h)); a.commit()   # B → avklaring_kreves
        gen = _dkrow(migrator, ANNEN_TENANT, h)[1]
        # godkjent → B verifisert (saken avgjøres for NØYAKTIG denne generasjonen)
        a.execute("SELECT avgjor_domeneovertakelse(%s,%s,%s,true,'sys')",
                  (ANNEN_TENANT, h, gen)); a.commit()
    finally:
        a.close()
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "verifisert"


@pg
def test_avgjor_avvist_gir_tilbakekalt(migrator):
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (ANNEN_TENANT, h)); a.commit()
        gen = _dkrow(migrator, ANNEN_TENANT, h)[1]
        a.execute("SELECT avgjor_domeneovertakelse(%s,%s,%s,false,'sys')",
                  (ANNEN_TENANT, h, gen)); a.commit()
    finally:
        a.close()
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "tilbakekalt"


@pg
def test_foreldet_overtakelsessak_avvises(migrator):
    """Codex: avgjørelsen er GJERDET av overtakelsesgenerasjonen.

    En gammel M-37-sak (generasjon N) skal ikke kunne autorisere en NYERE
    overtakelse (generasjon N+2) bare fordi raden tilfeldigvis står i
    `avklaring_kreves` igjen.

    MUTASJONEN SOM DREPER DENNE: fjern generasjonssjekken i
    `avgjor_domeneovertakelse` — da godkjenner den foreldede saken den nye
    konflikten.
    """
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (ANNEN_TENANT, h)); a.commit()      # B → avklaring (gen N)
        gammel_gen = _dkrow(migrator, ANNEN_TENANT, h)[1]
        # Overtakelse 1 AVVISES → B tilbakekalt (gen N+1).
        a.execute("SELECT avgjor_domeneovertakelse(%s,%s,%s,false,'sys')",
                  (ANNEN_TENANT, h, gammel_gen)); a.commit()
        # A verifiserer på nytt og B tar over igjen → ny konflikt (gen N+2).
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (ANNEN_TENANT, h)); a.commit()
        ny_gen = _dkrow(migrator, ANNEN_TENANT, h)[1]
        assert ny_gen > gammel_gen
        # Den GAMLE sakens godkjenning treffer den nye konflikten → avvist.
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            a.execute("SELECT avgjor_domeneovertakelse(%s,%s,%s,true,'sys')",
                      (ANNEN_TENANT, h, gammel_gen))
        a.rollback()
    finally:
        a.close()
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "avklaring_kreves", \
        "en foreldet sak autoriserte en nyere overtakelse"


@pg
def test_reverifisering_under_avklaring_blokkeres(migrator):
    """Codex: `avklaring_kreves` er terminal for verifiseringsveien.

    Etter at B har overtatt står B i avklaring MED bindingen på seg. Et retry
    av samme verifisering hopper da over overtakelsesgrenen (eieren er B selv)
    og traff upserten, som satte B rett til `verifisert` — hele M-37-avgjørelsen
    omgått.

    MUTASJONEN SOM DREPER DENNE: fjern avklaring-sjekken øverst i
    `verifiser_domenekontroll`.
    """
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        res = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                        (ANNEN_TENANT, h)).fetchone()[0]
        a.commit()
        assert res == "konflikt:" + TENANT
        gen = _dkrow(migrator, ANNEN_TENANT, h)[1]
        # RETRY av samme verifisering.
        res2 = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                         (ANNEN_TENANT, h)).fetchone()[0]
        a.commit()
        assert res2 == "avklaring_kreves"
    finally:
        a.close()
    rad = _dkrow(migrator, ANNEN_TENANT, h)
    assert rad[0] == "avklaring_kreves", "retry verifiserte forbi M-37-avgjørelsen"
    assert rad[1] == gen, "retry flyttet generasjonen"


@pg
def test_revalider_endrer_ikke_status(migrator):
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT revalider_domenekontroll(%s,%s,'sys')", (TENANT, h))
        a.commit()
    finally:
        a.close()
    assert _dkrow(migrator, TENANT, h)[0] == "verifisert"


@pg
def test_tilbakekall_idempotent(migrator):
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT tilbakekall_domenekontroll(%s,%s,'grunn','sys')",
                  (TENANT, h)); a.commit()
        g1 = _dkrow(migrator, TENANT, h)[1]
        a.execute("SELECT tilbakekall_domenekontroll(%s,%s,'igjen','sys')",
                  (TENANT, h)); a.commit()   # idempotent, ingen gen++
    finally:
        a.close()
    row = _dkrow(migrator, TENANT, h)
    assert row[0] == "tilbakekalt" and row[1] == g1


@pg
def test_runtime_kan_ikke_kalle_domenefunksjoner(migrator):
    from db.pg import koble
    c = koble(DSN)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT verifiser_domenekontroll('t','h',false,'x')")
        c.rollback()
    finally:
        c.close()


# ---------------- artefakt-funksjoner ----------------

def _artefakt_oppsett(migrator):
    at = "at-" + secrets.token_hex(4); modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    from db import kryptering
    _sett_kontekst(migrator, TENANT)
    key_id, _ = kryptering.hent_eller_opprett_aktiv_dek(migrator, TENANT)
    migrator.commit()
    return at, modul, kh, opp, key_id


# port 36 (idempotent (jti,hash) + konflikt) dekkes nå av
# test_pr014b_artefakt_api::test_upload_* via den ekte kapabilitetsflyten
# (lagre_artefakt_staged validerer+forbruker kapabiliteten atomisk, 016:502).


@pg
def test_promoter_epoch_avvik_port37(migrator):
    at = "at-" + secrets.token_hex(4); modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    aid = _artefakt(migrator, TENANT, opp, at, modul, kh)
    _sett_kontekst(migrator, TENANT)
    rel, h = migrator.execute("SELECT release_id, klartekst_sha256 FROM artefakt"
                              " WHERE artefakt_id=%s", (aid,)).fetchone()
    migrator.rollback()
    # epoch-avvik (5 <> stemplet 0) → avvist, ingen promotering.
    with pytest.raises(psycopg.errors.Error):
        _rt_call("SELECT promoter_artefakt(%s,%s,%s,%s,5,%s,'sys')",
                 (aid, TENANT, opp, rel, h))
    # riktig epoch → promotert.
    _rt_call("SELECT promoter_artefakt(%s,%s,%s,%s,0,%s,'sys')",
             (aid, TENANT, opp, rel, h))
    _sett_kontekst(migrator, TENANT)
    st = migrator.execute("SELECT tilstand FROM artefakt WHERE artefakt_id=%s",
                          (aid,)).fetchone()[0]
    migrator.rollback()
    assert st == "promotert"


@pg
def test_rydd_staged_forkaster_og_nuller_port40(migrator):
    at = "at-" + secrets.token_hex(4); modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    aid = _artefakt(migrator, TENANT, opp, at, modul, kh)
    # tving opprettet > 24 t tilbake.
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE artefakt SET opprettet=now()-interval '25 hours'"
                     " WHERE artefakt_id=%s", (aid,)); migrator.commit()
    a = _admin()
    try:
        n = a.execute("SELECT rydd_staged_artefakter()").fetchone()[0]
        a.commit()
        assert n >= 1
    finally:
        a.close()
    _sett_kontekst(migrator, TENANT)
    row = migrator.execute("SELECT tilstand, ciphertext FROM artefakt"
                           " WHERE artefakt_id=%s", (aid,)).fetchone()
    migrator.rollback()
    assert row[0] == "forkastet" and row[1] is None


@pg
def test_b4_overtakelsessak_idempotent_port11(migrator):
    # Port 11: konflikt → ÉN M-37-sak (familie domeneovertakelse), idempotent per
    # overtakelsesgenerasjon; ny generasjon → ny sak.
    from api.domeneovertakelse import opprett_overtakelsessak
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        res = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                        (ANNEN_TENANT, h)).fetchone()[0]
        a.commit()
        assert res == "konflikt:" + TENANT
    finally:
        a.close()
    tapt = res.split(":", 1)[1]
    gen = _dkrow(migrator, ANNEN_TENANT, h)[1]   # B-generasjon etter overtakelse
    _sett_kontekst(migrator, ANNEN_TENANT)
    uid1 = opprett_overtakelsessak(migrator, tenant_ny=ANNEN_TENANT, hostname=h,
                                   tenant_tapt=tapt, generasjon=gen, aktor="sys")
    migrator.commit()
    _sett_kontekst(migrator, ANNEN_TENANT)
    row = migrator.execute("SELECT kategori, sakstype, status FROM unntak"
                           " WHERE tenant=%s AND id=%s",
                           (ANNEN_TENANT, uid1)).fetchone()
    migrator.rollback()
    assert row == ("domeneovertakelse", "sikkerhet", "ny")
    # idempotent: samme konflikt (samme generasjon) → samme sak.
    _sett_kontekst(migrator, ANNEN_TENANT)
    uid2 = opprett_overtakelsessak(migrator, tenant_ny=ANNEN_TENANT, hostname=h,
                                   tenant_tapt=tapt, generasjon=gen, aktor="sys")
    migrator.commit()
    assert uid2 == uid1
    # ny overtakelsesgenerasjon → ny sak.
    _sett_kontekst(migrator, ANNEN_TENANT)
    uid3 = opprett_overtakelsessak(migrator, tenant_ny=ANNEN_TENANT, hostname=h,
                                   tenant_tapt=tapt, generasjon=gen + 1, aktor="sys")
    migrator.commit()
    assert uid3 != uid1


@pg
def test_karantene_bevares_gjennom_rydd(migrator):
    # Codex §7 pkt. 8: et karantenesatt artefakt (uverifisert kvittering) må
    # OVERLEVE oppryddingen — den rører kun 'staged'.
    at = "at-" + secrets.token_hex(4); modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    aid = _artefakt(migrator, TENANT, opp, at, modul, kh)
    from db.pg import koble
    c = koble(DSN)
    try:
        c.execute("SELECT karantenesett_artefakt(%s,%s,%s)", (aid, TENANT, opp))
        c.commit()
    finally:
        c.close()
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute("SELECT tilstand FROM artefakt WHERE artefakt_id=%s",
                            (aid,)).fetchone()[0] == "karantene"
    migrator.execute("UPDATE artefakt SET opprettet=now()-interval '25 hours'"
                     " WHERE artefakt_id=%s", (aid,)); migrator.commit()
    a = _admin()
    try:
        a.execute("SELECT rydd_staged_artefakter()"); a.commit()
    finally:
        a.close()
    _sett_kontekst(migrator, TENANT)
    st = migrator.execute("SELECT tilstand FROM artefakt WHERE artefakt_id=%s",
                          (aid,)).fetchone()[0]
    migrator.rollback()
    assert st == "karantene", "rydd forkastet et karantene-artefakt"


@pg
def test_append_only_tabeller_ingen_truncate(migrator):
    """Codex P2: TRUNCATE omgår rad-triggere i PostgreSQL. De append-only
    tabellene har nå statement-nivå TRUNCATE-vakt slik at skjemaeieren ikke kan
    tømme dem tross immutabilitets-invarianten.

    MUTASJONEN SOM DREPER DENNE: fjern BEFORE TRUNCATE-triggerne."""
    # domenekontroll_hendelse har ingen innkommende FK → triggeren er det ENESTE
    # som stopper en TRUNCATE.
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("TRUNCATE domenekontroll_hendelse")
    migrator.rollback()
    # artefakttype_register: FK fra artefakt blokkerer plain TRUNCATE, men CASCADE
    # (som ellers ville tømt BEGGE) fanges nettopp av statement-triggeren.
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("TRUNCATE artefakttype_register CASCADE")
    migrator.rollback()


@pg
def test_overtakelse_baerer_ny_wildcard(migrator):
    """Codex P2: overtakelsen bærer den NETTOPP forespurte wildcard-scopen inn i
    avklaringsraden, ikke den gamle. Ellers kunne en tenant med en gammel
    wildcard-rad fullføre en eksakt-host-overtakelse og etter M-37 bli verifisert
    med feil scope.

    MUTASJONEN SOM DREPER DENNE: fjern `wildcard = p_wildcard` fra ON CONFLICT."""
    h = _host(); a = _admin()
    try:
        # B har en rad med wildcard=false; settes tilbakekalt (behold gammel rad).
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (ANNEN_TENANT, h)); a.commit()
        _sett_kontekst(migrator, ANNEN_TENANT)
        migrator.execute("UPDATE domenekontroll SET status='tilbakekalt',"
                         " autorisasjonsgenerasjon=autorisasjonsgenerasjon+1"
                         " WHERE tenant=%s AND hostname=%s", (ANNEN_TENANT, h))
        migrator.commit()
        # A blir eier.
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        # B tar over igjen, nå med wildcard=TRUE.
        a.execute("SELECT verifiser_domenekontroll(%s,%s,true,'sys')",
                  (ANNEN_TENANT, h)); a.commit()
    finally:
        a.close()
    _sett_kontekst(migrator, ANNEN_TENANT)
    rad = migrator.execute("SELECT status, wildcard FROM domenekontroll"
                           " WHERE tenant=%s AND hostname=%s",
                           (ANNEN_TENANT, h)).fetchone()
    migrator.rollback()
    assert rad == ("avklaring_kreves", True), \
        "overtakelsen beholdt den gamle wildcard-scopen"


@pg
def test_wildcard_ikke_utvidbar_under_avklaring(migrator):
    """Codex: mens en rad avventer M-37-avklaring kan scope IKKE endres. Ellers
    kunne B reutstede en wildcard-challenge i avklaring_kreves, og avgjor godkjenne
    den utvidede scopen uten at wildcard-challengen ble verifisert.

    MUTASJONEN SOM DREPER DENNE: fjern 'avklaring_kreves' fra CASE-en i
    utsted_challenge sin ON CONFLICT."""
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (ANNEN_TENANT, h)); a.commit()   # B → avklaring_kreves (wildcard=false)
        # B reutsteder en WILDCARD-challenge mens den avventer avklaring.
        a.execute("SELECT utsted_challenge(%s,%s,true,%s,'sys')",
                  (ANNEN_TENANT, h, "th-" + secrets.token_hex(8))); a.commit()
    finally:
        a.close()
    _sett_kontekst(migrator, ANNEN_TENANT)
    rad = migrator.execute("SELECT status, wildcard FROM domenekontroll"
                           " WHERE tenant=%s AND hostname=%s",
                           (ANNEN_TENANT, h)).fetchone()
    migrator.rollback()
    assert rad == ("avklaring_kreves", False), \
        "wildcard-scopen ble utvidet mens raden avventet avklaring"


@pg
def test_artefakt_dek_ref_frosset(migrator):
    """Codex: dek_ref er frosset. Krypteringen binder AES-GCM-AAD til tenant|key_id,
    så en repointing til en annen nøkkel gjør artefaktet udekrypterbart mens
    ciphertext/hash/tilstand står urørt.

    MUTASJONEN SOM DREPER DENNE: fjern dek_ref fra frys-sjekken i artefakt_statemaskin."""
    at = "at-" + secrets.token_hex(4); modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    aid = _artefakt(migrator, TENANT, opp, at, modul, kh)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE artefakt SET dek_ref='annen-key'"
                         " WHERE artefakt_id=%s", (aid,))
    migrator.rollback()


@pg
def test_visning_eksponerer_wildcard_scope(migrator):
    """Codex: v_domeneautorisasjon MÅ eksponere scope-biten, ellers kan egress
    ikke skille en eksakt-host- fra en wildcard-verifisering."""
    cols = migrator.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_name='v_domeneautorisasjon'").fetchall()
    migrator.rollback()
    assert ("wildcard_scope",) in cols, "visningen mangler scope-biten"
    # egress har KOLONNE-SELECT på wildcard (security_invoker leser basiskolonnen).
    ok = migrator.execute("SELECT has_column_privilege('disponit_egress',"
                          "'domenekontroll','wildcard','SELECT')").fetchone()[0]
    migrator.rollback()
    assert ok is True, "egress mangler SELECT på wildcard-kolonnen"
