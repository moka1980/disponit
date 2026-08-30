"""Portene fra BESLUTNING-168 (docs/pr/BESLUTNING-168-rapportidentitet.md,
migrasjon 072): identiteten er tuppelen (artefakttype, skjemaversjon),
aldri navnet. Numrene i testnavnene er dommens egne porter 1–8."""
import json
import secrets

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       migrator, miljo)
from .test_m57_kandidatlagre import (FIXTUR, _claimet, _prosess,
                                     _reaperkobling, _sett_kontekst)
from .test_m57_utsending import _rt, pg

TYPE = "rekruttering.evaluering.rapport"


def _versjonsdør(m, *args):
    m.execute("SET LOCAL ROLE disponit_domains_admin")
    m.execute("SELECT registrer_artefaktskjemaversjon(%s,%s,%s,%s,%s)",
              args)
    m.execute("RESET ROLE")


def _skjema_hash(m, skjema: dict) -> str:
    import sys
    sys.path.insert(0, "platform/core")
    from api.artefaktskjema import registrer
    m.execute("SET LOCAL ROLE disponit_modules_admin")
    h = registrer(m, skjema, "test")
    m.execute("RESET ROLE")
    return h


def _provisjoner(m, navn) -> str:
    """Registrert artefakttype (test_m57_controller-formen); fødsels-
    triggeren i 072 gir versjon 1 gjeldende i samme innsetting."""
    import sys
    sys.path.insert(0, "platform/core")
    from modules.m57_ats import rapportskjema
    from .test_bestilling_rekruttering import _sikre_m57_claimbar
    _sikre_m57_claimbar(m)   # kontrakt + deployment, idempotent
    h = _skjema_hash(m, rapportskjema.SKJEMA)
    # EGEN kontraktversjon (samme grunn som _fremmed_artefakttype i
    # rapportflate-riggen): kapabilitetsutstedelsen krever NØYAKTIG ÉN
    # registrert type per claim-kontrakt, og registeret er append-only —
    # en testtype på v1 ville stille drept m57-kapabiliteten for hele
    # den delte basen. Den ekte rapporttypen (port 8) ER v1-typen.
    versjon = 1 if navn == TYPE else 4
    if versjon != 1:
        m.execute(
            "INSERT INTO modulkontrakt (modul_id,kontraktversjon,"
            "kontrakt_hash,payload_schema_hash,kvittering_schema_hash,"
            "sideeffektklasse,reversibilitet)"
            " VALUES ('m57_ats',4,%s,'p','k','krever_outbox',"
            "'kompenserende') ON CONFLICT DO NOTHING",
            ("k4-" + secrets.token_hex(8),))
        m.commit()
    khash = m.execute(
        "SELECT kontrakt_hash FROM modulkontrakt"
        " WHERE modul_id='m57_ats' AND kontraktversjon=%s",
        (versjon,)).fetchone()[0]
    m.execute(
        "INSERT INTO artefakttype_register (artefakttype, eiermodul,"
        " kontraktversjon, kontrakt_hash, skjema_hash)"
        " VALUES (%s,'m57_ats',%s,%s,%s) ON CONFLICT DO NOTHING",
        (navn, versjon, khash, h))
    m.commit()
    return navn


def _sikre_rapporttype(m):
    """Den EKTE rapporttypen — kun for port 8 (prefikslukkingen), som
    aldri flipper versjoner på den."""
    _provisjoner(m, TYPE)


def _testtype(m) -> str:
    """Portenes EGEN type: en committet v2-flip på den ekte rapporttypen
    ville lekket inn i resten av riggens suiter (delt base)."""
    return _provisjoner(m, f"test.skjemaversjon.t{secrets.token_hex(4)}")


def _nytt_v2(m) -> str:
    """Registrer et syntetisk v2-skjema og returner hashen."""
    from modules.m57_ats import rapportskjema
    return _skjema_hash(m, rapportskjema.SKJEMA_V2)


@pg
def test_port1_artefakt_uten_registrert_versjon_fk_avvises(migrator):
    oid, _ = _claimet(migrator)
    ttype = _testtype(migrator)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        migrator.execute(
            "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype,"
            " modul_id, release_id, kontraktversjon, kontrakt_hash,"
            " module_epoch, tilstand, storrelse_bytes, klartekst_sha256,"
            " ciphertext, nonce, dek_ref, kapabilitet_jti, skjemaversjon)"
            " SELECT %s, %s, %s, 'm57_ats', r.release_id, 1,"
            "        k.kontrakt_hash, h.module_epoch, 'staged', 10, 'h',"
            "        %s, %s, 'dek', %s, 999"
            "   FROM moduldeployment r, modulkontrakt k, modulhode h"
            "  WHERE r.modul_id='m57_ats' AND k.modul_id='m57_ats'"
            "    AND k.kontraktversjon=1"
            "    AND h.modul_id='m57_ats' LIMIT 1",
            (TENANT, oid, ttype, b"c" * 32, b"n" * 12,
             "jti-" + secrets.token_hex(6)))
    migrator.rollback()


