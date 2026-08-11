"""PR-013 CP6 — HTTP-portene + utkast-livssyklusen.

Behandlingslogikken er bevist på funksjonsnivå (test_pr013_policyadmin_flyt);
her sjekkes at endepunktene er koblet inn, at HTTP-portene (form/auth/idempotens)
stopper det de skal FØR noe røres, og at utkast-CRUD-funksjonene (opprett →
rediger → valider) håndhever optimistisk lås + skjemavalidering + frysing.
"""
import copy
import secrets
from pathlib import Path

import pytest
import yaml

from api import policyadmin

from .test_api import DSN, MIGRATOR_DSN, app, klient, miljo  # noqa: F401

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
TEN = "t-phttp-" + secrets.token_hex(3)

_MAL = (Path(__file__).resolve().parents[3]
        / "policies" / "bransjemal-handverk-bygg.yaml")


def _gyldig() -> dict:
    """En kjent skjemagyldig policy (bransjemalen som CI allerede validerer)."""
    return yaml.safe_load(_MAL.read_text(encoding="utf-8"))


def _rt():
    from db.pg import koble
    return koble(DSN)


# Idempotency-Key er nå PÅKREVD på alle skriveveier (Codex P1 R3); disse
# wrapperne injiserer en fersk nøkkel per kall så funksjonstestene forblir korte.
def _opprett(rt, **kw):
    k = secrets.token_hex(8)
    return policyadmin.opprett_utkast(rt, idempotency_key=k, input_hash=k, **kw)


def _rediger(rt, **kw):
    k = secrets.token_hex(8)
    return policyadmin.rediger_utkast(rt, idempotency_key=k, input_hash=k, **kw)


def _valider(rt, **kw):
    k = secrets.token_hex(8)
    return policyadmin.valider_utkast(rt, idempotency_key=k, input_hash=k, **kw)


# ---- HTTP-porter (uten sesjon: gates skal svare før noe røres) ------------

@pg
def test_opprett_utkast_uautentisert_avvises(klient):
    r = klient.post("/v1/policyutkast",
                    json={"policy_id": "p", "innhold": {}})
    assert r.status_code == 401
    assert r.json()["feil"] == "token_ugyldig"


@pg
def test_liste_utkast_uautentisert_avvises(klient):
    r = klient.get("/v1/policyutkast")
    assert r.status_code == 401


@pg
def test_opprett_utkast_feilformet_body(klient):
    # Auth-porten ligger FØR form her (browsermutasjon): uten sesjon når vi
    # aldri formkontrollen, så et tomt objekt gir 401 — beviser at ruten finnes
    # og er gatet, ikke 404/405.
    r = klient.post("/v1/policyutkast", json={})
    assert r.status_code == 401


@pg
def test_attester_manglende_idempotency_naar_ruten(klient):
    # Uten sesjon: 401 (auth før idempotens). Ruten MÅ finnes (ikke 404/405).
    r = klient.post("/v1/policyutkast/u-abc/attester",
                    json={"diff_hash": "x"})
    assert r.status_code in (401, 403)


@pg
def test_ruter_finnes_ikke_405_paa_feil_metode(klient):
    # DELETE finnes ikke på kolleksjonen → 405, ikke 404 (ruten er registrert).
    r = klient.request("DELETE", "/v1/policyutkast")
    assert r.status_code == 405


# ---- Utkast-CRUD på funksjonsnivå -----------------------------------------

@pg
def test_utkast_livssyklus_opprett_rediger_valider():
    pid = "pol-" + secrets.token_hex(3)
    base = _gyldig()
    endret = copy.deepcopy(base)
    endret["roller"].append({"id": "ny_rolle", "beskrivelse": "lagt til"})
    rt = _rt()
    try:
        o = _opprett(
            rt, tenant=TEN, aktor="forf", request_id="r", policy_id=pid,
            innhold=base)
        uid = o["utkast_id"]
        assert o["utkastversjon"] == 1 and o["status"] == "utkast"

        # Rediger m/ riktig versjon → 2.
        red = _rediger(
            rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
            forventet_utkastversjon=1, innhold=endret)
        assert red["utkastversjon"] == 2

        # Stale versjon → optimistisk lås slår til.
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _rediger(
                rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
                forventet_utkastversjon=1, innhold={"roller": []})
        assert e.value.kode == "utkastversjon_utdatert"

        # Valider → validert + frosset hash.
        val = _valider(
            rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid)
        assert val["utfall"] == "validert"
        assert val["innholds_hash"]

        # Etter validering er innholdet frosset: redigering avvises (ikke
        # lenger status 'utkast').
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _rediger(
                rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
                forventet_utkastversjon=2, innhold={"roller": []})
        assert e.value.kode == "utkast_ulovlig_tilstand"
    finally:
        rt.close()


