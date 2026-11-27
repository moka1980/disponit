"""Porten for 234: leverandøren redigeres i dashbordet, ikke i koden.

Eiers ordre 27/11: «det må være et skjema for admin også slik at admin kan
selv legge inn/redigere slike info … alt kan gjøres selvbetjent».

  1. Plattformeieren lagrer opplysningene gjennom en ekte økt, og leser dem
     tilbake.
  2. En bruker med samme scope, men uten plattformfullmakt, får 403.
  3. Formen holdes — org.nr. er ni siffer, kontakten er en adresse — og
     basen sier det samme som ruten (CHECKene i 234).
  4. Prøvebanneret henter kontaktadressen herfra.

MUTASJONER SOM DREPER DENNE: fjern `plattform_krev_eier` fra skrivedøra
(2), dropp orgnummer-kontrollen i ruten og i basen (3), la banneret lese
bare miljøvariabelen (4).
"""
import secrets

from .test_api import DSN, MIGRATOR_DSN, app, klient, miljo  # noqa: F401
from .test_plattform_http import (_browsersesjon, _bruker,  # noqa: F401
                                  _rydd_plattformeiere, pg)


def _okt(navn, eier):
    from api import sesjon as sesjonmodul
    bid = _bruker(navn, plattformeier=eier)
    cookie, csrf = _browsersesjon(bid)
    return {sesjonmodul.C_SESJON: cookie}, {"x-disponit-csrf": csrf}


@pg
def test_eieren_lagrer_og_leser_leverandoren(klient):
    c, h = _okt("lev-eier", True)
    kontakt = f"kontakt-{secrets.token_hex(3)}@disponit.example"
    r = klient.post("/v1/plattform/leverandor", cookies=c, headers=h, json={
        "navn": "Disponit", "orgnummer": "123 456 789",
        "adresse": "2007 Kjeller", "kontakt_epost": kontakt,
        "verneting": "Lillestrøm tingrett"})
    assert r.status_code == 200, r.text
    lev = klient.get("/v1/plattform/leverandor", cookies=c).json()[
        "leverandor"]
    assert lev["navn"] == "Disponit"
    assert lev["orgnummer"] == "123456789", "mellomrommene skal bort"
    assert lev["kontakt_epost"] == kontakt
    # Tomt felt er «ikke oppgitt», ikke en tom streng.
    r = klient.post("/v1/plattform/leverandor", cookies=c, headers=h, json={
        "navn": "Disponit", "orgnummer": "", "kontakt_epost": kontakt})
    assert r.status_code == 200, r.text
    assert r.json()["leverandor"]["orgnummer"] is None
    assert r.json()["leverandor"]["adresse"] is None

    # 4. Banneret henter kontakten herfra.
    from api import firmastatus
    from db.pg import koble, sett_kontekst
    k = koble(DSN)
    try:
        t = "t-lev-" + secrets.token_hex(2)
        sett_kontekst(k, t, "test", "r")
        assert firmastatus.status_for(k, t)["kontakt_epost"] == kontakt
        k.rollback()
    finally:
        k.close()


@pg
def test_uten_plattformfullmakt_er_det_403(klient):
    c, h = _okt("lev-uten", False)
    r = klient.post("/v1/plattform/leverandor", cookies=c, headers=h,
                    json={"navn": "Kapret AS"})
    assert r.status_code == 403, r.text
    assert r.json()["feil"] == "ikke_plattformeier"


@pg
def test_formen_holdes_i_ruten_og_i_basen(klient):
    import psycopg
    import pytest

    from db.pg import koble
    c, h = _okt("lev-form", True)
    for kropp in ({"navn": "D", "orgnummer": "12345"},
                  {"navn": "D", "kontakt_epost": "ikke-en-adresse"},
                  {"navn": "   "}):
        r = klient.post("/v1/plattform/leverandor", cookies=c, headers=h,
                        json=kropp)
        assert r.status_code == 400, (kropp, r.text)
    # Basen selv — forbi ruten.
    m = koble(MIGRATOR_DSN)
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("INSERT INTO plattform_leverandor (id, navn,"
                      " orgnummer, endret_av) VALUES (1,'D','12345','t')"
                      " ON CONFLICT (id) DO UPDATE SET orgnummer='12345'")
        m.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("INSERT INTO plattform_leverandor (id, navn, endret_av)"
                      " VALUES (2,'To','t')")
        m.rollback()
    finally:
        m.close()
