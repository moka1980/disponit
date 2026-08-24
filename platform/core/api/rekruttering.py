"""M-57s leseflate + signeringsvei (utførelsesarmen, første ben).

Tre ruter, formet av flatens kontrakt (flater/rekruttering.js):

* GET  /v1/rekruttering/prosesser — prosessene med kandidater, vekter og
  innstilte lister, lest RETT fra 057-lagrene under RLS. `decisions:read`
  (flatens svakeste ledd, WCAG-flate-formen).
* POST /v1/rekruttering/lister/{id}/signer — signeringen. Går gjennom
  DEN EKTE kjeden: `signer_utsendingsliste` (056) med øktens bruker som
  signatar og SP-2-nøkkel fra Idempotency-Key. Endepunktet verifiserer
  først at innholdshashen KROPPEN bærer er listens — signataren signerer
  bytene dialogen viste kortformen av, aldri bare et liste-id.
* POST /v1/rekruttering/prosesser/{id}/blinding — avskruing er en
  AUDITERT handling med varig revisjonsevidens, og evidensdesignet er
  #159 (K2: selvattestert avskruing er ikke evidens). Til #159 lander,
  svarer ruten en KODET avvisning — aldri en stille suksess uten spor.

Vektene: den varige kilden er stillingsprofilen (#162-kjeden). Til den
finnes leses vektene av evalueringsartefaktets `vekter`-felt (skrevet av
kjøringen), med fall til vekt 3 per krav — flaten regner poeng
klientsidig av nedbrytningen uansett, og serveren lyver aldri om
opphavet: feltet `vekter_kilde` sier hvilken vei som ga tallene.
"""
from __future__ import annotations

import json

import psycopg


def _leseauth_beslutninger(tjeneste, request, conn, rid):
    """Som policyadmin_http._leseauth, men for `decisions:read` — flatens
    lese-scope. -> (tenant, bid)."""
    from . import kjerne
    from .app import _autentiser
    from .policyadmin_http import _Avbrudd, _feil, _gjenopprett_kontekst
    try:
        auth = _autentiser(tjeneste, request, conn, rid, "decisions:read")
    except kjerne.Feilsvar as f:
        raise _Avbrudd(_feil(f.kode, rid))
    bid = auth.token_id.split("sesjon:", 1)[-1]
    conn.rollback()
    _gjenopprett_kontekst(conn, auth.tenant, bid, rid)
    return auth.tenant, bid


def _kandidater(conn, tenant, prosess_id):
    rader = conn.execute(
        "SELECT kandidat_id, artefakt FROM kandidat_evalueringsartefakt"
        " WHERE tenant=%s AND prosess_id=%s AND slettet_ts IS NULL"
        " ORDER BY kandidat_id", (tenant, prosess_id)).fetchall()
    kandidater, vekter, kilde = [], None, "standard"
    for kid, artefakt in rader:
        art = artefakt or {}
        if vekter is None and isinstance(art.get("vekter"), dict):
            vekter, kilde = art["vekter"], "evalueringsartefakt"
        funn = art.get("funn") or []
        status = art.get("status") or (
            "innstilt_avslag" if any(
                f.get("kategori") == "krav_ikke_dokumentert" for f in funn)
            else "vurderes" if funn else "anbefalt")
        kandidater.append({
            "kandidat_id": str(kid),
            "oppfylt": art.get("oppfylt") or {},
            "status": status,
            "funn": funn,
            "intervjusporsmal": art.get("intervjusporsmal") or [],
        })
    if vekter is None:
        krav = sorted({k for kand in kandidater for k in kand["oppfylt"]})
        vekter = {k: 3 for k in krav}
    return kandidater, vekter, kilde


def _lister(conn, tenant, oppdrag_id):
    """Innstilte lister på evalueringsoppdraget: nyeste versjon per serie
    (den uten barn), med signaturstatus."""
    rader = conn.execute(
        "SELECT l.liste_id, l.listetype, l.antall, l.innhold_hash,"
        "       (s.liste_id IS NOT NULL) AS signert"
        "  FROM utsendingsliste l"
        "  LEFT JOIN utsendingssignatur s"
        "    ON s.tenant = l.tenant AND s.liste_id = l.liste_id"
        " WHERE l.tenant=%s AND l.oppdrag_id=%s"
        "   AND NOT EXISTS (SELECT 1 FROM utsendingsliste b"
        "                    WHERE b.tenant=l.tenant"
        "                      AND b.utkast_serie=l.utkast_serie"
        "                      AND b.forrige_liste_id=l.liste_id)"
        " ORDER BY l.opprettet", (tenant, oppdrag_id)).fetchall()
    return [{"liste_id": str(r[0]), "listetype": r[1], "antall": r[2],
             "innhold_hash": r[3], "signert": bool(r[4])} for r in rader]


