"""M-57 utsendingskjeden (056): opprinnelsen, lineagen og signaturtvangen.

Klarsignalets porter 1–12 + funksjonsveiene. Det bærende beviset er
NEGATIVT og tas med direkte DML: ingen gyldig signatur ⇒ ingen
representerbar ATS-utsendelse — uansett hvilken vei noen skriver.

Alle tester konstruerer egen tilstand; ingen delt fixture.
"""
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

import psycopg
import pytest

from .test_api import (ANNEN_TENANT, DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       migrator, miljo)
from .test_m37 import _sett_kontekst

VARSEL_DSN = os.environ.get("DISPONIT_TEST_VARSEL_DSN")


def _rt():
    from db.pg import koble
    return koble(DSN)


def _sender():
    """Utsendingsveiens EGEN innlogging (varselsenderen) — 056 gir
    EXECUTE dit og ingen andre steder; lokalt uten rollen faller vi til
    eierens SET ROLE, som i _drill."""
    from db.pg import koble
    if VARSEL_DSN:
        return koble(VARSEL_DSN)
    k = koble(MIGRATOR_DSN)
    k.execute("SET ROLE disponit_m37_claimer")
    return k

ROT = Path(__file__).resolve().parents[3]

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")


def _grunnlag(m, *, oppdragstype="kontroll.wcag.nettsted", status=None):
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
        " VALUES ('beslutning',%s,%s,%s,%s,'m_wcag_audit',%s,%s,%s,"
        " now()+interval '1 hour', now()+interval '1 day','KOBLET')"
        " RETURNING id",
        (TENANT, logg, oppdragstype, oppdragstype, ct, key_id,
         nonce)).fetchone()[0]
    if status:
        # Lovlig vei gjennom `oppdrag_kolonnelaas`' statusmaskin:
        # opprettet → plukket → utfort, og opprettet → kansellert.
        for steg in (("plukket", "utfort") if status == "utfort"
                     else (status,)):
            m.execute("UPDATE oppdrag SET status=%s WHERE tenant=%s"
                      " AND id=%s", (steg, TENANT, oid))
    m.commit()
    return int(oid), (ct, key_id, nonce)


def _evaluering(m):
    """Et FULLFØRT `rekruttering.evaluering`-oppdrag — det ENESTE en
    liste kan promotere (klarsignal §1 + §7/port 28)."""
    return _grunnlag(m, oppdragstype="rekruttering.evaluering",
                     status="utfort")


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


def _signatar(m, *, medlem=True, tenant=None, aktiv=True):
    """En brukeridentitet — med AKTIVT medlemskap i tenanten som standard
    (signaturporten krever det); `medlem=False`, `aktiv=False` eller
    annen tenant for de negative stiene."""
    _sett_kontekst(m, TENANT)
    bid = m.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://m57.test', %s) RETURNING bruker_id",
        ("s-" + secrets.token_hex(6),)).fetchone()[0]
    if medlem:
        # RLS på brukermedlemskap: WITH CHECK krever at konteksten ER
        # tenanten det skrives for — settes eksplisitt for fremmed-stien.
        _sett_kontekst(m, tenant or TENANT)
        m.execute(
            "INSERT INTO brukermedlemskap (tenant, bruker_id, roller,"
            " aktiv) VALUES (%s,%s,%s,%s)",
            (tenant or TENANT, bid, ["admin"], aktiv))
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
def test_frigivelsen_er_append_only(migrator):
    """Runde 3-beslutning (K2 utløst på #140): §5s TTL-sletting av
    `mottaker_ref` hører til TTL-kontrollpunktet (portene 18–20), ikke
    til CP1 — reaperen (funksjon + rolle) finnes ikke ennå. Runde 2s
    nullbare kolonne brøt idempotensen (NULL ≠ NULL under unik-nøkkelen),
    og to runder på samme kolonne for to ulike krav er et
    spesifikasjonsvalg, ikke et formforsøk. `mottaker_ref` er derfor
    NOT NULL igjen, og frigivelsen er en REN append-only-tabell: DELETE
    og enhver omskriving avvises, uten unntak."""
    oid, _ = _grunnlag(migrator)
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    hvor = "WHERE tenant=%s AND frigivelse_id=%s"
    for setning in (
            f"DELETE FROM utsendingsfrigivelse {hvor}",
            f"UPDATE utsendingsfrigivelse SET mottaker_ref='en-annen' {hvor}",
            f"UPDATE utsendingsfrigivelse SET liste_id=gen_random_uuid()"
            f" {hvor}"):
        _sett_kontekst(migrator, TENANT)
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(setning, (TENANT, fid))
        migrator.rollback()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.NotNullViolation):
        migrator.execute(
            "INSERT INTO utsendingsfrigivelse (tenant, frigivelse_id,"
            " liste_id, innhold_hash, utkast_serie, mottaker_ref)"
            " VALUES (%s, gen_random_uuid(), %s, %s, %s, NULL)",
            (TENANT, liste[0], liste[2], liste[1]))
    migrator.rollback()


