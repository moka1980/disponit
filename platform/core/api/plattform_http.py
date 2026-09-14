"""Plattformeierens flate over HTTP (199).

Eier, ordrett: «Jeg er allerede admin/root/eier som eliassi@gmail.com og skal
også ha mulighet til å opprette nye firmaer, redigere og slette. Andre
kunder/firma kan registrere seg og ha prøve tid i 30 dager.»

FEM RUTER, OG ALLE HVILER PÅ DØRA — IKKE PÅ SCOPET:

* GET  /v1/plattform/meg            — «er JEG plattformeier?»
* GET  /v1/plattform/firmaer        — alle firmaer, på tvers av tenanter
* POST /v1/plattform/firmaer        — opprett
* POST /v1/plattform/firmaer/{t}/oppdater
* POST /v1/plattform/firmaer/{t}/status

HVORFOR `policy:read` OG IKKE ET NYTT SCOPE
Autoriteten er en rad i `plattformeier`, og dørene i 199 slår den opp selv —
de nekter med `insufficient_privilege` uansett hva scopet sier. Scopet er
bare det `_autentiser` krever for å slippe forbi den generelle porten; den er
bygget for ett påkrevd scope og avviser `None`.

Det er samme situasjon som `/v1/invitasjoner/innloes`, og valget er tatt av
samme grunn: et nytt scope måtte inn hos ALLE roller, i rolleguiden og i en
migrasjon — et betydelig apparat for å uttrykke noe døra allerede håndhever
strengere. `policy:read` er dessuten et scope plattformeieren HAR (hun er
admin i sitt eget firma), og det står i `LESESCOPES`, så browsersesjonen
slipper forbi uten at scopet må inn i `BROWSER_MUTASJONSSCOPES`.

SKULLE VI LIKEVEL VILLE HA ET EGET SCOPE en dag, er det en ren utvidelse:
døra endres ikke, bare porten foran den.

TENANTEN I STIEN AUTORISERER INGENTING. Dørene binder `p_bruker_id` til
kallerens aktørkontekst, og aktøren kommer fra ØKTEN — aldri fra stien eller
kroppen. En plattformeier kan derfor røre hvilket firma som helst; en som
ikke er det, kan ikke røre sitt eget engang gjennom disse rutene.
"""
from __future__ import annotations

MAKS_NAVN = 200
MAKS_TENANT = 63
SCOPE = "policy:read"


def _bid(tjeneste, request, conn, rid):
    from .policyadmin_http import _browserkontekst
    return _browserkontekst(tjeneste, request, conn, rid, SCOPE)


def meg_endepunkt(tjeneste, request):
    """GET /v1/plattform/meg — «er JEG plattformeier?»

    Flaten trenger svaret for å vite om menyvalget skal finnes. Døra svarer
    bare om KALLEREN selv (199), så dette er ikke et oppslagsverk over hvem
    som er eier.
    """
    from .app import _rid, kanonisk_json
    from .policyadmin_http import _leseauth, _med_conn
    rid = _rid(request)

    def kjor(conn):
        _tenant, bid = _leseauth(tjeneste, request, conn, rid)
        eier = conn.execute("SELECT plattform_er_eier(%s)",
                            (bid,)).fetchone()[0]
        return kanonisk_json({"eier": bool(eier), "request_id": rid}, 200,
                             {"x-request-id": rid})

    return _med_conn(tjeneste, rid, kjor)


def firmaer_endepunkt(tjeneste, request):
    """GET /v1/plattform/firmaer — alle firmaer, på tvers av tenanter."""
    from .app import _rid, kanonisk_json
    from .policyadmin_http import _leseauth, _med_conn
    rid = _rid(request)

    def kjor(conn):
        import psycopg

        _tenant, bid = _leseauth(tjeneste, request, conn, rid)
        try:
            rader = conn.execute(
                "SELECT tenant, navn, orgnummer, status, prove_utloper,"
                " stengt_ts, opprettet, endret"
                " FROM plattform_firmaliste(%s)", (bid,)).fetchall()
        except psycopg.errors.InsufficientPrivilege:
            # Døras egen dom. ÉN KODE: å skille «ikke eier» fra «finnes
            # ikke» ville latt noen kartlegge hvem som er plattformeier.
            from .policyadmin_http import _feil
            return _feil("ikke_plattformeier", rid, 403)
        ut = [{"tenant": r[0], "navn": r[1], "orgnummer": r[2],
               "status": r[3],
               "prove_utloper": r[4].isoformat() if r[4] else None,
               "stengt": r[5].isoformat() if r[5] else None,
               "opprettet": r[6].isoformat(), "endret": r[7].isoformat()}
              for r in rader]
        return kanonisk_json({"firmaer": ut, "request_id": rid}, 200,
                             {"x-request-id": rid})

    return _med_conn(tjeneste, rid, kjor)


def _tekst(k, felt, maks, *, paakrevd=True):
    """Returnerer (verdi, feilfelt). Tom streng blir None for valgfrie."""
    v = k.get(felt)
    if v is None and not paakrevd:
        return None, None
    if not isinstance(v, str):
        return None, felt
    v = v.strip()
    if not v:
        return (None, None) if not paakrevd else (None, felt)
    if len(v) > maks:
        return None, felt
    return v, None


