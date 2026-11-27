"""Vilkår, personvern og databehandleravtale — redigert i dashbordet (235).

Eiers ordre 27/11: «alt må betjenes via dashboard både for kunden og admin
panel, ikke gjennom koden». Tekstene ble flyttet fra `docs/juridisk/` til
basen; plattformeieren redigerer og publiserer dem selv.

TRE VEIER:

* OFFENTLIG (ingen innlogging): `GET /v1/juridisk/{dok}` — siste
  publiserte versjon, med leverandørens opplysninger (234) fylt inn. Det er
  det sidene `/vilkar`, `/personvern` og `/databehandleravtale` viser, og
  det kunden godtar. `GET /v1/juridisk` — hvilke versjoner som gjelder nå
  (registreringen spør om dette).
* PLATTFORMEIEREN: `GET /v1/plattform/juridisk`, `POST …/{dok}/utkast`,
  `POST …/{dok}/publiser`. Fullmakten er raden i `plattformeier` (199),
  slått opp av dørene selv.
* REGISTRERINGEN (`firmaregistrering.py`): `krav()` og `godta()`.

FELTENE: `{{navn}}`, `{{orgnummer}}`, `{{adresse}}`, `{{kontakt_epost}}`,
`{{verneting}}`. Fylles inn ved LESING, ikke lagret i teksten — endrer
eieren adressen i «Om Disponit», står den nye på sidene med én gang, uten
en ny versjon. (Den publiserte TEKSTEN er det samtykket gjelder; adressen
er en opplysning om hvem man har avtalen med.)
"""
from __future__ import annotations

import re

DOKUMENTER = ("vilkar", "personvern", "databehandleravtale")
#: Det en ny kunde må godta ved registrering — når de er publisert.
KREVES_VED_REGISTRERING = ("vilkar", "databehandleravtale")
MAKS_TEKST = 200_000

_FELT = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
_UTFYLT = {"orgnummer": "(under registrering)"}


def fyll_inn(tekst: str, lev: dict | None) -> str:
    """`{{felt}}` → leverandørens verdi. Et felt uten verdi får en tydelig
    plassholder, aldri en tom streng midt i en setning."""
    lev = lev or {}

    def bytt(m):
        felt = m.group(1)
        v = lev.get(felt)
        if v:
            return str(v)
        return _UTFYLT.get(felt, "[ikke fylt inn]")
    return _FELT.sub(bytt, tekst)


def _leverandor(conn):
    from .plattform_http import leverandor_for
    try:
        with conn.transaction():
            return leverandor_for(conn)
    except Exception:                                       # noqa: BLE001
        return None


def publisert(conn, dok: str) -> dict | None:
    r = conn.execute("SELECT versjon, tekst, publisert_ts"
                     " FROM juridisk_publisert(%s)", (dok,)).fetchone()
    if r is None:
        return None
    return {"dok": dok, "versjon": r[0],
            "tekst": fyll_inn(r[1], _leverandor(conn)),
            "publisert": r[2].isoformat()}


def krav(conn) -> dict:
    """-> {dok: versjon} for det registreringen krever NÅ. Tomt før noe
    er publisert — da krever registreringen ingenting."""
    ut = {}
    for dok in KREVES_VED_REGISTRERING:
        r = conn.execute("SELECT versjon FROM juridisk_publisert(%s)",
                         (dok,)).fetchone()
        if r is not None:
            ut[dok] = r[0]
    return ut


def godta(conn, tenant: str, versjoner: dict, aktor: str) -> None:
    """Samtykket, i registreringens transaksjon, med konteksten på det
    NYE firmaet (døra binder `p_tenant` til den)."""
    for dok, versjon in versjoner.items():
        conn.execute("SELECT juridisk_godta(%s,%s,%s,%s)",
                     (tenant, dok, versjon, aktor))


# ---------------------------------------------------------------------------
# Offentlig
# ---------------------------------------------------------------------------

def _offentlig(tjeneste, request, fn):
    """Lesing UTEN innlogging: egen forbindelse, ingen tenantkontekst,
    alltid tilbakerullet. Dørene som kalles er SECURITY DEFINER og leser
    bare publisert innhold."""
    import psycopg

    from .app import _rid, kanonisk_json
    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        from .lesing import _feilsvar
        return _feilsvar("db_utilgjengelig", rid)
    try:
        status, svar = fn(conn)
        svar["request_id"] = rid
        return kanonisk_json(svar, status, {
            "x-request-id": rid, "cache-control": "public, max-age=60"})
    finally:
        try:
            conn.rollback()
        except psycopg.Error:
            pass
        tjeneste.pool.gi_tilbake(conn)


