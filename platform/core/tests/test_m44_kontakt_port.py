"""Porten for ARC B kampanje, PR 1: mottakeren har en adresse (kryptert),
kampanjen har et innhold — og ingenting leverer noe ennå.

  1. Registrering med `kontakt`: masken som før, og chifferteksten i
     samme transaksjon. Klartekst finnes ikke i raden, dekrypteres bare
     med tenantens DEK, og svar/liste bærer bare `har_kontakt`.
  2. `POST …/kontakt` setter/retter adressen på en aktiv mottaker;
     en deaktivert mottaker nekter.
  3. Innhold ved registrering (`emne` + `tekst`) → `har_innhold`; halvt
     innhold er 400 med feltnavn; `POST …/innhold` retter; avlyst nekter.
  4. Adressen står ikke i noen lesevei.
"""
import secrets
import uuid

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_m37 import _sett_kontekst
from .test_m44_kampanje import _kampanje, _mottaker, _rt


def _post(klient, tok, sti, kropp):
    return klient.post(sti, json=kropp,
                       headers={"authorization": f"Bearer {tok}",
                                "Idempotency-Key": secrets.token_urlsafe(24)})


def _rad(migrator, mid):
    _sett_kontekst(migrator, TENANT)
    r = migrator.execute(
        "SELECT kontakt_maske, kontakt_kryptert, kontakt_nonce,"
        " kontakt_key_id, kontakt_satt_ts FROM kampanjemottaker"
        " WHERE tenant=%s AND mottaker_id=%s", (TENANT, mid)).fetchone()
    migrator.rollback()
    return r


def _dekrypter(migrator, r):
    from db import kryptering
    _sett_kontekst(migrator, TENANT)
    nok = migrator.execute(
        "SELECT wrapped_dek FROM tenant_nokler WHERE tenant=%s AND key_id=%s",
        (TENANT, r[3])).fetchone()[0]
    migrator.rollback()
    dek = kryptering._pakk_ut((r[3], nok), TENANT)[1]
    return kryptering.dekrypter(dek, bytes(r[1]), bytes(r[2]), TENANT, r[3],
                                ekstra_aad=b"m44:kontakt")["e"]


@pg
def test_registrering_lagrer_adressen_kryptert_og_viser_bare_masken(
        miljo, klient, token, migrator):
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "okonomi:read"))
    adresse = "kunde-" + secrets.token_hex(3) + "@nordvik.example"
    r = _post(klient, tok, "/v1/kampanje/mottaker",
              {"ekstern_ref": "M-" + secrets.token_hex(3),
               "navn": "Nordvik AS", "kontakt": adresse})
    assert r.status_code == 200, r.text
    assert r.json()["kontakt_maske"] == "k****@nordvik.example"
    assert adresse not in r.text
    mid = r.json()["mottaker_id"]
    rad = _rad(migrator, mid)
    assert rad[1] is not None and rad[4] is not None
    assert adresse.encode() not in bytes(rad[1])
    assert _dekrypter(migrator, rad) == adresse
    r = klient.get("/v1/kampanje", headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    m = [x for x in r.json()["mottakere"] if x["mottaker_id"] == mid][0]
    assert m["har_kontakt"] is True and adresse not in r.text


@pg
def test_kontakt_kan_settes_paa_aktiv_mottaker_men_ikke_deaktivert(
        miljo, klient, token, migrator):
    tok, _ = token(rolle="bestiller", scopes=("bestilling:opprett",))
    c = _rt()
    try:
        mid, _ = _mottaker(c, TENANT)          # 114-døra: uten chiffertekst
    finally:
        c.close()
    assert _rad(migrator, mid)[1] is None
    r = _post(klient, tok, f"/v1/kampanje/mottaker/{mid}/kontakt",
              {"kontakt": "ny@nordvik.example"})
    assert r.status_code == 200 and r.json()["endret"] is True, r.text
    assert _dekrypter(migrator, _rad(migrator, mid)) == "ny@nordvik.example"
    # Rettes: ny adresse, ny chiffertekst.
    r = _post(klient, tok, f"/v1/kampanje/mottaker/{mid}/kontakt",
              {"kontakt": "rettet@nordvik.example"})
    assert r.status_code == 200, r.text
    assert _dekrypter(migrator, _rad(migrator, mid)) == "rettet@nordvik.example"
    # Deaktivert → nei.
    r = _post(klient, tok, f"/v1/kampanje/mottaker/{mid}/aktiv",
              {"aktiv": False})
    assert r.status_code == 200, r.text
    r = _post(klient, tok, f"/v1/kampanje/mottaker/{mid}/kontakt",
              {"kontakt": "x@nordvik.example"})
    assert r.status_code in (400, 409), r.text
    r = _post(klient, tok, f"/v1/kampanje/mottaker/{uuid.uuid4()}/kontakt",
              {"kontakt": "x@nordvik.example"})
    assert r.status_code in (404, 409), r.text


@pg
def test_innholdet_er_begge_eller_ingen_og_kan_rettes(miljo, klient, token,
                                                    migrator):
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "okonomi:read"))
    hode = {"authorization": f"Bearer {tok}"}
    basis = {"ekstern_ref": "K-" + secrets.token_hex(3), "navn": "Høstsjekk",
             "formal": "tilbud", "avmeldingslenke": "https://x.example/av",
             "planlagt_sendt": "2026-10-01"}
    r = _post(klient, tok, "/v1/kampanje/kampanje",
              {**basis, "emne": "Høstsjekk av elanlegget"})
    assert r.status_code == 400, r.text
    assert "tekst" in r.json().get("detalj", ""), r.text
    r = _post(klient, tok, "/v1/kampanje/kampanje", basis)
    assert r.status_code == 200, r.text
    kid = r.json()["kampanje_id"]
    k = [x for x in klient.get("/v1/kampanje", headers=hode).json()["kampanjer"]
         if x["kampanje_id"] == kid][0]
    assert k["har_innhold"] is False and k["emne"] is None
    r = _post(klient, tok, f"/v1/kampanje/kampanje/{kid}/innhold",
              {"emne": "Høstsjekk av elanlegget",
               "tekst": "Hei {navn}, vi tilbyr høstsjekk."})
    assert r.status_code == 200 and r.json()["endret"] is True, r.text
    k = [x for x in klient.get("/v1/kampanje", headers=hode).json()["kampanjer"]
         if x["kampanje_id"] == kid][0]
    assert k["har_innhold"] is True and k["emne"] == "Høstsjekk av elanlegget"
    # Med innhold fra første stund.
    r = _post(klient, tok, "/v1/kampanje/kampanje",
              {**basis, "ekstern_ref": "K-" + secrets.token_hex(3),
               "emne": "E", "tekst": "T"})
    assert r.status_code == 200, r.text
    kid2 = r.json()["kampanje_id"]
    k = [x for x in klient.get("/v1/kampanje", headers=hode).json()["kampanjer"]
         if x["kampanje_id"] == kid2][0]
    assert k["har_innhold"] is True
    # Avlyst → innhold nektes.
    r = _post(klient, tok, f"/v1/kampanje/kampanje/{kid2}/avlys", {})
    assert r.status_code == 200, r.text
    r = _post(klient, tok, f"/v1/kampanje/kampanje/{kid2}/innhold",
              {"emne": "E2", "tekst": "T2"})
    assert r.status_code in (400, 409), r.text
    # Grensene: for langt emne er 400 med feltnavn.
    r = _post(klient, tok, f"/v1/kampanje/kampanje/{kid}/innhold",
              {"emne": "x" * 201, "tekst": "T"})
    assert r.status_code == 400 and "emne" in r.json().get("detalj", "")