def opprett_endepunkt(tjeneste, request):
    """POST /v1/plattform/firmaer — opprett et firma på vegne av kunden.

    Prøveperioden er eiers regel: «Andre kunder/firma kan registrere seg og
    ha prøve tid i 30 dager.» Standarden er derfor 30, men eier kan sette en
    annen — 190s dør håndhever 1-365.
    """
    from .app import _rid
    from .policyadmin_http import (_feil, _kropp, _med_conn, _ok_lagret)
    rid = _rid(request)

    def kjor(conn):
        import psycopg

        _tenant, bid = _bid(tjeneste, request, conn, rid)
        k = _kropp(request)
        tenant, feil = _tekst(k, "tenant", MAKS_TENANT)
        if feil:
            return _feil("request_feilformet", rid, 400, detalj=feil)
        navn, feil = _tekst(k, "navn", MAKS_NAVN)
        if feil:
            return _feil("request_feilformet", rid, 400, detalj=feil)
        orgnummer, feil = _tekst(k, "orgnummer", 32, paakrevd=False)
        if feil:
            return _feil("request_feilformet", rid, 400, detalj=feil)
        dogn = k.get("prove_dogn", 30)
        if isinstance(dogn, bool) or not isinstance(dogn, int):
            return _feil("request_feilformet", rid, 400, detalj="prove_dogn")

        try:
            with conn.transaction():
                frist = conn.execute(
                    "SELECT plattform_firma_opprett(%s,%s,%s,%s,%s,%s)",
                    (bid, tenant, navn, orgnummer, dogn,
                     f"bruker:{bid}")).fetchone()[0]
        except psycopg.errors.InsufficientPrivilege:
            return _feil("ikke_plattformeier", rid, 403)
        except psycopg.errors.UniqueViolation:
            # 190: «registrering er ikke en oppdatering» — finnes firmaet, er
            # et nytt kall enten en feil eller et forsøk på å overta en rad.
            return _feil("firma_finnes", rid, 409)
        except psycopg.errors.InvalidParameterValue:
            return _feil("request_feilformet", rid, 400)
        except psycopg.errors.CheckViolation:
            # Tenantformen (`^[a-z0-9][a-z0-9-]{1,62}$`) og orgnummer-
            # kontrollen er CHECKer i 190 — døras dom, ikke vår kopi.
            return _feil("request_feilformet", rid, 400)
        return _ok_lagret(conn, {"tenant": tenant,
                                 "prove_utloper": frist.isoformat()}, rid, 201)

    return _med_conn(tjeneste, rid, kjor)


def oppdater_endepunkt(tjeneste, request):
    """POST /v1/plattform/firmaer/{tenant}/oppdater — navn og orgnummer."""
    from .app import _rid
    from .policyadmin_http import (_feil, _kropp, _med_conn, _ok_lagret)
    rid = _rid(request)
    maal = request.path_params.get("tenant", "")

    def kjor(conn):
        import psycopg

        _tenant, bid = _bid(tjeneste, request, conn, rid)
        k = _kropp(request)
        navn, feil = _tekst(k, "navn", MAKS_NAVN)
        if feil:
            return _feil("request_feilformet", rid, 400, detalj=feil)
        orgnummer, feil = _tekst(k, "orgnummer", 32, paakrevd=False)
        if feil:
            return _feil("request_feilformet", rid, 400, detalj=feil)
        try:
            with conn.transaction():
                conn.execute("SELECT plattform_firma_oppdater(%s,%s,%s,%s,%s)",
                             (bid, maal, navn, orgnummer, f"bruker:{bid}"))
        except psycopg.errors.InsufficientPrivilege:
            return _feil("ikke_plattformeier", rid, 403)
        except psycopg.errors.ForeignKeyViolation:
            return _feil("firma_ukjent", rid, 404)
        except psycopg.errors.InvalidParameterValue:
            return _feil("request_feilformet", rid, 400)
        return _ok_lagret(conn, {"tenant": maal}, rid)

    return _med_conn(tjeneste, rid, kjor)


def status_endepunkt(tjeneste, request):
    """POST /v1/plattform/firmaer/{tenant}/status — livssyklusen.

    «SLETT» ER `stengt`, IKKE EN SLETTING. 190 eier angrefristen og reaperen;
    en DELETE her ville omgått begge, og et firma som stenges ved en feil
    ville vært uopprettelig. Lovlige overganger står i 190s ENE tabell, og
    døra håndhever dem — denne ruten kopierer dem ikke.
    """
    from .app import _rid
    from .policyadmin_http import (_feil, _kropp, _med_conn, _ok_lagret)
    rid = _rid(request)
    maal = request.path_params.get("tenant", "")

    def kjor(conn):
        import psycopg

        _tenant, bid = _bid(tjeneste, request, conn, rid)
        k = _kropp(request)
        status, feil = _tekst(k, "status", 16)
        if feil:
            return _feil("request_feilformet", rid, 400, detalj="status")
        try:
            with conn.transaction():
                conn.execute("SELECT plattform_firma_status(%s,%s,%s,%s)",
                             (bid, maal, status, f"bruker:{bid}"))
        except psycopg.errors.InsufficientPrivilege:
            return _feil("ikke_plattformeier", rid, 403)
        except psycopg.errors.ForeignKeyViolation:
            return _feil("firma_ukjent", rid, 404)
        except psycopg.errors.IntegrityConstraintViolation:
            # Ulovlig overgang, eller angrefristen er ute. 190s dom.
            return _feil("overgang_ulovlig", rid, 409)
        except psycopg.errors.InvalidParameterValue:
            return _feil("request_feilformet", rid, 400)
        return _ok_lagret(conn, {"tenant": maal, "status": status}, rid)

    return _med_conn(tjeneste, rid, kjor)
