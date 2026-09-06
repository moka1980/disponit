"""M-40 HR- og medarbeideragent (140) — KLYNGE 10s FJERDE OG SISTE.

V1-DOMMEN: MODULEN AVGJØR INGENTING OM ET MENNESKE. Ingen beslutning
med rettsvirkning, ingen individprofilering, ingen produktivitetsscore.

DET SYNES I RUTELISTA, OG DET ER MENINGEN. Det finnes ingen
`/ansett`, ingen `/si-opp`, ingen `/vurder` og ingen `/score`. De er
ikke utelatt fordi de var vanskelige, men fordi modulen ikke skal ta
en beslutning et menneske må leve med.

  EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN ANGRES IKKE AV EN
  ROLLBACK.

PULSDØRA ER MODULENS SÆREGNE, og den skiller seg fra alle andre
skriveruter i huset: `/puls` tar ikke imot hvem som svarer, fordi det
ikke finnes noe sted å skrive det. Signaturen kan ikke bryte et løfte
den ikke er i stand til å gi.

AGGREGATET LESES ALDRI UNDER TERSKELEN, og terskelen er MÅLINGENS —
låst da den ble åpnet. En 404 på et pulsbilde er derfor ikke
nødvendigvis «finnes ikke»; det kan være «for få har svart», og det er
riktig svar.

ANSATTREGISTERET ER M-39s (113). Ruta slår opp i `lonnstaker` og
bygger ikke et andre register. To registre over de samme menneskene
gir to svar på «jobber hun her», og det er ett for mange.

MALENE ER M-5s (094). En kontrakt spores til `malversjon` og
`malfelt`, og hashen festes ved utstedelse.

SCOPENE. LESING `okonomi:read`, SKRIVING `bestilling:opprett` — samme
par som M-39 (113) og hele 096-112-rekka.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes av Idempotency-Key-en.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

MAKS_LOP = 200
MAKS_KONTRAKTER = 200
MAKS_MAALINGER = 200
MAKS_FUNN = 200
MAKS_TEKST = 8000
MAKS_REF = 200
MAKS_FELT = 100

#: Speiler CHECK-ene i 140.
GRENSER = {
    "gruppeterskel_min": (5, 1000),
    "gruppeterskel": (5, 1000),
    "apent_lop_frist_dogn": (1, 3650),
    "stegnr": (1, 100),
    "verdi": (1, 5),
}

#: STEGENE I EN FØRSTEUKE, LUKKET SETT (137s form).
#:
#: En åpen `stegtype` ville gjort katalogen til fritekst, og da kan
#: ingen si hva et løp faktisk inneholder.
STEGTYPER = (
    "utstyr_utlevert",
    "tilgang_opprettet",
    "kontrakt_utstedt",
    "introsamtale_holdt",
    "hms_gjennomgatt",
    "fadder_tildelt",
    "opplaering_fullfort",
)

#: AVSLUTNINGENE. Et løp er fullført eller avbrutt, aldri bare borte.
AVSLUTNINGER = ("fullfort", "avbrutt")

_M40_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m40:medarbeider")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M40_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    verdi = verdi.strip()
    if not verdi or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _valg(kropp, felt: str, rid, lovlige) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi not in lovlige:
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


def _feltnokler(kropp, rid) -> list[str]:
    """KILDEFELTENE, OG BARE NØKLENE.

    Ruta tar imot hvilke av malens felter som ble fylt — aldri hva som
    sto i dem. En kontraktverdi er persondata, og v1 har ingen grunn
    til å eie den.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get("feltnokler")
    if not isinstance(verdi, list) or not verdi:
        raise _Avbrudd(_feil("request_feilformet", rid))
    if len(verdi) > MAKS_FELT:
        raise _Avbrudd(_feil("request_feilformet", rid))
    ut = []
    for n in verdi:
        if not isinstance(n, str):
            raise _Avbrudd(_feil("request_feilformet", rid))
        n = n.strip()
        if not n or len(n) > 63:
            raise _Avbrudd(_feil("request_feilformet", rid))
        ut.append(n)
    return ut


