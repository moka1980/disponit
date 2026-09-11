"""Partsregisteret over HTTP (183/184): ÉT sted å legge inn kundene.

EIERS ORD 11/9: «det skal være en enkel plass der firmaene enten fyller
ut et enkelt skjema om seg selv og legge til deres kunder … Ikke gå
gjennom hver modul og fylle.»

Fire ruter:

* GET  /v1/parter?sok=&grense=      (`part:read`)        — lista.
* POST /v1/parter                   (`part:administrer`) — én kunde,
  idempotent på `part_ref`: samme referanse er samme kunde, og navnet
  kan rettes uten å føde en tvilling.
* POST /v1/parter/{part_id}/kontakt (`part:administrer`) — et
  kontaktpunkt. KLARTEKSTEN FORLATER ALDRI DETTE LAGET: adressen
  krypteres her med tenantens DEK og pseudonymiseres av basen, og døra
  ser bare ciphertext, maske og pseudonym.
* POST /v1/parter/{part_id}/deaktiver (`part:administrer`) — enveis, og
  det er den som starter retensjonsklokken (184).

TO SCOPE, IKKE ETT, og grunnen er den samme som CodeRabbit fant i M-6:
å SE kundelisten og å ENDRE den er to ting, og de bør kunne gis hver for
seg. Enhver leser trenger den første.

INGEN SLETTEVEI. En kunde avvikles, og adressen dør av seg selv når
fristen er ute (184). En knapp som slettet raden ville tatt sporet med
seg — og fordringer, tilbud og prosjekter peker hit.
"""
from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

MAKS_PARTER = 500
MAKS_NAVN = 200
MAKS_REF = 100
MAKS_VERDI = 320          # RFC 5321s lengste adresse
KANALER = ("epost", "telefon")
PARTTYPER = ("bedrift", "person")


def _maske(verdi: str, kanal: str) -> str:
    """Det flaten får VISE. Aldri hele verdien — masken er det eneste
    som forlater registeret uten et eget dekrypteringssteg.

    MINST ETT TEGN SKJULES, ALLTID (CodeRabbit). Første utgave viste
    HELE verdien for korte input: et firesifret internnummer ble
    `*1234`, og et enbokstavs lokalnavn ble `a*@x.no`. En maske som kan
    leses baklengs er ingen maske — og masken er nettopp det vi lover at
    er trygt å vise.
    """
    v = verdi.strip()
    if kanal == "epost" and "@" in v:
        lokal, _, domene = v.partition("@")
        synlig_antall = min(2, max(len(lokal) - 1, 0))
        synlig = lokal[:synlig_antall]
        return f"{synlig}{'*' * (len(lokal) - synlig_antall)}@{domene}"
    synlig_antall = min(4, max(len(v) - 1, 0))
    synlig = v[-synlig_antall:] if synlig_antall else ""
    return f"{'*' * (len(v) - synlig_antall)}{synlig}"


def _rad(r) -> dict:
    return {"part_id": str(r[0]), "part_ref": r[1], "navn": r[2],
            "orgnummer": r[3], "parttype": r[4], "aktiv": bool(r[5]),
            "epost_maske": r[6], "telefon_maske": r[7],
            "antall_kontakter": int(r[8])}



def _krev_csrf(tjeneste, request: Request, conn, rid, auth):
    """CSRF-vakten for browsersesjoner. None = i orden, ellers svaret.

    HULLET DETTE LUKKER, funnet ved å kjøre: `part:administrer` ble lagt
    i `BROWSER_MUTASJONSSCOPES`, og den carve-outen slipper scopet forbi
    den generelle «en browsersesjon muterer aldri»-porten. Da står
    DOBBEL-INNSENDINGEN igjen som eneste vern — og den håndheves i
    ENDEPUNKTET, ikke i carve-outen. Uten disse linjene kunne en fremmed
    side skrive i kunderegisteret med brukerens egen øktkake.

    MASKINVEIEN ER UBERØRT (policyadmin_http-formen, ordrett): et
    Bearer-token i en header kan ikke sendes av en fremmed side, så
    kravet er meningsløst der og ble i praksis et forbud mot
    integrasjoner.
    """
    from api import sesjon as sesjonmodul

    from .policyadmin_http import _feil
    kake = request.cookies.get(sesjonmodul.C_SESJON)
    token_id = getattr(auth, "token_id", "") or ""
    if kake is None and token_id and not token_id.startswith("sesjon:"):
        return None
    rad = conn.execute("SELECT csrf_hash FROM slaa_opp_sesjon(%s)",
                       (sesjonmodul._hash(kake),)).fetchone() if kake else None
    conn.commit()
    if rad is None or not sesjonmodul.csrf_matcher(rad[0], request):
        tjeneste.logg.hendelse("csrf_ugyldig", rid)
        return _feil("csrf_ugyldig", rid)
    return None

