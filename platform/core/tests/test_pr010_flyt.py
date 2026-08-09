"""PR-010: sesjonsflyten gjennom de fire rutene — statusmaskin, cookies,
rate, prinsipal. Nettverket (discovery + kodeveksling) mockes; DB-en er
ekte. Live OIDC mot en test-IdP måles på disponit.com.

@dekker registrerer de sju nye feilveiene i test_apis dekningsregister.
"""
import ipaddress
import json

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT, _lag_token,  # noqa: F401
                       app, dekker, klient, migrator, miljo)
from .test_pr010_db import _ctx, _rydd_oidc, _identitet, _provider as _prov_row

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

# Tenant utledes fra Host: host "t-oidc-flyt.example" → slug "t-oidc-flyt".
TFLYT = "t-oidc-flyt"
HOST = f"{TFLYT}.example"


# ---------------------------------------------------------------------------
# P1 (Codex review-runde 1): rene funksjoner — Origin + retursti
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("origin,host,ok", [
    ("https://disponit.com", "disponit.com", True),
    ("https://disponit.com:443", "disponit.com", True),
    ("https://DISPONIT.com", "disponit.com", True),          # case-insensitiv
    ("https://disponit.com.evil.example", "disponit.com", False),  # suffiks
    ("https://evil-disponit.com", "disponit.com", False),    # prefiks
    ("https://disponit.com@evil", "disponit.com", False),    # userinfo
    ("http://disponit.com", "disponit.com", False),          # ikke https
    ("https://disponit.com:8443", "disponit.com", False),    # feil port
    ("https://disponit.com/path", "disponit.com", False),    # path
    ("https://disponit.com?q=1", "disponit.com", False),     # query
    ("ikke-en-url", "disponit.com", False),
    ("", "disponit.com", False),
])
def test_p1_origin_er_eksakt_ikke_substring(origin, host, ok):
    from api.sesjon import er_forventet_origin
    assert er_forventet_origin(origin, host) is ok


@pytest.mark.parametrize("raa,forventet", [
    ("/oversikt", "/oversikt"),
    ("/", "/"),
    ("/a/b?c=1", "/a/b?c=1"),
    ("//evil.example/phish", "/"),        # scheme-relativ → fremmed vert
    ("/\\evil.example", "/"),             # backslash-triks
    ("\\\\evil.example", "/"),
    ("https://evil.example/x", "/"),      # absolutt URL
    ("/x\r\nSet-Cookie: y", "/"),         # CR/LF-injeksjon
    ("ingen-slash", "/"),
    (None, "/"),
    (123, "/"),
])
def test_p1_retursti_kun_lokal_absolutte_path(raa, forventet):
    from api.sesjon import trygg_retursti
    assert trygg_retursti(raa) == forventet


def test_les_startkropp_godtar_bade_json_og_form():
    """V2 (PR-011): native <form> sender urlencoded; JSON-veien er uendret."""
    import json as _j
    from api.sesjon import les_startkropp
    assert les_startkropp("application/json",
                          _j.dumps({"provider_id": "e2e"}).encode()) \
        == {"provider_id": "e2e"}
    assert les_startkropp("", b"") == {}
    assert les_startkropp("application/x-www-form-urlencoded",
                          b"provider_id=e2e&retursti=%2F") \
        == {"provider_id": "e2e", "retursti": "/"}
    assert les_startkropp("application/x-www-form-urlencoded; charset=utf-8",
                          b"provider_id=e2e") == {"provider_id": "e2e"}
    with pytest.raises(ValueError):
        les_startkropp("application/json", b"{ikke json")


def _seed(migrator, roller=("leser",)):
    """Provider + tenant-binding + identitet + medlemskap for TFLYT."""
    _ctx(migrator, TFLYT)
    migrator.execute("DELETE FROM brukersesjon WHERE tenant=%s", (TFLYT,))
    migrator.execute("DELETE FROM oidc_logintransaksjon WHERE tenant_kandidat=%s",
                     (TFLYT,))
    migrator.execute("DELETE FROM brukermedlemskap WHERE tenant=%s", (TFLYT,))
    migrator.execute("DELETE FROM oidc_rate WHERE true")
    _prov_row(migrator, "pflyt")
    migrator.execute(
        "INSERT INTO tenant_oidc_provider (tenant, provider_id, redirect_uris)"
        " VALUES (%s,'pflyt',ARRAY[%s]) ON CONFLICT DO NOTHING",
        (TFLYT, f"https://{HOST}/v1/oidc/callback"))
    bid = _identitet(migrator, issuer="https://pflyt.example", sub="sub-flyt")
    migrator.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller)"
        " VALUES (%s,%s,%s) ON CONFLICT (tenant,bruker_id) DO UPDATE"
        " SET roller=EXCLUDED.roller",
        (TFLYT, bid, list(roller)))
    migrator.commit()
    return bid


