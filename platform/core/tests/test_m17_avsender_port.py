"""Porten for ARC B kundeservice, PR 1: adressen på henvendelsen og
dommen «godkjent».

  1. Inntak over HTTP med kanal e-post lagrer adressen KRYPTERT (AAD
     m17:avsender) med maske; hashen består; køen bærer `har_avsender`
     og masken — aldri adressen. Skjema/telefon lagrer ingen adresse.
  2. `POST …/avsender` setter (og retter) adressen på en åpen
     henvendelse; ugyldig adresse → 400 med detalj; lukket → 409.
  3. Dommen `godkjent`: foreslått → godkjent er lov, godkjent →
     forkastet er ikke (avgjort), og «besvart» krever fortsatt
     `brukt_manuelt` — godkjent er et ja til at plattformen sender, ikke
     et spor etter at noen sendte.

MUTASJONER SOM DREPER DENNE: legg adressen i klartekst i køen (1) og
sløyf lukket-sjekken i `m17_sett_avsender` (2) — begge målt. Å la
`m17_avgjor_utkast` ta godkjent → forkastet (3) dreper den IKKE alene:
`m17_utkast_vakt` (102) er det andre gjerdet og svarer 409 for seg
(målt med døra mutert). Porten måler oppførselen, ikke hvilket gjerde
som holdt.
"""
import secrets
import uuid

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_m17_kundeservice import _nokkel, _ta_imot, _utkast
from .test_m37 import _sett_kontekst


def _post(klient, tok, sti, kropp):
    return klient.post(sti, json=kropp,
                       headers={"authorization": f"Bearer {tok}",
                                "Idempotency-Key": secrets.token_urlsafe(24)})


def _rad(migrator, hid):
    _sett_kontekst(migrator, TENANT)
    r = migrator.execute(
        "SELECT avsender_maske, avsender_kryptert, nonce_avsender,"
        " avsender_key_id, avsender_hash FROM henvendelse"
        " WHERE tenant=%s AND henvendelse_id=%s", (TENANT, hid)).fetchone()
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
                                ekstra_aad=b"m17:avsender")["t"]


