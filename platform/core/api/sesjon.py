"""Brukersesjon over OIDC (PR-010 v1–v6): fire ruter, tre cookies,
callback-statusmaskin, rate-grenser, browserbinding.

Sikkerhetsgrensene som bor HER (ikke i biblioteket): browserbindingen
(v4 §1), tenant/provider-allowlist, callback-statusmaskinen med atomisk
konsum FØR nettverkskall (v4 §3), sesjonsgrensen serialisert (v3 §6),
rate-grensene (v4 §4) og at cookie XOR Bearer (v2 §8).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

import psycopg
from starlette.requests import Request
from starlette.responses import Response

from db import kryptering
from db.pg import sett_kontekst, sett_tenant
from . import oidc
from .autorisasjon import scopes_for_roller

# --- Cookie-navn (v2 §2, v3 §4, v4 §1) -------------------------------------
C_SESJON = "__Host-disponit_sesjon"     # HttpOnly, referanse
C_CSRF = "__Host-disponit_csrf"         # JS-lesbar (UI må lese)
C_BINDING = "__Host-disponit_oidc"      # HttpOnly, browserbinding

INAKTIV_MIN = 30
ABSOLUTT_TIMER = 12
LOGIN_TX_MIN = 10
MAKS_SESJONER = 5

# Rate (v4 §4): (fase, vindu_s, maks, backoff_s)
RATE = {
    "start":            (300, 20, 0),
    "callback_ugyldig": (300, 10, 900),
    "callback_token":   (300, 10, 900),
    "medlemskap":       (900, 5, 3600),
    "nodbrems":         (900, 200, 1800),
}


class SesjonFeil(Exception):
    def __init__(self, kode: str, http: int = 401, retry_after: int = 0):
        self.kode, self.http, self.retry_after = kode, http, retry_after


def _hash(v: str) -> str:
    return hashlib.sha256(v.encode("utf-8")).hexdigest()


def er_forventet_origin(origin: str, host: str) -> bool:
    """EKSAKT same-origin (P1): scheme https, host == forventet host, ingen
    userinfo/path/query/fragment, og port default (443) eller udefinert.
    En substringtest ville sluppet `disponit.com.evil.example` og
    `https://disponit.com@evil` gjennom."""
    from urllib.parse import urlsplit
    if not origin:
        return False
    try:
        d = urlsplit(origin)
    except ValueError:
        return False
    if d.scheme != "https" or d.username or d.password:
        return False
    if d.path or d.query or d.fragment:
        return False
    if (d.hostname or "").lower() != host.lower():
        return False
    # Port må være 443 (https-default) eller uoppgitt — aldri en avvikende.
    if d.port not in (None, 443):
        return False
    return True


def trygg_retursti(raa) -> str:
    """En LOKAL absolute-path-referanse (P1): nøyaktig én ledende skråstrek,
    ingen scheme/netloc, ingen backslash eller kontrolltegn. `startswith('/')`
    slapp `//evil.example/phish` gjennom — en scheme-relativ URL som
    navigerer browseren til et fremmed vertsnavn. Ugyldig → '/'."""
    from urllib.parse import urlsplit
    if not isinstance(raa, str) or not raa.startswith("/"):
        return "/"
    # `//x` (scheme-relativ) og `/\x` (backslash-triks) må avvises.
    if raa.startswith("//") or raa.startswith("/\\") or "\\" in raa:
        return "/"
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in raa):   # CR/LF/kontroll
        return "/"
    d = urlsplit(raa)
    # En ren path-referanse har verken scheme eller netloc.
    if d.scheme or d.netloc:
        return "/"
    return d.path + (("?" + d.query) if d.query else "")


def _naa() -> datetime:
    return datetime.now(timezone.utc)


def _cookie(navn: str, verdi: str, *, http_only: bool, max_age: int) -> str:
    flagg = [f"{navn}={verdi}", "Path=/", "Secure", "SameSite=Lax",
             f"Max-Age={max_age}"]
    if http_only:
        flagg.append("HttpOnly")
    return "; ".join(flagg)


def _slett_cookie(navn: str, http_only: bool = True) -> str:
    return _cookie(navn, "", http_only=http_only, max_age=0)


# ---------------------------------------------------------------------------
# Rate-grenser: atomisk increment + grensekontroll (v4 §4)
# ---------------------------------------------------------------------------

def _rate(conn, fase: str, nokkel: str) -> None:
    vindu_s, maks, backoff_s = RATE[fase]
    rad = conn.execute(
        "INSERT INTO oidc_rate (fase, nokkel, teller, vindu_start)"
        " VALUES (%s,%s,1,now())"
        " ON CONFLICT (fase, nokkel) DO UPDATE SET"
        "   teller = CASE WHEN oidc_rate.vindu_start"
        "                 < now() - make_interval(secs => %s)"
        "                THEN 1 ELSE oidc_rate.teller + 1 END,"
        "   vindu_start = CASE WHEN oidc_rate.vindu_start"
        "                 < now() - make_interval(secs => %s)"
        "                THEN now() ELSE oidc_rate.vindu_start END,"
        "   sperret_til = CASE WHEN oidc_rate.teller + 1 > %s AND %s > 0"
        "                 THEN now() + make_interval(secs => %s)"
        "                 ELSE oidc_rate.sperret_til END"
        " RETURNING teller, sperret_til",
        (fase, nokkel, vindu_s, vindu_s, maks, backoff_s, backoff_s)
    ).fetchone()
    teller, sperret = rad
    if sperret is not None and sperret > _naa():
        raise SesjonFeil("rate_grense_login", 429,
                         int((sperret - _naa()).total_seconds()))
    if teller > maks:
        raise SesjonFeil("rate_grense_login", 429, vindu_s)


def _ip_prefiks(request: Request) -> str:
    # nginx setter X-Forwarded-For = klientens IP (PR-009b). /32 v4, /64 v6.
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() or (request.client.host if request.client
                                       else "0.0.0.0")
    if ":" in ip:
        return ":".join(ip.split(":")[:4]) + "::/64"
    return ip + "/32"


# ---------------------------------------------------------------------------
# Provider-oppslag: tenant fra host, provider fra allowlist
# ---------------------------------------------------------------------------

def _tenant_fra_host(tjeneste, request: Request) -> str:
    """Workspace utledes SERVER-SIDE fra den kanoniske hosten nginx satte
    (X-Disponit-Host), aldri fra body (v2 §5 / v5 §1)."""
    host = request.headers.get("x-disponit-host") \
        or request.headers.get("host", "").split(":")[0]
    # I v1 er workspace == host; en ekte slug→tenant-mapping er data. For
    # staging bruker vi host direkte som tenant-slug.
    return host.lower().split(".")[0] if host else ""


def _provider_for(conn, tenant: str, provider_id: str,
                  allowlist_env: dict) -> oidc.Provider:
    rad = conn.execute(
        "SELECT p.issuer, p.discovery_url, p.client_id, p.client_secret_ref,"
        " p.tillatte_algoritmer, t.redirect_uris"
        "  FROM oidc_provider p JOIN tenant_oidc_provider t"
        "    ON t.provider_id = p.provider_id"
        " WHERE p.provider_id=%s AND p.aktiv AND t.tenant=%s",
        (provider_id, tenant)).fetchone()
    if rad is None:
        raise SesjonFeil("ukjent_provider", 400)
    issuer, disc, cid, secret_ref, algs, redirect_uris = rad
    secret = allowlist_env.get(secret_ref)
    if not secret:
        # Manglende credential → provideren er utilgjengelig, fail-closed.
        raise SesjonFeil("provider_utilgjengelig", 400)
    allow = _staging_allowlist()
    return (oidc.Provider(provider_id, issuer, disc, cid, secret,
                          tuple(algs), allow), list(redirect_uris))


def _staging_allowlist() -> tuple:
    """Eksakt (scheme,host,port,cidr) for test-IdP, fra miljø. Tom i prod."""
    raa = os.environ.get("DISPONIT_OIDC_ALLOWLIST", "")
    ut = []
    for post in filter(None, (p.strip() for p in raa.split(";"))):
        s, h, p, c = post.split(",")
        ut.append((s, h, int(p), c))
    return tuple(ut)


def _cred_env() -> dict:
    """Provider-hemmeligheter fra LoadCredential-miljøet (PR-009 §5).
    Navn på formen DISPONIT_OIDC_SECRET_<ref>."""
    ut = {}
    for k, v in os.environ.items():
        if k.startswith("DISPONIT_OIDC_SECRET_"):
            ut[k[len("DISPONIT_OIDC_SECRET_"):].lower()] = v
    return ut


# ---------------------------------------------------------------------------
# POST /v1/oidc/start (v5 §1) → 303
# ---------------------------------------------------------------------------

def les_startkropp(content_type: str, raa: bytes) -> dict:
    """Parse /oidc/start-kroppen. V2 (PR-011): innlogging skjer som TOPPNIVÅ-
    navigasjon via et ordinært <form method="post">, som sender
    application/x-www-form-urlencoded — ikke JSON. Vi tar imot begge:
    JSON-veien er uendret (byte-for-byte), form-veien er additiv. Begge
    kroppene går gjennom NØYAKTIG samme Origin/Sec-Fetch- og
    provider-validering i handleren. Kaster ValueError ved uleselig kropp.
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct == "application/x-www-form-urlencoded":
        from urllib.parse import parse_qsl
        return dict(parse_qsl(raa.decode("utf-8"), keep_blank_values=True))
    return json.loads(raa.decode("utf-8")) if raa else {}