def liste_endepunkt(tjeneste, request: Request) -> Response:
    """GET /v1/parter (part:read): kundene, med masker og aldri adresser."""
    from .app import _rid
    from .policyadmin_http import _feil, _med_conn, _ok
    rid = _rid(request)

    def kjor(conn):
        from db.pg import sett_kontekst

        from . import kjerne
        from .app import _autentiser
        try:
            auth = _autentiser(tjeneste, request, conn, rid, "part:read")
        except kjerne.Feilsvar as f:
            return _feil(f.kode, rid)
        sok = request.query_params.get("sok")
        if sok is not None and len(sok) > MAKS_NAVN:
            return _feil("request_feilformet", rid, 400, detalj="sok er lang")
        try:
            grense = min(int(request.query_params.get("grense",
                                                      MAKS_PARTER)),
                         MAKS_PARTER)
        except ValueError:
            return _feil("request_feilformet", rid, 400, detalj="grense")
        if grense < 1:
            return _feil("request_feilformet", rid, 400, detalj="grense")
        conn.rollback()
        sett_kontekst(conn, auth.tenant, auth.aktor, rid)
        # ÉN RAD EKSTRA, og den vises aldri (CodeRabbit): `len(rader) >=
        # grense` var sant også når registeret hadde NØYAKTIG så mange
        # kunder, og flaten sa «lista er avkortet» om en komplett liste.
        # Den ekstra raden er svaret på «finnes det mer?», ikke data.
        rader = conn.execute(
            "SELECT part_id, part_ref, navn, orgnummer, parttype, aktiv,"
            " epost_maske, telefon_maske, antall_kontakter"
            " FROM part_liste(%s,%s,%s)",
            (auth.tenant, sok, grense + 1)).fetchall()
        avkortet = len(rader) > grense
        return _ok({"parter": [_rad(r) for r in rader[:grense]],
                    "avkortet": avkortet}, rid)

    return _med_conn(tjeneste, rid, kjor)


def registrer_endepunkt(tjeneste, request: Request) -> Response:
    """POST /v1/parter (part:administrer, idem): én kunde.

    IDEMPOTENT PÅ REFERANSEN, fordi det er det importen (PR 4) kommer til
    å be om tusen ganger: samme `part_ref` er samme kunde.
    """
    from db.pg import sett_kontekst

    from .app import _rid
    from .policyadmin_http import _feil, _kropp, _med_conn, _ok_lagret
    rid = _rid(request)

    def kjor(conn):
        import psycopg

        from . import kjerne
        from .app import _autentiser
        try:
            auth = _autentiser(tjeneste, request, conn, rid,
                               "part:administrer")
        except kjerne.Feilsvar as f:
            return _feil(f.kode, rid)
        avbrudd = _krev_csrf(tjeneste, request, conn, rid, auth)
        if avbrudd is not None:
            return avbrudd
        k = _kropp(request)
        ref = k.get("part_ref")
        navn = k.get("navn")
        for verdi, felt, maks in ((ref, "part_ref", MAKS_REF),
                                  (navn, "navn", MAKS_NAVN)):
            if not isinstance(verdi, str) or not verdi.strip():
                return _feil("request_feilformet", rid, 400, detalj=felt)
            if len(verdi) > maks:
                return _feil("request_feilformet", rid, 400,
                             detalj=f"{felt} er lang")
        org = k.get("orgnummer")
        if org is not None:
            if not isinstance(org, str):
                return _feil("request_feilformet", rid, 400,
                             detalj="orgnummer")
            org = org.replace(" ", "")
            if org and (len(org) != 9 or not org.isdigit()):
                return _feil("request_feilformet", rid, 400,
                             detalj="orgnummer er ni siffer")
            org = org or None
        parttype = k.get("parttype", "bedrift")
        if parttype not in PARTTYPER:
            return _feil("request_feilformet", rid, 400, detalj="parttype")
        conn.rollback()
        sett_kontekst(conn, auth.tenant, auth.aktor, rid)
        try:
            with conn.transaction():
                pid = conn.execute(
                    "SELECT part_registrer(%s,%s,%s,%s,%s,%s)",
                    (auth.tenant, ref.strip(), navn.strip(), org, parttype,
                     auth.aktor)).fetchone()[0]
        except psycopg.errors.IntegrityConstraintViolation:
            # Dørens egen dom (form eller vakt). Alt annet er drift og
            # bobler videre — lærdommen fra #480/#483.
            return _feil("part_ulovlig_tilstand", rid, 409)
        return _ok_lagret(conn, {"part_id": str(pid), "part_ref": ref.strip()},
                          rid)

    return _med_conn(tjeneste, rid, kjor)


