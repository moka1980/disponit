"""PR-010: OIDC-flytmodulen — discovery-validering + ID-token-validering.

ID-tokenene signeres MED EN EKTE RSA-nøkkel via joserfc, så negativveiene
(alg none, feil aud/iss, utløpt, feil nonce, manipulert signatur) måles mot
bibliotekets faktiske validering — ikke mot en attrapp. Nettverket
(discovery/JWKS) mockes via `_hent_json`, så testene er offline.
"""
import time

import pytest

from .conftest import CORE  # noqa: F401
from api import oidc, ssrf

from joserfc import jwt
from joserfc.jwk import RSAKey, KeySet

# Én RSA-nøkkel for hele modulen; JWKS eksponerer den offentlige delen.
_KEY = RSAKey.generate_key(2048, {"kid": "k1", "use": "sig"})
_KEYSET_DOC = KeySet([_KEY]).as_dict(private=False)

ISSUER = "https://idp.example.com"
CLIENT_ID = "disponit-client"
DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/authorize",
    "token_endpoint": f"{ISSUER}/token",
    "jwks_uri": f"{ISSUER}/jwks",
}


def _provider(**over):
    d = dict(provider_id="p1", issuer=ISSUER,
             discovery_url=f"{ISSUER}/.well-known/openid-configuration",
             client_id=CLIENT_ID, client_secret="hemmelig",
             tillatte_algoritmer=("RS256",), allowlist=())
    d.update(over)
    return oidc.Provider(**d)


@pytest.fixture(autouse=True)
def _ren_cache():
    oidc.toem_cache()
    yield
    oidc.toem_cache()


def _mock_nett(monkeypatch, discovery=None, jwks=None):
    disc = discovery if discovery is not None else DISCOVERY
    jw = jwks if jwks is not None else _KEYSET_DOC

    def fake(url, allowlist):
        if url.endswith("openid-configuration"):
            return disc
        if url.endswith("/jwks"):
            return jw
        raise AssertionError(f"uventet URL {url}")
    monkeypatch.setattr(oidc, "_hent_json", fake)
    # Egress-valideringen (v5 §2) gjør EKTE DNS på endepunktene. For
    # offline validerings-logikk lar vi idp.example.com «resolve» til en
    # offentlig IP — men 127.0.0.1-testen setter sin egen literal og
    # treffer ikke denne (literaler resolves ikke).
    import ipaddress
    monkeypatch.setattr(
        ssrf, "_resolv",
        lambda host, port: [ipaddress.ip_address("93.184.216.34")])


def _id_token(nonce="n0", aud=CLIENT_ID, iss=ISSUER, alg="RS256",
              exp_om=3600, key=_KEY, **ekstra):
    naa = int(time.time())
    claims = {"iss": iss, "sub": "bruker-123", "aud": aud,
              "exp": naa + exp_om, "iat": naa, "nonce": nonce}
    claims.update(ekstra)
    header = {"alg": alg, "kid": key.kid if hasattr(key, "kid") else "k1"}
    return jwt.encode(header, claims, key)


# ---------------------------------------------------------------------------
# Discovery-validering (v6 §2)
# ---------------------------------------------------------------------------

def test_discovery_issuer_mismatch_avvises(monkeypatch):
    _mock_nett(monkeypatch, discovery={**DISCOVERY, "issuer": "https://ond"})
    with pytest.raises(oidc.OidcFeil, match="issuer"):
        oidc.hent_discovery(_provider())


def test_discovery_endepunkt_utenfor_egress_avvises(monkeypatch):
    # token_endpoint peker på loopback → egresspolicyen avviser (uten
    # staging-allowlist).
    _mock_nett(monkeypatch, discovery={
        **DISCOVERY, "token_endpoint": "https://127.0.0.1/token"})
    with pytest.raises(oidc.OidcFeil, match="egresspolicy"):
        oidc.hent_discovery(_provider())


def test_discovery_caches_og_utloper_fail_closed(monkeypatch):
    kall = {"n": 0}

    def fake(url, allowlist):
        kall["n"] += 1
        return DISCOVERY if "configuration" in url else _KEYSET_DOC
    monkeypatch.setattr(oidc, "_hent_json", fake)
    import ipaddress
    monkeypatch.setattr(
        ssrf, "_resolv",
        lambda host, port: [ipaddress.ip_address("93.184.216.34")])
    p = _provider()
    oidc.hent_discovery(p, naa=1000.0)
    n1 = kall["n"]
    oidc.hent_discovery(p, naa=1500.0)          # innenfor TTL → cache
    assert kall["n"] == n1, "innenfor TTL skal ikke refetche"
    oidc.hent_discovery(p, naa=1000.0 + oidc.DISCOVERY_TTL + 1)  # utløpt
    assert kall["n"] > n1, "etter TTL skal den refetche"


# ---------------------------------------------------------------------------
# ID-token-validering (v6 §3) — ekte signaturer
# ---------------------------------------------------------------------------

