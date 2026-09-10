"""Porten for M-6 PR-D a: meldingsflaten.

  1. `GET /v1/epost/meldinger?kilde=` (epost:read) dekrypterer avsender,
     emne og forhåndsvisning for økten — basen ser aldri klarteksten;
     detaljen bærer kroppen; en annen tenant ser ingenting; en reapet
     melding vises som reapet (ingen tekst).
  2. Rutene er lesende, scopet er `epost:read`, og en økt uten scopet
     får 403 — aldri innlogging.

Gjerdet mot en annen tenant er RLS (088 `tenant_isolasjon`), ikke
SQL-ens tenantfilter — mutasjonen «detaljen uten tenantfilter» overlever
derfor, og det er riktig: porten måler basens gjerde. Mutasjonen «lista
bærer kroppen» feller.
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_m37 import _sett_kontekst

ADRESSE = "per-" + secrets.token_hex(3) + "@nordvik.example"
EMNE = "Befaring " + secrets.token_hex(2)
KROPP = "Hei, kan dere komme på befaring neste uke?\nHilsen Per"


def _kilde_med_melding(m, *, reapet=False):
    from db import kryptering
    _sett_kontekst(m, TENANT)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(m, TENANT)
    ct, nonce = kryptering.krypter(dek, {"refresh_token": "r"}, TENANT, key_id)
    kid = m.execute(
        "INSERT INTO epost_kilde (tenant, leverandor, postboks, auth_kryptert,"
        " nonce, key_id) VALUES (%s,'m365',%s,%s,%s,%s) RETURNING kilde_id",
        (TENANT, f"pb-{secrets.token_hex(4)}@example.org", ct, nonce, key_id)
    ).fetchone()[0]
    payload = {"fra": ADRESSE, "fra_navn": "Per", "til": ["post@x.example"],
               "emne": EMNE, "forhandsvisning": KROPP[:20], "kropp": KROPP,
               "kropp_type": "text"}
    ct2, nonce2 = kryptering.krypter(dek, payload, TENANT, key_id)
    mid = m.execute(
        "INSERT INTO epost_melding (tenant, kilde_id, leverandor_melding_id,"
        " mottatt_ts, retning, avsender_hash, emne_hash, kropp_kryptert, nonce,"
        " key_id, har_vedlegg) VALUES (%s,%s,%s,now(),'inn',%s,%s,%s,%s,%s,true)"
        " RETURNING melding_id",
        (TENANT, kid, "lev-" + secrets.token_hex(4), "a" * 64, "b" * 64,
         ct2, nonce2, key_id)).fetchone()[0]
    if reapet:
        m.execute("UPDATE epost_melding SET kropp_kryptert=NULL, nonce=NULL,"
                  " key_id=NULL, slettet_ts=now() WHERE tenant=%s AND melding_id=%s",
                  (TENANT, mid))
    m.commit()
    return str(kid), str(mid)


@pg
def test_flaten_dekrypterer_for_okten_og_bare_for_tenanten(migrator, miljo,
                                                             klient, token):
    kid, mid = _kilde_med_melding(migrator)
    _, rmid = _kilde_med_melding(migrator, reapet=True)
    tok, _ = token(rolle="leser", scopes=("epost:read",))
    hode = {"authorization": f"Bearer {tok}"}
    r = klient.get(f"/v1/epost/meldinger?kilde={kid}", headers=hode)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["vist"] == 1 and d["avkortet"] is False
    m = d["meldinger"][0]
    assert m["melding_id"] == mid and m["fra"] == ADRESSE \
        and m["fra_navn"] == "Per" and m["emne"] == EMNE \
        and m["forhandsvisning"] == KROPP[:20] and m["har_vedlegg"] is True \
        and m["reapet"] is False and m["slettes_ts"]
    assert "kropp" not in m
    r = klient.get(f"/v1/epost/meldinger/{mid}", headers=hode)
    assert r.status_code == 200 and r.json()["kropp"] == KROPP \
        and r.json()["til"] == ["post@x.example"]
    # Alle kilder: den reapede står som reapet uten tekst.
    r = klient.get("/v1/epost/meldinger", headers=hode)
    rad = {x["melding_id"]: x for x in r.json()["meldinger"]}
    assert rad[rmid]["reapet"] is True and rad[rmid]["emne"] is None \
        and rad[rmid]["fra"] is None
    r = klient.get(f"/v1/epost/meldinger/{rmid}", headers=hode)
    assert r.status_code == 200 and r.json()["kropp"] == "" \
        and r.json()["reapet"] is True
    # Basen: bare ciphertext.
    _sett_kontekst(migrator, TENANT)
    raa = migrator.execute("SELECT epost_melding::text FROM epost_melding"
                           " WHERE tenant=%s AND melding_id=%s",
                           (TENANT, mid)).fetchone()[0]
    migrator.rollback()
    assert ADRESSE not in raa and EMNE not in raa
    # En annen tenant ser ingenting.
    annen, _ = token(rolle="leser", scopes=("epost:read",), tenant="annen-" + secrets.token_hex(2))
    r = klient.get(f"/v1/epost/meldinger/{mid}",
                   headers={"authorization": f"Bearer {annen}"})
    assert r.status_code == 404, r.text
    r = klient.get("/v1/epost/meldinger?kilde=ikke-uuid", headers=hode)
    assert r.status_code == 400


@pg
def test_rutene_er_lesende_med_epost_read(klient, migrator, miljo, token):
    from api.app import RUTESCOPE
    ruter = {(m, s) for m, s in RUTESCOPE if s.startswith("/v1/epost/meldinger")}
    # Skriveveiene er navngitt: slettingen FJERNER (176), og
    # svarutkastet er en TILSTAND et menneske skriver (179) — ingen av
    # dem sender noe. Sendingen har sin egen vei gjennom policyporten.
    assert ruter == {("GET", "/v1/epost/meldinger"),
                     ("GET", "/v1/epost/meldinger/{melding_id:uuid}"),
                     ("POST", "/v1/epost/meldinger/{melding_id:uuid}/slett"),
                     ("POST", "/v1/epost/meldinger/{melding_id:uuid}"
                              "/svarutkast")}
    assert all(sc == "epost:read" for (m, s), sc in RUTESCOPE.items()
               if s.startswith("/v1/epost/meldinger") and m == "GET")
    tok, _ = token(rolle="leser", scopes=("okonomi:read",))
    r = klient.get("/v1/epost/meldinger",
                   headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 403, r.text
    from pathlib import Path
    kilde = (Path(__file__).resolve().parents[1] / "api" / "epost_meldinger.py"
             ).read_text(encoding="utf-8")
    # Modulen skriver aldri direkte i lagrene: slettingen går gjennom
    # døra (176), som gjør reaperens overgang under sin egen vakt.
    for forbudt in ("INSERT", "UPDATE ", "DELETE", "smtplib", "sendMail"):
        assert forbudt not in kilde, forbudt
    assert "m6_slett_melding" in kilde
    assert "m6_skriv_svarutkast" in kilde and "m6_avgjor_utkast" in kilde