@pg
def test_port3_og_4_en_gjeldende_og_enveis_avvikling(migrator):
    _claimet(migrator)
    ttype = _testtype(migrator)
    _sett_kontekst(migrator, TENANT)
    # Port 3: to gjeldende for samme type er unikavvist — døren gjør
    # flippen atomisk, så rå INSERT er veien til å BEVISE indeksen.
    migrator.execute("SET LOCAL ROLE disponit_domene_eier")
    with pytest.raises(psycopg.errors.UniqueViolation):
        migrator.execute(
            "INSERT INTO artefakttype_versjon (artefakttype,"
            " skjemaversjon, skjema_hash, status)"
            " SELECT %s, 97, skjema_hash, 'gjeldende'"
            "   FROM artefakttype_register WHERE artefakttype=%s",
            (ttype, ttype))
    migrator.rollback()
    # Port 4: avviklet -> gjeldende er trigger-avvist.
    _sett_kontekst(migrator, TENANT)
    v2 = _nytt_v2(migrator)
    _versjonsdør(migrator, ttype, 2, v2, True, "test")
    migrator.execute("SET LOCAL ROLE disponit_domene_eier")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "UPDATE artefakttype_versjon SET status='gjeldende'"
            " WHERE artefakttype=%s AND skjemaversjon=1", (ttype,))
    migrator.rollback()


@pg
def test_port5_promotering_mot_avviklet_avvises(migrator):
    """Et staged artefakt fra før flippen blir aldri varig evidens mot en
    avviklet versjon — registreringsvinduet er synlig og lukket."""
    from db import kryptering
    oid, _ = _claimet(migrator)
    ttype = _testtype(migrator)
    _sett_kontekst(migrator, TENANT)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator, TENANT)
    ct, nonce = kryptering.krypter(dek, {"rapporttype": ttype}, TENANT,
                                   key_id)
    migrator.execute(
        "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype, modul_id,"
        " release_id, kontraktversjon, kontrakt_hash, module_epoch,"
        " tilstand, storrelse_bytes, klartekst_sha256, ciphertext, nonce,"
        " dek_ref, kapabilitet_jti, skjemaversjon)"
        " SELECT %s, %s, %s, 'm57_ats', r.release_id, 1, k.kontrakt_hash,"
        "        h.module_epoch, 'staged', 10, 'h', %s, %s, %s, %s, 1"
        "   FROM moduldeployment r, modulkontrakt k, modulhode h"
        "  WHERE r.modul_id='m57_ats' AND k.modul_id='m57_ats'"
            "    AND k.kontraktversjon=1"
        "    AND h.modul_id='m57_ats' LIMIT 1",
        (TENANT, oid, ttype, ct, nonce, key_id,
         "jti-" + secrets.token_hex(6)))
    v2 = _nytt_v2(migrator)
    _versjonsdør(migrator, ttype, 2, v2, True, "test")   # v1 -> avviklet
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "UPDATE artefakt SET tilstand='promotert', promotert_ts=now()"
            " WHERE tenant=%s AND oppdrag_id=%s AND tilstand='staged'",
            (TENANT, oid))
    migrator.rollback()


@pg
def test_port5_foedt_promotert_mot_avviklet_avvises(migrator):
    """Samme port, andre skrivevei: en rad som FØDES promotert mot en
    avviklet versjon er samme hull som en som promoteres dit."""
    from db import kryptering
    oid, _ = _claimet(migrator)
    ttype = _testtype(migrator)
    _sett_kontekst(migrator, TENANT)
    v2 = _nytt_v2(migrator)
    _versjonsdør(migrator, ttype, 2, v2, True, "test")   # v1 -> avviklet
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator, TENANT)
    ct, nonce = kryptering.krypter(dek, {"rapporttype": ttype}, TENANT,
                                   key_id)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype,"
            " modul_id, release_id, kontraktversjon, kontrakt_hash,"
            " module_epoch, tilstand, storrelse_bytes, klartekst_sha256,"
            " ciphertext, nonce, dek_ref, kapabilitet_jti, skjemaversjon,"
            " promotert_ts)"
            " SELECT %s, %s, %s, 'm57_ats', r.release_id, 1,"
            "        k.kontrakt_hash, h.module_epoch, 'promotert', 10, 'h',"
            "        %s, %s, %s, %s, 1, now()"
            "   FROM moduldeployment r, modulkontrakt k, modulhode h"
            "  WHERE r.modul_id='m57_ats' AND k.modul_id='m57_ats'"
            "    AND k.kontraktversjon=1"
            "    AND h.modul_id='m57_ats' LIMIT 1",
            (TENANT, oid, ttype, ct, nonce, key_id,
             "jti-" + secrets.token_hex(6)))
    migrator.rollback()