def test_rutene_er_deklarert_med_scope():
    from api.app import RUTESCOPE
    assert RUTESCOPE[("POST", "/v1/kampanje/mottaker/{mottaker_id:uuid}/kontakt")] \
        == "bestilling:opprett"
    assert RUTESCOPE[("POST", "/v1/kampanje/kampanje/{kampanje_id:uuid}/innhold")] \
        == "bestilling:opprett"


@pg
def test_ny_adresse_gir_ny_maske_og_ny_hash(migrator, miljo, klient, token):
    """Bevisrunden 9/9 (gult funn): 153 byttet chifferteksten, men masken
    og hashen fra registreringen sto igjen — flaten og kvitteringen viste
    en maske som ikke var adressens. 159: samme kall setter alle tre."""
    from .test_m44_kampanje import _mottaker, _rt
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "okonomi:read"))
    c = _rt()
    try:
        mid, maske_for = _mottaker(c, TENANT, kontakt="Siv.Berg@Tromso.example")
        mid = str(mid)
    finally:
        c.close()
    assert maske_for == "s****@tromso.example"
    _sett_kontekst(migrator, TENANT)
    hasj_for = migrator.execute(
        "SELECT kontakt_hash FROM kampanjemottaker WHERE tenant=%s"
        " AND mottaker_id=%s", (TENANT, mid)).fetchone()[0]
    migrator.rollback()
    r = _post(klient, tok, f"/v1/kampanje/mottaker/{mid}/kontakt",
              {"kontakt": "Kari.Nordmann@Nordvik.EXAMPLE"})
    assert r.status_code == 200, r.text
    assert r.json()["kontakt_maske"] == "k****@nordvik.example", r.text
    rad = _rad(migrator, mid)
    assert rad[0] == "k****@nordvik.example"
    assert _dekrypter(migrator, rad) == "Kari.Nordmann@Nordvik.EXAMPLE"
    _sett_kontekst(migrator, TENANT)
    hasj = migrator.execute(
        "SELECT kontakt_hash FROM kampanjemottaker WHERE tenant=%s"
        " AND mottaker_id=%s", (TENANT, mid)).fetchone()[0]
    migrator.rollback()
    assert hasj != hasj_for and len(hasj) == 64
    # …og listen viser den nye masken, aldri adressen.
    r = klient.get("/v1/kampanje", headers={"authorization": f"Bearer {tok}"})
    m = [x for x in r.json()["mottakere"] if x["mottaker_id"] == mid][0]
    assert m["kontakt_maske"] == "k****@nordvik.example"
    assert "nordvik.example" in r.text and "Kari.Nordmann" not in r.text
