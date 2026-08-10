"""PR-012 Increment 3 + P1-fikser: `behandle_unntakshandling` ende-til-ende.

Alt skjer under saks­låsen. De tre vaktene Codex krevde negative tester for:
optimistisk lås (`saksversjon`), idempotens (identisk + avvikende replay), og
REAUTORISERING etter låsen (medlemskap/scope fjernet i vinduet). Hver test
muterer bort én vakt og dør.
"""
import hashlib
from datetime import datetime, timezone

import pytest

from api.mac_register import MacRegister
from api.minimering import bygg_handlingsintensjon
from api.unntaksbehandling import Godkjenningsfeil, behandle_unntakshandling
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
POL_FIRE = {**POL, "menneskelig_overstyring": {
    **POL["menneskelig_overstyring"], "krever_fire_oyne": True}}
POL_FIRE_HASH = _policy_innholds_hash(POL_FIRE)


class _Pool:
    def hent(self, timeout=5.0):
        from db.pg import koble
        return koble(DSN)

    def gi_tilbake(self, conn):
        conn.close()


def _macreg():
    return MacRegister({"mk1": {"rolle": "signerer", "hemmelighet": "z" * 40}})


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


def _oppsett(conn, phash=POL_HASH):
    """Manuell, godkjennbar sak med ekte kryptert intensjon (INGEN runde —
    behandle_unntakshandling åpner den under låsen)."""
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
    ev = {"handling": "faktura.bokfor", "belop": "45000.00", "valuta": "NOK",
          "ressurs_id": "fak-1"}
    uid = _skriv_unntak(conn, TEN, lid, "faktura.bokfor", "over_grense",
                        "normal", "normal", {"handling": "faktura.bokfor"},
                        snap, bygg_handlingsintensjon(ev, "agent"))
    conn.execute("UPDATE unntak SET status='manuell' WHERE tenant=%s AND id=%s",
                 (TEN, uid))
    conn.commit()
    return uid


def _medlem(conn, sub, roller="ARRAY['godkjenner','okonomi']"):
    # brukermedlemskap er OIDC-forvaltet (FK til brukeridentitet, runtime-rollen
    # kan SELECT men ikke INSERT). Opprett identitet + medlemskap via en
    # privilegert (migrator) forbindelse, som i drift. Returnerer bruker_id.
    from db.pg import koble, sett_kontekst
    from .test_pr010_db import _identitet
    m = koble(MIGRATOR_DSN)
    sett_kontekst(m, TEN, "sys", "r0")
    bid = _identitet(m, sub=f"{TEN}-{sub}")
    m.execute(f"INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
              f" VALUES (%s,%s,{roller})"
              f" ON CONFLICT (tenant,bruker_id) DO UPDATE SET"
              f" roller=EXCLUDED.roller", (TEN, bid))
    m.commit()
    m.close()
    return bid


def _sv(conn, uid):
    from db.pg import sett_tenant
    sett_tenant(conn, TEN)
    v = conn.execute("SELECT saksversjon FROM unntak WHERE tenant=%s AND id=%s",
                     (TEN, uid)).fetchone()[0]
    conn.rollback()
    return v


def _kall(conn, uid, oh, bid, reg, *, saksversjon=None, idem=None):
    if saksversjon is None:
        saksversjon = _sv(conn, uid)
    if idem is None:
        idem = f"idem-{uid}-{oh}-{bid}"
    ih = hashlib.sha256(
        f"{TEN}\x1f{bid}\x1f{uid}\x1f{oh}\x1f{saksversjon}".encode()).hexdigest()
    return behandle_unntakshandling(
        conn, _Pool(), reg, tenant=TEN, aktor=bid, request_id="r",
        unntak_id=uid, operatorhandling=oh, forventet_saksversjon=saksversjon,
        idempotency_key=idem, input_hash=ih, naa=NAA)


def _status(conn, uid):
    from db.pg import sett_tenant
    sett_tenant(conn, TEN)
    s = conn.execute("SELECT status FROM unntak WHERE tenant=%s AND id=%s",
                     (TEN, uid)).fetchone()[0]
    conn.rollback()
    return s


@pg
def test_godkjenn_gir_tillat_og_venter_utforelse(conn):
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    res = _kall(conn, uid, "godkjenn", bid, _macreg())
    assert res["utfall"] == "TILLAT"
    assert "menneskelig_godkjenning_anvendt" in res["begrunnelse"]
    assert _status(conn, uid) == "venter_utførelse"


