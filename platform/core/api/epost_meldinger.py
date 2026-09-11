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
          # HVEM tok den, sagt av tallene: en melding som forsvant FØR
          # fristen ble slettet av et menneske (176); en som forsvant
          # etter, ble tatt av retensjonen. Flaten skal ikke si «slettet
          # etter fristen» om noe eier nettopp slettet selv.
          "slettet_ts": r[10].isoformat() if r[10] else None,
          "slettet_for_fristen": bool(r[10] is not None and r[10] < r[6]),
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


# ---------------------------------------------------------------------
# Svarutkastet (179): mennesket skriver, mennesket godkjenner.
#
# Teksten krypteres HER med tenantens DEK, som meldingskroppen — basen
# ser aldri et svar i klartekst. Sendingen er en egen vei (PR 3–5): et
# godkjent utkast er en TILSTAND, ikke en handling, og ingenting går ut
# før policyporten har sagt ja.
# ---------------------------------------------------------------------

MAKS_SVAR = 32 * 1024


def skriv_utkast_endepunkt(tjeneste, request: Request) -> Response:
    """POST /v1/epost/meldinger/{melding_id}/svarutkast
    (epost:utkast:behandle, idem): svaret et menneske skrev."""
    from db import kryptering
    from db.pg import sett_kontekst

    from .app import _rid
    from .epost_kilde import _modul_inaktiv
    from .policyadmin_http import _feil, _kropp, _med_conn, _ok_lagret
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
                               "epost:utkast:behandle")
        except kjerne.Feilsvar as f:
            return _feil(f.kode, rid)
        kropp = _kropp(request)
        tekst = kropp.get("tekst")
        if not isinstance(tekst, str) or not tekst.strip():
            return _feil("request_feilformet", rid, 400,
                         detalj="tekst mangler")
        if len(tekst) > MAKS_SVAR:
            return _feil("request_feilformet", rid, 400, detalj="tekst er lang")
        conn.rollback()
        sett_kontekst(conn, auth.tenant, auth.aktor, rid)
        key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, auth.tenant)
        ct, nonce = kryptering.krypter(dek, {"tekst": tekst}, auth.tenant,
                                       key_id)
        try:
            with conn.transaction():
                uid = conn.execute(
                    "SELECT m6_skriv_svarutkast(%s,%s,%s,%s,%s,%s)",
                    (auth.tenant, mid, ct, nonce, key_id,
                     auth.aktor)).fetchone()[0]
        except psycopg.errors.InsufficientPrivilege:
            # VAKTENS NEI BETYR AT MELDINGEN IKKE FINNES FOR ØKTEN:
            # usynlig under RLS, eller alt reapet. Begge leses «ikke
            # funnet» av et menneske.
            #
            # Fangsten var `psycopg.Error` (CodeRabbit): en brutt
            # tilkobling eller en serialiseringsfeil ble da rapportert
            # som at MELDINGEN ikke fantes, og et menneske som nettopp
            # hadde skrevet et svar fikk vite at meldingen var borte.
            # Alt annet bobler videre til rammens vanlige driftsvei.
            return _feil("ikke_funnet", rid, 404)
        return _ok_lagret(conn, {"utkast_id": str(uid), "status": "foreslatt"},
                          rid)

    return _med_conn(tjeneste, rid, kjor)


def send_svaret_endepunkt(tjeneste, request: Request) -> Response:
    """POST /v1/epost/utkast/{utkast_id}/send (epost:utkast:behandle, idem).

    MENNESKETS EGEN SENDING, ikke en agenthandling: teksten er skrevet av
    et menneske, og den går ut fra kundens egen postboks. Derfor ingen
    policyport og ingen godkjenningsrunde med seg selv — eiervedtak 10/9,
    andre runde.

    Sendingen SELV skjer ikke her: nøkkelen til postboksen er med vilje
    utenfor web-API-ets rekkevidde (088), så et innbrudd her ikke gir
    noen retten til å sende i kundens navn. Dette setter utkastet i kø;
    bakgrunnsprosessen med nøkkelen sender det."""
    from db.pg import sett_kontekst

    from .app import _rid
    from .epost_kilde import _modul_inaktiv
    from .policyadmin_http import _feil, _med_conn, _ok_lagret
    rid = _rid(request)
    av = _modul_inaktiv(tjeneste, rid)
    if av is not None:
        return av
    uid = request.path_params["utkast_id"]

    def kjor(conn):
        import psycopg

        from . import kjerne
        from .app import _autentiser
        try:
            auth = _autentiser(tjeneste, request, conn, rid,
                               "epost:utkast:behandle")
        except kjerne.Feilsvar as f:
            return _feil(f.kode, rid)
        conn.rollback()
        sett_kontekst(conn, auth.tenant, auth.aktor, rid)
        try:
            with conn.transaction():
                ny = conn.execute("SELECT m6_send_svaret(%s,%s,%s)",
                                  (auth.tenant, uid, auth.aktor)).fetchone()[0]
        except psycopg.errors.ForeignKeyViolation:
            return _feil("ikke_funnet", rid, 404)
        except psycopg.errors.IntegrityConstraintViolation:
            # BARE dørens egen dom blir 409 (CodeRabbit): alt sendt,
            # forkastet, eller en postboks uten sendetilgang. En brutt
            # tilkobling eller en serialiseringsfeil er DRIFT, og skal
            # ikke se ut som at svaret ikke kunne sendes — den boblet
            # videre til rammens vanlige feilvei.
            return _feil("epost_ulovlig_tilstand", rid, 409)
        return _ok_lagret(conn, {"utkast_id": str(uid), "status": ny}, rid)

    return _med_conn(tjeneste, rid, kjor)


