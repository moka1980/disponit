"""M-57s egen rapportflate ("ats"-diskriminatoren): den promoterte
evalueringsrapporten leses via SIN rute, aldri via WCAG-rendrerens — og
motsatt. 200-og-feiler-under-rendring-klassen er umulig per
konstruksjon når flatene filtrerer på hver sin diskriminator."""
import secrets

import pytest

from .test_api import DSN, MIGRATOR_DSN, klient, migrator, miljo  # noqa: F401
from .test_bestilling_rekruttering import (_adminsesjon, _bestill,
                                           _evalkropp, _profil,
                                           _rekr_policy,
                                           _sikre_m57_claimbar,
                                           _sett_kontekst)
from .test_inndata_http import inndata_rot  # noqa: F401
from .test_m57_controller import (_MAALINGER, _Modell, _Uttrekker,
                                  _bunt_via_http, _registrer_rapporttypen)

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

TENANT = "t-api"


def _utfort_oppdrag(migrator, klient_ubrukt, monkeypatch):
    """Hele kjeden til promotert rapport — controllertestens rigg."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    from modules.m57_ats import controller
    from .test_m37 import _signer_kvittering
    from .test_modul_onboarding_http import _onboard_token

    _rekr_policy(migrator)
    _sikre_m57_claimbar(migrator)
    _registrer_rapporttypen(migrator)
    rel = migrator.execute(
        "SELECT release_id FROM moduldeployment WHERE modul_id='m57_ats'"
        " AND livslop='claiming' LIMIT 1").fetchone()[0]
    migrator.rollback()
    a = lag_app(DSN)
    c = TestClient(a)
    c.__enter__()
    cookie, csrf = _adminsesjon()
    ref = _bunt_via_http(c, cookie, csrf)
    profilref = _profil(migrator)
    r = _bestill(c, cookie, csrf, _evalkropp(ref, profilref),
                 "n-" + secrets.token_hex(8))
    assert r.status_code == 200 and r.json()["beslutning"] == "tillat", r.text
    oppdrag_id = r.json()["oppdrag_id"]
    mtk, _ = _onboard_token(c, migrator, "m57_ats", rel)
    res = controller.kjor_en(c, mtk, _Modell(), _Uttrekker(),
                             _MAALINGER, _signer_kvittering)
    assert res["utfall"] == "utfort", res
    return c, cookie, oppdrag_id


@pg
def test_rapporten_leses_paa_sin_egen_flate(migrator, miljo, inndata_rot,
                                            monkeypatch):
    from api import sesjon as sesjonmodul

    c, cookie, oid = _utfort_oppdrag(migrator, klient, monkeypatch)
    try:
        ck = {sesjonmodul.C_SESJON: cookie}
        r = c.get(f"/v1/rekruttering/rapport/{oid}", cookies=ck)
        assert r.status_code == 200, r.text
        rapport = r.json()["rapport"]
        assert rapport["rapporttype"] == "rekruttering.evaluering.rapport"
        assert rapport["rangering"][0]["kandidat_id"] == "k1"
        # KRYSS-FLATE-ISOLASJON, begge veier: WCAG-rendrerens rute skal
        # aldri servere ats-formen — 404, ikke 200-og-feiler-hos-klienten.
        rw = c.get(f"/v1/rapport/{oid}", cookies=ck)
        assert rw.status_code == 404, rw.text

        # Listeveien: raden finnes, status utført, rapport klar.
        rl = c.get("/v1/rekruttering/evalueringer", cookies=ck)
        assert rl.status_code == 200, rl.text
        rad = next(e for e in rl.json()["evalueringer"]
                   if e["oppdrag_id"] == oid)
        assert rad["status"] == "utfort" and rad["rapport_klar"] is True

        # Upromotert/ukjent/fremmed er samme 404.
        assert c.get("/v1/rekruttering/rapport/999999",
                     cookies=ck).status_code == 404
    finally:
        c.__exit__(None, None, None)


@pg
def test_listeveien_viser_ogsaa_uferdige(migrator, miljo, inndata_rot,
                                         monkeypatch):
    """Et nettopp bestilt oppdrag står i listen som ventende med
    `rapport_klar: false` — flaten skal kunne vise fremdrift, ikke bare
    fasit."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    from api import sesjon as sesjonmodul

    _rekr_policy(migrator)
    _sikre_m57_claimbar(migrator)
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            cookie, csrf = _adminsesjon()
            ref = _bunt_via_http(c, cookie, csrf)
            profilref = _profil(migrator)
            r = _bestill(c, cookie, csrf, _evalkropp(ref, profilref),
                         "n-" + secrets.token_hex(8))
            assert r.status_code == 200, r.text
            oid = r.json()["oppdrag_id"]
            rl = c.get("/v1/rekruttering/evalueringer",
                       cookies={sesjonmodul.C_SESJON: cookie})
            rad = next(e for e in rl.json()["evalueringer"]
                       if e["oppdrag_id"] == oid)
            assert rad["rapport_klar"] is False
            assert c.get(f"/v1/rekruttering/rapport/{oid}",
                         cookies={sesjonmodul.C_SESJON: cookie}
                         ).status_code == 404
    finally:
        pass
