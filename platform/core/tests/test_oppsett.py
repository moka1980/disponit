"""GET /v1/oppsett — dashbordets «Kom i gang».

Portene: bare TILDELTE moduler, bare med modulens EGET lesescope, og én
modul som feiler river ikke med seg de andre.
"""
from __future__ import annotations

import importlib
import inspect
import json

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")


def _stub(monkeypatch, kall):
    from api import oppsett

    def les(conn, tenant, apimodul, klarflagg):
        kall.append(apimodul)
        return {"tilgjengelig": True, "klar": False, "apne_funn": 0}
    monkeypatch.setattr(oppsett, "les_modul", les)
    return oppsett


def test_bare_tildelte_moduler_med_eget_lesescope(monkeypatch):
    kall = []
    oppsett = _stub(monkeypatch, kall)
    svar = oppsett.svar_for(None, "t", {"decisions:read"}, [17, 27])
    # 27 (lager) krever okonomi:read — økten har det ikke.
    assert [m["modul"] for m in svar["moduler"]] == [17]
    assert kall == ["kundeservice"]


def test_ukjent_tildeling_viser_ingenting(monkeypatch):
    kall = []
    oppsett = _stub(monkeypatch, kall)
    alle = {"decisions:read", "okonomi:read"}
    assert oppsett.svar_for(None, "t", alle, None) == {"moduler": []}
    assert kall == []


def test_adapterne_peker_pa_ekte_lesedorer():
    """En adapter mot en omdøpt modul eller et omdøpt flagg ville stille
    vist «ukjent» for alltid. Flagget må finnes i modulens svar_for."""
    from api import oppsett
    nr = [a[0] for a in oppsett.ADAPTERE]
    assert len(nr) == len(set(nr))
    for _nr, apimodul, scope, klarflagg in oppsett.ADAPTERE:
        modul = importlib.import_module(f"api.{apimodul}")
        kilde = inspect.getsource(modul.svar_for)
        assert '"sammendrag"' in kilde and '"apne_funn"' in kilde, apimodul
        if klarflagg:
            assert f'"{klarflagg}"' in kilde, (apimodul, klarflagg)
        assert f'"{scope}"' in inspect.getsource(modul), (apimodul, scope)


def test_ruten_har_lesescope():
    from api.app import RUTESCOPE
    assert RUTESCOPE[("GET", "/v1/oppsett")] == "decisions:read"


def _tildel(monkeypatch, moduler):
    from api import utrulling
    rad = {"id": TENANT, "navn": "Test", "plan": "pilot",
           "moduler": tuple(moduler), "neste": {"nb": "", "en": ""}}
    monkeypatch.setattr(utrulling, "_UTRULLING", (rad,))


def _okt(migrator, roller):
    from .test_m14_faktura import _browserokt
    return _browserokt(migrator, roller)[0]


def _hent(klient, cookie):
    from .test_m27_lager import _C_SESJON
    r = klient.get("/v1/oppsett", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    return {m["modul"]: m for m in r.json()["moduler"]}


@pg
def test_lager_gar_fra_mangler_krav_til_klar(migrator, klient, monkeypatch):
    from .test_m27_lager import _rt, _terskler
    _tildel(monkeypatch, [27])
    cookie = _okt(migrator, ["admin"])
    for_ = _hent(klient, cookie)[27]
    assert for_["tilgjengelig"] is True
    assert isinstance(for_["klar"], bool)
    assert isinstance(for_["apne_funn"], int)
    c = _rt()
    try:
        _terskler(c, TENANT)
    finally:
        c.close()
    assert _hent(klient, cookie)[27]["klar"] is True


@pg
def test_leser_uten_okonomiscope_ser_ikke_lageret(
        migrator, klient, monkeypatch):
    _tildel(monkeypatch, [27])
    assert 27 not in _hent(klient, _okt(migrator, ["leser"]))


@pg
def test_en_modul_som_feiler_river_ikke_med_seg_de_andre(
        migrator, klient, monkeypatch):
    from api import faktura
    _tildel(monkeypatch, [14, 27])

    def sprekker(conn, tenant):
        conn.execute("SELECT 1/0")
    monkeypatch.setattr(faktura, "svar_for", sprekker)
    moduler = _hent(klient, _okt(migrator, ["admin"]))
    assert moduler[14]["tilgjengelig"] is False
    assert moduler[27]["tilgjengelig"] is True, json.dumps(moduler)
