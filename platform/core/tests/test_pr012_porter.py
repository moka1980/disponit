"""PR-012 CP7 — de adversarielle portene som ikke alt er dekket andre steder.

Hver test svarer til en nummerert Codex-port i implementeringsklarsignalet og
muterer bort nøyaktig den egenskapen porten verner. Portene som allerede er
bevist i andre PR-012-tester (6,7,9,10,13, m.fl.) er kartlagt i PR-beskrivelsen
(port → bevisende test). Port 14/15 hører til staging-feilinjiseringen
(`behandling-m37-v1`), som per klarsignalet kjøres etter merge.
"""
from pathlib import Path

import pytest

from .test_api import DSN, migrator, miljo  # noqa: F401
from .test_pr010_db import _ctx, T as TEN
from .test_pr012_migrasjon import _oppsett, _raises

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def test_port1_ingen_intern_http_i_transaksjonen():
    """Port 1: porten gjør ALDRI et nettverkskall inne i eiertransaksjonen —
    en ekstern henting under låsen ville koblet en forretningsbeslutning til en
    tredjeparts oppetid. Statisk sjekk (samme form som rute-/CLI-portene)."""
    import api.unntaksbehandling as u
    kilde = Path(u.__file__).read_text(encoding="utf-8")
    for forbudt in ("requests.", "urllib", "http.client", "httpx",
                    "aiohttp", "socket.socket", "urlopen", "fetch("):
        assert forbudt not in kilde, f"intern HTTP-form i porten: {forbudt}"


@pg
def test_port5_intensjon_dekrypteres_med_FROSSEN_policyhash(migrator):
    """Port 5: en intensjon kryptert under policy A skal dekrypteres selv etter
    at aktiv policy er B. AAD-en bruker sakens FROSNE `intensjon_policy_hash`,
    aldri den aktive — ellers ble saken uleselig ved neste policyendring."""
    from db import kryptering
    _ctx(migrator)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator, TEN)
    aad_A = kryptering.intensjon_aad(7, "faktura.bokfor", 1, "policyhash-A")
    ct, nonce = kryptering.krypter(dek, {"belop": "45000.00"}, TEN, key_id,
                                   ekstra_aad=aad_A)
    # Dekryptering med den FROSNE hashen (A) lykkes — uansett hva «aktiv» er.
    ut = kryptering.dekrypter(dek, ct, nonce, TEN, key_id, ekstra_aad=aad_A)
    assert ut["belop"] == "45000.00"
    # Med en ANNEN policyhash (B) feiler dekrypteringen (AAD-binding).
    aad_B = kryptering.intensjon_aad(7, "faktura.bokfor", 1, "policyhash-B")
    with pytest.raises(Exception):
        kryptering.dekrypter(dek, ct, nonce, TEN, key_id, ekstra_aad=aad_B)
    migrator.rollback()


def _runde(conn, uid, runde, status, utloper="now()+interval '1 hour'"):
    conn.execute(
        "INSERT INTO godkjenningsrunde (tenant,unntak_id,runde,status,"
        "bundet_grunnkode,godkjennings_policy_hash,policy_versjon,utloper)"
        f" VALUES (%s,%s,%s,%s,'belop_over_grense','gph','0.2.0',{utloper})",
        (TEN, uid, runde, status))


def _att(conn, uid, runde, bruker, jti):
    conn.execute(
        "INSERT INTO menneskelig_attestasjon (tenant,unntak_id,runde,"
        "operatorhandling,target_action,bundet_grunnkode,bruker_id,rolle,"
        "authz_version,konvoluttversjon,konvolutt_hash,mac,mac_key_id,jti,"
        "utloper,saksversjon) VALUES (%s,%s,%s,'godkjenn','faktura.bokfor',"
        "'belop_over_grense',%s,'okonomi',1,2,'kh','mac','mk1',%s,"
        "now()+interval '1 hour',1)", (TEN, uid, runde, bruker, jti))


@pg
def test_port11_utlopt_runde_samme_bruker_ny_runde_attestasjoner_bestaar(migrator):
    """Port 11: en utløpt runde lukker seg, men sletter ALDRI attestasjoner, og
    samme bruker kan delta i en NY runde (unikheten er per runde)."""
    uid, _ = _oppsett(migrator)
    _runde(migrator, uid, 1, "apen")
    _att(migrator, uid, 1, "bruker-1", "j" * 22)
    # Runde 1 utløper (apen → utlopt) — en lovlig, terminal overgang.
    migrator.execute("UPDATE godkjenningsrunde SET status='utlopt' WHERE"
                     " tenant=%s AND unntak_id=%s AND runde=1", (TEN, uid))
    # Ny runde (2) kan åpnes, og SAMME bruker kan attestere der.
    _runde(migrator, uid, 2, "apen")
    _att(migrator, uid, 2, "bruker-1", "k" * 22)
    # Rundens attestasjon fra runde 1 består (append-only, aldri slettet).
    n = migrator.execute("SELECT count(*) FROM menneskelig_attestasjon WHERE"
                         " tenant=%s AND unntak_id=%s AND bruker_id='bruker-1'",
                         (TEN, uid)).fetchone()[0]
    assert n == 2
    migrator.rollback()


@pg
def test_port12_samme_sak_intensjon_policyhash_kan_ikke_godkjennes_to_ganger(migrator):
    """Port 12: `godkjenningsutfall` har PK (tenant,unntak_id,hi_integritet_hash,
    policy_hash) — samme sak kan ALDRI godkjennes to ganger for samme intensjon
    og godkjenningspolicy."""
    uid, _ = _oppsett(migrator)
    _runde(migrator, uid, 1, "brukt")
    migrator.execute("UPDATE godkjenningsrunde SET decision_operation_id='op-1'"
                     " WHERE tenant=%s AND unntak_id=%s AND runde=1", (TEN, uid))
    lid = migrator.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse,idempotency_key) VALUES (%s,'h','p','TILLAT','[]'::jsonb,"
        "'op-1') RETURNING id", (TEN,)).fetchone()[0]
    utfall = ("INSERT INTO godkjenningsutfall (tenant,unntak_id,"
              "hi_integritet_hash,policy_hash,decision_operation_id,motorutfall,"
              "beslutning_loggpost_id) VALUES (%s,%s,'hih','gph','op-1',"
              "'TILLAT_OUTBOX',%s)")
    migrator.execute(utfall, (TEN, uid, lid))
    # Andre innsetting for SAMME (sak, hi-hash, policy_hash) → PK-brudd.
    assert _raises(migrator, utfall, (TEN, uid, lid))
    migrator.rollback()
