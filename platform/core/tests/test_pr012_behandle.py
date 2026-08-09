"""PR-012 Increment 3: `behandle_unntakshandling` — hele porten ende-til-ende.

Mot EKTE Postgres: en MAC-signert godkjenning matet som en NY beslutning gir
TILLAT + revisjonslogg + godkjenningsutfall + venter_utførelse; en ugyldig MAC
og et bindingsavvik gir STOPP + sikkerhetsevidens (V3, egen forbindelse) uten
statusendring.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from api.mac_register import MacRegister
from api.minimering import bygg_handlingsintensjon
from api.unntaksbehandling import (behandle_unntakshandling,
                                   opprett_godkjenningsrunde,
                                   skriv_sikkerhetsevidens)
from policy_validator.engine import _policy_innholds_hash
from .test_api import DSN, KEK, MIGRATOR_DSN

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
NAA = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
TEN = "t-beh"

POL = {
    "meta": {"policy_id": "test-mg", "versjon": "1.0.0"},
    "tidssone": "Europe/Oslo",
    "handlinger": [{
        "id": "faktura.bokfor", "modus": "auto", "ved_brudd": "unntakskø",
        "tillatt_for": ["agent"],
        "grenser": {"belop_maks": "25000.00", "valuta": ["NOK"]}}],
    "menneskelig_overstyring": {
        "godkjennbare": [{"grunnkode": "belop_over_grense",
                          "handling": "faktura.bokfor",
                          "belop_maks": "50000.00", "valuta": "NOK"}],
        "krever_rolle": "okonomi"},
}
POL_HASH = _policy_innholds_hash(POL)


class _Pool:
    """Minimal pool for V3-evidens: fersk forbindelse per hent."""
    def hent(self, timeout=5.0):
        from db.pg import koble
        return koble(DSN)

    def gi_tilbake(self, conn):
        conn.close()


@pytest.fixture()
def conn(monkeypatch):
    from db.pg import koble, migrer
    monkeypatch.setenv("DISPONIT_KEK", KEK)
    monkeypatch.setattr("api.unntaksbehandling.policyregister.hent_aktiv",
                        lambda conn, tenant, pid: (POL, POL_HASH))
    m = koble(MIGRATOR_DSN)
    migrer(m)
    m.commit()
    m.close()
    c = koble(DSN)
    yield c
    c.close()


def _oppsett_sak(conn, policy=POL, phash=POL_HASH):
    """Manuell, godkjennbar sak med ekte kryptert intensjon + åpen runde."""
    from api.kjerne import _skriv_unntak
    from db.pg import sett_kontekst
    import types

    sett_kontekst(conn, TEN, "sys", "r0")
    lid = conn.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse) VALUES (%s,'h','test-mg@1.0.0/faktura.bokfor','UNNTAK',"
        "%s::jsonb) RETURNING id",
        (TEN, '[{"kode":"rolle_ok","params":{"rolle":"agent"}},'
              '{"kode":"belop_over_grense"}]')).fetchone()[0]
    snap = types.SimpleNamespace(maks_auto_forsok=3, versjon="1.0.0",
                                 innholds_hash=phash)
    event = {"handling": "faktura.bokfor", "belop": "45000.00", "valuta": "NOK",
             "ressurs_id": "fak-1"}
    intensjon = bygg_handlingsintensjon(event, "agent")
    uid = _skriv_unntak(conn, TEN, lid, "faktura.bokfor", "over_grense",
                        "normal", "normal", {"handling": "faktura.bokfor"},
                        snap, intensjon)
    conn.execute("UPDATE unntak SET status='manuell' WHERE tenant=%s AND id=%s",
                 (TEN, uid))
    opprett_godkjenningsrunde(conn, tenant=TEN, unntak_id=uid, aktor="sys",
                              request_id="r0", policy=policy, policy_hash=phash,
                              naa=NAA)
    hi_hash = conn.execute("SELECT hi_integritet_hash FROM unntak WHERE"
                           " tenant=%s AND id=%s", (TEN, uid)).fetchone()[0]
    conn.commit()
    return uid, hi_hash


def _macreg():
    return MacRegister({"mk1": {"rolle": "signerer", "hemmelighet": "z" * 40}})


def _konvolutt(uid, hi_hash, *, bruker="op1", ghash=POL_HASH, **over):
    k = {"konvoluttversjon": 2, "operatorhandling": "godkjenn", "tenant": TEN,
         "unntak_id": uid, "runde": 1, "target_action": "faktura.bokfor",
         "ressurs_id": "fak-1", "belop": "45000.00", "valuta": "NOK",
         "hi_integritet_hash": hi_hash, "bundet_grunnkode": "belop_over_grense",
         "godkjennings_policy_hash": ghash, "bruker_id": bruker,
         "rolle": "okonomi", "authz_version": 1,
         "jti": f"{bruker}{uid}" + "j" * 22,   # uid gjør jti unik per kjøring
         "utloper": (NAA + timedelta(hours=1)).isoformat()}
    k.update(over)
    return k


def _signer(reg, konvolutt):
    mac_key_id, mac = reg.signer(konvolutt)
    return {**konvolutt, "mac": mac, "mac_key_id": mac_key_id}


@pg
def test_godkjenning_gir_tillat_og_venter_utforelse(conn):
    uid, hi_hash = _oppsett_sak(conn)
    reg = _macreg()
    konv = _signer(reg, _konvolutt(uid, hi_hash))
    res = behandle_unntakshandling(conn, _Pool(), reg, tenant=TEN, aktor="op1",
                                   request_id="r1", konvolutt=konv, naa=NAA)
    assert res["utfall"] == "TILLAT"
    assert "menneskelig_godkjenning_anvendt" in res["begrunnelse"]

    c2 = _reconn()
    st = c2.execute("SELECT status FROM unntak WHERE tenant=%s AND id=%s",
                    (TEN, uid)).fetchone()[0]
    utfall = c2.execute("SELECT motorutfall FROM godkjenningsutfall WHERE"
                        " tenant=%s AND unntak_id=%s", (TEN, uid)).fetchone()
    runde_st = c2.execute("SELECT status FROM godkjenningsrunde WHERE tenant=%s"
                          " AND unntak_id=%s AND runde=1", (TEN, uid)).fetchone()[0]
    c2.close()
    assert st == "venter_utførelse"
    assert utfall == ("TILLAT_OUTBOX",)
    assert runde_st == "brukt"


@pg
def test_ugyldig_mac_gir_sikkerhetsstopp_uten_statusendring(conn):
    uid, hi_hash = _oppsett_sak(conn)
    reg = _macreg()
    konv = _signer(reg, _konvolutt(uid, hi_hash))
    konv["mac"] = "0" * 64   # forfalsket signatur
    res = behandle_unntakshandling(conn, _Pool(), reg, tenant=TEN, aktor="op1",
                                   request_id="r1", konvolutt=konv, naa=NAA)
    assert res["utfall"] == "STOPP" and res["sikkerhet"] == "mac_ugyldig"

    c2 = _reconn()
    st = c2.execute("SELECT status FROM unntak WHERE tenant=%s AND id=%s",
                    (TEN, uid)).fetchone()[0]
    evid = c2.execute("SELECT count(*) FROM unntak_historikk WHERE tenant=%s AND"
                      " unntak_id=%s AND hendelse='godkjenning_stoppet_av_policy'",
                      (TEN, uid)).fetchone()[0]
    c2.close()
    assert st == "venter_godkjenning"   # uendret
    assert evid == 1                    # evidensen overlevde (egen forbindelse)


@pg
def test_bindingsavvik_gir_sikkerhetsstopp(conn):
    uid, hi_hash = _oppsett_sak(conn)
    reg = _macreg()
    # Gyldig signatur, men konvolutten peker på feil sak (hi-hash).
    konv = _signer(reg, _konvolutt(uid, hi_hash, hi_integritet_hash="f" * 64))
    res = behandle_unntakshandling(conn, _Pool(), reg, tenant=TEN, aktor="op1",
                                   request_id="r1", konvolutt=konv, naa=NAA)
    assert res["utfall"] == "STOPP"
    assert res["sikkerhet"] == "bindingsavvik:hi_integritet_hash"


POL_FIRE = {**POL, "menneskelig_overstyring": {
    **POL["menneskelig_overstyring"], "krever_fire_oyne": True}}
POL_FIRE_HASH = _policy_innholds_hash(POL_FIRE)


@pg
def test_fire_oyne_forste_venter_andre_gir_tillat(conn, monkeypatch):
    monkeypatch.setattr("api.unntaksbehandling.policyregister.hent_aktiv",
                        lambda conn, tenant, pid: (POL_FIRE, POL_FIRE_HASH))
    uid, hi_hash = _oppsett_sak(conn, policy=POL_FIRE, phash=POL_FIRE_HASH)
    reg = _macreg()

    # Første godkjenner: venter på nummer to (ingen beslutning ennå).
    k1 = _signer(reg, _konvolutt(uid, hi_hash, bruker="op1", ghash=POL_FIRE_HASH))
    r1 = behandle_unntakshandling(conn, _Pool(), reg, tenant=TEN, aktor="op1",
                                  request_id="r1", konvolutt=k1, naa=NAA)
    assert r1["utfall"] == "venter_andre_godkjenner" and r1["gjenstaar"] == 1

    # Samme bruker igjen → fire-øyne-brudd (UNIQUE).
    from api.unntaksbehandling import Godkjenningsfeil
    k1b = _signer(reg, _konvolutt(uid, hi_hash, bruker="op1",
                                  ghash=POL_FIRE_HASH,
                                  jti=f"op1b{uid}" + "j" * 22))
    with pytest.raises(Godkjenningsfeil):
        behandle_unntakshandling(conn, _Pool(), reg, tenant=TEN, aktor="op1",
                                 request_id="r1b", konvolutt=k1b, naa=NAA)

    # Andre, ULIKE godkjenner: terskel nådd → TILLAT.
    k2 = _signer(reg, _konvolutt(uid, hi_hash, bruker="op2", ghash=POL_FIRE_HASH))
    r2 = behandle_unntakshandling(conn, _Pool(), reg, tenant=TEN, aktor="op2",
                                  request_id="r2", konvolutt=k2, naa=NAA)
    assert r2["utfall"] == "TILLAT"


def _reconn():
    from db.pg import koble, sett_tenant
    c = koble(DSN)
    sett_tenant(c, TEN)
    return c
