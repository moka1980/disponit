"""OIDC-flyt over den IP-pinnede transporten (PR-010 v3–v6).

Ansvarsdeling (v6 §3): Authlib eier authorization-code + PKCE-veksling,
joserfc eier JWS/ID-token-validering. Disponit eier SSRF-transporten (som
biblioteket får INJISERT), discovery-validering, browserbinding og
sesjonsopprettelse. INGEN hjemmelaget JWT-parser her (grep-port).

Discovery er ENESTE metadatakilde (v6 §2): endepunktene hentes fra
discovery-dokumentet, `issuer` må matche eksakt, og HVERT returnerte
endepunkt kjøres gjennom egresspolicyen før bruk. Validert metadata caches
med TTL 1 t; refresh feiler FAIL-CLOSED (gammel metadata brukes aldri på
ubestemt tid).
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

from authlib.integrations.httpx_client import OAuth2Client
from joserfc import jwt
from joserfc.jwk import KeySet

from . import ssrf

DISCOVERY_TTL = 3600.0        # 1 time (v3 §2 / v6 §2)
JWKS_MIN_REFRESH = 60.0       # ukjent kid utløser refresh, men ratebegrenset


class OidcFeil(Exception):
    """Enhver avvisning i OIDC-flyten — generisk utad, detaljert i logg."""


@dataclass(frozen=True)
class Discovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


@dataclass(frozen=True)
class Provider:
    provider_id: str
    issuer: str
    discovery_url: str
    client_id: str
    client_secret: str            # hentet fra credential — aldri lagret i DB
    tillatte_algoritmer: tuple
    allowlist: tuple = ()         # staging (scheme,host,port,cidr)


# ---------------------------------------------------------------------------
# Egress: hvert provider-endepunkt valideres FØR bruk (v5 §2)
# ---------------------------------------------------------------------------

def _valider_egress(url: str, allowlist: tuple) -> None:
    """Kaster OidcFeil hvis URL-en ikke er en trygg HTTPS-adresse innenfor
    egresspolicyen. Ingen userinfo, ingen fragment (v5 §2)."""
    delt = urlsplit(url)
    if delt.username or delt.password or delt.fragment:
        raise OidcFeil(f"endepunkt har userinfo/fragment: {url}")
    port = delt.port or (443 if delt.scheme == "https" else 80)
    try:
        ssrf.valider_og_pin(delt.scheme, delt.hostname or "", port, allowlist)
    except ssrf.SsrfAvvist as e:
        raise OidcFeil(f"endepunkt utenfor egresspolicy: {url} ({e})") from e


# ---------------------------------------------------------------------------
# Discovery: hent, valider, cache (fail-closed)
# ---------------------------------------------------------------------------

#: provider_id -> (Discovery, keyset, utloper_ts, jwks_sist_refresh)
_CACHE: dict[str, tuple] = {}


def _hent_json(url: str, allowlist: tuple) -> dict:
    klient = ssrf.lag_klient(allowlist)
    try:
        r = klient.get(url)
        if r.status_code != 200:
            raise OidcFeil(f"{url} ga status {r.status_code}")
        raw = ssrf.les_begrenset(r)          # 256 KiB-tak
        return json.loads(raw)
    except ssrf.SsrfAvvist as e:
        raise OidcFeil(str(e)) from e
    finally:
        klient.close()


def hent_discovery(provider: Provider, naa: float | None = None) -> Discovery:
    """Validert discovery, cachet med TTL. Refresh etter utløp; feiler
    refresh → provideren er utilgjengelig (gammel metadata brukes ikke)."""
    naa = naa if naa is not None else time.monotonic()
    cachet = _CACHE.get(provider.provider_id)
    if cachet is not None and cachet[2] > naa:
        return cachet[0]

    doc = _hent_json(provider.discovery_url, provider.allowlist)
    if doc.get("issuer") != provider.issuer:
        raise OidcFeil(
            f"discovery-issuer {doc.get('issuer')!r} != forventet "
            f"{provider.issuer!r}")
    d = Discovery(
        issuer=doc["issuer"],
        authorization_endpoint=doc["authorization_endpoint"],
        token_endpoint=doc["token_endpoint"],
        jwks_uri=doc["jwks_uri"])
    for ep in (d.authorization_endpoint, d.token_endpoint, d.jwks_uri):
        _valider_egress(ep, provider.allowlist)

    keyset = _hent_jwks(d.jwks_uri, provider.allowlist)
    _CACHE[provider.provider_id] = (d, keyset, naa + DISCOVERY_TTL, naa)
    return d


def _hent_jwks(jwks_uri: str, allowlist: tuple) -> KeySet:
    doc = _hent_json(jwks_uri, allowlist)
    return KeySet.import_key_set(doc)


def _keyset_for(provider: Provider, ukjent_kid: bool = False,
                naa: float | None = None) -> KeySet:
    """JWKS fra cachen; ved ukjent `kid` refetches ÉN gang, ratebegrenset
    (v3 §2 / v6 §1). Fortsatt ukjent → kalleren avviser fail-closed."""
    naa = naa if naa is not None else time.monotonic()
    cachet = _CACHE.get(provider.provider_id)
    if cachet is None:
        hent_discovery(provider, naa)
        cachet = _CACHE[provider.provider_id]
    d, keyset, utlop, jwks_sist = cachet
    if ukjent_kid and naa - jwks_sist >= JWKS_MIN_REFRESH:
        keyset = _hent_jwks(d.jwks_uri, provider.allowlist)
        _CACHE[provider.provider_id] = (d, keyset, utlop, naa)
    return keyset


# ---------------------------------------------------------------------------
# /start: state, nonce, PKCE (S256) — Disponit eier verdiene (V6)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Startverdier:
    autorisasjonsurl: str
    state: str
    nonce: str
    code_verifier: str


def _b64url(raa: bytes) -> str:
    return base64.urlsafe_b64encode(raa).decode("ascii").rstrip("=")


def bygg_start(provider: Provider, redirect_uri: str,
               scope: str = "openid profile email") -> Startverdier:
    d = hent_discovery(provider)
    state = _b64url(secrets.token_bytes(32))
    nonce = _b64url(secrets.token_bytes(32))
    code_verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())
    q = urlencode({
        "response_type": "code",
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return Startverdier(f"{d.authorization_endpoint}?{q}", state, nonce,
                        code_verifier)


# ---------------------------------------------------------------------------
# /callback: kodeveksling (Authlib) + ID-token-validering (joserfc)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Identitet:
    issuer: str
    sub: str
    profil: dict          # LUKKET DTO (v5 §5)


def veksle_og_valider(provider: Provider, redirect_uri: str, code: str,
                      code_verifier: str, nonce: str,
                      naa: float | None = None) -> Identitet:
    """Veksler koden og validerer ID-tokenet. Kaster OidcFeil ved ethvert
    avvik (generisk utad). Bruker den pinnede transporten (V4)."""
    d = hent_discovery(provider, naa)
    transport = ssrf.PinnetTransport(allowlist=provider.allowlist, retries=0)
    klient = OAuth2Client(
        client_id=provider.client_id, client_secret=provider.client_secret,
        redirect_uri=redirect_uri, transport=transport,
        timeout=ssrf.READ_TIMEOUT, follow_redirects=False)
    try:
        token = klient.fetch_token(
            d.token_endpoint, grant_type="authorization_code",
            code=code, code_verifier=code_verifier)
    except Exception as e:
        raise OidcFeil(f"tokenveksling feilet: {type(e).__name__}") from e
    finally:
        klient.close()

    id_token = token.get("id_token")
    if not id_token:
        raise OidcFeil("token-svaret manglet id_token")
    return _valider_id_token(provider, d, id_token, nonce, naa)


def _valider_id_token(provider: Provider, d: Discovery, id_token: str,
                      nonce: str, naa: float | None) -> Identitet:
    # `alg: none` og enhver algoritme utenfor allowlisten avvises av
    # `algorithms=` (v6 §3) — joserfc eier JWS-valideringen, ingen egen
    # base64-dekoding.
    algs = list(provider.tillatte_algoritmer)
    keyset = _keyset_for(provider, naa=naa)
    try:
        tok = jwt.decode(id_token, keyset, algorithms=algs)
    except Exception:
        # Ukjent kid? Refetch JWKS ÉN gang (ratebegrenset) og prøv igjen.
        keyset = _keyset_for(provider, ukjent_kid=True, naa=naa)
        try:
            tok = jwt.decode(id_token, keyset, algorithms=algs)
        except Exception as e:
            raise OidcFeil(f"id_token-signatur ugyldig: {type(e).__name__}") \
                from e

    claims = tok.claims
    naa_epoch = int(naa) if naa is not None else int(time.time())
    krav = jwt.JWTClaimsRegistry(
        iss={"essential": True, "value": provider.issuer},
        aud={"essential": True, "value": provider.client_id},
        exp={"essential": True}, iat={"essential": True},
        now=naa_epoch)
    try:
        krav.validate(claims)
    except Exception as e:
        raise OidcFeil(f"id_token-claims ugyldige: {type(e).__name__}") from e

    if claims.get("nonce") != nonce:
        raise OidcFeil("nonce-mismatch")
    sub = claims.get("sub")
    if not sub:
        raise OidcFeil("id_token manglet sub")

    # Lukket profil-DTO (v5 §5): kun tre felt, resten forkastes.
    profil = {
        "visningsnavn": str(claims.get("name", ""))[:128],
        "epost": (str(claims["email"])[:254] if claims.get("email") else None),
        "epost_verifisert": (bool(claims["email_verified"])
                             if "email_verified" in claims else None),
    }
    return Identitet(issuer=provider.issuer, sub=str(sub), profil=profil)


def toem_cache() -> None:
    """For tester: nullstill discovery-/JWKS-cachen."""
    _CACHE.clear()
