"""M-32 global lokaliserings- og skatteagentens API (migrasjon 138).

Åtte endepunkter: fem leseveier og tre skriveveier, alle mot dører.
Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_skatt_eier`-eid SECURITY DEFINER-dør i 138, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

DET FINNES INGEN RUTE SOM INNBERETTER NOE.

Ingen `/innsending`, ingen `/send`, ingen `/rapporter`. En innberettet
mva-oppgave er hos skattemyndigheten, og en rollback gjør den ikke
usendt — den gjør bare at vi ikke lenger vet hva vi sendte.

OG INGEN RUTE SKRIVER I LANDREGISTERET.

`landpakke` og `landsats` er globale og tenantløse, og
`disponit_skatt_eier` har SELECT og ingenting annet på begge. En rute
her ville ikke hatt noe å skrive med. DOMMENE FELLES I GIT, IKKE
GJENNOM EN DØR — M-31s plattformregisterform, arvet gjennom
`retensjonslager` (093) og `m36_funnregister` (132).

KALLEREN OPPGIR ALDRI JURISDIKSJONEN.

`POST /beregn` tar en ADRESSEVERSJON; landet leses derfra. En parameter
for jurisdiksjonen ville gjort modulen til en kalkulator som regner på
det den får beskjed om — og et fallback til selgerlandets sats ville
gitt en tysk transaksjon norsk mva, med et regnskap som så riktig ut.

USIKKER JURISDIKSJON STOPPER TRANSAKSJONEN. Døra nekter fire ganger:
ukjent adresseversjon, land uten landpakke som gjaldt PÅ
transaksjonsdatoen, pakke uten den satskoden, og ukjent kravversjon.
Alle fire kommer ut som 400 og ikke som 500 — en 500 på «Tyskland har
ingen landpakke» er en feilmelding ingen kan handle på.

SCOPENE. LESING `okonomi:read`, SKRIVING `bestilling:opprett`.

Lesescopet er `okonomi:read` — det samme M-13 og M-14 bruker: en
skattevurdering er et beløp og en sats på en transaksjon, og den hører
til i regnskapet framfor i sikkerhetsbildet. Landregisteret leses med
samme scope: det er ikke tenantdata, men det er heller ikke noe en
uinnlogget skal bla i.

SP-2 PÅ BEREGNINGSDØRA: id-en utledes av Idempotency-Key-en.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

MAKS_VURDERINGER = 200
MAKS_FUNN = 200
MAKS_TEKST = 8000
MAKS_REF = 200
#: Maks beløp i én transaksjon. Ti milliarder øre er hundre millioner
#: kroner — over det er det en importfeil, ikke et salg.
MAKS_BELOP_ORE = 10_000_000_000
#: Grensene API-et håndhever før døra. Speiler CHECK-ene i 138.
KRAVGRENSER = {
    "kontrollfrist_dogn": (1, 365),
}

_M32_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m32:skatt")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M32_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    verdi = verdi.strip()
    if not verdi or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _landkode(kropp, felt: str, rid) -> str:
    """ISO 3166-1 alpha-2, VERSALER.

    Formen håndheves her OG i basen. Et «land» som ikke er en landkode
    er en skrivefeil vi ikke skal regne skatt på — og en liten `no` er
    den vanligste av dem.
    """
    import re
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str) or not re.fullmatch(r"[A-Z]{2}", verdi):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _satskode(kropp, felt: str, rid) -> str:
    """SETTET AV SATSKODER ER LANDETS, IKKE VÅRT.

    Derfor ingen lukket liste her — bare formen. Et lukket sett ville
    vært huset som bestemte hvilke satser verden har lov til å ha, og
    det neste landet ville hatt en vi ikke kjente.
    """
    import re
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str) or not re.fullmatch(r"[a-z_]{2,40}",
                                                      verdi):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _heltall(kropp, felt: str, rid, minst: int, mest: int) -> int:
    """`isinstance(x, bool)` fordi `True` er en `int` i Python."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, int) or isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if not (minst <= verdi <= mest):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _dato(kropp, felt: str, rid) -> str:
    """ISO-dato som STRENG. Døra parser den; API-et validerer formen.

    En dato frosset ved import ville råtnet med kalenderen (124s
    CodeRabbit-funn), så `date.today()` står ingen steder her.
    """
    import datetime
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    try:
        datetime.date.fromisoformat(verdi)
    except ValueError as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e
    return verdi


