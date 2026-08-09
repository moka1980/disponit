"""PR-012 CP1: migrasjon 011 — statusmaskin, bindingstrigger, append-only.

Verifiserer at de nye triggerne håndhever kontraktene mot en EKTE Postgres
(ikke bare at DDL-en er syntaktisk gyldig): den kontrollerte gjenåpningen
`manuell → venter_godkjenning` (aldri naken flipp), menneskeflyt-overgangene,
`godkjenningsrunde`s livssyklus, og — den skarpeste — bindingstriggeren på
`godkjenningsutfall` som avviser en loggpost fra RIKTIG operasjon men FEIL sak
(det FK-ene ikke fanger, v6 §2). Alt kjøres i fikstur-transaksjonen og rulles
tilbake; de append-only tabellene kan ikke ryddes etterpå.
"""
import pytest

from .test_api import DSN, migrator, miljo  # noqa: F401
from .test_pr010_db import _ctx, T as TEN

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _raises(conn, sql, args=()):
    conn.execute("SAVEPOINT s")
    try:
        conn.execute(sql, args)
        conn.execute("RELEASE SAVEPOINT s")
        return False
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT s")
        return True


def _oppsett(conn):
    """Minimal gyldig sak i `manuell` + nøkkel + loggpost. Returnerer
    (unntak_id, loggpost_id)."""
    _ctx(conn, TEN)
    conn.execute("INSERT INTO tenant_nokler (tenant,key_id,wrapped_dek,aktiv)"
                 " VALUES (%s,'k1',%s,true) ON CONFLICT DO NOTHING",
                 (TEN, b"\x00" * 44))
    lid = conn.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse) VALUES (%s,'h','p','STOPP','[]'::jsonb) RETURNING id",
        (TEN,)).fetchone()[0]
    uid = conn.execute(
        "INSERT INTO unntak (tenant,loggpost_id,handling,kategori,"
        "payload_kryptert,key_id,nonce,maks_auto_forsok_snapshot,policy_versjon,"
        "policy_content_hash,status) VALUES (%s,%s,'faktura.bokfor',"
        "'over_grense',%s,'k1',%s,3,'0.2.0','ph','manuell') RETURNING id",
        (TEN, lid, b"\x00", b"\x00" * 12)).fetchone()[0]
    return uid, lid


@pg
def test_manuell_gjenapning_krever_apen_runde(migrator):
    uid, _ = _oppsett(migrator)
    # Ingen naken flipp: uten en apen runde er gjenåpningen ulovlig.
    assert _raises(migrator, "UPDATE unntak SET status='venter_godkjenning'"
                   " WHERE id=%s", (uid,))
    # Med en apen runde tillates den kontrollerte gjenåpningen.
    migrator.execute(
        "INSERT INTO godkjenningsrunde (tenant,unntak_id,runde,status,"
        "godkjennings_policy_hash,policy_versjon,utloper) VALUES"
        " (%s,%s,1,'apen','gph','0.2.0',now()+interval '1 hour')", (TEN, uid))
    migrator.execute("UPDATE unntak SET status='venter_godkjenning' WHERE id=%s",
                     (uid,))
    # Ulovlig menneskeflyt-overgang avvises.
    assert _raises(migrator, "UPDATE unntak SET status='løst' WHERE id=%s", (uid,))
    # løst/avvist forblir absolutt terminale (regresjon fra 007).
    migrator.rollback()


@pg
def test_intensjonsfelt_uforanderlige(migrator):
    uid, _ = _oppsett(migrator)
    for felt in ("intensjon_pakrevd=true", "hi_integritet_hash='x'",
                 "intensjon_policy_hash='y'"):
        assert _raises(migrator, f"UPDATE unntak SET {felt} WHERE id=%s", (uid,)), \
            felt
    migrator.rollback()


@pg
def test_runde_livssyklus(migrator):
    uid, _ = _oppsett(migrator)
    migrator.execute(
        "INSERT INTO godkjenningsrunde (tenant,unntak_id,runde,status,"
        "godkjennings_policy_hash,policy_versjon,utloper) VALUES"
        " (%s,%s,1,'klar','gph','0.2.0',now()+interval '1 hour')", (TEN, uid))
    # brukt uten decision_operation_id avvises.
    assert _raises(migrator, "UPDATE godkjenningsrunde SET status='brukt'"
                   " WHERE tenant=%s AND unntak_id=%s AND runde=1", (TEN, uid))
    migrator.execute("UPDATE godkjenningsrunde SET status='brukt',"
                     " decision_operation_id='op-1' WHERE tenant=%s AND"
                     " unntak_id=%s AND runde=1", (TEN, uid))
    # brukt er terminal; decision_operation_id uforanderlig.
    assert _raises(migrator, "UPDATE godkjenningsrunde SET status='kansellert'"
                   " WHERE tenant=%s AND unntak_id=%s AND runde=1", (TEN, uid))
    assert _raises(migrator, "UPDATE godkjenningsrunde SET"
                   " decision_operation_id='op-2' WHERE tenant=%s AND"
                   " unntak_id=%s AND runde=1", (TEN, uid))
    # to aktive runder samtidig avvises (delindeks en_aktiv_runde).
    assert _raises(migrator, "INSERT INTO godkjenningsrunde (tenant,unntak_id,"
                   "runde,status,godkjennings_policy_hash,policy_versjon,utloper)"
                   " VALUES (%s,%s,2,'apen','g','0.2.0',now()+interval '1 hour'),"
                   " (%s,%s,3,'apen','g','0.2.0',now()+interval '1 hour')",
                   (TEN, uid, TEN, uid))
    migrator.rollback()


