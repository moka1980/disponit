"""M-57 utsendingskjeden (056): opprinnelsen, lineagen og signaturtvangen.

Klarsignalets porter 1–12 + funksjonsveiene. Det bærende beviset er
NEGATIVT og tas med direkte DML: ingen gyldig signatur ⇒ ingen
representerbar ATS-utsendelse — uansett hvilken vei noen skriver.

Alle tester konstruerer egen tilstand; ingen delt fixture.
"""
from __future__ import annotations

import os
import secrets

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, TENANT, migrator, miljo  # noqa: F401
from .test_m37 import _sett_kontekst

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")


def _grunnlag(m):
    """TILLAT-loggpost + kryptert payload + beslutningsoppdrag — kjedens
    startpunkt (listen peker på evalueringsoppdraget)."""
    from db import kryptering
    _sett_kontekst(m, TENANT)
    logg = m.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'test','api_token','ih','p@1.0.0/x.y','TILLAT','[]',%s)"
        " RETURNING id", (TENANT, secrets.token_hex(8))).fetchone()[0]
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(m, TENANT)
    ct, nonce = kryptering.krypter(dek, {"m57": True}, TENANT, key_id)
    oid = m.execute(
        "INSERT INTO oppdrag (opprinnelse, tenant, beslutning_loggpost_id,"
        " oppdragstype, handling, eiermodul, payload_kryptert, key_id,"
        " nonce, utforelsesfrist, evidensfrist, koblingsstatus)"
        " VALUES ('beslutning',%s,%s,'kontroll.wcag.nettsted',"
        "'kontroll.wcag.nettsted','m_wcag_audit',%s,%s,%s,"
        " now()+interval '1 hour', now()+interval '1 day','KOBLET')"
        " RETURNING id", (TENANT, logg, ct, key_id, nonce)).fetchone()[0]
    m.commit()
    return int(oid), (ct, key_id, nonce)


def _liste(m, oid, *, serie=None, forrige=None, hash_="h1", antall=3):
    _sett_kontekst(m, TENANT)
    rad = m.execute(
        "INSERT INTO utsendingsliste (tenant, liste_id, utkast_serie,"
        " forrige_liste_id, oppdrag_id, listetype, malversjon,"
        " innhold_hash, antall)"
        " VALUES (%s, gen_random_uuid(), %s, %s, %s, 'invitasjon', 'm@1',"
        " %s, %s) RETURNING liste_id, utkast_serie, innhold_hash",
        (TENANT, serie or __import__("uuid").uuid4(), forrige, oid,
         hash_, antall)).fetchone()
    m.commit()
    return rad


def _signatar(m):
    _sett_kontekst(m, TENANT)
    bid = m.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://m57.test', %s) RETURNING bruker_id",
        ("s-" + secrets.token_hex(6),)).fetchone()[0]
    m.commit()
    return bid


def _signer(m, liste, bid, *, nokkel=None):
    _sett_kontekst(m, TENANT)
    m.execute(
        "INSERT INTO utsendingssignatur (tenant, liste_id, utkast_serie,"
        " innhold_hash, signatar, operasjonsnokkel)"
        " VALUES (%s,%s,%s,%s,%s,%s)",
        (TENANT, liste[0], liste[1], liste[2], bid,
         nokkel or "s-" + secrets.token_hex(6)))
    m.commit()


def _frigi(m, liste, *, mottaker="m1"):
    _sett_kontekst(m, TENANT)
    fid = m.execute(
        "INSERT INTO utsendingsfrigivelse (tenant, frigivelse_id,"
        " liste_id, innhold_hash, utkast_serie, mottaker_ref)"
        " VALUES (%s, gen_random_uuid(), %s, %s, %s, %s)"
        " RETURNING frigivelse_id",
        (TENANT, liste[0], liste[2], liste[1], mottaker)).fetchone()[0]
    m.commit()
    return fid


def _ats_oppdrag(m, fid, payload):
    ct, key_id, nonce = payload
    _sett_kontekst(m, TENANT)
    oid = m.execute(
        "INSERT INTO oppdrag (opprinnelse, tenant, frigivelse_id,"
        " oppdragstype, handling, eiermodul, payload_kryptert, key_id,"
        " nonce, utforelsesfrist, evidensfrist, koblingsstatus)"
        " VALUES ('frigivelse',%s,%s,'rekruttering.utsending',"
        "'rekruttering.utsending','m57_ats',%s,%s,%s,"
        " now()+interval '4 hours', now()+interval '1 day','KOBLET')"
        " RETURNING id", (TENANT, fid, ct, key_id, nonce)).fetchone()[0]
    m.commit()
    return int(oid)