def avgjor_utkast_endepunkt(tjeneste, request: Request) -> Response:
    """POST /v1/epost/utkast/{utkast_id}/dom
    (epost:utkast:behandle, idem): menneskets ja eller nei.

    `sendt` er IKKE en dom her — den er kvitteringens vei tilbake når
    plattformen faktisk har sendt (PR 6), og døra nekter den."""
    from db.pg import sett_kontekst

    from .app import _rid
    from .epost_kilde import _modul_inaktiv
    from .policyadmin_http import _feil, _kropp, _med_conn, _ok_lagret
    rid = _rid(request)
    av = _modul_inaktiv(tjeneste, rid)
    if av is not None:
        return av
    uid = request.path_params["utkast_id"]

    def kjor(conn):
        import psycopg

        from . import kjerne
        from .app import _autentiser
        try:
            auth = _autentiser(tjeneste, request, conn, rid,
                               "epost:utkast:behandle")
        except kjerne.Feilsvar as f:
            return _feil(f.kode, rid)
        status = (_kropp(request) or {}).get("status")
        if status not in ("godkjent", "forkastet", "brukt_manuelt"):
            return _feil("request_feilformet", rid, 400, detalj="status")
        conn.rollback()
        sett_kontekst(conn, auth.tenant, auth.aktor, rid)
        try:
            with conn.transaction():
                ny = conn.execute("SELECT m6_avgjor_utkast(%s,%s,%s,%s)",
                                  (auth.tenant, uid, status,
                                   auth.aktor)).fetchone()[0]
        except psycopg.errors.ForeignKeyViolation:
            return _feil("ikke_funnet", rid, 404)
        except (psycopg.errors.IntegrityConstraintViolation,
                psycopg.errors.InsufficientPrivilege):
            # Vaktens nei er en TILSTANDSDOM: et utkast som alt er sendt
            # eller forkastet, kan ikke avgjøres på nytt. Alt annet er
            # drift og boblet videre (CodeRabbit).
            return _feil("epost_ulovlig_tilstand", rid, 409)
        return _ok_lagret(conn, {"utkast_id": str(uid), "status": ny}, rid)

    return _med_conn(tjeneste, rid, kjor)


def utkast_for(conn, tenant: str, melding_id) -> list:
    """Utkastene under én melding, nyeste først. Teksten dekrypteres for
    økten; en reapet rad bærer ingen."""
    from db import kryptering
    ut = []
    for r in conn.execute(
            "SELECT utkast_id, status, opprettet, avgjort_ts, avgjort_av,"
            " tekst_kryptert, nonce, key_id, slettet_ts, sendt_ts,"
            " feilgrunn FROM epost_utkast"
            " WHERE tenant=%s AND melding_id=%s"
            " ORDER BY opprettet DESC, utkast_id",
            (tenant, melding_id)).fetchall():
        tekst = None
        if r[5] is not None and r[8] is None:
            dek = kryptering.hent_dek(conn, tenant, r[7])
            tekst = kryptering.dekrypter(dek, bytes(r[5]), bytes(r[6]),
                                         tenant, r[7]).get("tekst")
        ut.append({"utkast_id": str(r[0]), "status": r[1],
                   "opprettet": r[2].isoformat(),
                   "avgjort_ts": r[3].isoformat() if r[3] else None,
                   "avgjort_av": r[4], "tekst": tekst,
                   "slettet": r[8] is not None,
                   "sendt_ts": r[9].isoformat() if r[9] else None,
                   "feilgrunn": r[10]})
    return ut


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
        except psycopg.errors.ForeignKeyViolation:
            # Dørens egen «finnes ikke» (176 reiser nettopp denne).
            # Fangsten var `psycopg.Error` (CodeRabbit): drift ble
            # rapportert som en melding som ikke fantes.
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
        ut = _rad(conn, tenant, r, med_kropp=True)
        # Svarutkastene hører til meldingen, og detaljen er der de leses.
        ut["utkast"] = utkast_for(conn, tenant, mid)
        return _ok(ut, rid)

    return _med_conn(tjeneste, rid, kjor)