from joserfc.jwk import RSAKey, KeySet
_JWKS = KeySet([RSAKey.generate_key(2048, {"kid": "k1", "use": "sig"})]).as_dict(
    private=False)


def _mock_discovery(monkeypatch):
    from api import oidc, ssrf
    ISS = "https://pflyt.example"
    disc = {"issuer": ISS, "authorization_endpoint": f"{ISS}/auth",
            "token_endpoint": f"{ISS}/token", "jwks_uri": f"{ISS}/jwks"}
    monkeypatch.setattr(oidc, "_hent_json",
                        lambda url, allow: _JWKS if url.endswith("/jwks")
                        else disc)
    monkeypatch.setattr(
        ssrf, "_resolv",
        lambda h, p: [ipaddress.ip_address("93.184.216.34")])
    oidc.toem_cache()


def _start(klient, monkeypatch, cred="hemmelig-verdi"):
    _mock_discovery(monkeypatch)
    # cred-ref er 'pflyt_secret' → LoadCredential-navn (i miljøet)
    # DISPONIT_OIDC_SECRET_PFLYT_SECRET.
    monkeypatch.setenv("DISPONIT_OIDC_SECRET_PFLYT_SECRET", cred)
    return klient.post("/v1/oidc/start", json={"provider_id": "pflyt"},
                       headers={"host": HOST, "sec-fetch-site": "same-origin"},
                       follow_redirects=False)


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@pg
def test_start_gir_303_og_bindingcookie_og_logintx(klient, migrator,
                                                   monkeypatch):
    _seed(migrator)
    r = _start(klient, monkeypatch)
    assert r.status_code == 303, r.text
    assert "auth?" in r.headers["location"] or "/auth" in r.headers["location"]
    cookies = r.headers.get_list("set-cookie")
    assert any("__Host-disponit_oidc=" in c and "HttpOnly" in c
               and "Secure" in c for c in cookies)
    _ctx(migrator, TFLYT)
    n = migrator.execute("SELECT count(*) FROM oidc_logintransaksjon"
                         " WHERE tenant_kandidat=%s AND status='NY'",
                         (TFLYT,)).fetchone()[0]
    migrator.rollback()
    assert n == 1, "én NY login-transaksjon skal finnes"


@pg
@dekker("ukjent_provider")
def test_start_ukjent_provider_generisk(klient, migrator, monkeypatch):
    _seed(migrator)
    _mock_discovery(monkeypatch)
    r = klient.post("/v1/oidc/start", json={"provider_id": "finnesikke"},
                    headers={"host": HOST, "sec-fetch-site": "same-origin"},
                    follow_redirects=False)
    assert r.status_code == 400 and r.json()["feil"] == "ukjent_provider"


@pg
@dekker("provider_utilgjengelig")
def test_start_manglende_credential_er_utilgjengelig(klient, migrator,
                                                     monkeypatch):
    _seed(migrator)
    _mock_discovery(monkeypatch)
    # Ingen DISPONIT_OIDC_SECRET_* satt → credential mangler → fail-closed.
    monkeypatch.delenv("DISPONIT_OIDC_SECRET_PFLYT_SECRET", raising=False)
    r = klient.post("/v1/oidc/start", json={"provider_id": "pflyt"},
                    headers={"host": HOST, "sec-fetch-site": "same-origin"},
                    follow_redirects=False)
    assert r.status_code == 400
    assert r.json()["feil"] == "provider_utilgjengelig"


# ---------------------------------------------------------------------------
# /callback — state-maskin + sesjonsoppretting (kodeveksling mocket)
# ---------------------------------------------------------------------------

