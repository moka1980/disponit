"""Porten for 225: tildeling og spor per henvendelse (felles innboks).

  1. «Ta saken» setter tildelingen, køen viser hvem som har den, og et
     dobbeltklikk er et stille ja uten ny sporrad.
  2. En sak kan bare gis til et AKTIVT medlem av firmaet; køen viser
     NAVNET, aldri bare id-en.
  3. Frigivelse fjerner tildelingen og står i sporet.
  4. En lukket henvendelse kan ikke tildeles.
  5. Hver bokført handling står i sporet med loggposten sin — sporet og
     evidensen skrives av samme dør i samme transaksjon.
  6. Sporet er append-only ved grant, og tenantbundet.

MUTASJONER SOM DREPER DENNE: fjern INSERT-en i `m17_evidens` (5), fjern
medlemsprøven i `tildel_endepunkt` (2), fjern lukket-sjekken i
`m17_tildel` (4), gi eieren UPDATE på `henvendelsesspor` (6).
"""
import secrets

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_m17_kundeservice import _nokkel, _ta_imot
from .test_m37 import _sett_kontekst

SKRIV = ("bestilling:opprett", "decisions:read")


def _post(klient, tok, sti, kropp):
    return klient.post(sti, json=kropp,
                       headers={"authorization": f"Bearer {tok}",
                                "Idempotency-Key": secrets.token_urlsafe(24)})


def _get(klient, tok, sti):
    return klient.get(sti, headers={"authorization": f"Bearer {tok}"})


def _ny(migrator):
    key_id, dek = _nokkel(migrator, TENANT)
    return str(_ta_imot(migrator, TENANT, key_id, dek))


def _koerad(klient, tok, hid):
    r = _get(klient, tok, "/v1/kundeservice")
    assert r.status_code == 200, r.text
    return [x for x in r.json()["koe"] if x["henvendelse_id"] == hid][0]


def _spor(klient, tok, hid):
    r = _get(klient, tok, f"/v1/kundeservice/henvendelse/{hid}/spor")
    assert r.status_code == 200, r.text
    return r.json()["spor"]


def _medlem(migrator, navn):
    """Et aktivt medlem av TENANT med visningsnavn."""
    import json
    _sett_kontekst(migrator, TENANT)
    bid = migrator.execute(
        "INSERT INTO brukeridentitet (issuer, sub, profil) VALUES"
        " ('https://tildel.test', %s, %s) RETURNING bruker_id",
        (secrets.token_hex(6), json.dumps({"visningsnavn": navn}))
    ).fetchone()[0]
    migrator.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller, aktiv)"
        " VALUES (%s,%s,ARRAY['leser'],true)", (TENANT, bid))
    migrator.commit()
    return str(bid)


@pg
def test_ta_saken_vises_i_koen_og_dobbeltklikk_er_stille(
        migrator, miljo, klient, token):
    tok, _ = token(rolle="bestiller", scopes=SKRIV)
    hid = _ny(migrator)
    assert _koerad(klient, tok, hid)["tildelt"] is None

    r = _post(klient, tok, f"/v1/kundeservice/henvendelse/{hid}/tildel",
              {"bruker_id": "meg"})
    assert r.status_code == 200, r.text
    assert r.json()["ny"] is True
    meg = r.json()["tildelt"]
    assert _koerad(klient, tok, hid)["tildelt"]["bruker_id"] == meg

    r = _post(klient, tok, f"/v1/kundeservice/henvendelse/{hid}/tildel",
              {"bruker_id": "meg"})
    assert r.status_code == 200 and r.json()["ny"] is False
    handlinger = [s["handling"] for s in _spor(klient, tok, hid)]
    assert handlinger == ["henvendelse.mottatt", "henvendelse.tildelt"]


@pg
def test_bare_et_medlem_kan_faa_saken_og_koen_viser_navnet(
        migrator, miljo, klient, token):
    tok, _ = token(rolle="bestiller", scopes=SKRIV)
    hid = _ny(migrator)
    sti = f"/v1/kundeservice/henvendelse/{hid}/tildel"

    r = _post(klient, tok, sti, {"bruker_id": "bid_finnes_ikke"})
    assert r.status_code == 400, r.text
    assert _koerad(klient, tok, hid)["tildelt"] is None
    for ugyldig in ("", "   ", 7, ["x"]):
        assert _post(klient, tok, sti,
                     {"bruker_id": ugyldig}).status_code == 400

    kollega = _medlem(migrator, "Kari Nordmann")
    r = _post(klient, tok, sti, {"bruker_id": kollega})
    assert r.status_code == 200, r.text
    t = _koerad(klient, tok, hid)["tildelt"]
    assert t["bruker_id"] == kollega and t["navn"] == "Kari Nordmann"
    siste = _spor(klient, tok, hid)[-1]
    assert siste["handling"] == "henvendelse.tildelt"
    assert siste["til"] == "Kari Nordmann"


