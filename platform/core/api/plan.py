"""Planflaten (044): CRUD over de HERDEDE funksjonene — ingen knapp
skriver status, og runtime har ingen bordtilgang.

Scopene er planens egne (§6): `plan:opprett` (opprett + stans),
`plan:aktiver`, `plan:gjenoppta`. Én menneskelig handling per endepunkt;
planen er stående INTENSJON, ikke stående utførelsesfullmakt — retten
til å utføre avgjøres av policyen per tick, aldri her.
"""
from __future__ import annotations

import json

import psycopg
from starlette.requests import Request
from starlette.responses import Response

from .bestilling import BESTILLINGSTYPER, normaliser, Bestillingsfeil

#: Lukket kropp for opprettelsen. Parametrene valideres med SAMME
#: normaliser som bestillingsveien (minus bestillingstype-nøkkelen) —
#: en plan skal ikke kunne bære en kropp bestillingen ville avvist.
SKJEMAFELT = {"bestillingstype", "hostname", "sti", "kravsett", "omfang",
              "maks_sider", "rytme", "ukedag", "manedsdag", "time_lokal",
              "tidssone"}


def _planfeil(kode: str):
    class _F(Exception):
        pass
    f = _F()
    f.kode = kode
    return f


def _normaliser_plan(tenant: str, data: dict) -> dict:
    if not isinstance(data, dict) or set(data) - SKJEMAFELT:
        raise Bestillingsfeil("request_feilformet")
    rytme = data.get("rytme")
    if rytme not in ("daglig", "ukentlig", "manedlig"):
        raise Bestillingsfeil("request_feilformet")
    time_lokal = data.get("time_lokal")
    if not isinstance(time_lokal, int) or isinstance(time_lokal, bool) \
            or not (0 <= time_lokal <= 23):
        raise Bestillingsfeil("request_feilformet")
    tidssone = data.get("tidssone")
    if not isinstance(tidssone, str) or not tidssone:
        raise Bestillingsfeil("request_feilformet")
    ukedag, manedsdag = data.get("ukedag"), data.get("manedsdag")
    # Rytme-komplettheten speiles her for LESBAR feil; CHECK-en i basen
    # er porten (port 2/4).
    if rytme == "daglig" and (ukedag is not None or manedsdag is not None):
        raise Bestillingsfeil("request_feilformet")
    if rytme == "ukentlig" and (not isinstance(ukedag, int)
                                or not (1 <= ukedag <= 7)
                                or manedsdag is not None):
        raise Bestillingsfeil("request_feilformet")
    if rytme == "manedlig" and (not isinstance(manedsdag, int)
                                or not (1 <= manedsdag <= 28)
                                or ukedag is not None):
        raise Bestillingsfeil("request_feilformet")
    # Bestillingsparametrene gjennom bestillingsveiens EGEN normaliser:
    # kropp-kontrakten er én, ikke to.
    bestillingsdel = {k: data[k] for k in
                      ("bestillingstype", "hostname", "sti", "kravsett",
                       "omfang", "maks_sider") if k in data}
    norm = normaliser(tenant, bestillingsdel)
    return {"bestillingstype": norm["bestillingstype"],
            "parametre": bestillingsdel,
            "rytme": rytme, "ukedag": ukedag, "manedsdag": manedsdag,
            "time_lokal": time_lokal, "tidssone": tidssone}


def _med_browserkontekst(tjeneste, request, scope, fn, *, lesing=False):
    """Mutasjoner går gjennom `_browserkontekst` (scope + CSRF). Lesende
    ruter (`lesing=True`) autentiserer uten CSRF — dobbel innsending
    verner skrivinger; en GET bærer aldri et mutasjonsscope (samme
    begrunnelse som domenelisten i domener.py)."""
    from . import kjerne
    from .app import _autentiser, _feilsvar, _rid, kanonisk_json
    from .policyadmin_http import (_Avbrudd, _browserkontekst,
                                   _gjenopprett_kontekst)
    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        return _feilsvar("db_utilgjengelig", rid)
    try:
        if lesing:
            try:
                auth = _autentiser(tjeneste, request, conn, rid, scope)
            except kjerne.Feilsvar as f:
                return _feilsvar(f.kode, rid)
            tenant = auth.tenant
            bid = auth.token_id.split("sesjon:", 1)[-1]
            conn.rollback()
            _gjenopprett_kontekst(conn, tenant, bid, rid)
        else:
            try:
                tenant, bid = _browserkontekst(tjeneste, request, conn,
                                               rid, scope)
            except _Avbrudd as a:
                return a.respons
        return fn(conn, tenant, bid, rid, _feilsvar, kanonisk_json)
    except psycopg.Error as e:
        conn.rollback()
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift",
                               feiltype=type(e).__name__)
        return _feilsvar("db_utilgjengelig", rid)
    finally:
        tjeneste.pool.gi_tilbake(conn)