def oidc_start(tjeneste, request: Request) -> Response:
    from .app import _rid, _feilsvar, kanonisk_json
    rid = _rid(request)
    # Fetch Metadata / Origin (v5 §1 + v6 §4): fravær av Sec-Fetch-Site →
    # krev godkjent Origin, ellers avvis.
    sfs = request.headers.get("sec-fetch-site")
    host = request.headers.get("x-disponit-host") \
        or request.headers.get("host", "").split(":")[0]
    if sfs is not None:
        if sfs not in ("same-origin", "same-site", "none"):
            return _feilsvar("request_feilformet", rid)
    else:
        # P1 (Codex): en substringtest (`host in origin`) er IKKE same-origin
        # — `Origin: https://disponit.com.evil.example` inneholder host-navnet.
        # Krev EKSAKT forventet origin: https://<kanonisk host>, riktig/default
        # port, ingen userinfo/path/query/fragment.
        if not host or not er_forventet_origin(request.headers.get("origin", ""),
                                               host):
            return _feilsvar("request_feilformet", rid)

    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        return _feilsvar("db_utilgjengelig", rid)
    try:
        tenant = _tenant_fra_host(tjeneste, request)
        raa = request.scope.get("state", {}).get("kropp", b"")
        try:
            data = les_startkropp(request.headers.get("content-type", ""), raa)
        except ValueError:
            return _feilsvar("request_feilformet", rid)
        provider_id = data.get("provider_id")
        if not isinstance(provider_id, str) or not tenant:
            return _feilsvar("request_feilformet", rid)

        sett_kontekst(conn, tenant, "oidc-start", rid)
        try:
            _rate(conn, "start", f"{_ip_prefiks(request)}|{tenant}|{provider_id}")
            _rate(conn, "nodbrems", tenant)
            provider, redirect_uris = _provider_for(
                conn, tenant, provider_id, _cred_env())
            redirect_uri = redirect_uris[0]
            sv = oidc.bygg_start(provider, redirect_uri)
            binding = secrets.token_urlsafe(32)
            # pkce krypteres i ro med tenantens DEK.
            key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
            ct, nonce = kryptering.krypter(dek, {"v": sv.code_verifier},
                                           tenant, key_id)
            # Én binding om gangen (v5 §8): ryddes implisitt av at bare denne
            # transaksjonens binding_hash matcher i callback.
            conn.execute(
                "INSERT INTO oidc_logintransaksjon (state_hash, binding_hash,"
                " nonce, pkce_kryptert, pkce_nonce, pkce_key_id, provider_id,"
                " tenant_kandidat, retursti, utloper) VALUES"
                " (%s,%s,%s,%s,%s,%s,%s,%s,%s, now() + make_interval(mins => %s))",
                (_hash(sv.state), _hash(binding), sv.nonce, ct, nonce, key_id,
                 provider_id, tenant, trygg_retursti(data.get("retursti")),
                 LOGIN_TX_MIN))
            conn.commit()
        except SesjonFeil as f:
            conn.rollback()
            r = _feilsvar(f.kode, rid, f.http)
            if f.retry_after:
                r.headers["Retry-After"] = str(f.retry_after)
            return r
        except oidc.OidcFeil:
            conn.rollback()
            return _feilsvar("provider_utilgjengelig", rid, 400)

        r = Response(status_code=303, headers={"Location": sv.autorisasjonsurl,
                                               "x-request-id": rid})
        r.raw_headers.append(
            (b"set-cookie",
             _cookie(C_BINDING, binding, http_only=True,
                     max_age=LOGIN_TX_MIN * 60).encode()))
        return r
    finally:
        tjeneste.pool.gi_tilbake(conn)


