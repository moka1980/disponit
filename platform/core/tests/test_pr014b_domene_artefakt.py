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
    # nulling (forkastet) er lov.
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