@pg
def test_valider_ugyldig_policy_gir_feilliste_uten_tilstandsendring():
    pid = "pol-" + secrets.token_hex(3)
    rt = _rt()
    try:
        # `roller` med feil type → skjemafeil.
        o = _opprett(
            rt, tenant=TEN, aktor="forf", request_id="r", policy_id=pid,
            innhold={"roller": "ikke-en-liste"})
        uid = o["utkast_id"]
        res = _valider(
            rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid)
        assert res["utfall"] == "ugyldig"
        assert res["feil"]
        # Status urørt (fortsatt utkast, ingen frosset hash).
        det = policyadmin.hent_utkast_detalj(
            rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid)
        assert det["status"] == "utkast"
        assert det["innholds_hash"] is None
    finally:
        rt.close()


@pg
def test_opprett_idempotent_replay_samme_utkast_id():
    # Codex P1 R3: Idempotency-Key på skriveveien. Samme nøkkel + input →
    # NØYAKTIG samme utkast_id, ikke et nytt utkast.
    pid = "pol-" + secrets.token_hex(3)
    k = secrets.token_hex(8)
    rt = _rt()
    try:
        a = policyadmin.opprett_utkast(
            rt, tenant=TEN, aktor="forf", request_id="r", policy_id=pid,
            innhold=_gyldig(), idempotency_key=k, input_hash=k)
        b = policyadmin.opprett_utkast(
            rt, tenant=TEN, aktor="forf", request_id="r", policy_id=pid,
            innhold=_gyldig(), idempotency_key=k, input_hash=k)
        assert b.get("replay") is True
        assert a["utkast_id"] == b["utkast_id"]
        # Nøyaktig ett utkast med den id-en (sett tenant-GUC: _fullfor committet,
        # og LOCAL-konteksten nulles ved commit → ellers skjuler RLS raden).
        rt.execute("SELECT set_config('disponit.tenant',%s,false)", (TEN,))
        n = rt.execute("SELECT count(*) FROM policyutkast WHERE tenant=%s AND"
                       " utkast_id=%s", (TEN, a["utkast_id"])).fetchone()[0]
        rt.rollback()
        assert n == 1
    finally:
        rt.close()


@pg
def test_opprett_samme_nokkel_annet_input_gir_konflikt():
    # Codex R2: `rollback_av_versjon` inngår nå i input-hashen. Samme nøkkel med
    # ANNET input (her simulert via ulik input_hash) → idempotenskonflikt, ikke
    # en stille ny/replay-operasjon.
    pid = "pol-" + secrets.token_hex(3)
    k = secrets.token_hex(8)
    rt = _rt()
    try:
        policyadmin.opprett_utkast(
            rt, tenant=TEN, aktor="forf", request_id="r", policy_id=pid,
            innhold=_gyldig(), idempotency_key=k, input_hash=k + "-a")
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            policyadmin.opprett_utkast(
                rt, tenant=TEN, aktor="forf", request_id="r", policy_id=pid,
                innhold=_gyldig(), idempotency_key=k, input_hash=k + "-b")
        assert e.value.kode == "idempotenskonflikt"
    finally:
        rt.close()


@pg
def test_hent_detalj_har_diff_og_klasse():
    pid = "pol-" + secrets.token_hex(3)
    rt = _rt()
    try:
        o = _opprett(
            rt, tenant=TEN, aktor="forf", request_id="r", policy_id=pid,
            innhold=_gyldig())
        det = policyadmin.hent_utkast_detalj(
            rt, tenant=TEN, aktor="forf", request_id="r",
            utkast_id=o["utkast_id"])
        assert det["risikoklasse"] == "UTVIDER"     # fra DENY_ALL
        assert det["diff"]["endringer"]
        assert det["aktiv_runde"] is None
    finally:
        rt.close()
