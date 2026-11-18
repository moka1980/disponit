"""GET /v1/firma/medlemmer — eiervelgerens kilde."""
from __future__ import annotations

import inspect
import json
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")


def test_sporringen_leser_aldri_epost():
    from api import medlemmer
    kilde = inspect.getsource(medlemmer.svar_for)
    assert "epost" not in kilde and "email" not in kilde


def test_ruten_har_lesescope():
    from api.app import RUTESCOPE
    assert RUTESCOPE[("GET", "/v1/firma/medlemmer")] == "decisions:read"


def _medlem(migrator, tenant, navn, *, aktiv=True, epost=None):
    from .test_m37 import _sett_kontekst
    _sett_kontekst(migrator, tenant)
    profil = {"visningsnavn": navn}
    if epost:
        profil["epost"] = epost
    bid = migrator.execute(
        "INSERT INTO brukeridentitet (issuer, sub, profil) VALUES"
        " ('https://medlem.test', %s, %s::jsonb) RETURNING bruker_id",
        ("m-" + secrets.token_hex(6), json.dumps(profil))).fetchone()[0]
    migrator.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller, aktiv)"
        " VALUES (%s,%s,ARRAY['leser'],%s)", (tenant, bid, aktiv))
    migrator.commit()
    return str(bid)


@pg
def test_bare_egne_aktive_medlemmer_og_aldri_epost(migrator, klient):
    from .test_m14_faktura import _browserokt
    from .test_m27_lager import _C_SESJON
    merke = secrets.token_hex(3)
    aktiv = _medlem(migrator, TENANT, f"Aktiv {merke}",
                    epost=f"aktiv-{merke}@eksempel.test")
    sluttet = _medlem(migrator, TENANT, f"Sluttet {merke}", aktiv=False)
    fremmed = _medlem(migrator, "t-fremmed-" + merke, f"Fremmed {merke}")
    cookie, _csrf = _browserokt(migrator, ["leser"])
    r = klient.get("/v1/firma/medlemmer", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    ider = {m["bruker_id"]: m["navn"] for m in r.json()["medlemmer"]}
    assert ider.get(aktiv) == f"Aktiv {merke}"
    assert sluttet not in ider
    assert fremmed not in ider
    assert f"aktiv-{merke}@" not in r.text