# ---------------------------------------------------------------------------
# GET /v1/oidc/callback
# ---------------------------------------------------------------------------

def oidc_callback(tjeneste, request: Request) -> Response:
    from .app import _rid, _feilsvar
    rid = _rid(request)
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    binding = request.cookies.get(C_BINDING)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        return _feilsvar("db_utilgjengelig", rid)
    try:
        ip = _ip_prefiks(request)
        # Kontekstløst rate-vindu på ugyldig state (før tenant er kjent):
        # bruk en RLS-fri tabell → sett en nøytral tenantkontekst.
        sett_tenant(conn, "_oidc")
        if not code or not state or not binding:
            _rate(conn, "callback_ugyldig", ip)
            conn.commit()
            return _feil_side(rid)

        # ATOMISK NY→KONSUMERT med binding-sjekk (v4 §3), committes FØR
        # nettverkskall. tenant kommer FRA transaksjonen (v5 §8).
        rad = conn.execute(
            "UPDATE oidc_logintransaksjon SET status='KONSUMERT'"
            " WHERE state_hash=%s AND binding_hash=%s AND status='NY'"
            "   AND utloper > now()"
            " RETURNING provider_id, tenant_kandidat, nonce, pkce_kryptert,"
            "           pkce_nonce, pkce_key_id, retursti",
            (_hash(state), _hash(binding))).fetchone()
        if rad is None:
            _rate(conn, "callback_ugyldig", ip)
            conn.commit()
            return _feil_side(rid)
        (provider_id, tenant, nonce, ct, pnonce, pkey_id, retursti) = rad
        conn.commit()                 # ingen lås under nettverkskallet

        # Dekrypter pkce + veksle + valider (UTEN DB-lås).
        sett_kontekst(conn, tenant, "oidc-callback", rid)
        dekrad = conn.execute("SELECT wrapped_dek FROM tenant_nokler"
                              " WHERE tenant=%s AND key_id=%s",
                              (tenant, pkey_id)).fetchone()
        conn.rollback()
        dek = kryptering._pakk_ut((pkey_id, dekrad[0]), tenant)[1]
        verifier = kryptering.dekrypter(dek, ct, pnonce, tenant, pkey_id)["v"]
        try:
            provider, redirect_uris = _provider_for(
                conn, tenant, provider_id, _cred_env()) \
                if _sett_ctx(conn, tenant, rid) else (None, None)
            ident = oidc.veksle_og_valider(
                provider, redirect_uris[0], code, verifier, nonce)
        except (oidc.OidcFeil, SesjonFeil, Exception):
            _terminer(conn, _hash(state), "FEILET", tenant, rid)
            sett_tenant(conn, "_oidc")
            _rate(conn, "callback_token", ip)
            conn.commit()
            return _feil_side(rid)

        # Medlemskap må finnes (ingen JIT, v3 §2). Sesjon opprettes
        # serialisert (5-grensen, v3 §6), FULLFØRT i SAMME transaksjon.
        try:
            sesjon_cookie, csrf_cookie = _opprett_sesjon(
                conn, tenant, ident, _hash(state), rid, ip)
        except SesjonFeil as f:
            conn.rollback()
            return _feil_side(rid)

        # Ren, relativ redirect (v5 §6) — fjerner code/state fra historikken.
        r = Response(status_code=303, headers={
            # Validert alt ved /start; revalideres her (forsvar i dybden).
            "Location": trygg_retursti(retursti),
            "Referrer-Policy": "no-referrer", "x-request-id": rid})
        for c in (sesjon_cookie, csrf_cookie,
                  _slett_cookie(C_BINDING)):
            r.raw_headers.append((b"set-cookie", c.encode()))
        return r
    finally:
        tjeneste.pool.gi_tilbake(conn)