# ---------------------------------------------------------------------------
# Portene 1–3 + 5: opprinnelsesformen


@pg
def test_frigivelsesoppdrag_uten_frigivelse_avvises(migrator):
    """Port 1: opprinnelse='frigivelse' uten frigivelse_id → CHECK-avvist,
    med direkte DML — formen håndheves av basen, ikke av funksjonen."""
    oid, payload = _grunnlag(migrator)
    ct, key_id, nonce = payload
    _sett_kontekst(migrator, TENANT)
    logg = migrator.execute(
        "SELECT beslutning_loggpost_id FROM oppdrag WHERE tenant=%s"
        " AND id=%s", (TENANT, oid)).fetchone()[0]
    # Raden er ellers gyldig (KOBLET m/ beslutningsloggpost), men
    # opprinnelsen sier 'frigivelse' uten frigivelse_id: ingen arm i
    # totalformen passer. BEFORE-triggere kan nå å si nei først —
    # begge er lagringens avvisning av samme rad.
    with pytest.raises((psycopg.errors.CheckViolation,
                        psycopg.errors.RaiseException)):
        migrator.execute(
            "INSERT INTO oppdrag (opprinnelse, tenant,"
            " beslutning_loggpost_id, oppdragstype,"
            " handling, eiermodul, payload_kryptert, key_id, nonce,"
            " utforelsesfrist, evidensfrist, koblingsstatus)"
            " VALUES ('frigivelse',%s,%s,'rekruttering.utsending','h','e',"
            " %s,%s,%s, now()+interval '1 hour', now()+interval '1 day',"
            " 'KOBLET')", (TENANT, logg, ct, key_id, nonce))
    migrator.rollback()


@pg
def test_frigivelse_pa_annen_opprinnelse_avvises(migrator):
    """Port 2: frigivelse_id på 'beslutning' → CHECK-avvist. Referansen
    hører til ÉN arm, og totalformen tar stilling i alle."""
    oid, payload = _grunnlag(migrator)
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    ct, key_id, nonce = payload
    _sett_kontekst(migrator, TENANT)
    logg = migrator.execute(
        "SELECT beslutning_loggpost_id FROM oppdrag WHERE tenant=%s"
        " AND id=%s", (TENANT, oid)).fetchone()[0]
    with pytest.raises((psycopg.errors.CheckViolation,
                        psycopg.errors.RaiseException)):
        migrator.execute(
            "INSERT INTO oppdrag (opprinnelse, tenant,"
            " beslutning_loggpost_id, frigivelse_id, oppdragstype,"
            " handling, eiermodul, payload_kryptert, key_id, nonce,"
            " utforelsesfrist, evidensfrist, koblingsstatus)"
            " VALUES ('beslutning',%s,%s,%s,'kontroll.wcag.nettsted',"
            "'kontroll.wcag.nettsted','m_wcag_audit',%s,%s,%s,"
            " now()+interval '1 hour', now()+interval '1 day','KOBLET')",
            (TENANT, logg, fid, ct, key_id, nonce))
    migrator.rollback()


@pg
def test_opprinnelsesreferansene_er_fodselsattributter(migrator):
    """Port 3 — VERIFISERT, ikke antatt: kolonnelåsen er en eksplisitt
    liste, og klarsignalets «gratis»-antakelse var feil målt mot basen
    (038 glemte beslutning_loggpost_id — runtime kunne repeke
    opprinnelsen til et beslutningsoppdrag). Begge referansene avvises
    nå, også for eieren."""
    oid, payload = _grunnlag(migrator)
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    aoid = _ats_oppdrag(migrator, fid, payload)
    fid2 = _frigi(migrator, liste, mottaker="m2")
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute(
            "UPDATE oppdrag SET frigivelse_id=%s WHERE tenant=%s AND id=%s",
            (fid2, TENANT, aoid))
    migrator.rollback()
    _sett_kontekst(migrator, TENANT)
    logg2 = migrator.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'test','api_token','ih','p@1.0.0/x.y','TILLAT','[]',%s)"
        " RETURNING id", (TENANT, secrets.token_hex(8))).fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute(
            "UPDATE oppdrag SET beslutning_loggpost_id=%s"
            " WHERE tenant=%s AND id=%s", (logg2, TENANT, oid))
    migrator.rollback()


