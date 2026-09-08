"""Porten mot #411: en integrasjon kan skrive inn i en modul med en token.

Eiervedtak 7/9: «it should be able enter the module by machine». Fram
til da krevde ALLE 278 skriveveier en browsersesjon med CSRF, og en
Bearer-token med riktig scope fikk `csrf_ugyldig` (Fjordlys-kampanjen,
request-id c19fd29785128f577e78f45b).

Fire ting måles, mot ekte base gjennom HTTP-døra:
  1. Bearer MED rutens scope → 200, og raden finnes med tokenet som aktør.
  2. Bearer UTEN scopet → 403. Scope-gaten står.
  3. Cookie UTEN CSRF → 403 `csrf_ugyldig`. Browserveien er uendret.
  4. Cookie OG Bearer samtidig → 400 `dobbel_principal` (v2 §8).

MUTASJONEN SOM DREPER DENNE: fjern maskinveien i `_browserkontekst`,
eller la den slippe forbi scope-sjekken.
"""
import secrets

from .test_api import (DSN, MIGRATOR_DSN, TENANT, pg,  # noqa: F401
                       app, klient, migrator, miljo, token)
from .test_m37 import _sett_kontekst


def _orgnr() -> str:
    # mod-11-gyldig, fiktivt
    base = "9" + f"{secrets.randbelow(10**7):07d}"
    w = [3, 2, 7, 6, 5, 4, 3, 2]
    r = 11 - sum(int(d) * x for d, x in zip(base, w)) % 11
    if r == 11:
        r = 0
    if r == 10:
        return _orgnr()
    return base + str(r)


def _post(klient, headers, kropp):
    return klient.post("/v1/motpart/registrer", json=kropp,
                       headers={"Idempotency-Key": secrets.token_urlsafe(24),
                                **headers})


@pg
def test_bearer_med_rutens_scope_skriver(miljo, migrator, klient, token):
    tok, tid = token(rolle="bestiller",
                     scopes=("bestilling:opprett", "okonomi:read"))
    navn = "Maskinvei AS " + secrets.token_hex(3)
    r = _post(klient, {"authorization": f"Bearer {tok}"},
              {"organisasjonsnummer": _orgnr(), "navn_oppgitt": navn})
    assert r.status_code == 200, r.text
    mid = r.json()["motpart_id"]
    # …og lesedøra ser raden, og aktøren er TOKENET, navngitt som det.
    r = klient.get("/v1/motpart", headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    assert any(m["motpart_id"] == mid for m in r.json()["motparter"]), r.text
    _sett_kontekst(migrator, TENANT)
    antall = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND kilde='m48_motpart' AND aktor=%s",
        (TENANT, f"token:{tid}")).fetchone()[0]
    migrator.rollback()
    assert antall >= 1, "revisjonsloggen navngir ikke tokenet som aktør"


@pg
def test_bearer_uten_scopet_nektes(miljo, migrator, klient, token):
    tok, _ = token(rolle="bruker", scopes=("decisions:read",))
    r = _post(klient, {"authorization": f"Bearer {tok}"},
              {"organisasjonsnummer": _orgnr(), "navn_oppgitt": "Uten scope AS"})
    assert r.status_code == 403, r.text


@pg
def test_cookie_uten_csrf_nektes_fortsatt(miljo, migrator, klient):
    from api import sesjon as sesjonmodul
    _sett_kontekst(migrator, TENANT)
    bid = migrator.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://maskinvei.test', %s) RETURNING bruker_id",
        ("s411-" + secrets.token_hex(6),)).fetchone()[0]
    migrator.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller, aktiv)"
        " VALUES (%s,%s,%s,true)", (TENANT, bid, ["admin"]))
    ver = migrator.execute(
        "SELECT authz_version FROM brukermedlemskap WHERE tenant=%s"
        " AND bruker_id=%s", (TENANT, bid)).fetchone()[0]
    cookie, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    migrator.execute(
        "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
        " authz_snapshot, csrf_hash, opprettet, siste_bruk, utloper,"
        " tilbakekalt) VALUES (%s,%s,%s,%s,%s, now(), now(),"
        " now()+interval '1 hour', false)",
        (sesjonmodul._hash(cookie), TENANT, bid, ver,
         sesjonmodul._hash(csrf)))
    migrator.commit()
    kropp = {"organisasjonsnummer": _orgnr(), "navn_oppgitt": "Cookie AS"}
    r = klient.post("/v1/motpart/registrer", json=kropp,
                    cookies={sesjonmodul.C_SESJON: cookie},
                    headers={"Idempotency-Key": secrets.token_urlsafe(24)})
    assert r.status_code == 403 and r.json()["feil"] == "csrf_ugyldig", r.text
    # …og med CSRF virker browserveien som før.
    r = klient.post("/v1/motpart/registrer", json=kropp,
                    cookies={sesjonmodul.C_SESJON: cookie},
                    headers={"Idempotency-Key": secrets.token_urlsafe(24),
                             "X-Disponit-CSRF": csrf})
    assert r.status_code == 200, r.text


@pg
def test_cookie_og_bearer_samtidig_er_400(miljo, migrator, klient, token):
    from api import sesjon as sesjonmodul
    tok, _ = token(rolle="bestiller", scopes=("bestilling:opprett",))
    r = _post(klient, {"authorization": f"Bearer {tok}"},
              {"organisasjonsnummer": _orgnr(), "navn_oppgitt": "Dobbel AS"})
    assert r.status_code == 200, r.text
    r = klient.post("/v1/motpart/registrer",
                    json={"organisasjonsnummer": _orgnr(),
                          "navn_oppgitt": "Dobbel AS"},
                    cookies={sesjonmodul.C_SESJON: "x"},
                    headers={"authorization": f"Bearer {tok}",
                             "Idempotency-Key": secrets.token_urlsafe(24)})
    assert r.status_code == 400 and r.json()["feil"] == "dobbel_principal", r.text