def _sett_ctx(conn, tenant, rid) -> bool:
    sett_kontekst(conn, tenant, "oidc-callback", rid)
    return True


def _terminer(conn, state_hash, status, tenant, rid):
    sett_kontekst(conn, tenant, "oidc-callback", rid)
    conn.execute("UPDATE oidc_logintransaksjon SET status=%s"
                 " WHERE state_hash=%s AND status='KONSUMERT'",
                 (status, state_hash))


def _opprett_sesjon(conn, tenant, ident: oidc.Identitet, state_hash, rid, ip):
    sett_kontekst(conn, tenant, "oidc-callback", rid)
    # Upsert identitet (issuer,sub).
    bid = conn.execute(
        "INSERT INTO brukeridentitet (issuer, sub, profil)"
        " VALUES (%s,%s,%s) ON CONFLICT (issuer,sub) DO UPDATE"
        " SET profil=EXCLUDED.profil RETURNING bruker_id",
        (ident.issuer, ident.sub,
         json.dumps(ident.profil, ensure_ascii=False))).fetchone()[0]
    # Serialiser sesjonsgrensen på (tenant, bruker_id) med en advisory lock
    # (v3 §6). To samtidige callbacker for samme bruker kan da ikke begge
    # se fire sesjoner og lage fem og seks. Advisory lock krever ingen
    # tabellrettighet — til forskjell fra SELECT ... FOR UPDATE.
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                 (f"{tenant}|{bid}",))
    # Medlemskap må finnes + aktivt (ingen JIT).
    med = conn.execute(
        "SELECT roller, authz_version FROM brukermedlemskap"
        " WHERE tenant=%s AND bruker_id=%s AND aktiv",
        (tenant, bid)).fetchone()
    if med is None:
        sett_tenant(conn, "_oidc")
        _rate(conn, "medlemskap", f"{ident.issuer}|{ident.sub}")
        conn.commit()
        raise SesjonFeil("ingen_tilgang", 401)
    roller, authz = med
    scopes = scopes_for_roller(roller)

    # 5-grensen: tell aktive, tilbakekall eldste over taket, ATOMISK.
    aktive = conn.execute(
        "SELECT sesjon_id_hash FROM brukersesjon WHERE tenant=%s"
        " AND bruker_id=%s AND NOT tilbakekalt AND utloper > now()"
        " ORDER BY opprettet, id", (tenant, bid)).fetchall()
    while len(aktive) >= MAKS_SESJONER:
        eldst = aktive.pop(0)[0]
        conn.execute("UPDATE brukersesjon SET tilbakekalt=true"
                     " WHERE sesjon_id_hash=%s", (eldst,))

    sesjon = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
        " authz_snapshot, csrf_hash, utloper) VALUES"
        " (%s,%s,%s,%s,%s, now() + make_interval(hours => %s))",
        (_hash(sesjon), tenant, bid, authz, _hash(csrf), ABSOLUTT_TIMER))
    conn.execute("UPDATE oidc_logintransaksjon SET status='FULLFØRT'"
                 " WHERE state_hash=%s AND status='KONSUMERT'", (state_hash,))
    # Innloggingsevidens (uten claims/hemmeligheter).
    conn.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, handling) VALUES"
        " (%s,%s,'oidc','ih','oidc','TILLAT','[]','bruker.innlogging')",
        (tenant, f"bruker:{bid}"))
    conn.commit()
    return (_cookie(C_SESJON, sesjon, http_only=True,
                    max_age=ABSOLUTT_TIMER * 3600),
            _cookie(C_CSRF, csrf, http_only=False,
                    max_age=ABSOLUTT_TIMER * 3600))