@pg
def test_kjedetabellene_taaler_ikke_truncate(migrator):
    """Codex P2 (runde 1, aldri lukket): TRUNCATE fyrer INGEN radtrigger.
    Uten en statement-vakt kunne tabelleieren tømt hele bevisrekken —
    signerte lister, signaturer og frigivelser — uten å møte
    `avvis_endring` en eneste gang.

    CASCADE så FK-sperren ikke skygger for vakten som faktisk prøves:
    BEFORE TRUNCATE fyrer først. Kontroll: fjern `*_ingen_truncate` i
    056, så blir denne rød."""
    _sett_kontekst(migrator, TENANT)
    for tabell in ("utsendingsfrigivelse", "utsendingssignatur",
                   "utsendingsliste"):
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(f"TRUNCATE {tabell} CASCADE")
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
    oid, payload = _evaluering(migrator)
    ct, key_id, nonce = payload
    bid, bid2 = _signatar(migrator), _signatar(migrator)
    # Kallene går som de EKTE rollene: runtime lager og signerer (API-
    # veien), senderen frigir og legger ut oppdraget — migrator er
    # utestengt fra funksjonene, og det er en del av det som måles.
    rt = _rt()
    snd = _sender()
    _sett_kontekst(rt, TENANT)
    serie = __import__("uuid").uuid4()
    liste_id = rt.execute(
        "SELECT opprett_utsendingsliste(%s,%s,NULL,%s,'invitasjon','m@1',"
        "'h-fn',2)", (TENANT, serie, oid)).fetchone()[0]
    nk = "sig-" + secrets.token_hex(6)
    rt.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
               (TENANT, liste_id, bid, nk))
    # Grunnlaget committes FØR den negative proben — rollbacken etter det
    # FORVENTEDE nei-et skal kaste probens virkning, aldri signaturen.
    rt.commit()
    _sett_kontekst(rt, TENANT)
    # Identisk replay → no-op; annet innhold/annen signatar → avvist.
    rt.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
               (TENANT, liste_id, bid, nk))
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        rt.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                   (TENANT, liste_id, bid2, nk))
    rt.rollback()
    rt.close()
    _sett_kontekst(snd, TENANT)
    fid = snd.execute("SELECT frigi_utsendelse(%s,%s,'fn-m1')",
                      (TENANT, liste_id)).fetchone()[0]
    # Idempotent per mottaker: samme kall gir samme frigivelse.
    fid2 = snd.execute("SELECT frigi_utsendelse(%s,%s,'fn-m1')",
                       (TENANT, liste_id)).fetchone()[0]
    assert fid == fid2
    aoid = snd.execute(
        "SELECT opprett_frigivelsesoppdrag(%s,%s,'rekruttering.utsending',"
        "'rekruttering.utsending','m57_ats',%s,%s,%s,"
        " now()+interval '4 hours', now()+interval '1 day')",
        (TENANT, fid, ct, key_id, nonce)).fetchone()[0]
    snd.commit()
    snd.close()
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT opprinnelse, frigivelse_id, antall FROM oppdrag o"
        " JOIN utsendingsliste l ON l.tenant=o.tenant"
        "  AND l.liste_id=%s WHERE o.tenant=%s AND o.id=%s",
        (liste_id, TENANT, aoid)).fetchone()
    migrator.rollback()
    assert rad == ("frigivelse", fid, 2)


