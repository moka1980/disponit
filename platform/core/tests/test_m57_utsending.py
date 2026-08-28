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
from .test_m37 import _lag_oppdrag, _lag_sak, _sett_kontekst

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


def _grunnlag(m, *, oppdragstype="rekruttering.evaluering",
              status="utfort", eiermodul=None):
    """TILLAT-loggpost + kryptert payload + beslutningsoppdrag — kjedens
    startpunkt (listen peker på evalueringsoppdraget).

    DEFAULTEN ER DEN PROMOTERBARE (Cursor P2, runde 7 på #140): med
    `utsendingsliste_promotering` er «fullført rekruttering.evaluering»
    en SKJEMApåstand, så en liste kan ikke lenger plantes på et
    WCAG-oppdrag med direkte DML. Testoppsettet brukte tidligere
    nettopp det hullet, og lot dermed hver lineage-/signatur-/
    frigivelsestest bevise sitt eget funn på et grunnlag kjeden aldri
    skal ha hatt. De negative stiene oppgir nå avviket EKSPLISITT.

    EIERMODULEN FØLGER TYPEN (Cursor P2/P3): hjelperen skrev
    `m_wcag_audit` på ALT, også på `rekruttering.evaluering`-oppdrag —
    et par kontrakten aldri kan produsere (`_eiermodul_for` binder dem
    ved opprettelsen). Fasiten hentes derfor fra kontrakten, og en test
    som VIL ha avviket oppgir det eksplisitt."""
    from db import kryptering
    from oppdragskontrakt import type_for_handling
    if eiermodul is None:
        kontrakt = type_for_handling(oppdragstype)
        eiermodul = kontrakt.eiermodul if kontrakt else "m_wcag_audit"
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
        " VALUES ('beslutning',%s,%s,%s,%s,%s,%s,%s,%s,"
        " now()+interval '1 hour', now()+interval '1 day','KOBLET')"
        " RETURNING id",
        (TENANT, logg, oppdragstype, oppdragstype, eiermodul, ct, key_id,
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


def _m37_trio(m):
    """Den KOBLEDE m37-trioen uten oppdraget: unntakssak, reparasjons-
    operasjon og fase-2-loggpost. `_lag_oppdrag` (test_m37) lager rad og
    trio i ett, men de negative stiene under må FØDE raden selv — det er
    nettopp radens form som prøves."""
    sak, logg = _lag_sak(m, TENANT)
    rid = secrets.token_hex(32)
    _sett_kontekst(m, TENANT)
    m.execute(
        "INSERT INTO reparasjonsoperasjoner (tenant, unntak_id,"
        " repair_operation_id, repair_generation, handler_id,"
        " handler_versjon, maalhandling, input_hash, kategori)"
        " VALUES (%s,%s,%s,0,'r1_reinnsending','1','purring.send',%s,"
        "'manglende_data')", (TENANT, sak, rid, secrets.token_hex(32)))
    fase2 = m.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'test','arbeidskapabilitet','ih2','p@1.0.0/x.y',"
        "'TILLAT','[]',%s) RETURNING id", (TENANT, rid)).fetchone()[0]
    m.commit()
    return sak, logg, rid, fase2


def _m37_evaluering(m):
    """En FULLFØRT `rekruttering.evaluering` på M37-ARMEN. Både vakten og
    funksjonen tillater `opprinnelse IN ('beslutning','m37_reparasjon')`:
    en evaluering kan også ha kommet av en reparasjon."""
    sak, logg = _lag_sak(m, TENANT)
    oid, _ = _lag_oppdrag(m, TENANT, sak, logg,
                          oppdragstype="rekruttering.evaluering")
    _sett_kontekst(m, TENANT)
    for steg in ("plukket", "utfort"):
        m.execute("UPDATE oppdrag SET status=%s WHERE tenant=%s AND id=%s",
                  (steg, TENANT, oid))
    m.commit()
    return int(oid)


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
    # Raden er ellers gyldig (KOBLET m/ beslutningsloggpost, og den
    # LOVLIGE utsendingstrippelen — så det er frigivelse_id som mangler,
    # ikke kontrakten), men opprinnelsen sier 'frigivelse' uten
    # frigivelse_id: ingen arm i totalformen passer. BEFORE-triggere kan
    # nå å si nei først — begge er lagringens avvisning av samme rad.
    with pytest.raises((psycopg.errors.CheckViolation,
                        psycopg.errors.RaiseException)):
        migrator.execute(
            "INSERT INTO oppdrag (opprinnelse, tenant,"
            " beslutning_loggpost_id, oppdragstype,"
            " handling, eiermodul, payload_kryptert, key_id, nonce,"
            " utforelsesfrist, evidensfrist, koblingsstatus)"
            " VALUES ('frigivelse',%s,%s,'rekruttering.utsending',"
            "'rekruttering.utsending','m57_ats',"
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
def test_frigivelse_pa_m37_opprinnelse_avvises(migrator):
    """Cursor P2-1 (runde 10 på #140): port 2 var målt på ÉN av de to
    andre armene.

    Testen over dekker `beslutning`. Fjernet man `AND frigivelse_id IS
    NULL` fra m37-armen alene, ble ingen test rød — og da kunne direkte
    DML føde et KOBLET reparasjonsoppdrag som OGSÅ bar en gyldig
    `frigivelse_id`. `oppdrag_en_per_frigivelse` er unik på den
    referansen, så frigivelsens ENE forsøk ville vært brent på et oppdrag
    `m57_ats` aldri eier: den irreversible utsendingen ble permanent
    uplukkbar, uten at noe feilet underveis.

    Positiv kontroll etterpå: NØYAKTIG samme rad uten `frigivelse_id`
    står. Avvisningen skyldes altså referansen og ikke trioen — uten den
    kontrollen kunne testen bestått av feil grunn."""
    ev, payload = _evaluering(migrator)
    bid = _signatar(migrator)
    liste = _liste(migrator, ev)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    sak, logg, rid, fase2 = _m37_trio(migrator)
    ct, key_id, nonce = payload
    rad = ("INSERT INTO oppdrag (opprinnelse, tenant, unntak_id,"
           " loggpost_id, repair_operation_id, beslutning_loggpost_id,"
           " frigivelse_id, oppdragstype, handling, eiermodul,"
           " payload_kryptert, key_id, nonce, utforelsesfrist,"
           " evidensfrist, koblingsstatus)"
           " VALUES ('m37_reparasjon',%s,%s,%s,%s,%s,%s,'reinnsending',"
           "'purring.send','eiermodul:reinnsending',%s,%s,%s,"
           " now()+interval '1 hour', now()+interval '30 days','KOBLET')")
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(rad, (TENANT, sak, logg, rid, fase2, fid,
                               ct, key_id, nonce))
    migrator.rollback()
    _sett_kontekst(migrator, TENANT)
    migrator.execute(rad, (TENANT, sak, logg, rid, fase2, None,
                           ct, key_id, nonce))
    migrator.rollback()


@pg
@pytest.mark.parametrize("trippel", [
    ("kontroll.wcag.nettsted", "kontroll.wcag.nettsted", "m_wcag_audit"),
    ("rekruttering.utsending", "rekruttering.utsending", "m_wcag_audit"),
    ("rekruttering.utsending", "kontroll.wcag.nettsted", "m57_ats"),
    ("rekruttering.evaluering", "rekruttering.utsending", "m57_ats"),
])
def test_frigivelseskontrakten_er_skjemasann_ikke_bare_funksjonssann(
        migrator, trippel):
    """Cursor P1 på #140 (runde 7): runde 6 låste trippelen i
    `opprett_frigivelsesoppdrag`, men porten sto i FUNKSJONEN mens
    `disponit_m37_claimer` har `INSERT ON oppdrag` (038). Direkte DML
    kunne dermed føde et KOBLET frigivelsesoppdrag i en ANNEN moduls kø
    — og siden `oppdrag_en_per_frigivelse` gir frigivelsen NØYAKTIG ETT
    forsøk, ville den raden samtidig BRENT den signerte utsendelsen:
    aldri plukkbar for `m57_ats`, aldri erstattbar.

    Uten denne testen kunne CHECK-en fjernes igjen og
    `test_frigivelsesoppdraget_maa_beskrive_utsendingskontrakten`
    fortsatt være grønn — det er funksjonsveien, ikke skjemaveien.

    MUTASJONEN SOM DREPER DENNE: ta trippelen ut av frigivelse-armen i
    `oppdrag_opprinnelse_komplett`."""
    oid, payload = _grunnlag(migrator)
    ct, key_id, nonce = payload
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "INSERT INTO oppdrag (opprinnelse, tenant, frigivelse_id,"
            " oppdragstype, handling, eiermodul, payload_kryptert,"
            " key_id, nonce, utforelsesfrist, evidensfrist,"
            " koblingsstatus)"
            " VALUES ('frigivelse',%s,%s,%s,%s,%s,%s,%s,%s,"
            " now()+interval '4 hours', now()+interval '1 day','KOBLET')",
            (TENANT, fid, *trippel, ct, key_id, nonce))
    migrator.rollback()
    # ... og ingen rad ble liggende igjen: frigivelsen har fortsatt sitt
    # ene forsøk i behold, og den GYLDIGE trippelen slipper gjennom.
    assert _ats_oppdrag(migrator, fid, payload) is not None


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
def test_koblingsvakten_dekker_frigivelsespekeren(migrator):
    """Codex P2 (runde 3) + Cursor P2 (runde 5), samme funn fra to
    reviewere: 056 speilet fødselsattributtet i KOLONNELÅSEN, men
    koblingsvakten — husets andre lag — sto igjen med bare
    `koblingsstatus` og `beslutning_loggpost_id`. Ett lag er en
    regresjonsflate på en irreversibel vei: en senere «rydding» i
    kolonnelåsen ville åpnet repeking av autorisasjonen uten at noen port
    sa fra.

    Vakten fyrer FØR kolonnelåsen (`oppdrag_koblingslaas` <
    `oppdrag_laas`), så meldingen her er vaktens egen.

    MUTASJONEN SOM DREPER DENNE: fjern `frigivelse_id`-leddet fra
    UPDATE-armen, eller INSERT-armen, i 056 §11."""
    oid, payload = _grunnlag(migrator)
    ct, key_id, nonce = payload
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    aoid = _ats_oppdrag(migrator, fid, payload)
    fid2 = _frigi(migrator, liste, mottaker="kv-m2")
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException) as ei:
        migrator.execute(
            "UPDATE oppdrag SET frigivelse_id=%s WHERE tenant=%s AND id=%s",
            (fid2, TENANT, aoid))
    assert "uforanderlige etter innsetting" in str(ei.value), str(ei.value)
    migrator.rollback()
    # INSERT-armen: et frigivelsesoppdrag er alltid KOBLET — vakten sier
    # det selv, før CHECK-en får rapportere.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException) as ei:
        migrator.execute(
            "INSERT INTO oppdrag (opprinnelse, tenant, frigivelse_id,"
            " oppdragstype, handling, eiermodul, payload_kryptert,"
            " key_id, nonce, utforelsesfrist, evidensfrist,"
            " koblingsstatus)"
            " VALUES ('frigivelse',%s,%s,'verifikasjon','h','e',%s,%s,%s,"
            " now()+interval '1 hour', now()+interval '1 day',"
            "'VERIFIKASJON')", (TENANT, fid2, ct, key_id, nonce))
    assert "alltid " in str(ei.value), str(ei.value)
    migrator.rollback()


