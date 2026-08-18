"""Selvbetjent domeneverifisering (039, eiers krav 18/8).

Kundens egen flate for målautorisasjon: se domenene sine, legge til et
nytt (challenge utstedes, TXT-verdien vises ÉN gang), og følge statusen
til DOMENER-arbeideren har funnet beviset i DNS og
`verifiser_domenekontroll` har gjort resten.

Snittet er bevisst SMALT:
  * API-et kan bare UTSTEDE (skape en `ventende` rad + hash) — aldri
    bekrefte: det genererte tokenet selv og kunne ellers ha «bevist» det
    uten at noen DNS-sone noensinne bar det. Bekreftelsen tilhører
    arbeideren (039).
  * Scope `bestilling:opprett` — domeneregisteret er nøyaktig porten
    bestillingsveien håndhever, og den som kan bestille kontroller er
    den som trenger å autorisere mål. Ingen ny rolle for én flate.
  * Tokenet lagres ALDRI (016: kun sha256); svaret er eneste visning.
"""
from __future__ import annotations

import hashlib
import json
import secrets

import psycopg
from starlette.requests import Request
from starlette.responses import Response

from .bestilling import _HOSTNAME

#: Challenge-tokenets form: 32 byte entropi, hex — enkel å lime inn i en
#: sonefil, umulig å gjette, og trimmes robust av bekreftelsens btrim.
_TOKENBYTES = 32


def _rader(conn, tenant: str) -> list[dict]:
    rader = conn.execute(
        "SELECT hostname, status, wildcard, verifisert_ts, utloper,"
        " siste_vellykkede_revalidering, challenge_utstedt,"
        " challenge_utloper FROM domenekontroll WHERE tenant=%s"
        " ORDER BY hostname", (tenant,)).fetchall()
    ut = []
    for (host, status, wildcard, vts, utl, srv, cu, cul) in rader:
        ut.append({
            "hostname": host, "status": status, "wildcard": wildcard,
            "verifisert_ts": vts.isoformat() if vts else None,
            "utloper": utl.isoformat() if utl else None,
            "siste_vellykkede_revalidering":
                srv.isoformat() if srv else None,
            "challenge_utstedt": cu.isoformat() if cu else None,
            "challenge_utloper": cul.isoformat() if cul else None,
        })
    return ut


def liste_endepunkt(tjeneste, request: Request) -> Response:
    """GET /v1/domener — tenantens egne domener med status.

    Lesende rute, lese-scope (`decisions:read` — pr008-invarianten: en
    GET bærer aldri et mutasjonsscope) og UTEN CSRF — dobbel innsending
    verner skrivinger. Å SE listen er lesing av egen tilstand; å ENDRE
    den (POST) krever bestilling:opprett + CSRF."""
    from . import kjerne
    from .app import _autentiser, _feilsvar, _rid, kanonisk_json
    from .policyadmin_http import _gjenopprett_kontekst
    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        return _feilsvar("db_utilgjengelig", rid)
    try:
        try:
            auth = _autentiser(tjeneste, request, conn, rid,
                               "decisions:read")
        except kjerne.Feilsvar as f:
            return _feilsvar(f.kode, rid)
        tenant = auth.tenant
        bid = auth.token_id.split("sesjon:", 1)[-1]
        conn.rollback()
        _gjenopprett_kontekst(conn, tenant, bid, rid)
        rader = _rader(conn, tenant)
        conn.rollback()
        return kanonisk_json({"domener": rader, "request_id": rid}, 200,
                             {"x-request-id": rid})
    except psycopg.Error as e:
        conn.rollback()
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift",
                               feiltype=type(e).__name__)
        return _feilsvar("db_utilgjengelig", rid)
    finally:
        tjeneste.pool.gi_tilbake(conn)


