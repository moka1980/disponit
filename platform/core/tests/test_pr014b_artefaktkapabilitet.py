"""PR-014b CP5: artefakt-opplastingskapabilitet (§7).

Egen kapabilitet med eget scope, bundet til det claimede oppdragets kontrakt +
epoch. Kryssbruk mot kvitteringskapabiliteten avvises STRUKTURELT (egen tabell,
egne funksjoner). Utstedes kun for et plukket oppdrag med matchende binding;
innløses kun av den holdende modulen; forbrukes atomisk og idempotent.
"""
import secrets

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, TENANT, migrator, miljo  # noqa: F401
from .test_m37 import _lag_sak, _lag_oppdrag, _sett_kontekst

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _admin():
    """runtime-tilkobling (funksjonene har EXECUTE til disponit)."""
    from db.pg import koble
    return koble(DSN)


def _mk_admin(rolle):
    """migrator SET ROLE <rolle>, committed (varig på tvers av rollback)."""
    from db.pg import koble
    c = koble(MIGRATOR_DSN)
    c.execute(f"SET ROLE {rolle}")
    c.commit()
    return c


def _plukket_oppdrag_med_binding(conn, modul, kh):
    """Et LEGITIMT claimet, kontraktbundet oppdrag: registrer kontrakt+release,
    aktiver modulen med en claiming-deployment, registrer oppdragstype+
    artefakttype, og claim via den herdede claim-veien (014a-final tillater kun
    claim-funksjonen å stemple bindingen). Returnerer (oppdrag_id, artefakttype)."""
    from .test_pr014a_cp5_claim import _lag_oppdrag_type
    ma = _mk_admin("disponit_modules_admin")
    try:
        ma.execute("SELECT registrer_kontrakt(%s,1,%s,'p','k','krever_outbox',"
                   "'kompenserende','sys')", (modul, kh))
        ma.execute("SELECT registrer_release(%s,'r1',1,%s,'mh','ad','sys')",
                   (modul, kh))
        ma.execute("SELECT installer_modul(%s,'sys')", (modul,))
        ma.execute("SELECT sett_modulstatus(%s,'staging_verifisert',NULL,'sys')",
                   (modul,))
        ma.execute("SELECT bytt_release(%s,'staging','r1',1,%s,'sys')", (modul, kh))
        ma.execute("SELECT sett_modulstatus(%s,'aktiv','r1','sys')", (modul,))
        ot = "cp5b-" + secrets.token_hex(4)
        ma.execute("SELECT registrer_oppdragstype(%s,%s,1,%s,'sys')", (ot, modul, kh))
        ma.commit()
    finally:
        ma.close()
    at = "at-" + secrets.token_hex(4)
    da = _mk_admin("disponit_domains_admin")
    try:
        da.execute("SELECT registrer_artefakttype(%s,%s,1,%s,'sh','sys')",
                   (at, modul, kh))
        da.commit()
    finally:
        da.close()
    sak, logg = _lag_sak(conn, TENANT)
    opp, _ = _lag_oppdrag_type(conn, TENANT, sak, logg, oppdragstype=ot,
                               eiermodul=modul)
    from db.pg import koble
    c = koble(DSN)
    try:
        c.execute("SELECT set_config('disponit.aktor','m37',true),"
                  "       set_config('disponit.request_id','r',true)")
        rad = c.execute("SELECT id FROM claim_neste_oppdrag(%s,%s,%s,300,'r1',"
                        "'staging',0)",
                        (modul, ["purring."], secrets.token_hex(16))).fetchone()
        c.commit()
    finally:
        c.close()
    assert rad is not None and rad[0] == opp, "oppdraget ble ikke claimet"
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
def test_frittstaende_brenner_finnes_ikke(migrator):
    """Codex: den foreldede brenneren er FJERNET, ikke bare ubrukt.

    `bruk_artefaktkapabilitet(jti, uuid)` tok bare en jti og en vilkårlig UUID —
    verken holdende modul eller at artefaktet fantes ble verifisert. Med EXECUTE
    til runtime kunne en misbrukt tilkobling merke en LEVENDE kapabilitet `brukt`
    med et oppdiktet artefakt-id, hvorpå staged-writen leste den statusen som
    autoritativ og den legitime opplastingen aldri kom inn. Forbruk skjer nå kun
    der artefaktraden faktisk skrives.

    MUTASJONEN SOM DREPER DENNE: gjeninnfør funksjonen (eller GRANT-en).
    """
    n = migrator.execute(
        "SELECT count(*) FROM pg_proc WHERE proname='bruk_artefaktkapabilitet'"
    ).fetchone()[0]
    migrator.rollback()
    assert n == 0, "den frittstående kapabilitetsbrenneren finnes fortsatt"


@pg
def test_atomisk_forbruk_avviser_utlopt_kapabilitet(migrator):
    """Utløpet håndheves der kapabiliteten faktisk brennes (017:145).

    En request kan passere `innlos` rett før utløp og deretter bruke tid på
    kanonisering og kryptering. Etter at den frittstående brenneren er fjernet
    er `lagre_artefakt_staged` det eneste forbrukspunktet, og det er DER — under
    radlåsen — utløpet må nektes.
    """
    from db import kryptering
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    a = _admin()
    try:
        jti = _utsted(a, opp, modul, kh, at)
        _sett_kontekst(a, TENANT)
        key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(a, TENANT)
        ct, nonce = kryptering.krypter(dek, {"r": 1}, TENANT, key_id)
        a.commit()
        # Aldre kapabiliteten forbi utløp. `utloper` er frosset av statusmaskinen,
        # så triggeren skrus av for nøyaktig denne testmutasjonen.
        migrator.execute("ALTER TABLE artefaktkapabilitet DISABLE TRIGGER"
                         " artefaktkapabilitet_overgang")
        migrator.execute("UPDATE artefaktkapabilitet SET utloper=now()"
                         " - interval '1 s' WHERE jti=%s", (jti,))
        migrator.execute("ALTER TABLE artefaktkapabilitet ENABLE TRIGGER"
                         " artefaktkapabilitet_overgang")
        migrator.commit()
        _sett_kontekst(a, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            a.execute("SELECT lagre_artefakt_staged(%s,%s,%s,%s,'r1',1,%s,0,100,"
                      "%s,%s,%s,%s,%s)",
                      (TENANT, opp, at, modul, kh, "h-" + secrets.token_hex(8),
                       ct, nonce, key_id, jti))
        a.rollback()
    finally:
        a.close()
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute("SELECT count(*) FROM artefakt WHERE kapabilitet_jti=%s",
                         (jti,)).fetchone()[0]
    migrator.rollback()
    assert n == 0, "en utløpt kapabilitet fikk skrive et artefakt"


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