@pg
def test_godkjenningsutfall_binding(migrator):
    uid, _ = _oppsett(migrator)
    # brukt runde med op-1
    migrator.execute(
        "INSERT INTO godkjenningsrunde (tenant,unntak_id,runde,status,"
        "godkjennings_policy_hash,policy_versjon,utloper,decision_operation_id)"
        " VALUES (%s,%s,1,'brukt','gph','0.2.0',now()+interval '1 hour','op-1')",
        (TEN, uid))
    riktig = migrator.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse,idempotency_key) VALUES (%s,'h','p','TILLAT','[]'::jsonb,"
        "'op-1') RETURNING id", (TEN,)).fetchone()[0]
    feil_op = migrator.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse,idempotency_key) VALUES (%s,'h','p','TILLAT','[]'::jsonb,"
        "'ANNEN') RETURNING id", (TEN,)).fetchone()[0]

    def utfall(uid_, lid_, hih="hih"):
        return ("INSERT INTO godkjenningsutfall (tenant,unntak_id,"
                "hi_integritet_hash,policy_hash,decision_operation_id,"
                "motorutfall,beslutning_loggpost_id) VALUES"
                f" (%s,%s,'{hih}','gph','op-1','TILLAT_OUTBOX',%s)"), (TEN, uid_, lid_)

    # Loggpost bærer ikke op-1 → bindingstrigger avviser.
    assert _raises(migrator, *utfall(uid, feil_op))
    # Riktig loggpost + brukt runde → tillatt.
    migrator.execute(*utfall(uid, riktig))
    # Riktig op-id men FEIL sak → bindingskjeden avviser (v6 §2 — det FK-ene
    # ikke fanger).
    lid2 = migrator.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse) VALUES (%s,'h','p','STOPP','[]'::jsonb) RETURNING id",
        (TEN,)).fetchone()[0]
    uid2 = migrator.execute(
        "INSERT INTO unntak (tenant,loggpost_id,handling,kategori,"
        "payload_kryptert,key_id,nonce,maks_auto_forsok_snapshot,policy_versjon,"
        "policy_content_hash,status) VALUES (%s,%s,'x','over_grense',%s,'k1',%s,"
        "3,'0.2.0','ph','manuell') RETURNING id",
        (TEN, lid2, b"\x00", b"\x00" * 12)).fetchone()[0]
    assert _raises(migrator, *utfall(uid2, riktig, hih="hih2"))
    # append-only
    assert _raises(migrator, "UPDATE godkjenningsutfall SET motorutfall='STOPP'"
                   " WHERE tenant=%s AND unntak_id=%s", (TEN, uid))
    migrator.rollback()


@pg
def test_attestasjon_append_only_og_fire_oyne(migrator):
    uid, _ = _oppsett(migrator)
    migrator.execute(
        "INSERT INTO godkjenningsrunde (tenant,unntak_id,runde,status,"
        "godkjennings_policy_hash,policy_versjon,utloper) VALUES"
        " (%s,%s,1,'apen','gph','0.2.0',now()+interval '1 hour')", (TEN, uid))

    def att(jti, bruker="u1"):
        return ("INSERT INTO menneskelig_attestasjon (tenant,unntak_id,runde,"
                "operatorhandling,target_action,bruker_id,rolle,authz_version,"
                "konvoluttversjon,konvolutt_hash,mac,mac_key_id,jti,utloper,"
                "saksversjon) VALUES (%s,%s,1,'godkjenn','faktura.bokfor',%s,"
                "'okonomi',1,1,'kh','mac','mk1',%s,now()+interval '1 hour',1)"), \
               (TEN, uid, bruker, jti)

    aid = migrator.execute(att("j" * 22)[0] + " RETURNING id",
                           att("j" * 22)[1]).fetchone()[0]
    # append-only
    assert _raises(migrator, "UPDATE menneskelig_attestasjon SET mac='x'"
                   " WHERE id=%s", (aid,))
    # fire øyne: samme (sak,runde,bruker) → UNIQUE-brudd
    assert _raises(migrator, *att("k" * 22, bruker="u1"))
    # ny jti, ANNEN bruker → tillatt (den andre godkjenneren)
    migrator.execute(*att("m" * 22, bruker="u2"))
    migrator.rollback()