def _kropp_uuid(kropp, felt: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(kropp.get(felt)))
    except (ValueError, AttributeError, TypeError) as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e


def _sti_uuid(request, navn: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(request.path_params[navn])
    except (KeyError, ValueError, AttributeError, TypeError) as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e


def _doerfeil(e, rid):
    """Dørens NEKT er brukerens feil, ikke serverens.

    «Tyskland har ingen landpakke» er en 400, ikke en 500: det er noe
    kalleren kan gjøre noe med, og en 500 ville sendt den til
    driftsvakten framfor til den som skal felle en landpakke.
    """
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, (psycopg.errors.RaiseException,
                      psycopg.errors.InvalidParameterValue,
                      psycopg.errors.InsufficientPrivilege,
                      psycopg.errors.CheckViolation,
                      psycopg.errors.NotNullViolation,
                      psycopg.errors.NoDataFound,
                      psycopg.errors.UniqueViolation,
                      psycopg.errors.ForeignKeyViolation)):
        return _Avbrudd(_feil("request_feilformet", rid,
                              detalj=str(e).split("\n")[0]))
    return None


# ---------------------------------------------------------------------
# RADBYGGERNE — ÉN FORM, ETT STED.
# ---------------------------------------------------------------------

def _bilderad(r) -> dict:
    return {
        "vurderinger": r[0], "land_i_bruk": r[1],
        "over_kontrollgrense": r[2], "skatt_ore": r[3],
        # DET VIKTIGSTE TALLET I MODULEN, OG DET ER ALLTID 0.
        #
        # Ikke en telling av en kolonne — en påstand om at kolonnen
        # ikke finnes. Ingen tabell i 138 har `innsendt_ts`,
        # `sendt_ts` eller `kvittering`. Blir dette noen gang noe annet
        # enn 0, er v1-dommen brutt av noen som la til en tabell.
        "innberetninger": r[4],
        "apne_funn": r[5], "har_krav": r[6], "selgerland": r[7],
        "manuell_kontroll_over_ore": r[8],
        "kontrollfrist_dogn": r[9], "kravversjon": r[10],
    }


def _landrad(r) -> dict:
    return {
        "landkode": r[0], "regelversjon": r[1], "valuta": r[2],
        "desimaler": r[3], "avrundingsregel": r[4],
        "dokumentformat": r[5],
        "gyldig_fra": r[6].isoformat(),
        "gyldig_til": r[7].isoformat() if r[7] else None,
        "gjelder": r[8],
        # EN PAKKE UTEN SATSER ER IKKE KOMPLETT. Tallet står i lista så
        # den som lurer på hvorfor en beregning stoppet, ser det selv.
        "satser": r[9],
        # ET MENNESKE HAR SETT PÅ DEN. En landpakke ingen har satt
        # navnet sitt på er ikke godkjent — den er bare skrevet.
        "signert_av": r[10],
    }


def _satsrad(r) -> dict:
    return {"satskode": r[0], "promille": r[1], "begrunnelse": r[2]}


def _vurderingsrad(r) -> dict:
    return {
        "vurdering_id": str(r[0]), "transaksjonsref": r[1],
        "jurisdiksjon": r[2],
        # BEGGE LANDENE, ALLTID. v1s regel er at jurisdiksjonen er
        # kjøperens land — riktig for fjernsalg til forbruker i EØS og
        # feil for flere andre tilfeller. Derfor lagres begge, så en
        # senere regel kan regnes om og etterprøves.
        "kjoperland": r[3], "selgerland": r[4],
        # REGELVERSJONEN. En sats uten versjonen den kom fra er et tall
        # ingen kan etterprøve.
        "regelversjon": r[5], "satskode": r[6], "promille": r[7],
        "belop_ore": r[8], "skatt_ore": r[9],
        "transaksjonsdato": r[10].isoformat(),
        "over_kontrollgrense": r[11],
        "beregnet_ts": r[12].isoformat(),
    }


def _funnrad(r) -> dict:
    return {
        "funn_id": str(r[0]), "funntype": r[1], "referanse": r[2],
        "detalj": r[3], "sveipens": r[4],
        "forst_sett": r[5].isoformat(),
    }