@pg
def test_liste_krever_fullfort_evalueringsoppdrag(migrator):
    """Codex P1 (runde 2), klarsignalets port 28: en avbrutt kjøring skal
    ikke kunne promoteres. Retningsporten alene (opprinnelsen) slapp
    fortsatt gjennom feil oppdragstype, en kjøring som fortsatt går, og
    en som ble kansellert — og derfra bar kjeden videre gjennom signatur
    og frigivelse."""
    feil_type, _ = _grunnlag(migrator)
    gar_fortsatt, _ = _grunnlag(migrator,
                                oppdragstype="rekruttering.evaluering")
    avbrutt, _ = _grunnlag(migrator, oppdragstype="rekruttering.evaluering",
                           status="kansellert")
    fullfort, _ = _evaluering(migrator)
    uuid = __import__("uuid")
    rt = _rt()
    try:
        for oid in (feil_type, gar_fortsatt, avbrutt):
            _sett_kontekst(rt, TENANT)
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                rt.execute(
                    "SELECT opprett_utsendingsliste(%s,%s,NULL,%s,"
                    "'invitasjon','m@1','h-neg',2)",
                    (TENANT, uuid.uuid4(), oid))
            rt.rollback()
        # Positiv kontroll: den fullførte evalueringen promoteres.
        _sett_kontekst(rt, TENANT)
        rt.execute(
            "SELECT opprett_utsendingsliste(%s,%s,NULL,%s,'invitasjon',"
            "'m@1','h-pos',2)", (TENANT, uuid.uuid4(), fullfort))
        rt.rollback()
    finally:
        rt.close()


@pg
def test_signatar_uten_aktivt_medlemskap_avvises(migrator):
    """Codex P1 + Cursor P1-1 (runde 2): signaturen ER den menneskelige
    autorisasjonen for en irreversibel utsending, men FK-en mot
    `brukeridentitet` er GLOBAL. Uten medlemskapsporten kunne runtime —
    som selv har INSERT på `brukeridentitet` — tilskrive signaturen en
    fabrikkert bruker, en avskrudd bruker eller en bruker i en ANNEN
    tenant. Alle tre skal falle; et aktivt medlem består."""
    oid, _ = _grunnlag(migrator)
    liste = _liste(migrator, oid)
    fabrikkert = _signatar(migrator, medlem=False)
    avskrudd = _signatar(migrator, aktiv=False)
    fremmed = _signatar(migrator, tenant=ANNEN_TENANT)
    rt = _rt()
    try:
        for bid in (fabrikkert, avskrudd, fremmed):
            _sett_kontekst(rt, TENANT)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rt.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                           (TENANT, liste[0], bid,
                            "n-" + secrets.token_hex(6)))
            rt.rollback()
        # Positiv kontroll: aktivt medlemskap i DENNE tenanten går gjennom.
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                   (TENANT, liste[0], _signatar(migrator),
                    "n-" + secrets.token_hex(6)))
        rt.rollback()
    finally:
        rt.close()


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


@pg
def test_en_frigivelse_gir_ett_oppdrag(migrator):
    """Cursor P1 på #140: utsendelsen er irreversibel, så kardinaliteten
    én frigivelse -> ett oppdrag er en sikkerhetsinvariant — samme form
    som beslutningsveiens `oppdrag_en_per_beslutning`. Direkte DML på
    duplikatet avvises av indeksen; funksjonens andre kall er idempotent
    og gir VINNERENS oppdrag tilbake, aldri et nytt."""
    oid, payload = _grunnlag(migrator)
    ct, key_id, nonce = payload
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    aoid = _ats_oppdrag(migrator, fid, payload)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.UniqueViolation):
        migrator.execute(
            "INSERT INTO oppdrag (opprinnelse, tenant, frigivelse_id,"
            " oppdragstype, handling, eiermodul, payload_kryptert,"
            " key_id, nonce, utforelsesfrist, evidensfrist,"
            " koblingsstatus)"
            " VALUES ('frigivelse',%s,%s,'rekruttering.utsending','h',"
            "'e',%s,%s,%s, now()+interval '1 hour',"
            " now()+interval '1 day','KOBLET')",
            (TENANT, fid, ct, key_id, nonce))
    migrator.rollback()
    snd = _sender()
    try:
        _sett_kontekst(snd, TENANT)
        aoid2 = snd.execute(
            "SELECT opprett_frigivelsesoppdrag(%s,%s,"
            "'rekruttering.utsending','rekruttering.utsending','m57_ats',"
            "%s,%s,%s, now()+interval '4 hours', now()+interval '1 day')",
            (TENANT, fid, ct, key_id, nonce)).fetchone()[0]
        snd.rollback()
    finally:
        snd.close()
    assert int(aoid2) == aoid, "retryet lagde et NYTT oppdrag"