def kontakt_endepunkt(tjeneste, request: Request) -> Response:
    """POST /v1/parter/{part_id}/kontakt (part:administrer, idem).

    KLARTEKSTEN STOPPER HER. Adressen krypteres med tenantens DEK og
    pseudonymiseres av basen; døra får ciphertext, maske og pseudonym —
    aldri verdien. Samme adresse to ganger er ett kontaktpunkt.
    """
    from db import kryptering
    from db.pg import sett_kontekst

    from .app import _rid
    from .policyadmin_http import _feil, _kropp, _med_conn, _ok_lagret
    rid = _rid(request)
    pid = request.path_params["part_id"]

    def kjor(conn):
        import psycopg

        from . import kjerne
        from .app import _autentiser
        try:
            auth = _autentiser(tjeneste, request, conn, rid,
                               "part:administrer")
        except kjerne.Feilsvar as f:
            return _feil(f.kode, rid)
        avbrudd = _krev_csrf(tjeneste, request, conn, rid, auth)
        if avbrudd is not None:
            return avbrudd
        k = _kropp(request)
        kanal = k.get("kanal")
        verdi = k.get("verdi")
        if kanal not in KANALER:
            return _feil("request_feilformet", rid, 400, detalj="kanal")
        if not isinstance(verdi, str) or not verdi.strip():
            return _feil("request_feilformet", rid, 400, detalj="verdi")
        if len(verdi) > MAKS_VERDI:
            return _feil("request_feilformet", rid, 400,
                         detalj="verdi er lang")
        if kanal == "epost" and ("@" not in verdi or verdi.strip().endswith("@")):
            return _feil("request_feilformet", rid, 400,
                         detalj="verdi er ikke en adresse")
        merkelapp = k.get("merkelapp")
        if merkelapp is not None and (not isinstance(merkelapp, str)
                                      or len(merkelapp) > MAKS_NAVN):
            return _feil("request_feilformet", rid, 400, detalj="merkelapp")
        conn.rollback()
        sett_kontekst(conn, auth.tenant, auth.aktor, rid)
        key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, auth.tenant)
        ct, nonce = kryptering.krypter(dek, {"verdi": verdi.strip()},
                                       auth.tenant, key_id)
        try:
            with conn.transaction():
                psn = conn.execute("SELECT tenant_pseudonym(%s,%s)",
                                   (auth.tenant, verdi.strip())).fetchone()[0]
                kid = conn.execute(
                    "SELECT part_sett_kontakt(%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                    "%s,%s)",
                    (auth.tenant, pid, kanal, _maske(verdi, kanal), ct,
                     nonce, key_id, psn, bool(k.get("primar", True)),
                     (merkelapp or None), auth.aktor)).fetchone()[0]
        except psycopg.errors.ForeignKeyViolation:
            return _feil("ikke_funnet", rid, 404)
        except psycopg.errors.IntegrityConstraintViolation:
            return _feil("part_ulovlig_tilstand", rid, 409)
        return _ok_lagret(conn, {"kontakt_id": str(kid),
                                 "maske": _maske(verdi, kanal)}, rid)

    return _med_conn(tjeneste, rid, kjor)


def deaktiver_endepunkt(tjeneste, request: Request) -> Response:
    """POST /v1/parter/{part_id}/deaktiver (part:administrer, idem).

    ENVEIS, og det er den som starter retensjonsklokken (184): fristen
    løper fra HER, ikke fra opprettelsen. Gjenspill er et stille ja.
    """
    from db.pg import sett_kontekst

    from .app import _rid
    from .policyadmin_http import _feil, _med_conn, _ok_lagret
    rid = _rid(request)
    pid = request.path_params["part_id"]

    def kjor(conn):
        import psycopg

        from . import kjerne
        from .app import _autentiser
        try:
            auth = _autentiser(tjeneste, request, conn, rid,
                               "part:administrer")
        except kjerne.Feilsvar as f:
            return _feil(f.kode, rid)
        avbrudd = _krev_csrf(tjeneste, request, conn, rid, auth)
        if avbrudd is not None:
            return avbrudd
        conn.rollback()
        sett_kontekst(conn, auth.tenant, auth.aktor, rid)
        try:
            with conn.transaction():
                ny = conn.execute("SELECT part_deaktiver(%s,%s,%s)",
                                  (auth.tenant, pid,
                                   auth.aktor)).fetchone()[0]
        except psycopg.errors.ForeignKeyViolation:
            return _feil("ikke_funnet", rid, 404)
        return _ok_lagret(conn, {"part_id": str(pid), "aktiv": False,
                                 "ny": bool(ny)}, rid)

    return _med_conn(tjeneste, rid, kjor)
