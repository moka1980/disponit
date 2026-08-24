"""#162 PR-1: inndata-veien over HTTP — reservasjon + strømmet opplasting.

Ende-til-ende gjennom `klient` (ekte browserøkt, ekte pool,
test_varsel_http-formen), og sannheten måles i BASEN og på DISKEN:
filen finnes, er kryptert (ikke klartekst), og dekrypterer til nøyaktig
bytene som ble sendt — med sha-en raden bærer.
"""
import hashlib
import os
import secrets
import zipfile
from io import BytesIO

import pytest

from .test_api import DSN, MIGRATOR_DSN, app, klient, miljo  # noqa: F401
from .test_rekruttering_http import _browsersesjon as _sesjon_for
from .test_rekruttering_http import _bruker as _bruker_for

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
TEN = "t-rhttp-" + secrets.token_hex(3)   # gjenbruker naboens prefiks-vei


def _zipbytes(n_filer=3) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(n_filer):
            zf.writestr(f"k{i}/soknad.html", f"<p>søker {i}</p>")
    return buf.getvalue()


@pytest.fixture()
def inndata_rot(tmp_path, monkeypatch):
    from api import inndata
    monkeypatch.setattr(inndata, "INNDATA_ROT", str(tmp_path / "inndata"))
    return tmp_path / "inndata"


def _reserver(klient, cookie, csrf):
    from api import sesjon as sesjonmodul
    return klient.post("/v1/inndata/reserver",
                       json={"eiermodul": "m57_ats",
                             "formaal": "soknadsbunt"},
                       cookies={sesjonmodul.C_SESJON: cookie},
                       headers={"X-Disponit-CSRF": csrf})


def _opplast(klient, cookie, csrf, jti, kropp: bytes):
    from api import sesjon as sesjonmodul
    return klient.put(f"/v1/inndata/opplast/{jti}", content=kropp,
                      cookies={sesjonmodul.C_SESJON: cookie},
                      headers={"X-Disponit-CSRF": csrf,
                               "content-type": "application/zip"})


def _rigg(klient):
    # NB: test_rekruttering_http sitt TEN — sesjons-/brukerhjelperne er
    # bundet dit, og denne suiten deler tenantprefiks med vilje.
    from .test_rekruttering_http import TEN as NABOTEN
    bid = _bruker_for("innlaster", ["admin"])
    cookie, csrf = _sesjon_for(bid)
    return NABOTEN, bid, cookie, csrf


@pg
def test_reservasjon_og_opplasting_ende_til_ende(klient, inndata_rot):
    tenant, _bid, cookie, csrf = _rigg(klient)
    r = _reserver(klient, cookie, csrf)
    assert r.status_code == 201, r.text
    jti = r.json()["reservasjon_jti"]
    assert len(jti) >= 32 and r.json()["maks_bytes"] > 0
    ref = r.json()["inndata_ref"]
    assert ref.startswith("inndata:")

    kropp = _zipbytes()
    sha = hashlib.sha256(kropp).hexdigest()
    r2 = _opplast(klient, cookie, csrf, jti, kropp)
    assert r2.status_code == 201, r2.text
    assert r2.json()["innhold_sha256"] == sha
    assert r2.json()["faktiske_bytes"] == len(kropp)
    assert r2.json()["inndata_ref"] == ref

    # Sannheten i BASEN …
    from db.pg import koble, sett_kontekst
    m = koble(MIGRATOR_DSN)
    try:
        sett_kontekst(m, tenant, "test", "r1")
        rad = m.execute(
            "SELECT status, faktiske_bytes, innhold_sha256, lager_sti,"
            "       key_id, nonce FROM inndata_artefakt"
            " WHERE tenant=%s AND reservasjon_jti=%s",
            (tenant, jti)).fetchone()
        assert rad and rad[0] == "lastet" and rad[1] == len(kropp) \
            and rad[2] == sha
        sti, key_id, nonce = rad[3], rad[4], rad[5]
        # … og på DISKEN: kryptert (aldri klartekst-zip), og dekrypterer
        # til nøyaktig de sendte bytene.
        raa_fil = open(sti, "rb").read()
        assert not raa_fil.startswith(b"PK"), \
            "payloaden ligger i KLARTEKST på disken"
        from db import kryptering
        _kid, dek = kryptering.hent_eller_opprett_aktiv_dek(m, tenant)
        assert kryptering.dekrypter_bytes(
            dek, raa_fil, bytes(nonce), tenant, key_id,
            formaal=b"inndata") == kropp
        m.rollback()
    finally:
        m.close()

    # Engangs-jti: andre opplasting er en KODET konflikt.
    r3 = _opplast(klient, cookie, csrf, jti, kropp)
    assert r3.status_code == 409 and r3.json()["feil"] == \
        "inndata_alt_lastet"


@pg
def test_ukjent_reservasjon_og_tom_kropp(klient, inndata_rot):
    _tenant, _bid, cookie, csrf = _rigg(klient)
    r = _opplast(klient, cookie, csrf, "f" * 48, _zipbytes())
    assert r.status_code == 409 and r.json()["feil"] == \
        "inndata_reservasjon_ugyldig"
    r2 = _reserver(klient, cookie, csrf)
    r3 = _opplast(klient, cookie, csrf, r2.json()["reservasjon_jti"], b"")
    assert r3.status_code == 400


@pg
def test_reservasjonen_krever_kontraktens_kombinasjon(klient, inndata_rot):
    from api import sesjon as sesjonmodul
    _tenant, _bid, cookie, csrf = _rigg(klient)
    for kropp in ({"eiermodul": "m_wcag_audit", "formaal": "soknadsbunt"},
                  {"eiermodul": "m57_ats", "formaal": "noe_annet"},
                  {}):
        r = klient.post("/v1/inndata/reserver", json=kropp,
                        cookies={sesjonmodul.C_SESJON: cookie},
                        headers={"X-Disponit-CSRF": csrf})
        assert r.status_code == 400, kropp


@pg
def test_scopet_gater_reservasjonen(klient, inndata_rot):
    from api import sesjon as sesjonmodul
    bid = _bruker_for("innsyn", ["leser"])
    cookie, csrf = _sesjon_for(bid)
    r = _reserver(klient, cookie, csrf)
    assert r.status_code in (401, 403)


@pg
def test_taket_avviser_for_stor_kropp(klient, inndata_rot, monkeypatch):
    """Kontrakttaket i endepunktet (transport-taket i middleware deler
    tallet): én byte over → 413, og reservasjonen står UBRUKT — et
    avvist forsøk brenner ingenting."""
    from api import app as appmodul
    from api import inndata as inndatamodul
    monkeypatch.setattr(appmodul, "INNDATA_MAKS_FYSISK", 4096)
    tenant, _bid, cookie, csrf = _rigg(klient)
    r = _reserver(klient, cookie, csrf)
    jti = r.json()["reservasjon_jti"]
    r2 = _opplast(klient, cookie, csrf, jti, b"P" * 4097)
    assert r2.status_code == 413, r2.text
    # …og en lovlig kropp går fortsatt på SAMME reservasjon.
    liten = _zipbytes(1)
    assert len(liten) <= 4096
    r3 = _opplast(klient, cookie, csrf, jti, liten)
    assert r3.status_code == 201, r3.text