def test_gyldig_id_token_gir_identitet(monkeypatch):
    _mock_nett(monkeypatch)
    p = _provider()
    ident = oidc._valider_id_token(
        p, oidc.Discovery(**DISCOVERY), _id_token(nonce="abc"), "abc",
        naa=None)
    assert ident.issuer == ISSUER and ident.sub == "bruker-123"
    assert set(ident.profil) == {"visningsnavn", "epost", "epost_verifisert"}


def test_alg_none_avvises(monkeypatch):
    _mock_nett(monkeypatch)
    p = _provider()
    # Et token uten signatur (alg none). joserfc nekter å lage det med en
    # RSA-nøkkel, så vi konstruerer det rått — poenget er at DEKODINGEN
    # avviser 'none' fordi den ikke er i algorithms-allowlisten.
    import base64
    import json as _json

    def seg(d):
        return base64.urlsafe_b64encode(
            _json.dumps(d).encode()).decode().rstrip("=")
    naa = int(time.time())
    ondt = seg({"alg": "none", "kid": "k1"}) + "." + seg(
        {"iss": ISSUER, "sub": "x", "aud": CLIENT_ID, "exp": naa + 60,
         "iat": naa, "nonce": "n"}) + "."
    with pytest.raises(oidc.OidcFeil):
        oidc._valider_id_token(p, oidc.Discovery(**DISCOVERY), ondt, "n",
                               naa=None)


def test_feil_aud_avvises(monkeypatch):
    _mock_nett(monkeypatch)
    with pytest.raises(oidc.OidcFeil, match="claims"):
        oidc._valider_id_token(_provider(), oidc.Discovery(**DISCOVERY),
                               _id_token(aud="en-annen-klient"), "n0",
                               naa=None)


def test_feil_issuer_avvises(monkeypatch):
    _mock_nett(monkeypatch)
    with pytest.raises(oidc.OidcFeil, match="claims"):
        oidc._valider_id_token(_provider(), oidc.Discovery(**DISCOVERY),
                               _id_token(iss="https://ond"), "n0", naa=None)


def test_utlopt_token_avvises(monkeypatch):
    _mock_nett(monkeypatch)
    with pytest.raises(oidc.OidcFeil, match="claims"):
        oidc._valider_id_token(_provider(), oidc.Discovery(**DISCOVERY),
                               _id_token(exp_om=-10), "n0", naa=None)


def test_nonce_mismatch_avvises(monkeypatch):
    _mock_nett(monkeypatch)
    with pytest.raises(oidc.OidcFeil, match="nonce"):
        oidc._valider_id_token(_provider(), oidc.Discovery(**DISCOVERY),
                               _id_token(nonce="feil"), "riktig", naa=None)


def test_manipulert_signatur_avvises(monkeypatch):
    _mock_nett(monkeypatch)
    tok = _id_token(nonce="n0")
    manipulert = tok[:-6] + ("aaaaaa" if not tok.endswith("aaaaaa")
                             else "bbbbbb")
    with pytest.raises(oidc.OidcFeil, match="signatur"):
        oidc._valider_id_token(_provider(), oidc.Discovery(**DISCOVERY),
                               manipulert, "n0", naa=None)


def test_signert_med_fremmed_nokkel_avvises(monkeypatch):
    _mock_nett(monkeypatch)
    fremmed = RSAKey.generate_key(2048, {"kid": "k1", "use": "sig"})
    tok = _id_token(nonce="n0", key=fremmed)
    with pytest.raises(oidc.OidcFeil, match="signatur"):
        oidc._valider_id_token(_provider(), oidc.Discovery(**DISCOVERY),
                               tok, "n0", naa=None)


def test_profil_er_lukket_ukjent_claim_forkastes(monkeypatch):
    _mock_nett(monkeypatch)
    tok = _id_token(nonce="n", name="Ada Lovelace", email="ada@x.no",
                    email_verified=True, avdeling="hemmelig",
                    ssn="ikke-lagre-dette")
    ident = oidc._valider_id_token(_provider(), oidc.Discovery(**DISCOVERY),
                                   tok, "n", naa=None)
    assert ident.profil == {"visningsnavn": "Ada Lovelace",
                            "epost": "ada@x.no", "epost_verifisert": True}
    assert "avdeling" not in ident.profil and "ssn" not in ident.profil


# ---------------------------------------------------------------------------
# /start: PKCE S256 (v3 §1)
# ---------------------------------------------------------------------------

def test_bygg_start_har_s256_pkce_og_unike_verdier(monkeypatch):
    import base64
    import hashlib
    _mock_nett(monkeypatch)
    s1 = oidc.bygg_start(_provider(), "https://disponit.com/v1/oidc/callback")
    s2 = oidc.bygg_start(_provider(), "https://disponit.com/v1/oidc/callback")
    assert s1.state != s2.state and s1.nonce != s2.nonce, "engangsverdier"
    assert "code_challenge_method=S256" in s1.autorisasjonsurl
    # Challenge = base64url(sha256(verifier)) — verifiserbart.
    forventet = base64.urlsafe_b64encode(
        hashlib.sha256(s1.code_verifier.encode()).digest()).decode().rstrip("=")
    assert f"code_challenge={forventet}" in s1.autorisasjonsurl
    assert "response_type=code" in s1.autorisasjonsurl