@pg
def test_de_gamle_armene_star_urort(migrator):
    """Port 5: regresjon på begge eksisterende opphavsveier gjennom
    swappen — beslutningsoppdraget i `_grunnlag` er selve beviset for
    beslutningsarmen; m37-armen måles av test_outbox_bestillings egne
    porter mot samme base. Her: beslutningsarmen tar fortsatt IKKE en
    frigivelse, og frigivelsesarmen tar ikke en beslutningsloggpost."""
    oid, payload = _grunnlag(migrator)
    ct, key_id, nonce = payload
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    _sett_kontekst(migrator, TENANT)
    logg = migrator.execute(
        "SELECT beslutning_loggpost_id FROM oppdrag WHERE tenant=%s"
        " AND id=%s", (TENANT, oid)).fetchone()[0]
    with pytest.raises((psycopg.errors.CheckViolation,
                        psycopg.errors.RaiseException)):
        migrator.execute(
            "INSERT INTO oppdrag (opprinnelse, tenant,"
            " beslutning_loggpost_id, frigivelse_id, oppdragstype,"
            " handling, eiermodul, payload_kryptert, key_id, nonce,"
            " utforelsesfrist, evidensfrist, koblingsstatus)"
            " VALUES ('frigivelse',%s,%s,%s,'rekruttering.utsending','h',"
            "'e',%s,%s,%s, now()+interval '1 hour',"
            " now()+interval '1 day','KOBLET')",
            (TENANT, logg, fid, ct, key_id, nonce))
    migrator.rollback()


# ---------------------------------------------------------------------------
# Portene 6–7: signaturtvangen


@pg
def test_frigivelse_uten_signatur_er_ikke_representerbar(migrator):
    """Port 6: FK-kjeden krever signaturen — direkte DML."""
    oid, _ = _grunnlag(migrator)
    liste = _liste(migrator, oid)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        migrator.execute(
            "INSERT INTO utsendingsfrigivelse (tenant, frigivelse_id,"
            " liste_id, innhold_hash, utkast_serie, mottaker_ref)"
            " VALUES (%s, gen_random_uuid(), %s, %s, %s, 'm1')",
            (TENANT, liste[0], liste[2], liste[1]))
    migrator.rollback()


@pg
def test_ingen_gyldig_signatur_ingen_ats_utsendelse(migrator):
    """Port 7, hele kjeden med direkte DML: uten signatur finnes ingen
    frigivelse; uten frigivelse finnes ingen frigivelses-referanse et
    ATS-oppdrag kan bære (FK), og uten den avviser CHECK-en oppdraget.
    Signaturens egen FK avviser dessuten en signatur på en hash listen
    aldri har hatt — signaturen binder INNHOLDET, ikke navnet."""
    oid, payload = _grunnlag(migrator)
    ct, key_id, nonce = payload
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _sett_kontekst(migrator, TENANT)
    # Signatur på et ANNET innhold enn versjonens → FK-avvist.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        migrator.execute(
            "INSERT INTO utsendingssignatur (tenant, liste_id,"
            " utkast_serie, innhold_hash, signatar, operasjonsnokkel)"
            " VALUES (%s,%s,%s,'en-annen-hash',%s,%s)",
            (TENANT, liste[0], liste[1], bid, secrets.token_hex(6)))
    migrator.rollback()
    # ATS-oppdrag med oppdiktet frigivelses-referanse → FK-avvist.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        migrator.execute(
            "INSERT INTO oppdrag (opprinnelse, tenant, frigivelse_id,"
            " oppdragstype, handling, eiermodul, payload_kryptert,"
            " key_id, nonce, utforelsesfrist, evidensfrist,"
            " koblingsstatus)"
            " VALUES ('frigivelse',%s, gen_random_uuid(),"
            "'rekruttering.utsending','h','e',%s,%s,%s,"
            " now()+interval '1 hour', now()+interval '1 day','KOBLET')",
            (TENANT, ct, key_id, nonce))
    migrator.rollback()
    # Positiv kontroll: med signaturen står hele kjeden.
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    aoid = _ats_oppdrag(migrator, fid, payload)
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT opprinnelse, frigivelse_id FROM oppdrag WHERE tenant=%s"
        " AND id=%s", (TENANT, aoid)).fetchone()
    migrator.rollback()
    assert rad == ("frigivelse", fid)


