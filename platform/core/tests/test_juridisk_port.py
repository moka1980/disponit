"""Porten for 235: vilkår, personvern og databehandleravtale i dashbordet.

Eiers ordre 27/11: «alt må betjenes via dashboard både for kunden og admin
panel, ikke gjennom koden».

  1. Før noe er publisert, er siden ærlig («ikke publisert») og
     registreringen krever ingenting.
  2. Plattformeieren lagrer et utkast og publiserer det — gjennom en ekte
     økt. Siden viser den publiserte teksten med leverandørens
     opplysninger (234) fylt inn.
  3. En publisert versjon endres ALDRI (vakten), heller ikke av migratoren.
  4. Uten plattformfullmakt: 403 på utkast og publisering.
  5. Når vilkår og databehandleravtale er publisert, nekter registreringen
     uten samtykke til NETTOPP de versjonene — og lagrer samtykket med firmaet.

RYDDER ETTER SEG: tekstene er plattformens EGNE rader, delt av hele basen.
Publiserte vilkår ville felt hver registreringstest etter denne fila, så
fixturen legger tabellen tilbake slik den var (med vakten slått av bare i
oppryddingen, og bare av migratoren som eier tabellen).

MUTASJONER SOM DREPER DENNE: dropp samtykkesjekken i registreringen (5),
fjern vakten (3), fjern `plattform_krev_eier` fra publiseringsdøra (4).
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, app, dekker, klient,  # noqa: F401
                       miljo)
from .test_plattform_http import (_browsersesjon, _bruker,  # noqa: F401
                                  _rydd_plattformeiere, pg)
from .test_selvregistrering_port import _bruker as _ny_identitet
from .test_selvregistrering_port import _registrantokt


@pytest.fixture
def ren_juridisk():
    from db.pg import koble
    m = koble(MIGRATOR_DSN)
    try:
        dok = m.execute("SELECT dok, versjon, tekst, opprettet, endret_av,"
                        " publisert_ts, publisert_av FROM juridisk_dokument"
                        ).fetchall()
        lev = m.execute("SELECT navn, orgnummer, adresse, kontakt_epost,"
                        " verneting, endret, endret_av"
                        " FROM plattform_leverandor").fetchall()
        m.rollback()
        yield
        m.execute("ALTER TABLE juridisk_dokument"
                  " DISABLE TRIGGER juridisk_publisert_vakt")
        m.execute("DELETE FROM firma_samtykke")
        m.execute("DELETE FROM juridisk_dokument")
        for r in dok:
            m.execute("INSERT INTO juridisk_dokument VALUES"
                      " (%s,%s,%s,%s,%s,%s,%s)", r)
        m.execute("ALTER TABLE juridisk_dokument"
                  " ENABLE TRIGGER juridisk_publisert_vakt")
        m.execute("DELETE FROM plattform_leverandor")
        for r in lev:
            m.execute("INSERT INTO plattform_leverandor (id, navn, orgnummer,"
                      " adresse, kontakt_epost, verneting, endret, endret_av)"
                      " VALUES (1,%s,%s,%s,%s,%s,%s,%s)", r)
        m.commit()
    finally:
        m.close()


def _okt(navn, eier):
    from api import sesjon as sesjonmodul
    bid = _bruker(navn, plattformeier=eier)
    cookie, csrf = _browsersesjon(bid)
    return {sesjonmodul.C_SESJON: cookie}, {"x-disponit-csrf": csrf}


def _registrer(klient, migrator, kropp_ekstra):
    from api import sesjon as sesjonmodul
    bid = _ny_identitet(migrator)
    cookie, csrf = _registrantokt(migrator, bid)
    return klient.post(
        "/v1/firma/registrer",
        json={"navn": f"Vilkår {secrets.token_hex(3)} AS",
              "bransje": "tjenestebedrift", "fullmakter": [],
              **kropp_ekstra},
        cookies={sesjonmodul.C_SESJON: cookie, sesjonmodul.C_CSRF: csrf},
        headers={"X-Disponit-CSRF": csrf,
                 "Idempotency-Key": secrets.token_hex(16)})


@pytest.fixture
def migrator():
    from db.pg import koble
    m = koble(MIGRATOR_DSN)
    yield m
    m.close()


@pg
@dekker("vilkar_ikke_godtatt")
def test_eieren_publiserer_og_registreringen_krever_samtykke(
        klient, migrator, ren_juridisk):
    c, h = _okt("jur-eier", True)

    # 1. Ingenting publisert: siden sier det, registreringen krever ingenting.
    d = klient.get("/v1/juridisk/vilkar").json()
    assert d["tekst"] is None and d["versjon"] is None
    assert klient.get("/v1/juridisk").json()["kreves"] == {}
    side = klient.get("/vilkar")
    assert side.status_code == 200
    assert "juridisk-rot" in side.text
    assert "default-src 'none'" in side.headers["content-security-policy"]

    # 2. Leverandøren og to dokumenter, publisert av eieren.
    klient.post("/v1/plattform/leverandor", cookies=c, headers=h, json={
        "navn": "Disponit", "adresse": "Kjeller",
        "kontakt_epost": "kontakt@disponit.example",
        "verneting": "Lillestrøm tingrett"})
    for dok in ("vilkar", "databehandleravtale"):
        r = klient.post(f"/v1/plattform/juridisk/{dok}/utkast", cookies=c,
                        headers=h, json={"tekst": f"# {dok}\n\nLeverandør: "
                                         "{{navn}}, {{adresse}}, org.nr. "
                                         "{{orgnummer}}. Tvister: "
                                         "{{verneting}}."})
        assert r.status_code == 200, r.text
        r = klient.post(f"/v1/plattform/juridisk/{dok}/publiser", cookies=c,
                        headers=h)
        assert r.status_code == 200, r.text
    d = klient.get("/v1/juridisk/vilkar").json()
    assert "Disponit, Kjeller" in d["tekst"]
    assert "org.nr. (under registrering)" in d["tekst"]
    assert "Lillestrøm tingrett" in d["tekst"]
    kreves = klient.get("/v1/juridisk").json()["kreves"]
    assert set(kreves) == {"vilkar", "databehandleravtale"}

    # Eierens oversikt sier hva som gjelder.
    liste = klient.get("/v1/plattform/juridisk", cookies=c).json()
    vilkar = next(x for x in liste["dokumenter"] if x["dok"] == "vilkar")
    assert vilkar["gjeldende"]["versjon"] == kreves["vilkar"]
    assert vilkar["utkast"] is None

    # 5. Registreringen uten samtykke — og med en gammel versjon — nektes.
    r = _registrer(klient, migrator, {})
    assert r.status_code == 409 and r.json()["feil"] == "vilkar_ikke_godtatt"
    feil = {**kreves, "vilkar": kreves["vilkar"] - 1}
    r = _registrer(klient, migrator, {"godtar": feil})
    assert r.status_code == 409, r.text
    r = _registrer(klient, migrator, {"godtar": kreves})
    assert r.status_code in (200, 201), r.text
    tenant = r.json()["tenant"]
    rader = migrator.execute(
        "SELECT dok, versjon, godtatt_av LIKE 'bruker:%%' FROM firma_samtykke"
        " WHERE tenant=%s ORDER BY dok", (tenant,)).fetchall()
    migrator.rollback()
    assert rader == [("databehandleravtale", kreves["databehandleravtale"],
                      True), ("vilkar", kreves["vilkar"], True)]


@pg
def test_en_publisert_versjon_endres_aldri(klient, migrator, ren_juridisk):
    import psycopg
    c, h = _okt("jur-vakt", True)
    klient.post("/v1/plattform/juridisk/personvern/utkast", cookies=c,
                headers=h, json={"tekst": "# P\n\nFørst."})
    v = klient.post("/v1/plattform/juridisk/personvern/publiser", cookies=c,
                    headers=h).json()["versjon"]
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute("UPDATE juridisk_dokument SET tekst='Etterpå'"
                         " WHERE dok='personvern' AND versjon=%s", (v,))
    migrator.rollback()
    # En endring er en NY versjon, og den gamle står.
    klient.post("/v1/plattform/juridisk/personvern/utkast", cookies=c,
                headers=h, json={"tekst": "# P\n\nEtterpå."})
    v2 = klient.post("/v1/plattform/juridisk/personvern/publiser",
                     cookies=c, headers=h).json()["versjon"]
    assert v2 == v + 1
    assert "Etterpå." in klient.get("/v1/juridisk/personvern").json()["tekst"]
    # Publisering uten utkast er 404, ikke en tom versjon.
    r = klient.post("/v1/plattform/juridisk/personvern/publiser", cookies=c,
                    headers=h)
    assert r.status_code == 404


@pg
def test_uten_plattformfullmakt_er_det_403(klient, ren_juridisk):
    c, h = _okt("jur-uten", False)
    for sti, kropp in (("/v1/plattform/juridisk/vilkar/utkast",
                        {"tekst": "Kapret"}),
                       ("/v1/plattform/juridisk/vilkar/publiser", {})):
        r = klient.post(sti, cookies=c, headers=h, json=kropp)
        assert r.status_code == 403, (sti, r.text)
    assert klient.get("/v1/plattform/juridisk", cookies=c).status_code == 403
    assert klient.get("/v1/juridisk/finnes-ikke").status_code == 404


def test_feltene_fylles_inn_og_mangler_sies():
    from api.juridisk import fyll_inn
    assert fyll_inn("{{navn}} – {{ kontakt_epost }}", {
        "navn": "Disponit", "kontakt_epost": "k@d.no"}) == "Disponit – k@d.no"
    assert fyll_inn("{{adresse}}", {}) == "[ikke fylt inn]"
    assert fyll_inn("{{orgnummer}}", None) == "(under registrering)"