def _feil_side(rid: str) -> Response:
    # Generisk, gjengir ALDRI URL-parametere (v5 §6).
    return Response(
        content=b'{"feil":"innlogging_feilet"}', status_code=400,
        media_type="application/json",
        headers={"Referrer-Policy": "no-referrer", "x-request-id": rid})


# ---------------------------------------------------------------------------
# GET /v1/sesjon (hvem er jeg) + DELETE (logout)
# ---------------------------------------------------------------------------

def sesjon_hvem(tjeneste, request: Request) -> Response:
    from .app import _rid, _feilsvar, kanonisk_json
    rid = _rid(request)
    conn = tjeneste.pool.hent()
    try:
        prin = slaa_opp_prinsipal(tjeneste, conn, request, rid)
        if prin is None:
            return _feilsvar("sesjon_ugyldig", rid)
        tenant, bid, scopes, utloper, roller, epost = prin
        return kanonisk_json(
            {"tenant": tenant, "bruker_id": bid, "scopes": sorted(scopes),
             "roller": sorted(roller), "epost": epost,
             "utloper": utloper, "request_id": rid}, 200, {"x-request-id": rid})
    finally:
        tjeneste.pool.gi_tilbake(conn)


def csrf_matcher(lagret_hash: str | None, request: Request) -> bool:
    """True hvis X-Disponit-CSRF-headeren matcher øktens lagrede `csrf_hash` i
    KONSTANT tid. Manglende header eller hash → False. Dobbel-innsending
    (v2 §8): CSRF-verdien er JS-lesbar (`__Host-disponit_csrf`) og speiles i
    headeren; en fremmed eller fraværende token matcher aldri. Gjenbrukes av
    logout OG browser-mutasjoner (PR-012 /handling)."""
    csrf = request.headers.get("x-disponit-csrf")
    return bool(csrf) and bool(lagret_hash) \
        and hmac.compare_digest(_hash(csrf), lagret_hash)