@pg
def test_port6_forrige_versjon_er_fk_bundet(migrator):
    _claimet(migrator)
    ttype = _testtype(migrator)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET LOCAL ROLE disponit_domene_eier")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        migrator.execute(
            "INSERT INTO artefakttype_versjon (artefakttype,"
            " skjemaversjon, skjema_hash, forrige_versjon, status)"
            " SELECT %s, 98, skjema_hash, 55, 'avviklet'"
            "   FROM artefakttype_register WHERE artefakttype=%s",
            (ttype, ttype))
    migrator.rollback()


@pg
def test_port7_a1_fullt_rapporten_bestaar_med_null_treff(migrator, miljo):
    """A1 begge halvdeler (dommens §4): etter reaping FINNES det
    payloadfrie beslutningssporet fortsatt — og det har null treff av
    kandidatpayloaden. En rapport som forsvant ville også gitt null
    treff; derfor måles eksistensen først. v1-naboen på samme oppdrag
    makuleres som før (067)."""
    from db import kryptering
    rt = _rt()
    try:
        # `ett_promotert_per_oppdrag`: v1-legacyen og v2-sporet bor på
        # hver sin prosess — nøyaktig som i drift, der de tre gamle
        # v1-rapportene har sine egne oppdrag.
        oid, pid = _prosess(migrator, rt, frist=90)
        oid2, pid2 = _prosess(migrator, rt, frist=90)
        ttype = _testtype(migrator)
        _sett_kontekst(migrator, TENANT)
        key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator,
                                                              TENANT)
        v2 = _nytt_v2(migrator)

        def artefakt(versjon, innhold, *, oppdrag=None):
            ct, nonce = kryptering.krypter(dek, innhold, TENANT, key_id)
            migrator.execute(
                "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype,"
                " modul_id, release_id, kontraktversjon, kontrakt_hash,"
                " module_epoch, tilstand, storrelse_bytes,"
                " klartekst_sha256, ciphertext, nonce, dek_ref,"
                " kapabilitet_jti, promotert_ts, skjemaversjon)"
                " SELECT %s, %s, %s, 'm57_ats', r.release_id, 1,"
                "        k.kontrakt_hash, h.module_epoch, 'promotert',"
                "        10, 'h', %s, %s, %s, %s, now(), %s"
                "   FROM moduldeployment r, modulkontrakt k, modulhode h"
                "  WHERE r.modul_id='m57_ats' AND k.modul_id='m57_ats'"
            "    AND k.kontraktversjon=1"
                "    AND h.modul_id='m57_ats' LIMIT 1",
                (TENANT, oppdrag if oppdrag is not None else oid,
                 ttype, ct, nonce, key_id,
                 "jti-" + secrets.token_hex(6), versjon))

        # v1 bærer payloaden (fixture-strengen) og promoteres FØR flippen
        # — nøyaktig som de tre driftsrapportene; å føde den promotert
        # mot en avviklet versjon avvises nå av vakten (port 5).
        artefakt(1, {"rapporttype": ttype, "versjon": 1,
                     "kandidater": {"k1": {"kildetekst": FIXTUR}}},
                 oppdrag=oid)
        _versjonsdør(migrator, ttype, 2, v2, True, "test")
        artefakt(2, {"rapporttype": ttype, "versjon": 2,
                     "rangering": [{"kandidat_id": "k1", "poeng": 3,
                                    "nedbrytning": {"drift": 3}}]},
                 oppdrag=oid2)
        _sett_kontekst(rt, TENANT)
        for p in (pid, pid2):
            rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                       " now() - interval '91 days')", (TENANT, p))
        rt.commit()
        migrator.commit()
        rp, _timer = _reaperkobling()
        try:
            reapet = rp.execute(
                "SELECT * FROM reap_kandidatdata(50)").fetchall()
            rp.commit()
        finally:
            rp.close()
        tatt = [(r[0], r[1]) for r in reapet]
        assert (TENANT, pid) in tatt and (TENANT, pid2) in tatt

        _sett_kontekst(migrator, TENANT)
        rader = migrator.execute(
            "SELECT skjemaversjon, ciphertext IS NOT NULL,"
            "       nonce, dek_ref, ciphertext, makulert_ts IS NOT NULL"
            "  FROM artefakt WHERE tenant=%s AND oppdrag_id = ANY(%s)"
            " ORDER BY skjemaversjon", (TENANT, [oid, oid2])).fetchall()
        migrator.rollback()
        pr_versjon = {r[0]: r for r in rader}
        # v1: makulert som i 067 — payloaden er borte.
        assert pr_versjon[1][5] and not pr_versjon[1][1], \
            "v1-payloaden overlevde fristen"
        # v2 HALVDEL 1: rapporten FINNES — umakulert, med payload intakt.
        assert pr_versjon[2][1] and not pr_versjon[2][5], \
            "beslutningssporet ble revet av reaperen — varig evidens borte"
        # v2 HALVDEL 2: null treff av kandidatpayloaden i det som består.
        v2rad = pr_versjon[2]
        _sett_kontekst(migrator, TENANT)
        dek2 = kryptering.hent_dek(migrator, TENANT, v2rad[3])
        innhold = kryptering.dekrypter(dek2, bytes(v2rad[4]),
                                       bytes(v2rad[2]), TENANT, v2rad[3])
        migrator.rollback()
        assert FIXTUR not in json.dumps(innhold), \
            "kandidatpayload i beslutningssporet ETTER reaping"
    finally:
        rt.close()