def prosesser_endepunkt(tjeneste, request):
    """GET /v1/rekruttering/prosesser."""
    from .app import _rid
    from .policyadmin_http import _med_conn, _ok
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _leseauth_beslutninger(tjeneste, request, conn, rid)
        prosesser = []
        for pid, oppdrag_id in conn.execute(
                "SELECT prosess_id, oppdrag_id FROM rekrutteringsprosess"
                " WHERE tenant=%s AND slettet_ts IS NULL"
                " ORDER BY opprettet DESC", (tenant,)).fetchall():
            kandidater, vekter, kilde = _kandidater(conn, tenant, pid)
            prosesser.append({
                "prosess_id": str(pid),
                "blinding_av": False,   # avskruing finnes ikke før #159
                "vekter": vekter,
                "vekter_kilde": kilde,
                "kandidater": kandidater,
                "lister": _lister(conn, tenant, oppdrag_id),
            })
        return _ok({"prosesser": prosesser}, rid)

    return _med_conn(tjeneste, rid, kjor)


def signer_endepunkt(tjeneste, request):
    """POST /v1/rekruttering/lister/{liste_id}/signer — 056-kjeden.

    Signeringen er den irreversible handlingen i M-57, og endepunktet
    legger ingenting til kjeden: medlemskaps- og materialitetsportene bor
    i `signer_utsendingsliste` (056). Det ene laget HER er hash-ekkoet:
    kroppen bærer innholdshashen dialogen viste, og en liste som har fått
    en NY versjon i mellomtiden er ikke listen signataren leste (409).
    """
    from .app import _rid
    from .policyadmin_http import (_Avbrudd, _browserkontekst, _feil,
                                   _krev_idem, _kropp, _med_conn, _ok)
    rid = _rid(request)
    # `:uuid`-konverteren i ruten avviser misformede id-er FØR basen
    # (CodeRabbit major, pre-commit-pass 24/8) — 404 fra ruteren, aldri
    # en psycopg-feil på en tekst som ikke er en UUID.
    liste_id = request.path_params["liste_id"]

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "bestilling:opprett")
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        if not isinstance(kropp.get("innhold_hash"), str):
            raise _Avbrudd(_feil("request_feilformet", rid))
        rad = conn.execute(
            "SELECT innhold_hash, antall, listetype FROM utsendingsliste"
            " WHERE tenant=%s AND liste_id=%s",
            (tenant, liste_id)).fetchone()
        if rad is None:
            raise _Avbrudd(_feil("liste_ukjent", rid, 404))
        if rad[0] != kropp["innhold_hash"]:
            raise _Avbrudd(_feil("innhold_endret", rid, 409))
        try:
            conn.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                         (tenant, liste_id, bid, nokkel))
        except psycopg.errors.InsufficientPrivilege as e:
            tjeneste.logg.hendelse("signatar_avvist", rid)
            raise _Avbrudd(_feil("signatar_uten_medlemskap", rid, 403)) \
                from e
        except psycopg.errors.UniqueViolation as e:
            raise _Avbrudd(_feil("serien_alt_signert", rid, 409)) from e
        conn.commit()
        return _ok({"innhold_hash": rad[0], "antall": rad[1],
                    "listetype": rad[2]}, rid, 201)

    return _med_conn(tjeneste, rid, kjor)


def blinding_endepunkt(tjeneste, request):
    """POST /v1/rekruttering/prosesser/{id}/blinding — se modul-docstring:
    KODET avvisning til #159s evidensdesign er landet. Autentiserer og
    CSRF-verner likevel, så avvisningen aldri blir en anonym probe."""
    from .app import _rid
    from .policyadmin_http import _browserkontekst, _feil, _med_conn
    rid = _rid(request)

    def kjor(conn):
        _browserkontekst(tjeneste, request, conn, rid, "bestilling:opprett")
        return _feil("blinding_avskruing_krever_159", rid, 409)

    return _med_conn(tjeneste, rid, kjor)
