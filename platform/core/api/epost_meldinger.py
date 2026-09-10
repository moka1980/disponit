"""M-6 PR-D a: meldingsflaten — det innhenteren (175) la i registeret,
lest av et menneske med `epost:read`.

To ruter, begge lesende:
* GET /v1/epost/meldinger?kilde=<uuid>&grense=<n> — lista: avsender,
  emne, mottatt, forhåndsvisning, vedlegg-flagg og slettefristen.
  Persondataene ligger i den krypterte payloaden og dekrypteres HER,
  under RLS-kontekst, med tenantens DEK — basen selv ser aldri
  klarteksten (088). En reapet melding vises som reapet: hasher og
  tidspunkt består, teksten er borte, og det sies.
* GET /v1/epost/meldinger/{melding_id} — kroppen.
* POST /v1/epost/meldinger/{melding_id}/slett — sletter NÅ, før fristen
  (176). Én skrivevei, og den fjerner: teksten og adressen tømmes i alle
  lagrene samtidig, tidspunktet og hashene består som revisjonsspor, og
  handlingen bokføres. `epost:kilde:administrer` — M-6s forvaltnings-
  scope; en lesende økt kan se, ikke slette.

Ingen sendevei, ingen modellvei (dommen 31/8): flaten VISER, og det ene
den kan gjøre med en melding, er å fjerne den. Lista er avkortet
(`MAKS_MELDINGER`) og sier det.
"""
from __future__ import annotations

import uuid as uuidlib

from starlette.requests import Request
from starlette.responses import Response

MAKS_MELDINGER = 200
FORHANDSVISNING = 200


def _pakk_ut(conn, tenant: str, r) -> dict | None:
    """Payloaden i klartekst, eller None for en reapet rad. En rad som
    IKKE lar seg dekryptere er en feil, ikke en tom melding (CodeRabbit):
    den går til endepunktets vanlige feilvei."""
    from db import kryptering
    ct, nonce, key_id = r
    if ct is None or nonce is None or key_id is None:
        return None
    dek = kryptering.hent_dek(conn, tenant, key_id)
    return kryptering.dekrypter(dek, bytes(ct), bytes(nonce), tenant, key_id)


def _rad(conn, tenant: str, r, *, med_kropp: bool) -> dict:
    p = _pakk_ut(conn, tenant, (r[7], r[8], r[9]))
    ut = {"melding_id": str(r[0]), "kilde_id": str(r[1]),
          "mottatt_ts": r[2].isoformat(), "retning": r[3],
          "har_vedlegg": bool(r[4]), "trad_id": r[5],
          "slettes_ts": r[6].isoformat(),
          "reapet": r[10] is not None,
          "fra": None, "fra_navn": None, "emne": None,
          "forhandsvisning": None}
    if med_kropp:
        ut.update({"til": [], "kropp": "", "kropp_type": "text"})
    if p is not None:
        ut.update({"fra": p.get("fra"), "fra_navn": p.get("fra_navn"),
                   "emne": p.get("emne"),
                   "forhandsvisning": str(p.get("forhandsvisning")
                                          or p.get("kropp") or "")
                   [:FORHANDSVISNING]})
        if med_kropp:
            ut["til"] = p.get("til") or []
            ut["kropp"] = p.get("kropp") or ""
            ut["kropp_type"] = p.get("kropp_type") or "text"
    return ut


_SELECT = ("SELECT melding_id, kilde_id, mottatt_ts, retning, har_vedlegg,"
           " trad_id, mottatt_ts + slettefrist_dogn * interval '1 day',"
           " kropp_kryptert, nonce, key_id, slettet_ts FROM epost_melding")


def liste_endepunkt(tjeneste, request: Request) -> Response:
    from .app import _rid
    from .epost_kilde import _leseauth_epost, _modul_inaktiv
    from .policyadmin_http import _feil, _med_conn, _ok
    rid = _rid(request)
    av = _modul_inaktiv(tjeneste, rid)
    if av is not None:
        return av

    def kjor(conn):
        tenant, _bid = _leseauth_epost(tjeneste, request, conn, rid)
        kilde = request.query_params.get("kilde")
        try:
            kid = uuidlib.UUID(kilde) if kilde else None
            grense = min(max(int(request.query_params.get("grense")
                                 or MAKS_MELDINGER), 1), MAKS_MELDINGER)
        except ValueError:
            return _feil("request_feilformet", rid, 400)
        if kid is not None:
            rader = conn.execute(
                _SELECT + " WHERE tenant=%s AND kilde_id=%s"
                " ORDER BY mottatt_ts DESC, melding_id LIMIT %s",
                (tenant, kid, grense + 1)).fetchall()
        else:
            rader = conn.execute(
                _SELECT + " WHERE tenant=%s"
                " ORDER BY mottatt_ts DESC, melding_id LIMIT %s",
                (tenant, grense + 1)).fetchall()
        avkortet = len(rader) > grense
        rader = rader[:grense]
        return _ok({"meldinger": [_rad(conn, tenant, r, med_kropp=False)
                                  for r in rader],
                    "vist": len(rader), "avkortet": avkortet}, rid)

    return _med_conn(tjeneste, rid, kjor)


def slett_endepunkt(tjeneste, request: Request) -> Response:
    """POST /v1/epost/meldinger/{melding_id}/slett
    (epost:kilde:administrer, idem): retensjonen gjort NÅ, av et
    menneske. Idempotent — en melding som alt er slettet svarer 200 med
    `ny: false`, aldri en feil."""
    from db.pg import sett_kontekst

    from .app import _rid
    from .epost_kilde import _modul_inaktiv
    from .policyadmin_http import _feil, _med_conn, _ok_lagret
    rid = _rid(request)
    av = _modul_inaktiv(tjeneste, rid)
    if av is not None:
        return av
    mid = request.path_params["melding_id"]

    def kjor(conn):
        import psycopg

        from . import kjerne
        from .app import _autentiser
        try:
            auth = _autentiser(tjeneste, request, conn, rid,
                               "epost:kilde:administrer")
        except kjerne.Feilsvar as f:
            return _feil(f.kode, rid)
        conn.rollback()
        sett_kontekst(conn, auth.tenant, auth.aktor, rid)
        try:
            with conn.transaction():
                ny = conn.execute("SELECT m6_slett_melding(%s,%s,%s)",
                                  (auth.tenant, mid, auth.aktor)).fetchone()[0]
        except psycopg.Error:
            return _feil("ikke_funnet", rid, 404)
        # `_ok_lagret` COMMITTER: uten det ville poolen rullet tilbake
        # slettingen mens svaret sa at den skjedde.
        return _ok_lagret(conn, {"melding_id": str(mid), "slettet": True,
                                 "ny": bool(ny)}, rid)

    return _med_conn(tjeneste, rid, kjor)


def detalj_endepunkt(tjeneste, request: Request) -> Response:
    from .app import _rid
    from .epost_kilde import _leseauth_epost, _modul_inaktiv
    from .policyadmin_http import _feil, _med_conn, _ok
    rid = _rid(request)
    av = _modul_inaktiv(tjeneste, rid)
    if av is not None:
        return av
    mid = request.path_params["melding_id"]

    def kjor(conn):
        tenant, _bid = _leseauth_epost(tjeneste, request, conn, rid)
        r = conn.execute(_SELECT + " WHERE tenant=%s AND melding_id=%s",
                         (tenant, mid)).fetchone()
        if r is None:
            return _feil("ikke_funnet", rid, 404)
        return _ok(_rad(conn, tenant, r, med_kropp=True), rid)

    return _med_conn(tjeneste, rid, kjor)
