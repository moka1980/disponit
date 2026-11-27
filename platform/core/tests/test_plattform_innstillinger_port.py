"""Porten for 236: prøveperioden og bransjemalene, i plattformadmin.

Eiers ordre 27/11: «alt må betjenes via dashboard både for kunden og admin
panel, ikke gjennom koden».

  1. Startverdiene er de som sto i koden (30 døgn, alle tre malene), og de
     står offentlig på `/v1/registrering/valg`.
  2. Plattformeieren endrer dem gjennom en ekte økt — og registreringen
     følger: en bransje som er tatt ut, nektes; prøvefristen er eierens tall.
  3. Uten plattformfullmakt: 403.
  4. Formen holdes i ruten og i basen (1-365, minst én kjent bransje).

RYDDER ETTER SEG: raden er plattformens EGEN og delt av hele basen — en
innsnevret bransjeliste ville felt hver registreringstest etter denne fila.

MUTASJONER SOM DREPER DENNE: la registreringen bruke `BRANSJEMALER` alene
(2), behold `PROVE_DOGN = 30` (2), fjern `plattform_krev_eier` fra
skrivedøra (3), dropp CHECK-ene (4).
"""
import datetime
import secrets

import pytest

from .test_api import DSN, MIGRATOR_DSN, app, klient, miljo  # noqa: F401
from .test_plattform_http import (_browsersesjon, _bruker,  # noqa: F401
                                  _rydd_plattformeiere, pg)
from .test_selvregistrering_port import _bruker as _ny_identitet
from .test_selvregistrering_port import _registrantokt

ALLE = ["tjenestebedrift", "handverk-bygg", "netthandel"]


@pytest.fixture
def ren_innstilling():
    from db.pg import koble
    m = koble(MIGRATOR_DSN)
    try:
        rad = m.execute("SELECT prove_dogn, bransjer, endret, endret_av"
                        " FROM plattform_innstilling WHERE id = 1").fetchone()
        m.rollback()
        yield
        m.execute("DELETE FROM plattform_innstilling")
        if rad is not None:
            m.execute("INSERT INTO plattform_innstilling (id, prove_dogn,"
                      " bransjer, endret, endret_av) VALUES (1,%s,%s,%s,%s)",
                      rad)
        m.commit()
    finally:
        m.close()


@pytest.fixture
def migrator():
    from db.pg import koble
    m = koble(MIGRATOR_DSN)
    yield m
    m.close()


def _okt(navn, eier):
    from api import sesjon as sesjonmodul
    bid = _bruker(navn, plattformeier=eier)
    cookie, csrf = _browsersesjon(bid)
    return {sesjonmodul.C_SESJON: cookie}, {"x-disponit-csrf": csrf}


def _registrer(klient, migrator, bransje):
    from api import sesjon as sesjonmodul
    bid = _ny_identitet(migrator)
    cookie, csrf = _registrantokt(migrator, bid)
    return klient.post(
        "/v1/firma/registrer",
        json={"navn": f"Innst {secrets.token_hex(3)} AS",
              "bransje": bransje, "fullmakter": []},
        cookies={sesjonmodul.C_SESJON: cookie, sesjonmodul.C_CSRF: csrf},
        headers={"X-Disponit-CSRF": csrf,
                 "Idempotency-Key": secrets.token_hex(16)})


@pg
def test_eieren_endrer_og_registreringen_folger(klient, migrator,
                                                 ren_innstilling):
    # 1. Startverdiene, offentlig.
    d = klient.get("/v1/registrering/valg").json()
    assert d["prove_dogn"] == 30 and d["bransjer"] == ALLE

    # 2. Eieren snevrer inn og korter ned.
    c, h = _okt("innst-eier", True)
    r = klient.post("/v1/plattform/innstillinger", cookies=c, headers=h,
                    json={"prove_dogn": 14,
                          "bransjer": ["netthandel", "tjenestebedrift"]})
    assert r.status_code == 200, r.text
    # I malenes rekkefølge, ikke i rekkefølgen de ble sendt.
    assert r.json()["innstillinger"]["bransjer"] == ["tjenestebedrift",
                                                     "netthandel"]
    lest = klient.get("/v1/plattform/innstillinger", cookies=c).json()
    assert lest["innstillinger"]["prove_dogn"] == 14
    assert lest["alle_bransjer"] == ALLE
    d = klient.get("/v1/registrering/valg").json()
    assert (d["prove_dogn"], d["bransjer"]) == (14, ["tjenestebedrift",
                                                     "netthandel"])

    # Registreringen: bransjen som er tatt ut, nektes …
    r = _registrer(klient, migrator, "handverk-bygg")
    assert r.status_code == 400, r.text
    assert r.json().get("detalj") == "bransje"
    # … og prøvefristen er eierens tall.
    r = _registrer(klient, migrator, "netthandel")
    assert r.status_code in (200, 201), r.text
    frist = datetime.date.fromisoformat(r.json()["prove_utloper"][:10])
    dager = (frist - datetime.date.today()).days
    assert 13 <= dager <= 15, dager


@pg
def test_uten_plattformfullmakt_er_det_403(klient, ren_innstilling):
    c, h = _okt("innst-uten", False)
    r = klient.post("/v1/plattform/innstillinger", cookies=c, headers=h,
                    json={"prove_dogn": 1, "bransjer": ["netthandel"]})
    assert r.status_code == 403, r.text
    assert r.json()["feil"] == "ikke_plattformeier"
    assert klient.get("/v1/registrering/valg").json()["prove_dogn"] == 30


@pg
def test_formen_holdes_i_ruten_og_i_basen(klient, ren_innstilling):
    import psycopg

    from db.pg import koble
    c, h = _okt("innst-form", True)
    for kropp in ({"prove_dogn": 0, "bransjer": ALLE},
                  {"prove_dogn": 366, "bransjer": ALLE},
                  {"prove_dogn": True, "bransjer": ALLE},
                  {"prove_dogn": "30", "bransjer": ALLE},
                  {"prove_dogn": 30, "bransjer": []},
                  {"prove_dogn": 30, "bransjer": ["fiske"]}):
        r = klient.post("/v1/plattform/innstillinger", cookies=c, headers=h,
                        json=kropp)
        assert r.status_code == 400, (kropp, r.text)
    # Basen selv — forbi ruten.
    m = koble(MIGRATOR_DSN)
    try:
        for sql in ("UPDATE plattform_innstilling SET prove_dogn = 0",
                    "UPDATE plattform_innstilling SET bransjer = '{}'",
                    "UPDATE plattform_innstilling SET bransjer = '{fiske}'",
                    "INSERT INTO plattform_innstilling (id, bransjer,"
                    " endret_av) VALUES (2, '{netthandel}', 't')"):
            with pytest.raises(psycopg.errors.CheckViolation):
                m.execute(sql)
            m.rollback()
    finally:
        m.close()
