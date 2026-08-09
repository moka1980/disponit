#!/usr/bin/env python3
"""Mock OIDC-IdP for staging-e2e (PR-010). KUN for test — signerer med en
efemer RSA-nøkkel og har ingen ekte brukere. Kjøres på loopback og legges
i DISPONIT_OIDC_ALLOWLIST slik at den pinnede transporten slipper den
gjennom (det eksakte staging-unntaket, aldri «tillat private IP-er»).

Endepunkter: discovery, JWKS, /authorize (302 tilbake med code), /token
(bytter code mot et signert ID-token som ekko-er nonce og sub).
"""
import json
import sys
import time
from urllib.parse import urlencode

import uvicorn
from joserfc import jwt
from joserfc.jwk import RSAKey, KeySet
from starlette.applications import Starlette
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

ISSUER = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9000"
SUB = "test-bruker-001"
KEY = RSAKey.generate_key(2048, {"kid": "mock-k1", "use": "sig"})
_KODER: dict[str, dict] = {}    # code -> {nonce, aud}


async def discovery(request):
    return JSONResponse({
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/jwks",
        "id_token_signing_alg_values_supported": ["RS256"],
    })


async def jwks(request):
    return JSONResponse(KeySet([KEY]).as_dict(private=False))


async def authorize(request):
    q = request.query_params
    code = "kode_" + str(int(time.time() * 1000))
    _KODER[code] = {"nonce": q.get("nonce", ""), "aud": q.get("client_id")}
    tilbake = q["redirect_uri"] + "?" + urlencode(
        {"code": code, "state": q.get("state", "")})
    return RedirectResponse(tilbake, status_code=302)


async def token(request):
    # Parse form-urlencoded body uten python-multipart-avhengighet.
    from urllib.parse import parse_qs
    raa = (await request.body()).decode("utf-8")
    form = {k: v[0] for k, v in parse_qs(raa).items()}
    code = form.get("code")
    lagret = _KODER.pop(code, None)
    if lagret is None:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    naa = int(time.time())
    claims = {"iss": ISSUER, "sub": SUB, "aud": lagret["aud"],
              "exp": naa + 300, "iat": naa, "nonce": lagret["nonce"],
              "name": "Test Bruker", "email": "test@disponit.example",
              "email_verified": True}
    id_token = jwt.encode({"alg": "RS256", "kid": KEY.kid}, claims, KEY)
    return JSONResponse({"access_token": "mock-at", "token_type": "Bearer",
                         "id_token": id_token, "expires_in": 300})


app = Starlette(routes=[
    Route("/.well-known/openid-configuration", discovery),
    Route("/jwks", jwks),
    Route("/authorize", authorize),
    Route("/token", token, methods=["POST"]),
])

if __name__ == "__main__":
    port = int(ISSUER.rsplit(":", 1)[1]) if ":" in ISSUER.split("//")[1] \
        else 9000
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