@pg
def test_frigivelse_fjerner_tildelingen_og_staar_i_sporet(
        migrator, miljo, klient, token):
    tok, _ = token(rolle="bestiller", scopes=SKRIV)
    hid = _ny(migrator)
    sti = f"/v1/kundeservice/henvendelse/{hid}/tildel"
    assert _post(klient, tok, sti, {"bruker_id": "meg"}).status_code == 200
    r = _post(klient, tok, sti, {"bruker_id": None})
    assert r.status_code == 200 and r.json()["ny"] is True
    assert _koerad(klient, tok, hid)["tildelt"] is None
    assert [s["handling"] for s in _spor(klient, tok, hid)][-2:] == [
        "henvendelse.tildelt", "henvendelse.frigitt"]
    # Å frigi en sak ingen har, er et stille ja.
    assert _post(klient, tok, sti, {"bruker_id": None}).json()["ny"] is False


@pg
def test_en_lukket_henvendelse_kan_ikke_tildeles(
        migrator, miljo, klient, token):
    tok, _ = token(rolle="bestiller", scopes=SKRIV)
    hid = _ny(migrator)
    r = _post(klient, tok, f"/v1/kundeservice/henvendelse/{hid}/lukk",
              {"utfall": "ikke_aktuell"})
    assert r.status_code == 200, r.text
    r = _post(klient, tok, f"/v1/kundeservice/henvendelse/{hid}/tildel",
              {"bruker_id": "meg"})
    assert r.status_code in (400, 409), r.text
    spor = _spor(klient, tok, hid)
    assert [s["handling"] for s in spor] == ["henvendelse.mottatt",
                                             "henvendelse.lukket"]
    assert spor[-1]["utfall"] == "ikke_aktuell"


@pg
def test_sporet_peker_paa_loggposten_og_baerer_ikke_innholdet(
        migrator, miljo, klient, token):
    tok, _ = token(rolle="bestiller", scopes=SKRIV)
    key_id, dek = _nokkel(migrator, TENANT)
    hemmelig = "Fortrolig-" + secrets.token_hex(6)
    hid = str(_ta_imot(migrator, TENANT, key_id, dek, emne=hemmelig,
                       tekst=hemmelig, avsender=f"{hemmelig}@x.example"))
    r = _get(klient, tok, f"/v1/kundeservice/henvendelse/{hid}/spor")
    assert r.status_code == 200, r.text
    assert hemmelig.lower() not in r.text.lower()
    rad = r.json()["spor"][0]
    assert rad["handling"] == "henvendelse.mottatt"
    assert rad["etterfylt"] is False and isinstance(rad["loggpost_id"], int)
    # Loggposten finnes, og er M-17s egen.
    _sett_kontekst(migrator, TENANT)
    logg = migrator.execute(
        "SELECT kilde, handling FROM revisjonslogg WHERE tenant=%s AND id=%s",
        (TENANT, rad["loggpost_id"])).fetchone()
    migrator.rollback()
    assert logg == ("m17_kundeservice", "henvendelse.mottatt")
    # En leser uten skrivescope ser sporet, men kan ikke tildele.
    les, _ = token(rolle="leser", scopes=("decisions:read",))
    assert _get(klient, les,
                f"/v1/kundeservice/henvendelse/{hid}/spor").status_code == 200
    assert _post(klient, les, f"/v1/kundeservice/henvendelse/{hid}/tildel",
                 {"bruker_id": "meg"}).status_code == 403


@pg
def test_sporet_er_append_only_og_tenantbundet(migrator):
    hid = _ny(migrator)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET LOCAL ROLE disponit_kundeservice_eier")
    for sql in ("UPDATE henvendelsesspor SET aktor='x' WHERE tenant=%s",
                "DELETE FROM henvendelsesspor WHERE tenant=%s"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(sql, (TENANT,))
        migrator.rollback()
        _sett_kontekst(migrator, TENANT)
        migrator.execute("SET LOCAL ROLE disponit_kundeservice_eier")
    migrator.rollback()
    # En annen tenant ser ikke raden.
    _sett_kontekst(migrator, "t-225-annen")
    n = migrator.execute(
        "SELECT count(*) FROM m17_sporet(%s,%s,%s)",
        ("t-225-annen", hid, 50)).fetchone()[0]
    migrator.rollback()
    assert n == 0
