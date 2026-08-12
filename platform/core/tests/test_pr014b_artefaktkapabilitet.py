"""PR-014b CP5: artefakt-opplastingskapabilitet (§7).

Egen kapabilitet med eget scope, bundet til det claimede oppdragets kontrakt +
epoch. Kryssbruk mot kvitteringskapabiliteten avvises STRUKTURELT (egen tabell,
egne funksjoner). Utstedes kun for et plukket oppdrag med matchende binding;
innløses kun av den holdende modulen; forbrukes atomisk og idempotent.
"""
import secrets
import uuid

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, TENANT, migrator, miljo  # noqa: F401
from .test_m37 import _lag_sak, _lag_oppdrag, _sett_kontekst

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _admin():
    """runtime-tilkobling (funksjonene har EXECUTE til disponit)."""
    from db.pg import koble
    return koble(DSN)


def _plukket_oppdrag_med_binding(conn, modul, kh):
    """Et claimet, kontraktbundet oppdrag (satt direkte — 014b-basen har ennå
    ikke 014as current_user-gate; her måler vi kapabiliteten, ikke claimen)."""
    kontrakt = ("INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
                "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
                "reversibilitet) VALUES (%s,1,%s,'p','k','krever_outbox',"
                "'kompenserende') ON CONFLICT DO NOTHING")
    conn.execute(kontrakt, (modul, kh))
    at = "at-" + secrets.token_hex(4)
    conn.execute("INSERT INTO artefakttype_register (artefakttype,eiermodul,"
                 "kontraktversjon,kontrakt_hash,skjema_hash) VALUES (%s,%s,1,%s,'sh')",
                 (at, modul, kh))
    conn.commit()
    sak, logg = _lag_sak(conn, TENANT)
    opp, _ = _lag_oppdrag(conn, TENANT, sak, logg)
    _sett_kontekst(conn, TENANT)
    conn.execute(
        "UPDATE oppdrag SET status='plukket', owner_claim_id=%s,"
        " owner_lease_utloper=now()+interval '5 min', modul_id=%s,"
        " kontraktversjon=1, kontrakt_hash=%s, module_epoch=0"
        " WHERE tenant=%s AND id=%s",
        (secrets.token_hex(16), modul, kh, TENANT, opp))
    conn.commit()
    return opp, at


def _utsted(conn, opp, modul, kh, at, jti=None):
    jti = jti or secrets.token_hex(16)
    conn.execute(
        "SELECT jti FROM utsted_artefaktkapabilitet(%s,%s,%s,'r1',1,%s,0,%s,%s,900)",
        (TENANT, opp, modul, kh, at, jti))
    conn.commit()
    return jti


@pg
def test_utsted_krever_plukket_oppdrag_med_binding(migrator):
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    a = _admin()
    try:
        jti = _utsted(a, opp, modul, kh, at)
        assert len(jti) >= 32
        # feil kontrakt-hash → avvist (binding matcher ikke oppdraget).
        with pytest.raises(psycopg.errors.Error):
            a.execute("SELECT utsted_artefaktkapabilitet(%s,%s,%s,'r1',1,'feil',0,"
                      "%s,%s,900)", (TENANT, opp, modul, at, secrets.token_hex(16)))
        a.rollback()
    finally:
        a.close()


@pg
def test_innlos_kun_holdende_modul(migrator):
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    a = _admin()
    try:
        jti = _utsted(a, opp, modul, kh, at)
        rad = a.execute("SELECT tenant, oppdrag_id, release_id, artefakttype"
                        " FROM innlos_artefaktkapabilitet(%s,%s)",
                        (jti, modul)).fetchone()
        a.commit()
        assert rad == (TENANT, opp, "r1", at)
        # feil modul → ingen rad.
        assert a.execute("SELECT count(*) FROM innlos_artefaktkapabilitet(%s,%s)",
                         (jti, "annen-modul")).fetchone()[0] == 0
        a.commit()
    finally:
        a.close()


@pg
def test_bruk_idempotent_og_konflikt(migrator):
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    a = _admin()
    try:
        jti = _utsted(a, opp, modul, kh, at)
        aid = str(uuid.uuid4())
        assert a.execute("SELECT bruk_artefaktkapabilitet(%s,%s)",
                         (jti, aid)).fetchone()[0] == "brukt"
        a.commit()
        assert a.execute("SELECT bruk_artefaktkapabilitet(%s,%s)",
                         (jti, aid)).fetchone()[0] == "idempotent"
        a.commit()
        assert a.execute("SELECT bruk_artefaktkapabilitet(%s,%s)",
                         (jti, str(uuid.uuid4()))).fetchone()[0] == "konflikt"
        a.rollback()
        assert a.execute("SELECT bruk_artefaktkapabilitet(%s,%s)",
                         (secrets.token_hex(16), str(uuid.uuid4()))
                         ).fetchone()[0] == "ugyldig"
        a.commit()
    finally:
        a.close()


@pg
def test_kryssbruk_mot_kvitteringskapabilitet_avvist(migrator):
    # Strukturelt: en artefakt-jti finnes ikke i kvitteringskapabiliteter-tabellen,
    # så innlos_kvitteringskapabilitet returnerer ingenting (og omvendt).
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    a = _admin()
    try:
        jti = _utsted(a, opp, modul, kh, at)
        n = a.execute("SELECT count(*) FROM innlos_kvitteringskapabilitet(%s,%s)",
                      (jti, modul)).fetchone()[0]
        a.commit()
        assert n == 0, "artefakt-kapabilitet ble innløst som kvitteringskapabilitet"
    finally:
        a.close()


@pg
def test_bindingsfelter_uforanderlige(migrator):
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    a = _admin()
    try:
        jti = _utsted(a, opp, modul, kh, at)
    finally:
        a.close()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE artefaktkapabilitet SET module_epoch=9 WHERE jti=%s",
                         (jti,))
    migrator.rollback()
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("DELETE FROM artefaktkapabilitet WHERE jti=%s", (jti,))
    migrator.rollback()