@pg
def test_stale_saksversjon_stoppes_uten_sideeffekt(conn):
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    with pytest.raises(Godkjenningsfeil) as ei:
        _kall(conn, uid, "godkjenn", bid, _macreg(), saksversjon=999)
    assert ei.value.kode == "saksversjon_utdatert"
    conn.rollback()
    # Ingen sideeffekt: saken er fortsatt manuell, ingen runde åpnet.
    assert _status(conn, uid) == "manuell"
    from db.pg import sett_tenant
    sett_tenant(conn, TEN)
    n = conn.execute("SELECT count(*) FROM godkjenningsrunde WHERE tenant=%s AND"
                     " unntak_id=%s", (TEN, uid)).fetchone()[0]
    conn.rollback()
    assert n == 0


@pg
def test_idempotens_identisk_og_avvikende_replay(conn):
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    sv = _sv(conn, uid)
    nokkel = f"nokkel-{uid}"   # uid gjør nøkkelen unik per kjøring
    r1 = _kall(conn, uid, "godkjenn", bid, _macreg(), saksversjon=sv,
               idem=nokkel)
    assert r1["utfall"] == "TILLAT"
    # Identisk replay (samme nøkkel + samme input) → samme lagrede respons.
    r2 = _kall(conn, uid, "godkjenn", bid, _macreg(), saksversjon=sv,
               idem=nokkel)
    assert r2.get("replay") is True and r2["utfall"] == "TILLAT"
    # Avvikende replay (samme nøkkel, ANNET input) → sikkerhetsstopp.
    r3 = _kall(conn, uid, "godkjenn", bid, _macreg(), saksversjon=sv + 5,
               idem=nokkel)
    assert r3["utfall"] == "STOPP" and r3["sikkerhet"] == "idempotenskonflikt"
    # Port 3: sikkerhetsevidensen ER persistert (egen forbindelse), selv om
    # forretnings-tx-en ble rullet tilbake.
    from db.pg import sett_tenant
    sett_tenant(conn, TEN)
    ev = conn.execute("SELECT count(*) FROM unntak_historikk WHERE tenant=%s AND"
                      " unntak_id=%s AND hendelse='godkjenning_stoppet_av_policy'",
                      (TEN, uid)).fetchone()[0]
    conn.rollback()
    assert ev >= 1


@pg
def test_port2_uten_aktiv_runde_avbrytes_ingen_ny_beslutning(conn):
    # Port 2: menneskeflyt uten aktiv runde → avbrutt, ingen ny beslutning.
    from db.pg import sett_tenant
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    _kall(conn, uid, "godkjenn", bid, _macreg())   # → venter_utførelse, runde brukt
    sett_tenant(conn, TEN)
    logg_for = conn.execute("SELECT count(*) FROM revisjonslogg WHERE tenant=%s",
                            (TEN,)).fetchone()[0]
    conn.rollback()
    # Andre godkjenn: ingen aktiv runde + sak ikke manuell → avbrutt.
    with pytest.raises(Godkjenningsfeil) as ei:
        _kall(conn, uid, "godkjenn", bid, _macreg(), idem=f"port2-{uid}")
    assert ei.value.kode == "runde_ulovlig_tilstand"
    conn.rollback()
    sett_tenant(conn, TEN)
    logg_etter = conn.execute("SELECT count(*) FROM revisjonslogg WHERE tenant=%s",
                              (TEN,)).fetchone()[0]
    conn.rollback()
    assert logg_etter == logg_for   # ingen ny beslutning skrevet


@pg
def test_reautorisering_etter_laas_mangler_medlemskap(conn):
    uid = _oppsett(conn)
    # INGEN medlemskap opprettet → reauth etter låsen feiler fail-closed.
    with pytest.raises(Godkjenningsfeil) as ei:
        _kall(conn, uid, "godkjenn", "op-uten", _macreg())
    assert ei.value.kode == "mangler_medlemskap"
    conn.rollback()
    assert _status(conn, uid) == "manuell"


@pg
def test_reautorisering_scope_fjernet_stoppes(conn):
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1", roller="ARRAY['leser']")   # ingen approve-scope
    with pytest.raises(Godkjenningsfeil) as ei:
        _kall(conn, uid, "godkjenn", bid, _macreg())
    assert ei.value.kode == "scope_mangler"
    conn.rollback()
    assert _status(conn, uid) == "manuell"