# ---------------------------------------------------------------------------
# Portene 8–12: lineage


@pg
def test_listeversjonen_er_append_only(migrator):
    """Port 8: UPDATE og DELETE på en versjon → trigger-avvist. Raden ER
    innholdet signaturen binder."""
    oid, _ = _grunnlag(migrator)
    liste = _liste(migrator, oid)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "UPDATE utsendingsliste SET antall=4 WHERE tenant=%s AND"
            " liste_id=%s", (TENANT, liste[0]))
    migrator.rollback()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "DELETE FROM utsendingsliste WHERE tenant=%s AND liste_id=%s",
            (TENANT, liste[0]))
    migrator.rollback()


@pg
def test_forelder_i_annen_serie_avvises(migrator):
    """Port 9: FK-en går på serienøkkelen — en versjon kan ikke adoptere
    en annen series historikk."""
    oid, _ = _grunnlag(migrator)
    a = _liste(migrator, oid, hash_="ha")
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        migrator.execute(
            "INSERT INTO utsendingsliste (tenant, liste_id, utkast_serie,"
            " forrige_liste_id, oppdrag_id, listetype, malversjon,"
            " innhold_hash, antall)"
            " VALUES (%s, gen_random_uuid(), gen_random_uuid(), %s, %s,"
            " 'invitasjon','m@1','hb',3)", (TENANT, a[0], oid))
    migrator.rollback()


@pg
def test_to_barn_av_samme_forelder_avvises(migrator):
    """Port 10: lineariteten — én vinner, taperen får unik-konflikt."""
    oid, _ = _grunnlag(migrator)
    rot = _liste(migrator, oid, hash_="h-rot")
    _liste(migrator, oid, serie=rot[1], forrige=rot[0], hash_="h-b1")
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.UniqueViolation):
        migrator.execute(
            "INSERT INTO utsendingsliste (tenant, liste_id, utkast_serie,"
            " forrige_liste_id, oppdrag_id, listetype, malversjon,"
            " innhold_hash, antall)"
            " VALUES (%s, gen_random_uuid(), %s, %s, %s,"
            " 'invitasjon','m@1','h-b2',3)",
            (TENANT, rot[1], rot[0], oid))
    migrator.rollback()


@pg
def test_to_rotter_i_samme_serie_avvises(migrator):
    """Port 11: nøyaktig én rot per serie."""
    oid, _ = _grunnlag(migrator)
    rot = _liste(migrator, oid, hash_="h-r1")
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.UniqueViolation):
        migrator.execute(
            "INSERT INTO utsendingsliste (tenant, liste_id, utkast_serie,"
            " oppdrag_id, listetype, malversjon, innhold_hash, antall)"
            " VALUES (%s, gen_random_uuid(), %s, %s,"
            " 'invitasjon','m@1','h-r2',3)", (TENANT, rot[1], oid))
    migrator.rollback()


@pg
def test_en_signert_versjon_per_serie(migrator):
    """Port 12: den andre signaturen i samme serie avvises — og en
    signatur som oppgir en annen serie enn listens, avvises av FK-en."""
    oid, _ = _grunnlag(migrator)
    bid = _signatar(migrator)
    rot = _liste(migrator, oid, hash_="h-s1")
    barn = _liste(migrator, oid, serie=rot[1], forrige=rot[0], hash_="h-s2")
    _signer(migrator, rot, bid)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.UniqueViolation):
        migrator.execute(
            "INSERT INTO utsendingssignatur (tenant, liste_id,"
            " utkast_serie, innhold_hash, signatar, operasjonsnokkel)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (TENANT, barn[0], barn[1], barn[2], bid,
             secrets.token_hex(6)))
    migrator.rollback()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        migrator.execute(
            "INSERT INTO utsendingssignatur (tenant, liste_id,"
            " utkast_serie, innhold_hash, signatar, operasjonsnokkel)"
            " VALUES (%s,%s, gen_random_uuid(), %s,%s,%s)",
            (TENANT, barn[0], barn[2], bid, secrets.token_hex(6)))
    migrator.rollback()


