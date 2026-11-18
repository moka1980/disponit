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


def _default_i_migrasjonene(felt: str) -> int:
    import re
    from pathlib import Path
    mappe = Path(__file__).resolve().parents[1] / "db" / "migrations"
    treff = set()
    for fil in mappe.glob("*.sql"):
        for m in re.finditer(
                rf"^\s+{felt}\s+(?:INT|BIGINT)\s+NOT NULL\s+DEFAULT\s+(\d+)",
                fil.read_text(encoding="utf-8"), re.M):
            treff.add(int(m.group(1)))
    assert len(treff) == 1, (felt, treff)
    return treff.pop()


def test_anbefalt_er_skjemaets_default():
    """Anbefalingen er modulforfatterens eget tall, ikke et nytt."""
    from api import oppsett
    for nr, (_sti, kropp) in oppsett.ANBEFALT.items():
        for felt, verdi in kropp.items():
            assert verdi == _default_i_migrasjonene(felt), (nr, felt)


def test_anbefalt_gaar_til_modulens_egen_skrivevei():
    from api import oppsett
    from api.app import RUTESCOPE
    apimoduler = {nr: a for nr, a, _s, _k in oppsett.ADAPTERE}
    for nr, (sti, kropp) in oppsett.ANBEFALT.items():
        assert RUTESCOPE[("POST", sti)] == oppsett.SKRIVESCOPE, sti
        kilde = inspect.getsource(
            importlib.import_module(f"api.{apimoduler[nr]}"))
        start = kilde.index(f"POST {sti}")
        bygg = kilde[start:kilde.index("return _skriv", start)]
        for felt in kropp:
            assert f'"{felt}"' in bygg, (sti, felt)


def test_anbefalt_bare_naar_kravet_mangler_og_brukeren_kan_skrive(
        monkeypatch):
    from api import oppsett
    tilstand = {"klar": False}
    monkeypatch.setattr(oppsett, "les_modul", lambda *a: {
        "tilgjengelig": True, "klar": tilstand["klar"], "apne_funn": 0})
    lese = {"decisions:read", "okonomi:read"}
    skrive = lese | {"bestilling:opprett"}
    rad = oppsett.svar_for(None, "t", skrive, [27])["moduler"][0]
    assert rad["anbefalt"]["sti"] == "/v1/lager/terskler"
    assert "anbefalt" not in oppsett.svar_for(None, "t", lese,
                                              [27])["moduler"][0]
    tilstand["klar"] = True
    assert "anbefalt" not in oppsett.svar_for(None, "t", skrive,
                                              [27])["moduler"][0]


@pg
@pytest.mark.parametrize("nr", [14, 24, 25, 26, 27, 39, 41, 42])
def test_anbefalt_godtas_av_modulen_og_gjor_den_klar(
        migrator, klient, monkeypatch, nr):
    from api import oppsett
    from .test_m14_faktura import _browserokt, _hpost
    sti, kropp = oppsett.ANBEFALT[nr]
    _tildel(monkeypatch, [nr])
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _hpost(klient, cookie, csrf, sti, kropp)
    assert r.status_code == 200, r.text
    rad = _hent(klient, cookie)[nr]
    assert rad["klar"] is True and "anbefalt" not in rad, rad


def test_hvert_anbefalt_felt_har_etikett_i_dashbordet():
    """Uten etikett ville kortet vist et rått kolonnenavn til kunden."""
    from pathlib import Path
    from api import oppsett
    js = (Path(__file__).resolve().parents[1] / "ui" / "static" / "js"
          / "flater" / "oversikt.js").read_text(encoding="utf-8")
    for _sti, kropp in oppsett.ANBEFALT.values():
        for felt in kropp:
            assert f"  {felt}: \"ui." in js, felt
