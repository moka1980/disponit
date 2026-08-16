"""PR-013 CP6 — HTTP-endepunktene for policyadministrasjon.

Tynne endepunkter: form + auth (`policy:write`/`policy:activate`, ADSKILTE) +
CSRF (browsermutasjon, dobbel-innsending som PR-012), så delegeres ALT til
`policyadmin`-orkestreringen i én eiertransaksjon. Muterende ruter krever en
browsersesjon (samme carve-out som unntaksbehandlingen); lesende ruter er rent
`policy:read`.

`Idempotency-Key` er PÅKREVD kun for attesteringen (den kan aktivere en policy
og må derfor være replay-sikker); utkast-CRUD er naturlig idempotent på
`utkastversjon`/utkast_id.
"""
from __future__ import annotations

import hashlib
import json

import psycopg

from . import policyadmin

#: Feilkode → HTTP. Ukjent kode → 409 (tilstandskonflikt), som PR-012.
_FEIL_HTTP = {
    "utkast_ukjent": 404, "utkast_feilformet": 400,
    "utkast_ulovlig_tilstand": 409, "utkastversjon_utdatert": 409,
    "utkast_ikke_validert": 409, "runde_allerede_aapen": 409,
    "ingen_aktiv_runde": 409, "runde_utlopt": 409, "diff_utdatert": 409,
    "allerede_attestert": 409, "rebasering_kreves": 409,
    "semantikk_endret": 409, "base_mangler": 409, "base_korrupt": 409,
    "aktiv_peker_usynk": 409,
    "versjon_i_bruk": 409, "versjon_mangler": 409,
    "idempotenskonflikt": 409, "sikkerhet": 409,
    "scope_mangler": 403, "mangler_medlemskap": 403,
    "token_ugyldig": 401, "rate_grense": 429,
    "dobbel_principal": 400, "sesjon_ugyldig": 401, "csrf_ugyldig": 403,
    "request_feilformet": 400, "idempotensnokkel_mangler": 400,
    "db_utilgjengelig": 503, "policy_ugyldig": 422,
    # Innholdet er ugyldig (innføringskontrakten), ikke tilstanden: 422 som
    # `policy_ugyldig` — eier må rette utkastet, ikke prøve igjen.
    "utkast_ugyldig": 422,
}


class _Avbrudd(Exception):
    """Bærer et ferdig HTTP-svar ut av auth/CSRF-hjelperen."""

    def __init__(self, respons):
        self.respons = respons


def _feil(kode: str, rid: str, http: int | None = None):
    from starlette.responses import JSONResponse
    return JSONResponse({"feil": kode, "request_id": rid},
                        status_code=http or _FEIL_HTTP.get(kode, 409),
                        headers={"x-request-id": rid})


def _ok(res: dict, rid: str, http: int = 200):
    from starlette.responses import JSONResponse
    return JSONResponse(res, status_code=http, headers={"x-request-id": rid})


def _krev_idem(request, rid: str) -> str:
    """`Idempotency-Key` er PÅKREVD på ALLE skriveruter (spec, Codex P1 R3).
    Reiser `_Avbrudd` med 400 hvis den mangler."""
    idem = request.headers.get("idempotency-key")
    if not idem or not idem.strip():
        raise _Avbrudd(_feil("idempotensnokkel_mangler", rid))
    return idem.strip()


def _input_hash(*deler) -> str:
    return hashlib.sha256("\x1f".join(str(d) for d in deler)
                          .encode("utf-8")).hexdigest()


def opprett_input_hash(tenant, bid, policy_id, innhold, rollback_av, idem) -> str:
    """Idempotens-inputhash for utkastopprettelse. `rollback_av_versjon` INNGÅR
    (Codex R2/R3): en rullbakk og en ordinær opprettelse med samme nøkkel er
    ULIKE operasjoner og MÅ gi konflikt, ikke replay. Egen funksjon så bindingen
    er direkte testbar. `rollback_av` JSON-kodes så `null` og `""` gir ULIKE
    representasjoner (Codex R4: `str(None)`/`str("")` kolliderte ikke lenger)."""
    return _input_hash(tenant, bid, "opprett", policy_id,
                       json.dumps(innhold, sort_keys=True),
                       json.dumps(rollback_av), idem)


def _kropp(request) -> dict:
    raa = request.scope.get("state", {}).get("kropp", b"")
    try:
        body = json.loads(raa.decode("utf-8"))
    except Exception:
        raise _Avbrudd(None)
    if not isinstance(body, dict):
        raise _Avbrudd(None)
    return body


