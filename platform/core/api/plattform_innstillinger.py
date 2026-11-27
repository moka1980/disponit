"""Plattformens innstillinger — prøveperioden og bransjemalene (236).

Eiers ordre 27/11: «alt må betjenes via dashboard både for kunden og admin
panel, ikke gjennom koden». `PROVE_DOGN = 30` og listen over bransjemaler sto
i `firmaregistrering.py`; nå står de i én rad som plattformeieren endrer i
plattformadmin.

TRE VEIER:

* OFFENTLIG: `GET /v1/registrering/valg` — hvor lang prøveperioden er og
  hvilke bransjer en ny kunde kan velge. Forsiden («Prøv gratis i N dager»)
  og registreringsskjemaet leser dette, også før noen er logget inn.
* PLATTFORMEIEREN: `GET`/`POST /v1/plattform/innstillinger`. Fullmakten er
  raden i `plattformeier` (199), slått opp av skrivedøra selv.
* REGISTRERINGEN (`firmaregistrering.py`): `les()`.
"""
from __future__ import annotations

#: Malene som følger med appen — samme rekkefølge som i skjemaet. Hvilke av
#: dem som TILBYS, er eierens valg (236); selve malene er policyfiler.
ALLE_BRANSJER = ("tjenestebedrift", "handverk-bygg", "netthandel")
#: Brukes bare hvis raden mangler (en base før 236). Da oppfører
#: registreringen seg som før.
STANDARD = {"prove_dogn": 30, "bransjer": list(ALLE_BRANSJER),
            "endret": None}
MIN_DOGN, MAKS_DOGN = 1, 365


def les(conn) -> dict:
    r = conn.execute("SELECT prove_dogn, bransjer, endret"
                     " FROM plattform_innstillinger_les()").fetchone()
    if r is None:
        return dict(STANDARD)
    # I malenes rekkefølge, ikke i rekkefølgen de ble krysset av.
    valgt = set(r[1] or [])
    return {"prove_dogn": int(r[0]),
            "bransjer": [b for b in ALLE_BRANSJER if b in valgt],
            "endret": r[2].isoformat() if r[2] else None}


def valg_endepunkt(tjeneste, request):
    """GET /v1/registrering/valg — offentlig."""
    from .juridisk import _offentlig

    def fn(conn):
        d = les(conn)
        return 200, {"prove_dogn": d["prove_dogn"],
                     "bransjer": d["bransjer"]}
    return _offentlig(tjeneste, request, fn)


def eier_endepunkt(tjeneste, request):
    """GET /v1/plattform/innstillinger — skjemaets innhold."""
    from .app import _rid, kanonisk_json
    from .policyadmin_http import _leseauth, _med_conn
    rid = _rid(request)

    def kjor(conn):
        _leseauth(tjeneste, request, conn, rid)
        return kanonisk_json({"innstillinger": les(conn),
                              "alle_bransjer": list(ALLE_BRANSJER),
                              "grenser": {"prove_dogn": [MIN_DOGN, MAKS_DOGN]},
                              "request_id": rid}, 200,
                             {"x-request-id": rid})
    return _med_conn(tjeneste, rid, kjor)


def sett_endepunkt(tjeneste, request):
    """POST /v1/plattform/innstillinger — {prove_dogn, bransjer}.

    Formen kontrolleres HER for å gi et tydelig felt tilbake; basen har de
    samme CHECKene (236) og er den som til slutt sier nei."""
    from .app import _rid
    from .plattform_http import _bid
    from .policyadmin_http import _feil, _kropp, _med_conn, _ok_lagret
    rid = _rid(request)

    def kjor(conn):
        import psycopg

        _tenant, bid = _bid(tjeneste, request, conn, rid)
        k = _kropp(request)
        dogn = k.get("prove_dogn")
        # `bool` er en `int` i Python — `true` er ikke én dag.
        if isinstance(dogn, bool) or not isinstance(dogn, int) \
                or not MIN_DOGN <= dogn <= MAKS_DOGN:
            return _feil("request_feilformet", rid, 400, detalj="prove_dogn")
        bransjer = k.get("bransjer")
        if not isinstance(bransjer, list) or not bransjer or not all(
                isinstance(b, str) and b in ALLE_BRANSJER for b in bransjer):
            return _feil("request_feilformet", rid, 400, detalj="bransjer")
        bransjer = [b for b in ALLE_BRANSJER if b in set(bransjer)]
        try:
            with conn.transaction():
                conn.execute(
                    "SELECT plattform_innstillinger_sett(%s,%s,%s)",
                    (bid, dogn, bransjer))
        except psycopg.errors.InsufficientPrivilege:
            return _feil("ikke_plattformeier", rid, 403)
        except psycopg.errors.CheckViolation:
            return _feil("request_feilformet", rid, 400)
        return _ok_lagret(conn, {"innstillinger": les(conn)}, rid)
    return _med_conn(tjeneste, rid, kjor)