@pg
def test_port8_prefikslukkingen_staar(migrator):
    """Regresjon: navneformen fikk INGEN dispensasjon — `…rapport.v2`
    avvises fortsatt som overlapp. Det er navneregisterets eget bevis
    for at versjonen hører i tuppelen."""
    _claimet(migrator)
    _sikre_rapporttype(migrator)
    _sett_kontekst(migrator, TENANT)
    khash = migrator.execute(
        "SELECT kontrakt_hash FROM modulkontrakt"
        " WHERE modul_id='m57_ats' AND kontraktversjon=1").fetchone()[0]
    shash = migrator.execute(
        "SELECT skjema_hash FROM artefakttype_register"
        " WHERE artefakttype=%s", (TYPE,)).fetchone()[0]
    migrator.execute("SET LOCAL ROLE disponit_domains_admin")
    # Overlapp-porten reiser unique_violation med sin egen ordlyd (035).
    with pytest.raises(psycopg.errors.UniqueViolation) as e:
        migrator.execute(
            "SELECT registrer_artefakttype(%s, 'm57_ats', 1, %s, %s,"
            " 'test')", (TYPE + ".v2", khash, shash))
    migrator.rollback()
    assert "overlapper" in str(e.value), e.value


@pg
def test_versjonsdoren_er_idempotent_og_lineaer(migrator):
    _claimet(migrator)
    ttype = _testtype(migrator)
    _sett_kontekst(migrator, TENANT)
    v2 = _nytt_v2(migrator)
    _versjonsdør(migrator, ttype, 2, v2, True, "test")
    # Idempotent på identisk innhold …
    _versjonsdør(migrator, ttype, 2, v2, True, "test")
    # … immutabel på avvik …
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _versjonsdør(migrator, ttype, 2, v2, False, "test")
    migrator.rollback()
    # … og aldri bakover.
    _sett_kontekst(migrator, TENANT)
    v2 = _nytt_v2(migrator)
    _versjonsdør(migrator, ttype, 2, v2, True, "test")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _versjonsdør(migrator, ttype, 1, v2, False, "test")
    migrator.rollback()
    # Lineage: v2 peker på v1, og statusene flippet i samme transaksjon.
    _sett_kontekst(migrator, TENANT)
    v2 = _nytt_v2(migrator)
    _versjonsdør(migrator, ttype, 2, v2, True, "test")
    # Aktøren SKRIVES (036-presedensen) — og idempotent no-op lager
    # ingen ny hendelse.
    _versjonsdør(migrator, ttype, 2, v2, True, "test")
    hendelser = migrator.execute(
        "SELECT aktor FROM modulregister_hendelse"
        " WHERE hendelse='artefaktskjemaversjon_registrert'"
        "   AND detalj->>'artefakttype'=%s", (ttype,)).fetchall()
    assert hendelser == [("test",)]
    rader = dict(migrator.execute(
        "SELECT skjemaversjon, status FROM artefakttype_versjon"
        " WHERE artefakttype=%s", (ttype,)).fetchall())
    forrige = migrator.execute(
        "SELECT forrige_versjon FROM artefakttype_versjon"
        " WHERE artefakttype=%s AND skjemaversjon=2", (ttype,)).fetchone()[0]
    migrator.rollback()
    assert rader == {1: "avviklet", 2: "gjeldende"} and forrige == 1