def _fullfor_callback(klient, migrator, monkeypatch, bid, sub="sub-flyt"):
    """Kjør /start, hent binding+state, mock veksling, kjør /callback."""
    from api import oidc
    r = _start(klient, monkeypatch)
    binding = [c.split("__Host-disponit_oidc=")[1].split(";")[0]
               for c in r.headers.get_list("set-cookie")
               if "__Host-disponit_oidc=" in c][0]
    state = r.headers["location"].split("state=")[1].split("&")[0]
    monkeypatch.setattr(oidc, "veksle_og_valider",
                        lambda *a, **k: oidc.Identitet(
                            "https://pflyt.example", sub,
                            {"visningsnavn": "T", "epost": None,
                             "epost_verifisert": None}))
    r = klient.get("/v1/oidc/callback",
                   params={"code": "kode", "state": state},
                   cookies={"__Host-disponit_oidc": binding},
                   follow_redirects=False)
    return r


def _sesjon_cookie(callback_respons):
    """Henter sesjonscookie-verdien fra callback-responsen. TestClient
    sender ikke `__Host-`-cookies over http (Secure), så påfølgende kall
    må bære den eksplisitt."""
    for c in callback_respons.headers.get_list("set-cookie"):
        if "__Host-disponit_sesjon=" in c:
            return c.split("__Host-disponit_sesjon=")[1].split(";")[0]
    return None


@pg
def test_callback_oppretter_sesjon_og_konsumerer_state(klient, migrator,
                                                       monkeypatch):
    bid = _seed(migrator)
    r = _fullfor_callback(klient, migrator, monkeypatch, bid)
    assert r.status_code == 303, r.text
    cookies = r.headers.get_list("set-cookie")
    assert any("__Host-disponit_sesjon=" in c and "HttpOnly" in c
               for c in cookies)
    assert any("__Host-disponit_csrf=" in c and "HttpOnly" not in c
               for c in cookies), "CSRF-cookien må være JS-lesbar"
    _ctx(migrator, TFLYT)
    st = migrator.execute("SELECT status FROM oidc_logintransaksjon"
                          " WHERE tenant_kandidat=%s ORDER BY opprettet DESC"
                          " LIMIT 1", (TFLYT,)).fetchone()[0]
    migrator.rollback()
    assert st == "FULLFØRT"


@pg
@dekker("ingen_tilgang")
def test_callback_uten_medlemskap_avvises_ingen_jit(klient, migrator,
                                                    monkeypatch):
    _seed(migrator)
    # En identitet UTEN medlemskap (annen sub).
    r = _fullfor_callback(klient, migrator, monkeypatch, None,
                          sub="ukjent-sub")
    # Generisk feilside; ingen sesjon.
    assert r.status_code == 400
    _ctx(migrator, TFLYT)
    n = migrator.execute("SELECT count(*) FROM brukersesjon WHERE tenant=%s",
                         (TFLYT,)).fetchone()[0]
    migrator.rollback()
    assert n == 0


@pg
@dekker("innlogging_feilet")
def test_callback_replay_avvist(klient, migrator, monkeypatch):
    bid = _seed(migrator)
    from api import oidc
    r = _start(klient, monkeypatch)
    binding = [c.split("__Host-disponit_oidc=")[1].split(";")[0]
               for c in r.headers.get_list("set-cookie")
               if "__Host-disponit_oidc=" in c][0]
    state = r.headers["location"].split("state=")[1].split("&")[0]
    monkeypatch.setattr(oidc, "veksle_og_valider",
                        lambda *a, **k: oidc.Identitet(
                            "https://pflyt.example", "sub-flyt",
                            {"visningsnavn": "T", "epost": None,
                             "epost_verifisert": None}))
    kw = dict(params={"code": "k", "state": state},
              cookies={"__Host-disponit_oidc": binding})
    r1 = klient.get("/v1/oidc/callback", follow_redirects=False, **kw)
    assert r1.status_code == 303
    r2 = klient.get("/v1/oidc/callback", follow_redirects=False, **kw)   # replay på samme state
    assert r2.status_code == 400
    assert r2.json()["feil"] == "innlogging_feilet"


@pg
@dekker("innlogging_feilet")
def test_callback_uten_bindingcookie_avvist_for_veksling(klient, migrator,
                                                         monkeypatch):
    """v4 §1: gyldig callback åpnet i annen browser (uten bindingcookie)
    avvises FØR tokenveksling."""
    bid = _seed(migrator)
    from api import oidc
    r = _start(klient, monkeypatch)
    state = r.headers["location"].split("state=")[1].split("&")[0]
    kalt = {"n": 0}
    monkeypatch.setattr(oidc, "veksle_og_valider",
                        lambda *a, **k: kalt.__setitem__("n", 1))
    r2 = klient.get("/v1/oidc/callback",
                    params={"code": "k", "state": state},
                    follow_redirects=False)   # ingen cookie
    assert r2.status_code == 400
    assert kalt["n"] == 0, "veksling skal ALDRI skje uten bindingcookie"


