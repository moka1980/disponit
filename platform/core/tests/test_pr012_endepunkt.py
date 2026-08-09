"""PR-012 CP5: POST /v1/unntak/{id}/handling — HTTP-portene.

Selve behandlingslogikken er bevist på funksjonsnivå (test_pr012_behandle);
her sjekkes at endepunktet er koblet inn og at HTTP-portene (form, autentisering)
stopper det de skal FØR noen sak røres.
"""
import pytest

from .test_api import DSN, app, klient, miljo  # noqa: F401

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


@pg
def test_uautentisert_handling_avvises(klient):
    r = klient.post("/v1/unntak/1/handling",
                    json={"operatorhandling": "godkjenn"})
    assert r.status_code == 401
    assert r.json()["feil"] == "token_ugyldig"


@pg
def test_ukjent_operatorhandling_er_feilformet(klient):
    # Formkontrollen ligger FØR autentisering: en ukjent handling er en
    # klientfeil uansett, og skal ikke lekke om ruten finnes eller ei.
    r = klient.post("/v1/unntak/1/handling",
                    json={"operatorhandling": "slett_alt"})
    assert r.status_code == 400
    assert r.json()["feil"] == "request_feilformet"