def utsted_endepunkt(tjeneste, request: Request) -> Response:
    """POST /v1/domener {hostname} → challenge. TXT-verdien vises ÉN gang.

    Reutstedelse er gratis og idempotent på radnivå (hash/vindu oppdateres)
    — men gir selvsagt en NY verdi, og da er det den nye som gjelder.

    En rad som står `utlopt` — eller `tilbakekalt` uten motpart, altså av en
    operatør — KØES samtidig tilbake til `ventende` (039), slik at arbeideren
    faktisk ser utfordringen. En kandidat M-37 AVVISTE får også utstede, men
    raden blir stående `tilbakekalt` med motparten: arbeideren tar den likevel,
    og beviset fører til en NY avklaringsgenerasjon, aldri til `verifisert`.

    Står raden i en pågående M-37-avklaring, svarer basen nei og klienten får
    409 `domene_challenge_avvist` — aldri 201 med en TXT-oppskrift ingen
    arbeider kommer til å lese.
    """
    from .app import _feilsvar, _rid, kanonisk_json
    from .policyadmin_http import _Avbrudd, _browserkontekst
    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        return _feilsvar("db_utilgjengelig", rid)
    try:
        try:
            tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                           "bestilling:opprett")
        except _Avbrudd as a:
            return a.respons
        raa = request.scope.get("state", {}).get("kropp", b"")
        try:
            data = json.loads(raa.decode("utf-8"))
        except (ValueError, RecursionError):
            # `json.loads` er REKURSIV (Codex P2). Et syntaktisk gyldig, dypt
            # nøstet dokument på noen få kilobyte ligger godt under
            # kroppsgrensen og treffer likevel rekursjonsgrensen —
            # RecursionError er en RuntimeError, ikke en ValueError, så
            # `except ValueError` alene slapp klientinput ut som generisk 500
            # i stedet for det dokumenterte `request_feilformet`. DYBDE er
            # klientinput på lik linje med syntaks; naboendepunktet
            # (`bestilling`) fanger begge av nøyaktig samme grunn, og denne
            # parseren skal ikke være unntaket.
            return _feilsvar("request_feilformet", rid)
        if not isinstance(data, dict) or set(data) - {"hostname"}:
            return _feilsvar("request_feilformet", rid)
        hostname = data.get("hostname")
        if (not isinstance(hostname, str)
                or not _HOSTNAME.match(hostname.strip().lower())):
            return _feilsvar("request_feilformet", rid)
        hostname = hostname.strip().lower()

        token = secrets.token_hex(_TOKENBYTES)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        try:
            # Den GUARDEDE formen (039), aldri 016s rå `utsted_challenge`:
            # den stoler på `p_tenant`, og gitt til den delte runtime-rollen
            # var den et kryss-tenant skriveprimitiv — bytt hashen på en
            # annen tenants `ventende` rad, og DNS-beviset holdes mot ditt
            # token. Innpakningen binder `p_tenant` til den tenantkonteksten
            # `_browserkontekst` nettopp satte (`krev_tenantkontekst`, 038).
            conn.execute(
                "SELECT utsted_challenge_selvbetjent(%s,%s,false,%s,%s)",
                (tenant, hostname, token_hash, f"bruker:{bid}"))
            conn.commit()
        except psycopg.errors.InvalidParameterValue as e:
            # DEN FORVENTEDE NEIEN, og bare den (Codex P2). Funksjonens egne
            # porter — åpen M-37-avklaring, ukanonisk hostname — reiser
            # `invalid_parameter_value`. Det er en TILSTAND hos kunden, og 409
            # er riktig svar.
            conn.rollback()
            tjeneste.logg.hendelse("domene_challenge_avvist", rid, tenant,
                                   art="sikkerhet",
                                   feiltype=type(e).__name__)
            return _feilsvar("domene_challenge_avvist", rid)
        except psycopg.Error as e:
            # ALT ANNET er drift, ikke kundens tilstand: funksjonen er ikke
            # utrullet (UndefinedFunction), grantet mangler
            # (InsufficientPrivilege), basen er nede. Fanget som 409 fortalte
            # vi kunden at DOMENET hennes forbød en utfordring — mens en
            # utrullingsfeil som rammer ALLE lå i loggen som en
            # sikkerhetsavvisning, altså det ene stedet ingen leter etter en
            # nedetid.
            conn.rollback()
            tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift",
                                   feiltype=type(e).__name__)
            return _feilsvar("db_utilgjengelig", rid)
        tjeneste.logg.hendelse("domene_challenge_utstedt", rid, tenant,
                               hostname=hostname)
        return kanonisk_json({
            "hostname": hostname,
            "txt_navn": hostname,
            "txt_verdi": token,
            "gyldig_dager": 7,
            "request_id": rid,
        }, 201, {"x-request-id": rid})
    finally:
        tjeneste.pool.gi_tilbake(conn)