@pg
def test_frigivelse_kan_ikke_overstige_signert_antall(migrator):
    """Codex P1 (runde 2): unikheten på (liste, mottaker) hindret bare
    DUBLETTER — ikke at senderen frigir flere FORSKJELLIGE mottakere enn
    listen er signert for. «Dette sender N e-poster. Kan ikke angres.» må
    holde: mottaker N+1 krever en ny signert versjon. Et replay på en
    allerede frigitt mottaker skal fortsatt svare idempotent, også når
    listen står på taket."""
    oid, _ = _grunnlag(migrator)
    bid = _signatar(migrator)
    liste = _liste(migrator, oid, antall=2)
    _signer(migrator, liste, bid)
    snd = _sender()
    try:
        _sett_kontekst(snd, TENANT)
        forste = snd.execute("SELECT frigi_utsendelse(%s,%s,'tak-m1')",
                             (TENANT, liste[0])).fetchone()[0]
        snd.execute("SELECT frigi_utsendelse(%s,%s,'tak-m2')",
                    (TENANT, liste[0]))
        snd.commit()
    finally:
        snd.close()
    # Egen tilkobling per steg: `_sender()` faller lokalt tilbake på
    # migrator + SET ROLE, og en rollback ville tatt rollen med seg.
    snd = _sender()
    try:
        _sett_kontekst(snd, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            snd.execute("SELECT frigi_utsendelse(%s,%s,'tak-m3')",
                        (TENANT, liste[0]))
        snd.rollback()
    finally:
        snd.close()
    snd = _sender()
    try:
        _sett_kontekst(snd, TENANT)
        replay = snd.execute("SELECT frigi_utsendelse(%s,%s,'tak-m1')",
                             (TENANT, liste[0])).fetchone()[0]
        snd.rollback()
    finally:
        snd.close()
    assert replay == forste, "replay på taket skal fortsatt være idempotent"


@pg
def test_frigi_er_idempotent_under_kapplop(migrator):
    """Cursor P2 på #140: SELECT-så-INSERT lot kappløpstaperen dø på
    unik-bruddet. Nå får taperen vinnerens frigivelse: A setter inn uten
    å committe; B (egen tilkobling) kaller funksjonen og blokkerer på
    indeksen til A committer — og skal da returnere A-radens id."""
    import threading

    oid, _ = _grunnlag(migrator)
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    a = _sender()
    b = _sender()
    try:
        _sett_kontekst(a, TENANT)
        fid_a = a.execute("SELECT frigi_utsendelse(%s,%s,'kapp-m1')",
                          (TENANT, liste[0])).fetchone()[0]
        resultat: dict = {}

        def taper():
            _sett_kontekst(b, TENANT)
            resultat["fid"] = b.execute(
                "SELECT frigi_utsendelse(%s,%s,'kapp-m1')",
                (TENANT, liste[0])).fetchone()[0]
            b.commit()

        t = threading.Thread(target=taper)
        t.start()
        t.join(timeout=2)
        assert t.is_alive(), "B skulle blokkere på As ucommittede rad"
        a.commit()
        t.join(timeout=10)
        assert not t.is_alive(), "B kom aldri gjennom etter As commit"
        assert resultat["fid"] == fid_a, "taperen fikk en ANNEN frigivelse"
    finally:
        a.close(); b.close()


@pg
def test_frigi_gjenkjenner_mottaker_etter_laasen_selv_med_fullt_tak(migrator):
    """Codex på #140 (runde 3): to FØRSTEGANGS-kall for SAMME mottaker på
    en liste signert for kun ÉN mottaker (antall=1) kan begge bomme på
    oppslaget FØR låsen. Uten gjenlesning ETTER låsen ville taperen møtt
    telleporten (v_frigitt=1 >= antall=1) og blitt avvist der
    idempotens-kontrakten lovte vinnerens id."""
    import threading

    oid, _ = _grunnlag(migrator)
    bid = _signatar(migrator)
    liste = _liste(migrator, oid, antall=1)
    _signer(migrator, liste, bid)
    a = _sender()
    b = _sender()
    try:
        _sett_kontekst(a, TENANT)
        fid_a = a.execute("SELECT frigi_utsendelse(%s,%s,'tak-kapp-m1')",
                          (TENANT, liste[0])).fetchone()[0]
        resultat: dict = {}

        def taper():
            _sett_kontekst(b, TENANT)
            try:
                resultat["fid"] = b.execute(
                    "SELECT frigi_utsendelse(%s,%s,'tak-kapp-m1')",
                    (TENANT, liste[0])).fetchone()[0]
                b.commit()
            except Exception as e:            # noqa: BLE001 — dommen måles
                resultat["feil"] = e
                b.rollback()

        t = threading.Thread(target=taper)
        t.start()
        t.join(timeout=2)
        assert t.is_alive(), "B skulle blokkere på As ucommittede rad"
        a.commit()
        t.join(timeout=10)
        assert not t.is_alive(), "B kom aldri gjennom etter As commit"
        assert "feil" not in resultat, (
            "taperen ble avvist av taket i stedet for å gjenkjenne"
            f" mottakeren: {resultat.get('feil')}")
        assert resultat["fid"] == fid_a, "taperen fikk en ANNEN frigivelse"
    finally:
        a.close(); b.close()


@pg
def test_signer_kapplopstaper_faar_replaydommen(migrator):
    """Cursor P2 på #140: identisk replay er no-op OGSÅ når kallene er
    samtidige — taperen på unik nøkkel går inn i samme dom som en
    sekvensiell replay, og avvikende innhold på nøkkelen skal fortsatt
    høres."""
    import threading

    oid, _ = _grunnlag(migrator)
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    nk = "kapp-" + secrets.token_hex(6)
    a = _rt()
    b = _rt()
    try:
        _sett_kontekst(a, TENANT)
        a.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                  (TENANT, liste[0], bid, nk))
        feil: dict = {}

        def taper():
            _sett_kontekst(b, TENANT)
            try:
                b.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                          (TENANT, liste[0], bid, nk))
                b.commit()
            except Exception as e:            # noqa: BLE001 — dommen måles
                feil["e"] = e
                b.rollback()

        t = threading.Thread(target=taper)
        t.start()
        t.join(timeout=2)
        assert t.is_alive(), "B skulle blokkere på As ucommittede signatur"
        a.commit()
        t.join(timeout=10)
        assert not t.is_alive()
        assert "e" not in feil, f"identisk samtidig replay døde: {feil}"
    finally:
        a.close(); b.close()