# ---------------------------------------------------------------------------
# /v1/sesjon: hvem, logout, cookie-auth mot lese-endepunkt, dobbel principal
# ---------------------------------------------------------------------------

def _logg_inn(klient, migrator, monkeypatch):
    _fullfor_callback(klient, migrator, monkeypatch, _seed(migrator))
    return klient.cookies.get("__Host-disponit_sesjon")


@pg
def test_sesjon_hvem_og_cookie_naar_lese_endepunkt(klient, migrator,
                                                   monkeypatch):
    cb = _fullfor_callback(klient, migrator, monkeypatch, _seed(migrator))
    sc = {"__Host-disponit_sesjon": _sesjon_cookie(cb)}
    r = klient.get("/v1/sesjon", headers={"host": HOST}, cookies=sc)
    assert r.status_code == 200, r.text
    k = r.json()
    assert k["tenant"] == TFLYT and "decisions:read" in k["scopes"]
    assert "security:read" not in k["scopes"], "leser skal ikke ha sikkerhet"
    # Cookien når et PR-008 lese-endepunkt.
    r2 = klient.get("/v1/oversikt", headers={"host": HOST}, cookies=sc)
    assert r2.status_code == 200, r2.text


@pg
@dekker("sesjon_ugyldig")
def test_logout_gjor_sesjon_ugyldig_idempotent(klient, migrator, monkeypatch):
    cb = _fullfor_callback(klient, migrator, monkeypatch, _seed(migrator))
    sc = {"__Host-disponit_sesjon": _sesjon_cookie(cb)}
    assert klient.get("/v1/oversikt", headers={"host": HOST},
                      cookies=sc).status_code == 200
    r = klient.request("DELETE", "/v1/sesjon", headers={"host": HOST},
                       cookies=sc, content=b"")
    assert r.status_code == 204
    # Etter logout er sesjonen tilbakekalt → 401 med SAMME cookie.
    r2 = klient.get("/v1/oversikt", headers={"host": HOST}, cookies=sc)
    assert r2.status_code == 401 and r2.json()["feil"] == "sesjon_ugyldig"
    # Idempotent: ny logout ok.
    assert klient.request("DELETE", "/v1/sesjon", headers={"host": HOST},
                          cookies=sc, content=b"").status_code == 204


@pg
@dekker("dobbel_principal")
def test_cookie_og_bearer_samtidig_gir_400(klient, migrator, monkeypatch):
    cb = _fullfor_callback(klient, migrator, monkeypatch, _seed(migrator))
    sc = {"__Host-disponit_sesjon": _sesjon_cookie(cb)}
    tok, _ = _lag_token(migrator, TENANT, "bruker", ["decisions:read"])
    r = klient.get("/v1/oversikt", cookies=sc,
                   headers={"host": HOST, "authorization": f"Bearer {tok}"})
    assert r.status_code == 400 and r.json()["feil"] == "dobbel_principal"


@pg
@dekker("sesjon_ugyldig")
def test_authz_version_bump_ugyldiggjor_sesjon(klient, migrator, monkeypatch):
    bid = _seed(migrator)
    cb = _fullfor_callback(klient, migrator, monkeypatch, bid)
    sc = {"__Host-disponit_sesjon": _sesjon_cookie(cb)}
    assert klient.get("/v1/oversikt", headers={"host": HOST},
                      cookies=sc).status_code == 200
    # Rolleendring bumper authz_version → sesjonens snapshot matcher ikke.
    _ctx(migrator, TFLYT)
    migrator.execute("UPDATE brukermedlemskap SET roller=ARRAY['admin']"
                     " WHERE tenant=%s AND bruker_id=%s", (TFLYT, bid))
    migrator.commit()
    r = klient.get("/v1/oversikt", headers={"host": HOST}, cookies=sc)
    assert r.status_code == 401 and r.json()["feil"] == "sesjon_ugyldig"


@pg
@dekker("rate_grense_login")
def test_start_rate_grense_gir_429_med_retry_after(klient, migrator,
                                                   monkeypatch):
    _seed(migrator)
    siste = None
    for _ in range(RATE_START_MAKS := 22):
        siste = _start(klient, monkeypatch)
        if siste.status_code == 429:
            break
    assert siste.status_code == 429
    assert siste.json()["feil"] == "rate_grense_login"
    assert "retry-after" in {k.lower() for k in siste.headers}