def svar_for(conn, tenant: str) -> dict:
    """HELE BILDET I ETT KALL (124/127/128/130/132-137s form)."""
    return {
        "sammendrag": _bilderad(
            conn.execute("SELECT * FROM m32_bildet(%s)",
                         (tenant,)).fetchone()),
        "vurderinger": [_vurderingsrad(r) for r in conn.execute(
            "SELECT * FROM m32_vurderingene(%s,%s)",
            (tenant, MAKS_VURDERINGER)).fetchall()],
        # LANDREGISTERET ER MED I BILDET, og det er med vilje: den som
        # lurer på hvorfor en beregning stoppet, skal se at landet
        # mangler en pakke framfor å måtte spørre oss.
        "land": [_landrad(r) for r in conn.execute(
            "SELECT * FROM m32_landene(NULL)").fetchall()],
        "funn": [_funnrad(r) for r in conn.execute(
            "SELECT * FROM m32_skattefunn(%s,%s)",
            (tenant, MAKS_FUNN)).fetchall()],
    }


# ---------------------------------------------------------------------
# LESEVEIENE.
# ---------------------------------------------------------------------

def skattebilde(tjeneste, request):
    """GET /v1/skatt (okonomi:read) — hele registeret."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def vurderinger_endepunkt(tjeneste, request):
    """GET /v1/skatt/vurderinger (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m32_vurderingene(%s,%s)",
                             (auth.tenant, MAKS_VURDERINGER)).fetchall()
        return kanonisk_json(
            {"vurderinger": [_vurderingsrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def land_endepunkt(tjeneste, request):
    """GET /v1/skatt/land (okonomi:read) — det globale registeret.

    IKKE TENANTDATA. Verdens regler, lesbare for alle som har scopet.
    En tenant som lurer på hvorfor en beregning stoppet, skal kunne se
    at landet mangler en pakke — framfor å måtte spørre oss.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, _auth, rid):
        rader = conn.execute("SELECT * FROM m32_landene(NULL)").fetchall()
        return kanonisk_json(
            {"land": [_landrad(r) for r in rader], "request_id": rid},
            200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def satser_endepunkt(tjeneste, request):
    """GET /v1/skatt/land/{landkode}/{regelversjon} (okonomi:read)."""
    from .app import _rid
    from .lesing import _les, kanonisk_json
    from .policyadmin_http import _Avbrudd, _feil
    import re
    rid = _rid(request)
    kode = str(request.path_params.get("landkode", ""))
    if not re.fullmatch(r"[A-Z]{2}", kode):
        raise _Avbrudd(_feil("request_feilformet", rid))
    try:
        versjon = int(request.path_params["regelversjon"])
    except (KeyError, ValueError, TypeError) as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e

    def _fn(conn, _auth, rid_):
        rader = conn.execute("SELECT * FROM m32_satsene(%s,%s)",
                             (kode, versjon)).fetchall()
        return kanonisk_json(
            {"landkode": kode, "regelversjon": versjon,
             "satser": [_satsrad(r) for r in rader],
             "request_id": rid_}, 200, {"x-request-id": rid_})

    return _les(tjeneste, request, "okonomi:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/skatt/funn (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m32_skattefunn(%s,%s)",
                             (auth.tenant, MAKS_FUNN)).fetchall()
        return kanonisk_json(
            {"funn": [_funnrad(r) for r in rader], "request_id": rid},
            200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


# ---------------------------------------------------------------------
# SKRIVEVEIENE.
#
# TRE, OG INGEN AV DEM SENDER NOE. `/krav` setter tenantens grenser,
# `/beregn` svarer på hva som gjaldt, `/funn/{id}/lukk` lukker et funn
# et menneske har håndtert.
#
# DET FINNES INGEN FJERDE. Ingen `/innsending`, ingen `/send`, ingen
# `/landpakke` — og de to første er utelatt fordi modulen ikke skal
# sende, den tredje fordi landregisteret felles i git.
# ---------------------------------------------------------------------

def _skriv(tjeneste, request, bygg):
    """Rammen to av de tre skriveveiene deler.

    `/beregn` står utenfor: svaret er mer enn en kvittering. Kalleren
    oppgir aldri jurisdiksjonen eller satsen, så begge må komme
    tilbake — sammen med valutaen og om beløpet krever kontroll.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "bestilling:opprett")
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        sql, args, svar, felt = bygg(tenant, bid, nokkel, kropp, rid,
                                     request)
        try:
            ut = conn.execute(sql, args).fetchone()[0]
        except (psycopg.errors.InvalidDatetimeFormat,
                psycopg.errors.DatetimeFieldOverflow) as e:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid)) from e
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        if felt is not None:
            svar = {**svar, felt: ut}
        return _ok(svar, rid)

    return _med_conn(tjeneste, rid, kjor)