def test_migrer_baerer_utsendingskjedens_rettigheter():
    """Cursor P1 på #140: kjøreren er AUTORITATIV — den revoker alt og
    re-granter kun det som står i blokkene, så migrasjonens grants
    overlever ikke ett `migrer.py`-kjør. Kjedens tilganger må stå i
    kjørerens egne blokker: lesing til runtime, liste/signering på
    API-veien, frigivelse/oppdrag hos senderen."""
    tekst = (ROT / "deploy" / "staging" / "migrer.py").read_text(
        encoding="utf-8")
    assert ("GRANT SELECT ON utsendingsliste, utsendingssignatur,"
            " utsendingsfrigivelse TO {rolle};") in tekst
    for fn in ("opprett_utsendingsliste(TEXT, UUID, UUID, BIGINT, TEXT,"
               " TEXT, TEXT, INT)",
               "signer_utsendingsliste(TEXT, UUID, TEXT, TEXT)",
               "frigi_utsendelse(TEXT, UUID, TEXT)",
               "opprett_frigivelsesoppdrag(TEXT, UUID, TEXT, TEXT, TEXT,"
               " BYTEA, TEXT, BYTEA, TIMESTAMPTZ, TIMESTAMPTZ)"):
        assert f"GRANT EXECUTE ON FUNCTION {fn} TO {{rolle}};" in tekst, fn