# ---------------------------------------------------------------------------
# Funksjonsveiene (port 4 + SP-2)


@pg
def test_funksjonskjeden_ende_til_ende_med_replay(migrator):
    """Positiv kontroll for funksjonsveien + SP-2: opprett → signer →
    frigi → frigivelsesoppdrag; identisk replay er no-op, samme nøkkel
    med annet innhold (også en ANNEN SIGNATAR — 055-regelen) avvises."""
    oid, payload = _grunnlag(migrator)
    ct, key_id, nonce = payload
    bid, bid2 = _signatar(migrator), _signatar(migrator)
    _sett_kontekst(migrator, TENANT)
    serie = __import__("uuid").uuid4()
    liste_id = migrator.execute(
        "SELECT opprett_utsendingsliste(%s,%s,NULL,%s,'invitasjon','m@1',"
        "'h-fn',2)", (TENANT, serie, oid)).fetchone()[0]
    nk = "sig-" + secrets.token_hex(6)
    migrator.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                     (TENANT, liste_id, bid, nk))
    # Grunnlaget committes FØR den negative proben — rollbacken etter det
    # FORVENTEDE nei-et skal kaste probens virkning, aldri signaturen.
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    # Identisk replay → no-op; annet innhold/annen signatar → avvist.
    migrator.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                     (TENANT, liste_id, bid, nk))
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        migrator.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                         (TENANT, liste_id, bid2, nk))
    migrator.rollback()
    _sett_kontekst(migrator, TENANT)
    fid = migrator.execute("SELECT frigi_utsendelse(%s,%s,'fn-m1')",
                           (TENANT, liste_id)).fetchone()[0]
    # Idempotent per mottaker: samme kall gir samme frigivelse.
    fid2 = migrator.execute("SELECT frigi_utsendelse(%s,%s,'fn-m1')",
                            (TENANT, liste_id)).fetchone()[0]
    assert fid == fid2
    aoid = migrator.execute(
        "SELECT opprett_frigivelsesoppdrag(%s,%s,'rekruttering.utsending',"
        "'rekruttering.utsending','m57_ats',%s,%s,%s,"
        " now()+interval '4 hours', now()+interval '1 day')",
        (TENANT, fid, ct, key_id, nonce)).fetchone()[0]
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT opprinnelse, frigivelse_id, antall FROM oppdrag o"
        " JOIN utsendingsliste l ON l.tenant=o.tenant"
        "  AND l.liste_id=%s WHERE o.tenant=%s AND o.id=%s",
        (liste_id, TENANT, aoid)).fetchone()
    migrator.rollback()
    assert rad == ("frigivelse", fid, 2)


@pg
def test_funksjonene_er_eneste_vei_for_ordinaere_roller(migrator):
    """Port 4, grant-halvdelen: runtime har SELECT men ikke INSERT på
    kjedetabellene — skrivingen går gjennom funksjonene. (Den statiske
    halvdelen — at ingen annen kodevei setter opprinnelsen — måles av
    modultestene når modulen kommer; i dag finnes ingen kodevei.)"""
    from db.pg import koble
    oid, _ = _grunnlag(migrator)
    liste = _liste(migrator, oid)
    rt = koble(DSN)
    try:
        rt.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute(
                "INSERT INTO utsendingsfrigivelse (tenant, frigivelse_id,"
                " liste_id, innhold_hash, utkast_serie, mottaker_ref)"
                " VALUES (%s, gen_random_uuid(), %s, %s, %s, 'x')",
                (TENANT, liste[0], liste[2], liste[1]))
        rt.rollback()
        rt.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute(
                "INSERT INTO utsendingsliste (tenant, liste_id,"
                " utkast_serie, oppdrag_id, listetype, malversjon,"
                " innhold_hash, antall) VALUES (%s, gen_random_uuid(),"
                " gen_random_uuid(), %s, 'invitasjon','m@1','hx',1)",
                (TENANT, oid))
        rt.rollback()
    finally:
        rt.close()
