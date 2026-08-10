"""PR-012 CP5: POST /v1/unntak/{id}/handling — HTTP-portene.

Selve behandlingslogikken er bevist på funksjonsnivå (test_pr012_behandle);
her sjekkes at endepunktet er koblet inn og at HTTP-portene (form, autentisering)
stopper det de skal FØR noen sak røres.
"""
import pytest

from .test_api import DSN, app, klient, miljo  # noqa: F401

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _velformet(**over):
    body = {"operatorhandling": "godkjenn", "saksversjon": 0}
    body.update(over)
    return body


@pg
def test_uautentisert_handling_avvises(klient):
    # Velformet request (m/ saksversjon + Idempotency-Key) → skal nå auth-porten.
    r = klient.post("/v1/unntak/1/handling", json=_velformet(),
                    headers={"Idempotency-Key": "k1"})
    assert r.status_code == 401
    assert r.json()["feil"] == "token_ugyldig"


@pg
def test_ukjent_operatorhandling_er_feilformet(klient):
    # Formkontrollen ligger FØR autentisering: en ukjent handling er en
    # klientfeil uansett, og skal ikke lekke om ruten finnes eller ei.
    r = klient.post("/v1/unntak/1/handling",
                    json=_velformet(operatorhandling="slett_alt"),
                    headers={"Idempotency-Key": "k1"})
    assert r.status_code == 400
    assert r.json()["feil"] == "request_feilformet"


@pg
def test_manglende_saksversjon_er_feilformet(klient):
    r = klient.post("/v1/unntak/1/handling",
                    json={"operatorhandling": "godkjenn"},
                    headers={"Idempotency-Key": "k1"})
    assert r.status_code == 400
    assert r.json()["feil"] == "request_feilformet"


@pg
def test_manglende_idempotency_key_avvises(klient):
    r = klient.post("/v1/unntak/1/handling", json=_velformet())
    assert r.status_code == 400
    assert r.json()["feil"] == "idempotensnokkel_mangler"


def test_tillatte_handlinger_ikke_handterbar_status():
    from api.lesing import _tillatte_handlinger
    h, aarsak = _tillatte_handlinger(None, "t", "x", "ny", [], False, "p")
    assert h == [] and aarsak is None


def test_tillatte_handlinger_manuell_uten_intensjon():
    from api.lesing import _tillatte_handlinger
    # avvis/eskaler alltid mulig; godkjenn utilgjengelig uten intensjon.
    h, aarsak = _tillatte_handlinger(
        None, "t", "faktura.bokfor", "manuell",
        [{"kode": "belop_over_grense"}], False, "p@1.0.0/faktura.bokfor")
    assert h == ["avvis", "eskaler"] and aarsak == "ingen_intensjon"