def test_sp10_daekker_056():
    """Cursor P2-3 på #140 (runde 3): CI kjører allerede
    `sp10-provekjoring.py 56` og skriptet har allerede
    `56: (_seed_056, _mal_056)` registrert — men ingen pytest speilet
    koblingen (48/49 har den, jf. `test_begge_sp10_kjoringene_staar_i_ci`
    og `test_sp10_daekker_049`). Uten en maskinell port kunne bebodd
    SP-10-prøvekjøring for 056 fjernes stille."""
    ci = (ROT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8")
    assert re.search(r"sp10-provekjoring\.py 56\b", ci), (
        "SP-10-prøvekjøringen for 056 mangler i CI")
    sp10 = (ROT / "deploy" / "staging" / "sp10-provekjoring.py").read_text(
        encoding="utf-8")
    assert "56: (_seed_056, _mal_056)" in sp10, (
        "056 har ingen registrert seed+måling")


@pg
def test_frigivelsesoppdragets_retry_maa_beskrive_samme_oppdrag(migrator):
    """Runde 3-beslutning (K2 utløst på #140): materialiteten er snevret
    til de DETERMINISTISKE feltene (oppdragstype/handling/eiermodul/
    key_id). `db/kryptering.py` gir hver kryptering en FERSK tilfeldig
    nonce, så et legitimt retry som krypterer identisk klartekst på nytt
    (etter en tvetydig commit/timeout) ALDRI får samme
    `payload_kryptert`/`nonce` som forrige forsøk — runde 2s fulle
    byte-likhet avviste nøyaktig det retryet den skulle godkjenne. Ekte
    binding til det signerte innholdet er et eget, utsatt spørsmål
    (Funn 8); denne porten dekker bare kappløps-/retry-klassen: samme
    frigivelse + samme jobbtype/håndterer/eiermodul/nøkkel er samme
    logiske utsendelse — mens et retry med ANNEN metadata fortsatt skal
    høres."""
    from db import kryptering
    oid, payload = _grunnlag(migrator)
    ct, key_id, nonce = payload
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    snd = _sender()
    try:
        _sett_kontekst(snd, TENANT)
        aoid = snd.execute(
            "SELECT opprett_frigivelsesoppdrag(%s,%s,"
            "'rekruttering.utsending','rekruttering.utsending','m57_ats',"
            "%s,%s,%s, now()+interval '4 hours', now()+interval '1 day')",
            (TENANT, fid, ct, key_id, nonce)).fetchone()[0]
        snd.commit()
        # identisk chiffertekst, nye frister -> vinnerens id
        _sett_kontekst(snd, TENANT)
        aoid2 = snd.execute(
            "SELECT opprett_frigivelsesoppdrag(%s,%s,"
            "'rekruttering.utsending','rekruttering.utsending','m57_ats',"
            "%s,%s,%s, now()+interval '2 hours', now()+interval '2 day')",
            (TENANT, fid, ct, key_id, nonce)).fetchone()[0]
        assert aoid2 == aoid
        snd.rollback()
        # FERSK kryptering av SAMME klartekst: ny chiffertekst/nonce (AES-
        # GCM randomiserer noncen), men samme deterministiske felter —
        # nøyaktig det ekte retry-scenariet runde 3 fant og lukket.
        _sett_kontekst(migrator, TENANT)
        key_id3, dek = kryptering.hent_eller_opprett_aktiv_dek(
            migrator, TENANT)
        ct3, nonce3 = kryptering.krypter(dek, {"m57": True}, TENANT, key_id3)
        migrator.commit()
        assert (ct3, nonce3) != (ct, nonce), (
            "testen forutsetter at krypteringen faktisk er randomisert")
        _sett_kontekst(snd, TENANT)
        aoid3 = snd.execute(
            "SELECT opprett_frigivelsesoppdrag(%s,%s,"
            "'rekruttering.utsending','rekruttering.utsending','m57_ats',"
            "%s,%s,%s, now()+interval '4 hours', now()+interval '1 day')",
            (TENANT, fid, ct3, key_id3, nonce3)).fetchone()[0]
        assert aoid3 == aoid, "et retry med fersk kryptering lagde et NYTT oppdrag"
        snd.rollback()
        # annen METADATA (annen håndterer) -> fortsatt avvist
        _sett_kontekst(snd, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            snd.execute(
                "SELECT opprett_frigivelsesoppdrag(%s,%s,"
                "'rekruttering.utsending','en-annen-handling',"
                "'m57_ats',%s,%s,%s, now()+interval '4 hours',"
                " now()+interval '1 day')",
                (TENANT, fid, ct, key_id, nonce))
        snd.rollback()
    finally:
        snd.close()


@pg
def test_runtime_har_ikke_utsendingsveien(migrator):
    """Cursor P2 på #140 (runde 2): grant-grensen måles, ikke bare
    tabell-INSERT — runtime skal mangle EXECUTE på frigivelsen og
    frigivelsesoppdraget (utsendingsveien er senderens), og senderen
    skal ha den (positiv kontroll i funksjonskjede-testen)."""
    oid, payload = _grunnlag(migrator)
    ct, key_id, nonce = payload
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT frigi_utsendelse(%s,%s,'rt-m1')",
                       (TENANT, liste[0]))
        rt.rollback()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute(
                "SELECT opprett_frigivelsesoppdrag(%s, gen_random_uuid(),"
                "'rekruttering.utsending','rekruttering.utsending',"
                "'m57_ats',%s,%s,%s, now()+interval '4 hours',"
                " now()+interval '1 day')", (TENANT, ct, key_id, nonce))
        rt.rollback()
    finally:
        rt.close()


@pg
def test_barn_arver_forelderens_evalueringsoppdrag(migrator):
    """Cursor P2 på #140 (runde 3): en «lineær» serie kunne likevel la et
    barn adoptere en ANNEN fullført evaluering enn forelderen sin —
    proveniensen ville forgrene seg inni en kjede klarsignalet beskriver
    som lineær. Barnet arver forelderens evalueringsoppdrag; det velger
    det ikke."""
    e1, _ = _evaluering(migrator)
    e2, _ = _evaluering(migrator)
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        rot = rt.execute(
            "SELECT opprett_utsendingsliste(%s, gen_random_uuid(), NULL,"
            " %s, 'invitasjon','m@1','h-rot-serie',1)",
            (TENANT, e1)).fetchone()[0]
        rot_serie = rt.execute(
            "SELECT utkast_serie FROM utsendingsliste WHERE tenant=%s"
            " AND liste_id=%s", (TENANT, rot)).fetchone()[0]
        rt.commit()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute(
                "SELECT opprett_utsendingsliste(%s, %s, %s, %s,"
                " 'invitasjon','m@1','h-barn-feil',1)",
                (TENANT, rot_serie, rot, e2))
        rt.rollback()
        # positiv kontroll: samme evalueringsoppdrag som forelderen -> OK.
        _sett_kontekst(rt, TENANT)
        rt.execute(
            "SELECT opprett_utsendingsliste(%s, %s, %s, %s,"
            " 'invitasjon','m@1','h-barn-ok',1)",
            (TENANT, rot_serie, rot, e1))
        rt.commit()
    finally:
        rt.close()


@pg
def test_listen_starter_i_et_evalueringsoppdrag(migrator):
    """Cursor P2 på #140 (runde 2): lineagen har RETNING — en liste kan
    aldri startes på et frigivelsesoppdrag (kjeden ville sirklet inn i
    seg selv). Evalueringsoppdrag (beslutning/m37) er de lovlige
    startpunktene — og siden runde 2 må evalueringen dessuten være
    FULLFØRT (se `test_liste_krever_fullfort_evalueringsoppdrag`)."""
    oid, payload = _evaluering(migrator)
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    aoid = _ats_oppdrag(migrator, fid, payload)
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute(
                "SELECT opprett_utsendingsliste(%s, gen_random_uuid(),"
                " NULL, %s, 'invitasjon','m@1','h-sirkel',1)",
                (TENANT, aoid))
        rt.rollback()
        # positiv kontroll: evalueringsoppdraget er lovlig startpunkt.
        _sett_kontekst(rt, TENANT)
        rt.execute(
            "SELECT opprett_utsendingsliste(%s, gen_random_uuid(),"
            " NULL, %s, 'invitasjon','m@1','h-eval',1)", (TENANT, oid))
        rt.rollback()
    finally:
        rt.close()