@pg
def test_listeversjon_kan_ikke_vaere_sin_egen_forelder(migrator):
    """Cursor P2 på #140 (runde 5): en self-FK er lovlig i PostgreSQL, så
    direkte DML kunne sette `forrige_liste_id = liste_id`. Serien fikk da
    NULL røtter — `en_rot_per_serie` teller kun rader med forelder NULL —
    og kjeden signer → frigi → oppdrag virket likevel. «Én rot per serie»
    skal være schema-håndhevet.

    MUTASJONEN SOM DREPER DENNE: fjern
    `utsendingsliste_ikke_egen_forelder`."""
    import uuid as _uuid

    oid, _ = _evaluering(migrator)
    _sett_kontekst(migrator, TENANT)
    lid = _uuid.uuid4()
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "INSERT INTO utsendingsliste (tenant, liste_id, utkast_serie,"
            " forrige_liste_id, oppdrag_id, listetype, malversjon,"
            " innhold_hash, antall)"
            " VALUES (%s,%s,%s,%s,%s,'invitasjon','m@1','h-selv',3)",
            (TENANT, lid, _uuid.uuid4(), lid, oid))
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
            " VALUES ('frigivelse',%s,%s,%s,'rekruttering.utsending',"
            "'rekruttering.utsending','m57_ats',%s,%s,%s,"
            " now()+interval '1 hour',"
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
            "'rekruttering.utsending','rekruttering.utsending','m57_ats',"
            "%s,%s,%s,"
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
    """Port 11: HØYST én rot per serie (indeksen er unik, ikke
    tvingende — se `test_flerrads_sykel_er_dagens_tillatte_avvik`)."""
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
def test_flerrads_sykel_er_dagens_tillatte_avvik(migrator):
    """Codex P2 (runde 12 på #140), DOKUMENTERT AVVIK — ikke en port.

    Testene over måler lineariteten rad for rad, og der holder den. Én
    flerrads-`INSERT` gjør noe ingen av dem prøver: A får B som forelder
    og B får A, i samme setning. Self-FK-en er oppfylt (RI-triggerne
    fyrer etter setningen, og begge radene står), `ett_barn_per_versjon`
    ser to ULIKE foreldre, `utsendingsliste_ikke_egen_forelder` ser to
    ULIKE id-er, og `en_rot_per_serie` er UNIK og ikke tvingende — så
    NULL røtter er ikke et brudd. Serien har da ingen opprinnelse, og
    begge radene er fortsatt signerbare og frigivbare.

    K2 (RUTINER §9) forbyr et fjerde formforsøk på lineagen; rotårsaken
    står ved `en_rot_per_serie` i 056 §1 og er den samme som §-hodets:
    den herdede døren og bakdøren har SAMME eier. Setningen under krever
    `INSERT` på tabellen, altså `disponit_m37_claimer` eller eieren —
    ingen ORDINÆR rolle (jf. `test_funksjonene_er_eneste_vei_...`).

    DENNE TESTEN SKAL BLI RØD når #150 lander: da finnes ingen rolle noen
    kan LOGGE INN som med den rettigheten, og avviket forsvinner med
    eierskillet i stedet for med en femte lineage-constraint. Samme
    form som `test_frigivelse_binder_ikke_payload_til_signert_innhold`
    gjør for #149."""
    import uuid
    oid, _ = _grunnlag(migrator)
    serie = uuid.uuid4()
    a, b = uuid.uuid4(), uuid.uuid4()
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "INSERT INTO utsendingsliste (tenant, liste_id, utkast_serie,"
        " forrige_liste_id, oppdrag_id, listetype, malversjon,"
        " innhold_hash, antall) VALUES"
        " (%s,%s,%s,%s,%s,'invitasjon','m@1','h-syk-a',3),"
        " (%s,%s,%s,%s,%s,'invitasjon','m@1','h-syk-b',3)",
        (TENANT, a, serie, b, oid, TENANT, b, serie, a, oid))
    rotter, rader = migrator.execute(
        "SELECT count(*) FILTER (WHERE forrige_liste_id IS NULL),"
        " count(*) FROM utsendingsliste WHERE tenant=%s AND utkast_serie=%s",
        (TENANT, serie)).fetchone()
    migrator.rollback()
    assert rader == 2, "sykelen skal faktisk ha stått i basen"
    assert rotter == 0, (
        "avviket er nettopp at serien er ROTLØS — blir denne 1 eller"
        " reiser INSERT-en, er avviket lukket og testen skal skrives om"
        " til en port")


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
    og frigivelse.

    ALLE FIRE IKKE-`utfort`-TILSTANDENE MÅLES (Cursor P2, runde 5 på
    #170). Testen dekket `opprettet` (som `gar_fortsatt`), `kansellert`
    og feil type — men ikke de faktiske AVBRUDDStilstandene: `plukket`
    (oppdraget er claimet og går NÅ) og `feilet` (terminal etter krasj).
    Nettopp de to er kjøringene port 28 handler om, og en mutasjon som
    slakket vakten til `o.status IN ('utfort','plukket')` — eller til
    `o.status <> 'kansellert'` — ville sluppet en halvferdig evaluering
    videre til signatur og utsending uten at en eneste test rødnet."""
    feil_type, _ = _grunnlag(migrator,
                             oppdragstype="kontroll.wcag.nettsted")
    gar_fortsatt, _ = _grunnlag(migrator,
                                oppdragstype="rekruttering.evaluering",
                                status=None)
    plukket, _ = _grunnlag(migrator, oppdragstype="rekruttering.evaluering",
                           status="plukket")
    feilet, _ = _grunnlag(migrator, oppdragstype="rekruttering.evaluering",
                          status="feilet")
    avbrutt, _ = _grunnlag(migrator, oppdragstype="rekruttering.evaluering",
                           status="kansellert")
    fullfort, _ = _evaluering(migrator)
    uuid = __import__("uuid")
    rt = _rt()
    try:
        for oid in (feil_type, gar_fortsatt, plukket, feilet, avbrutt):
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
def test_m37_evaluering_kan_promotere_liste(migrator):
    """Cursor P2-2 (runde 10 på #140): BEGGE de tillatte opprinnelsene må
    måles, ikke bare den ene.

    Vakten og funksjonen sier eksplisitt `opprinnelse IN ('beslutning',
    'm37_reparasjon')` — en evaluering kan også ha kommet av en
    reparasjon. Ingen test konstruerte den armen, så en mutasjon som
    strøk `'m37_reparasjon'` fra begge `IN`-listene forble grønn: en
    stille regresjon som ville stengt reparasjonsveien ut av ATS-en, og
    først vist seg hos en kunde med en reparert evaluering.

    Begge veiene måles, for påstanden er skjemasann OG funksjonssann."""
    oid = _m37_evaluering(migrator)
    uuid = __import__("uuid")
    assert _liste(migrator, oid, hash_="h-m37") is not None   # skjemaveien
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        rt.execute(
            "SELECT opprett_utsendingsliste(%s,%s,NULL,%s,'invitasjon',"
            "'m@1','h-m37-fn',2)", (TENANT, uuid.uuid4(), oid))
        rt.rollback()
    finally:
        rt.close()


@pg
def test_promoteringen_er_skjemasann_ikke_bare_funksjonssann(migrator):
    """Cursor P2 på #140 (runde 7): porten over er FUNKSJONSveien.
    FK-en `(tenant, oppdrag_id)` sier bare at oppdraget finnes hos
    tenanten, og `disponit_m37_claimer` har `INSERT` på listetabellene —
    så direkte DML kunne promotere en liste på et WCAG-oppdrag, på en
    kansellert evaluering eller på et FRIGIVELSESOPPDRAG (kjeden inn i
    seg selv). Derfra var listen signerbar og sendbar som en hvilken som
    helst annen. Klarsignalets bevisform er negativ; dette er den.

    MUTASJONEN SOM DREPER DENNE: fjern triggeren
    `utsendingsliste_promotering` fra 056 §1.

    AVBRUDDStilstandene måles også her (Cursor P2, runde 5 på #170):
    skjemaveien skal ikke være svakere enn funksjonsveien, så `plukket`
    og `feilet` prøves mot direkte DML på samme måte som over. En vakt
    som slapp `plukket` gjennom ville ellers stått ubevist på nettopp
    den veien `disponit_m37_claimer` har `INSERT` på."""
    fullfort, payload = _evaluering(migrator)
    bid = _signatar(migrator)
    liste = _liste(migrator, fullfort)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    frigivelsesoppdrag = _ats_oppdrag(migrator, fid, payload)
    wcag, _ = _grunnlag(migrator, oppdragstype="kontroll.wcag.nettsted")
    avbrutt, _ = _grunnlag(migrator,
                           oppdragstype="rekruttering.evaluering",
                           status="kansellert")
    gar_fortsatt, _ = _grunnlag(migrator,
                                oppdragstype="rekruttering.evaluering",
                                status=None)
    plukket, _ = _grunnlag(migrator,
                           oppdragstype="rekruttering.evaluering",
                           status="plukket")
    feilet, _ = _grunnlag(migrator,
                          oppdragstype="rekruttering.evaluering",
                          status="feilet")
    for oid in (frigivelsesoppdrag, wcag, avbrutt, gar_fortsatt,
                plukket, feilet):
        _sett_kontekst(migrator, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            migrator.execute(
                "INSERT INTO utsendingsliste (tenant, liste_id,"
                " utkast_serie, oppdrag_id, listetype, malversjon,"
                " innhold_hash, antall)"
                " VALUES (%s, gen_random_uuid(), gen_random_uuid(), %s,"
                " 'invitasjon','m@1','h-dml',2)", (TENANT, oid))
        migrator.rollback()
    # Positiv kontroll: den fullførte evalueringen bærer fortsatt en
    # liste, også når raden skrives direkte.
    assert _liste(migrator, fullfort, hash_="h-dml-ok") is not None


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
def test_signaturen_holder_medlemskapslaasen(migrator):
    """Codex P1 (runde 10 på #140): medlemskapsporten var en LESNING, og
    en lesning er et øyeblikksbilde.

    Testen over måler at et INAKTIVT medlemskap avvises. Den sier ingenting
    om medlemskapet som er aktivt NÅ og deaktiveres ett øyeblikk senere: en
    administrator kan committe tilbakekallingen etter porten og før
    signaturen står i tabellen, og da er en tilbakekalt bruker skrevet inn
    — append-only — som den som autoriserte en irreversibel utsending.

    Muterings-drepende bevis, 013s form: mens signeringstransaksjonen står
    åpen, skal en `UPDATE` på NØYAKTIG den medlemskapsraden blokkeres
    (`lock_timeout` → `LockNotAvailable`). Med den gamle `IF NOT EXISTS
    (SELECT ...)` låses ingenting, tilbakekallingen går rett gjennom, og
    denne testen er rød. Kontrollen etterpå viser at låsen SLIPPES: en
    tilbakekalling er fortsatt lovlig når signeringen er ferdig."""
    from db.pg import koble
    oid, _ = _grunnlag(migrator)
    liste = _liste(migrator, oid)
    bid = _signatar(migrator)
    rt = _rt()
    revoker = koble(MIGRATOR_DSN)
    try:
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                   (TENANT, liste[0], bid, "n-" + secrets.token_hex(6)))
        # INGEN commit: signaturen er ikke synlig ennå, og låsen på
        # medlemskapsraden holdes ut transaksjonen.
        _sett_kontekst(revoker, TENANT)
        revoker.execute("SET lock_timeout='800ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            revoker.execute("UPDATE brukermedlemskap SET aktiv=false"
                            " WHERE tenant=%s AND bruker_id=%s",
                            (TENANT, bid))
        revoker.rollback()
        rt.commit()                                  # låsen slippes her
        _sett_kontekst(revoker, TENANT)
        revoker.execute("UPDATE brukermedlemskap SET aktiv=false"
                        " WHERE tenant=%s AND bruker_id=%s", (TENANT, bid))
        naa = revoker.execute(
            "SELECT aktiv FROM brukermedlemskap WHERE tenant=%s"
            " AND bruker_id=%s", (TENANT, bid)).fetchone()[0]
        revoker.commit()
        assert naa is False
    finally:
        rt.close(); revoker.close()


@pg
def test_replay_overlever_at_signataren_deaktiveres(migrator):
    """Codex P2 (runde 11 på #140): låsen fra forrige runde flyttet
    medlemskapsporten FORAN nøkkeloppslaget, og da fikk det ferdige
    replayet feil dom.

    Den tvetydige committen er selve grunnen til at nøkkelen finnes:
    signaturen landet, svaret gikk tapt, kalleren prøver igjen. Blir
    brukeren deaktivert i mellomtiden, svarte funksjonen
    `insufficient_privilege` på et retry der kontrakten lover no-op —
    og kalleren kan ikke skille «signaturen ble AVVIST» fra «signaturen
    STÅR». Den ene betyr ikke send, den andre allerede autorisert, og
    forskjellen er irreversibel e-post.

    Muterings-drepende: fjern det tidlige oppslaget, så faller replayet
    tilbake til låsen og testen er rød. To kontroller viser at porten
    IKKE er svekket: en NY nøkkel fra samme avskrudde signatar avvises
    fortsatt, og nøkkelen gjenbrukt med annen signatar er fortsatt et
    avvik — ellers kunne testen bestått ved at porten var borte."""
    oid, _ = _grunnlag(migrator)
    liste = _liste(migrator, oid)
    bid = _signatar(migrator)
    nk = "n-" + secrets.token_hex(6)
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                   (TENANT, liste[0], bid, nk))
        rt.commit()                    # signaturen står; svaret gikk tapt
        _sett_kontekst(migrator, TENANT)
        migrator.execute("UPDATE brukermedlemskap SET aktiv=false"
                         " WHERE tenant=%s AND bruker_id=%s", (TENANT, bid))
        migrator.commit()
        # Retryet: identisk nøkkel, identisk innhold → dokumentert no-op.
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                   (TENANT, liste[0], bid, nk))
        rt.rollback()
        # Kontroll 1: porten står. En NY signatur fra den nå avskrudde
        # brukeren er fortsatt avvist.
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                       (TENANT, liste[0], bid,
                        "n-" + secrets.token_hex(6)))
        rt.rollback()
        # Kontroll 2: samme nøkkel med ANNEN signatar er et avvik, ikke
        # et replay — dommen skal ikke svekkes av at oppslaget kom først.
        bid2 = _signatar(migrator)
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                       (TENANT, liste[0], bid2, nk))
        rt.rollback()
        # Replayet skrev ingenting: signaturen er fortsatt den ene.
        _sett_kontekst(migrator, TENANT)
        antall = migrator.execute(
            "SELECT count(*) FROM utsendingssignatur WHERE tenant=%s"
            " AND liste_id=%s", (TENANT, liste[0])).fetchone()[0]
        migrator.rollback()
        assert antall == 1
    finally:
        rt.close()


@pg
def test_replay_overlever_deaktivering_i_laaskoen(migrator):
    """Codex P2 (runde 12 på #140): testen over er SEKVENSIELL — den
    deaktiverer etter at signaturen står, altså før retryet i det hele
    tatt begynner. Det tidlige oppslaget fanger den saken. Det fanger
    IKKE at låsen er et VENTEPUNKT.

    Tre transaksjoner, FIFO på medlemskapsraden:

      A  signerer og holder medlemskapslåsen (ingen commit)
      R  deaktiverer medlemskapet  → stiller seg i kø bak A
      B  replay med SAMME nøkkel   → tidlig oppslag ser INGEN signatur
                                      (A er ucommittet), stiller seg bak R

    A committer; R får låsen og committer; B våkner til et medlemskap
    som ikke lenger er aktivt — enda den identiske signaturen NÅ står.
    Uten gjentakelsen av oppslaget etter låsen får B
    `insufficient_privilege` på et retry der kontrakten lover no-op:
    samme umulige valg for kalleren som runde 11 lukket («ble den
    AVVIST, eller STÅR den?»), bare med kappløpet i stedet for klokken.

    MUTASJONEN SOM DREPER DENNE: fjern det gjentatte oppslaget i
    `IF NOT FOUND`-armen i 056 §7b — B dør da med privilegiefeil.

    Kontrollen til slutt viser at porten ikke er svekket underveis:
    signaturen er fortsatt ÉN, altså skrev replayet ingenting."""
    import threading

    from db.pg import koble
    oid, _ = _grunnlag(migrator)
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    nk = "ko-" + secrets.token_hex(6)
    a = _rt()
    b = _rt()
    rev = koble(MIGRATOR_DSN)
    feil: dict = {}
    try:
        _sett_kontekst(a, TENANT)
        a.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                  (TENANT, liste[0], bid, nk))

        def deaktiver():
            try:
                _sett_kontekst(rev, TENANT)
                rev.execute("UPDATE brukermedlemskap SET aktiv=false"
                            " WHERE tenant=%s AND bruker_id=%s",
                            (TENANT, bid))
                rev.commit()
            except Exception as e:            # noqa: BLE001 — måles
                feil["rev"] = e
                rev.rollback()

        def replay():
            try:
                _sett_kontekst(b, TENANT)
                b.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                          (TENANT, liste[0], bid, nk))
                b.commit()
            except Exception as e:            # noqa: BLE001 — dommen måles
                feil["b"] = e
                b.rollback()

        tr = threading.Thread(target=deaktiver)
        tr.start()
        tr.join(timeout=2)
        assert tr.is_alive(), "R skulle blokkere på As medlemskapslås"
        tb = threading.Thread(target=replay)
        tb.start()
        tb.join(timeout=2)
        assert tb.is_alive(), "B skulle stille seg BAK R i låskøen"
        a.commit()                          # låsen slippes: R, så B
        tr.join(timeout=10)
        tb.join(timeout=10)
        assert not tr.is_alive() and not tb.is_alive()
        assert "rev" not in feil, f"deaktiveringen døde: {feil}"
        assert "b" not in feil, (
            "identisk replay skal være no-op også når deaktiveringen vant"
            f" låskøen foran det: {feil}")
        _sett_kontekst(migrator, TENANT)
        antall = migrator.execute(
            "SELECT count(*) FROM utsendingssignatur WHERE tenant=%s"
            " AND liste_id=%s", (TENANT, liste[0])).fetchone()[0]
        migrator.rollback()
        assert antall == 1
    finally:
        a.close(); b.close(); rev.close()


@pg
def test_funksjonene_er_eneste_vei_for_ordinaere_roller(migrator):
    """Port 4, grant-halvdelen: runtime har SELECT men ikke INSERT på
    kjedetabellene — skrivingen går gjennom funksjonene. (Den statiske
    halvdelen — at ingen annen kodevei setter opprinnelsen — måles av
    modultestene når modulen kommer; i dag finnes ingen kodevei.)

    ORDINÆRE er ordet som bærer avgrensningen (runde 10 på #140, issue
    #150): `disponit_m37_claimer` EIER kjedefunksjonene og må derfor ha
    INSERT på tabellene deres. For den rollen er taket mot signert
    `antall`, signatarens medlemskap og fristporten funksjonssanne, ikke
    skjemasanne. Den negative bærebjelken — ingen gyldig signatur ⇒ ingen
    representerbar utsendelse — er derimot skjemasann også for eieren, og
    måles slik i portene 6–12."""
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
def test_runtime_kan_ikke_drive_frigivelsesoppdraget(migrator):
    """Codex P1 (runde 12 på #140): grant-halvdelen over dekker
    KJEDETABELLENE, men `oppdrag` er ikke en av dem — kjøreren gir
    runtime-rollen `SELECT, UPDATE ON oppdrag` (PR-006 → 038), og den
    granten kan ikke trekkes: de to eldre opprinnelsene er runtimes egne
    jobber. Den tredje er det ikke. Uten porten i §6 kunne runtime, helt
    uten EXECUTE på en eneste kjedefunksjon, enten SLUKKE en autorisert
    utsending (`kansellert` på en rad frigivelsen aldri får en erstatning
    for, jf. `oppdrag_en_per_frigivelse`) eller FALSK-KVITTERE den
    (`plukket`→`utfort` med egne kvitteringsfelter).

    Begge veiene måles her, og feilen skal være en PRIVILEGIEFEIL —
    ikke statusmaskinens `RaiseException`, som ville betydd at raden var
    runtimes og bare tok feil vei gjennom maskinen.

    MUTASJONEN SOM DREPER DENNE: fjern `OLD.opprinnelse = 'frigivelse'`-
    blokken fra `oppdrag_kolonnelaas` i 056 §6 — da er begge UPDATE-ene
    lovlige overganger og testen er rød i begge ender.

    Eieren er UNNTATT med vilje: `disponit_m37_claimer` eier
    `claim_neste_oppdrag` og `reap_evidensfrister`, som er de to lovlige
    veiene i dag. Siste del av testen holder det unntaket ærlig — uten
    det ville porten stanset reaperen og latt en utløpt utsending stå
    ikke-terminal for alltid (Codex P2, runde 5)."""
    from db.pg import koble
    oid, payload = _grunnlag(migrator)
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    aoid = _ats_oppdrag(migrator, fid, payload)
    rt = koble(DSN)
    try:
        for sett in ("status='kansellert'", "status='plukket'"):
            rt.execute("SELECT set_config('disponit.tenant',%s,true)",
                       (TENANT,))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rt.execute("UPDATE oppdrag SET " + sett +
                           " WHERE tenant=%s AND id=%s", (TENANT, aoid))
            rt.rollback()
    finally:
        rt.close()
    # ... og et BESLUTNINGSOPPDRAG står urørt: porten er snever på den
    # tredje opprinnelsen, ikke en generell innstramming av 038s grant.
    # `_grunnlag` gir som standard et FULLFØRT oppdrag (det er det en
    # liste kan promotere), og `utfort` er terminal — kontrollen trenger
    # derfor sitt eget ferske oppdrag, ellers måler den statusmaskinen i
    # stedet for porten.
    ferskt, _ = _grunnlag(migrator, status=None)
    rt = koble(DSN)
    try:
        rt.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
        rt.execute("UPDATE oppdrag SET status='kansellert'"
                   " WHERE tenant=%s AND id=%s", (TENANT, ferskt))
        rt.rollback()
    finally:
        rt.close()
    # Eieren slipper gjennom — ellers hadde reaperen (§10) vært stengt ute
    # av sin egen port.
    # `_sender()` duger ikke her: den kan være varselsenderens EGEN
    # innlogging, og porten slipper bare kjedens eier gjennom. Rollen
    # settes derfor eksplisitt, og `SET ROLE` må stå i SAMME transaksjon
    # som UPDATE-en (en rollback tar rollen med seg).
    eier = koble(MIGRATOR_DSN)
    try:
        eier.execute("SET ROLE disponit_m37_claimer")
        eier.execute("SELECT set_config('disponit.tenant',%s,true)",
                     (TENANT,))
        eier.execute("UPDATE oppdrag SET status='plukket'"
                     " WHERE tenant=%s AND id=%s", (TENANT, aoid))
        eier.rollback()
    finally:
        eier.close()


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
            " VALUES ('frigivelse',%s,%s,'rekruttering.utsending',"
            "'rekruttering.utsending','m57_ats',%s,%s,%s,"
            " now()+interval '1 hour',"
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
def test_to_ulike_mottakere_mot_tak_paa_en(migrator):
    """Cursor P2 på #140 (runde 5): advisory-låsen finnes nettopp for
    tilfellet «begge leser `antall-1` og begge setter inn» — men suiten
    dekket bare sekvensielt tak og SAMME mottaker under kappløp. Uten
    denne kunne låsen fjernes og alt fortsatt være grønt, mens en liste
    signert for ÉN e-post sendte to.

    A frigir `m1` uten å committe; B kaller for `m2` og skal blokkere på
    låsen, ikke på unik-nøkkelen (mottakerne er ulike). Etter As commit
    ser B taket og avvises.

    MUTASJONEN SOM DREPER DENNE: fjern `pg_advisory_xact_lock` fra
    `frigi_utsendelse`."""
    import threading

    oid, _ = _grunnlag(migrator)
    bid = _signatar(migrator)
    liste = _liste(migrator, oid, antall=1)
    _signer(migrator, liste, bid)
    a = _sender()
    b = _sender()
    try:
        _sett_kontekst(a, TENANT)
        a.execute("SELECT frigi_utsendelse(%s,%s,'tak1-m1')",
                  (TENANT, liste[0]))
        resultat: dict = {}

        def taper():
            try:
                _sett_kontekst(b, TENANT)
                b.execute("SELECT frigi_utsendelse(%s,%s,'tak1-m2')",
                          (TENANT, liste[0]))
                resultat["utfall"] = "godtatt"
                b.commit()
            except psycopg.errors.InvalidParameterValue:
                resultat["utfall"] = "avvist"
                b.rollback()

        t = threading.Thread(target=taper)
        t.start()
        t.join(timeout=2)
        assert t.is_alive(), "B skulle blokkere på As advisory-lås"
        a.commit()
        t.join(timeout=10)
        assert not t.is_alive(), "B kom aldri gjennom etter As commit"
        assert resultat.get("utfall") == "avvist", resultat
    finally:
        a.close(); b.close()
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT count(*) FROM utsendingsfrigivelse WHERE tenant=%s"
        " AND liste_id=%s", (TENANT, liste[0])).fetchone()[0] == 1


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
def test_frigi_krever_read_committed(migrator):
    """Codex på #140 (runde 4) + Cursor P1 (runde 5): advisory-låsen
    serialiserer utførelsen, men friskner ikke opp et snapshot. Begge
    løftene funksjonen gir — taket mot det signerte `antall` og «samme
    mottaker → samme id» — er utledet av LESNINGER, ikke av
    skrivekonflikter, og holder derfor kun der hvert steg ser ferske data.

    Runde 4 slapp SERIALIZABLE gjennom fordi SSI antas å fange
    rw-syklusen. Den fanger OVERSENDINGEN, men ikke REPLAY-IDEN: to
    førstegangskall for SAMME mottaker gir taperen et unik-brudd som
    `ON CONFLICT DO NOTHING` svelger, og gjenlesningen leser fortsatt
    taperens eget snapshot — funksjonen svarer NULL, stille, der
    kontrakten lover vinnerens id. Begge fastholdt-snapshot-nivåene
    avvises derfor høylytt. READ COMMITTED-veien er uendret.

    MUTASJONEN SOM DREPER DENNE: slipp `serializable` gjennom porten
    igjen."""
    oid, _ = _grunnlag(migrator)
    bid = _signatar(migrator)
    liste = _liste(migrator, oid, antall=1)
    _signer(migrator, liste, bid)
    for niva in (psycopg.IsolationLevel.REPEATABLE_READ,
                 psycopg.IsolationLevel.SERIALIZABLE):
        snd = _sender()
        try:
            # `SET ROLE` i `_sender()` er sesjonsnivå og overlever
            # commit-en; isolasjonsnivået kan bare byttes utenfor en åpen
            # transaksjon.
            snd.commit()
            snd.isolation_level = niva
            _sett_kontekst(snd, TENANT)
            with pytest.raises(psycopg.errors.InvalidTransactionState):
                snd.execute("SELECT frigi_utsendelse(%s,%s,'rr-m1')",
                            (TENANT, liste[0]))
            snd.rollback()
        finally:
            snd.close()
    snd = _sender()
    try:
        _sett_kontekst(snd, TENANT)
        fid = snd.execute("SELECT frigi_utsendelse(%s,%s,'rr-m1')",
                          (TENANT, liste[0])).fetchone()[0]
        snd.commit()
    finally:
        snd.close()
    assert fid is not None, "READ COMMITTED-veien skal være uendret"


@pg
def test_signer_krever_read_committed(migrator):
    """Codex P2 på #140 (runde 7): replay-løftet i
    `signer_utsendingsliste` — «samme nøkkel + samme innhold ⇒ no-op» —
    er utledet av LESNINGER i begge ender (nøkkel-oppslaget før
    innsettingen, og gjenlesningen i `unique_violation`-armen).

    PostgreSQL oversetter ikke et unik-brudd mot en samtidig COMMITTET
    rad til en serialiseringsfeil: taperen får 23505 også under
    REPEATABLE READ og SERIALIZABLE, og gjenlesningen etterpå bruker
    fortsatt transaksjonens fastholdte snapshot — vinnerens signatur
    finnes ikke der, armen faller til `RAISE`, og et helt legitimt
    replay får en feil kontrakten lover skal være en no-op. Samme klasse
    og samme ratifiserte form som `frigi_utsendelse`.

    MUTASJONEN SOM DREPER DENNE: fjern porten i §7b."""
    oid, _ = _grunnlag(migrator)
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    nk = "rr-" + secrets.token_hex(6)
    for niva in (psycopg.IsolationLevel.REPEATABLE_READ,
                 psycopg.IsolationLevel.SERIALIZABLE):
        k = _rt()
        try:
            k.commit()                 # nivå byttes kun utenfor en tx
            k.isolation_level = niva
            _sett_kontekst(k, TENANT)
            with pytest.raises(psycopg.errors.InvalidTransactionState):
                k.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                          (TENANT, liste[0], bid, nk))
            k.rollback()
        finally:
            k.close()
    # READ COMMITTED-veien er uendret, replay inkludert.
    k = _rt()
    try:
        _sett_kontekst(k, TENANT)
        k.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                  (TENANT, liste[0], bid, nk))
        k.commit()
        _sett_kontekst(k, TENANT)
        k.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                  (TENANT, liste[0], bid, nk))
        k.commit()
    finally:
        k.close()
    # `utsendingssignatur` har FORCE ROW LEVEL SECURITY (§4), og GUC-en er
    # transaksjonslokal — uten kontekst teller vi et tomt vindu.
    _sett_kontekst(migrator, TENANT)
    antall = migrator.execute(
        "SELECT count(*) FROM utsendingssignatur WHERE tenant=%s"
        " AND liste_id=%s", (TENANT, liste[0])).fetchone()[0]
    migrator.rollback()
    assert antall == 1, "replayet lagde en NY signatur"


@pg
def test_frigivelsesoppdrag_krever_read_committed(migrator):
    """Codex P2 på #140 (runde 7): retry-løftet i
    `opprett_frigivelsesoppdrag` («samme frigivelse + samme kontrakt ⇒
    vinnerens oppdrag-id») leser oppdraget PÅ NYTT etter unik-bruddet.

    Et retry som startet FØR vinneren committet, ser med fastholdt
    snapshot hverken vinnerens rad i materialitetsoppslaget eller i
    `EXISTS`-en som skiller «annet innhold» fra «bruddet var ikke
    frigivelsens» — armen faller til bar `RAISE`, og den dokumenterte
    idempotente retry-veien er brutt nettopp i situasjonen den finnes
    for: etter en tvetydig commit. Utsendelsen er irreversibel.

    MUTASJONEN SOM DREPER DENNE: fjern porten i §7d."""
    oid, payload = _grunnlag(migrator)
    ct, key_id, nonce = payload
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    kall = ("SELECT opprett_frigivelsesoppdrag(%s,%s,"
            "'rekruttering.utsending','rekruttering.utsending','m57_ats',"
            "%s,%s,%s, now()+interval '4 hours', now()+interval '1 day')")
    for niva in (psycopg.IsolationLevel.REPEATABLE_READ,
                 psycopg.IsolationLevel.SERIALIZABLE):
        snd = _sender()
        try:
            snd.commit()
            snd.isolation_level = niva
            _sett_kontekst(snd, TENANT)
            with pytest.raises(psycopg.errors.InvalidTransactionState):
                snd.execute(kall, (TENANT, fid, ct, key_id, nonce))
            snd.rollback()
        finally:
            snd.close()
    snd = _sender()
    try:
        _sett_kontekst(snd, TENANT)
        aoid = snd.execute(kall, (TENANT, fid, ct, key_id, nonce)
                           ).fetchone()[0]
        snd.commit()
        _sett_kontekst(snd, TENANT)
        aoid2 = snd.execute(kall, (TENANT, fid, ct, key_id, nonce)
                            ).fetchone()[0]
        snd.commit()
    finally:
        snd.close()
    assert aoid is not None and aoid2 == aoid, (
        "READ COMMITTED-veien, retry inkludert, skal være uendret")


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
    # ... og utsendingsveien må TREKKES fra runtime, ikke bare la være å
    # bli gitt (Cursor P2 på #140, runde 5 — husets egen lære fra
    # varsel-funnet: «en grant som bare slutter å bli GITT er ikke trukket
    # tilbake»). Veien er irreversibel; en tidligere kjøring eller en
    # manuell grant skal ikke overleve i stillhet.
    for fn in ("frigi_utsendelse(TEXT, UUID, TEXT)",
               "opprett_frigivelsesoppdrag(TEXT, UUID, TEXT, TEXT, TEXT,"
               " BYTEA, TEXT, BYTEA, TIMESTAMPTZ, TIMESTAMPTZ)"):
        assert f"REVOKE ALL ON FUNCTION {fn} FROM {{rolle}};" in tekst, fn


@pg
def test_frigivelsesoppdrag_faar_sak_med_revisjonslinje(migrator):
    """Codex P1 på #140 (runde 5): `sikre_sak_for_oppdrag` utleder
    saksloggen av `coalesce(beslutning_loggpost_id, loggpost_id)`, men
    frigivelses-armen i totalformen tvinger BEGGE til NULL —
    autorisasjonen der er signaturen. Uten en utledet linje brøt
    `unntak.loggpost_id NOT NULL`, og HELE den sene kvitteringen eller
    sikkerhetskonflikten rullet tilbake: en IRREVERSIBEL utsending uten
    sak og uten et menneske som fikk se den.

    Linjen slås opp der den finnes — frigivelse → liste →
    evalueringsoppdrag — og saken skal bære nøyaktig den loggposten
    evalueringen ble autorisert av.

    MUTASJONEN SOM DREPER DENNE: fjern `IF v_logg IS NULL AND
    o.frigivelse_id IS NOT NULL`-armen fra 056 §9."""
    oid, payload = _evaluering(migrator)
    _sett_kontekst(migrator, TENANT)
    logg = migrator.execute(
        "SELECT beslutning_loggpost_id FROM oppdrag WHERE tenant=%s"
        " AND id=%s", (TENANT, oid)).fetchone()[0]
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    aoid = _ats_oppdrag(migrator, fid, payload)
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        # Sen kvittering på et frigivelsesoppdrag — veien app.py tar.
        sak = rt.execute("SELECT sikre_sak_for_oppdrag(%s,%s,'evidensfrist',"
                         "'kvitteringsport','r-m57')",
                         (TENANT, aoid)).fetchone()[0]
        rt.commit()
    finally:
        rt.close()
    assert sak is not None
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT loggpost_id, oppdrag_id, arsak FROM unntak WHERE id=%s",
        (sak,)).fetchone()
    assert rad == (logg, aoid, "evidensfrist"), rad


@pg
def test_utlopt_frigivelsesoppdrag_blir_terminalt(migrator):
    """Codex P2 på #140 (runde 5): `claim_neste_oppdrag` plukker kun
    rader med `utforelsesfrist > now()`, og reaperen tok bare
    `opprinnelse='beslutning'`. Et frigivelsesoppdrag som løp ut i køen
    var dermed hverken plukkbart eller terminaliserbart — det sto
    ikke-terminalt for alltid, og en e-post som ALDRI ble sendt så ut som
    en jobb som fortsatt skulle sendes.

    Den tredje opprinnelsen hører hjemme i reaperen (i motsetning til
    m37-veien, som 038 holdt utenfor fordi dens oppdrag alt HAR en sak):
    autorisasjonen er en signatur, ikke et unntak.

    MUTASJONEN SOM DREPER DENNE: sett predikatet i 056 §10 tilbake til
    `o.opprinnelse = 'beslutning'`."""
    from .test_outbox_bestilling import _reaperkobling

    oid, payload = _evaluering(migrator)
    ct, key_id, nonce = payload
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    _sett_kontekst(migrator, TENANT)
    aoid = migrator.execute(
        "INSERT INTO oppdrag (opprinnelse, tenant, frigivelse_id,"
        " oppdragstype, handling, eiermodul, payload_kryptert, key_id,"
        " nonce, utforelsesfrist, evidensfrist, koblingsstatus)"
        " VALUES ('frigivelse',%s,%s,'rekruttering.utsending',"
        "'rekruttering.utsending','m57_ats',%s,%s,%s,"
        " now()-interval '2 minutes', now()-interval '1 minute','KOBLET')"
        " RETURNING id", (TENANT, fid, ct, key_id, nonce)).fetchone()[0]
    migrator.commit()
    rp = None
    try:
        rp, _timerrolle = _reaperkobling()
        rader = rp.execute("SELECT tenant, oppdrag_id, unntak_id"
                           " FROM reap_evidensfrister(200)").fetchall()
        rp.commit()
    finally:
        if rp is not None:
            rp.close()
    mine = [r for r in rader if r[0] == TENANT and r[1] == aoid]
    assert len(mine) == 1, f"reaperen lot frigivelsesoppdraget stå: {rader!r}"
    _sett_kontekst(migrator, TENANT)
    status, kvittering = migrator.execute(
        "SELECT status, kvittering IS NULL FROM oppdrag WHERE tenant=%s"
        " AND id=%s", (TENANT, aoid)).fetchone()
    assert (status, kvittering) == ("feilet", True), (status, kvittering)
    assert migrator.execute(
        "SELECT arsak FROM unntak WHERE id=%s",
        (mine[0][2],)).fetchone()[0] == "evidensfrist"


@pg
def test_frigivelsesoppdrag_avviser_alt_utlopt_frist(migrator):
    """Codex P2 på #140 (runde 5), andre halvdel: reaperen rydder rader
    som løper ut ETTER at de ble laget — en jobb som er uplukkbar i det
    den fødes skal ikke fødes i det hele tatt. `oppdrag_en_per_frigivelse`
    gir frigivelsen nøyaktig ETT forsøk, så et dødfødt oppdrag blokkerer
    dessuten det gyldige som aldri kommer.

    MUTASJONEN SOM DREPER DENNE: fjern fristporten
    (`IF p_utforelsesfrist <= clock_timestamp()`) fra
    `opprett_frigivelsesoppdrag`."""
    oid, payload = _evaluering(migrator)
    ct, key_id, nonce = payload
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    snd = _sender()
    try:
        _sett_kontekst(snd, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            snd.execute(
                "SELECT opprett_frigivelsesoppdrag(%s,%s,"
                "'rekruttering.utsending','rekruttering.utsending',"
                "'m57_ats',%s,%s,%s, now()-interval '1 minute',"
                " now()+interval '1 day')", (TENANT, fid, ct, key_id, nonce))
        snd.rollback()
    finally:
        snd.close()
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT count(*) FROM oppdrag WHERE tenant=%s AND frigivelse_id=%s",
        (TENANT, fid)).fetchone()[0] == 0, "dødfødt oppdrag ble laget"


@pg
def test_frigivelsesoppdrag_maaler_fristen_mot_veggklokken(migrator):
    """Codex P2 på #140 (runde 9): porten over målte mot `now()`, som er
    `transaction_timestamp()` — frosset ved transaksjonens FØRSTE setning.

    Senderen åpner transaksjonen, gjør sitt arbeid, og kaller først
    DERETTER hit. En frist som lå i fremtiden ved `BEGIN`, men var utløpt
    ved kallet, slapp dermed gjennom en port som finnes nettopp for å
    hindre den — og brukte opp frigivelsens ENE forsøk
    (`oppdrag_en_per_frigivelse`) på et oppdrag `claim_neste_oppdrag`
    aldri kan plukke.

    Testen holder transaksjonen åpen forbi fristen: `pg_sleep` flytter
    veggklokken, ikke transaksjonstiden.

    MUTASJONEN SOM DREPER DENNE: sett `clock_timestamp()` tilbake til
    `now()` i `opprett_frigivelsesoppdrag`."""
    oid, payload = _evaluering(migrator)
    ct, key_id, nonce = payload
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    snd = _sender()
    try:
        _sett_kontekst(snd, TENANT)          # her fryses now() for alltid
        snd.execute("SELECT pg_sleep(1.5)")  # veggklokken går, now() står
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            snd.execute(
                "SELECT opprett_frigivelsesoppdrag(%s,%s,"
                "'rekruttering.utsending','rekruttering.utsending',"
                "'m57_ats',%s,%s,%s,"
                " transaction_timestamp()+interval '500 milliseconds',"
                " transaction_timestamp()+interval '1 day')",
                (TENANT, fid, ct, key_id, nonce))
        snd.rollback()
    finally:
        snd.close()
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT count(*) FROM oppdrag WHERE tenant=%s AND frigivelse_id=%s",
        (TENANT, fid)).fetchone()[0] == 0, "dødfødt oppdrag ble laget"


@pg
def test_retry_etter_utlopt_frist_faar_vinnerens_oppdrag(migrator):
    """Codex P2 på #140 (runde 13): fristporten sto FØR innsettingen,
    altså også før `unique_violation`-armen som gir et retry vinnerens
    oppdrags-id.

    Et retry etter en tvetydig commit sender med den SAMME absolutte
    fristen som førstegangskallet — det er nettopp det som gjør det til
    et retry. Kom retryet etter at den fristen hadde passert, døde det på
    fristporten uten noen gang å få vite at oppdraget alt STÅR: samme
    umulige valg for kalleren som runde 11 lukket i signeringsveien
    («ble den avvist, eller er den opprettet?»), på en IRREVERSIBEL
    utsendelse.

    Fristporten finnes for å hindre at et dødfødt oppdrag FØDES. Et
    ferdig retry føder ingenting, så oppslaget må avgjøres først.

    MUTASJONEN SOM DREPER DENNE: flytt replay-oppslaget i
    `opprett_frigivelsesoppdrag` tilbake til ETTER fristporten."""
    oid, payload = _evaluering(migrator)
    ct, key_id, nonce = payload
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    snd = _sender()
    try:
        _sett_kontekst(snd, TENANT)
        forste = snd.execute(
            "SELECT opprett_frigivelsesoppdrag(%s,%s,"
            "'rekruttering.utsending','rekruttering.utsending','m57_ats',"
            "%s,%s,%s, now()+interval '4 hours', now()+interval '1 day')",
            (TENANT, fid, ct, key_id, nonce)).fetchone()[0]
        snd.commit()
        # Svaret gikk tapt; retryet kommer etter at den samme absolutte
        # fristen har passert. Fristen er utenfor materialiteten, så
        # oppslaget kjenner igjen oppdraget uansett.
        _sett_kontekst(snd, TENANT)
        omigjen = snd.execute(
            "SELECT opprett_frigivelsesoppdrag(%s,%s,"
            "'rekruttering.utsending','rekruttering.utsending','m57_ats',"
            "%s,%s,%s, now()-interval '1 minute', now()+interval '1 day')",
            (TENANT, fid, ct, key_id, nonce)).fetchone()[0]
        snd.commit()
    finally:
        snd.close()
    assert omigjen == forste, (
        "retry etter utløpt frist fikk ikke vinnerens oppdrag")
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT count(*) FROM oppdrag WHERE tenant=%s AND frigivelse_id=%s",
        (TENANT, fid)).fetchone()[0] == 1, "retryet lagde et NYTT oppdrag"


@pg
def test_retry_venter_paa_vinneren_foer_fristporten(migrator):
    """Codex P2 (runde 13), den samtidige halvdelen: testen over er
    SEKVENSIELL — vinneren har committet før retryet begynner, og det
    tidlige oppslaget ser den. Det fanger ikke at oppslaget uten en lås
    er et rent TOCTOU, samme som `frigi_utsendelse` lukket i runde 3.

    To transaksjoner:

      A  oppretter oppdraget med en gyldig frist, men committer ikke
      B  retry med den samme fristen, som nå har passert

    Uten låsen bommer Bs oppslag (A er ucommittet), og B faller rett i
    fristporten — avvist på et retry der kontrakten lover vinnerens id.
    Med låsen står B og venter til A er ferdig, gjenleser, og finner
    oppdraget.

    MUTASJONEN SOM DREPER DENNE: fjern `pg_advisory_xact_lock` foran
    replay-oppslaget i `opprett_frigivelsesoppdrag` — B dør da med
    `InvalidParameterValue`."""
    import threading

    oid, payload = _evaluering(migrator)
    ct, key_id, nonce = payload
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    a = _sender()
    b = _sender()
    utfall: dict = {}
    try:
        _sett_kontekst(a, TENANT)
        aid = a.execute(
            "SELECT opprett_frigivelsesoppdrag(%s,%s,"
            "'rekruttering.utsending','rekruttering.utsending','m57_ats',"
            "%s,%s,%s, now()+interval '4 hours', now()+interval '1 day')",
            (TENANT, fid, ct, key_id, nonce)).fetchone()[0]

        def retry():
            try:
                _sett_kontekst(b, TENANT)
                utfall["id"] = b.execute(
                    "SELECT opprett_frigivelsesoppdrag(%s,%s,"
                    "'rekruttering.utsending','rekruttering.utsending',"
                    "'m57_ats',%s,%s,%s, now()-interval '1 minute',"
                    " now()+interval '1 day')",
                    (TENANT, fid, ct, key_id, nonce)).fetchone()[0]
                b.commit()
            except Exception as e:            # noqa: BLE001 — dommen måles
                utfall["feil"] = e
                b.rollback()

        tb = threading.Thread(target=retry)
        tb.start()
        tb.join(timeout=2)
        assert tb.is_alive(), "B skulle vente på As lås, ikke avgjøre selv"
        a.commit()
        tb.join(timeout=10)
        assert not tb.is_alive()
    finally:
        a.close()
        b.close()
    assert "feil" not in utfall, (
        "samtidig retry døde i stedet for å få vinnerens oppdrag:"
        f" {utfall.get('feil')}")
    assert utfall["id"] == aid, "samtidig retry fikk et annet oppdrag"
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT count(*) FROM oppdrag WHERE tenant=%s AND frigivelse_id=%s",
        (TENANT, fid)).fetchone()[0] == 1, "retryet lagde et NYTT oppdrag"


def test_cp1_har_ikke_den_konsumerende_benen():
    """Codex 2 × P1 på #140 (runde 13), UTSATT UNDER K1 — sporet er
    issue #151. Denne testen er ikke en port mot en angriper; den fester
    FORUTSETNINGEN begge utsettelsene hviler på, og skal bli RØD i samme
    PR som fjerner den.

    CP1 bygger autorisasjonsbenen (evaluering → liste → signatur →
    frigivelse → oppdrag i køen). Den bygger ikke den konsumerende
    (claim → utførelse → kvittering), og den benen er avstengt ved
    roten: `rekruttering.utsending` står ikke i `OPPDRAGSTYPER`, og
    `api/app.py` kjører hver claimet payload gjennom
    `oppdragskontrakt.minimer`, som kaster `Oppdragstypeukjent` og ruller
    claimet tilbake. Et frigivelsesoppdrag kan derfor i dag ikke claimes,
    ikke utføres og ikke kvitteres.

    Nøyaktig i det øyeblikket typen registreres blir to funn ekte:

      * kvitteringsveien (`_ingest_kvittering`) skriver den avsluttende
        overgangen som en rå UPDATE som runtime, og porten i 056 §6
        avviser den. Å slippe den gjennom er ikke en fiks — det ER
        angrepsvei to (falsk kvittering), og porten kan ikke skille
        ærlig fra falsk: det er samme setning fra samme rolle;
      * `oppdrag_en_per_frigivelse` gir frigivelsen ett OPPDRAG, ikke én
        SENDING. Krasjer arbeideren etter utsendelsen men før
        kvitteringen, gjenclaimes raden når `owner_lease_utloper` går
        (049), og den irreversible e-posten kan sendes om igjen.

    Begge krever ny maskin = egen PR (K1, RUTINER §9). #151 bærer dem med
    akseptkriterier, og fjerningen av denne testen er punkt 4 der.

    MUTASJONEN SOM DREPER DENNE: registrer `rekruttering.utsending` i
    `OPPDRAGSTYPER` uten å ta funnene i #151."""
    from oppdragskontrakt import OPPDRAGSTYPER
    assert "rekruttering.utsending" not in OPPDRAGSTYPER, (
        "typen er registrert — da er #151s to P1 ekte, og denne testen"
        " skal fjernes i samme PR som lukker dem")


def test_056_navngir_aldri_runtime_rollen():
    """Codex P1 på #140 (runde 13) — runde 5s `IF EXISTS`-form lukket bare
    HALVE funnet, og denne testen målte bare den halvdelen.

    `disponit` er LOKAL-/TESTNAVNET på runtime-rollen; `migrer.py` tar
    navnet som ARGUMENT. Betingelsen fjernet utfall 1 (rollen finnes ikke
    → hele 056 ruller tilbake før kjøreren rekker `M37_RETTIGHETER_API`),
    men gjorde utfall 2 STILLE: finnes navnet som en urelatert eller
    UTRANGERT innlogging, er betingelsen sann, og den innloggingen får
    EXECUTE på opprettelsen og SIGNERINGEN — for alltid, fordi kjørerens
    nullstilling gjelder den KONFIGURERTE rollen, ikke alle roller.

    Porten er derfor ikke lenger «betinget», men «ikke nevnt»: 056 skal
    ikke inneholde ÉN grant til lokalnavnet. At fjerningen ikke er et TAP
    av rettighet måles av naboen
    `test_migrer_baerer_utsendingskjedens_rettigheter`, som krever de
    samme grantene på `{rolle}`-form i kjøreren.

    MUTASJONEN SOM DREPER DENNE: legg grant-blokken tilbake i §7 eller
    §8."""
    sql = (ROT / "platform" / "core" / "db" / "migrations"
           / "056_m57_utsending.sql").read_text(encoding="utf-8")
    treff = list(re.finditer(r"TO disponit\b\s*;", sql))
    assert not treff, (
        "056 navngir runtime-rollen ved lokalnavn — kjøreren er eneste "
        "rettighetskilde: " + repr([
            sql[max(0, t.start() - 120):t.end()][-120:] for t in treff]))


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
        # annen METADATA -> fortsatt avvist. Etter at trippelen ble låst
        # (Codex P1, runde 6) er `key_id` det ENESTE frie feltet i
        # materialiteten, og det er FK-bundet til tenantens egne nøkler —
        # scenariet er altså et retry som kommer inn på den andre siden av
        # en nøkkelrotasjon. Den utrangerte nøkkelen legges inn direkte
        # (aktiv=false, ikke destruert) fordi det er nettopp den formen en
        # rotasjon etterlater.
        _sett_kontekst(migrator, TENANT)
        rotert = "dek-" + secrets.token_hex(8)
        migrator.execute(
            "INSERT INTO tenant_nokler (tenant, key_id, wrapped_dek, aktiv)"
            " VALUES (%s,%s,%s,false)", (TENANT, rotert, b"utrangert"))
        migrator.commit()
        _sett_kontekst(snd, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            snd.execute(
                "SELECT opprett_frigivelsesoppdrag(%s,%s,"
                "'rekruttering.utsending','rekruttering.utsending',"
                "'m57_ats',%s,%s,%s, now()+interval '4 hours',"
                " now()+interval '1 day')",
                (TENANT, fid, ct, rotert, nonce))
        snd.rollback()
    finally:
        snd.close()


@pg
def test_frigivelse_binder_ikke_payload_til_signert_innhold(migrator):
    """DOKUMENTERER ET ÅPENT AVVIK — den skal SNU, ikke bestå, når
    manifestet kommer (GitHub-issue #149; Funn 8 fra Codex runde 2,
    gjentatt av Cursor som P1-2/P2-4 i runde 7 på #140).

    CP1 binder frigivelsens IDENTITET, ikke innholdet som sendes.
    `payload_kryptert` er utenfor retry-materialiteten fordi AES-GCM gir
    ny nonce per kryptering — riktig for retry-klassen, men det betyr at
    `disponit_varselsender` kan feste VILKÅRLIG chiffertekst til en
    gyldig frigivelse, og at `mottaker_ref` er et antalltak og ikke et
    medlemskap i det signerte innholdet. Ekte binding krever et
    per-mottaker-manifest, altså ny produktflate (K1), og hører i egen
    PR.

    Testen fester dagens oppførsel så regresjonen SYNES: to kall med
    samme frigivelse og samme kontrakt, men ULIK chiffertekst, gir samme
    oppdrag — og vinnerens chiffertekst er den som blir stående og sendt.

    NÅR #149 LANDER: denne testen skal erstattes av «avvikende
    innholdsdigest avvises; matchende digest er idempotent»."""
    from db import kryptering
    oid, payload = _grunnlag(migrator)
    ct, key_id, nonce = payload
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    _sett_kontekst(migrator, TENANT)
    _key, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator, TENANT)
    annet_ct, annet_nonce = kryptering.krypter(
        dek, {"m57": "ET HELT ANNET INNHOLD"}, TENANT, _key)
    migrator.commit()
    assert _key == key_id, "testen forutsetter samme aktive DEK"
    kall = ("SELECT opprett_frigivelsesoppdrag(%s,%s,"
            "'rekruttering.utsending','rekruttering.utsending','m57_ats',"
            "%s,%s,%s, now()+interval '4 hours', now()+interval '1 day')")
    snd = _sender()
    try:
        _sett_kontekst(snd, TENANT)
        forst = snd.execute(kall, (TENANT, fid, ct, key_id, nonce)
                            ).fetchone()[0]
        snd.commit()
        _sett_kontekst(snd, TENANT)
        andre = snd.execute(
            kall, (TENANT, fid, annet_ct, key_id, annet_nonce)).fetchone()[0]
        snd.commit()
    finally:
        snd.close()
    assert andre == forst, (
        "forutsetningen for avviket er borte — se #149 og snu testen")
    _sett_kontekst(migrator, TENANT)
    lagret = migrator.execute(
        "SELECT payload_kryptert FROM oppdrag WHERE tenant=%s AND id=%s",
        (TENANT, forst)).fetchone()[0]
    migrator.rollback()
    assert bytes(lagret) == bytes(ct), (
        "vinnerens chiffertekst skal bli stående — det ER avviket")


@pg
@pytest.mark.parametrize("trippel", [
    ("kontroll.wcag.nettsted", "kontroll.wcag.nettsted", "m_wcag_audit"),
    ("rekruttering.utsending", "rekruttering.utsending", "m_wcag_audit"),
    ("rekruttering.utsending", "kontroll.wcag.nettsted", "m57_ats"),
    ("rekruttering.evaluering", "rekruttering.utsending", "m57_ats"),
])
def test_frigivelsesoppdraget_maa_beskrive_utsendingskontrakten(
        migrator, trippel):
    """Codex P1 på #140 (runde 6) + Cursor P1 (runde 5) — samme funn fra
    begge reviewerne: funksjonen satte `opprinnelse` og `frigivelse_id`
    selv, men lot kalleren velge HVILKEN outbox-jobb signaturen
    autoriserte. `claim_neste_oppdrag` plukker på eiermodul +
    handlingsprefiks, så senderen kunne føde et KOBLET, frigivelses-bærende
    oppdrag i en ANNEN moduls kø — og den modulen dekrypterer og utfører
    det. Mennesket signerte en utsendingsliste, ikke en WCAG-kontroll.

    Alle fire varianter treffer ETT felt hver (eller typen alene), så
    testen måler at porten leser hele trippelen, ikke bare eiermodulen.

    MUTASJONEN SOM DREPER DENNE: fjern kontrakt-porten i
    `opprett_frigivelsesoppdrag`."""
    oid, payload = _grunnlag(migrator)
    ct, key_id, nonce = payload
    bid = _signatar(migrator)
    liste = _liste(migrator, oid)
    _signer(migrator, liste, bid)
    fid = _frigi(migrator, liste)
    snd = _sender()
    try:
        _sett_kontekst(snd, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            snd.execute(
                "SELECT opprett_frigivelsesoppdrag(%s,%s,%s,%s,%s,%s,%s,%s,"
                " now()+interval '4 hours', now()+interval '1 day')",
                (TENANT, fid, *trippel, ct, key_id, nonce))
        snd.rollback()
        # ... og ingen rad ble lagt igjen: `oppdrag_en_per_frigivelse` gir
        # frigivelsen nøyaktig ETT forsøk, så et avvist forsøk som likevel
        # skrev ville blokkert det gyldige oppdraget for alltid.
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT count(*) FROM oppdrag WHERE tenant=%s AND"
            " frigivelse_id=%s", (TENANT, fid)).fetchone()[0] == 0
        migrator.rollback()
        # positiv kontroll: den godkjente trippelen slipper gjennom.
        _sett_kontekst(snd, TENANT)
        aoid = snd.execute(
            "SELECT opprett_frigivelsesoppdrag(%s,%s,"
            "'rekruttering.utsending','rekruttering.utsending','m57_ats',"
            "%s,%s,%s, now()+interval '4 hours', now()+interval '1 day')",
            (TENANT, fid, ct, key_id, nonce)).fetchone()[0]
        assert aoid
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
def test_serien_baerer_ETT_evalueringsoppdrag_ogsaa_ved_direkte_dml(migrator):
    """Codex P2 på #140 (runde 6) + Cursor P2 (runde 5), samme funn:
    funksjonsporten over stopper funksjonsveien, men de to FK-ene på
    tabellen var UAVHENGIGE — barnet måtte stå i forelderens serie, og
    barnet måtte peke på ET evalueringsoppdrag, men ingenting knyttet de to.
    Direkte DML fra eier/claimer kunne derfor lage en «lineær» serie der
    proveniensen forgrenet seg, og den forgrenede versjonen var fortsatt
    signerbar og sendbar. Klarsignalets bevisform er negativ og tas med
    direkte DML — da må påstanden stå i skjemaet, ikke i funksjonen.

    MUTASJONEN SOM DREPER DENNE: ta `oppdrag_id` ut av self-FK-en (tilbake
    til `(tenant, utkast_serie, forrige_liste_id)`)."""
    import uuid as _uuid

    e1, _ = _evaluering(migrator)
    e2, _ = _evaluering(migrator)
    rot = _liste(migrator, e1, hash_="h-rot-dml")
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        migrator.execute(
            "INSERT INTO utsendingsliste (tenant, liste_id, utkast_serie,"
            " forrige_liste_id, oppdrag_id, listetype, malversjon,"
            " innhold_hash, antall)"
            " VALUES (%s,%s,%s,%s,%s,'invitasjon','m@1','h-barn-dml',3)",
            (TENANT, _uuid.uuid4(), rot[1], rot[0], e2))
    migrator.rollback()
    # positiv kontroll: forelderens evalueringsoppdrag -> raden står. Roten
    # selv er upåvirket (NULL forelder ⇒ MATCH SIMPLE sjekker ikke FK-en),
    # ellers ville `_liste` over feilet.
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "INSERT INTO utsendingsliste (tenant, liste_id, utkast_serie,"
        " forrige_liste_id, oppdrag_id, listetype, malversjon,"
        " innhold_hash, antall)"
        " VALUES (%s,%s,%s,%s,%s,'invitasjon','m@1','h-barn-dml-ok',3)",
        (TENANT, _uuid.uuid4(), rot[1], rot[0], e1))
    migrator.commit()


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


@pg
def test_serielaasen_serialiserer_signering_mot_ny_versjon(migrator):
    """#180 (Codex P1 runde 2 + Cursor P1 på #176): spissjekkens TOCTOU.

    Signeringsveien leser «finnes det et barn med
    `forrige_liste_id = liste_id`» og svarer 409 `liste_utdatert`. Sjekken
    og `signer_utsendingsliste` var to steg i samme READ COMMITTED-
    transaksjon, uten lås på serien. Committet en annen transaksjon en
    barnversjon i mellomrommet, var utfallet:

      * hash-ekkoet stemte fortsatt — forelderens `innhold_hash` er uendret
      * `signer_utsendingsliste` verifiserer ikke spiss; den signerer
        hvilken som helst `liste_id`
      * `en_signert_versjon_per_serie` gir serien nøyaktig ÉN signatur-slot

    Altså: feil innhold irreversibelt autorisert, og den faktiske spissen
    permanent usignerbar.

    Migrasjon 065 lar BEGGE veier ta samme advisory-lås på serien. Testen
    måler serialiseringen direkte: A holder serielåsen (som endepunktet
    tar før porten leser), og B skal da ikke komme gjennom
    `opprett_utsendingsliste` før A er ferdig.

    ADVISORY OG IKKE `FOR UPDATE` er ikke en stilsak: PostgreSQL krever
    UPDATE-privilegium for enhver radlåsklausul, også `FOR SHARE`, og
    runtime har kun SELECT på `utsendingsliste`. Radlåsen var ikke nåbar
    fra signeringssiden i det hele tatt.

    BEGGE BEN MÅLES (Cursor P2 på #239). Kappløpet under simulerer
    signeringsbenet med låsuttrykket skrevet ut for hånd — DB-siden av
    #180 blir dermed målt, men Python-siden ikke: fjernet man låsblokken
    i `signer_endepunkt`, sto CI grønt mens spiss-TOCTOU-en var
    gjenåpnet. Kildeporten under binder simuleringen til endepunktets
    faktiske kode, samme form som `test_056_navngir_aldri_runtime_rollen`
    og re-sjekkporten i `test_m57_rapportflate.py`.

    MUTASJONEN SOM DREPER DENNE — én per ben: fjern advisory-låsen fra
    `opprett_utsendingsliste` (065) → B går rett gjennom mens A holder
    sin; ELLER fjern låsblokken fra `signer_endepunkt`
    (`api/rekruttering.py`) → kildeporten faller.
    """
    import inspect
    import threading

    from api import rekruttering

    # SIGNERINGSBENET TAR LÅSEN, OG DEN TAR DEN FØRST. Kappløpet under
    # kan ikke måle dette selv: det går ikke gjennom `signer_endepunkt`,
    # så uten denne porten er endepunktets låsblokk udekket.
    kilde = inspect.getsource(rekruttering.signer_endepunkt)
    assert "hashtextextended('m57:serie:' || %s || ':'" in kilde, (
        "`signer_endepunkt` tar ikke serielåsen — da er spissjekken og"
        " `signer_utsendingsliste` igjen to steg uten lås imellom,"
        " og #180 står åpent på signeringssiden")
    assert (kilde.index("pg_advisory_xact_lock(")
            < kilde.index("EXISTS (SELECT 1 FROM utsendingsliste b")), \
        "serielåsen skal tas FØR spisslesningen — låses det etterpå, er" \
        " vinduet bare ett hakk mindre, ikke lukket"

    oid, _ = _grunnlag(migrator)
    liste = _liste(migrator, oid)
    _sett_kontekst(migrator, TENANT)
    serie, = migrator.execute(
        "SELECT utkast_serie FROM utsendingsliste"
        " WHERE tenant=%s AND liste_id=%s", (TENANT, liste[0])).fetchone()
    migrator.rollback()

    # BEGGE veier trenger EXECUTE på `opprett_utsendingsliste`, og den
    # ligger hos runtime (migrer.py `M37_RETTIGHETER_API`), ikke hos
    # varselsenderen `_sender()` bruker. Migrator med funksjonens egen
    # eierrolle er testenes vanlige vei inn — samme som `_liste`.
    from db.pg import koble

    def _eier():
        k = koble(MIGRATOR_DSN)
        k.execute("SET ROLE disponit_m37_claimer")
        return k

    a = _eier()
    b = _eier()
    try:
        # A tar serielåsen — nøyaktig samme uttrykk som `signer_endepunkt`
        # kjører før spissporten leser.
        _sett_kontekst(a, TENANT)
        a.execute(
            "SELECT pg_advisory_xact_lock("
            "         hashtextextended('m57:serie:' || %s || ':'"
            "                          || utkast_serie::text, 0))"
            "  FROM utsendingsliste WHERE tenant=%s AND liste_id=%s",
            (TENANT, TENANT, liste[0]))

        resultat: dict = {}

        def barneversjon():
            try:
                _sett_kontekst(b, TENANT)
                resultat["id"] = b.execute(
                    "SELECT opprett_utsendingsliste(%s,%s,%s,%s,'invitasjon',"
                    " 'mal@1',%s,1)",
                    (TENANT, serie, liste[0], oid, "b" * 64)).fetchone()[0]
                b.commit()
            except Exception as e:            # noqa: BLE001 — meldes videre
                resultat["feil"] = f"{type(e).__name__}: {e}"

        t = threading.Thread(target=barneversjon)
        t.start()
        t.join(timeout=2)
        assert t.is_alive(), (
            "B opprettet en barnversjon MENS A holdt serielåsen — da er"
            " spissjekken og signaturen fortsatt to steg uten lås imellom,"
            " og #180 står åpent")
        a.commit()
        t.join(timeout=10)
        assert not t.is_alive(), "B kom aldri gjennom etter As commit"
        assert resultat.get("id"), (
            "barneversjonen ble aldri opprettet:"
            f" {resultat.get('feil', 'ingen feil meldt')}")
    finally:
        a.close(); b.close()


# ============================================================
# #159 — revisjonshendelsen (migrasjon 066)
#
# Porten i modulen var SELVATTESTERT: kalleren leverte selv beviset på at
# avskruingen var auditert. Testene her måler den andre halvdelen —
# at hendelsen faktisk er en rad, at raden er udødelig, og at den er
# TENANT-BUNDET. Uten den siste er «auditert» bare «noen, et sted».
# ============================================================

@pg
def test_revisjonshendelsen_skrives_og_slaas_opp(migrator):
    """Den lovlige veien: skriv, les tilbake, gjenkjenn handlingen."""
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        rt.execute(
            "SELECT skriv_revisjonshendelse(%s, 'm57.blinding_avskrudd',"
            " %s, %s)",
            (TENANT, "eier@kunde", "intern rekruttering, avtalt med HR"))
        hid = rt.fetchone()[0]
        assert hid, "skriveren ga ingen hendelses-ID"
        rt.execute("SELECT handling, aktor FROM"
                   " les_revisjonshendelse(%s, %s)", (TENANT, hid))
        rad = rt.fetchone()
        assert rad is not None, "hendelsen kunne ikke slås opp igjen"
        assert rad[0] == "m57.blinding_avskrudd"
        assert rad[1] == "eier@kunde"
    finally:
        rt.rollback()
        rt.close()


@pg
def test_en_fabrikkert_hendelses_id_finnes_ikke(migrator):
    """#159s første negative: en velformet UUID er ikke en rad.

    Det var nettopp dette den gamle formporten ikke kunne måle — en
    strengere kontroll av feltene ville flyttet påstanden ett hakk, ikke
    fjernet den.
    """
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT handling FROM les_revisjonshendelse(%s, %s)",
                   (TENANT, "00000000-0000-4000-8000-000000000000"))
        assert rt.fetchone() is None, (
            "en fabrikkert hendelses-ID ga et oppslag — da er «auditert»"
            " fortsatt en påstand")
    finally:
        rt.rollback()
        rt.close()


@pg
def test_en_hendelse_i_EN_ANNEN_tenant_finnes_ikke_her(migrator):
    """#159s andre negative, og den viktigste.

    Uten tenantbindingen ville en hvilken som helst kundes
    revisjonshendelse autorisert avskruing hos en annen — «auditert» ville
    betydd «noen, et sted, har skrevet noe». RLS-policyen og
    `krev_tenantkontekst` skal begge stå i veien.
    """
    rt = _rt()
    try:
        _sett_kontekst(rt, ANNEN_TENANT)
        rt.execute(
            "SELECT skriv_revisjonshendelse(%s, 'm57.blinding_avskrudd',"
            " %s, %s)", (ANNEN_TENANT, "nabo@annen", "naboens egen sak"))
        fremmed = rt.fetchone()[0]
        # ... og NÅ leser vi den som oss selv.
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT handling FROM les_revisjonshendelse(%s, %s)",
                   (TENANT, fremmed))
        assert rt.fetchone() is None, (
            "naboens revisjonshendelse var synlig her — da autoriserer"
            " naboens lapp vår avskruing")
        # Å be om DEN andre tenanten direkte skal avvises, ikke betjenes:
        # `krev_tenantkontekst` binder tenanten til konteksten.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT handling FROM les_revisjonshendelse(%s, %s)",
                       (ANNEN_TENANT, fremmed))
    finally:
        rt.rollback()
        rt.close()


@pg
def test_revisjonshendelsen_er_udodelig(migrator):
    """Append-only, begge veier: rad OG statement.

    En revisjonshendelse som kan endres eller slettes er ikke et
    revisjonsspor. TRUNCATE fyrer ALDRI radtriggere, så statement-vakten
    er ikke en gjentakelse — den er den andre halvdelen (samme par som
    011/014/036/053/056).

    RADEN SÅS PÅ NYTT FØR HVER MUTASJON (Cursor P1, runde 1 på #247).
    Første utgave sådde ÉN gang utenfor løkka, og `rollback()` etter
    UPDATE-caset angret også seedingen: DELETE traff da null rader,
    `BEFORE DELETE` fyrte aldri, og testen målte ingenting den påsto å
    måle. Derfor tellingen under — en mutasjon som flytter seedingen ut
    igjen skal gjøre testen rød PÅ RADEN, ikke på et uteblitt unntak.
    """
    def _sa_en_hendelse():
        migrator.execute("SET LOCAL disponit.tenant = %s", (TENANT,))
        migrator.execute(
            "INSERT INTO revisjonshendelse (tenant, handling, aktor,"
            " begrunnelse) VALUES (%s, 'm57.blinding_avskrudd', 'drift',"
            " 'manuell kontroll av kandidat') RETURNING hendelse_id",
            (TENANT,))
        return migrator.fetchone()[0]

    for lag_mutasjon in (
        lambda hid: ("UPDATE revisjonshendelse SET aktor = 'noen andre'"
                     " WHERE hendelse_id = %s", (hid,)),
        lambda hid: ("DELETE FROM revisjonshendelse WHERE hendelse_id = %s",
                     (hid,)),
        lambda _hid: ("TRUNCATE revisjonshendelse", ()),
    ):
        hid = _sa_en_hendelse()
        migrator.execute("SELECT count(*) FROM revisjonshendelse"
                         " WHERE hendelse_id = %s", (hid,))
        assert migrator.fetchone()[0] == 1, (
            "raden manglet FØR mutasjonen — da måler caset ingenting:"
            " en DELETE mot null rader fyrer ikke radtriggeren")
        sql, args = lag_mutasjon(hid)
        with pytest.raises(psycopg.errors.RaiseException):
            migrator.execute(sql, args)
        migrator.rollback()


@pg
def test_handlingen_er_et_lukket_sett(migrator):
    """En ny slags revisjonshendelse er en kontraktsendring.

    Uten CHECK-en kunne en kaller skrevet «m57.blinding_avskrudd_liksom»
    og fått en rad som SER ut som beviset porten leter etter.
    """
    migrator.execute("SET LOCAL disponit.tenant = %s", (TENANT,))
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "INSERT INTO revisjonshendelse (tenant, handling, aktor,"
            " begrunnelse) VALUES (%s, 'm57.blinding_avskrudd_liksom',"
            " 'drift', 'ser riktig ut, er det ikke')", (TENANT,))
    migrator.rollback()


@pg
def test_begrunnelsen_kan_ikke_vaere_et_tastetrykk(migrator):
    """Funnet som skapte tabellen brukte «x» som begrunnelse.

    En ubrukelig revisjonshendelse er verre enn ingen: den ser ut som et
    svar på spørsmålet «hvem bestemte dette».
    """
    migrator.execute("SET LOCAL disponit.tenant = %s", (TENANT,))
    for aktor, begrunnelse in (("drift", "x"), ("  ", "en ekte begrunnelse")):
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(
                "INSERT INTO revisjonshendelse (tenant, handling, aktor,"
                " begrunnelse) VALUES (%s, 'm57.blinding_avskrudd', %s, %s)",
                (TENANT, aktor, begrunnelse))
        migrator.rollback()