@pg
def test_reautorisering_skjer_ETTER_saks_laasen(conn):
    """Bevis at reauth skjer etter FOR UPDATE, ikke bare at koden står der.

    Deterministisk vindu: B låser saken OG sletter medlemskapet (UCOMMITTED),
    A (behandle) blokkerer på saks­låsen, B committer. A slipper løs, låser, og
    leser DA medlemskapet — som nå er borte. Var reauth FØR låsen, ville A lest
    medlemskapet mens B-slettingen ennå var usynlig (MVCC) og sluppet gjennom.
    """
    import threading
    import time
    from db.pg import koble, sett_kontekst

    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    sv = _sv(conn, uid)

    b = koble(MIGRATOR_DSN)
    sett_kontekst(b, TEN, "sys", "rb")
    b.execute("SELECT id FROM unntak WHERE tenant=%s AND id=%s FOR UPDATE",
              (TEN, uid))
    b.execute("DELETE FROM brukermedlemskap WHERE tenant=%s AND bruker_id=%s",
              (TEN, bid))

    ut = {}

    def kjor_a():
        a = koble(DSN)
        try:
            ih = hashlib.sha256(
                f"{TEN}\x1f{bid}\x1f{uid}\x1fgodkjenn\x1f{sv}".encode()).hexdigest()
            behandle_unntakshandling(
                a, _Pool(), _macreg(), tenant=TEN, aktor=bid, request_id="ra",
                unntak_id=uid, operatorhandling="godkjenn",
                forventet_saksversjon=sv, idempotency_key="traad-nokkel",
                input_hash=ih, naa=NAA)
            ut["kode"] = "INGEN_FEIL"   # reauth skjedde IKKE etter låsen
        except Godkjenningsfeil as e:
            ut["kode"] = e.kode
        finally:
            a.close()

    ta = threading.Thread(target=kjor_a)
    ta.start()
    time.sleep(1.0)                     # la A blokkere på saks­låsen
    assert not ut, "A skulle fortsatt blokkere på FOR UPDATE"
    b.commit()                         # slipp låsen + commit slettingen
    b.close()
    ta.join(timeout=10)
    assert ut.get("kode") == "mangler_medlemskap"


@pg
def test_fire_oyne_krever_to_ulike_godkjennere(conn, monkeypatch):
    monkeypatch.setattr("api.unntaksbehandling.policyregister.hent_aktiv",
                        lambda conn, tenant, pid: (POL_FIRE, POL_FIRE_HASH))
    uid = _oppsett(conn, phash=POL_FIRE_HASH)
    bid1 = _medlem(conn, "op1")
    bid2 = _medlem(conn, "op2")
    reg = _macreg()
    r1 = _kall(conn, uid, "godkjenn", bid1, reg)
    assert r1["utfall"] == "venter_andre_godkjenner" and r1["gjenstaar"] == 1
    # Samme bruker igjen → fire-øyne-brudd.
    with pytest.raises(Godkjenningsfeil):
        _kall(conn, uid, "godkjenn", bid1, reg, idem="op1-igjen")
    conn.rollback()
    r2 = _kall(conn, uid, "godkjenn", bid2, reg)
    assert r2["utfall"] == "TILLAT"


@pg
def test_port15_teknisk_feil_under_siste_godkjenning(conn, monkeypatch):
    """Port 15: en teknisk feil under den SISTE (terskeloppnående) godkjenningen
    ruller ALT tilbake — runden forblir `apen`, KUN første attestasjon består,
    og en retry av andre godkjenner virker."""
    import api.unntaksbehandling as u
    from db.pg import sett_tenant
    monkeypatch.setattr("api.unntaksbehandling.policyregister.hent_aktiv",
                        lambda conn, tenant, pid: (POL_FIRE, POL_FIRE_HASH))
    uid = _oppsett(conn, phash=POL_FIRE_HASH)
    bid1 = _medlem(conn, "op1")
    bid2 = _medlem(conn, "op2")
    reg = _macreg()
    assert _kall(conn, uid, "godkjenn", bid1, reg)["utfall"] \
        == "venter_andre_godkjenner"

    # Andre godkjenner nås terskelen, men beslutningen kaster en teknisk feil.
    orig = u.sikker_beslutning_pg

    def boom(*a, **k):
        raise RuntimeError("injisert teknisk feil")

    monkeypatch.setattr(u, "sikker_beslutning_pg", boom)
    with pytest.raises(RuntimeError):
        _kall(conn, uid, "godkjenn", bid2, reg, idem=f"feil-{uid}")
    conn.rollback()
    monkeypatch.setattr(u, "sikker_beslutning_pg", orig)

    sett_tenant(conn, TEN)
    rst = conn.execute("SELECT status FROM godkjenningsrunde WHERE tenant=%s AND"
                       " unntak_id=%s AND runde=1", (TEN, uid)).fetchone()[0]
    natt = conn.execute("SELECT count(*) FROM menneskelig_attestasjon WHERE"
                        " tenant=%s AND unntak_id=%s", (TEN, uid)).fetchone()[0]
    conn.rollback()
    assert rst == "apen"      # runden overlevde feilen — ikke `brukt`
    assert natt == 1          # kun op1 sin attestasjon består

    # Retry av andre godkjenner virker (uten den injiserte feilen).
    r = _kall(conn, uid, "godkjenn", bid2, reg, idem=f"retry-{uid}")
    assert r["utfall"] == "TILLAT"