def _doerfeil(e, rid):
    """Dørens NEKT er brukerens feil, ikke serverens.

    «Malen er ikke publisert» og «terskelen er under gulvet» er begge
    noe kalleren kan gjøre noe med. En 500 ville sendt henne til
    driftsvakten framfor til den som skal publisere malen.

    `InsufficientPrivilege` STÅR I LISTEN fordi husets tenantvakt
    (`krev_tenantkontekst`, 038) reiser nettopp den når et kall ber om
    en annen tenants data.
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
# RADBYGGERNE.
# ---------------------------------------------------------------------

def _bilderad(r) -> dict:
    return {
        "apne_lop": r[0], "fullforte_lop": r[1], "kontrakter": r[2],
        "apne_maalinger": r[3], "lesbare_grupper": r[4],
        "apne_funn": r[5],
        # DE TO VIKTIGSTE TALLENE I MODULEN, OG DE ER ALLTID 0.
        #
        # Ikke en telling av en kolonne — en påstand om at kolonnen
        # ikke finnes. Et tall som alltid er null er stedet et menneske
        # kan se etter for å oppdage den dagen det ikke er det.
        "beslutninger": r[6], "individprofiler": r[7],
        # KRAVET, SOM FLATEN TRENGER FOR Å VITE OM DEN KAN VISE
        # SKJEMAENE I DET HELE TATT.
        "har_krav": r[8], "gruppeterskel_min": r[9],
        "apent_lop_frist_dogn": r[10], "kravversjon": r[11],
    }


def _loprad(r) -> dict:
    return {
        "lop_id": str(r[0]), "taker_id": str(r[1]),
        # ANSATTNUMMERET, IKKE NAVNET. Modulen vet AT hun er ansatt.
        "ekstern_ref": r[2],
        "status": r[3], "startet": r[4].isoformat(),
        "steg": r[5], "steg_utfort": r[6],
    }


def _kontraktrad(r) -> dict:
    return {
        "kontrakt_id": str(r[0]), "taker_id": str(r[1]),
        "ekstern_ref": r[2], "malversjon_id": str(r[3]),
        "malversjonsnr": r[4], "malnavn": r[5],
        # MALENS STATUS I DAG, ikke da kontrakten ble utstedt. Det er
        # nettopp den forskjellen `kontrakt_paa_tilbaketrukket_mal`
        # handler om.
        "malstatus": r[6],
        # KILDEFELTENE — hvilke, aldri hva som sto i dem.
        "felt": list(r[7] or []),
        "utstedt": r[8].isoformat(),
    }


def _maalingsrad(r) -> dict:
    return {
        "maaling_id": str(r[0]), "tittel": r[1],
        "gruppeterskel": r[2], "apnet": r[3].isoformat(),
        "lukket": r[4].isoformat() if r[4] else None,
        # LESBARE GRUPPER, ALDRI ANTALL SVAR. Et totaltall for en
        # måling med én gruppe VILLE VÆRT gruppens tall, og da hadde
        # terskelen vært omgått av oversikten framfor av aggregatet.
        "lesbare_grupper": r[5],
    }


def _funnrad(r) -> dict:
    # KOLONNEREKKEFØLGEN ER DØRAS, IKKE RADBYGGERENS.
    #
    # `m40_medarbeiderfunn` gir sju felt: funn_id, funntype, referanse,
    # detalj, forst_sett, sist_sett, sveipens. Første utgave leste
    # `sveipens` på 4 og `forst_sett` på 5 — arvet fra en tidligere
    # form av tabellen som ikke hadde `sist_sett`.
    #
    # Den ville sendt en tidsstempel som «sveipens» og krasjet på
    # `.isoformat()` mot en boolsk. Ingen port kalte døra, så
    # ingenting fanget det. CodeRabbit gjorde det.
    return {
        "funn_id": str(r[0]), "funntype": r[1], "referanse": r[2],
        "detalj": r[3], "forst_sett": r[4].isoformat(),
        "sveipens": r[6],
    }


def _pulsrad(r) -> dict:
    return {"gruppe": r[0], "antall": r[1], "snitt": float(r[2])}


def svar_for(conn, tenant: str) -> dict:
    """HELE BILDET I ETT KALL (124-139s form).

    PULSSVARENE ER IKKE MED, og det er ikke en forglemmelse: et
    aggregat hører til én måling og hentes med målingens id. En
    samlerute som ga alle aggregatene på én gang ville invitert til å
    lese dem som en tidsserie per gruppe — og en gruppe som krymper
    fra åtte til tre gjør gårsdagens anonyme svar identifiserbare i
    dag.
    """
    return {
        "sammendrag": _bilderad(
            conn.execute("SELECT * FROM m40_bildet(%s)",
                         (tenant,)).fetchone()),
        "lop": [_loprad(r) for r in conn.execute(
            "SELECT * FROM m40_lopene(%s,%s)",
            (tenant, MAKS_LOP)).fetchall()],
        "kontrakter": [_kontraktrad(r) for r in conn.execute(
            "SELECT * FROM m40_kontraktene(%s,%s)",
            (tenant, MAKS_KONTRAKTER)).fetchall()],
        "maalinger": [_maalingsrad(r) for r in conn.execute(
            "SELECT * FROM m40_maalingene(%s,%s)",
            (tenant, MAKS_MAALINGER)).fetchall()],
        "funn": [_funnrad(r) for r in conn.execute(
            "SELECT * FROM m40_medarbeiderfunn(%s,%s)",
            (tenant, MAKS_FUNN)).fetchall()],
    }


# ---------------------------------------------------------------------
# LESEVEIENE.
# ---------------------------------------------------------------------

def medarbeiderbilde(tjeneste, request):
    """GET /v1/medarbeider (okonomi:read) — hele registeret."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def lop_endepunkt(tjeneste, request):
    """GET /v1/medarbeider/lop (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m40_lopene(%s,%s)",
                             (auth.tenant, MAKS_LOP)).fetchall()
        return kanonisk_json(
            {"lop": [_loprad(r) for r in rader], "request_id": rid},
            200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def kontrakt_endepunkt(tjeneste, request):
    """GET /v1/medarbeider/kontrakt (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m40_kontraktene(%s,%s)",
                             (auth.tenant, MAKS_KONTRAKTER)).fetchall()
        return kanonisk_json(
            {"kontrakter": [_kontraktrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def maaling_endepunkt(tjeneste, request):
    """GET /v1/medarbeider/maaling (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m40_maalingene(%s,%s)",
                             (auth.tenant, MAKS_MAALINGER)).fetchall()
        return kanonisk_json(
            {"maalinger": [_maalingsrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def pulsbilde_endepunkt(tjeneste, request):
    """GET /v1/medarbeider/maaling/{maaling_id}/puls (okonomi:read).

    AGGREGATET, OG DET ENESTE STEDET SVARENE LESES.

    ET TOMT SVAR ER ET GYLDIG SVAR og betyr «ingen gruppe er stor nok».
    Ruta skiller ikke mellom det og «målingen finnes ikke», og det er
    med vilje: forskjellen ville i seg selv fortalt at det finnes svar
    under terskelen.
    """
    from .lesing import _les, kanonisk_json
    from .app import _rid

    def _fn(conn, auth, rid):
        mid = _sti_uuid(request, "maaling_id", _rid(request))
        rader = conn.execute("SELECT * FROM m40_pulsbildet(%s,%s)",
                             (auth.tenant, mid)).fetchall()
        return kanonisk_json(
            {"grupper": [_pulsrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/medarbeider/funn (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m40_medarbeiderfunn(%s,%s)",
                             (auth.tenant, MAKS_FUNN)).fetchall()
        return kanonisk_json(
            {"funn": [_funnrad(r) for r in rader], "request_id": rid},
            200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


# ---------------------------------------------------------------------
# SKRIVEVEIENE.
#
# ÅTTE, OG INGEN AV DEM AVGJØR NOE OM ET MENNESKE. `/krav` setter
# grensene, `/lop` starter en førsteuke, `/lop/{id}/steg` krysser av
# for noe som ble gjort, `/lop/{id}/avslutt` lukker den, `/kontrakt`
# utsteder et dokument fra en låst mal, `/maaling` åpner en puls,
# `/maaling/{id}/puls` tar imot et svar, `/maaling/{id}/lukk` lukker
# målingen og `/funn/{id}/lukk` lukker et funn noen har håndtert.
#
# DET FINNES INGEN NIENDE. Ingen `/ansett`, ingen `/si-opp`, ingen
# `/vurder`, ingen `/score` — og de er utelatt fordi modulen ikke skal
# ta en beslutning et menneske må leve med, ikke fordi de var
# vanskelige.
# ---------------------------------------------------------------------

def _skriv(tjeneste, request, bygg):
    """Rammen alle skriveveiene deler."""
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
    """POST /v1/medarbeider/krav (bestilling:opprett, idem).

    `gruppeterskel_min` ER ET GULV, IKKE EN TERSKEL. Det sier hvor lavt
    en tenant får sette terskelen på en NY måling — og en senere heving
    kan ikke røre en måling som alt er åpnet.

    VERSJONEN TILDELES AV DØRA. Raden er append-only (135-139s form).
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        terskel = _heltall(kropp, "gruppeterskel_min", rid,
                           *GRENSER["gruppeterskel_min"])
        frist = _heltall(kropp, "apent_lop_frist_dogn", rid,
                         *GRENSER["apent_lop_frist_dogn"])
        del nokkel
        return ("SELECT m40_sett_krav(%s,%s,%s,%s)",
                (tenant, terskel, frist, bid), {}, "kravversjon")
    return _skriv(tjeneste, request, bygg)


def start_lop_endepunkt(tjeneste, request):
    """POST /v1/medarbeider/lop (bestilling:opprett, idem).

    ANSATTREGISTERET SLÅS OPP, DET BYGGES IKKE. Døra krever at
    `taker_id` finnes i `lonnstaker` og er aktiv — «jobber hun her»
    besvares ett sted i huset, og det er M-39s register.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        taker = _kropp_uuid(kropp, "taker_id", rid)
        kravversjon = _heltall(kropp, "kravversjon", rid, 1, 1_000_000)
        lid = _utled("lop", tenant, nokkel)
        return ("SELECT m40_start_lop(%s,%s,%s,%s,%s)",
                (tenant, lid, taker, kravversjon, bid),
                {"lop_id": str(lid)}, None)
    return _skriv(tjeneste, request, bygg)


def steg_endepunkt(tjeneste, request):
    """POST /v1/medarbeider/lop/{lop_id}/steg (bestilling:opprett, idem)."""
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        lid = _sti_uuid(request, "lop_id", rid)
        stegnr = _heltall(kropp, "stegnr", rid, *GRENSER["stegnr"])
        stegtype = _valg(kropp, "stegtype", rid, STEGTYPER)
        del nokkel
        return ("SELECT m40_utfor_steg(%s,%s,%s,%s,%s)",
                (tenant, lid, stegnr, stegtype, bid),
                {"lop_id": str(lid), "stegtype": stegtype}, None)
    return _skriv(tjeneste, request, bygg)


def avslutt_lop_endepunkt(tjeneste, request):
    """POST /v1/medarbeider/lop/{lop_id}/avslutt (bestilling:opprett).

    FULLFØRT ELLER AVBRUTT, ALDRI BARE BORTE. En førsteuke som stille
    forsvant ville sett ut som en som aldri fantes.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        lid = _sti_uuid(request, "lop_id", rid)
        status = _valg(kropp, "status", rid, AVSLUTNINGER)
        del nokkel
        return ("SELECT m40_avslutt_lop(%s,%s,%s,%s)",
                (tenant, lid, status, bid),
                {"lop_id": str(lid), "status": status}, None)
    return _skriv(tjeneste, request, bygg)


def utsted_kontrakt_endepunkt(tjeneste, request):
    """POST /v1/medarbeider/kontrakt (bestilling:opprett, idem).

    «KONTRAKTER KAN ALLTID SPORES TIL MALVERSJON OG KILDEFELT».

    Ruta tar imot HVILKE felter som ble fylt, aldri hva som sto i dem.
    Malen må være publisert — et utkast er ingen hjemmel, og en
    tilbaketrukket mal er en hjemmel noen har fjernet med vilje.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        taker = _kropp_uuid(kropp, "taker_id", rid)
        malversjon = _kropp_uuid(kropp, "malversjon_id", rid)
        felter = _feltnokler(kropp, rid)
        kid = _utled("kontrakt", tenant, nokkel)
        return ("SELECT m40_utsted_kontrakt(%s,%s,%s,%s,%s,%s)",
                (tenant, kid, taker, malversjon, felter, bid),
                {"kontrakt_id": str(kid)}, None)
    return _skriv(tjeneste, request, bygg)


def apne_maaling_endepunkt(tjeneste, request):
    """POST /v1/medarbeider/maaling (bestilling:opprett, idem).

    TERSKELEN LÅSES HER, ÉN GANG. Etterpå har ingen retten til å skrive
    kolonnen — heller ikke denne ruta. En terskel som kan endres i
    ettertid er ingen terskel; den er en innstilling.

    Den får være HØYERE enn tenantens gulv, aldri lavere: den som vil
    verne små grupper bedre enn huset krever, skal få lov.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        tittel = _tekst(kropp, "tittel", rid, 200)
        terskel = _heltall(kropp, "gruppeterskel", rid,
                           *GRENSER["gruppeterskel"])
        mid = _utled("maaling", tenant, nokkel)
        return ("SELECT m40_apne_maaling(%s,%s,%s,%s,%s)",
                (tenant, mid, tittel, terskel, bid),
                {"maaling_id": str(mid), "gruppeterskel": terskel}, None)
    return _skriv(tjeneste, request, bygg)


def avgi_puls_endepunkt(tjeneste, request):
    """POST /v1/medarbeider/maaling/{maaling_id}/puls.

    MODULENS SÆREGNE DØR, OG DEN ENESTE I HUSET SOM IKKE SKRIVER ET
    SPOR.

    Ruta tar ikke imot hvem som svarer. Ikke fordi feltet er valgfritt,
    men fordi det ikke finnes noen kolonne å skrive det i — og et
    bevisspor per svar ville hatt tidspunkt, gruppe og aktør i samme
    rad, som er nøyaktig den koblingen `pulssvar` ikke har.

    `bid` BRUKES DERFOR IKKE, og det er verdt å legge merke til: dette
    er den ene skriveruta der hvem som kaller ikke havner noe sted.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        mid = _sti_uuid(request, "maaling_id", rid)
        gruppe = _tekst(kropp, "gruppe", rid, 100)
        verdi = _heltall(kropp, "verdi", rid, *GRENSER["verdi"])
        sid = _utled("svar", tenant, nokkel)
        del bid
        return ("SELECT m40_avgi_puls(%s,%s,%s,%s,%s)",
                (tenant, sid, mid, gruppe, verdi),
                {"maaling_id": str(mid)}, None)
    return _skriv(tjeneste, request, bygg)


def lukk_maaling_endepunkt(tjeneste, request):
    """POST /v1/medarbeider/maaling/{maaling_id}/lukk."""
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        mid = _sti_uuid(request, "maaling_id", rid)
        del nokkel, kropp
        return ("SELECT m40_lukk_maaling(%s,%s,%s)",
                (tenant, mid, bid), {"maaling_id": str(mid)}, None)
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/medarbeider/funn/{funn_id}/lukk.

    DØRA NEKTER PÅ SVEIPENS EGNE. Et funn sveipen reiser og et menneske
    lukker, blir reist på nytt neste natt — det er å lukke en måling og
    ikke en sak (132s form, og M-28s lærdom).
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        fid = _sti_uuid(request, "funn_id", rid)
        grunn = _tekst(kropp, "lukkegrunn", rid, MAKS_TEKST)
        del nokkel
        return ("SELECT m40_lukk_funn(%s,%s,%s,%s)",
                (tenant, fid, grunn, bid), {"funn_id": str(fid)}, None)
    return _skriv(tjeneste, request, bygg)
