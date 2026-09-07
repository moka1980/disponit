"""Porten mot #410 (del 2): dørens nekt skal bli et 4xx, ikke et 500.

Tolv moduler oversetter databasedørens RAISE til
`_feil("request_feilformet", rid, detalj=<dørens setning>)`. Hjelperen
i `policyadmin_http` hadde ikke `detalj`, så oversettelsen selv kastet
`TypeError` — og det som skulle vært «løpet er ikke åpent» (400) ble en
500 til driftsvakten (Fjordlys-kampanjen 7/9,
`POST /v1/medarbeider/lop/{id}/steg`).

Porten kaller hver `_doerfeil` med en ekte psycopg-feil og krever at
svaret er et ferdig feilsvar med dørens setning i kroppen.

MUTASJONEN SOM DREPER DENNE: fjern `detalj` fra `_feil`.
"""
import importlib
import json

import psycopg
import pytest

MODULER = ("esg", "hendelse", "hms", "innhold", "likviditet", "medarbeider",
           "moteoperasjon", "optimalisator", "prognose", "skatt", "telefoni",
           "transport")


@pytest.mark.parametrize("navn", MODULER)
def test_doerfeil_gir_feilsvar_med_detalj(navn):
    mod = importlib.import_module(f"api.{navn}")
    from api.policyadmin_http import _Avbrudd
    feil = psycopg.errors.RaiseException("m: løpet x er ikke apent\nCONTEXT: …")
    avbrudd = mod._doerfeil(feil, "rid-test")
    assert isinstance(avbrudd, _Avbrudd), avbrudd
    svar = avbrudd.respons
    assert svar.status_code == 400
    kropp = json.loads(svar.body)
    assert kropp["feil"] == "request_feilformet"
    assert kropp["detalj"] == "m: løpet x er ikke apent"


@pytest.mark.parametrize("navn", MODULER)
def test_doerfeil_holder_postgres_egne_ord_hjemme(navn):
    """Constraint-navn og «DETAIL: Key (…)» er husets indre. Bare dørens
    egen RAISE-setning går ut; alt annet blir 400 UTEN detalj."""
    mod = importlib.import_module(f"api.{navn}")
    feil = psycopg.errors.UniqueViolation(
        'duplicate key value violates unique constraint "x_pkey"\nDETAIL: Key (id)=(1)')
    avbrudd = mod._doerfeil(feil, "rid-test")
    kropp = json.loads(avbrudd.respons.body)
    assert kropp["feil"] == "request_feilformet"
    assert "detalj" not in kropp, kropp