def sesjon_logout(tjeneste, request: Request) -> Response:
    """Logout tilbakekaller økten — men KUN mot gyldig CSRF (dobbel-innsending,
    v2 §8-ånd). UI-et sender X-Disponit-CSRF fra den JS-lesbare csrf-cookien;
    serveren sammenligner hashen mot øktens lagrede `csrf_hash` i konstant tid.
    En manglende, feil eller ANNEN økts token skal ALDRI kunne logge brukeren
    ut (ellers er logout en CSRF-vektor). Uten CSRF-porten forblir økten urørt.
    """
    from .app import _rid, _feilsvar
    rid = _rid(request)
    sesjon = request.cookies.get(C_SESJON)
    conn = tjeneste.pool.hent()
    try:
        if sesjon:
            # Finn en LEVENDE økt (den herdede funksjonen skjuler tilbakekalte/
            # utløpte). Ingen levende økt → ingenting å verne, idempotent 204.
            rad = conn.execute(
                "SELECT csrf_hash FROM slaa_opp_sesjon(%s)",
                (_hash(sesjon),)).fetchone()
            conn.rollback()
            if rad is not None:
                if not csrf_matcher(rad[0], request):
                    # Forget-forsøk med feil/uten token: ØKTEN URØRT, 403.
                    tjeneste.logg.hendelse("csrf_ugyldig", rid)
                    return _feilsvar("csrf_ugyldig", rid)
                sett_tenant(conn, "_oidc")
                conn.execute("UPDATE brukersesjon SET tilbakekalt=true"
                             " WHERE sesjon_id_hash=%s AND NOT tilbakekalt",
                             (_hash(sesjon),))
                conn.commit()
        r = Response(status_code=204, headers={"x-request-id": rid})
        for c in (_slett_cookie(C_SESJON), _slett_cookie(C_CSRF, False),
                  _slett_cookie(C_BINDING)):
            r.raw_headers.append((b"set-cookie", c.encode()))
        return r
    finally:
        tjeneste.pool.gi_tilbake(conn)


