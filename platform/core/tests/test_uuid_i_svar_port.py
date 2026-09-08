"""Porten mot #417: dørens UUID i svaret er en streng, ikke en 500.

35 `_skriv`-kopier legger dørens retur rett i svaret (`{**svar, felt:
ut}`). Der døra returnerer `UUID` (innhold/kilde, innhold/visning,
telefoni/hjemmel, telefoni/regel, esg, avstemming) serialiserte
Starlettes JSONResponse uten `default`, og klienten fikk 500 «Object of
type UUID is not JSON serializable» — ETTER at raden var committet
(Fjordlys-kampanjen 8/9). Fiksen er klassens: `_ok` gjør UUID og
tidspunkt til tekst selv.

Porten går gjennom HTTP-døra mot ekte base for to av de fire, og
enhetstester `_ok` direkte.

MUTASJONEN SOM DREPER DENNE: fjern `default=_json_standard` i `_ok`.
"""
import datetime
import json
import secrets
import uuid

from .test_api import (DSN, MIGRATOR_DSN, TENANT, pg,  # noqa: F401
                       app, klient, migrator, miljo, token)


def test_ok_gjor_uuid_og_tidspunkt_til_tekst():
    from api.policyadmin_http import _ok
    u = uuid.uuid4()
    t = datetime.datetime(2026, 9, 8, 10, 12, tzinfo=datetime.timezone.utc)
    svar = _ok({"kilde_id": u, "ts": t, "d": datetime.date(2026, 9, 8),
                "n": 1, "æ": "ø"}, "rid-1")
    kropp = json.loads(svar.body)
    assert kropp == {"kilde_id": str(u), "ts": "2026-09-08T10:12:00+00:00",
                     "d": "2026-09-08", "n": 1, "æ": "ø"}
    assert svar.headers["x-request-id"] == "rid-1"
    assert svar.headers["content-type"].startswith("application/json")


def _post(klient, tok, sti, kropp):
    return klient.post(sti, json=kropp,
                       headers={"authorization": f"Bearer {tok}",
                                "Idempotency-Key": secrets.token_urlsafe(24)})


@pg
def test_innhold_kilde_svarer_200_med_kilde_id_som_tekst(miljo, migrator,
                                                         klient, token):
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "okonomi:read"))
    r = _post(klient, tok, "/v1/innhold/kilde",
              {"tittel": "Åpningstider " + secrets.token_hex(3),
               "dokumenttype": "policy",
               "innhold_sha256": secrets.token_hex(32)})
    assert r.status_code == 200, r.text
    kid = r.json()["kilde_id"]
    assert isinstance(kid, str) and uuid.UUID(kid)


@pg
def test_telefoni_hjemmel_svarer_200_med_hjemmel_id_som_tekst(miljo, migrator,
                                                              klient, token):
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "okonomi:read"))
    r = _post(klient, tok, "/v1/telefoni/hjemmel",
              {"grunnlagstype": "berettiget_interesse",
               "beskrivelse": "Opptak av servicesamtaler for kvalitetssikring"
                              " og dokumentasjon av avtalt arbeid",
               "formal": "kvalitetssikring", "gyldig_fra": "2026-01-01"})
    assert r.status_code == 200, r.text
    hid = r.json()["hjemmel_id"]
    assert isinstance(hid, str) and uuid.UUID(hid)
