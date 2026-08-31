"""M-8 tidsvalg — de OFFENTLIGE dørene (082): kandidaten velger
intervjutid uten innlogging, bak et kapabilitetstoken.

NY ruteklasse — uautentisert utenom OIDC: ingen cookie, ingen sesjon,
ingen CSRF. Kapabiliteten ER credentialet (004/035-formen): lenkens
token er `tid_<token_id>.<secret>`, båret i URL-FRAGMENTET — fragmentet
forlater aldri klienten og står aldri i en serverlogg. Klienten leser
fragmentet og POSTer tokenet i kroppen; serversiden lagrer kun
HMAC-SHA256(pepper, secret), og sammenligningen er konstanttid INNE i
den authenticator-eide defineren (`m8_tidsvalg_oppslag`/`m8_velg_slot`).

Uniform feildom utad (planen §2): ukjent token, feil MAC, utløpt,
erstattet, reapet prosess og lukket vindu er ÉN kode —
`tidsvalg_avvist`. Kun `slot_fullt` og `valg_alt_registrert` er
skillbare, og begge krever et gyldig token. Kandidatsiden ser KUN
binært ledig/fullt — aldri tellere, aldri hvem (DOM 4).

Forsvar i dybden foran dørene:
* kun `application/json`, kroppstak 4 KiB (RUTEKROPPSGRENSER i app.py),
* app-side ratebøtte i `oidc_rate` (sesjon._rate-formen) nøklet på
  IP + token_id ETTER formatsjekk — et token som ikke engang har formen
  brenner bøtten på IP-en alene,
* nginx har i tillegg sin egen sone (disponit_tidsvalg, 30 r/m).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import uuid as uuidlib

#: Lenkens tokenform: `tid_` + 16 byte hex token_id + `.` + 32 byte hex
#: secret. Fast form er forutsetningen for at MAC-sammenligningen i
#: basen er konstant (004: format-guarden FØR alt annet).
_TOKENMONSTER = re.compile(r"^tid_([0-9a-f]{32})\.([0-9a-f]{64})$")


def _svar(kropp: dict, rid: str, http: int = 200):
    from .app import kanonisk_json
    return kanonisk_json(kropp | {"request_id": rid}, http,
                         {"x-request-id": rid})


def _avvist(rid: str):
    """Den ENE koden for alt tokenet ikke skal kunne skille på."""
    return _svar({"feil": "tidsvalg_avvist"}, rid, 403)


def _token_fra_kropp(request, rid):
    """-> (token_id, secret) eller et ferdig feilsvar via _Avbrudd.

    Feil FORM på forespørselen (ikke JSON, ikke dict, manglende felt) er
    `request_feilformet` — formen er klientens egen kode og lekker
    ingenting. Et token som ikke matcher mønsteret dømmes derimot som
    `tidsvalg_avvist` i kalleren (uniform utad), med rate-nøkkel på IP
    alene.
    """
    from .policyadmin_http import _Avbrudd, _kropp
    kropp = _kropp(request)
    token = kropp.get("token")
    if not isinstance(token, str):
        raise _Avbrudd(_svar({"feil": "request_feilformet"}, rid, 400))
    m = _TOKENMONSTER.match(token)
    if m is None:
        return None, None, kropp
    return m.group(1), m.group(2), kropp


def _krev_json(request, rid):
    from .policyadmin_http import _Avbrudd
    ct = (request.headers.get("content-type") or "").split(";")[0].strip()
    if ct != "application/json":
        raise _Avbrudd(_svar({"feil": "request_feilformet"}, rid, 400))


def _ip(request) -> str:
    from .sesjon import _ip_prefiks
    return _ip_prefiks(request)


def _rate(tjeneste, conn, request, rid, token_id):
    """Ratebøtta (sesjon._rate-formen, egen fase). Nøkkelen er IP +
    token_id etter formatsjekk; committes med én gang så en avvist
    forespørsel også teller."""
    from . import sesjon as sesjonmodul
    from .policyadmin_http import _Avbrudd
    try:
        sesjonmodul._rate(conn, "tidsvalg",
                          f"{_ip(request)}|{token_id or 'ugyldig'}")
        conn.commit()
    except sesjonmodul.SesjonFeil as f:
        conn.rollback()
        tjeneste.logg.hendelse("tidsvalg_rate", rid, art="sikkerhet")
        raise _Avbrudd(_svar({"feil": "rate_grense"}, rid, 429)) from f


def _mac(pepper: str, secret: str) -> str:
    """modulonboarding._mac-formen (app._mac): pepperet bor i
    API-prosessen, aldri i basen."""
    return hmac.new(pepper.encode("utf-8"), secret.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def oppslag_endepunkt(tjeneste, request):
    """POST /v1/tidsvalg/oppslag {token} -> kandidatens eget valg +
    slots med binært ledig/fullt. All dømming bor i den
    authenticator-eide defineren; en tom retur er den uniforme
    avvisningen."""
    from .app import _rid
    from .policyadmin_http import _med_conn
    rid = _rid(request)

    def kjor(conn):
        _krev_json(request, rid)
        token_id, secret, _ = _token_fra_kropp(request, rid)
        _rate(tjeneste, conn, request, rid, token_id)
        if token_id is None:
            return _avvist(rid)
        rader = conn.execute(
            "SELECT * FROM m8_tidsvalg_oppslag(%s,%s)",
            (token_id, _mac(tjeneste.pepper, secret))).fetchall()
        conn.rollback()
        if not rader:
            return _avvist(rid)
        valgt = rader[0][0]
        slots = [{"slot_id": str(sid), "start": start.isoformat(),
                  "slutt": slutt.isoformat(), "ledig": ledig}
                 for _v, sid, start, slutt, ledig in rader
                 if sid is not None]
        return _svar({"valgt_slot": str(valgt) if valgt else None,
                      "slots": slots}, rid)

    return _med_conn(tjeneste, rid, kjor)


def velg_endepunkt(tjeneste, request):
    """POST /v1/tidsvalg/velg {token, slot_id} -> bekreftelse.

    Radlåsen i `m8_velg_slot` serialiserer kapasiteten; valget og
    token->brukt committes i SAMME transaksjon (DOM 3: valget er
    endelig — gjenspill med samme slot er et stille ja, annen slot
    avvises som `valg_alt_registrert`)."""
    from .app import _rid
    from .policyadmin_http import _Avbrudd, _med_conn
    rid = _rid(request)

    def kjor(conn):
        _krev_json(request, rid)
        token_id, secret, kropp = _token_fra_kropp(request, rid)
        _rate(tjeneste, conn, request, rid, token_id)
        if token_id is None:
            return _avvist(rid)
        try:
            slot_id = uuidlib.UUID(str(kropp.get("slot_id")))
        except (ValueError, TypeError):
            raise _Avbrudd(_svar({"feil": "request_feilformet"}, rid, 400))
        rad = conn.execute(
            "SELECT * FROM m8_velg_slot(%s,%s,%s)",
            (token_id, _mac(tjeneste.pepper, secret), slot_id)).fetchone()
        if rad is None:
            conn.rollback()
            return _avvist(rid)
        utfall, start, slutt = rad
        if utfall == "valgt":
            conn.commit()
            tjeneste.logg.hendelse("tidsvalg_valgt", rid, art="drift")
            return _svar({"valgt": True, "start": start.isoformat(),
                          "slutt": slutt.isoformat()}, rid)
        conn.rollback()
        # De to skillbare utfallene — begge krever et gyldig token.
        return _svar({"feil": utfall}, rid, 409)

    return _med_conn(tjeneste, rid, kjor)