def krav_endepunkt(tjeneste, request):
    """POST /v1/skatt/krav (bestilling:opprett, idem).

    SELGERLANDET MÅ HA EN LANDPAKKE. Uten den kan ingen si hva som er
    innenlands, og «usikker jurisdiksjon» begynner allerede her. Døra
    nekter, og nektet er en 400: det er noe kalleren kan gjøre noe med.

    VERSJONEN TILDELES AV DØRA, IKKE HER OG IKKE AV KALLEREN. Raden er
    append-only fordi vurderingene peker på den.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        land = _landkode(kropp, "selgerland", rid)
        grense = _heltall(kropp, "manuell_kontroll_over_ore", rid, 0,
                          MAKS_BELOP_ORE)
        frist = _heltall(kropp, "kontrollfrist_dogn", rid,
                         *KRAVGRENSER["kontrollfrist_dogn"])
        del nokkel
        return ("SELECT m32_sett_krav(%s,%s,%s,%s,%s)",
                (tenant, land, grense, frist, bid), {}, "kravversjon")
    return _skriv(tjeneste, request, bygg)


def beregn_endepunkt(tjeneste, request):
    """POST /v1/skatt/beregn (bestilling:opprett, idem).

    MODULENS HOVEDDØR — og den som ikke bruker `_skriv`, fordi svaret
    er mer enn en kvittering.

    KALLEREN OPPGIR EN ADRESSEVERSJON, IKKE ET LAND. Jurisdiksjonen
    leses derfra, og adressen er versjonert: en jurisdiksjon regnet ut
    fra dagens adresse for fjorårets transaksjon er feil på nøyaktig
    den måten klynge 7s dom advarer mot.

    OG SATSEN ER LANDETS. Kalleren oppgir en satskode, aldri en
    promille — en promilleparameter ville gjort landregisteret til
    pynt.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "bestilling:opprett")
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        ref = _tekst(kropp, "transaksjonsref", rid, MAKS_REF)
        kravversjon = _heltall(kropp, "kravversjon", rid, 1, 1_000_000)
        adresse = _kropp_uuid(kropp, "adresseversjon_id", rid)
        satskode = _satskode(kropp, "satskode", rid)
        belop = _heltall(kropp, "belop_ore", rid, 0, MAKS_BELOP_ORE)
        dato = _dato(kropp, "transaksjonsdato", rid)
        uid = _utled("vurdering", tenant, nokkel)
        try:
            rad = conn.execute(
                "SELECT * FROM m32_beregn(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, uid, ref, kravversjon, adresse, satskode,
                 belop, dato, bid)).fetchone()
        except (psycopg.errors.InvalidDatetimeFormat,
                psycopg.errors.DatetimeFieldOverflow) as e:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid)) from e
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        return _ok({"vurdering_id": str(uid), "jurisdiksjon": rad[0],
                    "regelversjon": rad[1], "promille": rad[2],
                    "skatt_ore": rad[3], "valuta": rad[4],
                    "krever_kontroll": rad[5]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/skatt/funn/{funn_id}/lukk (bestilling:opprett, idem).

    OG DØRA NEKTER FOR SVEIPENS EGNE. Et menneske som kunne lukket
    `landpakke_uten_sats` ville lukket en måling og ikke en sak — og de
    fire umulige kan ingen lukke, fordi ingen kan reise dem.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        del nokkel
        f = _sti_uuid(request_, "funn_id", rid)
        grunn = _tekst(kropp, "grunn", rid, MAKS_TEKST)
        return ("SELECT m32_lukk_funn(%s,%s,%s,%s)",
                (tenant, f, grunn, bid), {"funn_id": str(f)}, None)
    return _skriv(tjeneste, request, bygg)