def opprett_endepunkt(tjeneste, request: Request) -> Response:
    def _fn(conn, tenant, bid, rid, _feilsvar, kanonisk_json):
        from db.pg import sett_kontekst
        raa = request.scope.get("state", {}).get("kropp", b"")
        try:
            data = json.loads(raa.decode("utf-8"))
        except (ValueError, RecursionError):
            return _feilsvar("request_feilformet", rid)
        try:
            plan = _normaliser_plan(tenant, data)
        except Bestillingsfeil as f:
            return _feilsvar(f.kode, rid)
        sett_kontekst(conn, tenant, f"bruker:{bid}", rid)
        try:
            plan_id = conn.execute(
                "SELECT opprett_plan(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, plan["bestillingstype"],
                 json.dumps(plan["parametre"], ensure_ascii=False),
                 plan["rytme"], plan["ukedag"], plan["manedsdag"],
                 plan["time_lokal"], plan["tidssone"],
                 f"bruker:{bid}", rid)).fetchone()[0]
        except psycopg.errors.InvalidParameterValue:
            conn.rollback()
            return _feilsvar("request_feilformet", rid)
        conn.commit()
        return kanonisk_json({"plan_id": str(plan_id), "status": "utkast",
                              "request_id": rid}, 201,
                             {"x-request-id": rid})
    return _med_browserkontekst(tjeneste, request, "plan:opprett", _fn)


def _overgang(tjeneste, request, plan_id, scope, funksjon, ekstra=()):
    def _fn(conn, tenant, bid, rid, _feilsvar, kanonisk_json):
        from db.pg import sett_kontekst
        sett_kontekst(conn, tenant, f"bruker:{bid}", rid)
        try:
            conn.execute(f"SELECT {funksjon}(%s,%s{',%s' * len(ekstra)},"
                         "%s,%s)",
                         (tenant, plan_id, *ekstra, f"bruker:{bid}", rid))
        except psycopg.errors.NoDataFound:
            conn.rollback()
            return _feilsvar("ikke_funnet", rid)
        except psycopg.errors.InvalidParameterValue:
            conn.rollback()
            return _feilsvar("plan_ulovlig_tilstand", rid)
        conn.commit()
        return kanonisk_json({"plan_id": plan_id, "request_id": rid}, 200,
                             {"x-request-id": rid})
    return _med_browserkontekst(tjeneste, request, scope, _fn)


def aktiver_endepunkt(tjeneste, request: Request) -> Response:
    return _overgang(tjeneste, request, request.path_params["id"],
                     "plan:aktiver", "aktiver_plan")


def gjenoppta_endepunkt(tjeneste, request: Request) -> Response:
    return _overgang(tjeneste, request, request.path_params["id"],
                     "plan:gjenoppta", "gjenoppta_plan")


def stans_endepunkt(tjeneste, request: Request) -> Response:
    return _overgang(tjeneste, request, request.path_params["id"],
                     "plan:opprett", "stans_plan")


def liste_endepunkt(tjeneste, request: Request) -> Response:
    def _fn(conn, tenant, bid, rid, _feilsvar, kanonisk_json):
        from db.pg import sett_kontekst
        sett_kontekst(conn, tenant, f"bruker:{bid}", rid)
        rader = conn.execute(
            "SELECT plan_id, bestillingstype, parametre, rytme, ukedag,"
            " manedsdag, time_lokal, tidssone, status, pause_aarsak,"
            " opprettet FROM hent_planer(%s)", (tenant,)).fetchall()
        conn.rollback()
        planer = [{"plan_id": str(r[0]), "bestillingstype": r[1],
                   "parametre": r[2], "rytme": r[3], "ukedag": r[4],
                   "manedsdag": r[5], "time_lokal": r[6], "tidssone": r[7],
                   "status": r[8], "pause_aarsak": r[9],
                   "opprettet": r[10].isoformat()} for r in rader]
        return kanonisk_json({"planer": planer, "request_id": rid}, 200,
                             {"x-request-id": rid})
    return _med_browserkontekst(tjeneste, request, "decisions:read", _fn,
                                lesing=True)


def historikk_endepunkt(tjeneste, request: Request) -> Response:
    plan_id = request.path_params["id"]

    def _fn(conn, tenant, bid, rid, _feilsvar, kanonisk_json):
        from db.pg import sett_kontekst
        sett_kontekst(conn, tenant, f"bruker:{bid}", rid)
        tick = conn.execute(
            "SELECT vindu_start, utfall, oppdrag_id, detalj, registrert"
            "  FROM hent_plan_tick(%s,%s,50)", (tenant, plan_id)).fetchall()
        hendelser = conn.execute(
            "SELECT hendelse, aktor, detalj, ts"
            "  FROM hent_plan_hendelser(%s,%s,50)",
            (tenant, plan_id)).fetchall()
        conn.rollback()
        return kanonisk_json({
            "tick": [{"vindu_start": r[0].isoformat(), "utfall": r[1],
                      "oppdrag_id": r[2], "detalj": r[3],
                      "registrert": r[4].isoformat()} for r in tick],
            "hendelser": [{"hendelse": r[0], "aktor": r[1], "detalj": r[2],
                           "ts": r[3].isoformat()} for r in hendelser],
            "request_id": rid}, 200, {"x-request-id": rid})
    return _med_browserkontekst(tjeneste, request, "decisions:read", _fn,
                                lesing=True)