def _browserkontekst(tjeneste, request, conn, rid: str, scope: str):
    """auth (browsersesjon, gitt scope) + CSRF. -> (tenant, bid). Reiser
    `_Avbrudd` med ferdig feilsvar. Speiler `handling_endepunkt` (PR-012)."""
    from . import kjerne
    from . import sesjon as sesjonmodul
    from .app import _autentiser

    try:
        auth = _autentiser(tjeneste, request, conn, rid, scope)
    except kjerne.Feilsvar as f:
        raise _Avbrudd(_feil(f.kode, rid))
    sesjon_cookie = request.cookies.get(sesjonmodul.C_SESJON)
    rad = conn.execute("SELECT csrf_hash FROM slaa_opp_sesjon(%s)",
                       (sesjonmodul._hash(sesjon_cookie),)).fetchone() \
        if sesjon_cookie else None
    conn.rollback()
    if rad is None or not sesjonmodul.csrf_matcher(rad[0], request):
        tjeneste.logg.hendelse("csrf_ugyldig", rid)
        raise _Avbrudd(_feil("csrf_ugyldig", rid))
    tenant = auth.tenant
    bid = auth.token_id.split("sesjon:", 1)[-1]
    return tenant, bid


def _leseauth(tjeneste, request, conn, rid: str):
    """auth for en lesende rute (`policy:read`, ingen CSRF). -> (tenant, bid)."""
    from . import kjerne
    from .app import _autentiser
    try:
        auth = _autentiser(tjeneste, request, conn, rid, "policy:read")
    except kjerne.Feilsvar as f:
        raise _Avbrudd(_feil(f.kode, rid))
    bid = auth.token_id.split("sesjon:", 1)[-1]
    conn.rollback()
    return auth.tenant, bid


def _med_conn(tjeneste, rid: str, fn):
    """Hent en forbindelse, kjør `fn(conn)`, håndter Aktiveringsfeil + drift."""
    from .app import _rid  # noqa: F401  (holder importgrafen lik app.py)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift")
        return _feil("db_utilgjengelig", rid)
    try:
        return fn(conn)
    except _Avbrudd as a:
        conn.rollback()
        return a.respons if a.respons is not None else _feil(
            "request_feilformet", rid)
    except policyadmin.Aktiveringsfeil as g:
        conn.rollback()
        return _feil(g.kode, rid)
    finally:
        tjeneste.pool.gi_tilbake(conn)


# --------------------------------------------------------------------------
# Muterende ruter (browsersesjon + CSRF).
# --------------------------------------------------------------------------

def opprett_utkast_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "policy:write")
        idem = _krev_idem(request, rid)
        body = _kropp(request)
        policy_id = body.get("policy_id")
        innhold = body.get("innhold")
        if not isinstance(policy_id, str) or not policy_id.strip() \
                or not isinstance(innhold, dict):
            return _feil("request_feilformet", rid)
        rollback_av = body.get("rollback_av_versjon")
        ih = opprett_input_hash(tenant, bid, policy_id, innhold, rollback_av,
                                idem)
        res = policyadmin.opprett_utkast(
            conn, tenant=tenant, aktor=bid, request_id=rid,
            policy_id=policy_id, innhold=innhold,
            idempotency_key=idem, input_hash=ih,
            rollback_av_versjon=rollback_av)
        return _ok(res, rid, 201)

    return _med_conn(tjeneste, rid, kjor)


def rediger_utkast_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)
    utkast_id = request.path_params["utkast_id"]

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "policy:write")
        idem = _krev_idem(request, rid)
        body = _kropp(request)
        innhold = body.get("innhold")
        uv = body.get("utkastversjon")
        if not isinstance(innhold, dict) or not isinstance(uv, int) \
                or isinstance(uv, bool):
            return _feil("request_feilformet", rid)
        ih = _input_hash(tenant, bid, "rediger", utkast_id, uv,
                         json.dumps(innhold, sort_keys=True), idem)
        res = policyadmin.rediger_utkast(
            conn, tenant=tenant, aktor=bid, request_id=rid,
            utkast_id=utkast_id, forventet_utkastversjon=uv, innhold=innhold,
            idempotency_key=idem, input_hash=ih)
        return _ok(res, rid)

    return _med_conn(tjeneste, rid, kjor)