def offentlig_endepunkt(tjeneste, request):
    """GET /v1/juridisk/{dok} — siste publiserte versjon, utfylt."""
    dok = request.path_params.get("dok", "")

    def fn(conn):
        if dok not in DOKUMENTER:
            return 404, {"feil": "ikke_funnet"}
        d = publisert(conn, dok)
        if d is None:
            return 200, {"dok": dok, "versjon": None, "tekst": None,
                         "publisert": None}
        return 200, d
    return _offentlig(tjeneste, request, fn)


def gjeldende_endepunkt(tjeneste, request):
    """GET /v1/juridisk — {dok: versjon} som gjelder nå, og hva
    registreringen krever."""
    def fn(conn):
        alle = {}
        for dok in DOKUMENTER:
            r = conn.execute("SELECT versjon FROM juridisk_publisert(%s)",
                             (dok,)).fetchone()
            alle[dok] = r[0] if r else None
        return 200, {"gjeldende": alle, "kreves": krav(conn)}
    return _offentlig(tjeneste, request, fn)


# ---------------------------------------------------------------------------
# Plattformeieren
# ---------------------------------------------------------------------------

def eier_liste_endepunkt(tjeneste, request):
    """GET /v1/plattform/juridisk — per dokument: gjeldende og utkast."""
    from .app import _rid, kanonisk_json
    from .policyadmin_http import _feil, _leseauth, _med_conn
    rid = _rid(request)

    def kjor(conn):
        import psycopg
        _tenant, bid = _leseauth(tjeneste, request, conn, rid)
        try:
            rader = conn.execute(
                "SELECT dok, versjon, tekst, opprettet, publisert_ts,"
                " antall_samtykker FROM juridisk_for_eier(%s)",
                (bid,)).fetchall()
        except psycopg.errors.InsufficientPrivilege:
            return _feil("ikke_plattformeier", rid, 403)
        dok = {d: {"dok": d, "gjeldende": None, "utkast": None}
               for d in DOKUMENTER}
        for d, v, tekst, opprettet, pub, antall in rader:
            rad = {"versjon": v, "tekst": tekst,
                   "opprettet": opprettet.isoformat(),
                   "publisert": pub.isoformat() if pub else None,
                   "samtykker": int(antall)}
            dok[d]["gjeldende" if pub else "utkast"] = rad
        return kanonisk_json({"dokumenter": [dok[d] for d in DOKUMENTER],
                              "felt": ["navn", "orgnummer", "adresse",
                                       "kontakt_epost", "verneting"],
                              "request_id": rid}, 200,
                             {"x-request-id": rid})
    return _med_conn(tjeneste, rid, kjor)


def _eier_skriv(tjeneste, request, sql_fn):
    from .app import _rid
    from .plattform_http import _bid
    from .policyadmin_http import _feil, _med_conn, _ok_lagret
    rid = _rid(request)
    dok = request.path_params.get("dok", "")

    def kjor(conn):
        import psycopg
        _tenant, bid = _bid(tjeneste, request, conn, rid)
        if dok not in DOKUMENTER:
            return _feil("ikke_funnet", rid, 404)
        try:
            with conn.transaction():
                svar = sql_fn(conn, bid, dok)
        except psycopg.errors.InsufficientPrivilege:
            return _feil("ikke_plattformeier", rid, 403)
        except psycopg.errors.NoDataFound:
            return _feil("ikke_funnet", rid, 404, detalj="ingen utkast")
        except psycopg.errors.CheckViolation:
            return _feil("request_feilformet", rid, 400)
        if isinstance(svar, tuple):
            return _feil(svar[0], rid, 400, detalj=svar[1])
        return _ok_lagret(conn, svar, rid)
    return _med_conn(tjeneste, rid, kjor)


def utkast_endepunkt(tjeneste, request):
    """POST /v1/plattform/juridisk/{dok}/utkast — {tekst}."""
    from .policyadmin_http import _kropp

    def sql(conn, bid, dok):
        tekst = _kropp(request).get("tekst")
        if not isinstance(tekst, str) or not tekst.strip() \
                or len(tekst) > MAKS_TEKST:
            return ("request_feilformet", "tekst")
        v = conn.execute("SELECT juridisk_lagre_utkast(%s,%s,%s)",
                         (bid, dok, tekst)).fetchone()[0]
        return {"dok": dok, "versjon": v}
    return _eier_skriv(tjeneste, request, sql)


def publiser_endepunkt(tjeneste, request):
    """POST /v1/plattform/juridisk/{dok}/publiser — utkastet blir
    gjeldende. Enveis: en publisert versjon endres aldri (235)."""
    def sql(conn, bid, dok):
        v = conn.execute("SELECT juridisk_publiser(%s,%s)",
                         (bid, dok)).fetchone()[0]
        return {"dok": dok, "versjon": v}
    return _eier_skriv(tjeneste, request, sql)