@pg
def test_inntak_med_epost_lagrer_adressen_kryptert_og_viser_masken(
        migrator, miljo, klient, token):
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "decisions:read"))
    adresse = "Kari.Nordmann@Nordvik.EXAMPLE"
    r = _post(klient, tok, "/v1/kundeservice/henvendelse",
              {"kanal": "epost", "ekstern_ref": "MSG-" + secrets.token_hex(4),
               "avsender": adresse, "emne": "Spørsmål om faktura",
               "kropp": "Hei, kan dere forklare linje 3?",
               "mottatt": "2026-09-09T10:00:00+00:00"})
    assert r.status_code == 200, r.text
    hid = r.json()["henvendelse_id"]
    assert adresse not in r.text
    rad = _rad(migrator, hid)
    assert rad[0] == "k****@nordvik.example"
    assert rad[1] is not None and adresse.encode() not in bytes(rad[1])
    assert _dekrypter(migrator, rad) == adresse
    assert len(rad[4]) == 64                   # hashen består
    r = klient.get("/v1/kundeservice", headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    h = [x for x in r.json()["koe"] if x["henvendelse_id"] == hid][0]
    assert h["har_avsender"] is True and h["avsender_maske"] == "k****@nordvik.example"
    assert h["godkjent_utkast"] is False
    assert adresse not in r.text and "nordmann" not in r.text.lower()
    # Skjema lagrer ingen adresse — det finnes ingen å svare til.
    r = _post(klient, tok, "/v1/kundeservice/henvendelse",
              {"kanal": "skjema", "ekstern_ref": "SKJ-" + secrets.token_hex(4),
               "avsender": "Ola Kunde", "emne": "Skjema", "kropp": "Tekst",
               "mottatt": "2026-09-09T10:00:00+00:00"})
    assert r.status_code == 200, r.text
    rad = _rad(migrator, r.json()["henvendelse_id"])
    assert rad[0] is None and rad[1] is None
    # Idempotent inntak (samme kanal + referanse) rører ikke adressen:
    # samme referanse med en ANNEN adresse gir samme henvendelse og den
    # første adressen står.
    ref = "MSG-" + secrets.token_hex(4)
    kropp = {"kanal": "epost", "ekstern_ref": ref,
             "avsender": "forste@nordvik.example", "emne": "E", "kropp": "K",
             "mottatt": "2026-09-09T10:00:00+00:00"}
    r1 = _post(klient, tok, "/v1/kundeservice/henvendelse", kropp)
    r2 = _post(klient, tok, "/v1/kundeservice/henvendelse",
               {**kropp, "avsender": "andre@nordvik.example"})
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    assert r1.json()["henvendelse_id"] == r2.json()["henvendelse_id"]
    rad = _rad(migrator, r1.json()["henvendelse_id"])
    assert rad[0] == "f****@nordvik.example"
    assert _dekrypter(migrator, rad) == "forste@nordvik.example"


@pg
def test_avsender_kan_settes_paa_aapen_men_ikke_lukket(migrator, miljo,
                                                       klient, token):
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "decisions:read"))
    from db.pg import koble
    c = koble(DSN)
    try:
        key_id, dek = _nokkel(c, TENANT)
        hid = _ta_imot(c, TENANT, key_id, dek, kanal="telefon")
        lukket = _ta_imot(c, TENANT, key_id, dek, kanal="telefon")
        _sett_kontekst(c, TENANT)
        c.execute("SELECT m17_lukk(%s,%s,'ikke_aktuell','u-test')",
                  (TENANT, lukket))
        c.commit()
    finally:
        c.close()
    r = _post(klient, tok, f"/v1/kundeservice/henvendelse/{hid}/avsender",
              {"avsender": "ikke en adresse"})
    assert r.status_code == 400 and "avsender" in r.json().get("detalj", ""), r.text
    r = _post(klient, tok, f"/v1/kundeservice/henvendelse/{hid}/avsender",
              {"avsender": "per@olsen.example"})
    assert r.status_code == 200, r.text
    assert r.json()["avsender_maske"] == "p****@olsen.example"
    assert _dekrypter(migrator, _rad(migrator, hid)) == "per@olsen.example"
    # Rettes: ny adresse, ny maske.
    r = _post(klient, tok, f"/v1/kundeservice/henvendelse/{hid}/avsender",
              {"avsender": "siv@berg.example"})
    assert r.status_code == 200 and r.json()["avsender_maske"] == "s****@berg.example"
    r = _post(klient, tok, f"/v1/kundeservice/henvendelse/{lukket}/avsender",
              {"avsender": "per@olsen.example"})
    assert r.status_code == 409, r.text
    r = _post(klient, tok, f"/v1/kundeservice/henvendelse/{uuid.uuid4()}/avsender",
              {"avsender": "per@olsen.example"})
    assert r.status_code == 404, r.text


@pg
def test_godkjent_er_en_dom_som_ikke_gaar_om_igjen(migrator, miljo, klient,
                                                   token):
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "decisions:read"))
    from db.pg import koble
    c = koble(DSN)
    try:
        key_id, dek = _nokkel(c, TENANT)
        hid = _ta_imot(c, TENANT, key_id, dek)
        uid = _utkast(c, TENANT, hid, key_id, dek, tekst="Takk for henvendelsen.")
    finally:
        c.close()
    r = _post(klient, tok, f"/v1/kundeservice/utkast/{uid}/dom",
              {"status": "godkjent"})
    assert r.status_code == 200 and r.json()["status"] == "godkjent", r.text
    r = _post(klient, tok, f"/v1/kundeservice/utkast/{uid}/dom",
              {"status": "forkastet"})
    assert r.status_code == 409, r.text
    r = _post(klient, tok, f"/v1/kundeservice/utkast/{uid}/dom",
              {"status": "sendt"})
    assert r.status_code == 400, r.text
    r = klient.get("/v1/kundeservice", headers={"authorization": f"Bearer {tok}"})
    h = [x for x in r.json()["koe"] if x["henvendelse_id"] == str(hid)][0]
    assert h["godkjent_utkast"] is True and h["brukt_utkast"] is False
    # «Besvart» krever fortsatt et menneskes spor — godkjent er ikke det.
    r = _post(klient, tok, f"/v1/kundeservice/henvendelse/{hid}/lukk",
              {"utfall": "besvart"})
    assert r.status_code == 409, r.text
    _sett_kontekst(migrator, TENANT)
    ev = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND kilde='m17_kundeservice' AND handling='utkast.godkjent'",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert ev >= 1