def valider_utkast_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)
    utkast_id = request.path_params["utkast_id"]

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "policy:write")
        idem = _krev_idem(request, rid)
        body = _kropp(request)
        uv = body.get("utkastversjon")
        if not isinstance(uv, int) or isinstance(uv, bool):
            return _feil("request_feilformet", rid)
        # Versjonen inngår i input-hashen → nøkkelen er bundet til utkastets
        # tilstand (Codex R3).
        ih = _input_hash(tenant, bid, "valider", utkast_id, uv, idem)
        res = policyadmin.valider_utkast(
            conn, tenant=tenant, aktor=bid, request_id=rid, utkast_id=utkast_id,
            forventet_utkastversjon=uv, idempotency_key=idem, input_hash=ih)
        if res.get("utfall") == "ugyldig":
            from starlette.responses import JSONResponse
            return JSONResponse({"feil": "policy_ugyldig", "detaljer": res["feil"],
                                 "request_id": rid}, status_code=422,
                                headers={"x-request-id": rid})
        return _ok(res, rid)

    return _med_conn(tjeneste, rid, kjor)


def forkast_utkast_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)
    utkast_id = request.path_params["utkast_id"]

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "policy:write")
        idem = _krev_idem(request, rid)
        body = _kropp(request)
        uv = body.get("utkastversjon")
        if not isinstance(uv, int) or isinstance(uv, bool):
            return _feil("request_feilformet", rid)
        ih = _input_hash(tenant, bid, "forkast", utkast_id, uv, idem)
        res = policyadmin.forkast_utkast(
            conn, tenant=tenant, aktor=bid, request_id=rid, utkast_id=utkast_id,
            forventet_utkastversjon=uv, idempotency_key=idem, input_hash=ih)
        return _ok(res, rid)

    return _med_conn(tjeneste, rid, kjor)


def apne_runde_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)
    utkast_id = request.path_params["utkast_id"]

    def kjor(conn):
        from datetime import datetime, timezone
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "policy:activate")
        idem = _krev_idem(request, rid)
        ih = _input_hash(tenant, bid, "aapne_runde", utkast_id, idem)
        res = policyadmin.opprett_aktiveringsrunde(
            conn, tenant=tenant, aktor=bid, request_id=rid,
            utkast_id=utkast_id, idempotency_key=idem, input_hash=ih,
            naa=datetime.now(timezone.utc))
        return _ok(res, rid, 201)

    return _med_conn(tjeneste, rid, kjor)


def attester_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)
    utkast_id = request.path_params["utkast_id"]

    def kjor(conn):
        from datetime import datetime, timezone
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "policy:activate")
        body = _kropp(request)
        diff_hash = body.get("diff_hash")
        if not isinstance(diff_hash, str) or not diff_hash:
            return _feil("request_feilformet", rid)
        idem = request.headers.get("idempotency-key")
        if not idem or not idem.strip():
            return _feil("idempotensnokkel_mangler", rid)
        idem = idem.strip()
        input_hash = hashlib.sha256(
            f"{tenant}\x1f{bid}\x1f{utkast_id}\x1f{diff_hash}\x1f{idem}"
            .encode("utf-8")).hexdigest()
        res = policyadmin.attester_aktivering(
            conn, tjeneste.mac_register, tenant=tenant, aktor=bid,
            request_id=rid, utkast_id=utkast_id, forventet_diff_hash=diff_hash,
            idempotency_key=idem, input_hash=input_hash,
            naa=datetime.now(timezone.utc))
        return _ok(res, rid)

    return _med_conn(tjeneste, rid, kjor)


# --------------------------------------------------------------------------
# Lesende ruter (policy:read, ingen CSRF).
# --------------------------------------------------------------------------

def hent_utkast_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)
    utkast_id = request.path_params["utkast_id"]

    def kjor(conn):
        tenant, bid = _leseauth(tjeneste, request, conn, rid)
        res = policyadmin.hent_utkast_detalj(
            conn, tenant=tenant, aktor=bid, request_id=rid, utkast_id=utkast_id)
        return _ok(res, rid)

    return _med_conn(tjeneste, rid, kjor)


def maler_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)

    def kjor(conn):
        _leseauth(tjeneste, request, conn, rid)      # policy:read
        return _ok({"maler": policyadmin.hent_maler()}, rid)

    return _med_conn(tjeneste, rid, kjor)


def list_utkast_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _leseauth(tjeneste, request, conn, rid)
        policy_id = request.query_params.get("policy_id")
        res = policyadmin.list_utkast(
            conn, tenant=tenant, aktor=bid, request_id=rid, policy_id=policy_id)
        return _ok({"utkast": res}, rid)

    return _med_conn(tjeneste, rid, kjor)