# ---------------------------------------------------------------------------
# Enhetlig prinsipal: cookie XOR Bearer (v2 §8) + authz_version (v2 §3)
# ---------------------------------------------------------------------------

def slaa_opp_prinsipal(tjeneste, conn, request: Request, rid: str):
    """-> (tenant, bruker_id, scopes, utloper_iso) for en gyldig
    sesjonscookie, ellers None. Kaster SesjonFeil('dobbel_principal') hvis
    BÅDE cookie og Authorization er satt."""
    sesjon = request.cookies.get(C_SESJON)
    har_bearer = bool(request.headers.get("authorization"))
    if sesjon and har_bearer:
        raise SesjonFeil("dobbel_principal", 400)
    if not sesjon:
        return None
    rad = conn.execute(
        "SELECT tenant, bruker_id, authz_snapshot FROM slaa_opp_sesjon(%s)",
        (_hash(sesjon),)).fetchone()
    if rad is None:
        return None
    tenant, bid, snapshot = rad
    # authz_version: sesjonens snapshot må matche aktiv versjon (v2 §3).
    sett_kontekst(conn, tenant, f"bruker:{bid}", rid)
    med = conn.execute(
        "SELECT roller, authz_version FROM brukermedlemskap"
        " WHERE tenant=%s AND bruker_id=%s AND aktiv", (tenant, bid)).fetchone()
    conn.rollback()
    if med is None or med[1] != snapshot:
        return None
    roller = list(med[0] or ())
    scopes = scopes_for_roller(med[0])
    # Hvem er dette? Fire øyne krever at TO FORSKJELLIGE prinsipaler
    # attesterer, og flaten kunne ikke vise hvem som var innlogget — bare
    # `bid_10e5674…`. Med to konti i samme nettleser er det ikke en
    # kosmetisk mangel: eier kunne attestert to ganger som samme prinsipal og
    # først fått vite det av primærnøkkelen. E-posten er øktens EGEN, og
    # hentes derfor uten videre autorisasjon.
    ident = conn.execute(
        "SELECT profil->>'epost' FROM brukeridentitet WHERE bruker_id=%s",
        (bid,)).fetchone()
    conn.rollback()
    epost = ident[0] if ident else None
    utloper = (_naa() + timedelta(hours=ABSOLUTT_TIMER)).isoformat()
    return tenant, bid, scopes, utloper, roller, epost
